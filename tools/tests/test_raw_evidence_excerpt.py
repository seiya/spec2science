"""Unit tests for `tools/raw_evidence_excerpt.py` (issue #169, Z3; issue #582; issue #724 PR-2).

The module holds the one definition of a raw-evidence artifact's spelling, which the validator
reads. Its excerpt — the window the `validate.judge` leaf was seeded in place of `raw/` — went
with that substep in issue #724 PR-2, and with it the 47 rows that drove
`raw_evidence_excerpt()`: each observed that function alone (classified per class by an `ast`
scan of the names each body reaches, then read). What stays is the identity row and a row over
the alias table, the one behaviour of the excerpt's alias rows whose subject survives.
"""

from __future__ import annotations

import unittest

from tools import raw_evidence_excerpt as rex
from tools import validate_pipeline_semantics as vps


class GateReadsThisSpellingTest(unittest.TestCase):
    """The gate reads this module's definition of an artifact's spelling. Pinned by identity of
    the function object: two copies agree on every input until the day one is edited."""

    def test_post_execute_gate_imports_the_same_function(self):
        self.assertIs(vps._normalize_raw_evidence_artifact,
                      rex.normalize_raw_evidence_artifact)


class AliasTableTest(unittest.TestCase):
    """Re-hosted (issue #724 PR-2) from the excerpt's `required_evidence` rows: an alias spelling
    resolves to its canonical artifact, and a token outside the table resolves to none."""

    def test_each_spelling_resolves_to_its_canonical_artifact(self):
        for token, canonical in (("raw/state_snapshots/", "state_snapshots"),
                                 ("RAW\\State_Snapshots", "state_snapshots"),
                                 ("state_history", "state_history"),
                                 ("step_series", "step_series"),
                                 ("restart_points", "restart_points")):
            with self.subTest(token=token):
                self.assertEqual(rex.normalize_raw_evidence_artifact(token), canonical)

    def test_an_unrecognized_artifact_resolves_to_none(self):
        self.assertIsNone(rex.normalize_raw_evidence_artifact("telemetry.json"))
        self.assertIsNone(rex.normalize_raw_evidence_artifact("raw/state_history"))


if __name__ == "__main__":
    unittest.main()
