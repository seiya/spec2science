! Oracle adapter (docs/SPEC.md requirement 17): publishes this node's Controlled Spec §5.1 surface
! over the original SCALE procedures of `oracle.yaml`'s `origin`, linked from the external
! library its build names. It does what docs/design/kessler_measurement/driver.F90 does per column.
!
! - The constant arguments are not read: the original holds them as compile-time parameters of
!   `scale_const`, so the `default` build links the library built from the original and the
!   `alt` build the one built with the suite's `alt_constants` (`oracle.yaml` routes the
!   `constants: alt` cases to it).
! - `intent(out)` arrays start at 0 and the original writes interior cells only, so a halo cell
!   of an output is 0, as the Controlled Spec defines it.
! - `guard_pass` and `converged` are not outputs of the original: it has no input guard, and it
!   aborts on a cell that does not converge, which ends the oracle run. A call that returns did
!   not abort, which is what both state on the cases the oracle runs (every compared case is a
!   `pass` case).
module microphysics_saturation_liquid_1d_column_model
  use, intrinsic :: iso_fortran_env, only: real64
  use scale_precision, only: RP
  use scale_io, only: IO_FID_CONF
  use scale_const, only: CONST_setup
  use scale_atmos_saturation, only: ATMOS_SATURATION_setup, ATMOS_SATURATION_dens2qsat_liq
  use scale_atmos_phy_mp_common, only: ATMOS_PHY_MP_saturation_adjustment
  implicit none
  private

  integer, parameter :: dp = real64
  logical :: ready = .false.

  public :: microphysics_saturation_liquid_1d_column__dens2qsat_liq
  public :: microphysics_saturation_liquid_1d_column__saturation_adjustment

contains

  ! The original's setup, once: an empty namelist file keeps every default.
  subroutine setup_once()
    if (ready) return
    open(newunit=IO_FID_CONF, status='scratch', form='formatted')
    call CONST_setup
    call ATMOS_SATURATION_setup
    ready = .true.
  end subroutine setup_once

  subroutine microphysics_saturation_liquid_1d_column__dens2qsat_liq(ka, ks, ke, temp, dens, &
      rvap, cpvap, cl, lhv0, psat0, tem00, qsat, guard_pass)
    integer, intent(in) :: ka
    integer, intent(in) :: ks
    integer, intent(in) :: ke
    real(dp), intent(in) :: temp(ka)
    real(dp), intent(in) :: dens(ka)
    real(dp), intent(in) :: rvap
    real(dp), intent(in) :: cpvap
    real(dp), intent(in) :: cl
    real(dp), intent(in) :: lhv0
    real(dp), intent(in) :: psat0
    real(dp), intent(in) :: tem00
    real(dp), intent(out) :: qsat(ka)
    logical, intent(out) :: guard_pass
    integer :: k
    real(RP) :: q

    call setup_once()
    qsat = 0.0_dp
    do k = ks, ke
      call ATMOS_SATURATION_dens2qsat_liq(real(temp(k), RP), real(dens(k), RP), q)
      qsat(k) = q
    end do
    guard_pass = .true.
  end subroutine microphysics_saturation_liquid_1d_column__dens2qsat_liq

  subroutine microphysics_saturation_liquid_1d_column__saturation_adjustment(ka, ks, ke, dens, &
      temp, qv, qc, cptot, cvtot, rvap, cpvap, cl, lhv0, psat0, tem00, rhoe_d, converged, &
      guard_pass)
    integer, intent(in) :: ka
    integer, intent(in) :: ks
    integer, intent(in) :: ke
    real(dp), intent(in) :: dens(ka)
    real(dp), intent(inout) :: temp(ka)
    real(dp), intent(inout) :: qv(ka)
    real(dp), intent(inout) :: qc(ka)
    real(dp), intent(inout) :: cptot(ka)
    real(dp), intent(inout) :: cvtot(ka)
    real(dp), intent(in) :: rvap
    real(dp), intent(in) :: cpvap
    real(dp), intent(in) :: cl
    real(dp), intent(in) :: lhv0
    real(dp), intent(in) :: psat0
    real(dp), intent(in) :: tem00
    real(dp), intent(out) :: rhoe_d(ka)
    logical, intent(out) :: converged
    logical, intent(out) :: guard_pass
    real(RP) :: d3(ka,1,1), t3(ka,1,1), v3(ka,1,1), c3(ka,1,1), i3(ka,1,1), p3(ka,1,1), &
        w3(ka,1,1), e3(ka,1,1)

    call setup_once()
    d3(:,1,1) = dens
    t3(:,1,1) = temp
    v3(:,1,1) = qv
    c3(:,1,1) = qc
    i3 = -1.0_RP  ! no ice: the liquid branch does not read it
    p3(:,1,1) = cptot
    w3(:,1,1) = cvtot
    e3 = 0.0_RP
    call ATMOS_PHY_MP_saturation_adjustment(ka, ks, ke, 1, 1, 1, 1, 1, 1, d3, .true., &
        t3, v3, c3, i3, p3, w3, e3)
    temp = t3(:,1,1)
    qv = v3(:,1,1)
    qc = c3(:,1,1)
    cptot = p3(:,1,1)
    cvtot = w3(:,1,1)
    rhoe_d = e3(:,1,1)
    converged = .true.
    guard_pass = .true.
  end subroutine microphysics_saturation_liquid_1d_column__saturation_adjustment

end module microphysics_saturation_liquid_1d_column_model
