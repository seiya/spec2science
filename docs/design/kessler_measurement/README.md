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

The in-workflow comparison reads only what each cross-target judgment names, so it shares the suites' blind spots there. Measured by a reviewer of issue #680 PR-4 at `ce4c651d`, with the certified Kessler model of the `_20261105_001` pipeline mutated to set `rhoe_t` from the unlimited vapour tendency (`dens*L00*(dqc + dqr)` in place of `dens*edot`): the node's own suite passes and the oracle comparison reads `agree` (76 records), although on `l0_limiter_qv` the mutant's interior `rhoe_t` is about 2000 times the original's. `tests.md` leaves `rhoe_t` out of the cross-target judgment wherever `qc` is limited, and no other judgment reads it on that case. Closing it is a `spec` edit of the node; the offline comparison reads it.

The operator lists both under the local site's `libraries:` (`docs/RUNBOOK.md` §Certifying a node against its oracle). The in-workflow comparison reads fewer variables and a looser bound than §Operations' comparison (`docs/design/scale_migration.md` §Order row 4), so both stay.

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
