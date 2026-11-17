#!/usr/bin/env python3
"""Issue #644 P6 V-a, netcdf half (unbilled, no LLM): P5's seeded-defect bench
(`tools/tests/test_harness_capture_groups.py`, `FortranCpuNetcdfCaptureGroupTests`) against the
GENERATED `harness_fortran_cpu_netcdf@0.1.0` model R3 certified (orchestration
`orch_20261117T055417Z_ab3bf7d6`), in place of the hand model.

  PYTHONPATH=. python3 <this file> --model <workspace/pipelines/.../src/harness_fortran_cpu_netcdf_model.f90>

Every row of the bench class runs, with the generated model in place of the hand model, except
the two that edit the model's text:

- the seeded defects: `NETCDF_DEFECTS` replaces text of the hand model, which the generated model
  does not contain. `GEN_DEFECTS` below re-expresses each of the sixteen on the generated text
  (one or two substitutions each; each substituted text must occur exactly once), and expects the
  same set of failed predicates the hand model's row expects. They are re-expressions written by
  hand, not the hand model's defects: what is shown is that each written-down breakage is
  caught, not that the generated model has no other one.
- the fanout row's carried-count variants, re-expressed the same way (`CARRIED`).

The substitutions are bound to one source; `--model` is refused unless its sha256 is `MODEL_SHA256`.
Exit 0 when every row passes.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(REPO))

from tools.tests import test_harness_capture_groups as g  # noqa: E402
from tools.tests.test_harness_capture_groups import (  # noqa: E402
    ALL, FINAL, HISTORY_A, HISTORY_B, INITIAL, OTHER, CASE, POINTS, UNREAD)

#: The generated model's sha256 (its Generate `source_meta.json#artifact_hashes`).
MODEL_SHA256 = "5836f3fe9ec3c9f07da680fa2c1504d714b8294c193ecc33a629a7cae12b7246"

R2_END = "    if (size(a) > 0) call chk(nf90_put_var(ncid, vid, a, start=st, count=ct))\n  end subroutine harness_fortran_cpu_netcdf__capture_array_r2"
GEN_DEFECTS = {
    "r2_transposed": ([(R2_END, R2_END.replace(", a, start", ", reshape(transpose(a), [size(a)]), start"))],
                      {INITIAL, FINAL, HISTORY_A, POINTS}),
    "fill_value_omitted": ([("    call chk(nf90_put_att(ncid, varid, '_FillValue', ieee_value(1.0_dp, ieee_quiet_nan)))\n", "")],
                           ALL | {UNREAD}),
    "fill_off": ([("  subroutine def_double(vname, dids, varid)\n", "  subroutine def_double(vname, dids, varid)\n    use netcdf, only: nf90_def_var_fill\n"),
                  ("    call chk(nf90_put_att(ncid, varid, '_FillValue', ieee_value(1.0_dp, ieee_quiet_nan)))\n",
                   "    call chk(nf90_put_att(ncid, varid, '_FillValue', ieee_value(1.0_dp, ieee_quiet_nan)))\n    call chk(nf90_def_var_fill(ncid, varid, 1, 0.0_dp))\n")],
                 ALL | {UNREAD}),
    "history_step_is_index": ([("vid_hstep(k), [step], start=[idx]", "vid_hstep(k), [idx], start=[idx]")], {HISTORY_A, HISTORY_B}),
    "complete_never_set": ([("    call chk(nf90_put_var(ncid, vid_cmp, 1))\n", "")], ALL | {UNREAD}),
    "stacked_count_short": ([("call chk(nf90_put_var(ncid, vid_rc, idx))", "call chk(nf90_put_var(ncid, vid_rc, idx - 1))"),
                             ("call chk(nf90_put_var(ncid, vid_sc, idx))", "call chk(nf90_put_var(ncid, vid_sc, idx - 1))")],
                            ALL | {UNREAD}),
    "zero_record_count_unset": ([("    call chk(nf90_put_var(ncid, vid_rc, 0))\n", "")], {UNREAD}),
    "zero_extent_one_cell": ([("itoa(d), nf90_unlimited, &", "itoa(d), 1, &")], ALL),
    "stacked_time_dropped": ([("vid_rt, [t], start", "vid_rt, [0.0_dp], start"), ("vid_st, [t], start", "vid_st, [0.0_dp], start")],
                             {POINTS}),
    "record_dimension_sized_by_capacity": ([("'series__n', nf90_unlimited, sdim", "'series__n', series_capacity, sdim")], ALL | {UNREAD}),
    "record_counters_carried": ([("    rpcount = 0\n    sercount = 0\n", "")], {UNREAD}),
    "restart_point_counter_carried": ([("    rpcount = 0\n", "")], {UNREAD}),
    "history_count_carried": ([("    hcount = 0\n", "    hcount = sercount\n")], {UNREAD}),
    "history_count_unset_at_open": ([("      if (vid_hcnt(k) >= 0) call chk(nf90_put_var(ncid, vid_hcnt(k), 0))\n", "")], {UNREAD}),
    "series_row_at_its_step": ([("      idx = sercount + 1\n      call chk(nf90_put_var(ncid, vid_ss", "      idx = step\n      call chk(nf90_put_var(ncid, vid_ss")], {UNREAD}),
    "final_count_unset": ([("    call chk(nf90_put_var(ncid, vid_fc, 1))\n", "")], ALL | {UNREAD}),
}

#: The hand model's `cap_n_rp = 0` (the restart points' counter reset at open), on the
#: generated text.
CARRIED = ("    rpcount = 0\n", "")

MODEL: Path


def _generated() -> g._Harness:
    return dataclasses.replace(g._fortran_cpu_netcdf(), model=MODEL, defects={})


def _apply(model: str, subs: list[tuple[str, str]]) -> str:
    for old, new in subs:
        assert model.count(old) == 1, old
        model = model.replace(old, new)
    return model


class GeneratedNetcdfBench(g.FortranCpuNetcdfCaptureGroupTests):
    harness = staticmethod(_generated)

    def test_each_seeded_defect_fails_the_predicates_that_read_what_it_breaks(self) -> None:
        self.assertEqual(set(GEN_DEFECTS), set(g.NETCDF_DEFECTS))
        for name, (subs, expected) in GEN_DEFECTS.items():
            with self.subTest(defect=name):
                self.assertEqual(expected, g.NETCDF_DEFECTS[name][2])
                binary = g._build(self.h, _apply(self.h.model_text, subs), self.root / name)
                run = g._Run(self.h, binary, self.root / f"{name}_run", self.h.launches[-1])
                print(f"{name}: rc={run.proc.returncode} failed={sorted(run.failed())}")
                self.assertEqual(run.proc.returncode, 0, run.proc.stderr)
                self.assertEqual(run.failed(), expected)

    def test_the_fanout_case_judges_the_records_it_commits_and_the_carried_counts(
            self) -> None:
        """The hand row's two carried-count variants on the generated text: the restart
        points' counter carried fails the fanout predicate when the fanout case runs second,
        and fails the capture-group case when it runs first."""
        def satisfied(run: g._Run) -> list[bool]:
            return [r["satisfied"] for r in self._fanout(run)]
        self.assertEqual(satisfied(self.clean[-1]), [True])
        carried = _apply(self.h.model_text, [CARRIED])
        binary = g._build(self.h, carried, self.root / "fanout_carried")
        run = g._Run(self.h, binary, self.root / "fanout_carried_run", self.h.launches[-1])
        self.assertEqual(satisfied(run), [False])
        run = g._Run(self.h, binary, self.root / "fanout_first_run", self.h.launches[-1],
                     cases=(OTHER, CASE))
        self.assertEqual(satisfied(run), [True])
        self.assertTrue(run.failed())


def main() -> int:
    global MODEL
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True, type=Path)
    args = ap.parse_args()
    digest = hashlib.sha256(args.model.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        print(f"--model: sha256 {digest}, not the source the substitutions name", file=sys.stderr)
        return 2
    MODEL = args.model.resolve()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GeneratedNetcdfBench)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
