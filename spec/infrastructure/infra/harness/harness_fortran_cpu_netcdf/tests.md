# Tests: Fortran/CPU runner harness writing NetCDF (L0 plumbing self-test)

## 0. Meta information
- `test_profile_id`: `harness_fortran_cpu_netcdf_l0`
- `test_profile_version`: `0.2.0`
- `status`: `draft`
- `spec_ref.spec_kind`: `infrastructure`
- `spec_ref.spec_id`: `harness_fortran_cpu_netcdf`
- `spec_ref.spec_version`: `0.1.0`
- `spec_ref.controlled_spec_path`: `spec/infrastructure/infra/harness/harness_fortran_cpu_netcdf/controlled_spec.md`

## 1. Test purpose
This suite verifies the published runner-plumbing operations of `harness_fortran_cpu_netcdf` at `L0`: the capture of a scalar and of rank-1..4 arrays, read back by the host through the target's capture store, the capture of every group (`initial`, `history` at two strides, `restart_point`, `series` and `final`) with the step each record carries and the time of each record but a `series` row, a variable of zero extent, the case-set fan-out (one `final` record per case), the derived `perf` throughput, a capture of the size a consuming node commits, the reading of the host-written case file with its refusal of an absent one, and the `--cases` input guard. The self-test runner exercises each operation and captures what the host judges in that case's records, through the capture operations.

## 2. Input-defaulting rules
- Each normal case supplies a small fixed set of sentinel values to the capture operation under test. **The sentinel values are DECLARED in the IR as that case's inputs** (`case.test_case_set[].inputs.initial`), and the self-test captures exactly the declared values, so the host can compare what the capture holds against what the case declares (§6, the host-evaluated `predicate` of each test):
  - `l0_numeric_roundtrip_pass`: `inputs.initial.x_in = [-1.5, 1.0e-30, 1.0e+30]` (a negative, a `1e-30`-scale and a `1e+30`-scale real).
  - `l0_array_emit_pass`: `inputs.initial.a1 = [1, 2]`, `a2 = [[1, 2], [3, 4]]`, `a3 = [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]`, `a4` the same pattern over `1..16`, each written in the JSON nesting order of the case file (the leading array index outermost), and captured as declared.
