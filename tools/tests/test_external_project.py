"""A Project outside the framework checkout (issue #705 PR-1).

A run reads one Project, named by `--repo-root`, and the framework finds its own documents,
schema and child tools beside the module that runs (`tools/framework_root.py`). These rows build
a Project that holds the sample's Project directories and NOTHING of the framework — no `docs/`,
no `spec/schema/`, and a `tools/` directory of its own that is an unrelated package — and drive
the framework against it. Before issue #705 the first refused at startup
(`missing_canonical_schema`), and with that file supplied every child tool failed, because each
was started as `python3 tools/<x>.py` relative to the Project root and its `PYTHONPATH` named
that root (the plan's E3 and X4).
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tools.framework_root import FRAMEWORK_ROOT
from tools.target_profile import list_target_ids, load_target_profile
from tools.tests.target_fixtures import SAMPLE_ROOT

#: The sample's Project directories (`docs/design/scale_migration.md` §Structural layers):
#: everything under `spec/` but the framework's schema.
_PROJECT_SPEC_DIRS = ("component", "infrastructure", "libraries", "problem", "profile",
                      "registry", "targets")
_HARNESS = "spec/infrastructure/infra/harness/harness_fortran_cpu"


def make_external_project(dest: Path) -> Path:
    """A Project at `dest` holding the sample's `spec/` Project directories, an `llm.yaml`, and a
    `tools/` package of its own that shadows the framework's if anything imports through the
    Project root."""
    for name in _PROJECT_SPEC_DIRS:
        shutil.copytree(SAMPLE_ROOT / "spec" / name, dest / "spec" / name)
    (dest / "tools").mkdir(parents=True)
    (dest / "tools" / "__init__.py").write_text(
        "raise ImportError('the Project tools/ package was imported')\n", encoding="utf-8")
    shutil.copyfile(FRAMEWORK_ROOT / "docs" / "examples" / "llm_claude.example.yaml",
                    dest / "llm.yaml")
    return dest


class ChildToolPathTests(unittest.TestCase):
    """Every child tool the host starts is named by its path under the framework root."""

    _HOSTS = ("tools/workflow_conductor.py", "tools/run_workflow.py")

    def _framework_tool_names(self) -> list[tuple[str, str]]:
        names = []
        for rel in self._HOSTS:
            tree = ast.parse((FRAMEWORK_ROOT / rel).read_text(encoding="utf-8"))
            # A `framework_tool` inside `shlex.join(...)` spells a command for a RECORD or a
            # remedy (the recorded `invocation.command`, the resume command), not a launch.
            spelled = {id(inner) for call in ast.walk(tree)
                       if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                       and call.func.attr == "join"
                       for arg in call.args for inner in ast.walk(arg)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "framework_tool" and id(node) not in spelled):
                    self.assertEqual(len(node.args), 1)
                    self.assertIsInstance(node.args[0], ast.Constant)
                    names.append((rel, node.args[0].value))
        return names

    def test_each_child_names_a_script_that_exists_under_the_framework_root(self) -> None:
        names = self._framework_tool_names()
        # Eleven launch sites of five scripts: the conductor's and `run_workflow.py`'s children,
        # and the `--jobs` child.
        self.assertEqual(len(names), 11, names)
        for rel, name in names:
            with self.subTest(host=rel, tool=name):
                self.assertTrue((FRAMEWORK_ROOT / "tools" / name).is_file())

    def test_no_child_is_started_by_a_path_relative_to_the_project(self) -> None:
        """An argv list whose first element is `python3` and whose second is a `tools/` literal
        is a child started relative to the working directory, which is the Project root. The two
        recorded `command` strings (a record of how the operator started the tool) are not
        argv: they sit inside `shlex.join`."""
        offenders = []
        for path in sorted((FRAMEWORK_ROOT / "tools").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            recorded = {id(n.args[0]) for n in ast.walk(tree)
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "join" and n.args}
            for node in ast.walk(tree):
                if (isinstance(node, ast.List) and len(node.elts) >= 2
                        and isinstance(node.elts[1], ast.Constant)
                        and isinstance(node.elts[1].value, str)
                        and node.elts[1].value.startswith("tools/")
                        and id(node) not in recorded):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])


class ExternalProjectTests(unittest.TestCase):
    """The framework, driven against a Project that holds nothing of it."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.proj = make_external_project(Path(tmp.name).resolve() / "proj")

    def test_the_projects_profiles_and_catalog_load(self) -> None:
        from tools import orchestration_runtime as ort
        ids = list_target_ids(self.proj)
        self.assertEqual(ids, list_target_ids(SAMPLE_ROOT))
        for tid in ids:
            with self.subTest(target=tid):
                self.assertEqual(load_target_profile(self.proj, tid).sha256,
                                 load_target_profile(SAMPLE_ROOT, tid).sha256)
        self.assertEqual(ort._load_spec_catalog(str(self.proj)),
                         ort._load_spec_catalog(str(SAMPLE_ROOT)))

    def _env(self) -> dict[str, str]:
        # The suite's own environment, PYTHONPATH included: it may carry the site-packages the
        # children need (CI's shape puts the user site there). It never names the Project, so
        # only the PYTHONPATH the host composes could bring the Project's `tools/` in.
        env = dict(os.environ)
        self.assertNotIn(str(self.proj), env.get("PYTHONPATH", ""))
        return env

    def test_a_start_reaches_init_and_the_workspace_validator_accepts_its_state(self) -> None:
        """`run_workflow.py` started from the Project by the framework's path: past the startup
        load of the framework's schema, `init` runs as a real child with the Project as its
        working directory, and `validate_workspace_root.py` — another real child — accepts the
        State it wrote. `--no-run-conductor` stops before any leaf. The leaf CLI is a
        stand-in outside the Project that answers preflight's two probes, so the row needs no
        installed CLI."""
        stub = self.proj.parent / "standin_cli"
        stub.write_text(
            "#!/bin/sh\n"
            'for a in "$@"; do [ "$a" = "--version" ] && { echo "0.0.0 (stand-in)"; exit 0; }; done\n'
            'if [ "$#" -eq 1 ] && [ "$1" = "-p" ]; then\n'
            '  echo "Error: Input must be provided either through stdin or as a prompt argument'
            ' when using --print" >&2; exit 1\n'
            "fi\n"
            "exit 3\n", encoding="utf-8")
        stub.chmod(0o755)
        config = self.proj / "llm.yaml"
        config.write_text(re.sub(r"^(\s*)provider: claude_cli\s*$",
                                 lambda m: f"{m.group(0)}\n{m.group(1)}command: {stub}",
                                 config.read_text(encoding="utf-8"), flags=re.MULTILINE),
                          encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(FRAMEWORK_ROOT / "tools" / "run_workflow.py"), _HARNESS,
             "compile", "--target", "fortran_cpu", "--orchestration-id", "orch_external",
             "--no-run-conductor", "--stdout-format", "jsonl"],
            cwd=self.proj, env=self._env(), capture_output=True, text=True, timeout=120,
            check=False)
        self.assertNotIn("Traceback", proc.stderr, proc.stderr)
        last = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(last.get("status"), "ok", proc.stdout + proc.stderr)
        meta = self.proj / "workspace" / "orchestrations" / "orch_external"
        self.assertTrue((meta / "orchestration_meta.json").is_file())
        check = subprocess.run(
            [sys.executable, str(FRAMEWORK_ROOT / "tools" / "validate_workspace_root.py"),
             "--repo-root", str(self.proj)],
            cwd=self.proj, env=self._env(), capture_output=True, text=True, timeout=120,
            check=False)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)


