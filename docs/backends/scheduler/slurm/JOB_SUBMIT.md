# Job submission — `slurm`

> **Audience: the operator who maps a target to a Slurm site, and the maintainer of the remote
> executor.** No leaf reads this document. It is the `slurm` binding of the `job_submit`
> capability; the executor, the site configuration and every refusal are
> `docs/ORCHESTRATION.md` §Execution sites. The code is
> `tools/backends/scheduler/slurm/submit.py` (issue #293 PR-4; batch submission, issue #696).

## 1. How a job runs

A job is ONE Slurm batch job. The executor submits it, polls it and collects it:

```
ssh <options> -- <host> 'echo; printf ... spec2science-submitted <epoch> && exec sbatch --parsable --time=<minutes> <directives> --job-name=spec2science-<agent_run_id> --output=<job_dir>/ctl/job.out --error=<job_dir>/ctl/job.err --no-requeue'   # the stub on stdin
ssh <options> -- <host> 'echo; scontrol show job -o <id> </dev/null; printf ...'           # one per poll
```

- **The script.** `sbatch` reads the batch script on its standard input. What the executor hands
  it is a two-line stub: `#!/bin/sh`, then `exec sh -c '<the job script>'`. Slurm keeps the batch
  script as a file the job's own user can write (`<SlurmdSpoolDir>/job<id>/slurm_script`); the
  stub's shell reads its one command whole before running it, and after `exec` the job script is
  an argument held in memory, so a command that rewrites the kept file changes nothing that runs.
- **The options.** `sbatch` takes the LAST of a repeated option, so the order is the policy.
  `--parsable` and `--time` come first: the time limit is the job's commands' bounds plus the
  executor's grace (and the device probe's and toolchain probe's bounds when the request carries
  them), rounded up to whole minutes — 96 or 97 minutes with the build-runtime library's default
  command timeouts — and a directive may replace it. A site whose partition, QOS or association
  allows less states its maximum there (`"--time=30"`); a job that reaches the shorter limit ends
  `TIMEOUT` and is refused, never recorded. `scheduler_directives` come next, each split on
  whitespace into words (`"-p gpu"` is two words), and the executor's remaining options come
  last: the job's name, its two files and `--no-requeue`. A directive that repeats one of them
  loses to it. A directive whose LAST word is an option that takes a separate value (`"--account"`
  with the value missing, or `"-J"`) swallows the next word — `--job-name=…` — as that value;
  write a value-taking option as `--name=value` or with its value in the same directive.
- **The two files.** The script's standard output is `<job_dir>/ctl/job.out`, where each
  command's status line is; its standard error, where a site's `setup` lines print and the job
  script names a failure outside its commands, is `<job_dir>/ctl/job.err`.
- **The id** is the first `;`-separated field of `sbatch --parsable`'s line.
- **The state.** One command, `scontrol show job -o <id>`, polled 2 seconds after the submission
  and then at an interval that doubles to 60 seconds. The answer's `JobState` and `ExitCode`
  (`<exit>:<signal>`) are read as below. A poll that fails, or that names a state the backend does
  not list, decides nothing and is asked again; an id `scontrol` does not know is refused.
- **What is accepted.** The job script exits 0 when every command exited 0 and 1 when a command
  ran and exited non-zero. The executor reads the statuses in `job.out` as "every command
  exited 0" only under `COMPLETED` with `ExitCode=0:0`, and as "this command failed" only under
  `FAILED` with `ExitCode=1:0`; every other terminal record is refused with the tail of `job.err`.
  A command's own process can rewrite `job.out`, and cannot make the shell that started it exit
  with a status of its choice: killing it leaves a signal in the record.

