#!/usr/bin/env python3
"""Issue #680 V-b (unbilled, no LLM): what the in-workflow oracle comparison reads of the two
microphysics nodes, against what the offline comparison `docs/design/kessler_measurement/compare.py
compare` reads.

  PYTHONPATH=. python3 <this file> --driver <dir>/O2/driver --alt-driver <dir>/O2alt/driver \\
      --work <scratch dir> [--jobs 12] [--only-table 1|2]

`--driver` / `--alt-driver` are the offline drivers `docs/design/kessler_measurement/Makefile`
builds (README §Operations); `--work` is a scratch directory this script fills (the oracle runs,
the mutant builds and their runs) and never a directory of the checkout. Needs the certified
pipelines of billed run R-O (`PIPELINES` below; main `e27b6b1c`), whose `bin_oracle/` binaries
it runs, and `gfortran` and `make` for the mutants. The measurement directory and the two nodes
are the SCALE Project's since issue #705 PR-3: set `KESSLER_MEASUREMENT=<that Project>/docs/design/
kessler_measurement`, and read every `docs/design/kessler_measurement/` path here as that copy.

Table 1. For each node, per (compared case, variable): the effective absolute tolerance each
comparison applies to the captured variable.
- offline: `compare.py compare` reads every variable its operation returns (`compare.RETURNS`), at
  the interior cells 2..7, per element: a difference beyond `max(1e-15, 1e-12*max(|q|, |q_ref|))`
  is a deviation. `evaporate` is read as `max(-pcsat, 0)` and `rhoe_t` as `rhoe_t*dt/(dens*L00)`;
  their tolerance is converted back into the captured variable's units (`evaporate =
  max(-pcsat, 0)*dens/vol`, `vol = 4/3*pi*dwatr*r_c**3`; `rhoe_t = x*dens*L00/dt`).
- workflow: `Conductor._shape_state_agreement` reads only the variables a cross-target predicate
  of the IR names in that case, with the bound `1e-12` on `maxabs(q - q_ref) / max(maxabs(q),
  maxabs(q_ref), 1)` over the whole column, halo cells included; its effective absolute
  tolerance is `1e-12*max(maxabs(q), maxabs(q_ref), 1)`.
Magnitudes are the R-O run's captures (`q`) and the oracle's captures (`q_ref`), re-taken here
from the certified `bin_oracle/` binaries on `tools.oracle.case_plan`. The offline tolerance
varies per cell; the table gives its smallest and largest over the cells, and the factor is the
workflow tolerance over each: above 1 the workflow is looser (on every cell when `factor_min`
is), below 1 tighter (`evaporate` and `rhoe_t`, where the offline transforms loosen the
captured variable's tolerance on purpose).
The last column is the workflow's expression evaluated on the R-O run against the oracle over
its bound `1e-12`, for every row, named or not (an open question of issue #680: whether `evaporate` and `rhoe_t`
would hold `1e-12` under the default normaliser where no judgment names them). The rows the
offline comparison reads and the workflow does not, or reads looser by a factor above 1, are
listed after the table.

Table 2. The seeded variants of `docs/design/kessler_measurement/README.md` §"What the suites do
not separate", seeded into the CERTIFIED generated model sources of the R-O pipelines (the
independent kernel they were first measured with is not kept): each copied to `--work`,
mutated, built with that pipeline's own `Makefile`, runner and harness (and, for Kessler, the
saturation model of its closure, mutated where the variant is in the saturation adjustment,
which the Kessler model calls through its dependency), and run on every case of the IR. Per
variant and node: (a) `Conductor._shape_state_agreement(ir, <mutant run>, "oracle", <oracle
captures>, "json")`: its status and record count, and the (case, variable) pairs beyond the
bound, counted over a copy of the IR whose predicates name one case each (a `per_case`
predicate stops at its first failing case); (b) `compare.py compare`:
exit code and rows beyond its tolerance; (c) the node's own suite, `Conductor._shape_verdict`
(the IR's predicates without a comparand): self verdict and the tests whose status differs
from the unmutated build's. The row `none` is the unmutated build, the control.

Exit 0 when both controls read `agree`, exit 0 on `compare`, and `pass` on their own suite; 1
otherwise. The figures are a record; nothing is judged from them here.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(REPO))
#: The sample Project (issue #705 PR-2): the spec documents and the `workspace/` State.
PROJECT = REPO / "sample"
#: Where the two nodes' `spec` files are read (their oracle declarations and `tests.md`): they left
#: the sample by issue #705 PR-3, and the suite's fixture holds them at the bytes R-O certified.
#: R-O's State (its pipelines and IRs) stays the sample's, read through `PROJECT`.
SPEC_ROOT = REPO / "tools" / "tests" / "data" / "scale_project"

from tools import capture_store  # noqa: E402
from tools import oracle as oracle_mod  # noqa: E402
from tools.primary_evidence import (  # noqa: E402
    ComparandEvidence,
    cross_target_predicates,
    evaluate_primary_predicates,
)
from tools.workflow_conductor import Conductor  # noqa: E402

#: The measurement directory left for the SCALE Project with its nodes (issue #705 PR-3): name
#: that Project's copy in `KESSLER_MEASUREMENT` (its `docs/design/kessler_measurement`).
COMPARE = Path(os.environ.get("KESSLER_MEASUREMENT")
               or REPO / "docs" / "design" / "kessler_measurement") / "compare.py"
sys.path.insert(0, str(COMPARE.parent))
import compare as offline  # noqa: E402

SAT = "saturation"
KES = "kessler"
#: The certified pipelines of R-O (orchestration of 2026-11-19, main `e27b6b1c`).
PIPELINES = {
    SAT: ("workspace/pipelines/component__microphysics_saturation_liquid_1d_column__0.1.0/"
          "fortran_cpu/microphysics-saturation-liquid-1d-column_20261119_001",
          "microphysics_saturation_liquid_1d_column"),
    KES: ("workspace/pipelines/component__microphysics_kessler_warm_rain_1d_column__0.1.1/"
          "fortran_cpu/microphysics-kessler-warm-rain-1d-column_20261119_001",
          "microphysics_kessler_warm_rain_1d_column"),
}
SAT_MODEL = "microphysics_saturation_liquid_1d_column_model.f90"
KES_MODEL = "microphysics_kessler_warm_rain_1d_column_model.f90"
REL, FLOOR, WF_BOUND = 1e-12, 1e-15, 1e-12
CT_EXPR = re.compile(r"maxabs\(final\.(\w+) - comparand\.final\.\1\) / max\(maxabs\(final\.\1\), "
                     r"maxabs\(comparand\.final\.\1\), 1(?:\.0)?\)")

#: name -> (file the edit is in, [(old, new)]). Each `old` occurs once in the certified source.
_VIRTUAL_TEMP = ("        temp(k) = t_v\n",
                 "        if (qc0 == 0.0_dp) then\n          temp(k) = temp0\n        else\n"
                 "          temp(k) = t_v\n        end if\n")
VARIANTS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "none": (SAT_MODEL, []),
    "threshold 1.5e-6": (SAT_MODEL, [("newton_tol = 1.0e-6_dp", "newton_tol = 1.5e-6_dp")]),
    "threshold 2e-6": (SAT_MODEL, [("newton_tol = 1.0e-6_dp", "newton_tol = 2.0e-6_dp")]),
    "threshold 3e-6": (SAT_MODEL, [("newton_tol = 1.0e-6_dp", "newton_tol = 3.0e-6_dp")]),
    "threshold 1e-5": (SAT_MODEL, [("newton_tol = 1.0e-6_dp", "newton_tol = 1.0e-5_dp")]),
    "limit 150": (SAT_MODEL, [("max_newton_iter = 100", "max_newton_iter = 150")]),
    "limit 1000": (SAT_MODEL, [("max_newton_iter = 100", "max_newton_iter = 1000")]),
    "virtual keeps temp at qc=0": (SAT_MODEL, [_VIRTUAL_TEMP]),
    # Kessler's limited update (`adj_s04`): each limited result clipped to non-negative.
    "clip limited result >= 0": (KES_MODEL, [
        ("qv(k) = qvo + qdot_v*dt", "qv(k) = max(qvo + qdot_v*dt, 0.0_dp)"),
        ("qc(k) = qco + qdot_c*dt", "qc(k) = max(qco + qdot_c*dt, 0.0_dp)"),
        ("qr(k) = qro + qdot_r*dt", "qr(k) = max(qro + qdot_r*dt, 0.0_dp)")]),
}


class Node:
    def __init__(self, name: str) -> None:
        self.name = name
        rel, self.stem = PIPELINES[name]
        self.pipeline = PROJECT / rel
        lineage = json.loads((self.pipeline / "lineage.json").read_text(encoding="utf-8"))
        self.spec_ref = lineage["spec_ref"]
        self.node_key = lineage["node_key"]
        self.ir_spec = PROJECT / lineage["ir_ref"] / "spec.ir.yaml"
        self.ir = yaml.safe_load(self.ir_spec.read_text(encoding="utf-8"))
        self.src = self.pipeline / "source" / lineage["source_id"] / "src"
        self.bin_dir = self.pipeline / "binary" / lineage["binary_id"]
        safe = self.node_key.replace("/", "__").replace("@", "__")
        self.run_dir = self.pipeline / "runs" / lineage["run_id"] / safe
        meta = json.loads((self.bin_dir / "binary_meta.json").read_text(encoding="utf-8"))
        self.closure = {b["node_key"].split("/")[1].split("@")[0]: PROJECT / b["model_source_ref"]
                        for b in meta["dependency_check"]["closure_bindings"]}
        self.exe = f"{self.stem}_runner"
        self.cases = [c["case_id"] for c in self.ir["case"]["test_case_set"]]


def run_cases(binary: Path, ir_spec: Path, cases: list[str], run_dir: Path) -> None:
    """Run `binary --cases <ir> <cases>` serially as Validate.execute's reference run does."""
    for part in ("initial", "history", "restart_point", "series"):
        (run_dir / "raw" / "state_snapshots" / part).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "OMP_NUM_THREADS": "1"}
    done = subprocess.run([str(binary), "--cases", str(ir_spec), *cases], cwd=run_dir, env=env,
                          capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError(f"{binary} exited {done.returncode}: {done.stderr[-600:]}")


def oracle_captures(node: Node, work: Path) -> Path:
    """The oracle's captures of every compared case, merged as `_compare_with_oracle` does."""
    oracle = oracle_mod.load(SPEC_ROOT, node.spec_ref, "fortran")
    plan = oracle_mod.case_plan(node.ir, oracle)
    root = work / node.name / "oracle"
    if root.exists():
        shutil.rmtree(root)
    merged = root / "merged"
    capture_store.snapshot_dir(merged).mkdir(parents=True)
    store = capture_store.store("json")
    for build_id, cases in plan.items():
        if not cases:
            continue
        run_dir = root / build_id
        run_cases(node.bin_dir / "bin_oracle" / build_id / node.exe, node.ir_spec, cases, run_dir)
        for case_id in cases:
            store.move_case(capture_store.snapshot_dir(run_dir),
                            capture_store.snapshot_dir(merged), case_id)
    return merged


def final(run_dir: Path, case_id: str) -> dict:
    return json.loads((capture_store.snapshot_dir(run_dir) / f"{case_id}.json")
                      .read_text(encoding="utf-8"))


def maxabs(values) -> float:
    return max(abs(v) for v in values)


# ---------------------------------------------------------------- table 1
def workflow_reads(ir: dict) -> dict[tuple[str, str], float]:
    """(case, variable) -> bound, for every cross-target predicate of the IR."""
    out: dict[tuple[str, str], float] = {}
    for pred in cross_target_predicates(ir):
        m = CT_EXPR.fullmatch(pred["expr"])
        if not m or pred.get("op") != "le":
            raise SystemExit(f"unexpected cross-target predicate: {pred}")
        cases = [pred["case"]] if "case" in pred else pred["target_cases"]
        for case_id in cases:
            out[(case_id, m.group(1))] = float(pred["value"])
    return out


def offline_tolerance(suite: dict, case: dict, var: str, got: dict, ref: dict) -> list[float]:
    """Per interior cell, the absolute tolerance `compare.py compare` applies, in the captured
    variable's own units."""
    grid = suite["grid"]
    cells = range(grid["ks"] - 1, grid["ke"])
    c = offline.case_inputs(suite, case)["constants"]
    if var == "evaporate":
        vol = 4.0 / 3.0 * math.pi * c["dwatr"] * 8.0e-6 ** 3
        return [max(FLOOR, REL * max(max(-got["pcsat"][k], 0.0), max(-ref["pcsat"][k], 0.0)))
                * ref["dens"][k] / vol for k in cells]
    if var == "rhoe_t":
        l00 = c["lhv0"] - (c["cpvap"] - c["cl"]) * c["tem00"]
        s = [case["dt"] / (ref["dens"][k] * l00) for k in range(len(ref["dens"]))]
        return [max(FLOOR, REL * max(abs(got[var][k]) * s[k], abs(ref[var][k]) * s[k])) / s[k]
                for k in cells]
    return [max(FLOOR, REL * max(abs(got[var][k]), abs(ref[var][k]))) for k in cells]


def table1(node: Node, merged: Path) -> list[dict]:
    suite = offline.load_suite(SPEC_ROOT / node.spec_ref / "tests.md", node.name)
    reads = workflow_reads(node.ir)
    rows = []
    for case in suite["cases"]:
        if offline.refused(case):
            continue
        cid = case["case_id"]
        got, ref = final(node.run_dir, cid), final(merged, cid)
        names = list(offline.RETURNS[case["operation"]])
        names += sorted({v for (c, v) in reads if c == cid} - set(names))
        for var in names:
            row = {"case": cid, "var": var}
            if var in offline.RETURNS[case["operation"]]:
                tol = offline_tolerance(suite, case, var, got, ref)
                row["off_min"], row["off_max"] = min(tol), max(tol)
            # What the workflow's expression gives on the R-O run, named or not, over its bound.
            row["ratio"] = (maxabs([x - y for x, y in zip(got[var], ref[var])])
                            / max(maxabs(got[var]), maxabs(ref[var]), 1.0) / WF_BOUND)
            if (cid, var) in reads:
                row["wf"] = reads[(cid, var)] * max(maxabs(got[var]), maxabs(ref[var]), 1.0)
            if "wf" in row and "off_min" in row:
                row["f_min"], row["f_max"] = row["wf"] / row["off_max"], row["wf"] / row["off_min"]
            rows.append(row)
    return rows


def print_table1(node: Node, rows: list[dict]) -> None:
    def g(x):
        return "-" if x is None else f"{x:.1e}"
    print(f"\n### Table 1: {node.node_key}\n")
    print("| case | variable | offline tol (min..max) | workflow tol | factor (min..max) "
          "| R-O value / 1e-12 |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        off = f"{g(r.get('off_min'))}..{g(r.get('off_max'))}" if "off_min" in r else "not read"
        wf = g(r["wf"]) if "wf" in r else "not read"
        fac = f"{g(r.get('f_min'))}..{g(r.get('f_max'))}" if "f_min" in r else "-"
        print(f"| {r['case']} | {r['var']} | {off} | {wf} | {fac} | {g(r['ratio'])} |")
    print("\nBy variable (factor over the rows both read):\n")
    print("| variable | offline rows | workflow reads | factor_min over rows | factor_max over rows |")
    print("|---|---:|---:|---|---|")
    for var in dict.fromkeys(r["var"] for r in rows):
        mine = [r for r in rows if r["var"] == var]
        both = [r for r in mine if "f_min" in r]
        fmin = (f"{g(min(r['f_min'] for r in both))}..{g(max(r['f_min'] for r in both))}"
                if both else "-")
        fmax = (f"{g(min(r['f_max'] for r in both))}..{g(max(r['f_max'] for r in both))}"
                if both else "-")
        print(f"| {var} | {sum(1 for r in mine if 'off_min' in r)} | "
              f"{sum(1 for r in mine if 'wf' in r)} | {fmin} | {fmax} |")
    unread = [r for r in rows if "off_min" in r and "wf" not in r]
    looser = [r for r in rows if "f_min" in r and r["f_max"] > 1.0]
    print(f"\nread offline, not by the workflow: {len(unread)} of "
          f"{sum(1 for r in rows if 'off_min' in r)} offline rows")
    by_var: dict[str, list[str]] = {}
    for r in unread:
        by_var.setdefault(r["var"], []).append(r["case"])
    for var, cases in by_var.items():
        worst = max(r["ratio"] for r in unread if r["var"] == var)
        print(f"  {var}: {len(cases)} cases (the workflow's expression on R-O, were it named: "
              f"at most {worst:.1e} of its bound): {', '.join(cases)}")
    print(f"read by both, the workflow looser on some cell (factor_max > 1): {len(looser)}; on "
          f"every cell (factor_min > 1): {sum(1 for r in looser if r['f_min'] > 1.0)}")
    if looser:
        top = sorted(looser, key=lambda r: -r["f_min"])
        print("  largest factor_min: " + "; ".join(
            f"{r['case']}/{r['var']} {r['f_min']:.1e}" for r in top[:8]))
    extra = [r for r in rows if "wf" in r and "off_min" not in r]
    if extra:
        print("read by the workflow only: " + ", ".join(f"{r['case']}/{r['var']}" for r in extra))


# ---------------------------------------------------------------- table 2
def per_case_split(ir: dict) -> dict:
    """A copy of `ir` whose cross-target predicates each name one case."""
    out = json.loads(json.dumps(ir))
    preds = []
    for pred in out["io_contract"]["predicates"]:
        if pred.get("quantity") == "cross_target_state_agreement" and pred.get("per_case"):
            base = {k: v for k, v in pred.items() if k != "per_case"}
            preds.extend({**base, "case": c} for c in pred["target_cases"])
        else:
            preds.append(pred)
    out["io_contract"]["predicates"] = preds
    return out


def build_mutant(node: Node, variant: str, work: Path) -> Path:
    """The node's certified model, its closure's models and runner, with the variant's edits,
    built by the pipeline's own Makefile into `work`; returns the binary."""
    target, edits = VARIANTS[variant]
    root = work / node.name / "mutants" / re.sub(r"[^a-z0-9.]+", "_", variant.lower())
    if root.exists():
        shutil.rmtree(root)
    src, obj, bindir = root / "src", root / "obj", root / "bin"
    shutil.copytree(node.src, src, ignore=shutil.ignore_patterns("command_log.jsonl"))
    obj.mkdir()
    for path in node.closure.values():
        shutil.copy(path, obj / path.name)
    model = next((p for p in (src / target, obj / target) if p.exists()), None)
    if model is None:
        raise SystemExit(f"{variant}: {target} is in neither {node.name}'s source nor closure")
    text = model.read_text(encoding="utf-8")
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"{variant}: {old!r} occurs {text.count(old)} times in {model}")
        text = text.replace(old, new)
    model.write_text(text, encoding="utf-8")
    done = subprocess.run(["make", "-C", str(src), "LIBINC=", "LIBS=", f"OBJDIR={obj}",
                           f"BINDIR={bindir}", f"BIN={node.exe}"],
                          capture_output=True, text=True, check=False)
    if done.returncode:
        raise SystemExit(f"{variant} {node.name}: make failed: {done.stderr[-800:]}")
    return bindir / node.exe


def applies(node: Node, variant: str) -> bool:
    target = VARIANTS[variant][0]
    return (node.src / target).exists() or any(p.name == target for p in node.closure.values())


def measure(node: Node, variant: str, work: Path, merged: Path,
            driver: str, alt_driver: str) -> dict:
    binary = build_mutant(node, variant, work)
    run_dir = binary.parent.parent / "run"
    run_cases(binary, node.ir_spec, node.cases, run_dir)
    agreement = Conductor._shape_state_agreement(node.ir, run_dir, "oracle", merged, "json")
    # A `per_case` predicate stops at its first failing case, so the failing count reads a
    # copy of the IR with each cross-target predicate split into one `case:` predicate per case.
    comparand = ComparandEvidence("oracle", "", "", merged, "json")
    records = evaluate_primary_predicates(per_case_split(node.ir), run_dir, output_format="json",
                                          comparands=[comparand], cross_target_only=True)
    failing = sorted({(ev.get("case"), CT_EXPR.fullmatch(r["expr"]).group(1))
                      for r in records for ev in r.get("evaluated") or []
                      if not ev.get("satisfied")})
    compared = len({(ev.get("case"), CT_EXPR.fullmatch(r["expr"]).group(1))
                    for r in records for ev in r.get("evaluated") or []})
    done = subprocess.run([sys.executable, str(COMPARE), "compare", node.name, "--driver", driver,
                           "--alt-driver", alt_driver, "--run-dir", str(run_dir)],
                          capture_output=True, text=True, check=False)
    beyond = []
    for line in done.stdout.splitlines():
        parts = line.split()
        if (len(parts) == 4 and parts[0] in {v for vs in offline.RETURNS.values() for v in vs}
                and float(parts[2]) > REL):
            beyond.append(f"{parts[1]}/{parts[0]} {float(parts[2]):.1e}")
    verdict = Conductor._shape_verdict(node.ir, run_dir, "json")
    return {"variant": variant, "node": node.name, "status": agreement["status"],
            "records": agreement.get("records_count"), "failing": failing,
            "compared": compared,
            "compare_rc": done.returncode, "beyond": beyond,
            "compare_err": done.stderr[-400:] if done.returncode not in (0, 1) else "",
            "self_verdict": (verdict or {}).get("self_verdict"),
            "per_test": (verdict or {}).get("per_test", {})}


def print_table2(results: list[dict]) -> bool:
    control = {r["node"]: r for r in results if r["variant"] == "none"}
    print("\n### Table 2: seeded variants\n")
    print("| variant | node | (a) oracle comparison | (b) compare.py | (c) own suite |")
    print("|---|---|---|---|---|")
    for r in results:
        base = control[r["node"]]["per_test"]
        changed = sorted(t for t, s in r["per_test"].items() if base.get(t) != s)
        a = (f"{r['status']} ({r['records']} records; {len(r['failing'])} of {r['compared']} "
             f"(case, variable) beyond)")
        b = f"exit {r['compare_rc']}, {len(r['beyond'])} rows beyond"
        moved = ", ".join(f"{t}={r['per_test'][t]}" for t in changed)
        c = f"{r['self_verdict']}" + (f"; changed: {moved}" if changed else "")
        print(f"| {r['variant']} | {r['node']} | {a} | {b} | {c} |")
    print()
    for r in results:
        if r["failing"]:
            print(f"(a) {r['variant']} / {r['node']}: failing (case, variable): "
                  + ", ".join(f"{c}/{v}" for c, v in r["failing"]))
        if r["beyond"]:
            print(f"(b) {r['variant']} / {r['node']}: beyond: " + "; ".join(r["beyond"]))
        if r["compare_err"]:
            print(f"(b) {r['variant']} / {r['node']}: compare.py error: {r['compare_err']}")
    # compare.py exits 2 on an error: a variant whose comparison errored was not measured.
    return (all(c["status"] == "agree" and c["compare_rc"] == 0 and c["self_verdict"] == "pass"
                for c in control.values())
            and all(r["compare_rc"] in (0, 1) for r in results))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--driver", required=True)
    ap.add_argument("--alt-driver", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--only-table", choices=("1", "2"))
    args = ap.parse_args()
    work = Path(args.work).resolve()
    if work == REPO or REPO in work.parents:
        raise SystemExit("--work must be outside the checkout")
    nodes = {n: Node(n) for n in (SAT, KES)}
    merged = {n: oracle_captures(node, work) for n, node in nodes.items()}
    ok = True
    for n, node in nodes.items():
        base = Conductor._shape_state_agreement(node.ir, node.run_dir, "oracle", merged[n], "json")
        worst = base.get("worst") or [{}]
        print(f"R-O run {node.node_key} against the re-taken oracle captures: {base['status']}, "
              f"{base.get('records_count')} records, worst {worst[0].get('value')}")
        ok &= base["status"] == "agree"
    if args.only_table != "2":
        for n, node in nodes.items():
            print_table1(node, table1(node, merged[n]))
    if args.only_table != "1":
        jobs = [(node, v) for v in VARIANTS for node in nodes.values() if applies(node, v)]
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(lambda j: measure(j[0], j[1], work, merged[j[0].name],
                                                      args.driver, args.alt_driver), jobs))
        ok &= print_table2(results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
