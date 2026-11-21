"""A scratch FRAMEWORK root for rows that break a framework document (issue #705).

The leaf context builders read the framework's own documents — the phase contracts, the IR
examples, the runner-output contract — under `FRAMEWORK_ROOT`, never under the Project root. A
row whose subject is "this framework document missing / unreadable / altered makes the builder
raise" therefore cannot break a copy inside its scratch Project: production would read the real
file and the row would observe nothing. Such a row calls `scratch_framework_root`, which copies
the real documents into a fresh directory, points `tools.workflow_conductor.FRAMEWORK_ROOT` at it
for the rest of the test, and returns it for the row to break.

A scratch Project, in turn, holds no framework file at all, so a builder that went back to
reading one through the Project root fails its row.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.workflow_conductor as wc
from tools.framework_root import FRAMEWORK_ROOT as REAL_FRAMEWORK_ROOT

#: The framework documents the leaf context builders read under `FRAMEWORK_ROOT`.
FRAMEWORK_DOCUMENTS: tuple[str, ...] = (
    "docs/workflow/phases/phase_01_compile.md",
    "docs/examples/spec_ir_algorithm_section.example.yaml",
    "docs/examples/spec_ir_algorithm_2d_problem_contract.example.yaml",
    "docs/workflow/RUNNER_OUTPUT_CONTRACT.md",
    "docs/workflow/phases/phase_02_generate.md",
)


def scratch_framework_root(test: unittest.TestCase) -> Path:
    """A fresh directory holding copies of the real `FRAMEWORK_DOCUMENTS`, patched in as
    `wc.FRAMEWORK_ROOT` until `test` ends. Each call makes a new root and a new patch, so a row
    that breaks one document per `subTest` calls it once per subtest."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    for rel in FRAMEWORK_DOCUMENTS:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REAL_FRAMEWORK_ROOT / rel, dst)
    patcher = mock.patch.object(wc, "FRAMEWORK_ROOT", root)
    patcher.start()
    test.addCleanup(patcher.stop)
    return root
