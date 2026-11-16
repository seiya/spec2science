# Controlled Spec: Fortran/CPU runner harness (infrastructure spec)

## 0. Meta information
- `spec_id`: `harness_fortran_cpu`
- `spec_version`: `0.11.0`
- `status`: `controlled_draft`
- `spec_kind`: `infrastructure`
- `domain`: `infra`
- `family`: `harness`

## 1. Responsibility and scope
This `infrastructure` node (R1 harness) is responsible for the shared **runner plumbing** that every Fortran/CPU physics node's runner is built against: argv / `--cases` parsing, the case-set loop driver, the reading of the host-written case file (§3.2), the capture of a case's state through the format-neutral capture interface (§3.2), which this node writes in the `json` output format, the JSON token of a real, and the `perf.json` writer — the last of which records the **thread count** the run's shared-memory parallel runtime gives the program, which its writer obtains at run time (§3.2). It carries **no physics**: the kernel belongs to the consuming physics node, never here, and the host judges each test from the captures. It targets `(language=fortran, hardware=cpu)`; a different `(language, hardware)` target is a separate harness node.

The node's own generated code is a `harness_fortran_cpu_model.f90` publishing the plumbing operations plus a self-test `harness_fortran_cpu_runner.f90` that exercises them and emits the standard runner outputs (so the harness is verified through the exact same Compile→Generate→Build→Validate path as any node; it is self-hosting — the self-test writes its evidence through its own capture operations).

The published surface is a **binding, signature-level contract**: §5.1 gives the canonical language-neutral structured interface block (every public type and every operation signature) in a machine-readable fenced code block, from which the language backend renders the Fortran surface. The generated `harness_fortran_cpu_model.f90` must publish exactly those signatures; a consuming physics node's host-rendered runner glue is written against them and names a capture group and a value and holds no knowledge of the output format (the file names and the JSON assembly live only inside these certified operations).

## 2. input/output contract
Input (to the self-test runner): the standard runner argv `--cases <spec.ir.yaml> <case_id>...` — the spec path is taken positionally, `__parse_cases` keeps it, and the spec file itself need not be read; the token array holds that path whole, so its width is not `case_id_len`; the trailing tokens are the `case_id`s to run, one per `case.test_case_set[]`. The main program marshals the process argv into a token array and calls `harness_fortran_cpu__parse_cases` on it. Each `case_id` selects, by dispatching on the `case_id`, the plumbing aspect that case verifies. Every `case_id` is also the `test_id` of the single-target test that names it, and every test is single-target. A missing `--cases` flag (no cases) makes `__parse_cases` return `ok = false` (the input guard); the guard case verifies this by calling `__parse_cases` on a length-0 token array.

