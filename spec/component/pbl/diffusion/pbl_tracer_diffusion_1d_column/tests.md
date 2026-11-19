# Tests: implicit turbulent diffusion of a tracer in a column (L0)

## 0. Meta information
- `test_profile_id`: `pbl_tracer_diffusion_1d_column_l0`
- `test_profile_version`: `0.1.0`
- `status`: `draft`
- `spec_ref.spec_kind`: `component`
- `spec_ref.spec_id`: `pbl_tracer_diffusion_1d_column`
- `spec_ref.spec_version`: `0.1.0`
- `spec_ref.controlled_spec_path`: `spec/component/pbl/diffusion/pbl_tracer_diffusion_1d_column/controlled_spec.md`

## 1. Test purpose
This suite verifies the published `operation` at `L0`: the conservation of the tracer by the tendency, the relation between the tendency and the face flux, the backward-Euler flux, the closed-form solution of the smallest system, the top of the solved layer that the scan of Controlled Spec §3 gives (a profile with `kh = 0` aloft, one with `kh > 0` at every face, one with a gap), the column with no diffusivity, the uniform tracer, the effect of `mass`, an index range other than `ks=2`, `ke=9` with its smallest valid extent, the last interior cell at the top of the array (`ke = ka`), the halo contract, and the input guard of Controlled Spec §4.

The symmetry pairs of `docs/TESTS.md` §Symmetry pairs are not judged: a one-dimensional column has no axis exchange, and a reversal of the column is not a symmetry of the scheme (Controlled Spec §3, Not defined here). The CFL number is `N/A`: the `operation` is implicit and has no explicit transport. The comparison with theory is the conservation, the flux relations and the closed form of §6.

## 2. Input-defaulting rules
The block below is the suite's whole input. Every case has `ka=10`, and every case without a `grid` entry passes `ks=2`, `ke=9`, so the first and the last cell are the halo. A case's `grid`, `dt`, `sflx_q`, `mass` and `ke_pbl` are its inputs `grid.ka`, `grid.ks`, `grid.ke`, `time.dt`, `surface.sflx_q`, `tracer.mass` and `judge.ke_pbl`.

