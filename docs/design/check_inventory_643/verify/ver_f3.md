# Issue #643 step 2: adversarial verification, batch f3 (checkout ec2ddda3)

Rows: every `remove` / `merge` row of `cls_or3.md` (52) and `cls_or4.md` (69 `OR4-*` + `X-1`, `X-2`, `X-3`), 124 in all. Source: `tools/orchestration_runtime.py` L9593-19182.

## Counts

| verdict | count | rows |
|---|---|---|
| CONFIRMED | 104 | all rows not listed below |
| PARTIAL | 20 | OR3-22, 30, 33, 40, 42, 43, 44, 48, 65, 97, 103; OR4-24, 41, 63, 66, 67, 68, 129, 134; X-3 |
| REFUTED | 0 | |
| NEEDS-RUN | 0 | the classifier's own `needs-run` marks: OR3-17, OR3-33, OR3-67, OR4-09, OR4-24 and OR4-62 are settled below by a run or a census; OR3-43, OR3-99 and OR4-47 by reading (each cell says which) |

No row has a leaf gain: no value a leaf authors reaches any of these checks as a deciding input. What fails is the stated evidence on 20 rows (a survivor that another file removes, a mask that does not hold on one path, an order claim that is false). Each PARTIAL row names the arm that holds.

## What was run

- `verify/f3/order2.py`, `order3.py` (logs beside them): `record_launch`'s validation sequence (prepare, request validator, prompt validator; order read at L15687-15710) over the 8 recorded requests of `orch_20261117T063943Z_f3f49f4b`, with 45 mutated requests. All 8 recorded requests re-validate.
- `verify/f3/probe_oid.py` + `mut.py`: `init_orchestration` and `_create_workflow_backend_home` with the id walls removed by an in-memory text mutation of the module, in a scratch root.
- Censuses over `workspace/orchestrations/` (1027 orchestrations, 2026-07-23 to 2026-11-17):
  - 8704 `launches/*.request.json`: 0 carry `agent_session_id`; 0 have an empty `agent_model`.
  - 8704 `reply.txt`: largest 497 bytes; 0 over 2000.
  - `hooks/workflow_hooks.jsonl`: `post_phase_complete` allow 3090, deny 0; `reply_over_budget` 0.
  - `agent_runs.jsonl`: 877 orchestration `pass` rows, 0 with `output_refs`.
  - 1025 `preflight.json`: 1006 with `providers`, 19 without; all carry `session_policy` and `session_policy_launchable: true`.
  - `orchestration_meta.json` `reason_detail` / `resumed_from_reason_detail` starting `runtime `: 3 runs, none from a row of this batch (a retired write audit, a still-certified `revoke-artifact`, a `set-status` traceback). A refusal of these rows reaches the meta that way (`Conductor.runtime` raises on a non-zero exit, workflow_conductor.py:4105-4121; `run_workflow` records `conductor_error` with the text), so the census can see its subject. Its limits: it reads only the last terminal reason of each run and the one before a resume, and it is blind to a refusal the conductor folds into another route (it has catch sites that name `RuntimeError` at workflow_conductor.py:15900, :15956, :16416, :16448 and :16485, plus broad `except Exception` sites; which of them wrap a runtime call was not traced).
- `claude --no-such-opt-xyz -p </dev/null` on CLI 2.1.326: `error: unknown option`, rc=1. `workflow_conductor._leaf_infra_error` on that stderr: `None`.
- Small table checks: `PROVIDER_BACKEND_TOKENS`, `SUPPORTED_BACKENDS`, `WORKSHOP_FLAG_SET_CHECKS`; `set(WORKSHOP_LAUNCH_PAIRS) == set(PURE_CONTEXT_REQUIRED_KEYS)`; the three repair template keys; `_slug_of` against `_SLUG_DATE_SEQ3_PATTERN`; 17 subparsers against 17 dispatch branches; `_extract_launch_prompt_text` / `_extract_launch_reply_text` over 8 blank spellings.

