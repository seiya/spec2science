#!/usr/bin/env python3
"""Issue #644 P6 V-a, LAPACK half (unbilled, no LLM): P2's seeded defects
(`pr3_evidence/dense_solve_lu_mutants.yaml`, pinned on the hand model by
`tools/tests/test_dense_solve_lu_sample.py`) against the GENERATED `dense_solve_lu@0.1.0` model
R3 certified (orchestration `orch_20261117T054441Z_4b86b10c`), judged on the IR that run
certified.

  PYTHONPATH=. python3 <this file> --model <.../src/dense_solve_lu_model.f90> --ir <.../spec.ir.yaml>

The model is built against the hand harness model and the host-rendered driver and run on every
case (`pr3_evidence/verify_spec.py` `run`, the bench P2's rows use), and each test's status is
evaluated by the host evaluator over the certified IR. The mutants' patches replace text of the
hand model; `MUTANTS` below re-expresses each on the generated text (each substituted text must
occur exactly once) and expects the tests the hand row expects to move. They are re-expressions
written by hand: what is shown is that each written-down breakage is caught, not that the
generated model has no other one. `library-never-reached` moves no test; it is judged, as on the
hand model, by the post-build symbol check (`external_library.symbol_check`).

The substitutions are bound to one source and one IR: each is refused unless its sha256 is the one
below. Exit 0 when the unmutated model has every test at its expected outcome and every mutant
moves exactly the tests it names (or, for the symbol row, is refused as `library_declared_unused`).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(REPO))

from tools import external_library as xl  # noqa: E402
from tools import primary_evidence as pe  # noqa: E402
from tools import verdict_evaluator  # noqa: E402
from tools.tests.target_fixtures import FORTRAN_CPU  # noqa: E402

EVIDENCE = HERE.parent / "pr3_evidence"
HAND = yaml.safe_load((EVIDENCE / "dense_solve_lu_mutants.yaml").read_text(encoding="utf-8"))
MODEL_SHA256 = "63bc91e16871502dc6f09ab7adf9a23b3f5a27668977779870225f45312caf35"
IR_SHA256 = "833f53ae9ef7e4568a853a02be653d886ff857f7fac9704f2d669a3cb9322dc1"

CALL = "    call dgesv(n, 1, a_work, n, ipiv, b_work, n, info)\n"
INTERFACE = """  interface
    subroutine dgesv(n, nrhs, a, lda, ipiv, b, ldb, info)
      import :: dp
      implicit none
      integer, intent(in) :: n
      integer, intent(in) :: nrhs
      integer, intent(in) :: lda
      real(dp), intent(inout) :: a(lda, n)
      integer, intent(out) :: ipiv(n)
      integer, intent(in) :: ldb
      real(dp), intent(inout) :: b(ldb, nrhs)
      integer, intent(out) :: info
    end subroutine dgesv
  end interface
"""
# the hand row's own elimination, on the generated names (lu -> a_work, rhs -> b_work)
_hand_elimination = HAND["library-never-reached"]["patches"][2]["new"]
ELIMINATION = _hand_elimination.replace("lu(", "a_work(").replace("rhs(", "b_work(")

MUTANTS: dict[str, list[tuple[str, str]]] = {
    "transposed-matrix": [("    a_work = a\n", "    a_work = transpose(a)\n")],
    "ignored-status": [("    solved = (info == 0)\n", "    solved = .true.\n")],
    "status-compared-with-order": [("    solved = (info == 0)\n", "    solved = (info /= n)\n")],
    "wrong-leading-dimension": [(
        "    allocate(a_work(n, n), b_work(n, 1), ipiv(n))\n    a_work = a\n",
        "    allocate(a_work(n + 1, n), b_work(n, 1), ipiv(n))\n    a_work = 0.0_dp\n"
        "    a_work(1:n, :) = a\n")],
    "inputs-overwritten": [
        ("      real(dp), intent(inout) :: a(lda, n)\n", "      real(dp), intent(in) :: a(lda, n)\n"),
        ("      real(dp), intent(inout) :: b(ldb, nrhs)\n",
         "      real(dp), intent(in) :: b(ldb, nrhs)\n"),
        (CALL, "    call dgesv(n, 1, a, n, ipiv, b, n, info)\n"),
        ("      x = b_work(:, 1)\n", "      x = b\n")],
    "library-never-reached": [
        (INTERFACE, ""),
        ("    integer :: info\n",
         "    integer :: info\n    integer :: i, k, p\n    real(dp) :: f\n    real(dp) :: row(n)\n"),
        (CALL, ELIMINATION)],
}


def _apply(text: str, subs: list[tuple[str, str]]) -> str:
    for old, new in subs:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


def _load_bench():
    spec = importlib.util.spec_from_file_location("pr3_verify_spec", EVIDENCE / "verify_spec.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--ir", required=True, type=Path)
    args = ap.parse_args()
    for path, pinned in ((args.model, MODEL_SHA256), (args.ir, IR_SHA256)):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != pinned:
            print(f"{path}: sha256 {digest}, not the file the substitutions name", file=sys.stderr)
            return 2
    assert set(MUTANTS) == set(HAND), set(MUTANTS) ^ set(HAND)
    bench = _load_bench()
    ir = yaml.safe_load(args.ir.read_text(encoding="utf-8"))
    resolved = bench.libraries(["lapack"])
    expected = {t["test_id"]: t["expected_outcome"] for t in ir["io_contract"]["tests"]}
    source = args.model.read_text(encoding="utf-8")
    ok = True

    def moved(text: str) -> set[str]:
        with tempfile.TemporaryDirectory() as td:
            model = Path(td) / args.model.name
            model.write_text(text, encoding="utf-8")
            work = Path(td) / "w"
            work.mkdir()
            bench.run(ir, model, [], work, [], resolved)
            records = pe.evaluate_primary_predicates(ir, work / "run", output_format="json")
        verdict = verdict_evaluator.evaluate_verdict(ir["io_contract"]["tests"], records)
        got = {t["test_id"]: t["status"] for t in verdict["per_test"]}
        return {tid for tid, outcome in expected.items() if got.get(tid) != outcome}

    def symbol_violation(text: str) -> str | None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "model.f90").write_text(text, encoding="utf-8")
            subprocess.run(["gfortran", *bench.FLAGS, "-c", "model.f90", "-o", "model.o"],
                           cwd=td, check=True, capture_output=True)
            _record, violation = xl.symbol_check(
                FORTRAN_CPU, [Path(td) / "model.o"], resolved,
                xl.node_library_ids(REPO, "spec/component/numerics/linear_algebra/dense_solve_lu"))
        return violation

    base = moved(source)
    print(f"unmutated: moved={sorted(base)} symbol={symbol_violation(source)}")
    ok &= base == set() and symbol_violation(source) is None
    for name, subs in MUTANTS.items():
        text = _apply(source, subs)
        got = moved(text)
        want = set(HAND[name]["by"])
        line = f"{name}: moved={sorted(got)} expected={sorted(want)}"
        hold = got == want
        if HAND[name].get("build"):
            violation = symbol_violation(text) or ""
            line += f" symbol={violation[:120]!r}"
            hold &= HAND[name]["build"] in violation
        print(("OK   " if hold else "FAIL ") + line)
        ok &= hold
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
