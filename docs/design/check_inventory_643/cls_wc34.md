# Issue #643 step 2 — classification, group wc34 (WC3 + WC4: `tools/workflow_conductor.py` L7302-13438, checkout ec2ddda3)

Inputs: `wc3.md` (WC3-01..96), `wc4.md` (WC4-01..70). Read-only; nothing in the checkout was changed.

Conventions used here:
- A row whose arms classify differently is split into suffixed sub-rows (`WC3-58a`/`WC3-58b`, `WC4-31b`, ...). The unsuffixed id keeps the row's main class.
- `pointer` is NOT one of the brief's classes: a `pointer` row only calls a check owned by another module/range, and that owner's classifier decides it. Counted separately so it is not read as `undecided`.
- **DRIFT** marks a row whose removal edits a method digested by `tools/tests/test_derivation_transformation_drift.py` (re-pin decision needed). In range, the digested methods are: `_write_dependency_headers`, `_copy_bound_file`, `_run_raw_dirs`, `_build_inproc`, `_stage_dependency_sources`, `_classify_build_failure_category`, `_require_build_execute`, `_builds_checked`, `_snapshot_deliverable_gap`, `_promote_run_evidence`, `_merge_continuation_captures`, `_author_quality_check`, `_author_snapshot_schema`, `_shape_state_agreement`, `_quality_check_failure_lines`, `read_base_case_ids` (WC3 range: `read_base_case_ids` only; `read_case_ids` is NOT digested).
- `twc` = `tools/tests/test_workflow_conductor.py`; test paths are under `tools/tests/`.

## Counts (178 classified lines: 166 inventory rows + 12 sub-rows)