- `fill` is each variable's value at every cell before the rules below apply. `rhoq_t` and `flx` hold `-999` before the call.
- The block's `columns` are the rules of every case; a case's own `columns` replace the rule of each variable they name. A rule replaces the cells $k=2..9$: `[a, b]` gives `a + b*(k-2)` at cell `k`, and `{cells: [...]}` gives the ten values literally, the first and the last being the `fill` value. A variable with no rule keeps its `fill` value at every cell.
- `overrides` set one cell after every other rule, and are what force most of the guard cases.
- `ke_pbl` is the $k_{pbl}$ of Controlled Spec §3 for the case's `kh`, `ks` and `ke`, stated by hand from that rule. The `operation` does not take it; the judgments of §5 read it. The `xfail` cases state none.
- `expected` is the outcome §6 states.
- The driver holds these states in every case: the rank-1 arrays of extent `ka` `dens`, `qtrc`, `kh`, `cz`, `fz`, `f2h_above`, `f2h_below`, `rhoq_t`, `flx`, and the scalar `guard_pass`, which is the logical result of the `operation` and takes no pre-call content. Every array dummy binds the state of its own name, `ka`, `ks`, `ke` the inputs `grid.ka`, `grid.ks`, `grid.ke`, `dt` the input `time.dt`, `sflx_q` the input `surface.sflx_q`, `mass` the input `tracer.mass`, and `guard_pass` the state `guard_pass`.
- The pre-call content of a state is written over the coordinate `c` of §5, with `floor(c)` equal to $k-1$, and the mask of the cells $k=2..9$, `m = min(1, floor(c)) * min(1, 9 - floor(c))`, which does not read the case's `ks`, `ke`. A variable with no rule is its `fill` number. With $f$ its `fill` value, a variable with a `[a, b]` rule is the expression `(1 - m)*f + m*(a + b*(floor(c) - 1))`, and one with a `cells` rule is that list of ten numbers, the input `initial.columns.<var>.cells`. An override of cell `k` with value `v` turns the expression `E` of its variable into `E*(1 - p) + p*v`, with `p = max(0, 1 - abs(floor(c) - (k - 1)))`. Every multiplication by a mask is by exactly `0` or `1`, so each cell holds exactly the value of its own rule.
- `grid`, on an index-range case, gives the `ks`, `ke` passed to the `operation`; the state is the one the rules above describe. So on `l0_three_cells` the cells `5..9`, and on `l0_range` the cells `2` and `9`, are halo cells of the call, and on `l0_ke_at_ka` the cell `10` is an interior cell of it; the masks of §5 read each through the case's own `ks`, `ke`. `l0_ke_at_ka` sets `fz` and `cz` of cell `10` above those of cell `9`, as `l0_ke_above_ka` does, so that its column is valid with `ke = ka`. `l0_range` holds `dens = -1` at the cells `2` and `9`, a value the input guard refuses at an interior cell and does not read at a halo cell.
- The grid is stretched: the cells `2..8` are `50`, `70`, `90`, `120`, `150`, `180` and `220` `m` thick, each with its centre midway between its faces, and the centre of cell `9` is `135` `m` above its lower face. `l0_ke_at_ka` is the only case that solves the face `8`, between the centres of the cells `8` and `9`, and its positive `kh` there makes that face's flux depend on $\delta z_8$: a kernel that forms $\delta z$ from `fz` instead of `cz` does not meet it. The weights `f2h_above`, `f2h_below` are not those of the geometry: the `operation` takes them as inputs. They sum to `1` at every cell of the block's rules; on `l0_pbl_profile` and `l0_range` `f2h_below` is `[0.62, -0.01]` and the sum is `1.04`, so a kernel that forms one weight from the other does not meet those cases. The interior `kh` of the block's rule is positive at the faces `2..6` and `0` at the faces `7..9`, a mixed layer under a layer with no turbulence, so its scan stops at `ke-2 = 7` and $k_{pbl}$ is `8`. The other profiles are each case's: `l0_kh_all_positive` is positive at every face, so $k_{pbl}$ is `ks+2 = 4`; `l0_kh_gap` is positive at the faces `5..7` and `0` at face `4`, so the scan stops at face `4` and $k_{pbl}$ is `6`; on `l0_range` the scan reads the faces `6`, `5`, `4`, all positive, so $k_{pbl}$ is `ks+2 = 5`; on `l0_ke_at_ka` the block's rule holds but for `kh = 15` at the face `8`, so the scan reads the face `ke-2 = 8`, positive, stops at the face `7`, where `kh` is `0`, and $k_{pbl}$ is `9`; on `l0_three_cells` the scan reads no face and $k_{pbl}$ is `ke-1 = 3`.
- `l0_pbl_profile_passive` has `sflx_q = -1` with `mass = 0`, so $\rho^*_{ks}$ is `dens(ks) = 1.15` while `dens(ks) + dt*sflx_q/`$\Delta z_{ks}$ is $1.15-60\cdot1/50<0$: a valid input that a guard reading $\rho^*_{ks}$ without `mass`, or refusing a negative `sflx_q`, refuses.
- `l0_kh_gap` has `dt = 45` and `l0_kh_all_positive` has `mass = 0.5` with `sflx_q = -1e-4`, the only `pass` cases with a `dt` other than `60`, a `mass` other than `0` or `1`, and a negative `sflx_q` with a nonzero `mass`, for which $\rho^*_{ks}=1.15-60\cdot0.5\cdot10^{-4}/50>0$: a kernel that fixes `dt`, reads `mass` as a switch, drops the sign of a negative `sflx_q` in $\rho^*_{ks}$, or refuses a negative `sflx_q` with a nonzero `mass`, meets the other cases.
- `l0_surface_density_nonpositive` has `sflx_q = -1` with `mass = 1`, so $\rho^*_{ks}=1.15-60\cdot1/50<0$. `l0_dens_nonpositive` and `l0_cz_nonincreasing` hold the invalid value at the last interior cell `ke`, and `l0_diffusivity_negative` at the last interior face `ke-1`, cells the solve never uses; `l0_fz_nonincreasing` holds it at the face `ke`, which the solve reads as $\Delta z_{k_{pbl}}$, so a kernel without the guard divides by zero there. `l0_ke_above_ka` sets `fz` and `cz` of cell `10` above those of cell `9`, so that within the column no clause but `ke > ka` holds. `l0_ks_below_two` passes `ks = 1`, the largest value the clause `ks < 2` refuses (Controlled Spec §1: the source writes its flux at `ks-1`), on a column in which the cell `1`, then interior, meets no other clause.

