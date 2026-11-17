# Check inventory for issue #643

Status: **record** (2026-11-17). Steps 1 and 2 of the Method of [issue #643](https://github.com/seiya/spec2science/issues/643), measured at main `ec2ddda3`. The summary is [issuecomment-6945727174](https://github.com/seiya/spec2science/issues/643#issuecomment-6945727174). The line numbers and the classifications describe `ec2ddda3`; a later commit moves them. Nothing here is a rule; the removal itself (step 3) lands in its own pull requests.

## Decisions recorded with this note

The operator decided on 2026-11-17:

- This record lives in `docs/design/`.
- The two in-scope defects are filed as their own issues: [#661](https://github.com/seiya/spec2science/issues/661) (VP3-052 + VP3-096) and [#662](https://github.com/seiya/spec2science/issues/662) (VP2-31).
- Rule 1-b's demand for an execution record is waived for #643: a row may be removed on the #643 criterion (the leaf gains nothing, and no breakage follows) when the evidence is code reading alone.
- The 17 operator decisions are decided as `DECISIONS.md` recommends (posted as [issuecomment-6946630610](https://github.com/seiya/spec2science/issues/643#issuecomment-6946630610)); item 4 is option A, removing the preflight option-set probes on both providers. Where `DECISIONS.md` and `FINAL.md` differ, `DECISIONS.md` holds.

## Files

| file | content |
|---|---|
| `FINAL.md` | The reconciled result: counts, defects, operator decisions, the removal units U01-U40, the survivor anchors, the unverified sub-arms, and the full table of 1,681 rows. Start here. |
| `DECISIONS.md` | The 17 operator decisions: the question, the options, the recommendation adopted, and what was verified for each. |
| `BRIEF.md`, `CLASSIFY.md`, `VERIFY.md` | The briefs the three passes ran under: inventory (facts only), classification, adversarial verification. |
| `pilot_small.md`, `ao.md`, `or1.md`-`or4.md`, `wc1.md`-`wc5.md`, `vp1.md`-`vp3.md`, `pe.md` | Step 1, the inventory. One row per distinct refusal or record, with the author of each input and the pinning tests. `or*` is `orchestration_runtime.py`, `wc*` is `workflow_conductor.py`, `vp*` is `validate_pipeline_semantics.py`, `pe` is `primary_evidence.py`, `ao` is `audit_orchestration.py`; `pilot_small` covers `build_runtime.py`, `workshop_wrapper.py`, `spec_input_gates.py` and `tools/hooks/`. |
| `cls_*.md` | Step 2, the proposed classification per group, before verification. `FINAL.md` supersedes it where the two differ. |
| `verify/ver_f1.md`-`ver_f5.md`, `verify/f1/` | The adversarial verification of every row proposed for `remove` or `merge`, and the cross-group survivor check (`ver_f5.md` §B). |
| `verify/ver_defects.md` | The five suspected holes run to a verdict. |

The probe scripts, mutants and scratch checkouts that the verification files cite (`f2/p_or151b.py` and the like) are not landed; the files state what each run did and what it returned.
