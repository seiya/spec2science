"""The `netcdf` output format's capture store (issue #644, Part 3 P5a), and every reader of a
run's captures driven over a `netcdf` run.

What each class pins:

- `LayoutTests` / `ReadTests` / `RefusalTests` — the store against
  `docs/backends/output_format/netcdf/CAPTURE_LAYOUT.md` on files written by
  `netcdf_capture_fixture` (netCDF4-python laying them out as a column-major harness would): a
  record reads as the `json` store reads the same record, value for value; each writer defect
  the fixture seeds (`netcdf_capture_fixture.DEFECTS`, and one variable per group wrongly
  defined) is refused, never read as a record.
- `ReaderParityTests` — the readers of a run's captures driven here (the verdict and a
  comparand, the post-execute gate's `_validate_raw_evidence`, the
  conductor's promotion / schema / deliverable / continuation-merge helpers,
  `tools/run_configuration.py`'s restart source and kept records, `_final_records`) gives over
  a `netcdf` run exactly what it gives over the `json` run holding the same records: a reader
  that still builds a `json` path finds nothing in the `netcdf` run and is caught. The
  restart-point and continuation readers of `primary_evidence` and `run_configuration`'s run
  are driven over files a Fortran writer made (`test_driver_render.NetcdfHarnessEndToEndTest`,
  `test_run_configuration.NetcdfRunConfigurationTest`, issue #644 P5b). Not driven over
  `netcdf`: the conductor's restart commands, remote paths and `_captured` (they reach the
  store through the same `read_record` / `case_ids` / `run_dirs` the rows drive).
- `StackedGroupParityTests` — samples, restart points and a chunked series read the same in
  both formats through the store, `load_case_env` and `kept_records`.
- `ReaderRefusesAnIncompleteCaseTests` — the same readers over a `netcdf` run one of whose case
  files was never completed (`complete` not 1: the program stopped before its output ended,
  issue #644 O12): each refuses it or reports it unread; none reads it as a case.

The files here are written by the reader's own binding. A Fortran writer of the layout, the
hand model of the harness that writes it, is driven by `test_driver_render`,
`test_harness_capture_groups` and `test_run_configuration` (P5b).
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from tools import capture_store as cs
from tools.capture_store import CaptureError
from tools.tests.netcdf_capture_fixture import from_json_run, write_case

NC = cs.store("netcdf")
JS = cs.store("json")

H0 = np.arange(6.0).reshape(3, 2)
SHAPES = {"h": (3, 2), "z": (0, 4), "s": ()}


def _good_case(sdir: Path, case_id: str = "c", **overrides) -> Path:
    """A complete case with every group, a zero-extent variable and a non-square array."""
    kw = dict(
        shapes=SHAPES,
        initial={"h": H0, "z": np.zeros((0, 4)), "s": 1.5}, initial_step=0, initial_t=0.0,
        final={"h": H0 + 1, "z": np.zeros((0, 4)), "s": 2.5}, final_step=4, final_t=1.0,
        history={"h": [(0, 0.0, H0), (2, 0.5, H0 + 0.5), (4, 1.0, H0 + 1)],
                 "s": [(0, 0.0, 1.5), (4, 1.0, 2.5)]},
        restart_vars=["h", "s"], restart_points=[(2, 0.5, {"h": H0 + 0.5, "s": 2.0}),
                                                 (4, 1.0, {"h": H0 + 1, "s": 2.5})],
        series_names=["m", "mass"],
        series=[(1, 0.25, {"m": 1.0, "mass": 10.0}), (2, 0.5, {"m": 2.0, "mass": 20.0})])
    kw.update(overrides)
    sdir.mkdir(parents=True, exist_ok=True)
    return write_case(sdir / f"{case_id}.nc", **kw)


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sdir = self.root / "raw" / "state_snapshots"
        self.sdir.mkdir(parents=True)


class LayoutTests(_Tmp):
    def test_every_record_of_a_case_is_its_one_file(self) -> None:
        # Kills: a group directory or a per-record file name leaking into the netcdf layout.
        for group, step in ((cs.FINAL, None), (cs.INITIAL, None), (cs.SERIES, None),
                            (cs.HISTORY, 2), (cs.RESTART_POINT, 2)):
            self.assertEqual(NC.record_path(self.sdir, group, "c", step), self.sdir / "c.nc")
        self.assertEqual(NC.expected_layout("c"),
                         {cs.FINAL: "c.nc", cs.INITIAL: "c.nc", cs.SERIES: "c.nc"})
        with self.assertRaises(ValueError):
            NC.record_path(self.sdir, cs.HISTORY, "c")
        with self.assertRaises(ValueError):
            NC.record_path(self.sdir, cs.FINAL, "c", 2)

    def test_the_capture_directory_itself_is_created_and_no_group_directory(self) -> None:
        self.assertEqual(NC.run_dirs(), ("",))

    def test_the_layout_document_is_the_one_the_store_reads(self) -> None:
        # Kills: seeding a harness author the json layout, or no layout, for a netcdf target.
        text = NC.capture_layout_document()
        self.assertTrue(text.startswith("# Capture layout of the `netcdf` output format"))
        for name in ("complete", "_FillValue", "history__<v>__count", "restart_point__n",
                     "series__step", "<v>__d1", "initial__count"):
            self.assertIn(name, text)

    def test_reading_needs_the_binding_and_importing_does_not(self) -> None:
        # Kills: a module-level `import netCDF4`: the startup check reads
        # REQUIRED_PYTHON_MODULES from this module on a host that may lack the binding.
        import ast
        tree = ast.parse(Path(NC.__file__).read_text(encoding="utf-8"))
        top = {alias.name for node in tree.body if isinstance(node, ast.Import)
               for alias in node.names}
        self.assertNotIn("netCDF4", top)
        self.assertEqual(NC.REQUIRED_PYTHON_MODULES, (("netCDF4", "netCDF4"),))


class ReadTests(_Tmp):
    def setUp(self) -> None:
        super().setUp()
        _good_case(self.sdir)

    def test_single_records_read_in_declared_index_order(self) -> None:
        # Kills: no transpose (a (3, 2) array read back (2, 3)); auto-masking left on; a step
        # read as a float.
        final = NC.read_record(self.sdir, cs.FINAL, "c")
        self.assertEqual(final, {"h": (H0 + 1).tolist(), "z": [], "s": 2.5, "step": 4, "t": 1.0})
        self.assertIsInstance(final["step"], int)
        self.assertEqual(NC.read_record(self.sdir, cs.INITIAL, "c")["h"], H0.tolist())

    def test_history_records_hold_the_variables_due_at_their_step(self) -> None:
        self.assertEqual(NC.history_steps(self.sdir, "c"), [0, 2, 4])
        self.assertEqual(NC.read_record(self.sdir, cs.HISTORY, "c", 2),
                         {"h": (H0 + 0.5).tolist(), "step": 2, "t": 0.5})
        self.assertEqual(set(NC.read_record(self.sdir, cs.HISTORY, "c", 4)), {"h", "s", "step", "t"})
        with self.assertRaises(CaptureError) as ctx:
            NC.read_record(self.sdir, cs.HISTORY, "c", 3)
        self.assertTrue(ctx.exception.absent)

    def test_restart_points_and_the_series(self) -> None:
        # Kills: a restart point's values or `t` read at another index than its step's.
        self.assertEqual(NC.restart_point_steps(self.sdir, "c"), [2, 4])
        self.assertEqual(NC.read_record(self.sdir, cs.RESTART_POINT, "c", 2),
                         {"h": (H0 + 0.5).tolist(), "s": 2.0, "step": 2, "t": 0.5})
        self.assertEqual(NC.read_record(self.sdir, cs.RESTART_POINT, "c", 4),
                         {"h": (H0 + 1).tolist(), "s": 2.5, "step": 4, "t": 1.0})
        with self.assertRaises(CaptureError) as ctx:
            NC.read_record(self.sdir, cs.RESTART_POINT, "c", 3)
        self.assertTrue(ctx.exception.absent)
        self.assertTrue(NC.has_record(self.sdir, cs.RESTART_POINT, "c", 4))
        self.assertFalse(NC.has_record(self.sdir, cs.RESTART_POINT, "c", 3))
        self.assertTrue(NC.has_record(self.sdir, cs.HISTORY, "c", 2))
        self.assertFalse(NC.has_record(self.sdir, cs.HISTORY, "c", 3))
        self.assertEqual(NC.read_record(self.sdir, cs.SERIES, "c"),
                         {"m": [1.0, 2.0], "mass": [10.0, 20.0], "step": [1, 2],
                          "t": [0.25, 0.5]})

    def test_a_record_reads_as_the_json_store_reads_the_same_record(self) -> None:
        # Kills: any difference of value domain (an ndarray, a numpy scalar, a rank kept or
        # lost differently for a zero extent) between the two formats.
        jdir = self.root / "j"
        for group, step, doc in (
                (cs.FINAL, None, {"h": (H0 + 1).tolist(), "z": [], "s": 2.5, "step": 4,
                                  "t": 1.0}),):
            path = JS.record_path(jdir, group, "c", step)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc), encoding="utf-8")
            got = NC.read_record(self.sdir, group, "c", step)
            self.assertEqual(got, JS.read_record(jdir, group, "c", step))
            self.assertEqual({type(v) for v in got.values()}, {list, float, int})

    def test_a_zero_extent_reads_as_json_spells_it(self) -> None:
        # Kills: a zero-extent axis read as anything but the nesting json closes it with.
        write_case(self.sdir / "z.nc", shapes={"a": (3, 0), "b": (0, 4)},
                   final={"a": np.zeros((3, 0)), "b": np.zeros((0, 4))})
        self.assertEqual(NC.read_record(self.sdir, cs.FINAL, "z"),
                         {"a": [[], [], []], "b": [], "step": 0, "t": 0.0})

    def test_a_value_equal_to_a_missing_value_reads_as_written(self) -> None:
        # Kills: auto-masking left on (a value equal to the variable's missing_value read as
        # masked, i.e. None).
        import netCDF4
        path = _good_case(self.sdir, "mv")
        with netCDF4.Dataset(str(path), "a") as ds:
            ds.variables["final__s"].missing_value = 2.5
        self.assertEqual(NC.read_record(self.sdir, cs.FINAL, "mv")["s"], 2.5)

    def test_a_big_endian_double_reads_as_written(self) -> None:
        # Kills: a byte order judged as a type (a conforming big-endian `double` refused).
        import netCDF4
        write = write_case(self.sdir / "be2.nc", shapes={"h": (2,)}, final={"h": [1.5, 2.5]},
                           overrides={"final__h": (">f8", float("nan"))})
        with netCDF4.Dataset(str(write)) as ds:
            self.assertEqual(ds.variables["final__h"].dtype.byteorder, ">")
        self.assertEqual(NC.read_record(self.sdir, cs.FINAL, "be2")["h"], [1.5, 2.5])

    def test_a_scale_attribute_does_not_change_a_value(self) -> None:
        # Kills: automatic scaling left on (`set_auto_mask` alone): the value would read
        # multiplied by the attribute.
        import netCDF4
        path = _good_case(self.sdir, "sc")
        with netCDF4.Dataset(str(path), "a") as ds:
            ds.variables["final__s"].scale_factor = 10.0
        self.assertEqual(NC.read_record(self.sdir, cs.FINAL, "sc")["s"], 2.5)

    def test_a_name_with_another_separator_is_no_variable_of_the_group(self) -> None:
        # Kills: `values_of` taking `final__a__b` as a variable `a__b` of the final record.
        import netCDF4
        path = _good_case(self.sdir, "sep")
        with netCDF4.Dataset(str(path), "a") as ds:
            ds.createVariable("final__a__b", "f8", (), fill_value=float("nan"))[...] = 1.0
        self.assertNotIn("a__b", NC.read_record(self.sdir, cs.FINAL, "sep"))

    def test_an_unwritten_cell_reads_non_finite(self) -> None:
        # Kills: a fill value that reads as a number (the finiteness check is what sees it).
        write_case(self.sdir / "u.nc", shapes={"h": (3, 2)}, final={"h": H0},
                   defects=["unwritten_cell"])
        h = NC.read_record(self.sdir, cs.FINAL, "u")["h"]
        self.assertTrue(math.isnan(h[2][1]))
        self.assertEqual(h[0][0], 0.0)

    def test_enumeration_copy_and_move(self) -> None:
        _good_case(self.sdir, "d", initial=None)
        self.assertEqual(NC.case_ids(self.sdir), ["c", "d"])
        self.assertEqual(NC.case_ids(self.sdir, cs.INITIAL), ["c"])
        self.assertEqual(NC.case_ids(self.sdir, cs.SERIES), ["c", "d"])
        with self.assertRaises(ValueError):
            NC.case_ids(self.sdir, cs.HISTORY)
        with self.assertRaises(ValueError):
            NC.case_ids(self.root / "empty", cs.RESTART_POINT)
        self.assertEqual(NC.case_paths(self.sdir, "c"), [self.sdir / "c.nc"])
        self.assertEqual(NC.case_paths(self.sdir, "x"), [])
        (self.sdir / "deep").mkdir()
        (self.sdir / "deep" / "e.nc").write_bytes(b"")
        (self.sdir / "snapshot_schema.json").write_text("{}")
        self.assertEqual([p.relative_to(self.sdir).as_posix() for p in NC.all_case_paths(self.sdir)],
                         ["c.nc", "d.nc", "deep/e.nc"])
        dst = self.root / "dst"
        dst.mkdir()
        self.assertEqual(NC.copy_run(self.sdir, dst), ["c.nc", "d.nc"])
        other = self.root / "other"
        other.mkdir()
        self.assertIsNone(NC.move_case(dst, other, "c"))
        self.assertEqual(sorted(p.name for p in other.iterdir()), ["c.nc"])
        (dst / "c.nc").write_bytes(b"x")
        reason = NC.move_case(dst, other, "c")
        self.assertIn("c.nc", reason)
        self.assertTrue((dst / "c.nc").is_file())

    def test_locate_names_the_case_files_final_record_only(self) -> None:
        self.assertEqual(NC.locate(self.sdir / "c.nc", "c"), (self.sdir, cs.FINAL, None))
        self.assertIsNone(NC.locate(self.sdir / "c.nc", "d"))
        self.assertIsNone(NC.locate(self.sdir / "c.json", "c"))

    def test_a_complete_files_series_is_its_prefix(self) -> None:
        self.assertEqual(NC.read_series_prefix(self.sdir, "c")[1], 2)


class RefusalTests(_Tmp):
    """Each writer defect of `netcdf_capture_fixture.DEFECTS` that leaves a file the reader must
    not take: every record of the case is refused, none read."""

    REFUSED = {
        "complete_unset": "incomplete: complete is 0",
        "complete_two": "incomplete: complete is 2",
        "no_complete": "incomplete: complete is None",
        "dims_unreversed": "has dimensions ('h__d1', 'h__d2'), the layout ('h__d2', 'h__d1')",
        "fill_not_nan": "variable initial__h fills unwritten cells with 0.0, not NaN",
        "no_fill": "variable initial__h fills unwritten cells with 9.969",
        "nofill_mode": "variable initial__h fills unwritten cells with None, not NaN",
        "t_fill_not_nan": "variable initial__t fills unwritten cells with 0.0, not NaN",
        "value_float32": "variable initial__h is of type float32, the layout float64",
        "step_double": "variable history__h__step is of type float64, the layout int32",
        "history_step_own_dim": "variable history__h__step has dimensions "
                                "('history__h__step__own',), the layout ('history__h__n',)",
        "rp_t_own_dim": "variable restart_point__t has dimensions ('restart_point__t__own',), "
                        "the layout ('restart_point__n',)",
        "series_step_own_dim": "variable series__step has dimensions ('series__step__own',), "
                               "the layout ('series__n',)",
        "series_float32": "variable series__m is of type float32, the layout float64",
        "series_column_own_dim": "variable series__m has dimensions ('series__m__own',), the "
                                 "layout ('series__n',)",
        "complete_double": "variable complete is of type float64, the layout int32",
        "count_short": "history__h__count is 2, and its record dimension history__h__n has "
                       "length 3",
        "final_count_two": "final__count is 2, not 0 or 1",
        "history_steps_repeat": "history__h__step [0, 2, 2] is not strictly ascending",
        "rp_steps_repeat": "restart_point__step [2, 2] is not strictly ascending",
        "series_steps_repeat": "series__step [1, 1] is not strictly ascending",
        "series_dim_fixed": "record dimension series__n is not unlimited",
        "history_dim_fixed": "record dimension history__h__n is not unlimited",
        "rp_dim_fixed": "record dimension restart_point__n is not unlimited",
        "series_step_unwritten": "series__step [-2147483647, 2] holds a step no commit wrote",
        "rp_count_short": "restart_point__count is 1, and its record dimension "
                          "restart_point__n has length 2",
        "series_count_short": "series__count is 1, and its record dimension series__n has "
                              "length 2",
        "not_netcdf": "unreadable",
    }

    def test_each_defect_refuses_every_record_of_its_case(self) -> None:
        for defect, reason in sorted(self.REFUSED.items()):
            with self.subTest(defect=defect):
                path = _good_case(self.sdir, defect, defects=[defect])
                for group, step in ((cs.FINAL, None), (cs.INITIAL, None), (cs.SERIES, None),
                                    (cs.RESTART_POINT, 2), (cs.HISTORY, 2)):
                    with self.assertRaises(CaptureError) as ctx:
                        NC.read_record(self.sdir, group, defect, step)
                    self.assertIn(reason, str(ctx.exception))
                    self.assertFalse(ctx.exception.absent)
                    self.assertEqual(ctx.exception.label, path.name)
                # listed, so its reader is refused rather than the case taken as never run
                self.assertIn(defect, NC.case_ids(self.sdir))
                self.assertTrue(NC.has_record(self.sdir, cs.INITIAL, defect))
                self.assertEqual(NC.history_steps(self.sdir, defect), [])
                self.assertEqual(NC.read_series_prefix(self.sdir, defect), ({}, None))
                path.unlink()

    def test_an_unwritten_cell_of_a_no_fill_file_is_not_read_as_zero(self) -> None:
        # Kills: judging the `_FillValue` attribute instead of the fill in effect (the
        # attribute is NaN; the unwritten cells read 0.0 or the type's default).
        path = write_case(self.sdir / "nf.nc", shapes={"h": (3,)}, final={"h": [5.0, 6.0, 7.0]},
                          defects=["nofill_mode"])
        import netCDF4
        with netCDF4.Dataset(str(path)) as ds:
            self.assertTrue(math.isnan(ds.variables["final__h"]._FillValue))
        with self.assertRaisesRegex(CaptureError, "fills unwritten cells with None"):
            NC.read_record(self.sdir, cs.FINAL, "nf")

    def test_each_groups_variables_are_judged_one_at_a_time(self) -> None:
        # Kills: a type, fill or dimension check that reaches only the first variable defined
        # (`initial__*`): every other group's variable carries the defect alone here.
        cases = {
            "final__h": ("f8", 0.0), "final__s": ("f4", None), "final__t": ("f8", 0.0),
            "history__h": ("f8", 0.0), "history__h__t": ("f8", 0.0),
            "history__s": ("f4", None), "restart_point__h": ("f4", None),
            "restart_point__s": ("f8", 0.0), "restart_point__t": ("f8", 0.0),
            "series__m": ("f8", 0.0), "series__t": ("f4", None)}
        for name, (kind, fill) in sorted(cases.items()):
            with self.subTest(variable=name):
                path = _good_case(self.sdir, "one", overrides={name: (kind, fill)})
                with self.assertRaises(CaptureError) as ctx:
                    NC.read_record(self.sdir, cs.FINAL, "one")
                self.assertIn(f"variable {name} ", str(ctx.exception))
                path.unlink()

    def test_a_malformed_marker_is_refused_not_raised(self) -> None:
        # Kills: decoding `complete` outside the refusal (a string marker raised
        # AttributeError out of `read_record` and `has_record`).
        path = _good_case(self.sdir, "sm", defects=["no_complete"])
        import netCDF4
        with netCDF4.Dataset(str(path), "a") as ds:
            ds.createVariable("complete", str, ())[0] = "1"
        with self.assertRaisesRegex(CaptureError, "complete cannot be read"):
            NC.read_record(self.sdir, cs.FINAL, "sm")
        self.assertTrue(NC.has_record(self.sdir, cs.INITIAL, "sm"))

    def test_the_defect_list_is_every_seeded_file_defect(self) -> None:
        from tools.tests.netcdf_capture_fixture import DEFECTS
        self.assertEqual(set(self.REFUSED) | {"unwritten_cell", "history_t_disagree"}, DEFECTS)

    def test_history_times_that_disagree_at_one_step_refuse_that_record(self) -> None:
        _good_case(self.sdir, "td", defects=["history_t_disagree"])
        with self.assertRaisesRegex(CaptureError, "carry different t"):
            NC.read_record(self.sdir, cs.HISTORY, "td", 0)
        self.assertEqual(NC.read_record(self.sdir, cs.HISTORY, "td", 2)["t"], 0.5)

    def test_a_case_without_an_initial_record_has_none(self) -> None:
        _good_case(self.sdir, "ni", initial=None)
        self.assertFalse(NC.has_record(self.sdir, cs.INITIAL, "ni"))
        with self.assertRaises(CaptureError) as ctx:
            NC.read_record(self.sdir, cs.INITIAL, "ni")
        self.assertTrue(ctx.exception.absent)

    def test_an_absent_file_is_absent(self) -> None:
        with self.assertRaises(CaptureError) as ctx:
            NC.read_record(self.sdir, cs.FINAL, "x")
        self.assertTrue(ctx.exception.absent)
        self.assertFalse(NC.has_record(self.sdir, cs.FINAL, "x"))


# ----------------------------------------------------------------------------- readers


def _twin_runs(test: unittest.TestCase, *, defects: dict | None = None
               ) -> tuple[Path, Path, dict]:
    """A `json` run of two cases (`test_primary_evidence`'s fixture: an initial and a final
    record each) and its `netcdf` twin, and the IR they answer."""
    from tools.tests import test_primary_evidence as tpe
    js = tpe._RunDir()
    test.addCleanup(js.cleanup)
    for i, cid in enumerate("ab"):
        field = tpe._field(i)
        js.write_state(cid, field, field)
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    nc_root = Path(tmp.name)
    from_json_run(cs.snapshot_dir(js.root), cs.snapshot_dir(nc_root), defects=defects)
    return js.root, nc_root, tpe._ir([tpe.MASS, tpe.HMIN])


class ReaderParityTests(unittest.TestCase):
    def test_the_verdict(self) -> None:
        from tools import primary_evidence as pe
        js, nc, ir = _twin_runs(self)
        got = pe.evaluate_primary_predicates(ir, nc, output_format="netcdf")
        self.assertEqual(got, pe.evaluate_primary_predicates(ir, js, output_format="json"))
        self.assertTrue(got and all(r["satisfied"] for r in got), got)

    def test_a_comparand_is_read_in_its_own_format(self) -> None:
        from tools import primary_evidence as pe
        from tools.tests import test_primary_evidence as tpe
        js, nc, _ir = _twin_runs(self)
        ir = tpe._ir([tpe.MASS, tpe.CROSS])
        # the json store finds no capture in a netcdf run directory
        self.assertTrue(cs.capture_paths("netcdf", nc))
        self.assertEqual(cs.capture_paths("json", nc), [])
        comparand = pe.ComparandEvidence(target_id="t2", pipeline_ref="p", run_id="r",
                                         run_dir=nc, output_format="netcdf")
        records = pe.evaluate_primary_predicates(ir, js, output_format="json",
                                                 comparands=[comparand])
        self.assertTrue(records and all(r["satisfied"] for r in records), records)

    def test_the_stores_own_reader(self) -> None:
        # Re-hosted (issue #724 PR-2) from the `validate.judge` excerpt, whose vehicle went with
        # the judge: the two stores read the twin runs to the same cases and the same records
        # at both capture points.
        js, nc, _ir = _twin_runs(self)
        got_store, want_store = cs.store("netcdf"), cs.store("json")
        got_dir, want_dir = cs.snapshot_dir(nc), cs.snapshot_dir(js)
        case_ids = list(got_store.case_ids(got_dir))
        self.assertTrue(case_ids)
        self.assertEqual(case_ids, list(want_store.case_ids(want_dir)))
        for cid in case_ids:
            for group in (cs.FINAL, cs.INITIAL):
                got = dict(got_store.read_record(got_dir, group, cid))
                want = dict(want_store.read_record(want_dir, group, cid))
                self.assertTrue(got, (cid, group))
                self.assertEqual(set(got), set(want), (cid, group))
                for name in got:
                    self.assertEqual(np.asarray(got[name]).tolist(),
                                     np.asarray(want[name]).tolist(), (cid, group, name))

    def test_the_conductors_helpers(self) -> None:
        import types

        from tools import workflow_conductor as wc
        from tools.tests import test_primary_evidence as tpe
        js, nc, ir = _twin_runs(self)
        verdict_ir = tpe._ir([tpe.MASS, tpe.HMIN, tpe.SYM])
        shaped = wc.Conductor._shape_verdict(verdict_ir, nc, "netcdf")
        self.assertIsNotNone(shaped)
        self.assertEqual(shaped, wc.Conductor._shape_verdict(verdict_ir, js, "json"))
        self.assertEqual(wc.Conductor._shape_state_agreement(ir, nc, "s1", nc, "netcdf"),
                         wc.Conductor._shape_state_agreement(ir, js, "s1", js, "json"))
        stub = types.SimpleNamespace(target=types.SimpleNamespace(output_format="netcdf"),
                                     _rel=lambda p: Path(p).name)
        # read in the json run's own place, the netcdf store finds no case of it
        self.assertIn("missing=['a.nc', 'b.nc']", wc.Conductor._snapshot_deliverable_gap(
            stub, cs.snapshot_dir(js), ["a", "b"], ["state_snapshots"]))
        self.assertEqual(wc.Conductor._snapshot_deliverable_gap(
            stub, cs.snapshot_dir(nc), ["a", "b"], ["state_snapshots"],
            initial_required=True, series_required=True), "")
        # a case file without its initial record names the record, not a misnamed file
        write_case(cs.snapshot_dir(nc) / "c.nc", shapes={"h": (2,)}, final={"h": [1.0, 2.0]})
        gap = wc.Conductor._snapshot_deliverable_gap(
            stub, cs.snapshot_dir(nc), ["a", "c"], ["state_snapshots"], initial_required=True)
        self.assertIn("missing=['c.nc (initial record)']", gap)
        self.assertIn("runner wrote=['a.nc', 'a.nc (initial record)', 'b.nc', "
                      "'b.nc (initial record)', 'c.nc']", gap)
        self.assertIn("(and one raw/state_snapshots/<case_id>.nc (initial record),", gap)
        self.assertIn("or commit no record of the group named.", gap)
        (cs.snapshot_dir(nc) / "c.nc").unlink()
        with tempfile.TemporaryDirectory() as tmp:
            node = Path(tmp) / "node"
            refs = wc.Conductor._promote_run_evidence(stub, nc, node, ["state_snapshots"])
            self.assertEqual([r.split("state_snapshots/")[1] for r in refs], ["a.nc", "b.nc"])
            wc.Conductor._author_snapshot_schema(stub, ir, node)
            self.assertEqual(json.loads((cs.snapshot_dir(node) / "snapshot_schema.json")
                                        .read_text())["samples"], ["a.nc", "b.nc"])
            dst = Path(tmp) / "dst"
            cs.snapshot_dir(dst).mkdir(parents=True)
            self.assertIsNone(wc.Conductor._merge_continuation_captures(nc, dst, "a", "netcdf"))
            self.assertTrue((cs.snapshot_dir(dst) / "a.nc").is_file())

    def test_run_configuration(self) -> None:
        from tools import run_configuration as rc
        _js, nc, _ir = _twin_runs(self)
        source = rc._restart_source(cs.snapshot_dir(nc) / "a.nc", "a", "netcdf")
        self.assertEqual((source.run_dir, source.kind), (nc, cs.FINAL))
        self.assertEqual(source.point["step"], 2)
        self.assertEqual(rc.kept_records(nc, "a", "netcdf"),
                         {"restart_points": [], "series": {"rows": 0, "last_step": None}})

    def test_the_final_records_of_a_harness_run(self) -> None:
        """`validate_pipeline_semantics._final_records`, the reader of a harness self-test's
        reported block size and grid (issue #644 P5b): the same members on both runs."""
        import types

        from tools import validate_pipeline_semantics as vps
        js, nc, _ir = _twin_runs(self)

        def finals(run: Path, fmt: str) -> list[tuple[str, dict]]:
            target = types.SimpleNamespace(output_format=fmt)
            return [(p.name, doc) for p, doc in vps._final_records(target, cs.snapshot_dir(run))]
        got = finals(nc, "netcdf")
        self.assertEqual([name for name, _doc in got], ["a.nc", "b.nc"])
        self.assertEqual([doc for _n, doc in got], [doc for _n, doc in finals(js, "json")])

    def test_the_post_execute_gate(self) -> None:
        js, nc, ir = _twin_runs(self)
        self.assertEqual(_post_execute(self, nc, ir, "netcdf"), _post_execute(self, js, ir, "json"))


class StackedGroupParityTests(unittest.TestCase):
    """A run with every group — samples at different steps holding different variables, two
    restart points, a series in two chunks — read in both formats: the readers of the stacked
    groups (`load_case_env`'s history, restart points and series; `kept_records`) see the same
    values. The twin runs above hold an initial and a final record only."""

    SCHEMA = {"variables": [{"name": "h", "shape_expr": "[3, 2]"},
                            {"name": "s", "shape_expr": "scalar"}],
              "time_variable": "t", "time_shape_expr": "scalar"}

    def _runs(self) -> tuple[Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        js, nc = Path(tmp.name) / "js", Path(tmp.name) / "nc"
        jdir = cs.snapshot_dir(js)

        def put(group: str, doc: dict, step: int | None = None, name: str | None = None) -> None:
            path = (jdir / "series" / name) if name else JS.record_path(jdir, group, "c", step)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc), encoding="utf-8")

        h = lambda k: (H0 + k).tolist()  # noqa: E731
        put(cs.INITIAL, {"h": h(0), "s": 1.0, "step": 0, "t": 0.0})
        put(cs.FINAL, {"h": h(4), "s": 5.0, "step": 4, "t": 1.0})
        put(cs.HISTORY, {"h": h(2), "s": 3.0, "step": 2, "t": 0.5}, 2)
        put(cs.HISTORY, {"h": h(3), "step": 3, "t": 0.75}, 3)
        put(cs.HISTORY, {"h": h(4), "s": 5.0, "step": 4, "t": 1.0}, 4)
        put(cs.RESTART_POINT, {"h": h(2), "s": 3.0, "step": 2, "t": 0.5}, 2)
        put(cs.RESTART_POINT, {"h": h(4), "s": 5.0, "step": 4, "t": 1.0}, 4)
        put(cs.SERIES, {"step": [1, 2], "t": [0.25, 0.5], "m": [1.0, 2.0]}, name="c__0.json")
        put(cs.SERIES, {"step": [3, 4], "t": [0.75, 1.0], "m": [3.0, 4.0]}, name="c__1.json")
        from_json_run(jdir, cs.snapshot_dir(nc))
        return js, nc

    def test_every_record_reads_the_same(self) -> None:
        js, nc = self._runs()
        jdir, ndir = cs.snapshot_dir(js), cs.snapshot_dir(nc)
        self.assertEqual(NC.history_steps(ndir, "c"), [2, 3, 4])
        self.assertEqual(NC.restart_point_steps(ndir, "c"), [2, 4])
        for group, steps in ((cs.HISTORY, [2, 3, 4]), (cs.RESTART_POINT, [2, 4]),
                             (cs.FINAL, [None]), (cs.INITIAL, [None]), (cs.SERIES, [None])):
            for step in steps:
                with self.subTest(group=group, step=step):
                    self.assertEqual(NC.read_record(ndir, group, "c", step),
                                     JS.read_record(jdir, group, "c", step))

    def test_the_verdicts_case_environment(self) -> None:
        from tools import primary_evidence as pe
        js, nc = self._runs()
        case = {"case_id": "c", "inputs": {}}
        envs = [pe.load_case_env(run, case, self.SCHEMA, output_format=fmt)
                for run, fmt in ((js, "json"), (nc, "netcdf"))]
        for part in ("history", "restart_point", "series"):
            got, want = (dict(getattr(env, part).get()) for env in envs)
            with self.subTest(part=part):
                self.assertEqual(set(got), set(want))
                self.assertTrue(want)
                for key in want:
                    np.testing.assert_array_equal(got[key], want[key])

    def test_kept_records(self) -> None:
        from tools import run_configuration as rc
        js, nc = self._runs()
        self.assertEqual(rc.kept_records(nc, "c", "netcdf"), rc.kept_records(js, "c", "json"))
        self.assertEqual(rc.kept_records(nc, "c", "netcdf")["restart_points"], [2, 4])


