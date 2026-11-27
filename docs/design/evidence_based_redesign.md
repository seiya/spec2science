# Evidence-based redesign: from a `spec` and its verification set to code optimized per target

Status: **proposed**, in part adopted. Recorded 2026-11-26. [Issue #724](https://github.com/seiya/spec2science/issues/724), closed 2026-11-27 (PRs #731 to #735, pin `4cbeb701`), implemented the part §Decision Criteria assigns to it: the generator reads `controlled_spec.md`, `Generate.verify` reviews the source against it (stage C), and `Validate.judge` is deleted (§Relation, A4). The removal of the IR's `algorithm` section (§Relation, A5) is open as [issue #737](https://github.com/seiya/spec2science/issues/737). Stages B and D are neither implemented nor adopted. It states the design the measurements of the R8 A-1 experiment support when the existing implementation is set aside, and names what those measurements do not cover. [Issue #724](https://github.com/seiya/spec2science/issues/724) carries the part of the same evidence that is already decided (three changes inside the existing phases); this note does not change that decision.

## Purpose

Define the pipeline that turns a human-authored `controlled_spec.md` and `tests.md` into certified code optimized for each target profile, using only what was measured, and state for every element the measurement it rests on or that it rests on none.

## Scope

- In scope: the stages between the human-authored inputs and a certified artifact per target, which role an `LLM` holds in each, what judges each output, and where a finding is routed.
- Out of scope: the runtime substrate, the leaf sandbox, the configuration of providers, and the `Tune` / `Promote` flows.
- This note is a second reference point beside `zero_base_architecture.md`; §Relation states where the two agree and differ. It supersedes no section of that note.

## Evidence

The measurements are from the R8 A-1 experiment and its three follow-ups: framework `49dd82c9`, two tasks (a Kessler warm-rain column with its saturation dependency; `shallow_water2d` with its three components), one CPU target, an independent evaluator per task with public and held-out cases that no leaf reads. The experiment's repository is local and is not part of this repository. Where a figure is posted, the row cites the comment; the other rows are recorded here for the first time. The ids in parentheses are the experiment's own.

| # | finding | measurement |
|---|---|---|
| E1 | A general coding agent given the `spec` closure writes correct code. Every judged delivery was correct: 44 of 44 against an independent evaluator or the original model code, a three-dimensional dynamical core included (3 of 3 agree with the original to 1.8e-13). | (R8, F3, D4, D5, D6) |
| E2 | A restatement of the algorithm between the `spec` and the generator is not needed, and reviewing against it rejects correct code. Without the IR: 4 of 4 runs pass every level and the evaluator. With an IR whose `algorithm` section was made wrong: the source follows the `spec` in 3 of 3. The IR-relative review rejected 21 of 25 correct independent sources. | #724 body; [comment](https://github.com/seiya/spec2science/issues/724#issuecomment-7155932379) (M1, M13, F1) |
| E3 | The deterministic levels detect what the authored cases exercise and nothing else. 84 of 86 effective one-site mutants detected; a defect on an input that no case of `tests.md` exercises was in the source of 3 of 5 baseline `shallow_water2d` runs and passed every level in each. | #724 body; [comment](https://github.com/seiya/spec2science/issues/724#issuecomment-7176910105) (D2, R8) |
| E4 | Tests derived from the `spec` by a leaf that reads no implementation match the existing test contract. Four derived suites: no false failure on 100 suite and correct-delivery pairs; every mutant the deterministic levels detect is detected, and one more; under half the cost of `Compile`. With one generic instruction on degenerate inputs the suites also detect the defect of E3 in 3 of 3 deliveries. | (M2, M3, M8) |
| E5 | A review relative to the `spec` holds a class no test holds. It names 21 of 21 injected defects, among them the 2 that escape every deterministic level, and the natural defect of E3 in 3 of 3 deliveries on two model families. In end-to-end runs it stopped that defect in 2 of 2 first sources that carried it. Without its paragraph on degenerate inputs it named the defect in 0 of 3. | #724 body; [codex comment](https://github.com/seiya/spec2science/issues/724#issuecomment-7162102847) (D3, M7, M8, M10, M15, M16) |
| E6 | What judges can itself be wrong. A certified IR predicate carried a `bind` its expression does not read; the bind failed to evaluate and a correct source was refused. Of the defects the baseline's `LLM` reviews found, all 3 were in the harness. | [issue #725](https://github.com/seiya/spec2science/issues/725) (M11, M12, D1) |
| E7 | A defect of the `spec` that is an ambiguity or a missing test is not found downstream: `Compile` passed its gate in 4 of 4 runs over 2 such `spec` documents and named neither defect. A contradiction is found: `Compile.generate` stopped before generation on a `tests.md` still calibrated for a scheme §5 no longer names. One reviewer finding, a periodic wrap for a ghost width larger than the extent, recurred in 3 of 5 runs because the `spec` neither refuses nor defines that input. | (D4, WP5, M11, M16) |
| E8 | A generator that reads the `spec` still writes the defect of E3: 2 of 5 first sources of the `problem` node. The review of E5 is what stopped it. | (M16) |
| E9 | `Validate.judge` named no real defect in the baseline's certified runs, and 9 end-to-end runs without it certified no wrong source. | #724 body (D1, M11, M15, M16) |
| E10 | A misreading made in `Compile` did not reach a certified source in 6 runs at the pin and 6 with the generator reading the `spec`. `Compile.verify` named the injected misreading in 5 of 5 replays. Where the IR's predicates were wrong, the run stopped at `Validate.execute` after `Generate` and `Build`. | [comment](https://github.com/seiya/spec2science/issues/724#issuecomment-7158980537) (M13, M14) |

Not measured: optimization for a target. No measurement of the experiment concerns performance, the quality of a lowering, a distributed-memory target or an accelerator. Stage D below is derived from E1, E3 and E6 by argument.

## Design Policy

1. The `spec` is the only statement of the algorithm. No stage restates it for another stage to read (E2).
2. What judges is independent of what generates. The generator does not read the tests that certify it.
3. Correctness is established once per `spec` version, on a reference implementation. A target variant is certified by agreement with the reference and by measured performance.
4. An `LLM` judgment is used only for what no test can observe, and it is stated relative to the `spec` (E5, E9).
5. Every finding has a destination: the code, the tests, or the `spec` (E6, E7).

## Terms of this note

These terms are local to this note and are not entries of `docs/GLOSSARY.md` until the design is adopted.

- **reference implementation**: the generated source of a node written for portability and legibility, with no target-specific optimization.
- **development suite** and **certification suite**: two test derivations of one node, made independently from the same inputs. The generator reads the first and never the second.
- **target variant**: the generated source of a node for one target profile, derived from the reference implementation.

## Pipeline

```
controlled_spec.md + tests.md (human-authored)
   |
   +- A. contract extraction (deterministic)     interface; the cases of tests.md
   |
   +- B. test derivation (LLM, twice, independent)
   |      development suite    -> read by the generator
   |      certification suite  -> never read by a generator
   |
   +- C. reference implementation (LLM)
   |      judged by: both suites, the oracle where declared,
   |                 a bounds-checked build, the spec-relative review
   |
   +- D. target variant, per target profile (LLM)
   |      judged by: agreement with the reference, the certification suite,
   |                 thread and rank sweeps, measured performance
   |
   +- E. certification record (content-addressed, per node and target)
```

### A. Contract extraction

- The calling interface is taken from the machine-readable interface block of the `spec` by the host. No `LLM` takes part.
- The cases of `tests.md` are taken as they are authored.
- No document restating the algorithm is produced (E2).
- Stage A holds no `LLM`, so the one stop of E7 before generation, made by `Compile.generate` on a contradiction between `tests.md` and the `spec`, has no counterpart in this pipeline. A general agent reported the same contradiction after generation in 3 of 3 runs (F2), so stage C reaches it after generation. A check before generation is undefined and is not part of this design.

### B. Test derivation

- A leaf that reads the `spec` and `tests.md`, and no implementation, writes the tests.
- Every case of `tests.md` is present in each suite; the host checks this by case id.
- For every guard the `spec` states, each suite holds refusal cases at every operation the guard can reach, the top-level one included, with degenerate values of each argument the guard concerns: zero and negative extents, counts and step sizes, the smallest valid value and the largest invalid value (E3, E4).
- Two suites are derived independently. A disagreement between them on one source is a finding about the tests or about an ambiguity of the `spec`, not a refusal of the source (E6).
- A case records its origin. A failure on a case authored in `tests.md` is a refusal. A failure on a derived case alone goes to adjudication.
- The detecting power of a suite is measured by host-made mutants of the reference implementation.
- The form of a derived test is a declarative predicate that the host evaluates over state the host captured, as the current test contract is. The suites of E4 were free executable code; whether the declarative form keeps their detection is not measured.

### C. Reference implementation

- The generator reads the `spec`, the interface, the development suite, and has build and run operations.
- Four things judge the source: both suites; the oracle where the node declares one (on the Kessler task the existing deterministic levels detected 33 of 35 effective mutants with it and 31 without, M2); a bounds-checked build (it aborted on an out-of-range halo read, D2); the spec-relative review with its paragraph on degenerate inputs.
- A review finding takes one of two routes. A disagreement between the source and what the `spec` states is repaired by regeneration, within a bounded number of attempts. A statement that the `spec` neither refuses nor defines an input is recorded against the `spec` and does not stop the run (E7).

### D. Target variant

- The generator reads the reference implementation and the target profile.
- The judgment is deterministic:
  - agreement with the reference implementation on the cases of `tests.md` and on host-generated inputs drawn from the valid domain the `spec` states;
  - the certification suite;
  - runs at several thread and rank counts;
  - measured performance against the reference implementation.
- No `LLM` reviews a lowering plan. In the baseline that review was the most frequent ground for rejecting correct code: 10 of the 21 rejections of correct independent sources cited only the lowering-plan criterion G6 (F1), and the pinned review on a second model family rejected 12 of 12 correct rows with 23 of its 35 findings against the lowering plan (M15).
- The cost of adding a target is a generation and deterministic checks; the judgment of correctness is not repeated.

### E. Certification record

- Certification stays content-addressed per node and target, and the derivation key decides what is re-derived after a change. This mechanism decided correctly in the experiment and is kept: after a scheme change, the five unchanged dependencies were reused without a leaf launch and only the changed nodes were derived (WP5).

### Roles and isolation

- Four `LLM` roles exist: test derivation (two instances), reference generation, spec-relative review, target-variant generation. Each is defined by a prompt and a work tree, on every declared provider.
- A generator can neither read the certification suite nor write a file that judges it. This closes the `leaf shortcut` of fitting a source to the cases that certify it.
- The harness is certified infrastructure selected by the target profile, as today.

## Relation to `zero_base_architecture.md`

| `zero_base_architecture.md` | this note |
|---|---|
| A1, content-addressed derivation | kept (stage E) |
| A4, the verdict is computed by the host from evidence and predicates | kept. The predicates are derived twice and one derivation is withheld from the generator |
| A4, the `LLM` judge as an advisory reviewer | removed (E9). The one `LLM` review is the spec-relative review of the source |
| A5, the semantic IR carries the algorithm structure as a generation input | removed (E2). The interface and the cases remain |
| A5, the target lowering plan is validated | recorded only; not reviewed |
| A4, `cross_target` variants validate each other | narrowed: one variant is the reference, certified before the others, and agreement is also tested on host-generated inputs |
| A6, the harness as certified infrastructure | kept |

## Decision Criteria

Adoption of a stage requires the measurement named for it.

| stage | measurement required before adoption |
|---|---|
| B | derived tests in the declarative form, inside the pipeline and per node: no false failure on correct sources, detection not below the current test contract, the adjudication of a disagreement between two suites, the connection to an oracle and to the runner format and bounds-checked build of `Validate.execute` |
| B | the cost of the mutant measurement and the level at which a suite is accepted |
| C | none beyond issue #724, which measures the generator and the review inside the existing phases |
| D | agreement tolerances over long integrations and on nodes other than the Kessler column, whose four operations were measured against the original at five optimization settings and against an independent implementation: the correct implementations reached at most 0.050 of the band, every variant the band detected exceeded it, and a fixed relative bound of 1e-12 failed a correct implementation near saturation; a distributed-memory target; an accelerator target; that measured performance separates a good variant from a poor one |
| all | a second model family for every role; a task count above two |

## Not verified

- The paragraph on degenerate inputs was written after the defect of E3 was known. It shows that the class is closed, not that an unknown class is anticipated.
- No means of finding an ambiguity or a missing test in the `spec` before generation was found (E7). The general agent was not asked to review the `spec` while `Compile` was (D4), so the result bounds the roles as run and not a role assigned to review the `spec`. The design routes such findings back; it does not detect them earlier.
- The strictness of the spec-relative review differs between model families: it rejected 0 of 12 correct rows on one and 4 of 12 on the other. Each of the 4 rejections carries at least one substantiated finding about an input no test exercises; 2 of the 8 findings are unsubstantiated.
- Counts are small: one to five per cell, two tasks, one CPU target.
- Every `spec` of the two tasks was matured through runs of the existing pipeline, and the two SCALE nodes were authored by an agent. A run on an immature `spec` was planned and not made (F4).
- The evaluators, the mutants, the independent implementations and the scientific review were all written by one `LLM` family, with no human review. The mutants were designed against the existing pipeline's code and tests, and each mutant was run on its own node only.
- "Correct" means correct against the independent evaluator. The derived suites flagged 3 mutants that the evaluator judges equivalent to the original; each deviates from a stated requirement the evaluator does not check (M2).
- Inside a producing leaf only the wrapper's tool calls are recorded; a correction the leaf made between two calls is not observable (D1).
