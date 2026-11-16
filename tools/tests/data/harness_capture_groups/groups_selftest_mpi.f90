! The test program of `test_harness_capture_groups.py`: a hand-written realization of what
! `harness_fortran_cpu_mpi`'s `controlled_spec.md` §3 states of the self-test's
! `l0_capture_groups_pass` case, over the hand-written model of `data/driver_e2e/mpi/`:
! `__init` first, every rank runs every case and reads the case file itself, rank 0 alone
! calls the capture operations, and every rank calls `__finalize` last. Every
! other case id commits the placeholder `final` record a case of the suite commits for the
! variables of this case. It is a test fixture, not a certified source: it shows the case's
! predicates hold on a model that follows the contract and fail on one that does not.
program groups_selftest
  use harness_fortran_cpu_mpi_model, only: dp, case_id_len, harness_fortran_cpu_mpi__h_var, &
    harness_fortran_cpu_mpi__parse_cases, harness_fortran_cpu_mpi__read_case, &
    harness_fortran_cpu_mpi__get_real, harness_fortran_cpu_mpi__get_int, &
    harness_fortran_cpu_mpi__get_array_r1, harness_fortran_cpu_mpi__get_array_r2, &
    harness_fortran_cpu_mpi__get_array_r3, harness_fortran_cpu_mpi__get_array_r4, &
    harness_fortran_cpu_mpi__capture_open, harness_fortran_cpu_mpi__capture_real, &
    harness_fortran_cpu_mpi__capture_array_r1, harness_fortran_cpu_mpi__capture_array_r2, &
    harness_fortran_cpu_mpi__capture_array_r3, harness_fortran_cpu_mpi__capture_array_r4, &
    harness_fortran_cpu_mpi__capture_commit, harness_fortran_cpu_mpi__capture_close, &
    harness_fortran_cpu_mpi__init, harness_fortran_cpu_mpi__finalize, &
    harness_fortran_cpu_mpi__comm_rank, harness_fortran_cpu_mpi__comm_size
  implicit none
  character(len=4096) :: tokens(64)
  character(len=case_id_len) :: case_ids(64)
  ! The series names, padded to one width as an array constructor pads them.
  character(len=13) :: series_names(2)
  integer :: argc, i, ncases
  logical :: ok

  call harness_fortran_cpu_mpi__init()
  argc = command_argument_count()
  do i = 1, argc
    call get_command_argument(i, tokens(i))
  end do
  call harness_fortran_cpu_mpi__parse_cases(tokens, argc, case_ids, ncases, ok)
  if (.not. ok) error stop 3
  series_names = [character(len=13) :: 'g_a', 'g_series_long']
  do i = 1, ncases
    if (trim(case_ids(i)) == 'l0_capture_groups_pass') then
      call groups_case(case_ids(i))
    else if (harness_fortran_cpu_mpi__comm_rank() == 0) then
      call placeholder_case(case_ids(i))
    end if
  end do
  if (harness_fortran_cpu_mpi__comm_rank() == 0) &
    & write(*, '(a, i0)') 'ranks ', harness_fortran_cpu_mpi__comm_size()
  call harness_fortran_cpu_mpi__finalize()