| class | n |
|---|---|
| keep:leaf | 30 |
| keep:break | 78 |
| remove | 13 |
| merge | 11 |
| standing (#172: 1, #173: 3) | 4 |
| defect | 0 |
| operator-diag? | 14 |
| undecided | 3 |
| pointer (owner decides) | 25 |

Removal candidates (remove + merge) = 24. DRIFT among them: WC4-08 (`_require_build_execute`), WC4-23 (`_build_inproc`), WC4-65b (`_author_quality_check`). Marked `needs-run` (masking/unreachability argued from reading only, rule 1-b): WC3-10, 17, 32, 58b, 70b, 84b, 86, 87, WC4-08, 17, 18, 37, 43a.

## Table

| id | class | reason & evidence | tests affected |
|---|---|---|---|
| WC3-01 | remove | host-twin. Every component of `<ir_ref>/spec.ir.yaml` is host-composed (`refs.ir_ref` + constant; read wc:7313-7323), so (a) a leaf has no input to it and gains nothing; (b) the only escape is a caller passing non-derived refs, which no in-tree caller does (one caller, producer `write_artifacts`). Its own comment says "Defense-in-depth". The twin at wc:6765 carries a LEAF `logical_path` and stays. | none pin it (`rg "resolves outside" tools/tests` hits only the bundle twin) |
| WC3-02 | keep:break | `ir_meta.json` is read by the verify-severity route (wc:16766-16772) and `check-phase-certified`; without it Compile cannot be routed or certified. | — |
| WC3-03 | keep:break | per-attempt meta read by `classify_failure` (`failure_category`/`failure_excerpt`), audit_orchestration, judge_replay. `_write_verdict_meta` being a byte-for-byte twin of `_write_leaf_attempt_meta` is a simplification (dedupe the writer), not a check removal. | — |
| WC3-04 | remove | dead. All 6 in-tree callers pass the literal `leaf_mode="workshop"` (`grep -n 'leaf_mode=' wc` → 9051/9058/9073/9122/9996/10134) and the default is `"workshop"`; (a) no leaf input; (b) the next line writes `"workshop"` regardless. Two sites (wc:7421, 9441); the bundle_meta copy at wc:6862 is WC2's. | none (`rg "leaf_mode must be workshop" tools/tests`: no hit) |
| WC3-05 | keep:break | `semantic_review.json` is what the `--stage pre_judge` gate reads (`review_method`, `scope.*`, VPS:7728-7760) and `_judge_semantic_decision` routes on. | — |
| WC3-05b | operator-diag? | fields `raw_excerpt_policy_version` / `prompt_contract_version` in this file: no code reader (grep: writer only). docs/workflow/phases/phase_04_validate.md:101 says the version is recorded "so a recorded review says which window its judge was looking through" (operator). The constant is already in the derivation key (derivation.py:103-109). | — |
| WC3-06 | keep:leaf | rewrites leaf `evidence_refs` keys to workspace paths; without it the pre_judge record carries leaf-chosen keys instead of the host's paths. The verbatim branch is only past WC3-08's refusal. | — |
| WC3-07 | keep:break | `scope.model_ref/runner_ref/raw_refs` are what pre_judge checks for existence; a bundle with two files in one role falls back to host naming, which the gates refuse elsewhere. | — |
| WC3-08 | pointer | owner `leaf_contract.semantic_review_document_violations`. | — |
| WC3-09 | keep:break | `result_summary` is the only field that satisfies `_validate_agent_summary_text`; without it `finalize_child` is refused. | — |
| WC3-10 | remove | masked (needs-run). `spec.build_context` runs first (wc:8815) and the context builder that puts `runner_document` in the context reads the runner from the same `src/` and raises a named missing-runner refusal first (test_leaf_producer.py::test_missing_runner_fails_closed_before_any_leaf_spawn). The only gap is a runner that is a symlink/non-file, which the host renderer never writes. (a) host input only; (b) no breakage. | none pin the raise |
| WC3-11 | keep:break | host-table invariant: without it a context document added to a builder but not to `_WORKSHOP_*_SEEDS` silently misses the reviewer's tree, so a review is certified without that document (second defended class: a defect a change introduces). | — |
| WC3-12 | keep:break | same invariant class as WC3-11: two seeds of one name overwrite silently (a reviewer reads the wrong document). With the codegen_bundle extension rule only host-named collisions reach it. Note (not a removal): the interface-header name (wc:7962) is assigned without this check. | — |
| WC3-13 | keep:break | a seed named after an output would be read back as the leaf's answer: a host seed certified as the leaf's verdict/bundle. Pinned. | test_workshop_producer.py::test_a_seed_named_after_an_output_file_is_refused |
| WC3-14 | keep:break | skipping the control file / symlinks keeps host-only files out of the leaf tree; the #548 trailing newline is a measured fix. | — |
| WC3-15 | standing:#172 | provider→workshop backend mapping; the provider surface. No in-tree config reaches the raise, but it is the line a newly declared provider without a backend fails on. | — |
| WC3-16 | keep:break | fail-closed catch-all turning a netns setup error into `workshop_lineage_setup_failed` instead of a conductor crash. | — |
| WC3-17 | remove | dead (needs-run): `# pragma: no cover`, the cold preparation "creates or raises" (comment). Removing it only changes a `None` into an AttributeError that WC3-48's catch turns into the same `workshop_lineage_setup_failed`. (a) host only; (b) same outcome. Tiny (3 lines). | none |
| WC3-18 | keep:break | `ended_by` is read by `_workshop_session_resumable`, `next_launch`, the reopen, `after_transport`. | — |
| WC3-18b | operator-diag? | `last_launch` {agent_run_id, rc, ended_at}: no code reader (grep over tools/skills: writer only); docs/ORCHESTRATION.md:228/235 name it as THE field saying whether an open lineage's last launch ended cleanly (operator reading). | — |
| WC3-19 | keep:break | a damaged lineage record reads as "no lineage" → cold launch, instead of a crash. | — |
| WC3-20 | undecided | the `[A-Za-z0-9_-]+` session-id gate before a glob: Claude's id is a host arid, but how the codex `agent_session_id` index row is populated (possibly from CLI output) was not traced, so whether the glob-safety branch bounds a leaf/CLI value is unknown. | — |
| WC3-21 | keep:break | computes the launch bound and its name (feeds `lineage_timeout` vs `leaf_timeout`); functional. | — |
| WC3-22 | keep:break | a reading lineage has `ctx is None`; without the refusal an undeclared tool name from the leaf reaches `run_wrapper_op(None, ...)` and raises instead of returning repairable feedback. Whether `llm_workshop_loop` refuses first was not traced (if it does, this becomes merge). | — |
| WC3-23 | operator-diag? | `loop_turns.jsonl`: no code parser; orchestration_diagnostics.py:579 names it as the place to search; docs/ORCHESTRATION.md describes it as the HTTP lineage's record. | — |
| WC3-24 | operator-diag? | `launches/<arid>.http_response.txt`: only reader is the operator skill script `skills/workflow-timing-audit/scripts/leaf_token_report.py`. | — |
| WC3-25 | operator-diag? | event `http_leaf_transport_error`: event log only. | — |
| WC3-26 | keep:break | a warm launch whose home is gone falls back cold; without it the substep fails on an operator prune. | — |
| WC3-27 | keep:break | fail-closed catch-all → named `workshop_lineage_setup_failed` rather than a crash. | — |
| WC3-28 | keep:leaf | without the re-seed, a seed the leaf edited is what the wrapper's feedback reads (comment names it): the leaf can steer its own feedback away from what the gates read. | — |
| WC3-29 | keep:break | `CONTRACT.md` is the leaf's contract after compaction. | — |
| WC3-30 | pointer | owner `_credential_identity` (wc:14743, #616). | — |
| WC3-31 | keep:break | snapshotting under a live handler would assemble a torn tree; fails closed. | — |
| WC3-32 | remove | no-reader (needs-run): the HTTP override of `cancelled` (wc:8462-8468); its own comment: "NOT PINNED … such a launch returns rc 0, so nothing below reads a difference today". The CLI arm (`cancel.is_set()`) stays. (a) host value; (b) no reader → no breakage. ~6 lines. | none |
| WC3-33 | keep:break | a dead/timed-out leaf's lineage is not resumed ("its session may hold a half-written turn"). | test_leaf_verify.py::test_a_timed_out_reviewer_is_ended_by_its_bound_and_never_waits_out_a_quota |
| WC3-34 | operator-diag? | event `workshop_launch_complete`: event log only; the per_attempt row carries the same fields. | — |
| WC3-35 | keep:break | without it a warm turn with no calls/time left is launched, refused on its first call, and charged as a repair attempt. | — |
| WC3-36 | keep:leaf | operator path, but bounds a leaf value: a rollout under a codex-shell-writable root could be edited by the leaf to pass the cut-read audit (WC3-70). | test_workshop_reviewer.py::test_a_lineage_home_a_codex_shell_can_write_is_refused_before_a_launch |
| WC3-37 | keep:leaf | carrying a dead reviewer's `verdict.json` forward would certify a document no finished launch stood behind (docstring). | — |
| WC3-38 | keep:leaf | a warm cut-read repair could rewrite the same document from memory without reading (docstring). | — |
| WC3-39 | keep:break | warm only with findings; a findingless warm turn would be re-sent a prompt describing a fresh tree. | — |
| WC3-40 | keep:break | dead leaf → `pure_transport` (retry), otherwise assemble; the routing split. | — |
| WC3-41 | keep:break | `per_attempt[]` fields read by orchestration_diagnostics, audit_orchestration, judge_replay. | — |
| WC3-41b | operator-diag? | fields `ended_reason`, `truncated`, `cut_outputs`, `leaf_rc`, `exemplar_ref`: no code reader (grep); docs/ORCHESTRATION.md:235 points the operator to `leaf_rc` for a kill's rc. | tests asserting `leaf_rc` (test_leaf_verify.py:905) |
| WC3-42 | keep:break | the diagnosis text is the only cue the repair turn gets for a cut reply / no tool call; without it the retry is blind. | — |
| WC3-43 | keep:break | fail-closed catch-all: a context-assembly error becomes a named outcome instead of an abort one frame later (wc:6424 comment, measured). Survivor of WC3-58a/59/61a. | — |
| WC3-44 | keep:break | a damaged launch record degrades instead of raising (pinned). | test_llm_http_wiring.py::test_a_damaged_launch_record_degrades_instead_of_raising |
| WC3-45 | keep:break | continuing another node's lineage would seed this node's tree with another node's files. The target arid can come from a revocation record (wc:16822). | — |
| WC3-46 | operator-diag? | event `workshop_reopen`: event log only (pinned by test_llm_http_wiring.py reopen tests). | test_llm_http_wiring.py::test_a_reopen_naming_the_none_target_carries_nothing_and_says_so, ::test_a_reopen_without_findings_is_a_plain_launch |
| WC3-47 | keep:break | a stale `EXEMPLAR.md`/`DEPENDENCIES.md` the host no longer resolves would stay in a reopened tree. | — |
| WC3-48 | keep:break | fail-closed catch-all (5 sites) → `workshop_lineage_setup_failed`. | — |
| WC3-49 | pointer | owners `_leaf_ir_document_violations`, `_leaf_bundle_violations`, leaf_contract. | — |
| WC3-50 | keep:leaf | a leaf-authored lone surrogate would otherwise crash the host write and route a valid `fail` through the transport branch (comment). | — |
| WC3-51 | pointer | owner `_leaf_ir_declared_fail` (WC2). | — |
| WC3-52 | keep:break | fail-closed catch-all on host writes after the window (5 sites). | — |
| WC3-53 | keep:leaf | backslashreplace of leaf-derived findings; without it a leaf surrogate crashes repair/meta writes. Four sites on four different values (not a merge). | — |
| WC3-54 | operator-diag? | per-attempt `failure_category/failure_excerpt` + `*_attempt_failed` event: docstring "observability only — ADDITIVE fields"; no routing reader. | test_leaf_producer.py::test_per_attempt_records_failure_of_a_superseded_attempt_only |
| WC3-55 | pointer | owners `_usage_limit_wait`, `_stale_credential_relaunch`, `_leaf_transient_retry` (WC5 range). | — |
| WC3-56 | keep:break | the repair budget; without it a leaf that never converges loops forever. | — |
| WC3-57 | keep:break | a bound-killed lineage with a valid `fail` must reach the severity route, not transport (docstring). | — |
| WC3-58a | merge→WC3-43 | the `_document_missing` arm (dev repo doc `phase_02_generate.md` unreadable): without the wrapper the `OSError`/`UnicodeError` propagates to WC3-43, which already yields the same category `pure_context_assembly_failed`, rc 1, no leaf spawned, with the path in the exception text. (a) no leaf input (dev-authored doc); (b) only the label in `reason_detail` changes. Two copies (verify + harness-verify builders). | test_leaf_verify.py::test_missing_phase_02_raises_the_named_contract, ::test_non_utf8_phase_02_raises_the_named_contract, ::test_every_repository_document_of_this_context_fails_CLOSED (assert the label; would assert the category) |
| WC3-58b | merge→WC3-43 | the `_unsliceable` arm: the slicer `_generate_verify_severity_rubric_section` (wc:1244) raises `ValueError`, which WC3-43 catches into the same `pure_context_assembly_failed`, rc 1, no spawn; only the label changes. needs-run: confirm the slicer has no non-raising empty return. | test_leaf_verify.py::test_unsliceable_phase_02_raises_the_named_contract |
| WC3-59 | merge→WC3-43 | as WC3-58a for `RUNNER_OUTPUT_CONTRACT_REF` (dev doc). | test_leaf_verify.py::test_every_repository_document_of_this_context_fails_CLOSED |
| WC3-60 | pointer | owner `_leaf_node_document` (wc:6885, WC2). | — |
| WC3-61a | merge→WC3-43 | `_read`'s `pure_{key}_missing` wrapper over 9 judge-context files: the bare `OSError` reaches WC3-43 with the same outcome and the path. The files' authorship does not matter here: both forms fail closed identically. | test_leaf_judge.py::test_every_missing_document_raises (assert label per key) |
| WC3-61b | merge→WC3-43 | judge `_unsliceable` arm of `_runner_output_contract_sections` (wc:1213): as WC3-58b. | none found |
| WC3-62 | keep:leaf | io_contract from the LEAF IR: without it the judge is seeded with no io_contract and judges blind. Possibly duplicated by a Compile-stage io_contract requirement (not traced) — if that gate requires it, this becomes merge. | test_leaf_judge.py::test_an_ir_without_an_io_contract_raises, ::test_an_unparseable_ir_raises_its_own_named_outcome |
| WC3-63 | pointer | owner `tools/raw_evidence_excerpt.py`; `allow_nan=False` fails closed via WC3-43. | — |
| WC3-64 | keep:break | `source_meta.json` read by the verify-severity route, `_read_repair_findings`, `check-phase-certified`. | — |
| WC3-65 | pointer | owner `workshop_tree.expand_bundle_document`. | — |
| WC3-66 | keep:break | both CLIs cut a long single line; a reviewer could not page a one-line JSON seed (comment). | — |
| WC3-67 | pointer | owner `workshop_tree.assemble_document_file`. | — |
| WC3-68 | keep:break | naming constants that keep host seeds off output names; renaming them makes WC3-13 fire. Not a check. | — |
| WC3-69 | keep:leaf | a codex reader whose command output was cut certifies text it never read. | — |
| WC3-70 | keep:leaf | rollout scan: cut texts and delegations the byte rule cannot see. | — |
| WC3-70b | merge→WC3-36 | the backstop arm "the lineage home is under {w}, which a codex shell can write" (wc:9684-9688). needs-run. Every codex reading lineage is opened by `open_cold` (wc:9841; L8943 is the producer's, L16326 the diagnostician's), which refuses the same `_codex_shell_writable_root` answer before a launch in the same process (same env); a warm reviewer turn reuses that lineage. (a) no leaf gain (the home was already refused); (b) no path reaches `_codex_cut_reads` without passing `open_cold`. | test_workshop_reviewer.py:1129 (asserts the backstop text) |
| WC3-71 | keep:leaf | a codex reading launch that read past its budget cannot certify. | test_workshop_reviewer.py (cut-read family) |
| WC3-72 | keep:leaf | an unreadable rollout means the cut-read audit cannot run; accepting would certify an unaudited read (MR5-e). | test_workshop_reviewer.py::test_an_unreadable_rollout_fails_closed_and_is_never_retried |
| WC3-73 | keep:leaf | a provisional `pass` written before the bound would certify an unfinished review (docstring). | test_workshop_reviewer.py::test_a_pass_left_by_a_lineage_its_bound_ended_is_run_again_from_the_seeds |
| WC3-74 | keep:leaf | dropping the truncated conversation is WC3-37's rule applied at exhaustion: a later launch must not inherit an unfinished review's context. | — |
| WC3-75 | pointer | owner orchestration_runtime CLI (`write-step-result`, `reserve-phase-root`, `set-status`, `reset-phase`). | — |
| WC3-76 | keep:break | a non-dict answer reads as "not certified" (the phase runs): fail-safe; `--no-record` keeps a false record from outliving the run. | — |
| WC3-77 | keep:break | raises the runtime's launch refusal into a named fail_closed (pinned). | twc::test_a_launch_check_refusal_ends_fail_closed_under_its_own_reason_code |
| WC3-78 | keep:break | not a re-check: the filters drop `None`/`"none"` severity and absent strategy, which the routing passes legitimately (`severity` is `None` when no grade); without them the CLI gets an invalid `--severity none`. | twc:1561 |
| WC3-79 | keep:break | a lost revocation would make `--resume` skip a phase this run decided to re-derive (docstring). | twc revocation tests |
| WC3-80 | operator-diag? | event `revoke_artifact_noop` (pinned by the WC3-79 test). | twc::test_a_noop_revocation_over_a_still_certified_phase_fails_closed |
| WC3-81 | pointer | owner orchestration_runtime `leaf_env_from` / `LEAF_ENV_ALLOWLIST`. | — |
| WC3-82 | undecided | drops `api_key_env` names inside the forwarded `SPEC2SCIENCE_` prefix from a CLI leaf env. (a) Leaf gain is nothing (reading the operator's credential is out of scope per AGENTS.md); but (b) without it the secret is written into `sandbox_profiles/<arid>.json`, a workspace record. Secret persistence in records is not covered by the #643 criterion: the operator's call. | twc::test_a_cli_leaf_does_not_get_the_http_credential |
| WC3-83 | keep:break | the two ids feed the profile builder (orchestration_runtime.py:8425-8450), TMPDIR the leaf's scratch. Note: the comment's "codex hook selecting a capability" is stale (Z4) — a doc fix, not a check. | — |
| WC3-84 | keep:break | `CLAUDE_CODE_MAX_OUTPUT_TOKENS`: without it the default ceiling truncates a hard leaf mid-think (comment). | twc max-tokens tests |
| WC3-84b | remove | operator-only (needs-run). `CLAUDE_CODE_DISABLE_AUTO_MEMORY`: the measurement it cites was on the deleted agentic launch reading the OPERATOR's `~/.claude/projects/<slug>/memory`. A workshop claude leaf runs with a private per-lineage config home under a tmpfs checkout (CLAUDE.md §leaf; `claude_workshop_profile_kwargs`), so the operator's memory index is not in its home. (a) A leaf gains nothing toward a verdict from its own memory dir; a cold lineage starts from a fresh home. (b) No breakage if the private home holds no memory — must be measured by capturing a workshop leaf's first request without the variable (rule 1-b). ~20 comment lines + 1. | twc::test_child_env_closes_the_operators_auto_memory_for_a_claude_leaf |
| WC3-84c | undecided | `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`: without it one refused Datadog CONNECT per launch lands in `net_routes.jsonl` (measured, CLI 2.1.312). Whether the refused route delays or errors the CLI (a breakage) was not measured. | set identity via `CLAUDE_LEAF_ENV_EXTRAS` |
| WC3-85 | remove | operator-only. Reads only the HOST env `CODEX_HOME` vs deprecated `SPEC2SCIENCE_HOME`; the comment (wc:10438-10450) states neither name survives into `env`, nothing is promoted, and "no host-side reader consumes `child_env["CODEX_HOME"]`" — the leaf's home is the one `_prepare_codex_workshop_lineage` prepares. (a) no leaf input; (b) the conflict changes nothing the run uses. | twc::test_codex_home_conflict_still_raises_after_the_filter (delete) |
| WC3-86 | remove | host-twin (needs-run). Re-checks `ir_meta.json`/`source_meta.json`, whose only writers are now the host's `_write_ir_meta` ("the ONE writer") and `_write_verify_source_meta` (grep of writers: wc:7359, 9473; workshop_wrapper.py:782 writes a scratch copy). Traced the leaf-carried values: `verification_status`/`issue_severity` come from a verdict `verify_verdict_violations` already enum-checked (leaf_contract.py:892-900), `last_fail_reason` is `verify_repair_text(...)` (a str), `attempt_count`/`debug_mode`/`context_isolated` are host. The rationale ("a FAILED verify LEAF could persist a schema-violating meta") predates the host writer. Open item for the run: a `--resume` over a workspace whose meta an older leaf-writer version wrote. (a) no leaf gain; (b) none if no other writer remains. Removes the method (~42) + the caller branch at wc:16766. | twc::test_contract_findings_empty_when_meta_absent, test_schema_violating_meta_escalates_without_a_repair_turn, ::test_a_transport_failed_verify_fail_closes_before_the_meta_schema_gate, ::test_the_harness_self_tests_own_writer_is_what_the_thread_sweep_checks; test_leaf_compile.py::test_the_host_written_meta_satisfies_the_stage_meta_contract (keep: it pins the writer) |
| WC3-87 | merge→VPS `_validate_case_ids` | the drop of unsafe case ids in `read_case_ids` (wc:10501-10508) duplicates the Compile gate `_validate_case_ids` (validate_pipeline_semantics.py:9127), same `CASE_ID_TOKEN_RE` + no `..`, which the LEAF IR crosses first and which routes to `compile.generate`. All three callers (wc:10518, 13523, 14560) read the certified IR after Compile. The comment's remaining case is "a hand-crafted IR that bypassed Compile" — an operator act. needs-run: confirm no Validate path reads an IR not certified by `--stage compile` (e.g. `--rederive` of a later phase over an uncertified IR); weigh that `CASES` is interpolated unquoted into the control file (#457). Keep the strip/sort (parsing). Not DRIFT (`read_case_ids` is not digested; `read_base_case_ids` calls it, so its OUTPUT feeds a digested method — confirm). | twc::test_read_case_ids_drops_path_traversal_tokens |
| WC3-88 | keep:break | fail-open by design: the exemplar is best-effort; without the catch an exemplar error fails Generate. | — |
| WC3-89 | keep:break | an HTTP leaf needs its declared credential; absent stays absent (`missing_api_key`). | twc HTTP credential tests |
| WC3-90 | remove | operator-only. The pop matters only when the operator's llm.yaml names `CLAUDE_CONFIG_DIR`/`CODEX_HOME` as an HTTP entry's `api_key_env` (its own comment and test say so); `leaf_env_from` keeps both names out on the way in and `render_bwrap_command` validates names on the way out. An HTTP leaf has no CLI to read a backend home. (a) no leaf input; (b) no breakage. ~3 lines + ~12 comment lines. | twc::test_the_pop_is_what_keeps_a_declared_backend_home_out (delete); ::test_child_env_never_carries_a_backend_home stays |
| WC3-91 | pointer | owner `orchestration_runtime.WORKSHOP_HOME_PREPARERS`. | — |
| WC3-92 | pointer | owner `record_launch` / orchestration_runtime `record-launch`. | — |
| WC3-93 | pointer | owner `finalize_child` / `_validate_agent_summary_text`. | — |
| WC3-94 | pointer | owner `_persist_leaf_output`. | — |
| WC3-95 | pointer | owner tools/workshop_tree.py. | — |
| WC3-96 | pointer | owner tools/llm_workshop_loop.py. | — |
| WC4-01 | keep:break | in `run_substep` (wc:14580) the probe's mtime is the freshness window `min_mtime` of WC4-04. | twc::test_the_launch_instant_probe_records_the_instant_it_returns |
| WC4-01b | remove | no-reader record. The workshop-leaf call `self._launch_instant(child_arid)` at wc:8432 discards the return value; no code reads `launch_instant.probe.json` (`grep LAUNCH_INSTANT_PROBE_BASENAME` → constant + writer only). (a) host record; (b) nothing reads it on this path. Also removes up to 2 s of tick-wait per workshop launch. 1 line. Not DRIFT. | test_leaf_verify.py:634, test_leaf_producer.py:1055 (assert the file exists) |
| WC4-02 | operator-diag? | event `launch_instant_tick_wait_timeout`: degrades loudly; event log only. | twc::test_a_clock_that_does_not_advance_degrades_loudly_and_says_so |
| WC4-03 | keep:break | the deterministic substep's status. | — |
| WC4-04 | keep:break | a retry/reopen reusing an artifact dir would pass on a prior attempt's meta (wrong certification). | twc freshness tests |
| WC4-05 | keep:break | the terminal of a dispatch that replaced a vacuous-pass tail; without it the function falls through. Unreachable, but its absence restores a silent pass for the next mis-dispatch. | test_leaf_model.py::test_determine_substep_status_refuses_an_llm_pair |
| WC4-06 | keep:break | reads the leaf's judge decision for routing; normalisation only. | — |
| WC4-07 | keep:break | the agent_runs finalize payload; codex fields standing (#172). | — |
| WC4-08 | merge→launch gate `target_profile.toolchain_servable_reasons` | DRIFT (`_require_build_execute`). operator-only: profile `toolchain.build_system`; the launch gate requires `("build_system", bs, "build_execute")` (target_profile.py:668-676) before any phase. Only `make` exists. Keep the `capability_module` call (it returns the module); drop the `missing_capability_reason` pre-check and let the registry's own exception reach WC4-10. needs-run: confirm every conductor entry (incl. `--resume`) passes `target_profile_violations`. | twc::test_require_build_execute_rejects_a_build_system_it_does_not_drive; drift re-pin |
| WC4-09 | keep:break | dispatch terminal (as WC4-05). | none |
| WC4-10 | keep:break | fail-closed sink of every in-process body error. Survivor of WC4-08, WC4-43a. | — |
| WC4-11 | keep:break | IR (leaf) declares deps but the closure is empty: without it the build fails on an unresolved module and the leaf is blamed (misattribution charges its retry budget). DRIFT if touched. | twc::test_stage_dependency_sources_raises_on_empty_closure_with_direct_deps |
| WC4-12 | keep:break | no derivation record → nothing bound; without it the stage proceeds unbound and a build failure is blamed on the leaf. | — |
| WC4-13 | keep:break | operator inputs changing between phases, but the check is the key's integrity: without it the derivation key records closure X while the build used Y, and a later reuse serves the wrong artifact. | twc::test_staging_refuses_a_closure_that_moved_since_the_key |
| WC4-14 | keep:break | same as WC4-13 for the bytes (the key binds the hash). | twc::test_staging_refuses_bytes_that_are_not_the_bound_ones |
| WC4-15 | standing:#173 | every in-tree language/build_system backend has `control_file`; the refusal is the surface a future backend without one meets (pins patch the registry). | — |
| WC4-16 | keep:break | a language whose sources include a header but whose binding has none would build without it and blame the leaf. | — |
| WC4-17 | merge→launch-time host prerequisites | operator-only: profile `toolchain.language` vs `linter_for_language`, asked at launch (test_host_prerequisites.py:77). Two conductor copies (freeze wc:11242, gate wc:12141). needs-run: confirm the host prerequisites run on every entry; otherwise keep the gate copy (a `--resume` that starts at `generate.gate` never freezes) and merge only the freeze copy into it. Without either, `tool_run_linter(preset=None)` is refused by build_runtime → WC4-10. | twc::test_gate_lint_check_unknown_language_raises; test_leaf_producer.py::test_a_language_with_no_lint_preset_is_a_host_precondition |
| WC4-18 | merge→launch gate `toolchain_servable_reasons` | operator-only: `syntax_promotions` is among `LANGUAGE_CAPABILITIES_EVERY_NODE` the launch gate requires ("the syntax-stage facts", target_profile.py:650-656). Two copies (freeze wc:11246, gate wc:12291). needs-run as WC4-08. | twc::test_gate_syntax_check_refuses_a_language_with_no_syntax_stage |
| WC4-19 | keep:break | sets `run_unavailable` so a host that cannot run the binary still lets the leaf work (and tells it why) instead of failing the launch. | test_leaf_producer.py launch-shape tests |
| WC4-20 | keep:break | an absent sidecar is omitted so `check_ir` reports what the gate would. The symlink arm is a host-twin of a host-authored file (negligible). | — |
| WC4-21 | standing:#173 | `BUILDS_IN_SOURCE` false: no in-tree backend; the build-system surface. DRIFT. | — |
| WC4-22 | pointer | owner tools/host_execution.py (backstop of the driver's site gate). | — |
| WC4-23 | remove | host-twin, DRIFT (`_build_inproc`). Compares two `_stage_dependency_sources` returns computed from the same `_phase_closure_bindings` entry in one call (wc:11462, 11468); any drift between them is already refused inside each call by WC4-13/14. (a) no leaf input; (b) equal by construction. 4 lines. | none (`rg "checked build staging bound" tools/tests`); drift re-pin |
| WC4-24 | pointer | owner tools/checked_build.py. | — |
| WC4-25 | pointer | owner tools/remote_execution.py. | — |
| WC4-26 | keep:leaf | without the rmtree an earlier attempt's binary stands in for one this attempt did not build. | test_build_at_site.py::test_a_stale_file_in_the_nodes_bin_is_not_kept |
| WC4-27 | keep:leaf | a pass binary_meta pointing at a missing binary. | twc::test_build_inproc_fails_when_binary_not_at_contract_path |
| WC4-28 | keep:leaf | the checked build is the leaf code's checked run. | test_checked_build.py |
| WC4-29 | pointer | owner tools/external_library.py `symbol_check`. | — |
| WC4-30 | operator-diag? | `compile.stdout/stderr.log`: no code reader; exempt from hashing (AUDIT_LOG_BASENAMES); the full build output for an operator. | test_build_at_site.py (reads both) |
| WC4-31 | keep:break | `binary_meta.json` read by WC4-03, routing, orchestration_runtime, VPS. | — |
| WC4-31b | operator-diag? | fields `dependency_check` (+`closure_bindings`) and `library_check`: no code reader (grep → writer, docstring L6270, reference docs); comment L11756 "A RECORD, not a comparison". DRIFT if removed (`_build_inproc`). | test_external_library.py:886-917 |
| WC4-32 | keep:break | fail-closed toward the verdict. Note: every nonzero rc of the `post_build` validator, a traceback included, is attributed to the leaf (`validate_post_build_violation`); untested. Misattribution, not a fail-open. | none |
| WC4-33 | keep:break | skip the static checker after lint/syntax fail; the certifier hard-fails non-ok evidence. | twc::test_union_verdict_lint_and_syntax_fail_static_skipped |
| WC4-34 | keep:break | `gate_meta.json` read by WC4-03, `_read_repair_findings`, `classify_gate_failure`; `checkers.*` reach only the diagnostician's context. | — |
| WC4-35 | keep:break | a timed-out lint probe would otherwise be classified as a host finding (comment). | twc::test_a_run_with_no_exit_status_is_a_transport_fail_closed |
| WC4-36 | pointer | owner `tools/backends/linter/<preset>/lint.py`. | — |
| WC4-37 | remove | masked (needs-run): `except Exception: continue` around `backend_registry.get("linter", sub_preset)` — `# pragma: no cover - an unregistered preset is refused upstream` (build_runtime refuses an unsupported preset before running). Removing it turns a hypothetical fail-open into WC4-10's fail-closed. (a) host value; (b) none. 3 lines. | none |
| WC4-38 | keep:leaf | lint finding attribution: "Assume the leaf" hides host findings; the unattributed arm fails closed. | twc lint attribution tests |
| WC4-39 | keep:leaf | a nested leaf file would borrow the host's attribution (terminal, not retried). | twc::test_a_nested_file_cannot_borrow_the_host_attribution |
| WC4-40 | keep:break | lint evidence read by VPS `_validate_generate_lint_command_logs`. | — |
| WC4-41 | keep:leaf | a bundle with no source passes syntax vacuously. | twc::test_gate_syntax_check_no_sources_is_content_fail |
| WC4-42 | standing:#173 | optional second-stage compilers ("the future target-compiler second stage"). | — |
| WC4-43 | keep:break | the second arm: a mandatory adapter that reads another language would syntax-check the sources with the wrong rules and pass them. | twc::test_a_mandatory_stage_whose_adapter_reads_another_language_fails_closed |
| WC4-43a | merge→WC4-10 | the first arm "has no registered syntax-check adapter": host backend tables only; without it the `None` adapter raises AttributeError into WC4-10's same transport fail_closed. needs-run. | none |
| WC4-44 | keep:leaf | a refused source name is routed to the leaf (warm retry) rather than transport. | twc refused-source-name tests |
| WC4-45 | keep:break | operator environment (compiler absent), but without it the stage records `skipped`, VPS refuses a skipped mandatory stage at post_generate, and the leaf is blamed (`post_generate_violation`, warm retry). | twc::test_gate_syntax_check_missing_gfortran_is_transport_fail |
| WC4-46 | keep:leaf | a model reaching a private sibling passes the whole-src stage and breaks every consumer (comment). | twc model-alone tests |
| WC4-47 | keep:leaf | a silently skipped model-alone probe turns WC4-46 off. | twc::test_a_skipped_model_alone_probe_fails_closed |
| WC4-48 | keep:break | a broken invocation (operator `toolchain.standard`) would otherwise be retried on the leaf forever. | twc::test_gate_syntax_check_unviable_invocation_fails_closed_not_blaming_deps |
| WC4-49 | keep:break | a dependency failing under this standard is not the leaf's to repair. | twc dependency-probe tests |
| WC4-50 | operator-diag? | `syntax.attribution`: "OBSERVATION ONLY … nothing routes on it" (comment); no code reader; it reaches gate_meta and so the diagnostician's context. Costs one compiler run per failing stage on nodes with host-rendered files. Operator's call. | 7 twc attribution tests |
| WC4-51 | keep:break | syntax evidence read by VPS (VPS:5092-5139). | — |
| WC4-52 | keep:break | workspace-root violation routed to the leaf. Same note as WC4-32 (a script crash is attributed to the leaf). | — |
| WC4-53 | keep:leaf | rc-based classification; reading output text let a leaf filename forge the marker (comment). | twc forge tests |
| WC4-54 | keep:leaf | the Compile gate over the leaf IR. | — |
| WC4-55 | keep:break | `compile_static_meta.json` read by WC4-03, `_read_repair_findings`, orchestration_runtime. | — |
| WC4-56 | keep:break | the reader of `required_evidence` (functional); the Compile gate checks the IR shape. | twc::test_required_evidence_artifacts |
| WC4-57 | pointer | owner capture-store backends. DRIFT if touched. | — |
| WC4-58 | keep:leaf | runner aux files and stale samples are not promoted as evidence. DRIFT. | twc promote tests |
| WC4-59 | keep:break | `snapshot_schema.json` read by primary_evidence, run_configuration, raw_evidence_excerpt. | — |
| WC4-60 | keep:break | the actionable mismatch text; primary_evidence.py:1459 relies on it for the initial capture. DRIFT. | twc::test_mismatch_yields_actionable_diagnostic |
| WC4-61 | keep:break | malformed IR is a named outcome (fail-closed / diagnostician), not a crash. | twc::test_a_malformed_test_entry_is_unevaluable_not_a_crash |
| WC4-62 | keep:leaf | cross-target state agreement over leaf captures. DRIFT. | twc state-agreement tests |
| WC4-63 | keep:leaf | per-shape verdict. Note: launch/block/thread match is computed here AND recomputed by VPS `_validate_execution_shapes` from the recorded `observed_*`; not merged because this copy decides the Execute route and its repair text, and VPS only certifies later. DRIFT. | twc shape tests |
| WC4-64 | keep:break | unreadable `perf.json` → `{}` → applicable checks fail (fail-closed). | — |
| WC4-65 | keep:break | `quality_check.json` read by VPS, `_execute_inproc`, orchestration_runtime, checked_build. | — |
| WC4-65b | remove | no-reader record, DRIFT (`_author_quality_check`): field `state_agreement_applies` — grep over tools/skills/docs finds only the writer (wc:13315). (a) host field; (b) no reader. 1 line. | drift re-pin only |
| WC4-66 | keep:break | the repair excerpt for a failed shape. DRIFT. | — |
| WC4-67 | keep:leaf | the syntax verdict over leaf sources. | twc::test_gate_syntax_check_compile_error_is_content_fail |
| WC4-68 | keep:leaf | the Build verdict over leaf sources (`return_code or 1` makes a timeout a failure). DRIFT. | — |
| WC4-69 | pointer | owner tools/build_runtime.py (BR rows). | — |
| WC4-70 | pointer | owner other WC ranges. | — |

## Removal groups (candidate PR units)

1. **Workshop-loop dead and host-twin refusals** — WC3-01, WC3-04, WC3-10, WC3-17, WC3-32 (+ WC3-70b merge). ~30 lines. No DRIFT. Tests: only test_workshop_reviewer.py:1129's backstop assert changes. Needs-run: WC3-10, WC3-17, WC3-32, WC3-70b.
2. **Named context-assembly wrappers → WC3-43** — WC3-58a, WC3-59, WC3-61a WC3-58b, WC3-61b. ~15 lines here; the same pattern exists in the WC2 range (wc:5814, 5835, 6437, 6513, 6939-6976), so this unit should be cut across WC2 + WC3 together, and `_leaf_assembly_detail`'s `pure_` prefix rule reviewed with it. Tests: the label asserts in test_leaf_verify.py / test_leaf_judge.py change to category asserts. No DRIFT.
3. **Stage-meta contract re-check (host-twin)** — WC3-86 + its route branch (wc:16766). ~45 lines. Needs-run: an old-workspace `--resume` with a leaf-written meta. Tests: 4 twc tests deleted. No DRIFT.
4. **`_child_env` operator-only branches** — WC3-85, WC3-90, WC3-84b. ~40 lines (mostly comment). Tests: 3 twc tests deleted. WC3-84b needs a capture-the-request measurement first.
5. **Build/gate capability duplicates of the launch gate** — WC4-08 (DRIFT `_require_build_execute`), WC4-17, WC4-18, WC4-43a, WC4-37. ~35 lines. Needs-run: every conductor entry passes the launch gate / host prerequisites. Re-pin drift for `_require_build_execute`.
6. **Host-twin / no-reader in `_build_inproc` / `_author_quality_check`, plus the workshop probe** — WC4-23, WC4-65b (both DRIFT), WC4-01b. ~6 lines. One re-pin decision covers both digests.
7. **Case-id runtime filter → Compile gate** — WC3-87. ~3 lines. Alone, because it is the runtime bound for an unquoted `CASES` interpolation (#457) and needs its own needs-run evidence.

Not grouped (operator decisions): the 15 `operator-diag?` rows (events, `loop_turns.jsonl`, `http_response.txt`, compile logs, unread fields `last_launch`, `per_attempt` extras, `dependency_check`/`library_check`, `syntax.attribution`), WC3-82 (secret persistence in sandbox profiles), WC3-84c.

## Observations (no class change)
- WC4-32 / WC4-52: a crash of the `post_build` validator or `validate_workspace_root.py` (rc 1) is attributed to the leaf and charged to its retry budget; neither is tested for that branch. Fail-closed, so not a `defect` under the brief, but a misattribution.
- WC3-03: `_write_verdict_meta` duplicates `_write_leaf_attempt_meta` (and bundle_meta's writer, WC2) — a writer dedupe, not a check removal.
- WC3-83: the comment on `SPEC2SCIENCE_CHILD_AGENT_RUN_ID` describes the deleted codex capability hook.
- WC3-12: the interface header name is added to the seeds without the duplicate check (unreachable under the extension rule).
