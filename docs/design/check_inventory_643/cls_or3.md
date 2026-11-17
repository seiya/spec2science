# Issue #643 step 2: classification of OR3 (tools/orchestration_runtime.py L9593-14355, checkout ec2ddda3)

Input: `or3.md` (116 rows). Every row is classified.

## Counts per class

| class | count |
|---|---|
| keep:break | 31 |
| keep:leaf | 5 (OR3-72, 82, 83, 84, 111) |
| remove | 44 |
| merge | 8 (OR3-22→OR4-134, 30→VP3-027, 42→OR4-52, 43→OR3-16, 44→OR3-15, 62→OR4-81, 65→VP3-025, 103→OR4-52) |
| standing:#172 | 4 (OR3-93, 107, 110, 115) |
| standing:#173 | 6 (OR3-06, 08, 12, 13, 14, 45) |
| operator-diag? | 3 (OR3-37, 38, 80) |
| undecided | 15 (OR3-05, 41, 66, 69, 73, 74, 78, 79, 87, 88, 90, 94, 96, 98, 114) |
| defect | 0 |

## Merge survivors in other groups (must not be removed there)

| survivor | covers |
|---|---|
| OR4-134 | OR3-22 |
| OR4-52 | OR3-42, OR3-103 |
| OR4-81 | OR3-62 |
| VP3-027 | OR3-30 |
| VP3-025 | OR3-65 |

Two pairs need care so that both copies do not go:

- **VP3-025.** Its comment (vps L8509) calls it a re-audit of OR3-65. A VPS classifier must not merge it back into OR3-65.
- **OR3-79 and OR4-135.** These are a duplicate pair; whichever group decides first, exactly one of them stays.

