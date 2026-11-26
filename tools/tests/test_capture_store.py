#!/usr/bin/env python3
"""Tests for the capture store seam (`tools/capture_store.py`) and the `json` output format's
store (`tools/backends/output_format/json/store.py`) — issue #644, Part 3 P3.

Each row names the mutation it kills. The readers that go through the store are tested where
they live (`test_primary_evidence.py`, `test_validate_pipeline_semantics.py`,
`test_workflow_conductor.py`, `test_run_configuration.py`);
`CaptureStoreReachTests` here drives each of them through a recording store.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import capture_store as cs
from tools.backends import registry
from tools.tests.target_fixtures import SAMPLE_ROOT

STORE = cs.store("json")


def _write(path: Path, doc: object = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"t": 0.0} if doc is None else doc), encoding="utf-8")
    return path


class SeamTests(unittest.TestCase):
    def test_the_store_is_the_registry_capability_of_the_format(self) -> None:
        # Kills: the seam importing the json package by name.
        self.assertIs(STORE, registry.capability_module("output_format", "json", "capture_store"))

    def test_an_undeclared_format_has_no_store(self) -> None:
        # Kills: a fallback to json for a format nobody declared.
        with self.assertRaises(registry.UnsupportedBackend):
            cs.store("hdf5")

    def test_run_dirs_are_the_stores_directories_for_every_node(self) -> None:
        # Kills: dropping a group directory (`restart_point/` among them), or reordering them
        # (the control file's recipe renders them in this order). Every node gets them since
        # issue #644 P4: a harness self-test writes through the capture interface too.
        self.assertEqual(
            cs.run_dirs("json"),
            (("raw", "state_snapshots", "initial"), ("raw", "state_snapshots", "history"),
             ("raw", "state_snapshots", "restart_point"), ("raw", "state_snapshots", "series")))
        # Kills: a netcdf run whose capture directory nobody creates (its case files sit in
        # the capture directory itself, and the harness creates no directory).
        # A remote job's directory list refuses an empty element (`run/raw/state_snapshots/`).
        from tools.remote_execution import _relative
        self.assertEqual(cs.run_dirs("netcdf"), (("raw", "state_snapshots"),))
        with tempfile.TemporaryDirectory() as tmp:
            for parts in cs.run_dirs("netcdf"):
                Path(tmp).joinpath(*parts).mkdir(parents=True)
                _relative("/".join(("run", *parts)), "directory")
            self.assertTrue(cs.snapshot_dir(Path(tmp)).is_dir())

    def test_capture_paths_drops_the_snapshot_schema_at_the_top_only(self) -> None:
        # Kills: hashing the host-written schema into a comparand's evidence; and dropping a
        # nested file that merely shares its name.
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            sdir = cs.snapshot_dir(run)
            _write(sdir / "snapshot_schema.json")
            _write(sdir / "c1.json")
            _write(sdir / "history" / "c1__2.json")
            _write(sdir / "deep" / "snapshot_schema.json")
            (sdir / "note.txt").write_text("x", encoding="utf-8")
            got = [p.relative_to(sdir).as_posix() for p in cs.capture_paths("json", run)]
        self.assertEqual(got, ["c1.json", "deep/snapshot_schema.json", "history/c1__2.json"])


class ControlFileRecipeGoldenTests(unittest.TestCase):
    """The `test` recipe's directory line, byte for byte: main `b3282e9b`'s host-rendered line
    with `restart_point/` added before `series/` (issue #644 P4, which also gives it to every
    node). The two renderers are held equal to each other by `test_codegen_bundle.py`'s parity
    row; this holds one of them to the bytes, so the pair cannot drift together."""

    GOLDEN = ("\tmkdir -p $(RUNDIR)/raw/state_snapshots/initial "
              "$(RUNDIR)/raw/state_snapshots/history $(RUNDIR)/raw/state_snapshots/restart_point "
              "$(RUNDIR)/raw/state_snapshots/series")

    def test_the_rendered_recipe_is_unchanged(self) -> None:
        # Kills: a reordered or renamed directory, a changed separator, a directory dropped —
        # each moves a rendered byte of every control file, and with it a Generate output.
        from tools import workflow_conductor as wc
        from tools.target_profile import load_target_profile
        repo = SAMPLE_ROOT
        profile = load_target_profile(repo, "fortran_cpu")
        rules = wc.Conductor._control_file_rules(
            {**profile.toolchain, "backend": profile.parallel_backend}, None)
        control_file = registry.capability_module("build_system", "make", "control_file")
        text = control_file.render_node(
            rules=rules, compiler="gfortran", bin_name="x_runner",
            cases_default="c1 c2", model_stem="x_model", runner_stem="x_runner",
            closure=["dep_a", "dep_b"], capture_dirs=cs.run_dirs(profile.output_format))
        self.assertEqual([ln for ln in text.split("\n")
                          if ln.startswith("\tmkdir -p $(RUNDIR)")], [self.GOLDEN])


class StartupModuleTests(unittest.TestCase):
    def test_every_stores_python_modules_are_required_whatever_the_target(self) -> None:
        # Kills: asking only the resolved target's language (or only its own format): a
        # cross-target agreement reads another target's run through that target's store.
        from unittest import mock

        from tools.host_prerequisites import required_python_modules
        for language in registry.backend_ids("language"):
            with self.subTest(language=language):
                reader = registry.capability_module(
                    "language", language, "source_reading").REQUIRED_PYTHON_MODULES
                netcdf = cs.store("netcdf").REQUIRED_PYTHON_MODULES
                self.assertEqual(netcdf, (("netCDF4", "netCDF4"),))
                self.assertEqual(required_python_modules({"language": language}),
                                 (*reader, *netcdf))
                with mock.patch.object(STORE, "REQUIRED_PYTHON_MODULES",
                                       (("store_mod", "store-dist"),)):
                    self.assertEqual(required_python_modules({"language": language}),
                                     (*reader, ("store_mod", "store-dist"), *netcdf))


class LayoutTests(unittest.TestCase):
    def test_record_paths(self) -> None:
        # Kills: any change of where a group's record sits or how a sample is named.
        sdir = Path("/x")
        self.assertEqual(STORE.record_path(sdir, cs.FINAL, "c"), sdir / "c.json")
        self.assertEqual(STORE.record_path(sdir, cs.INITIAL, "c"), sdir / "initial" / "c.json")
        self.assertEqual(STORE.record_path(sdir, cs.SERIES, "c"), sdir / "series" / "c__0.json")
        self.assertEqual(STORE.record_path(sdir, cs.HISTORY, "c", 12),
                         sdir / "history" / "c__12.json")
        self.assertEqual(STORE.record_path(sdir, cs.RESTART_POINT, "c", 12),
                         sdir / "restart_point" / "c__12.json")

    def test_a_sample_needs_a_step_and_no_other_record_takes_one(self) -> None:
        # Kills: a history read without a step silently reading the final record's name.
        with self.assertRaises(ValueError):
            STORE.record_path(Path("/x"), cs.HISTORY, "c")
        with self.assertRaises(ValueError):
            STORE.record_path(Path("/x"), cs.FINAL, "c", 3)

    def test_a_restart_point_needs_a_step_and_a_series_takes_none(self) -> None:
        # Kills: `_STEPPED` losing `restart_point` (a restart point read without a step would
        # name `restart_point/c.json`, which no harness writes), and gaining `series` (a series
        # is named by its chunk 0, never by a step its caller passes).
        with self.assertRaises(ValueError):
            STORE.record_path(Path("/x"), cs.RESTART_POINT, "c")
        with self.assertRaises(ValueError):
            STORE.record_path(Path("/x"), cs.SERIES, "c", 3)

    def test_expected_layout_names_the_once_per_case_files(self) -> None:
        self.assertEqual(STORE.expected_layout(), {
            "final": "<case_id>.json", "initial": "initial/<case_id>.json",
            "series": "series/<case_id>__0.json"})

    def test_the_store_needs_no_module_and_states_its_layout(self) -> None:
        self.assertEqual(STORE.REQUIRED_PYTHON_MODULES, ())
        layout = STORE.capture_layout_document()
        # every group's file, as `record_path` spells it for a case `<C>`
        for group, step in ((cs.FINAL, None), (cs.INITIAL, None)):
            self.assertIn(f"`{STORE.record_path(Path(), group, '<C>', step).as_posix()}`", layout)
        for name in ("`history/<C>__<k>.json`", "`restart_point/<C>__<k>.json`",
                     "`series/<C>__<i>.json`"):
            self.assertIn(name, layout)


class ReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sdir = Path(self._tmp.name)

    def test_a_record_reads_as_its_members(self) -> None:
        _write(self.sdir / "initial" / "c.json", {"t": 0.5, "u": [[1, 2], [3, 4]], "case_id": "c"})
        self.assertEqual(STORE.read_record(self.sdir, cs.INITIAL, "c"),
                         {"t": 0.5, "u": [[1, 2], [3, 4]], "case_id": "c"})

    def test_an_absent_record_is_absent(self) -> None:
        # Kills: an absent file reported as merely unreadable (the restart-point reader words
        # the two differently).
        with self.assertRaises(cs.CaptureError) as ctx:
            STORE.read_record(self.sdir, cs.FINAL, "c")
        self.assertTrue(ctx.exception.absent)
        self.assertEqual((ctx.exception.label, ctx.exception.reason), ("c.json", "absent"))
        self.assertEqual(str(ctx.exception), "c.json is absent")

    def test_a_file_that_is_no_json_is_unreadable_and_not_absent(self) -> None:
        # Kills: swallowing a parse error (the skip `_validate_raw_evidence` had).
        (self.sdir / "c.json").write_text('{"t": 0.0, "u": [1, 2', encoding="utf-8")
        with self.assertRaises(cs.CaptureError) as ctx:
            STORE.read_record(self.sdir, cs.FINAL, "c")
        self.assertFalse(ctx.exception.absent)
        self.assertTrue(ctx.exception.reason.startswith("unreadable: "), ctx.exception.reason)

    def test_a_file_that_is_no_object_is_refused(self) -> None:
        # Kills: handing a list to a reader that indexes it by name.
        _write(self.sdir / "history" / "c__3.json", [1, 2, 3])
        with self.assertRaises(cs.CaptureError) as ctx:
            STORE.read_record(self.sdir, cs.HISTORY, "c", 3)
        self.assertEqual((ctx.exception.label, ctx.exception.reason),
                         ("c__3.json", "not a JSON object"))

    def test_has_record(self) -> None:
        # A series is had when its chunk 0 is: a later chunk alone is not the case's series.
        _write(self.sdir / "series" / "c__0.json")
        _write(self.sdir / "series" / "d__1.json")
        self.assertTrue(STORE.has_record(self.sdir, cs.SERIES, "c"))
        self.assertFalse(STORE.has_record(self.sdir, cs.SERIES, "d"))
        self.assertFalse(STORE.has_record(self.sdir, cs.INITIAL, "c"))


class SeriesChunkTests(unittest.TestCase):
    """A case's series is its chunks `series/<C>__<i>.json` joined in the order written."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sdir = Path(self._tmp.name)

    def _chunk(self, case: str, i: int, rows: list[int], **extra: object) -> None:
        _write(self.sdir / "series" / f"{case}__{i}.json",
               {"step": rows, "t": [0.5 * r for r in rows], "h_min": [float(r) for r in rows],
                **extra})

    def _refusal(self, case: str = "c") -> cs.CaptureError:
        with self.assertRaises(cs.CaptureError) as ctx:
            STORE.read_record(self.sdir, cs.SERIES, case)
        return ctx.exception

    def test_the_chunks_join_in_numeric_order(self) -> None:
        # Kills: joining in file-name order (`c__10` sorts before `c__2`), so the rows of chunk
        # 10 would read before those of chunk 2; and reading chunk 0 alone.
        for i in range(11):
            self._chunk("c", i, [i])
        self._chunk("cc", 0, [99])
        self.assertEqual(STORE.read_record(self.sdir, cs.SERIES, "c"),
                         {"step": list(range(11)), "t": [0.5 * i for i in range(11)],
                          "h_min": [float(i) for i in range(11)]})

    def test_a_missing_middle_chunk_is_refused(self) -> None:
        # Kills: dropping the gap check in `_read_series` (chunks 0 and 2 would read as a
        # shorter series that skips chunk 1's rows).
        self._chunk("c", 0, [1])
        self._chunk("c", 2, [3])
        exc = self._refusal()
        self.assertTrue(exc.absent)
        self.assertEqual(exc.label, "c__1.json")

    def test_a_member_that_is_no_list_is_refused(self) -> None:
        # Kills: dropping the non-list check (a scalar member would raise inside `extend`, or
        # a string be joined character by character).
        self._chunk("c", 0, [1])
        self._chunk("c", 1, [2], h_min=2.0)
        exc = self._refusal()
        self.assertEqual((exc.label, exc.reason, exc.absent),
                         ("c__1.json", "not an object of lists", False))

    def test_a_chunk_whose_members_differ_from_the_first_is_refused(self) -> None:
        # Kills: dropping the member-set check: a name missing from a later chunk would read as
        # a column shorter than the rest, and one added there as a column of only its rows.
        self._chunk("c", 0, [1])
        self._chunk("c", 1, [2], h_max=[2.0])
        exc = self._refusal()
        self.assertEqual(exc.label, "c__1.json")
        self.assertTrue(exc.reason.startswith("unreadable: it holds "), exc.reason)

    def test_no_chunk_zero_is_absent(self) -> None:
        # Kills: reading a series whose first chunk is missing from its later ones.
        self._chunk("c", 1, [1])
        exc = self._refusal()
        self.assertTrue(exc.absent)
        self.assertEqual(self._refusal("none").label, "none__0.json")

    def test_a_case_that_committed_no_row_reads_as_an_empty_series(self) -> None:
        # Kills: refusing (or skipping) a chunk of empty lists — every case has a series, an
        # empty one where it committed no row (the harness's `capture_close`).
        _write(self.sdir / "series" / "c__0.json", {"step": [], "t": [], "h_min": []})
        self.assertEqual(STORE.read_record(self.sdir, cs.SERIES, "c"),
                         {"step": [], "t": [], "h_min": []})


