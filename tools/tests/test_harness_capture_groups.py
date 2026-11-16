"""The capture-group case of the harness suites (issue #644 P4b, the harness side), run.

`data/harness_capture_groups/` holds a hand-written realization of what each harness spec states
of the self-test's `l0_capture_groups_pass` case, one per harness, over the hand-written models
of `data/driver_e2e/`. These rows pin that the case's predicates, as each harness's `tests.md`
states them, hold through the host's own readers (the capture store,
`evaluate_primary_predicates`) on a model that follows the contract, and that each seeded defect
of a model fails a predicate or a reader. They hold the MPI and CUDA hand models, as well as the
Fortran one, to restart-point behaviour. They do not certify a harness: a generated one is
judged by its own run and by `generate.verify`.
"""
from __future__ import annotations

import os
import copy
import re
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from tools import capture_store
from tools import primary_evidence as pe
from tools.tests import skip_probes
from tools.tests.object_cache import compile_cached

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).parent / "data" / "harness_capture_groups"
MODELS = Path(__file__).parent / "data" / "driver_e2e"
_HARNESS_DIR = ROOT / "spec" / "infrastructure" / "infra" / "harness"
HARNESSES = ("harness_fortran_cpu", "harness_fortran_cpu_mpi", "harness_cpp_gpu",
             "harness_fortran_cpu_netcdf")
CASE = "l0_capture_groups_pass"
OTHER = "l0_case_fanout_pass"
#: A third case of the netcdf bench: a placeholder case that commits no record of a stacked
#: group (the netcdf §3's fanout case commits one of each).
PLAIN = "l0_perf_derived_pass"

#: The snapshot variables of the case, with the `shape_expr` each harness spec declares.
VARIABLES = {"g_s": "scalar", "g_r1": "[3]", "g_r2": "[2,3]", "g_r3": "[2,3,4]",
             "g_r4": "[2,3,4,5]", "g_never": "[2]", "g_z1": "[0]", "g_z2": "[3,0]"}
SERIES_NAMES = ["step", "g_a", "g_series_long"]


def _tests_md(spec_id: str) -> str:
    return (_HARNESS_DIR / spec_id / "tests.md").read_text(encoding="utf-8")


def sentinels() -> dict:
    """The inputs `tests.md` §2 declares for the case, as a Compile leaf writes them: what the
    self-test reads (`inputs.initial`) and the stacks the host compares with
    (`inputs.expected`), each the base plus the committed step of its record."""
    initial = {
        "g_n": 9, "g_every_a": 2, "g_every_b": 3, "g_every_p": 4, "g_dt": 0.25,
        "g_s": 0.5, "g_r1": [-1.5, 2.0, 4.25],
        "g_r2": (np.arange(1, 7).reshape(2, 3) * 0.5).tolist(),
        "g_r3": np.arange(1, 25).reshape(2, 3, 4).tolist(),
        "g_r4": np.arange(1, 121).reshape(2, 3, 4, 5).tolist(),
    }
    steps = {"a": [2, 4, 6, 8], "b": [3, 6, 9], "p": [4, 8]}

    def stack(name: str, which: str) -> list:
        return np.stack([np.asarray(initial[name], dtype=float) + k
                         for k in steps[which]]).tolist()

    expected = {
        "steps_a": steps["a"], "steps_b": steps["b"], "steps_p": steps["p"],
        "steps_all": list(range(1, 10)),
        "g_s_a": stack("g_s", "a"), "g_r1_a": stack("g_r1", "a"), "g_r2_a": stack("g_r2", "a"),
        "g_r3_b": stack("g_r3", "b"),
        "g_s_p": stack("g_s", "p"), "g_r1_p": stack("g_r1", "p"), "g_r2_p": stack("g_r2", "p"),
        "g_r3_p": stack("g_r3", "p"),
        "g_a": [0.5 + k for k in range(1, 10)],
        "g_series_long": [-1.5 - k for k in range(1, 10)],
    }
    return {"initial": initial, "expected": expected}


def predicates(spec_id: str) -> list[str]:
    """Every predicate expression of the case's test in `tests.md`, each `<expr> == 0.0`."""
    text = _tests_md(spec_id)
    row = text[text.index(f"- `test_id`: `{CASE}`") + 1:]
    row = row[:row.index("- `test_id`:")] if "- `test_id`:" in row else row[:row.index("## 7.")]
    line = re.search(r"`predicate`[^\n]*", row).group(0)
    body = line.split("): ", 1)[1].split("(op `eq`)", 1)[0]
    expressions = re.findall(r"`([^`]+)`", body)
    assert expressions and all(e.endswith(" == 0.0") for e in expressions), line
    return [e[:-len(" == 0.0")] for e in expressions]


def ir(spec_id: str) -> dict:
    """The part of the node's IR the readers take: the schema, the evidence entries, the two
    cases and the case's predicates, as the Compile contract has a leaf write them."""
    return {
        "case": {"test_case_set": [
            {"case_id": CASE, "inputs": sentinels()},
            {"case_id": OTHER, "inputs": {}},
        ]},
        "io_contract": {
            "raw_requirements": {"required_evidence": [
                {"artifact": "state_snapshots", "schema": {
                    "variables": [{"name": n, "shape_expr": s} for n, s in VARIABLES.items()],
                    "time_variable": "t", "time_shape_expr": "scalar"}},
                {"artifact": "state_history"},
                {"artifact": "restart_points"},
                {"artifact": "step_series", "names": SERIES_NAMES},
            ]},
            "tests": [{"test_id": CASE, "target_cases": [CASE], "expected_outcome": "pass",
                       "quantities": ["capture_groups"]}],
            "predicates": [
                {"test_id": CASE, "quantity": "capture_groups", "target_cases": [CASE],
                 "expr": expr, "op": "eq", "value": 0.0, "per_case": True}
                for expr in predicates(spec_id)],
        },
    }


