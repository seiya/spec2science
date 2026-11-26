"""The captures of a run, read and placed through the target's output format (issue #644, Part 3
P3): the neutral seam between the host and the `output_format` backends.

A run writes its captures — a case's state before its first call and after its last, the
samples between and the per-step series — under `raw/state_snapshots/` of its run directory, in
the format the target profile chooses (`output.format`, the `output_format` axis). Which files
hold them, how one is decoded and what a damaged one looks like is that format's knowledge, held
by its backend's `capture_store` capability (`tools/backends/output_format/<id>/store.py`). The
host — the conductor's `Validate.execute`, the verdict's reader (`tools/primary_evidence.py`),
the post-execute gate and `tools/run_configuration.py` — reaches it here
and holds no file name, suffix or directory of its own.

What stays here is what no format decides: where the capture directory is, the host-written
`snapshot_schema.json` in it (JSON on every target), the groups a record belongs to, and the
error a store raises.

The store that reads a directory is the store of the target whose run WROTE it: a caller passes
that target's `output_format` (`TargetProfile.output_format`), never a format guessed from the
directory's content.

What a store is: a module with `REQUIRED_PYTHON_MODULES` and these functions, which the host
calls —
`run_dirs`, `record_path`, `has_record`, `read_record`, `case_ids`, `history_steps`,
`restart_point_steps`, `read_series_prefix`, `case_paths`, `all_case_paths`, `copy_run`,
`move_case`, `locate`,
`expected_layout`, and `capture_layout_document`, the layout as a harness's author is told it
(`tools/backends/output_format/json/store.py` states each). A record is what
one commit of the harness's capture interface closed, except a case's series, which is every
row the case committed: how many files hold either is the store's. A record's
members are returned as JSON spells them — a number, a boolean, a string, nested lists of
those — whatever the format holds on disk: the neutral readers judge rank, extents and
finiteness on lists and refuse any other value as a non-number.

Imports only the registry, so every reader can import it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.backends import registry

#: The axis and the capability a store is reached by.
AXIS = "output_format"
CAPABILITY = "capture_store"

#: The run directory's capture directory, as path parts.
SNAPSHOT_DIR_PARTS: tuple[str, ...] = ("raw", "state_snapshots")
#: The host-authored file in it that is no capture: the snapshot schema the conductor writes
#: from the IR. JSON on every target.
SNAPSHOT_SCHEMA_FILENAME = "snapshot_schema.json"

#: The groups a record belongs to. `INITIAL` and `FINAL` hold one record of a case's state
#: each, `HISTORY` one per sampled step (the variables due at that step), `RESTART_POINT` one
#: per restart point (the restart variables), `SERIES` the case's per-step series. The words
#: are the group names of the harness's capture interface.
INITIAL = "initial"
FINAL = "final"
HISTORY = "history"
RESTART_POINT = "restart_point"
SERIES = "series"


class CaptureError(ValueError):
    """A record could not be read. `label` is the store's name for it (what a message calls the
    record), `reason` what is wrong with it, worded to follow "is" (`absent`, `unreadable: …`);
    `absent` is True when the record is simply not there."""

    def __init__(self, label: str, reason: str, *, absent: bool = False) -> None:
        super().__init__(f"{label} is {reason}")
        self.label = label
        self.reason = reason
        self.absent = absent


def snapshot_dir(run_dir: Path) -> Path:
    """The capture directory of a run directory."""
    return Path(run_dir).joinpath(*SNAPSHOT_DIR_PARTS)


def store(output_format: str) -> Any:
    """The `capture_store` module of ``output_format``. Raises the registry's refusal for a
    value that is no available member (the launch gate refuses such a target first,
    `target_profile.target_profile_violations`)."""
    return registry.capability_module(AXIS, output_format, CAPABILITY)


def run_dirs(output_format: str) -> tuple[tuple[str, ...], ...]:
    """The directories created under a run's working directory before the binary runs, each as
    its path parts: the store's own directories below the capture directory (`run_dirs()`;
    `""` is the capture directory itself, given without an empty part, which a remote job's
    directory list refuses), for every node. Every runner writes its captures through the harness's capture
    interface, and the harness opens each file relatively and creates no directory, a harness
    self-test's included (issue #644 P4; before it only the host-rendered runner got them,
    issue #368, when a self-test wrote its own files and could list the directory strictly).
    One answer, read by `Validate.execute`, its remote job, a `workshop leaf`'s wrapper `run`,
    the build control file's `test` recipe and `tools/run_configuration.py`."""
    return tuple((*SNAPSHOT_DIR_PARTS, sub) if sub else SNAPSHOT_DIR_PARTS
                 for sub in store(output_format).run_dirs())


def capture_paths(output_format: str, run_dir: Path) -> list[Path]:
    """Every capture file under a run directory the store recognises, the snapshot schema
    excluded, sorted by path."""
    sdir = snapshot_dir(run_dir)
    return sorted(p for p in store(output_format).all_case_paths(sdir)
                  if p.relative_to(sdir).as_posix() != SNAPSHOT_SCHEMA_FILENAME)
