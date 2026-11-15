#!/usr/bin/env python3
"""Tests for the capture store seam (`tools/capture_store.py`) and the `json` output format's
store (`tools/backends/output_format/json/store.py`) — issue #644, Part 3 P3.

Each row names the mutation it kills. The readers that go through the store are tested where
they live (`test_primary_evidence.py`, `test_validate_pipeline_semantics.py`,
`test_workflow_conductor.py`, `test_raw_evidence_excerpt.py`, `test_run_configuration.py`);
`CaptureStoreReachTests` here drives each of them through a recording store.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import capture_store as cs
from tools.backends import registry

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
            cs.store("netcdf")

    def test_run_dirs_of_a_hand_authored_runner_is_the_capture_directory_alone(self) -> None:
        # Kills: creating the group directories for every runner (issue #368's rule).
        self.assertEqual(cs.run_dirs("json", host_rendered=False), (("raw", "state_snapshots"),))

    def test_run_dirs_of_a_host_rendered_runner_are_the_stores_directories(self) -> None:
        # Kills: dropping a group directory, or reordering them (the control file's recipe
        # renders them in this order).
        self.assertEqual(
            cs.run_dirs("json", host_rendered=True),
            (("raw", "state_snapshots", "initial"), ("raw", "state_snapshots", "history"),
             ("raw", "state_snapshots", "series")))

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
                self.assertEqual(required_python_modules({"language": language}), tuple(reader))
                with mock.patch.object(STORE, "REQUIRED_PYTHON_MODULES",
                                       (("store_mod", "store-dist"),)):
                    self.assertEqual(required_python_modules({"language": language}),
                                     (*reader, ("store_mod", "store-dist")))


class LayoutTests(unittest.TestCase):
    def test_record_paths(self) -> None:
        # Kills: any change of where a group's record sits or how a sample is named.
        sdir = Path("/x")
        self.assertEqual(STORE.record_path(sdir, cs.FINAL, "c"), sdir / "c.json")
        self.assertEqual(STORE.record_path(sdir, cs.INITIAL, "c"), sdir / "initial" / "c.json")
        self.assertEqual(STORE.record_path(sdir, cs.SERIES, "c"), sdir / "series" / "c.json")
        self.assertEqual(STORE.record_path(sdir, cs.HISTORY, "c", 12),
                         sdir / "history" / "c__12.json")

    def test_a_sample_needs_a_step_and_no_other_record_takes_one(self) -> None:
        # Kills: a history read without a step silently reading the final record's name.
        with self.assertRaises(ValueError):
            STORE.record_path(Path("/x"), cs.HISTORY, "c")
        with self.assertRaises(ValueError):
            STORE.record_path(Path("/x"), cs.FINAL, "c", 3)

    def test_expected_layout_names_the_once_per_case_files(self) -> None:
        self.assertEqual(STORE.expected_layout(), {
            "final": "<case_id>.json", "initial": "initial/<case_id>.json",
            "series": "series/<case_id>.json"})

    def test_format_facts_carry_the_suffix(self) -> None:
        self.assertEqual(STORE.FORMAT_FACTS["suffix"], ".json")
        self.assertEqual(STORE.REQUIRED_PYTHON_MODULES, ())


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
        _write(self.sdir / "series" / "c.json")
        self.assertTrue(STORE.has_record(self.sdir, cs.SERIES, "c"))
        self.assertFalse(STORE.has_record(self.sdir, cs.INITIAL, "c"))


class EnumerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sdir = Path(self._tmp.name)

    def test_case_ids_are_every_file_of_the_group_in_file_name_order(self) -> None:
        # Kills: listing the snapshot schema as a case; sorting by id (`a` before `a-b`: the
        # judge's excerpt lists cases in file-name order, where `a-b.json` sorts first);
        # reading another group's directory; counting a non-capture file.
        for name in ("snapshot_schema.json", "a.json", "a-b.json", "zz.json", "note.txt"):
            _write(self.sdir / name)
        _write(self.sdir / "initial" / "a.json")
        _write(self.sdir / "series" / "zz.json")
        self.assertEqual(STORE.case_ids(self.sdir), ["a-b", "a", "zz"])
        self.assertEqual(STORE.case_ids(self.sdir, cs.INITIAL), ["a"])
        self.assertEqual(STORE.case_ids(self.sdir, cs.SERIES), ["zz"])
        self.assertEqual(STORE.case_ids(self.sdir / "absent"), [])

    def test_case_ids_of_history_is_not_a_question(self) -> None:
        with self.assertRaises(ValueError):
            STORE.case_ids(self.sdir, cs.HISTORY)

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
        for rel in ("c.json", "initial/c.json", "series/c.json", "history/c__2.json",
                    "history/c__4.json", "history/c__x.json", "cc.json", "history/cc__2.json",
                    "initial/cc.json"):
            _write(self.sdir / rel)
        got = sorted(p.relative_to(self.sdir).as_posix() for p in STORE.case_paths(self.sdir, "c"))
        self.assertEqual(got, ["c.json", "history/c__2.json", "history/c__4.json",
                               "initial/c.json", "series/c.json"])
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
        for rel in ("b.json", "a.json", "initial/a.json", "history/a__2.json", "series/a.json",
                    "debug.txt", "deep/x.json", "history/note.txt"):
            _write(self.src / rel)
        copied = STORE.copy_run(self.src, self.dst)
        self.assertEqual(copied, ["a.json", "b.json", "initial/a.json", "history/a__2.json",
                                  "series/a.json"])
        self.assertEqual(self._tree(self.dst), sorted(copied))

    def test_copy_run_removes_an_earlier_attempts_group_files_and_keeps_its_finals(self) -> None:
        # Kills: leaving a sample of another step to be read as this run's; and deleting a
        # final record this run did not rewrite (today's rule: finals are overwritten by name).
        _write(self.dst / "old.json", {"t": 9})
        _write(self.dst / "a.json", {"t": 9})
        _write(self.dst / "history" / "a__99.json")
        _write(self.dst / "initial" / "old.json")
        _write(self.dst / "series" / "old.json")
        _write(self.src / "a.json", {"t": 1})
        _write(self.src / "history" / "a__2.json")
        STORE.copy_run(self.src, self.dst)
        self.assertEqual(self._tree(self.dst), ["a.json", "history/a__2.json", "old.json"])
        self.assertEqual(json.loads((self.dst / "a.json").read_text()), {"t": 1})

    def test_copy_run_creates_no_empty_group_directory(self) -> None:
        # Kills: a harness node's run gaining `initial/` (issue #368: its self-test may refuse
        # an entry it did not write).
        _write(self.src / "a.json")
        STORE.copy_run(self.src, self.dst)
        self.assertEqual(sorted(p.name for p in self.dst.iterdir()), ["a.json"])

    def test_move_case_moves_exactly_the_cases_files(self) -> None:
        # Kills: moving a failed sibling continuation's captures with it (`c__x`, `cc`).
        for rel in ("c.json", "initial/c.json", "series/c.json", "history/c__2.json",
                    "history/c__x.json", "cc.json", "initial/cc.json"):
            _write(self.src / rel)
        self.assertIsNone(STORE.move_case(self.src, self.dst, "c"))
        self.assertEqual(self._tree(self.dst),
                         ["c.json", "history/c__2.json", "initial/c.json", "series/c.json"])
        self.assertEqual(self._tree(self.src), ["cc.json", "history/c__x.json", "initial/cc.json"])

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
    def test_a_final_record_and_a_sample_are_located(self) -> None:
        sdir = Path("/run/raw/state_snapshots")
        self.assertEqual(STORE.locate(sdir / "c.json", "c"), (sdir, cs.FINAL, None))
        self.assertEqual(STORE.locate(sdir / "history" / "c__41.json", "c"),
                         (sdir, cs.HISTORY, 41))

    def test_another_cases_file_or_another_place_is_not(self) -> None:
        # Kills: restarting from another case's capture, or from a sample-named file outside
        # `history/`. (An `initial/<C>` file is named like a final record; the caller holds the
        # located directory to `<run>/raw/state_snapshots`, `run_configuration._source_run_dir`.)
        sdir = Path("/run/raw/state_snapshots")
        for path in (sdir / "d.json", sdir / "history" / "d__41.json", sdir / "c__41.json",
                     sdir / "series" / "c__41.json", sdir / "history" / "c__x.json",
                     sdir / "c.txt", sdir / "history" / "c__1__41.json"):
            self.assertIsNone(STORE.locate(path, "c"), path)


if __name__ == "__main__":
    unittest.main()
