"""The large-capture case of the harness suites (`l0_capture_large_pass`).

A generated `harness_fortran_cpu` once built each record by appending every value to the text of
the whole record, so a `256 x 256` history record took about a minute to write and a consuming
node's run passed its time bound. The case captures one `[384,384]` array, and every other case
writes it as a zero placeholder, so a writer whose cost grows faster than linearly in a record's
size runs past the run's time bound in the harness's own Validate. These rows pin what the
documents state of the case and that the host's own readers judge it: the predicates pass the
Compile-stage gates and hold on the record the case describes, and fail on a record with one
value changed. They do not time a writer: a generated harness is judged by its own run.
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tools import capture_store
from tools import primary_evidence as pe

ROOT = Path(__file__).resolve().parents[2]
_HARNESS_DIR = ROOT / "spec" / "infrastructure" / "infra" / "harness"
HARNESSES = ("harness_fortran_cpu", "harness_fortran_cpu_mpi", "harness_cpp_gpu",
             "harness_fortran_cpu_netcdf")
CASE = "l0_capture_large_pass"
N = 384


def _text(spec_id: str, name: str) -> str:
    return (_HARNESS_DIR / spec_id / name).read_text(encoding="utf-8")


def big() -> np.ndarray:
    """`big(i,j) = i + j/2`, `i, j = 1..384`, the leading index `i` first."""
    i = np.arange(1, N + 1, dtype=float)[:, None]
    j = np.arange(1, N + 1, dtype=float)[None, :]
    return i + 0.5 * j


def predicates(spec_id: str) -> list[tuple[str, float]]:
    """The case's predicate expressions in `tests.md`, each `<expr> == <value>`."""
    text = _text(spec_id, "tests.md")
    row = text[text.index(f"- `test_id`: `{CASE}`"):]
    row = row[:row.index("## 7.")]
    line = re.search(r"`predicate`[^\n]*", row).group(0)
    out = []
    for expr in re.findall(r"`([^`]+ == [^`]+)`", line):
        lhs, rhs = expr.rsplit(" == ", 1)
        out.append((lhs, float(rhs)))
    return out


def ir(spec_id: str) -> dict:
    return {
        "case": {"test_case_set": [{"case_id": CASE, "inputs": {}}]},
        "io_contract": {
            "raw_requirements": {"required_evidence": [
                {"artifact": "state_snapshots", "schema": {
                    "variables": [{"name": "big", "shape_expr": f"[{N},{N}]"}],
                    "time_variable": "t", "time_shape_expr": "scalar"}}]},
            "tests": [{"test_id": CASE, "target_cases": [CASE], "expected_outcome": "pass",
                       "quantities": ["capture_large"]}],
            "predicates": [
                {"test_id": CASE, "quantity": "capture_large", "target_cases": [CASE],
                 "expr": expr, "op": "eq", "value": value, "per_case": True}
                for expr, value in predicates(spec_id)],
        },
    }


class CaptureLargeDocumentTests(unittest.TestCase):
    def test_the_four_harnesses_state_the_case_identically(self) -> None:
        """The case's §2 bullets, its §3 paragraph, its test and the cost bullet of §3.2 are
        one text across the harnesses but for the harness's name."""
        def slices(spec_id: str) -> list[str]:
            spec = _text(spec_id, "controlled_spec.md")
            tests = _text(spec_id, "tests.md")
            row = tests[tests.index(f"- `test_id`: `{CASE}`"):]
            texts = [
                re.search(rf"^  - `{CASE}`: [^\n]*$", spec, re.M).group(0),
                re.search(rf"^The `{CASE}` case calls[^\n]*$", spec, re.M).group(0),
                re.search(r"^- \*\*Cost\.\*\*[^\n]*$", spec, re.M).group(0),
                re.search(rf"^- `{CASE}` declares[^\n]*$", tests, re.M).group(0),
                row[:row.index("## 7.")].rstrip("\n"),
            ]
            return [t.replace(spec_id, "H") for t in texts]

        reference = slices(HARNESSES[0])
        for spec_id in HARNESSES[1:]:
            with self.subTest(spec_id=spec_id):
                self.assertEqual(slices(spec_id), reference)

    def test_the_stated_values_are_the_array_s(self) -> None:
        """The largest element, the smallest and the sum the predicates compare with are those
        of the array the case states, and each is exact in binary64."""
        a = big()
        values = dict(predicates(HARNESSES[0]))
        self.assertEqual(values, {"max(final.big)": a.max(), "min(final.big)": a.min(),
                                  "sum(final.big)": a.sum()})
        self.assertEqual(a.sum(), 576 * 73920)
        self.assertLess(a.sum(), 2.0 ** 53)

    def test_the_predicates_pass_the_compile_stage_gates(self) -> None:
        for spec_id in HARNESSES:
            with self.subTest(spec_id=spec_id):
                doc = ir(spec_id)
                cases = pe._cases_by_id(doc)
                context = pe.predicate_schema_context(doc)
                contract = doc["io_contract"]
                findings = [
                    *pe.validate_primary_predicate_schema(
                        contract["predicates"], case_ids=set(cases), test_ids=[CASE],
                        schema=pe.snapshot_schema(doc), cases=cases,
                        test_target_cases={CASE: [CASE]}, **context),
                    *pe.quantity_violations(contract["tests"], contract["predicates"],
                                            context["reads"]),
                    *pe.drive_violations(doc, kind="infrastructure")]
                self.assertEqual(findings, [])


class CaptureLargeJudgedTests(unittest.TestCase):
    """The host's readers on a `json` record of the case, written as the layout states."""

    def _records(self, value: np.ndarray) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            for parts in capture_store.run_dirs("json"):
                run_dir.joinpath(*parts).mkdir(parents=True, exist_ok=True)
            snap = capture_store.snapshot_dir(run_dir)
            record = {"big": value.tolist(), "step": 1, "t": 0.0}
            (snap / f"{CASE}.json").write_text(json.dumps(record), encoding="utf-8")
            return pe.evaluate_primary_predicates(ir(HARNESSES[0]), run_dir,
                                                  output_format="json")

    def test_the_case_s_record_passes_every_predicate(self) -> None:
        records = self._records(big())
        self.assertEqual(len(records), 3)
        for record in records:
            self.assertEqual((record["kind"], record["satisfied"]), ("pass", True), record)

    def test_one_value_changed_fails_the_sum(self) -> None:
        changed = big()
        changed[100, 200] += 0.5
        self.assertEqual([r["satisfied"] for r in self._records(changed)], [True, True, False])

    def test_a_placeholder_fails_every_predicate(self) -> None:
        """A case that leaves `big` at its placeholder does not pass."""
        self.assertEqual([r["satisfied"] for r in self._records(np.zeros((N, N)))],
                         [False, False, False])


if __name__ == "__main__":
    unittest.main()