```yaml
grid: {ka: 10, ks: 2, ke: 9}
fill: {dens: 0.77, qtrc: 0.0123, kh: 7.0, cz: -3.0, fz: -6.0, f2h_above: 0.5, f2h_below: 0.5, rhoq_t: -999.0, flx: -999.0}
columns: {dens: [1.15, -0.04], qtrc: {cells: [0.0123, 0.016, 0.0125, 0.0118, 0.0102, 0.009, 0.0071, 0.005, 0.0042, 0.0123]}, kh: {cells: [7.0, 30.0, 60.0, 80.0, 50.0, 20.0, 0.0, 0.0, 0.0, 7.0]}, cz: {cells: [-3.0, 25.0, 85.0, 165.0, 270.0, 405.0, 570.0, 770.0, 1015.0, -3.0]}, fz: {cells: [-6.0, 0.0, 50.0, 120.0, 210.0, 330.0, 480.0, 660.0, 880.0, -6.0]}, f2h_above: [0.42, 0.01], f2h_below: [0.58, -0.01]}
cases:
- {case_id: l0_pbl_profile, expected: pass, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, ke_pbl: 8, columns: {f2h_below: [0.62, -0.01]}}
- {case_id: l0_pbl_profile_passive, expected: pass, dt: 60.0, sflx_q: -1.0, mass: 0.0, ke_pbl: 8}
- {case_id: l0_kh_all_positive, expected: pass, dt: 60.0, sflx_q: -1.0e-4, mass: 0.5, ke_pbl: 4, columns: {kh: {cells: [7.0, 30.0, 60.0, 80.0, 50.0, 20.0, 10.0, 5.0, 3.0, 7.0]}}}
- {case_id: l0_kh_gap, expected: pass, dt: 45.0, sflx_q: 2.0e-4, mass: 1.0, ke_pbl: 6, columns: {kh: {cells: [7.0, 30.0, 60.0, 0.0, 40.0, 30.0, 20.0, 0.0, 0.0, 7.0]}}}
- {case_id: l0_kh_zero, expected: pass, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, ke_pbl: 8, columns: {kh: [0.0, 0.0]}}
- {case_id: l0_uniform_tracer, expected: pass, dt: 60.0, sflx_q: 0.0, mass: 1.0, ke_pbl: 8, columns: {qtrc: [0.01, 0.0]}}
- {case_id: l0_three_cells, expected: pass, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, ke_pbl: 3, grid: {ks: 2, ke: 4}}
- {case_id: l0_range, expected: pass, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, ke_pbl: 5, grid: {ks: 3, ke: 8}, columns: {f2h_below: [0.62, -0.01]}, overrides: [{var: dens, k: 2, value: -1.0}, {var: dens, k: 9, value: -1.0}]}
- {case_id: l0_ke_at_ka, expected: pass, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, ke_pbl: 9, grid: {ks: 2, ke: 10}, overrides: [{var: fz, k: 10, value: 1100.0}, {var: cz, k: 10, value: 1200.0}, {var: kh, k: 8, value: 15.0}]}
- {case_id: l0_ks_below_two, expected: xfail, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, grid: {ks: 1, ke: 9}}
- {case_id: l0_ke_above_ka, expected: xfail, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, grid: {ks: 2, ke: 11}, overrides: [{var: fz, k: 10, value: 1100.0}, {var: cz, k: 10, value: 1200.0}]}
- {case_id: l0_too_few_cells, expected: xfail, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, grid: {ks: 2, ke: 3}}
- {case_id: l0_dt_nonpositive, expected: xfail, dt: 0.0, sflx_q: 2.0e-4, mass: 1.0}
- {case_id: l0_dens_nonpositive, expected: xfail, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, overrides: [{var: dens, k: 9, value: 0.0}]}
- {case_id: l0_fz_nonincreasing, expected: xfail, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, overrides: [{var: fz, k: 9, value: 660.0}]}
- {case_id: l0_cz_nonincreasing, expected: xfail, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, overrides: [{var: cz, k: 9, value: 770.0}]}
- {case_id: l0_diffusivity_negative, expected: xfail, dt: 60.0, sflx_q: 2.0e-4, mass: 1.0, overrides: [{var: kh, k: 8, value: -1.0}]}
- {case_id: l0_surface_density_nonpositive, expected: xfail, dt: 60.0, sflx_q: -1.0, mass: 1.0}
```