Common facts used below:
- **A1. One route.** Every entry function of this range is reached only from `main` (grep of `tools/*.py` for each `def`); the conductor shells out (`Conductor.runtime`). The exceptions are the `WORKSHOP_HOME_PREPARERS`, which the conductor also calls in-process (workflow_conductor.py:8038, from :8108, :8129, :8364).
- **A2. The request is host-composed.** `build_launch_request` (workflow_conductor.py:1579-1960) sets every field. The repair block of an LLM request is four host constants plus the findings excerpt (:8906-8913, :9803-9810). No prompt field is composed, so `prepare_launch_request_payload` always renders (L10666).
- **A3. Message or traceback, the conductor sees the same thing.** `Conductor.runtime` turns any non-zero exit into `RuntimeError`, which ends the run `conductor_error`. No repair turn is charged.

## Table

| id | proposed | verdict | evidence |
|---|---|---|---|
| OR3-01 | remove | CONFIRMED | Read: refs come from `NodeRefs` (A2). Ran: a placeholder `dependency_ref` is refused by the loop at L11748, so L11769 never fires. |
| OR3-02 | remove | CONFIRMED | Read: a rewrap of `OSError` over repo-owned files; both forms end the same way (A3). Callers L10344, L10521 only. |
| OR3-17 | remove | CONFIRMED | Ran: the three repair keys are all in `_load_launch_prompt_templates()`. Without the arm a `KeyError` stops the same render. |
| OR3-21 | remove | CONFIRMED | Read: needs a caller-supplied prompt and a respelled role; the conductor supplies neither (A2). |
| OR3-22 | merge→OR4-134 | PARTIAL | The survivor OR4-134 is `remove` in cls_or4.md, and its own evidence names this function as the re-check: the merge is circular. The claim "L16668 re-reads what record_launch validated" is false on codex: `_write_json_transaction` (L5115-5127) rewrites the response's `agent_session_id` with the CLI's thread id (1124 of 2115 recorded codex responses differ from the arid). Holds: host-twin. The conductor writes the id unconditionally (workflow_conductor.py:5446-5448), and the codex value is host-written from the CLI stream. |
| OR3-23 | remove | CONFIRMED | Ran: a workshop request with a hand prompt is accepted because the prompt is re-rendered; the refusal fires only for a hand prompt on a deterministic request. |
| OR3-24 | remove | CONFIRMED | Ran: `leaf_mode: pure` is refused in prepare with or without a prompt (cases a, b, b2). Arm 1 needs a hand prompt. |
| OR3-25 | remove | CONFIRMED | Read: the prompt is rendered from the same request one call earlier (L15687). |
| OR3-26 | remove | CONFIRMED | Ran: cases c, d, d2. Its masker OR3-61 is also `remove`; after both go, a neither-shape request with a hand prompt is accepted. Only a hand-run CLI builds one; without a prompt OR3-19 (keep) still refuses. |
| OR3-27 | remove | CONFIRMED | Ran: fires only for a hand prompt on a deterministic request. |
| OR3-28 | remove | CONFIRMED | Read: same input as OR3-27. |
| OR3-30 | merge→VP3-027 | PARTIAL | cls_vp23.md classes VP3-027 `merge→OR _validate_agent_summary_text`: each file names the other as survivor, so both copies would go. Holds: host-twin. The text is composed from the host payload by `_extract_agent_summary_text` one line earlier; its only live arm is "a row with no output_refs carries a result_summary", which every conductor site passes (workflow_conductor.py:8974-16392). |
| OR3-31 | remove | CONFIRMED | Census: 8704 replies, largest 497 bytes; 0 `reply_over_budget` hook entries. Grep (tools, docs, skills, .claude, .github): no reader of the field or the entry; docs/CLI_REFERENCE.md:218 documents it and changes with it. |
| OR3-32 | remove | CONFIRMED | Grep: `SPEC2SCIENCE_ENFORCE_REPLY_BUDGET` is read at L11031 and L17038 and set nowhere outside tests. |
| OR3-33 | remove | PARTIAL | The mask claim fails: its maskers OR4-24 and OR3-97 are also `remove`. Ran (`probe_oid.py`): with the walls removed, ids `orch.x1` and `a b` are accepted by init and by home creation; `sub/dir` passes init and fails at the first lineage-home preparation. Holds: operator-only (`--orchestration-id`; minted ids are tokens). Consequence to state: `prune_workflow_homes.py:417` keeps the same predicate and answers `refused:invalid_orchestration_id` for such a home. |
| OR3-34 | remove | CONFIRMED | Read: operator-only; a missing meta raises from `_read_json` at the same point; nothing is launched. |
| OR3-35 | remove | CONFIRMED | Grep of every status writer: conductor `fail` / `fail_closed` / `pass`; run_workflow `cancel` / `fail` and the operator's `--status`; judge_replay `running`. An unplaceable status needs a hand-run CLI. |
| OR3-39 | remove | CONFIRMED | Read: refs are `NodeRefs` (A2); judge_replay re-derives them with its own shape check (judge_replay.py:309-314). Ran: fires only on a malformed ref. Note: the two other copies of the root-id grammar are also `remove` (X-3, VP1-51). |
| OR3-40 | remove | PARTIAL | The mask claim for arm 1 holds only for a non-deterministic request. Ran: a deterministic request carrying `leaf_mode` `None`, `''`, `'agentic'` or `'pure'` is refused by OR3-40 itself; without it the next refusal is OR3-41 (`undecided`). Holds: host-twin. The conductor writes `leaf_mode` only as `workshop` and never with `deterministic` (workflow_conductor.py:1656-1660, :1934). |
| OR3-42 | merge→OR4-52 | PARTIAL | The survivor OR4-52 is `remove` in cls_or4.md, as are OR4-54 and OR3-103: every copy of the lineage-token rule goes. Holds: host-twin. The id is a uuid from `new_agent_run_id`, or a recorded request's own field. |
| OR3-43 | merge→OR3-16 | PARTIAL | Ran with a traceback: on a warm repair request a bad provider is refused at L11544 (this row), because the warm render never reads the provider (L10566-10568); OR3-16 (L10387) fires only on a cold render. The other readers of the field on that path, OR4-50 and OR4-51, are also `remove`. Holds: host-twin (workflow_conductor.py:1941), and on the warm path the value selects nothing. |
| OR3-44 | merge→OR3-15 | PARTIAL | Ran: on a warm repair a bad shape is refused at L11555 (this row), not by OR3-15; a blank shape (`''`, `'  '`) is refused by this row's third arm on a cold request too, which OR3-15 does not cover. Holds: host-twin. The conductor sets a shape only when non-empty (workflow_conductor.py:1948). |
| OR3-46 | remove | CONFIRMED | Grep: no writer of `pure_part`; judge_replay builds a new request through the conductor and copies no such field. |
| OR3-47 | remove | CONFIRMED | Ran: the two tables have equal key sets. Holds while OR3-41 or the table-equality pin stays. |
| OR3-48 | remove | PARTIAL | "Always equal in-tree" misses one path: the conductor holds the constant in memory for the whole run and every `record-launch` is a fresh subprocess reading the checkout (workflow_conductor.py:4111), so a checkout that changes under a live run makes them differ, and this is what stops that run today. Holds: no leaf path; that case is the operator's own, the class the issue names "files changing between phases". |
| OR3-49 | remove | CONFIRMED | Read: forced `[]` at workflow_conductor.py:1942, so the persisted field is `[]`. `record_launch` also ignores the field for a host-rendered request: its own list is a local `[]` (L15785), not written back. |
| OR3-50 | remove | CONFIRMED | Ran: cases e, e2 accepted; prepare blanks the fields first. |
| OR3-52 | remove | CONFIRMED | Read: A2. Ran: an empty step is refused earlier by the render; an empty node_key by this row. It is the named survivor of OR4-63; see that row. |
| OR3-53 | remove | CONFIRMED | Read: `deterministic` is computed from the pair (workflow_conductor.py:1656-1660). |
| OR3-54 | remove | CONFIRMED | Census: 0 of 8704 requests have an empty `agent_model`. Read: the claude default is stamped at conductor start (:4062-4073); codex and HTTP require `model:` (llm_config.py:692, :829). |
| OR3-55 | remove | CONFIRMED | Read: A2. |
| OR3-56 | remove | CONFIRMED | Read: `f"{spec}/deps.yaml"` from the operator's spec path (workflow_conductor.py:1697). |
| OR3-57 | remove | CONFIRMED | Read: the request's repair block is host constants (A2). The grade a leaf authors crosses the verify schema and the conductor's filters (:1409-1421, :10262-10269) before any CLI. |
| OR3-58 | remove | CONFIRMED | Read: same block; the target is a host arid and the reason a host constant. |
| OR3-59 | remove | CONFIRMED | Read: `refs.source_id`. Ran: fires only on a malformed request. |
| OR3-60 | remove | CONFIRMED | Ran: case f answers with OR3-59's message. Both go together; the value is host-set. |
| OR3-61 | remove | CONFIRMED | Ran: cases c, d, d2. |
| OR3-62 | merge→OR4-81 | CONFIRMED | Read: the two loops are the same code line for line (L11910-11943, L16201-16257); a shared reader keeps the refusal on all four callers. |
| OR3-65 | merge→VP3-025 | PARTIAL | cls_vp23.md classes VP3-025 `merge→OR _validate_terminal_run_payload`: each file names the other as survivor. Holds: host-twin. Every host-rendered site passes `[]` (workflow_conductor.py:8974, :9038, :9986, :10045, :16392). |
| OR3-67 | remove | CONFIRMED | Census: 877 orchestration pass rows, none with `output_refs`. Settles the row's needs-run. |
| OR3-68 | remove | CONFIRMED | Read: `output_refs` is the existing subset of the request's own `allowed_output_paths` (workflow_conductor.py:10731). Those paths embed IR case ids, which a leaf authors, but the check only asks for a prefix and decides no verdict. |
| OR3-85 | remove | CONFIRMED | Census: 0 of 8704 requests carry `agent_session_id`; 3090 `allow`, 0 `deny`. Grep: no writer of the field on a request (L15684-16048; the codex transaction writes the response and the index only). |
| OR3-86 | remove | CONFIRMED | Grep: `_hooks_log_path` has one user, the writer. docs/WORKSPACE_LAYOUT.md:67 names the file, which other hooks keep writing. |
| OR3-89 | remove | CONFIRMED | Read: workflow_conductor.py:16013 composes a list; L17173 defaults it. |
| OR3-95 | remove | CONFIRMED | Read: the chmod targets are the container, its ancestors and a lineage's `home` / `config` (L13699-13968). A leaf can change the mode of its own bind root but cannot swap a mount point for a link; the race needs a same-uid process outside the sandbox. Not run. |
| OR3-97 | remove | PARTIAL | As OR3-33: this is one of three id walls that all go, and the "second wall" then has no first. One caller reads the refusal as control flow: `_resecure_workflow_home_on_reuse` returns on this `ValueError` (L13420-13423). Ran: with the arm removed it still returns for an out-of-layout home, through the path comparison at L13425. Holds: operator-only. |
| OR3-99 | remove | CONFIRMED | Read: all three arms read only the operator's homes tree, which no leaf sandbox binds. Arm 2 fires after a crash between `mkdir` and the meta write, or a reused id; a lineage home stays exclusive per lineage id either way. |
| OR3-100 | remove | CONFIRMED | Read: the marker sits in the parent of the backend directory, outside every leaf bind; ids are unique unless the operator reuses one. |
| OR3-103 | merge→OR4-52 | PARTIAL | Two arms fail. "record_launch refuses first" is false: the conductor calls the preparer in-process before the launch is recorded (workflow_conductor.py:8108) and when locating a lineage (:8129). The survivor OR4-52 is `remove`. Holds: host-twin (a uuid, or a recorded request's own field). Note: `_locate_workshop_lineage` catches `ValueError` from the preparer as "no lineage" (:8127-8131). |
| OR3-104 | remove | CONFIRMED | Grep: the parameter defaults to `True` and no non-test caller passes `False`. Correction: it IS pinned, by test_orchestration_runtime.py:772-776 (subTest `pure_layout_resume`, message regex); the row's other assertions observe live code and stay. |
| OR3-105 | remove | CONFIRMED | Read: init writes the meta before any launch. Note: `_locate_workshop_lineage` catches only `ValueError`, so the replacement must not turn this into an uncaught `OSError` on that path. |
| OR3-116 | remove | CONFIRMED | Grep (tools incl. tests, docs, skills, .claude): `_pass_values_by_check_name` appears only at its definition. |
| OR4-01 | remove | CONFIRMED | Ran: `shlex.split` of a non-blank string is never empty (6 spellings); the caller substitutes the default for a blank one (L14869-14874). |
| OR4-02 | remove | CONFIRMED | Read: operator's CLI; `agent_version` is still captured. |
| OR4-03 | remove | CONFIRMED | Read: operator's CLI. Its maskers OR4-05/06 go with it; the first launch then decides (see OR4-06). |
| OR4-05 | remove | CONFIRMED | Read: with OR4-06 also gone no row checks `--restricted`; the option's absence ends the first launch (OR4-06). |
| OR4-06 | remove | CONFIRMED | Ran: CLI 2.1.326 exits 1 on an unknown option; `_leaf_infra_error` returns `None`. Read: a non-zero exit is `pure_transport` (workflow_conductor.py:8730), gets no transient retry (:14656) and no repair turn (:9115), and the phase ends `fail_closed` / `leaf_transport_error` (:16055-16101). Not run end to end through `spawn_leaf`. Decision to name: the codex twin stays as `standing:#172` (OR4-08), so preflight would check one CLI's option set and not the other's. |
| OR4-07 | remove | CONFIRMED | Read: the claude arm of the catch goes with OR4-06. |
| OR4-09 | remove | CONFIRMED | Ran: CLI provider tokens are `claude` / `codex`, exactly the table's keys. Read: both callers reach `probe_execution_platform` with the same token first (L9402, L14822). |
| OR4-20 | remove | CONFIRMED | Read: argparse `choices` at L18316; in-process callers pass host tokens; a `KeyError` follows otherwise. |
| OR4-21 | remove | CONFIRMED | Read: operator's `command:`. |
| OR4-22 | remove | CONFIRMED | Grep (tools, docs, skills, .claude, .codex, .github): the two env names and `session_policy` appear only in orchestration_runtime.py and its tests. Census: every recorded preflight is `session_policy_launchable: true`. OR2-A34 agrees. |
| OR4-24 | remove | PARTIAL | The claim "a bad id fails at the first gate that interpolates it" is false: the build runtime checks no id content since #457 (build_runtime.py:175-189), and with the walls removed `orch.x1` and `a b` run through init and home creation (ran). The in-code comment at L14998-15000 is stale for the same reason. Holds: operator-only. Same prune-tool consequence as OR3-33. |
| OR4-25 | merge→later steps | CONFIRMED | Read both branches of L15086-15097: a prior usable value overwrites the supplied one; with no prior, `_record_until_phase_high_water` takes the max with the supplied `until_phase`. |
| OR4-26 | remove | CONFIRMED | Read: argparse `choices` at L18254 is the narrower set; the one caller is `main`. |
| OR4-28 | remove | CONFIRMED | Read: run_workflow composes it; `json.loads` raises without the check (A3). |
| OR4-31 | remove | CONFIRMED | Census: all 1025 recorded preflights carry a `session_policy` dict. The one writer of the payload is `probe_execution_platform`. |
| OR4-33 | remove | CONFIRMED | Read: `SpecCatalogCorruption` then leaves the preflight CLI as a traceback and the run stops before any launch. Grep: the four event names have no reader. |
| OR4-35 | remove | CONFIRMED | Read: an llm.yaml edited between two reads in one startup, the issue's own example class. |
| OR4-38 | remove | CONFIRMED | Read: always passed (workflow_conductor.py:5486-5487); the call sits inside the fail-closed catch at L16057. |
| OR4-39 | remove | CONFIRMED | Read: the directory is a fresh `mkdtemp` made by the conductor for this launch (leaf_netns.py:159-163). |
| OR4-40 | remove | CONFIRMED | Read: same directory, made 0700 before any leaf of this launch exists. |
| OR4-41 | merge→conductor `leaf_command` | PARTIAL | The survivor runs on codex only: `workshop_mcp_url` (leaf_contract.py:645-655) has one caller, `codex_workshop_overrides` (:708). The claude argv passes the JSON through unchecked (:636-642). Holds: host-twin. The URL is the host's own `WrapperMcpServer` endpoint (workflow_conductor.py:8411). |
| OR4-43 | remove | CONFIRMED | Read: orchestration arid from the meta, child arid from `new_agent_run_id`. |
| OR4-46 | merge→OR4-59 | CONFIRMED | Read: `pre_phase_launch` runs on every launch with the same token (L15626-15635) and refuses before any launch file is written. It fires after the lineage-home lookup and the claude sequential gate. Correction: the survivor does not set `fail_closed` as the row says. Its code `backend_not_probed` is outside `FAIL_CLOSED_REASON_CODES` (ran), so OR4-129 refuses the status write and L15647 swallows the refusal; the run still ends, as `fail` / `conductor_error`. See OR4-129. |
| OR4-47 | remove | CONFIRMED | Read, not run: both drivers rewrite the preflight with `--llm-config` before any launch, run_workflow on every start, resume included (run_workflow.py:4072, outside the `if not resume_mode` block), and judge_replay (judge_replay.py:420-426). Census: the 19 preflights without `providers` are all `orch_202607{23,24,25,29}` records, none a replay; a resume would overwrite them. |
| OR4-49 | remove | CONFIRMED | Read: passed only for a warm workshop launch (workflow_conductor.py:5481-5482). |
| OR4-50 | remove | CONFIRMED | Read: provider and backend come from one entry. With OR4-51 and OR3-43 it is one of three readers of the field that all go. |
| OR4-51 | remove | CONFIRMED | Read: same entry. |
| OR4-52 | remove | CONFIRMED | Read: host uuid. It is the named survivor of OR3-42 and OR3-103; see those rows. |
| OR4-53 | remove | CONFIRMED | Read: a cold request's lineage id is the child arid. |
| OR4-54 | remove | CONFIRMED | Read: host value. |
| OR4-55 | remove | CONFIRMED | Read: host value. |
| OR4-62 | remove | CONFIRMED | Ran: neither extractor can return blank text (8 spellings each fall through to `json.dumps`). Both arms are dead on every input, which settles the row's needs-run. |
| OR4-63 | merge→L11657-11663 | PARTIAL | The survivor is OR3-52, classed `remove` in cls_or3.md. Without both, a request with no node_key skips the block that writes the sandbox profile (L15744). Holds: host-twin (A2), and `spawn_leaf` refuses a launch with no profile (workflow_conductor.py:4437-4444). |
| OR4-64 | remove | CONFIRMED | Grep: one writer (L15779), no reader. Also to update: docs/CLI_REFERENCE.md:135 and docs/workflow/COMMAND_LOG_PLACEMENT.md:66 name the field. |
| OR4-66 | remove | PARTIAL | Holds: host values (`NodeRefs`). Counted with OR4-68, which it feeds. |
| OR4-67 | remove | PARTIAL | Holds: Build writes the file in-process. Counted with OR4-68. |
| OR4-68 | remove | PARTIAL | `host-twin` names no twin, and there is none: grep finds `binary_meta.source_source_id` compared with a source id only here (L15851). The gate binds a trial to its binary path and to its source directory (validate_pipeline_semantics.py:1598-1622, :2617-2697) but not the binary to the source. After removal that bind rests on `NodeRefs` staying one chain across fresh, resume and cross-pipeline adoption (workflow_conductor.py:15110-15120, whose comment records a past defect there). Holds: no leaf authors either id or the meta. Census: the refusal ended no recorded run. |
| OR4-74 | remove | CONFIRMED | Read: the renderer refuses a non-workshop LLM request first; an exception here is still inside the catch at L16057. |
| OR4-75 | remove | CONFIRMED | Read: `_resolve_backend_type` returns the response's `backend` when it is `claude` / `codex` (L7881-7883), and the conductor always writes it. |
| OR4-83 | remove | CONFIRMED | Read: `entry.backend_token` (workflow_conductor.py:10850), or the host-written response on the timeout route. |
| OR4-87 | remove | CONFIRMED | Read: host-written response. Correction: on codex the session id in it was rewritten after `record_launch` (see OR3-22), by the host. |
| OR4-88 | remove | CONFIRMED | Read: on claude and HTTP both sides are the child arid; on codex both come from one transaction (response and index, L5126-5133). |
| OR4-90 | remove | CONFIRMED | Read: the response is under the checkout, which no leaf sandbox binds; `spawn_leaf` reads the profile and refuses without one. |
| OR4-91 | remove | CONFIRMED | Read: as OR4-90. |
| OR4-92 | remove | CONFIRMED | Read: stamped unconditionally at L16055. |
| OR4-93 | remove | CONFIRMED | Read: as OR4-90. |
| OR4-94 | remove | CONFIRMED | Read: written by `record_launch`; read by `spawn_leaf`. |
| OR4-101 | remove | CONFIRMED | Grep and census as OR3-31. |
| OR4-104 | remove | CONFIRMED | Read: on the finalize route OR4-105 (keep) runs first (L17009). On the manual route the later refusal is OR3-69, which is `undecided` and must stay for this to hold. |
| OR4-106 | remove | CONFIRMED | Read: both ids come from one `child_arid` (workflow_conductor.py:5580-5587); a non-dict payload cannot pass the argparse converters. |
| OR4-107 | remove | CONFIRMED | Grep and census as OR3-31, OR3-32. |
| OR4-110 | remove | CONFIRMED | Read: the conductor passes the orchestration arid or the step arid (workflow_conductor.py:16180). |
| OR4-111 | remove | CONFIRMED | Read: host value, set by default at L17172. |
| OR4-114 | remove | CONFIRMED | Read: manual route only (A1; no in-tree caller of `record-timeout`). |
| OR4-115 | remove | CONFIRMED | Read: a missing file raises `FileNotFoundError` at the same point. |
| OR4-116 | remove | CONFIRMED | Read: both values come from host-written launch files. |
| OR4-117 | merge→OR4-84 | CONFIRMED | Read L16283-16494: nothing durable is written before `record_agent_run`, whose duplicate check (L16561) is its first check on the ledger. |
| OR4-118 | merge→OR4-84 | CONFIRMED | Grep of every index writer: a step / substep entry leaves `running` only at L16859, after the ledger append, so a non-running entry always has a row OR4-84 sees. |
| OR4-119 | remove | CONFIRMED | Read: (b) holds; after an unguarded timeout the marker is gone and the conductor's finalize is refused by OR4-108 with nothing written. The cost is the operator's: a mis-fired command ends a live billed attempt where today it is refused with a remedy. `--force-reason`, the `forced` fields and RUNBOOK §substep-timeout-recovery (L1093-1119) go with it. |
| OR4-121 | merge→argparse | CONFIRMED | Read: `choices` at L18716, L18782; `main` is the only caller. |
| OR4-122 | merge→conductor filter | CONFIRMED | Read: the filter's two literal sets equal `REVOCATION_SEVERITIES` and `REVOCATION_REPAIR_STRATEGIES` (L5732, L5739); an out-of-set grade is dropped before the CLI. |
| OR4-127 | remove | CONFIRMED | Read: `_read_json` raises without it; set-status has no catch (A3). |
| OR4-129 | remove | PARTIAL | "Every caller passes a constant code" is false for one in-range caller: `record_launch` forwards the launch check's own code (L15638-15645), and `backend_not_probed` is not in the allowlist (ran). Today that pair cannot meet, because OR4-46 raises first; merging OR4-46 into OR4-59 makes it the route. With this row kept, the status write is refused and swallowed (L15647) and the run ends `fail` / `conductor_error`; with it removed, the run is recorded `fail_closed` / `backend_not_probed`. Grep: no reader compares a persisted `reason_code` with the vocabulary (the two uses are L17669 and the conductor's own mapping, workflow_conductor.py:17049). Holds: no leaf value; the conductor maps its own reasons to allowlisted codes before the CLI (:17049-17053, :17104-17112; see defect 1 for what that mapping reads). |
| OR4-133 | merge→OR4-82 | CONFIRMED | Read: OR4-82 covers the role and arid arms on all three routes. The `agent_backend` and `agent_session_id` arms have twins in OR4-83 and OR4-88, also `remove`; all are host-composed (`_agent_run_json`, L16466-16488). |
| OR4-134 | remove | PARTIAL | Its evidence names `_validate_response_agent_session_id` as the re-check, and OR3-22 merges that function's raise into this row: circular. Holds: host-twin. The conductor writes all four fields unconditionally (workflow_conductor.py:5446-5462). |
| OR4-135 | merge→L12698 | CONFIRMED | Read: the `validation_stage` arm is repeated at L12707-12718 on the only route. That survivor is OR3-79, `undecided`; exactly one of the pair must stay. |
| OR4-136 | remove | CONFIRMED | Read: manual route only. With OR4-104 also gone, an empty reply is refused only at record-agent-run (OR3-69, `undecided`). |
| OR4-137 | merge→OR4-105 | CONFIRMED | Read: `finalize_child` refuses an empty or absent reply before any side effect (L17009-17012), and `main` turns it into exit 1. |
| OR4-139 | remove | CONFIRMED | Read: `_child_env` is passed on every call (workflow_conductor.py:5488-5489). |
| X-1 | remove | CONFIRMED | Ran: 17 subparsers, 17 branches, `required=True`. |
| X-2 | remove | CONFIRMED | Read: `--target` is passed exactly when the step is `generate` (workflow_conductor.py:10225). |
| X-3 | remove | PARTIAL | "Keep one: VPS's copy" fails: that copy is VP1-51, `remove` in cls_vp1.md, and the launch-time copy OR3-39 is `remove` too. All three writer-side copies go while the freshness reader keeps ignoring a non-canonical id silently. Holds: host-twin. Ran: `_slug_of` yields a canonical id for 7 spellings of a spec id. |

