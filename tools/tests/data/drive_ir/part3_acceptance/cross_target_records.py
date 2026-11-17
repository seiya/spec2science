#!/usr/bin/env python3
"""Issue #644 P6 V-d (unbilled, no LLM): cross-target agreement between `fortran_cpu` and
`fortran_cpu_netcdf` as the workflow recorded it.

  PYTHONPATH=. python3 <this file> [--stage s5_netcdf_rotating]

For each node of the R3 stage (`r3_runs.yaml`), the record the conductor wrote just before the
run recorded `pass` (`workspace/orchestrations/<id>/cross_target/<node_key_safe>.json`, written
for every status but `not_applicable`): its status, the comparands it read, and for each record
the number of comparisons and the largest value against its bound. Each run's evidence is read
through the store of the target that wrote it (`docs/RUNBOOK.md` §Certifying one node on several
targets). Exit 0 when every node of the stage has a record whose status is `agree` with
`fortran_cpu` among its comparands and at least one comparison; 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", default="s5_netcdf_rotating")
    args = ap.parse_args()
    stage = next(s for s in yaml.safe_load((HERE / "r3_runs.yaml").read_text())
                 if s["stage"] == args.stage)
    ok = True
    for node in stage["nodes"]:
        safe = node["node"].replace("/", "__").replace("@", "__")
        path = (REPO / "workspace" / "orchestrations" / node["orchestration_id"] / "cross_target"
                / f"{safe}.json")
        if not path.exists():
            print(f"FAIL {node['node']}: no record ({path.relative_to(REPO)})")
            ok = False
            continue
        rec = json.loads(path.read_text(encoding="utf-8"))
        evaluated = [e for r in rec.get("records", []) for e in r.get("evaluated", [])]
        comparands = [c["target_id"] for c in rec.get("comparands", [])]
        worst = max((e["value"] for e in evaluated), default=None)
        hold = rec["status"] == "agree" and "fortran_cpu" in comparands and bool(evaluated)
        print(f"{'OK  ' if hold else 'FAIL'} {node['node']}: {rec['status']}, comparands "
              f"{comparands}, {len(rec.get('records', []))} records, {len(evaluated)} "
              f"comparisons, largest value {worst}, absent "
              f"{[a['target_id'] for a in rec.get('absent', [])]}")
        ok &= hold
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