## 3. Execution-control rules
`N/A`: the `operation` is a single evaluation of one column. `dt` is a case input of §2 and the suite performs one step. Stepping belongs to the `problem` that composes this `operation`.

distribute: none

## 4. Case-expansion rules
`N/A`: the `L0` suite uses the fixed cases of §2 and defines no `case` sweep.

## 5. Judged quantities
A test is judged by the host's predicates over the captures alone (`docs/TESTS.md` requirement 9). Each quantity below is named on the `quantities` line of the tests that judge it, and is evaluated from the states captured before the call (`initial`) and after it (`final`) in each of the test's target cases.

The host-rendered driver calls the `operation` once, on the states of §2 themselves. `dens`, `qtrc`, `kh`, `cz`, `fz`, `f2h_above`, `f2h_below` are inputs and `rhoq_t`, `flx` outputs of the `operation`. `guard_pass`: the driver writes the logical result into the state after the call as `1` when the call returned true and `0` when it returned false; it starts at `0`, so a result the `operation` never assigned captures `0`.

- A judgment reads a halo cell only through the masks below. With $i=k-1$ and $c=i+1/2$ the cell-centre coordinate of a unit-length cell, `coordinates[]` axis `0`, `count` and `length` `ka`, placement `cell_center`, `idx` $=\lfloor c\rfloor$, and `kp` the input `judge.ke_pbl`: `interior` $=\min(1,\max(0,idx-(ks-2)))\cdot\min(1,\max(0,ke-idx))$ marks the cells $k=ks..ke$, `halo` $=1-$`interior`, `first` $=\max(0,1-|idx-(ks-1)|)$ the cell $k=ks$, `second` $=\max(0,1-|idx-ks|)$ the cell $k=ks+1$, `pairs` $=\min(1,\max(0,idx-(ks-2)))\cdot\min(1,\max(0,ke-1-idx))$ the cells $k=ks..ke-1$, `faces` $=\min(1,\max(0,idx-(ks-2)))\cdot\min(1,\max(0,kp-1-idx))$ the faces $k=ks..k_{pbl}-1$, `unsolved` $=\min(1,\max(0,idx-(kp-1)))\cdot\min(1,\max(0,ke-idx))$ the cells $k=k_{pbl}+1..ke$, `zero_faces` $=\min(1,\max(0,idx-(kp-2)))\cdot\min(1,\max(0,ke-idx))$ the faces $k=k_{pbl}..ke$, and `top_face` $=\max(0,1-|idx-(kp-2)|)$ the face $k=k_{pbl}-1$.
- The names a judgment reads, over the `initial` state and the case's inputs: `dz = roll(initial.fz, -1) - initial.fz` (the thickness $\Delta z_k$ at the cells `ks..ke-1`), `dzc = roll(initial.cz, -1) - initial.cz` (the distance $\delta z_k$ between the centres of the cells $k$ and $k+1$), `s = sflx_q / sum(first*dz)`, `rho_s = initial.dens + first*dt*s*mass` (the $\rho^*_k$ of Controlled Spec §3), `K = (initial.f2h_above*roll(initial.dens, -1) + initial.f2h_below*initial.dens)*initial.kh` (the $K_k$), `R = maxabs(interior*initial.dens*initial.qtrc)/dt`, the scale of the density of the tracer over one step, and `D = sum(pairs*dz)`, the depth from the lower face of the cell `ks` to the lower face of the cell `ke`. Over the `final` state, `x = (initial.qtrc*initial.dens + dt*(final.rhoq_t + first*s))/rho_s`, the new mixing ratio that `rhoq_t` determines (Controlled Spec §3, Conservation). A roll wraps around the column; every value it brings from the other end is multiplied by a mask that is `0` there, and no judgment divides by a difference of two cells.
- The round-off bounds of §6 are offline calibrations against an independent implementation of the scheme, the original `scale-met/scale-dev` code at commit `dc58155` (`docs/design/pbl_tracer_measurement/README.md`), and each carries a margin of at least `100` over the largest value measured there. A value `rhoq_t(k)`, $k>ks$, is $(x_k-\mathtt{qtrc}(k))\rho^*_k/\mathtt{dt}$, so its rounding is of order $2^{-52}$ times $\rho\,\mathtt{qtrc}/\mathtt{dt}$, which is `R`; a flux is $K_k$ times a difference of two $x$, so its rounding is of order $2^{-52}\,K\,\mathtt{qtrc}/\delta z$.
- `guard_pass`: the captured flag, which is `== 1` on the cases of a `pass` test and `== 0` on the cases of an `xfail` test.
- `conservation`: `abs(sum(pairs*final.rhoq_t*dz)) / sum(pairs*abs(final.rhoq_t)*dz)`, the column integral of the tendency over the integral of its magnitude.
- `flux_divergence`: `maxabs(pairs*(final.rhoq_t*dz + final.flx - roll(final.flx, 1))) / maxabs(interior*final.flx)`. At the cell `ks` the rolled flux is that of the cell `ks-1`, a halo cell on every case it judges, which holds `0` (Controlled Spec §2).
- `implicit_flux`: `maxabs(faces*(final.flx*dzc + K*(roll(x, -1) - x))) / maxabs(faces*final.flx*dzc)`, the backward-Euler flux of Controlled Spec §3 at the solved faces, with the new mixing ratio `x`.
- `pbl_top`: `maxabs(unsolved*final.rhoq_t)` and `maxabs(zero_faces*final.flx)`; on `l0_kh_all_positive`, `l0_kh_gap`, `l0_three_cells` and `l0_range`, where `kh` is positive at the face $k_{pbl}-1$, also `maxabs(top_face*final.flx)`.
- `zero_diffusivity`: `maxabs(final.flx)`, `maxabs((interior - first)*final.rhoq_t)` and `maxabs(first*final.rhoq_t) / R`.
- `uniform_tracer`: `maxabs(interior*final.rhoq_t) / R` and `maxabs(final.flx) / (R*D)`.
- `closed_form`: on `l0_three_cells`, $k_{pbl}=ks+1$ and the system has the two rows `ks`, `ks+1`. With `M1 = sum(first*rho_s*dz)`, `M2 = sum(second*initial.dens*dz)`, `F = dt*sum(first*K)/sum(first*dzc)`, `d1 = sum(first*(initial.qtrc*initial.dens + dt*s))/sum(first*rho_s)`, `d2 = sum(second*initial.qtrc)`, the solution has $x_{ks}-x_{ks+1}=(d_1-d_2)/(1+F/M_1+F/M_2)$, so `fref = F/dt*(d1 - d2)/(1 + F/M1 + F/M2)` is `flx(ks)`, and `rhoq_t` is `-fref/dz` at `ks` and `fref/dz` at `ks+1`: `abs(sum(first*final.flx) - fref) / abs(fref)` and `maxabs(first*(final.rhoq_t*dz + fref) + second*(final.rhoq_t*dz - fref)) / abs(fref)`.
- `halo_zero`: `maxabs(halo*final.rhoq_t)` and `maxabs(halo*final.flx)`.
- `cross_target_state_agreement`: this target's run compared with every other target's certified variant of this node, case by case (`docs/TESTS.md` §Cross-target judgment), with a normaliser of this suite's own, because the outputs are far below `1` and the floor `1` of the default would make the bound absolute: for `rhoq_t`, `maxabs(final.rhoq_t - comparand.final.rhoq_t) / max(maxabs(final.rhoq_t), maxabs(comparand.final.rhoq_t), R)`; for `flx`, `maxabs(final.flx - comparand.final.flx) / max(maxabs(final.flx), maxabs(comparand.final.flx), R*D)`. `R` and `D` read the `initial` state, which the two variants share, so the normaliser is symmetric in them; on `l0_kh_zero` and `l0_uniform_tracer`, where the outputs are rounding only, that rounding is a multiple of $2^{-52}$ of `R` and `R*D`. The host evaluates it from the captured state, against each other variant separately; on a target with no other certified variant it holds vacuously. It is outside the `quantities` line.

