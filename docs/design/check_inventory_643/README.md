# Check inventory for issue #643

Status: **record** (2026-11-17; step 3 landed 2026-11-18, see §Step 3 below). Steps 1 and 2 of the Method of [issue #643](https://github.com/seiya/spec2science/issues/643), measured at main `ec2ddda3`. The summary is [issuecomment-6945727174](https://github.com/seiya/spec2science/issues/643#issuecomment-6945727174). The line numbers and the classifications describe `ec2ddda3`; a later commit moves them. Nothing here is a rule; the removal itself (step 3) lands in its own pull requests.

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

## Step 3: the removals

The removal units landed in six pull requests, each recording its rows, the rows kept against the plan, the survivors it relies on and the digests it re-pinned:

| PR | units |
|---|---|
| [#664](https://github.com/seiya/spec2science/pull/664) | U01, U02, U03, U04, U38 (with the AO trims), U39 (HK-02), U40 (HK-05) |
| [#666](https://github.com/seiya/spec2science/pull/666) | U05, U06, U07, U08, U09, U12, U17, U18, U20, OR2-C62 |
| [#669](https://github.com/seiya/spec2science/pull/669) | U10, U11, U13, U14, U15, U16, U21, U22 |
| [#670](https://github.com/seiya/spec2science/pull/670) | U23, U24, U25, U26, U27, U28, item 17 (with HK-03) |
| [#672](https://github.com/seiya/spec2science/pull/672) | U19, U29, U30, U31, U32, U33, WC4-01b, WC4-50, WC5-40 |
| PR-6 (the pull request that adds this section) | U34, U35, U36, PE-021, the B-1 rows |

U37 was withdrawn (item 10). Every digest move was a re-pin; no derivation version was bumped.

**The B-1 run** (`verify/ver_f5.md` §B). It was run on a scratch tree with HK-06's `command_id` arm and the validator's lint certification (VP2-16/17/19/20/21/22) removed (the syntax-side probes also with the syntax certification, VP2-23..25, removed). A `cuda_cpp` node whose tree holds no `.cu` file was still refused, and neither removed check did the refusing. Bundle admission requires `<sid>_model.cu` (and a harness bundle `<sid>_runner.cu`), and the `Generate.gate` syntax stage refuses a tree with no source of its suffix (WC4-41). Over the regular files the host writes, lint's file set contains the syntax stage's, so a lint skip implies that refusal (a top-level symbolic link would count for syntax and not for lint, and the host writes no link: a leaf tree holding one is refused at bundle assembly). The one composite preset, `mixed`, has no member that takes files by suffix, so it cannot skip. HK-06, HK-07, HK-08, HK-09, VP1-54, VP2-15..17, VP2-19..25 and VP3-112 went, and no presence check stayed. BR-21 was kept: it is a result, not a refusal, and its live reader is the workshop wrapper's lint feedback.

**The D2 run** (`verify/ver_f4.md` D2). The run used a target with no sweep and no checked row, whose quality-check re-run writes complete captures and exits non-zero. On it, `_author_quality_check` records `pass`, and only VP2-72's `ok` member refuses. No declared target has that shape. VP2-72's `ok` member stays, and the conductor-side fix is [#674](https://github.com/seiya/spec2science/issues/674), outside #643.

**Residual of the removals.** The evaluator no longer re-checks the IR fields the Compile gate refuses (U34). Behind a regressed gate, an IR that breaks one of those rules is not refused a second time: measured in PR-6's review on a real IR, the reads-state rows (PE-060/061), the value rows (PE-066/067), a bind name (PE-043) and three predicate-entry arms (PE-059) then give a `pass` verdict, and the other rows end in an exception or a `fail`. The gate's arms are pinned by their own rows; the operator accepted the residual (2026-11-18).

**Residual: what step 3 did not take.**
- The `FINAL.md` §6 unverified sub-arms.
- The `undecided` rows, among them the launch-record audit frame VP3-001/004/008/013/022/028/029, OR2-B03/B09-B11, OR2-C16..C20/C26/C27, OR4-04 and VP1-36.
- VP1-22, which waits on VP3-029.
- Both copies of VP3-012 / VP3-025 / VP3-027 and their helpers VP1-23 / VP1-24.
- The `standing` rows (#169, #172, #173, #174) and BR-10.
- The out-of-scope defects of `FINAL.md` §2, other than OR1-87 and the swallowed `fail_closed` status write, which PR #669 fixed.
- [#661](https://github.com/seiya/spec2science/issues/661) and [#662](https://github.com/seiya/spec2science/issues/662).
