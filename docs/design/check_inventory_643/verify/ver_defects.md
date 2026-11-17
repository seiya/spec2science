# Verification of five suspected holes (issue #643), at main ec2ddda3

Scratch root: a `git archive HEAD` checkout under `.../verify/defects/root`, with the
advdiff1d_linear@0.5.3 closure's `workspace/ir` + `workspace/pipelines` copied in. All runs
read-only w.r.t. the real repo. `git status --porcelain` at end: only the pre-existing
`transcript_data_20261110/` untracked dir.

Decision rule applied: a hole is IN SCOPE (`leaf shortcut`) when a leaf that takes it gets closer
to reporting its substep done (a gate cleared / verdict green). OUT OF SCOPE when what it would
gain is nameable and nil. A fail-open that only corrupts a record, with no leaf gain, is REAL but
out of the defended set per the decision criterion.

---

## Item 2 — VP3-052 + VP3-096 (version-drifted `direct_deps` key clears Compile.static): REAL, IN SCOPE

**Run (full `--stage compile`, scratch copies of the certified IR):**
- Control `component/…advdiff_flux…@0.5.0` + op `…__fabricated` → **FAIL** (1 membership violation).
- `…@0.9.9` + `…__fabricated` → **PASS**. `…` (no version) + `…__fabricated` → **PASS**.
- `…@0.9.9` + the real op → **PASS**. `…@0.4.0` (a real older version) + `…__fabricated` → **PASS**.
- `…@0.9.9` + `operations: []` → **FAIL** (the separate empty-ops gate fires).

**Mechanism confirmed in code + run.** `_validate_component_dep_operations_membership`
(vps L9276) looks the dep up in `dependency_surface.json` by the EXACT node_key incl. `@version`
(`surface_by_key.get(called[2])`); a drifted/versionless key finds no entry and the gate goes
inert (the comment at vps L9349-9353 concedes the fail-open). `_validate_compile_dependency_
consistency` (vps L11215+) compares `direct_deps` to the closure VERSION-AGNOSTICALLY
(`_normalize_node_key_token` drops `@version`), so the drifted key still equals the required set.
Neither fires → fabricated operation names clear Compile.static.

