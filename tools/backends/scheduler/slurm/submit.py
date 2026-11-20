#!/usr/bin/env python3
"""Slurm's `job_submit` (issues #293, #696): a job is submitted with `sbatch`, its condition is
asked with `scontrol`, and the record `scontrol` keeps of how the job ended decides whether the
statuses in the job's output file are read.

`tools/remote_execution.py` submits, polls and collects a job the same way for every batch
scheduler; this module spells the commands and reads their answers. Measured on Slurm 20.02.5
(issue #696, M-S1 to M-S6; `docs/backends/scheduler/slurm/JOB_SUBMIT.md` §1):

- `sbatch --parsable` reads the batch script on its standard input, prints `<id>` (or
  `<id>;<cluster>` on a multi-cluster site, from the manual) and returns before the job starts;
- `scontrol show job -o <id>` prints one line of `Key=Value` fields, among them `JobState` and
  `ExitCode=<exit>:<signal>`; an id it does not know (never issued, or ended more than
  `MinJobAge` ago) exits 1 with `slurm_load_jobs error: Invalid job id specified` on stderr and
  nothing on stdout;
- a script that exits 0 ends `COMPLETED 0:0`, one that exits 1 or 3 ends `FAILED 1:0` /
  `FAILED 3:0`, and one whose shell a command killed ends `FAILED 0:9` (KILL) or `FAILED 0:15`
  (TERM), never `COMPLETED 0:0`; a cancelled job and one past its time limit pass through
  `COMPLETING` and end `CANCELLED 0:15` / `TIMEOUT 0:15`;
- under `--no-requeue` a plain `scontrol requeue` of the job's own id is refused, also when a
  directive says `--requeue` before it (the last one wins); without it the same job was
  requeued. The option does not stop the job's own commands from turning the flag back on
  (`scontrol update JobId=<id> Requeue=1`) and then requeueing it: that job ran twice and ended
  `COMPLETED 0:0` with `Restarts=1`, so `job_state` accepts an own exit only at `Restarts=0`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

#: The programs a site's non-interactive login must resolve for a job of this scheduler: the
#: submit command and the state command (`host_prerequisites.required_site_executables` adds
#: them to the launch probe).
REMOTE_EXECUTABLES: tuple[str, ...] = ("sbatch", "scontrol")

#: `JobState` values `man squeue` documents (24 on Slurm 20.02), by what they mean for the
#: host. Every documented value is in exactly one set; a value in none is not an answer.
#: `CONFIGURING` is queued: the job "has been allocated resources, but are waiting for them to
#: become ready for use (e.g. booting)" (`man squeue`), so its script has not started. Breakage
#: were it counted as running: at a power-saving site the nodes' boot is spent against the run
#: bound (the script's time limit plus grace), and the host gives up on a healthy job.
QUEUED_STATES = frozenset({"PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_FED", "REQUEUE_HOLD",
                           "RESV_DEL_HOLD", "SPECIAL_EXIT"})
#: A job the scheduler is still tearing down (`COMPLETING`) has not ended: its files may still
#: be written (issue #696 review 1, M3).
RUNNING_STATES = frozenset({"RUNNING", "COMPLETING", "SIGNALING", "STAGE_OUT", "RESIZING"})
#: A job whose execution the scheduler has suspended (`SUSPENDED`: preemption by suspension, or
#: an administrator's `scontrol suspend`) or stopped with SIGSTOP (`STOPPED`), both from
#: `man squeue`: it has started, and a suspended job's time limit does not advance. Breakage
#: were they counted as running: at a site that preempts by suspension the host's wall-clock run
#: bound is spent while the job's own limit is not, and a healthy job is refused. Whether a
#: STOPPED job's limit advances the manual does not say; not counting it only lengthens the
#: host's wait, which the job's own limit still ends.
SUSPENDED_STATES = frozenset({"STOPPED", "SUSPENDED"})
ENDED_STATES = frozenset({"COMPLETED", "FAILED", "BOOT_FAIL", "CANCELLED", "DEADLINE",
                          "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED", "REVOKED", "TIMEOUT"})

#: The record of a script that ran to its own exit, by that exit: an ALLOWLIST of
#: (`JobState`, `ExitCode`) pairs. Every other terminal record — a signal in `ExitCode`, a limit,
#: a cancellation, any other exit status — is "ended otherwise". Success is never computed from
#: the exit part alone: a script whose shell was killed has exit part 0 (`FAILED 0:9`, measured).
OWN_EXITS: dict[tuple[str, str], int] = {("COMPLETED", "0:0"): 0, ("FAILED", "1:0"): 1}

#: What `scontrol` prints on stderr for an id it does not know (measured).
UNKNOWN_ID_TEXT = "Invalid job id specified"


def submit_argv(*, directives: Sequence[str], job_name: str, wall_clock_sec: int,
                output_path: str, error_path: str) -> tuple[str, ...]:
    """The command that submits the batch script it reads on its standard input and returns at
    once. `sbatch` takes the LAST of a repeated option (measured), so the order is the policy:

    1. `--parsable` and a time limit of `wall_clock_sec` rounded up to whole minutes, FIRST, so
       that a directive may replace the limit — a site whose partitions allow less than the
       job's full bound states its maximum there;
    2. the operator's `scheduler_directives`, each split on whitespace into words. Quotes are
       not honoured, unlike on a `#SBATCH` line (measured on Slurm 20.02.5: `#SBATCH
       --comment="a b"` gives `Comment=a b`, while here it is the two words `--comment="a` and
       `b"`), so a directive cannot carry a value that contains whitespace;
    3. the options a repeating directive does not override, LAST: the job's name, its two files
       and `--no-requeue`. A requeued batch job runs its script from the beginning in a
       directory holding the first pass's files and truncates its output file (this machine's
       `JobRequeue = 1`, `JobFileAppend = 0`), so the host would record two passes as one run:
       that breakage, introduced by submitting a batch job, is what the option answers.

    A directive whose last word is an option taking a separate value swallows the first of
    these as that value: not guarded, because a flag (`--exclusive`) and a value-taking option
    look alike here; JOB_SUBMIT.md tells the operator to write `--name=value`."""
    if wall_clock_sec < 1:
        raise ValueError("wall_clock_sec must be >= 1")
    minutes = -(-wall_clock_sec // 60)
    return ("sbatch", "--parsable", f"--time={minutes}",
            *(w for d in directives for w in d.split()),
            f"--job-name={job_name}", f"--output={output_path}", f"--error={error_path}",
            "--no-requeue")


def job_id(stdout: str) -> str | None:
    """The job id in what `submit_argv`'s command printed: the first `;`-separated field of the
    `--parsable` line, or None for an empty answer. The caller holds what this returns to one
    path element, so an answer of more than one line is refused there."""
    text = stdout.strip()
    return text.split(";", 1)[0] if text else None


def state_argvs(job_id: str) -> tuple[tuple[str, ...], ...]:
    """The commands that state the job's condition, tried in order until one answers. One:
    `sacct` does not answer on the machine measured, so no output of it is recorded, and it
    stays out until a site's is (issue #696, R-G)."""
    return (("scontrol", "show", "job", "-o", job_id),)