class OperatorToolRootTests(unittest.TestCase):
    """The operator tools read the Project they are pointed at, by default the working
    directory, never the tool's own checkout (issue #705)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.proj = make_external_project(Path(tmp.name).resolve() / "proj")
        cwd = os.getcwd()
        os.chdir(self.proj)
        self.addCleanup(os.chdir, cwd)

    def test_run_configuration_defaults_to_the_working_directory(self) -> None:
        from tools import run_configuration as rc
        seen = []

        def refuse(root, *_args):
            seen.append(Path(root))
            raise rc.ConfigurationRefused("stop")

        with mock.patch.object(rc, "run_configuration", side_effect=refuse), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(rc.main(["spec/x", "--target", "fortran_cpu", "--config", "c.yaml",
                                      "--out", "out"]), rc.EXIT_REFUSED)
        self.assertEqual([p.resolve() for p in seen], [self.proj])

    def test_judge_replay_defaults_to_the_working_directory(self) -> None:
        from tools import judge_replay as jr
        args = jr.build_parser().parse_args(["orch", "arid", "--llm-config", "x.yaml"])
        self.assertEqual(Path(args.repo_root).resolve(), self.proj)

    def test_site_smoke_reads_the_sites_file_of_its_project(self) -> None:
        from tools import site_smoke
        (self.proj / "sites.yaml").write_text(":\n- not a mapping\n", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = site_smoke.main(["box"])
        self.assertEqual(code, 2)
        self.assertIn("sites.yaml refused", err.getvalue())
        self.assertIn(str(self.proj), err.getvalue())

    def test_judge_replays_init_child_imports_the_framework_not_the_project(self) -> None:
        """The replay's `init` is a real child started with the Project as its working
        directory; the Project's own `tools/` package raises on import, so a `PYTHONPATH` that
        named the Project root would fail the child."""
        from tools import judge_replay as jr
        from tools import run_workflow as rw
        from tools.llm_config import load_llm_config

        real = rw._runtime_command

        class _Stop(Exception):
            pass

        def runtime(repo_root, env, args):
            if args[0] != "init":
                raise _Stop(args[0])
            return real(repo_root, env, args)

        source = types.SimpleNamespace(meta={"spec_ref": _HARNESS, "source_dependency_ref": ""})
        with mock.patch.object(rw, "_runtime_command", side_effect=runtime), \
                mock.patch.object(jr, "finish_replay_orchestration"), \
                self.assertRaises(_Stop):
            jr.create_replay_orchestration(self.proj, source,
                                           load_llm_config(self.proj / "llm.yaml"), {})
        metas = list((self.proj / "workspace" / "orchestrations").glob("*/orchestration_meta.json"))
        self.assertEqual(len(metas), 1, metas)


class RecordedCommandTests(unittest.TestCase):
    """The commands an operator re-runs or pastes name the framework's script by its absolute
    path (issue #705): a Project outside the checkout holds no `tools/` of the framework."""

    def test_the_driver_interrupted_resume_command_is_anchored(self) -> None:
        from tools import run_workflow as rw
        proj = Path("/scratch/proj")
        out = io.StringIO()
        with mock.patch.object(rw, "_read_orchestration_meta", return_value={"status": "running"}), \
                mock.patch.object(rw, "_runtime_command"), contextlib.redirect_stdout(out):
            rw._terminalize_interrupted_orchestration(proj, {}, "orch_1")
        event = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(event["event"], "driver_interrupted")
        self.assertEqual(event["resume_command"], shlex.join([
            "python3", str(FRAMEWORK_ROOT / "tools" / "run_workflow.py"), "--resume",
            "--orchestration-id", "orch_1", "--repo-root", str(proj)]))

    def test_the_judge_replay_record_names_the_framework_script(self) -> None:
        from tools import judge_replay as jr
        source = types.SimpleNamespace(
            meta={"spec_ref": _HARNESS}, orchestration_id="orch_src", agent_run_id="arid",
            request={}, pair=("compile", "verify"))
        llm = types.SimpleNamespace(path=FRAMEWORK_ROOT / "llm.yaml", sha256="x",
                                    provenance_map=dict)
        record = jr.replay_invocation(source, llm_config=llm, argv=["orch_src", "arid"],
                                      check=True, label=None, mutations={},
                                      repo_root=Path("/scratch/proj"))
        self.assertEqual(record["command"], shlex.join([
            "python3", str(FRAMEWORK_ROOT / "tools" / "judge_replay.py"), "orch_src", "arid"]))


if __name__ == "__main__":
    unittest.main()
