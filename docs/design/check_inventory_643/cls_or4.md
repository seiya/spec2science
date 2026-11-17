# Issue #643 classification — or4: tools/orchestration_runtime.py L14356-19182 (checkout ec2ddda3)

Input `or4.md`: 140 rows plus 6 extra rows (X-1..X-6) from its "Further facts" section.

**Counts.**

| class | count |
|---|---|
| keep:leaf | 1 |
| keep:break | 39 |
| remove | 61 |
| merge | 11 |
| standing:#172 | 6 |
| defect | 0 |
| operator-diag? | 3 |
| undecided | 12 |
| pointer (classified in the owner's group) | 13 |

The total is 146: OR4-01..140 and X-1..X-6.

**Facts behind the classes.**
- **Where leaf values enter.** No value a leaf authors reaches this range as a check input, apart from three routes: the host-formatted reply, the `result_summary` excerpt and the revoke arguments (or4.md header (a)-(c)). Most refusals here re-check values the conductor composed, so they are `host-twin`.
- **The sandbox profile is the leaf's real confinement.** `spawn_leaf` reads `sandbox_profiles/<arid>.json` and refuses to launch without it (workflow_conductor.py:4338-4440, read). That makes OR4-70 `keep:leaf`. The post-run re-checks of the stamp on the response (OR4-90..94) are host-twins.
- **Both preflight callers pass `--llm-config`** (run_workflow.py:4085, judge_replay.py:~424). So `providers` is always written and holds the `defaults` surface (probe_all_providers L14815-14834, main L18879-18894). The pre-#28 arms are therefore legacy-only.
- **Measured: claude CLI 2.1.326 refuses an unknown option.** `claude --no-such-opt-xyz -p </dev/null` printed `error: unknown option` with rc=1. A CLI that lacks a workshop option therefore fails its first leaf launch closed: it is the `pure_transport` arm (workflow_conductor.py:8730-8732), and that is not a retryable tag (`_RETRYABLE_LEAF_INFRA_TAGS`, :3635). `_leaf_infra_error` on that stderr was run and returns None. That means no transient retry happens (workflow_conductor.py:14656) and no repair turn is charged (`can_repair` excludes `pure_transport`, :9115); the substep fails with that category. The rest of the end-to-end launch was read, not run.
- **Derivation drift.** No method in this range is digested by `test_derivation_transformation_drift.py`. Its `ort.*` references are `toolchain_version_argv` (L2504), `WORKFLOW_PHASE_DOC_BY_STEP` and `PURE_CONTEXT_REQUIRED_KEYS*`. No re-pin is needed for any row below.
- **Pins.** Pins tagged `(unverified)` in or4.md were not opened here either. The "tests affected" column lists them as the inventory gives them.

| id | class | reason & evidence | tests affected |
|---|---|---|---|
| OR4-01 | remove | `masked`: the only caller, `probe_execution_platform`, substitutes the default command for an empty command at L14869-14874 (read), so `shlex.split` is never empty. Leaf gain: none (operator command). | none |
| OR4-02 | remove | Only the *gating* goes; the `agent_version` capture stays as a record. `operator-only` (installed CLI). A missing or broken binary also fails OR4-06/07, and failing that, the first launch (see header). | test_run_workflow.py::test_preflight_fail_names_each_failing_check_and_its_detail (adjust) |
| OR4-03 | remove | `operator-only` (substitute binary). It is also masked by OR4-05/06: an empty `--help` names no option line. Removed with the CLI-probe group. | test_probe_execution_platform_claude_fails_when_help_exit0_but_empty_stdout |
| OR4-04 | undecided | `operator-only` (CLI stdin semantics). What is missing: a run of the first leaf launch on a CLI that does NOT take its prompt from stdin. If that launch yields a reply the host assembles as a leaf failure, the misattribution charges the leaf's repair budget (keep:break). If it exits non-zero, the failure is a clean transport fail_closed (remove). | test_probe_claude_backend_requires_the_cli_to_name_stdin_as_an_input |
| OR4-05 | remove | `masked` by OR4-06 on every in-tree path: `providers` always carries the defaults surface and is re-probed on TTL expiry (L9468-9471), and OR4-36 fails the preflight. `operator-only`: a CLI without `--restricted` exits 1 on that option (measured above). This reverses issue #453's decision to probe for it; name that in the PR. | test_a_claude_help_without_restricted_fails_preflight |
| OR4-06 | remove | (a) Leaf gain is nothing: the input is the operator's installed CLI. (b) No breakage: a CLI lacking an option exits 1 on it rather than dropping it silently (measured, CLI 2.1.326). The first leaf launch then returns `pure_transport` (workflow_conductor.py:8730-8732). `_leaf_infra_error` on that stderr returns None (run), so no transient retry happens (:14656) and no repair turn is charged (`can_repair` excludes `pure_transport`, :9115). The substep fails with that category. Cost: a provider that only a late substep uses fails after the earlier billed phases. Reverses the issue #534 decision to probe the option set. | test_workshop_launch.py::test_the_check_requires_every_option_as_an_option_line, ::test_every_cli_surface_an_entry_launches_through_takes_the_check; test_leaf_contract.py::test_the_preflight_option_census_is_the_union_of_both_argvs |
| OR4-07 | remove | The fail-closed catch of OR4-06 (claude arm). It goes with OR4-06; the codex arm stays with OR4-08. | test_workshop_launch.py::test_an_unrunnable_cli_fails_the_row_rather_than_the_preflight |
| OR4-08 | standing:#172 | Codex workshop flag-set probe. | — |
| OR4-09 | remove | `dead` fail arm: both callers pass only `claude_cli`/`codex_cli` tokens (L14815-14816, L9400), which are exactly the table's keys. Without the arm, a KeyError still fails closed. `needs-run` for the unreachability. The function shrinks to the codex dispatch once OR4-06 goes. | none |
| OR4-10 | undecided | A fail-closed arm of OR4-12 (unreadable file). What is missing: whether the claude CLI applies any `env` from a `~/.claude.json` the host cannot read or parse. If it applies none, the arm defends nothing. Not run, because the run would rewrite the operator's home config. | none |
| OR4-11 | undecided | Same as OR4-10, for a file that is valid JSON but not an object. | none |
| OR4-12 | keep:break | An `env` block reaches every claude leaf despite `--restricted` and changes the model / base URL. Without the check, the launch record and derivation key name the llm.yaml model while another model produced the result, which is a false record. Kept by operator decision (#453). | — |
| OR4-13 | standing:#172 | HTTP key-env probe. | — |
| OR4-14 | standing:#172 | HTTP base_url probe. | — |
| OR4-15 | standing:#172 | HTTP reachability probe (escape hatch has no in-tree setter). | — |
| OR4-16 | keep:break | `preflight.json#providers` is read by the per-launch provider gate (L6531-6545), by `record_launch` and by the TTL re-probe. | — |
| OR4-17 | keep:break | The top-level preflight payload is read by `_require_preflight_launchable` / `_preflight_launch_refusals`, and the audit reads `agent_version`. | — |
| OR4-18 | pointer | Owners are in OR1-3: `_all_strict_boolean_probe_checks_pass`, `_probe_bwrap_sandbox`. | — |
| OR4-19 | standing:#172 | Pointer to the codex probe. | — |
| OR4-20 | remove | `masked`: the only CLI route has argparse `choices=sorted(SUPPORTED_BACKENDS)` (L18316). The in-process callers pass host tokens: `probe_all_providers` passes CLI-row tokens only, and `_run_live_probe_and_update` coerces to `claude` (L9450-9452, read). | none |
| OR4-21 | remove | `operator-only` (llm.yaml `command` vs `backend`). It fires only when the command is the other backend's literal default. That CLI then fails the remaining probe rows, or its first launch. | test_probe_execution_platform_rejects_backend_command_mismatch |
| OR4-22 | remove | `operator-only` environment kill switch. No in-tree setter: rg finds only the reader at L14914-14917, and no doc names it. The `child_agent_forbidden_by_session_policy` refusal in `_check_session_policy_launchable` (L6352, owner OR2/3) goes with it, as do OR4-31's `session_policy` arms. | test_workflow_launch_check_fail_closed_when_session_policy_missing (+ any session-policy tests in OR2/3) |
| OR4-23 | operator-diag? | `meta.repo_revision`: no code reader (rg: writers only, L15047 and conductor :14230). docs/ORCHESTRATION.md:353 states it as provenance for tracing a run to its source. | — |
| OR4-24 | remove | `operator-only` (`--orchestration-id`; ids from `_new_orchestration_id` and judge_replay are always tokens). A bad id fails at the first gate that interpolates it, before any leaf launch (the comment's claim; `needs-run`). `resume_orchestration` L11045 holds the twin, which goes in the same PR (owner OR3). | test_init_and_resume_refuse_an_id_that_is_not_a_path_token |
| OR4-25 | merge→`_record_until_phase_high_water` L5781 + the assign loop L15086-15091 | The in-code comment states that both later steps already cover the property: a prior value overwrites, and a missing prior takes the max with the run's own `until_phase`. A supplied value can only raise the bar. The input is the operator's (`--invocation-json`). Removal reverses the comment's "kept anyway (1-b)"; name that. | none (comment: the suite stays green) |
| OR4-26 | remove | `masked` by argparse `choices=sorted(SUPPORTED_BACKENDS)` at L18254, a narrower set, on the only route. `init_orchestration` has no in-process caller (rg). | none |
| OR4-27 | keep:break | The init records: meta, the graph, the orchestration row and the session index. All of them are read by gates and validators. | — |
| OR4-28 | remove | `host-twin`: run_workflow composes `--invocation-json` (run_workflow.py:3947). Without the check, `json.loads` or the later `.get` still raises, fail-closed (a traceback instead of a message). | none |
| OR4-29 | pointer | `resume_orchestration` (OR3). | — |
| OR4-30 | pointer | `_validate_preflight_payload` (OR3). | — |
| OR4-31 | remove | Only the `session_policy` / `session_policy_launchable` backfill arms go. They are `dead` on the main route, because `probe_execution_platform` always writes both fields (L14921-14937, read). The `probed_at` backfill stays: the TTL reads it. These arms go with OR4-22. | test_get_preflight_ttl_status_* (check none feeds a payload without session_policy) |
| OR4-32 | undecided | The persisted `dependency_readiness` is required to EXIST by `_dependency_ready` (L6380-6420, owner OR2/3), which then recomputes live. Its only other reader is the `certified_deps` leaf-reset at L15331. What is missing: OR2/3's classification of that existence check. If it is a host-twin, this refresh, OR4-33 and OR4-140 can go as one unit. | — |
| OR4-33 | remove | `operator-only` (catalog corrupt, PyYAML missing). Without the catch the RuntimeError propagates, so the preflight fails closed. The gate recomputes live anyway, so a preserved record unblocks nothing. Its four log events have no reader (rg: writers only, L15261-15319). | none found |
| OR4-34 | keep:break | `preflight_passed` drives the phase-state machine. Sub-removal: the `preflight_written_not_launchable` event is a `no-reader record` (rg: writer L15348 only). | — |
| OR4-35 | remove | `operator-only`: an llm.yaml edited during the startup window. The run's invocation already pins the sha (meta `invocation`), and the resume gate compares it. A launch of an unprobed token is still refused per launch (L6531-6545). | test_preflight_refuses_a_snapshot_that_changed_under_it |
| OR4-36 | keep:break | Without it, `preflight.json` records `status=pass` while a configured provider cannot launch. That is a false record, and the failure surfaces mid-run instead. | — |
| OR4-37 | pointer | `get_preflight_ttl_status` (OR3). | — |
| OR4-38 | remove | `host-twin`: the conductor always passes `--netns-socket-dir` (workflow_conductor.py:5486-5487). Without it, `network_block(None, …)` raises inside OR4-71's catch, which is fail-closed. | test_record_launch_requires_the_socket_dir_to_be_the_conductors_own (subtest "absent") |
| OR4-39 | remove | `host-twin`: the dir comes from `leaf_netns.make_socket_dir`. A missing dir also fails the bwrap bind at spawn. | same test, subtest "missing" |
| OR4-40 | remove | `host-twin`: the host created the dir 0700 and owns it. That it lies outside the checkout and outside any leaf work tree is the inventory's note (`leaf_netns.make_socket_dir` was not re-read). A leaf therefore cannot swap it. Leaf gain: none. | same test, subtests "not private", "a link" |
| OR4-41 | merge→conductor `leaf_command` loopback refusal (test_workshop_codex.py::test_the_endpoint_must_be_a_loopback_wrapper_url) | The same URL is checked upstream on the only route. The port parse stays; `int()` of a non-digit raises inside OR4-71. | none found |
| OR4-42 | pointer | `leaf_netns.network_block_violations`. | — |
| OR4-43 | remove | `host-twin` (orchestration arid and uuid child arid). A hand-run `record-launch` is the operator's own act. Path-segment safety is out of scope: a traversal gains the leaf nothing. | test_record_launch_rejects_traversal_in_child_agent_run_id, ::…parent… |
| OR4-44 | pointer | `_require_preflight_launchable` (OR3). | — |
| OR4-45 | undecided | The best-effort status=`fail` on a preflight-gate refusal. What is missing: whether the conductor's own failure path already terminalizes the orchestration when `record-launch` exits 1. If it does, this is a host-twin. | — |
| OR4-46 | merge→OR4-59 | The same `providers[token].launchable` test runs in `pre_phase_launch` → `workflow_launch_check` (L6531-6545, read). `record_launch` calls it with the same `backend_token` on every launch, and every launch has step and node_key (OR4-63). Change of order: the survivor fires after the warm lineage-home lookup, OR4-48 and OR4-58. It sets status `fail_closed` (reason `backend_not_probed`) where this raised without a status. The `backend_token` assignment stays. | test_record_launch_refuses_a_provider_preflight_did_not_probe (re-point to the L6542 reason) |
| OR4-47 | remove | `dead` on in-tree routes: the pre-#28 / no-`providers` fallbacks. Both preflight writers always record `providers` (header). `needs-run`: resume an orchestration whose preflight predates #28. | none |
| OR4-48 | standing:#172 | HTTP-provider admissibility. Note: by its own comment it is a 3-copy defense in depth (llm_config, the conductor, here). Any merge is the operator's call. | — |
| OR4-49 | remove | `host-twin`: the conductor passes `--workshop-lineage-id` only for a warm workshop launch. | test_record_launch_refuses_a_lineage_id_off_workshop_or_malformed (first half) |
| OR4-50 | remove | `host-twin`: `workshop_provider` and `backend` come from one conductor entry. | none found |
| OR4-51 | remove | `host-twin`: same source as OR4-50, so the request and the launch cannot disagree. | test_workshop_launch.py::test_a_request_naming_another_provider_is_refused; test_workshop_codex.py::test_the_request_names_this_launch_s_provider |
| OR4-52 | remove | `host-twin`: the lineage id is a host uuid. | none found |
| OR4-53 | remove | `host-twin`: a cold request's lineage id = child arid, set by the conductor. | none found |
| OR4-54 | remove | `host-twin`: a warm id is the prior child arid (host). Path-segment safety is out of scope. | test_record_launch_refuses_a_lineage_id_off_workshop_or_malformed (second half) |
| OR4-55 | remove | `host-twin`, as OR4-53. | none found |
| OR4-56 | keep:break | Without it, a cold launch with no home would return the warm-only `workshop_lineage_home_missing` sentinel. The conductor would then take its "fall back to cold" route on a cold launch. | — |
| OR4-57 | keep:break | The conductor's warm→cold fallback depends on this answer. | — |
| OR4-58 | undecided | The comment says it defends against a conductor bug that launches two claude leaves, and drivers are serialized by the claim. What is missing: why claude launches must still be sequential now that each lineage has a private config home. If no reason remains, this check goes with the `active_child_agent_run_id.txt` marker (part of OR4-79), OR4-102 and OR4-119's legacy arm. | test_a_second_launch_while_a_child_is_live_is_refused_and_fails_closed |
| OR4-59 | keep:break | It is the only per-launch site for the provider check and for `_downstream_phase_launch_gate`. The conductor's workflow-launch-check runs once per phase, with `defaults` (workflow_conductor.py:15859-15865). So the inventory's "runs twice" holds only partly. The content is classified by its owner (OR2/3). | — |
| OR4-60 | keep:break | Picks the executable bound into the sandbox. With the wrong one, the leaf's own binary is outside its sandbox. | — |
| OR4-61 | pointer | `prepare_launch_request_payload`, `_validate_response_agent_session_id`, `_validate_launch_request_payload`, `_validate_launch_prompt_text` (OR3). | — |
| OR4-62 | remove | Reply arm: `_extract_launch_reply_text` falls back to `json.dumps(response)`, which is never empty (L10717-10722). Prompt arm: host-rendered, and an empty prompt fails `_validate_launch_prompt_text`'s workshop-sentinel AND (L10835-10842) on a workshop request. `needs-run` for the deterministic-request arm. | none found |
| OR4-63 | merge→`_validate_launch_request_payload` L11657-11663 | That check refuses an empty node_key or step on the same request earlier (read). The `if` at L15744 is always true. | test_record_launch_rejects_missing_step_or_node_key (re-point to L11661 message) |
| OR4-64 | remove | Only the `_resolved_makefile_host_authored` field goes: a `no-reader record` (rg: writer L15779 only; its comment says the reader went in Z4). `_resolved_build_system` and the TargetProfileError path stay. | test_workflow_conductor.py::test_conductor_runtime_makefile_authorship_agree, the key list at test_workflow_conductor.py:162, fixture data/conductor_launch_requests/*.json |
| OR4-65 | pointer | `_allowed_output_paths_for_launch` (OR3). | — |
| OR4-66 | remove | `host-twin`: `source_id` / `source_binary_id` come from `NodeRefs`. Validate.execute is deterministic and executes `refs.source_binary_id` itself (workflow_conductor.py:13494/:13562). No leaf authors either value. | test_execute_launch_requires_source_build_id_for_in_phase_log_only |
| OR4-67 | remove | `host-twin`: Build is in-process and writes `binary_meta.json`. | none found |
| OR4-68 | remove | `host-twin`: `source_source_id` is host-written by Build. | test_execute_launch_rejects_unrelated_generation_when_build_meta_records_lineage |
| OR4-69 | undecided | Reads `source_meta.verification_status`, which derives from the leaf's verify verdict, so it is NOT a host-twin. Its stated purpose ("write authority") died in Z4, but it also refuses build/execute on a non-pass generation. What is missing: the survivor that covers the same value on every path. Candidates are the conductor's phase sequencing and VPS (test_run_quality_checks_rejects_failed_source_generation). Neither was traced. | test_validate_launch_rejects_cross_phase_log_for_unknown_source_id, test_execute_launch_rejects_cross_phase_log_for_failed_generation |
| OR4-70 | keep:leaf | Without it, a leaf is closer to a green verdict: unconfined, it could edit the checkout's validators. `spawn_leaf` launches only inside `sandbox_profiles/<arid>.json` (workflow_conductor.py:4338-4440). Sub-removal: the response field `sandbox_command` is a `no-reader record` (rg: writer L16052 and comments only). | — |
| OR4-71 | keep:break | Fail-closed catch around the profile build. It writes the `violations/` record and `sandbox_enforcement_violation` status, which audit_orchestration, run_workflow and the conductor read. | — |
| OR4-72 | keep:break | Lineage stamps, read by VPS:724. | — |
| OR4-73 | keep:break | HTTP / in-process stamps, read by `record_agent_run`'s exemption (OR4-89) and the audits. | — |
| OR4-74 | remove | `host-twin`: by the comment, the prompt renderer has already refused a non-workshop CLI request. A removal would leave `assert workshop_isolation is not None` inside OR4-71's catch, which is still fail-closed. | none found |
| OR4-75 | remove | `host-twin`: `backend` and `backend_command` in the response are both written by the conductor from one entry (workflow_conductor.py:5452-5461, read), and `backend` is always present. So the family resolver cannot disagree with the token. | none found |
| OR4-76 | keep:break | `launches/` records, read by record_timeout, record_agent_run, the validators and the audit. | — |
| OR4-77 | keep:break | The session-index row and graph edge are read by VPS and the conductor. Sub-item: the `workflow_hooks.jsonl` `pre_agent_launch` entry has no code reader (rg: writer L5887 only). WORKSPACE_LAYOUT.md:67 names it as a host-side log, so it is operator-diag?. | — |
| OR4-78 | undecided | A malformed `agent_graph.json` is silently reset to `{"edges": []}`, and the prior edges are lost. What is missing: whether a VPS check that reads the edges can PASS on a reset graph that the lost edges would have failed. If so, it is a fail-open (defect). The simpler shape is to raise. | none found |
| OR4-79 | keep:break | `active_children/<arid>.txt` is read by OR4-108 and the diagnostics. The claude single-file marker follows OR4-58. | — |
| OR4-80 | keep:break | Torn-last-line tolerance while a writer holds the lock. Without it, the unlocked pre-reads (record_timeout, record_agent_run) report durable corruption during a normal write. | — |
| OR4-81 | keep:break | Without it, a corrupt line is silently skipped, the duplicate-arid check (OR4-84) is blind to that row, and a second terminal row could be recorded. | — |
| OR4-82 | keep:break | The one role and arid check that covers all three routes: record-agent-run, finalize-child, and record-timeout. record-timeout calls `record_agent_run` in-process at L16494 and never passes through OR4-133; there the role comes from the host-written launch request (L16313-16321, read). A role outside the vocabulary silently switches off `_validate_terminal_run_payload`'s surviving checks, a fail-open (comment L16531-16538). Survivor for OR4-133's role and arid arms. | — |
| OR4-83 | remove | `host-twin` on every route. On finalize, `agent_backend` is the conductor's `entry.backend_token`, already authorized per launch (OR4-59). On record-timeout it is read from the host-written launch response (L16323-16328). | test_record_agent_run_requires_agent_backend, test_record_agent_run_rejects_unknown_backend |
| OR4-84 | keep:break | One terminal row per arid; the locked re-check closes the race. It is the survivor for OR4-117/118. | — |
| OR4-85 | pointer | `_validate_step_or_substep_launch_refs`, `_evaluate_reply_budget` (OR3). The budget joins removal group G5. | — |
| OR4-86 | keep:break | The backfilled fields are read by pre_judge, the audits and `collect_token_cost_summary`. | — |
| OR4-87 | remove | `host-twin`: `record_launch` wrote that response as a dict, after running the same `_validate_response_agent_session_id` on it (L15695). | test_session_id_helper_failure_records_specific_fail_reason |
| OR4-88 | remove | `host-twin`: both sides are the child arid. The conductor writes them at workflow_conductor.py:5447-5448, and record_timeout resolves the session id from the index or response that record_launch wrote (L16447-16451). | test_rejects_agent_run_when_launch_response_session_id_differs |
| OR4-89 | keep:break | Without the exemption, every HTTP and in-process row would be refused. | — |
| OR4-90 | remove | `host-twin`: `record_launch` stamps `bwrap` on every CLI launch or fails closed (OR4-70/71). The response sits outside every leaf bind. The real confinement gate is `spawn_leaf` (fail-closed on a missing profile). | test_sandbox_violation_records_specific_fail_reason |
| OR4-91 | remove | `host-twin`, as OR4-90. | none found |
| OR4-92 | remove | `host-twin`, as OR4-90 (stamp L16055). | test_a_launch_without_the_network_stamp_is_refused_by_name |
| OR4-93 | remove | `host-twin`, as OR4-90. | none found |
| OR4-94 | remove | `host-twin`: the profile file was written by `record_launch`, and `spawn_leaf` refuses to launch without it. | none found |
| OR4-95 | undecided | `setdefault` of sandbox fields into the agent_runs row. What is missing: a reader of the ROW's sandbox fields. VPS:8274 reads `sandbox_profile_ref` from some payload, which was not traced. | — |
| OR4-96 | pointer | `_validate_terminal_run_payload` (OR3). | — |
| OR4-97 | keep:break | `agent_runs_invalid.jsonl` is read by audit_orchestration, diagnostics and VPS. | — |
| OR4-98 | keep:break | agent.result.json and summary, read by leaf_usage, VPS and the conductor. | — |
| OR4-99 | keep:break | The terminal session-index row and marker release. Finalization needs both. | — |
| OR4-100 | keep:break | `cleanup_committed`, read by validate_workspace_root. | — |
| OR4-101 | remove | `no-reader record`: `reply_over_budget` has no reader in the conductor or elsewhere (rg: orchestration_runtime only). It measures a host-formatted reply. Group G5. | test_over_budget_soft_returns_info_and_audits, test_always_keep_surfaces_soft_failure_field |
| OR4-102 | undecided | It reads the claude single-file marker, so it follows OR4-58. | test_deactivate_child_validates_before_unlinking_per_arid_marker |
| OR4-103 | undecided | An idempotency answer on the manual `deactivate-child` route, not a check. Unreachable from finalize (OR4-108). It is in scope only if that manual command is retired, which is the operator's call. | — |
| OR4-104 | remove | `operator-only` on the manual record-reply route; there, an empty reply is still refused later by `_validate_step_or_substep_launch_refs` (L12123-12126). On the finalize route it is masked by OR4-105, which runs first. | none found |
| OR4-105 | keep:break | Survivor of the non-empty-reply family. It refuses before `deactivate_child_agent` consumes the marker. With an empty reply, `record_agent_run` → `_validate_step_or_substep_launch_refs` refuses "launch_reply_ref target must be non-empty" (L12123-12126, read), but only after the marker is gone, so the run can no longer be finalized. The `--agent-run-id` arm is a host-twin of argparse `required=True` plus the conductor's arid. | test_empty_stdin_reply_is_a_dispatch_error_not_a_silent_pass (cat-masked) |
| OR4-106 | remove | The dict arm is `masked` by `_json_arg` / `_json_file_arg` (OR4-130) on the only route. The equality arm is a `host-twin`: the conductor writes both from one `child_arid` (workflow_conductor.py:5580-5587). | none found |
| OR4-107 | remove | `operator-only` environment switch (no in-tree setter; rg finds only the readers at L11031 and L17038) on a host-formatted reply. Leaf gain: none. The readers of `reply.txt` are `_validate_step_or_substep_launch_refs` and VPS:8214 (both presence and non-empty only), run_workflow.py:896 (an operator tail) and audit_orchestration.py:173 (operator text). None judges the content (rg `launch_reply_ref|reply\.txt`). Goes with `_evaluate_reply_budget`'s hard and soft arms (OR3), OR4-101 and docs/CLI_REFERENCE.md:218. | test_finalize_child_hard_budget_fails_before_consuming_state |
| OR4-108 | keep:break | Cross-invocation state (resume / crash between finalize and next step). Without it, a second finalize overwrites `reply.txt` before OR4-84 refuses, which leaves a reply that disagrees with the recorded row: a false record. | — |
| OR4-109 | pointer | `_require_preflight_launchable`, `_phase_state_allows_write_step_result` (OR3). | — |
| OR4-110 | remove | `host-twin`: the conductor passes its own orchestration arid or the step arid (:10176/:16180). The comment names the pre_judge validator (VPS:8875-8910) as the later catch. The "re-run with the correct arid" remedy addressed an agentic caller that no longer exists. | none found |
| OR4-111 | remove | `host-twin`: VPS:8882 re-checks executor == step_result directory arid. | none found |
| OR4-112 | pointer | `_validate_step_result_payload`, certification stamp, `post_phase_complete` (OR2/3). | — |
| OR4-113 | operator-diag? | `step_result.overwritten.<N>.json` has no code reader (rg). docs/CLI_REFERENCE.md:266 and WORKSPACE_LAYOUT.md:72 state it as the attempt archive. The rollback on hook rejection is keep:break and is not part of this question. | — |
| OR4-114 | remove | `operator-only` (manual CLI only; inventory grep G1). Argparse `required=True` covers presence. | none found |
| OR4-115 | remove | `operator-only`. Without it, the read of a missing file still raises `FileNotFoundError`, uncaught by main, so it is fail-closed as a traceback. | test_record_timeout_rejects_missing_launch_request |
| OR4-116 | remove | `operator-only` (manual CLI only). Both values come from the host-written launch files. Role: OR4-82's vocabulary check stays. An orchestration arid has no launch request, so the read fails closed. | none found |
| OR4-117 | merge→OR4-84 | Same value; the comment says so. Read L16420-16509: record_timeout writes nothing before `record_agent_run`. Its marker unlinks run after it, and the tmp cleanup happens inside record_agent_run after the append (OR4-100). OR4-84 at L16561 is that function's first durable check. | test_record_timeout_refuses_when_arid_already_in_agent_runs_jsonl |
| OR4-118 | merge→OR4-84 | An already-finalized run hits OR4-84. A never-launched run has no `launches/<arid>.request.json`, so `_read_json` raises once OR4-115 goes (fail-closed; a traceback, because main's record-timeout catch excludes OSError). | test_record_timeout_refuses_when_session_run_index_status_not_running |
| OR4-119 | remove | (a) `operator-only`: only the operator's manual command reaches it; the automated callers it was written for ("Adv-14") no longer exist. (b) An operator who times out a live child has the conductor's later finalize refused by OR4-108: the substep fails closed and no wrong record results. The RUNBOOK §substep-timeout-recovery step changes with it. | test_record_timeout_refuses_for_codex_backend_when_marker_present, ::force_reason_bypasses… |
| OR4-120 | operator-diag? | `forced`, `forced_reason`, `timeout_reason` have no code reader (rg: writer and CLI help only). The CLI help says "for audit". | — |
| OR4-121 | merge→argparse `choices=list(STEP_KEYS_FOR_NODE_STATE)` L18716/L18782 | Argparse runs first on the only route. | test_reset_phase_refuses_a_step_that_is_not_a_phase (direct call) |
| OR4-122 | merge→conductor filter workflow_conductor.py:10262/:10268 | The leaf-derived grade crosses the conductor's fixed-set filter first, on the only automated route. The manual route is operator-only. | none found |
| OR4-123 | keep:break | The noop answer the conductor decides from (#369). | — |
| OR4-124 | keep:break | Without it, the documented manual revoke recipe exits 0 while the phase stays certified. The revocation the operator ordered silently does not happen, so the certified artifact is reused. | — |
| OR4-125 | keep:break | Phase reset record, read by the completion vouch. | — |
| OR4-126 | keep:break | Orchestration row rewrites, read by validate_workspace_root and the session audits. | — |
| OR4-127 | remove | `host-twin` (meta written by init). `_read_json` and the later `.get` raise anyway, and set-status has no catch, so it is fail-closed either way. | none found |
| OR4-128 | pointer | `_validate_orchestration_completion_for_pass` (OR3). | — |
| OR4-129 | remove | `host-twin`: every caller passes a constant code (in-range OR4-58/59/71, the conductor, run_workflow). No test drives it. Keep the constant `FAIL_CLOSED_REASON_CODES` if other readers use it. | none (test_workflow_conductor.py near :1746 asserts membership only) |
| OR4-130 | keep:break | The argparse JSON converters themselves. Survivor for OR4-106's dict arm. | — |
| OR4-131 | keep:break | Without it, a terminal record could be rewritten, e.g. `fail` → `pass` by a later set-status in another invocation (run_workflow after the conductor). | — |
| OR4-132 | keep:break | `set_status_noop_replay` / `cleanup_retry` events, read by audit_orchestration. | — |
| OR4-133 | merge→OR4-82 | The role and arid arms duplicate OR4-82, which runs on every route including record-timeout. The `status`, `node_key` and `agent_session_id` arms are `host-twin`s: the conductor composes the payload in `_agent_run_json`, and record_timeout composes all three itself (L16466-16488). The docstring calls the duplication deliberate; this removal reverses that. | test_terminal_payload_rejects_a_role_outside_the_vocabulary |
| OR4-134 | remove | `host-twin` (workflow_conductor.py:5446-5462). `backend` is re-checked per launch (L6531-6545); `agent_session_id` by `_validate_response_agent_session_id` (L15695). | none found |
| OR4-135 | merge→`_validate_step_result_payload` L12698 | The `validation_stage` arm is duplicated verbatim at L12711-12718 (read). The `status` and `substep_agent_run_ids` arms are host-twins (conductor :10176). | none found (help-text test unaffected) |
| OR4-136 | remove | `operator-only` (the manual record-reply route). An empty reply is still refused later by `_validate_step_or_substep_launch_refs` (L12123-12126). | none found |
| OR4-137 | merge→OR4-105 | `finalize_child` refuses the same empty reply before any side effect, and `main` catches its ValueError → exit 1. | test_empty_stdin_reply_is_a_dispatch_error_not_a_silent_pass (stays green; the fragment is in both) |
| OR4-138 | keep:break | The terse projection the conductor parses. It must pass `workshop_lineage_home_missing` through (OR4-57). | — |
| OR4-139 | remove | `host-twin`: `Conductor._child_env` is deterministic and non-empty (workflow_conductor.py:5488-5489). A malformed env makes `json.loads` raise, and an empty env fails the bwrap launch; both are fail-closed. | test_the_stdin_flag_parses_only_a_non_empty_object_of_strings |
| OR4-140 | undecided | `operator-only` manual command (inventory grep G1) over deps.yaml and the catalog. Its record is audit-only, except that the gate requires a persisted dict to exist (OR4-32). It follows OR4-32's decision. Removal would rewrite about 50 tests that use it as setup, plus docs/CLI_REFERENCE.md §mark-dependency-readiness. | test_verification_error_overwrites_previous_passing_readiness (+~50 setup uses) |
| X-1 | remove | `main` L19172 `RuntimeError("unhandled command")`: `dead`. Every subparser has a branch, and the subparsers are `required=True`. | none |
| X-2 | remove | `reserve_phase_root` `--target` refusals (L18023-18031): `host-twin` (conductor :10227 passes `--target` iff step=generate). | none found (the accept-arm test stays) |
| X-3 | remove | `reserved_id` grammar (L18040-18049): `host-twin` (host-minted ids). A second copy sits at VPS:536. Keep one: VPS's copy reads the persisted record. | test_reserve_rejects_non_canonical_reserved_id |
| X-5 | keep:break | `reserve_phase_root` record `reservations/<node_safe>/<step>.json`. Readers: `resume_node_refs`, `_revocable_stage_meta_path`, audit_orchestration and the conductor. | — |
| X-6 | keep:break | `update_orchestration_status` forward record (meta status fields, row finalization, the `set_status` event). Read by run_workflow, audit_orchestration, the completion vouch and validate_workspace_root. | — |
| X-4 | keep:break | The `main` dispatch catch-alls (fail-closed exit 1). This is a fact, not a check to remove. Note: set-status has no status vocabulary, but every caller is host, so it is not a defect. | — |

## Removal groups (one PR each, by mechanism)

- **G1. Preflight CLI probes (operator environment).** Rows OR4-01, 02 (gate only), 03, 05, 06, 07 (claude arm), 09, 20, 21; OR4-04 too if its run says so.
  - About 130 lines in L14356-14569, plus the `CLAUDE_WORKSHOP_REQUIRED_OPTIONS` table and about 15 tests.
  - Evidence: the CLI's unknown-option exit was measured, and the classifier was run on that stderr. An end-to-end run of one launch is still worth doing in the PR.
  - Reverses the #453 and #534 preflight decisions.
- **G2. Preflight and init policing of operator inputs.** Rows OR4-22, 31 (session_policy arms), 24 (+ the resume twin in OR3), 25, 26, 28, 33, 35, plus the session-policy refusal in `_check_session_policy_launchable` (OR2/3).
  - About 110 lines and 6-8 tests.
- **G3. record_launch host-twin re-checks of the conductor's request and response.** Rows OR4-38, 39, 40, 41, 43, 46, 47, 49-55, 62, 63, 66-68, 74, 75, 134, 139.
  - About 200 lines and about 15 tests, several of them subtests.
- **G4. record_agent_run and write_step_result post-hoc host-twins.** Rows OR4-83, 87, 88, 90-94, 106, 110, 111, 133, 135.
  - About 130 lines. `agent_runs_invalid` loses its `fail_reason` values: `sandbox_*`, `launch_response_*`, `agent_session_id_*`.
  - About 8 tests, plus test_audit_orchestration fixtures that use those reason strings as data.
- **G5. Reply budget and reply text.** Rows OR4-101, 104, 107, 136, 137, plus `_evaluate_reply_budget` and `REPLY_BUDGET_CHARS` (OR3), the `reply_over_budget` entry in `_TERSE_ALWAYS_KEEP`, and docs/CLI_REFERENCE.md:218.
  - About 90 lines and about 5 tests.
- **G6. record-timeout manual-route guards.** Rows OR4-114, 115, 116, 117, 118, 119, plus RUNBOOK §substep-timeout-recovery wording.
  - About 90 lines and about 6 tests. These are the guards of a documented operator recovery tool, so name that in the PR.
- **G7. No-reader record fields and events.** The `preflight_written_not_launchable` event (OR4-34), `_resolved_makefile_host_authored` (OR4-64), `sandbox_command` (OR4-70), and the four readiness log events (OR4-33, if G2 does not take them).
  - About 25 lines, plus the conductor test key list and the launch-request fixtures.
- **G8. CLI-boundary leftovers.** Rows OR4-121, 122, 127, 129, X-1, X-2, X-3.
  - About 45 lines and about 3 tests.
- **Held, pending the undecided rows.**
  - OR4-58 + 102 + the claude half of 79 + 119's legacy arm: sequential claude launches.
  - OR4-32 + 140 + 33: persisted dependency readiness.

Estimated total if every remove/merge lands: about 800 lines of non-test code.
