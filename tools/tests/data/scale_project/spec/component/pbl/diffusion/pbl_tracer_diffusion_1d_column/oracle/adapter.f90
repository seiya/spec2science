! Oracle adapter (docs/SPEC.md requirement 17): publishes this node's Controlled Spec §5.1 surface
! over the original SCALE procedure of `oracle.yaml`'s `origin`, linked from the external library
! `scale_bl_oracle`. It is what docs/design/pbl_tracer_measurement/driver.F90 calls per column.
!
! - The original's arrays are `(KA,IA,JA)` with `IA = JA = 1`; `fz(k)` here is the original's
!   `FZ(k-1)`, and `f2h_above`, `f2h_below` are `F2H(:,1)`, `F2H(:,2)`. `FZ(KA)` is never read by
!   the original (Controlled Spec §1) and is set to 0.
! - `flx` is the field the original writes to its history output (`ZFLX_<name>_BL`), which the
!   library's `scale_file_history` stub keeps as `captured`. Only its cells `ks..ke` are copied:
!   the original leaves the cells outside `ks-1..ke` unwritten, and the Controlled Spec defines
!   every halo cell as 0.
! - `rhoq_t` starts at 0 and the original writes cells `ks..ke` only, so a halo cell is 0, as the
!   Controlled Spec defines it.
! - `guard_pass` is not an output of the original, which has no input guard: a call that returns
!   is what `guard_pass` states on the cases the oracle runs (every compared case is a `pass`
!   case).
! - The original needs no setup: it reads no module state. Its `DDT` is `real(DP)`, the kind of
!   `dt` here, so `dt` is passed as it is.
module pbl_tracer_diffusion_1d_column_model
  use, intrinsic :: iso_fortran_env, only: real64
  use scale_precision, only: RP
  use scale_file_history, only: captured
  use scale_atmos_phy_bl_common, only: ATMOS_PHY_BL_tendency_tracer
  implicit none
  private

  integer, parameter :: dp = real64

  public :: pbl_tracer_diffusion_1d_column__tendency

contains

  subroutine pbl_tracer_diffusion_1d_column__tendency(ka, ks, ke, dens, qtrc, sflx_q, kh, mass, &
      cz, fz, f2h_above, f2h_below, dt, rhoq_t, flx, guard_pass)
    integer, intent(in) :: ka
    integer, intent(in) :: ks
    integer, intent(in) :: ke
    real(dp), intent(in) :: dens(ka)
    real(dp), intent(in) :: qtrc(ka)
    real(dp), intent(in) :: sflx_q
    real(dp), intent(in) :: kh(ka)
    real(dp), intent(in) :: mass
    real(dp), intent(in) :: cz(ka)
    real(dp), intent(in) :: fz(ka)
    real(dp), intent(in) :: f2h_above(ka)
    real(dp), intent(in) :: f2h_below(ka)
    real(dp), intent(in) :: dt
    real(dp), intent(out) :: rhoq_t(ka)
    real(dp), intent(out) :: flx(ka)
    logical, intent(out) :: guard_pass
    real(RP) :: d3(ka,1,1), q3(ka,1,1), s2(1,1), kh3(ka,1,1), cz3(ka,1,1), fz3(0:ka,1,1), &
        f2h4(ka,2,1,1), t3(ka,1,1), mass_rp

    d3(:,1,1) = dens
    q3(:,1,1) = qtrc
    s2(1,1) = sflx_q
    kh3(:,1,1) = kh
    cz3(:,1,1) = cz
    fz3(0:ka-1,1,1) = fz
    fz3(ka,1,1) = 0.0_RP
    f2h4(:,1,1,1) = f2h_above
    f2h4(:,2,1,1) = f2h_below
    mass_rp = mass
    t3 = 0.0_RP
    call ATMOS_PHY_BL_tendency_tracer(ka, ks, ke, 1, 1, 1, 1, 1, 1, d3, q3, s2, kh3, mass_rp, &
        cz3, fz3, f2h4, dt, 'QTRC', t3)
    rhoq_t = 0.0_dp
    rhoq_t(ks:ke) = t3(ks:ke,1,1)
    flx = 0.0_dp
    flx(ks:ke) = captured(ks:ke,1,1)
    guard_pass = .true.
  end subroutine pbl_tracer_diffusion_1d_column__tendency

end module pbl_tracer_diffusion_1d_column_model