## 6. Test definitions
Every `pass` test also requires `guard_pass == 1` on each of its cases.

- `test_id`: `l0_conservation_pass`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `pass`
  - `quantities`: `conservation`, `guard_pass`
  - `judgment`: `guard_pass == 1`, and, on `l0_pbl_profile`, `l0_pbl_profile_passive`, `l0_kh_all_positive`, `l0_kh_gap`, `l0_three_cells`, `l0_range` and `l0_ke_at_ka`, the tendency carries no net source, the surface flux included: `abs(sum(pairs*final.rhoq_t*dz)) / sum(pairs*abs(final.rhoq_t)*dz)` is `<= 1e-12` (`conservation`). The cross-target judgment is applied: `cross_target_state_agreement` over each of `rhoq_t` and `flx` is `<= 1e-12`.
- `test_id`: `l0_flux_divergence_pass`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `pass`
  - `quantities`: `flux_divergence`, `guard_pass`
  - `judgment`: `guard_pass == 1`, and, on `l0_pbl_profile`, `l0_pbl_profile_passive`, `l0_kh_all_positive`, `l0_kh_gap`, `l0_three_cells`, `l0_range` and `l0_ke_at_ka`, the tendency at each cell `ks..ke-1` is the divergence of the face flux, with no surface flux in it: `maxabs(pairs*(final.rhoq_t*dz + final.flx - roll(final.flx, 1))) / maxabs(interior*final.flx)` is `<= 1e-12` (`flux_divergence`). The cross-target judgment is applied: `cross_target_state_agreement` over each of `rhoq_t` and `flx` is `<= 1e-12`.