**Downstream (ran, did NOT take the reporter's word):**
- `--stage post_generate` / `post_build` on the drifted IR against the real certified source → PASS.
  The Generate gate `fortran/source.validate_dependency_operations` only checks that the model
  `use`s `<spec_id>_model` and `call`s some `<spec_id>__*` — it does not check the declared op
  NAME against the published surface. So a drifted-version fabricated-op IR is not caught there
  either.
- The REAL `_resolve_dependency_facts` on the drifted IR (no patch): the drifted dep is DROPPED
  entirely (`select` fails on `@0.9.9`), so the leaf is shown no interface for it — the facts and
  the build closure are host-derived (from `dependency_graph.json`), so the fabricated op NAME
  never reaches the linker.

**Leaf gain:** Compile.static cleared with a fabricated dependency surface / a version the closure
does not carry. **Severity:** a wrong Compile.static certification + a false IR record
(`direct_deps` records operations the dependency does not publish, and a version drift the
consistency gate tolerates). It does NOT reach a wrong final verdict: the build links the
host-derived closure, so no fabricated symbol links, and `_resolve_dependency_facts` drops the
drifted dep rather than injecting its fake surface. The membership gate exists precisely to catch
the `__apply` drift (its docstring), and the version-drift path walks around it.

**Simplest fix shape:** make the membership gate look up by the version-agnostic token (the same
`_normalize_node_key_token` the consistency gate already uses) so a drifted key still finds its
entry; or have the consistency gate flag version drift (today deliberately soft). One edit to one
of the two gates closes it.

---

## Item 3 — OR1-87 (`_resolve_dependency_facts` fold: ready-but-no-fact → `pass`): REAL fail-open, OUT OF SCOPE as a leaf shortcut (false record only)

**Run (REAL fold + REAL producer, scratch root; the flux dep's aggregate restamped to a
consistent `xfail`):**
- Baseline IR (key matches): dep folds `xfail`, node `aggregate_verdict=xfail`,
  `dependency_summary.xfail=1`. (Correct.)
- Drifted-key IR (`@0.9.9` / versionless): dep folds **`pass`**, node `aggregate_verdict=pass`,
  `dependency_summary.pass=4`, while `ready=True` and `pipeline_ref=null` on that dependency row.

**Mechanism.** `Conductor._author_derived_validate_artifacts` (wc L15537+) decides `ready` from
`_closure_node_validated_in_own_pipeline` — a version- and key-AGNOSTIC glob over
`workspace/pipelines` (vps L6682). A drifted/versionless key is still ready there. But
`_resolve_dependency_facts` resolves the fact through `DerivationResolver.select`, which IS
key/version/revocation-aware, so the drifted dep yields no fact. With no fact `display` defaults to
`"pass"` (wc L15560-61). The two readers disagree exactly on the version.

**Real (unpatched) fact-missing routes:** (a) the same version drift as item 2; (b) a dependency
revoked or re-keyed after the consumer's launch gate — the glob reads `binary_meta.verification_
status` and the aggregate value off disk and can still show ready, while `select` honours the
revocation and drops the fact. Both reach "ready, no fact → pass".

**Census (read-only, 52 workspace snapshots, 876 aggregate_verdict.json):** 0 ready-with-no-fact
rows, 0 aggregate `xfail`, 0 self `xfail` in the corpus. So the drop path has not fired on a
real xfail dependency to date (coverage: the aggregates on disk; a `pass`-only history cannot
exhibit it).

**Leaf gain / reader analysis.** No reader distinguishes a node's aggregate `pass` from `xfail`:
readiness (`_closure_node_validated_in_own_pipeline`) accepts both; a dependency's `xfail` does
NOT block a consumer. Promote requires the node's OWN `aggregate_verdict overall=pass`, but the
fold hides a DEPENDENCY's xfail, which did not block anyway. So a leaf that drives the drop gets
no closer to any verdict it could not already reach — what it gains is naming its own
`aggregate_verdict`/`summary.json` dependency counts `pass` instead of `xfail`, a false record,
not a cleared gate. **Verdict: REAL fail-open, severity = false record only, leaf gain nil → out
of the defended set per the decision criterion.** (Still a genuine fail-open worth closing cheaply.)

**Simplest fix shape:** fold "ready but no fact" fail-closed (`xfail`/`unknown`) rather than
`pass`; or read the ready dep's verdict from the SAME selection readiness uses, so the fold and
readiness cannot disagree on the version. Owner: the fold (WC5-40), not the orientation producer.

---

## Item 4 — VP2-31 (leaf-selectable flat-form lift via top-level `raw_requirements`): REAL, IN SCOPE

**Run (full `--stage compile`, root `raw_requirements` variants):**
- Root dict = copy of the valid nested block → **PASS** (the flat path reads the ROOT block).
- Root `null` / `{}` / `{required_evidence: []}` → FAIL with the generic shape findings.

**Decoy pairs (defect planted in the NESTED, actually-consumed block; then a ROOT decoy holding
the original valid block added):**
- V3 (unknown artifact), V4 (bad `time_shape_expr`), V5 (second snapshots entry): control
  **FAIL (1)** → with root decoy **PASS (0)**. The decoy MASKS the finding.
- V1 (drop a schema variable), V8 (bad `shape_expr`): partially masked (4→3, 5→3 findings) — the
  snapshot-schema branch is suppressed, other readers still fire.

**post_execute divergence (ran):** with a root decoy `required: false` while the nested block keeps
`required: true`, the `--stage post_execute` check "declared state_variables missing in snapshot
files" went from firing (a capture missing `ok`) to **NOT firing**. So the root form switches off
the vps capture-completeness gate, while the conductor's `_required_evidence_artifacts`
(wc L12945) and `_author_snapshot_schema` (wc L13020) read the NESTED block unconditionally and
still promote/require snapshots.

**Mechanism.** Both `_validate_io_contract_file` (vps L5642) and `_io_contract_for_execution`
(vps L3571) lift the nested block to the flat shape ONLY when `"raw_requirements" not in
contract` — a top-level key disables the lift and the flat checks then read the ROOT block
instead of the nested one. The conductor never consults a root block. Divergent `required_evidence`
between vps and the conductor, both leaf-selectable.

**Corpus:** 0 of 947 IR files across all workspace snapshots carry a root `raw_requirements`; the
schema admits it (the root-dict probe passed compile). So the root form is pure legacy
(`derived_contract.json`) surface with no live producer.

**Leaf gain:** clear a compile and/or post_execute evidence gate with a decoy while the conductor
still reads the real (nested) block. **Severity:** wrong Compile and/or Validate certification
(a capture gap or a malformed snapshot schema passes). **Simplest fix shape:** delete the legacy
flat-form escape — drop the `"raw_requirements" not in contract/data` condition so vps always
reads the nested block (no live IR uses the root form). The reporter's "remove the lift as legacy"
is the right call; it is also a #643 simplification.

---

## Item 5 — OR4-78 (`_load_graph` silently resets a malformed `agent_graph.json`): REAL behavior, NOT A HOLE

**Run (`_load_graph` shapes + the REAL completion check `update_orchestration_status(pass)`):**
- `_load_graph`: `{"edges":"boom"}`, `{}`, `{"edges":null}`, `[]` → all become `{"edges":[]}`;
  truncated/empty JSON → raises `JSONDecodeError`.
- A certified one-edge run, graph reset to malformed: completion check **REFUSED** —
  `"agent_graph.json is missing or has no \`edges\` list"`. The check deliberately re-reads the
  RAW file (`_read_json(graph_path)`, orch_runtime L12254), NOT `_load_graph`, precisely so the
  normalization cannot hide a lost-edges record. The vps audit likewise requires a non-empty
  `edges` list (`graph.get("edges")`, vps L8075-8077).
- Control (clean one-edge graph, full launch records) → **pass**. Dangling-edge graph → REFUSED
  (`child_agent_run_id missing`). After a subsequent `record_launch` graph tail re-appends the
  real edge on the reset graph → **pass** with the correct edge.

**Who writes the file:** `record_launch` and `_prune_orphan_agent_graph_edges` only — host,
runtime-owned, under `workspace/orchestrations/<id>/`, bound into no leaf sandbox (the leaf's
checkout path is an empty tmpfs, issue #227). A leaf cannot write it.

**Verdict:** the reset is real, but no validator passes where the lost edges would have failed —
every pass-granting path (the completion vouch and the vps audit) re-reads the raw file and
refuses a missing/non-list/empty `edges`. The file is not leaf-writable, so no leaf gains anything.
**NOT in the defended set; NOT a hole.** (If tidiness is wanted, `_load_graph` could raise on a
malformed-but-parseable graph instead of resetting, per the reporter — but it is not needed: the
raw re-read already guards the only pass paths. Removing the normalize-then-raw-reread pair would
be a simplification, not a fix.)

---

## Item 1 — leaf-built binary runs unsandboxed at Validate.execute / quality-check / remote: REAL, OUT OF SCOPE (documented accepted residual; no new closure-cut route found)

**Confirmed unsandboxed.** Every `tool_run_program` / `tool_run_quality_checks` call in
`_execute_inproc` passes NO `sandbox_argv_prefix` (grep of the function; the recorded
`command_log.jsonl` entries of a real run carry `sandbox_argv_prefix: None`). The only
`sandbox_argv_prefix` producers are `workshop_wrapper` (the leaf's OWN wrapper runs) and the
build-runtime library itself; Build and Validate.execute of the snapshot are not among them. This
matches `docs/design/producer_workshop_leaf.md` §Not adopted and `simplification_program.md`
("build and execute run in-process and unsandboxed today").

**What stands in for a sandbox: a text scan, not confinement.** The leaf-authored physics source
is gated by `fortran/source.model_harness_isolation_violations`, which refuses `open` (file I/O),
`call execute_command_line`/`system`, and `bind(c)` — the routes by which generated code could
rewrite a capture after the host-rendered driver wrote it. The harness (host/certified) does the
file I/O; the quality check reruns the HOST-rendered Makefile (`make test`) on the same binary —
there are no separate leaf-authored tests in `src/`.

**Attacked the gate (compiled with the target flags `-std=f2008 -O2 -fopenmp`, ran from a cwd two
levels under a scratch "checkout"):**
- `call execute_command_line('touch ../../VICTIM')` and `open(...,file='../../VICTIM')` — gate
  **REFUSES** both; each would otherwise compile and write outside cwd.
- `include 'helper.inc'` with the command/open hidden in the include file — gate **passes** (it
  scans `.f90` statements, not included text) and the write lands. BUT shipping `helper.inc`
  requires a non-`.f90` bundle file, and `codegen_bundle` logical_path rules allow only each
  language's `SOURCE_EXTENSIONS` (`.f90`) — so the include file is refused upstream. (I did not
  execute the bundle logical_path gate on a `.inc`; stated as the one unexercised layer.)
- `integer, external :: system` + `rc = system(...)` and `call rename(...)` — compile FAILS to
  link under `-std=f2008` (no GNU intrinsic), so no reach.
- Writing to a pre-connected unit (`write(99,...)`) — gate passes, but writes a scratch `fort.99`
  in cwd, not a verdict artifact.

**In-scope reach (per the decision criterion): what a leaf GAINS from writing in the run dir.**
The binary can write under its own cwd (the scratch run dir) and, absent the gate, could rewrite a
promoted capture. This is exactly the Z6 accepted residual (`zero_base_architecture.md` Z6, the
decoy-storage argument): decoy state registration already lets the kernel present ANY value at the
capture point that satisfies every host predicate, so rewriting the file after the fact gains the
leaf nothing beyond what a decoy already gives. The #589 amendment narrowed it further (the driver
is the sole writer; "the decoy residual has no subject"), but the file-rewrite family the amendment
does NOT fully close is exactly the one the text gate + the decoy argument cover. The doc's own
reopening test is "a route that closure alone cuts" — I found none: the one gate-bypass (include)
is cut by the bundle extension allowlist, not by the sandbox, so sandboxing the execute would add
no reach the current gates don't already deny.

**Remote path:** the reach is whatever `remote_execution.execute_job` copies back (the declared
evidence set); same decoy argument applies. Not executed (no remote site available).

**Verdict: REAL (unsandboxed) and OUT OF SCOPE** — the in-scope gain (rewriting a capture) is the
already-accepted decoy residual, so the leaf gains nothing a decoy doesn't already give; the
out-of-scope half (escaping the checkout, touching another pipeline) buys the leaf nothing toward a
verdict. **No new route that closure-alone would cut was found.** Layers NOT executed: the bundle
logical_path gate on a planted non-`.f90` include; the remote-site path; a `cpp_gpu`-target
source gate (I checked only the Fortran gate, which is this corpus's target).
**Simplest fix shape (only if the decoy residual is ever reopened):** confine the execute/quality
runs under `bwrap` the way the wrapper runs already are (`render_wrapper_sandbox_prefix`) — the
mechanism exists; it is declined today on the decoy-equivalence ground, not for want of a tool.

---

### Summary table

| Item | Verdict | Leaf gain | Severity |
|---|---|---|---|
| 2 VP3-052/096 | REAL, IN SCOPE | Compile.static cleared with a fabricated/ drifted-version dep surface | wrong Compile.static cert + false IR record (no wrong final verdict: build links host-derived closure) |
| 3 OR1-87 | REAL fail-open, OUT OF SCOPE | nil (xfail dep doesn't block; no reader distinguishes pass/xfail of a dep) | false record only (aggregate_verdict / dependency counts) |
| 4 VP2-31 | REAL, IN SCOPE | compile and/or post_execute evidence gate cleared with a root decoy; conductor reads nested | wrong Compile/Validate cert (capture gap or bad schema passes) |
| 5 OR4-78 | REAL behavior, NOT A HOLE | nil (file host-only-writable; pass paths re-read raw & require non-empty edges) | none |
| 1 unsandboxed run | REAL, OUT OF SCOPE | nil beyond the accepted decoy residual (Z6); no closure-cut route found | — (documented accepted residual) |