def _field(stdout: str, key: str) -> str | None:
    """The value of `key` when the answer carries exactly one field of that name. A job's own
    commands can rename the job (`scontrol update JobName=...`), and a name carrying
    `JobState=COMPLETED ExitCode=0:0` before the real fields would otherwise be read as the
    job's record: a leaf whose later command failed would be closer to a certified Validate
    (issue #696, §6.2's P1). A second field of either name is therefore no answer."""
    values = re.findall(rf"(?:^|\s){key}=(\S*)", stdout)
    return values[0] if len(values) == 1 else None


def job_state(returncode: int, stdout: str, stderr: str) -> tuple[str, int | str | None]:
    """What one state command's answer means, as one of eight answers: `("queued", None)`,
    `("running", None)`, `("suspended", None)`, `("ended", 0)` and `("ended", 1)` only for the records `OWN_EXITS`
    lists of a job whose script ran once (`Restarts=0`), `("ended", None)` for every other
    terminal record, `("unknown_id", None)` when
    `scontrol` says it does not know the id, and `("no_answer", <text>)` for anything else — a
    failure for another reason, a `JobState` this module does not list, a field missing or
    repeated."""
    if returncode != 0:
        if not stdout.strip() and UNKNOWN_ID_TEXT in stderr:
            return ("unknown_id", None)
        return ("no_answer", f"exit status {returncode}: {(stderr or stdout).strip()[-500:]}")
    state, exit_code = _field(stdout, "JobState"), _field(stdout, "ExitCode")
    if state is None or exit_code is None:
        return ("no_answer", f"not one JobState and one ExitCode field: {stdout.strip()[:500]}")
    if state in QUEUED_STATES:
        return ("queued", None)
    if state in RUNNING_STATES:
        return ("running", None)
    if state in SUSPENDED_STATES:
        return ("suspended", None)
    if state in ENDED_STATES:
        # A job ran its script more than once when `Restarts` is not 0: its own command can turn
        # its requeue flag back on and requeue itself under `--no-requeue` (`scontrol update
        # JobId=$SLURM_JOB_ID Requeue=1`, then `scontrol requeue`; measured, M-S5), and the
        # second pass ends `COMPLETED 0:0` in a directory holding the first pass's files with
        # the first pass's statuses truncated away. Breakage without this: two passes recorded
        # as one run (issue #696 §6.12), a first pass's failure gone from the record.
        if _field(stdout, "Restarts") != "0":
            return ("ended", None)
        return ("ended", OWN_EXITS.get((state, exit_code)))
    return ("no_answer", f"JobState {state!r} is not a state this backend lists")
