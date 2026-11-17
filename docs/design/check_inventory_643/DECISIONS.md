## Operator decisions on the 17 items (2026-11-17)

The operator adopted every recommendation below. Item 4 is decided as option A: the `standing:#172` tag protects the codex provider working, not each row that names it, so the preflight option-set probes go on both providers (OR4-03/05/06/07, OR4-08, OR4-09).

The operator also decided: this record lives in `docs/design/check_inventory_643/`; the two in-scope defects are filed as #661 and #662; rule 1-b's demand for an execution record is waived for #643, so a row may be removed on the #643 criterion from code reading.

---

## Recommendations for the 17 operator decisions (checkout `ec2ddda3`)

This comment gives one recommendation per item of §"Operator decisions needed" in the reconciled inventory. Each recommendation states the question, the options, the choice, the lines re-read at `ec2ddda3` to reach it, and what is still unknown. The work was read-only.

### Basis

- **Criterion.** `AGENTS.md` §Development premises: a check stays when a leaf that lacks it gets closer to reporting its task done, or when a correct run fails or a wrong result is certified or recorded without it. The operator's inputs and environment are not policed beyond hashing into a derivation key. When two designs work, the simpler one is taken.
- **Evidence standard.** Rule 1-b's execution-record requirement is waived for this issue. A removal below rests on code reading unless a run or a census is named.
- **Record policy** (applied to items 8, 10 and 13). A record is kept when one of these holds:
  - R1: a reader other than the operator exists (a later stage, a leaf that is shown the file, the console renderer, a script under `skills/`);
  - R2: a document or a failure message sends the operator to it during a recovery or a diagnosis;
  - R3: it is one field or one event written at a point the code already passes, and it is the only copy of the fact.

  A record is removed when it carries machinery of its own (an extra process run, a wait, a refusal) and no reader acts on it, when it duplicates another record of the same fact, or when it exists only for a format no current writer produces. Where a keep rule and a remove rule both apply, the remove rule decides in two cases: the document pointer of R2 belongs to rows another unit already removes, or the only reader under R1 is a leaf that is shown a whole file and the record needs a process run of its own.
- **Provider rule.** `AGENTS.md` states that retiring an enforcement layer is complete only when it lands on every declared provider. An outcome that treats the claude CLI and the codex CLI differently is therefore not offered as a recommendation.
- **Censuses used.** Recorded orchestrations: `workspace*/orchestrations` (47 workspace directories). Development sessions: the session transcripts kept for this checkout (325 sessions and their subagent files). A session census that counts command text includes searches for a name and is an upper bound on use; where a figure is of parsed invocations, the text says so.

### Corrections to the inventory summary

Re-reading the code changed the premise of six items.

| item | summary statement | what the code says |
|---|---|---|
| 2 | "credential-copy mode of the lineage config home" | No credential is copied. The lineage home holds a placeholder, and the sandbox profile binds the operator's file over it (`orchestration_runtime.py:13658`, `:13815-13817`). The 0700 checks protect the CLI's transcripts only. |
| 5 | The guard is the question | The whole `record-timeout` route has lost its scope: the conductor finalizes a timed-out leaf in band and `--resume` clears a dead driver's markers. The last recorded `timeout` row is dated 2026-06-18. |
| 10 | The dev CLI "has no in-tree caller and no doc names it" | True for the tree. Development sessions invoke it as a measurement instrument: 32 invocations in 8 session transcript files dated 2026-11-10 to 2026-11-15. |
| 13 | 72 rows "whose only reader is the operator" | 5 rows have another reader, and 4 rows are named refusals or the record a RUNBOOK procedure reads. |
| 14 | "IR fields that no host code reads" | `update_semantics`, `temporaries`, `derived_field_rules` and `invariants` are read by three validator gates. Only `ordering`, `splitting_policy` and `control_condition` have no host reader. |
| 15 | WW-29 and WW-32 land together | That holds only when WW-29 goes. WW-32 can go alone, because WW-29 runs before it. |

### Summary

