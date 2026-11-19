# PBL tracer measurement: oracle library and offline driver

## Status
The build record of the `oracle` of `pbl_tracer_diffusion_1d_column` (`spec/component/pbl/diffusion/pbl_tracer_diffusion_1d_column/`), the node converted by `skills/spec-from-code` in the O7 trial of [issue #680](https://github.com/seiya/spec2science/issues/680). It belongs to no suite and no workflow phase, and nothing in `tools/` reads it.

**Placement exception.** As `docs/design/kessler_measurement/` is, this directory is placed beside the node it builds for, for as long as the SCALE nodes live in this repository: `TODO.md` carries the removal condition. Direct compiler calls here are a development measurement; `AGENTS.md` §Build-runtime execution rules binds the workflow's `compile`.

## Source
`scale-met/scale-dev` at commit `dc58155` (`develop`, 2026-10-31; not among the refs the remote advertises, so a fresh clone may lack it). The tree is read only, and nothing is copied from it into this directory. The real files are `scalelib/src/common/scale_precision.F90`, `scale_const.F90`, `scale_matrix.F90` and `scalelib/src/atmosphere/physics/pbl/scale_atmos_phy_bl_common.F90`, plus `scalelib/include`. A tree holding only these paths is extracted with `git -C <clone> archive dc58155 scalelib/include scalelib/src/common scalelib/src/atmosphere/physics/pbl | tar -x -C <tree>`.

## Contents
- `Makefile`: with the target `lib`, the four real files and four stubs as the archive `libscale_bl_oracle.a` and its module files; with no target, also the node's oracle adapter and `driver.F90`. `SCALE_ROOT` is required; `BUILD` defaults to `build/`. `FC` selects the compiler, and `MODFLAG` its module-directory flag (`-J` by default; `MODFLAG="-module "` for ifort or ifx).
- The stubs are `scale_io`, `scale_prc`, `scale_prof` and `scale_file_history` of `docs/design/kessler_measurement/stubs/`, read by path. `scale_file_history` keeps the last field written to the history output as `captured`: here the flux `ZFLX_<name>_BL`, which the adapter publishes as `flx`. `scale_prc` turns the abort into an error stop; the original procedure calls neither it nor `scale_prof`, which its modules `use`.
- `driver.F90`: reads one case from standard input and calls the adapter, so a run of it exercises the adapter over the original.
- `crosscheck.py`: runs the `pass` cases of the node's `tests.md` through two builds of the driver and prints the cross-target agreement of `rhoq_t` and `flx` under the suite's own normaliser. It reads the cases from the one fenced YAML block of `tests.md` §2.

## Oracle library
`oracle/oracle.yaml` of the node declares one build, linked with the external library `scale_bl_oracle` (`spec/libraries/scale_bl_oracle.yaml`). The original takes no constant from module state and needs no setup, so one library serves every case. Build it with the compiler the target builds with, since the adapter reads its module files, into a directory that holds nothing else: the driver build writes the adapter's module `pbl_tracer_diffusion_1d_column_model`, which in the library's include directory would stand beside the generated model's.

```sh
make SCALE_ROOT=<tree> BUILD=<dir>/default lib    # <dir>/default/libscale_bl_oracle.a and its module files
```

The operator lists it under the local site's `libraries:` (`docs/RUNBOOK.md` §Certifying a node against its oracle).

## Operations
The judgments of `tests.md` §6 are evaluated on the original by `tools/tests/data/drive_ir/pr3_evidence/verify_spec.py` over the hand transcription `pr3_evidence/ir/pbl_tracer_diffusion_1d_column.yaml`, with the adapter as the model and the library of §Oracle library located in its build directory. `--compared-only` runs only the cases the oracle is compared on, which are `pass` cases, and judges only the tests whose cases are all among them: the original has no guard and does not survive a refused case. From the repository root:

```sh
PYTHONPATH=. python3 tools/tests/data/drive_ir/pr3_evidence/verify_spec.py \
    tools/tests/data/drive_ir/pr3_evidence/ir/pbl_tracer_diffusion_1d_column.yaml \
    --model spec/component/pbl/diffusion/pbl_tracer_diffusion_1d_column/oracle/adapter.f90 \
    --library scale_bl_oracle=<dir>/default --compared-only [--show-values]
```

`<dir>` is an absolute path in every command of this section and of §Oracle library: `make -C` resolves a relative `BUILD` against `docs/design/pbl_tracer_measurement/`, and the commands run from the repository root resolve a relative path against the root, so a relative `<dir>` names two different directories.

The script compiles the adapter at `-O2`. At another level, the library is built at that level into a directory of its own and the level is appended to the adapter's flags:

```sh
make -C docs/design/pbl_tracer_measurement SCALE_ROOT=<tree> BUILD=<dir>/O3fma_lib \
    FFLAGS="-O3 -march=native -mfma -cpp -ffree-line-length-none" lib
PYTHONPATH=. python3 tools/tests/data/drive_ir/pr3_evidence/verify_spec.py \
    tools/tests/data/drive_ir/pr3_evidence/ir/pbl_tracer_diffusion_1d_column.yaml \
    --model spec/component/pbl/diffusion/pbl_tracer_diffusion_1d_column/oracle/adapter.f90 \
    --library scale_bl_oracle=<dir>/O3fma_lib --flag=-O3 --flag=-march=native --flag=-mfma \
    --compared-only [--show-values]
```

`--show-values` prints every record whole, with the value of each case it evaluated. `--mutate 'old=>new'` seeds a defect into the adapter the same way. The cross-target agreement of two builds of the original is measured by `crosscheck.py` over two offline drivers, each built into a directory of its own:

```sh
make -C docs/design/pbl_tracer_measurement SCALE_ROOT=<tree> BUILD=<dir>/O2
make -C docs/design/pbl_tracer_measurement SCALE_ROOT=<tree> BUILD=<dir>/O3fma \
    FFLAGS="-O3 -march=native -mfma -cpp -ffree-line-length-none"
make -C docs/design/pbl_tracer_measurement SCALE_ROOT=<tree> BUILD=<dir>/O0 \
    FFLAGS="-O0 -cpp -ffree-line-length-none"
python3 docs/design/pbl_tracer_measurement/crosscheck.py <dir>/O3fma/driver <dir>/O2/driver
python3 docs/design/pbl_tracer_measurement/crosscheck.py <dir>/O3fma/driver <dir>/O0/driver
python3 docs/design/pbl_tracer_measurement/crosscheck.py <dir>/O0/driver <dir>/O2/driver
```

## Results
gfortran 11.4, 2026-11-19, on the tree of `e0a5ab00`: its `tests.md`, its hand transcription and its `verify_spec.py`. The commands of §Operations with `--show-values`, on the library built at `-O2` and at `-O3 -march=native -mfma`: each run judges the 8 `pass` tests over the 9 `pass` cases, and all 8 reach their `expected_outcome`. The largest `value` over every case of each bounded judgment's record, against its bound, over both runs:

| judgment | worst value | bound | case |
|---|---:|---:|---|
| `conservation` | `3.9e-15` | `1e-12` | `l0_kh_all_positive` (`-mfma`) |
| `flux_divergence` | `7.8e-15` | `1e-12` | `l0_ke_at_ka` |
| `implicit_flux` | `2.1e-15` | `1e-12` | `l0_pbl_profile` |
| `zero_diffusivity`, cell `ks` | `9.8e-17` | `1e-13` | `l0_kh_zero` (`-mfma`) |
| `uniform_tracer`, `rhoq_t` | `1.7e-16` | `1e-13` | `l0_uniform_tracer` |
| `uniform_tracer`, `flx` | `1.7e-17` | `1e-13` | `l0_uniform_tracer` |
| `closed_form`, `flx` | `1.0e-15` | `1e-12` | `l0_three_cells` (`-mfma`) |
| `closed_form`, `rhoq_t` | `1.7e-15` | `1e-12` | `l0_three_cells` |

The exact judgments (`guard_pass == 1`, and `== 0` of `pbl_top`, `halo_zero` and the two exact parts of `zero_diffusivity`) hold on every case, and `maxabs(top_face*flx)` of `pbl_top` is between `3.0e-4` and `1.0e-3` on its four cases. The cross-target judgment holds vacuously in these runs, which have no comparand. `crosscheck.py` over offline drivers built at `-O0`, `-O2` and `-O3 -march=native -mfma`: `-O3 -mfma` against `-O2` and against `-O0` reaches `2.8e-16` (`l0_pbl_profile_passive`, `rhoq_t`) against the bound `1e-12`, and `-O0` and `-O2` agree to the last bit on every case. Two seeded defects of the adapter, run with `--mutate` on the `-O2` library: passing `mass = 0` (`'mass_rp = mass=>mass_rp = 0.0_RP'`) moves `l0_implicit_flux_pass` and `l0_three_cell_closed_form_pass`, and shifting `fz` by one cell (`'fz3(0:ka-1,1,1) = fz=>fz3(1:ka,1,1) = fz'`) moves those two and `l0_conservation_pass`, `l0_flux_divergence_pass`.
