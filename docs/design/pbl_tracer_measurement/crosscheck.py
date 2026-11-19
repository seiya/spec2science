"""Run the `pass` cases of pbl_tracer_diffusion_1d_column's tests.md through two builds of the offline
driver and print, per case, the cross-target agreement of `rhoq_t` and `flx` under the suite's own
normaliser (tests.md §5): a measure of how far two correct builds of the original drift apart.

  python3 crosscheck.py <driver> <comparand driver>

The cases are read from the one fenced YAML block of tests.md §2, so no second copy of them exists.
The judgments of tests.md §6 are not evaluated here: README.md §Operations runs them through the
host's predicate evaluator."""
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml

NODE = Path(__file__).resolve().parents[3] / "spec/component/pbl/diffusion/pbl_tracer_diffusion_1d_column"
ARRAYS = ["dens", "qtrc", "kh", "cz", "fz", "f2h_above", "f2h_below"]


def columns(block, case):
    ka = block["grid"]["ka"]
    k = np.arange(1, ka + 1)
    rules = dict(block["columns"])
    rules.update(case.get("columns", {}))
    out = {}
    for v in ARRAYS:
        col = np.full(ka, float(block["fill"][v]))
        rule = rules.get(v)
        if isinstance(rule, list):
            inner = (k >= 2) & (k <= ka - 1)
            col[inner] = rule[0] + rule[1] * (k[inner] - 2)
        elif rule is not None:
            col = np.array(rule["cells"], float)
        for o in case.get("overrides", []):
            if o["var"] == v:
                col[o["k"] - 1] = o["value"]
        out[v] = col
    return out


def run(driver, grid, case, cols):
    text = f"{grid['ka']} {grid['ks']} {grid['ke']}\n{case['dt']!r} {case['sflx_q']!r} {case['mass']!r}\n"
    text += "".join(" ".join(repr(float(x)) for x in cols[v]) + "\n" for v in ARRAYS)
    out = subprocess.run([driver], input=text, capture_output=True, text=True, check=True).stdout.split("\n")
    return [np.array([float(x) for x in line.split()]) for line in out[:2]]


def main():
    driver, comparand = sys.argv[1:3]
    text = (NODE / "tests.md").read_text(encoding="utf-8")
    block = yaml.safe_load(re.search(r"```yaml\n(.*?)```", text, re.S).group(1))
    worst = 0.0
    for case in block["cases"]:
        if case["expected"] != "pass":
            continue
        grid = dict(block["grid"])
        grid.update(case.get("grid", {}))
        cols = columns(block, case)
        k = np.arange(1, grid["ka"] + 1)
        interior = (k >= grid["ks"]) & (k <= grid["ke"])
        pairs = (k >= grid["ks"]) & (k <= grid["ke"] - 1)
        r_scale = np.max(np.abs(interior * cols["dens"] * cols["qtrc"])) / case["dt"]
        depth = np.sum(pairs * (np.roll(cols["fz"], -1) - cols["fz"]))
        a = run(driver, grid, case, cols)
        b = run(comparand, grid, case, cols)
        agree = []
        for q, q_ref, floor in ((a[0], b[0], r_scale), (a[1], b[1], r_scale * depth)):
            agree.append(np.max(np.abs(q - q_ref)) / max(np.max(np.abs(q)), np.max(np.abs(q_ref)), floor))
        worst = max(worst, *agree)
        print(f"{case['case_id']:26s} rhoq_t {agree[0]:.3e}  flx {agree[1]:.3e}")
    print(f"worst {worst:.3e}")


if __name__ == "__main__":
    main()
