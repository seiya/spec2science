#!/usr/bin/env python3
"""Issue #644 P6 V-e (unbilled, no LLM): history selection on `shallow_water2d_rotating`.

  PYTHONPATH=. python3 <this file> --before <state_snapshots dir> --after <state_snapshots dir> \
      --work <new dir outside workspace/>

1. Size, before and after. `--before` is the capture directory of a Validate run certified before
   history selection (every history record the whole state: `t`, `h`, `hu`, `hv`, `z_b`, `f`,
   `ok`, `step`), `--after` that of R3's run of the same node (each record the variables the
   IR's `drive.history` samples at its step). Both are `json`. Printed per case and in total: the
   records, their bytes and their members.
2. A stride override: `tools/run_configuration.py` on the certified `fortran_cpu` binary, the base
   case `BASE` as certified (`control`) and with `history.every` changed (`override`). Every IR
   certified so far binds `h`, `hu` and `hv` to the one input `history.every`
   (`drive.history`), so the override moves the stride of the three together: no certified node
   has a variable with a stride of its own. Checked: each run's history steps are the multiples
   of its stride up to the final step, each record holds exactly the variables `drive.history`
   names, and at the steps both runs sample the records are equal (binary64); the two final
   captures are equal, so the stride is a sampling choice and not a change of the run.

Exit 0 when every check of 2 holds (1 is a measurement and does not fail).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from tools import capture_store  # noqa: E402
from format_equivalence import _diff  # noqa: E402

SPEC = "spec/problem/dynamics/shallow_water/shallow_water2d_rotating"
BASE = "swr_shear_n064"
STRIDE = 7
FAILURES: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("OK   " if ok else "FAIL ") + what)
    if not ok:
        FAILURES.append(what)


def sizes(snap: Path) -> dict[str, tuple[int, int, list[str]]]:
    """Per case: history records, their bytes, the members of the first."""
    out: dict[str, tuple[int, int, list[str]]] = {}
    for p in sorted((snap / "history").iterdir()):
        case = p.name.rsplit("__", 1)[0]
        n, b, members = out.get(case, (0, 0, []))
        if not members:
            members = sorted(json.loads(p.read_text(encoding="utf-8")))
        out[case] = (n + 1, b + p.stat().st_size, members)
    return out


def run(config: dict, out: Path) -> int:
    cfg = out.with_suffix(".yaml")
    cfg.write_text(yaml.safe_dump(config), encoding="utf-8")
    return subprocess.run([sys.executable, "tools/run_configuration.py", SPEC, "--target",
                           "fortran_cpu", "--config", str(cfg), "--out", str(out)],
                          cwd=REPO, capture_output=True, text=True).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--before", required=True, type=Path)
    ap.add_argument("--after", required=True, type=Path)
    ap.add_argument("--work", required=True, type=Path)
    args = ap.parse_args()

    before, after = sizes(args.before), sizes(args.after)
    print(f"{'case':32} {'records':>15} {'bytes before':>14} {'bytes after':>14}  members")
    for case in sorted(set(before) | set(after)):
        nb, bb, mb = before.get(case, (0, 0, []))
        na, ba, ma = after.get(case, (0, 0, []))
        print(f"{case:32} {nb:>7} -> {na:<5} {bb:>14} {ba:>14}  {mb} -> {ma}")
    tb, ta = sum(v[1] for v in before.values()), sum(v[1] for v in after.values())
    print(f"total: {sum(v[0] for v in before.values())} -> {sum(v[0] for v in after.values())} "
          f"records, {tb} -> {ta} bytes ({ta / tb:.3f})")

    work = args.work.resolve()
    work.mkdir(parents=True)
    control, override = work / "control", work / "override"
    check(run({"base_case": BASE}, control) == 0, "control: ok")
    check(run({"base_case": BASE, "inputs": {"history.every": STRIDE}}, override) == 0,
          f"override history.every = {STRIDE}: ok")
    rec = json.loads((control / "run_record.json").read_text(encoding="utf-8"))
    ir = yaml.safe_load((control / "spec.ir.yaml").read_text(encoding="utf-8"))
    sampled = sorted(ir["drive"]["history"])
    certified_every = next(c for c in ir["case"]["test_case_set"]
                           if c["case_id"] == BASE)["inputs"]["history"]["every"]
    store = capture_store.store("json")
    final_step = rec["final_step"]
    steps = {}
    for label, out, every in (("control", control, certified_every),
                              ("override", override, STRIDE)):
        snap = capture_store.snapshot_dir(out)
        steps[label] = store.history_steps(snap, BASE)
        want = list(range(every, final_step + 1, every))
        check(steps[label] == want, f"{label}: history steps are the multiples of {every} up to "
              f"{final_step} ({len(steps[label])} records)")
        members = {tuple(sorted(set(store.read_record(snap, capture_store.HISTORY, BASE, s))
                                - {"step", "t"})) for s in steps[label]}
        check(members == {tuple(sampled)}, f"{label}: every record holds {sampled} "
              f"(found {sorted(members)})")
    common = sorted(set(steps["control"]) & set(steps["override"]))
    diffs: list[str] = []
    numbers = 0
    cs, os_ = capture_store.snapshot_dir(control), capture_store.snapshot_dir(override)
    for s in common:
        numbers += _diff(store.read_record(cs, capture_store.HISTORY, BASE, s),
                         store.read_record(os_, capture_store.HISTORY, BASE, s), f"@{s}", diffs)
    numbers += _diff(store.read_record(cs, capture_store.FINAL, BASE),
                     store.read_record(os_, capture_store.FINAL, BASE), "final", diffs)
    check(bool(common) and not diffs, f"the records at the {len(common)} common steps "
          f"{common} and the final captures are equal ({numbers} numbers; {diffs[:5]})")
    print(f"{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