contains

  function variables(n_a, n_b) result(vars)
    integer, intent(in) :: n_a, n_b
    type(harness_fortran_cpu_mpi__h_var) :: vars(8)

    vars(1) = harness_fortran_cpu_mpi__h_var('g_s', 0, [0, 0, 0, 0], n_a, .true.)
    vars(2) = harness_fortran_cpu_mpi__h_var('g_r1', 1, [3, 0, 0, 0], n_a, .true.)
    vars(3) = harness_fortran_cpu_mpi__h_var('g_r2', 2, [2, 3, 0, 0], n_a, .true.)
    vars(4) = harness_fortran_cpu_mpi__h_var('g_r3', 3, [2, 3, 4, 0], n_b, .true.)
    vars(5) = harness_fortran_cpu_mpi__h_var('g_r4', 4, [2, 3, 4, 5], n_b, .true.)
    vars(6) = harness_fortran_cpu_mpi__h_var('g_never', 1, [2, 0, 0, 0], 0, .false.)
    vars(7) = harness_fortran_cpu_mpi__h_var('g_z1', 1, [0, 0, 0, 0], n_a, .true.)
    vars(8) = harness_fortran_cpu_mpi__h_var('g_z2', 2, [3, 0, 0, 0], n_b, .true.)
  end function variables

  subroutine groups_case(case_id)
    character(len=*), intent(in) :: case_id
    real(dp) :: s, dt, t, d
    real(dp), allocatable :: r1(:), r2(:, :), r3(:, :, :), r4(:, :, :, :)
    real(dp) :: never(2), z1(0), z2(3, 0)
    integer :: n, every_a, every_b, every_p, k
    logical :: read_ok, due_a, due_b

    call harness_fortran_cpu_mpi__read_case(case_id, read_ok)
    if (.not. read_ok) error stop 4
    n = harness_fortran_cpu_mpi__get_int('inputs.initial.g_n')
    every_a = harness_fortran_cpu_mpi__get_int('inputs.initial.g_every_a')
    every_b = harness_fortran_cpu_mpi__get_int('inputs.initial.g_every_b')
    every_p = harness_fortran_cpu_mpi__get_int('inputs.initial.g_every_p')
    dt = harness_fortran_cpu_mpi__get_real('inputs.initial.g_dt')
    s = harness_fortran_cpu_mpi__get_real('inputs.initial.g_s')
    call harness_fortran_cpu_mpi__get_array_r1('inputs.initial.g_r1', r1)
    call harness_fortran_cpu_mpi__get_array_r2('inputs.initial.g_r2', r2)
    call harness_fortran_cpu_mpi__get_array_r3('inputs.initial.g_r3', r3)
    call harness_fortran_cpu_mpi__get_array_r4('inputs.initial.g_r4', r4)
    never = 0.0_dp
    if (harness_fortran_cpu_mpi__comm_rank() /= 0) return

    call harness_fortran_cpu_mpi__capture_open(case_id, variables(n / every_a, n / every_b), &
      & n / every_p, series_names, n)
    call harness_fortran_cpu_mpi__capture_real('initial', 'g_s', s)
    call harness_fortran_cpu_mpi__capture_array_r1('initial', 'g_r1', r1)
    call harness_fortran_cpu_mpi__capture_array_r2('initial', 'g_r2', r2)
    call harness_fortran_cpu_mpi__capture_array_r3('initial', 'g_r3', r3)
    call harness_fortran_cpu_mpi__capture_array_r4('initial', 'g_r4', r4)
    call harness_fortran_cpu_mpi__capture_array_r1('initial', 'g_never', never)
    call harness_fortran_cpu_mpi__capture_array_r1('initial', 'g_z1', z1)
    call harness_fortran_cpu_mpi__capture_array_r2('initial', 'g_z2', z2)
    call harness_fortran_cpu_mpi__capture_commit('initial', 0, 0.0_dp)
    do k = 1, n
      d = real(k, dp)
      t = dt * d
      ! The first name is passed as the padded array element, the second as a literal.
      call harness_fortran_cpu_mpi__capture_real('series', series_names(1), s + d)
      call harness_fortran_cpu_mpi__capture_real('series', 'g_series_long', r1(1) - d)
      call harness_fortran_cpu_mpi__capture_commit('series', k, t)
      due_a = mod(k, every_a) == 0
      due_b = mod(k, every_b) == 0
      if (due_a) then
        call harness_fortran_cpu_mpi__capture_real('history', 'g_s', s + d)
        call harness_fortran_cpu_mpi__capture_array_r1('history', 'g_r1', r1 + d)
        call harness_fortran_cpu_mpi__capture_array_r2('history', 'g_r2', r2 + d)
        call harness_fortran_cpu_mpi__capture_array_r1('history', 'g_z1', z1)
      end if
      if (due_b) then
        call harness_fortran_cpu_mpi__capture_array_r3('history', 'g_r3', r3 + d)
        call harness_fortran_cpu_mpi__capture_array_r4('history', 'g_r4', r4 + d)
        call harness_fortran_cpu_mpi__capture_array_r2('history', 'g_z2', z2)
      end if
      if (due_a .or. due_b) call harness_fortran_cpu_mpi__capture_commit('history', k, t)
      if (mod(k, every_p) == 0) then
        call harness_fortran_cpu_mpi__capture_real('restart_point', 'g_s', s + d)
        call harness_fortran_cpu_mpi__capture_array_r1('restart_point', 'g_r1', r1 + d)
        call harness_fortran_cpu_mpi__capture_array_r2('restart_point', 'g_r2', r2 + d)
        call harness_fortran_cpu_mpi__capture_array_r3('restart_point', 'g_r3', r3 + d)
        call harness_fortran_cpu_mpi__capture_array_r4('restart_point', 'g_r4', r4 + d)
        call harness_fortran_cpu_mpi__capture_array_r1('restart_point', 'g_z1', z1)
        call harness_fortran_cpu_mpi__capture_array_r2('restart_point', 'g_z2', z2)
        call harness_fortran_cpu_mpi__capture_commit('restart_point', k, t)
      end if
    end do
    d = real(n, dp)
    call harness_fortran_cpu_mpi__capture_real('final', 'g_s', s + d)
    call harness_fortran_cpu_mpi__capture_array_r1('final', 'g_r1', r1 + d)
    call harness_fortran_cpu_mpi__capture_array_r2('final', 'g_r2', r2 + d)
    call harness_fortran_cpu_mpi__capture_array_r3('final', 'g_r3', r3 + d)
    call harness_fortran_cpu_mpi__capture_array_r4('final', 'g_r4', r4 + d)
    call harness_fortran_cpu_mpi__capture_array_r1('final', 'g_never', never)
    call harness_fortran_cpu_mpi__capture_array_r1('final', 'g_z1', z1)
    call harness_fortran_cpu_mpi__capture_array_r2('final', 'g_z2', z2)
    call harness_fortran_cpu_mpi__capture_commit('final', n, dt * d)
    call harness_fortran_cpu_mpi__capture_close()
  end subroutine groups_case

  ! A case of another aspect: every variable of this case as a placeholder in its one `final`
  ! record, the same series names, and no row.
  subroutine placeholder_case(case_id)
    character(len=*), intent(in) :: case_id
    real(dp) :: r1(3), r2(2, 3), r3(2, 3, 4), r4(2, 3, 4, 5), never(2), z1(0), z2(3, 0)

    r1 = 0.0_dp
    r2 = 0.0_dp
    r3 = 0.0_dp
    r4 = 0.0_dp
    never = 0.0_dp
    call harness_fortran_cpu_mpi__capture_open(case_id, variables(0, 0), 0, series_names, 0)
    call harness_fortran_cpu_mpi__capture_real('final', 'g_s', 0.0_dp)
    call harness_fortran_cpu_mpi__capture_array_r1('final', 'g_r1', r1)
    call harness_fortran_cpu_mpi__capture_array_r2('final', 'g_r2', r2)
    call harness_fortran_cpu_mpi__capture_array_r3('final', 'g_r3', r3)
    call harness_fortran_cpu_mpi__capture_array_r4('final', 'g_r4', r4)
    call harness_fortran_cpu_mpi__capture_array_r1('final', 'g_never', never)
    call harness_fortran_cpu_mpi__capture_array_r1('final', 'g_z1', z1)
    call harness_fortran_cpu_mpi__capture_array_r2('final', 'g_z2', z2)
    call harness_fortran_cpu_mpi__capture_commit('final', 1, 0.0_dp)
    call harness_fortran_cpu_mpi__capture_close()
  end subroutine placeholder_case
end program groups_selftest