class SeriesPrefixTests(unittest.TestCase):
    """`read_series_prefix` (issue #644 P4b): what a case that did not finish kept of its
    series — the rows of its chunks from chunk 0 up to the first that is absent, does not
    decode, holds other members than chunk 0, or whose `step` column does not continue the one
    before it by 1 — and the last step among them."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sdir = Path(self._tmp.name)

    def _chunk(self, i: int, rows: list, case: str = "c", **extra: object) -> None:
        _write(self.sdir / "series" / f"{case}__{i}.json",
               {"step": rows, "t": [0.5] * len(rows), "h_min": [1.0] * len(rows), **extra})

    def test_every_chunk_in_order_is_kept(self) -> None:
        for i, rows in enumerate(([1, 2], [3], [4, 5])):
            self._chunk(i, rows)
        self._chunk(0, [7], case="cc")
        rows, last = STORE.read_series_prefix(self.sdir, "c")
        self.assertEqual(rows["step"], [1, 2, 3, 4, 5])
        self.assertEqual(len(rows["h_min"]), 5)
        self.assertEqual(last, 5)

    def test_it_stops_at_the_first_chunk_it_cannot_continue_with(self) -> None:
        """Kills: dropping any one stop: the gap, the undecodable chunk, the member set, the
        non-list member, the step that does not continue by 1 (a repeat or a jump), or a
        boolean step."""
        def undecodable() -> None:
            (self.sdir / "series" / "c__1.json").write_text("{")
            self._chunk(2, [3])   # a later chunk that would continue the rows is not read
        for label, write in (
                ("a gap", lambda: self._chunk(2, [3])),
                ("undecodable", undecodable),
                ("other members", lambda: self._chunk(1, [3], h_max=[1.0])),
                ("a member no list", lambda: self._chunk(1, [3], h_min=1.0)),
                ("a repeated step", lambda: self._chunk(1, [2, 3])),
                ("a jump", lambda: self._chunk(1, [4])),
                ("a boolean step", lambda: self._chunk(1, [True])),
                ("a boolean step equal to the next", None),
                ("a step no number", lambda: self._chunk(1, ["3"])),
                # the layout writes `step` as an integer token: a float is no step, and a
                # non-finite one must not raise (a reviewer's crash on `int(nan)`)
                ("a fractional step", lambda: self._chunk(1, [3.5])),
                ("a step that is not finite", lambda: self._chunk(1, [float("nan")])),
                ("an infinite step", lambda: self._chunk(1, [float("inf")]))):
            with self.subTest(label):
                for path in (self.sdir / "series").glob("*") if (self.sdir / "series").exists() \
                        else []:
                    path.unlink()
                if write is None:
                    # `True == 1`: a boolean is no step even where it equals the next one
                    self._chunk(0, [0])
                    self._chunk(1, [True])
                    self.assertEqual(STORE.read_series_prefix(self.sdir, "c"),
                                     ({"step": [0], "t": [0.5], "h_min": [1.0]}, 0))
                    continue
                self._chunk(0, [1, 2])
                write()
                rows, last = STORE.read_series_prefix(self.sdir, "c")
                self.assertEqual(rows["step"], [1, 2])
                self.assertEqual(last, 2)

    def test_nothing_kept_is_no_last_step(self) -> None:
        """Kills: a `last_step` read from a series with no row or no `step` column."""
        self.assertEqual(STORE.read_series_prefix(self.sdir, "c"), ({}, None))
        # a first chunk whose one step is no integer keeps nothing, and does not raise
        for bad in (float("nan"), float("inf"), 2.5):
            self._chunk(0, [bad])
            self.assertEqual(STORE.read_series_prefix(self.sdir, "c"), ({}, None), bad)
            (self.sdir / "series" / "c__0.json").unlink()
        self._chunk(0, [])
        self.assertEqual(STORE.read_series_prefix(self.sdir, "c")[1], None)
        _write(self.sdir / "series" / "d__0.json", {"t": [0.5], "h_min": [1.0]})
        rows, last = STORE.read_series_prefix(self.sdir, "d")
        self.assertEqual((rows, last), ({"t": [0.5], "h_min": [1.0]}, None))
        (self.sdir / "series" / "e__0.json").write_text("{")
        self.assertEqual(STORE.read_series_prefix(self.sdir, "e"), ({}, None))


class EnumerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sdir = Path(self._tmp.name)

    def test_case_ids_are_every_file_of_the_group_in_file_name_order(self) -> None:
        # Kills: listing the snapshot schema as a case; sorting by id (`a` before `a-b`: the
        # store lists cases in file-name order, where `a-b.json` sorts first);
        # reading another group's directory; counting a non-capture file.
        for name in ("snapshot_schema.json", "a.json", "a-b.json", "zz.json", "note.txt"):
            _write(self.sdir / name)
        _write(self.sdir / "initial" / "a.json")
        # a series is listed by its chunk 0 alone, once whatever its chunks
        for name in ("zz__0.json", "zz__1.json", "a__1.json", "b__10.json", "a-b__0.json"):
            _write(self.sdir / "series" / name)
        self.assertEqual(STORE.case_ids(self.sdir), ["a-b", "a", "zz"])
        self.assertEqual(STORE.case_ids(self.sdir, cs.INITIAL), ["a"])
        self.assertEqual(STORE.case_ids(self.sdir, cs.SERIES), ["a-b", "zz"])
        self.assertEqual(STORE.case_ids(self.sdir / "absent"), [])

    def test_case_ids_of_history_is_not_a_question(self) -> None:
        for group in (cs.HISTORY, cs.RESTART_POINT):
            with self.subTest(group=group), self.assertRaises(ValueError):
                STORE.case_ids(self.sdir, group)

    def test_restart_point_steps_are_the_cases_own_restart_points_ascending(self) -> None:
        # Kills: `restart_point_steps` reading `history/` (or the reverse), a prefix match and
        # a lexical sort — the rules `history_steps` holds, on the other group.
        for name in ("a__9.json", "a__10.json", "a__1__5.json", "b__3.json"):
            _write(self.sdir / "restart_point" / name)
        _write(self.sdir / "history" / "a__4.json")
        self.assertEqual(STORE.restart_point_steps(self.sdir, "a"), [9, 10])
        self.assertEqual(STORE.restart_point_steps(self.sdir, "a__1"), [5])
        self.assertEqual(STORE.history_steps(self.sdir, "a"), [4])
        for step in (9, 10):
            self.assertTrue(STORE.has_record(self.sdir, cs.RESTART_POINT, "a", step))

    def test_history_steps_are_the_cases_own_samples_ascending(self) -> None:
        # Kills: a prefix match (`a__1__5` is step 5 of case `a__1`, never of `a`); a lexical
        # sort (10 before 9); reading a leading-zero spelling `record_path` never produces as a
        # sample (it would be listed and then read as absent).
        for name in ("a__9.json", "a__10.json", "a__0.json", "a__1__5.json", "a__007.json",
                     "a__x.json", "b__3.json"):
            _write(self.sdir / "history" / name)
        self.assertEqual(STORE.history_steps(self.sdir, "a"), [0, 9, 10])
        self.assertEqual(STORE.history_steps(self.sdir, "a__1"), [5])
        self.assertEqual(STORE.history_steps(self.sdir, "none"), [])
        for step in STORE.history_steps(self.sdir, "a"):
            self.assertTrue(STORE.has_record(self.sdir, cs.HISTORY, "a", step))

    def test_case_paths_are_the_cases_files_that_exist(self) -> None:
        # Kills: owning another case's file (the continuation merge moves exactly these), or
        # leaving one of the case's own behind.
        for rel in ("c.json", "initial/c.json", "series/c__0.json", "series/c__1.json",
                    "history/c__2.json", "history/c__4.json", "history/c__x.json",
                    "restart_point/c__3.json", "cc.json", "history/cc__2.json",
                    "initial/cc.json", "series/cc__0.json", "restart_point/cc__3.json"):
            _write(self.sdir / rel)
        got = sorted(p.relative_to(self.sdir).as_posix() for p in STORE.case_paths(self.sdir, "c"))
        self.assertEqual(got, ["c.json", "history/c__2.json", "history/c__4.json",
                               "initial/c.json", "restart_point/c__3.json", "series/c__0.json",
                               "series/c__1.json"])
        self.assertEqual(STORE.case_paths(self.sdir, "absent"), [])

    def test_all_case_paths_are_every_file_of_the_suffix_at_any_depth(self) -> None:
        # Kills: narrowing a comparand's evidence hash to the declared layout (a stale file of
        # an undeclared case moves the hash today).
        for rel in ("c.json", "history/c__2.json", "deep/er/x.json", "snapshot_schema.json"):
            _write(self.sdir / rel)
        (self.sdir / "c.txt").write_text("x", encoding="utf-8")
        got = [p.relative_to(self.sdir).as_posix() for p in STORE.all_case_paths(self.sdir)]
        self.assertEqual(got, ["c.json", "deep/er/x.json", "history/c__2.json",
                               "snapshot_schema.json"])
        self.assertEqual(STORE.all_case_paths(self.sdir / "absent"), [])


class CopyAndMoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name) / "src"
        self.dst = Path(self._tmp.name) / "dst"
        self.dst.mkdir()

    def _tree(self, root: Path) -> list[str]:
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())

    def test_copy_run_is_selective_and_ordered(self) -> None:
        # Kills: a blind copytree (a debug dump or a nested file promoted as evidence); a
        # changed order of the names returned (they become `raw_artifact_refs`).
        for rel in ("b.json", "a.json", "initial/a.json", "history/a__2.json",
                    "restart_point/a__2.json", "series/a__0.json", "debug.txt", "deep/x.json",
                    "history/note.txt"):
            _write(self.src / rel)
        copied = STORE.copy_run(self.src, self.dst)
        self.assertEqual(copied, ["a.json", "b.json", "initial/a.json", "history/a__2.json",
                                  "restart_point/a__2.json", "series/a__0.json"])
        self.assertEqual(self._tree(self.dst), sorted(copied))

    def test_copy_run_removes_an_earlier_attempts_group_files_and_keeps_its_finals(self) -> None:
        # Kills: leaving a sample of another step to be read as this run's; and deleting a
        # final record this run did not rewrite (today's rule: finals are overwritten by name).
        _write(self.dst / "old.json", {"t": 9})
        _write(self.dst / "a.json", {"t": 9})
        _write(self.dst / "history" / "a__99.json")
        _write(self.dst / "initial" / "old.json")
        _write(self.dst / "series" / "old__0.json")
        _write(self.dst / "restart_point" / "a__99.json")
        _write(self.src / "a.json", {"t": 1})
        _write(self.src / "history" / "a__2.json")
        STORE.copy_run(self.src, self.dst)
        self.assertEqual(self._tree(self.dst), ["a.json", "history/a__2.json", "old.json"])
        self.assertEqual(json.loads((self.dst / "a.json").read_text()), {"t": 1})

    def test_copy_run_creates_no_empty_group_directory(self) -> None:
        # Kills: `copy_run` creating a group directory it copies nothing into (the promoted
        # tree holds what the run wrote and no more).
        _write(self.src / "a.json")
        STORE.copy_run(self.src, self.dst)
        self.assertEqual(sorted(p.name for p in self.dst.iterdir()), ["a.json"])

    def test_move_case_moves_exactly_the_cases_files(self) -> None:
        # Kills: moving a failed sibling continuation's captures with it (`c__x`, `cc`).
        # Also: leaving a restart point or a later series chunk behind (`case_paths`).
        for rel in ("c.json", "initial/c.json", "series/c__0.json", "series/c__1.json",
                    "history/c__2.json", "restart_point/c__2.json", "history/c__x.json",
                    "cc.json", "initial/cc.json", "restart_point/cc__2.json"):
            _write(self.src / rel)
        self.assertIsNone(STORE.move_case(self.src, self.dst, "c"))
        self.assertEqual(self._tree(self.dst),
                         ["c.json", "history/c__2.json", "initial/c.json",
                          "restart_point/c__2.json", "series/c__0.json", "series/c__1.json"])
        self.assertEqual(self._tree(self.src), ["cc.json", "history/c__x.json", "initial/cc.json",
                                                "restart_point/cc__2.json"])

    def test_move_case_refuses_a_collision_and_moves_nothing(self) -> None:
        # Kills: overwriting a first-pass capture; a partial move before the refusal.
        for rel in ("c.json", "history/c__2.json"):
            _write(self.src / rel)
        _write(self.dst / "history" / "c__2.json")
        why = STORE.move_case(self.src, self.dst, "c")
        self.assertEqual(why, "its capture c__2.json names a file the first pass wrote "
                              "(history/c__2.json)")
        self.assertEqual(self._tree(self.src), ["c.json", "history/c__2.json"])
        self.assertEqual(self._tree(self.dst), ["history/c__2.json"])


class LocateTests(unittest.TestCase):
    def test_a_final_record_and_a_restart_point_are_located_and_a_sample_is_not(self) -> None:
        # Issue #644 P4b: a run restarts from a restart point (the records that hold every
        # restart variable) or its final record; a history sample holds only the variables due
        # at its step, so it is no longer located. Kills: `locate` left on `history/`.
        sdir = Path("/run/raw/state_snapshots")
        self.assertEqual(STORE.locate(sdir / "c.json", "c"), (sdir, cs.FINAL, None))
        self.assertEqual(STORE.locate(sdir / "restart_point" / "c__41.json", "c"),
                         (sdir, cs.RESTART_POINT, 41))
        self.assertIsNone(STORE.locate(sdir / "history" / "c__41.json", "c"))

    def test_another_cases_file_or_another_place_is_not(self) -> None:
        # Kills: restarting from another case's capture, or from a restart-point-named file
        # outside `restart_point/`. (An `initial/<C>` file is named like a final record; the caller holds the
        # located directory to `<run>/raw/state_snapshots`, `run_configuration._restart_source`.)
        sdir = Path("/run/raw/state_snapshots")
        for path in (sdir / "d.json", sdir / "history" / "d__41.json", sdir / "c__41.json",
                     sdir / "series" / "c__41.json", sdir / "history" / "c__x.json",
                     sdir / "c.txt", sdir / "history" / "c__1__41.json",
                     sdir / "restart_point" / "d__41.json", sdir / "restart_point" / "c__x.json",
                     sdir / "restart_point" / "c__1__41.json", sdir / "initial" / "c__41.json"):
            self.assertIsNone(STORE.locate(path, "c"), path)


class _SpyStore:
    """The json store under another name, recording `(name, function, first argument)` of
    every call: which store a reader asked, and of which directory or file."""

    def __init__(self, name: str, log: list[tuple[str, str, object]]) -> None:
        self._name = name
        self._log = log

    def __getattr__(self, attr: str) -> object:
        target = getattr(STORE, attr)
        if not callable(target):
            return target

        def call(*args: object, **kwargs: object) -> object:
            self._log.append((self._name, attr, args[0] if args else None))
            return target(*args, **kwargs)
        return call


class CaptureStoreReachTests(unittest.TestCase):
    """Each reader and writer of a run's captures asks the store of the format it was HANDED —
    the format of the target that wrote the directory — and reads nothing around it. Two spy
    stores stand for two formats (`own`, the reading run's, and `theirs`, a comparand's); both
    decode json, so a reader that built a path itself would still pass its own tests and is
    caught here by the directory it never asked a store about.

    Pinned: the entry points driven below. Not driven here (their own files drive them with
    the json store): `Validate.execute` end to end, the validator's `--stage post_execute`
    (`_execution_output_format` is pinned below), `tools/run_configuration.py`'s run."""

    def setUp(self) -> None:
        from unittest import mock
        self.log: list[tuple[str, str, object]] = []
        spies = {name: _SpyStore(name, self.log) for name in ("own", "theirs")}
        patcher = mock.patch.object(cs, "store", side_effect=lambda fmt: spies[fmt])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _asked(self, root: Path) -> set[str]:
        """The stores asked about anything under ``root``."""
        return {name for name, _fn, arg in self.log
                if isinstance(arg, Path) and (arg == root or root in arg.parents)}

    def _runs(self) -> tuple[object, object, dict]:
        from tools.tests import test_primary_evidence as tpe
        own, other = tpe._RunDir(), tpe._RunDir()
        self.addCleanup(own.cleanup)
        self.addCleanup(other.cleanup)
        for i, cid in enumerate("ab"):
            field = tpe._field(i)
            own.write_state(cid, field, field)
            other.write_state(cid, field, field)
        return own, other, tpe._ir([tpe.MASS, tpe.CROSS])

    @staticmethod
    def _verdict_ir() -> dict:
        """An IR whose every test has its predicates, so the host verdict is evaluable."""
        from tools.tests import test_primary_evidence as tpe
        return tpe._ir([tpe.MASS, tpe.HMIN, tpe.SYM])

    def test_the_verdict_reads_each_run_through_its_writers_store(self) -> None:
        # Kills: reading a comparand's run with the asking run's format (`env_for` handing
        # `output_format` instead of `comp.output_format`), and the reverse.
        from tools import primary_evidence as pe
        own, other, ir = self._runs()
        comparand = pe.ComparandEvidence(
            target_id="t2", pipeline_ref="p", run_id="r", run_dir=other.root,
            output_format="theirs")
        records = pe.evaluate_primary_predicates(
            ir, own.root, output_format="own", comparands=[comparand])
        self.assertTrue(all(r["satisfied"] for r in records), records)
        self.assertEqual(self._asked(own.root), {"own"})
        self.assertEqual(self._asked(other.root), {"theirs"})
        self.assertIn(("own", "read_record", cs.snapshot_dir(own.root)), self.log)
        # the format is how the directory is read, not part of the record
        self.assertNotIn("output_format", comparand.detail())

    def test_the_stores_own_reader_reads_each_case_of_both_capture_points(self) -> None:
        # Re-hosted (issue #724 PR-2) from the `validate.judge` excerpt's row, whose vehicle went
        # with the judge: the store's own reader, asked of the run it is handed, lists the
        # cases and reads each one's final and initial record. Kills: a store that lists or
        # decodes a directory it was not handed.
        own, _other, _ir = self._runs()
        store = cs.store("own")
        sdir = cs.snapshot_dir(own.root)
        case_ids = list(store.case_ids(sdir))
        self.assertEqual(case_ids, ["a", "b"])
        for cid in case_ids:
            self.assertTrue(store.read_record(sdir, cs.FINAL, cid))
            self.assertTrue(store.has_record(sdir, cs.INITIAL, cid))
            self.assertTrue(store.read_record(sdir, cs.INITIAL, cid))
        self.assertEqual(self._asked(own.root), {"own"})
        self.assertEqual({fn for _n, fn, _a in self.log} & {"case_ids", "read_record"},
                         {"case_ids", "read_record"})

    def test_the_conductors_static_helpers_read_through_the_format_they_are_handed(self) -> None:
        # Kills: `_shape_verdict` / `_shape_state_agreement` / `_merge_continuation_captures`
        # dropping the format they are passed for a constant.
        from tools import workflow_conductor as wc
        own, other, ir = self._runs()
        self.assertIsNotNone(wc.Conductor._shape_verdict(self._verdict_ir(), own.root, "own"))
        agreement = wc.Conductor._shape_state_agreement(ir, own.root, "s1", other.root, "own")
        self.assertEqual(agreement["status"], "agree", agreement)
        self.assertEqual(self._asked(own.root) | self._asked(other.root), {"own"})
        self.log.clear()
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp)
            self.assertIsNone(wc.Conductor._merge_continuation_captures(
                other.root, dst, "a", "theirs"))
            self.assertTrue((cs.snapshot_dir(dst) / "a.json").is_file())
        self.assertEqual({(n, fn) for n, fn, _a in self.log if fn == "move_case"},
                         {("theirs", "move_case")})

    def test_the_conductors_writers_use_the_targets_format(self) -> None:
        # Kills: promotion, the snapshot schema, the deliverable check or the run directories
        # naming a format other than `self.target.output_format`.
        import types

        from tools import workflow_conductor as wc
        own, _other, ir = self._runs()
        stub = types.SimpleNamespace(
            target=types.SimpleNamespace(output_format="theirs"),
            _rel=lambda p: Path(p).name, _conductor_authors_runner=lambda refs: True)
        with tempfile.TemporaryDirectory() as tmp:
            node = Path(tmp) / "node"
            refs = wc.Conductor._promote_run_evidence(stub, own.root, node, ["state_snapshots"])
            self.assertEqual([r.split("state_snapshots/")[1] for r in refs],
                             ["a.json", "b.json", "initial/a.json", "initial/b.json"])
            wc.Conductor._author_snapshot_schema(stub, ir, node)
            self.assertEqual(json.loads((cs.snapshot_dir(node) / "snapshot_schema.json")
                                        .read_text())["samples"], ["a.json", "b.json"])
        gap = wc.Conductor._snapshot_deliverable_gap(
            stub, cs.snapshot_dir(own.root), ["a", "b", "c"], ["state_snapshots"],
            initial_required=True)
        self.assertIn("missing=['c.json', 'initial/c.json']", gap)
        self.assertNotIn("or commit no record", gap)
        self.assertEqual(wc.Conductor._run_raw_dirs(stub, None), cs.run_dirs("theirs"))
        self.assertEqual({n for n, _fn, _a in self.log}, {"theirs"})
        self.assertEqual({fn for _n, fn, _a in self.log}
                         & {"copy_run", "case_ids", "expected_layout", "run_dirs"},
                         {"copy_run", "case_ids", "expected_layout", "run_dirs"})

    def test_a_restart_source_is_located_and_read_by_the_targets_store(self) -> None:
        # Kills: `run_configuration` parsing the capture path or decoding the file itself.
        from tools import run_configuration as rc
        own, _other, _ir = self._runs()
        final = cs.snapshot_dir(own.root) / "a.json"
        source = rc._restart_source(final, "a", "own")
        self.assertEqual((source.run_dir, source.kind), (own.root, cs.FINAL))
        self.assertEqual(source.point, json.loads(final.read_text()))
        self.assertEqual([(n, fn) for n, fn, _a in self.log],
                         [("own", "locate"), ("own", "read_record")])
        # a capture named like a final record that is not in a run's capture directory
        with self.assertRaisesRegex(rc.ConfigurationRefused, "not under <run>/raw/state_snap"):
            rc._restart_source(cs.snapshot_dir(own.root) / "initial" / "a.json", "a", "own")
        with self.assertRaisesRegex(rc.ConfigurationRefused, "no restart source of 'b'"):
            rc._restart_source(final, "b", "own")


