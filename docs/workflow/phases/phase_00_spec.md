# Phase 0: Spec (manual)

## Overview
The phase that manually writes the `Controlled Spec`, `tests`, and `deps` and establishes the starting point of the core workflow. It is not an LLM-using phase and is outside the scope of orchestration.

The files may be drafted from existing code by the conversion flow (`skills/spec-from-code`); the person who commits them is their author, and the flow is outside orchestration as this phase is.

## I/O contract
- execution input: the requirements, physics requirements, and dependency-selection policy given outside the workflow
- verification input: none
- output:
  - `spec/<spec_kind>/<domain>/<family>/<spec_id>/controlled_spec.md`
  - `spec/<spec_kind>/<domain>/<family>/<spec_id>/tests.md` (every `spec_kind` but `profile`, which has none — `docs/SPEC.md` req. 5)
  - `spec/<spec_kind>/<domain>/<family>/<spec_id>/deps.yaml`
  - optional: `spec/<spec_kind>/<domain>/<family>/<spec_id>/oracle/`, the declaration of existing code the node's generated variant is compared with, and its adapter (`docs/SPEC.md` requirement 17 is canonical)

## Required requirements
- Define the intent of the physics algorithm (A) in the `Controlled Spec`.
- A `problem spec` declares its adopted `profile` in `deps.yaml`, together with any `component` no adopted `profile` selects. A `component` has exactly one source: one an adopted `profile` selects is not declared again here.
- `tests.md` defines the experiment conditions, judgment conditions, and the required evidence per `test_id`. A `profile spec` authors none: nothing is generated or executed for it.
- The natural-language notation is the canonical source, and the `Compile` phase integrates it into the structured IR (`spec.ir.yaml`).
- The `spec_version` of `controlled_spec.md` is a required record. When updating a spec, update `spec_version`.

## Connection to later stages
- The `Compile` phase takes `controlled_spec.md` + `tests.md` + `deps.yaml` + `spec/registry/spec_catalog.yaml` as input and generates `spec.ir.yaml`.
- From `Generate` onward, `controlled_spec.md` is read at `Generate` alone: `Generate.generate` takes the algorithm from it and the calling interface, the cases and the I/O contract from `spec.ir.yaml`, and the model `Generate.verify` reviewer judges the source against it and `tests.md` ([issue #724](https://github.com/seiya/spec2science/issues/724); `docs/workflow/phases/phase_02_generate.md`). `Build` and `Validate` read the IR and the captures, not `controlled_spec.md`.
- A specification change is expressed by updating one of `controlled_spec.md` / `tests.md` / `deps.yaml`, and a specification must not be changed by an implementation-side modification alone (the Spec-First principle).
