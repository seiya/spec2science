---
name: spec-from-code
description: Use this to draft a node's `spec` (`controlled_spec.md` / `tests.md` / `deps.yaml`) and its `oracle` from existing code, and to triage an `oracle_disagreement`. It is the conversion flow of the `Spec` phase: an operator runs it, alone or with an interactive coding agent, outside orchestration. The drafted files are the person's `spec`; the workflow's input is unchanged.
---

# Spec From Code

## Purpose
Turn procedures of existing code into one `node` the workflow can certify, and declare the code as that node's `oracle` (`docs/SPEC.md` requirement 17), so `Validate.execute` compares the generated variant with it. This is a first step of conversion ([issue #680](https://github.com/seiya/spec2science/issues/680)): a procedure, measured by its first uses, and no tool. Item 2 of `docs/design/scale_migration.md` §Order authored the two microphysics nodes this way (`docs/design/kessler_measurement/README.md`); they are the worked example of every step below. The conversion trial of that issue drafted `pbl_tracer_diffusion_1d_column` from one procedure with this flow; it is the worked example of the steps that cite it.

This flow is the `Spec` phase (`docs/workflow/phases/phase_00_spec.md`): no phase of the workflow runs it and no leaf reads it. The person who commits the files is their author.

## Input
- The existing code: its repository and revision, and the procedures to take.
- The language of the targets the node is to certify on, which is the oracle's `language`.

## Canonical sources
This skill restates none of the formats. Each step names the document that is canonical for it:
- `docs/CONTROLLED_SPEC.md`, `docs/TESTS.md`, `docs/SPEC.md` — the three files, the catalog, and the `oracle` declaration (requirement 17) and `external library` declarations (requirement 16).
- `docs/backends/language/<language>/ORACLE_ADAPTER.md` — what an adapter in that language is. A language without one has had no oracle yet: write it with the first.
- `docs/RUNBOOK.md` §Certifying a node against its oracle — the site entry, the record, the triage.