class ValidatorFormatResolutionTests(unittest.TestCase):
    def test_the_post_execute_gate_reads_in_the_pipeline_targets_format(self) -> None:
        # Kills: the validator reading every run as json whatever the target's profile says;
        # and guessing a format for a pipeline path that names no target.
        import yaml

        from tools import target_profile as tp
        from tools import validate_pipeline_semantics as vps
        repo = SAMPLE_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = yaml.safe_load((repo / tp.TARGETS_DIR / "fortran_cpu.yaml").read_text())
            doc["target_id"] = "t_other"
            doc["output"] = {"format": "other_format"}
            (root / tp.TARGETS_DIR).mkdir(parents=True)
            (root / tp.TARGETS_DIR / "t_other.yaml").write_text(yaml.safe_dump(doc))
            safe = "component__x__0.1.0"
            for target_id, expected in (("t_other", "other_format"),
                                        ("t_missing", tp.DEFAULT_OUTPUT_FORMAT)):
                pipeline = root / "workspace" / "pipelines" / safe / target_id / "x_20260101_001"
                execution = vps.NodeExecution(
                    node_key="component/x@0.1.0", node_dir=pipeline / "runs" / "r" / safe,
                    exec_dir=pipeline / "runs" / "r", pipeline_dir=pipeline)
                with self.subTest(target=target_id):
                    self.assertEqual(vps._execution_output_format(root, execution), expected)
            legacy = root / "workspace" / "pipelines" / safe / "x_20260101_001"
            execution = vps.NodeExecution(
                node_key="component/x@0.1.0", node_dir=legacy / "runs" / "r" / safe,
                exec_dir=legacy / "runs" / "r", pipeline_dir=legacy)
            self.assertEqual(vps._execution_output_format(root, execution),
                             tp.DEFAULT_OUTPUT_FORMAT)