- `l0_read_case_roundtrip_pass` declares the sentinels the host writes into its case file and the self-test reads back through `harness_fortran_cpu_netcdf__read_case` and the getters: `inputs.initial.rc_real = 0.30000000000000004` (a token of full length), `rc_whole = 2` (an integer token read as a real), `rc_int = -12`, `rc_true = true`, `rc_false = false` (written `1` and `0`), `rc_empty = []`, `rc_empty2 = [[], []]`, `rc_r1 = [-1.5, 1.0e-30, 1.0e+30]`, `rc_r2 = [[1, 2, 3], [4, 5, 6]]`, `rc_r3` of extents `[2,2,3]` holding `1..12` and `rc_r4` of extents `[2,2,2,3]` holding `1..24`, each in the JSON nesting order of the case file (the leading array index outermost). The extents differ so that a flattened read is a shape-pairing error; a getter and a capture operation that share one wrong element order agree with each other, so `rc_e2`, `rc_e3` and `rc_e4` observe one element by its indices, which in the declared nesting holds `2`.
- `l0_read_case_second_pass` declares other values under two of those names, so that a reader that kept an earlier case fails: `inputs.initial.rc_real = -0.7`, `rc_r1 = [2.5, -3.25, 0.125]`.
- `l0_missing_case_file_xfail` declares no sentinel input: it calls `harness_fortran_cpu_netcdf__read_case` with the id `l0_no_such_case_file`, which names no case, so no case file exists for it.
- `l0_capture_groups_pass` declares, under `inputs.initial`, what the self-test reads from its case file: `g_n = 9` (the steps it commits), `g_every_a = 2` and `g_every_b = 3` (the two `history` strides), `g_every_p = 4` (the `restart_point` stride), `g_dt = 0.25` (step `k` is committed at time `g_dt * k`), and the bases `g_s = 0.5`, `g_r1 = [-1.5, 2.0, 4.25]`, `g_r2` of extents `[2,3]` holding `0.5, 1.0, ..., 3.0` (`0.5 * (1..6)`), `g_r3` of extents `[2,3,4]` holding `1..24` and `g_r4` of extents `[2,3,4,5]` holding `1..120`, each in the JSON nesting order of the case file (the leading array index outermost). Under `inputs.expected` it declares what the host compares the records with, which the self-test does not read: the committed steps `steps_a = [2, 4, 6, 8]`, `steps_b = [3, 6, 9]`, `steps_p = [4, 8]` and `steps_all = [1, 2, 3, 4, 5, 6, 7, 8, 9]`; the stacks of a base plus the committed step of each record, the records on the leading axis — `g_s_a`, `g_r1_a` and `g_r2_a` over `steps_a`, `g_r3_b` over `steps_b`, and `g_s_p`, `g_r1_p`, `g_r2_p` and `g_r3_p` over `steps_p` (so `g_s_a = [2.5, 4.5, 6.5, 8.5]` and `g_r1_p = [[2.5, 6.0, 8.25], [6.5, 10.0, 12.25]]`); and the series columns `g_a = [1.5, 2.5, ..., 9.5]` (`g_s + k`) and `g_series_long = [-2.5, -3.5, ..., -10.5]` (the first element of `g_r1` minus `k`), `k = 1..9`. The stack of the rank-4 variable would have rank 5, which no input holds, so `g_r4` is judged by its extents and, per record, by its largest element, its smallest and its sum. Every base is a multiple of `0.25` of small magnitude, so every sum is exact and the comparison has no tolerance.
- `l0_capture_large_pass` declares no input: the case computes `big` (`controlled_spec.md` §2) and reads no case file.
- The abnormal case (`l0_missing_cases_xfail`) synthesizes an empty argv-token list (no `--cases`) and drives `harness_fortran_cpu_netcdf__parse_cases` to exercise the guard.

## 3. Execution-control rules
`N/A`: the harness self-test defines no time-stepping. Each case runs its named plumbing check once (`steps = 1`). The `l0_capture_groups_pass` case commits its records at the steps `1 .. g_n` of a count of its own (§2) and advances no state.

- `zero_extent`: `g_z1`, `g_z2`

## 4. Case-expansion rules
The suite defines ten `case`s and ten `test_id`s (no sweep). The self-test's own runner commits each case's `final` record once, after the case ran; the `l0_capture_groups_pass` and `l0_case_fanout_pass` cases alone commit records of the other groups too (`controlled_spec.md` §2 and §3). Every host-evaluated `predicate` of §6 reads the case's own captures and its declared `inputs` only. Every test is **single-target**: it names exactly one case, and that case's `case_id` equals the `test_id` — the ten `case.test_case_set[].case_id` are exactly those ten `test_id`s. Each `case_id` selects the plumbing aspect that case verifies. The multi-case run itself is the fan-out evidence: the host finds exactly one `final` record per case.

**Every `final` record holds every variable `controlled_spec.md` §2 declares**, plus the `step` and the time `t` its commit carries: `1` and `0.0` (a single `steps=1` step) in every case but `l0_capture_groups_pass`, whose own are §2's. A variable §2 lists under the case holds what the case computed; any other variable holds a shape-valid placeholder, `0.0` or an array of zeros of its declared shape (the empty array for a variable of the `zero_extent` line of §3), which no predicate of that case reads but `l0_case_fanout_pass`'s, which reads the placeholders it commits to `history` and `restart_point`; a test's own predicates fail on a placeholder in the variables they read for what the case computed, so a case that does not fill its variables fails its test.

