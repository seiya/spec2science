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


class ControlFileRecipeGoldenTests(unittest.TestCase):
    """The `test` recipe's directory line, byte for byte as main `b3282e9b` rendered it (before
    the directories came from the store), for both runners. The two renderers are held equal
    to each other by `test_codegen_bundle.py`'s parity row; this holds one of them to the
    bytes, so the pair cannot drift together."""

    GOLDEN = {
        False: "\tmkdir -p $(RUNDIR)/raw/state_snapshots",
        True: ("\tmkdir -p $(RUNDIR)/raw/state_snapshots/initial "
               "$(RUNDIR)/raw/state_snapshots/history $(RUNDIR)/raw/state_snapshots/series"),
    }

    def test_the_rendered_recipe_is_unchanged(self) -> None:
        # Kills: a reordered or renamed directory, a changed separator, a directory added for
        # the hand-authored runner — each moves a rendered byte of every control file, and with
        # it a Generate output this change says it does not move.
        from tools import workflow_conductor as wc
        from tools.target_profile import load_target_profile
        repo = Path(__file__).resolve().parents[2]
        profile = load_target_profile(repo, "fortran_cpu")
        rules = wc.Conductor._control_file_rules(
            {**profile.toolchain, "backend": profile.parallel_backend}, None)
        control_file = registry.capability_module("build_system", "make", "control_file")
        for host_rendered, line in self.GOLDEN.items():
            with self.subTest(host_rendered=host_rendered):
                text = control_file.render_node(
                    rules=rules, compiler="gfortran", bin_name="x_runner",
                    cases_default="c1 c2", model_stem="x_model", runner_stem="x_runner",
                    closure=["dep_a", "dep_b"],
                    capture_dirs=cs.run_dirs(profile.output_format, host_rendered=host_rendered))
                self.assertEqual([ln for ln in text.split("\n")
                                  if ln.startswith("\tmkdir -p $(RUNDIR)")], [line])


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
            evidence=pe.comparand_evidence_sha256(other.root, "theirs"), output_format="theirs")
        records = pe.evaluate_primary_predicates(
            ir, own.root, output_format="own", comparands=[comparand])
        self.assertTrue(all(r["satisfied"] for r in records), records)
        self.assertEqual(self._asked(own.root), {"own"})
        self.assertEqual(self._asked(other.root), {"theirs"})
        self.assertIn(("theirs", "all_case_paths", cs.snapshot_dir(other.root)), self.log)
        self.assertIn(("own", "read_record", cs.snapshot_dir(own.root)), self.log)
        # the format is how the directory is read, not part of the record
        self.assertNotIn("output_format", comparand.detail())

    def test_the_judges_excerpt_reads_through_the_store_it_is_handed(self) -> None:
        # Kills: the excerpt listing or decoding captures by itself.
        from tools.raw_evidence_excerpt import raw_evidence_excerpt
        own, _other, ir = self._runs()
        excerpt = raw_evidence_excerpt(own.root / "raw", ir["io_contract"], output_format="own")
        self.assertEqual([c["case_id"] for c in excerpt["state_snapshots"]["cases"]], ["a", "b"])
        self.assertTrue(all(c["read"] and c["initial"]["read"]
                            for c in excerpt["state_snapshots"]["cases"]))
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
        self.assertEqual(wc.Conductor._run_raw_dirs(stub, None),
                         cs.run_dirs("theirs", host_rendered=True))
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
        with self.assertRaisesRegex(rc.ConfigurationRefused, "not a capture of 'b'"):
            rc._restart_source(final, "b", "own")


class ValidatorFormatResolutionTests(unittest.TestCase):
    def test_the_post_execute_gate_reads_in_the_pipeline_targets_format(self) -> None:
        # Kills: the validator reading every run as json whatever the target's profile says;
        # and guessing a format for a pipeline path that names no target.
        import yaml

        from tools import target_profile as tp
        from tools import validate_pipeline_semantics as vps
        repo = Path(__file__).resolve().parents[2]
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
    a `*.json` glob in a module on the capture path. What it does not see: a name computed at
    run time, and a reader that never names the directory at all — `CaptureStoreReachTests`
    is the row for those. A fourth rule, over the capture-path modules: none names a declared
    format by its id — the format is the target's, and with one declared member a literal reads
    the same as the right answer in every other test."""

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
    _PATH_PARTS = frozenset({"state_snapshots", "initial", "history", "series"})
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
                    and str(node.args[0].value).rsplit("/", 1)[-1] == "*.json":
                found.append((node.lineno, "a JSON glob on the capture path"))
            # a format named by its id where the code wants the target's (`target.output_format`)
            if globs and isinstance(node, ast.Constant) and id(node) not in docstrings \
                    and id(node) not in in_fstring and isinstance(node.value, str) \
                    and node.value in NeutralCaptureLiteralTests._FORMAT_IDS:
                found.append((node.lineno, f"the format {node.value!r} is named by its id"))
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
            '    ok6 = f"a json file of {run}"\n'), globs=True)
        self.assertEqual([f.split(": ", 1)[0] for f in seen],
                         ["x.py:3", "x.py:4", "x.py:5", "x.py:6", "x.py:7", "x.py:8",
                          "x.py:14", "x.py:15", "x.py:16"], seen)
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
