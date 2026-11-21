#!/usr/bin/env python3
"""Issue #644 P6 V-b (unbilled, no LLM): format equivalence. ONE certified model source, built
against the `json` harness and against the `netcdf` harness, writes the same captures.

  PYTHONPATH=. python3 <this file> --binary-meta <fortran_cpu Build's binary_meta.json> \
      --netcdf-binary-meta <fortran_cpu_netcdf Build's binary_meta.json> [--keep <dir>]

The source is the closure R3 certified on `fortran_cpu` for `problem/shallow_water2d_rotating`
(`--binary-meta`: its Build's stamp, whose `closure_bindings` name every dependency's certified
model source, and whose `source_source_id` names the node's own model and driver). It is built
twice, outside `workspace/`, with the flags the conductor's Makefile gives (`-std=f2008 -O2
-fopenmp`):

- `json`: with the `harness_fortran_cpu` model the same stamp binds;
- `netcdf`: with the `harness_fortran_cpu_netcdf` model the `fortran_cpu_netcdf` Build of the same
  node binds (`--netcdf-binary-meta`), and the node's driver with the harness's name replaced.
  The script first checks that this text is the driver the host rendered for the
  `fortran_cpu_netcdf` Build, byte for byte: the driver differs between the targets in the
  harness's name alone.

Each binary runs every base case of the certified IR in one process with `OMP_NUM_THREADS=4`,
then each continuation case through the host's second pass (`test_driver_render.second_pass`,
the conductor's continuation writer, start check and merge). Every record of every case — the
initial and final records, each history sample, each restart point, the series — is read through
the store of the format that wrote it (`capture_store.store(<format>).read_record`), and the two
are compared member by member: the same member names, the same nesting and extents, and every
number the same binary64 (NaN equal to NaN). There is no tolerance: a difference is a finding.

Exit 0 when both runs had no problem (pass 1 exit status, every continuation merged), every case
holds a final record, every record of every case is equal and both stores hold the same records; 1
otherwise, with the first differences printed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(REPO))
#: The sample Project (issue #705 PR-2): the `workspace/` State.
PROJECT = REPO / "sample"

from tools import capture_store  # noqa: E402
from tools import primary_evidence as pe  # noqa: E402
from tools.tests import netcdf_harness  # noqa: E402
from tools.tests.test_driver_render import second_pass  # noqa: E402

FLAGS = ["-std=f2008", "-O2", "-fopenmp"]
JSON_H, NETCDF_H = "harness_fortran_cpu", "harness_fortran_cpu_netcdf"
NODE = "shallow_water2d_rotating"


def _stamp(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sources(meta: Path) -> tuple[list[Path], Path, Path, Path]:
    """The closure's model sources in dependency order (the harness excluded), the harness's
    model, the node's model and its driver, as the Build's stamp binds them."""
    d = _stamp(meta)
    bindings = d["dependency_check"]["closure_bindings"]
    harness = [PROJECT / b["model_source_ref"] for b in bindings
               if b["node_key"].startswith("infrastructure/")]
    deps = [PROJECT / b["model_source_ref"] for b in bindings
            if not b["node_key"].startswith("infrastructure/")]
    src = meta.parents[2] / "source" / d["source_source_id"] / "src"
    return deps, harness[0], src / f"{NODE}_model.f90", src / f"{NODE}_runner.f90"


def _order(src_dir: Path, names: list[str]) -> list[str]:
    """The Makefile's compile order of the closure (each object after the ones it needs)."""
    text = (src_dir / "Makefile").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("$(BINDIR)/$(BIN):"))
    objs = [w.split("/")[-1][:-2] for w in line.split(":", 1)[1].split("|")[0].split()]
    return [o for o in objs if o in names]