Input (to `__read_case`): the **case file** `case_inputs/<case_id>.json`, which the host writes beside `<spec.ir.yaml>` for every case before the program runs (`docs/workflow/RUNNER_OUTPUT_CONTRACT.md` §6), one flat JSON object on one line of any length. Its keys are names (`inputs.initial.x_in`, `derived.dt`, a state variable's name) and hold no `\` escape; each value is a number or a rectangular nested array of numbers of depth 1 to 4, an empty array admitted at any level. A number is an RFC 8259 token, so `3`, `-1.5` and `1e-30` are all numbers, and it is not `__real_token`'s spelling. JSON whitespace between tokens is admitted. A document holding anything else — a nested object, a string, `true` / `false` / `null`, a `NaN` / `Infinity` / `-Infinity` token, a ragged array, an array deeper than 4, a number that does not convert to a finite binary64 value — is unreadable.

Output artifacts (produced by the writers, into the run node dir relative to `cwd=RUNDIR`):
- **`perf.json`** — one object with `case_id`, `target` (`"cpu"`), `walltime_sec`, `steps`, `cells_updated`, `throughput_cells_per_sec` (`= cells_updated / walltime_sec`), a `parallelism` object (`mpi_ranks`, `threads_per_rank`, `gpu_devices`, `parallel_degree_total = mpi_ranks*threads_per_rank*max(gpu_devices,1)`), and `timestamp_utc` (ISO-8601). `threads_per_rank` is the number of threads the run was given, which `__write_perf` obtains from the run's shared-memory parallel runtime when it is called (§3.2) — never a constant and never a value its caller passes: the Validate phase runs the same binary at several thread counts and compares it with the count each run was launched with.
- **The captures** — the files under `raw/state_snapshots/`, written only through the capture operations of §3.2, in the layout §3.2 states. The self-test commits one `final` record per case — at step `1` and time `0.0` in every case but `l0_capture_groups_pass` (each of the others has a single `steps=1` step) — and that case alone commits records of the other groups (§3). An `initial` and a `final` record hold every variable declared in `snapshot_schema.json`, a `history` and a `restart_point` record the variables §3 names for it, and every record the `step` and `t` its commit carries. The snapshot state variables, with their `shape_expr`, are listed under the case that computes them:
  - `l0_numeric_roundtrip_pass`: `x_out` (rank-1, `[3]` — the sentinel reals, captured through `__capture_array_r1`).
  - `l0_array_emit_pass`: `a1` (rank-1, `[2]`), `a2` (rank-2, `[2,2]`), `a3` (rank-3, `[2,2,2]`), `a4` (rank-4, `[2,2,2,2]`) — the sentinel arrays, captured through `__capture_array_r1..r4`.
  - `l0_case_fanout_pass`: `case_index` (scalar — this case's ordinal in the run).
  - `l0_perf_derived_pass`: `throughput_cells_per_sec`, `cells_updated`, `walltime_sec` (scalars — the three fields as the case re-parsed them from the `perf.json` the writer wrote), from which the host evaluates the RELATIVE residual `tests.md` judges.
  - `l0_nonfinite_token_pass`: `nonfinite_match` (scalar, `1.0` iff the four non-finite reals of §3 formatted by `__real_token` produced exactly the literals `NaN`, `NaN`, `Infinity`, `-Infinity`).
  - `l0_read_case_roundtrip_pass`: `rc_real`, `rc_whole`, `rc_int`, `rc_true`, `rc_false`, `rc_empty_read`, `rc_e2`, `rc_e3`, `rc_e4` (scalars) and `rc_r1` (`[3]`), `rc_r2` (`[2,3]`), `rc_r3` (`[2,2,3]`), `rc_r4` (`[2,2,2,3]`) — what the getters returned for the case's own case file, captured (§3): `rc_true` / `rc_false` are `1.0` iff `__get_bool` returned true / false for them, `rc_empty_read` is `1.0` iff the arrays `__get_array_r1` and `__get_array_r3` returned for `rc_empty`, and `__get_array_r2` and `__get_array_r3` for `rc_empty2`, each have size 0, and `rc_e2` / `rc_e3` / `rc_e4` are one element of `rc_r2` / `rc_r3` / `rc_r4` obtained by indexing the array the getter returned: every index at its first value but the last, which is at its second.
  - `l0_read_case_second_pass`: `rc_real` and `rc_r1` of the same shapes, read from that case's own case file.
  - `l0_missing_case_file_xfail`: `read_refused` (scalar, `1.0` when `__read_case` returned `ok = false` for a `case_id` no case file exists for).
  - `l0_missing_cases_xfail`: `guard_fired` (scalar, `1.0` when `__parse_cases` on a length-0 token array returned `ok = false`).
  - `l0_capture_groups_pass`: `g_s` (scalar), `g_r1` (`[3]`), `g_r2` (`[2,3]`), `g_r3` (`[2,3,4]`) and `g_r4` (`[2,3,4,5]`) — the bases `tests.md` §2 declares, read from the case file and captured in every group at the steps §3 states, each record holding the base plus its committed step; `g_never` (`[2]`, zeros, held by the `initial` and `final` records alone); and `g_z1` (`[0]`) and `g_z2` (`[3,0]`), the two variables of zero extent (`tests.md` §3), which hold no element in any record.
  - `l0_capture_large_pass`: `big` (rank-2, `[384,384]`) — `big(i,j) = i + j/2` for `i, j = 1..384`, the leading index `i`, computed by the case and captured through `__capture_array_r2`: one record of the size a consuming physics node commits many times per case (a `256 x 256` state of three variables holds `196608` values), so a writer whose cost grows faster than linearly in a record's size (§3.2) shows in this case's run time.
  A variable is declared once in `snapshot_schema.json` and written by every case: in a case it is not listed under it holds a shape-valid placeholder, `0.0` or an array of zeros of its declared shape (the empty array for `g_z1` and `g_z2`), which no predicate of that case reads.

Numeric serialization follows the abstract runner-output contract: a finite real as a round-trip-lossless exponential token, a non-finite real as the literal token `NaN` (whatever the sign bit), `Infinity` or `-Infinity` chosen by branching on the value (never handed to a numeric format, never written as `null`, and never a reason for the writer to stop the program), an integer as its minimal decimal — a truncating form (one that may drop a leading digit) is forbidden. A capture holds no boolean: the caller converts a logical state to `1.0` / `0.0`. The target-language realization (for Fortran: reals via `ES24.16E3` then `trim(adjustl())`, or a bounded `Fw.d`, never `F0`/`F0.d`; integers via `I0`; booleans by branching to the literal, never an `L`-family descriptor; non-finite reals by branching on `ieee_is_nan` / `ieee_is_finite` from the intrinsic module `ieee_arithmetic`, imported with an `only:` list) is canonical in `docs/backends/language/fortran/RUNNER_OUTPUT.md` §1, the Fortran binding of `docs/workflow/RUNNER_OUTPUT_CONTRACT.md §4`.

## 3. Operation definition
The published operations (all under module `harness_fortran_cpu_model`, prefix `harness_fortran_cpu__`) are the plumbing surface a physics-node runner reuses. The canonical machine-readable interface (every signature and public type, verbatim) is §5.1; the prose below states each operation's contract. The module declares two module-level integer parameters the signatures reference: `dp` (the double-precision real kind token; §5.1 value `float64` = IEEE-754 binary64) and `case_id_len = 64` (the fixed storage width of a parsed case id, known to the caller, because an assumed-length `intent(out)` string dummy is disallowed). Their accessibility is not part of this contract — §5.1 pins their VALUES, not whether the module exports them: the module may export them or keep them private, and a consuming runner imports them where the module exports them or declares its own matching values (a double-precision-real kind for the actuals it passes and a fixed-width (`case_id_len`) case-id buffer). Each of these shapes is correct. Their VALUES are pinned (the gate rejects a drifted `case_id_len`), because a consumer's hardcoded length must match. The Fortran binding of these tokens (`dp = real64`, `character(len=64)`) is produced by the language backend, not authored here.

### 3.1 Published derived types
The module publishes one derived type (named by its fully-qualified `harness_fortran_cpu__<name>`):
- `harness_fortran_cpu__h_var` `{ name: string (dynamic length), rank: integer, shape: integer array of 4, history_capacity: integer, restart: logical }` — what `__capture_open` is told of one state variable: its `name`, its `rank` (0 for a scalar), its extents in the first `rank` entries of `shape` (the entries after them are `0` and are not read), the number of `history` records the case may commit for it, and whether a `restart_point` record holds it. It carries what an output format may need defined before a case's first record. The `json` format this node writes needs none of it, and `__capture_open` here does not read it. (Types and lengths use the §5.1 neutral vocabulary; the binding is the language backend's.)

### 3.2 Published operations
- `harness_fortran_cpu__parse_cases(tokens, ntokens, case_ids, ncases, ok)` — parse a supplied argv **token list** (NOT the process argv directly, so the guard is exercisable with a synthetic list): find `--cases` in `tokens`, keep the following token as the spec path (the positional token, which `__read_case` finds the case files beside and which the call replaces only when it returns `ok = true`), collect the remaining tokens as `case_ids` (each stored in a fixed-width `case_id_len` slot); return `ok = false` when `--cases` is absent from `tokens` or no `case_id` follows it (the input guard), else `ok = true`. The self-test's main program marshals the real process argv (via the language-standard argv accessors) into `tokens` for the normal cases, and passes a length-0 `tokens` for the guard case.
- `harness_fortran_cpu__real_token(x) result(s)` — format a `real (kind dp)` scalar as a JSON numeric token: a finite value in round-trip-lossless exponential form, a non-finite value as the literal `NaN` / `Infinity` / `-Infinity` §2 names. Every real the capture operations and `__write_perf` write is spelled as this operation spells it.
- `harness_fortran_cpu__capture_open(case_id, vars, restart_point_capacity, series_names, series_capacity)` — start the output of the case `case_id`. `vars` describes the case's state variables (§3.1); `restart_point_capacity` and `series_capacity` are the most `restart_point` records and `series` rows the case commits; `series_names` are the names of the series' columns, without `step` and `t`, which every commit carries. A name is its text without trailing blanks: the caller's array pads its elements to one width, and the name written and matched is the unpadded one. The same holds for every `case_id`, `group` and `name` argument of the capture operations. It writes nothing. One case is open at a time.
- `harness_fortran_cpu__capture_real(group, name, x)`, `harness_fortran_cpu__capture_array_r1(group, name, a)` … `__capture_array_r4(group, name, a)` — put the value `name` into the open record of `group`, one of the five words `initial`, `final`, `history`, `restart_point`, `series`: a scalar, or an assumed-shape rank-1..4 `real (kind dp)` array, written as a nested JSON array (`[ ... ]`, row-major over the leading index; a zero extent closes as that nesting does, so an array of extents `[0, 4]` is `[]` and one of `[3, 0]` is `[[], [], []]`). In `series` a value is a scalar and `name` is one of `series_names`. Every value is float64: the caller converts an integer or a logical state. A non-finite value is written as the token §2 names and is not refused here; the host refuses it.
- `harness_fortran_cpu__capture_commit(group, step, t)` — close the open record of `group` as the record of the committed step `step` at time `t`: it holds the values put into `group` since that group's previous commit, and `step` and `t`. A `series` commit closes one row, and the caller has put every one of `series_names` into it. A record of `initial`, `final`, `history` or `restart_point` is in its file when the call returns; a `series` row is in a file no later than the return of the next `restart_point` commit or of `__capture_close`.
- **Cost.** The time a capture operation takes grows linearly with the number of values it writes: what a record already holds is not rebuilt or copied again for each value or row put into it. A consuming physics node commits records of `10^5` values and more, many per case, and every run of its binary is bounded in time.
- `harness_fortran_cpu__capture_close()` — end the open case's output: the `series` rows not yet written are written, and the case is complete when the call returns.
- Each capture operation stops the program with a non-zero exit status when it is called with no case open (`__capture_open`: with one open), with a `group` that is none of the five words, with an array put into `series`, with a `series` name that is none of `series_names`, or with `step` or `t` among `series_names`.
- **The `json` layout.** Every file is one JSON object, under `raw/state_snapshots/`, opened by a path relative to the working directory and built at run time from the case id (never a hardcoded or sequential literal). The directories `initial/`, `history/`, `restart_point/` and `series/` exist in it before the program runs — the host creates them for every node — and this node creates no directory and lists none. With `<C>` the case id, `<k>` the committed step and `<i>` a count from 0, each in decimal digits with no leading zero: a `final` record is `<C>.json`, an `initial` record `initial/<C>.json`, a `history` record `history/<C>__<k>.json` and a `restart_point` record `restart_point/<C>__<k>.json`, each a **flat** object holding every value put under its name as a top-level key, then `step` (an integer token) and `t`. The `series` is written in chunks, `series/<C>__<i>.json`, the `<i>`-th chunk the case writes: an object holding `step`, `t` and each of `series_names`, each a list with one entry per row, in commit order. A `restart_point` commit first writes the rows committed since the previous chunk as the next chunk, when there is such a row, and then its own file, so a restart point that is in its file has every earlier row of the series in a file. `__capture_close` writes the rows not yet written as the next chunk, and writes a chunk whose lists are empty when the case wrote none, so every case has chunk `0`. Every file is written once and is never rewritten or appended to.
- `harness_fortran_cpu__write_perf(case_id, target, steps, cells_updated, walltime_sec, mpi_ranks, gpu_devices)` — write `perf.json` with all required fields incl. the derived `throughput_cells_per_sec` and the `parallelism` object (`parallel_degree_total = mpi_ranks*threads_per_rank*max(gpu_devices,1)`). It writes `mpi_ranks` and `gpu_devices` exactly as its caller passes them. It takes no thread count: it writes as `threads_per_rank` the number of threads the run's shared-memory parallel runtime gives the program, and uses that number in `parallel_degree_total`. **The thread count.** The HOST chooses how many threads the program runs with, and varies it between runs of the same binary (the Validate phase runs the binary once per execution shape of the target and compares each run's `perf.json` with the count it was launched with): it sets the count in the run's launch environment for the target's shared-memory parallel runtime, and the program learns it from that runtime alone. `__write_perf` obtains it AT RUN TIME on every call, as the maximum team size that runtime reports — never a constant and never read from an environment variable by this node. The query is reached through conditional compilation, so that a build without the runtime enabled compiles it away and the count written is `1` (for Fortran with OpenMP: `omp_get_max_threads()` from the intrinsic module `omp_lib`, both the import and the call written on `!$` conditional-compilation lines, after an unconditional assignment of `1`; `docs/backends/parallel/openmp/GENERATE_RULES.md` §5). It starts no parallel region. Because the count is obtained inside this writer, the self-test and the host-rendered runner of every consuming node record it through the same code, and this node's own Validate run, which runs the self-test at several thread counts, is what checks it.
- `harness_fortran_cpu__read_case(case_id, ok)` — read the case file of `case_id`: `<dir>/case_inputs/<case_id>.json`, where `<dir>` is the directory part of the spec path the last successful `__parse_cases` kept (a spec path with no directory part: the current directory). `ok = true` when the file exists and is readable under §2: the case it holds replaces the one loaded before. Otherwise `ok = false` and no case is loaded, so no value of an earlier case remains readable; the same holds when no `__parse_cases` has succeeded. This operation does not stop the program.
- `harness_fortran_cpu__get_real(name) result(x)` — the value of `name` in the loaded case: any number token, the nearest binary64 value.
- `harness_fortran_cpu__get_int(name) result(i)` — the value of `name`, an integer token (no fraction and no exponent) that fits the default integer.
- `harness_fortran_cpu__get_bool(name) result(b)` — the value of `name`, exactly the token `1` (true) or `0` (false): on an `infrastructure` node the host writes a boolean input that way, and a physics node's `IR` states its value as that token.
- `harness_fortran_cpu__get_array_r1(name, a)` … `__get_array_r4(name, a)` — allocate `a` to the extents of `name`'s array and fill it, the inverse of the nesting `__capture_array_r1..r4` write: the leading array index is the outermost JSON level. An empty array at a level gives extent 0 there and at every level below it, so `[]` reads at every rank and `[[],[]]` at rank 2 and above; any other value whose nesting depth differs from the rank is a mismatch. Number tokens read as in `__get_real`.
- Each getter holds no default and takes no `ok`: with no case loaded, a name the case does not hold, a scalar asked as an array or an array as a scalar, or a token its type does not admit, it stops the program with a non-zero exit status (`error stop`).

