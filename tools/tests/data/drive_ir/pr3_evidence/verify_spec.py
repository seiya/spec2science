#!/usr/bin/env python3
"""Issue #589 PR-3 authoring check, no LLM: a hand-converted IR of one node, against the
working-tree spec documents and a certified (or hand-cut) kernel. The IRs in `ir/` are that
evidence, a hand-written transcription of each `tests.md` and not an output of `Compile`: their
`algorithm` text is the certified IR's, which is why a retired word can remain there.
`verify_all.py` runs every node; the kernels are the operator's `workspace/pipelines/`.

  PYTHONPATH=. python3 <this file> <ir.yaml> --model <model.f90> [--dep <dep.f90>]...
      [--library <library_id>[=<dir>]]... [--flag=<flag>]... [--compared-only] [--gate-only] [--keep <dir>]
      [--mutate 'old=>new']... [--show-values] [--project <dir>]

1. `validate_compile_ir_dir` with the sample Project as root (`--project <dir>`: another Project
   root, e.g. the SCALE Project, whose spec documents, catalog and library declarations the gates
   and `--library` then read): Compile.static's gates, incl. the quantities / expected_outcome /
   history lines against the real tests.md.
2. Render the Fortran driver, compile harness + deps + model + runner with bounds checking, run
   every case, evaluate every predicate, print each test's status against expected_outcome and
   every record that did not hold, cut at 400 characters (`--show-values`: every record, whole).
   Exit 0 only when gates are clean and every status is expected.

`--mutate 'old=>new'` replaces text in the model before compiling (repeatable): the same run then
prints which tests the defect moved; exit status is then always 0 unless the build fails.

`--library <library_id>` (repeatable, issue #644 P2) links the `external library` of that
declaration (`spec/libraries/<library_id>.yaml`), located by the compiler's own search, as a site
entry with no directory is (`tools/external_library.py` `resolve`), and runs the binary with the
loader path the host composes. `--library <library_id>=<dir>` locates it in `<dir>` instead, as a
site entry whose `include` and `lib` are both `<dir>` is, and compiles with its include flags, so
an oracle adapter that reads the library's module files can stand in as the model (issue #680).

`--flag=<flag>` (repeatable) appends a compiler flag to every compile and the link, e.g. another
optimisation level. `--compared-only` runs only the cases an oracle is compared on
(`tools/oracle.py` `compared_cases`) and the continuations of those cases, and judges only the
tests whose cases are all among them, the restart test included: an oracle has no input guard, so
the original cannot run a refused case (issue #680).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO))
#: The sample Project (issue #705 PR-2): the spec documents, catalog and library declarations.
PROJECT = REPO / "sample"

from tools.tests.target_fixtures import provides_for
from tools import capture_store  # noqa: E402
from tools import external_library as xl  # noqa: E402
from tools import primary_evidence as pe  # noqa: E402
from tools import validate_pipeline_semantics as vps  # noqa: E402
from tools.checked_build import target_compiler  # noqa: E402
from tools import verdict_evaluator  # noqa: E402
from tools.backends.language.fortran import runner as f_runner  # noqa: E402
from tools.tests.target_fixtures import FORTRAN_CPU  # noqa: E402
from tools.tests.test_driver_render import CPU, DATA  # noqa: E402

FLAGS = ["-std=f2008", "-O2", "-fopenmp", "-fcheck=bounds"]


def module_name(path: Path) -> str:
    return path.name


def gate(ir: dict) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "x_001"
        d.mkdir()
        (d / "spec.ir.yaml").write_text(yaml.safe_dump(ir, sort_keys=False))
        node_key = ir["meta"]["node_key"]
        deps = [e["node_key"] for e in (ir.get("dependency") or {}).get("direct_deps") or []]
        (d / "dependency_graph.json").write_text(json.dumps({
            "node_key": node_key,
            "all_nodes": [{"node_key": k, "topo_level": 0} for k in deps]
            + [{"node_key": node_key, "topo_level": 1 if deps else 0}],
            "transitive_deps": [], "direct_deps": deps,
            "profiles": (ir.get("dependency") or {}).get("profiles") or []}))
        (d / "ir_meta.json").write_text(json.dumps({
            "attempt_count": 1, "verification_status": "pass", "last_fail_reason": None,
            "debug_mode": False, "context_isolated": True}))
        return vps.validate_compile_ir_dir(PROJECT, d)


def libraries(specs: list[str]) -> list:
    """Each declared library, `<library_id>` or `<library_id>=<dir>`: located by the compiler's
    own search, as a site entry naming no directory is, or in `<dir>`."""
    from types import SimpleNamespace

    from tools.execution_sites import SiteLibrary
    out = []
    for spec in specs:
        library_id, _, where = spec.partition("=")
        entry = SiteLibrary(include=where, lib=where) if where else SiteLibrary()
        site = SimpleNamespace(site_id="local", library=lambda _library_id, e=entry: e)
        out.append(xl.resolve(xl.load_declaration(PROJECT, library_id), site,
                              target_compiler(FORTRAN_CPU)))
    return out


def run(ir: dict, model: Path, deps: list[Path], work: Path, mutate: list[str],
        resolved: list | tuple = (), extra_flags: list[str] | tuple = (),
        only: set[str] | None = None) -> None:
    include, link = xl.make_values(target_compiler(FORTRAN_CPU), list(resolved))
    flags = [*FLAGS, *extra_flags, *include.split()]
    (work / "ir").mkdir()
    (work / "ir" / "spec.ir.yaml").write_text(yaml.safe_dump(ir))
    pe.write_case_inputs(ir, work / "ir")
    for parts in capture_store.run_dirs("json"):
        (work / "run").joinpath(*parts).mkdir(parents=True)
    sid = ir["meta"]["spec_id"]
    (work / "runner.f90").write_text(f_runner.render_runner(ir, sid, "harness_fortran_cpu", target=CPU,
        harness_provides=provides_for("harness_fortran_cpu")))
    objs = []
    sources = [DATA / "harness_fortran_cpu_model.f90", *deps, model]
    for i, src in enumerate(sources):
        text = src.read_text()
        if src == model:
            for m in mutate:
                old, new = m.split("=>", 1)
                if old not in text:
                    raise SystemExit(f"mutation target absent: {old!r}")
                text = text.replace(old, new)
        dst = work / f"s{i}_{src.name}"
        dst.write_text(text)
        obj = f"s{i}.o"
        r = subprocess.run(["gfortran", *flags, "-c", dst.name, "-o", obj], cwd=work,
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"compile {src}: {r.stderr[-3000:]}")
        objs.append(obj)
    r = subprocess.run(["gfortran", *flags, "-c", "runner.f90", "-o", "runner.o"], cwd=work,
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("compile runner: " + r.stderr[-3000:])
    r = subprocess.run(["gfortran", *flags, *objs, "runner.o", *link.split(), "-o", "bin"], cwd=work,
                       capture_output=True, text=True, check=False)
    if r.returncode:
        raise SystemExit("link: " + r.stderr[-3000:])
    # pass 1 over the base cases, then each continuation case in a process of its own
    # (issue #632, `test_driver_render.second_pass`)
    env = {**os.environ, "OMP_NUM_THREADS": "4"}
    env.update(xl.loader_env(FORTRAN_CPU, xl.library_dirs(list(resolved)), os.environ))
    ids = sorted(c["case_id"] for c in ir["case"]["test_case_set"]
                 if pe.RESTART_CASE_KEY not in c and (only is None or c["case_id"] in only))
    proc = subprocess.run([str(work / "bin"), "--cases", str(work / "ir" / "spec.ir.yaml"), *ids],
                          cwd=work / "run", capture_output=True, text=True, timeout=3000,
                          env=env)
    if proc.returncode:
        raise SystemExit(f"run rc={proc.returncode}: {proc.stderr[-3000:]}\n{proc.stdout[-1000:]}")
    from tools.tests.test_driver_render import second_pass
    _merged, problems = second_pass(ir, work / "run", work / "bin", env)
    if problems:
        raise SystemExit("second pass: " + "; ".join(problems))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ir")
    ap.add_argument("--model")
    ap.add_argument("--dep", action="append", default=[])
    ap.add_argument("--library", action="append", default=[])
    ap.add_argument("--flag", action="append", default=[])
    ap.add_argument("--compared-only", action="store_true")
    ap.add_argument("--gate-only", action="store_true")
    ap.add_argument("--keep")
    ap.add_argument("--mutate", action="append", default=[])
    ap.add_argument("--show-values", action="store_true", help="print every record's value")
    ap.add_argument("--project", help="the Project root the IR's node is in (default: sample/)")
    a = ap.parse_args()
    if a.project:
        global PROJECT
        PROJECT = Path(a.project).resolve()
    ir = yaml.safe_load(Path(a.ir).read_text())
    findings = gate(ir)
    print(f"GATE findings: {len(findings)}")
    for f in findings:
        print("  ", f[:900])
    if a.gate_only:
        return 1 if findings else 0
    base = Path(a.keep) if a.keep else Path(tempfile.mkdtemp())
    if a.keep and base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    only = None
    if a.compared_only:
        from tools.oracle import compared_cases
        only = set(compared_cases(ir))
        # The continuations of those cases: the second pass runs every continuation anyway,
        # and the restart test (base and continuation) is judged on the original too (issue
        # #709 F-1 left continuations out of `compared_cases`).
        only |= {cid for cid, (base, _k) in pe.continuation_cases(ir).items() if base in only}
    try:
        run(ir, Path(a.model).resolve(), [Path(d).resolve() for d in a.dep], base, a.mutate,
            libraries(a.library), a.flag, only)
        records = pe.evaluate_primary_predicates(ir, base / "run", output_format="json")
        verdict = verdict_evaluator.evaluate_verdict(ir["io_contract"]["tests"], records)
        expected = {t["test_id"]: t["expected_outcome"] for t in ir["io_contract"]["tests"]
                    if only is None or set(t["target_cases"]) <= only}
        got = {t["test_id"]: t["status"] for t in verdict["per_test"]}
        bad = {k: (got.get(k), v) for k, v in expected.items() if got.get(k) != v}
        whole = "" if only is not None else f"; self_verdict {verdict['self_verdict']}"
        print(f"TESTS: {len(expected)}; status != expected: {len(bad)}{whole}")
        for k, v in bad.items():
            print("  MOVED", k, v)
        for r in records:
            d = dict(r) if isinstance(r, dict) else r
            if only is not None and d.get("test_id") not in expected:
                continue
            if a.show_values or not d.get("satisfied", False):
                line = json.dumps({k: v for k, v in d.items() if k != "reads" and k != "expr"}, default=str)
                # `--show-values` prints the whole record, so its values can be read back.
                print("  rec", line if a.show_values else line[:400])
        if a.mutate:
            return 0
        return 1 if bad or findings else 0
    finally:
        if not a.keep:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
