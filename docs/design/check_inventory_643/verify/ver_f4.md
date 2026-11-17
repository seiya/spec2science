# Issue #643 step 2 — adversarial verification, batch f4 (checkout ec2ddda3)

Rows: every `remove` / `merge` row of `cls_vp1.md` (46) and `cls_vp23.md` (78) = 124 rows, all in `tools/validate_pipeline_semantics.py` (vps).

**Counts:** CONFIRMED 105 · PARTIAL 16 · REFUTED 3 · NEEDS-RUN 0.

Verdict use:
- `CONFIRMED`: the removal/merge holds; the evidence column says what was opened or run. A condition on another row is stated.
- `PARTIAL`: the row may still go, but one stated arm or the stated evidence is false, or the named survivor is not settled. The correction is stated.
- `REFUTED`: the claim as written cannot be acted on (a `merge` whose survivor is itself proposed for removal or merged back into this row).

No row was refuted on a leaf gain. The three REFUTED rows and most PARTIAL rows are survivor problems across the group files, not holes in vps.

Facts established for the whole batch (opened at ec2ddda3 unless marked RUN):
- F1. `--stage pre_judge` is the only in-tree caller with `require_orchestration=True` (vps main L12388-12395; conductor wc:15714). It runs in Validate's `post_judge` substep, after every leaf of the orchestration has run, and never when `until_phase < validate`. So the launch-record audit (`_validate_orchestration_hierarchy`) guards no launch; it only fails the node's Validate after the fact.
- F2. Every orchestration record the audit reads is written by the conductor or `orchestration_runtime`. The launch response is composed before the leaf runs (wc:5446-5462: `agent_session_id: child_arid`, no `launch_reply` key). A workshop leaf has no write route to `workspace/orchestrations/`.
- F3. `_conduct_phases` (wc:17014-17036) always iterates `phases_through(until_phase)` from `compile`, and `run_phase` opens with `check_phase_certified` (wc:15816). `_phase_certification` runs `_ir_certification` for every step (or:2043), which runs `validate_compile_stage` on the selected IR (or:1895, 1929). RUN: `test_check_phase_certified_refuses_an_ir_the_current_validator_rejects`, `test_a_stale_ir_is_not_adopted_on_a_cold_run`, `test_a_certified_ir_the_current_validator_rejects_is_refused_at_readiness` pass.
- F4. `run_phase` runs the substeps of one phase once, in order, and `_ensure_fresh_producer_id` (wc:15221, called at wc:15866) mints a fresh `source/<id>` / `runs/<id>` per `run_phase` entry. So a gate retry never re-stages into an old `src/`, and `pre_judge` always follows a `post_execute` of the same attempt, in the same conductor process. `check_phase_certified('validate')` reuses a certified run without re-running vps.
- F5. `structural_ok = ir_unevaluable or (gate rc == 0 and qc_status == "pass" and not snapshot_gap)` (wc:14306). `qc_status` is computed in memory by `_author_quality_check` (wc:13214-13322) from the runs' own directories; each row's `launch_match` / `block_match` / `thread_match` / `run_ok` / verdict / state agreement folds into it.
- F6. `trial_meta.json` (wc:14289) and `quality_check.json` (wc:13320) are written after the last leaf-built binary run of the attempt; no `trial_meta.json` is written when `run_program` failed (wc:14095-14103).
- F7 (RUN, census). The lint/syntax evidence certification is called only under `if meta_path.exists()` (vps L11759), and `source_meta.json` is written only after `generate.verify`. Over all 774 `source/src_*` dirs under `workspace/pipelines` that hold a `gate_meta.json`: `source_meta.json` absent in 74, born after the gate's `gate_meta.json` mtime in 700, born at or before it in 0 (`verify/f4/census_g1.sh`). The G1 block has not run in any recorded gate.

Probes are in `verify/f4/`: `mutplug.py` + `run.sh` (source-patched vps under pytest, nothing written to the tree), `mut_063.py`, `mut_072.py`, `probe_misc.py`, `census_g1.sh`.

## Table