## 5. Judged quantities
Each test of §6 judges one quantity, named by its `quantities` line and defined by its `predicate` lines: an expression over the case's captures and the inputs the case declares. The predicates of `l0_capture_groups_pass` and one of `l0_case_fanout_pass` read the case's `history`, `restart_point` and `series` records, so the IR declares the `state_history`, `restart_points` and `step_series` evidence entries, the last with the names `step`, `g_a` and `g_series_long`. The host evaluates them, and they alone decide the test (`docs/TESTS.md` requirement 9); the self-test writes no verdict of its own. Every snapshot variable is written by this node's self-test, which is the trust root for it (A6).

## 6. Test definitions
- `test_id`: `l0_numeric_roundtrip_pass`
  - `level`: `L0`
  - `operation_id`: `harness_fortran_cpu_netcdf__capture_array_r1`
  - `expected_outcome`: `pass`
  - `judgment`: the sentinel reals `x_in` (the three values §2 declares) captured as `x_out` through `harness_fortran_cpu_netcdf__capture_array_r1` reproduce the inputs within an absolute tolerance of `1e-12` (`max|x_out - x_in| <= 1e-12`).
  - `quantities`: `numeric_roundtrip`
  - `predicate` (host-evaluated, quantity `numeric_roundtrip`, `per_case: true`): `maxabs(final.x_out - inputs.initial.x_in) <= 1.0e-12` — the captured values, as the host reads them, against the declared sentinel.
- `test_id`: `l0_array_emit_pass`
  - `level`: `L0`
  - `operation_id`: `harness_fortran_cpu_netcdf__capture_array_r2` (representative; the case exercises `__capture_array_r1..r4`)
  - `expected_outcome`: `pass`
  - `judgment`: the rank-1..4 real arrays `a1..a4` captured through `harness_fortran_cpu_netcdf__capture_array_r1..r4` read back with the shape and the element values of the inputs within `1e-12`.
  - `quantities`: `array_emit`
  - `predicate` (quantity `array_emit`, `per_case: true`): `max(maxabs(final.a1 - inputs.initial.a1), maxabs(final.a2 - inputs.initial.a2), maxabs(final.a3 - inputs.initial.a3), maxabs(final.a4 - inputs.initial.a4)) <= 1.0e-12` — the captured arrays, as the host reads them, against the declared sentinels (shape and values: a wrong nesting order or extent is a shape-pairing error, which fails the predicate).
- `test_id`: `l0_case_fanout_pass`
  - `level`: `L0`
  - `operation_id`: `harness_fortran_cpu_netcdf__capture_commit`
  - `expected_outcome`: `pass`
  - `judgment`: the case loop commits exactly one `final` record per case, each carrying every declared variable; `case_index` is this case's 1-based position in the case loop, so `1 <= case_index <= 10`. The case also commits, at step `2`, one `history` record of `g_s` and `g_never`, one `restart_point` record and one `series` row with `g_a = 1.0` (`controlled_spec.md` §3), and each reads back as that one record: a writer that carries a group's record count from another case into this one, or from this one into `l0_capture_groups_pass`, fails this predicate or that case's.
  - `quantities`: `case_fanout`
  - `predicate` (quantity `case_fanout`, `per_case: true`, three entries): `final.case_index >= 1.0`, `final.case_index <= 10.0` and `max(maxabs(history.g_s.step - 2), maxabs(history.g_never.step - 2), maxabs(history.g_s), maxabs(restart_point.step - 2), maxabs(restart_point.g_s), maxabs(series.step - 2), maxabs(series.g_a - 1)) == 0.0` (op `eq`).
