# Controlled Spec: Fortran/CPU runner harness writing NetCDF (infrastructure spec)

## 0. Meta information
- `spec_id`: `harness_fortran_cpu_netcdf`
- `spec_version`: `0.1.0`
- `status`: `controlled_draft`
- `spec_kind`: `infrastructure`
- `domain`: `infra`
- `family`: `harness`

## 1. Responsibility and scope
This `infrastructure` node is responsible for the shared **runner plumbing** that every Fortran/CPU physics node's runner is built against on a target whose output format is `netcdf`: argv / `--cases` parsing, the case-set loop driver, the reading of the host-written case file (§3.2), the capture of a case's state through the format-neutral capture interface (§3.2), which this node writes in the `netcdf` output format, and the `perf.json` writer — the last of which records the **thread count** the run's shared-memory parallel runtime gives the program, which its writer obtains at run time (§3.2). It carries **no physics**: the kernel belongs to the consuming physics node, never here, and the host judges each test from the captures. It targets `(language=fortran, hardware=cpu)` with the `netcdf` output format; the `json` output format on the same target is `harness_fortran_cpu`, a separate harness node.

The node declares the external library `netcdf_fortran` (its `deps.yaml`), the Fortran binding of the NetCDF library, and reaches it only through that library's module `netcdf`: its capture operations create, define, write, synchronize and close the case's file through the binding's `nf90_` procedures. Every node built over this harness links the library; no physics source may reference it.

The node's own generated code is a `harness_fortran_cpu_netcdf_model.f90` publishing the plumbing operations plus a self-test `harness_fortran_cpu_netcdf_runner.f90` that exercises them and emits the standard runner outputs (so the harness is verified through the exact same Compile→Generate→Build→Validate path as any node; it is self-hosting — the self-test writes its evidence through its own capture operations).

The published surface is a **binding, signature-level contract**: §5.1 gives the canonical language-neutral structured interface block (every public type and every operation signature) in a machine-readable fenced code block, from which the language backend renders the Fortran surface. The generated `harness_fortran_cpu_netcdf_model.f90` must publish exactly those signatures; a consuming physics node's host-rendered runner glue is written against them and names a capture group and a value and holds no knowledge of the output format (the file and the NetCDF calls live only inside these certified operations).

## 2. input/output contract
Input (to the self-test runner): the standard runner argv `--cases <spec.ir.yaml> <case_id>...` — the spec path is taken positionally, `__parse_cases` keeps it, and the spec file itself need not be read; the token array holds that path whole, so its width is not `case_id_len`; the trailing tokens are the `case_id`s to run, one per `case.test_case_set[]`. The main program marshals the process argv into a token array and calls `harness_fortran_cpu_netcdf__parse_cases` on it. Each `case_id` selects, by dispatching on the `case_id`, the plumbing aspect that case verifies. Every `case_id` is also the `test_id` of the single-target test that names it, and every test is single-target. A missing `--cases` flag (no cases) makes `__parse_cases` return `ok = false` (the input guard); the guard case verifies this by calling `__parse_cases` on a length-0 token array.