| `JobState` | read as | measured |
|---|---|---|
| `PENDING` | queued | yes (`--begin`, `Reason=BeginTime`) |
| `REQUEUED`, `REQUEUE_FED`, `REQUEUE_HOLD`, `RESV_DEL_HOLD`, `SPECIAL_EXIT` | queued | no, from `man squeue` |
| `RUNNING` | running | yes |
| `COMPLETING` | running: the job's files may still be written | yes (before `CANCELLED` and `TIMEOUT`) |
| `CONFIGURING`, `SIGNALING`, `STAGE_OUT`, `STOPPED`, `SUSPENDED`, `RESIZING` | running | no, from `man squeue` |
| `COMPLETED` with `0:0` | ended, the script's own exit 0 | yes |
| `FAILED` with `1:0` | ended, the script's own exit 1 | yes |
| `FAILED` with `3:0`, `0:9`, `0:15` | ended otherwise | yes (exit 3; a command KILLed, TERMed the script's shell) |
| `CANCELLED` with `0:15`, `TIMEOUT` with `0:15` | ended otherwise | yes (`scancel`; past `--time`) |
| `COMPLETED` or `FAILED` with any other `ExitCode`; `BOOT_FAIL`, `DEADLINE`, `NODE_FAIL`, `OUT_OF_MEMORY`, `PREEMPTED`, `REVOKED` | ended otherwise | no, from `man squeue` / `man scontrol` |
| any terminal state when `Restarts` is not `0`, is missing or is named twice | ended otherwise: the script ran more than once | yes (`COMPLETED 0:0` with `Restarts=1`, a job that requeued itself) |
| a state not above, or a field named twice (a job may rename itself) | no answer | — |

Measured on Slurm 20.02.5, a one-node cluster (issue #696, M-S1 to M-S6;
`tools/tests/data/scheduler_slurm/measure_slurm.py`, whose recorded answers are the fixtures of
`tools/tests/test_scheduler_slurm.py`): `sbatch --parsable` with the script on standard input
prints `<id>` and returns in about 10 ms, before the job starts; the states and exit codes in the
table's measured rows; a script whose shell a command killed is never `COMPLETED 0:0`, whether or
not the command rewrote `job.out` first; an id ended more than `MinJobAge` (300 s there) ago is
answered exactly as one never issued — exit status 1, nothing on stdout,
`slurm_load_jobs error: Invalid job id specified` on stderr; the kept script is mode 0700 and the
job's user rewrote it from inside an unstubbed job (`rewrote rc=0`), and under the stub the shell
held no descriptor of it; `/bin/sh` there is dash, which read the small unstubbed script whole, so
that rewrite did not change what ran either — under bash an offset-preserving rewrite of an
unstubbed script does, and of the stub's file does not (`tools/tests/test_remote_execution.py`
`StubTests`, over shims, not on Slurm); `job.out` and `job.err` were whole at the first terminal state (there
the job writes where the host reads, so this shows nothing about a site where they differ);
under `--no-requeue` a plain `scontrol requeue` of the job's own id is refused (`Requested
operation is presently disabled`), also with `--requeue` before it, and without the option the
same job was requeued twice, but a job whose command first turns the flag back on (`scontrol
update JobId=$SLURM_JOB_ID Requeue=1`, which the job's user may do) and then requeues itself ran
twice and ended `COMPLETED 0:0` with `Restarts=1`, its `job.out` holding the second pass alone;
and a `setup` line printed to stdout without a final newline glues onto the first
marked line of `job.out`, to stderr it stays in `job.err` (the executor sends `setup` output to
stderr).

## 2. What the site must provide

A site entry in `./sites.yaml` (the shape is `docs/ORCHESTRATION.md` §Execution sites):

```yaml
  cluster_cpu:
    host: login-node
    workdir: /work/spec2science-jobs
    executes: [cpu]
    scheduler: slurm
    scheduler_directives: ["--partition=compute", "--time=120"]
    queue_timeout_sec: 7200
  cluster_gpu:
    host: login-node
    workdir: /work/spec2science-jobs
    executes: [gpu]
    scheduler: slurm
    scheduler_directives: ["--partition=gpu", "--gres=gpu:1", "--time=120"]
    queue_timeout_sec: 7200