- `test_id`: `l0_implicit_flux_pass`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `pass`
  - `quantities`: `guard_pass`, `implicit_flux`
  - `judgment`: `guard_pass == 1`, and, on `l0_pbl_profile`, `l0_pbl_profile_passive`, `l0_kh_all_positive`, `l0_kh_gap`, `l0_three_cells`, `l0_range` and `l0_ke_at_ka`, the flux at each solved face is the backward-Euler flux over the new mixing ratio `x` that `rhoq_t` determines, with `mass` in $\rho^*_{ks}$: `maxabs(faces*(final.flx*dzc + K*(roll(x, -1) - x))) / maxabs(faces*final.flx*dzc)` is `<= 1e-12` (`implicit_flux`). A flux over the old mixing ratio, or a $\rho^*_{ks}$ without `mass`, does not meet it. The cross-target judgment is applied: `cross_target_state_agreement` over each of `rhoq_t` and `flx` is `<= 1e-12`.
- `test_id`: `l0_pbl_top_pass`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `pass`
  - `quantities`: `guard_pass`, `pbl_top`
  - `judgment`: `guard_pass == 1`, and, on every `pass` case, the cells above $k_{pbl}$ are not solved, whatever `kh` holds there: `maxabs(unsolved*final.rhoq_t)` is `== 0` and `maxabs(zero_faces*final.flx)` is `== 0`. On `l0_kh_all_positive`, `l0_kh_gap`, `l0_three_cells` and `l0_range`, the face $k_{pbl}-1$ is solved: `maxabs(top_face*final.flx)` is `> 0` (together `pbl_top`). The cross-target judgment is applied: `cross_target_state_agreement` over each of `rhoq_t` and `flx` is `<= 1e-12`.
