#!/usr/bin/env python3
"""Tests for the `slurm` scheduler backend's `job_submit` (`tools/backends/scheduler/slurm/`,
issues #293, #696). The executor's path through it, end to end over shims, is
`tools/tests/test_remote_execution.py` `SchedulerTests`, `BindingTests`, `StubTests` and
`PollingTests`. The answers `JobStateTests` reads were recorded on a real Slurm by
`tools/tests/data/scheduler_slurm/measure_slurm.py`, which is not a suite row (issue #696, O11)."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tools.backends import registry
from tools.backends.scheduler.slurm import submit

_ANSWERS = Path(__file__).resolve().parent / "data" / "scheduler_slurm" / "scontrol_answers.json"

#: The 24 `JobState` values `man squeue` documents on Slurm 20.02.5, and what each must mean.
_DOCUMENTED = {
    "PENDING": "queued", "REQUEUED": "queued", "REQUEUE_FED": "queued",
    "REQUEUE_HOLD": "queued", "RESV_DEL_HOLD": "queued", "SPECIAL_EXIT": "queued",
    "CONFIGURING": "queued", "RUNNING": "running", "COMPLETING": "running",
    "SIGNALING": "running", "STAGE_OUT": "running",
    # Measured, not in the fixture: a job's own `scancel --signal=STOP $SLURM_JOB_ID` stops it,
    # and its time limit advances while it is stopped (job 1693: stopped at 5 s, `TIMEOUT 0:15` at
    # 75 s, RunTime 00:01:10), so it is running for the run bound.
    "STOPPED": "running",
    # From `man squeue` alone: no preemption on the cluster measured, and a job's own
    # `scontrol suspend` of its id is refused there (`Access/permission denied`).
    "SUSPENDED": "running", "RESIZING": "running",
    "COMPLETED": "ended", "FAILED": "ended", "BOOT_FAIL": "ended", "CANCELLED": "ended",
    "DEADLINE": "ended", "NODE_FAIL": "ended", "OUT_OF_MEMORY": "ended", "PREEMPTED": "ended",
    "REVOKED": "ended", "TIMEOUT": "ended",
}


def _measured() -> list[dict]:
    return json.loads(_ANSWERS.read_text())["answers"]


def _template() -> str:
    """A measured answer (`COMPLETED 0:0`) whose state and exit code a manual row replaces."""
    (done,) = [a for a in _measured() if a["case"] == "exit0 (poll)"]
    return done["stdout"]


def _with(state: str, exit_code: str = "0:0") -> str:
    text = re.sub(r"\bJobState=\S+", f"JobState={state}", _template())
    return re.sub(r"\bExitCode=\S+", f"ExitCode={exit_code}", text)


class SubmitArgvTests(unittest.TestCase):

    def _argv(self, **kw) -> tuple[str, ...]:
        args = {"directives": (), "job_name": "spec2science-j", "wall_clock_sec": 60,
                "output_path": "/w/j/ctl/job.out", "error_path": "/w/j/ctl/job.err"}
        args.update(kw)
        return submit.submit_argv(**args)

    def test_the_time_limit_comes_before_the_directives_and_the_executors_options_after(
            self) -> None:
        """A directive may shorten the time limit (a short partition's maximum); one repeating
        the name, a file or the requeue policy loses to the executor's, which come last
        (measured: the last of a repeated option wins, `--requeue` before `--no-requeue`
        included)."""
        self.assertEqual(
            self._argv(directives=("--partition=gpu", "-p  debug", "--time=30", "--requeue")),
            ("sbatch", "--parsable", "--time=1", "--partition=gpu", "-p", "debug", "--time=30",
             "--requeue", "--job-name=spec2science-j", "--output=/w/j/ctl/job.out",
             "--error=/w/j/ctl/job.err", "--no-requeue"))

    def test_the_job_may_not_run_a_second_time(self) -> None:
        self.assertEqual(self._argv()[-1], "--no-requeue")

    def test_the_time_limit_is_rounded_up_to_whole_minutes(self) -> None:
        for sec, minutes in ((1, 1), (60, 1), (61, 2), (3600, 60), (3601, 61)):
            with self.subTest(sec=sec):
                self.assertIn(f"--time={minutes}", self._argv(wall_clock_sec=sec))

    def test_a_bound_below_one_second_is_refused(self) -> None:
        for sec in (0, -1):
            with self.subTest(sec=sec), self.assertRaises(ValueError):
                self._argv(wall_clock_sec=sec)


class JobIdTests(unittest.TestCase):

    def test_the_parsable_line_with_and_without_a_cluster(self) -> None:
        """Measured (M-S1): `<id>\\n`. With a cluster (from the manual): `<id>;<cluster>`."""
        self.assertEqual(submit.job_id("1613\n"), "1613")
        self.assertEqual(submit.job_id("1613;linux\n"), "1613")

    def test_an_empty_answer_is_none(self) -> None:
        for text in ("", "\n", "  \n"):
            with self.subTest(text=text):
                self.assertIsNone(submit.job_id(text))

    def test_a_line_that_is_not_one_element_is_returned_for_the_caller_to_refuse(self) -> None:
        """The executor holds the id to one path element (`remote_execution._ELEMENT`)."""
        from tools import remote_execution as rx
        for text in ("warning: x\n1613\n", "16 13\n"):
            with self.subTest(text=text):
                got = submit.job_id(text)
                self.assertIsNotNone(got)
                self.assertIsNone(rx._ELEMENT.fullmatch(got))


class StateArgvTests(unittest.TestCase):

    def test_one_command_states_the_job(self) -> None:
        self.assertEqual(submit.state_argvs("1613"), (("scontrol", "show", "job", "-o", "1613"),))


class JobStateTests(unittest.TestCase):
    """Every documented state, by an answer recorded on Slurm 20.02.5 where one was measured
    (`source: measured` in the fixture) and otherwise by the measured `COMPLETED 0:0` answer with
    its `JobState` and `ExitCode` replaced (a manual row, written from `man squeue`)."""

    def test_the_backend_classifies_exactly_the_documented_states(self) -> None:
        self.assertEqual(len(_DOCUMENTED), 24)
        sets = (submit.QUEUED_STATES, submit.RUNNING_STATES, submit.ENDED_STATES)
        self.assertEqual(sum(len(s) for s in sets), 24)
        self.assertEqual(set().union(*sets), set(_DOCUMENTED))

    def test_each_measured_answer(self) -> None:
        expected = {
            "held (after_submit)": ("queued", None),
            "running (running)": ("running", None),
            "exit0 (poll)": ("ended", 0),
            "exit1 (poll)": ("ended", 1),
            "exit3 (poll)": ("ended", None),
            "kill_shell_KILL (poll)": ("ended", None),
            "kill_shell_TERM (poll)": ("ended", None),
            "kill_shell_after_rewrite (poll)": ("ended", None),
            "never_issued (never_issued)": ("unknown_id", None),
            "exit0 (after_330s)": ("unknown_id", None),
            "kill_shell_KILL (after_330s)": ("unknown_id", None),
            "requeue_self_update (after_requeue_attempt)": ("ended", None),
        }
        teardown = {"COMPLETING": ("running", None), "CANCELLED": ("ended", None),
                    "TIMEOUT": ("ended", None)}
        seen = set()
        for a in _measured():
            with self.subTest(case=a["case"], stdout=a["stdout"][:120]):
                self.assertEqual(a["source"], "measured")
                got = submit.job_state(a["returncode"], a["stdout"], a["stderr"])
                if a["case"] in expected:
                    self.assertEqual(got, expected[a["case"]])
                else:
                    state = re.search(r"\bJobState=(\S+)", a["stdout"]).group(1)
                    self.assertEqual(got, teardown[state])
                    seen.add(state)
        self.assertEqual(seen, set(teardown), "a cancelled and a timed-out job, measured")
        self.assertLessEqual(set(expected), {a["case"] for a in _measured()})

    def test_every_documented_state_from_the_manual(self) -> None:
        for state, kind in _DOCUMENTED.items():
            with self.subTest(state=state):
                got = submit.job_state(0, _with(state, "0:15"), "")
                self.assertEqual(got, (kind, None))

    def test_success_and_failure_are_an_allowlist_of_both_parts(self) -> None:
        """A script whose shell was killed has exit part 0 (`FAILED 0:9`, measured); success is
        `COMPLETED 0:0` alone and "a command failed" `FAILED 1:0` alone."""
        for state, code, own_exit in (
                ("COMPLETED", "0:0", 0), ("COMPLETED", "0:9", None), ("COMPLETED", "1:0", None),
                ("FAILED", "1:0", 1), ("FAILED", "3:0", None), ("FAILED", "0:9", None),
                ("FAILED", "0:0", None), ("FAILED", "1:9", None), ("TIMEOUT", "0:0", None),
                ("CANCELLED", "1:0", None), ("NODE_FAIL", "0:0", None)):
            with self.subTest(state=state, code=code):
                self.assertEqual(submit.job_state(0, _with(state, code), ""), ("ended", own_exit))

    def test_an_own_exit_is_read_only_from_a_job_whose_script_ran_once(self) -> None:
        """Under `--no-requeue` the job's own command can turn its requeue flag back on and
        requeue itself; measured (M-S5): the second pass ended `COMPLETED 0:0` with
        `Restarts=1`. Two passes must not be recorded as one run, so a terminal record is read
        as an own exit only at `Restarts=0`; a missing or repeated `Restarts` is not."""
        (again,) = [a for a in _measured()
                    if a["case"] == "requeue_self_update (after_requeue_attempt)"]
        self.assertRegex(again["stdout"], r"\bJobState=COMPLETED\b.*\bExitCode=0:0\b")
        self.assertIn(" Restarts=1 ", again["stdout"])
        self.assertEqual(submit.job_state(0, again["stdout"], ""), ("ended", None))
        # The measured own exits carry Restarts=0 and are still read as such.
        for case, own_exit in (("exit0 (poll)", 0), ("exit1 (poll)", 1)):
            (a,) = [a for a in _measured() if a["case"] == case]
            with self.subTest(case=case):
                self.assertIn(" Restarts=0 ", a["stdout"])
                self.assertEqual(submit.job_state(0, a["stdout"], ""), ("ended", own_exit))
        for state, code in (("COMPLETED", "0:0"), ("FAILED", "1:0")):
            text = _with(state, code)
            for label, changed in (
                    ("Restarts=1", text.replace(" Restarts=0 ", " Restarts=1 ")),
                    ("Restarts=2", text.replace(" Restarts=0 ", " Restarts=2 ")),
                    ("missing", text.replace(" Restarts=0 ", " ")),
                    ("twice", text.replace(" Restarts=0 ", " Restarts=0 Restarts=0 "))):
                with self.subTest(state=state, restarts=label):
                    self.assertNotEqual(changed, text)
                    self.assertEqual(submit.job_state(0, changed, ""), ("ended", None))
        # A job not yet ended is answered by its state alone: a requeued one is queued again.
        pending = _with("PENDING").replace(" Restarts=0 ", " Restarts=1 ")
        self.assertEqual(submit.job_state(0, pending, ""), ("queued", None))

    def test_a_state_not_listed_is_no_answer_quoting_it(self) -> None:
        kind, text = submit.job_state(0, _with("ZZ_UNLISTED"), "")
        self.assertEqual(kind, "no_answer")
        self.assertIn("ZZ_UNLISTED", text)

    def test_a_field_named_twice_is_no_answer(self) -> None:
        """The job's own commands can rename it; a name carrying the success fields before the
        real ones must not be read as the record."""
        failed = _with("FAILED", "1:0")
        forged = failed.replace("JobName=ms-exit0",
                                "JobName=x JobState=COMPLETED ExitCode=0:0")
        self.assertNotEqual(forged, failed)
        self.assertEqual(submit.job_state(0, forged, "")[0], "no_answer")
        for missing in ("JobState", "ExitCode"):
            with self.subTest(missing=missing):
                text = re.sub(rf"\b{missing}=\S+", "", failed)
                self.assertEqual(submit.job_state(0, text, "")[0], "no_answer")
        # A field whose name only ENDS with the key is another field.
        glued = failed.replace("JobName=ms-exit0", "JobName=xJobState=COMPLETED")
        self.assertEqual(submit.job_state(0, glued, ""), ("ended", 1))

    def test_another_failure_of_the_command_is_no_answer(self) -> None:
        for rc, stdout, stderr in (
                (1, "", "slurm_load_jobs error: Socket timed out on send/recv operation\n"),
                (1, "", ""), (127, "", "sh: scontrol: not found\n"), (0, "", ""),
                (1, "JobId=1 Invalid job id specified", "Invalid job id specified")):
            with self.subTest(rc=rc, stderr=stderr):
                self.assertEqual(submit.job_state(rc, stdout, stderr)[0], "no_answer")


class DeclarationTests(unittest.TestCase):

    def test_the_registry_reaches_this_module_for_job_submit(self) -> None:
        self.assertIs(registry.capability_module("scheduler", "slurm", "job_submit"), submit)
        self.assertIsNone(registry.unavailable_reason("scheduler", "slurm"))
        self.assertEqual(submit.REMOTE_EXECUTABLES, ("sbatch", "scontrol"))
        self.assertEqual({argv[0] for argv in submit.state_argvs("1")} | {"sbatch"},
                         set(submit.REMOTE_EXECUTABLES))


if __name__ == "__main__":
    unittest.main()