@dataclass
class _Harness:
    """What a row builds and runs for one harness: its hand model, its self-test, the compile
    command (each source compiled to an object in a stage of its own, then linked), the files
    every compile sees (the rendered header of the C++ harness), and the launches of a run (an
    argv prefix each; the MPI harness's first is the singleton bare, its last several ranks)."""
    spec_id: str
    model: Path
    selftest: str
    compiler: list[str]
    defects: dict[str, tuple[str, str, set[int]]]
    unjudged: tuple[str, str]
    selftest_defects: dict[str, tuple[str, str, set[int]]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    launches: tuple[tuple[str, ...], ...] = ((),)
    env: dict[str, str] | None = None
    #: The format the harness writes, and the store a run's records are read through.
    output_format: str = "json"
    #: The words the link takes after the objects (the library the harness declares).
    link: list[str] = field(default_factory=list)
    #: The self-test's source when it is not the file `selftest` names in `DATA`.
    selftest_source: str | None = None
    #: Cases a run takes after the two every harness's run takes.
    extra_cases: tuple[str, ...] = ()

    @property
    def model_text(self) -> str:
        return self.model.read_text(encoding="utf-8")

    @property
    def selftest_text(self) -> str:
        if self.selftest_source is not None:
            return self.selftest_source
        return (DATA / self.selftest).read_text(encoding="utf-8")


def _stage(h: _Harness, out: Path, files: dict[str, bytes], source: str) -> None:
    """Compile `source` alone in a fresh directory holding `files` and the harness's headers
    (through the object cache), and copy the object and any module file it wrote to `out`."""
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for name, text in h.headers.items():
            (stage / name).write_text(text, encoding="utf-8")
        for name, data in files.items():
            (stage / name).write_bytes(data)
        compile_cached([*h.compiler, "-c", source, "-o", f"{Path(source).stem}.o"], stage)
        for path in stage.iterdir():
            if path.suffix in (".o", ".mod"):
                shutil.copy(path, out)


def _build(h: _Harness, model_text: str, out: Path, selftest_text: str | None = None) -> Path:
    """The self-test (`selftest_text`, else the fixture's) over `model_text`, built in `out`;
    returns the binary."""
    out.mkdir(parents=True)
    _stage(h, out, {h.model.name: model_text.encode()}, h.model.name)
    modules = {p.name: p.read_bytes() for p in out.glob("*.mod")}
    _stage(h, out, {**modules, h.selftest: (selftest_text if selftest_text is not None
                                              else h.selftest_text).encode()},
           h.selftest)
    subprocess.run([*h.compiler, f"{h.model.stem}.o", f"{Path(h.selftest).stem}.o", *h.link,
                    "-o", "selftest"], cwd=out, check=True, capture_output=True, text=True)
    return out / "selftest"


class _Run:
    """One run of a built self-test on both cases, in a fresh run dir under `out`."""

    def __init__(self, h: _Harness, binary: Path, out: Path, launch: tuple[str, ...],
                 cases: tuple[str, ...] | None = None) -> None:
        self.cases = cases if cases is not None else (CASE, OTHER, *h.extra_cases)
        self.run_dir = out / "run"
        self.output_format = h.output_format
        for parts in capture_store.run_dirs(h.output_format):
            self.run_dir.joinpath(*parts).mkdir(parents=True, exist_ok=True)
        ir_dir = out / "ir"
        ir_dir.mkdir()
        (ir_dir / "spec.ir.yaml").write_text("# not opened\n", encoding="utf-8")
        self.ir = ir(h.spec_id)
        pe.write_case_inputs(self.ir, ir_dir)
        self.proc = subprocess.run(
            [*launch, str(binary), "--cases", str(ir_dir / "spec.ir.yaml"), *self.cases],
            cwd=self.run_dir, capture_output=True, text=True, check=False, env=h.env,
            timeout=300)

    def unread(self) -> list[str]:
        """The cases whose final record the store does not read: refused or absent."""
        store = capture_store.store(self.output_format)
        snap = capture_store.snapshot_dir(self.run_dir)
        out = []
        for case_id in self.cases:
            try:
                store.read_record(snap, capture_store.FINAL, case_id)
            except capture_store.CaptureError:
                out.append(case_id)
        return out

    def failed(self) -> set[int]:
        """The predicates that do not hold, and `UNREAD` when a case's final is not read."""
        return ({i for i, record in enumerate(self.records()) if not record["satisfied"]}
                | ({UNREAD} if self.unread() else set()))

    def records(self) -> list[dict]:
        return pe.evaluate_primary_predicates(self.ir, self.run_dir,
                                              output_format=self.output_format)


#: The predicates of the test, by what each reads (their order in `tests.md`).
INITIAL, FINAL, HISTORY_A, HISTORY_B, POINTS, POINTS_R4, SERIES = range(7)
ALL = {INITIAL, FINAL, HISTORY_A, HISTORY_B, POINTS, POINTS_R4, SERIES}
#: A run in which the store reads no final record of some case (`_Run.unread`): the reading
#: every case's evidence takes, which no predicate of a case without one can show.
UNREAD = -1

#: Seeded defects of the Fortran model: name -> (the text replaced, its replacement, the
#: predicates that do not hold). Each is a writer that still produces a complete, readable run.
DEFECTS: dict[str, tuple[str, str, set[int]]] = {
    # a record keeps the values of the group's earlier records
    "kept_members": (
        "    cap_body(g)%text = ''\n  end subroutine harness_fortran_cpu__capture_commit",
        "  end subroutine harness_fortran_cpu__capture_commit", {HISTORY_A, HISTORY_B}),
    # every chunk of the series is written under the first chunk's name
    "chunk_overwritten": (
        "    cap_chunks = cap_chunks + 1\n    cap_pending = 0", "    cap_pending = 0", {SERIES}),
    # a chunk holds the rows of the chunks before it again
    "rows_repeated": (
        "    cap_chunks = cap_chunks + 1\n    cap_pending = 0",
        "    cap_chunks = cap_chunks + 1", {SERIES}),
    # the time of a commit is not written
    "time_dropped": (
        "', \"t\": ' // harness_fortran_cpu__real_token(t) // '}')",
        "', \"t\": ' // harness_fortran_cpu__real_token(0.0_dp) // '}')",
        {FINAL, HISTORY_A, HISTORY_B, POINTS}),
    # a series name is written at its padded width
    "series_name_padded": (
        "doc = doc // ', \"' // trim(cap_names(k)) // '\": '",
        "doc = doc // ', \"' // cap_names(k) // '\": '", {SERIES}),
    # a series row carries its position in the chunk, not its step
    "series_step_is_row": (
        "      cap_step(cap_pending) = step", "      cap_step(cap_pending) = cap_pending",
        {SERIES}),
    # a restart point is written among the history records
    "restart_point_in_history": (
        "      name = 'restart_point/' // cap_case", "      name = 'history/' // cap_case",
        {HISTORY_B, POINTS, POINTS_R4}),
    # a record carries another step than the one committed
    "record_step_off": (
        "'\"step\": ' // trim(buf) // &", "'\"step\": 1' // trim(buf) // &",
        {INITIAL, FINAL, HISTORY_A, HISTORY_B, POINTS, POINTS_R4}),
    # the close writes the rows not yet written only when there are two or more
    "close_drops_a_single_row": (
        "    if (cap_pending > 0 .or. cap_chunks == 0) call cap_write_series()",
        "    if (cap_pending > 1 .or. cap_chunks == 0) call cap_write_series()", {SERIES}),
    # a rank-4 array is nested with its last index outermost
    "r4_inner_first": (
        "      s = s // json_r3(a(i, :, :, :))",
        "      s = s // json_r3(reshape(a(i, :, :, :), [size(a, 4), size(a, 3), size(a, 2)], "
        "order=[3, 2, 1]))",
        ALL),
}

#: Seeded defects of the SELF-TEST over the clean model: a zero-extent variable left out of
#: the `history` records. The two terms that read the steps of those records are what fails
#: (review round 1 added them; without these rows the terms could be dropped unnoticed).
def _selftest_defects(prefix: str) -> dict[str, tuple[str, str, set[int]]]:
    return {
        "g_z1_not_in_history": (
            f"        call {prefix}__capture_array_r1('history', 'g_z1', z1)\n", "",
            {HISTORY_A}),
        "g_z2_not_in_history": (
            f"        call {prefix}__capture_array_r2('history', 'g_z2', z2)\n", "",
            {HISTORY_B}),
    }


#: The C++ self-test puts each stride's variables through one closure for every group, so its
#: variant leaves the variable out of every record; the history term is still what fails.
CUDA_SELFTEST_DEFECTS: dict[str, tuple[str, str, set[int]]] = {
    "g_z1_not_put": (
        '        harness_cpp_gpu__capture_array_r1(group, "g_z1", view<1>(none, z1_extent));\n',
        "", {HISTORY_A}),
    "g_z2_not_put": (
        '        harness_cpp_gpu__capture_array_r2(group, "g_z2", view<2>(none, z2_extent));\n',
        "", {HISTORY_B}),
}

#: A defect no predicate of a complete run can show (`TODO.md`, the lost series): the rows
#: committed before a restart point are written at the close, not before the restart point.
#: The whole series is in its files once the case is closed, so the store reads the same.
UNJUDGED = ("      if (cap_pending > 0) call cap_write_series()\n", "")

#: The MPI hand model's capture writer is the Fortran one under the MPI harness's names (the
#: same text but for the prefix), so each defect is the same replacement there, and its
#: failing set is measured on that model.
MPI_DEFECTS: dict[str, tuple[str, str, set[int]]] = {
    name: (old.replace("harness_fortran_cpu__", "harness_fortran_cpu_mpi__"),
           new.replace("harness_fortran_cpu__", "harness_fortran_cpu_mpi__"), failed)
    for name, (old, new, failed) in DEFECTS.items()}

#: The same defects in the C++ hand model. `series_name_padded` has no place in it: a series
#: name is a `std::string` of the name's own length, so there is no padded width to write.
CUDA_DEFECTS: dict[str, tuple[str, str, set[int]]] = {
    "kept_members": ("    body.clear();\n}", "}", {HISTORY_A, HISTORY_B}),
    "chunk_overwritten": ("    ++cap_chunks;\n    cap_rows.clear();", "    cap_rows.clear();",
                          {SERIES}),
    "rows_repeated": ("    ++cap_chunks;\n    cap_rows.clear();\n    cap_step.clear();\n"
                      "    cap_t.clear();", "    ++cap_chunks;", {SERIES}),
    "time_dropped": ("+ harness_cpp_gpu__real_token(t) + \"}\");",
                     "+ harness_cpp_gpu__real_token(0.0) + \"}\");",
                     {FINAL, HISTORY_A, HISTORY_B, POINTS}),
    "close_drops_a_single_row": ("    if (!cap_step.empty() || cap_chunks == 0) {",
                                 "    if (cap_step.size() > 1 || cap_chunks == 0) {", {SERIES}),
    "series_step_is_row": ("        cap_step.push_back(step);",
                           "        cap_step.push_back(static_cast<int>(cap_rows.size()));",
                           {SERIES}),
    "restart_point_in_history": ("        name = \"restart_point/\" + cap_case",
                                 "        name = \"history/\" + cap_case",
                                 {HISTORY_B, POINTS, POINTS_R4}),
    "record_step_off": ("\"\\\"step\\\": \" + int_token(step)",
                        "\"\\\"step\\\": 1\" + int_token(step)",
                        {INITIAL, FINAL, HISTORY_A, HISTORY_B, POINTS, POINTS_R4}),
    # the whole array is nested with its last index outermost (the reversed extents and
    # strides of the column-major view)
    "r4_inner_first": (
        "    cap_put(group, name, emit_view<4>(a));",
        "    const long e[4] = {a.extent[3], a.extent[2], a.extent[1], a.extent[0]};\n"
        "    const long st[4] = {a.extent[0] * a.extent[1] * a.extent[2],"
        " a.extent[0] * a.extent[1], a.extent[0], 1};\n"
        "    cap_put(group, name, emit_nested(a.data, e, st, 4, 0, 0));",
        ALL),
}

#: Seeded defects of the `netcdf` hand model (issue #644 P5b; the plan's six, the reversed
#: dimensions realized as an array written in the other index order): name -> (the text
#: replaced, its replacement, the predicates that do not hold). Each is a writer a normal run shows: either a
#: predicate reads what it broke, or the store refuses the case file, which every predicate of
#: the case then fails on.
NETCDF_DEFECTS: dict[str, tuple[str, str, set[int]]] = {
    # an array written in the other index order (its dimensions not reversed back)
    "r2_transposed": (
        "    call put_values(group, name, 2, reshape(a, [size(a)]), shape(a))",
        "    call put_values(group, name, 2, reshape(transpose(a), [size(a)]), shape(a))",
        {INITIAL, FINAL, HISTORY_A, POINTS}),
    # no NaN fill value: an unwritten cell reads the library's default fill, a finite number
    # (the store refuses)
    "fill_value_omitted": (
        ("    call nc(nf90_def_var_fill(cap_ncid, varid, 0, ieee_value(0.0_dp, ieee_quiet_nan)), &\n"
         "      & 'fill '//name)\n"), "", ALL | {UNREAD}),
    # fill mode off: an unwritten cell reads whatever the disk held (the store refuses)
    "fill_off": (
        "nf90_def_var_fill(cap_ncid, varid, 0, ieee_value",
        "nf90_def_var_fill(cap_ncid, varid, 1, ieee_value", ALL | {UNREAD}),
    # a sample carries its index among the variable's samples, not its step
    "history_step_is_index": (
        "[step], &\n          & start=[cap_vars(k)%h_count]",
        "[cap_vars(k)%h_count], &\n          & start=[cap_vars(k)%h_count]",
        {HISTORY_A, HISTORY_B}),
    # the close never marks the file complete (the store refuses every case)
    "complete_never_set": ("    call put_int(cap_complete, 1)\n", "", ALL | {UNREAD}),
    # a stacked group's count one short of its record dimension (the store refuses)
    "stacked_count_short": (
        "    call put_int(ids(3), n)\n", "    call put_int(ids(3), n - 1)\n", ALL | {UNREAD}),
    # the count of a group with no record left unwritten: the case with no restart point
    # reads it as the integer fill (the store refuses that case alone)
    "zero_record_count_unset": ("    call put_int(cap_rp(3), 0)\n", "", {UNREAD}),
    # a zero extent declared as one cell: the variable reads one unwritten (NaN) element
    "zero_extent_one_cell": (
        "          call nc(nf90_def_dim(cap_ncid, v // '__d' // itoa(r), nf90_unlimited, &",
        "          call nc(nf90_def_dim(cap_ncid, v // '__d' // itoa(r), 1, &", ALL),
    # the time of a restart point and of a series row is not written
    "stacked_time_dropped": (
        "    call nc(nf90_put_var(cap_ncid, ids(2), [t], start=[n], count=[1]), 'time')",
        "    call nc(nf90_put_var(cap_ncid, ids(2), [0.0_dp], start=[n], count=[1]), 'time')",
        {POINTS}),
    # a record dimension sized by the capacity the case was opened with, not unlimited: the
    # case that commits fewer rows than its capacity holds a count short of the dimension
    "record_dimension_sized_by_capacity": (
        "    call nc(nf90_def_dim(cap_ncid, 'series__n', nf90_unlimited, dim_s), 'dimension')",
        "    call nc(nf90_def_dim(cap_ncid, 'series__n', series_capacity, dim_s), 'dimension')",
        ALL | {UNREAD}),
    # the record counters outlive their case: the next case's records land after fill rows
    "record_counters_carried": ("    cap_n_rp = 0\n    cap_n_s = 0\n", "", {UNREAD}),
    # the restart points' counter alone outlives its case
    "restart_point_counter_carried": ("    cap_n_rp = 0\n", "", {UNREAD}),
    # a variable's samples start at the previous case's row count, not at 0
    "history_count_carried": ("      cap_vars(k)%h_count = 0\n",
                              "      cap_vars(k)%h_count = cap_n_s\n", {UNREAD}),
    # a sampled variable's count left unwritten when the case opens: a variable the case
    # samples and commits no record of reads its count as the integer fill
    "history_count_unset_at_open": (
        "      if (cap_vars(k)%sampled) call put_int(cap_vars(k)%vid_h_roles(3), 0)\n", "",
        {UNREAD}),
    # a series row written at the index of its step, not of its order: a case whose first
    # committed step is not 1 leaves fill rows ahead of it
    "series_row_at_its_step": ("      cap_n_s = cap_n_s + 1\n", "      cap_n_s = step\n",
                               {UNREAD}),
    # the final record's count never set: the store finds no final record
    "final_count_unset": (
        "      call put_int(cap_single(g, 3), 1)\n",
        "      if (g == 1) call put_int(cap_single(g, 3), 1)\n", ALL | {UNREAD}),
}

CUDA_UNJUDGED = ("        if (!cap_step.empty()) {\n            cap_write_series();\n"
                 "        }\n", "")


def _fortran_cpu() -> _Harness:
    return _Harness("harness_fortran_cpu", MODELS / "harness_fortran_cpu_model.f90",
                    "groups_selftest.f90", ["gfortran", "-O0", "-fcheck=bounds"],
                    DEFECTS, UNJUDGED, _selftest_defects("harness_fortran_cpu"))


def _fortran_cpu_mpi() -> _Harness:
    """Built with the MPI pair `test_parallel_mpi` resolves (a missing pair fails, #498), run
    as the singleton bare and on four ranks under its launcher. The grid variable is removed
    from the environment, so the model's `__init` takes the ranks as one line."""
    from tools.backends.parallel.mpi import execution, launcher, wrapper
    from tools.tests.test_parallel_mpi import _require_mpi_pair
    pair = Path(_require_mpi_pair())
    env = {k: v for k, v in os.environ.items() if k != execution.PROCESS_GRID_VARIABLE}
    ranks = (str(pair / launcher.EXECUTABLE), "--oversubscribe", "-n", "4")
    return _Harness("harness_fortran_cpu_mpi", MODELS / "mpi" / "harness_fortran_cpu_mpi_model.f90",
                    "groups_selftest_mpi.f90",
                    [str(pair / wrapper.COMPILER_WRAPPER), "-O0", "-fcheck=bounds"],
                    MPI_DEFECTS, UNJUDGED, _selftest_defects("harness_fortran_cpu_mpi"),
                    launches=((), ranks), env=env)


def _cpp_gpu() -> _Harness:
    """Host code only, against the header the host renders from the spec's §5.1."""
    from tools.backends.language.cuda_cpp import header
    from tools.tests.test_driver_render import _harness_public_api
    return _Harness("harness_cpp_gpu", MODELS / "harness_cpp_gpu_model.cu", "groups_selftest.cu",
                    [shutil.which("nvcc") or "nvcc", "-std=c++17", "-O2", "-I."],
                    CUDA_DEFECTS, CUDA_UNJUDGED, CUDA_SELFTEST_DEFECTS,
                    headers={"harness_cpp_gpu_model.cuh": header.render(
                        "harness_cpp_gpu", _harness_public_api(), spec_kind="infrastructure")})


#: The placeholder case's `capture_open` in the json self-test, and what the netcdf
#: harness's §3 has `l0_case_fanout_pass` (the bench's other case) do in its place.
FANOUT_OPEN_JSON = ("    call harness_fortran_cpu_netcdf__capture_open(case_id, variables(0, 0), 0, "
                    "series_names, 0)\n")


def fanout_commits(h: str) -> str:
    """The netcdf §3's `l0_case_fanout_pass` opens with capacities 2 (every variable sampled,
    the seven restart variables in its restart points) and commits, at step 2 and time 0, one
    `history` record of `g_s` and `g_never`, one `restart_point` record of the seven restart
    variables and one series row with `g_a = 1`, each a placeholder. Every other placeholder
    case opens as the json self-test's."""
    out = [f"    if (trim(case_id) /= '{OTHER}') then\n",
           "  " + FANOUT_OPEN_JSON.replace("harness_fortran_cpu_netcdf", h),
           "    else\n",
           "    fv = variables(2, 2)\n",
           "    fv(6)%history_capacity = 2\n",
           f"    call {h}__capture_open(case_id, fv, 2, series_names, 2)\n",
           f"    call {h}__capture_real('history', 'g_s', 0.0_dp)\n",
           f"    call {h}__capture_array_r1('history', 'g_never', never)\n",
           f"    call {h}__capture_commit('history', 2, 0.0_dp)\n",
           f"    call {h}__capture_real('restart_point', 'g_s', 0.0_dp)\n",
           f"    call {h}__capture_array_r1('restart_point', 'g_r1', r1)\n",
           f"    call {h}__capture_array_r2('restart_point', 'g_r2', r2)\n",
           f"    call {h}__capture_array_r3('restart_point', 'g_r3', r3)\n",
           f"    call {h}__capture_array_r4('restart_point', 'g_r4', r4)\n",
           f"    call {h}__capture_array_r1('restart_point', 'g_z1', z1)\n",
           f"    call {h}__capture_array_r2('restart_point', 'g_z2', z2)\n",
           f"    call {h}__capture_commit('restart_point', 2, 0.0_dp)\n",
           f"    call {h}__capture_real('series', 'g_a', 1.0_dp)\n",
           f"    call {h}__capture_real('series', series_names(2), 0.0_dp)\n",
           f"    call {h}__capture_commit('series', 2, 0.0_dp)\n",
           "    end if\n"]
    return "".join(out)


#: The placeholder case's declarations, and the netcdf self-test's with the fanout case's
#: variable list.
PLACEHOLDER_DECL = ("    real(dp) :: r1(3), r2(2, 3), r3(2, 3, 4), r4(2, 3, 4, 5), never(2), "
                    "z1(0), z2(3, 0)\n")


def fanout_predicates() -> list[str]:
    """The expressions of `l0_case_fanout_pass`'s predicate in the netcdf `tests.md` that read
    the stacked records (the entries of the form `<expr> == 0.0`)."""
    text = _tests_md("harness_fortran_cpu_netcdf")
    row = text[text.index(f"- `test_id`: `{OTHER}`") + 1:]
    row = row[:row.index("- `test_id`:")]
    line = re.search(r"`predicate`[^\n]*", row).group(0)
    found = [e[:-len(" == 0.0")] for e in re.findall(r"`([^`]+)`", line)
             if e.endswith(" == 0.0")]
    assert len(found) == 1, line
    return found


def _fortran_cpu_netcdf() -> _Harness:
    """The `netcdf` harness's hand model under the Fortran self-test with its names: built with
    the module directory of the library it declares and linked against it, run with the
    library's directory first on the loader path (`netcdf_harness`)."""
    from tools.tests import netcdf_harness
    h = netcdf_harness.HARNESS
    selftest = (DATA / "groups_selftest.f90").read_text(encoding="utf-8").replace(
        "harness_fortran_cpu", h)
    # this harness's §3: the other case, `l0_case_fanout_pass` here, commits one record of
    # each stacked group (`FANOUT_COMMITS`), so a record count carried across cases leaves one
    # of the two cases' records wrong
    assert selftest.count(FANOUT_OPEN_JSON) == 1
    selftest = selftest.replace(FANOUT_OPEN_JSON, fanout_commits(h))
    assert selftest.count(PLACEHOLDER_DECL) == 1
    selftest = selftest.replace(PLACEHOLDER_DECL, PLACEHOLDER_DECL + f"    type({h}__h_var) :: fv(8)\n")
    return _Harness(netcdf_harness.HARNESS, netcdf_harness.MODEL, "groups_selftest.f90",
                    ["gfortran", "-O0", "-fcheck=bounds", *netcdf_harness.compile_flags()],
                    NETCDF_DEFECTS, ("", ""), _selftest_defects(netcdf_harness.HARNESS),
                    env=netcdf_harness.run_env(), output_format="netcdf",
                    link=netcdf_harness.link_words(), selftest_source=selftest,
                    extra_cases=(PLAIN,))


class _CaptureGroupRows:
    """The rows of one harness; a subclass names it as `harness`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.h = cls.harness()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        binary = _build(cls.h, cls.h.model_text, cls.root / "clean")
        cls.clean = [_Run(cls.h, binary, cls.root / f"clean_{i}", launch)
                     for i, launch in enumerate(cls.h.launches)]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _variant(self, name: str, old: str, new: str) -> _Run:
        """The model with `old` replaced by `new`, run under the last launch."""
        model = self.h.model_text
        self.assertEqual(model.count(old), 1, old)
        binary = _build(self.h, model.replace(old, new), self.root / name)
        return _Run(self.h, binary, self.root / f"{name}_run", self.h.launches[-1])

    def test_the_case_passes_every_predicate_of_the_document(self) -> None:
        for launch, run in zip(self.h.launches, self.clean):
            with self.subTest(launch=launch):
                self.assertEqual(run.proc.returncode, 0, run.proc.stderr)
                if len(self.h.launches) > 1:
                    # the launch ran the ranks it names: the self-test prints the size of
                    # its communicator (a launcher that started one rank passes otherwise)
                    self.assertIn(f"ranks {launch[-1] if launch else 1}\n", run.proc.stdout)
                records = run.records()
                self.assertEqual(run.unread(), [])
                self.assertEqual(len(records), len(predicates(self.h.spec_id)))
                for record in records:
                    self.assertEqual((record["kind"], record["satisfied"]), ("pass", True),
                                     record)

    def test_each_seeded_defect_fails_the_predicates_that_read_what_it_breaks(self) -> None:
        for name, (old, new, expected) in self.h.defects.items():
            with self.subTest(defect=name):
                run = self._variant(name, old, new)
                self.assertEqual(run.proc.returncode, 0, run.proc.stderr)
                self.assertEqual(run.failed(), expected)

    def test_each_self_test_defect_fails_the_term_that_reads_it(self) -> None:
        source = self.h.selftest_text
        self.assertTrue(self.h.selftest_defects)
        for name, (old, new, expected) in self.h.selftest_defects.items():
            with self.subTest(defect=name):
                self.assertEqual(source.count(old), 1, old)
                binary = _build(self.h, self.h.model_text, self.root / name,
                                source.replace(old, new))
                run = _Run(self.h, binary, self.root / f"{name}_run", self.h.launches[-1])
                self.assertEqual(run.proc.returncode, 0, run.proc.stderr)
                self.assertEqual(run.failed(), expected)



class _SeriesChunkRows:
    """The rows of a harness whose format writes the series in chunks (`json`)."""

    def test_a_series_written_only_at_the_close_passes_the_complete_run(self) -> None:
        """The limit of the case, pinned so the documents that state it stay true: when a
        chunk is written is not something a complete run shows."""
        run = self._variant("close_only", *self.h.unjudged)
        self.assertEqual(run.proc.returncode, 0, run.proc.stderr)
        self.assertTrue(all(record["satisfied"] for record in run.records()))

        def chunks(r: _Run) -> list[str]:
            return sorted(p.name for p in (r.run_dir / "raw/state_snapshots/series").iterdir()
                          if p.name.startswith(CASE))
        self.assertEqual(chunks(run), [f"{CASE}__0.json"])
        self.assertEqual(chunks(self.clean[-1]), [f"{CASE}__{i}.json" for i in range(3)])


class FortranCpuCaptureGroupTests(_CaptureGroupRows, _SeriesChunkRows, unittest.TestCase):
    harness = staticmethod(_fortran_cpu)


class FortranCpuMpiCaptureGroupTests(_CaptureGroupRows, _SeriesChunkRows, unittest.TestCase):
    harness = staticmethod(_fortran_cpu_mpi)


@skip_probes.skip_unless_installed("cuda_compiler_driver")
class CppGpuCaptureGroupTests(_CaptureGroupRows, _SeriesChunkRows, unittest.TestCase):
    harness = staticmethod(_cpp_gpu)


class FortranCpuNetcdfCaptureGroupTests(_CaptureGroupRows, unittest.TestCase):
    harness = staticmethod(_fortran_cpu_netcdf)

    def _fanout(self, run: _Run) -> list[dict]:
        doc = copy.deepcopy(run.ir)
        doc["io_contract"]["tests"].append(
            {"test_id": OTHER, "target_cases": [OTHER], "expected_outcome": "pass",
             "quantities": ["case_fanout"]})
        doc["io_contract"]["predicates"] = [
            {"test_id": OTHER, "quantity": "case_fanout", "target_cases": [OTHER],
             "expr": expr, "op": "eq", "value": 0.0, "per_case": True}
            for expr in fanout_predicates()]
        return pe.evaluate_primary_predicates(doc, run.run_dir, output_format="netcdf")

    def test_the_fanout_case_judges_the_records_it_commits_and_the_carried_counts(
            self) -> None:
        """The netcdf `tests.md` judges what §3 has `l0_case_fanout_pass` commit, so a
        self-test that leaves those records out fails its own case: what keeps a carried
        record count from passing the suite is a predicate, not the self-test's good will. A
        carried count fails it when the fanout case runs second (the bench's order), and
        fails the capture-group case when it runs first."""
        def satisfied(run: _Run) -> list[bool]:
            return [r["satisfied"] for r in self._fanout(run)]
        self.assertEqual(satisfied(self.clean[-1]), [True])
        skipped = self.h.selftest_text.replace(fanout_commits(self.h.spec_id), FANOUT_OPEN_JSON)
        self.assertNotEqual(skipped, self.h.selftest_text)
        binary = _build(self.h, self.h.model_text, self.root / "fanout_skipped", skipped)
        run = _Run(self.h, binary, self.root / "fanout_skipped_run", self.h.launches[-1])
        self.assertEqual(run.proc.returncode, 0, run.proc.stderr)
        self.assertEqual(satisfied(run), [False])
        carried = self._variant("fanout_carried", "    cap_n_rp = 0\n", "")
        self.assertEqual(satisfied(carried), [False])
        # each stacked group's commit is read by its own terms: a self-test that leaves out
        # one group's record fails the predicate
        for group in ("history", "restart_point", "series"):
            with self.subTest(left_out=group):
                text = self.h.selftest_text.replace(fanout_commits(self.h.spec_id), "".join(
                    line for line in fanout_commits(self.h.spec_id).splitlines(keepends=True)
                    if f"('{group}'," not in line))
                self.assertNotEqual(text, self.h.selftest_text)
                binary = _build(self.h, self.h.model_text, self.root / f"fanout_no_{group}",
                                text)
                run = _Run(self.h, binary, self.root / f"fanout_no_{group}_run",
                           self.h.launches[-1])
                self.assertEqual(run.proc.returncode, 0, run.proc.stderr)
                self.assertEqual(satisfied(run), [False])
        first = self.h.model_text.replace("    cap_n_rp = 0\n", "")
        binary = _build(self.h, first, self.root / "fanout_first")
        run = _Run(self.h, binary, self.root / "fanout_first_run", self.h.launches[-1],
                   cases=(OTHER, CASE))
        self.assertEqual(satisfied(run), [True])
        self.assertTrue(run.failed())

    def test_the_fanout_predicate_passes_the_compile_stage_gates(self) -> None:
        """As `CaptureGroupDocumentTests` holds the capture-group case's: a gate that came to
        refuse the fanout case's predicate would stop the harness's Compile."""
        doc = ir(self.h.spec_id)
        contract = doc["io_contract"]
        contract["tests"].append({"test_id": OTHER, "target_cases": [OTHER],
                                  "expected_outcome": "pass", "quantities": ["case_fanout"]})
        contract["predicates"] += [
            {"test_id": OTHER, "quantity": "case_fanout", "target_cases": [OTHER],
             "expr": expr, "op": "eq", "value": 0.0, "per_case": True}
            for expr in fanout_predicates()]
        cases = pe._cases_by_id(doc)
        context = pe.predicate_schema_context(doc)
        self.assertEqual([
            *pe.validate_primary_predicate_schema(
                contract["predicates"], case_ids=set(cases), test_ids=[CASE, OTHER],
                schema=pe.snapshot_schema(doc), cases=cases,
                test_target_cases={CASE: [CASE], OTHER: [OTHER]}, **context),
            *pe.quantity_violations(contract["tests"], contract["predicates"],
                                    context["reads"]),
            *pe.drive_violations(doc, kind="infrastructure")], [])


#: How the `json` harnesses and the `netcdf` one name the nesting the case's inputs are written in.
JSON_NESTING = "in the JSON nesting order the capture operations produce"
NETCDF_NESTING = "in the JSON nesting order of the case file"

#: The sentence only the Fortran harnesses carry: C++ has no padded name to pass.
PADDING = re.compile(r" It passes the `name` of the `g_a` put.*?as a literal\.")


class CaptureGroupDocumentTests(unittest.TestCase):
    def test_the_four_harnesses_state_the_case_identically(self) -> None:
        """The case's §2 bullet and §6 test in `tests.md`, and its §2 bullet and §3 paragraph
        in `controlled_spec.md`, are one text across the harnesses but for the harness's name
        and the Fortran harnesses' sentence on the padded series name."""
        def slices(spec_id: str) -> tuple[list[str], list[str]]:
            tests = _tests_md(spec_id)
            row = tests[tests.index(f"- `test_id`: `{CASE}`"):]
            end = row.index("## 7.")
            nxt = row.find("- `test_id`:", 1)
            spec = (_HARNESS_DIR / spec_id / "controlled_spec.md").read_text(encoding="utf-8")
            paragraph = re.search(rf"^The `{CASE}` case calls[^\n]*$", spec, re.M).group(0)
            padding = PADDING.findall(paragraph)
            texts = [re.search(rf"^- `{CASE}` declares[^\n]*$", tests, re.M).group(0),
                     row[:nxt if 0 < nxt < end else end],
                     re.search(rf"^  - `{CASE}`: [^\n]*$", spec, re.M).group(0),
                     PADDING.sub("", paragraph)]
            # the `netcdf` harness's captures write no JSON, so its documents name the case
            # file's nesting where the others name the one the capture operations produce
            if spec_id == "harness_fortran_cpu_netcdf":
                texts = [t.replace(NETCDF_NESTING, JSON_NESTING) for t in texts]
            return [t.replace(spec_id, "H") for t in texts], padding

        reference, padding = slices(HARNESSES[0])
        self.assertEqual(len(padding), 1)
        for spec_id in HARNESSES[1:]:
            mine, my_padding = slices(spec_id)
            with self.subTest(spec_id=spec_id):
                self.assertEqual(mine, reference)
                self.assertEqual(my_padding, padding if "fortran" in spec_id else [])

    def test_the_documents_predicates_pass_the_compile_stage_gates(self) -> None:
        """The seven expressions of each `tests.md`, with the evidence entries the Compile
        contract ties to their reads, draw nothing from the predicate schema gate, the
        quantity gate, the infrastructure half of the drive gate or the case evaluation: a
        gate that came to refuse one would stop every harness Compile, which `tests.md`
        gives a leaf no way around. Kills: dropping an evidence entry from `ir()` (the gate
        refuses the reads of that group, the second half below)."""
        for spec_id in HARNESSES:
            with self.subTest(spec_id=spec_id):
                doc = ir(spec_id)
                cases = pe._cases_by_id(doc)
                context = pe.predicate_schema_context(doc)

                def findings(document: dict) -> list[str]:
                    contract = document["io_contract"]
                    return [
                        *pe.validate_primary_predicate_schema(
                            contract["predicates"], case_ids=set(cases), test_ids=[CASE],
                            schema=pe.snapshot_schema(document), cases=cases,
                            test_target_cases={CASE: [CASE]},
                            **pe.predicate_schema_context(document)),
                        *pe.quantity_violations(contract["tests"], contract["predicates"],
                                                context["reads"]),
                        *pe.drive_violations(document, kind="infrastructure")]
                self.assertEqual(findings(doc), [])
                self.assertEqual(set(pe.evaluate_case_inputs(doc)), {CASE, OTHER})
                for artifact in ("state_history", "restart_points", "step_series"):
                    cut = copy.deepcopy(doc)
                    entries = cut["io_contract"]["raw_requirements"]["required_evidence"]
                    entries[:] = [e for e in entries if e["artifact"] != artifact]
                    self.assertTrue(findings(cut), artifact)

    def test_the_sentinels_are_the_values_the_document_states(self) -> None:
        """Every literal `<name> = <value>` of the case's §2 bullet is the fixture's value."""
        tests = _tests_md(HARNESSES[0])
        bullet = re.search(rf"^- `{CASE}` declares[^\n]*$", tests, re.M).group(0)
        values = sentinels()
        initial = ("g_n", "g_every_a", "g_every_b", "g_every_p", "g_dt", "g_s", "g_r1")
        expected = ("steps_a", "steps_b", "steps_p", "steps_all", "g_s_a", "g_r1_p")
        for group, names in (("initial", initial), ("expected", expected)):
            for name in names:
                with self.subTest(name=name):
                    stated = re.search(rf"`{name} = ([^`]+)`", bullet).group(1)
                    self.assertEqual(yaml.safe_load(stated), values[group][name])


if __name__ == "__main__":
    unittest.main()