class NeutralCaptureLiteralTests(unittest.TestCase):
    """No neutral module spells a capture file's place itself. A SAMPLE of three spellings over
    every neutral `tools/*.py` but the seam, read by `ast` (docstrings and comments are not
    code): a string that continues the capture directory with a file or group name, a path
    built with `/` or `joinpath` from the capture directory's name or a group directory's, and
    a glob by a store's suffix in a module on the capture path — the directories and suffixes
    of every declared store. What it does not see: a name computed at run time, and a reader
    that never names the directory at all — `CaptureStoreReachTests` is the row for those. A fourth rule, over the capture-path modules: none names a declared
    format by its id in a call argument, an assignment or a parameter default — the format is
    the target's, and with one declared member a literal reads the same as the right answer in
    every other test. A returned literal is not seen."""

    #: The neutral modules that read or place captures (the glob rule applies to these alone:
    #: other modules glob JSON of their own).
    _CAPTURE_PATH_MODULES = frozenset({
        "tools/primary_evidence.py", "tools/workflow_conductor.py",
        "tools/validate_pipeline_semantics.py", "tools/raw_evidence_excerpt.py",
        "tools/run_configuration.py", "tools/workshop_wrapper.py",
        "tools/orchestration_runtime.py", "tools/runner_ir.py", "tools/spec_input_gates.py",
        "tools/remote_execution.py", "tools/host_render.py"})
    #: `state_snapshots/` followed by a name: a file or a group below the capture directory.
    _CONTINUED = __import__("re").compile(r"state_snapshots/[A-Za-z0-9_<{*$]")
    #: The capture directory's name and every declared store's directories below it.
    _PATH_PARTS = frozenset({cs.SNAPSHOT_DIR_PARTS[-1]} | {
        sub for fmt in registry.backend_ids(cs.AXIS) for sub in cs.store(fmt).run_dirs()})
    #: Every file of a directory by a declared store's suffix.
    _SUFFIX_GLOBS = frozenset(
        "*" + cs.store(fmt).record_path(Path(), cs.FINAL, "c").suffix
        for fmt in registry.backend_ids(cs.AXIS))
    #: The declared formats: a capture-path module that names one of these ids reads every
    #: target's run in that format, whatever the target writes.
    _FORMAT_IDS = frozenset(registry.backend_ids(cs.AXIS))

    @staticmethod
    def _findings(rel: str, source: str, *, globs: bool) -> list[str]:
        import ast
        tree = ast.parse(source)
        docstrings = {
            id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)}
        found: list[tuple[int, str]] = []
        # An f-string is read whole, each interpolation as `{}`: `f".../state_snapshots/{cid}"`
        # continues the directory though no single piece of it does.
        in_fstring = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
                      for part in node.values}
        for node in ast.walk(tree):
            text = None
            if isinstance(node, ast.JoinedStr):
                text = "".join(str(part.value) if isinstance(part, ast.Constant) else "{}"
                               for part in node.values)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in docstrings and id(node) not in in_fstring:
                text = node.value
            if text is not None and NeutralCaptureLiteralTests._CONTINUED.search(
                    text.replace("state_snapshots/" + cs.SNAPSHOT_SCHEMA_FILENAME, "")):
                found.append((node.lineno, "a string continues the capture directory "
                                           f"({text!r:.80})"))
            operands: list[ast.expr] = []
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                operands = [node.left, node.right]
            elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "joinpath":
                operands = list(node.args)
            for operand in operands:
                if isinstance(operand, ast.Constant) \
                        and operand.value in NeutralCaptureLiteralTests._PATH_PARTS:
                    found.append((operand.lineno, f"a path is built from {operand.value!r}"))
            # every JSON file of a directory, by suffix (a glob for one NAMED file, such as
            # `*/step_result.json`, lists no capture)
            if globs and isinstance(node, ast.Call) \
                    and getattr(node.func, "attr", "") in ("glob", "rglob") and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and str(node.args[0].value).rsplit("/", 1)[-1] in \
                    NeutralCaptureLiteralTests._SUFFIX_GLOBS:
                found.append((node.lineno, "a capture-suffix glob on the capture path"))
            # a format named by its id where the code wants the target's
            # (`target.output_format`): handed to a call, or bound to a name. A comparison, a
            # list element or a mapping value that happens to equal an id is none.
            handed: list[ast.expr | None] = []
            if isinstance(node, ast.Call):
                handed = [*node.args, *(kw.value for kw in node.keywords)]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                handed = [node.value]
            elif isinstance(node, ast.arguments):
                handed = [*node.defaults, *node.kw_defaults]
            for value in handed if globs else ():
                if isinstance(value, ast.Constant) \
                        and value.value in NeutralCaptureLiteralTests._FORMAT_IDS:
                    found.append((value.lineno, f"the format {value.value!r} is named by its id"))
        return [f"{rel}:{lineno}: {what}" for lineno, what in sorted(set(found))]

    def test_the_instrument_sees_each_spelling(self) -> None:
        # The self-test: each of the spellings this change removed is found, and what stays
        # legitimate (the schema file, the artifact token, a docstring, a case file) is not.
        seen = self._findings("x.py", (
            'def f(run, cid, sdir):\n'
            '    "raw/state_snapshots/initial/<case_id>.json in a docstring"\n'
            '    a = f"{run}/raw/state_snapshots/{cid}.json"\n'
            '    b = run / "raw" / "state_snapshots"\n'
            '    c = sdir / "initial" / "x"\n'
            '    d = sdir.joinpath("history")\n'
            '    e = sdir.glob("*.json")\n'
            '    g = "write raw/state_snapshots/<case_id>.json per case"\n'
            '    ok1 = f"{run}/raw/state_snapshots/snapshot_schema.json"\n'
            '    ok2 = "state_snapshots" in run\n'
            '    ok3 = "under raw/state_snapshots/)"\n'
            '    ok4 = run / "case_inputs" / f"{cid}.json"\n'
            '    ok5 = sdir.glob("*/step_result.json")\n'
            '    h = read(run, "json")\n'
            '    i = read(run, output_format="json")\n'
            '    fmt = "json"\n'
            '    ok6 = f"a json file of {run}"\n'
            '    ok7 = fmt == "json"\n'
            '    ok8 = launch(["tool", "--output-format", "json"])\n'
            '    ok9 = {"type": "json"}\n'
            'def g(run, fmt="json"):\n'
            '    pass\n'), globs=True)
        self.assertEqual([f.split(": ", 1)[0] for f in seen],
                         ["x.py:3", "x.py:4", "x.py:5", "x.py:6", "x.py:7", "x.py:8",
                          "x.py:14", "x.py:15", "x.py:16", "x.py:21"], seen)
        # the glob rule is the capture-path modules' alone
        self.assertEqual(self._findings("x.py", 'x = d.glob("*.json")\n', globs=False), [])

    def test_no_neutral_module_spells_a_capture_path(self) -> None:
        # Kills: a reader moved back onto a path of its own (the fail-open a second format
        # would meet: a reader left on one format's layout finds nothing of the other's).
        from tools.tests.test_backend_boundary import REPO_ROOT, neutral_core_files
        seam = REPO_ROOT / "tools" / "capture_store.py"
        scanned: list[str] = []
        found: list[str] = []
        for path in neutral_core_files():
            if path.suffix != ".py" or path == seam:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            scanned.append(rel)
            found += self._findings(rel, path.read_text(encoding="utf-8"),
                                    globs=rel in self._CAPTURE_PATH_MODULES)
        self.assertEqual(found, [])
        # the modules the glob rule names are among the scanned ones (a renamed module would
        # otherwise drop out of the rule silently)
        self.assertEqual(sorted(self._CAPTURE_PATH_MODULES - set(scanned)), [])


if __name__ == "__main__":
    unittest.main()
