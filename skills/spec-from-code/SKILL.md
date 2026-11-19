---
name: spec-from-code
description: Use this to draft a node's `spec` (`controlled_spec.md` / `tests.md` / `deps.yaml`) and its `oracle` from existing code, and to triage an `oracle_disagreement`. It is the conversion flow of the `Spec` phase: an operator runs it, alone or with an interactive coding agent, outside orchestration. The drafted files are the person's `spec`; the workflow's input is unchanged.
---

# Spec From Code

## Purpose
Turn procedures of existing code into one `node` the workflow can certify, and declare the code as that node's `oracle` (`docs/SPEC.md` requirement 17), so `Validate.execute` compares the generated variant with it. This is a first step of conversion ([issue #680](https://github.com/seiya/spec2science/issues/680)): a procedure, measured by its first uses, and no tool. Item 2 of `docs/design/scale_migration.md` §Order authored the two microphysics nodes this way (`docs/design/kessler_measurement/README.md`); they are the worked example of every step below.

This flow is the `Spec` phase (`docs/workflow/phases/phase_00_spec.md`): no phase of the workflow runs it and no leaf reads it. The person who commits the files is their author.

## Input
- The existing code: its repository and revision, and the procedures to take.
- The language of the targets the node is to certify on, which is the oracle's `language`.

## Canonical sources
This skill restates none of the formats. Each step names the document that is canonical for it:
- `docs/CONTROLLED_SPEC.md`, `docs/TESTS.md`, `docs/SPEC.md` — the three files, the catalog, and the `oracle` declaration (requirement 17) and `external library` declarations (requirement 16).
- `docs/backends/language/<language>/ORACLE_ADAPTER.md` — what an adapter in that language is.
- `docs/RUNBOOK.md` §Certifying a node against its oracle — the site entry, the record, the triage.

## Procedure
1. **Fix the node boundary.** From the procedures taken, follow the module dependency closure of the existing code. A procedure the node publishes is one the caller needs; a procedure only it calls is inside the node or is another node it depends on (`deps.yaml`). Module state, setup routines, log and history output, and the termination of the program are not published: each becomes an argument, a refusal state, or an output argument, and each is a recorded difference (step 2).
2. **Write `controlled_spec.md`.** §1 names the source (repository, revision, files, procedures) and lists **every difference from the source**: what is out of scope, the array form, constants that become arguments, refusals the source does not make, values the source leaves undefined. §3 states the operation from the code as executed, including the order of steps where the result depends on it; §4 the refusals; §5 and §5.1 the published surface.
3. **Write `tests.md`.** The checks are independent of the code: analytic values, conservation, convergence, the refusals of §4 (`docs/design/scale_migration.md` §Constraints on the new features: the comparison establishes equivalence, not correctness). Run the existing code on each `pass` case before relying on it: a judgment false of the original is a finding on the `spec` or on the original, decided before any billed run. Every `pass` test applies the cross-target judgment (`docs/TESTS.md` §Cross-target judgment); its cases are the cases the oracle is compared on.
4. **Declare the existing code as libraries.** Build the code the adapter calls, with stubs for the modules the boundary cut, into a library per distinct build, and write `spec/libraries/<library_id>.yaml` for each (`docs/SPEC.md` requirement 16). Code that holds a value the cases vary as a compile-time constant needs one library per value set. Record how to rebuild each library beside the node; the files themselves are the operator's, at the site.
5. **Write the oracle.** `oracle/oracle.yaml` and the adapter, per `docs/SPEC.md` requirement 17 and `docs/backends/language/<language>/ORACLE_ADAPTER.md`. One `builds:` entry per library set; the entry that omits `cases` takes every compared case the others do not name. A compared case that is a continuation case (the `restart` line of `docs/TESTS.md` §Description format) cannot be compared: the oracle runs no second pass, and the comparison stops as `oracle_unevaluable`. A node whose compared cases include one does not declare an oracle.
6. **Check the input.** Run `skills/spec-input-check` on the node; its check of the `oracle/` directory reads the declaration against `tests.md`.
7. **Build the adapter once by hand** against the libraries and run it on the compared cases, as the measurement directory's driver and comparison did for item 2: an adapter that does not compile, or a judgment false of the original, costs nothing here and a billed run there.
8. **List the libraries at the site and run the workflow** (`docs/RUNBOOK.md` §Certifying a node against its oracle).

## Triage of an `oracle_disagreement`
The run ended at `Validate.execute`; `Compile` and `Generate` stay certified, and `oracle_check.json#comparison.failures` names the case, the quantity and the value. The host cannot tell which side is wrong. Take the causes in the order a freshly converted node reaches them:
1. **The adapter or the declaration**: an argument in another unit or array form, a setup step on first use, a case routed to a build with the wrong library. Fix it and `--resume`: `Build` and `Validate` re-derive, `Compile` and `Generate` do not.
2. **The `spec`**: a behaviour the `spec` states otherwise than the code does. Fix `controlled_spec.md` or `tests.md`, bump `spec_version`, and run again.
3. **The generated code**: `--rederive generate`.

A difference that is intended in a compared case has no declaration: the case plan takes every compared case. Remove the node's oracle declaration. Never drop the cross-target judgment from a test to take its cases out of the comparison — it also compares two declared targets — and never widen its bound.

## Output
- `spec/<spec_kind>/<domain>/<family>/<spec_id>/` with `controlled_spec.md`, `tests.md`, `deps.yaml` and `oracle/`, and the catalog entry (`spec/registry/spec_catalog.yaml`).
- `spec/libraries/<library_id>.yaml` for each library, and the record of how each is built.

## Decision Criteria
- Every difference from the source is listed in §1 of `controlled_spec.md`.
- Every `pass` case was run through the existing code, and every judgment is true of it.
- The adapter publishes the §5.1 surface and builds against the declared libraries.
- `skills/spec-input-check` reports no `blocker`.
