#!/usr/bin/env python3
"""What an `io_contract` says about raw evidence: the one definition of an artifact's spelling.

`RAW_EVIDENCE_ALIASES` and the two normalizers below are read by the validator
(`tools/validate_pipeline_semantics.py`), so every reader of an IR's raw-evidence tokens applies
one spelling rule. The module also held `raw_evidence_excerpt`, the host-computed summary of
`raw/` that the `validate.judge` leaf was seeded (issue #169, Z3), and its policy version; both
went with that substep in issue #724 PR-2. The normalizers stay here rather than move: a move
is a classification of its own.
"""

from __future__ import annotations

#: Spellings the IR may use for a raw-evidence artifact, mapped to its canonical name.
RAW_EVIDENCE_ALIASES = {
    "state_snapshots": "state_snapshots",
    "raw/state_snapshots": "state_snapshots",
    "raw/state_snapshots/": "state_snapshots",
    # Issue #589: the driver's samples and its per-step series.
    "state_history": "state_history",
    "step_series": "step_series",
    # Issue #644 P4b: the driver's restart points, a capture group of their own.
    "restart_points": "restart_points",
}


def normalize_raw_evidence_spelling(token: str) -> str:
    """The one spelling rule every raw-evidence token reader applies before lookup."""
    return token.strip().lower().replace("\\", "/")


def normalize_raw_evidence_artifact(token: str) -> str | None:
    """The canonical artifact name for an IR spelling, or None when it names none."""
    return RAW_EVIDENCE_ALIASES.get(normalize_raw_evidence_spelling(token))
