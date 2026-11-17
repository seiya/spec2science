# Issue #643 step 2: classification, group wc12 (`tools/workflow_conductor.py` L1-7301), checkout ec2ddda3

Inputs: `wc1.md` (68 rows, L1-3952) and `wc2.md` (84 rows, L3953-7301). Two inventory rows are split here because they hold one live part and one dead part: WC1-04 → 04 / 04b, WC1-33 → 33 / 33b. That gives 154 classified rows.

| class | WC1 | WC2 | total |
|---|---|---|---|
| keep:break | 46 | 38 | 84 |
| keep:leaf | 2 | 11 | 13 |
| standing (#172 / #169) | 11 (6 + 5) | 11 (#172) | 22 |
| remove | 6 | 15 | 21 |
| merge | 1 | 7 | 8 |
| operator-diag? | 2 | 1 | 3 |
| undecided | 2 | 1 | 3 |
| defect | 0 | 0 | 0 |

Pointer rows (the call site of a check owned somewhere else) are classed by what the call site does, which is nearly always keep:break. The owner's row makes the real decision.

Facts re-verified against the code for this classification:
- Every LLM `build_launch_request` call passes `pure_leaf=True`: L8918, 9177, 9813 and 16240. The only deterministic call is L14554, and it is reached only after the LLM substeps have returned (L14547).
- The leaf argv carries `--permission-mode acceptEdits` (leaf_contract.py:633/640).
- `toolchain_servable_reasons` (target_profile.py:669-670) requires `control_file` and `build_execute` at launch.
- `TOKEN_PATTERN` (target_profile.py:58, 404-412) only admits lowercase, unpadded toolchain tokens.
- `_write_leaf_bundle_artifacts` has exactly one caller (L7823, through `write_artifacts`). That call runs only at L9069, after `spec.violations` passed at L8994.
- `_launch_setting_surface` returns for an HTTP entry before it calls `leaf_command` (L5545).
- `llm_config.py:818-830` refuses an empty or `codex` model at load time.
- `dependency_violation` has no in-tree writer. `rg dependency_violation tools -g '*.py'` hits only L294; `_problem_dependency_violations` is unrelated.
- The post_judge meta does not record the severity class (L15772-15802).

Methods covered by the derivation drift digest (`tools/tests/test_derivation_transformation_drift.py`), present in this range: `_write_runner`, `_write_makefile`, `_control_file_rules`, `_control_file_module`, `_write_leaf_bundle_artifacts`, `_write_interface_header`, `_interface_header_text`, `build_compiler`, `compiler_wrapper`, `_local_site_record`, `_closure_libraries`, `_stderr_head_and_tail`, and the `_SHAPE_STDERR_*` / `_EXCERPT_*` constants. Removing any row marked **[re-pin]** changes a digest.

| id | class | reason & evidence | tests affected |
|---|---|---|---|
| WC1-01 | keep:break | A corrupt or non-object meta must become a fail, not a crash. The tests drive the real method over corrupt payloads. | — |
| WC1-02 | keep:break | Pointer. The `AUDIT_LOG_BASENAMES` owner is orchestration_runtime. | — |
| WC1-03 | keep:break | Pointer. The owner is orchestration_runtime `_required_child_agent_kind`. | — |
| WC1-04 | keep:break | The build routing table. Without it, a build failure caused by the leaf's sources (compile_error/link_error) is not routed back to generate. The no-category and unknown-category arms make the lookup total over a backend module's classifier output, and backend modules are an extension point. | — |
| WC1-04b | remove | The `dependency_violation` table entry is `dead`: `rg '"dependency_violation"' tools -g '*.py'` hits only L294, and no writer emits it. The leaf gains nothing (no value reaches it), and nothing breaks. Docs to update: phase_03_build.md:58/65/76. | test_build_failure_routing, if it asserts that row (check) |
| WC1-05 | keep:break | Gate routing. The terminal set stops a warm loop on host-classified failures. | — |
| WC1-06 | remove | Scope: the two `escalate` arms (`compile_static_fail_no_category`, `compile_static_unknown_category`). The table itself stays. Evidence kind `host-twin`: the only writer is L12919, which writes the literal `compile_static_violation` together with status fail. A pass carries no category and never reaches the classifier. The leaf gains nothing because the value is host-literal, and both arms are unpinned. needs-run (reading only). | none |
| WC1-07 | keep:break | Execute routing and its terminal set. | — |
| WC1-08 | remove | `dead`: it has no non-test reader (rg -w finds only the definition lines). It is a set difference kept so a test compares two definitions. The test can compute the difference inline. Leaf gain: none. Breakage: none. | test_terminal_execute_set_matches_the_gate_set_and_is_disjoint_from_the_warm_tables, test_the_category_this_row_couples_on_is_the_one_the_code_answers (inline the sets) |
| WC1-09 | keep:break | Without it, a snapshot gap on a host-rendered runner reopens Generate, which cannot repair it, so a correct run burns its budget. | — |
| WC1-10 | keep:break | Routing for a bundle that is still bad after its repair budget. Without it, the leaf failure is not routed. | — |
| WC1-11 | standing:#172 | Codex reading lineage tokens. | — |
| WC1-12 | keep:break | Verify-verdict exhaustion → cold restart routing. | — |
| WC1-13 | keep:break | Repair-loop category for the judge document. | — |
| WC1-14 | keep:break | IR-document exhaustion routing. | — |
| WC1-15 | keep:break | The declared `compile_fail` third exit. Without it, a valid declaration has no route. | — |
| WC1-16 | standing:#169 | The severity class is not recorded (L15772-15802). The only effect is `unknown` → `escalate`, which acts in prod only. The recoverable/unrecoverable precedence still decides whether a mixed list counts as `unknown`. | — |
| WC1-17 | keep:break | Without the attempt cap, a leaf that keeps failing loops the run forever. | — |
| WC1-18 | keep:break | C2 reattribution to the IR. | — |
| WC1-19 | keep:break | Names the fail_closed reason. The `_meta_missing` arm keeps a non-dict meta from crashing (pinned). The `_failed_without_category` arm is a two-line totality arm; both outcomes are fail_closed. | — |
| WC1-20 | keep:leaf | Without it, a leaf verify verdict of `major` or `critical` advances the substep. The value travels verify verdict `issue_severity` → `classify_verify_severity` → advance. | — |
| WC1-21 | keep:break | Without the anchor raise, the slice is silently wrong or empty and the leaf prompt loses its contract. The raise becomes a named fail-closed error. | — |
| WC1-22 | keep:break | Pointer to WC1-21. | — |
| WC1-23 | keep:break | Same as WC1-21, for GENERATE_RULES (per language). | — |
| WC1-24 | keep:break | Pointer to WC1-21. | — |
| WC1-25 | keep:break | Without the anchors, a truncated or absorbed rubric reaches the verify reviewer, which then chooses `issue_severity` wrongly. The third arm (exact terminator) checks a dev-only doc, and the drift digest overlaps it. The docstring records a measured absorption that only this arm names; trimming it is the operator's call. | — |
| WC1-26 | standing:#169 | Diagnostician context budget. | — |
| WC1-27 | standing:#169 | `diagnosis.json` seed. | — |
| WC1-28 | standing:#169 | Directive validation. | — |
| WC1-29 | standing:#169 | Severity → strategy forcing. | — |
| WC1-30 | keep:break | Syntax staging copies the bundle sources. The symlink skip is unpinned, but `src/` is written by the host from bundle content, so it holds no leaf-made symlink. | — |
| WC1-31 | merge→OR record-launch `_validate_leaf_launch_request_payload` (orchestration_runtime.py:11534-11548) | All three arms bound conductor call arguments (`host-twin`). Arms 2 and 3 produce a `leaf_mode=workshop` request without a lineage or provider, and record-launch refuses that one frame later on the only path. Arm 1 (a lineage without `pure_leaf`) only drops a field that is never written. Leaf gain: none. | test_a_workshop_request_names_its_lineage_and_provider_together (re-point to the record-launch message) |
| WC1-32 | remove | `masked`: `child_agent_role(step)` at L1630 raises first for any step outside the table, and every caller passes a `PHASE_ORDER` member. The arm is already `# pragma: no cover`. Leaf gain: none (host value). | none |
| WC1-33 | keep:break | The launch-request payload (deterministic lists, repair fields, workshop fields). It is read by record-launch and by the record audits. | — |
| WC1-33b | remove | The `allowed_output_paths` lists for the LLM substeps (compile.generate, compile.verify, generate.generate, generate.verify, validate.judge; L1707-1780, 1855-1860) and their long comment (L1886-1902) are `masked`. Every LLM call passes `pure_leaf=True`, and the workshop branch then overwrites the list with `[]`. The only deterministic call (L14554) never carries those substeps. Leaf gain: none. Breakage: none. | possibly field-for-field launch-request tests (check that none asserts the pre-override list) |
| WC1-34 | operator-diag? | `_variadic_values` only feeds `claude_tools` (WC2-40). That field has no in-tree reader; ORCHESTRATION.md:177 names it as evidence for "a later audit" that a human would run. Shown: the tool set the claude leaf came up with. | test_variadic_values_reads_a_list_and_stops_at_any_option_token, test_a_command_prefix_carrying_a_tools_flag_is_recorded_as_the_cli_reads_it |
| WC1-35 | keep:break | The leaf wall-clock cap (a measured 99-minute hang). The parsing is lenient normalisation, not policing. | — |
| WC1-36 | keep:break | The timeout marker is the evidence string in the `leaf_timeout` reason. | — |
| WC1-37 | keep:break | Cancel ends a leaf whose lineage the wrapper has ended. | — |
| WC1-38 | undecided | `_admissible_leaf_profile` re-checks a profile the host built (`build_lineage_bwrap_profile` raises on half shapes, orchestration_runtime.py:8372-8389). The persisted-record twin (validate_pipeline_semantics.py:738/8471) audits it again before certification, so this looks like a `host-twin`. Missing before it can be merged: (a) does the VPS twin run over EVERY lineage's launch records (producer, reading, diagnostician) at a stage that blocks certification? (b) does the builder enforce every clause here (credential mapping, codex ro set, proxy env)? The stake is high: a profile that grants checkout write lets a leaf edit gates. | test_each_family_s_profile_is_admitted_for_its_own_entry_only, test_every_other_shape_is_refused |
| WC1-39 | keep:break | Capture budgets bound memory (measured 475 MB / 20 s). | — |
| WC1-40 | keep:break | Records that the output may be truncated. | — |
| WC1-41 | standing:#172 | Codex record reassembly. | — |
| WC1-42 | standing:#172 | Codex line parse. | — |
| WC1-43 | standing:#172 | Bounds a leaf-writable codex rollout (it is also keep:leaf). The backstop at L9684 duplicates it; that is the sibling range's row. | — |
| WC1-44 | standing:#172 | Codex command output sizes. | — |
| WC1-45 | remove | `dead`: `_LEAF_CONDUCTOR_MARKER_LABELS` has no non-test reader (rg -w: L2499 comment and L2516 definition). The test that checks no marker matches an infra pattern can enumerate the label constants itself. Leaf gain: none. Breakage: none. | test_no_conductor_marker_can_be_claimed_as_an_infra_tag_by_a_leaf (enumerate in the test) |
| WC1-46 | standing:#172 | Codex failure-event bound. | — |
| WC1-47 | keep:break | Without the identity proof, a recycled pid gets signalled, which kills an unrelated process. | — |
| WC1-48 | keep:break | Same as WC1-47. | — |
| WC1-49 | keep:break | An out-of-band timeout flag that no leaf text can forge. | — |
| WC1-50 | keep:break | Timeout and infra tag decision. | — |
| WC1-51 | keep:break | Without it, a transient flake fails a correct run (no retry) and a usage limit is not waited out. | — |
| WC1-52a | keep:break | Usage limit: terminal, and arms the wait. | — |
| WC1-52b | keep:break | Retryable (overloaded). | — |
| WC1-52c | keep:break | Retryable (rate limit). | — |
| WC1-52d | keep:break | Arms the stale-credential relaunch (#616). | — |
| WC1-52e | keep:break | Terminal client error. It is also a cross-stream promoting tag. | — |
| WC1-52f | undecided | `llm_permission_probe_unavailable` matches a claude auto-mode message. The leaf argv uses `--permission-mode acceptEdits` (leaf_contract.py:633/640), so only an operator `command:` prefix could produce the message. The tag is terminal like an untagged exit. Missing: whether an untagged nonzero exit is routed differently from a terminal tag (leaf-blamed repair vs transport fail_closed). If it is routed the same way, remove as `dead` (needs-run). | test_classify_leaf_infra_error_tags_each_known_cause (one input row) |
| WC1-52g | keep:break | Retryable transport flake. | — |
| WC1-53 | keep:break | Retry and relaunch bounds. | — |
| WC1-54 | keep:break | Usage-wait schedule (operator opt-in). | — |
| WC1-55 | keep:break | Without it, a cost is recorded from an incomplete envelope (a wrong record). | — |
| WC1-56 | keep:break | The usage row read by `collect_token_cost_summary`. | — |
| WC1-57 | keep:break | Bounds the repair-prompt excerpt and `run_failure` text. [digest: `_stderr_head_and_tail`] | — |
| WC1-58 | operator-diag? | `per_attempt[].failure_excerpt` has no programmatic reader (rg `per_attempt` outside the conductor finds readers of categories and usage only). Comment L400-403 says it is for observability only. Shown: the first 400 characters of each failed attempt's findings. | none |
| WC1-59 | keep:break | Bounded persisted detail. | — |
| WC1-60 | keep:break | Seed layout for a reading leaf; it falls back to verbatim text. | — |
| WC1-61 | keep:leaf | Without the probe instant, the freshness gate cannot tell a stale deliverable from a new one, so a leaf that writes nothing passes on a previous attempt's file. | — |
| WC1-62 | keep:break | `_local_site_record` is read by run_configuration (`build_site`). `_runner_host_authored_m3c` is read by judge_replay.py:688. The compiler helpers are #173 surface. Note: the docstring says the `True` stamp is wrong for an `infrastructure` node, so judge_replay may replay one with the wrong stamp. That is a dev-tool inaccuracy with no leaf gain. | — |
| WC2-01 | remove | `host-twin`: the only production constructor (`run_conductor`, L17787) always passes `llm_config`. Without the check, a None value fails at its first use. Leaf gain: none. Unpinned. | none |
| WC2-02 | standing:#172 | Codex transaction roll-forward. | — |
| WC2-03 | remove | `host-twin`: `run_conductor` always passes `target_profile` (L17793), so only a hand-built Conductor in a unit test reaches the check. Leaf gain: none. | test_target_keyed_store.py::test_a_conductor_without_a_target_refuses_to_read_one |
| WC2-04 | keep:break | The event stream that run_workflow formats. | — |
| WC2-05 | keep:break | Turns a runtime refusal into an exception. The stdin guard stops a run from blocking on the tty. | — |
| WC2-06 | keep:break | Minting an id must fail loudly. | — |
| WC2-07 | standing:#172 | Codex session id. | — |
| WC2-08 | merge→llm_config.py:826 `llm_config_codex_cli_requires_model` | Same test on the same operator value; the load-time check runs before init and preflight. Leaf gain: none. The codex provider stays, so no #172 surface is removed. | test_codex_leaf_rejects_generic_model_alias (re-point to the loader) |
| WC2-09 | remove | `host-twin`: both callers derive `reading` and the endpoint from one predicate (L8400-8411). The persisted record is audited anyway by validate_pipeline_semantics.py:676-696: a producing record must name an endpoint and a reading one must not. Leaf gain: none. Unpinned. | none |
| WC2-10 | merge→WC2-15 | `masked`: the spawn path refuses an HTTP entry earlier (WC2-15), and `_launch_setting_surface` returns for HTTP before `leaf_command` (L5545). | test_llm_http_wiring.py::test_the_launch_argv_builder_refuses_an_http_entry |
| WC2-11 | keep:break | Stdin delivery. | — |
| WC2-12 | remove | `dead` in production: `_bwrap_enabled` always returns True, so the `else`/raw `raise` arm at L4516 is unreachable. Leaf gain: none (the sandbox stays mandatory). Cost: about 13 test sites patch it to False and need another seam. | test_workshop_launch.py:802/821/1214/1243, test_workshop_codex.py:550, test_workflow_conductor.py:11076 |
| WC2-13 | keep:break | Feeds WC2-17. | — |
| WC2-14 | remove | `host-twin`: the only caller passes `cancel` only when `not reading and not http` (L8400). Leaf gain: none. Unpinned. | none |
| WC2-15 | keep:break | Survivor of WC2-10: an HTTP entry must not be spawned as a CLI. It is pinned. | — |
| WC2-16 | merge→`_workshop_timeout` (L8172-8179) | The single caller already produces an int ≥1. Keep the clamp. Leaf gain: none (host value). | test_an_explicit_bound_is_a_positive_integer |
| WC2-17 | keep:leaf | Without it, a missing profile would let a leaf run unsandboxed, where it can write the gate inputs and records that decide its substep. | — |
| WC2-18 | merge→WC2-19 | `masked`: WC1-38's first clause (`leaf_mode != workshop or readonly is not False`) refuses the same shapes. Only the message differs. | test_the_retired_readonly_profile_is_refused_by_name, test_a_writable_profile_without_a_leaf_mode_is_refused, test_a_profile_with_no_readonly_key_at_all_is_refused (re-point messages) |
| WC2-19 | undecided | The raise site of WC1-38; decided with it. | — |
| WC2-20 | keep:break | An invalid profile fails closed. | — |
| WC2-21 | keep:break | Gives a clear sandbox error for a missing bwrap instead of a raw OSError. | — |
| WC2-22 | keep:break | Stdout capture bound (memory). | — |
| WC2-23 | keep:break | Stream-abandoned marker. | — |
| WC2-24 | keep:break | Without it, a cancelled leaf that exits 0 would count as a success. | — |
| WC2-25 | keep:break | Timeout event and record. | — |
| WC2-26 | standing:#172 | Codex clean-finish race. | — |
| WC2-27 | merge→OR `_validate_launch_request_payload` (record-launch role check) | The role is read back from a request file that record-launch already validated (`host-twin`). The WC2-29 catch-all turns any failure into a transport result. The codex provider stays. | test_codex_thread_registration_refuses_to_guess_a_missing_or_unknown_role |
| WC2-28 | standing:#172 | — | — |
| WC2-29 | standing:#172 | — | — |
| WC2-30 | standing:#172 | — | — |
| WC2-31 | standing:#172 | — | — |
| WC2-32 | standing:#172 | — | — |
| WC2-33 | standing:#172 | — | — |
| WC2-34 | standing:#172 | — | — |
| WC2-35 | keep:break | Transport (argv E2BIG). The kept file is a side effect. | — |
| WC2-36 | keep:break | The derivation key on launches. | — |
| WC2-37 | keep:break | `backend_command` is bound into the bwrap profile. | — |
| WC2-38 | keep:break | Pointer to the record-launch refusals. | — |
| WC2-39 | standing:#172 | The HTTP tool surface, audited by VPS. | — |
| WC2-40 | operator-diag? | `claude_tools` has no in-tree reader (`rg claude_tools` outside the writer and docs: none). ORCHESTRATION.md:177 calls it evidence for a later human audit. Shown: the tool set the claude leaf came up with. | test_record_launch_records_the_tool_set_the_leaf_came_up_with, test_the_defaults_entry_fallback_still_records_the_surface, test_orchestration_runtime.py::test_the_setting_surface_is_persisted_in_the_launch_response |
| WC2-41 | keep:break | It is the input the VPS endpoint audit reads. Without it, every producing record is flagged. | — |
| WC2-42 | keep:break | Pointer to the finalize-child refusals. | — |
| WC2-43 | keep:break | `lineage.json` is read by VPS, revocation and judge_replay. Note: `direct_dependency_status` is always the constant "ready" and carries no information. | — |
| WC2-44 | keep:break | The dependency graph sidecar; a builder error fails the phase closed. | — |
| WC2-45 | keep:leaf | Without the surface sidecar, the L3 membership gate is inert (validate_pipeline_semantics.py:9317) and a leaf IR may name dependency operations that do not exist. | — |
| WC2-46 | remove | The two fail-open arms in `_write_dependency_surface`. The graph-read `except Exception` is `host-twin`: the graph was written by WC2-44 moments earlier, and a builder error already failed the phase closed. The `LibraryDeclarationError` arm is masked by the spec-input gate. Replacing both with propagation turns a fail-open into fail-closed, and the leaf gains nothing. Cross-range note for the VPS classifier: L9317 treats a missing sidecar as inert. | test_empty_when_no_graph (it asserts `[]`) |
| WC2-47 | remove | The padding arm is `masked` by the target-profile loader: `TOKEN_PATTERN` fullmatch over `toolchain.build_system`/`language`, target_profile.py:404-412. In fact the whole False answer is masked, because `toolchain_servable_reasons` requires `control_file` at launch. Keep the predicate or collapse it to True together with WC2-57/66. Parity twin to decide together: `orchestration_runtime.control_file_host_authored`. needs-run. | none for the padding arm |
| WC2-48 | keep:leaf | `meta.spec_kind` is written by the leaf. Without it (with WC2-80), the leaf chooses whether the host renders the runner and which gates apply. | — |
| WC2-49 | keep:break | Bundle shape. The `None` arm fails closed. | — |
| WC2-50 | keep:break | Named fail-closed error before any leaf spawn. | — |
| WC2-51 | keep:break | Same as WC2-50 (`_unavailable` is unpinned). | — |
| WC2-52 | keep:break | Clear `--with-deps` precondition error. Without it, the failure surfaces later as a render or build failure charged to the node. | — |
| WC2-53 | keep:break | Same as WC2-52. [digest: `_write_runner`] | — |
| WC2-54 | keep:break | Without it, the condition is misattributed as interface drift instead of a build precondition (the pin discriminates the two). | — |
| WC2-55 | keep:break | Named registry/backend mismatch. | — |
| WC2-56 | keep:break | The product artifacts. | — |
| WC2-57 | remove | `masked`: `_write_makefile`'s early return needs `_core_authors_control_file` to be False, and launch gate `toolchain_servable_reasons` (target_profile.py:669-670) refuses every toolchain without `control_file`. The docstring says the same. needs-run. [re-pin `_write_makefile`] | none |
| WC2-58 | remove | `operator-only`: the launch gate resolves the harness first, so these raises fire only when the catalog or profile is edited mid-run, which is out of the defended set. needs-run: confirm a raw `TargetProfileError` still terminalizes, not as a leaf-blamed category. Unpinned. | none |
| WC2-59 | keep:break | Without it, two closure deps with the same `spec_id` basename overwrite each other's staged sources, which gives a wrong build. | — |
| WC2-60 | remove | The `LibraryDeclarationError` → RuntimeError conversion is `masked` by the spec-input gate and driver, which decide the declarations before any phase (docstring L6234-6236). It is `operator-only` (deps.yaml, spec/libraries). `LibraryUnusable` stays with its owner. needs-run. [re-pin `_closure_libraries`] | none |
| WC2-61 | keep:break | Named error for a missing host-rendered runner. Otherwise a blank ABI ships. | — |
| WC2-62 | keep:break | Named fail-closed errors for missing contract documents. | — |
| WC2-63 | keep:break | Named error for a missing lint rule set. Without it, the leaf is prompted without rules. | — |
| WC2-64 | keep:leaf | Pointer to the CodegenBundle validation, the survivor for WC2-67/68. Without it, a malformed or colliding leaf bundle is written and built. | — |
| WC2-65 | remove | `host-twin`: the guard drops malformed entries from `dependency_graph.json`, which the host itself wrote (WC2-44). Leaf gain: none. Unpinned. | none |
| WC2-66 | remove | `masked`: `control_file_*_unavailable` is reached only after `_core_authors_control_file` is True (that is, the registry provides `control_file`), and the launch gate requires the same capability. needs-run. [re-pin `_control_file_module`, `_control_file_rules`] | none |
| WC2-67 | merge→WC2-64 | `validate_bundle` rejects non-dict entries, empty `logical_path` and non-str content. The only write path (L9069) runs after `spec.violations` passed (L8994). [re-pin `_write_leaf_bundle_artifacts`] | none |
| WC2-68 | merge→WC2-64 | `logical_path_violations` (codegen_bundle.py:563/742) covers the same leaf path on the only write path (L8994 → L9069). The docstring itself says "never fires on a validated bundle". Survivor coverage was confirmed by tracing the single caller. [re-pin `_write_leaf_bundle_artifacts`] | test_leaf_model.py::test_a_traversing_logical_path_is_refused_before_the_write (re-point to the validator) |
| WC2-69 | keep:break | `codegen_bundle.json` is the tamper-gate input. The reader's drop arm is a host-file nicety. | — |
| WC2-70 | remove | `masked`: the §5.1 pin at Compile refuses an IR whose `public_api` the header renderer would reject (docstring L6793-6795). The IR is certified before this runs. needs-run (one spelling per renderer). [re-pin `_interface_header_text`] | none |
| WC2-71 | keep:break | `bundle_meta.json` is read by repair findings, audits and diagnostics. | — |
| WC2-72 | remove | `host-twin`: every caller passes `leaf_mode="workshop"` or the default (grep `leaf_mode=` L9051-10134). Leaf gain: none. Unpinned. | none |
| WC2-73 | keep:break | Named error per unreadable or empty node document (pinned). It also covers run outputs that the spec-input gates do not see. | — |
| WC2-74 | keep:break | The profile document; the sentinel is required by the launch validator. | — |
| WC2-75 | remove | `masked` fail-open: an unresolvable profile already fails the phase closed in `_write_dependency_graph` (WC2-44, docstring L6964-6966). Let it propagate into the named assembly failure instead. needs-run. | test_leaf_compile.py::test_an_unresolvable_profile_is_named_rather_than_failing_the_substep |
| WC2-76 | keep:break | Pointer (the context builders). | — |
| WC2-77 | keep:leaf | Without the object and key-set check, a non-object such as `["ir","compile_fail"]` passes the key test and the document reaches the host write. | — |
| WC2-78 | keep:leaf | Without it, an IR beside a `compile_fail` is accepted as one of the two answers. The leaf could hedge and be taken as the declaration, ending Compile without a certified IR. | — |
| WC2-79 | keep:leaf | Without it, a half IR clears the shape floor. The docstring makes it cost one warm repair instead of a phase reopen. | — |
| WC2-80 | keep:leaf | Without it, a leaf-chosen `meta.spec_kind` turns the `--stage compile` published-surface gate off (comment L7179-7184). | — |
| WC2-81 | keep:leaf | Without it, a leaf-chosen `node_key` selects which gates `Compile.static` applies. | — |
| WC2-82 | keep:leaf | Without it, a leaf-chosen `source_refs.tests` points per-test coverage at another tests file. | — |
| WC2-83 | keep:break | Without it, an unserializable IR fails at the host write as transport fail_closed (misattribution) instead of being repaired. | — |
| WC2-84 | keep:leaf | Without it, a malformed or inadmissible `compile_fail` passes as the terminal declaration and ends Compile without an IR. | — |

## Removal groups

1. **Dead LLM-substep launch-request branches** (`build_launch_request`): WC1-33b and WC1-32. Removes the LLM `allowed_output_paths` lists, their comment block and the `pragma: no cover` raise. About 55 lines. No digest. Check the launch-request field-for-field tests.
2. **Host-twin argument refusals on the launch/spawn path**: WC1-31, WC2-01, WC2-03, WC2-09, WC2-10, WC2-14, WC2-16, WC2-18, WC2-72, plus WC2-08 and WC2-27 (codex duplicates of loader / record-launch checks; the provider stays). About 75 lines. Six pinned tests get re-pointed or deleted (listed per row). No digest.
3. **Toolchain capability predicates masked by the launch gate**: WC2-47, WC2-57, WC2-66. Collapse `_core_authors_control_file` to the registry answer (or True) and drop the leaf-authored control-file early return and the unavailable raises. About 30 lines here. The leaf-authored control-file path elsewhere in the tree is the same dead branch and should go in the same PR. [re-pin `_write_makefile`, `_control_file_module`, `_control_file_rules`] Decide with `orchestration_runtime.control_file_host_authored` and the validator mirror (validate_pipeline_semantics.py:2831/2874).
4. **Bundle write belt-and-suspenders**: WC2-67, WC2-68, WC2-65. About 20 lines. [re-pin `_write_leaf_bundle_artifacts`] One test re-pointed.
5. **Mid-run re-checks of operator files / certified IR**: WC2-46, WC2-58, WC2-60, WC2-70, WC2-75. The two fail-open arms become propagation; the rest are conversions masked by the launch gate or spec-input gate. About 50 lines. [re-pin `_closure_libraries`, `_interface_header_text`] All are needs-run: confirm that the raw exception terminalizes without a leaf-blamed category.
6. **Test-only constants and dead routing entries**: WC1-04b (plus phase_03_build.md lines 58/65/76), WC1-06 (two arms), WC1-08, WC1-45, WC2-12 (needs a replacement test seam at about 13 sites). About 60 lines of production code.

Total estimate: about 290 lines of non-test code across 21 remove and 8 merge rows.

Operator calls (operator-diag?): WC1-34 + WC2-40 (`claude_tools`, ~30 lines with `_variadic_values`), and WC1-58 (`per_attempt[].failure_excerpt`).
Undecided: WC1-38 / WC2-19 (spawn-time profile predicate vs builder plus VPS twin) and WC1-52f (permission-probe tag routing).