def _build(work: Path, files: dict[str, str], order: list[str], fmt: str) -> Path:
    work.mkdir(parents=True)
    extra = netcdf_harness.compile_flags() if fmt == "netcdf" else []
    for name, text in files.items():
        (work / f"{name}.f90").write_text(text, encoding="utf-8")
    for name in order:
        subprocess.run(["gfortran", *FLAGS, *extra, "-c", f"{name}.f90", "-o", f"{name}.o"],
                       cwd=work, check=True, capture_output=True, text=True)
    link = netcdf_harness.link_words() if fmt == "netcdf" else []
    subprocess.run(["gfortran", *FLAGS, *[f"{n}.o" for n in order], "-o", "bin", *link],
                   cwd=work, check=True, capture_output=True, text=True)
    return work / "bin"


def _run(ir: dict[str, Any], binary: Path, run_dir: Path, fmt: str) -> list[str]:
    (run_dir / "ir").mkdir(parents=True)
    (run_dir / "ir" / "spec.ir.yaml").write_text(yaml.safe_dump(ir))
    pe.write_case_inputs(ir, run_dir / "ir")
    shape = run_dir / "run"
    for parts in capture_store.run_dirs(fmt):
        shape.joinpath(*parts).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "OMP_NUM_THREADS": "4"}
    if fmt == "netcdf":
        env = netcdf_harness.run_env(env)
    ids = sorted(c["case_id"] for c in ir["case"]["test_case_set"]
                 if pe.RESTART_CASE_KEY not in c)
    proc = subprocess.run([str(binary), "--cases", str(run_dir / "ir" / "spec.ir.yaml"), *ids],
                          cwd=shape, capture_output=True, text=True, timeout=3600, env=env)
    problems = [] if proc.returncode == 0 else [f"{fmt}: rc={proc.returncode}: {proc.stderr[-800:]}"]
    _merged, more = second_pass(ir, shape, binary, env, fmt)
    return problems + [f"{fmt}: {p}" for p in more]


def _records(shape: Path, fmt: str, case_ids: list[str]) -> dict[tuple, Any]:
    """Every record of every case, keyed by (case, group, step), read through `fmt`'s store."""
    store = capture_store.store(fmt)
    snap = capture_store.snapshot_dir(shape)
    out: dict[tuple, Any] = {}
    for cid in case_ids:
        for group in (capture_store.INITIAL, capture_store.FINAL, capture_store.SERIES):
            if store.has_record(snap, group, cid):
                out[(cid, group, None)] = store.read_record(snap, group, cid)
        for step in store.history_steps(snap, cid):
            out[(cid, capture_store.HISTORY, step)] = store.read_record(
                snap, capture_store.HISTORY, cid, step)
        for step in store.restart_point_steps(snap, cid):
            out[(cid, capture_store.RESTART_POINT, step)] = store.read_record(
                snap, capture_store.RESTART_POINT, cid, step)
    return out


def _bits(x: float) -> bytes:
    return b"nan" if math.isnan(x) else struct.pack("<d", x)


