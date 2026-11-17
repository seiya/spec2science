# Issue #643 step 2: classification of `tools/primary_evidence.py` (group `pe`), checkout ec2ddda3

Input: `pe.md` (288 rows). Counts are per row, with one class for each row. Where only one member of a row's message family is removable, the row keeps its main class and the member is named in the reason and in "Removal groups".

| class | rows |
|---|---|
| keep:leaf | 83 |
| keep:break | 165 |
| merge | 15 |
| remove | 7 |
| operator-diag? | 3 |
| defect | 0 |
| undecided | 2 |
| pointer (NOT a CLASSIFY class: a composition/constant row with no check of its own; its owner row decides) | 13 |
| standing | 0 |
| total | 288 |

Member-level candidates inside kept rows (not counted above): PE-032 and PE-044 (remove, masked, needs-run); PE-150, 151, 157 and 158 (merge to gates (f)/(g)); PE-114 and PE-133 (constant merge to runner_ir); PE-063 (`grammar_version`: operator-diag?; `cases_read` / `scalar_pairing`: G6). The fork's tally excludes pointer rows from any class: an aggregate should not count them as keep.

The tally at the end of the file is the authoritative count: it was recomputed by script from the table.

## Facts that decide most rows (verified at ec2ddda3)

- **F1. Every IR that reaches the runtime evaluator on a workflow path has passed the CURRENT Compile gate.** The evaluator is `evaluate_primary_predicates`. Its non-test callers are workflow_conductor.py:13159, :13197 and :14464, orchestration_runtime.py:3097, and the dev CLI at PE:5493. A freshly derived IR passes `Compile.static`, the `validate_pipeline_semantics --stage compile` subprocess at workflow_conductor.py:12878ff. An adopted IR (on a cold run or on `--resume`) and every phase certification go through `orchestration_runtime._ir_certification` (orchestration_runtime.py:1917-1944, reached from :1913 and :2043). That function re-runs the current `--stage compile` validator on the selected IR (issue #238) before the IR is adopted or certified. The leaf has no write authority on `spec.ir.yaml` after Compile. So a runtime copy of a Compile-gate refusal on the same IR field is masked on every workflow path. The only path left uncovered is the dev CLI `main`, where the operator supplies the IR.
- **F2. The runner_ir render readers run after the gate, on the certified IR.** The render happens at Generate. A `RenderError` there becomes a transport fail_closed (workflow_conductor.py:15900), which is not routed to compile.generate. For a check that has both a PE gate copy and a runner_ir render twin, the **PE gate copy is the survivor**: it runs first and it routes to the leaf that authored the IR. The render twins are classified in runner_ir's group. `runner_ir` imports nothing from `primary_evidence`, and PE already imports runner_ir lazily (PE:1947, 4083, 4232, 4727). So a duplicated constant can become an import from runner_ir.
- **F3. The whole file `tools/primary_evidence.py` is a member of two derivation tuples** (test_derivation_transformation_drift.py:464 `execute_tuple` and :542 `verdict_tuple`). **Every** removal or merge in this file therefore touches a digested member. A behaviour-preserving removal is a re-pin, not a bump. A removal that changes which IRs a check admits needs the bump decision.
- **F4.** `_ir_catalog_spec_kind` (vps:9931) returns None for an empty stripped `meta.spec_id`. vps:11031 calls `drive_violations` only when `kind is not None`, and that call is the only non-test caller. `_validate_catalog_spec_kind` (vps:9941-9984) refuses a missing spec_id and any kind outside `_CANONICAL_SPEC_KINDS = (component, infrastructure, problem)` (vps:9825), in the same stage.

- **F5. What a Compile-side drive or case gate row (keep:break) protects.** Without the gate copy, the defect arrives later and routed to the wrong place. Where a runner_ir render twin exists (named in the row or in pe.md's "re-checked by" column: PE-080, 116-117, 119-128, 130-137, 192-197, 201, 210, 214-215, 240, 245-247, 254), it is refused at Generate, as a RenderError and then a transport fail_closed (F2), never repaired by compile.generate. Where the IR's case evaluation is the next reader (PE-143-149, 153-164, 167), it fails at Validate.execute's case-file write, which the conductor raises as a host RuntimeError (workflow_conductor.py:13628, PE-169's row). Where neither exists (e.g. PE-118 and the distribute B2/B8 rows), the render produces a driver that is wrong without failing (PE-118: record silently dropped; B2/B8: a gathered value that is not the global one), a wrong result certified. The per-row reasons name which case applies when it is not the render twin.

## Table

| id | class | reason & evidence | tests affected |
|---|---|---|---|
| PE-001 | merge→runner_ir.py:35-55 | This is a second definition of one set and one reason function. runner_ir's copy (`HARNESS_RESERVED_SNAPSHOT_KEYS`, `reserved_name_reason`) holds the same 13 names; the two are equal today and held equal only by a test. PE can import runner_ir's copy (F2): its consumers (PE:2385, 4986, 5200) keep raising, from a single definition. Only the reason text changes, and PE-080's message text with it. | test_runner_ir.py::test_the_reserved_names_agree (delete); test_primary_evidence.py::test_step_is_a_capture_name_and_a_reserved_variable_name (literal set / message); PE-080 message pins |
| PE-002 | keep:leaf | Bounds a leaf-authored expression's depth: without it, `_eval_node` recurses out of the interpreter on a deep chain. Gate and evaluator call the one `parse_expr`, so there is no second copy to merge. | — |
| PE-003 | keep:leaf | Without it, a leaf could write a string or bool constant. A predicate whose value is a host-chosen constant reads no kernel state, and a constant-valued predicate passes. | — |
| PE-004 | keep:leaf | The operator allowlist. Without it, a leaf could write `%`, `//` or bitwise operators whose value is constant by form (e.g. `x % 1`), which `effective_names` does not prune, so such a predicate passes. | — |
| PE-005 | keep:leaf | A bare root is not a value; without the refusal, evaluation falls to an undefined name (the grammar's closure). Same single function as the gate. | — |
| PE-006 | keep:leaf | Pins capture and comparand reference shapes. Without it, `final.h.T` and similar attribute chains would reach numpy attributes the grammar does not define. | — |
| PE-007 | keep:leaf | The same for history and sampled roots (`history.h.t` and similar). | — |
| PE-008 | keep:leaf | The `at()` shape and a cross-target/cross-case mix. Without it, `at()` could read history or restart points of another case, which the reads-state rule does not classify. | — |
| PE-009 | keep:leaf | Closes the attribute roots. Without it, `__builtins__.open`-style chains reach the evaluator. | — |
| PE-010 | keep:leaf | Closes the function table, its arity and its keyword rules. Without it, a leaf could call a function with no rank rule (gate crash) or one that is constant by form. | — |
| PE-011 | keep:leaf | Closes the node types. A `Compare` or `BoolOp` would evaluate to a bool, so `final.h == final.h` would be constant 1 and the predicate would pass vacuously. | — |
| PE-012 | keep:leaf | The non-string / unparsable / too-deep refusals of `parse_expr`, fail-closed. Shared by gate and evaluator. | — |
| PE-013 | keep:leaf | Decider of the gate's reads-state rule (issue #483). Without it, `final.h * 0` counts as a state read and a constant predicate passes. | — |
| PE-014 | keep:leaf | Decides what counts as a kernel-state read. The classes come from the IR's drive bindings, never from a label. Single function, shared. | — |
| PE-015 | keep:leaf | Fixes the one exempt closure form. Loosening it lets a predicate that reads only the driver's counter stand (the row's own note). | — |
| PE-016 | keep:leaf | Fixes the one exempt flag form. Without the fixed form, any `final.<logical>` predicate escapes the reads-state rule. | — |
| PE-017 | keep:leaf | `exempt_once`: a second copy of a fixed form is not exempt. Without it, N flag copies could stand in for state reads. | — |
| PE-018 | keep:leaf | The per-scope-case reads-state decider (`unread_scope_cases`). The gate's PE-106 depends on it. | — |
| PE-019 | keep:break | A classifier that answers False on a malformed entry. Removing the `except` would make `quantity_violations` (PE:2845) and vps:11146 raise on a malformed predicate inside the Compile gate, a gate crash (fail_closed) instead of the schema finding (PE-082ff) that routes to compile.generate. | — |
| PE-020 | operator-diag? | The agreement document `{status, comparands, absent, disagreeing, records, own_run, error}` is written to `cross_target/<node>.json`. docs/WORKSPACE_LAYOUT.md:160 names "the operator" as its reader and says "nothing reads it back". The in-process `status` decides certification (orchestration_runtime's row); the file and its `evidence` / `own_run.evidence` fields have no code reader (grep in the row). Removal is the operator's call. | sym tests over `.document()` |
| PE-021 | operator-diag? | The hash feeds only PE-020's operator-only `evidence` field. Its refusal ("no state snapshot capture to bind") duplicates capture absence, which evaluation refuses per case (PE-027 → structural record → unevaluable at orchestration_runtime.py:3101-3108 / workflow_conductor.py:13166-13170). If the operator drops the field, both the hash and the refusal go. | ::test_hash_is_over_the_captures_only, ::test_file_boundaries_are_framed, ::test_no_capture_raises; test_netcdf_capture_store.py:415; test_capture_store.py:601 |
| PE-022 | keep:break | Keeps a history/series/derived loader failure local to the predicates that name it. An eager load would turn one bad capture group into a structural record for every predicate. | — |
| PE-023 | keep:break | The `shape_expr` parser's failure branch. Without it, an unrecognised form yields no dims, and the shape checks of PE-028 read a wrong rank. Compile reaches it (PE-079). | — |
| PE-024 | keep:break | Shared decider of the zero-extent set. The skip on an unreadable `shape_expr` is fail-closed through PE-079 / PE-235 in the same stage. | — |
| PE-025 | keep:leaf | Resolves leaf-authored `inputs.<path>` (absent / non-finite / ragged / bool). Without it, a non-finite or bool input enters an expression. One function is shared by gate and evaluator. | — |
| PE-026 | keep:leaf | Coordinate refusals over IR plus case inputs. The shape-match half is evaluation-only, against the leaf kernel's capture. | — |
| PE-027 | keep:leaf | Rewraps capture-store refusals over leaf-kernel output files. Without it, an absent or non-JSON capture escapes as a different exception type, outside the per-case structural catch (PE-069). | — |
| PE-028 | keep:leaf | Shape, rank, finite and non-numeric checks on leaf-kernel capture values. Without them, a non-finite value or a wrong-rank array enters an evaluation. A possible overlap with the post_execute snapshot shape check (vps:1850-1900) is vps's row to classify. That check runs on the same files earlier, but whether it covers every member here was not established. | — |
| PE-029 | keep:leaf | Steps of history and restart records. On a harness self-test the runner is leaf-generated, so a mislabelled step would let a sample stand for another step. | — |
| PE-030 | keep:break | Stacking semantics of history and restart points. Not a refusal, but the shapes every later check reads. | — |
| PE-031 | keep:leaf | Series-file refusals over the per-step folds of kernel state, including non-finite ("never laundered"). | — |
| PE-032 | keep:leaf | "initial and final disagree on the state shape" bounds leaf captures. Member "case has no case_id": masked. `load_case_env`'s only caller passes cases from `_cases_by_id` (L1876-1882), which drops id-less cases, so this branch is a remove candidate (masked, needs-run). | member: none pinned |
| PE-033 | keep:break | Fail-closed conversion of arithmetic exceptions into a per-predicate structural record. Without it, they escape to the callers' broad `except` and the whole verdict, not the one predicate, becomes structural. | — |
| PE-034 | keep:break | Derived and coordinate broadcasting semantics (issue #607). Without them, a reduction over a field of one coordinate counts one cell, a wrong value. | — |
| PE-035 | keep:leaf | Refuses any non-finite intermediate. A NaN reaching `_apply_op` makes `ne` true, so a NaN state would pass a `ne` predicate. | — |
| PE-036 | keep:leaf | The extent half is evaluation-only over leaf captures. The rank half is the evaluator's own broadcasting rule; its static twin is PE-072. The two are equal only if `expr_rank` is exact, which was not established. Without it, numpy would silently broadcast right-aligned on a static-ranker defect. | — |
| PE-037 | keep:leaf | Reduction over an empty history/series, or over an unexpanded extent-1 field. The shapes come from leaf captures, and without the refusal a reduction over nothing passes as 0. | — |
| PE-038 | keep:leaf | The extent-1 state axis member is evaluation-only. The array-argument half is the runtime side of PE-073 (see PE-036's note). | — |
| PE-039 | keep:leaf | Integer, axis-range and divisibility checks are evaluation-only. The scalar/array/arity halves are the runtime sides of PE-074. | — |
| PE-040 | keep:leaf | Roll shift integer check (evaluation-only). The count/scalar halves are the runtime side of PE-075. The fallthrough raise is masked by PE-010 but is the function's closure; deleting it would return None silently on a future table addition. Not proposed. | — |
| PE-041 | keep:leaf | Capture-absence, no-comparand and not-sampled refusals are evaluation-only over leaf output. The name/`at()` members are masked by PE-093/PE-100 but are the evaluator's lookup failure: some raise is needed there. | — |
| PE-042 | keep:leaf | A comparand of another shape is a physics disagreement, not a gap. Its two inputs are leaf captures of two targets. | — |
| PE-043 | merge→PE-091 | `evaluate_binds`' three raises (non-mapping, non-identifier, shadowing including this case's coordinates) re-check the Compile gate's bind refusals on the same IR field. Its only caller is the evaluator, and PE-091 refuses all three before any run on every workflow path (F1). The gate's coordinate names are the schema's, the same in every case. | ::test_binds_evaluate_in_order_and_cannot_look_forward (msg rows), ::test_cross_case_coordinates_and_inputs (shadow row) |
| PE-044 | keep:break | The array half is the evaluator's result-type rule (static twin PE-076; see PE-036's note). Member "evaluated to a non-finite value": masked. Every value source passes `_finite` (PE-035) or a load-time finite refusal (PE-028, PE-031, PE-025), and binds are `evaluate` results. So it is a remove candidate (masked, needs-run). | member: none pinned |
| PE-045 | merge→PE-086 | The `_predicate_scope` "target_cases must be a non-empty list" branch. The gate calls `_predicate_scope` only with already-checked non-empty targets (L2482), so only the evaluator reaches the branch, and PE-086 refuses the value first on every workflow path (F1). `_predicate_rank_violation` (L2329) reaches it only when PE-086 added nothing (the PE-102 guard), so the branch's absence cannot crash it. | EvaluationTest::test_malformed_predicate_shape_raises (target_cases row) |
| PE-046 | keep:break | `.case` non-empty. A single function shared by gate (L2482) and evaluator, with no other copy. Without it, `case: ""` reaches the scope. | (unpinned: add or leave) |
| PE-047 | keep:break | `.case` not in target_cases. Shared function; the gate reaches the same raise. | — |
| PE-048 | keep:break | Exactly one scope spelling. Shared function. | — |
| PE-049 | keep:break | `_cases_by_id` is the shared case reader. It drops malformed cases and keeps the last copy of a duplicated id. **Measured, not a defect:** a certified IR (workspace/ir/component__dynamics_advdiff_flux_1d_upwind_center2__0.5.0/…_20261116_001) with case 0 appended a second time gives 0 → 1 Compile findings under `validate_compile_ir_dir`: `IR case.test_case_set has duplicate case_id(s) ['l0_constant_state']` (runner_ir.read_case_ids, reached at Compile through `_validate_harness_render_preconditions`, vps:9123). Duplicates therefore never reach this guard on a workflow path. | — |
| PE-050 | pointer | Shared constant. | — |
| PE-051 | keep:break | Shared reader of `restart`. Gate (p) refuses every malformed spelling it maps to None (PE-273). | — |
| PE-052 | keep:break | A case with any `restart` key is not a base. A shared decider used by the writers, the wrapper and the conductor. | — |
| PE-053 | keep:break | Shared reader. Gate (p) refuses the omitted shapes (PE-274/275). | — |
| PE-054 | keep:break | A continuation is evaluated as its base. Gate (p) PE-277/278 hold the two equal. | — |
| PE-055 | pointer | Conversion of runner_ir's answer. | — |
| PE-056 | keep:break | A non-list reads as `[]`. PE-077 refuses the non-list at the gate, and the verdict side refuses a test with no record. | — |
| PE-057 | merge→PE-078 | "predicates declared but no state_snapshots entry" re-checks PE-078 on the same IR. Masked on every workflow path (F1). | EvaluationTest::test_malformed_predicate_shape_raises ("no state_snapshots" row) |
| PE-058 | merge→PE-086 | `env_for` "case not in test_case_set". Target cases are refused by PE-086, and `at()` cases by PE-093 (⊂ target_cases). Masked (F1). | EvaluationTest::test_malformed_predicate_shape_raises (structural-record row) |
| PE-059 | merge→PE-082/084/085/089/090 | The runtime predicate-entry shape family (mapping, test_id non-empty, quantity regex, op, non-null value, forbidden keys) re-checks PE-082/083/084/085/089/090 on the same IR. The docstring itself (L1984-1986) calls reaching one "a defect of the certified IR". Masked (F1). `parse_expr` and the scope checks stay (shared functions). | EvaluationTest::test_malformed_predicate_shape_raises (5 rows); ::test_a_predicate_that_reads_no_state_raises_at_evaluation_too (forbidden-key part) |
| PE-060 | merge→PE-104 | Whole-entry reads-state refusal at evaluation. A weaker copy (raw refs) of PE-104/105, which run on every IR first (F1). The test's stated purpose is "an IR the gate never saw" (test_primary_evidence.py:3615-3618), and F1 shows no workflow path delivers one. | ::test_the_evaluator_refuses_an_entry_that_reads_no_state_past_the_gate (delete), ::test_a_predicate_that_reads_no_state_raises_at_evaluation_too, ::test_the_evaluator_refuses_each_counter_alone, ::test_the_evaluator_exempts_one_closure_per_test_too, ::test_the_coriolis_repro_is_refused_by_the_gate_and_the_evaluator (evaluator half) |
| PE-061 | merge→PE-106 | Per-scope-case reads-state at evaluation. A weaker copy of PE-106. Masked (F1). | ::test_a_scope_over_both_partitions_reads_state_in_each (evaluator half) |
| PE-062 | keep:break | The `cross_target_only` mode the cross-target agreement asks for. A mode, not a check. | — |
| PE-063 | keep:break | The per-predicate record that verdict_evaluator, the conductor and orchestration_runtime read (`kind`, `satisfied`, `evaluated`, `target_cases`). Member `grammar_version`: no code reader (grep in row), named only in docs. operator-diag?, the operator's call. Members `cases_read` / `scalar_pairing`: their only readers are verdict_evaluator's re-checks of gates PE-110 / PE-189 over records the host derived from the same gated IR, so they go with group G6. | judge_replay sym (grammar_version) |
| PE-064 | keep:break | verdict_evaluator.py:225-236 refuses any cross-target record that is not exactly this placeholder. | — |
| PE-065 | keep:leaf | Records a comparand shape mismatch as a physics failure. Without it, a comparand that sized its state differently would be a gap, not a disagreement. | — |
| PE-066 | merge→PE-090 | "value.per_case has no entry" re-checks PE-090's missing-threshold refusal on the same IR. Masked (F1). The `_resolve_value` lookup stays. | EvaluationTest::test_value_map_needs_every_case_and_a_number (row) |
| PE-067 | merge→PE-090 | "value is not a finite number" re-checks PE-090's finite-number refusal. Masked (F1). | same test (row) |
| PE-068 | keep:leaf | The comparison itself, of leaf kernel output against the IR threshold. | — |
| PE-069 | keep:break | A fail-closed per-case structural record. Without it, a PrimaryEvidenceError escapes and fails the whole verdict. | — |
| PE-070 | pointer | — | — |
| PE-071 | keep:break | Rank-rule table. A missing entry is a KeyError gate crash. Set identity is pinned. | — |
| PE-072 | keep:break | The static twin of PE-036's rank half. Without it, a rank error surfaces only at Validate, as a structural_violation after build and run, charged to the Generate loop rather than compile.generate (misattribution). The earliest copy. | — |
| PE-073 | keep:break | The same, for smax/smin/ssum. | — |
| PE-074 | keep:break | The same, for array-first functions. | — |
| PE-075 | keep:break | The same, for roll. | — |
| PE-076 | keep:break | The same, for the scalar result. The malformed-scope swallow is safe under PE-102. | — |
| PE-077 | keep:break | Non-list predicates. Without it, the gate iterates a non-list. | — |
| PE-078 | keep:break | Survivor of PE-057. Without it, a missing capture entry is found only at Validate. | — |
| PE-079 | keep:break | Unreadable schema: a finding, not a crash. The render's refusal comes later (F2). | — |
| PE-080 | keep:break | Survivor over the render twin runner_ir.py:119-122 (F2). Without it, a reserved variable name collides with a capture layout key, and only the render refuses it, at Generate as fail_closed. | (message changes under PE-001) |
| PE-081 | keep:break | Coordinates resolve in every case before any run. | — |
| PE-082 | keep:break | Survivor of PE-059 (mapping). | — |
| PE-083 | keep:leaf | Ties a predicate to a tests.md test. Without it, a leaf can hang predicates on a test the spec does not define, and quantity coverage of the real test is then checked against the wrong set. | — |
| PE-084 | keep:break | Survivor of PE-059 (quantity). | — |
| PE-085 | keep:break | Survivor of PE-059 (op). `_apply_op` would KeyError. | — |
| PE-086 | keep:break | Survivor of PE-045 / PE-058. | — |
| PE-087 | keep:leaf | A predicate ranges over every case its test does. Without it, a test is certified while some of its cases are never read. Its re-check on records (verdict_evaluator.py:240-245) is the host twin, in verdict_evaluator's group. | — |
| PE-088 | pointer | — | — |
| PE-089 | keep:break | Survivor of PE-059 (forbidden keys). | — |
| PE-090 | keep:break | Survivor of PE-059 / 066 / 067 (value family). | — |
| PE-091 | keep:break | Survivor of PE-043. | — |
| PE-092 | pointer | (no-cascade: a bind that fails to parse still counts as defined) | — |
| PE-093 | keep:break | `at()` outside target_cases. Survivor of PE-041's member. | — |
| PE-094 | keep:break | A reference to a non-schema variable fails before any run rather than structurally at Validate. | — |
| PE-095 | keep:break | History references need the declared entry, stride and time variable. Without it, the failure surfaces only at Validate. | — |
| PE-096 | keep:leaf | The leaf gain is named in the docstring (L2370-2376). On a driven node a restart point and a sample/final are committed from one state, so their difference is zero whatever the kernel computed. | — |
| PE-097 | keep:break | Series references need the entry and the name. | — |
| PE-098 | keep:break | A derived name must be host-evaluated for the case. | — |
| PE-099 | keep:break | Inputs resolve per scope case (issue #532). | — |
| PE-100 | keep:break | Names: coordinate / bind order / constant. Survivor of PE-041's lookup member. | — |
| PE-101 | keep:leaf | Without it, two histories sampled at different strides but with equal sample counts combine elementwise across different steps: a wrong value with no error. | — |
| PE-102 | keep:break | No-cascade guard. Without it, `expr_rank` runs on an unparsed expression (crash) or repeats a finding. | — |
| PE-103 | pointer | — | — |
| PE-104 | keep:leaf | The reads-state rule. Survivor of PE-060. Without it, a predicate over inputs or derived values alone certifies nothing about the kernel. | — |
| PE-105 | keep:leaf | The effective-refs reads-state rule (issue #483). It has no runtime twin. | — |
| PE-106 | keep:leaf | Per-scope-case reads-state. Survivor of PE-061. | — |
| PE-107 | keep:break | Fail-closed None on malformed input. The schema gate refuses those shapes in the same stage. | — |
| PE-108 | keep:leaf | Decider behind PE-110. Without it, a flag predicate counts as reading every case. | — |
| PE-109 | keep:break | `record_scalar_pairing` / the record's `scalar_pairing` map. Its reader verdict_evaluator `_scalar_pairing` (verdict_evaluator.py:111-125) refuses a record without the map, so removing this alone breaks every verdict. The reader `_check_scalar_pairing` (:139ff) is documented as a re-check of the Compile gate's D7 (PE-189) by the shared rule `scalar_pairs`, over records the host derived from the same gated IR (F1): a host twin. If verdict_evaluator's group removes that re-check, this record goes with it (G6). | — |
| PE-110 | keep:leaf | Every target case is read by some predicate of the test. Without it, a test passes without reading one of its cases. | — |
| PE-111 | keep:leaf | Every listed quantity has a predicate. Without it, a tests.md quantity goes unjudged. | — |
| PE-112 | keep:leaf | No unlisted quantity. Without it, a leaf substitutes an easier quantity. | — |
| PE-113 | keep:break | Skips malformed entries, which the schema gates report in the same stage. | — |
| PE-114 | pointer | Constants row. Member `LOOP_KEYS` (L2895) duplicates runner_ir.py:212 `_DRIVE_LOOP_KEYS` byte for byte: merge→runner_ir.py:212, by import (F2). | test pins of the equality, if any |
| PE-115 | merge→runner_ir.py:215-219 | `_IDENT` (a `[A-Za-z_][A-Za-z0-9_]*` fullmatch) and runner_ir's `_ident` (first character not a digit, every character in `[A-Za-z0-9_]`) admit the same set. One definition, by import. A 1-line saving; low value. | none |
| PE-116 | keep:break | Gate copy (survivor, F2) of a procedure binding's `state.<var>`. Its render twin runner_ir.py:222-226 refuses only at Generate, as fail_closed. | — |
| PE-117 | keep:break | A nan/inf binding literal. The render falls through to a generic error at Generate. | — |
| PE-118 | keep:break | The only refusal of a non-mapping `record`: the render silently drops it (runner_ir.py:238-246), so without this a driver that records nothing is rendered. | — |
| PE-119 | keep:break | The binding grammar at the gate (survivor, F2). | — |
| PE-120 | keep:break | `drive.distribute` shape (survivor over runner_ir.py:287-326). | — |
| PE-121 | keep:break | At most 2 axes (survivor; the render twin is runner_ir.py:323). | — |
| PE-122 | keep:break | Axis mapping and keys. | — |
| PE-123 | keep:break | Axis name is an identifier. | — |
| PE-124 | keep:break | cells/ghost/extended are `inputs.<path>`. | — |
| PE-125 | keep:break | extended requires ghost. | — |
| PE-126 | keep:break | min_block ≥ 1. | — |
| PE-127 | keep:break | Axis named twice, or one input serving two roles. The second has no render copy. | — |
| PE-128 | keep:break | Drive entry shape (survivor; the render twins in runner_ir.py:623-650 are the pinned copies). The PE copies are unpinned. | — |
| PE-129 | keep:leaf | The drive calls only a published operation. There is no render copy. Without it, the driver could call an unpublished helper that the published-surface gates never see. | — |
| PE-130 | keep:break | Signature present (survivor over runner_ir.py:613-621). | — |
| PE-131 | keep:break | Every dummy bound exactly once. | — |
| PE-132 | keep:break | `drive` is a mapping. | — |
| PE-133 | keep:break | Unknown drive key. Member `_DRIVE_KEYS` (L3082) duplicates runner_ir.py:214 `DRIVE_KEYS`: merge that constant by import (F2). | the tuple-equality test (comment L3081) |
| PE-134 | keep:break | init is not a list; step is required / non-empty. | — |
| PE-135 | keep:break | Loop keys and their kinds. | — |
| PE-136 | keep:break | `history` strides are `inputs.<path>` (gate (m) compares them). | — |
| PE-137 | keep:break | `restart_points.every` is an `inputs.<path>`. | — |
| PE-138 | keep:break | No-cascade: a malformed distribute does not mask the other gates; gate (n) reports it. | — |
| PE-139 | keep:break | `drive_block` raises the first structural finding. The vps readers that return [] rely on gate (b) in the same stage. | — |
| PE-140 | keep:break | `drive_model`'s preconditions. Gate-time callers catch only PrimaryEvidenceError, so without these an AttributeError/KeyError crashes the gate. | — |
| PE-141 | keep:leaf | Derives the state classes from bindings and intents, never from a leaf label. It decides what counts as a state read. Its twin runner_ir.drive_state_classes is held equal by DriveAgreementTest; unifying the two is the cross-module group X. | — |
| PE-142 | keep:break | `closure_refs=None` on a refused `n_step`. Gate (e) refuses it. | — |
| PE-143 | keep:break | A dims token's value is integral; otherwise extents are wrong. | — |
| PE-144 | keep:break | A dims token resolves only through allowed bindings. | — |
| PE-145 | keep:break | A dims token names a rank-0 dummy. | — |
| PE-146 | keep:break | An unbound or conflicting extent on a list form. | — |
| PE-147 | keep:break | The dims-token grammar and extent ≥ 0. | — |
| PE-148 | keep:break | An array dummy has dims. | — |
| PE-149 | keep:break | Broadcast fill of initial/derived values into the dummy's shape. | — |
| PE-150 | keep:leaf | `_host_expr_violation` (the decider) keeps host-evaluated initial/derived expressions from reading `final.*` / `at()`, i.e. a case configured from kernel output. Member: its two call sites inside `_evaluate_case` (L3824, L3857) are masked by gate (g) (PE-255). At Compile, (c) runs only when (g) found nothing (L5174); at Validate.execute, the wrapper and run_configuration it runs on a certified IR (F1); overrides change inputs only. Merge those two call sites →PE-255. | CaseInputsTest::test_each_host_refusal_names_the_case_and_the_entry (2 rows move to (g)) |
| PE-151 | keep:break | "derived must be a mapping": no gate twin was found. Member "initial_state must be a mapping": masked by gate (f) PE-249 for the same reason as PE-150. merge→PE-249. | none pinned |
| PE-152 | keep:break | Pre-pass swallow; the full pass re-evaluates. Fail-closed overall only if the full pass converts every TypeError/ValueError to PrimaryEvidenceError. That was not established, and an escape would crash the gate instead of producing a finding. | — |
| PE-153 | keep:break | Extents agree across the dummies of one state. | — |
| PE-154 | keep:leaf | Zero extent on a case a non-xfail test targets. Without it, a pass test over an empty state reduces over nothing and passes. | — |
| PE-155 | keep:break | Coordinate count equals the state extent. | — |
| PE-156 | merge→PE-250/251/253 | The three initial_state key refusals in `_evaluate_case` duplicate gate (f). At Compile, (c) runs only when v is empty (L5174), so (f) always reports first. Every runtime caller (`evaluate_case_inputs` from Validate.execute :13628, wrapper :624, run_configuration :300) runs on a certified IR whose initial_state keys operator overrides do not change (F1). | CaseInputsTest::test_each_host_refusal_names_the_case_and_the_entry rows "initial_state has no entry", "takes no initial_state entry" (move to (f) text) |
| PE-157 | keep:break | Literal shape and evaluation-error members are unique. Member "an output the operation only writes takes a number literal" is masked by (f) PE-252 (same argument as PE-156): merge→PE-252. | none pinned for that member |
| PE-158 | keep:break | The identifier and evaluation-error members are unique (the identifier check reaches Compile only through (c)). Member "a number or an expression" is masked by gate (g) PE-257: merge→PE-257. | none pinned for that member |
| PE-159 | keep:break | Bound host data shapes against dummy extents. | — |
| PE-160 | keep:break | An integer dummy gets an integral value. Without it, `int(1.5)` = 1 is written to the case file (the test docstring). | — |
| PE-161 | keep:leaf | `n_step ≥ 1` and `dt > 0`. Without them, a zero-step loop makes final equal initial and closure-style predicates hold trivially. | — |
| PE-162 | keep:break | The loop ends at t_end within 4 ulp. Without it, the kernel runs a different interval than the spec's and a wrong result is certified. It also applies to operator overrides (run_configuration). | — |
| PE-163 | keep:break | Stride is an integer ≥ 0. | — |
| PE-164 | keep:break | Inputs resolve; a procedure reads scalar inputs. | — |
| PE-165 | keep:break | Case prefix and refusal_subject plumbing. | — |
| PE-166 | remove | **masked**: `_authored_json`'s bool and non-number raises and its `_finite` calls are dead on its only caller (L4074). Every value it receives is `values.inputs[path]`, which `_evaluate_case` L3944 has just passed through `resolve_input_value`, and that function refuses bool (via `_is_number`, verdict_evaluator.py:69), non-number and non-finite values. (a) Leaf gain: none; the same value is already refused upstream in the same call. (b) No breakage; the int/float/list conversion stays. Read-only claim: needs-run. | none pinned |
| PE-167 | keep:break | Infrastructure payload, refusing non-finite values. | — |
| PE-168 | keep:break | The case file payload the driver reads. | — |
| PE-169 | keep:break | The writer; nothing is written on refusal. | — |
| PE-170 | merge→vps:9163 | The safe-token check at the case-file write duplicates Compile's `_validate_case_ids` (vps:9163, same regex + `..`, routed to compile.generate), and runner_ir.py:158 at render. Every caller writes from a certified IR (conductor :13628, wrapper :624 on the seeded certified IR, F1) or from host-synthesised `c<i>` ids (run_configuration.py:565 asserts them). | none pinned |
| PE-171 | keep:break | The case_inputs files the driver reads. | — |
| PE-172 | keep:break | Continuation case files and `refused`. | — |
| PE-173 | keep:leaf | A restart point is absent. The restart record comes from the leaf kernel's run. | — |
| PE-174 | keep:leaf | A restart point is unreadable. | — |
| PE-175 | keep:leaf | A restart point carries a wrong step. Without it, a continuation starts from another step's state. | — |
| PE-176 | keep:leaf | A restart variable is missing. `_load_capture` skips absence silently, so this is the only check. | — |
| PE-177 | keep:leaf | Restart values are numeric and rectangular. | — |
| PE-178 | keep:leaf | Restart extents equal the base case's. | — |
| PE-179 | keep:leaf | Restart values are finite. | — |
| PE-180 | keep:leaf | Restart time is finite. | — |
| PE-181 | keep:break | Fail-closed `refused` per case. | — |
| PE-182 | keep:leaf | Continuation start check (unreadable). | — |
| PE-183 | keep:leaf | The docstring names the leaf gain: a continuation that re-ran its base makes every equality predicate hold. | — |
| PE-184 | keep:leaf | The start capture holds the restart variables. | — |
| PE-185 | keep:leaf | The start values equal the restart point's. | — |
| PE-186 | keep:break | Fail-closed catch-alls of the context. `drive_violations` refuses the same IR in the same stage. | — |
| PE-187 | undecided | The first `required_evidence` entry per artifact wins; duplicates are dropped silently. Whether a duplicate entry is refused elsewhere (vps / verdict_evaluator) was not checked, and so neither was whether a leaf could declare two `state_snapshots` schemas so that the gate and some other reader read different ones. | — |
| PE-188 | keep:leaf | The xfail-only exemption. The label is pinned to tests.md elsewhere (`validate_tests_schema`). | — |
| PE-189 | keep:leaf | Gate (i), D7. Survivor for G6. | — |
| PE-190 | keep:break | A locally fail-open `[]`, closed by structural findings in the same call. Without the except, a gate crash. | — |
| PE-191 | merge→runner_ir.py:807 | `PROCEDURE_FUNCTIONS` is the same 8-name set as runner_ir.py:807, held equal by a test. Import it (F2). | test_driver_render.py::test_the_two_readers_of_a_procedure_binding_agree (constant half) |
| PE-192 | keep:break | The procedure dummy has a prototype with one real array out. Gate copy (survivor, F2). | — |
| PE-193 | keep:leaf | A procedure expression reads only in dummies, `inputs.*` and constants. Without it, a host-rendered procedure could read state and compute the checked answer on the host. | — |
| PE-194 | keep:break | `calls:` is a scalar schema variable. | — |
| PE-195 | keep:break | `record.*` rules. | — |
| PE-196 | keep:break | Drivable dummy types. | — |
| PE-197 | keep:break | A procedure-typed dummy takes only `{procedure}`. | — |
| PE-198 | keep:break | `state.*` bindings: schema var, rank, one type. | — |
| PE-199 | keep:break | inout rules. | — |
| PE-200 | keep:break | out rules (D3). | — |
| PE-201 | keep:break | in-dummy rules. | — |
| PE-202 | keep:break | Intent domain. | — |
| PE-203 | keep:break | One reader and one writer per state per entry. | — |
| PE-204 | keep:leaf | Without loop, an in/out pair binds two states. With one state on both, the initial capture is the host's value and the final is the kernel's, so the pairing reads a state the call overwrote in place. | — |
| PE-205 | keep:leaf | A schema variable no dummy touches is host data echoed into a capture. A predicate over it would read host data as kernel output. | — |
| PE-206 | pointer | — | — |
| PE-207 | remove | **no-reader**: the refused key `state_snapshots.schema.variables[].source` is read by no code. `rg -n '"source"' tools -g '!tools/tests/**'` hits only other dicts (command provenance, the dependency surface at orchestration_runtime.py:4057-4075/9729, codegen objects, workflow_conductor.py:15067 bindings, vps:9360 a public_api surface). `dget(..."source")` / `.get("source")` in runner_ir, host_render, backends/language, capture_store, verdict_evaluator: none. `tools/prompt_templates/workshop_*.txt`: no `source:`. (a) Leaf gain: none; the vocabulary was retired by D6, and nothing decides from the key, so writing it changes no gate, render or verdict. (b) No breakage: an IR that carries it renders and evaluates the same. The refusal guards a migration residue. | test_validate_pipeline_semantics.py::DriveGateStageTests::test_a_source_key_is_refused_on_an_infrastructure_node, ValidatePipelineSemanticsTests::test_a_source_key_is_refused_at_the_stage; test_primary_evidence.py::DriveViolationsTest::test_the_bindings (source row) |
| PE-208 | keep:break | `distribute` required on a component / problem node. | — |
| PE-209 | pointer | — | — |
| PE-210 | keep:break | `block.<axis>.first` names a declared axis. | — |
| PE-211 | keep:break | A rank-0 dummy is bound to an axis input consistently. | — |
| PE-212 | keep:break | An axis input is an integer. | — |
| PE-213 | keep:break | An axis input is bound by a rank-0 integer dummy. | — |
| PE-214 | keep:break | An array dummy without dims on an axis node. | — |
| PE-215 | keep:break | A recorded dummy without dims on an axis node. | — |
| PE-216 | pointer | Owner `runner_ir.array_layouts` (one shared derivation). | — |
| PE-217 | keep:break | Two dummies of one state derive one layout. | — |
| PE-218 | keep:break | A record state takes the recorded dummy's layout. | — |
| PE-219 | keep:break | No record of a ghost layout. | — |
| PE-220 | keep:break | A ghost layout needs a ghost input. | — |
| PE-221 | keep:break | B2: no ghost-margin out on an axis node. Otherwise the gather is wrong. | — |
| PE-222 | keep:break | B8: no block-local scalar out. Otherwise a wrong global value. | — |
| PE-223 | keep:leaf | An inconsistent `extended` input is refused on pass-only cases. Without it, a pass case runs replicated rather than distributed, so the distributed path is certified unexercised. | — |
| PE-224 | keep:break | Layout extents per case. | — |
| PE-225 | keep:break | `distribute_layouts` has no production caller. It is a test-support witness: DriveAgreementTest holds gate (n)'s derivation (over PE's entry parser) equal to the renderer's. Without it, the two drive readers can diverge unnoticed, and gate (n) would check layouts the driver does not use. It goes only with group X (reader unification). | DriveAgreementTest::test_the_layouts_agree |
| PE-226 | pointer | — | — |
| PE-227 | keep:break | Infrastructure series names include `step` and are distinct. | — |
| PE-228 | keep:break | Two kind-exclusive locations, not a duplicate. The drive-kind copy is reachable: a schema array named with a trailing `_` (e.g. `h_`, admitted by PE-080 since it contains no `__`) gives the required series name `h__min`, which contains the layout separator. The test row `h__min` exercises exactly that. | — |
| PE-229 | pointer | — | — |
| PE-230 | keep:break | An infrastructure node carries no drive. Otherwise `_has_drive` readers (case-input writer, render) treat it as driven. | — |
| PE-231 | merge→vps:9973 | "spec kind is not one of [component, infrastructure, problem]" re-checks the operator catalog value that `_validate_catalog_spec_kind` (vps:9973-9977, `_CANONICAL_SPEC_KINDS`) refuses in the same stage on the only non-test caller (F4). (a) Leaf gain: none (operator input, already refused). (b) No breakage: on removal the call site should skip `drive_violations` for a non-canonical kind, or the function returns [] for it; otherwise an unknown kind would fall into the drive gates and add cascade findings to an already-failing stage. Operator-only on every path. | DriveViolationsTest::test_the_kind ('physics' row) |
| PE-232 | keep:break | Drive is required on a component / problem node. Otherwise the render fails at Generate (fail_closed). | — |
| PE-233 | keep:break | No-cascade on structural drive findings. Without it, the semantic gates crash on block=None. | — |
| PE-234 | keep:break | Precondition of the drive gates. PE-078 refuses the same absence only when predicates exist. | — |
| PE-235 | keep:break | Unreadable schema: a finding, not a crash. | — |
| PE-236 | remove | **masked**: gate (a) "meta.spec_id is required". It uses the same normalisation (`str(... or "").strip()`, PE:5020) as `_ir_catalog_spec_kind` (vps:9935), which returns None for an empty id, and vps:11031 then does not call `drive_violations`. `_validate_catalog_spec_kind` reports the missing spec_id (F4). (a) Leaf gain: none; the absence is refused in the same stage. (b) No breakage: the branch is reachable only by a direct test call. Read-only claim: needs-run. | none pinned |
| PE-237 | keep:break | A problem publishes `<spec_id>__step`. | — |
| PE-238 | keep:leaf | A problem's step calls `<spec_id>__step`. Without it, the driver calls another published operation (e.g. a trivial one), and the run certifies code that is not the problem's step. | — |
| PE-239 | keep:leaf | The same, for init. | — |
| PE-240 | keep:break | result is a logical rank-0 out. Gate copy (survivor over runner_ir.py:634 / 1140). | — |
| PE-241 | keep:leaf | The test docstring (L5124-5127) names the gain: a host-filled scalar as `loop.n_step` dodges the n_step ≥ 1 / t_end host checks. | — |
| PE-242 | keep:break | A loop input resolves per case. | — |
| PE-243 | keep:break | A loop derived value exists per case. | — |
| PE-244 | keep:leaf | A step `dt`/`t` dummy is the loop's (R6-2). Without it, the kernel advances by a dt other than the loop's while the t_end check passes. | — |
| PE-245 | keep:break | history/restart_points are required with loop (render backstop at Generate). | — |
| PE-246 | keep:break | They are refused without loop. | — |
| PE-247 | keep:break | A history key is a schema variable. | — |
| PE-248 | keep:break | No-cascade: a case no entry runs is left to (f'). | — |
| PE-249 | keep:break | `initial_state` mapping (survivor of PE-151's member). | — |
| PE-250 | keep:break | `initial_state` key is a schema variable (survivor of PE-156). | — |
| PE-251 | keep:leaf | A variable init writes takes no `initial_state`. Without it, host data overrides what the kernel's init writes, and an `initial.<v>` predicate reads host data. Survivor of PE-156. | — |
| PE-252 | keep:break | An operation-only out takes a number literal (survivor of PE-157's member). | — |
| PE-253 | keep:break | Every variable init does not write has an entry (survivor of PE-156). | — |
| PE-254 | keep:break | List-form partition (f'). | — |
| PE-255 | keep:leaf | Gate (g): host expressions read no kernel output (survivor of PE-150's member). | — |
| PE-256 | keep:break | A coordinate failure means coords=set(). (c) reports the cause; this avoids a crash. | — |
| PE-257 | keep:break | `derived` entry type (survivor of PE-158's member). | — |
| PE-258 | keep:break | Gate (c): per-case evaluation at Compile, with the no-cascade guard. | — |
| PE-259 | keep:break | step_series iff loop. | — |
| PE-260 | keep:break | step_series names exact. | — |
| PE-261 | keep:break | state_history iff history. | — |
| PE-262 | keep:break | restart_points iff restart_points. | — |
| PE-263 | keep:leaf | Every init-written variable is read by a predicate. Without it, init's output is never judged. | — |
| PE-264 | keep:leaf | An init-written loop scalar is paired with an array read. | — |
| PE-265 | undecided | Gate (l) is skipped while (e) refuses `loop.n_step`. Without the skip, one more (l) finding joins (e)'s in an already-failing stage; no crash (`_zero_step_refs` answers on any binding, L5244). Whether that extra finding misleads the compile.generate repair is unmeasured. | DriveViolationsTest::test_a_host_materialised_scalar_cannot_set_the_loop (exact list) |
| PE-266 | keep:leaf | The zero-step closure is required on pass tests of a loop node. | — |
| PE-267 | keep:leaf | The init-result closure; the message names the 0 − 0 gain. | — |
| PE-268 | keep:leaf | A one-call closure names one result. | — |
| PE-269 | keep:leaf | A one-call closure flag predicate. | — |
| PE-270 | pointer | — | — |
| PE-271 | keep:break | A continuation needs a loop node. | — |
| PE-272 | keep:break | Exists for disagreement between PE's `_parse_drive` and runner_ir's reader. Goes only with group X. | — |
| PE-273 | keep:break | `restart` shape. | — |
| PE-274 | keep:break | The base is another case. | — |
| PE-275 | keep:break | The base is not a continuation. | — |
| PE-276 | keep:leaf | The base is targeted by a pass test and by no xfail test. | — |
| PE-277 | keep:break | A continuation's own `initial_state` would be silently ignored (it is evaluated as its base). | — |
| PE-278 | keep:break | The same, for inputs/derived. | — |
| PE-279 | keep:break | The base commits restart points. | — |
| PE-280 | keep:break | step divides `restart_points.every`. | — |
| PE-281 | keep:break | step < n_step. | — |
| PE-282 | keep:leaf | A `restart.<v>` predicate with a bind or a comparand would be cross-target and never evaluated, so the restart judgment would pass unevaluated. | — |
| PE-283 | keep:leaf | The one judgment form `maxabs(final.v - at(base).final.v) le 0`. | — |
| PE-284 | remove | **operator-only**: Dev CLI `main`: "unreadable IR / not a mapping". It reads a dev-session CLI argument on every path; the CLI has no in-tree caller and no doc names it (PE-288's grep). (a) Leaf gain: none; no leaf reaches the CLI. (b) No workflow breakage: the CLI would print a traceback instead of the message. Goes with the CLI if the operator removes it (PE-288). | CliTest |
| PE-285 | remove | **operator-only**: CLI `--run` has no snapshot dir. It reads a dev-session CLI argument on every path; the CLI has no in-tree caller and no doc names it (PE-288's grep). (a) Leaf gain: none; no leaf reaches the CLI. (b) No workflow breakage: the CLI would print a traceback instead of the message. Goes with the CLI if the operator removes it (PE-288). | CliTest |
| PE-286 | remove | **operator-only**: CLI `--target` profile error. It reads a dev-session CLI argument on every path; the CLI has no in-tree caller and no doc names it (PE-288's grep). (a) Leaf gain: none; no leaf reaches the CLI. (b) No workflow breakage: the CLI would print a traceback instead of the message. Goes with the CLI if the operator removes it (PE-288). | none |
| PE-287 | remove | **operator-only**: CLI `--comparand` syntax. It reads a dev-session CLI argument on every path; the CLI has no in-tree caller and no doc names it (PE-288's grep). (a) Leaf gain: none; no leaf reaches the CLI. (b) No workflow breakage: the CLI would print a traceback instead of the message. Goes with the CLI if the operator removes it (PE-288). | CrossTargetCliTest |
| PE-288 | operator-diag? | CLI output (stdout JSON, exit code). Human-only reader ("the offline measurement of issue #255"). Removing the CLI (~75 lines, L5422-5503) is the operator's call. | CliTest, CrossTargetCliTest (delete with it) |

## Defects
None. The one candidate, PE-049 (a duplicated case_id), was settled by a run: Compile.static refuses it (see PE-049).

## Removal groups

All groups touch `tools/primary_evidence.py`, a whole-file member of `execute_tuple` and `verdict_tuple` (F3), so each PR needs a re-pin-or-bump decision. G1–G4 do not change which IRs the workflow admits (each removed copy is masked on every workflow path), which argues for a re-pin. G5 (PE-207) widens what Compile admits, so it needs the bump decision explicitly.

- **G1: Evaluator re-checks of Compile-gated predicate fields** (merge to the gate). PE-043, 045, 057, 058, 059, 060, 061, 066, 067. Also the masked members PE-032 "case has no case_id" and PE-044 non-finite (remove, needs-run). Coverage argument F1 (`_ir_certification` re-runs the current validator on adoption and resume; fresh IRs pass Compile.static). The one uncovered path is the dev CLI. After removal, it gets a KeyError or TypeError traceback instead of a message on a malformed IR. Note that `exempt_once` / `predicate_reads_state` / `unread_scope_cases` remain gate-only after this. About 50-60 production lines; about 12 test rows/tests change or go (EvaluationTest::test_malformed_predicate_shape_raises, test_value_map_needs_every_case_and_a_number, test_binds_evaluate_in_order_and_cannot_look_forward rows, the four "evaluator … too / past the gate" tests).
- **G2: `_evaluate_case` copies of gates (f)/(g)** (merge to the gate). PE-156, and the members of PE-150 (two call sites), PE-151 (initial_state mapping), PE-157 (operation-only literal) and PE-158 (derived type). Coverage: at Compile, (c) runs only after (f)/(g) are clean (L5174). Every runtime caller has a certified IR, and run_configuration overrides change inputs only. About 25-30 lines; CaseInputsTest rows move to the (f)/(g) text.
- **G3: Duplicated definitions shared with runner_ir** (import instead). PE-001 (set + reason function), PE-114 member `LOOP_KEYS`, PE-115 `_IDENT`, PE-133 member `_DRIVE_KEYS`, PE-191 `PROCEDURE_FUNCTIONS`. runner_ir imports nothing from PE, so there is no cycle. About 25 lines plus 3-4 agreement tests deleted. PE-080's and PE-228's message text changes to runner_ir's reason text.
- **G4: Masked or duplicated single checks**: PE-166 (raises in `_authored_json`), PE-236 (gate (a) spec_id), PE-231 (→ vps:9973; adjust the vps call site), PE-170 (→ vps:9163). About 20 lines.
- **G5: Retired key**: PE-207 (`source` refusal, no reader). About 10 lines plus 3 tests. Note: an IR carrying `source` that `_ir_certification` currently refuses would become adoptable. It reads the same otherwise.
- **G6: Verdict-record re-check pair** (cross-group, with verdict_evaluator; PE-109 is classed keep:break until that group decides). PE-109 `record_scalar_pairing` and the record fields `scalar_pairing` / `cases_read` (PE-063 members) go only together with verdict_evaluator's `_check_scalar_pairing` (:111-142) and its `cases_read` check (:247-250). Those are host twins of gates PE-189 / PE-110 over records derived from the same gated IR. Decide in verdict_evaluator's group first. About 40-50 PE lines.
- **G7: Operator-diag, the operator's call**: PE-020 / PE-021 (`evidence` hash in the operator-only cross_target file), PE-063 member `grammar_version`, PE-288 (the dev CLI `main`, about 75 lines, with PE-284..287, its operator-only argument refusals).
- **X: Cross-module, not a PE removal.** PE's drive reader (`_parse_drive` / `parse_binding` / `parse_distribute` / `_parse_entry` / `_model_from_block`) and runner_ir's render readers (`drive_block`, `drive_binding`, `drive_distribute_axes`, `_drive_call`, `drive_state_classes`, `distribute_layouts`) are two readers of one IR block, held equal by DriveAgreementTest and test_the_two_readers_of_a_procedure_binding_agree. The PE gate copies are the survivors (F2). The runner_ir render refusals that duplicate them (PE-080, 116-137, 192-197, 201, 210, 214-215, 240, 245-247, 254 twins) are masked on every workflow path and belong to runner_ir's classification. Unifying the readers would also retire PE-225, PE-272 and the agreement tests. That is a design change of several hundred lines, not a check removal.

## Tally (recomputed from the table)
keep:leaf 83 · keep:break 165 · merge 15 · remove 7 · operator-diag? 3 · defect 0 · undecided 2 · pointer 13 = 288
