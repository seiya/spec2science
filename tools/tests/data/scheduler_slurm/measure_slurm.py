#!/usr/bin/env python3
"""Measurements of the `slurm` scheduler backend on a real Slurm (issue #696, O11(a)).

NOT a suite row: pytest collects `test_*.py` only, and a test does not skip
(`tools/tests/skip_probes.py`), so the rows that submit jobs to a real scheduler live here and are
run by hand. Every job is one CPU for at most two minutes, submitted one after another (they share
one node); on the machine this was written for nothing is billed.

    python3 tools/tests/data/scheduler_slurm/measure_slurm.py raw <out_dir>
    python3 tools/tests/data/scheduler_slurm/measure_slurm.py executor <out_dir>
    python3 tools/tests/data/scheduler_slurm/measure_slurm.py fixture <out_dir>/records.jsonl \\
        > tools/tests/data/scheduler_slurm/scontrol_answers.json

- `raw` is M-S1 to M-S6 of the issue's plan: `sbatch` with the script on standard input, every
  `scontrol show job -o` answer of a held, a running and an ended job (the script exits 0, 1 and
  3; a command KILLs or TERMs the script's shell; one rewrites the output file and then KILLs the
  shell; `scancel`; past `--time`), each id again after `MinJobAge`, where the scheduler keeps the
  script and whether the two-line stub survives a rewrite of it, `--no-requeue` against a requeue
  the job asks for (a plain one, and one after the job sets its own requeue flag back), and a `setup` line printed without a final newline. About 9 minutes, most of
  it the wait for `MinJobAge`.
- `executor` is V-c and V-e: `remote_execution.execute_job` with this checkout's code against the
  real `sbatch` and `scontrol`, ssh and scp SHIMMED (they run the command, and copy, on this
  machine under this process's `PATH`: a non-interactive ssh login here does not put Slurm's
  programs on its `PATH`). A script that exits 0 and one whose command exits non-zero are
  accepted as such; an exit of 3, a job past its time limit and a cancelled job are refused; and
  the two seeded rows of `BindingTests` (a leftover process rewrites the output file to all
  zeros, alone and after killing the script's shell) are never read as success. Each seeded row
  records `rewrite_landed`, whether the leftover's rewrite reached the output file (it leaves
  `job.out.rewritten` after it). On a site that tracks a job's processes by cgroup
  (`proctrack/cgroup`) the job's end kills the leftover before the rewrite lands — this
  machine, 2026-11-20, jobs 1690 and 1691: `false` in both, the first recorded as the command's
  failure, the second refused as ended otherwise — so there the rows show only that the failure
  is not read as success, and the binding itself is exercised by the suite's shim rows
  (`BindingTests`). About 4 minutes.
- `fixture` turns the `raw` records into the answers `tools/tests/test_scheduler_slurm.py`
  `JobStateTests` reads, with the operator's names and paths replaced (`_sanitize`).

Each record is one JSON line in `<out_dir>/records.jsonl`, written as it happens.

Provenance of the committed records (2026-11-20, Slurm 20.02.5, this repository's measurement
machine). This script's `raw` has NOT been re-run as a whole since it was revised. The records
`scontrol_answers.json` was built from came from two runs:

1. An earlier version of `raw` (jobs 1611 to 1627): the same cases but for M-S3, whose cases
   there were named `stub`, `self_rewrite_stub` and `self_rewrite_nostub` (here `stub_rewrite`
   and `nostub_rewrite`), and without `self_requeue`. Every answer in the fixture but the last
   is from this run; M-S3's own figures — the kept script's path and mode, `rewrote rc=0`, no
   descriptor under the stub — were re-measured in a separate short run of the same two
   scripts (jobs 1628 and 1629), and no fixture answer is from it.
2. `self_requeue` alone, called from a short driver after review 1 (job 1660): the last
   answer, `requeue_self_update (after_requeue_attempt)`.

`fixture <run 1>/records.jsonl <run 2>/records.jsonl` regenerates `scontrol_answers.json`
byte-identically from those two files (checked when the second was added); the files themselves
hold the operator's names and paths and are not committed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY",
            "BOOT_FAIL", "DEADLINE", "PREEMPTED", "REVOKED"}
START = 'printf "spec2science-start %s\\n" "$(date +%s)"; '


class Recorder:
    def __init__(self, out: Path) -> None:
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.path = out / "records.jsonl"

    def log(self, **kv) -> None:
        kv["t"] = round(time.time(), 3)
        with self.path.open("a") as f:
            f.write(json.dumps(kv) + "\n")
        print(json.dumps(kv)[:300], flush=True)


def _run(argv, stdin=None):
    p = subprocess.run(argv, input=stdin, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _stub(body: str) -> str:
    return "#!/bin/sh\nexec sh -c " + shlex.quote(body) + "\n"


# ------------------------------------------------------------------------------------------ raw

def _submit(rec: Recorder, case, body, extra=(), use_stub=True, no_requeue=True, time_min=2):
    d = rec.out / case
    d.mkdir(exist_ok=True)
    argv = ["sbatch", "--parsable", f"--time={time_min}", *extra, f"--job-name=ms-{case}",
            f"--output={d}/job.out", f"--error={d}/job.err"]
    if no_requeue:
        argv.append("--no-requeue")
    script = _stub(body) if use_stub else body
    t0 = time.time()
    rc, so, se = _run(argv, stdin=script)
    rec.log(case=case, what="sbatch", argv=argv, rc=rc, stdout=so, stderr=se,
            returned_after_s=round(time.time() - t0, 3), script=script)
    return so.strip().split(";")[0], d


def _show(rec: Recorder, case, jid, what="scontrol"):
    rc, so, se = _run(["scontrol", "show", "job", "-o", jid])
    fields = dict(tok.split("=", 1) for tok in so.split() if "=" in tok)
    rec.log(case=case, what=what, job_id=jid, rc=rc, stdout=so, stderr=se,
            JobState=fields.get("JobState"), ExitCode=fields.get("ExitCode"),
            Reason=fields.get("Reason"), Requeue=fields.get("Requeue"),
            Restarts=fields.get("Restarts"))
    return fields.get("JobState"), rc


def _wait_end(rec: Recorder, ended: list, case, jid, d, cap=400):
    t0 = time.time()
    while time.time() - t0 < cap:
        st, rc = _show(rec, case, jid, "poll")
        if st in TERMINAL or rc != 0:
            # M-S4: the two files at the first "ended".
            for name in ("job.out", "job.err"):
                p = d / name
                rec.log(case=case, what=f"file_at_end:{name}", exists=p.exists(),
                        content=p.read_text(errors="replace") if p.exists() else None)
            ended.append((case, jid))
            return st
        time.sleep(1)
    rec.log(case=case, what="wait_cap_reached")
    return None


def self_requeue(rec: Recorder, ended: list) -> None:
    """M-S5, second half (issue #696 review 1): under `--no-requeue` the job's own command turns
    its requeue flag back on (`scontrol update ... Requeue=1`) and then requeues itself. The
    second pass skips both (`SLURM_RESTART_COUNT`), so the job ends instead of looping."""
    case = "requeue_self_update"
    body = START + ('echo "pass restart=${SLURM_RESTART_COUNT:-0}"; '
                    'if [ "${SLURM_RESTART_COUNT:-0}" = 0 ]; then '
                    'scontrol update JobId=$SLURM_JOB_ID Requeue=1; echo "update rc=$?"; '
                    'scontrol requeue $SLURM_JOB_ID; echo "requeue rc=$?"; sleep 30; fi; '
                    'echo end')
    jid, d = _submit(rec, case, body)
    _wait_end(rec, ended, case, jid, d, cap=400)
    time.sleep(5)
    _show(rec, case, jid, "after_requeue_attempt")


def raw(rec: Recorder) -> None:
    out = rec.out
    ended: list[tuple[str, str]] = []

    def submit(*a, **k):
        return _submit(rec, *a, **k)

    def show(*a, **k):
        return _show(rec, *a, **k)

    def wait_end(*a, **k):
        return _wait_end(rec, ended, *a, **k)

    # M-S1, M-S2: held, running
    jid, d = submit("held", START + "echo hi", extra=["--begin=now+40"])
    show("held", jid, "after_submit")
    wait_end("held", jid, d, cap=200)
    jid, d = submit("running", START + "sleep 8; echo done")
    time.sleep(3)
    show("running", jid, "running")
    wait_end("running", jid, d)
    # M-S2: the script's own exits
    for code in (0, 1, 3):
        jid, d = submit(f"exit{code}", START + f"echo cmd; exit {code}")
        wait_end(f"exit{code}", jid, d)
    # M-S2: a command kills the script's shell (the stop condition of the plan's §6.3)
    for sig in ("KILL", "TERM"):
        jid, d = submit(f"kill_shell_{sig}",
                        START + f"echo before; sh -c 'kill -{sig} $PPID'; echo after; exit 0")
        wait_end(f"kill_shell_{sig}", jid, d)
    d = out / "kill_shell_after_rewrite"
    jid, d = submit("kill_shell_after_rewrite", START + "echo 'spec2science-status 0'; "
                    f"sh -c 'printf \"spec2science-status 0\\n\" > {d}/job.out; kill -KILL $PPID'; "
                    "exit 0")
    wait_end("kill_shell_after_rewrite", jid, d)
    # M-S2: cancelled, time limit
    jid, d = submit("cancelled", START + "sleep 60")
    time.sleep(3)
    rec.log(case="cancelled", what="scancel", res=_run(["scancel", jid]))
    wait_end("cancelled", jid, d)
    jid, d = submit("timelimit", START + "sleep 300", time_min=1)
    wait_end("timelimit", jid, d, cap=300)
    # M-S3: where the script is kept, and whether a rewrite of it changes what runs
    lines = ['f=$(ls -l /proc/$$/fd | sed -n "s/.*-> \\(.*slurm_script\\)$/\\1/p")',
             'echo "script=$f"', 'ls -la "$f"',
             'printf "#!/bin/sh\\necho REWRITTEN_RAN\\nexit 0\\n" > "$f" && echo "rewrote rc=$?"',
             "sleep 1", "echo second_command_ran_original", "exit 1"]
    for case, body, use_stub in (("stub_rewrite", "; ".join(lines), True),
                                 ("nostub_rewrite", "#!/bin/sh\n" + "\n".join(lines) + "\n",
                                  False)):
        jid, d = submit(case, body, use_stub=use_stub)
        wait_end(case, jid, d)
    # M-S5: --no-requeue against a requeue the job asks for
    body = START + 'echo pass; scontrol requeue $SLURM_JOB_ID; echo "rc=$?"; sleep 5; echo end'
    for case, extra, no_requeue in (("requeue_self", (), True),
                                    ("requeue_directive_first", ("--requeue",), True),
                                    ("requeue_without_option", (), False)):
        jid, d = submit(case, body, extra=extra, no_requeue=no_requeue)
        wait_end(case, jid, d, cap=200)
        time.sleep(5)
        show(case, jid, "after_requeue_attempt")
        if not no_requeue:
            rec.log(case=case, what="scancel", res=_run(["scancel", jid]))
    self_requeue(rec, ended)
    # M-S6: a setup line printed without a final newline
    jid, d = submit("setup_no_newline", 'printf "to-stdout-no-nl"; printf "to-stderr-no-nl" >&2; '
                    + START + 'printf "spec2science-status 0\\n"')
    wait_end("setup_no_newline", jid, d)
    show("never_issued", "99999999", "never_issued")
    rec.log(what="sleep_for_minjobage", seconds=330)
    time.sleep(330)
    for case, jid in ended:
        show(case, jid, "after_330s")
    rec.log(what="done")


# ------------------------------------------------------------------------------------- executor

_SSH_SHIM = r'''#!/usr/bin/env python3
import subprocess, sys
args = sys.argv[1:]
while args and args[0] != "--":
    args = args[2:] if args[0] == "-o" else args[1:]
sys.exit(subprocess.run(["sh", "-c", " ".join(args[2:])]).returncode)
'''
_SCP_SHIM = r'''#!/usr/bin/env python3
import re, subprocess, sys
args = sys.argv[1:]
paths = args[args.index("--") + 1:]
sys.exit(subprocess.run(["cp", "-r", *[re.sub(r"^[^/:]+:", "", p) for p in paths]]).returncode)
'''


def executor(rec: Recorder) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    import dataclasses

    from tools import execution_sites as es
    from tools import remote_execution as rx
    from tools.tests.test_remote_execution import _FORGER, _RUNNER

    out = rec.out
    shims = out / "shims"
    shims.mkdir(exist_ok=True)
    for name, body in (("ssh", _SSH_SHIM), ("scp", _SCP_SHIM)):
        (shims / name).write_text(body)
        (shims / name).chmod(0o755)
    os.environ["PATH"] = f"{shims}{os.pathsep}{os.environ['PATH']}"
    workdir = out / "jobs"
    workdir.mkdir(exist_ok=True)
    local = out / "local"
    local.mkdir(exist_ok=True)
    runner = local / "runner"
    runner.write_text(_RUNNER)
    runner.chmod(0o755)
    forger = local / "forger"
    forger.write_text(_FORGER)
    forger.chmod(0o755)
    site = es.Site(site_id="this_slurm", executes=("cpu",), host="localhost",
                   workdir=str(workdir), scheduler="slurm", queue_timeout_sec=600)
    handles: list = []
    real_submit = rx.submit_job

    def submit_and_keep(*a, **k):
        handles.append(real_submit(*a, **k))
        return handles[-1]

    rx.submit_job = submit_and_keep

    def case(name, *, commands, ship=None, dirs=("run",), directives=(), during=None):
        arid = f"{name}-{int(time.time())}"
        s = dataclasses.replace(site, scheduler_directives=directives)
        job = rx.job_dir(s, "measure", arid)

        def cmd(tag, argv, *, cwd="run", env=None, timeout=60):
            return rx.CommandSpec(tag=tag, tool_name="run_program", argv=argv, cwd=f"{job}/{cwd}",
                                  record_cwd=str(local / cwd), env=env or {}, timeout_sec=timeout,
                                  command_log_path=local / name / f"{tag}.jsonl",
                                  capture_limit=120000)

        request = rx.JobRequest(site=s, job_dir=job, ship=ship or {"bin/runner": runner},
                                commands=tuple(cmd(*c[:2], **(c[2] if len(c) > 2 else {}))
                                               for c in commands(job)),
                                dirs=dirs, attribution={"orchestration_id": "measure",
                                                        "agent_run_id": arid})
        handles.clear()
        stop = threading.Event()
        if during:
            threading.Thread(target=during, args=(f"spec2science-{arid}", stop),
                             daemon=True).start()
        t0 = time.time()
        local_tmp = Path(tempfile.mkdtemp(dir=local))
        try:
            result = rx.execute_job(request, local_tmp=local_tmp)
            outcome = {"outcome": "accepted", "job_id": result.site_record["job_id"],
                       "queue_wait_ms": result.site_record["queue_wait_ms"],
                       "results": [None if r is None else
                                   {"ok": r["ok"], "return_code": r["return_code"],
                                    "stdout": r["stdout"][-300:]} for r in result.results]}
        except rx.RemoteExecutionError as exc:
            outcome = {"outcome": "refused", "message": str(exc)[-1500:]}
        stop.set()
        jid = handles[0].job_id if handles else None
        state = _run(["scontrol", "show", "job", "-o", jid]) if jid else None
        if ship and ship.get("bin/runner") == forger:
            # Whether the seeded rewrite landed: the forger leaves `job.out.rewritten` after it,
            # in the job directory (left on a refusal) or the copy collected from it. A leftover
            # killed first, as the job's end kills it under proctrack/cgroup, leaves none.
            time.sleep(2)
            mark = Path(rx.CONTROL_DIR) / "job.out.rewritten"
            outcome["rewrite_landed"] = ((Path(job) / mark).exists()
                                         or (local_tmp / "collected" / mark).exists())
        rec.log(case=name, what="execute_job", seconds=round(time.time() - t0, 1),
                submitted_id=jid, scontrol=state, **outcome)

    def cancel_when_running(name, stop):
        while not stop.is_set():
            rc, so, _ = _run(["squeue", "-h", "-n", name, "-o", "%i %T"])
            if "RUNNING" in so:
                rec.log(case="vc_cancelled", what="scancel", res=_run(["scancel", "-n", name]))
                return
            time.sleep(1)

    # V-c
    case("vc_exit0", commands=lambda j: [("run", ("sh", "-c", "echo job=$SLURM_JOB_ID"))])
    case("vc_exit1", commands=lambda j: [("run", ("sh", "-c", "exit 3")),
                                         ("qc", ("true",), {"cwd": "src"})])
    case("vc_exit3", commands=lambda j: [("run", ("true",))],
         ship={"bin/runner": runner, "run": runner}, dirs=())
    case("vc_timelimit", directives=("--time=1",),
         commands=lambda j: [("run", ("sleep", "200"), {"timeout": 300})])
    case("vc_cancelled", during=cancel_when_running,
         commands=lambda j: [("run", ("sleep", "100"), {"timeout": 300})])
    # V-e: the two seeded rows of BindingTests
    case("ve_rewrite", ship={"bin/runner": forger},
         commands=lambda j: [("run", (f"{j}/bin/runner",)), ("qc", ("true",), {"cwd": "src"})])
    case("ve_rewrite_and_kill", ship={"bin/runner": forger},
         commands=lambda j: [("run", (f"{j}/bin/runner",), {"env": {"KILL": "1"}}),
                             ("qc", ("true",), {"cwd": "src"})])
    rec.log(what="done")


# -------------------------------------------------------------------------------------- fixture

#: The answers of `raw` that become fixtures: the first answer of each (case, what) below, and
#: of each case's polls every distinct answer from `COMPLETING` on.
_FIRST = {("held", "after_submit"), ("running", "running"), ("never_issued", "never_issued"),
          ("exit0", "after_330s"), ("kill_shell_KILL", "after_330s"),
          ("requeue_self_update", "after_requeue_attempt")}
_POLLED = ("exit0", "exit1", "exit3", "kill_shell_KILL", "kill_shell_TERM",
           "kill_shell_after_rewrite", "cancelled", "timelimit")


def _sanitize(text: str) -> str:
    """The operator's account, the login's name and the measurement's paths, replaced."""
    text = re.sub(r"\b(StdErr|StdOut)=\S*/([^/\s]+/job\.(?:err|out))\b", r"\1=/work/ms/\2", text)
    text = re.sub(r"UserId=\S+", "UserId=user(1000)", text)
    text = re.sub(r"GroupId=\S+", "GroupId=group(1000)", text)
    text = re.sub(r"AllocNode:Sid=\S+", "AllocNode:Sid=login:1000", text)
    return re.sub(r"WorkDir=\S+", "WorkDir=/work", text)


def fixture(*records: Path) -> dict:
    rows = [json.loads(line) for path in records for line in path.read_text().splitlines()]
    answers: list[dict] = []
    seen: set[tuple] = set()
    for r in rows:
        key = (r.get("case"), r.get("what"))
        if "rc" not in r or r.get("what") == "sbatch":
            continue
        if key in _FIRST:
            dedup: tuple = key
        elif r.get("what") == "poll" and r["case"] in _POLLED \
                and (r["JobState"] in TERMINAL or r["JobState"] == "COMPLETING"):
            dedup = (*key, r["JobState"], r["ExitCode"])
        else:
            continue
        if dedup in seen:
            continue
        seen.add(dedup)
        answers.append({"case": f"{r['case']} ({r['what']})", "source": "measured",
                        "returncode": r["rc"], "stdout": _sanitize(r["stdout"]),
                        "stderr": r["stderr"]})
    return {"measured_on": "Slurm 20.02.5, one node, 2026-11-20 (issue #696, M-S2, M-S5): "
                           "`scontrol show job -o <id>`, verbatim but for _sanitize",
            "answers": answers}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("raw", "executor", "fixture") \
            or (argv[0] != "fixture" and len(argv) != 2):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[0] == "fixture":
        json.dump(fixture(*(Path(a) for a in argv[1:])), sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0
    rec = Recorder(Path(argv[1]).resolve())
    (raw if argv[0] == "raw" else executor)(rec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