| id | proposed | verdict | evidence |
|---|---|---|---|
| VP1-11 | remove | PARTIAL | Host-twin holds: sole consumer L7462; the argv is `build_runtime.quality_check_command(preset)` from `_QUALITY_CHECK_PRESET_COMMANDS` (br:628-635), preset = `execute.QUALITY_CHECK_PRESET` (wc:13772). The cited survivor BR-16 ("does not allow custom command") is itself `remove` in cls_small.md. The ground is the preset table lookup, which stays; restate it so. |
| VP1-20 | remove | CONFIRMED | Consumers are L7938, L8137, L8822-8928 only (grep `-w`, tools/ skills/ docs/ .github). Follows VP3-038/041; `AGENT_TERMINAL_STATUSES` (L8137) sits in VP3-008, which is `undecided`, so that constant goes only with VP3-008's member. F2. |
| VP1-22 | remove | CONFIRMED | Sole caller L8620 (in VP3-029, frame, `undecided`: helper goes when that member goes). The response's id is `child_arid` (wc:5447-5448). |
| VP1-23 | remove | CONFIRMED | Callers L8198, L8657 (VP3-012 members). Ids are host-minted arids (F2). |
| VP1-24 | remove | PARTIAL | Sole caller L8572 = VP3-027, whose merge is REFUTED below (ring with OR3-30). The helper goes only if the vps copy is the one dropped. |
| VP1-25 | remove | CONFIRMED | vps copy consumed at L946 and L8320 only; both in VP3-016. The or copy (or:10041) stays. |
| VP1-26 | remove | CONFIRMED | Consumers L935/939, L8319 (VP3-016). pre_judge is scoped to the run's own orchestration (wc:15714 passes `--orchestration-id`), and no current renderer writes a slim prompt (or:10589-10598 refuses every non-deterministic, non-workshop request). Unscoped `--stage full` over an old workspace is the operator's (no in-tree caller). |
| VP1-27 | remove | CONFIRMED | Sole call L8286; feeds only the marker-set choice of VP3-016. |
| VP1-28 | remove | CONFIRMED | Callers L8288, L8296 (VP3-014). A pure request is refused at render (or:10594-10598). |
| VP1-29 | remove | CONFIRMED | Consumers L8400, L8438. Goes only together with VP1-31/32/35 and VP3-019..021 (the #172 symmetry the classifier names). |
| VP1-30 | remove | CONFIRMED | `_WORKSHOP_PROFILE_HOME_ENV` L789/794 (VP1-35), `_WORKSHOP_ENDPOINT_RESPONSE_FIELDS` L686 (VP1-31). |
| VP1-31 | remove | CONFIRMED | Request and response are conductor-written at record-launch (F2); F1: the audit runs after the launch it describes. Leaf gain: nothing (a reading leaf served an endpoint would hold operations that move no verdict, and it cannot make the host serve one). The classifier's older-checkout `needs-run`: a record of a finished launch cannot be un-run by a pre_judge refusal; pre-J4-b orchestrations are refused at start (RUNBOOK:560), by reading. |
| VP1-32 | remove | CONFIRMED | HTTP host loop writes the stamps from `HTTP_FILE_TOOLS` + `WORKSHOP_OPS_BY_PAIR`, the same tables this compares with. What confines an HTTP leaf is the loop serving those tools, not the record. |
| VP1-33 | merge→WC1-38 | PARTIAL | Shape arms are in `_admissible_leaf_profile` (wc:2236) and again in `render_bwrap_command` (or:8540-8553), both before the launch. But WC1-38 is `undecided` in cls_wc12.md and names THIS vps twin as its possible cover; OR2-C04..C07 also merge into WC1-38 and OR2-C16..C20/C27 are `undecided` on the same question. Holds once WC1-38 is fixed as keep. F1 answers cls_wc12's open question (a): the vps twin covers no launch and no run that stops before Validate, so WC1-38 is the copy to keep. The `write_roots != []` arm is not in WC1-38; a non-empty value mounts nothing (OR2-C15). |
| VP1-34 | merge→WC1-38 | PARTIAL | Bind arms: wc:2238-2251. Same survivor condition as VP1-33. Lineage-vs-request arm is host-twin (one record-launch writes both). |
| VP1-35 | merge→WC1-38 | PARTIAL | Home / family / credential / workdir: wc:2252-2281. Same survivor condition as VP1-33. |
| VP1-37 | merge→WC1-38 | PARTIAL | Same `leaf_netns.network_block_violations` call at wc:2261 and or:8598. Same survivor condition. |
| VP1-38 | remove | PARTIAL | Env arm = wc:2265 (same survivor condition as VP1-33). `rendered_command` arms audit the renderer's own output (or:8604-8612 composes `--unshare-net`, `--unshare-pid`, the relay); nothing else reads `rendered_command`. Leaf gain: nothing. |
| VP1-39 | remove | CONFIRMED | Callers L8291, L8308 (VP3-015). |
| VP1-40 | remove | CONFIRMED | Sole caller L8321 (VP3-016). |
| VP1-41 | remove | CONFIRMED | Sole caller L8327 (VP3-016). |
| VP1-44 | remove | CONFIRMED | Dead. RUN: `grep -rn _extract_first_output_block` over the repo (py/md/txt, tests included) returns the definition L1093 only. |
| VP1-48 | remove | CONFIRMED | `_node_executions` reads only the canonical child (L1196-1208); the conductor mints the run dir. A stray child changes no verdict of its own node. See defect D4 for the one reader that globs any child. |
| VP1-51 | remove | CONFIRMED | `lineage.json` is host-written at every pipeline phase start (wc:15874 `_write_lineage`). The check covers `node_key` / `pipeline_id` only, not `ir_ref`. Condition: VP3-109 cites this row as its cover and is also `remove` (see VP3-109). |
| VP1-52 | remove | PARTIAL | Host-twin base holds (`_write_verify_source_meta` wc:9450-9473 is the writer; leaf-carried values pass the verdict schema first). The stated cover is void: the write-point detector is WC3-86 (`remove`) and `write_step_result`'s copy is OR3-87 (`undecided`, "three readers"). Three twins each cite the others; decide them together. `missing` arm: by reading, Build and Validate start only on a certified Generate (F3). |
| VP1-53 | remove | PARTIAL | Same as VP1-52 for `_write_ir_meta` (wc:7350). Also: `check_ir` writes a pending `ir_meta.json` into its scratch dir only so this check passes (wc:11378-11380); that seed goes with it. |
| VP1-54 | remove | CONFIRMED | Sole caller L4865 (VP2-16). F7. |
| VP1-56 | remove | CONFIRMED | F6. `raw_artifact_refs` are composed by `_promote_run_evidence` (wc:12985-13013) and `_author_snapshot_schema`. The non-empty arm is decided first in memory by `snapshot_gap` (wc:14121-14133, F5) when snapshots are required; when the IR requires none the list is empty and this arm fails a correct run. |
| VP1-57 | remove | CONFIRMED | `refs.source_id` / `refs.source_binary_id` (wc:14232-14233), F6. Condition at removal: `_execution_in_scope_src_dir` returns None on a missing id and the callers then skip the leaf-source scans at L11960-11978 without a word; make that a raise, not a silent skip. |
| VP1-58 | remove | CONFIRMED | Refs written at wc:14239-14246 with host command ids. The `else Path(log_ref)` arm (L1653) is unreachable after the `continue` at L1650. |
| VP1-59 | remove | CONFIRMED | Literal `tool_name`s at wc:14240-14245; log rows by `build_runtime` in-process. |
| VP1-60b | remove | CONFIRMED | Written by `_author_quality_check` (wc:13320) before the gate; F5. |
| VP1-64 | remove | CONFIRMED | The host copies `variables` / `time_variable` / `time_shape_expr` verbatim from the IR entry (wc:13047-13052). The non-empty arms are refused on the IR itself in the same stage call (`_validate_io_contract_schema` L11953 → L5820, L5919). RUN (`probe_misc.py`): the `missing` arm is reachable only with the legacy flat key and no `state_snapshots` item, where the host writes no schema (wc:13022-13024); it goes with VP2-28. |
| VP1-66 | remove | CONFIRMED | Both sides come from the same IR entry (wc:13047-13052 vs `_state_snapshot_requirement_details`). |
| VP1-67 | merge→WC4-63 | CONFIRMED | Survivor WC4-63 is `keep:leaf`, WC5-21 `keep:break`. F5, F4: no pre_judge without the same attempt's post_execute. The full CLI mode has no in-tree caller. |
| VP1-71 | merge→WC4-63 | CONFIRMED | `launch_match` reads `run_tmp/perf.json` `mpi_ranks` with `type(...) is int and == shape.ranks` (wc:13270-13276); the gate reads the promoted copy of the same file. F5. |
| VP1-72 | merge→WC4-63 | CONFIRMED | Same row, `observed_process_grid == expected_grid` (wc:13273-13276). Condition (classifier's own): re-home the `_validate_harness_reported_grid` call (L2211). |
| VP1-73 | remove | CONFIRMED | `comparison.*.ranks` are the conductor's literals (wc:13303-13308). |
| VP1-74 | merge→WC4-63 | CONFIRMED | `shape_runs` is built from `target.certified_shape`, the sweep and the checked shape (wc:14143-14186); every row's status folds into `qc_status` (wc:13288-13296). Same three predicates (wc:14196-14199 vs L2286-2288). |
| VP1-74b | merge→WC4-63 | CONFIRMED | wc:13266-13284 compute the same comparisons from the same `perf.json`. Condition: re-home `_validate_harness_reported_block` (L2327). |
| VP1-75 | remove | CONFIRMED | Reads `binary_meta.json#checked_build` (Build-written) and the target; the leaf chooses no flag, and the control file is host-rendered (`_write_leaf_bundle_artifacts` wc:6776; codegen_bundle.py:596 refuses a reserved build filename). The record check does not read the binary's bytes, so it bounds no leaf route either way. |
| VP1-76 | remove | CONFIRMED | Binds two host-written log rows; the checked row's outcome is `run_ok` in memory (wc:14170-14186, F5). |
| VP1-79 | remove | CONFIRMED | `source_source_id` = `refs.source_id`. Same removal condition as VP1-57 (no silent None). |
| VP1-102 | remove | CONFIRMED | Reads only the operator's tests.md (the IR's `source_refs` are pinned to the node's own spec documents). Sole in-tree caller L11116. After removal a stale `reported:` line is ignored without a word (spec_input_gates reads no tests.md line); that is the operator's call, and docs/TESTS.md says "refused". |
| VP1-109 | remove | CONFIRMED | Sole caller L1260 (L1232 is a docstring). |
| VP1-111 | remove | CONFIRMED | Callers L2448, L2462 only. |
| VP2-15 | remove | CONFIRMED | Reader chain `_infer_run_linter_preset_from_command` L4844 → L5075 (VP2-22) only. F7. |
| VP2-16 | remove | CONFIRMED | F7: never ran in 774 recorded gates. By reading also host-twin: `command_log_ref` is written at wc:12152, and codegen_bundle.py:596-604 keeps `command_log.jsonl` out of a bundle. |
| VP2-17 | remove | CONFIRMED | F7. `ok` is decided in memory: `_gate_inproc` runs static only when `lint_ok and syntax_ok` (wc:11858-11873). |
| VP2-19 | remove | CONFIRMED | F7. |
| VP2-20 | remove | CONFIRMED | F7; the conductor raises on a language with no linter (wc:12140-12146). |
| VP2-21 | remove | CONFIRMED | F7; entries written per member at wc:12183-12200. |
| VP2-22 | remove | CONFIRMED | F7. |
| VP2-23 | remove | CONFIRMED | F7. |
| VP2-24 | remove | CONFIRMED | F7; a language with no syntax stage raises at wc:12290-12296. |
| VP2-25 | remove | CONFIRMED | F7. The mandatory-skipped member (`needs-run` in the row): every arm that would record the mandatory stage skipped raises (wc:12383, 12395, 12491, 12547), except the source-name refusal, which records `skipped` with `ok=False` and returns status `fail` (wc:12449-12476). |
| VP2-28 | remove | PARTIAL | RUN (`probe_misc.py`): the masked claim is false for `_state_snapshot_required`. With `required_evidence` a list that has no `state_snapshots` item (an empty list too), `state_snapshot_required: true` is read (L5565-5567) and the answer is True. The conclusion still holds: the reachable arm only turns the requirement ON, the leaf controls the key, and the conductor promotes no snapshot for such an IR, so the key can only fail the leaf's own run. Leaf gain from removal: nothing. `_required_raw_evidence`'s arm is masked as stated. |
| VP2-38 | remove | CONFIRMED | RUN: `grep -rl numerical_kernel_contract tools skills docs spec` → vps only. No reader, no document naming it. |
| VP2-39 | merge (emit once) | CONFIRMED | Both functions resolve the same path and are called back to back (L11952-11953). Keep the `return` in the dropped copy. |
| VP2-51 | remove (refusal only) | CONFIRMED | The member list is tied to the host graph (VP3-096, kept); the member's §5.1 is an operator file read by the same `problem_procedure_names` the spec-input gate uses (spec_input_gates.py:225-260). The fail-closed placement fallback stays. |
| VP2-53 | remove | CONFIRMED | The conductor resolves the same harness at every pipeline phase start (`_write_lineage` → `with_target_harness`, wc:15874) and at pre_judge (wc:15410). Condition: the `return dep_data` at L6633 would then drop the harness from the DAG check without a word; let the error propagate instead. |
| VP2-55 | remove | CONFIRMED | By the code's own sets: the per-token `available_nodes` (L12074) is a subset of `scope_nodes` and has no `_xp_satisfied` excuse (L12077), so a leaf that writes `resolved_at` can only add `dependency DAG incomplete`. RUN: `grep resolved_at tools` (non-test) → vps only. |
| VP2-62 | remove | CONFIRMED | Callers L7051, L7278, L7353 (all G2 members). |
| VP2-63 | remove | CONFIRMED | `environment.launch` = `launch.record()` (wc:14265), composed by `host_execution`; F6. |
| VP2-64 | remove | CONFIRMED | `trial_meta` writes `command_log_ref` only (wc:14239-14246). |
| VP2-65 | remove | CONFIRMED | F6: no `trial_meta.json` after a failed `run_program`. Sweep and checked runs are `run_ok` rows in memory. This does NOT extend to the quality check's `ok` (VP2-72, see defect D2). |
| VP2-66 | remove | CONFIRMED | Host-written ref (wc:14241). |
| VP2-67 | remove | CONFIRMED | argv composed at wc:14235 with the IR path. |
| VP2-68 | remove | CONFIRMED | Executable and prefix host-composed; `_bound_executable` stays for VP1-76 only if VP1-76 stays (it is `remove` too, so the helper goes with both). |
| VP2-71 | remove | CONFIRMED | `source_source_id` is `refs.source_id`; Validate starts only on a certified Generate (F3). |
| VP2-73 | remove | PARTIAL | Same correction as VP1-11: the named survivor BR-16 is `remove` in cls_small.md. The ground is that the argv is a preset-table lookup and no caller passes `command`. |
| VP2-74 | remove | CONFIRMED | `_execute_inproc` takes `execute.QUALITY_CHECK_PRESET` from the same `build_execute` module before it runs anything (wc:13772); a build system without it fails there. |
| VP2-75 | remove | CONFIRMED | Control file is host-rendered for every bundle (wc:6776-6778), and no bundle can ship one (codegen_bundle.py:596). A no-op test target would leave no reference captures, which `qc_status` refuses (wc:13254, 13262). |
| VP2-78 | remove (resolver refusals) | CONFIRMED | Sole caller L12365; `--pipeline-root` is `refs.pipeline_ref` (wc:14298, 15716). |
| VP3-003 | remove | CONFIRMED | Serves only VP3-032/034 exemptions; goes with them. Conductor edit at wc:15714-15719. |
| VP3-005 | remove | CONFIRMED | Host-written; launches are gated on the same document (`_preflight_launch_refusals`, or:9226-9234). F1. |
| VP3-006 | remove | CONFIRMED | F2. |
| VP3-007 | merge→OR completion vouch | PARTIAL | The vouch does re-check the edge facts (or:12309-12323) and the raw graph (or:12254-12258). But its rows OR3-73/74 are `undecided` in cls_or3.md and name VP3-007/032 as their possible cover. F1 answers cls_or3's "timing coverage" question: vps sees the graph before the Validate rows exist and never on `until_phase < validate`, so the vouch is the copy with full coverage. Holds once OR3-73/74 are fixed as keep. |
| VP3-009 | remove | CONFIRMED | F2; rows composed by `_agent_run_json` / `record_agent_run`. |
| VP3-010 | remove | CONFIRMED | Host-twin. Note: on the record-timeout path a codex row takes `context_id` from the session id (or:16451-16458), so this check can only refuse a correct record, never bound a leaf. |
| VP3-011 | remove | CONFIRMED | Literal `True` at every writer opened (or:16474; wc as cited). |
| VP3-012 | merge→OR `_validate_response_agent_session_id` | REFUTED | The survivor is proposed for removal: cls_or3 group R-request drops "the raise in `_validate_response_agent_session_id`" (OR3-22 merge→OR4-134), and OR4-134 is `remove` citing that same function (L15695). All three copies point at each other. As a plain host-twin `remove` it holds (the id is `child_arid`, wc:5447); as a merge it has no survivor. |
| VP3-014 | remove | CONFIRMED | A pure request cannot be recorded (or:10594-10598); pre_judge is scoped (wc:15714). |
| VP3-015 | remove | CONFIRMED | One record-launch writes both request and prompt (prompt force-rendered from the request, OR3-20 kept). |
| VP3-016 | remove | CONFIRMED | Audits the renderer's own output; F2. |
| VP3-017 | remove | PARTIAL | The stated evidence is false: `LEAF_PROMPT_CONTRACT_VERSION` "is in no derivation key since issue #574" (leaf_contract.py:36-38); `derivation.transformation_versions` (derivation.py:98-110) hashes only the three reading contracts. The conclusion still stands on another ground: #574 made a producer's text advisory and keyed the reading contracts, so an artifact made under an older producer prompt is certified only under the current keys (F3). What removal changes is documented behaviour: this audit is the only enforcement of the `--resume` barrier across a leaf-contract bump (RUNBOOK:560, derivation.py:71-73). Operator decision; leaf gain nothing. |
| VP3-019 | remove | CONFIRMED | F2; the exemption is read from host records only. |
| VP3-020 | remove | CONFIRMED | F2. |
| VP3-021 | remove | CONFIRMED | F2. |
| VP3-024 | remove | CONFIRMED | Reached only for a pure record, which cannot be written (see VP3-014); `render_bwrap_command` refuses any non-workshop profile (or:8540-8545). |
| VP3-025 | merge→OR `_validate_terminal_run_payload` | REFUTED | Ring: cls_or3 has OR3-65 `merge→VP3-025` with "VP3-025 must survive", and names it in its header ("A VPS classifier must not merge it back into OR3-65"). Both sides are host-composed (`[]` at wc:9038-9040). Keep exactly one, or drop both as host-twin; as written each PR's cover is the other PR's deletion. F1 argues for keeping the record-time copy (or:12033-12041): it runs on every row at write time. |
| VP3-026 | remove | CONFIRMED | F2 (`record_agent_run` writes result and refs). |
| VP3-027 | merge→OR `_validate_agent_summary_text` | REFUTED | Ring: OR3-30 is `merge→VP3-027` with "VP3-027 must survive the VPS classification", and cls_or3 group R-records deletes `_validate_agent_summary_text`. Same resolution as VP3-025; wc:14591 relies on the record-time copy to force a `result_summary`. |
| VP3-030 | remove | CONFIRMED | Dead on current records: no in-tree writer puts a `launch_reply` key into a response (RUN: `grep '"launch_reply"' tools/*.py` → the vps read and or:10718's extractor only; the conductor's response dict is wc:5446-5462). |
| VP3-031 | remove | CONFIRMED | `invalid_arids` is read only in the edge loop (L8721). |
| VP3-032 | merge→OR completion vouch | PARTIAL | Same as VP3-007 (survivor OR3-74 `undecided`). |
| VP3-033 | remove | CONFIRMED | F2. |
| VP3-034 | remove | CONFIRMED | Serves only the VP3-041 coverage map. |
| VP3-035 | remove | CONFIRMED | F2. |
| VP3-036 | remove | CONFIRMED | `steps/` is created at orchestration init (or:15011). |
| VP3-037 | remove | CONFIRMED | Read only for `step_coverage` (L8817-8819). |
| VP3-038 | remove | CONFIRMED | F2 (`write_step_result`, or:17125). |
| VP3-039 | merge→OR `write_step_result` + vouch | CONFIRMED | The vouch re-reads the object, executor and list (or:12360-12377); OR3-76 is `keep:break`. The `required_outputs` / `failed_substeps` list members have no survivor (OR3-89 is `remove`); they are host-composed (wc:16013-16024), so they go as host-twin, not as a merge. |
| VP3-040 | remove | CONFIRMED | The vouch raises without an orchestration row (or:12205-12212). |
| VP3-041 | merge→OR completion vouch | CONFIRMED | Survivor OR3-72 is `keep:leaf`: every reserved node certified through `until_phase` (or:12231-12238). Stage difference to record in the PR: the vouch gates the orchestration's `pass`, not the node's Validate; the fact is host-written either way. |
| VP3-042 | remove | CONFIRMED | Callers of `validate_compile_stage`: the CLI with `refs.ir_ref` (wc:12904) and or:1895 with the resolver's selection. `check_ir` uses `validate_compile_ir_dir`, which never ran this. |
| VP3-043 | remove | CONFIRMED | Callers L11704, L11801; value is `refs.pipeline_ref`. |
| VP3-044 | remove | CONFIRMED | RUN: `grep -- --workspace-root` over tools/ skills/ docs/ .github → the argparse default (L12194) and no caller passing a value. |
| VP3-047 | merge (emit once) | CONFIRMED | RUN (`probe_misc.py`): an empty IR dir yields the `spec.ir.yaml: missing` line twice. |
| VP3-063 | remove | CONFIRMED | RUN (`mut_063.py`): with the containment arm disabled, the decoy inside the IR directory is refused by the identity check ("is not the controlled_spec the registry records"). Other paths by reading: a registry entry without a path is refused at L10116-10121; a registry that cannot answer the kind returns at L10046 and `_validate_catalog_spec_kind` refuses. Test note (differs from the row): the `containment` subTest asserts the removed message and fails under the mutation; it must be re-pointed at the identity message, not kept as is. |
| VP3-072 | remove | CONFIRMED | RUN (`mut_072.py`): with the duplicate refusal and its return removed, all 18 module-parameter tests pass, the exact-duplicate and the case-only-duplicate rows included (both assert the earlier render-check message). Both callers render §5.1 first (L10209-10214, L10828-10835). |
| VP3-078 | merge→OR `_certified_ir_violations` | CONFIRMED | F3 (RUN for the skip branch, reading for the loop). `validate_compile_stage` calls the same reader (L9094). The classifier's `needs-run` on resume / with-deps: the guard reads the node's OWN IR, and `run_phase(compile)` precedes Generate in every conductor invocation; a ready dependency the closure driver skips is never this guard's subject. |
| VP3-082 | merge→OR `_certified_ir_violations` | CONFIRMED | F3. Compile's pin runs the same two comparisons in every signature language (L10166-10187 → L10247-10250), so the target-language form the guard uses is covered. |
| VP3-101 | remove | CONFIRMED | Sole vps caller L11574 (or:2495 is a comment). |
| VP3-102 | remove | CONFIRMED | Goes with G4; the bundle is host-written (wc:6772-6774). |
| VP3-103 | merge→`_validate_pipeline_targets_resolve` | CONFIRMED | Survivor VP1-85 is `keep:break` and runs in the same stage (L11710). Moot once G4 deletes the function. |
| VP3-104 | remove | CONFIRMED | One acceptance path: `spec.violations` → `accepted_doc` → `spec.write_artifacts` (wc:8994-9069); the same `pure_bundle_contract_violation`. Between it and the gate only lint/syntax run (syntax in a throwaway stage dir). No certified-reuse path re-runs post_generate. |
| VP3-105 | remove | CONFIRMED | Goes with G4 (the function is deleted). Its own comment states no input reaches it; not attacked separately. |
| VP3-106 | remove | CONFIRMED | Two host writes of one accepted document in one call (wc:6750-6774). The classifier's `needs-run` (same-phase retry reusing the dir): answered by F4, a fresh `source/<id>` per `run_phase` entry, artifacts written once per entry. |
| VP3-107 | remove | CONFIRMED | Same; with a fresh dir per entry no earlier bundle's file can be left in `src/`. |
| VP3-109 | remove | PARTIAL | Host-twin holds (F2-style: `_write_lineage`). The stated cover is void: `_validate_pipeline_lineage_presence` is VP1-51, also `remove`. Condition: the `return violations` at L11714 must not become a silent pass of the whole stage. |
| VP3-110 | remove | CONFIRMED | `--source-id` is `refs.source_id` (wc:12826). Same condition as VP3-109 for the two early returns (L11719, L11724; post_build L11809, L11814). |
| VP3-111 | remove | PARTIAL | Host-twin arm holds (the IR is the one `check_phase_certified` just selected, F3). The `masked` arm is false. RUN (`probe_misc.py`): `_validate_io_contract_file` on a missing path raises `FileNotFoundError`; it does not report `missing`. Keep the `exists()` guard, or accept a traceback that `generate.gate` charges to the Generate leaf as `post_generate_violation`. |
| VP3-112 | remove | CONFIRMED | Host-written file; and F7: the block it guards has not run in a recorded gate. |

## Defects found

- **D1. Survivor rings across the group files.** Acting on each file as written deletes every copy of the same fact while each PR says another copy covers it.
  - VP3-027 ↔ OR3-30 (`_validate_agent_summary_text`), VP3-025 ↔ OR3-65 (pure pass row `output_refs == []`): each side is `merge→` the other and says the other "must survive".
  - VP3-012 → `_validate_response_agent_session_id`, whose raise OR3-22 drops (merge→OR4-134), which is `remove` citing that function.
  - VP1-33/34/35/37/38 and OR2-C04..C07 → WC1-38, which is `undecided` and names the vps twin as cover; OR2-C16..C20/C27 are `undecided` on the same question. F1 says keep WC1-38.
  - VP3-007/032 → the completion vouch's edge clauses, OR3-73/74 `undecided` naming VP3-007/032. F1 says keep the vouch.
  - Stage-meta contract: VP1-52/53 (`remove`), WC3-86 (`remove`), OR3-87 (`undecided`): three readers of `meta_contracts`, each citing the others.
  - VP1-11 / VP2-73 → BR-16, which is `remove`. VP3-109 → VP1-51, which is `remove`.
- **D2. The quality check's own `ok` is read by nothing in the conductor (reading, not run).** `_author_quality_check` never reads `res_qc["ok"]` (wc:13214-13322). `res_qc.ok == False` stops the sweep and the checked run (wc:13786-13800), which then fail as `not_run` rows, but a target with no sweep and no checked flags has neither. All four declared targets have one or the other (`cpp_gpu.yaml` a `block_size` sweep with `nvcc`'s `CHECKED_FLAGS = ()`; the three gfortran targets `-fcheck=bounds`), so the gap is latent: it opens on a target profile with neither. There a serial reference re-run that writes complete captures and then exits non-zero gives `qc_status == "pass"`, and the only refusal is vps `run_quality_checks … ok must be true` (L7403, VP2-72's `ok` member, `undecided`). Leaf gain: a leaf-built test that fails at exit is certified. So G2 must not take that member; the durable place for it is `_author_quality_check` / `structural_ok`. The run that decides: a fixture target without sweep or checked flags, `make test` exiting 1 after writing the reference captures, through `_execute_inproc`.
- **D3. G1 has never run (F7).** The lint/syntax evidence certification sits under `if meta_path.exists()` and Generate.gate runs before `source_meta.json` exists; 0 of 774 recorded gates met the precondition. The conductor comments that say the post_generate certifier "hard-fails on lint/syntax evidence whose ok flag is not true" (wc:11843-11846, 12131-12133, 12211-12215) describe a check that does not execute. Not a hole (the gate decides `lint_ok` / `syntax_ok` in memory), but it reads as coverage. After G1, `write_lint_evidence` / `write_syntax_evidence` have no reader.
- **D4. `_closure_node_validated_in_own_pipeline` globs `runs/*/*/aggregate_verdict.json` (L6744), any child of a run, not the canonical `<node_key_safe>`.** VP1-48 was the only check naming a non-canonical child, and only for the node's own run. No certification follows from it today: a dependent's launches are gated on the derivation-keyed readiness of the dependency, which reads no such file. Low; narrow the glob when VP1-48 goes. Related to the classifier's VP2-59 note.
- **D5. `_validate_io_contract_file` raises `FileNotFoundError` on a missing IR** (RUN). Every caller depends on its own `exists()` guard (L5996, L9091, L11731); at `generate.gate` a traceback is rc 1, which the conductor records as `post_generate_violation` and routes to the Generate leaf.
- **D6. `_state_snapshot_required` reads the legacy flat key even when `required_evidence` is a list** (RUN, VP2-28). Its comment-level premise ("the list wins") holds only when the list has a `state_snapshots` item.
- **D7. Silent skips that the removals would expose.** Each of these returns quietly today because a removed row reported the condition: `_execution_in_scope_src_dir` → None skips the three leaf-source scans (VP1-57/79); the early `return violations` of post_generate / post_build (VP3-109/110); `_with_pipeline_harness` returning the block without the harness (VP2-53). All are host-written inputs, so the fix is a raise at the reader, not a kept check.
- **D8. Document drift met on the way.** RUNBOOK:560 says the compile / generate / validate keys hash `leaf-85`; since #574 they do not (leaf_contract.py:36-38). `docs/workflow/WORKFLOW_CORE.md:300` (CI runs the validator) is the drift the classifier already named.

## Not done

- No billed or end-to-end run. D2 is from reading. The "older checkout's records on `--resume`" question on VP1-31..38 was answered from RUNBOOK:560-562 and the code, not by resuming an old orchestration.
- The remote-site `cwd` of a command-log row (the `needs-run` the classifier attached to VP2-63..75) was not opened; those rows are confirmed on the local composition and on F6, which do not depend on it.
- `git status --porcelain` at the end: only the pre-existing `?? transcript_data_20261110/`.
