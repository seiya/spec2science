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
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.framework_root import FRAMEWORK_ROOT
from tools.target_profile import list_target_ids, load_target_profile

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
        shutil.copytree(FRAMEWORK_ROOT / "spec" / name, dest / "spec" / name)
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
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "framework_tool"):
                    self.assertEqual(len(node.args), 1)
                    self.assertIsInstance(node.args[0], ast.Constant)
                    names.append((rel, node.args[0].value))
        return names

    def test_each_child_names_a_script_that_exists_under_the_framework_root(self) -> None:
        names = self._framework_tool_names()
        # The ten child tools of the conductor and `run_workflow.py`, and the `--jobs` child.
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
        self.assertEqual(ids, list_target_ids(FRAMEWORK_ROOT))
        for tid in ids:
            with self.subTest(target=tid):
                self.assertEqual(load_target_profile(self.proj, tid).sha256,
                                 load_target_profile(FRAMEWORK_ROOT, tid).sha256)
        self.assertEqual(ort._load_spec_catalog(str(self.proj)),
                         ort._load_spec_catalog(str(FRAMEWORK_ROOT)))

    def _env(self) -> dict[str, str]:
        # No PYTHONPATH of the operator's: each tool's own bootstrap, and the one the host
        # composes for its children, are all that reach the framework's package.
        return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

    def test_a_start_reaches_init_and_the_workspace_validator_accepts_its_state(self) -> None:
        """`run_workflow.py` started from the Project by the framework's path: past the startup
        load of the framework's schema, `init` runs as a real child with the Project as its
        working directory, and `validate_workspace_root.py` — another real child — accepts the
        State it wrote. `--no-run-conductor` stops before any leaf."""
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


if __name__ == "__main__":
    unittest.main()
