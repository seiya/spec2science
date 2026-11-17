#!/usr/bin/env python3
"""Issue #644 P6 V-c (unbilled, no LLM): a run killed with SIGKILL, continued from its last
restart point that reads, on the binaries R3 certified.

  PYTHONPATH=. python3 <this file> --config <yaml> --work <new dir outside workspace/> \
      [--attempts N]

Every run goes through `tools/run_configuration.py`'s command line on
`problem/shallow_water2d_rotating`, with the configuration `--config` names (a base case with
restart points, enlarged so a run lasts long enough to be killed). The kill is a SIGKILL to the
BINARY's process group (`run_record.json#binary_pgid`), so the tool itself survives to write its
record (`kept`).

`fortran_cpu` (`json`):
1. `U`: the configuration uninterrupted.
2. `between`: killed while no restart point is being written (just after a new restart point's
   file reaches the size of U's file of that point and reads). Every restart point it kept must read;
   it is continued (`--restart-from`) from the last one, `K`.
3. `inside`: killed while a restart point is being written: the file of the next restart point is
   polled and the group killed as soon as the file exists and is shorter than U's file of that point.
   That is retried (`--attempts`) until a run leaves a restart point that does not read; the
   tool's `kept` must leave it out, `--restart-from` it must be refused (exit 2) with the message `<torn file>: unreadable`, and the run is
   continued from the last one that reads.
For each continuation: the final capture equals U's, every history sample and restart point after
`K` equals U's, the continuation's series equals the tail of U's series, and the killed run's
series, as `kept` reads it (`read_series_prefix`), equals the head of U's up to where it stops.
Equal is binary64-equal, NaN equal to NaN (`format_equivalence._diff`).

`fortran_cpu_netcdf`: the same configuration killed mid-run; the store must refuse the case's
file (the kill leaves it without `complete`), with a message that names the case.

Exit 0 when every check holds; 1 otherwise. An `inside` kill that never lands inside a write in
`--attempts` tries is reported as such (exit 1), not simulated by truncating a file.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from tools import capture_store  # noqa: E402
from format_equivalence import _diff  # noqa: E402

SPEC = "spec/problem/dynamics/shallow_water/shallow_water2d_rotating"
FAILURES: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("OK   " if ok else "FAIL ") + what)
    if not ok:
        FAILURES.append(what)


def _cmd(target: str, config: Path, out: Path, restart: Path | None) -> list[str]:
    cmd = [sys.executable, "tools/run_configuration.py", SPEC, "--target", target,
           "--config", str(config), "--out", str(out)]
    return cmd + (["--restart-from", str(restart)] if restart else [])


def run(target: str, config: Path, out: Path, restart: Path | None = None) -> int:
    return run_full(target, config, out, restart).returncode


def run_full(target: str, config: Path, out: Path,
             restart: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(_cmd(target, config, out, restart), cwd=REPO,
                          capture_output=True, text=True)


def record(out: Path) -> dict[str, Any]:
    return json.loads((out / "run_record.json").read_text(encoding="utf-8"))


def killed(target: str, config: Path, out: Path, when) -> tuple[int, dict[str, Any]]:
    """Start the tool, call `when(out)` until it is true, SIGKILL the binary's group."""
    proc = subprocess.Popen(_cmd(target, config, out, None), cwd=REPO,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    pgid = None
    while proc.poll() is None:
        if pgid is None and (out / "run_record.json").exists():
            try:
                pgid = record(out).get("binary_pgid")
            except (json.JSONDecodeError, OSError):
                pass
        if pgid and when(out):
            os.killpg(pgid, signal.SIGKILL)
            break
        time.sleep(0.0005)
    proc.communicate()
    return proc.returncode, record(out)


def snap(out: Path) -> Path:
    return capture_store.snapshot_dir(out)


def points(out: Path, fmt: str, base: str) -> list[int]:
    return capture_store.store(fmt).restart_point_steps(snap(out), base)


def reads(out: Path, fmt: str, base: str, step: int) -> bool:
    try:
        capture_store.store(fmt).read_record(snap(out), capture_store.RESTART_POINT, base, step)
    except capture_store.CaptureError:
        return False
    return True


def compare_continuation(u: Path, cont: Path, killed_run: Path, base: str, k: int,
                         label: str) -> None:
    store = capture_store.store("json")
    diffs: list[str] = []
    numbers = _diff(store.read_record(snap(u), capture_store.FINAL, base),
                    store.read_record(snap(cont), capture_store.FINAL, base), "final", diffs)
    after = [s for s in store.history_steps(snap(u), base) if s > k]
    check(after == [s for s in store.history_steps(snap(cont), base) if s > k],
          f"{label}: the continuation holds U's history steps after K={k} ({len(after)})")
    for s in after:
        numbers += _diff(store.read_record(snap(u), capture_store.HISTORY, base, s),
                         store.read_record(snap(cont), capture_store.HISTORY, base, s),
                         f"history@{s}", diffs)
    rp_after = [s for s in points(u, "json", base) if s > k]
    check(rp_after == [s for s in points(cont, "json", base) if s > k],
          f"{label}: the continuation holds U's restart points after K ({len(rp_after)})")
    for s in rp_after:
        numbers += _diff(store.read_record(snap(u), capture_store.RESTART_POINT, base, s),
                         store.read_record(snap(cont), capture_store.RESTART_POINT, base, s),
                         f"restart_point@{s}", diffs)
    su = store.read_record(snap(u), capture_store.SERIES, base)
    sc = store.read_record(snap(cont), capture_store.SERIES, base)
    n = len(sc["step"])
    numbers += _diff({f: su[f][-n:] for f in su}, sc, "series tail", diffs)
    check(sc["step"][0] == k + 1, f"{label}: the continuation's series starts at K+1 "
          f"({sc['step'][0]})")
    rows, last = store.read_series_prefix(snap(killed_run), base)
    m = max(len(v) for v in rows.values())
    numbers += _diff({f: su[f][:m] for f in su}, rows, "killed series head", diffs)
    check(last is not None and last >= k,
          f"{label}: the killed run's series reads up to step {last} (K={k}, {m} rows)")
    check(not diffs, f"{label}: continuation equals U ({numbers} numbers; "
          f"{len(diffs)} differences {diffs[:5]})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--attempts", type=int, default=20)
    args = ap.parse_args()
    import yaml
    base = yaml.safe_load(args.config.read_text(encoding="utf-8"))["base_case"]
    work = args.work.resolve()
    work.mkdir(parents=True)
    config = args.config.resolve()

    u = work / "U"
    check(run("fortran_cpu", config, u) == 0, "U: ok")
    u_points = points(u, "json", base)
    target_point = u_points[len(u_points) // 2]
    # the runs are deterministic, so the killed run's file of this point, once whole, is U's size
    full = capture_store.store("json").record_path(
        snap(u), capture_store.RESTART_POINT, base, target_point).stat().st_size
    print(f"U: final step {record(u)['final_step']}, restart points {u_points[:3]}..."
          f"{u_points[-1]} ({len(u_points)}); the point at {target_point}: {full} bytes")

    # 2. between restart points
    def between(out: Path) -> bool:
        p = capture_store.store("json").record_path(snap(out), capture_store.RESTART_POINT,
                                                    base, target_point)
        try:
            whole = p.stat().st_size == full
        except FileNotFoundError:
            return False
        return whole and reads(out, "json", base, target_point)
    b = work / "between"
    rc, rec = killed("fortran_cpu", config, b, between)
    kept = (rec.get("kept") or {}).get("restart_points") or []
    check(rc == 1 and rec["status"] != "ok", f"between: the tool exits 1 (rc={rc}, "
          f"status={rec['status']}, exit_status={rec['exit_status']})")
    check(kept == points(b, "json", base) and kept and kept[-1] >= target_point,
          f"between: every restart point on disk reads and is kept (last {kept[-1:]})")
    k = kept[-1]
    rp = capture_store.store("json").record_path(snap(b), capture_store.RESTART_POINT, base, k)
    bc = work / "between_cont"
    check(run("fortran_cpu", config, bc, rp) == 0, f"between: continued from K={k}: ok")
    check(record(bc)["start_check"] == "held",
          f"between: the start check held ({record(bc)['start_check']!r})")
    compare_continuation(u, bc, b, base, k, "between")

    # 3. inside a restart point's write
    landed = None
    for attempt in range(1, args.attempts + 1):
        out = work / f"inside_{attempt}"

        def inside(o: Path) -> bool:
            p = capture_store.store("json").record_path(snap(o), capture_store.RESTART_POINT,
                                                        base, target_point)
            try:
                return 0 < p.stat().st_size < full
            except FileNotFoundError:
                return False
        rc, rec = killed("fortran_cpu", config, out, inside)
        cut = capture_store.store("json").record_path(snap(out), capture_store.RESTART_POINT,
                                                      base, target_point)
        size = cut.stat().st_size if cut.exists() else None
        torn = size is not None and not reads(out, "json", base, target_point)
        print(f"inside attempt {attempt}: rc={rc} file {size} of {full} bytes; "
              f"{'does not read' if torn else 'reads'}")
        if torn:
            landed = (out, rec)
            break
    check(landed is not None, f"inside: a kill landed inside a restart point's write "
          f"within {args.attempts} attempts")
    if landed:
        out, rec = landed
        kept = (rec.get("kept") or {}).get("restart_points") or []
        check(target_point not in kept and kept and kept[-1] < target_point,
              f"inside: kept leaves the torn point {target_point} out (last kept {kept[-1:]})")
        refused = run_full("fortran_cpu", config, work / "inside_refused", cut)
        why = (refused.stdout + refused.stderr).strip()
        # exit 2 is every refusal's: the message must be the torn file's
        check(refused.returncode == 2 and f"{cut.name}: unreadable" in why,
              f"inside: --restart-from the torn point is refused as unreadable ({why[-160:]!r})")
        k = kept[-1]
        rp = capture_store.store("json").record_path(snap(out), capture_store.RESTART_POINT,
                                                     base, k)
        ic = work / "inside_cont"
        check(run("fortran_cpu", config, ic, rp) == 0, f"inside: continued from K={k}: ok")
        check(record(ic)["start_check"] == "held",
              f"inside: the start check held ({record(ic)['start_check']!r})")
        compare_continuation(u, ic, out, base, k, "inside")

    # netcdf: killed mid-run
    def some_points(o: Path) -> bool:
        # the case's file exists and the run is well into it
        return (time.monotonic() - started) > 15 and any(
            capture_store.snapshot_dir(o).glob(f"{base}*"))
    started = time.monotonic()
    nc = work / "netcdf_killed"
    rc, rec = killed("fortran_cpu_netcdf", config, nc, some_points)
    try:
        capture_store.store("netcdf").read_record(snap(nc), capture_store.FINAL, base)
        message = None
    except capture_store.CaptureError as exc:
        # an absent file names the case too: what is shown is the refusal of a file that is there
        message = None if exc.absent else str(exc)
    print(f"netcdf: rc={rc} status={rec['status']} kept={rec.get('kept')}; store: {message}")
    check(message is not None and base in message,
          f"netcdf: the store refuses the killed case, naming it ({message!r})")
    nc_u = work / "netcdf_U"
    check(run("fortran_cpu_netcdf", config, nc_u) == 0, "netcdf: the same configuration "
          "uninterrupted is ok (the refusal is the kill's)")

    print(f"{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