The twins removed together with rows here: OR4-107 (finalize_child's reply-budget copy, with OR3-31/32). OR4-113 (the `write_step_result` rollback) STAYS.

## Evidence used

- **The validator-order probe** is `or3cls/order.py`. It runs record_launch's validation sequence (prepare → request validator → prompt validator) over recorded requests from `orch_20261117T063943Z_f3f49f4b`; both controls are accepted. The cases it measured:
  - `leaf_mode: pure`, with no prompt, a hand prompt or a pure-sentinel prompt: refused by OR3-19 every time.
  - A neither-shape request with no prompt: refused by OR3-19.
  - A neither-shape request with a hand prompt: refused by OR3-61, before OR3-26.
  - Workshop requests with skill fields set: accepted, because OR3-20 blanks them.
  - A generate.verify request with no source_id: OR3-59's message.
  - Workshop with deterministic on generate.generate: refused by OR3-53.
- **OR3-85, run evidence.** None of the 13,218 recorded `launches/*.request.json` files carries `agent_session_id`, and no in-tree writer of that field was found.
- **Callers (full `git grep`, no truncation).** `record_launch`, `write_step_result` and `update_orchestration_status` are reached only through the runtime CLI dispatch: `Conductor.record_launch` and `Conductor.write_step_result` shell out (wc:5468, 10176). `record_agent_run` is also called in-process, but only from `finalize_child` and `record_launch`'s timeout path, both of which are themselves reached via the CLI.
- **OR3-41, OR3-47.**
  - `set(WORKSHOP_LAUNCH_PAIRS) ^ set(PURE_CONTEXT_REQUIRED_KEYS)` is empty (measured), and the equality is pinned at test_leaf_wiring.py:419.
  - The template keys `workshop {step}.{substep}` cover exactly the 9 pairs.
- **Dead code.**
  - OR3-46: `pure_part` has no in-tree writer. 120 OLD recorded requests carry it; audit_orchestration reads those, and that reader is unaffected.
  - OR3-104: the only caller passes `workshop=True`.
  - OR3-116: `_pass_values_by_check_name` has no caller.
- **The marker helpers.** `_required_launch_prompt_markers` / `_lines` have no reader outside `orchestration_runtime`. VPS has its own mirror, `_required_launch_prompt_markers_for_role`, which does not import them. Inside the runtime they are used by OR3-27/28 and by OR3-61 (L11870), all of which are removed.
- **Drift pins.** `test_derivation_transformation_drift.py` digests only `toolchain_version_argv` from `orchestration_runtime`, which is outside this range, so no row here needs a re-pin there. `test_leaf_prompt_contract_drift.py` digests the dependency-facts render (OR3-04/09) and the repair paragraphs (OR3-18); all three are keep rows.

## Defects

None found as a leaf gain or a wrong certification. Two inventory errors are corrected in the rows:

- **OR3-53.** The inventory said validate_pipeline_semantics re-checks the deterministic flag. The VPS test it cited calls the runtime function itself, so there is no twin.
- **OR3-78.** It is not covered by OR3-92 for deterministic phases.

| id | class | reason & evidence | tests affected |
|---|---|---|---|
| OR3-01 | remove | host-twin + operator-only. Request refs are reserved by the conductor (`build_launch_request` L1669-1680 copies `refs.*`); dependency_ref is the operator's spec path; the response id is `child_arid` (wc:5448). No leaf path to any of them (leaf is in bwrap with a tmpfs over the checkout), so the leaf gains nothing; the placeholder tokens were an agentic orchestrator's template residue. L11769 is masked by L11748 (same value, same predicate). | test_orchestration_runtime.py::test_rejects_launch_with_placeholder_plan_or_pipeline_refs, ::test_rejects_launch_with_placeholder_dependency_ref (delete) |
| OR3-02 | remove | host-twin: repo-owned template files keyed by a host table. An unwrapped OSError stops the same render at the same point; no leaf value is read. Low value, a two-line rewrap. | none |
| OR3-03 | keep:break | Shared tables consumed by OR3-51 (seed completeness), judge_replay.py:192-212 and the conductor's seed names (wc:7241); removing them breaks seeding. Diagnose rows: standing #169. Read by test_derivation_transformation_drift.py:2362-2465 (classification rows, not a digest). | — |
| OR3-04 | keep:break | Renders the certified dependency surface into the compile leaf's prompt; the drops keep a malformed entry from crashing the render. Prompt text, not a defense. Its output is digested by test_leaf_prompt_contract_drift (`dependency_surface_render`). | — |
| OR3-05 | undecided | The body is an EARLIER node's leaf-authored certified source, seeded into a LATER node's leaf. The leaf that wrote it gains nothing for its own substep, and code that contains `--- END EXEMPLAR ` by accident is implausible. But a later leaf misled by a forged fence certifies its own node wrongly. Missing: whether cross-node injection is in the defended set (operator call). | test_leaf_model.py::test_a_forged_fence_line_in_a_certified_source_is_neutralized |
| OR3-06 | standing:#173 | Per-language exemplar idiom composition. | — |
| OR3-07 | keep:break | Exemplar builder's selection logic (live caller wc:7951-7990); the skips are what "no exemplar" means. | — |
| OR3-08 | standing:#173 | Per-language dependency-signature rendering. | — |
| OR3-09 | keep:break | Prompt warnings that tell the leaf an IR-declared op does not exist or has drifted. Without them a leaf calls a missing op and the build fails late. Output pinned by an exact-text test. | — |
| OR3-10 | keep:break | Repair findings quote leaf source and gate output verbatim. A findings body that contains the fence token by accident would close the data fence early and corrupt the repair prompt, misleading a correct repair. Pinned (msg) twice. | — |
| OR3-11 | keep:break | Substitution mechanics: one pass, so inserted values are not re-expanded. It is how the renderer works, not a check. | — |
| OR3-12 | standing:#173 | Language-fragment composition refusals. | — |
| OR3-13 | standing:#173 | Parallel-backend fragment composition refusals. | — |
| OR3-14 | standing:#173 | A backend without fragments composes to "" (by design). | — |
| OR3-15 | keep:break | The renderer has to answer a request with no template. Without this a bare KeyError escapes as an unnamed conductor crash (comment L10361-10368 records that regression). Survivor for OR3-44 (merged below). | — |
| OR3-16 | keep:break | The renderer needs a `<tool_surface>` row; without the named refusal it fails with a KeyError. The provider table itself is #172 surface. Survivor for OR3-43. | — |
| OR3-17 | remove | masked: `_leaf_repair_template_name` returns one of three constants, all present in `_PROMPT_TEMPLATE_FILES` (measured: `workshop bundle/ir/verdict repair`). Unreachable while the registration holds; host-only values. needs-run if claimed beyond "the three keys are registered". | none |
| OR3-18 | keep:break | Selects the repair-scope paragraph and instruction slot; render logic over host constants. | — |
| OR3-19 | keep:break | The renderer's only answer to a request that is neither deterministic nor workshop: no renderer exists for it. On every path where prepare renders, this is the neither-shape refusal (survivor of the OR3-19/26/61 triple). | — |
| OR3-20 | keep:break | Prepare normalization: forced empty skill fields, defaults, and force-render of the host-rendered prompt. Every launch's recorded request and prompt depend on it. It also makes OR3-50 and most of OR3-23..28 hold by construction. | — |
| OR3-21 | remove | operator-only: needs a caller-supplied prompt. No in-tree caller supplies one (wc:1569 "intentionally OMITTED"; the grep in the inventory finds only comments). The leaf has no path to the CLI. | test_orchestration_runtime.py::test_a_caller_supplied_prompt_may_not_pair_with_a_respelled_role |
| OR3-22 | merge→OR4-134 | The same value (`agent_session_id` in `--response-json`) is required by `_validate_record_launch_response_fields`. The only non-test caller of `record_launch` is the CLI dispatch at L18928, which runs OR4-134 at L18927 (full `git grep` of callers; the conductor's `Conductor.record_launch` shells out to `record-launch`, wc:5468). The L16668 call re-reads the response file that record_launch already validated and wrote. Keep the extractor `_extract_response_agent_session_id`; drop the raise. Host value, no leaf gain. | test_orchestration_runtime.py::test_rejects_launch_response_without_child_agent_identifier (retarget to the CLI path or delete) |
| OR3-23 | remove | host-twin: a workshop request's prompt is always force-rendered from the same request by OR3-20, one call earlier in the same process. It can differ only for a hand-supplied prompt (operator). The leaf gains nothing: no leaf path to the request or the prompt. | test_leaf_model.py::test_a_non_workshop_request_may_not_carry_a_workshop_shaped_prompt, ::test_a_workshop_request_prompt_must_open_with_the_sentinel |
| OR3-24 | remove | masked + operator-only. Arm 2 measured: a `leaf_mode: pure` request carrying a pure-sentinel hand prompt is refused by OR3-19 inside prepare (probe `or3cls/order.py` (record_launch's order: prepare, request validator, prompt validator, run over recorded requests from orch_20261117T063943Z_f3f49f4b), case b2), so this arm is never reached. Arm 1 needs a hand-supplied pure-sentinel prompt on a deterministic request (operator). The pure leaf is deleted (PR-J4-b). | test_leaf_model.py::test_a_non_pure_request_may_not_carry_a_pure_shaped_prompt, ::test_a_pure_request_prompt_must_open_with_the_sentinel |
| OR3-25 | remove | host-twin: a host-rendered prompt is force-rendered from the request (OR3-20), so its identity lines hold by construction. The comment itself calls it "defense-in-depth". Unpinned. | none |
| OR3-26 | remove | masked, measured: a neither-shape request with a hand prompt is refused first by OR3-61 (same message; the request validator runs before the prompt validator), and without a prompt by OR3-19 (probe `or3cls/order.py` (record_launch's order: prepare, request validator, prompt validator, run over recorded requests from orch_20261117T063943Z_f3f49f4b), cases c, d, d2). | test_leaf_model.py::test_a_neither_shape_request_is_refused_outright |
| OR3-27 | remove | host-twin/operator-only: the markers hold by construction for a rendered prompt; the pin needs a hand-supplied deterministic body. | test_leaf_model.py::test_a_refused_launch_leaves_no_launch_record arm 1 (RETARGET: its subject, no record left on refusal, survives under another refusal) |
| OR3-28 | remove | host-twin/operator-only, as for OR3-27. The test docstring's "mutant survived" shows it is unpinned by function, not that it defends anything. | test_leaf_model.py::test_a_host_rendered_prompt_missing_a_request_field_value_is_refused (delete); ::test_a_refused_launch_leaves_no_launch_record arm 3 (retarget) |
| OR3-29 | keep:break | `agent.summary.txt` is read by validate_pipeline_semantics (L8566-8575) and carries the leaf's declared-fail reason for the operator. | — |
| OR3-30 | merge→VP3-027 | host-twin: it validates text the same process composed one line earlier from a host payload. VP3-027 (`_has_informative_agent_summary`, vps L8561-8576) re-checks the persisted file. The conductor comment at wc:14591 relies on this check to force a `result_summary` on non-pass rows; VP3-027 reports the same omission. needs-run: that VP3-027 runs on every orchestration. **VP3-027 must survive the VPS classification.** | test_orchestration_runtime.py::test_validate_agent_summary_text_orchestration_rejects_single_line (delete); the `…satisfies_the_real_summary_validator` positives in test_leaf_producer/test_leaf_verify (retarget to VP3-027's predicate) |
| OR3-31 | remove | host-twin: every in-tree reply is a host-composed ~3-line status string (wc:9026, 8973, 14601, 16391), far under 2000 chars. The leaf authors no reply. The hook entry has no code reader. `reply_over_budget` is only passed through to the result (L16906, L17103, L18219). | test_orchestration_runtime.py::test_over_budget_soft_returns_info_and_audits |
| OR3-32 | remove | host-twin + operator env (`SPEC2SCIENCE_ENFORCE_REPLY_BUDGET`, set by no non-test code). Remove together with its finalize_child twin OR4-107. | test_orchestration_runtime.py::test_over_budget_hard_raises_under_env |
| OR3-33 | remove | operator-only (`--resume` oid). Masked in effect: a non-token oid finds no meta (OR3-34), because every orchestration dir was created by `init`, which applies the same token rule. needs-run: a `a/../b`-style spelling resolving to an existing dir. | test_orchestration_runtime.py::test_init_and_resume_refuse_an_id_that_is_not_a_path_token (resume arm) |
| OR3-34 | remove | operator-only. A missing or invalid meta stops the resume with an unhandled read error at the same point. No leaf value; no misattribution (nothing is launched). Low value. | test_orchestration_runtime.py::test_resume_orchestration_raises_for_nonexistent_orchestration |
| OR3-35 | remove | operator-only. The only non-runtime writers of a status outside the reconcilable set are the hand-run `init --status`/`set-status` CLIs; no leaf path reaches them. Removing it also simplifies: always run the reconciliations. Reconciling an unplaceable status is what the operator wants (the comment's harm was skipping them). | test_orchestration_runtime.py::test_a_prior_that_is_neither_terminal_nor_running_is_refused, ::test_every_unplaceable_status_shape_is_refused_not_just_a_non_empty_string |
| OR3-36 | keep:break | Resume state with in-tree readers: OR3-70 (until_phase / high water), run_workflow (closure_*, wait_usage_reset), and status everywhere. | — |
| OR3-37 | operator-diag? | `resumed_*` and `last_driver_host` fields: no code reader (grep in inventory) besides the next resume's host-crossing event; docs/RUNBOOK.md and GLOSSARY describe them. They show when, from what state and on what host a run was resumed. | (if removed) test_a_resume_records_when_it_happened, test_a_resume_records_its_host_and_reports_crossing_one |
| OR3-38 | operator-diag? | Resume events in `phase_state_log.jsonl` with no code reader (audit_orchestration keeps only fail rows; diagnostics reads only record_launch). `resume_crossed_hosts` points the operator at RUNBOOK §3-1. They show what the resume reconciled. | (if removed) the three resume-reconcile tests' event asserts |
| OR3-39 | remove | host-twin: the refs come from the conductor's `reserve-root`, and node_key comes from the operator's closure. `ir_ref` is re-checked by validate_pipeline_semantics (`ir_ref must be under`). No leaf path. | test_target_keyed_store.py::test_a_launch_pipeline_ref_carries_its_target (3 bad-shape arms) |
| OR3-40 | remove | Arm 1 is masked, measured: `leaf_mode: pure`, with no prompt, a hand prompt or a pure-sentinel hand prompt, is refused by OR3-19 inside prepare every time (probe `or3cls/order.py` (record_launch's order: prepare, request validator, prompt validator, run over recorded requests from orch_20261117T063943Z_f3f49f4b), cases a, b, b2). Arm 2 (workshop with deterministic) is host-twin: the conductor sets `deterministic` (wc:1681) and the workshop block (wc:1934) from its own phase logic. On a non-build pair OR3-53 fires first (case g); on build.diagnose this arm is the only refusal, reached only by a host bug or a hand CLI. | test_leaf_wiring.py::test_the_validator_refuses_a_pure_request_by_name, ::test_validate_payload_rejects_unknown_leaf_mode, ::…_explicit_null_leaf_mode, ::…_pure_with_deterministic |
| OR3-41 | undecided | Host-composed pair. On a cold render, OR3-15 refuses every pair outside the 9 (the template keys equal `WORKSHOP_LAUNCH_PAIRS`, measured). A WARM repair renders no launch template, though, so on that path this is the only pair check. Missing: whether a warm-repair request can carry a pair other than its lineage's cold pair (trace wc repair path) before it can be merged into OR3-15. | test_leaf_wiring.py::test_validate_payload_rejects_pure_outside_the_migrated_pairs, ::test_the_pair_refusal_names_the_pairs_that_are_admissible_now; test_workshop_launch.py::test_a_workshop_request_on_another_pair_is_refused |
| OR3-42 | merge→OR4-52 | The same field and pattern are checked first in record_launch (OR4-52, L15546-15550), its one caller; the code comment says "Not observed by any test". Host value (the cold arid). **OR4-52 must survive.** | none |
| OR3-43 | merge→OR3-16 | Duplicate message on the same field; OR3-16 runs first on every rendered path. On a warm repair (no render) the lineage's cold launch already passed OR3-16 with the conductor's provider. needs-run: confirm a warm request carries its lineage's provider. | none |
| OR3-44 | merge→OR3-15 | An unknown shape, or a shape on a pair without one, has no template, so OR3-15 refuses it. The shape is host-set from the operator's spec kind. Warm-repair caveat as OR3-41; on that path OR3-51's context check is exempt, so the shape selects nothing. | test_leaf_wiring.py::test_the_shape_vocabulary_is_closed_and_pair_scoped (retarget to render) |
| OR3-45 | standing:#173 | `pure_language` / `pure_parallel` type checks (composers OR3-12/13 run first). | — |
| OR3-46 | remove | dead: no in-tree writer of `pure_part` (grep: only this check and audit_orchestration's reader of OLD records, 120 of which exist under workspace/). Host-only field; no leaf path. | test_orchestration_runtime.py::test_a_new_workshop_request_carrying_a_pure_part_is_refused |
| OR3-47 | remove | masked by OR3-41: `set(WORKSHOP_LAUNCH_PAIRS) == set(PURE_CONTEXT_REQUIRED_KEYS)`, measured (empty symmetric difference) and pinned by test_leaf_wiring.py:419. If OR3-41 goes (see OR3-41), the table-equality pin plus OR3-15 still covers it. | none |
| OR3-48 | remove | host-twin: the conductor stamps `LEAF_PROMPT_CONTRACT_VERSION` from the same constant, in the same checkout (wc:1937). Always equal in-tree; differs only for a hand-run CLI. No leaf path. | test_leaf_wiring.py::test_validate_payload_pure_requires_exact_contract_version |
| OR3-49 | remove | host-twin: the conductor forces `allowed_output_paths = []` unconditionally for a workshop request (wc:1941), and the leaf has no write authority by placement, whatever this field says. | test_leaf_wiring.py::test_validate_payload_pure_rejects_nonempty_output_paths |
| OR3-50 | remove | masked, measured: a workshop request with `skill_name`/`skill_ref` set, with or without a hand prompt, is ACCEPTED, because OR3-20 blanks the fields first (probe `or3cls/order.py` (record_launch's order: prepare, request validator, prompt validator, run over recorded requests from orch_20261117T063943Z_f3f49f4b), cases e, e2). The conductor also sets them to "" (wc:1942-1944). | none |
| OR3-51 | keep:break | Seeds are host-assembled, but their bodies are leaf/operator documents. A missing or blank seed launches a verify/judge leaf without its spec or IR, so a wrong verdict is certified. The conductor relies on this refusal (wc:6424, 6890, 6915, 9214, 9356: `pure_context_assembly_failed`). | — |
| OR3-52 | remove | host-twin: `build_launch_request` always sets node_key and step from refs and arguments (wc:1669-1673). No leaf path. | none |
| OR3-53 | remove | host-twin: `deterministic` is set by the conductor's phase logic (wc:1681). **Correction to the inventory:** its "re-check by validate_pipeline_semantics" is wrong. `test_validate_rejects_forged_deterministic_on_leaf_step` (test_validate_pipeline_semantics.py:15991) imports and calls THIS function (`_validate_launch_request_payload`); there is no VPS twin. No leaf path to the flag. | test_orchestration_runtime.py::test_validate_launch_request_payload_rejects_retired_generate_checker_substeps; test_validate_pipeline_semantics.py::test_validate_rejects_forged_deterministic_on_leaf_step |
| OR3-54 | remove | host-twin/operator-only: agent_model is the llm.yaml alias or a host default, and agent_role is a host constant per step. The role-per-step demand (`_require_child_agent_role_for_step`) is owned outside this range and is not classified here. | test_orchestration_runtime.py::test_validate_launch_request_payload_requires_agent_model (the role pin targets the outside check) |
| OR3-55 | remove | host-twin: refs are set by `build_launch_request` from reserved roots; dependency_ref is the operator spec path. | test_orchestration_runtime.py::test_rejects_launch_when_dependency_ref_is_missing |
| OR3-56 | remove | operator-only (the spec's deps.yaml path). Also re-checked by validate_workspace_root (test_rejects_plan_dependency_ref_outside_spec_deps_yaml). Unpinned here. | none |
| OR3-57 | remove | host-twin: repair_strategy and issue_severity come from the conductor's repair loop constants, or from OR3-20's defaults. No leaf path. | test_orchestration_runtime.py::test_record_launch_rejects_invalid_repair_strategy |
| OR3-58 | remove | host-twin: the repair target and reason are set by the conductor's repair path (wc:10162). | test_orchestration_runtime.py::test_record_launch_requires_repair_target_for_reuse, ::test_record_launch_requires_repair_reason_for_reuse |
| OR3-59 | remove | host-twin: source_id is reserved by the conductor. The docstring's "orchestration agent must supply" names the deleted agentic orchestrator. | test_orchestration_runtime.py::test_rejects_generate_verify_without_source_id |
| OR3-60 | remove | masked, measured: generate.verify without a source_id gets OR3-59's arm-1 message (probe `or3cls/order.py` (record_launch's order: prepare, request validator, prompt validator, run over recorded requests from orch_20261117T063943Z_f3f49f4b), case f). | none (its only pin is satisfied by OR3-59) |
| OR3-61 | remove | operator-only, measured: a neither-shape request reaches this line only with a hand-supplied prompt (`launch_prompt_full` or `execution_prompt`, cases d, d2). Without one, OR3-19 refuses it in prepare (case c). No in-tree caller supplies a prompt. | test_leaf_model.py::test_the_request_side_refuses_a_neither_shape_request |
| OR3-62 | merge→OR4-81 | Two parsers of the same host-written `agent_runs.jsonl` with the same messages; collapse them into one shared reader (`_read_existing_run_ids`, OR4-81). Its own arms are unpinned; the twin is pinned. | none |
| OR3-63 | keep:break | Reader of `agent_runs_invalid.jsonl` that the vouch needs to tell a corrupt edge from a refused row (OR3-74). | — |
| OR3-64 | keep:break | A reader. For all three callers, None selects the conservative branch: L12027 and L12769 apply the stricter non-empty-refs rule (OR3-66/90); wc:8844 makes the repair cold rather than warm. | — |
| OR3-65 | merge→VP3-025 | The conductor passes `[]` for every host-rendered row (wc:9038-9040, 9986-9987, 16392-16393); VP3-025 (vps L8503-8519) re-asserts exactly this on the record. Unpinned here. **VP3-025 must survive.** | none |
| OR3-66 | undecided | Deterministic pass rows must list output_refs. The step-level OR3-90 re-checks the same thing; OR3-91 needs the final meta in substep refs. Missing: whether `determine_substep_status` can return pass with no refs (wc:14586), and which of OR3-66/OR3-90 should survive if one is a duplicate. | none |
| OR3-67 | remove | host-twin: the orchestration row is written by the runtime or the conductor; the leaf authors no orchestration row. Unpinned (only a positive). needs-run: confirm no in-tree orchestration pass row carries refs. | none |
| OR3-68 | remove | host-twin: reached only for deterministic step/substep pass rows, whose output_refs come from the conductor's `determine_substep_status` over its own `allowed_output_paths` and whose refs are reserved. Unpinned. | none |
| OR3-69 | undecided | Launch refs (files written by record_launch) are checked at record time and again at the vouch (OR3-75 calls it per row on immutable rows: the second call is a duplicate). Missing: whether the record-time copy is covered by the VPS launch-ref reads (L8576ff), which would make the whole row a merge. | none |
| OR3-70 | keep:break | Without a recorded until_phase the vouch cannot know which phases must be certified, so it would mark pass vacuously, a wrong record. The high-water max is a narrower question: it binds the operator's own `--until-phase` lowering on resume (operator call: does a pass vouch for the highest phase ever requested?). | — |
| OR3-71 | keep:break | Refuses a vacuous orchestration pass (no runs, no orchestration row, no reservations): a wrong record. | — |
| OR3-72 | keep:leaf | Without it, an orchestration whose node failed or lost certification (certification over leaf output) is recorded pass, so a leaf's failed substep sits under a "pass" run. The conductor's pre-pass ask runs the same predicate (L12240-12242); this copy is the chokepoint on every path (conductor and hand set-status). | — |
| OR3-73 | undecided | A graph-integrity check on host records. VP3-007 (vps L8067-8104) re-checks the same `agent_graph.json` schema. Missing: whether a VPS run sees the final graph before an orchestration is marked pass (timing coverage). | — |
| OR3-74 | undecided | As OR3-73. VP3-032 (vps L8690-8741) checks the parent/child-missing arms, and VP3-031 the invalid-log tolerance. | — |
| OR3-75 | keep:break | A graph child still non-terminal at pass means a pass recorded while a child runs: a wrong record. The node_key/step-missing arms and the OR3-69 re-run are duplicates of record-time schema (merge candidates within the row). | — |
| OR3-76 | keep:break | The ordering arm (the executor must be in agent_runs) decides the latest attempt (pinned). The parse/type arms re-check a step_result that `write_step_result` validated (OR3-88/89 and CLI L18158): host-twin sub-arms. | — |
| OR3-77 | keep:break | The latest attempt's failure (a leaf's refused substep) must block the pass; an older certification would otherwise vouch. | — |
| OR3-78 | undecided | NOT a clean merge into OR3-92. OR3-92 checks disk existence only in its pure arm; its non-pure arm checks coverage by substep `output_refs`, not disk (read L12819-12838). For deterministic phases OR3-78 is the only on-disk check at the vouch. Missing: whether `determine_substep_status` lists only files that exist at status time (wc:14586). | test_orchestration_runtime.py::test_the_latest_attempt_must_have_its_declared_outputs_on_disk |
| OR3-79 | undecided | Duplicates OR4-135 (`_validate_write_step_result_fields`, L18151-18175) on the same table. Both sit on the CLI path only: `write_step_result`'s sole non-test caller is the CLI at L18984, right after OR4-135 at L18983. One copy should go, and either survivor covers every in-tree path. Prefer this one, since it carries the pins. Missing: whether `validation_stage` has a reader that needs it at all (host-set). | (if this copy goes) test_write_step_result_requires_validation_stage_for_build_pass/_validate_pass |
| OR3-80 | operator-diag? | A `workflow_hooks.jsonl` allow entry with no code reader; docs/CLI_REFERENCE.md:218 and WORKSPACE_LAYOUT describe the log for operators. It shows each pre-phase-complete pass. | none |
| OR3-81 | keep:break | Locates the judge's execution dir for OR3-82/83. Making these refusals skips would turn the leaf-verdict checks fail-open. | — |
| OR3-82 | keep:leaf | Without it, a judge step completes without the judge leaf's verdict document (`semantic_review.json` / `decision`), so the validate phase is reported done with no verdict. Unpinned. | — |
| OR3-83 | keep:leaf | Binds the judge LEAF's `decision` to the host status. Without it a leaf decision=fail can sit under a pass step_result (a wrong certification). The pass/fail arm is unpinned. | — |
| OR3-84 | keep:leaf | A blocked judge with a pass decision must still have the execution artifacts on disk. Without it the leaf's pass verdict on a blocked run stands with no evidence. | — |
| OR3-85 | remove | dead (reads a field no one writes). `post_phase_complete` compares `request.agent_session_id` to the response's. No in-tree writer puts `agent_session_id` into a launch REQUEST (grep of wc and ort: response, session index and agent_runs only). Run evidence: 0 of 13,218 recorded `launches/*.request.json` under workspace/orchestrations carry the key. For substep-aware phases the executor is the orchestration arid, which has no request file, so the check silently skips. Host values. The rollback in `write_step_result` (OR4-113) STAYS: it also restores the archived prior attempt when `_write_json` raises (L17229-17266); only the hook call inside its try goes. | test_orchestration_runtime.py::test_write_step_result_hook_rejection_rolls_back_overwrite, ::test_write_step_result_hook_rejection_restores_the_prior_attempt (RETARGET to a failing `_write_json`: the restore is their subject and it survives) |
| OR3-86 | remove | Goes with OR3-85: the hook's allow/deny log entries have no code reader. | as OR3-85 |
| OR3-87 | undecided | Stage-meta keys/types (fields carry leaf-derived values) are checked by three readers of the same `meta_contracts` clauses: here, the VPS read sweep and wc `_stage_meta_contract_findings` (wc:10540). Missing: which one the meta crosses first on every path, to pick the survivor. | the four write_step_result meta tests |
| OR3-88 | undecided | The row mixes a load-bearing decision (effective pass substeps: "non-pass must be excluded", "requires at least one effective pass": keep:break) with ~17 schema arms over the conductor's own payload (wc:10173): host-twin removal candidates, unpinned. Needs a per-arm split. | — |
| OR3-89 | remove | host-twin: the conductor composes a list, and the CLI/write_step_result `setdefault` it to `[]` (L17173). Unpinned. | none |
| OR3-90 | undecided | See OR3-66 (the same rule at step level over rows OR3-66 validated at record time). | none |
| OR3-91 | keep:break | A pass step must carry its parseable final stage meta. Downstream certification reads it, so a pass without it is recorded wrongly. | — |
| OR3-92 | keep:break | The step's deliverables (host-written from the leaf's document) must exist and be covered by the substep refs. Without it a step is vouched pass with missing outputs. Survivor for OR3-78. Unpinned (the test_leaf_verify docstring only names it). | — |
| OR3-93 | standing:#172 | codex home writable preflight row. | — |
| OR3-94 | undecided | Operator-only by the issue's criterion (no leaf path to `~/.spec2science/homes`). But it keeps the copy of the operator's credential at 0700, unreadable by other local users on a shared machine: a confidentiality property outside the leaf/breakage criterion. Operator call. Also needs-run: whether either CLI refuses a looser config home. | the two mode tests |
| OR3-95 | remove | operator-only: the docstring says the race needs another process of the SAME uid (the operator) and calls it "cheap insurance rather than a defence". Replace with a plain chmod if OR3-94 stays. | none |
| OR3-96 | undecided | Absolute / parent / not-a-dir arms: operator-only, removal candidates. The inside-/containing-the-repository arms may still matter to the leaf's mount layout (a tmpfs over the checkout plus a bind of a lineage home under it). Missing: a run with a homes root inside the checkout. The messages also describe deleted mechanisms. | 5 tests in test_orchestration_runtime/test_operator_private_root |
| OR3-97 | remove | host-twin: the oid comes from meta written after init's token check (and OR3-33), and the backend is a host constant. The docstring calls it "the second wall". Keep the `WORKFLOW_BACKEND_HOME_DIRNAMES` table (prune reads it). | test_orchestration_runtime.py::test_an_orchestration_id_that_is_not_a_plain_token_is_refused |
| OR3-98 | undecided | As OR3-94 (an ancestor of the credential home). | the three ancestor tests |
| OR3-99 | remove | operator-only (filesystem). Arm 2 (a container home not in meta) concerns a reused oid on the operator's machine; per lineage, OR3-106 already refuses a stale tree. Arms 1/3 only rewrap OSError. Unpinned. needs-run for arm 2's interaction with resume. | none |
| OR3-100 | remove | operator-only: a rival checkout or reused oid sharing one homes root on the operator's own machine. No leaf path; the incomplete-marker guard is already fail-open by design. | test_orchestration_runtime.py::test_a_marker_naming_another_orchestration_refuses_the_launch, ::test_a_rival_checkout_cannot_take_over_by_REUSING_an_existing_home |
| OR3-101 | keep:break | `owner.json` is read by tools/prune_workflow_homes.py to decide which homes are orphaned. | — |
| OR3-102 | keep:break | The partial-write unlink prevents a half-written credential or config file being reused by the next launch (pinned). The refusal arms (mode/owner/differs) are operator-only sub-arms that go with the OR3-94 decision. | — |
| OR3-103 | merge→OR4-52 | The third copy of the lineage-token rule (with OR3-42). record_launch refuses first (OR4-52, and OR4-54 on warm). The provider-home preparers themselves are #172 surface; this duplicate is not. | test_orchestration_runtime.py::test_prepare_codex_home_refuses_a_cold_launch_into_an_existing_lineage (bad-lineage subTest only) |
| OR3-104 | remove | dead: the only caller passes `workshop=True` (L13917; grep finds no `workshop=False`). The `workshop` parameter can go with it. | none |
| OR3-105 | remove | host-twin: meta is written by init before any launch. An unhandled read error stops the same preparation. Unpinned. | none |
| OR3-106 | keep:break | A cold launch into an existing lineage tree would start the leaf on stale tree files, which the host would then snapshot as the leaf's output (a wrong record). | — |
| OR3-107 | standing:#172 | Operator credential presence for the codex/claude homes. | — |
| OR3-108 | keep:break | Read by the preparers on reuse and by prune. | — |
| OR3-109 | keep:break | A warm repair to a vanished home must fall back via `workshop_lineage_home_missing`. | — |
| OR3-110 | standing:#172 | Codex-only (it also has a leaf gain: a tree the leaf writes is not trusted as a project, so a planted `.codex/` config is not loaded on its next turn). | — |
| OR3-111 | keep:leaf | The bwrap mounts are what bound the leaf's writes (only its config/tree; credential/config read-only on codex). Without them the leaf reaches files the verdict reads. Per-family rows are #172 surface. | — |
| OR3-112 | keep:break | Without the userns/exec probes, a host that cannot run bwrap fails every leaf launch late, with a non-zero exit the classifier may charge as a leaf failure (only a MISSING bwrap binary is caught by name, wc:4504-4516). needs-run for the userns case's category. | — |
| OR3-113 | keep:break | The same late-failure reasoning for `--unshare-net`. The route arm verifies the leaf's network closure (ORCHESTRATION.md §workshop leaf, a required preflight row). | — |
| OR3-114 | undecided | A test-only fail-open override (operator env; no non-test setter; docs/BWRAP_ENABLEMENT.md says do not set it). No leaf path. Removing it means the ~15 test sites that set it patch the probe instead. Operator call. | ~15 test sites |
| OR3-115 | standing:#172 | codex CLI capability probes. | — |
| OR3-116 | remove | dead: `_pass_values_by_check_name` has no caller (grep finds only its definition). The `codex command must be non-empty` arm in the same row is #172 surface and stays. | none |

## Removal groups

Each group is one PR, by mechanism. Line counts are estimates from the function spans at ec2ddda3.

1. **R-prompt: launch-prompt self-consistency** (OR3-21, 23, 24, 25, 26, 27, 28).
   - What goes: `_validate_launch_prompt_text` with `_required_launch_prompt_markers` / `_required_launch_prompt_lines` (L10761-10938; no outside reader), and its call at L15710.
   - What remains: OR3-19 (render-side neither-shape refusal) and OR3-20 (force-render). This group must land together with or after R-request's OR3-61, which also calls `_required_launch_prompt_markers`.
   - Size: about 175 lines; about 30 tests deleted, 1-2 retargeted.
2. **R-request: launch-request host twins** (OR3-01, 39, 40, 46, 47, 48, 49, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61; merges OR3-22, 42, 43, 44).
   - What goes: most of `_validate_leaf_launch_request_payload` (L11497-11655), most of `_validate_launch_request_payload` (L11656-11886), `_validate_canonical_workspace_root_ref`, `_is_placeholder_ref`, and the raise in `_validate_response_agent_session_id`.
   - What remains: OR3-51 (pure_context completeness) and, pending its decision, OR3-41.
   - Size: about 350 lines; about 30 tests.
   - Prerequisite: OR3-43's needs-run (does a warm request carry its lineage's provider?).
3. **R-reply: reply budget** (OR3-31, 32, plus OR4-107).
   - What goes: `REPLY_BUDGET_CHARS`, `_evaluate_reply_budget`, the `reply_over_budget` passthrough (L16906, L17103, L18219) and docs/CLI_REFERENCE.md:218.
   - Size: about 90 lines; 2 tests plus OR4-107's.
4. **R-hook: `post_phase_complete`** (OR3-85, 86).
   - What goes: the function (L12646-12695) and its call inside `write_step_result`'s try.
   - What stays: the rollback (OR4-113), which also covers a failing write.
   - Size: about 55 lines; 2 tests retargeted.
5. **R-resume: operator-input checks on resume** (OR3-33, 34, 35).
   - What changes: the reconciliations run for every prior status.
   - Size: about 40 lines; 4 tests.
6. **R-homes: home-preparation operator checks** (OR3-95, 97, 99, 100, 104, 105; merge OR3-103).
   - What goes: the no-follow chmod (replaced by a plain chmod), the oid/backend second wall, the create-time rewraps, the owner-marker agreement, the dead pure codex branch and its `workshop` parameter, and the meta-missing refusals.
   - What stays: `owner.json` is still written (prune reads it).
   - Size: about 170 lines; about 6 tests.
   - Prerequisite: the OR3-94/98 decision, which is the operator's call on the credential-copy mode.
7. **R-records: terminal-run and step-result host twins** (OR3-67, 68, 89; merges OR3-30, 62, 65).
   - What goes: `_validate_agent_summary_text`, `_validate_pass_output_refs_against_launch` (L11443-11496), the orchestration-role refs check and the required_outputs list check.
   - What merges: the two `agent_runs.jsonl` parsers become one reader.
   - Size: about 130 lines; about 3 tests deleted, 2 positives retargeted.
   - Prerequisite: OR3-30's needs-run on VP3-027's coverage.
8. **R-dead: dead helpers** (OR3-02, 17, 116). About 20 lines; no tests.

**Total if every group lands:** about 1,030 lines of non-test code, and about 75 tests deleted plus about 6 retargeted.
