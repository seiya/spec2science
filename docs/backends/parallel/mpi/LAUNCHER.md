# Launcher and compiler wrapper — `mpi`

> **Audience: the operator who runs an `mpi` target, and the maintainer of `Validate.execute`
> and `Build`.** No leaf reads this document. It is the `mpi` binding of the `launcher` and
> `compiler_wrapper` capabilities (issue #316); the execute path is
> `docs/workflow/phases/phase_04_validate.md`, and the code is
> `tools/backends/parallel/mpi/launcher.py` and `tools/backends/parallel/mpi/wrapper.py`.

## 1. What runs

A binary built for the `mpi` parallel model is compiled and linked through the runtime's
compiler wrapper, and it runs under the runtime's launcher at `Validate.execute`:

```
mpif90 <the compiler's own flags> ...                               # Generate.gate syntax stage and Build
mpirun -n <ranks> <binary> --cases <spec.ir.yaml> <case_id>...      # Validate.execute, the run
make test                                                           # the quality check: the same binary, one process
```

- `<ranks>` is the target profile's `execution.ranks` (1 when the profile states none). The
  launcher is used for every run of an `mpi` target, a one-rank target included, so the run is
  under the launcher and the quality check is not, whatever the count.
- The quality check's `make test` re-run starts the binary without a launcher. An MPI program
  started that way runs as one process (a singleton), which makes the re-run the serial
  reference the run is compared against (`phase_04_validate.md` §4-2). The comparison records
  both counts (`quality_check.json#comparison.{reference,candidate}.ranks`).
- The ranks form a process grid that the host, not the program, chooses: the harness reads it
  from the environment variable `SPEC2SCIENCE_PROCESS_GRID` (`p1,p2`) and arranges the ranks as
  `(ranks, 1)` when it is unset (the target's harness §3.3, issue #512). The launch shape
  sets it from the profile's `execution.process_grid` (`tools/backends/parallel/mpi/
  execution.py`; `fortran_cpu_mpi` imposes `[2, 2]`), and sets nothing when the profile states
  none. The singleton `make test` re-run is handed `1,1`, so it runs on `(1, 1)` whatever the
  conductor's own environment carries (`host_execution.serial_execution_env`).
- Each grid of the profile's `execution.sweep.grids` is a further run of the same binary and
  cases (issue #512): `mpirun -n <p1 p2>` with `SPEC2SCIENCE_PROCESS_GRID=p1,p2`, after the quality
  check, each compared with the singleton re-run (`phase_04_validate.md` §4-2). A grid arranges
  at most `execution.ranks` ranks; `fortran_cpu_mpi` sweeps `[4, 1]`, `[1, 4]`, `[3, 1]`,
  `[1, 3]`, `[2, 1]`, `[1, 2]`.
- The build control file pins its compiler variable to `mpif90`, and the `Generate.gate` syntax
  stage of the compiler the wrapper runs (`WRAPPED_COMPILER`, `gfortran`) runs with `mpif90` at
  `argv[0]`. The compiler's adapter — its flags and its diagnostics — is unchanged. A target
  whose backend is `mpi` does not pin `toolchain.compiler`; the launch gate refuses the pin.
- `mpif90` is the wrapper name that Intel MPI, Open MPI and MPICH all install. `mpifort` is not
  installed by Intel MPI.
- **Threads inside each rank** (issue #700). A profile whose `parallel.backend` combines `mpi`
  with `openmp` (`mpi+openmp`, `fortran_cpu_mpi_openmp`) runs each rank with
  `execution.threads_per_rank` threads: the launch shape sets the OpenMP backend's variables
  (`OMP_NUM_THREADS`, `OMP_THREAD_LIMIT`) beside `SPEC2SCIENCE_PROCESS_GRID`, the control file
  adds the compiler's thread switch, and `mpirun` passes the variables of its own environment
  to every rank it starts on this host. `mpi` itself declares no `RUNS_THREADS`, so on a profile
  whose value is `mpi` alone the gate refuses a count above one
  (`target_profile._thread_count_violations`). Each `execution.sweep.threads_per_rank` is a
  further run at the certified ranks and grid; the singleton re-run is handed one thread
  (`host_execution.serial_execution_env`). The harness initializes the runtime asking for the
  level under which only the initializing thread calls it (`MPI_THREAD_FUNNELED`), and does not
  stop on a lower grant (its controlled_spec §3.3).
- **Where the threads run** is the launcher's binding, which the host does not set (no binding
  argument is added, issue #700 O9). Measured on Open MPI 4.1.2 with 48 CPUs, one hardware thread
  per core (2026-11-20): at up to 2 ranks it binds each rank to one core, so a rank's threads
  share that core and the run is correct and no faster than one thread; at more ranks it binds
  each to a socket. The launcher's own environment variables change it without an argument
  (on Open MPI 4.x `OMPI_MCA_hwloc_base_binding_policy=none`, which leaves each rank unbound, or
  `OMPI_MCA_rmaps_base_mapping_policy=slot:PE=<threads>`, which gives each rank that many cores;
  both measured on 4.1.2 with `--report-bindings`, and the binding variable is named
  `hwmap_base_binding_policy` from Open MPI 5 on, which 4.1.2 ignores); export them in
  the host's environment, or a site's `setup` lines, as a performance choice. It is a
  recommendation, not a rule: the result does not depend on it.

## 2. What is recorded

| record | value |
|---|---|
| `trial_meta.json#environment.launch.argv_prefix` | `["mpirun", "-n", "<ranks>"]`, as `host_execution.launch_argv_prefix` composes it for every run of the target |
| `trial_meta.json#environment.ranks` | the profile's `execution.ranks` |
| `trial_meta.json#environment.platform.parallel_runtime` | the first line of `mpirun --version`, `null` when it cannot be read; recorded, not interpreted |
| `perf.json#parallelism.mpi_ranks` | the process count the runner writes; the quality check requires it to equal `execution.ranks` (`observed_mpi_ranks` below). It detects a launch that started independent one-process runs only when the runner writes the count the harness observes at run time, which the distributed runner a launcher target runs does (issue #316 PR-4) |
| `trial_meta.json#environment.launch.env` | `{"SPEC2SCIENCE_PROCESS_GRID": "p1,p2"}` when the profile imposes a grid, `{}` otherwise; on a combination with `openmp`, the OpenMP backend's two thread variables beside it |
| `perf.json#parallelism.threads_per_rank` | the team size the shared-memory runtime gives rank 0, which the harness's writer obtains itself (`1` in a build without the compiler's thread switch); on a profile some shape of which runs more than one thread the quality check requires each shape's count (`thread_match`, issue #700) |
| `perf.json#parallelism.process_grid` | `[p1, p2]`, the grid the harness arranged the ranks in; the quality check requires the profile's `execution.process_grid`, or `[execution.ranks, 1]` when it imposes none (issue #512; `observed_process_grid` below). The harness writes it from the grid it established; on the harness's own Validate run the post-execute gate requires the self-test snapshots' `p1`, `p2` (the grid `__grid_dims` reports) to be the launch grid, so a perf record that echoes the imposed grid while the harness arranged its ranks another way is refused (`validate_pipeline_semantics._validate_harness_reported_grid`) |
| `quality_check.json#comparison.reference.ranks` / `.candidate.ranks` | `execution.ranks` / `1`, as the conductor writes them |
| `quality_check.json#shapes[]` | one row per execution shape, the certified run's first: its `ranks` and `process_grid`, and `observed_mpi_ranks` / `observed_process_grid`, what its harness wrote in `perf.json`; the conductor writes one row per shape of the profile and fails the quality check, and so the run, when an observation is not its shape's (`Conductor._author_quality_check`, issue #512). Last, the bounds-checked binary's `checked` row when the compiler declares checked flags (issue #589): started bare, without `mpirun`, as one process under `{"SPEC2SCIENCE_PROCESS_GRID": "1,1"}`, so its harness reports one rank in `[1, 1]` (`docs/workflow/phases/phase_04_validate.md` §4-2) |
| `trial_meta.json#environment.sweep[]` | each sweep shape's launch: `["mpirun", "-n", "<p1 p2>"]` and `{"SPEC2SCIENCE_PROCESS_GRID": "p1,p2"}` |
| build key `toolchain.compiler_wrapper` / `toolchain.parallel_runtime` / `toolchain.compiler_version` | `mpif90`; the first line of `mpif90 -show` on the host that runs `Generate.gate`, the runtime installation a binary built at `local` links against (at a remote site, §4); and the compiler's version asked as `mpif90 --version`, which the wrapper passes to the compiler it is configured with. A change of installation or of that compiler is another build. The syntax stage's recorded `compiler_version` is asked the same way |

## 3. Requirements on the host

- `mpif90` and `mpirun` resolve on `PATH`, from ONE installation. A launcher of another
  installation starts the binary's processes without the runtime the binary was linked
  against, and each process runs alone as rank 0 of 1 with exit code 0 (measured 2026-09-27 in
  both directions between Intel MPI 2021.10 and Open MPI 4.1.2, `-n 2`). Where the two
  programs sit does not decide it — Debian's alternatives switch `mpirun` and the wrapper
  separately, both in `/usr/bin` — so the startup probe asks what the pair DOES: it builds
  `LAUNCH_CANARY_SOURCE` with `mpif90`, starts it with `mpirun -n 2`, and refuses the run
  (`parallel_toolchain_unusable`) unless both processes report a run of 2
  (`host_prerequisites.parallel_toolchain_problems`). Asked only of a run that starts the
  binary under the launcher with more than one rank — one that reaches `Validate`, or drives a
  dependency there (`--with-deps`, or the `--resume` of such a run, unless it stops at
  `Compile`), for a profile whose
  `execution.ranks` is above 1 — and when `mpirun` resolves: a one-rank run is one process
  whichever installation starts it. A launch that does not complete (too few slots, a user
  the launcher will not run as, a hang) is refused with the head of the launcher's own
  message, not as a pairing problem; for too few slots, give the launcher slots for
  `execution.ranks` processes (its host file) or lower `execution.ranks`.
- The wrapper compiles a `use mpi_f08` source with the target's compiler. The harness uses the
  `mpi_f08` binding. An installation whose `mpi_f08` module is built for another compiler cannot
  build it: Intel MPI 2021.10 ships its `mpi_f08` module for the Intel compilers only, and its
  gfortran module directory holds `mpi.mod` without `mpi_f08.mod` (measured 2026-09-27). The
  startup probe compiles `BINDING_CANARY_SOURCE` syntax-only with the resolved wrapper and
  refuses the run when it fails (`parallel_toolchain_unusable`), before anything is billed. Open
  MPI 4.1.2 built for gfortran passes it. The binding canary is asked first; the launch canary
  is written in the same binding.
- `mpirun` is asked only of a run that executes the binary: the local site's launch probe
  (`missing_required_site_tools`, `host_execution.execution_executables`). What a remote site
  is asked is §4's.
- At the local site, the CPUs this process may run on (its affinity mask) are at least `execution.ranks`
  (`site_unfit_for_ranks`). The mask counts logical CPUs. A launcher may count its slots
  differently (Open MPI's default is one slot per physical core), so on a host with more than
  one hardware thread per core a count that passes this check can still be refused by the
  launcher at `Validate.execute`. Not measured: the host this was written on has one thread per
  core.

## 4. Sites

A target whose parallel backend declares `launcher` runs at any site that executes its class
(issue #337; issue #316 had held it to `local` until issue #700 PR-3). The binary is built at
the site that runs it, with the site's wrapper (issue #333).

**What a remote site must provide.**

- `mpif90` and `mpirun` of ONE installation, on the PATH the site's `setup` lines give the job,
  whose `mpi_f08` binding is built for the target's compiler (§3: the same requirements as the
  host's). The wrapper must run the language's default compiler: the wrapper runs one compiler,
  and a second compiler of a language is not built (item 6 Part 2, held in `TODO.md`). A `none`
  site's login is asked for the wrapper at launch for a run that reaches `Build`, and for the
  launcher too when the run reaches `Validate` (`missing_required_site_tools`); a run that stops
  at `Build` meets a missing launcher in its `Build` job's canary instead (below), and `--resume`
  reuses its certified `Compile` and `Generate`. A batch site's job asks for both where it
  runs.
- Slots for `execution.ranks` processes. At a `none` site the launcher counts the machine's own
  cores. At a batch site it counts the allocation's tasks, which the site's
  `scheduler_directives` state (`docs/backends/scheduler/slurm/JOB_SUBMIT.md` §2: with no task
  option `mpirun -n 4` is refused inside a Slurm job). The threads of a rank are the CPUs per
  task the directives give it. No CPU count is asked of a remote site: the launcher is what
  decides whether the ranks fit.
- The job directory visible at one path from every node the launcher starts a rank on.

**The launch canary in the `Build` job.** For a profile of more than one rank every `Build` job
at a remote site starts with three commands, ahead of the build (`workflow_conductor.
remote_launch_canary`): `LAUNCH_CANARY_SOURCE` built with `mpif90`
(`LAUNCH_CANARY_BUILD_ARGV`); the program started under `mpirun -n <execution.ranks>`, which
must report one run of that many processes; and the program started bare, which must report
one run of one — how `Validate.execute` starts the serial reference and the checked row (a
runtime that does not start a bare program inside an allocation fails them). A canary that does
not build, does not complete, or reports another run fails the `Build` as the site's failure
(`deterministic_build_error`, a transport fail_closed, not routed to `Generate`), before the
build's result is read. The failure's message carries the tail of what the canary printed,
and is kept in `workspace/orchestrations/<orchestration_id>/agents/<agent_run_id>/dialogs/deterministic.stderr.log`; the canary's log entries,
`workspace/orchestrations/<orchestration_id>/launch_canary.jsonl`, hold each command's argv
as the site ran it (`site.remote_command`) and its exit code, not its output. It covers the first `Build` at
a site: a `Validate` that passes through no `Build` there (`--rederive validate`, or a
`Validate` whose key moved under a certified `Build`) starts the launcher with no canary
before it.

**What is not asked or recorded at a remote site.** The runtime's identity: `mpirun
--version` is recorded as `platform.parallel_runtime` at `local` only (issue #700 O15: no gate,
key or verdict reads it). The site's compiler version is recorded in `binary_meta.json`, not
keyed (`docs/ORCHESTRATION.md` §Execution sites). The build key's `parallel_runtime` (§2) is
this host's `mpif90 -show`, as its `compiler_version` is this host's: at a remote site it does
not name the installation the binary links against, and a change of the site's installation
moves no key.

**Verified on one node.** Open MPI 4.1.2 at a `none` site reached over ssh, and under Slurm
20.02.5 with no task plugin, on one node. Over several nodes the launcher places the ranks over
the allocation, and nothing more is built; what is not measured is whether the two variables
the host sets in the launcher's environment — `SPEC2SCIENCE_PROCESS_GRID` and the thread
variables — reach a rank on another node. Every rank's `__init` reads the grid from its own
environment, and only rank 0's grid and team size reach `perf.json`, so `launch_match` and
`thread_match` hold on rank 0's word.

## 5. Out of scope

- The presence floor of `mpi` in `Generate.static` (`parallel_directives`), and the
  distributed-state harness a domain-decomposed kernel calls. Both belong to later stages of
  issue #316.