| # | recommendation |
|---|---|
| 1 | Drop HK-03 (with item 17) and HK-02. Keep HK-04 and the entrypoint HK-01. |
| 2 | Remove OR3-94 and OR3-98. Keep creation with mode 0700 and nothing else. U12 proceeds. |
| 3 | Remove VP3-017. A `--resume` across a leaf-contract bump is allowed. |
| 4 | Remove the option-set probes of both CLIs (OR4-03/05/06/07 and OR4-08). OR4-08 needs the operator to rule that `standing:#172` does not cover it; otherwise both stay. |
| 5 | Delete the `record-timeout` route whole (OR4-114..120). If the route is kept, keep OR4-119. |
| 6 | Remove PE-060 and PE-061, with the residual stated (no fail-closed line remains behind the gate). |
| 7 | Remove BR-13. Keep the `AGENTS.md` sentence as a development rule. |
| 8 | Remove the field `source_ir_id` and its refusal OR1-52. |
| 9 | Remove VP1-102 and correct `docs/TESTS.md`. |
| 10 | Keep the dev CLI and its argument refusals (PE-284..288). |
| 11 | Keep WC3-82. The criterion does not cover it. |
| 12 | Keep WC3-84c. |
| 13 | Keep 61 rows, trim 2, remove 9, one of the 9 as a sub-field only (groups below). |
| 14 | Keep VP2-43, VP2-45 and the type members of VP2-44 / VP2-47. No policy change. |
| 15 | Keep WW-29. Remove WW-32 alone. |
| 16 | No action under this issue (standing: #173, #424 decision 2). |
| 17 | Drop `--stage full`, the waiver flags, `--legacy-mode` and the unscoped branch. |

---

### 1. DEV hook layer (HK-01..04, U39)

**Question.** Does the hook that judges the commands of a development agent session stay?

**Options.**
- A. Drop the layer whole. About 360 non-test lines, the 642-line test file, both registrations and `docs/HOOKS.md` §The DEV layer go. The sleep rule returns to prompt text.
- B. Drop HK-03 only, together with the validator's waiver flags (item 17).
- C. Drop HK-03 and HK-02. Keep HK-04 and the entrypoint HK-01.

**Recommendation: C.** No leaf and no run reaches this layer, so the criterion names nothing to defend. The layer is the operator's session tooling, and the choice rests on recorded incidents:
- HK-03 loses its subject when item 17 removes the two flags it names.
- HK-02 (the hard-reset command) has no recorded incident of the event it refuses. Its record is of over-refusals: it refused the commit that introduced it, and it refused a command of this inventory. The one mention of the command in the development documents is a deliberate use by a session that split a branch (`round-conduct.md:87`, 2026-08-14), which the rule refuses today. It samples one destructive command of several.
- HK-04 (a wait by sleeping) has a measured incident: 144 leftover processes after about 36 minutes on a shared machine, after the rule had failed three times as prompt text. The observed spelling passed the harness's own block on a foreground sleep. On the codex layer nothing else refuses it.

Option A is consistent with the criterion as well. Its cost is the return of the HK-04 incident class.

**Verified.** `tools/hooks/operator_safety.py:1-38` (purpose and the over-refusal record), `:47-51` (the flags); `tools/hooks/dev_session_hygiene.py:11-27` (the incident); `.claude/settings.json:16-27`; `docs/HOOKS.md:25-36`; `.claude/skills/spec2science-review-loop/references/round-conduct.md:84-92`. No document records an accidental hard reset (search over `docs/`, `.claude/skills/`, `TODO.md`).

**Unknown.** Whether the Claude Code permission layer alone stops a hard reset in every mode the operator uses. This does not block: HK-02 does not cover the other destructive commands either.

### 2. Mode of the lineage config home (OR3-94, OR3-98)

**Question.** Do the checks that verify and re-tighten mode 0700, ownership and non-symlink on the lineage home and its ancestors stay?

**Options.**
- A. Keep both.
- B. Remove both. Keep `mkdir(mode=0o700)` on creation. OR3-95 (`_chmod_directory_no_follow`) then has no caller and goes with U12.
- C. Remove the mode handling as well and let the umask decide.

**Recommendation: B.**
- The stated ground of the decision is wrong: the home holds no credential copy. Authentication is bound over a placeholder by the sandbox profile.
- What the mode protects is the leaf CLI's own session transcript. The launch prompt, the reply, the generated sources and, for an HTTP leaf, every turn are also recorded under `workspace/`, which is mode 0755 on this machine. The home alone holds the CLI leaf's intermediate turns, which are of the same kind of content.
- The docstring's other ground, a substituted configuration that is about to be hash-pinned, went with the configuration pin in Z4.
- No leaf sandbox binds the homes tree, so the leaf gains nothing.
- The checks have refused correct runs: the docstring records that a root created 0755 by a plain `mkdir` failed every launch, and that a restored backup refused every launch of every orchestration. A symlinked `~/.spec2science` is refused today.
- Creation with 0700 costs one argument and keeps the directory private on a default umask.

**Verified.** `orchestration_runtime.py:12921-12939`, `:13216-13234`, `:13199-13207` (the recorded refusals), `:13292`, `:13305`, `:13320` (creation), `:13658`, `:13815-13817` (no credential copy). Census of `~/.spec2science/homes` (70 orchestration homes): 279 files named `.credentials.json`, each 0 bytes and mode 0600, and no other file with a credential name, so no staging copy is left behind. Modes measured: checkout and `workspace/` 0755, `~/.spec2science` and `homes/` 0700, umask 022.

**Unknown.** Whether either CLI refuses a config home looser than 0700. This does not block, because creation keeps 0700.

### 3. The `--resume` barrier across a leaf-contract bump (VP3-017, U23)

**Question.** Does the launch-record audit keep refusing an orchestration that holds a launch record stamped with an older `LEAF_PROMPT_CONTRACT_VERSION`?

**Options.**
- A. Remove the audit member. A resume across a bump runs; the derivation keys decide what is re-derived.
- B. Keep it. A resumed run pays for its remaining launches and fails at Validate `post_judge`.
- C. Move the refusal to the start of `--resume`. This adds a check on a change of the operator's checkout.

**Recommendation: A.**
- The version is host-stamped, and `record_launch` refuses a new request that does not carry the current value. The leaf gains nothing.
- Since issue #574 the version is in no derivation key. A fresh run adopts, by key, an artifact produced under an older producer text. The audit therefore refuses on resume what a fresh run accepts.
- The keyed contracts are the three reading-contract versions. A bump of one re-derives that phase on resume as on a fresh run.
- The audit fires at the last substep, after the billed launches. The constant moved from `leaf-85` (2026-10-29) to `leaf-102` at this checkout, so the barrier meets almost every resume across a pull.

The PR edits `docs/RUNBOOK.md:560`, the comment at `derivation.py:70-73`, and the stale sentence at `leaf_contract.py:28-30` that says the version is hashed into the keys.

**Verified.** `leaf_contract.py:36-40`; `derivation.py:98-110` (the transformation tuples hold no leaf version); `orchestration_runtime.py:11606-11611`; `validate_pipeline_semantics.py:8334-8364`; `docs/RUNBOOK.md:560`.

**Unknown.** None that blocks. The network-block barrier of `docs/RUNBOOK.md:562` is a separate audit and is not part of this item.

### 4. Preflight option-set probes (OR4-03/05/06/07, U08; OR4-08)

**Question.** Does preflight keep parsing each CLI's `--help` for the options a leaf launch passes?

**Options.**
- A. Remove both probes: the claude rows and the codex twin OR4-08, with OR4-09. OR4-08 carries `standing:#172`, so this option needs the operator to rule that the tag does not cover it.
- B. Keep both.
- C. Remove the claude rows and keep the codex row. The provider rule excludes this outcome.

**Recommendation: A.**
- The input is the operator's installed CLI. The leaf gains nothing.
- Both CLIs refuse an unknown option before any request. Measured: claude 2.1.326 exits 1 (`error: unknown option`), codex-cli 0.169.0 exits 2 (`error: unexpected argument`).
- The failure is not charged to the leaf: a non-zero exit is `pure_transport`, takes no repair turn, and ends the phase `fail_closed` / `leaf_transport_error`.
- The probes are a parser of help prose. The option-line pattern is bounded by an indent measured on one CLI version, and a tuple of ten options is held in step with the launch argv by a suite row. An option that a CLI accepts and hides from `--help` fails a working CLI at preflight.
- Issue #172 decided that the codex leaf transport stays. Its closing comment does not name the preflight probes as part of that surface, and it does not exclude them either; it records unclosed preflight gaps on codex as work owed. The reading taken here is that the tag protects the provider and not each row that names it. That reading is the operator's to confirm. If the operator rules that the tag covers OR4-08, option B follows, not C.
- Option A reverses two recorded decisions and the PR names them: issue #453 (probe `--restricted`) and issue #534 (probe the workshop option set).

**Cost.** The final reason reads `leaf_transport_error: leaf_exit=1` with no option name. The CLI's message is recorded in three places: the attempt's `failure_excerpt` (first 400 characters), the `*_attempt_failed` event (first 200), both kept in item 13, and the leaf's stderr under `agents/<arid>/dialogs/`. A provider that only a late substep uses fails after the earlier phases; `--resume` adopts those phases by key.

**Verified.** `orchestration_runtime.py:14441-14455`, `:14464-14490`; `workflow_conductor.py:8730-8732`, `:9015-9025`, `:14941-14945`, `:14992-15007`, `:16082-16101`, `:3315-3316`; the text of issue #172 and its two closing comments. The two exit codes were measured in this session.

**Unknown.** The end-to-end path through `spawn_leaf` with a missing option was not run. OR4-04 (the stdin probe) stays undecided and is not part of this item.

### 5. The `record-timeout` manual guard (OR4-119, U17)

**Question.** Does the manual `record-timeout` command keep its refusal while a child's active marker exists?

**Options.**
- A. Delete the route whole: the subcommand, `record_timeout`, OR4-114..120, `--force-reason`, the `forced` fields, RUNBOOK §Substep timeout recovery, and the sentence at `orchestration_runtime.py:17064-17067` that names the command.
- B. Keep the route and keep OR4-119.
- C. Keep the route and remove its guards (U17 as listed). A mistyped command then ends a live billed attempt.

**Recommendation: A. If the operator keeps the route, B.**
- The RUNBOOK scopes the route to "an orchestration driven by hand, or a leaf the conductor could not finalize itself". The canonical entry is `run_workflow.py`; no orchestration is driven by hand.
- A leaf that wedges under a live conductor is killed at the per-leaf cap and finalized in band. A leaf orphaned by a dead driver is recovered by `--resume`, which clears the stale markers, prunes the orphan graph edge and resets the node state.
- Census: 5 recorded orchestrations hold a `timeout` row, the last dated 2026-06-18, and none is forced.
- Option C keeps a hand tool and removes the one property that makes it safe to type. A and B are the two coherent states.
- The `deactivate-child` subcommand stays under either option: it is a step of `finalize-child`. Under A, the refusal text of `finalize_child` stops pointing a hand-released run at `record-timeout`; that run is terminalized with `set-status` and recovered by `--resume`.

**Verified.** `docs/RUNBOOK.md:1088-1119`; `orchestration_runtime.py:16260-16418`, `:11245-11258`, `:17061-17067`. No in-tree caller of `record_timeout` exists besides the CLI dispatch at `:19028-19030`.

**Unknown.** Whether a state exists that `--resume` does not reconcile and `record-timeout` does. None was found in the RUNBOOK or the code. A recorded orchestration left `running` with a live driver and an unfinalized child would be the counterexample.

### 6. The second copy of the reads-state rule (PE-060, PE-061, U34)

**Question.** Does the evaluator keep re-checking that a predicate reads captured kernel state, after the Compile gate checked it?

**Options.**
- A. Remove both members. The gate rows PE-104, PE-105 and PE-106 stay.
- B. Keep both as a second line against a regression of the gate (about 11 lines).

**Recommendation: A, with the residual below stated in the PR.**
- Every workflow caller of the evaluator runs on an IR that passed Compile.static: `orchestration_runtime.py:3097`, `workflow_conductor.py:13159`, `:13197`, `:14464`. The verification found 0 gate-clean firings in 6,637.
- With the gate intact, a leaf gains nothing from the removal. The issue names a duplicate check as a removal candidate.
- The three gate members are pinned by message: the whole-entry form (`test_primary_evidence.py:3165`, `:3260`, `:3627`), the constant-by-form refusal (`:1416-1427`) and the per-case form (`test_a_scope_over_both_partitions_reads_state_in_each`).
- The evaluator copy is a partial second line. It calls the same `predicate_reads_state`, `state_reads` and `exempt_once` as the gate, so a regression in the rule passes both copies. It reads raw references, so a state read inside a term multiplied by a literal zero passes it. What it can catch is a wiring regression: a gate that is not run, or that reads other content than the evaluator values.

**Residual, and the ground for option B.** `AGENTS.md` makes a fail-open introduced by a change the second defended class. For the other evaluator re-checks that U34 removes, a malformed IR that passes a regressed gate still ends in an exception the conductor catches fail-closed. For the reads-state rule nothing fail-closed remains behind the gate: a wiring regression certifies a vacuous predicate, which is the most direct `leaf shortcut` on a predicate. This inventory found one live defect of the wiring class in the same gate family (VP2-31: the validator reads one block and the conductor another). Option B costs about 11 lines. An operator who weighs that wrong certification above the duplicate rule takes B; the criterion as written gives A.

The dev CLI (item 10) can evaluate an IR that no gate saw. After the removal it prints a record for a vacuous predicate where it printed an error. No certification follows from that output.

**Verified.** `primary_evidence.py:2051-2065` against `:2663-2690`; the caller list above and `primary_evidence.py:5493`; the test lines above.

**Unknown.** Whether the VP2-31 divergence reaches the content the reads-state rule reads was not traced. `tools/primary_evidence.py` is digested whole; the removal changes no admission of a gated IR, so it is a re-pin.

### 7. The build-tool sentence of `AGENTS.md` (BR-13, U02)

**Question.** Does `tool_compile_project` keep refusing a compiled language whose build system is outside `make` / `cmake` / `meson` / `ninja`?

**Options.**
- A. Remove BR-13 and the constant. Keep the `AGENTS.md` sentence unchanged, as a rule for a change that registers a build-system backend.
- B. Remove BR-13 and reword the sentence.
- C. Keep BR-13.

**Recommendation: A.**
- Both inputs are profile values. The leaf chooses neither.
- The launch gate admits only a build system with an extracted backend, and `make` is the only one. The check cannot fire.
- The remote build path calls `build_command` directly and never applied the rule.
- `AGENTS.md` addresses the agent that builds the workflow. The sentence states which build tools a target may use and forbids direct compiler calls. It binds a change that adds a backend, at review. It never depended on a run-time refusal.

**Verified.** `build_runtime.py:126-130`, `:557-565`; `workflow_conductor.py:11561-11574`.

**Unknown.** None.

### 8. The lineage field `source_ir_id` (OR1-52, U19)

**Question.** Do the field `source_ir_id` and the refusal that guards its stamping stay?

**Options.**
- A. Remove the refusal and the field. Edits: `_stamp_certification`, `_build_inproc` (digested: a re-pin of the Build transformation), the strip list, the `meta_contracts` type check, and five documents.
- B. Remove the refusal only. A field with no reader and five document statements remains.
- C. Keep both.

**Recommendation: A.**
- No code reads the field. The conductor's docstring states that the `source_ir_id` binding "went with the id chain" (issue #250).
- The IR a source was generated from is bound by the generate key: the stamped `derivation_inputs` carries the IR's output hash. The field duplicates that fact in a weaker form (R3 does not hold).
- The refusal cannot fire in a correct run: an adopted phase re-reserves `compile`.
- The comment at `workflow_conductor.py:11710-11712` names a reader (`_write_runner`) that no longer reads it.

**Verified.** `orchestration_runtime.py:2197-2216`, `:3083`; `workflow_conductor.py:11710-11713`, `:15080-15099`, `:6062-6066`; `meta_contracts.py:127-130`; `test_derivation_transformation_drift.py:346`.

**Unknown.** Whether the operator reads the field by hand to map a binary to an IR directory. The same answer is the `compile` reservation of the orchestration.

### 9. `reported:` lines in `tests.md` (VP1-102, U27)

**Question.** Does the Compile gate keep refusing a `reported:` line, a line form retired in issue #589?

**Options.**
- A. Remove the detector, its refusal and its corpus-test row. Change `docs/TESTS.md:66` to state that the line form is retired and not read.
- B. Keep it.
- C. Move it to the spec input check. This adds a check on an operator file.

**Recommendation: A.**
- The input is the operator's `tests.md`. The leaf does not author it.
- The refusal is raised at Compile.static, so it is charged to the Compile leaf, which cannot repair the operator's file.
- Ignoring the line loses no judgment: the judged set is the `quantities:` line and the IR predicates, which their own gates check.
- Every spec in the tree is already clean.

**Verified.** `validate_pipeline_semantics.py:271-274`, `:3959-3971`, `:11115-11121`; `docs/TESTS.md:66`; `test_spec_corpus_versions.py:222-223`.

**Unknown.** Specs kept outside this repository are not covered by the corpus test. A stale line there is ignored without a message.

### 10. The primary_evidence dev CLI (PE-284..287, U37; PE-288)

**Question.** Does `python3 -m tools.primary_evidence` stay, with its four argument refusals?

**Options.**
- A. Keep the CLI and the refusals.
- B. Remove the CLI whole (about 80 lines and two test classes).
- C. Keep the CLI and remove the refusals. Exit 2 becomes a traceback; about 10 lines go.

**Recommendation: A.**
- The CLI is a measurement instrument: read-only, no file written, no workflow caller, no leaf reach. It decides nothing, so the criterion has no check to remove.
- It is in use. 8 session transcript files dated 2026-11-10 to 2026-11-15 hold 32 invocations as a `Bash` command, among them review rounds that evaluated a recorded run against a comparand. The inventory's statement that nothing calls it holds for the tree only.
- The four refusals are the CLI's exit-2 contract. Removing them alone saves about 10 lines and changes that contract.

**Verified.** `primary_evidence.py:5422-5503`. The census parsed the `Bash` tool calls of the retained session transcripts and excluded commands that only search for the name.

**Unknown.** None that blocks. Option B is sound if the operator prefers that reviews measure through `judge_replay.py` or a scratch script.

### 11. Declared `api_key_env` names in the leaf environment (WC3-82)

**Question.** Does `_child_env` keep dropping every name that `llm.yaml` declares as an `api_key_env`?

**Options.**
- A. Keep the guard (a 7-line helper, a 2-line loop, one test).
- B. Remove it and state in the configuration reference that an `api_key_env` name must not start with `SPEC2SCIENCE_`.

**Recommendation: A. The criterion does not decide this row.**
- The leaf gains nothing, and no run breaks. By the two tests of the criterion the row is removable.
- Without the guard, a key held in a variable such as `SPEC2SCIENCE_API_KEY` is forwarded to every CLI leaf and written verbatim into `sandbox_profiles/<arid>.json`. That file lies under `workspace/`, which is mode 0755 on this machine, and it travels with any copy of the workspace.
- The name is a natural choice for a key used with this platform. The harm is a disclosed key, which no later change takes back.
- The guard refuses nothing and polices nothing: it corrects the set the prefix rule forwards.

**Verified.** `workflow_conductor.py:10353-10360`, `:10378-10385`; `orchestration_runtime.py:7768`. The example configurations name `ANTHROPIC_API_KEY` and `LOCAL_LLM_API_KEY`, both outside the prefix.

**Unknown.** None. The decision is one of value: the operator may hold that the name of a key variable is the operator's own responsibility, which gives B.

### 12. `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` (WC3-84c)

**Question.** Does the claude leaf environment keep this variable?

**Options.**
- A. Keep it.
- B. Remove it. One tuple member and one assignment go.

**Recommendation: A.**
- The row is launch configuration. It refuses nothing and decides nothing.
- Without it each launch adds one refused route to `net_routes.jsonl`, the record the RUNBOOK reads to diagnose a route failure.
- The effect of a refused telemetry or update request on the CLI's start is not measured, and it cannot be read from this repository. Measuring it costs more than the line.

**Verified.** `workflow_conductor.py:10424-10429`. No code reads `net_routes.jsonl` (the writer is `leaf_netns.py`; the conductor only passes the path at `:8071`), so a refused route fails no run today.

**Unknown.** The CLI's behaviour without the variable. It does not block option A.

### 13. The 72 `operator-diag?` rows

The record policy of §Basis decides each group. Every id below appears in exactly one group.

**G1. `audit_orchestration` report: keep (27 rows).** AO-2, AO-3, AO-4, AO-5, AO-6, AO-7, AO-9, AO-11, AO-12, AO-13, AO-14, AO-15, AO-17, AO-23, AO-25, AO-26, AO-27, AO-32, AO-33, AO-34, AO-35, AO-36, AO-37, AO-38, AO-39, AO-40, AO-41.
- The tool is an operator flow that `AGENTS.md` keeps (`skills/workflow-audit`). It writes no file and no gate reads it.
- It is in regular use: 66 session transcript files contain an invocation. AO-14 is the collector the billed-run cost method uses.
- Line-level dead code goes without a decision: the second disjunct at `:130-132`, the test at `:135` (AO-5) and the guard at `:245` (AO-9).

**G2. `audit_orchestration` branches for retired writers: trim (2 rows).** AO-10, AO-16.
- AO-10: the other-kind branch, the `detected_at` fallback and the present-empty state exist for records whose writer issue #171 PR-2 deleted.
- AO-16: the writer always writes `pure`; the unrecognized-value line and the `legacy` hint of AO-35 serve historical records only.
- Remove those branches. The live branches of both rows stay. An orchestration older than the retired writer is read from its files.

**G3. A reader other than the operator exists: keep (5 rows).** WC5-92, WC3-24, WC5-10, WC5-18, PE-288.
- WC5-92: `run_workflow`'s progress renderer reads the events.
- WC3-24: `skills/workflow-timing-audit/scripts/leaf_token_report.py` reads the file.
- WC5-10: the judge leaf is shown `trial_meta.json` whole.
- WC5-18: every `tests.md` §8 asks for the pair, and the leaf prompts name it.
- PE-288: item 10.

**G4. Named terminal reasons and the cross-target record: keep (4 rows).** WC5-67, WC5-68, WC5-69, PE-020.
- WC5-67 and WC5-68 are refusals, not records. Without them the completion vouch refuses the same state as `conductor_error`, which reads as a defect of the conductor. `docs/RUNBOOK.md:703` routes the operator on the named codes.
- `docs/RUNBOOK.md:633` and `:703` send the operator to `cross_target/<node>.json` and the `cross_target_agreement` event (R2).

**G5. Records a document or a failure message sends the operator to: keep (12 rows).** OR1-106, OR1-46, OR2-B43, WW-09, WC4-30, WC3-18b, WC3-41b, WC3-23, OR4-23, OR3-37, OR3-38, OR2-A40.
- OR1-106, OR1-46: the text of a refusal, naming which key input moved and which dependency is stale. The operator chooses the node to re-run from it.
- OR2-B43: the preflight hint that proxy variables are set and not forwarded.
- WW-09, WC4-30, WC3-23: the only full record of a lineage's operations, of the build output, and of an HTTP leaf's turns.
- WC3-18b, WC3-41b: `docs/ORCHESTRATION.md:228-235` names `last_launch` and `leaf_rc`.
- OR4-23: the commit a run was started from.
- OR3-37, OR3-38, OR2-A40: the only record that a run was resumed, from what state, on which host, and what the resume reconciled.

**G6. One-line events and fields: keep (9 rows).** WC3-25, WC3-34, WC3-46, WC3-54, WC3-80, WC4-02, WC1-58, WC3-05b, BR-26.
- Each is one emit or one field at a point the code already passes (R3). WC3-54 carries the CLI's own message that item 4 relies on. WC4-02 belongs to `_launch_instant`, which the deterministic substeps need.

**G7. The only copy of a fact: keep (4 rows).** WC4-31b, OR4-113, WC1-34, WC2-40.
- WC4-31b: `dependency_check` and `library_check` are the record form that issue #644 chose in place of a check.
- OR4-113: the archive is produced by the rename that moves the prior result aside before the new one is written (`orchestration_runtime.py:17221-17227`), and the rollback restores from it. The rollback is kept, so the archive costs nothing more.
- WC1-34, WC2-40: `claude_tools` is the only record of the tool set a claude leaf was launched with. The recorded `sandbox_command` ends at the bare `claude` command (inspected in a launch response of 2026-11-17). An operator `command:` prefix can change the set. Confidence is lower here than elsewhere in this group: if the operator never audits the tool surface, the field and its 24-line helper go.

**G8. Remove (9 rows).** OR2-A23, OR2-B12, OR3-80, OR2-C62, WC4-01b, WC4-50, WC5-40, OR4-120, PE-021.

| rows | what goes | ground | verified / open |
|---|---|---|---|
| OR2-A23, OR2-B12, OR3-80 | `hooks/workflow_hooks.jsonl`, its writer and every row, with U16 | No reader in `tools/`, `skills/`, `.claude/`. Each `deny` row precedes a raise or a returned refusal carrying the same reason; each `allow` row states what the launch record or the step result already states. The one document pointer (`docs/CLI_REFERENCE.md:218`) is for the reply-budget rows, which U16 already removes with the rows at `:6799`, `:11024`, `:12681-12689` and `:17043` | Writers re-read at `orchestration_runtime.py:6710-6747`, `:6799`, `:11024`, `:12681-12725`, `:16098`. Three tests read the file. The `allow` row at `:16098` is a member of OR4-77, a keep row whose other records stay; the member is not verified |
| OR2-C62 | the `preflight-status` subcommand | No procedure uses it; `preflight.json` is readable directly; 0 commands in the session census | Documents name it in three table rows only |
| WC4-01b | the `_launch_instant` call on the workshop path (`workflow_conductor.py:8432`) | The return value is discarded; the attempt's start is `started_at` in the launch response. The deterministic path (`:14580`) uses the value and keeps the call | Two pins and `docs/WORKSPACE_LAYOUT.md:172` change |
| WC4-50 | the `syntax.attribution` probe | One extra compiler run per failing syntax stage, 7 tests, nothing routes on it. Its comment states that the count is what a later change would decide on. Census: 16 recorded values, all `leaf` | `_gate_syntax_check` is not digested. `gate_meta.json` leads the `escalate` diagnostician's context (`workflow_conductor.py:1086`), so a leaf is shown the value; the precedence rule of §Basis applies. Confidence is lower than for the other rows. Open: gate records of superseded attempts are not retained, so 16 undercounts |
| WC5-40 | the sub-field `dependency_nodes` only | No reader in code or documents. The other fields of the row stay | `_author_derived_validate_artifacts` is digested: take it with U32 |
| OR4-120 | the `forced`, `forced_reason`, `timeout_reason` fields | They follow item 5 | — |
| PE-021 | the `evidence` hash and its refusal | A hash over every capture file of both runs, with no reader | Open: confirm in the PR that an own run with no capture still ends `unevaluable` through the per-case evaluation (`orchestration_runtime.py:3092` lets the refusal propagate today) |

**Not verified.** No row of this item was attacked by the inventory's adversarial pass. The reader searches were redone here for G8 only.

### 14. Shape checks on IR fields (VP2-43, VP2-45, type members of VP2-44 / VP2-47)

**Question.** Does the Compile gate keep shape checks on `algorithm` fields that no host code reads?

**Options.**
- A. Keep all four rows as they are.
- B. Remove the members on the three fields with no host reader and no floor: the two `splitting_policy` members and the type member of `control_condition`. About 6 lines.
- C. Remove every pure type member of the four rows.

**Recommendation: A.**
- The premise holds for three fields only. `update_semantics` is read by `_algorithm_state_contract`; `temporaries` and `derived_field_rules` by the provenance gate; `derived_field_rules` and `invariants` by the IR self-sufficiency gate, whose comment relies on the contract gate to flag a scalar `invariants`.
- `ordering` (VP2-43) is not a pure shape check. It holds a presence floor (non-empty when more than one step exists) and reference integrity (every entry names a known `step_id`). The IR is the sole algorithm carrier of `Generate`, so a dangling reference is a false IR record. Its type members are what those two checks iterate over.
- `iteration_contract must be object` is consumed by the floor and the loop-counter coupling that follow it.
- What option B removes is 6 lines, and the fields stay in the schema that `phase_01_compile.md:103-110` gives the Compile leaf.

**Policy for later changes.** A shape check on a leaf-authored IR field is written when a host reader or a kept floor consumes the shape, or when it is a presence floor or a reference-integrity check on a field the next leaf consumes. A type check that an empty value satisfies, on a field no host code reads, is not added.

**Verified.** `validate_pipeline_semantics.py:6199-6226`, `:6228-6238`, `:6242-6264`, `:6266-6311`, `:6365-6375`; the readers at `:3791`, `:6313-6331`, `:9571-9588`; `phase_01_compile.md:103-110`, `:589`.

**Unknown.** None. The four rows have no adversarial verification, and none is needed to keep them.

### 15. Feedback wording (WW-29 with WW-32, U04)

**Question.** Does the wrapper's `run` operation keep its two answers for a case that has no host-written case file?

**Options.**
- A. Keep WW-29 and remove WW-32 alone.
- B. Remove both. A continuation case then gets the driver's `ERROR STOP 2` on a declared case.
- C. Keep both.

**Recommendation: A.**
- WW-29 answers a call that names a declared case. The answer states that a continuation runs only in `Validate.execute`. Without it the leaf receives a run failure on a case of its own IR, which reads as a fault of its tree. The verification showed the way to turn it green: the leaf writes the case file itself, which reaches no host reader and spends wrapper calls. This is the ground on which WW-24 was kept.
- WW-32 answers a call that names an undeclared case. The driver's own refusal (`no readable case file for case <id>`, exit 2, nothing written) states the same fact, and a suite row pins it.
- WW-29 runs before WW-32, so a continuation never reaches WW-32. WW-32 can go alone.

**Rule.** An answer to a call that a correct leaf can make is part of the operation. An answer to a malformed call is not kept where a later layer answers accurately.

**Verified.** `workshop_wrapper.py:636-638`, `:659-667`, `:694-701`; `workshop_mcp.py:119-122`. The driver's answers are those of the verification's run.

**Unknown.** None.

### 16. Other-stack build-system marker rows (BR-10)

**Question.** Does the dead marker fallback `_recommended_build_system` go, with the marker rows it alone reads?

**Options.**
- A. No action under this issue.
- B. Delete the fallback and the table (about 50 lines and four tests).

**Recommendation: A.**
- Issue #173 proposed this deletion by name (its item 8) and was closed by the operator's decision on 2026-09-06: the non-`make` build systems are reserved surface. Issue #424 decision 2 kept the marker rows on 2026-10-06.
- Nothing about the mechanism has changed since: the fallback was unreachable then as well.
- The row stays `standing:#173`. Revisiting it is a decision on #173, not on this issue.

**Verified.** `build_runtime.py:361-376` (the comment cites the decision), `:394-408`; the text of issue #173 and its closing comment.

**Unknown.** None.

### 17. `--stage full` (VP3-002, VP3-118)

**Question.** Does the validator CLI keep `--stage full`, the two waiver flags and `--legacy-mode`?

**Options.**
- A. Drop all four, make `--stage` required, and drop the unscoped branch of the launch-record audit. HK-03 goes with the flags (item 1).
- B. Drop the waiver flags and `--legacy-mode` only.
- C. Keep everything.

**Recommendation: A.**
- The conductor never passes `full`; it names a stage on every call. `full` is the default when `--stage` is omitted.
- No workflow path, skill or CI job passes `full` or a waiver flag. `WORKFLOW_CORE.md:300` says that CI runs the validator; `.github/` does not reference it. That sentence is corrected in the same PR.
- The unscoped scan fails on the debris of any crashed orchestration in the workspace. The code comment at the scoping records that an unrelated crashed orchestration's dangling edge failed a healthy run, which is why `pre_judge` is scoped. Whether the present workspace of 1,027 orchestrations holds such debris was not measured.
- Census of development sessions (parsed `Bash` invocations of the validator CLI, 275 in all): `compile` 130, `--help` 68, `post_execute` 41, `pre_judge` 12, `post_generate` 11, a shell variable 10, `full` 3, no `--stage` 0. The 3 `full` runs are review measurements over scratch copies of a workspace.
- The waiver flags exist to switch a verify requirement off. No caller needs that, and a hook exists only to refuse them.

**Cost.** Two tests of the waiver flags go. The test call sites that pass `require_orchestration=True` without an orchestration id gain one (25 call sites pass the flag; the count without an id was not taken). `STEP_REQUIRED_VALIDATION_STAGES` loses the `full` token in four rows.

**Verified.** `validate_pipeline_semantics.py:12196-12206`, `:12274-12308`, `:12406-12417`, `:7909-7912`; `orchestration_runtime.py:12417-12429`; `docs/workflow/WORKFLOW_CORE.md:300`.

**Unknown.** Use of `--stage full` outside the retained session transcripts, for example from the operator's own shell. If the operator runs it, option B applies.

---

### Effects on the removal units

- U08 gains OR4-08 and the remainder of OR4-09 (item 4).
- U12 proceeds with OR3-94, OR3-95 and OR3-98 added (item 2).
- U17 becomes the deletion of the `record-timeout` route (item 5).
- U04 drops WW-29 from its list and keeps WW-32 (item 15).
- U37 is withdrawn (item 10). U39 shrinks to HK-02 and HK-03 (item 1).
- U16 takes the whole hook log (item 13, G8).
- Digest impact added by these recommendations: a Build re-pin for `source_ir_id` (item 8), a Validate re-pin for `dependency_nodes` (item 13), and a re-pin of `tools/primary_evidence.py` for PE-021, PE-060 and PE-061.
