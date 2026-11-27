# Design notes

## Purpose

This index states the status of every note under `docs/design/` and where the open work each note leaves is recorded. A note holds a decision (`docs/DEVELOPMENT.md` §Record placement); its own `Status` line is canonical for its status, and this table restates it in one word so that a reader can tell a landed decision from a proposed one without opening the note. Open work is recorded in `TODO.md` or in a GitHub issue, never in this index.

## Status vocabulary

| status | meaning |
|---|---|
| landed | The decision is implemented, and the note records it. Nothing in the note is open. |
| superseded | A later decision replaced the note. The note is kept as the record of the decision it took, and the `Status` line names what replaced it. |
| record | The note records a survey, an inventory or a follow-up list rather than a design. |
| proposed | The note states a design that is not implemented as a whole. Its `Status` line names the parts that have landed. |

## Notes

| note | status | replaced by, or open work recorded in |
|---|---|---|
| `codex_backend_support_plan.md` | superseded | [issue #534](https://github.com/seiya/spec2science/issues/534) |
| `dependency_build_followup_prompt.md` | superseded | `deterministic_followups.md` D1 |
| `deterministic_conductor.md` | landed | — |
| `deterministic_followups.md` | record (frozen 2026-09-06, issue #181) | — |
| `leaf_must_read_restructure.md` | superseded | Z4 ([issue #171](https://github.com/seiya/spec2science/issues/171)), then issue #534 PR-J4-b |
| `producer_workshop_leaf.md` | landed | — |
| `simplification_program.md` | landed (complete 2026-09-17) | — |
| `z4_enforcement_retirement.md` | landed | — |
| `check_inventory_643/` | record (step 3 landed 2026-11-18) | — |
| `workflow_scaling_redesign.md` | proposed (all but R3 `regression` and R6 model tiering landed) | — |
| `zero_base_architecture.md` | proposed (reference architecture; Z0 to Z6 landed) | — |
| `scale_migration.md` | proposed (items 1, 2, 4, 5 and 7, the node-level part of item 3, Parts 1 and 3 of item 6 and Stages 1 to 3 of item 8 landed) | `TODO.md` (the item "Migrating SCALE onto this framework" and the items it lists) |
| `evidence_based_redesign.md` | proposed (stage C and the judge removal landed by [issue #724](https://github.com/seiya/spec2science/issues/724)) | [issue #737](https://github.com/seiya/spec2science/issues/737) |

## Operations Rules

- A new note states its `Status` at its top, before its first section, and is added to the table above in the pull request that adds it.
- A pull request that changes a note's `Status` changes its row in the same commit.
- A note is not moved or renamed when its status changes, so that every citation into it stays valid.