## Cross-file conflicts (a survivor that another row removes)

| removed row | named survivor | survivor's class |
|---|---|---|
| OR3-22 | OR4-134 | `remove` (and it cites OR3-22's function back) |
| OR3-30 | VP3-027 | `merge→OR _validate_agent_summary_text` (mutual) |
| OR3-65 | VP3-025 | `merge→OR _validate_terminal_run_payload` (mutual) |
| OR3-42, OR3-103 | OR4-52 | `remove`; OR4-54 also `remove` |
| OR4-63 | OR3-52 | `remove` |
| X-3 | VP1-51 | `remove`; OR3-39 also `remove` |
| OR3-33, OR4-24, OR3-97 | each other | all `remove` |
| OR4-104, OR4-136 | OR3-69 | `undecided` |
| OR4-135 | OR3-79 | `undecided` |
| OR3-47 | OR3-41 | `undecided` |

Each pair is safe to remove jointly only on the host-twin or operator-only ground stated in its row, not on the merge.

## Defects found

1. **A diagnostician's reason text selects the run's `fail_closed` reason code** (outside this batch; `--mode prod` only, #169). `_directive_from_object` keeps the leaf-authored `reason` (≤120 chars) for a `fail_closed` directive, and `conduct` maps it by text: a reason starting `leaf_transport_error` is recorded as `leaf_transport_error`, one containing `sandbox` as `sandbox_enforcement_violation` (workflow_conductor.py:1420-1421, :17104-17110). Ran `_directive_from_object` + `resolve_severity_directive` on three reasons; the mapping lines were read. The leaf gains nothing toward done (the run fails closed either way); the record names a cause that did not happen and RUNBOOK routes the operator by that code. Severity: a false record, low.
2. **OR3-104's pin was missed.** cls_or3.md lists "tests: none"; test_orchestration_runtime.py:772-776 asserts the refusal's message.
3. **Stale comment at L14998-15000 and L11075-11077.** Both say a non-token id loses the run "at its first build-runtime call"; nothing refuses it since #457 (ran).
4. **A `fail_closed` status write that is refused and swallowed** (L15637-15649). `record_launch` passes the launch check's `reason_code` to `update_orchestration_status` inside `try / except Exception: pass`. A code outside `FAIL_CLOSED_REASON_CODES` (`backend_not_probed`, ran) is refused by OR4-129 and the refusal is dropped, so the orchestration keeps `running` until run_workflow records `conductor_error`. Latent today (OR4-46 raises first); it becomes live when OR4-46 is merged into OR4-59 and OR4-129 stays. The two removals should land together, or the code should join the allowlist.
5. **`re_execute` has no producer.** It is in `REVOCATION_REPAIR_STRATEGIES` and the conductor's filter (:10268) but no table or directive yields it (grep of workflow_conductor.py: five lines, none assigns it), and `VALID_REPAIR_STRATEGIES` would refuse it on a request (ran). A dead vocabulary member, not a hole.

`git status --porcelain` after the probes: only the pre-existing `?? transcript_data_20261110/`.
