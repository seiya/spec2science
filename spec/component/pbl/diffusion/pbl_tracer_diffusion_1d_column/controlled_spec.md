# Controlled Spec: implicit turbulent diffusion of a tracer in a column (component spec)

## 0. Meta information
- `spec_id`: `pbl_tracer_diffusion_1d_column`
- `spec_version`: `0.1.0`
- `status`: `controlled_draft`
- `spec_kind`: `component`
- `domain`: `pbl`
- `family`: `diffusion`

## 1. Responsibility and scope
This `component` evaluates, on the interior cells of one vertical column, the tendency of the density of one tracer by vertical turbulent diffusion over one implicit (backward Euler) step, with the surface flux of the tracer entering the lowest cell, and the turbulent flux of the tracer at the cell faces. It does not compute the eddy diffusivity, does not apply the tendency, and does not apply the surface flux: the tendency it returns carries no net source (§3, Conservation). The premise of the input state is a column whose face heights and cell-centre heights increase upward and whose density is positive.

The source is `ATMOS_PHY_BL_tendency_tracer` of the module `scale_atmos_phy_bl_common` (`scalelib/src/atmosphere/physics/pbl/scale_atmos_phy_bl_common.F90`) in `scale-met/scale-dev` at commit `dc58155`, in double precision, on the path compiled without `_OPENACC`, where it calls `MATRIX_SOLVER_tridiagonal` of `scale_matrix` (`scalelib/src/common/scale_matrix.F90`), which resolves to `MATRIX_SOLVER_tridiagonal_1D_TA`.

Differences from that source, each recorded here:
- The array form: the source's `(KA,IA,JA)` arrays `DENS`, `QTRC`, `Kh`, `CZ` and its output `RHOQ_t` are rank-1 columns `dens`, `qtrc`, `kh`, `cz`, `rhoq_t` of extent `ka` here, and its `(IA,JA)` surface flux `SFLX_Q` is the scalar `sflx_q`. The loop over the horizontal indices `IS..IE`, `JS..JE` is not taken.
- The face heights: the source's `FZ(0:KA)` holds at `FZ(k)` the height of the upper face of cell `k`. Here `fz` has extent `ka` and `fz(k)` is the height of the lower face of cell `k`, the source's `FZ(k-1)`. The source's `FZ(KA)` is not published: the source reads `FZ` at `KS-1..KE_PBL` only, and `KE_PBL` is at most `KE-1` (§3).
- The interpolation weights: the source's `F2H(KA,2)` is the two columns `f2h_above`, the source's `F2H(:,1)`, and `f2h_below`, the source's `F2H(:,2)`.
- The time step: the source's `DDT` is `real(DP)` and is converted to the working precision, which in double precision is the same value; here `dt` is `real(dp)`.
- The history output: the flux, which the source writes to the history output as `ZFLX_<TRACER_NAME>_BL` and to no argument, is the output argument `flx` here. The argument `TRACER_NAME`, which only names that output, is not published.
- The cells the source leaves undefined: the source writes `RHOQ_t` at `KS..KE` only, and its flux at `KS-1..KE` only, leaving every other cell undefined. Here `rhoq_t` is `0` at every halo cell and `flx` is `0` at every cell outside `ks..kpbl-1` (§3), which agrees with the source at every cell the source writes as `0`.
- The refusal of invalid input (§4): the source has no check on the index range, `dt`, the density, the heights or the diffusivity. With `KE = KS+1` the source's solver reads an element below the start of its own work array, so its result is not defined there; this `component` refuses `ke < ks+2`. With `KS = 1` the source writes its flux at `KS-1`, the index `0`, below the start of its own flux array `flx(KA,IA,JA)`, so its result is not defined there either; this `component` refuses `ks < 2`, so the cell `ks-1`, whose flux is `0` (§3), is always a halo cell.
- The path compiled with `_OPENACC`, which is not specified here: it forms the main diagonal by another expression, `b(k) = -a(k) + dt*RHOKh(k-1)*rFDZ(k-1)*rRC(k) + 1`, which is the value $1-a_k-c_k$ of §3 to the last bit, and solves the system in place by the partition method with 16 partitions (`matrix_solver_tridiagonal_pm.h`) instead of the Thomas algorithm. The two paths differ in rounding only.
- Single precision, which is not specified or measured here.

## 2. input/output contract
A column has `ka` cells numbered $k=1..ka$. The interior cells are $k=ks..ke$; the other cells are halo cells, which the `operation` does not read. Every array argument has rank 1 and extent `ka`.