def _post_execute(test: unittest.TestCase, run: Path, ir: dict, output_format: str) -> list[str]:
    """`_validate_raw_evidence` over a node whose capture directory is ``run``'s, read in
    ``output_format``: the violations, with the node's own path removed."""
    import shutil

    from tools import validate_pipeline_semantics as vps
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    repo = Path(tmp.name)
    safe = "component__demo__0.1.0"
    ir_ref = f"workspace/ir/{safe}/demo_20260101_001"
    pipeline = repo / "workspace" / "pipelines" / safe / "t" / "demo_20260101_001"
    pipeline.mkdir(parents=True)
    (repo / ir_ref).mkdir(parents=True)
    (pipeline / "lineage.json").write_text(json.dumps({"ir_ref": ir_ref}))
    (repo / ir_ref / "spec.ir.yaml").write_text(json.dumps(ir))
    node = pipeline / "runs" / "r" / safe
    shutil.copytree(cs.snapshot_dir(run), cs.snapshot_dir(node))
    schema = ir["io_contract"]["raw_requirements"]["required_evidence"][0]["schema"]
    (cs.snapshot_dir(node) / "snapshot_schema.json").write_text(json.dumps(schema))
    for name in ("perf.json", "quality_check.json"):
        (node / name).write_text("{}")
    execution = vps.NodeExecution(node_key="component/demo@0.1.0", node_dir=node,
                                  exec_dir=pipeline / "runs" / "r", pipeline_dir=pipeline)
    violations: list[str] = []
    with mock.patch.object(vps, "_execution_output_format", return_value=output_format):
        vps._validate_raw_evidence(repo, execution, violations)
    return [v.replace(str(node), "<node>") for v in violations]