Input (to `__read_case`): the **case file** `case_inputs/<case_id>.json`, which the host writes beside `<spec.ir.yaml>` for every case before the program runs (`docs/workflow/RUNNER_OUTPUT_CONTRACT.md` §6), one flat JSON object on one line of any length. Its keys are names (`inputs.initial.x_in`, `derived.dt`, a state variable's name) and hold no `\` escape; each value is a number or a rectangular nested array of numbers of depth 1 to 4, an empty array admitted at any level. A number is an RFC 8259 token, so `3`, `-1.5` and `1e-30` are all numbers. JSON whitespace between tokens is admitted. A document holding anything else — a nested object, a string, `true` / `false` / `null`, a `NaN` / `Infinity` / `-Infinity` token, a ragged array, an array deeper than 4, a number that does not convert to a finite binary64 value — is unreadable.

Output artifacts (produced by the writers, into the run node dir relative to `cwd=RUNDIR`):
- **`perf.json`** — one object with `case_id`, `target` (`"cpu"`), `walltime_sec`, `steps`, `cells_updated`, `throughput_cells_per_sec` (`= cells_updated / walltime_sec`), a `parallelism` object (`mpi_ranks`, `threads_per_rank`, `gpu_devices`, `parallel_degree_total = mpi_ranks*threads_per_rank*max(gpu_devices,1)`), and `timestamp_utc` (ISO-8601). `threads_per_rank` is the number of threads the run was given, which `__write_perf` obtains from the run's shared-memory parallel runtime when it is called (§3.2) — never a constant and never a value its caller passes: the Validate phase runs the same binary at several thread counts and compares it with the count each run was launched with.
- **The captures** — one file per case under `raw/state_snapshots/`, written only through the capture operations of §3.2, in the layout §3.2 names. The self-test commits one `final` record per case — at step `1` and time `0.0` in every case but `l0_capture_groups_pass` (each of the others has a single `steps=1` step) — and that case and `l0_case_fanout_pass` alone commit records of the other groups (§3). An `initial` and a `final` record hold every variable declared in `snapshot_schema.json`, a `history` and a `restart_point` record the variables §3 names for it, and every record the `step` and `t` its commit carries. The snapshot state variables, with their `shape_expr`, are listed under the case that computes them:
  - `l0_numeric_roundtrip_pass`: `x_out` (rank-1, `[3]` — the sentinel reals, captured through `__capture_array_r1`).
  - `l0_array_emit_pass`: `a1` (rank-1, `[2]`), `a2` (rank-2, `[2,2]`), `a3` (rank-3, `[2,2,2]`), `a4` (rank-4, `[2,2,2,2]`) — the sentinel arrays, captured through `__capture_array_r1..r4`.
  - `l0_case_fanout_pass`: `case_index` (scalar — this case's ordinal in the run).
  - `l0_perf_derived_pass`: `throughput_cells_per_sec`, `cells_updated`, `walltime_sec` (scalars — the three fields as the case re-parsed them from the `perf.json` the writer wrote), from which the host evaluates the RELATIVE residual `tests.md` judges.
  - `l0_read_case_roundtrip_pass`: `rc_real`, `rc_whole`, `rc_int`, `rc_true`, `rc_false`, `rc_empty_read`, `rc_e2`, `rc_e3`, `rc_e4` (scalars) and `rc_r1` (`[3]`), `rc_r2` (`[2,3]`), `rc_r3` (`[2,2,3]`), `rc_r4` (`[2,2,2,3]`) — what the getters returned for the case's own case file, captured (§3): `rc_true` / `rc_false` are `1.0` iff `__get_bool` returned true / false for them, `rc_empty_read` is `1.0` iff the arrays `__get_array_r1` and `__get_array_r3` returned for `rc_empty`, and `__get_array_r2` and `__get_array_r3` for `rc_empty2`, each have size 0, and `rc_e2` / `rc_e3` / `rc_e4` are one element of `rc_r2` / `rc_r3` / `rc_r4` obtained by indexing the array the getter returned: every index at its first value but the last, which is at its second.
  - `l0_read_case_second_pass`: `rc_real` and `rc_r1` of the same shapes, read from that case's own case file.
  - `l0_missing_case_file_xfail`: `read_refused` (scalar, `1.0` when `__read_case` returned `ok = false` for a `case_id` no case file exists for).
  - `l0_missing_cases_xfail`: `guard_fired` (scalar, `1.0` when `__parse_cases` on a length-0 token array returned `ok = false`).
  - `l0_capture_groups_pass`: `g_s` (scalar), `g_r1` (`[3]`), `g_r2` (`[2,3]`), `g_r3` (`[2,3,4]`) and `g_r4` (`[2,3,4,5]`) — the bases `tests.md` §2 declares, read from the case file and captured in every group at the steps §3 states, each record holding the base plus its committed step; `g_never` (`[2]`, zeros, held by the `initial` and `final` records alone); and `g_z1` (`[0]`) and `g_z2` (`[3,0]`), the two variables of zero extent (`tests.md` §3), which hold no element in any record.
  - `l0_capture_large_pass`: `big` (rank-2, `[512,512]`) — `big(i,j) = i + 0.5 * j` for `i, j = 1..512`, the leading index `i`, computed by the case and captured through `__capture_array_r2`: one record of the size a consuming physics node commits many times per case (a `256 x 256` state of three variables holds `196608` values), so a writer whose cost grows faster than linearly in a record's size (§3.2) shows in this case's run time.
  A variable is declared once in `snapshot_schema.json` and written by every case: in a case it is not listed under it holds a shape-valid placeholder, `0.0` or an array of zeros of its declared shape (the empty array for `g_z1` and `g_z2`), which no predicate of that case reads but `l0_case_fanout_pass`'s, which reads the `g_s` and `g_never` placeholders of the `history` and `restart_point` records §3 has that case commit.

`perf.json` is JSON and follows the abstract runner-output contract's numeric serialization: a finite real as a round-trip-lossless exponential token, an integer as its minimal decimal — a truncating form (one that may drop a leading digit) is forbidden. The target-language realization (for Fortran: reals via `ES24.16E3` then `trim(adjustl())`, or a bounded `Fw.d`, never `F0`/`F0.d`; integers via `I0`) is canonical in `docs/backends/language/fortran/RUNNER_OUTPUT.md` §1, the Fortran binding of `docs/workflow/RUNNER_OUTPUT_CONTRACT.md §4`. A capture holds no token: the case's file stores each value as a 64-bit IEEE real (§3.2), and a capture holds no boolean: the caller converts a logical state to `1.0` / `0.0`.

## 3. Operation definition
The published operations (all under module `harness_fortran_cpu_netcdf_model`, prefix `harness_fortran_cpu_netcdf__`) are the plumbing surface a physics-node runner reuses. The canonical machine-readable interface (every signature and public type, verbatim) is §5.1; the prose below states each operation's contract. The module declares two module-level integer parameters the signatures reference: `dp` (the double-precision real kind token; §5.1 value `float64` = IEEE-754 binary64) and `case_id_len = 64` (the fixed storage width of a parsed case id, known to the caller, because an assumed-length `intent(out)` string dummy is disallowed). Their accessibility is not part of this contract — §5.1 pins their VALUES, not whether the module exports them: the module may export them or keep them private, and a consuming runner imports them where the module exports them or declares its own matching values (a double-precision-real kind for the actuals it passes and a fixed-width (`case_id_len`) case-id buffer). Each of these shapes is correct. Their VALUES are pinned (the gate rejects a drifted `case_id_len`), because a consumer's hardcoded length must match. The Fortran binding of these tokens (`dp = real64`, `character(len=64)`) is produced by the language backend, not authored here.

### 3.1 Published derived types
The module publishes one derived type (named by its fully-qualified `harness_fortran_cpu_netcdf__<name>`):
- `harness_fortran_cpu_netcdf__h_var` `{ name: string (dynamic length), rank: integer, shape: integer array of 4, history_capacity: integer, restart: logical }` — what `__capture_open` is told of one state variable: its `name`, its `rank` (0 for a scalar), its extents in the first `rank` entries of `shape` (the entries after them are `0` and are not read), the number of `history` records the case may commit for it, and whether a `restart_point` record holds it. It carries what this node's output format defines before a case's first record: `__capture_open` defines the variable's dimensions from its `rank` and `shape`, its `history` group when `history_capacity` is positive, and its `restart_point` value when `restart` is true. (Types and lengths use the §5.1 neutral vocabulary; the binding is the language backend's.)

### 3.2 Published operations
- `harness_fortran_cpu_netcdf__parse_cases(tokens, ntokens, case_ids, ncases, ok)` — parse a supplied argv **token list** (NOT the process argv directly, so the guard is exercisable with a synthetic list): find `--cases` in `tokens`, keep the following token as the spec path (the positional token, which `__read_case` finds the case files beside and which the call replaces only when it returns `ok = true`), collect the remaining tokens as `case_ids` (each stored in a fixed-width `case_id_len` slot); return `ok = false` when `--cases` is absent from `tokens` or no `case_id` follows it (the input guard), else `ok = true`. The self-test's main program marshals the real process argv (via the language-standard argv accessors) into `tokens` for the normal cases, and passes a length-0 `tokens` for the guard case.
- `harness_fortran_cpu_netcdf__capture_open(case_id, vars, restart_point_capacity, series_names, series_capacity)` — start the output of the case `case_id`: create its file and define in it every dimension and variable a record of the case can hold, from `vars` (§3.1) and `series_names`, then set every group's count to `0`. `vars` describes the case's state variables (§3.1); `restart_point_capacity` and `series_capacity` are the most `restart_point` records and `series` rows the case commits; `series_names` are the names of the series' columns, without `step` and `t`, which every commit carries. A name is its text without trailing blanks: the caller's array pads its elements to one width, and the name written and matched is the unpadded one. The same holds for every `case_id`, `group` and `name` argument of the capture operations. One case is open at a time.
- `harness_fortran_cpu_netcdf__capture_real(group, name, x)`, `harness_fortran_cpu_netcdf__capture_array_r1(group, name, a)` … `__capture_array_r4(group, name, a)` — put the value `name` into the open record of `group`, one of the five words `initial`, `final`, `history`, `restart_point`, `series`: a scalar, or an assumed-shape rank-1..4 `real (kind dp)` array, whose element of indices `(i1, …, ir)` is the element of those indices in the file (the layout gives the order of the dimensions). A value with no element writes nothing, and its zero-extent dimension stays empty. In `series` a value is a scalar and `name` is one of `series_names`. Every value is float64: the caller converts an integer or a logical state. A non-finite value is stored as the IEEE value it is and is not refused here; the host refuses it.
- `harness_fortran_cpu_netcdf__capture_commit(group, step, t)` — close the open record of `group` as the record of the committed step `step` at time `t`: it holds the values put into `group` since that group's previous commit, and `step` and `t`, and the group's count is the number of its records. A `history` commit closes one sample of the variables put into `history` since the previous `history` commit. A `series` commit closes one row, and the caller has put every one of `series_names` into it. Nothing a case commits is evidence before `__capture_close` returns.
- **Cost.** The time a capture operation takes grows linearly with the number of array elements and scalars it writes: the text already built for an array, or for the record it is put into, is not copied again for each array element written. A consuming physics node commits records of `10^5` values and more, many per case, and every run of its binary is bounded in time.
- `harness_fortran_cpu_netcdf__capture_close()` — end the open case's output: write everything held to disk, then mark the file complete, then close it. The case is complete when the call returns; the host reads no record of a case whose file is not complete.
- Each capture operation stops the program with a non-zero exit status when it is called with no case open (`__capture_open`: with one open), with a `group` that is none of the five words, with an array put into `series`, with a `series` name that is none of `series_names`, with `step` or `t` among `series_names`, with a value the case's file has no variable for (the file is defined from `vars` when the case opens: a `name` that is no variable of `vars` outside `series`, an array of another rank than `vars` gives the name, a `history` value of a variable whose `history_capacity` is `0`, a `restart_point` value of a variable whose `restart` is false), or when the NetCDF binding reports an error.
- **The `netcdf` layout** is the one `docs/backends/output_format/netcdf/CAPTURE_LAYOUT.md` states, the capture layout that ends the runner-output contract this node's producer is given, and the host's reading of a run's captures on this node's output format: one netCDF-4 file per case, `raw/state_snapshots/<C>.nc` with `<C>` the case id, opened by a path relative to the working directory and built at run time from the case id (never a hardcoded or sequential literal), the names, types, dimensions, fill values and counts of every variable, and the order in which ending the output synchronizes the file, sets `complete` and closes it. The directory `raw/state_snapshots/` exists before the program runs — the host creates it — and this node creates no directory and lists none.
- `harness_fortran_cpu_netcdf__write_perf(case_id, target, steps, cells_updated, walltime_sec, mpi_ranks, gpu_devices)` — write `perf.json` with all required fields incl. the derived `throughput_cells_per_sec` and the `parallelism` object (`parallel_degree_total = mpi_ranks*threads_per_rank*max(gpu_devices,1)`). It writes `mpi_ranks` and `gpu_devices` exactly as its caller passes them. It takes no thread count: it writes as `threads_per_rank` the number of threads the run's shared-memory parallel runtime gives the program, and uses that number in `parallel_degree_total`. **The thread count.** The HOST chooses how many threads the program runs with, and varies it between runs of the same binary (the Validate phase runs the binary once per execution shape of the target and compares each run's `perf.json` with the count it was launched with): it sets the count in the run's launch environment for the target's shared-memory parallel runtime, and the program learns it from that runtime alone. `__write_perf` obtains it AT RUN TIME on every call, as the maximum team size that runtime reports — never a constant and never read from an environment variable by this node. The query is reached through conditional compilation, so that a build without the runtime enabled compiles it away and the count written is `1` (for Fortran with OpenMP: `omp_get_max_threads()` from the intrinsic module `omp_lib`, both the import and the call written on `!$` conditional-compilation lines, after an unconditional assignment of `1`; `docs/backends/parallel/openmp/GENERATE_RULES.md` §5). It starts no parallel region. Because the count is obtained inside this writer, the self-test and the host-rendered runner of every consuming node record it through the same code, and this node's own Validate run, which runs the self-test at several thread counts, is what checks it.
- `harness_fortran_cpu_netcdf__read_case(case_id, ok)` — read the case file of `case_id`: `<dir>/case_inputs/<case_id>.json`, where `<dir>` is the directory part of the spec path the last successful `__parse_cases` kept (a spec path with no directory part: the current directory). `ok = true` when the file exists and is readable under §2: the case it holds replaces the one loaded before. Otherwise `ok = false` and no case is loaded, so no value of an earlier case remains readable; the same holds when no `__parse_cases` has succeeded. This operation does not stop the program.
- `harness_fortran_cpu_netcdf__get_real(name) result(x)` — the value of `name` in the loaded case: any number token, the nearest binary64 value.
- `harness_fortran_cpu_netcdf__get_int(name) result(i)` — the value of `name`, an integer token (no fraction and no exponent) that fits the default integer.
- `harness_fortran_cpu_netcdf__get_bool(name) result(b)` — the value of `name`, exactly the token `1` (true) or `0` (false): on an `infrastructure` node the host writes a boolean input that way, and a physics node's `IR` states its value as that token.
- `harness_fortran_cpu_netcdf__get_array_r1(name, a)` … `__get_array_r4(name, a)` — allocate `a` to the extents of `name`'s array and fill it from the JSON nesting the case file holds it in: the leading array index is the outermost JSON level. An empty array at a level gives extent 0 there and at every level below it, so `[]` reads at every rank and `[[],[]]` at rank 2 and above; any other value whose nesting depth differs from the rank is a mismatch. Number tokens read as in `__get_real`.
- Each getter holds no default and takes no `ok`: with no case loaded, a name the case does not hold, a scalar asked as an array or an array as a scalar, or a token its type does not admit, it stops the program with a non-zero exit status (`error stop`).