- `test_id`: `l0_perf_derived_pass`
  - `level`: `L0`
  - `operation_id`: `harness_fortran_cpu_netcdf__write_perf`
  - `expected_outcome`: `pass`
  - `judgment`: `throughput_cells_per_sec == cells_updated / walltime_sec` within a relative tolerance of `1e-9`: `|throughput_cells_per_sec - cells_updated / walltime_sec| / throughput_cells_per_sec <= 1e-9` (the RELATIVE residual). The `steps`, `cells_updated` and `walltime_sec` the self-test passes to `__write_perf` are the run totals `controlled_spec.md` §3 fixes for it (not the per-case `steps = 1` of §3 of this document), all positive, so the throughput the residual divides by is positive and the residual is defined. The parallelism it passes is `mpi_ranks = 1` and `gpu_devices = 0`; `harness_fortran_cpu_netcdf__write_perf` takes no thread count (`controlled_spec.md` §3.2). The writer itself writes as `parallelism.threads_per_rank`, and uses in `parallel_degree_total`, the count the run's shared-memory parallel runtime gives the program, which it obtains when it is called: the maximum team size that runtime reports, never a constant and never read from an environment variable. In Fortran with OpenMP it imports `omp_get_max_threads` from the intrinsic module `omp_lib` and calls it, both on `!$` conditional-compilation lines, after an unconditional assignment of `1` to the count, so a build without OpenMP enabled reads both lines as comments and writes `1` (`docs/backends/parallel/openmp/GENERATE_RULES.md` §5). The Validate phase runs the self-test at several thread counts and compares `perf.json#parallelism.threads_per_rank` with the count each run was launched with, so a writer that records a constant fails on the first run whose count differs, and every consuming node's runner records its count through the same writer.
  - `quantities`: `perf_derived`
  - `predicate` (quantity `perf_derived`, `per_case: true`): `abs(final.throughput_cells_per_sec - final.cells_updated / final.walltime_sec) / final.throughput_cells_per_sec <= 1.0e-9` — evaluated by the host from the three fields the case re-parsed from `perf.json`; a placeholder `0.0` there divides by zero and fails the test.
- `test_id`: `l0_missing_cases_xfail`
  - `level`: `L0`
  - `operation_id`: `harness_fortran_cpu_netcdf__parse_cases`
  - `expected_outcome`: `xfail`
  - `xfail_condition`: a `--cases` flag is absent from the token list passed to `__parse_cases`
  - `judgment`: calling `__parse_cases` on a length-0 token array returns `ok = false`, so `guard_fired == 1.0`.
  - `quantities`: `input_guard`
  - `predicate` (quantity `input_guard`, `per_case: true`): `final.guard_fired == 1.0` (op `eq`) — the state fact the guard leaves behind, in the failure direction; the test certifies `xfail` when it holds.
- `test_id`: `l0_read_case_roundtrip_pass`
  - `level`: `L0`
  - `operation_id`: `harness_fortran_cpu_netcdf__read_case`
  - `expected_outcome`: `pass`
  - `judgment`: the sentinels of §2, read from the case file the host wrote through `harness_fortran_cpu_netcdf__read_case` and the getters and captured, equal the declared inputs exactly (the capture is lossless, so the comparison has no tolerance), in value and in shape; each indexed element is the one the nesting declares; the two booleans read as true and false; the empty array reads with size 0.
  - `quantities`: `read_case_roundtrip`
  - `predicate` (quantity `read_case_roundtrip`, `per_case: true`, five entries): `max(abs(final.rc_real - inputs.initial.rc_real), abs(final.rc_whole - inputs.initial.rc_whole), abs(final.rc_int - inputs.initial.rc_int), maxabs(final.rc_r1 - inputs.initial.rc_r1), maxabs(final.rc_r2 - inputs.initial.rc_r2), maxabs(final.rc_r3 - inputs.initial.rc_r3), maxabs(final.rc_r4 - inputs.initial.rc_r4)) == 0.0`, `max(abs(final.rc_e2 - 2.0), abs(final.rc_e3 - 2.0), abs(final.rc_e4 - 2.0)) == 0.0`, `final.rc_true == 1.0`, `final.rc_false == 1.0` and `final.rc_empty_read == 1.0` (op `eq`).