```

A site's `executes` is a claim about the machines its directives select. Nothing checks it: a
`gpu` job allocated no device runs on whatever node the directives give, its device probe
records `null`, and the runner's failure is recorded as the kernel's — a correct kernel fails
Validate. So a class whose device must be requested gets a site of its own whose directives
request it (`--gres`), and `targets:` maps each target to the site for its class. A `--time`
directive shorter than the job's bound (96-97 minutes with the build-runtime library's defaults) makes a command
that runs long a refusal rather than the command's own timeout.

- `sbatch` and `scontrol` on the PATH of a NON-INTERACTIVE login. Where they are added by an
  interactive login's startup files only, the launch probe refuses `missing_required_site_tools`
  naming them. They run on the login, outside the job, so a `setup` line does not reach them.
- Permission to submit batch jobs from the login the site's `host` reaches.
- The site's `workdir` at the same path on the machines Slurm runs the job on (a shared
  filesystem), and with no `%` in it: `sbatch` expands `%` in the two files' names. The launch
  probe asks the login only; the job script checks its programs again, on the machine it runs on,
  before its first command, and for a `Validate` job that machine's architecture against the one
  the binary was built on.
- **A retention longer than 60 seconds.** The executor reads how a job ended from `scontrol`,
  which states an ended job for `MinJobAge` seconds. A poll comes within one interval of the
  end — at most 60 seconds — so a site that forgets an ended job sooner than that refuses the
  jobs that ended between two polls (`the scheduler no longer knows job <id>`). A longer
  retention buys more: once a resumed run adopts a batch job (issue #696; not built yet), a job
  that ended while no run was waiting is adoptable for as long as the scheduler states it.
- `queue_timeout_sec` is how long one run waits for the job to start. Past it the run is refused
  as a transport failure and the job is LEFT queued: nothing cancels it. `scancel` it by hand
  (its name is `spec2science-<agent_run_id>`) when it is not wanted.

## 3. What it does not do

- **A job is not cancelled when the host stops waiting** — the queue bound, the bound on a
  running job, an interrupted run. It runs on until it ends or reaches the `--time` in force —
  the submit command's, or a directive's, which may be longer. Until a resumed run adopts such a
  job (issue #696; not built yet) a later attempt submits a job of its own in a directory of its own.
- **No requeue.** A requeued batch job runs its script again from the beginning, in a directory
  holding the first pass's files, and truncates its output file, so two passes would be recorded
  as one run. `--no-requeue` is the executor's own option, last: it stops the scheduler's own
  requeue and a plain `scontrol requeue` of the job. It does not stop the job's own commands from
  setting the flag back (`scontrol update JobId=<id> Requeue=1`) and then requeueing the job, so
  the backend reads an own exit only from a record with `Restarts=0`; a job that ran its script
  more than once is refused as ended otherwise.
- **No accounting.** Nothing reads `sacct`: it did not answer on the machine measured, so no
  output of it is recorded, and an ended job is stated for the scheduler's own retention alone.
  It enters as a second state command, with a recorded fixture, from the first site whose
  accounting answers.
- **Not measured: a limit Slurm enforces on the job beyond time.** A job ended by a memory limit
  is `OUT_OF_MEMORY`, or `FAILED` with a signal, and is refused as a host-side failure rather than
  recorded as the runner's runtime error. The cluster measured runs no task plugin that enforces
  memory.
- **One task.** The batch script runs once, on the first node of the allocation, and the
  executor asks for no task count. The `parallel` axis' `launcher` capability exists since issue
  #316, and a target whose parallel backend declares it runs at the `local` site only
  (`docs/backends/parallel/mpi/LAUNCHER.md` §4): nothing sizes or checks the allocation for the
  ranks. Placing a launcher at a batch site is outside issue #316. A `Build` job (issue #333) is
  the same one task: the build system's own parallelism runs inside it.