- `test_id`: `l0_zero_diffusivity_pass`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `pass`
  - `quantities`: `guard_pass`, `zero_diffusivity`
  - `judgment`: `guard_pass == 1`, and, on `l0_kh_zero` (`kh = 0` at every interior face, `sflx_q` positive), the surface flux makes no tendency: `maxabs(final.flx)` is `== 0`, `maxabs((interior - first)*final.rhoq_t)` is `== 0`, and at the cell `ks`, where the surface flux enters and is subtracted again, `maxabs(first*final.rhoq_t) / R` is `<= 1e-13` (together `zero_diffusivity`). The cross-target judgment is applied: `cross_target_state_agreement` over each of `rhoq_t` and `flx` is `<= 1e-12`.
- `test_id`: `l0_uniform_tracer_pass`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `pass`
  - `quantities`: `guard_pass`, `uniform_tracer`
  - `judgment`: `guard_pass == 1`, and, on `l0_uniform_tracer` (`qtrc = 0.01` at every interior cell, `sflx_q = 0`), diffusion leaves the tracer as it is: `maxabs(interior*final.rhoq_t) / R` and `maxabs(final.flx) / (R*D)` are `<= 1e-13` (together `uniform_tracer`). The cross-target judgment is applied: `cross_target_state_agreement` over each of `rhoq_t` and `flx` is `<= 1e-12`.
- `test_id`: `l0_three_cell_closed_form_pass`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `pass`
  - `quantities`: `closed_form`, `guard_pass`
  - `judgment`: `guard_pass == 1`, and, on `l0_three_cells` (`ks=2`, `ke=4`, the smallest extent §4 admits), the two-row system has the closed-form solution of §5: `abs(sum(first*final.flx) - fref) / abs(fref)` and `maxabs(first*(final.rhoq_t*dz + fref) + second*(final.rhoq_t*dz - fref)) / abs(fref)` are `<= 1e-12` (together `closed_form`). The cross-target judgment is applied: `cross_target_state_agreement` over each of `rhoq_t` and `flx` is `<= 1e-12`.
- `test_id`: `l0_halo_zero_pass`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `pass`
  - `quantities`: `guard_pass`, `halo_zero`
  - `judgment`: `guard_pass == 1`, and, on every `pass` case, `maxabs(halo*final.rhoq_t)` and `maxabs(halo*final.flx)` are `== 0` (together `halo_zero`). The cross-target judgment is applied: `cross_target_state_agreement` over each of `rhoq_t` and `flx` is `<= 1e-12`.
- `test_id`: `l0_ks_below_two_xfail`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `xfail`
  - `case`: `l0_ks_below_two`
  - `xfail_condition`: `ks < 2`
  - `quantities`: `guard_pass`
  - `judgment`: on `l0_ks_below_two` (`ks = 1`), the `operation` refuses the input: `guard_pass == 0`, in the failure direction. The outputs are not defined on a refused call (Controlled Spec §4), so the flag alone is judged. The cross-target judgment is not applied.