class ReaderRefusesAnIncompleteCaseTests(unittest.TestCase):
    """Case `a`'s file was never completed. No reader takes it as a case."""

    def setUp(self) -> None:
        self.js, self.nc, self.ir = _twin_runs(self, defects={"a": ["complete_unset"]})

    def test_the_verdict(self) -> None:
        from tools import primary_evidence as pe
        records = pe.evaluate_primary_predicates(self.ir, self.nc, output_format="netcdf")
        self.assertTrue(records)
        for record in records:
            self.assertEqual((record["satisfied"], record["kind"]), (False, "structural"))
            self.assertIn("a.nc is incomplete", record["evaluated"][0]["error"])

    def test_the_stores_own_reader(self) -> None:
        # Re-hosted (issue #724 PR-2) from the `validate.judge` excerpt, whose vehicle went with
        # the judge: the store refuses the incomplete case and reads the complete one.
        store, sdir = cs.store("netcdf"), cs.snapshot_dir(self.nc)
        with self.assertRaises(CaptureError) as caught:
            store.read_record(sdir, cs.FINAL, "a")
        self.assertIn("incomplete", str(caught.exception))
        self.assertTrue(dict(store.read_record(sdir, cs.FINAL, "b")))

    def test_the_conductors_helpers(self) -> None:
        from tools import workflow_conductor as wc
        from tools.tests import test_primary_evidence as tpe
        verdict_ir = tpe._ir([tpe.MASS, tpe.HMIN, tpe.SYM])
        shaped = wc.Conductor._shape_verdict(verdict_ir, self.nc, "netcdf")
        self.assertIsNotNone(shaped)
        self.assertNotEqual(shaped, wc.Conductor._shape_verdict(verdict_ir, self.js, "json"))
        agreement = wc.Conductor._shape_state_agreement(self.ir, self.nc, "s1", self.nc,
                                                        "netcdf")
        self.assertNotEqual(agreement["status"], "agree", agreement)

    def test_run_configuration(self) -> None:
        from tools import run_configuration as rc
        with self.assertRaisesRegex(rc.ConfigurationRefused, "incomplete"):
            rc._restart_source(cs.snapshot_dir(self.nc) / "a.nc", "a", "netcdf")
        step, why = rc._kernel_outcome(self.ir, self.nc, "a", "netcdf")
        self.assertIsNone(step)
        self.assertIn("incomplete", why)
        self.assertEqual(rc.kept_records(self.nc, "a", "netcdf"),
                         {"restart_points": [], "series": {"rows": 0, "last_step": None}})

    def test_the_final_records_of_a_harness_run_leave_it_out(self) -> None:
        import types

        from tools import validate_pipeline_semantics as vps
        target = types.SimpleNamespace(output_format="netcdf")
        self.assertEqual([p.name for p, _doc in vps._final_records(
            target, cs.snapshot_dir(self.nc))], ["b.nc"])

    def test_the_post_execute_gate_passes_it_to_the_verdict(self) -> None:
        # The shape checks pass over a record they cannot decode (`_decoded_state_records`);
        # the verdict's reader refuses it (`test_the_verdict`). Pinned: no violation names a
        # complete case, and the post-execute gate does not crash on the incomplete one.
        got = _post_execute(self, self.nc, self.ir, "netcdf")
        self.assertEqual(got, _post_execute(self, self.js, self.ir, "json"))


if __name__ == "__main__":
    unittest.main()
