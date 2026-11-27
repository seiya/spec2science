# Design notes

## Purpose

This index classifies every note under `docs/design/` by one status word and points to where the open work each note leaves is recorded. A note holds a decision (`docs/DEVELOPMENT.md` §Record placement); its own `Status` line is canonical for what has landed and what has not, and this index does not restate it. Open work is recorded in `TODO.md` or in a GitHub issue, never in this index.

## Status vocabulary

| status | meaning |
|---|---|
| landed | The decision is implemented, and the note records it. Nothing in the note is open. |
| superseded | A later decision replaced the note. The note is kept as the record of the decision it took, and the `Status` line names what replaced it. |
| record | The note records a survey, an inventory or a follow-up list rather than a design. |
| proposed | The note states a design that is not implemented as a whole. Its `Status` line names the parts that have landed. "not yet recorded" in the last column marks open work that has no `TODO.md` item or issue. |

## Notes

| note | status | replaced by, or open work recorded in |
|---|---|---|
| `codex_backend_support_plan.md` | superseded | [issue #534](https://github.com/seiya/spec2science/issues/534) |
| `dependency_build_followup_prompt.md` | superseded | `deterministic_followups.md` D1 |
| `deterministic_conductor.md` | landed | — |
| `deterministic_followups.md` | record | — |
| `leaf_must_read_restructure.md` | superseded | Z4 ([issue #171](https://github.com/seiya/spec2science/issues/171)), then issue #534 PR-J4-b |
| `producer_workshop_leaf.md` | landed | — |
| `simplification_program.md` | landed | — |
| `z4_enforcement_retirement.md` | landed | — |
| `check_inventory_643/` | record | the residual its `README.md` lists ("what step 3 did not take"): not yet recorded |
| `workflow_scaling_redesign.md` | proposed | not yet recorded |
| `zero_base_architecture.md` | proposed | not yet recorded |
| `scale_migration.md` | proposed | `TODO.md` (the item "Migrating SCALE onto this framework" and the items it lists) |
| `evidence_based_redesign.md` | proposed | [issue #737](https://github.com/seiya/spec2science/issues/737) for the IR's `algorithm` section; stages B and D not yet recorded |

## Operations Rules

- A new note states its `Status` at its top, before its first section, and is added to the table above in the pull request that adds it.
- A pull request that changes a note's status word, or records its open work in `TODO.md` or an issue, changes its row in the same commit.
- A note is not moved or renamed when its status changes, so that every citation into it stays valid.