def _diff(a: Any, b: Any, path: str, out: list[str]) -> int:
    """Differences between two record members; returns the count of numbers compared."""
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            out.append(f"{path}: members {sorted(set(a) ^ set(b))} on one side only")
        return sum(_diff(a[k], b[k], f"{path}.{k}", out) for k in sorted(set(a) & set(b)))
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: extent {len(a)} != {len(b)}")
            return 0
        return sum(_diff(x, y, f"{path}[{i}]", out) for i, (x, y) in enumerate(zip(a, b)))
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or isinstance(b, str):
        if a != b:
            out.append(f"{path}: {a!r} != {b!r}")
        return 0
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if _bits(float(a)) != _bits(float(b)):
            out.append(f"{path}: {a!r} != {b!r}")
        return 1
    out.append(f"{path}: {type(a).__name__} vs {type(b).__name__}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--binary-meta", required=True, type=Path)
    ap.add_argument("--netcdf-binary-meta", required=True, type=Path)
    ap.add_argument("--keep", type=Path)
    args = ap.parse_args()

    args.binary_meta = args.binary_meta.resolve()
    args.netcdf_binary_meta = args.netcdf_binary_meta.resolve()
    deps, json_harness, model, runner = _sources(args.binary_meta)
    _nc_deps, netcdf_harness_src, _nc_model, nc_runner = _sources(args.netcdf_binary_meta)
    runner_text = runner.read_text(encoding="utf-8")
    netcdf_runner_text = runner_text.replace(JSON_H, NETCDF_H)
    if netcdf_runner_text != nc_runner.read_text(encoding="utf-8"):
        print("the driver differs between the targets in more than the harness's name",
              file=sys.stderr)
        return 1
    stamp = _stamp(args.binary_meta)
    # The certified IR the source was generated from: the one whose output hash the source's
    # generate key binds (`binary_meta.source_ir_id` is no longer written, issue #643 PR-5).
    source_meta = _stamp(args.binary_meta.parents[2] / "source" / stamp["source_source_id"]
                         / "source_meta.json")
    ir_root = PROJECT / "workspace" / "ir" / stamp["node_key"].replace("/", "__").replace("@", "__")
    ir_path = next(m.parent for m in sorted(ir_root.glob("*/ir_meta.json"))
                   if _stamp(m).get("output_hash") == source_meta["derivation_inputs"]["ir"]
                   ) / "spec.ir.yaml"
    ir = yaml.safe_load(ir_path.read_text(encoding="utf-8"))
    case_ids = sorted(c["case_id"] for c in ir["case"]["test_case_set"])
    print(f"IR {ir_path.relative_to(REPO)}; {len(case_ids)} cases")

    physics = {p.stem: p.read_text(encoding="utf-8") for p in [*deps, model]}
    order = _order(runner.parent, [*physics, f"{JSON_H}_model", f"{NODE}_runner"])
    nc_order = [n.replace(f"{JSON_H}_model", f"{NETCDF_H}_model") for n in order]
    for p in [*deps, model, json_harness, netcdf_harness_src]:
        print(f"  {p.relative_to(REPO)}")

    tmp = Path(tempfile.mkdtemp(prefix="v_b_", dir=args.keep)) if args.keep else Path(
        tempfile.mkdtemp(prefix="v_b_"))
    try:
        records = {}
        run_problems: list[str] = []
        for fmt, harness_src, harness_name, drv, ordr in (
                ("json", json_harness, JSON_H, runner_text, order),
                ("netcdf", netcdf_harness_src, NETCDF_H, netcdf_runner_text, nc_order)):
            files = {**physics, f"{harness_name}_model": harness_src.read_text(encoding="utf-8"),
                     f"{NODE}_runner": drv}
            binary = _build(tmp / fmt / "build", files, ordr, fmt)
            problems = _run(ir, binary, tmp / fmt, fmt)
            run_problems += problems
            for p in problems:
                print(f"RUN {p}")
            records[fmt] = _records(tmp / fmt / "run", fmt, case_ids)
            print(f"{fmt}: {len(records[fmt])} records")
        diffs: list[str] = []
        keys_a, keys_b = set(records["json"]), set(records["netcdf"])
        for k in sorted(keys_a ^ keys_b, key=str):
            diffs.append(f"{k}: held by {'json' if k in keys_a else 'netcdf'} only")
        numbers = 0
        for k in sorted(keys_a & keys_b, key=str):
            numbers += _diff(records["json"][k], records["netcdf"][k], str(k), diffs)
        groups: dict[str, int] = {}
        for k in keys_a & keys_b:
            groups[k[1]] = groups.get(k[1], 0) + 1
        print(f"compared {len(keys_a & keys_b)} records {dict(sorted(groups.items()))}, "
              f"{numbers} numbers; {len(diffs)} differences")
        for line in diffs[:40]:
            print(f"DIFF {line}")
        # a case that failed on both sides is missing from both stores alike: a run problem
        # fails the comparison, and so does a case with no final record
        no_final = [c for c in case_ids if (c, capture_store.FINAL, None) not in keys_a]
        print(f"cases without a final record: {no_final}")
        return 0 if not diffs and not run_problems and not no_final else 1
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
