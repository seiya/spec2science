# Item 4 acceptance measurements V-a to V-e

## Status
The in-tree record of the acceptance measurements V-a to V-e of [issue #680](https://github.com/seiya/spec2science/issues/680) (item 4 of `docs/design/scale_migration.md` §Order: the `oracle` and the conversion flow `skills/spec-from-code`). Each section states what was run and where its evidence is. Nothing in this directory belongs to a suite. The billed run R-O, its costs and V-a and V-c are also on the issue's [R-O comment](https://github.com/seiya/spec2science/issues/680#issuecomment-7000499526), which holds no other measurement.

## V-a: both microphysics nodes certified `agree`
Billed run R-O, 2026-11-19, main `e27b6b1c`, `fortran_cpu`: `component/microphysics_saturation_liquid_1d_column@0.1.0` and `component/microphysics_kessler_warm_rain_1d_column@0.1.1`, every phase at attempt 1. Evidence: the run's `oracle_check.json` of each node, under `workspace/pipelines/component__microphysics_{saturation_liquid_1d_column__0.1.0,kessler_warm_rain_1d_column__0.1.1}/fortran_cpu/*_20261119_001/runs/run_20261119_001/*/oracle_check.json` (the operator's workspace; not in the repository). Read from those files:

| node | `status` | records | failures | largest value-to-bound ratio | at |
|---|---|---:|---:|---:|---|
| saturation `0.1.0` | `agree` | 30 | 0 | `1.2e-2` | `l0_satadj_alt_supersat` |
| Kessler `0.1.1` | `agree` | 76 | 0 | `2.7e-2` | `l0_supersaturated` |

The ratio is `comparison.worst[].value / rhs` of `cross_target_state_agreement`, the bound being `1e-12`.

## V-b: what each comparison reads
`vb_comparison_gap.py` in this directory, run without an `LLM` on the certified pipelines of R-O; its whole output is `vb_comparison_gap.out.txt`. The usage is in the script's docstring. `docs/design/kessler_measurement/README.md` §Two comparisons, measured states the result.

## V-c: both builds of each oracle compared
Read from the same `oracle_check.json` files, `builds[]`: saturation compares its `default` build on 12 cases and its `alt` build on 3, and Kessler its `default` build on 23 cases and its `alt` build on 2. Each build's `binary_ref` is under that pipeline's `binary/bin_20261119_001/bin_oracle/<build id>/`.

## V-d: an adapter edit moves `Build` and `Validate` only
Run 2026-11-19 at `0dfbd393` on the orchestration of R-O, `orch_20261119T042148Z_b5542a7c`. The Kessler adapter `spec/component/microphysics/kessler/microphysics_kessler_warm_rain_1d_column/oracle/adapter.f90` was copied to a backup with `cp -p`, the line `! V-d: an edited comment` was prepended to it, and for each phase this command was run:

```sh
python3 tools/orchestration_runtime.py check-phase-certified --repo-root . \
    --orchestration-id orch_20261119T042148Z_b5542a7c \
    --node-key component/microphysics_kessler_warm_rain_1d_column@0.1.1 --step <phase> --no-record
```

| phase | edited adapter | restored adapter |
|---|---|---|
| `compile` | `certified: true` | `certified: true` |
| `generate` | `certified: true` | `certified: true` |
| `build` | `certified: false`, `derivation_key_mismatch:oracle.source` | `certified: true` |
| `validate` | `certified: false`, `derivation_key_mismatch:oracle.source` | `certified: true` |

The file was restored from the backup with `cp -p`, and `cmp` found it identical to the backup before the second column was read.

## V-e: the conversion trial
The node `pbl_tracer_diffusion_1d_column` (`spec/component/pbl/diffusion/pbl_tracer_diffusion_1d_column/`), converted from `ATMOS_PHY_BL_tendency_tracer`, lines 55 to 261 (207 lines) of the 263-line file `scalelib/src/atmosphere/physics/pbl/scale_atmos_phy_bl_common.F90` of `scale-met/scale-dev` at `dc58155`.

- Authoring: an agent with `skills/spec-from-code`, 2026-11-19 14:23 to 14:50 JST, about 27 minutes of wall clock, through steps 1 to 7 of the skill (the hand build of the oracle and a run of every case on the original included, the refused ones through a guard inserted before the original and not committed; the committed route, `docs/design/pbl_tracer_measurement/README.md` §Operations, runs the compared cases). The times are the operator's session record; no file in the repository carries them.
- Input check: `skills/spec-input-check`, run by a second agent: 0 blockers and 2 warnings, both fixed. The counts are the operator's session record; no file in the repository carries them.
- Review before the billed run: one round by one reviewer: 0 high, 0 medium and 5 low findings, 3 of them fixed (the adapter writes the halo explicitly, the wording of the name `D` in `tests.md` §5, and the sentence of `controlled_spec.md` §4 that the index clauses are decided first). The counts are the operator's session record, as the input check's are; the three fixes are in the node's files.
- Billed run `orch_20261119T055500Z_6a8a7350`, 2026-11-19, on the tree of commit `aa9d90e6`, `fortran_cpu`: every substep at attempt 1; $5.63, the sum of `cost_usd` over the 5 leaves of `tools.audit_orchestration.collect_token_cost_summary` on its `agent_runs.jsonl`; `oracle_check.json#status: agree`, 16 records, worst value `3.2e-16` against the bound `1e-12` (`l0_uniform_tracer`), one `default` build on 8 cases.
- The skill gaps the trial found, each fixed in `skills/spec-from-code`: the repository files a new node needs, how to run the judgments on the original, where the build record goes, the cross-target normaliser, an array with another lower bound, and a source that reads past its own work array.

**That certification does not stand at the head of this branch.** Round 1 of the pull request's review found no `pass` case with a negative surface flux, and commit `5d7502c7` added one to `tests.md` §2 under `spec_version` `0.1.0`, which moves the node's `Compile` key. Round 2 changed `controlled_spec.md` §4, which now refuses `ks < 2`, and added and changed cases of `tests.md` §2, under the same `spec_version`. By the operator's decision of 2026-11-19 the node's certification is re-taken by a billed run once the review converges, at the final head; until then the node is certified on the tree of `aa9d90e6` only.

The hand IR `tools/tests/data/drive_ir/pr3_evidence/ir/pbl_tracer_diffusion_1d_column.yaml`, over which `verify_spec.py` runs the judgments on the original and on a model outside the workflow, is a hand transcription of the node's `tests.md` regenerated from it by a builder script local to the authoring session; that builder is not committed. The convention of `pr3_evidence/ir/` is a hand transcription gated by `Compile.static`'s gates (`verify_spec.py` docstring), and this IR passes them with no finding.
