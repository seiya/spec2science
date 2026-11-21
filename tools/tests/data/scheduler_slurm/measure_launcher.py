#!/usr/bin/env python3
"""Measurements of the launch canary of a remote `Build` job (issue #700 PR-3, V-e and V-f).

NOT a suite row: pytest collects `test_*.py` only, and these rows run a real launcher at a real
site, so they live here beside `measure_slurm.py` and are run by hand. Each case is one job of
the canary's three commands alone — built with the `mpi` backend's compiler wrapper, started
under its launcher with the case's rank count, started bare — composed by
`workflow_conductor.remote_launch_canary`, run by `remote_execution.execute_job` with this
checkout's code, and read by the canary's own `problem`. Nothing is billed.

    python3 tools/tests/data/scheduler_slurm/measure_launcher.py none <site_id> <out_dir>
    python3 tools/tests/data/scheduler_slurm/measure_launcher.py slurm <out_dir>

- `none` is V-e: the site `<site_id>` of `sample/sites.yaml`, a `scheduler: none` site reached by the
  real ssh and scp. Two cases: 4 ranks, which must be accepted, and one rank more than the CPUs
  of the machine the site's login runs on (asked with `nproc --all` over ssh), which the
  launcher must refuse and the canary must report as a launch that did not complete.
- `slurm` is V-f: this machine's Slurm, with ssh and scp SHIMMED as `measure_slurm.py executor`
  shims them (a non-interactive ssh login here does not put Slurm's programs on its `PATH`), so
  the job inherits THIS process's environment: run it with the wrapper and the launcher of one
  installation first on `PATH`. Two cases at 4 ranks: no task directive, which the launcher must
  refuse inside the job, and `--ntasks=4 --cpus-per-task=3`, which must be accepted — its bare
  start inside the allocation included.

Each record is one JSON line in `<out_dir>/records.jsonl`: the case, the outcome (`accepted`
with the canary's verdict, or the executor's refusal), and the three commands' exit codes and
output tails.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
#: The sample Project (issue #705 PR-2): its `sites.yaml` State and target profiles.
PROJECT = REPO_ROOT / "sample"

from tools import execution_sites as es
from tools import remote_execution as rx
from tools.workflow_conductor import remote_launch_canary

_TC = {"backend": "mpi", "compiler": "", "language": "fortran"}


def _case(out: Path, site: es.Site, name: str, ranks: int) -> dict:
    """Run the canary of `ranks` processes at `site` as one job; its record."""
    local = out / name
    canary = remote_launch_canary(types.SimpleNamespace(parallel_backend="mpi", ranks=ranks),
                                  _TC, local / "launch_canary",
                                  log=local / "launch_canary.jsonl")
    assert canary is not None
    arid = f"{name}-{int(time.time())}"
    job = rx.job_dir(site, "measure_launcher", arid)
    request = rx.JobRequest(site=site, job_dir=job, ship={canary.ship_path: canary.source},
                            commands=canary.commands(job),
                            attribution={"orchestration_id": "measure_launcher",
                                         "agent_run_id": arid})
    t0 = time.time()
    try:
        result = rx.execute_job(request, local_tmp=Path(tempfile.mkdtemp(dir=local)))
    except rx.RemoteExecutionError as exc:
        return {"case": name, "ranks": ranks, "outcome": "refused",
                "message": str(exc)[-1500:], "seconds": round(time.time() - t0, 1)}
    by_tag = dict(zip(canary.TAGS, result.results))
    return {"case": name, "ranks": ranks, "outcome": "accepted",
            "problem": canary.problem(by_tag), "job_id": result.site_record["job_id"],
            "seconds": round(time.time() - t0, 1),
            "results": {tag: None if r is None else
                        {"return_code": r["return_code"], "stdout": r["stdout"][-300:],
                         "stderr": r["stderr"][-600:]} for tag, r in by_tag.items()}}


def _log(out: Path, record: dict) -> None:
    record["t"] = round(time.time(), 3)
    with (out / "records.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")
    print(json.dumps(record)[:600], flush=True)


def none(site_id: str, out: Path) -> None:
    config = es.load_sites(PROJECT)
    site = config.sites[site_id]
    cpus = int(subprocess.run(["ssh", "-o", "BatchMode=yes", site.host, "nproc", "--all"],
                              capture_output=True, text=True, check=True,
                              stdin=subprocess.DEVNULL).stdout.strip())
    _log(out, _case(out, site, "none_fits", 4))
    _log(out, _case(out, site, "none_too_many", cpus + 1))


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


def slurm(out: Path) -> None:
    shims = out / "shims"
    shims.mkdir(exist_ok=True)
    for name, body in (("ssh", _SSH_SHIM), ("scp", _SCP_SHIM)):
        (shims / name).write_text(body)
        (shims / name).chmod(0o755)
    os.environ["PATH"] = f"{shims}{os.pathsep}{os.environ['PATH']}"
    workdir = out / "jobs"
    workdir.mkdir(exist_ok=True)
    site = es.Site(site_id="this_slurm", executes=("cpu",), host="localhost",
                   workdir=str(workdir), scheduler="slurm", queue_timeout_sec=600,
                   scheduler_directives=("--time=5",))
    _log(out, _case(out, site, "slurm_no_tasks", 4))
    _log(out, _case(out, dataclasses.replace(
        site, scheduler_directives=("--time=5", "--ntasks=4", "--cpus-per-task=3")),
        "slurm_tasks", 4))


def main(argv: list[str]) -> int:
    if not ((len(argv) == 3 and argv[0] == "none") or (len(argv) == 2 and argv[0] == "slurm")):
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[-1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if argv[0] == "none":
        none(argv[1], out)
    else:
        slurm(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
