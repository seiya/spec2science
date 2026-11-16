! A hand-written realization of `harness_fortran_cpu_netcdf@0.1.0` §5.1: `__parse_cases`
! keeping the spec path, `__read_case` and the typed getters (the reading half of
! `harness_fortran_cpu`'s fixture, under this harness's names), the capture operations in the
! `netcdf` layout of
! `docs/backends/output_format/netcdf/CAPTURE_LAYOUT.md` (issue #644 P5b), and `__write_perf`.
! It is a test fixture, not a certified source: it shows the layout is writable through the
! netCDF-Fortran binding and that the host's `netcdf` capture store reads what it writes, and it
! lets the host-rendered driver link and run against the whole of §5.1. The certified harness
! model is generated.
module harness_fortran_cpu_netcdf_model
  use, intrinsic :: iso_fortran_env, only: real64, error_unit
  use, intrinsic :: ieee_arithmetic, only: ieee_is_nan, ieee_is_finite, ieee_value, &
    & ieee_quiet_nan
  use netcdf, only: nf90_create, nf90_clobber, nf90_netcdf4, nf90_def_dim, nf90_unlimited, &
    & nf90_def_var, nf90_def_var_fill, nf90_int, nf90_double, nf90_enddef, nf90_put_var, &
    & nf90_sync, nf90_close, nf90_noerr, nf90_strerror
  implicit none
  private

  integer, parameter, public :: dp = real64
  integer, parameter, public :: case_id_len = 64

  public :: harness_fortran_cpu_netcdf__parse_cases, harness_fortran_cpu_netcdf__read_case
  public :: harness_fortran_cpu_netcdf__get_real, harness_fortran_cpu_netcdf__get_int
  public :: harness_fortran_cpu_netcdf__get_bool
  public :: harness_fortran_cpu_netcdf__get_array_r1, harness_fortran_cpu_netcdf__get_array_r2
  public :: harness_fortran_cpu_netcdf__get_array_r3, harness_fortran_cpu_netcdf__get_array_r4
  public :: harness_fortran_cpu_netcdf__h_var
  public :: harness_fortran_cpu_netcdf__capture_open, harness_fortran_cpu_netcdf__capture_real
  public :: harness_fortran_cpu_netcdf__capture_array_r1
  public :: harness_fortran_cpu_netcdf__capture_array_r2
  public :: harness_fortran_cpu_netcdf__capture_array_r3
  public :: harness_fortran_cpu_netcdf__capture_array_r4
  public :: harness_fortran_cpu_netcdf__capture_commit, harness_fortran_cpu_netcdf__capture_close
  public :: harness_fortran_cpu_netcdf__write_perf

  integer, parameter :: max_depth = 4

  type :: harness_fortran_cpu_netcdf__h_var
    character(len=:), allocatable :: name
    integer :: rank
    integer :: shape(4)
    integer :: history_capacity
    logical :: restart
  end type harness_fortran_cpu_netcdf__h_var

  type :: case_entry
    character(len=:), allocatable :: name
    integer :: depth = 0                ! 0 for a scalar
    integer :: extent(max_depth) = 0
    logical :: integer_token = .false.
    integer :: integer_value = 0
    logical :: integer_fits = .false.
    logical :: bool_token = .false.     ! exactly `1` or `0`
    real(dp), allocatable :: flat(:)    ! document order: the leading index outermost
  end type case_entry

  character(len=:), allocatable, save :: kept_spec_path
  logical, save :: case_loaded = .false.
  type(case_entry), allocatable, save :: entries(:)

  ! The capture writer's state: the open case's file and, per state variable, its dimensions,
  ! its variables in each group and its samples so far; the restart points' and the series'
  ! records so far. `vid_h_roles` and the role arrays hold the ids of `step`, `t`, `count`.
  type :: cap_var_t
    character(len=:), allocatable :: name
    integer :: rank = 0
    integer :: shape(4) = 0
    integer :: dim(4) = 0
    logical :: sampled = .false.
    logical :: restart = .false.
    logical :: h_put = .false.
    integer :: h_count = 0
    integer :: vid_initial = 0, vid_final = 0, vid_history = 0, vid_restart = 0
    integer :: vid_h_roles(3) = 0
  end type cap_var_t
  logical, save :: cap_open = .false.
  character(len=:), allocatable, save :: cap_case
  integer, save :: cap_ncid = 0, cap_complete = 0
  type(cap_var_t), allocatable, save :: cap_vars(:)
  character(len=:), allocatable, save :: cap_snames(:)
  integer, allocatable, save :: cap_svid(:)
  integer, save :: cap_single(2, 3) = 0, cap_rp(3) = 0, cap_s(3) = 0
  integer, save :: cap_n_rp = 0, cap_n_s = 0

contains

  subroutine harness_fortran_cpu_netcdf__parse_cases(tokens, ntokens, case_ids, ncases, ok)
    character(len=*), intent(in) :: tokens(:)
    integer, intent(in) :: ntokens
    character(len=case_id_len), intent(out) :: case_ids(:)
    integer, intent(out) :: ncases
    logical, intent(out) :: ok
    integer :: i, at

    ncases = 0
    ok = .false.
    at = 0
    do i = 1, ntokens
      if (trim(tokens(i)) == '--cases') then
        at = i
        exit
      end if
    end do
    if (at == 0 .or. at + 2 > ntokens) return
    kept_spec_path = trim(tokens(at + 1))
    do i = at + 2, ntokens
      ncases = ncases + 1
      case_ids(ncases) = tokens(i)
    end do
    ok = .true.
  end subroutine harness_fortran_cpu_netcdf__parse_cases

  subroutine harness_fortran_cpu_netcdf__read_case(case_id, ok)
    character(len=*), intent(in) :: case_id
    logical, intent(out) :: ok
    character(len=:), allocatable :: path, doc
    integer :: slash

    ok = .false.
    case_loaded = .false.
    if (allocated(entries)) deallocate (entries)
    if (.not. allocated(kept_spec_path)) return
    slash = index(kept_spec_path, '/', back=.true.)
    path = kept_spec_path(1:slash)//'case_inputs/'//trim(case_id)//'.json'
    call read_whole_file(path, doc, ok)
    if (.not. ok) return
    call parse_document(doc, ok)
    case_loaded = ok
    if (.not. ok .and. allocated(entries)) deallocate (entries)
  end subroutine harness_fortran_cpu_netcdf__read_case

  function harness_fortran_cpu_netcdf__get_real(name) result(x)
    character(len=*), intent(in) :: name
    real(dp) :: x
    integer :: k

    k = found(name)
    if (entries(k)%depth /= 0) call refuse(name, 'an array asked as a scalar')
    x = entries(k)%flat(1)
  end function harness_fortran_cpu_netcdf__get_real

  function harness_fortran_cpu_netcdf__get_int(name) result(i)
    character(len=*), intent(in) :: name
    integer :: i
    integer :: k

    k = found(name)
    if (entries(k)%depth /= 0) call refuse(name, 'an array asked as a scalar')
    if (.not. (entries(k)%integer_token .and. entries(k)%integer_fits)) then
      call refuse(name, 'not an integer token of the default integer')
    end if
    i = entries(k)%integer_value
  end function harness_fortran_cpu_netcdf__get_int

  function harness_fortran_cpu_netcdf__get_bool(name) result(b)
    character(len=*), intent(in) :: name
    logical :: b
    integer :: k

    k = found(name)
    if (entries(k)%depth /= 0) call refuse(name, 'an array asked as a scalar')
    if (.not. entries(k)%bool_token) call refuse(name, 'neither of the tokens 1 and 0')
    b = entries(k)%integer_value == 1
  end function harness_fortran_cpu_netcdf__get_bool

  subroutine harness_fortran_cpu_netcdf__get_array_r1(name, a)
    character(len=*), intent(in) :: name
    real(dp), allocatable, intent(out) :: a(:)
    integer :: k, e(max_depth)

    k = array_entry(name, 1, e)
    allocate (a(e(1)))
    a = entries(k)%flat
  end subroutine harness_fortran_cpu_netcdf__get_array_r1

  subroutine harness_fortran_cpu_netcdf__get_array_r2(name, a)
    character(len=*), intent(in) :: name
    real(dp), allocatable, intent(out) :: a(:, :)
    integer :: k, e(max_depth), i1, i2, at

    k = array_entry(name, 2, e)
    allocate (a(e(1), e(2)))
    at = 0
    do i1 = 1, e(1)
      do i2 = 1, e(2)
        at = at + 1
        a(i1, i2) = entries(k)%flat(at)
      end do
    end do
  end subroutine harness_fortran_cpu_netcdf__get_array_r2

  subroutine harness_fortran_cpu_netcdf__get_array_r3(name, a)
    character(len=*), intent(in) :: name
    real(dp), allocatable, intent(out) :: a(:, :, :)
    integer :: k, e(max_depth), i1, i2, i3, at

    k = array_entry(name, 3, e)
    allocate (a(e(1), e(2), e(3)))
    at = 0
    do i1 = 1, e(1)
      do i2 = 1, e(2)
        do i3 = 1, e(3)
          at = at + 1
          a(i1, i2, i3) = entries(k)%flat(at)
        end do
      end do
    end do
  end subroutine harness_fortran_cpu_netcdf__get_array_r3

  subroutine harness_fortran_cpu_netcdf__get_array_r4(name, a)
    character(len=*), intent(in) :: name
    real(dp), allocatable, intent(out) :: a(:, :, :, :)
    integer :: k, e(max_depth), i1, i2, i3, i4, at

    k = array_entry(name, 4, e)
    allocate (a(e(1), e(2), e(3), e(4)))
    at = 0
    do i1 = 1, e(1)
      do i2 = 1, e(2)
        do i3 = 1, e(3)
          do i4 = 1, e(4)
            at = at + 1
            a(i1, i2, i3, i4) = entries(k)%flat(at)
          end do
        end do
      end do
    end do
  end subroutine harness_fortran_cpu_netcdf__get_array_r4

  ! --------------------------------------------------------------------------- the getters' lookup

  integer function found(name)
    character(len=*), intent(in) :: name
    integer :: k

    if (.not. case_loaded) call refuse(name, 'no case is loaded')
    do k = 1, size(entries)
      if (entries(k)%name == name) then
        found = k
        return
      end if
    end do
    found = 0
    call refuse(name, 'the case does not hold this name')
  end function found

  integer function array_entry(name, rank, e)
    character(len=*), intent(in) :: name
    integer, intent(in) :: rank
    integer, intent(out) :: e(max_depth)
    integer :: k

    k = found(name)
    if (entries(k)%depth == 0) call refuse(name, 'a scalar asked as an array')
    if (size(entries(k)%flat) == 0) then
      if (entries(k)%depth > rank) call refuse(name, 'a nesting depth other than the rank')
    else if (entries(k)%depth /= rank) then
      call refuse(name, 'a nesting depth other than the rank')
    end if
    e = entries(k)%extent
    array_entry = k
  end function array_entry

  subroutine refuse(name, why)
    character(len=*), intent(in) :: name, why

    write (*, '(a)') 'case file: '//name//': '//why
    error stop 2
  end subroutine refuse

  ! --------------------------------------------------------------------------------- the reading

  subroutine read_whole_file(path, doc, ok)
    character(len=*), intent(in) :: path
    character(len=:), allocatable, intent(out) :: doc
    logical, intent(out) :: ok
    integer :: unit, length, stat

    ok = .false.
    open (newunit=unit, file=path, access='stream', form='unformatted', status='old', &
          action='read', iostat=stat)
    if (stat /= 0) return
    inquire (unit=unit, size=length)
    allocate (character(len=length) :: doc)
    read (unit, iostat=stat) doc
    close (unit)
    ok = stat == 0
  end subroutine read_whole_file

  subroutine parse_document(doc, ok)
    character(len=*), intent(in) :: doc
    logical, intent(out) :: ok
    type(case_entry) :: one
    type(case_entry), allocatable :: grown(:)
    integer :: at, n

    ok = .false.
    at = 1
    n = 0
    allocate (entries(0))
    call skip_blank(doc, at)
    if (.not. take(doc, at, '{')) return
    call skip_blank(doc, at)
    if (.not. take(doc, at, '}')) then
      do
        call parse_member(doc, at, one, ok)
        if (.not. ok) return
        ok = .false.
        n = n + 1
        allocate (grown(n))
        grown(1:n - 1) = entries
        grown(n) = one
        call move_alloc(grown, entries)
        call skip_blank(doc, at)
        if (take(doc, at, '}')) exit
        if (.not. take(doc, at, ',')) return
      end do
    end if
    call skip_blank(doc, at)
    ok = at > len(doc)
  end subroutine parse_document

  subroutine parse_member(doc, at, one, ok)
    character(len=*), intent(in) :: doc
    integer, intent(inout) :: at
    type(case_entry), intent(out) :: one
    logical, intent(out) :: ok
    integer :: close_at, leaf_depth

    ok = .false.
    call skip_blank(doc, at)
    if (.not. take(doc, at, '"')) return
    close_at = index(doc(at:), '"')
    if (close_at == 0) return
    close_at = at + close_at - 1
    one%name = doc(at:close_at - 1)
    if (index(one%name, '\') /= 0) return
    at = close_at + 1
    call skip_blank(doc, at)
    if (.not. take(doc, at, ':')) return
    call skip_blank(doc, at)
    allocate (one%flat(0))
    if (at <= len(doc) .and. doc(min(at, len(doc)):min(at, len(doc))) == '[') then
      leaf_depth = 0
      one%extent = -1
      call parse_array(doc, at, 1, one, leaf_depth, ok)
      if (.not. ok) return
      where (one%extent < 0) one%extent = 0
    else
      call parse_number(doc, at, one, ok)
    end if
  end subroutine parse_member

  recursive subroutine parse_array(doc, at, level, one, leaf_depth, ok)
    character(len=*), intent(in) :: doc
    integer, intent(inout) :: at
    integer, intent(in) :: level
    type(case_entry), intent(inout) :: one
    integer, intent(inout) :: leaf_depth
    logical, intent(out) :: ok
    integer :: count
    logical :: saw_array, saw_number

    ok = .false.
    saw_array = .false.
    saw_number = .false.
    if (level > max_depth) return
    if (.not. take(doc, at, '[')) return
    one%depth = max(one%depth, level)
    count = 0
    call skip_blank(doc, at)
    if (.not. take(doc, at, ']')) then
      do
        call skip_blank(doc, at)
        if (at > len(doc)) return
        if (doc(at:at) == '[') then
          if (saw_number) return
          saw_array = .true.
          call parse_array(doc, at, level + 1, one, leaf_depth, ok)
        else
          if (saw_array) return
          saw_number = .true.
          if (leaf_depth /= 0 .and. leaf_depth /= level) return
          leaf_depth = level
          call parse_number(doc, at, one, ok)
        end if
        if (.not. ok) return
        ok = .false.
        count = count + 1
        call skip_blank(doc, at)
        if (take(doc, at, ']')) exit
        if (.not. take(doc, at, ',')) return
      end do
    end if
    if (one%extent(level) >= 0 .and. one%extent(level) /= count) return
    one%extent(level) = count
    ok = .true.
  end subroutine parse_array

  subroutine parse_number(doc, at, one, ok)
    character(len=*), intent(in) :: doc
    integer, intent(inout) :: at
    type(case_entry), intent(inout) :: one
    logical, intent(out) :: ok
    integer :: start, stat, digits_from
    logical :: integer_token
    real(dp) :: value

    ok = .false.
    start = at
    integer_token = .true.
    if (peek(doc, at) == '-') at = at + 1
    digits_from = at
    call skip_digits(doc, at)
    if (at == digits_from) return
    if (doc(digits_from:digits_from) == '0' .and. at > digits_from + 1) return
    if (peek(doc, at) == '.') then
      integer_token = .false.
      at = at + 1
      digits_from = at
      call skip_digits(doc, at)
      if (at == digits_from) return
    end if
    if (peek(doc, at) == 'e' .or. peek(doc, at) == 'E') then
      integer_token = .false.
      at = at + 1
      if (peek(doc, at) == '+' .or. peek(doc, at) == '-') at = at + 1
      digits_from = at
      call skip_digits(doc, at)
      if (at == digits_from) return
    end if
    read (doc(start:at - 1), *, iostat=stat) value
    if (stat /= 0) return
    if (.not. ieee_is_finite(value)) return
    one%flat = [one%flat, value]
    if (one%depth == 0) then
      one%integer_token = integer_token
      one%bool_token = doc(start:at - 1) == '1' .or. doc(start:at - 1) == '0'
      if (integer_token) then
        read (doc(start:at - 1), *, iostat=stat) one%integer_value
        one%integer_fits = stat == 0
      end if
    end if
    ok = .true.
  end subroutine parse_number

  character function peek(doc, at)
    character(len=*), intent(in) :: doc
    integer, intent(in) :: at

    peek = ' '
    if (at <= len(doc)) peek = doc(at:at)
  end function peek

  subroutine skip_digits(doc, at)
    character(len=*), intent(in) :: doc
    integer, intent(inout) :: at

    do while (at <= len(doc))
      if (doc(at:at) < '0' .or. doc(at:at) > '9') exit
      at = at + 1
    end do
  end subroutine skip_digits

  subroutine skip_blank(doc, at)
    character(len=*), intent(in) :: doc
    integer, intent(inout) :: at

    do while (at <= len(doc))
      if (index(' '//achar(9)//achar(10)//achar(13), doc(at:at)) == 0) exit
      at = at + 1
    end do
  end subroutine skip_blank

  logical function take(doc, at, c)
    character(len=*), intent(in) :: doc
    integer, intent(inout) :: at
    character, intent(in) :: c

    take = .false.
    if (at > len(doc)) return
    if (doc(at:at) /= c) return
    at = at + 1
    take = .true.
  end function take

  ! --- the capture operations: one netCDF-4 file per case (CAPTURE_LAYOUT.md) ---

  ! Start the output of one case: create its file and define every dimension and variable a
  ! record of the case can hold, then set every count to 0. A variable has a `history` group
  ! when the case may sample it (`history_capacity > 0`) and a `restart_point` value when a
  ! restart point holds it; the record dimensions are unlimited, so no capacity bounds them.
  subroutine harness_fortran_cpu_netcdf__capture_open(case_id, vars, restart_point_capacity, &
      & series_names, series_capacity)
    character(len=*), intent(in) :: case_id
    type(harness_fortran_cpu_netcdf__h_var), intent(in) :: vars(:)
    integer, intent(in) :: restart_point_capacity
    character(len=*), intent(in) :: series_names(:)
    integer, intent(in) :: series_capacity
    integer :: k, r, nv, ns, dim_rp, dim_s, dim_h, dims(5)
    character(len=:), allocatable :: v

    if (cap_open) call cap_refuse('capture_open', 'a case is already open')
    if (restart_point_capacity < 0 .or. series_capacity < 0) &
      & call cap_refuse('capture_open', 'a negative capacity')
    nv = size(vars)
    ns = size(series_names)
    do k = 1, ns
      if (trim(series_names(k)) == 'step' .or. trim(series_names(k)) == 't') &
        & call cap_refuse('capture_open', 'series name '//trim(series_names(k))// &
        & ' is the commit''s own')
    end do
    if (allocated(cap_vars)) deallocate(cap_vars)
    allocate(cap_vars(nv))
    do k = 1, nv
      cap_vars(k)%name = trim(vars(k)%name)
      cap_vars(k)%rank = vars(k)%rank
      cap_vars(k)%shape = vars(k)%shape
      cap_vars(k)%sampled = vars(k)%history_capacity > 0
      cap_vars(k)%restart = vars(k)%restart
      cap_vars(k)%h_count = 0
      cap_vars(k)%h_put = .false.
      if (vars(k)%rank < 0 .or. vars(k)%rank > 4) &
        & call cap_refuse('capture_open', 'variable '//cap_vars(k)%name//' has no rank 0..4')
    end do
    if (allocated(cap_snames)) deallocate(cap_snames)
    allocate(character(len=len(series_names)) :: cap_snames(ns))
    cap_snames = series_names
    if (allocated(cap_svid)) deallocate(cap_svid)
    allocate(cap_svid(ns))
    cap_case = trim(case_id)

    call nc(nf90_create('raw/state_snapshots/' // cap_case // '.nc', &
      & ior(nf90_clobber, nf90_netcdf4), cap_ncid), 'create')
    call nc(nf90_def_var(cap_ncid, 'complete', nf90_int, cap_complete), 'complete')
    call nc(nf90_def_var_fill(cap_ncid, cap_complete, 0, 0), 'complete fill')
    ! the spatial dimensions, fastest first; an extent of 0 is unlimited and never written
    do k = 1, nv
      v = cap_vars(k)%name
      do r = 1, cap_vars(k)%rank
        if (cap_vars(k)%shape(r) == 0) then
          call nc(nf90_def_dim(cap_ncid, v // '__d' // itoa(r), nf90_unlimited, &
            & cap_vars(k)%dim(r)), 'dimension')
        else
          call nc(nf90_def_dim(cap_ncid, v // '__d' // itoa(r), cap_vars(k)%shape(r), &
            & cap_vars(k)%dim(r)), 'dimension')
        end if
      end do
    end do
    call nc(nf90_def_dim(cap_ncid, 'restart_point__n', nf90_unlimited, dim_rp), 'dimension')
    call nc(nf90_def_dim(cap_ncid, 'series__n', nf90_unlimited, dim_s), 'dimension')
    ! the single groups
    do k = 1, nv
      r = cap_vars(k)%rank
      dims(1:r) = cap_vars(k)%dim(1:r)
      cap_vars(k)%vid_initial = def_double('initial__' // cap_vars(k)%name, dims(1:r))
      cap_vars(k)%vid_final = def_double('final__' // cap_vars(k)%name, dims(1:r))
    end do
    call def_roles('initial', [integer ::], cap_single(1, :))
    call def_roles('final', [integer ::], cap_single(2, :))
    ! the samples, a record dimension per sampled variable
    do k = 1, nv
      if (.not. cap_vars(k)%sampled) cycle
      v = cap_vars(k)%name
      r = cap_vars(k)%rank
      call nc(nf90_def_dim(cap_ncid, 'history__' // v // '__n', nf90_unlimited, dim_h), &
        & 'dimension')
      dims(1:r) = cap_vars(k)%dim(1:r)
      dims(r + 1) = dim_h
      cap_vars(k)%vid_history = def_double('history__' // v, dims(1:r + 1))
      call def_roles('history__' // v, [dim_h], cap_vars(k)%vid_h_roles)
    end do
    ! the restart points
    do k = 1, nv
      if (.not. cap_vars(k)%restart) cycle
      r = cap_vars(k)%rank
      dims(1:r) = cap_vars(k)%dim(1:r)
      dims(r + 1) = dim_rp
      cap_vars(k)%vid_restart = def_double('restart_point__' // cap_vars(k)%name, &
        & dims(1:r + 1))
    end do
    call def_roles('restart_point', [dim_rp], cap_rp)
    ! the series
    do k = 1, ns
      cap_svid(k) = def_double('series__' // trim(cap_snames(k)), [dim_s])
    end do
    call def_roles('series', [dim_s], cap_s)
    call nc(nf90_enddef(cap_ncid), 'enddef')
    ! every count starts at 0
    call put_int(cap_single(1, 3), 0)
    call put_int(cap_single(2, 3), 0)
    call put_int(cap_rp(3), 0)
    call put_int(cap_s(3), 0)
    do k = 1, nv
      if (cap_vars(k)%sampled) call put_int(cap_vars(k)%vid_h_roles(3), 0)
    end do
    cap_n_rp = 0
    cap_n_s = 0
    cap_open = .true.
  end subroutine harness_fortran_cpu_netcdf__capture_open

  ! One scalar into the open record of `group`; in `series`, into the open row.
  subroutine harness_fortran_cpu_netcdf__capture_real(group, name, x)
    character(len=*), intent(in) :: group
    character(len=*), intent(in) :: name
    real(dp), intent(in) :: x
    integer :: k

    if (.not. cap_open) call cap_refuse('capture_real', 'no case is open')
    if (group == 'series') then
      do k = 1, size(cap_snames)
        if (trim(cap_snames(k)) == trim(name)) then
          call nc(nf90_put_var(cap_ncid, cap_svid(k), [x], start=[cap_n_s + 1], &
            & count=[1]), 'series value')
          return
        end if
      end do
      call cap_refuse('capture_real', 'no series name '//trim(name))
    end if
    call put_values(group, name, 0, [x])
  end subroutine harness_fortran_cpu_netcdf__capture_real

  subroutine harness_fortran_cpu_netcdf__capture_array_r1(group, name, a)
    character(len=*), intent(in) :: group
    character(len=*), intent(in) :: name
    real(dp), intent(in) :: a(:)

    call put_values(group, name, 1, a)
  end subroutine harness_fortran_cpu_netcdf__capture_array_r1

  subroutine harness_fortran_cpu_netcdf__capture_array_r2(group, name, a)
    character(len=*), intent(in) :: group
    character(len=*), intent(in) :: name
    real(dp), intent(in) :: a(:, :)

    call put_values(group, name, 2, reshape(a, [size(a)]), shape(a))
  end subroutine harness_fortran_cpu_netcdf__capture_array_r2

  subroutine harness_fortran_cpu_netcdf__capture_array_r3(group, name, a)
    character(len=*), intent(in) :: group
    character(len=*), intent(in) :: name
    real(dp), intent(in) :: a(:, :, :)

    call put_values(group, name, 3, reshape(a, [size(a)]), shape(a))
  end subroutine harness_fortran_cpu_netcdf__capture_array_r3

  subroutine harness_fortran_cpu_netcdf__capture_array_r4(group, name, a)
    character(len=*), intent(in) :: group
    character(len=*), intent(in) :: name
    real(dp), intent(in) :: a(:, :, :, :)

    call put_values(group, name, 4, reshape(a, [size(a)]), shape(a))
  end subroutine harness_fortran_cpu_netcdf__capture_array_r4

  ! Close the open record of `group`: its step and time at its index, then its count.
  subroutine harness_fortran_cpu_netcdf__capture_commit(group, step, t)
    character(len=*), intent(in) :: group
    integer, intent(in) :: step
    real(dp), intent(in) :: t
    integer :: k, g

    if (.not. cap_open) call cap_refuse('capture_commit', 'no case is open')
    select case (group)
    case ('initial', 'final')
      g = merge(1, 2, group == 'initial')
      call put_int(cap_single(g, 1), step)
      call nc(nf90_put_var(cap_ncid, cap_single(g, 2), t), 'time')
      call put_int(cap_single(g, 3), 1)
    case ('history')
      do k = 1, size(cap_vars)
        if (.not. cap_vars(k)%h_put) cycle
        cap_vars(k)%h_count = cap_vars(k)%h_count + 1
        call nc(nf90_put_var(cap_ncid, cap_vars(k)%vid_h_roles(1), [step], &
          & start=[cap_vars(k)%h_count], count=[1]), 'history step')
        call nc(nf90_put_var(cap_ncid, cap_vars(k)%vid_h_roles(2), [t], &
          & start=[cap_vars(k)%h_count], count=[1]), 'history time')
        call put_int(cap_vars(k)%vid_h_roles(3), cap_vars(k)%h_count)
        cap_vars(k)%h_put = .false.
      end do
    case ('restart_point')
      cap_n_rp = cap_n_rp + 1
      call stack_roles(cap_rp, cap_n_rp, step, t)
    case ('series')
      cap_n_s = cap_n_s + 1
      call stack_roles(cap_s, cap_n_s, step, t)
    case default
      call cap_refuse('capture_commit', 'no group '//trim(group))
    end select
  end subroutine harness_fortran_cpu_netcdf__capture_commit

  ! End the case's output: everything to disk, then `complete`, then the close.
  subroutine harness_fortran_cpu_netcdf__capture_close()
    if (.not. cap_open) call cap_refuse('capture_close', 'no case is open')
    call nc(nf90_sync(cap_ncid), 'sync')
    call put_int(cap_complete, 1)
    call nc(nf90_close(cap_ncid), 'close')
    cap_open = .false.
  end subroutine harness_fortran_cpu_netcdf__capture_close

  ! --- the capture writer's own procedures (not published) ---

  subroutine cap_refuse(op, why)
    character(len=*), intent(in) :: op, why

    write(error_unit, '(A)') 'error: harness_fortran_cpu_netcdf__'//op//': '//why
    error stop 2
  end subroutine cap_refuse

  subroutine nc(status, what)
    integer, intent(in) :: status
    character(len=*), intent(in) :: what

    if (status /= nf90_noerr) call cap_refuse('capture', what//': '//trim(nf90_strerror(status)))
  end subroutine nc

  function itoa(i) result(s)
    integer, intent(in) :: i
    character(len=:), allocatable :: s
    character(len=32) :: buf

    write(buf, '(I0)') i
    s = trim(buf)
  end function itoa

  ! A `double` variable filled with NaN, fill mode on.
  integer function def_double(name, dims) result(varid)
    character(len=*), intent(in) :: name
    integer, intent(in) :: dims(:)

    if (size(dims) == 0) then
      call nc(nf90_def_var(cap_ncid, name, nf90_double, varid), 'variable '//name)
    else
      call nc(nf90_def_var(cap_ncid, name, nf90_double, dims, varid), 'variable '//name)
    end if
    call nc(nf90_def_var_fill(cap_ncid, varid, 0, ieee_value(0.0_dp, ieee_quiet_nan)), &
      & 'fill '//name)
  end function def_double

  ! A group's `step` and `t` on `dims` (none for a single group) and its scalar `count`.
  subroutine def_roles(prefix, dims, ids)
    character(len=*), intent(in) :: prefix
    integer, intent(in) :: dims(:)
    integer, intent(out) :: ids(3)

    if (size(dims) == 0) then
      call nc(nf90_def_var(cap_ncid, prefix // '__step', nf90_int, ids(1)), prefix)
    else
      call nc(nf90_def_var(cap_ncid, prefix // '__step', nf90_int, dims, ids(1)), prefix)
    end if
    ids(2) = def_double(prefix // '__t', dims)
    call nc(nf90_def_var(cap_ncid, prefix // '__count', nf90_int, ids(3)), prefix)
  end subroutine def_roles

  subroutine put_int(varid, i)
    integer, intent(in) :: varid, i

    call nc(nf90_put_var(cap_ncid, varid, i), 'integer')
  end subroutine put_int

  subroutine stack_roles(ids, n, step, t)
    integer, intent(in) :: ids(3), n, step
    real(dp), intent(in) :: t

    call nc(nf90_put_var(cap_ncid, ids(1), [step], start=[n], count=[1]), 'step')
    call nc(nf90_put_var(cap_ncid, ids(2), [t], start=[n], count=[1]), 'time')
    call put_int(ids(3), n)
  end subroutine stack_roles

  integer function cap_var(name)
    character(len=*), intent(in) :: name
    integer :: k

    do k = 1, size(cap_vars)
      if (cap_vars(k)%name == trim(name)) then
        cap_var = k
        return
      end if
    end do
    cap_var = 0
    call cap_refuse('capture', 'no variable '//trim(name))
  end function cap_var

  ! The values of `name` (rank `rank`, `flat` in array element order, `ext` its extents) into
  ! the open record of `group`: at the group's next index on a stacked group. A value of no
  ! element writes no cell, so its zero-extent dimension stays 0.
  subroutine put_values(group, name, rank, flat, ext)
    character(len=*), intent(in) :: group, name
    integer, intent(in) :: rank
    real(dp), intent(in) :: flat(:)
    integer, intent(in), optional :: ext(:)
    integer :: j, k, varid, rec
    integer :: e(4)

    if (.not. cap_open) call cap_refuse('capture', 'no case is open')
    if (group == 'series') call cap_refuse('capture', 'a series value is a scalar')
    k = cap_var(name)
    if (cap_vars(k)%rank /= rank) call cap_refuse('capture', 'variable '//trim(name)// &
      & ' is put at another rank than it was opened with')
    e = 1
    if (rank == 1) e(1) = size(flat)
    if (rank > 1) e(1:rank) = ext
    select case (group)
    case ('initial')
      varid = cap_vars(k)%vid_initial
      rec = 0
    case ('final')
      varid = cap_vars(k)%vid_final
      rec = 0
    case ('history')
      if (.not. cap_vars(k)%sampled) call cap_refuse('capture', 'variable '//trim(name)// &
        & ' has no history')
      varid = cap_vars(k)%vid_history
      rec = cap_vars(k)%h_count + 1
      cap_vars(k)%h_put = .true.
    case ('restart_point')
      if (.not. cap_vars(k)%restart) call cap_refuse('capture', 'variable '//trim(name)// &
        & ' is in no restart point')
      varid = cap_vars(k)%vid_restart
      rec = cap_n_rp + 1
    case default
      call cap_refuse('capture', 'no group '//trim(group))
    end select
    if (rank == 0) then
      if (rec == 0) then
        call nc(nf90_put_var(cap_ncid, varid, flat(1)), 'value '//trim(name))
      else
        call nc(nf90_put_var(cap_ncid, varid, flat, start=[rec], count=[1]), &
          & 'value '//trim(name))
      end if
    else if (rec == 0) then
      call nc(nf90_put_var(cap_ncid, varid, flat, start=[(1, j = 1, rank)], &
        & count=e(1:rank)), 'value '//trim(name))
    else
      call nc(nf90_put_var(cap_ncid, varid, flat, start=[(1, j = 1, rank), rec], &
        & count=[e(1:rank), 1]), 'value '//trim(name))
    end if
  end subroutine put_values

  function real_text(x) result(s)
    real(dp), intent(in) :: x
    character(len=:), allocatable :: s
    character(len=32) :: buf

    if (ieee_is_nan(x)) then
      s = 'NaN'
    else if (.not. ieee_is_finite(x)) then
      if (x > 0.0_dp) then
        s = 'Infinity'
      else
        s = '-Infinity'
      end if
    else
      write(buf, '(ES24.16E3)') x
      s = trim(adjustl(buf))
    end if
  end function real_text

  ! The run's performance document perf.json, written once.
  subroutine harness_fortran_cpu_netcdf__write_perf(case_id, target, steps, cells_updated, &
      & walltime_sec, mpi_ranks, gpu_devices)
!$  use omp_lib, only: omp_get_max_threads
    character(len=*), intent(in) :: case_id
    character(len=*), intent(in) :: target
    integer, intent(in) :: steps
    integer, intent(in) :: cells_updated
    real(dp), intent(in) :: walltime_sec
    integer, intent(in) :: mpi_ranks
    integer, intent(in) :: gpu_devices
    integer :: nthreads
    integer :: degree
    integer :: u
    real(dp) :: throughput

    nthreads = 1
!$  nthreads = omp_get_max_threads()
    throughput = real(cells_updated, dp) / walltime_sec
    degree = mpi_ranks * nthreads * max(gpu_devices, 1)

    open(newunit=u, file='perf.json', status='replace', action='write', form='formatted')
    write(u, '(a)') '{'
    write(u, '(a)') '"case_id": "' // trim(case_id) // '",'
    write(u, '(a)') '"target": "' // trim(target) // '",'
    write(u, '(a)') '"walltime_sec": ' // real_text(walltime_sec) // ','
    write(u, '(a)') '"steps": ' // itoa(steps) // ','
    write(u, '(a)') '"cells_updated": ' // itoa(cells_updated) // ','
    write(u, '(a)') '"throughput_cells_per_sec": ' // real_text(throughput) // ','
    write(u, '(a)') '"parallelism": {"mpi_ranks": ' // itoa(mpi_ranks) &
      & // ', "threads_per_rank": ' // itoa(nthreads) &
      & // ', "gpu_devices": ' // itoa(gpu_devices) &
      & // ', "parallel_degree_total": ' // itoa(degree) // '},'
    write(u, '(a)') '"timestamp_utc": "' // utc_timestamp() // '"'
    write(u, '(a)') '}'
    close(u)
  end subroutine harness_fortran_cpu_netcdf__write_perf


  ! ISO-8601 UTC timestamp from date_and_time with the zone offset applied.
  function utc_timestamp() result(ts)
    character(len=20) :: ts
    integer :: v(8)
    integer :: days
    integer :: minutes
    integer :: y
    integer :: m
    integer :: d

    call date_and_time(values=v)
    days = days_from_civil(v(1), v(2), v(3))
    minutes = v(5) * 60 + v(6) - v(4)
    days = days + floor(real(minutes, dp) / 1440.0_dp)
    minutes = modulo(minutes, 1440)
    call civil_from_days(days, y, m, d)
    write(ts, '(I4.4,"-",I2.2,"-",I2.2,"T",I2.2,":",I2.2,":",I2.2,"Z")') &
      & y, m, d, minutes / 60, modulo(minutes, 60), v(7)
  end function utc_timestamp

  ! Days since 1970-01-01 of a proleptic Gregorian date.
  function days_from_civil(year, month, day) result(z)
    integer, intent(in) :: year
    integer, intent(in) :: month
    integer, intent(in) :: day
    integer :: z
    integer :: y
    integer :: era
    integer :: yoe
    integer :: doy
    integer :: doe

    y = year
    if (month <= 2) y = y - 1
    if (y >= 0) then
      era = y / 400
    else
      era = (y - 399) / 400
    end if
    yoe = y - era * 400
    if (month > 2) then
      doy = (153 * (month - 3) + 2) / 5 + day - 1
    else
      doy = (153 * (month + 9) + 2) / 5 + day - 1
    end if
    doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
    z = era * 146097 + doe - 719468
  end function days_from_civil

  ! Proleptic Gregorian date of a day count since 1970-01-01.
  subroutine civil_from_days(days, year, month, day)
    integer, intent(in) :: days
    integer, intent(out) :: year
    integer, intent(out) :: month
    integer, intent(out) :: day
    integer :: z
    integer :: era
    integer :: doe
    integer :: yoe
    integer :: doy
    integer :: mp

    z = days + 719468
    if (z >= 0) then
      era = z / 146097
    else
      era = (z - 146096) / 146097
    end if
    doe = z - era * 146097
    yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365
    doy = doe - (365 * yoe + yoe / 4 - yoe / 100)
    mp = (5 * doy + 2) / 153
    day = doy - (153 * mp + 2) / 5 + 1
    if (mp < 10) then
      month = mp + 3
    else
      month = mp - 9
    end if
    year = yoe + era * 400
    if (month <= 2) year = year + 1
  end subroutine civil_from_days

end module harness_fortran_cpu_netcdf_model
