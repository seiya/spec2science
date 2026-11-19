# Kessler measurement: offline driver and comparison

## Status
A measurement record of [issue #578](https://github.com/seiya/spec2science/issues/578), item 2 of `docs/design/scale_migration.md` §Order. It belongs to no suite and no workflow phase, and nothing in `tools/` reads it. Since [issue #680](https://github.com/seiya/spec2science/issues/680) its `Makefile` also builds the library the two nodes' `oracle`s link (§Oracle library).

**Placement exception.** `docs/DEVELOPMENT.md` §Record placement has no row for measurement code. This directory is the exception, placed beside the note it feeds, for as long as the two nodes it measures live in this repository: `TODO.md` carries the removal condition. Direct compiler calls here are a development measurement; `AGENTS.md` §Build-runtime execution rules binds the workflow's `compile`.

## Source
`scale-met/scale-dev` at commit `dc58155` (SCALE 5.5.5). The tree is read only, and nothing is copied from it into this directory. The real files are `scale_precision`, `scale_const`, `scale_atmos_hydrometeor`, `scale_atmos_saturation`, `scale_atmos_phy_mp_common` and `scale_atmos_phy_mp_kessler`, plus `scalelib/include`.

## Contents
- `Makefile`: builds the six real files, the six stubs and the driver, or, with the target `lib`, the six real files and the six stubs as one archive (§Oracle library). `SCALE_ROOT` is required; `BUILD` defaults to `build/`. `FC` selects the compiler, and `MODFLAG` its module-directory flag (`-J` by default; `MODFLAG="-module "` for ifort or ifx, which ignore `-J`). `ALT_CONSTANTS=1` builds a copy of `scale_const` holding the suites' `alt_constants`, since the original holds them as compile-time parameters.
- `stubs/`: `scale_io`, `scale_prc`, `scale_prof`, `scale_file_history`, `scale_tracer` and `scale_atmos_grid_cartesC_index`. `scale_file_history` keeps the one field the Kessler adjustment writes, which the driver publishes as `pcsat`. `scale_prc` turns the abort into an error stop.
- `driver.F90`: runs one column through `dens2qsat_liq`, `saturation_adjustment`, `moist_conversion_dens_liq`, the Kessler adjustment or the terminal velocity, with `IA = JA = 1`.
- `compare.py`: the screen and the comparison. It reads the cases from the one fenced YAML block of each node's `tests.md` §2, so no second copy of them exists.
- `predicates_saturation.yaml`, `predicates_kessler.yaml`: the judgments of each `tests.md` §6, transcribed by hand into the primary-predicate grammar.

## Operations
```sh
make SCALE_ROOT=<tree> BUILD=<dir>/O2
make SCALE_ROOT=<tree> BUILD=<dir>/O0 FFLAGS="-O0 -cpp -ffree-line-length-none"
make SCALE_ROOT=<tree> BUILD=<dir>/O2alt ALT_CONSTANTS=1
make SCALE_ROOT=<tree> BUILD=<dir>/O0alt ALT_CONSTANTS=1 FFLAGS="-O0 -cpp -ffree-line-length-none"
python3 compare.py screen saturation --driver <dir>/O2/driver --comparand-driver <dir>/O0/driver --alt-driver <dir>/O2alt/driver --alt-comparand-driver <dir>/O0alt/driver --work <dir>/work
python3 compare.py screen kessler    --driver <dir>/O2/driver --comparand-driver <dir>/O0/driver --alt-driver <dir>/O2alt/driver --alt-comparand-driver <dir>/O0alt/driver --work <dir>/work
python3 compare.py compare saturation --driver <dir>/O2/driver --alt-driver <dir>/O2alt/driver --run-dir <certified saturation run node directory>
python3 compare.py compare kessler   --driver <dir>/O2/driver --alt-driver <dir>/O2alt/driver --run-dir <certified Kessler run node directory>
```

**Screen.** Every `pass` case goes through the driver, its columns become state snapshots, and `tools.primary_evidence.evaluate_primary_predicates` evaluates each judgment, the cross-target ones against the `-O0` build; `validate_primary_predicate_schema` reads the same predicates against the real test ids. An `xfail` case does not run: the original has no guard, so its snapshot is the refusal state the Controlled Spec defines, written by the script, and its evaluation shows only that the judgment is expressible and evaluable. Forcing is checked with a nonzero exit: a case's inputs meet a refusal clause of Controlled Spec §4 exactly when it is `xfail`, and a non-convergence case aborts in the original at cell 4. The screen establishes that a `pass` judgment is true of the original and evaluable; it does not establish that the original is correct, and it does not show a judgment separates a wrong kernel.

**Comparison.** The same driver columns against a certified run's `raw/state_snapshots/<case_id>.json`, at interior cells, every returned variable: `1e-12` relative with a `1e-15` absolute floor. `evaporate` is compared as `max(-pcsat, 0)`, because the droplet-number factor amplifies a round-off `pcsat`, and `rhoe_t` as `rhoe_t*dt/(dens*L00)`, the mixing ratio it stands for, because `dens*L00/dt` amplifies a round-off `qv` the same way. A difference beyond the tolerance is a finding on the `spec` or on the generated code, never a tolerance change. The script prints the deviation list beside the maxima.

## Oracle library
Each node declares the original as its `oracle` (`docs/SPEC.md` requirement 17): `oracle/oracle.yaml` and the adapter `oracle/adapter.f90` in the node's directory. The adapter publishes the node's §5.1 surface and does per column what `driver.F90` does; it reads none of the constant arguments, so the `constants: alt` cases go to a second build linked with the archive built with `ALT_CONSTANTS=1`. The two archives are the external libraries `scale_mp_oracle` and `scale_mp_oracle_alt` (`spec/libraries/`), built with the compiler the target builds with, since the adapter reads their module files:

```sh
make SCALE_ROOT=<tree> BUILD=<dir>/default lib    # <dir>/default/libscale_mp_oracle.a and its module files
make SCALE_ROOT=<tree> BUILD=<dir>/alt ALT_CONSTANTS=1 lib    # <dir>/alt/libscale_mp_oracle_alt.a
```

The in-workflow comparison reads only what each cross-target judgment names, so it shares the suites' blind spots there. Measured by a reviewer of issue #680 PR-4 at `ce4c651d`, with the certified Kessler model of the `_20261105_001` pipeline mutated to set `rhoe_t` from the unlimited vapour tendency (`dens*L00*(dqc + dqr)` in place of `dens*edot`): the node's own suite passes and the oracle comparison reads `agree` (76 records), although on `l0_limiter_qv` the mutant's interior `rhoe_t` is 2300 to 3000 times the original's. `tests.md` left `rhoe_t` out of the cross-target judgment wherever `qc` is limited, and no other judgment read it on that case. `spec_version` `0.1.1` closes it in the node's own suite: `l0_limiter_positivity_pass` judges `rhoe_t*dt + dens*L00*dv` on `l0_limiter_qv` (bound `1e-12`; computed from captures of the certified `_20261105_001` model on that case: `0`, and `1.7e3` with the mutation above), so such a model fails its own verdict and the oracle is not reached. `compare.py` reads every returned variable (§Two comparisons, measured).

Unmutated, both adapters agree with the certified runs of the `_20261105_001` pipelines under those pipelines' IRs: saturation 30 records (worst `1.2e-14`), Kessler 76 (worst `2.4e-14`). Taken at `33622c2b` and re-taken by reviewers at `ce4c651d` and `bced7da0`, against tag `5.5.5` (`ddc1e1d2d`) of `scale-dev`, and taken again against `dc58155` with Kessler's worst at `2.7e-14` (`dc58155` is on `scale-dev`'s `develop` history, 2026-10-31, and is not among the refs the remote advertises today, so a fresh clone may lack it; four of the six real files differ between the two commits): each adapter compiled in place of the model with that pipeline's runner, the harness of `harness-fortran-cpu_20261104_001` and, for Kessler, the saturation model `src_20261105_002`, under `-std=f2008 -O2 -fopenmp` against the two archives; each build run on its cases of `tools.oracle.case_plan`, the captures merged, and `Conductor._shape_state_agreement(ir, <run node dir>, "oracle", <merged captures>, "json")` called. Those IRs predate `fd895868`, which added the `distribute:` line to each `tests.md` and changed no case or judgment.

The operator lists both under the local site's `libraries:` (`docs/RUNBOOK.md` §Certifying a node against its oracle). The in-workflow comparison reads fewer variables and a looser bound than §Operations' comparison (`docs/design/scale_migration.md` §Order row 4), so both stay (§Two comparisons, measured).

## Two comparisons, measured
Acceptance measurement V-b of [issue #680](https://github.com/seiya/spec2science/issues/680), taken without an `LLM` by `tools/tests/data/drive_ir/item4_acceptance/vb_comparison_gap.py` on the certified pipelines of billed run R-O (main `e27b6b1c`, 2026-11-19); its output, with every row, is `vb_comparison_gap.out.txt` beside it. `compare.py` reads the current §2 format: `compare` exits 0 on both nodes against R-O. `screen` runs and exits 1, because `predicates_saturation.yaml` and `predicates_kessler.yaml` lag `tests.md` §6 by 2 and 3 tests (saturation: `l0_satadj_temp_zero_xfail`, `l0_temp_zero_xfail`; Kessler: `l0_evaporate_closed_form_pass`, `l0_last_cell_invalid_xfail`, `l0_tv_last_cell_invalid_xfail`).

The effective absolute tolerance of each comparison on a captured variable: offline, `max(1e-15, 1e-12*max(|q|, |q_ref|))` per interior cell, with `evaporate` and `rhoe_t` through the transforms of §Operations; in the workflow, `1e-12*max(maxabs(q), maxabs(q_ref), 1)` over the column, on the variables a cross-target judgment names in that case. The factor is the workflow's tolerance over the offline one; above 1 the workflow is looser.

| node | (case, variable) rows read offline | also read in the workflow | workflow looser on some cell | on every cell | largest factor on every cell of a row | largest factor on one cell |
|---|---:|---:|---:|---:|---:|---:|
| saturation `0.1.0` | 60 | 60 | 60 | 40 | `1e3` (`qc`, `rhoe_d`) | `3.5e6` (`rhoe_d`) |
| Kessler `0.1.1` | 183 | 141 | 133 | 108 | `1e3` (`qv`, `qc`, `qr`, `pcsat`, `vterm_c`, `vterm_r`) | `1.1e6` (`cptot`) |

The 42 Kessler rows the workflow does not read are `rhoe_t` on 16 cases, `evaporate` on 13 and `pcsat` on 13. Where a judgment names `evaporate` or `rhoe_t`, the factor is `1.9e-9` to `8.7e-2` and `2.9e-3` to `1.3`: the workflow is tighter there, because the offline transforms loosen those two on purpose, except on `rhoe_t` of `l0_supersaturated`. Evaluated on R-O against the oracle on every row, named or not, the workflow's expression reaches, against its bound `1e-12`: `evaporate` `1.7e5`, `rhoe_t` `1.1`, `pcsat` `4.9e-6`. The default normaliser applied to those two variables would therefore fail correct code by a factor of up to `1.7e5` and `1.1`.

The seeded variants of §What the suites do not separate, seeded into the certified generated model sources of R-O and built with each pipeline's own `Makefile`, runner and harness:

| variant | oracle comparison, saturation | `compare saturation` | own suite, saturation | Kessler: all three |
|---|---|---|---|---|
| none, threshold `1.5e-6`, limit `150` or `1000`, kept `temp` | `agree` | exit 0 | pass | `agree`, exit 0, pass |
| threshold `2e-6` | `disagree`, 5 of 60 | exit 1, 5 rows | pass | `agree`, exit 0, pass |
| threshold `3e-6` | `disagree`, 15 of 60 | exit 1, 15 rows | fail | `agree`, exit 0, pass |
| threshold `1e-5` | `disagree`, 15 of 60 | exit 1, 15 rows | fail | `agree`, exit 0, pass |
| clip of a limited result | not seeded | not seeded | not seeded | `agree`, exit 0, pass |

The oracle comparison separates exactly the variants `compare.py compare` separates, on the same (case, variable) pairs. The counts are over a copy of the IR whose cross-target predicates name one case each: a `per_case` predicate stops at its first failing case, so the IR as transcribed records fewer.

Consequences, under which the offline files stay:
- The oracle route is an addition inside the workflow and not a replacement of the offline comparison; none of `driver.F90`, `compare.py` and `predicates_*.yaml` is deleted.
- The remedy that would let them go is a `spec` edit of the two nodes: a §5 normaliser per variable without the floor `1`, and every returned variable named by a judgment in every `pass` case. On the figures above, the default normaliser applied to `evaporate` and `rhoe_t` fails correct code, so each needs its own scale, as `pbl_tracer_diffusion_1d_column`'s `tests.md` §5 states one. That node's normaliser is not a model to copy whole: on the suite of its certified run (`orch_20261119T055500Z_6a8a7350`, on the tree of `aa9d90e6`) its floors `R` and `R*D` exceed the outputs they normalise on every `pass` case, so the bound there is fixed by the floor and never by the outputs. Measured on the captures of that run with the definitions of its `tests.md` §5: on the six cases with a nonzero tendency `R` is `12.5` to `31` times `maxabs(final.rhoq_t)` and `R*D` is `36` to `231` times `maxabs(final.flx)`, and on `l0_kh_zero` and `l0_uniform_tracer` the outputs are rounding only. The node's `tests.md` on the tree of `e0a5ab00`, edited since under the same `spec_version`, has one exception: on `l0_pbl_profile_passive` (`sflx_q = -1`, `mass = 0`) `R` is `0.049` times `maxabs(final.rhoq_t)` and `R*D` is `0.87` times `maxabs(final.flx)`, so the outputs fix the bound there; on its seven other cases with a nonzero tendency the two ratios are `12.2` to `30` and `36` to `280`. These figures are measured on the original, through the offline driver of `docs/design/pbl_tracer_measurement/` built at `-O2`. A node that takes the per-variable normaliser chooses a floor below the outputs it compares, so the bound stays relative to them. The edit is a `spec_version` bump and a re-derivation of both nodes, and it also tightens the judgment between two generated targets, whose drift the same bound states (`docs/TESTS.md` §Cross-target judgment). It is the operator's decision and is not taken by issue #680 (`TODO.md`).

## Calibration of `drift_ratio`
The bound `1e-3` and the statement that the kept cells measure below `1e-5` come from an independent kernel of about 250 lines written from the two Controlled Specs by a reviewer, in four arrangements of the arithmetic. The builds were gfortran 11.4 at `-O0`, `-O2`, `-O3`, `-Ofast` and `-O3 -mfma -mavx2`, and ifort and ifx of oneAPI 2023.2 at `-O0`, `-O2` and `-O3`, 44 in all, each at one and four threads; the largest value was `2.9e-6`, at one build. It and its wrong variants are not kept in this repository, so the calibration cannot be retaken from this tree; the record of the run is on [issue #578](https://github.com/seiya/spec2science/issues/578). The original is re-read by the screen.

## What the suites do not separate
Measured with an independent kernel written from the two Controlled Specs and with deliberately wrong variants of it, every judgment evaluated by `evaluate_primary_predicates`, on `xfail` cases too. These pass both suites, and each `tests.md` leaves them unstated because a `Generate` producer reads it:
- a convergence threshold within a factor of 2 of `1e-6`, since the Newton-branch bounds carry that factor (`1.5e-6` passes in every setting; `2e-6` passes only where the node has no comparand, because against a correct second target the saturation suite's `cross_target_state_agreement` goes false on 12 rows; `3e-6` fails the saturation suite and passes the Kessler suite);
- an iteration limit above 100 (`150` and `1000` leave `l0_satadj_limit` unconverged and every other case unchanged);
- the virtual branch keeping `temp` when `qc = 0`, which the bound `2e-13` admits;
- a clip of a limited result to non-negative, which changes only a remainder of order `1e-19` whose sign depends on rounding, so no bound separates it from a correct kernel on another target.

Of those four, the comparison reads part of the threshold. A threshold of `2e-6` exits 1 on `compare saturation` and 0 on `compare kessler`; so do `3e-6` and `1e-5`, which fail the saturation suite only (the Kessler suite is false 0 on both). A threshold of `1.5e-6` exits 0 on both and is false 0 on both suites, with or without a comparand. A limit of `150` or `1000`, the kept `temp` and the clip exit 0 on both: the comparison skips `xfail` cases, and the other differences fall under its `1e-15` floor. Those are held by §3 and §6 of the Controlled Specs and a `Generate` reviewer alone. A threshold change is therefore read on `compare saturation`.

## Results
Commit `db90db3e`, 2026-11-02, `fortran_cpu`, Claude leaves. Both nodes pass, with no framework change. The full record is on [issue #578](https://github.com/seiya/spec2science/issues/578#issuecomment-6568076705).

| | saturation | Kessler |
|---|---:|---:|
| orchestration | `orch_20261102T020111Z_9efd6f58` | `orch_20261102T020110Z_1cf6f9ce` |
| cost | $9.94 | $13.49 (one `Generate.verify` retry, $1.89) |
| `Compile.generate + Compile.verify` | 37.9% | 33.5% |
| share on the test representation | 50.4–61.4% | 50.2–57.8% |
| comparison rows beyond tolerance | 0 of 46 | 0 of 134 |
| largest relative difference | 3.24e-14 | 2.22e-16 |

## Deviations from the original
Listed, not banded, in `DEVIATIONS` of `compare.py`, which the comparison prints, and in §1 of each node's Controlled Spec.

The driver supplies the `0` halos by initialising each output to `0`, since the original writes interior cells only, and supplies `guard_pass = 1` and `converged = 1` on a run that did not abort.