The self-test `harness_fortran_cpu_netcdf_runner.f90` calls `__parse_cases`, then for each `case_id` runs (dispatching on the `case_id`) the plumbing check that case names — verifying the capture round-trips, the case fan-out, and the input guard — and captures that case's snapshot variables: `__capture_open` for the case (every declared variable in `vars`, the two names `g_a` and `g_series_long` as `series_names`, and, in every case but `l0_capture_groups_pass` and `l0_case_fanout_pass`, a `history_capacity` of `0` for every variable, `restart_point_capacity = 0` and `series_capacity = 0`), one put per declared variable into `final`, `__capture_commit` of `final` at step `1` and time `0.0`, and `__capture_close`; such a case commits no `series` row, and its series holds none. The `l0_case_fanout_pass` case opens its output with a `history_capacity` of `2` for every declared variable, `restart` true for `g_s`, `g_r1` … `g_r4`, `g_z1` and `g_z2` and false for every other, `restart_point_capacity = 2` and `series_capacity = 2`, and, before its `final` record, commits at step `2` and time `0.0` one `history` record holding the placeholders of `g_s` and `g_never`, one `restart_point` record holding the placeholders of the seven restart variables, and one `series` row holding `g_a = 1.0` and `g_series_long = 0.0`. It commits fewer records than its capacities, at a step other than `1`, samples variables it commits no record of, and records a sample of `g_never`, which no restart point holds. With `l0_capture_groups_pass` it is one of two cases that commit records of every stacked group, so a writer that carries a group's record count from one case into the next leaves the later of the two a record its predicates or the host refuse (`tests.md` §6). It writes and reads no file under `raw/state_snapshots/` in any other way, and does not list that directory. It reads its clock before the first case and again after the last, and then calls `__write_perf` once, with that elapsed time as `walltime_sec`, or `1.0e-9` when the elapsed time it reads is not positive (a clock tick longer than the interval), with `mpi_ranks = 1` and `gpu_devices = 0` (the thread count is the writer's own, above), and with `steps` and `cells_updated` both equal to the number of cases its `__parse_cases` call on the program's argv returned: each case counts as one step that updates one cell, whatever records it commits (`tests.md` §3), so each value is the number of cases that ran, independent of any case's `grid` member and positive whenever a case ran. With both quantities positive, the relative residual of the `tests.md` §6 `l0_perf_derived_pass` judgment is defined. Everything after that call is outside the measured interval. Only then does it fill the `l0_perf_derived_pass` variables, re-parsing the `perf.json` that call wrote. A case's captures are made inside the loop or after the `__write_perf` call — those of `l0_perf_derived_pass` only after those variables are filled. The captures are the evidence the host judges that plumbing by.

The `l0_read_case_roundtrip_pass` case calls `__read_case` with its own `case_id`, reads every sentinel of `tests.md` §2 through the getter of its type under its key `inputs.initial.<name>`, and captures what the getter returned; it holds no copy of a sentinel, and the program stops as §3.2 states when a getter refuses. The `l0_read_case_second_pass` case does the same for the two sentinels it declares, so a reader that kept an earlier case fails one of the two. The `l0_missing_case_file_xfail` case calls `__read_case` with the literal id `l0_no_such_case_file`, which names no case of the IR, so no case file exists for it, and captures `read_refused` (`1.0` when `ok = false`, else `0.0`). No case but these three and `l0_capture_groups_pass` calls `__read_case`.

The `l0_capture_groups_pass` case calls `__read_case` with its own `case_id` and reads, under the keys `inputs.initial.<name>`, `g_n`, `g_every_a`, `g_every_b` and `g_every_p` through `__get_int`, `g_dt` and `g_s` through `__get_real`, and `g_r1` … `g_r4` through `__get_array_r1` … `__get_array_r4`; it reads no key under `inputs.expected` and holds no copy of a value it reads. It opens the case with every declared variable in `vars` — a `history_capacity` of `g_n / g_every_a` (integer division) for `g_s`, `g_r1`, `g_r2` and `g_z1`, of `g_n / g_every_b` for `g_r3`, `g_r4` and `g_z2`, and of `0` for every other variable; `restart` true for those seven and false for every other — with `restart_point_capacity = g_n / g_every_p`, the two series names and `series_capacity = g_n`. It then commits, in this order: an `initial` record at step `0` and time `0.0`; for each step `k = 1 .. g_n`, at time `g_dt * k`, one `series` row holding `g_a = g_s + k` and `g_series_long` = the first element of `g_r1` minus `k`, then, when `k` is a multiple of `g_every_a` or of `g_every_b`, ONE `history` record holding `g_s`, `g_r1`, `g_r2` and `g_z1` when `k` is a multiple of `g_every_a` and `g_r3`, `g_r4` and `g_z2` when it is a multiple of `g_every_b` (all seven at a step that is a multiple of both), then, when `k` is a multiple of `g_every_p`, one `restart_point` record holding those seven variables; and last the `final` record, at step `g_n` and time `g_dt * g_n`. In a record of step `k` each of `g_s`, `g_r1` … `g_r4` holds its base plus `k` in every element (the base itself in the `initial` record), and `g_z1` and `g_z2` are arrays of extents `[0]` and `[3,0]`. The `initial` and the `final` record hold every declared variable, the ones this case does not compute as their placeholders; `g_never` is in no `history` and no `restart_point` record. It passes the `name` of the `g_a` put as the element of the `series_names` array it opened the case with, which is padded to that array's width, and the `name` of the `g_series_long` put as a literal.

The `l0_capture_large_pass` case calls no reader: it computes `big` as §2 states and, as every case does, puts it with every other declared variable into `final`. In every other case `big` holds its placeholder, an array of zeros of extents `[512,512]`.

## 4. Failure conditions and constraints
A missing `--cases` flag (or no `case_id` after it) is a hard input error — `__parse_cases` returns `ok = false`, and the self-test's `l0_missing_cases_xfail` case exercises this guard by calling `__parse_cases` on a synthesized empty token list and confirming `ok = false` (recorded as `guard_fired = 1.0`). A capture whose value, as the host reads it, does not reproduce what the case put within an absolute tolerance of `1e-12` fails the corresponding test's predicate.

A case file that is absent or unreadable under §2 makes `__read_case` return `ok = false`, which the self-test's `l0_missing_case_file_xfail` case exercises. A getter's refusal (§3.2) stops the program with a non-zero exit status (`error stop`), so that no evidence is written from a value the case does not hold.

## 5. Public API and compatibility
The published `operation_id`s are exactly: `harness_fortran_cpu_netcdf__parse_cases`, `harness_fortran_cpu_netcdf__capture_open`, `harness_fortran_cpu_netcdf__capture_real`, `harness_fortran_cpu_netcdf__capture_array_r1`, `harness_fortran_cpu_netcdf__capture_array_r2`, `harness_fortran_cpu_netcdf__capture_array_r3`, `harness_fortran_cpu_netcdf__capture_array_r4`, `harness_fortran_cpu_netcdf__capture_commit`, `harness_fortran_cpu_netcdf__capture_close`, `harness_fortran_cpu_netcdf__write_perf`, `harness_fortran_cpu_netcdf__read_case`, `harness_fortran_cpu_netcdf__get_real`, `harness_fortran_cpu_netcdf__get_int`, `harness_fortran_cpu_netcdf__get_bool`, `harness_fortran_cpu_netcdf__get_array_r1`, `harness_fortran_cpu_netcdf__get_array_r2`, `harness_fortran_cpu_netcdf__get_array_r3`, `harness_fortran_cpu_netcdf__get_array_r4`. The module also publishes the derived type `harness_fortran_cpu_netcdf__h_var`.

A change breaking compatibility of any signature (or of a published derived type's component layout) is a **breaking change released under a new `spec_version`**, not a rename. A change to how the published surface is CARRIED — the §5.1 / `IR public_api.signatures` REPRESENTATION — is likewise released under a new `spec_version` even when the ABI is byte-identical; an edit under an unchanged version is still re-derived by the derivation key (`docs/RUNBOOK.md` "Updating a shared dependency spec"). `0.1.0` (issue #644) is the first version: the single-process plumbing of `harness_fortran_cpu@0.11.0` under this node's names, without `__real_token` (a capture holds no token), writing the `netcdf` output format; the capability it provides beside `sync_single_case@1` is `capture_format_netcdf@1`. The workflow enforces a version skew between this node and a dependent mechanically at two points:

- **Regeneration** — a node's certified dependency resolution is recorded in its `dependency_graph.json` sidecar; when the catalog moves the harness to a new version, every dependent's recorded resolution stops matching the one `deps.yaml` + `spec_catalog.yaml` derive, so the dependency-freshness readiness check reports it stale and `run_workflow.py --with-deps` re-certifies the closure bottom-up.
- **Skew fail-close** — a consumer that would nonetheless render its runner glue against a drifted interface is stopped before Build by the renderer's signature pin (the language backend's `assert_harness_pin`, reached through `tools/host_render.py`), which compares this §5.1 block against the certified harness IR's `public_api.signatures` and its generated model source.

### 5.1 Canonical interface block
The exact published surface, as a machine-readable **language-neutral** signature block (`module_parameters` / `types` / `procedures`). It describes each published type and operation abstractly — for every argument, result, and derived-type component: its `name`, neutral `type` (`real` / `integer` / `logical` / `string` / `derived`), `rank`, and (for an argument) `intent`; plus the value-pinned module parameters the signatures reference. The vocabulary is neutral throughout: a `string` length is a neutral token — `deferred` (dynamic length) / `assumed` (caller-known length) / a fixed decimal width / a symbol — never the Fortran `:` / `*`; a module-parameter kind value is `float64` / `float32`, never the Fortran `real64` / `real32`. The target language's binding (here Fortran: `real(dp)`, `character(len=:)` / `character(len=*)`, `type(...)`, assumed-shape `(:)` ranks, `integer, parameter :: dp = real64`, the `<spec_id>__` names) is produced by the language backend (`tools/backends/language/fortran/signatures`), not authored here — so this contract is not tied to Fortran. The generated `harness_fortran_cpu_netcdf_model.f90` must publish every type and procedure below with the signature this block describes, and declare every module parameter below with the value this block pins (whether it exports a module parameter is free, §3) (formatting/continuations/comments may differ; names, argument order, types, ranks, `intent`s, `result` names, and component layout may not). The deterministic gates render this block to the target language and pin it: the `--stage compile` gate cross-checks its symbol set against §5, and the `Generate.static` gate pins the generated model source against these signatures (normalized: comments stripped, continuations joined, case-folded, whitespace-insensitive).

```yaml
module_parameters:
- name: dp
  value: float64
- name: case_id_len
  value: '64'
types:
- name: harness_fortran_cpu_netcdf__h_var
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
  name: harness_fortran_cpu_netcdf__parse_cases
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
- kind: subroutine
  name: harness_fortran_cpu_netcdf__capture_open
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
      name: harness_fortran_cpu_netcdf__h_var
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
  name: harness_fortran_cpu_netcdf__capture_real
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
  name: harness_fortran_cpu_netcdf__capture_array_r1
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
  name: harness_fortran_cpu_netcdf__capture_array_r2
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
  name: harness_fortran_cpu_netcdf__capture_array_r3
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
  name: harness_fortran_cpu_netcdf__capture_array_r4
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
  name: harness_fortran_cpu_netcdf__capture_commit
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
  name: harness_fortran_cpu_netcdf__capture_close
  args: []
- kind: subroutine
  name: harness_fortran_cpu_netcdf__write_perf
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
  name: harness_fortran_cpu_netcdf__read_case
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
  name: harness_fortran_cpu_netcdf__get_real
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
  name: harness_fortran_cpu_netcdf__get_int
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
  name: harness_fortran_cpu_netcdf__get_bool
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
  name: harness_fortran_cpu_netcdf__get_array_r1
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
  name: harness_fortran_cpu_netcdf__get_array_r2
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
  name: harness_fortran_cpu_netcdf__get_array_r3
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
  name: harness_fortran_cpu_netcdf__get_array_r4
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
- The capture operations write what they are given: every value put, under the name it was put with, and no variable but those the layout names. A body that treats a name, a case id or a group's content specially is forbidden, because a consuming physics node supplies arbitrary names and values. (`__write_perf` receives only scalars.) The envelope `perf.json`'s field set stays as specified in §2.
- No truncating or language-specific serialization in `perf.json`: never a numeric form that may drop a leading digit, never a language-specific boolean token. The forbidden Fortran realizations (`F0` / `F0.d` numeric, an `L`-family logical descriptor) are enumerated in `docs/backends/language/fortran/RUNNER_OUTPUT.md` §1.
- The case's file is complete only through `__capture_close`: no other operation sets `complete`, and no file of a case is written outside the capture operations.
- Never write `verdict.json`, `aggregate_verdict.json`, `summary.json`, or `trial_meta.json` — not even as a literal filename inside a comment or example string.
- A getter returns the value the case file holds or stops the program (§3.2): a default, a value left from an earlier case and a value this node computes are forbidden, and the only file the reading operations open is the case file `__read_case` names.
- No launch of an external interpreter (`python` / `bash` / `sh` / `node`).
- The thread count is the runtime's: the `threads_per_rank` `__write_perf` writes to `perf.json` is the maximum team size the shared-memory parallel runtime reports when the program runs, or `1` in a build without that runtime enabled, and no other — never a constant of this node, a count read from an environment variable or the host's configuration, or one chosen by a preference of this node.
- All output paths are written relatively (so `cd $(RUNDIR)` redirects them); no hardcoded / sequential capture filename literal.

## 7. Traceability
Record the harness adoption in `component_catalog.yaml` (as the `(language, hardware)` = `(fortran, cpu)` runner harness of the `netcdf` output format) and the resolved harness version each dependent physics node was certified against.

## 8. tests reference
The corresponding `tests.md` is `spec/infrastructure/infra/harness/harness_fortran_cpu_netcdf/tests.md`, with `test_profile_version` of `0.2.0`.