**`tendency`.** Inputs: `ka`, `ks`, `ke`; the density `dens` (`kg/m3`); the mixing ratio of the tracer `qtrc` (`kg/kg`, or the tracer's unit per `kg` of air); the surface flux of the tracer `sflx_q` (`kg/m2/s`, or the tracer's unit per `m2` per `s`); the eddy diffusivity `kh` (`m2/s`), where `kh(k)` is the value at the upper face of cell `k`; `mass`, the mass of the tracer per unit of the tracer (`1` for water vapour, `0` for a tracer that carries no mass), which scales the change of the density of air that the surface flux makes in the lowest cell; the height of each cell centre `cz` (`m`); the height of the lower face of each cell `fz` (`m`); the weights `f2h_above`, `f2h_below` (dimensionless) that interpolate the density of cells `k+1` and `k` to the upper face of cell `k`; the time step `dt` (`s`). Outputs: the tendency of the density of the tracer `rhoq_t` (`kg/m3/s`, or the tracer's unit per `m3` per `s`), the flux of the density of the tracer at the upper face of each cell `flx` (`kg/m2/s`, positive upward), and the input guard `guard_pass` (§4). The published arguments are exactly, in this order: `ka`, `ks`, `ke`, `dens`, `qtrc`, `sflx_q`, `kh`, `mass`, `cz`, `fz`, `f2h_above`, `f2h_below`, `dt`, `rhoq_t`, `flx`, `guard_pass`.

**Cells read.** The `operation` reads `dens`, `cz` and `fz` at cells `ks..ke`, and `qtrc`, `kh`, `f2h_above` and `f2h_below` at cells `ks..ke-1`. It reads no halo cell.

**Halo cells.** On a call that `guard_pass` does not refuse, `rhoq_t` and `flx` hold `0` at every halo cell.

## 3. Operation definition
The published `operation` is `pbl_tracer_diffusion_1d_column__tendency`.

**The top of the solved layer.** The integer $k_{pbl}$ starts at `ke-1`. Then, for $k=$ `ke-2`, `ke-3`, ..., `ks+1` in this order: if `kh(k)` $>0$, $k_{pbl}\leftarrow k+1$; otherwise the scan stops. So $k_{pbl}$ lies in `ks+1..ke-1`: it is `ke-1` when `kh(ke-2)` $\le0$ or when `ke-2 < ks+1`, and otherwise one more than the lowest cell of the run of cells with `kh` $>0$ that descends from `ke-2`, which is at least `ks+1`. With `ke` $\ge$ `ks+3`, a column with `kh` $>0$ at every cell `ks+1..ke-2` has $k_{pbl}=$ `ks+2`, so only the cells `ks..ks+2` are solved. The cells `ks..`$k_{pbl}$ are the solved cells; the cells $k_{pbl}+1..$`ke` are not solved, and the cell `ke` is never solved. This is the rule of the source as executed.

**The system.** With $\Delta z_k=$ `fz(k+1)` $-$ `fz(k)` for $k=ks..k_{pbl}$ and $\delta z_k=$ `cz(k+1)` $-$ `cz(k)` for $k=ks..k_{pbl}-1$:
$$
s=\frac{\mathtt{sflx\_q}}{\Delta z_{ks}},\qquad
\rho^*_{ks}=\mathtt{dens}(ks)+\mathtt{dt}\,s\,\mathtt{mass},\qquad
\rho^*_k=\mathtt{dens}(k)\ (k=ks+1..k_{pbl}),
$$
$$
K_k=\left(\mathtt{f2h\_above}(k)\,\mathtt{dens}(k+1)+\mathtt{f2h\_below}(k)\,\mathtt{dens}(k)\right)\mathtt{kh}(k)\quad(k=ks..k_{pbl}-1),
$$
$$
d_{ks}=\frac{\mathtt{qtrc}(ks)\,\mathtt{dens}(ks)+\mathtt{dt}\,s}{\rho^*_{ks}},\qquad d_k=\mathtt{qtrc}(k)\ (k=ks+1..k_{pbl}).
$$
$K_k$ uses `dens`, not $\rho^*$. For $k=ks..k_{pbl}-1$, with $p_k=-\mathtt{dt}\,K_k/\delta z_k$: $a_k=p_k/(\rho^*_k\Delta z_k)$ and $c_{k+1}=p_k/(\rho^*_{k+1}\Delta z_{k+1})$; $c_{ks}=0$, $a_{k_{pbl}}=0$, and $b_k=1-a_k-c_k$ for $k=ks..k_{pbl}$. The new mixing ratio $x_k$, $k=ks..k_{pbl}$, solves
$$
c_k\,x_{k-1}+b_k\,x_k+a_k\,x_{k+1}=d_k\qquad(k=ks..k_{pbl}),
$$
with the terms $c_{ks}x_{ks-1}$ and $a_{k_{pbl}}x_{k_{pbl}+1}$ absent. The solution is the Thomas algorithm: $\gamma_{ks}=a_{ks}/b_{ks}$, $\beta_{ks}=d_{ks}/b_{ks}$; for $k=ks+1..k_{pbl}-1$, with $r=1/(b_k-c_k\gamma_{k-1})$, $\gamma_k=a_k r$ and $\beta_k=(d_k-c_k\beta_{k-1})r$; then $x_{k_{pbl}}=(d_{k_{pbl}}-c_{k_{pbl}}\beta_{k_{pbl}-1})/(b_{k_{pbl}}-c_{k_{pbl}}\gamma_{k_{pbl}-1})$ and $x_k=\beta_k-\gamma_k x_{k+1}$ for $k=k_{pbl}-1$ down to `ks`. Under §4 every $p_k\le0$, so the matrix is diagonally dominant with $b_k\ge1$, and no pivot vanishes. The source forms the reciprocals $1/\delta z_k$, $1/(\rho^*_k\Delta z_k)$, $1/\Delta z_{ks}$, $1/\rho^*_{ks}$ and $1/\mathtt{dt}$ and multiplies by them; dividing instead changes a result by rounding only.

**The outputs.**
$$
\mathtt{rhoq\_t}(ks)=\frac{x_{ks}\,\rho^*_{ks}-\mathtt{qtrc}(ks)\,\mathtt{dens}(ks)}{\mathtt{dt}}-s,\qquad
\mathtt{rhoq\_t}(k)=\frac{(x_k-\mathtt{qtrc}(k))\,\rho^*_k}{\mathtt{dt}}\ (k=ks+1..k_{pbl}),
$$
$$
\mathtt{flx}(k)=-K_k\,\frac{x_{k+1}-x_k}{\delta z_k}\quad(k=ks..k_{pbl}-1).
$$
`rhoq_t` is `0` at the cells $k_{pbl}+1..$`ke` and at every halo cell; `flx` is `0` at every cell outside `ks..`$k_{pbl}-1$, the halo cells included.

**Conservation.** In exact arithmetic, multiplying row $k$ of the system by $\rho^*_k\Delta z_k$ gives, with $\mathtt{flx}(ks-1)$ read as `0`,
$$
\mathtt{rhoq\_t}(k)\,\Delta z_k=-\left(\mathtt{flx}(k)-\mathtt{flx}(k-1)\right)\qquad(k=ks..k_{pbl}),
$$
where $\mathtt{flx}(k_{pbl})=0$: at the cell `ks` the surface flux that enters $d_{ks}$ and $\rho^*_{ks}$ is subtracted again by the term $-s$. Summed over the solved cells, $\sum_{k=ks}^{k_{pbl}}\mathtt{rhoq\_t}(k)\,\Delta z_k=0$: the tendency redistributes the tracer within the column and carries no net source, the surface flux included. The flux at a face is the backward-Euler flux, evaluated with the new mixing ratio, which `rhoq_t` determines: $x_k=\left(\mathtt{qtrc}(k)\,\mathtt{dens}(k)+\mathtt{dt}\,(\mathtt{rhoq\_t}(k)+s\,[k=ks])\right)/\rho^*_k$. A column with `kh` $=0$ at every interior cell has $x_k=d_k$, so `rhoq_t` is `0` at every cell but `ks`, where it is rounding only, and `flx` is `0`. A column whose `qtrc` is the same at every cell `ks..ke-1`, with `sflx_q` $=0$, has $x_k=$ `qtrc(k)`, so `rhoq_t` and `flx` are rounding only.

**Not defined here.** The CFL number is not defined: the `operation` is implicit and has no explicit transport. A column admits no spatial symmetry of the scheme: the surface flux enters the lowest interior cell only, and the scan for $k_{pbl}$ runs downward from `ke-2`.

## 4. Failure conditions and constraints
**`tendency`** treats as invalid input: `ks<2`, `ke>ka`, `ke<ks+2`, `dt<=0`; `dens<=0` at an interior cell; `fz(k+1)<=fz(k)` or `cz(k+1)<=cz(k)` for some $k=ks..ke-1$; $\left(\mathtt{f2h\_above}(k)\,\mathtt{dens}(k+1)+\mathtt{f2h\_below}(k)\,\mathtt{dens}(k)\right)\mathtt{kh}(k)<0$ for some $k=ks..ke-1$; and $\mathtt{dens}(ks)+\mathtt{dt}\,\mathtt{mass}\,\mathtt{sflx\_q}/(\mathtt{fz}(ks+1)-\mathtt{fz}(ks))\le0$, the density $\rho^*_{ks}$ of §3.

The index clauses are decided before any array is read, so a refused index range reads no cell outside `1..ka`.

On a refusal `guard_pass` is false and the output arrays are not defined. On any other call `guard_pass` is true.

## 5. Public API and compatibility
The published `operation_id`s are exactly: `pbl_tracer_diffusion_1d_column__tendency`.

A change breaking compatibility of a published signature — the argument names, their order, their types, their ranks, or their `intent`s — is a **breaking change released under a new `spec_version`**, and so is a change to how the surface is carried. An update that keeps every signature and every result is a `minor` update.

### 5.1 Canonical interface block
The exact published surface as a machine-readable, language-neutral signature block. The rules are those of `docs/CONTROLLED_SPEC.md` §5.1: the deterministic gates render this block to the target language and pin the `IR`'s `public_api` and the generated model source against it.

```yaml
module_parameters:
- name: dp
  value: float64
types: []
procedures:
- kind: subroutine
  name: pbl_tracer_diffusion_1d_column__tendency
  args:
  - name: ka
    rank: 0
    intent: in
    spec:
      type: integer
  - name: ks
    rank: 0
    intent: in
    spec:
      type: integer
  - name: ke
    rank: 0
    intent: in
    spec:
      type: integer
  - name: dens
    rank: 1
    intent: in
    spec:
      type: real
      kind: dp
    dims:
    - ka
  - name: qtrc
    rank: 1
    intent: in
    spec:
      type: real
      kind: dp
    dims:
    - ka
  - name: sflx_q
    rank: 0
    intent: in
    spec:
      type: real
      kind: dp
  - name: kh
    rank: 1
    intent: in
    spec:
      type: real
      kind: dp
    dims:
    - ka
  - name: mass
    rank: 0
    intent: in
    spec:
      type: real
      kind: dp
  - name: cz
    rank: 1
    intent: in
    spec:
      type: real
      kind: dp
    dims:
    - ka
  - name: fz
    rank: 1
    intent: in
    spec:
      type: real
      kind: dp
    dims:
    - ka
  - name: f2h_above
    rank: 1
    intent: in
    spec:
      type: real
      kind: dp
    dims:
    - ka
  - name: f2h_below
    rank: 1
    intent: in
    spec:
      type: real
      kind: dp
    dims:
    - ka
  - name: dt
    rank: 0
    intent: in
    spec:
      type: real
      kind: dp
  - name: rhoq_t
    rank: 1
    intent: out
    spec:
      type: real
      kind: dp
    dims:
    - ka
  - name: flx
    rank: 1
    intent: out
    spec:
      type: real
      kind: dp
    dims:
    - ka
  - name: guard_pass
    rank: 0
    intent: out
    spec:
      type: logical
```

## 6. Prohibitions
Forbid a solved layer other than the one the rule of §3 gives, and forbid solving the cells above $k_{pbl}$. Forbid a time scheme other than the backward-Euler step of §3: the flux is evaluated with the new mixing ratio. Forbid adding the surface flux to `rhoq_t`, and forbid writing it to `flx`. Forbid terminating the program on a refusal, and forbid publishing a non-finite value. Forbid reading a halo cell, and forbid silently completing an invalid input.

## 7. Traceability
Require recording the adoption result in `component_catalog.yaml` and, by the host, in the adopting `spec`'s `<ir_ref>/dependency_graph.json` (`all_nodes`, at the resolved `version`). The scalars the suite fixes fall into the `case` section of `spec.ir.yaml` as case inputs.

## 8. tests reference
The corresponding `tests.md` is `spec/component/pbl/diffusion/pbl_tracer_diffusion_1d_column/tests.md`, with `test_profile_version` of `0.1.0`.

## 9. AD preparation information
`ad_readiness.enabled` is `true`. The scan for $k_{pbl}$ of §3, which depends on the sign of `kh`, and the refusal of §4 are made explicit as non-differentiable operations. The tridiagonal solve is differentiable in its coefficients and right-hand side.
