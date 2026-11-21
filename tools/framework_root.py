"""The framework root: the directory that holds this `tools/` package.

A run reads one Project, named by `--repo-root` (`repo_root` in code; `docs/GLOSSARY.md`
§Framework, Project and State). The framework's own files — the workflow documents under
`docs/`, the `shape_expr` schema under `spec/schema/`, the child tools under `tools/` — are read
from here, never through the Project root, so a Project at any directory holds nothing of the
framework (issue #705).

The running module knows where it is, so no option or environment variable names this root: a
second way to name it would only add a way for the two to disagree. This module imports nothing
of the framework, so any module may import it at any time.
"""

from __future__ import annotations

from pathlib import Path

FRAMEWORK_ROOT: Path = Path(__file__).resolve().parent.parent


def framework_tool(name: str) -> str:
    """The absolute path of the framework tool `tools/<name>`, as a child's argv element."""
    return str(FRAMEWORK_ROOT / "tools" / name)