## Procedure
1. **Fix the node boundary.** From the procedures taken, follow the module dependency closure of the existing code. A procedure the node publishes is one the caller needs; a procedure only it calls is inside the node or is another node it depends on (`deps.yaml`). Module state, setup routines, log and history output, and the termination of the program are not published: each becomes an argument, a refusal state, or an output argument, and each is a recorded difference (step 2). Read the callees too, down to the code that indexes the arrays: a source can read outside its own work array on some input range, and that range becomes a refusal in §4 and a difference in §1 (the trial node: the source's tridiagonal solve reads below its work array on a column of two interior cells, so the node refuses `ke < ks+2`).
2. **Write `controlled_spec.md`.** §1 names the source (repository, revision, files, procedures) and lists **every difference from the source**: what is out of scope, the array form, constants that become arguments, refusals the source does not make, values the source leaves undefined. §3 states the operation from the code as executed, including the order of steps where the result depends on it; §4 the refusals; §5 and §5.1 the published surface. §5.1 `dims` gives each dimension of an array one bound expression, its extent (`docs/CONTROLLED_SPEC.md`, the structured signature block), so an array whose lower bound is not 1, or that carries a dimension the node does not take, is shifted or split, and the shift or split is a difference in §1 (the trial node: a face-height array starting at index 0 is shifted to start at 1, and a two-column weight array is split into two arrays).
3. **Write `tests.md`.** The checks are independent of the code: analytic values, conservation, convergence, the refusals of §4 (`docs/design/scale_migration.md` §Constraints on the new features: the comparison establishes equivalence, not correctness). Run the existing code on each `pass` case before relying on it: a judgment false of the original is a finding on the `spec` or on the original, decided before any billed run. Every `pass` test applies the cross-target judgment (`docs/TESTS.md` §Cross-target judgment); its cases are the cases the oracle is compared on. Choose its normaliser per variable: the default divides by `max(maxabs(q), maxabs(q_ref), 1)`, so on an output far below magnitude 1 the bound becomes absolute and the comparison, with the oracle and between targets, reads almost nothing, and on an output whose round-off is amplified it can fail correct code (`docs/design/kessler_measurement/README.md` §Two comparisons, measured). State a scale for each variable from the case's own inputs, and choose it below the magnitudes the `pass` cases produce, so that the bound stays relative to the outputs and is not fixed by the floor (the same section: a node that takes the per-variable normaliser chooses a floor below the outputs it compares); on a case whose outputs are rounding only the floor is all the bound reads, so it also stays above that rounding by the bound's margin. `tests.md` §5 of `pbl_tracer_diffusion_1d_column` states such a scale from its inputs but not below its outputs: its floors exceed them 12 to 231 times on the cases of its certified run, so it shows the form and not the choice of the scale.
4. **Declare the existing code as libraries.** Build the code the adapter calls, with stubs for the modules the boundary cut, into a library per distinct build, and write `spec/libraries/<library_id>.yaml` for each (`docs/SPEC.md` requirement 16). Code that holds a value the cases vary as a compile-time constant needs one library per value set. Record how to rebuild each library in a measurement directory under `docs/design/`, with its placement exception (the precedents: `docs/design/kessler_measurement/` and `docs/design/pbl_tracer_measurement/`); the files themselves are the operator's, at the site. Build each library into a directory that holds nothing else, and a driver of step 7 elsewhere: a driver built there leaves the adapter's compiled interface, which carries the generated model's name, in the library's include directory beside the generated model's.
5. **Write the oracle.** `oracle/oracle.yaml` and the adapter, per `docs/SPEC.md` requirement 17 and `docs/backends/language/<language>/ORACLE_ADAPTER.md`. One `builds:` entry per library set; the entry that omits `cases` takes every compared case the others do not name, and when every entry lists `cases`, every compared case must be in one of the lists. A compared case that is a continuation case (the `restart` line of `docs/TESTS.md` §Description format) cannot be compared: the oracle runs no second pass, and the comparison stops as `oracle_unevaluable`. A node whose compared cases include one does not declare an oracle.
6. **Check the input.** Run `skills/spec-input-check` on the node; its check of the `oracle/` directory reads the declaration against `tests.md`.
7. **Build the adapter once by hand** against the libraries and run it on the cases: an adapter that does not compile, or a judgment false of the original, costs nothing here and a billed run there. With the hand transcription of `tests.md` into an IR (Output), `tools/tests/data/drive_ir/pr3_evidence/verify_spec.py` renders the host driver with the adapter as the model, runs the cases and evaluates the judgments (`docs/design/pbl_tracer_measurement/README.md` §Operations); a driver of your own does the same without an IR, as `docs/design/kessler_measurement/` did for item 2. Its `--library <library_id>=<dir>` locates each library in its build directory, as a site entry naming that directory would, and its `--compared-only` runs only the cases the oracle is compared on and judges only the tests whose cases are all among them, since an original without a guard does not survive a refused case; `--flag=<flag>` builds the adapter at another optimisation level, against a library built at that level. No wrapper and no guard is added. It serves the targets of one language.
8. **List the libraries at the site and run the workflow** (`docs/RUNBOOK.md` §Certifying a node against its oracle).

## Triage of an `oracle_disagreement`
The run ended at `Validate.execute`; `Compile` and `Generate` stay certified, and `oracle_check.json#comparison.failures` names the case, the quantity and the value. The host cannot tell which side is wrong. Take the causes in the order a freshly converted node reaches them:
1. **The adapter or the declaration**: an argument in another unit or array form, a setup step on first use, a case routed to a build with the wrong library. Fix it and `--resume`: `Build` and `Validate` re-derive, `Compile` and `Generate` do not.
2. **The `spec`**: a behaviour the `spec` states otherwise than the code does. Fix `controlled_spec.md` or `tests.md`, bump `spec_version`, and run again.
3. **The generated code**: `--rederive generate`.

A difference that is intended in a compared case has no declaration: the case plan takes every compared case. Remove the node's oracle declaration. Never drop the cross-target judgment from a test to take its cases out of the comparison — it also compares two declared targets — and never widen its bound.

## Output
- `spec/<spec_kind>/<domain>/<family>/<spec_id>/` with `controlled_spec.md`, `tests.md`, `deps.yaml` and `oracle/`, and the catalog entry (`spec/registry/spec_catalog.yaml`).
- `spec/libraries/<library_id>.yaml` for each library, and the record of how each is built (step 4).
- The repository work a new physics node needs, each held by the suite or by a document:
  - the hand transcription of `tests.md` into an IR, `tools/tests/data/drive_ir/pr3_evidence/ir/<spec_id>.yaml`, which `test_each_physics_node_has_a_transcription` (`tools/tests/test_spec_corpus_versions.py`) requires of every physics node;
  - the row of the `spec` table in `README.md`, which `tools/tests/test_readme_sync.py` compares with the catalog and the tree;
  - the counts of physics nodes and `tests.md` files the suite pins (search `tools/tests/` for the current count);
  - each library's site entry in `docs/RUNBOOK.md` §Certifying a node against its oracle;
  - for a node that lives in this repository only until its source's nodes move out, the `TODO.md` item that carries that removal condition.

## Decision Criteria
- Every difference from the source is listed in §1 of `controlled_spec.md`.
- Every `pass` case was run through the existing code, and every judgment is true of it.
- The adapter publishes the §5.1 surface and builds against the declared libraries.
- `skills/spec-input-check` reports no `blocker`.
