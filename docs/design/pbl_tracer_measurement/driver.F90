! Offline driver: one case of pbl_tracer_diffusion_1d_column through the oracle adapter, over the
! original SCALE procedure. Reads from standard input
!   ka ks ke
!   dt sflx_q mass
!   dens(1:ka)  qtrc(1:ka)  kh(1:ka)  cz(1:ka)  fz(1:ka)  f2h_above(1:ka)  f2h_below(1:ka)
! (each array one record), and writes `rhoq_t` and `flx`, one record each, to standard output.
program driver
  use pbl_tracer_diffusion_1d_column_model, only: pbl_tracer_diffusion_1d_column__tendency
  implicit none
  integer, parameter :: dp = kind(0.0d0)
  integer :: ka, ks, ke
  real(dp) :: dt, sflx_q, mass
  real(dp), allocatable :: dens(:), qtrc(:), kh(:), cz(:), fz(:), f2h_above(:), f2h_below(:)
  real(dp), allocatable :: rhoq_t(:), flx(:)
  logical :: guard_pass

  read(*,*) ka, ks, ke
  read(*,*) dt, sflx_q, mass
  allocate(dens(ka), qtrc(ka), kh(ka), cz(ka), fz(ka), f2h_above(ka), f2h_below(ka))
  allocate(rhoq_t(ka), flx(ka))
  read(*,*) dens
  read(*,*) qtrc
  read(*,*) kh
  read(*,*) cz
  read(*,*) fz
  read(*,*) f2h_above
  read(*,*) f2h_below
  call pbl_tracer_diffusion_1d_column__tendency(ka, ks, ke, dens, qtrc, sflx_q, kh, mass, cz, fz, &
      f2h_above, f2h_below, dt, rhoq_t, flx, guard_pass)
  write(*,'(*(1x,es25.17e3))') rhoq_t
  write(*,'(*(1x,es25.17e3))') flx
end program driver