- `test_id`: `l0_ke_above_ka_xfail`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `xfail`
  - `case`: `l0_ke_above_ka`
  - `xfail_condition`: `ke > ka`
  - `quantities`: `guard_pass`
  - `judgment`: on `l0_ke_above_ka`, the `operation` refuses the input: `guard_pass == 0`, in the failure direction. The cross-target judgment is not applied.
- `test_id`: `l0_too_few_cells_xfail`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `xfail`
  - `case`: `l0_too_few_cells`
  - `xfail_condition`: `ke < ks + 2`
  - `quantities`: `guard_pass`
  - `judgment`: on `l0_too_few_cells` (`ks=2`, `ke=3`, one cell below the smallest extent), the `operation` refuses the input: `guard_pass == 0`, in the failure direction. The cross-target judgment is not applied.
- `test_id`: `l0_dt_nonpositive_xfail`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `xfail`
  - `case`: `l0_dt_nonpositive`
  - `xfail_condition`: `dt <= 0`
  - `quantities`: `guard_pass`
  - `judgment`: on `l0_dt_nonpositive`, the `operation` refuses the input: `guard_pass == 0`, in the failure direction. The cross-target judgment is not applied.
- `test_id`: `l0_dens_nonpositive_xfail`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `xfail`
  - `case`: `l0_dens_nonpositive`
  - `xfail_condition`: `dens(ke) <= 0`
  - `quantities`: `guard_pass`
  - `judgment`: on `l0_dens_nonpositive`, the `operation` refuses the input: `guard_pass == 0`, in the failure direction. The cross-target judgment is not applied.
- `test_id`: `l0_fz_nonincreasing_xfail`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `xfail`
  - `case`: `l0_fz_nonincreasing`
  - `xfail_condition`: `fz(ke) <= fz(ke - 1)`
  - `quantities`: `guard_pass`
  - `judgment`: on `l0_fz_nonincreasing`, the `operation` refuses the input: `guard_pass == 0`, in the failure direction. The cross-target judgment is not applied.
- `test_id`: `l0_cz_nonincreasing_xfail`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `xfail`
  - `case`: `l0_cz_nonincreasing`
  - `xfail_condition`: `cz(ke) <= cz(ke - 1)`
  - `quantities`: `guard_pass`
  - `judgment`: on `l0_cz_nonincreasing`, the `operation` refuses the input: `guard_pass == 0`, in the failure direction. The cross-target judgment is not applied.
- `test_id`: `l0_diffusivity_negative_xfail`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `xfail`
  - `case`: `l0_diffusivity_negative`
  - `xfail_condition`: `(f2h_above(ke - 1)*dens(ke) + f2h_below(ke - 1)*dens(ke - 1))*kh(ke - 1) < 0`
  - `quantities`: `guard_pass`
  - `judgment`: on `l0_diffusivity_negative`, the `operation` refuses the input: `guard_pass == 0`, in the failure direction. The cross-target judgment is not applied.
- `test_id`: `l0_surface_density_nonpositive_xfail`
  - `level`: `L0`
  - `operation_id`: `pbl_tracer_diffusion_1d_column__tendency`
  - `expected_outcome`: `xfail`
  - `case`: `l0_surface_density_nonpositive`
  - `xfail_condition`: `dens(ks) + dt*mass*sflx_q/(fz(ks + 1) - fz(ks)) <= 0`
  - `quantities`: `guard_pass`
  - `judgment`: on `l0_surface_density_nonpositive`, the `operation` refuses the input: `guard_pass == 0`, in the failure direction. The cross-target judgment is not applied.

## 7. Pass/fail aggregation rules
- `per_test.pass_rule`: `pass` when every predicate of the test holds.
- `per_test.xfail_rule`: `xfail` when `xfail_condition` is true and every predicate of the test holds.
- `suite.pass_rule`: `pass` when all `test_id` are `pass` or `xfail`.

## 8. Traceability
- Record `test_profile_id` and `test_profile_version` in `trial_meta.json`.
