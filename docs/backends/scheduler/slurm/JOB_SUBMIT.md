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
  `--parsable` and `--time` come first: the time limit is the sum, over the job's commands, of
  each command's bound plus its 30 s kill grace, plus the executor's 300 s grace, plus the
  device probe's 60 s and two of the toolchain probe's 60 s when the request carries them,
  rounded up to whole minutes (`remote_execution._wall_clock_sec`) — and a directive may replace
  it. With the build-runtime library's default command timeouts (1800 s to build or run the
  quality check, 3600 s to run a program), the shapes the conductor submits come to: a `Build`
  job 38 minutes, 68 with the checked build; a `Validate.execute` job 96 (97 with the device
  probe) for the run and the quality check, and 3630 s more for each further program it runs —
  the device trace's summary, each sweep shape, the checked binary — so 157 or 158 minutes with
  one of them, 217 or 218 with two; and a restart job (the continuation cases' second pass)
  1830 s per quality check and 3630 s per run on top of 300 s (360 s with the device probe),
  66 or 67 minutes for one run. A site whose partition, QOS or association
  allows less states its maximum there (`"--time=30"`); a job that reaches the shorter limit ends
  `TIMEOUT` and is refused, never recorded. `scheduler_directives` come next, each split on
  whitespace into words (`"-p gpu"` is two words; quotes are not honoured, unlike on a
  `#SBATCH` line — measured, `#SBATCH --comment="a b"` gives `Comment=a b` while the directive
  `--comment="a b"` is the two words `--comment="a` and `b"` — so a directive cannot carry a value
  that contains whitespace), and the executor's remaining options come
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
  exited 0" only under `COMPLETED` with `ExitCode=0:0`, and a status that shows a failed command
  as that failure under `COMPLETED 0:0` or `FAILED 1:0` (a forged failure gains a leaf nothing);
  every other terminal record is refused with the tail of `job.err`.
  A command's own process can rewrite `job.out`; killing the shell that started it leaves a
  signal in the record. It can make that shell exit with a status of its choice only by
  deliberately attaching to it with a debugger (ptrace, where the site allows it), which a
  `none` site's pipe channel is equally open to: a deliberate attack with no ordinary spelling,
  outside what the executor defends. What is enforced is that a file showing no failed command
  is accepted only under `COMPLETED 0:0`. Under `FAILED 1:0`, which command failed and which
  ran are read from `job.out` as it stands: a leftover process can rewrite it to name another
  command as the failed one, and commands that did not run as having exited 0. Not defended:
  every command's failure fails the attempt, so no verdict reads one as a pass.

| `JobState` | read as | measured |
|---|---|---|
| `PENDING` | queued | yes (`--begin`, `Reason=BeginTime`) |
| `CONFIGURING` | queued: the job's resources are allocated and not yet ready (a node booting), so its script has not started and the run bound does not count | no, from `man squeue` |
| `REQUEUED`, `REQUEUE_FED`, `REQUEUE_HOLD`, `RESV_DEL_HOLD`, `SPECIAL_EXIT` | queued | no, from `man squeue` |
| `RUNNING` | running | yes |
| `COMPLETING` | running: the job's files may still be written | yes (before `CANCELLED` and `TIMEOUT`) |
| `STOPPED` | running: its time limit advances while it is stopped | yes (the job's own `scancel --signal=STOP $SLURM_JOB_ID`: stopped at 5 s, `TIMEOUT 0:15` at 75 s with `RunTime=00:01:10`, job 1693) |
| `SIGNALING`, `STAGE_OUT`, `RESIZING` | running | no, from `man squeue` |
| `SUSPENDED` | running: held to the same run bound (§3) | no, from `man squeue` (no preemption on the cluster measured; the job's own `scontrol suspend` of its id is refused, `Access/permission denied`) |
| `COMPLETED` with `0:0` | ended, the script's own exit 0 | yes |
| `FAILED` with `1:0` | ended, the script's own exit 1 | yes |
| `FAILED` with `3:0`, `0:9`, `0:15` | ended otherwise | yes (exit 3; a command KILLed, TERMed the script's shell) |
| `CANCELLED` with `0:15`, `TIMEOUT` with `0:15` | ended otherwise | yes (`scancel`; past `--time`) |
| `COMPLETED` or `FAILED` with any other `ExitCode`; `BOOT_FAIL`, `DEADLINE`, `NODE_FAIL`, `OUT_OF_MEMORY`, `PREEMPTED`, `REVOKED` | ended otherwise | no, from `man squeue` / `man scontrol` |
| any terminal state when `Restarts` is not `0`, is missing or is named twice | ended otherwise: the script ran more than once | yes (`COMPLETED 0:0` with `Restarts=1`, a job that requeued itself) |
| a state not above, or a field named twice (a job may rename itself) | no answer | — |

Measured on Slurm 20.02.5, a one-node cluster (issue #696, M-S1 to M-S6;
`tools/tests/data/scheduler_slurm/measure_slurm.py`, whose recorded answers are the fixtures of
`tools/tests/test_scheduler_slurm.py`; the records came from an earlier version of its `raw`, a
separate short run for M-S3 and a run of `self_requeue` alone, which its docstring lists, and
`fixture` regenerates the answers byte-identically from them): `sbatch --parsable` with the script on standard input
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
stderr). The script's `executor` runs the executor itself against that Slurm (ssh and scp
shimmed): a script exiting 0 and one whose command failed are accepted as such, an exit of 3, a
job past its `--time` and a cancelled one are refused, and a seeded leftover process that
rewrites `job.out` to all zeros is never read as success — but on that cluster, which tracks a
job's processes by cgroup (`proctrack/cgroup`), the job's end kills the leftover, racing its
rewrite: in the runs recorded the kill came first (`rewrite_landed: false`, alone and after
killing the script's shell, jobs 1690 and 1691), and in one earlier run (job 1642) the
leftover's truncation of `job.out` landed and the job was refused; so what refuses a rewritten
file under a failed record (§1, what is accepted) is exercised by the suite's shim rows
(`tools/tests/test_remote_execution.py` `BindingTests`), not on Slurm.

## 2. What the site must provide

A site entry in `./sites.yaml` (the shape is `docs/ORCHESTRATION.md` §Execution sites):

```yaml
  cluster_cpu:
    host: login-node
    workdir: /work/spec2science-jobs
    executes: [cpu]
    scheduler: slurm
    scheduler_directives: ["--partition=compute"]
    queue_timeout_sec: 7200
  cluster_gpu:
    host: login-node
    workdir: /work/spec2science-jobs
    executes: [gpu]
    scheduler: slurm
    scheduler_directives: ["--partition=gpu", "--gres=gpu:1", "--time=120"]   # the partition's maximum
    queue_timeout_sec: 7200
```

A site's `executes` is a claim about the machines its directives select. Nothing checks it: a
`gpu` job allocated no device runs on whatever node the directives give, its device probe
records `null`, and the runner's failure is recorded as the kernel's — a correct kernel fails
Validate. So a class whose device must be requested gets a site of its own whose directives
request it (`--gres`), and `targets:` maps each target to the site for its class. A `--time`
directive replaces the job's own limit for every job of the site (§1 gives the limits, from 38
minutes to over 200), so state one only as the partition's maximum: below a job's own limit it
makes a command that runs long a refusal rather than the command's own timeout.

**A launcher target's allocation** (issue #337). A target whose parallel backend starts its
binary under a launcher (`docs/backends/parallel/mpi/LAUNCHER.md`) runs that launcher inside
the batch script, and the launcher starts its ranks within the allocation the directives give.
So the site's directives state the task count and the CPUs per task, as they state a partition
or a device:

```yaml
  cluster_mpi:
    host: login-node
    workdir: /work/spec2science-jobs
    executes: [cpu]
    scheduler: slurm
    scheduler_directives: ["--partition=compute", "--ntasks=4", "--cpus-per-task=3"]
```

`--ntasks` is the profile's `execution.ranks`, and `--cpus-per-task` its
`execution.threads_per_rank` (the largest of its thread sweep). Measured on Slurm 20.02.5 with
Open MPI 4.1.2 on one node (issue #700, 2026-11-20): with no task option the job holds one CPU
and `mpirun -n 4` is refused, exit 1, "not enough slots"; with `--ntasks=4` four ranks run, and
with `--ntasks=4 --cpus-per-task=4` the job's record holds 16 CPUs; `--ntasks=1
--cpus-per-task=16` is refused as with no option. A program started without the launcher inside
the job runs as one process, which is how the quality check's serial reference and the checked
row start. The directives size every job of the site, the `Build` job included, and the `Build`
job of a launcher target of more than one rank starts with the backend's launch canary, started
under the launcher with `execution.ranks` and bare (`docs/ORCHESTRATION.md` §Execution sites):
a site whose directives give too few tasks fails that `Build` as the site's failure, before any
`Generate` repair. A site serves the shapes its directives allocate, so targets of different
shapes take site entries of their own (two entries may name one host). The scheduler backend
does not derive the options from the profile (issue #700 O7); that is reopened when one site
must serve launcher targets of different shapes.

- `sbatch` and `scontrol` on the PATH of a NON-INTERACTIVE login. Where they are added by an
  interactive login's startup files only, the launch probe refuses `missing_required_site_tools`
  naming them. They run on the login, outside the job, so a `setup` line does not reach them.
- Permission to submit batch jobs from the login the site's `host` reaches.
- The site's `workdir` at the same path on the machines Slurm runs the job on (a shared
  filesystem), and with no `%` in it: `sbatch` expands `%` in the two files' names. The launch
  probe asks the login only; the job script checks its programs again, on the machine it runs on,
  before its first command, and for a `Validate` job that machine's architecture against the one
  the binary was built on.
- **A retention of several minutes.** The executor reads how a job ended from `scontrol`, which
  states an ended job for `MinJobAge` seconds; a job forgotten before a poll reads it is refused
  (`the scheduler no longer knows job <id>`). The first poll after the end comes within one
  interval of it, at most 60 s (`remote_execution.POLL_MAX_SEC`), and each poll that answers
  nothing adds one more interval, plus the time t the poll took to fail: near 0 for a refused
  connection, up to 30 s for a host that does not answer (`ConnectTimeout=30`,
  `remote_execution.SSH_OPTIONS`), up to the transport bound of 300 s (`TRANSPORT_GRACE_SEC`)
  for one that hangs. So `MinJobAge` must be at least (1 + k) × 60 s + k × t to survive k
  unanswered polls. The 300 s of the machine measured is exactly (1 + 4) × 60 s, four refused
  polls that took no time at all, so it is read as covering 3 refused at once (240 s, a 60 s
  margin), 2 that reach the connect timeout (240 s, the same margin), and none that hangs, which
  needs 420 s for one. A longer retention buys more: a resumed run adopts a batch job
  (`docs/ORCHESTRATION.md` §Execution sites), and a job that ended while no run was waiting is
  adoptable for as long as the scheduler states it.
- `queue_timeout_sec` is how long one run waits for the job to start. Past it the run is refused
  as a transport failure and the job is LEFT as it is: nothing cancels it, and `--resume` adopts
  it. To abandon it, `scancel` it by hand (its name is `spec2science-<agent_run_id>`, and a
  `Validate.execute` restart job's — the continuation cases' second pass —
  `spec2science-<agent_run_id>.restart`) and remove its `site job record` (§3): a cancelled job
  whose record stays is adopted by the next `--resume`, which is refused, and only the resume
  after it submits a job of its own.

## 3. What it does not do

- **The host cancels no job.** A job the host stops waiting for — past the queue bound or the
  bound on a running job, or in an interrupted run — runs on until it ends or reaches the
  `--time` in force, the submit command's or a directive's, which may be longer. A resumed run
  adopts it when it would submit the same job (`docs/ORCHESTRATION.md` §Execution sites); a job
  it does not adopt is left behind, and the event `site_job_not_adopted` names its id and its
  directory. A directive changed after a queue timeout (a `--partition`, for example) changes
  the request, so the resumed run does not adopt the queued job and it is left behind. Cancel
  one by hand with `scancel <job_id>` (or `scancel --name=<its name>`, §2) and remove its
  directory at the site; cancel a job whose `site job record` you remove to abandon it the
  same way. How long an ended job stays adoptable is `MinJobAge` (§2).
- **A suspended job's time is spent against the run bound.** `SUSPENDED` (preemption by
  suspension, an administrator's `scontrol suspend`) is read as running, so the host's run bound
  — the job's own time limit plus its grace — keeps advancing while Slurm's own limit does not.
  At a site that preempts by suspension, a job suspended for long enough is refused at the run
  bound and left as it is with its `site job record` kept, and `--resume` adopts it and waits
  for it again under a run bound of its own (`docs/ORCHESTRATION.md` §Execution sites). The
  alternative, not counting a suspended interval, would wait without bound on a job that stays
  suspended.
- **No requeue.** A requeued batch job runs its script again from the beginning, in a directory
  holding the first pass's files, and truncates its output file, so two passes would be recorded
  as one run. `--no-requeue` is the executor's own option, last: it stops the scheduler's own
  requeue and a plain `scontrol requeue` of the job. It does not stop the job's own commands from
  setting the flag back (`scontrol update JobId=<id> Requeue=1`) and then requeueing the job, so
  the backend reads an own exit only from a record with `Restarts=0`; a job that ran its script
  more than once is refused as ended otherwise. `Restarts` counts the site's own requeues too
  (a launch or prolog failure, `RequeueExit`), so a job the site requeued on its own is refused
  the same way even when its script ran at most once; `--resume`, or a rerun, submits a new job.
- **One cluster.** A directive that selects another cluster (`-M`, `--clusters`) is not
  supported: the job would be submitted there, and every poll asks `scontrol` without `-M`,
  which answers for the local cluster alone.
- **No accounting.** Nothing reads `sacct`: it did not answer on the machine measured, so no
  output of it is recorded, and an ended job is stated for the scheduler's own retention alone.
  It enters as a second state command, with a recorded fixture, from the first site whose
  accounting answers.
- **Not measured: a limit Slurm enforces on the job beyond time.** A job ended by a memory limit
  is `OUT_OF_MEMORY`, or `FAILED` with a signal, and is refused as a host-side failure rather than
  recorded as the runner's runtime error. The cluster measured runs no task plugin that enforces
  memory.
- **No task layout of its own.** The batch script runs once, on the first node of the
  allocation, and the executor asks for no task count: a launcher inside the script starts its
  ranks over the allocation the operator's directives give (§2). Verified on one node through
  the workflow, and over two nodes outside it (`docs/backends/parallel/mpi/LAUNCHER.md` §4).
  A `Build` job (issue #333) runs in the same allocation: the build system's own parallelism
  runs inside it.