- `test_id`: `l0_read_case_second_pass`
  - `level`: `L0`
  - `operation_id`: `harness_fortran_cpu_netcdf__read_case`
  - `expected_outcome`: `pass`
  - `judgment`: a second case read after another one holds its own values: the sentinels of §2 equal the declared inputs exactly.
  - `quantities`: `read_case_second`
  - `predicate` (quantity `read_case_second`, `per_case: true`): `max(abs(final.rc_real - inputs.initial.rc_real), maxabs(final.rc_r1 - inputs.initial.rc_r1)) == 0.0` (op `eq`).
- `test_id`: `l0_missing_case_file_xfail`
  - `level`: `L0`
  - `operation_id`: `harness_fortran_cpu_netcdf__read_case`
  - `expected_outcome`: `xfail`
  - `xfail_condition`: the case file `harness_fortran_cpu_netcdf__read_case` is asked for does not exist
  - `judgment`: calling `__read_case` with an id no case file exists for returns `ok = false`, so `read_refused == 1.0`.
  - `quantities`: `read_case_guard`
  - `predicate` (quantity `read_case_guard`, `per_case: true`): `final.read_refused == 1.0` (op `eq`) — the state fact the refusal leaves behind, in the failure direction; the test certifies `xfail` when it holds.
- `test_id`: `l0_capture_groups_pass`
  - `level`: `L0`
  - `operation_id`: `harness_fortran_cpu_netcdf__capture_commit` (representative; the case exercises every capture operation, in every group)
  - `expected_outcome`: `pass`
  - `judgment`: the records the case commits in each capture group, as the host reads them through the capture store, are the ones §2 declares: the `initial` record holds the bases at step `0` and time `0.0`; the `final` record holds each base plus `g_n` at step `g_n` and time `g_dt * g_n`; the `history` records of `g_s`, `g_r1` and `g_r2` are those of the steps `steps_a` and the ones of `g_r3` and `g_r4` those of `steps_b`, each at time `g_dt` times its step and holding the base plus its step; the `restart_point` records are those of `steps_p`, holding the same; and the series holds one row per step `1..9` with the two columns §2 declares. A record lost, written twice, written at another step or holding another record's values changes a stack's extents or its values and fails the predicate. The two variables of the `zero_extent` line hold no element, so no predicate values them: the steps of the `history` records that hold each are judged (`history.g_z1.step`, `history.g_z2.step`), and the case passes only if the store reads every record that holds them. `g_never`, which no `history` or `restart_point` record holds, is read by no predicate. The time of a `series` row is read by no predicate either: a series has no readable time.
  - `quantities`: `capture_groups`
  - `predicate` (quantity `capture_groups`, `per_case: true`, seven entries): `max(abs(initial.step), abs(initial.t), abs(initial.g_s - inputs.initial.g_s), maxabs(initial.g_r1 - inputs.initial.g_r1), maxabs(initial.g_r2 - inputs.initial.g_r2), maxabs(initial.g_r3 - inputs.initial.g_r3), maxabs(initial.g_r4 - inputs.initial.g_r4)) == 0.0`, `max(abs(final.step - inputs.initial.g_n), abs(final.t - inputs.initial.g_dt * inputs.initial.g_n), abs(final.g_s - inputs.initial.g_s - inputs.initial.g_n), maxabs(final.g_r1 - inputs.initial.g_r1 - inputs.initial.g_n), maxabs(final.g_r2 - inputs.initial.g_r2 - inputs.initial.g_n), maxabs(final.g_r3 - inputs.initial.g_r3 - inputs.initial.g_n), maxabs(final.g_r4 - inputs.initial.g_r4 - inputs.initial.g_n)) == 0.0`, `max(maxabs(history.g_s.step - inputs.expected.steps_a), maxabs(history.g_r1.step - inputs.expected.steps_a), maxabs(history.g_r2.step - inputs.expected.steps_a), maxabs(history.g_s.t - inputs.initial.g_dt * history.g_s.step), maxabs(history.g_s - inputs.expected.g_s_a), maxabs(history.g_r1 - inputs.expected.g_r1_a), maxabs(history.g_r2 - inputs.expected.g_r2_a), maxabs(history.g_z1.step - inputs.expected.steps_a)) == 0.0`, `max(maxabs(history.g_r3.step - inputs.expected.steps_b), maxabs(history.g_r4.step - inputs.expected.steps_b), maxabs(history.g_r3.t - inputs.initial.g_dt * history.g_r3.step), maxabs(history.g_r3 - inputs.expected.g_r3_b), maxabs(smax(history.g_r4) - history.g_r4.step - max(inputs.initial.g_r4)), maxabs(smin(history.g_r4) - history.g_r4.step - min(inputs.initial.g_r4)), maxabs(ssum(history.g_r4) - 120 * history.g_r4.step - sum(inputs.initial.g_r4)), maxabs(history.g_z2.step - inputs.expected.steps_b)) == 0.0`, `max(maxabs(restart_point.step - inputs.expected.steps_p), maxabs(restart_point.t - inputs.initial.g_dt * restart_point.step), maxabs(restart_point.g_s - inputs.expected.g_s_p), maxabs(restart_point.g_r1 - inputs.expected.g_r1_p), maxabs(restart_point.g_r2 - inputs.expected.g_r2_p), maxabs(restart_point.g_r3 - inputs.expected.g_r3_p)) == 0.0`, `max(maxabs(smax(restart_point.g_r4) - restart_point.step - max(inputs.initial.g_r4)), maxabs(smin(restart_point.g_r4) - restart_point.step - min(inputs.initial.g_r4)), maxabs(ssum(restart_point.g_r4) - 120 * restart_point.step - sum(inputs.initial.g_r4))) == 0.0` and `max(maxabs(series.step - inputs.expected.steps_all), maxabs(series.g_a - inputs.expected.g_a), maxabs(series.g_series_long - inputs.expected.g_series_long), abs(final.g_s - inputs.initial.g_s - inputs.initial.g_n)) == 0.0` (op `eq`).
