! Oracle adapter (docs/SPEC.md requirement 17): publishes this node's Controlled Spec §5.1 surface
! over the original SCALE procedures of `oracle.yaml`'s `origin`, linked from the external
! library its build names. It does what docs/design/kessler_measurement/driver.F90 does per column.
!
! - The constant arguments are not read: the original holds them as compile-time parameters of
!   `scale_const`, so the `default` build links the library built from the original and the
!   `alt` build the one built with the suite's `alt_constants` (`oracle.yaml` routes the
!   `constants: alt` cases to it).
! - The original calls its own saturation procedures, not this node's dependency.
! - `pcsat` is the field the original writes to its history output (`QC_t_sat`), which the
!   library's `scale_file_history` stub keeps as `captured`.
! - `intent(out)` arrays start at 0 and the original writes interior cells only, so a halo cell
!   of an output is 0, as the Controlled Spec defines it.
! - `guard_pass` is not an output of the original: it has no input guard, and it aborts on a cell
!   that does not converge, which ends the oracle run. A call that returns did not abort, which
!   is what `guard_pass` states on the cases the oracle runs (every compared case is a `pass`
!   case).
module microphysics_kessler_warm_rain_1d_column_model
  use, intrinsic :: iso_fortran_env, only: real64
  use scale_precision, only: RP
  use scale_io, only: IO_FID_CONF
  use scale_const, only: CONST_setup
  use scale_file_history, only: captured
  use scale_atmos_saturation, only: ATMOS_SATURATION_setup
  use scale_atmos_phy_mp_kessler, only: ATMOS_PHY_MP_kessler_adjustment, &
      ATMOS_PHY_MP_kessler_terminal_velocity
  implicit none
  private

  integer, parameter :: dp = real64
  logical :: ready = .false.

  public :: microphysics_kessler_warm_rain_1d_column__adjustment
  public :: microphysics_kessler_warm_rain_1d_column__terminal_velocity

contains

  ! The original's setup, once: an empty namelist file keeps every default.
  subroutine setup_once()
    if (ready) return
    open(newunit=IO_FID_CONF, status='scratch', form='formatted')
    call CONST_setup
    call ATMOS_SATURATION_setup
    ready = .true.
  end subroutine setup_once

  subroutine microphysics_kessler_warm_rain_1d_column__adjustment(ka, ks, ke, dt, dens, pres, &
      temp, qv, qc, qr, cptot, cvtot, rvap, cpvap, cl, lhv0, psat0, tem00, eps, dwatr, rhoe_t, &
      evaporate, pcsat, guard_pass)
    integer, intent(in) :: ka
    integer, intent(in) :: ks
    integer, intent(in) :: ke
    real(dp), intent(in) :: dt
    real(dp), intent(in) :: dens(ka)
    real(dp), intent(in) :: pres(ka)
    real(dp), intent(inout) :: temp(ka)
    real(dp), intent(inout) :: qv(ka)
    real(dp), intent(inout) :: qc(ka)
    real(dp), intent(inout) :: qr(ka)
    real(dp), intent(inout) :: cptot(ka)
    real(dp), intent(inout) :: cvtot(ka)
    real(dp), intent(in) :: rvap
    real(dp), intent(in) :: cpvap
    real(dp), intent(in) :: cl
    real(dp), intent(in) :: lhv0
    real(dp), intent(in) :: psat0
    real(dp), intent(in) :: tem00
    real(dp), intent(in) :: eps
    real(dp), intent(in) :: dwatr
    real(dp), intent(out) :: rhoe_t(ka)
    real(dp), intent(out) :: evaporate(ka)
    real(dp), intent(out) :: pcsat(ka)
    logical, intent(out) :: guard_pass
    real(RP) :: d3(ka,1,1), p3(ka,1,1), t3(ka,1,1), q4(ka,1,1,3), cp3(ka,1,1), cv3(ka,1,1), &
        r3(ka,1,1), e3(ka,1,1)

    call setup_once()
    d3(:,1,1) = dens
    p3(:,1,1) = pres
    t3(:,1,1) = temp
    q4(:,1,1,1) = qv
    q4(:,1,1,2) = qc
    q4(:,1,1,3) = qr
    cp3(:,1,1) = cptot
    cv3(:,1,1) = cvtot
    r3 = 0.0_RP
    e3 = 0.0_RP
    call ATMOS_PHY_MP_kessler_adjustment(ka, ks, ke, 1, 1, 1, 1, 1, 1, d3, p3, dt, &
        t3, q4, cp3, cv3, r3, e3)
    temp = t3(:,1,1)
    qv = q4(:,1,1,1)
    qc = q4(:,1,1,2)
    qr = q4(:,1,1,3)
    cptot = cp3(:,1,1)
    cvtot = cv3(:,1,1)
    rhoe_t = r3(:,1,1)
    evaporate = e3(:,1,1)
    pcsat = 0.0_dp
    pcsat(ks:ke) = captured(ks:ke,1,1)
    guard_pass = .true.
  end subroutine microphysics_kessler_warm_rain_1d_column__adjustment

  subroutine microphysics_kessler_warm_rain_1d_column__terminal_velocity(ka, ks, ke, dens, &
      rhoq_c, rhoq_r, refstate_dens, vterm_c, vterm_r, guard_pass)
    integer, intent(in) :: ka
    integer, intent(in) :: ks
    integer, intent(in) :: ke
    real(dp), intent(in) :: dens(ka)
    real(dp), intent(in) :: rhoq_c(ka)
    real(dp), intent(in) :: rhoq_r(ka)
    real(dp), intent(in) :: refstate_dens(ka)
    real(dp), intent(out) :: vterm_c(ka)
    real(dp), intent(out) :: vterm_r(ka)
    logical, intent(out) :: guard_pass
    real(RP) :: d1(ka), rhoq(ka,2), ref1(ka), vterm(ka,2)

    call setup_once()
    d1 = dens
    rhoq(:,1) = rhoq_c
    rhoq(:,2) = rhoq_r
    ref1 = refstate_dens
    vterm = 0.0_RP
    call ATMOS_PHY_MP_kessler_terminal_velocity(ka, ks, ke, d1, rhoq, ref1, vterm)
    vterm_c = vterm(:,1)
    vterm_r = vterm(:,2)
    guard_pass = .true.
  end subroutine microphysics_kessler_warm_rain_1d_column__terminal_velocity

end module microphysics_kessler_warm_rain_1d_column_model