The self-test `harness_fortran_cpu_runner.f90` calls `__parse_cases`, then for each `case_id` runs (dispatching on the `case_id`) the plumbing check that case names — verifying the capture round-trips, the case fan-out, and the input guard — and captures that case's snapshot variables: `__capture_open` for the case (every declared variable in `vars`, the two names `g_a` and `g_series_long` as `series_names`, and, in every case but `l0_capture_groups_pass`, a `history_capacity` of `0` for every variable, `restart_point_capacity = 0` and `series_capacity = 0`), one put per declared variable into `final`, `__capture_commit` of `final` at step `1` and time `0.0`, and `__capture_close`; such a case commits no `series` row, and its series is the one `__capture_close` writes for a case with none. It writes and reads no file under `raw/state_snapshots/` in any other way, and does not list that directory. The `l0_nonfinite_token_pass` case obtains four non-finite reals AT RUN TIME from the language's IEEE facility (`ieee_value` with `ieee_quiet_nan` / `ieee_positive_inf` / `ieee_negative_inf`, and `ieee_copy_sign` for the sign bit) — a quiet NaN with the sign bit clear, a quiet NaN with the sign bit set, `+inf` and `-inf` — never from a literal division a compiler may refuse to fold; it formats each through `__real_token`, compares the four strings with `NaN`, `NaN`, `Infinity`, `-Infinity`, and captures `nonfinite_match` (`1.0` when all four match, else `0.0`) into its snapshot. It reads its clock before the first case and again after the last, and then calls `__write_perf` once, with that elapsed time as `walltime_sec`, or `1.0e-9` when the elapsed time it reads is not positive (a clock tick longer than the interval), with `mpi_ranks = 1` and `gpu_devices = 0` (the thread count is the writer's own, above), and with `steps` and `cells_updated` both equal to the number of cases its `__parse_cases` call on the program's argv returned: each case counts as one step that updates one cell, whatever records it commits (`tests.md` §3), so each value is the number of cases that ran, independent of any case's `grid` member and positive whenever a case ran. With both quantities positive, the relative residual of the `tests.md` §6 `l0_perf_derived_pass` judgment is defined. Everything after that call is outside the measured interval. Only then does it fill the `l0_perf_derived_pass` variables, re-parsing the `perf.json` that call wrote. A case's captures are made inside the loop or after the `__write_perf` call — those of `l0_perf_derived_pass` only after those variables are filled. The captures are the evidence the host judges that plumbing by.

The `l0_read_case_roundtrip_pass` case calls `__read_case` with its own `case_id`, reads every sentinel of `tests.md` §2 through the getter of its type under its key `inputs.initial.<name>`, and captures what the getter returned; it holds no copy of a sentinel, and the program stops as §3.2 states when a getter refuses. The `l0_read_case_second_pass` case does the same for the two sentinels it declares, so a reader that kept an earlier case fails one of the two. The `l0_missing_case_file_xfail` case calls `__read_case` with the literal id `l0_no_such_case_file`, which names no case of the IR, so no case file exists for it, and captures `read_refused` (`1.0` when `ok = false`, else `0.0`). No case but these three and `l0_capture_groups_pass` calls `__read_case`.

The `l0_capture_groups_pass` case calls `__read_case` with its own `case_id` and reads, under the keys `inputs.initial.<name>`, `g_n`, `g_every_a`, `g_every_b` and `g_every_p` through `__get_int`, `g_dt` and `g_s` through `__get_real`, and `g_r1` … `g_r4` through `__get_array_r1` … `__get_array_r4`; it reads no key under `inputs.expected` and holds no copy of a value it reads. It opens the case with every declared variable in `vars` — a `history_capacity` of `g_n / g_every_a` (integer division) for `g_s`, `g_r1`, `g_r2` and `g_z1`, of `g_n / g_every_b` for `g_r3`, `g_r4` and `g_z2`, and of `0` for every other variable; `restart` true for those seven and false for every other — with `restart_point_capacity = g_n / g_every_p`, the two series names and `series_capacity = g_n`. It then commits, in this order: an `initial` record at step `0` and time `0.0`; for each step `k = 1 .. g_n`, at time `g_dt * k`, one `series` row holding `g_a = g_s + k` and `g_series_long` = the first element of `g_r1` minus `k`, then, when `k` is a multiple of `g_every_a` or of `g_every_b`, ONE `history` record holding `g_s`, `g_r1`, `g_r2` and `g_z1` when `k` is a multiple of `g_every_a` and `g_r3`, `g_r4` and `g_z2` when it is a multiple of `g_every_b` (all seven at a step that is a multiple of both), then, when `k` is a multiple of `g_every_p`, one `restart_point` record holding those seven variables; and last the `final` record, at step `g_n` and time `g_dt * g_n`. In a record of step `k` each of `g_s`, `g_r1` … `g_r4` holds its base plus `k` in every element (the base itself in the `initial` record), and `g_z1` and `g_z2` are arrays of extents `[0]` and `[3,0]`. The `initial` and the `final` record hold every declared variable, the ones this case does not compute as their placeholders; `g_never` is in no `history` and no `restart_point` record. It passes the `name` of the `g_a` put as the element of the `series_names` array it opened the case with, which is padded to that array's width, and the `name` of the `g_series_long` put as a literal.

The `l0_capture_large_pass` case calls no reader: it computes `big` as §2 states and, as every case does, puts it with every other declared variable into `final`. In every other case `big` holds its placeholder, an array of zeros of extents `[384,384]`.

## 4. Failure conditions and constraints
A missing `--cases` flag (or no `case_id` after it) is a hard input error — `__parse_cases` returns `ok = false`, and the self-test's `l0_missing_cases_xfail` case exercises this guard by calling `__parse_cases` on a synthesized empty token list and confirming `ok = false` (recorded as `guard_fired = 1.0`). A capture whose value, as the host reads it, does not reproduce what the case put within an absolute tolerance of `1e-12` fails the corresponding test's predicate.

A case file that is absent or unreadable under §2 makes `__read_case` return `ok = false`, which the self-test's `l0_missing_case_file_xfail` case exercises. A getter's refusal (§3.2) stops the program with a non-zero exit status (`error stop`), so that no evidence is written from a value the case does not hold.

## 5. Public API and compatibility
The published `operation_id`s are exactly: `harness_fortran_cpu__parse_cases`, `harness_fortran_cpu__real_token`, `harness_fortran_cpu__capture_open`, `harness_fortran_cpu__capture_real`, `harness_fortran_cpu__capture_array_r1`, `harness_fortran_cpu__capture_array_r2`, `harness_fortran_cpu__capture_array_r3`, `harness_fortran_cpu__capture_array_r4`, `harness_fortran_cpu__capture_commit`, `harness_fortran_cpu__capture_close`, `harness_fortran_cpu__write_perf`, `harness_fortran_cpu__read_case`, `harness_fortran_cpu__get_real`, `harness_fortran_cpu__get_int`, `harness_fortran_cpu__get_bool`, `harness_fortran_cpu__get_array_r1`, `harness_fortran_cpu__get_array_r2`, `harness_fortran_cpu__get_array_r3`, `harness_fortran_cpu__get_array_r4`. The module also publishes the derived type `harness_fortran_cpu__h_var`.

A change breaking compatibility of any signature (or of a published derived type's component layout) is a **breaking change released under a new `spec_version`**, not a rename: `0.2.1` → `0.3.0` added the `case_id` component to `h_mb_entry`. A change to how the published surface is CARRIED — the §5.1 / `IR public_api.signatures` REPRESENTATION — is likewise released under a new `spec_version` even when the ABI is byte-identical; an edit under an unchanged version is still re-derived by the derivation key (`docs/RUNBOOK.md` "Updating a shared dependency spec"): `0.3.0` → `0.4.0` moved §5.1 and `public_api.signatures` from a Fortran interface block to the language-neutral structured form (`{symbol, signature}`); the published operations, argument types/ranks/`intent`s, and component layouts are unchanged. `0.4.0` → `0.5.0` began transcribing §5.1's value-pinned `module_parameters` (the `dp` / `case_id_len` values) into the `IR`'s `public_api.module_parameters` (a new carrier the `--stage compile` gate pins == §5.1 by value); the §5.1 block, the published operations/types, and the generated ABI are byte-identical — only the IR representation gained the field, so freshness must re-certify a stale `0.4.0` IR that lacks it. `0.5.0` → `0.6.0` made the §5.1 / `IR` leaf vocabulary fully language-neutral: a string length is `deferred` / `assumed` (not the Fortran `:` / `*`) and a kind value is `float64` / `float32` (not `real64` / `real32`); the language backend lowers these tokens to their Fortran spelling, so the published operations, argument types/ranks/`intent`s, component layouts, and the generated ABI are byte-identical — only the leaf-facing representation changed, so freshness must re-certify a stale `0.5.0` IR carrying the old tokens. `0.6.0` → `0.7.0` extends the self-test to exercise the per-case metric fold: the new case `l0_metric_leaf_pass` supplies two sentinel `h_metric` records and the new test of the same name asserts their serialized addresses, so a `__write_diagnostics` that drops the caller-supplied metrics is rejected inside this node instead of only in a consuming physics node. The §5.1 block, the published operations and types, the component layouts, and the generated ABI are unchanged; the self-test behavior and the test profile changed, so freshness must re-certify a stale `0.6.0` IR whose predicates and diagnostics contract lack the new case. Within `0.7.0` the self-test gained the case and test `l0_nonfinite_token_pass` (issue #315), which pins how `__emit_real` spells a non-finite real; the published signatures and types are unchanged, so no `spec_version` is released for it, and a certified `IR` that lacks the case is invalidated by its derivation key, which hashes this document and `tests.md`, not by its version. Within `0.7.0` the self-test's `steps` and `cells_updated` were fixed to the number of cases run, and a non-positive measured `walltime_sec` to `1.0e-9` (issue #394), so that the relative residual of `l0_perf_derived_pass` is defined; the published signatures and types are unchanged, so no `spec_version` is released for it, and every certified `IR` is re-derived, because its derivation key hashes this document and `tests.md`. Within `0.7.0` §2 and §3 state that a check status is written without the blanks that pad it to its width, and the self-test's `l0_metric_leaf_pass` case gained the check `status_na`, supplied padded, whose written value the `post_execute` gate reads (issue #437); the published signatures and types — including the width-4 `status` component of `h_check` — are unchanged, so no `spec_version` is released for it, and every certified `IR` is re-derived, because its derivation key hashes this document and `tests.md`. `0.8.0` (issue #512) changes the signature of `__write_perf`: it drops the `threads_per_rank` argument and writes the thread count the run's shared-memory parallel runtime gives the program, which it obtains itself at run time (§3.2), so that `perf.json` records the count a run was launched with rather than a value its caller chose, and the code that obtains the count is the code this node's own Validate run checks. The host-rendered runner of `0.8.0`'s consumers calls the seven-argument `__write_perf`, which `0.7.0` does not publish, so the release is a new `spec_version`; every other signature and every published type is unchanged. `0.9.0` (issue #582) removes `__write_diagnostics`, `__write_metrics_basis` and the derived types `h_check`, `h_metric`, `h_case_result` and `h_mb_entry`, the test `l0_metric_leaf_pass` with its case, and the test `l0_multi_case_evidence_pass`: the host judges every test from the captures, so no runner writes `diagnostics.json` or `raw/metrics_basis.json`, and every snapshot holds every declared variable. The remaining signatures and `h_named` are unchanged. `0.10.0` (issue #589) adds `__read_case`, `__get_real`, `__get_int`, `__get_bool` and `__get_array_r1..r4`, which read the host-written case file (§2, §3.2), and has `__parse_cases` keep the spec path it used to skip; the self-test gains `l0_read_case_roundtrip_pass`, `l0_read_case_second_pass` and `l0_missing_case_file_xfail`. No signature of `0.9.0` changes, and the next version of the host-rendered runner calls the new operations, which a harness of `0.9.0` does not publish, so the release is a new `spec_version`. `0.11.0` (issue #644) replaces `__emit_real`, `__emit_int`, `__emit_bool`, `__emit_array_r1..r4`, `__box`, `__write_snapshot` and the type `h_named` by the capture operations `__capture_open`, `__capture_real`, `__capture_array_r1..r4`, `__capture_commit`, `__capture_close` and the type `h_var` (§3.1, §3.2), and publishes `__real_token`, which is what `__emit_real` was: a consuming runner names a capture group and a value and holds no file name and no JSON, so the host renders one runner whatever output format the target's harness writes. The series is written in chunks, one before each restart point, and every case has one. The test `l0_boolean_literal_pass` and its case are removed with `__emit_bool`: a capture holds no boolean. The host creates the group directories of `raw/state_snapshots/` for every node, this one included. These signatures are a breaking change, released under this version, and the capability it adds to those it provided is `capture_format_json@1`. Dependent nodes are not migrated by hand and need no content-free version bump of their own. The workflow enforces the skew mechanically at two points:

- **Regeneration** — a node's certified dependency resolution is recorded in its `dependency_graph.json` sidecar; when the catalog moves the harness to a new version, every dependent's recorded resolution stops matching the one `deps.yaml` + `spec_catalog.yaml` derive, so the dependency-freshness readiness check reports it stale and `run_workflow.py --with-deps` re-certifies the closure bottom-up.
- **Skew fail-close** — a consumer that would nonetheless render its runner glue against a drifted interface is stopped before Build by the renderer's signature pin (the language backend's `assert_harness_pin`, reached through `tools/host_render.py`), which compares this §5.1 block against the certified harness IR's `public_api.signatures` and its generated model source.

### 5.1 Canonical interface block
The exact published surface, as a machine-readable **language-neutral** signature block (`module_parameters` / `types` / `procedures`). It describes each published type and operation abstractly — for every argument, result, and derived-type component: its `name`, neutral `type` (`real` / `integer` / `logical` / `string` / `derived`), `rank`, and (for an argument) `intent`; plus the value-pinned module parameters the signatures reference. The vocabulary is neutral throughout: a `string` length is a neutral token — `deferred` (dynamic length) / `assumed` (caller-known length) / a fixed decimal width / a symbol — never the Fortran `:` / `*`; a module-parameter kind value is `float64` / `float32`, never the Fortran `real64` / `real32`. The target language's binding (here Fortran: `real(dp)`, `character(len=:)` / `character(len=*)`, `type(...)`, assumed-shape `(:)` ranks, `integer, parameter :: dp = real64`, the `<spec_id>__` names) is produced by the language backend (`tools/backends/language/fortran/signatures`), not authored here — so this contract is not tied to Fortran. The generated `harness_fortran_cpu_model.f90` must publish every type and procedure below with the signature this block describes, and declare every module parameter below with the value this block pins (whether it exports a module parameter is free, §3) (formatting/continuations/comments may differ; names, argument order, types, ranks, `intent`s, `result` names, and component layout may not). The deterministic gates render this block to the target language and pin it: the `--stage compile` gate cross-checks its symbol set against §5, and the `Generate.static` gate pins the generated model source against these signatures (normalized: comments stripped, continuations joined, case-folded, whitespace-insensitive).

```yaml
module_parameters:
- name: dp
  value: float64
- name: case_id_len
  value: '64'
types:
- name: harness_fortran_cpu__h_var
  components:
  - name: name
    spec:
      type: string
      len: deferred
      alloc: true
  - name: rank
    spec:
      type: integer
  - name: shape
    rank: 1
    dims:
    - '4'
    spec:
      type: integer
  - name: history_capacity
    spec:
      type: integer
  - name: restart
    spec:
      type: logical
procedures:
- kind: subroutine
  name: harness_fortran_cpu__parse_cases
  args:
  - name: tokens
    rank: 1
    intent: in
    spec:
      type: string
      len: assumed
  - name: ntokens
    intent: in
    spec:
      type: integer
  - name: case_ids
    rank: 1
    intent: out
    spec:
      type: string
      len: case_id_len
  - name: ncases
    intent: out
    spec:
      type: integer
  - name: ok
    intent: out
    spec:
      type: logical
- kind: function
  name: harness_fortran_cpu__real_token
  args:
  - name: x
    intent: in
    spec:
      type: real
      kind: dp
  result:
    name: s
    spec:
      type: string
      len: deferred
      alloc: true
- kind: subroutine
  name: harness_fortran_cpu__capture_open
  args:
  - name: case_id
    intent: in
    spec:
      type: string
      len: assumed
  - name: vars
    rank: 1
    intent: in
    spec:
      type: derived
      name: harness_fortran_cpu__h_var
  - name: restart_point_capacity
    intent: in
    spec:
      type: integer
  - name: series_names
    rank: 1
    intent: in
    spec:
      type: string
      len: assumed
  - name: series_capacity
    intent: in
    spec:
      type: integer
- kind: subroutine
  name: harness_fortran_cpu__capture_real
  args:
  - name: group
    intent: in
    spec:
      type: string
      len: assumed
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  - name: x
    intent: in
    spec:
      type: real
      kind: dp
- kind: subroutine
  name: harness_fortran_cpu__capture_array_r1
  args:
  - name: group
    intent: in
    spec:
      type: string
      len: assumed
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  - name: a
    rank: 1
    intent: in
    spec:
      type: real
      kind: dp
- kind: subroutine
  name: harness_fortran_cpu__capture_array_r2
  args:
  - name: group
    intent: in
    spec:
      type: string
      len: assumed
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  - name: a
    rank: 2
    intent: in
    spec:
      type: real
      kind: dp
- kind: subroutine
  name: harness_fortran_cpu__capture_array_r3
  args:
  - name: group
    intent: in
    spec:
      type: string
      len: assumed
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  - name: a
    rank: 3
    intent: in
    spec:
      type: real
      kind: dp
- kind: subroutine
  name: harness_fortran_cpu__capture_array_r4
  args:
  - name: group
    intent: in
    spec:
      type: string
      len: assumed
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  - name: a
    rank: 4
    intent: in
    spec:
      type: real
      kind: dp
- kind: subroutine
  name: harness_fortran_cpu__capture_commit
  args:
  - name: group
    intent: in
    spec:
      type: string
      len: assumed
  - name: step
    intent: in
    spec:
      type: integer
  - name: t
    intent: in
    spec:
      type: real
      kind: dp
- kind: subroutine
  name: harness_fortran_cpu__capture_close
  args: []
- kind: subroutine
  name: harness_fortran_cpu__write_perf
  args:
  - name: case_id
    intent: in
    spec:
      type: string
      len: assumed
  - name: target
    intent: in
    spec:
      type: string
      len: assumed
  - name: steps
    intent: in
    spec:
      type: integer
  - name: cells_updated
    intent: in
    spec:
      type: integer
  - name: walltime_sec
    intent: in
    spec:
      type: real
      kind: dp
  - name: mpi_ranks
    intent: in
    spec:
      type: integer
  - name: gpu_devices
    intent: in
    spec:
      type: integer
- kind: subroutine
  name: harness_fortran_cpu__read_case
  args:
  - name: case_id
    intent: in
    spec:
      type: string
      len: assumed
  - name: ok
    intent: out
    spec:
      type: logical
- kind: function
  name: harness_fortran_cpu__get_real
  args:
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  result:
    name: x
    spec:
      type: real
      kind: dp
- kind: function
  name: harness_fortran_cpu__get_int
  args:
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  result:
    name: i
    spec:
      type: integer
- kind: function
  name: harness_fortran_cpu__get_bool
  args:
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  result:
    name: b
    spec:
      type: logical
- kind: subroutine
  name: harness_fortran_cpu__get_array_r1
  args:
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  - name: a
    rank: 1
    intent: out
    spec:
      type: real
      kind: dp
      alloc: true
- kind: subroutine
  name: harness_fortran_cpu__get_array_r2
  args:
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  - name: a
    rank: 2
    intent: out
    spec:
      type: real
      kind: dp
      alloc: true
- kind: subroutine
  name: harness_fortran_cpu__get_array_r3
  args:
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  - name: a
    rank: 3
    intent: out
    spec:
      type: real
      kind: dp
      alloc: true
- kind: subroutine
  name: harness_fortran_cpu__get_array_r4
  args:
  - name: name
    intent: in
    spec:
      type: string
      len: assumed
  - name: a
    rank: 4
    intent: out
    spec:
      type: real
      kind: dp
      alloc: true
```

## 6. Prohibitions
- No physics: the harness must embed no per-case kernel or per-test judgment logic; the kernel is the consuming physics node's, and the host judges each test.
- The capture operations write what they are given: every value put, under the name it was put with, and no key but those, `step` and `t`. A body that treats a name, a case id or a group's content specially is forbidden, because a consuming physics node supplies arbitrary names and values. (`__write_perf` receives only scalars.) The envelope `perf.json`'s field set stays as specified in §2.
- No truncating or language-specific serialization anywhere in the generated source: never a numeric form that may drop a leading digit, never a language-specific boolean token (no document this node writes holds a boolean), never a non-finite real handed to a numeric format or written as `null` (branch to the literal §2 names). The forbidden Fortran realizations (`F0` / `F0.d` numeric, an `L`-family logical descriptor) are enumerated in `docs/backends/language/fortran/RUNNER_OUTPUT.md` §1.
- Never write `verdict.json`, `aggregate_verdict.json`, `summary.json`, or `trial_meta.json` — not even as a literal filename inside a comment or example string.
- A getter returns the value the case file holds or stops the program (§3.2): a default, a value left from an earlier case and a value this node computes are forbidden, and the only file the reading operations open is the case file `__read_case` names.
- No launch of an external interpreter (`python` / `bash` / `sh` / `node`).
- The thread count is the runtime's: the `threads_per_rank` `__write_perf` writes to `perf.json` is the maximum team size the shared-memory parallel runtime reports when the program runs, or `1` in a build without that runtime enabled, and no other — never a constant of this node, a count read from an environment variable or the host's configuration, or one chosen by a preference of this node.
- All output paths are written relatively (so `cd $(RUNDIR)` redirects them); no hardcoded / sequential capture filename literal.

## 7. Traceability
Record the harness adoption in `component_catalog.yaml` (as the `(language, hardware)` = `(fortran, cpu)` runner harness) and the resolved harness version each dependent physics node was certified against.

## 8. tests reference
The corresponding `tests.md` is `spec/infrastructure/infra/harness/harness_fortran_cpu/tests.md`, with `test_profile_version` of `0.16.0`.