- `test_id`: `l0_capture_large_pass`
  - `level`: `L0`
  - `operation_id`: `harness_fortran_cpu_netcdf__capture_array_r2`
  - `expected_outcome`: `pass`
  - `judgment`: the `[512,512]` array `big`, `big(i,j) = i + j/2`, captured through `harness_fortran_cpu_netcdf__capture_array_r2`, reads back with its largest element `768`, its smallest `1.5` and its sum `100859904` (`768 * 131328`); every element is a multiple of `0.5` and every partial sum is below `2^52`, so the three are exact. The element order is judged by `l0_array_emit_pass`, and the host's reader refuses a value whose extents are not the declared ones. `big` is written in every case's `final` record (this case's array, every other case's placeholder) and in the `initial` record of `l0_capture_groups_pass`. A writer that copies the record built so far once per element written into it (`controlled_spec.md` §3.2) runs past the run's time bound over those records, and the run fails.
  - `quantities`: `capture_large`
  - `predicate` (quantity `capture_large`, `per_case: true`, three entries): `max(final.big) == 768.0`, `min(final.big) == 1.5` and `sum(final.big) == 100859904.0`.

## 7. Pass/fail aggregation rules
- `per_test.pass_rule`: `pass` when every predicate of the test holds.
- `per_test.xfail_rule`: `xfail` when `xfail_condition` is true and every predicate of the test holds (the input guard fires on a missing `--cases`, the read guard on an absent case file).
- `suite.pass_rule`: `pass` when all `test_id` are `pass` or `xfail`.

## 8. Traceability
- Record `test_profile_id` and `test_profile_version` in `trial_meta.json`.
