#!/usr/bin/env python3
"""The `capture_store` of the `json` output format (issue #644, Part 3 P3): JSON objects under
a run's capture directory (`tools/capture_store.py`, which every reader reaches this module
through).

Layout, `<C>` a case id, `<k>` a committed step and `<i>` a count from 0, both in decimal
digits with no leading zero:

* `<C>.json` — the final record of `<C>`;
* `initial/<C>.json` — its initial record;
* `history/<C>__<k>.json` — its sample at step `<k>`;
* `restart_point/<C>__<k>.json` — its restart point at step `<k>`;
* `series/<C>__<i>.json` — the `<i>`-th chunk of its per-step series: the rows committed
  since the chunk before it. The harness writes a chunk before each restart point and at the
  end of the case, so a case has at least chunk 0 and its series record is its chunks joined.

A record is the object's members as `json` decodes them — the value domain every store
returns (`tools/capture_store.py`). The readers judge the values (rank, extents, finiteness,
non-numbers); this module only finds, decodes and joins. A nested list cannot spell the
extents after a zero one, so a zero-size array reads back as the lists its writer closed.

Every file is written once by the run; nothing here writes a record.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from tools.capture_store import (
    FINAL,
    HISTORY,
    INITIAL,
    RESTART_POINT,
    SERIES,
    SNAPSHOT_SCHEMA_FILENAME,
    CaptureError,
)

#: The facts a language backend's runner-output lints build their patterns from.
FORMAT_FACTS: dict[str, Any] = {"suffix": ".json"}
_SUFFIX = FORMAT_FACTS["suffix"]

#: The Python modules reading this format needs beyond the standard library: none.
REQUIRED_PYTHON_MODULES: tuple[tuple[str, str], ...] = ()

#: The directory of each group below the capture directory; the final records sit in it.
_GROUP_DIR: dict[str, str] = {FINAL: "", INITIAL: "initial", HISTORY: "history",
                              RESTART_POINT: "restart_point", SERIES: "series"}
#: The groups whose records are named by a committed step.
_STEPPED = (HISTORY, RESTART_POINT)
#: The name, without the suffix, of a sample, a restart point or a series chunk: the case id,
#: `__`, and the step (the chunk's count) in decimal digits with no leading zero — the one
#: spelling `record_path` produces, so a record found is a record read.
_SAMPLE = re.compile(r"(?P<case>.+)__(?P<step>0|[1-9][0-9]*)")


def run_dirs() -> tuple[str, ...]:
    """The directories below the capture directory a run needs created before it starts: the
    harness opens each file relatively and creates no directory."""
    return tuple(_GROUP_DIR[group] for group in (INITIAL, HISTORY, RESTART_POINT, SERIES))


def _group_dir(snap_dir: Path, group: str) -> Path:
    sub = _GROUP_DIR[group]
    return Path(snap_dir) / sub if sub else Path(snap_dir)


def record_path(snap_dir: Path, group: str, case_id: str, step: int | None = None) -> Path:
    """The file holding one record: of `group`, of `case_id`, and — for a sample or a restart
    point — of the one at `step`. A series is named by its first chunk, which every case has."""
    if (group in _STEPPED) != (step is not None):
        raise ValueError(f"a {group} record is named {'with' if group in _STEPPED else 'without'}"
                         " a step")
    if group == SERIES:
        step = 0
    stem = f"{case_id}__{int(step)}" if step is not None else case_id
    return _group_dir(snap_dir, group) / f"{stem}{_SUFFIX}"


def has_record(snap_dir: Path, group: str, case_id: str, step: int | None = None) -> bool:
    return record_path(snap_dir, group, case_id, step).is_file()


def read_record(snap_dir: Path, group: str, case_id: str,
                step: int | None = None) -> dict[str, Any]:
    """One record as its members. Raises `CaptureError` — labelled with the file's name — when
    the file is absent, unreadable, no JSON, or no JSON object. A series is its chunks joined
    in order (`_read_series`)."""
    if group == SERIES:
        return _read_series(snap_dir, case_id)
    return _read_file(record_path(snap_dir, group, case_id, step))


def _read_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CaptureError(path.name, "absent", absent=True)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CaptureError(path.name, f"unreadable: {exc}") from None
    if not isinstance(doc, dict):
        raise CaptureError(path.name, "not a JSON object")
    return doc


def _series_chunks(snap_dir: Path, case_id: str) -> list[Path]:
    """The chunk files of one case's series, in the order written."""
    return [_group_dir(snap_dir, SERIES) / f"{case_id}__{i}{_SUFFIX}"
            for i in _steps(snap_dir, SERIES, case_id)]


def _read_series(snap_dir: Path, case_id: str) -> dict[str, Any]:
    """The series of one case: each member the lists of its chunks joined in the order written.
    Refused beyond what one file is: a chunk missing before a later one (the counts are not
    0, 1, …), a member that is no list, and a chunk whose members are not the first one's — a
    series cut in the middle is not read as a shorter one."""
    chunks = _series_chunks(snap_dir, case_id)
    if not chunks:
        raise CaptureError(record_path(snap_dir, SERIES, case_id).name, "absent", absent=True)
    joined: dict[str, Any] = {}
    for i, path in enumerate(chunks):
        if path.name != f"{case_id}__{i}{_SUFFIX}":
            raise CaptureError(f"{case_id}__{i}{_SUFFIX}", "absent", absent=True)
        doc = _read_file(path)
        if any(not isinstance(v, list) for v in doc.values()):
            raise CaptureError(path.name, "not an object of lists")
        if i and set(doc) != set(joined):
            raise CaptureError(path.name, f"unreadable: it holds {sorted(doc)}, and the "
                               f"chunk before it {sorted(joined)}")
        for name, rows in doc.items():
            joined.setdefault(name, []).extend(rows)
    return joined


def _files(directory: Path) -> list[Path]:
    """The capture files directly in ``directory``, sorted by name; the snapshot schema is none."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob(f"*{_SUFFIX}")
                  if p.is_file() and p.name != SNAPSHOT_SCHEMA_FILENAME)


def case_ids(snap_dir: Path, group: str = FINAL) -> list[str]:
    """The ids that have a record of `group` (final, initial or series), in file-name order:
    every capture file of the final or initial directory is one, whatever its name, and every
    first chunk of the series directory."""
    if group in _STEPPED:
        raise ValueError(f"a {group} record's name does not determine its case; ask "
                         "the steps of a case")
    stems = [p.name[:-len(_SUFFIX)] for p in _files(_group_dir(snap_dir, group))]
    if group == SERIES:
        return [stem[:-len("__0")] for stem in stems if stem.endswith("__0")]
    return stems


def _steps(snap_dir: Path, group: str, case_id: str) -> list[int]:
    steps: list[int] = []
    for path in _files(_group_dir(snap_dir, group)):
        match = _SAMPLE.fullmatch(path.name[:-len(_SUFFIX)])
        if match and match.group("case") == case_id:
            steps.append(int(match.group("step")))
    return sorted(steps)


def history_steps(snap_dir: Path, case_id: str) -> list[int]:
    """The steps `case_id` has a sample at, ascending."""
    return _steps(snap_dir, HISTORY, case_id)


def restart_point_steps(snap_dir: Path, case_id: str) -> list[int]:
    """The steps `case_id` has a restart point at, ascending."""
    return _steps(snap_dir, RESTART_POINT, case_id)


def case_paths(snap_dir: Path, case_id: str) -> list[Path]:
    """Every file of one case that exists: its final and initial records, its series chunks,
    its samples and its restart points."""
    single = [record_path(snap_dir, group, case_id) for group in (FINAL, INITIAL)]
    return [p for p in single if p.is_file()] + _series_chunks(snap_dir, case_id) + [
        record_path(snap_dir, group, case_id, step)
        for group in _STEPPED for step in _steps(snap_dir, group, case_id)]


def all_case_paths(snap_dir: Path) -> list[Path]:
    """Every file under the capture directory with this format's suffix, at any depth — the
    snapshot schema among them, which shares the suffix (`capture_store.capture_paths` drops
    it). A file of a case no IR declares is one too."""
    snap_dir = Path(snap_dir)
    if not snap_dir.is_dir():
        return []
    return sorted(p for p in snap_dir.rglob(f"*{_SUFFIX}") if p.is_file())


def copy_run(src_dir: Path, dst_dir: Path) -> list[str]:
    """Copy a run's captures from one capture directory into another (`dst_dir` exists): the
    files directly in it, then those of the group directories (`run_dirs`), and no other —
    a runner's file elsewhere is dropped. A final record overwrites the one of its name; the
    destination's earlier files of those directories are removed first, since a sample of
    another step would otherwise be read as this run's. Returns the names copied, relative to
    the capture directory, in that order."""
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    copied: list[str] = []
    for f in sorted(src_dir.glob(f"*{_SUFFIX}")):
        shutil.copy2(f, dst_dir / f.name)
        copied.append(f.name)
    for sub in run_dirs():
        source = src_dir / sub
        files = sorted(source.glob(f"*{_SUFFIX}")) if source.is_dir() else []
        for stale in (dst_dir / sub).glob(f"*{_SUFFIX}") if (dst_dir / sub).is_dir() else ():
            stale.unlink()
        if files:
            (dst_dir / sub).mkdir(exist_ok=True)
        for f in files:
            shutil.copy2(f, dst_dir / sub / f.name)
            copied.append(f"{sub}/{f.name}")
    return copied


def move_case(src_dir: Path, dst_dir: Path, case_id: str) -> str | None:
    """Move every file of `case_id` (`case_paths`) from one capture directory into another, and
    no other file. A name that exists there is refused: None when every file moved, else the
    reason, and nothing is moved."""
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    moves: list[tuple[Path, Path]] = []
    for f in case_paths(src_dir, case_id):
        rel = f.relative_to(src_dir)
        if (dst_dir / rel).exists():
            return (f"its capture {f.name} names a file the first pass wrote "
                    f"({rel.as_posix()})")
        moves.append((f, dst_dir / rel))
    for f, dest in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(dest))
    return None


def locate(path: Path, case_id: str) -> tuple[Path, str, int | None] | None:
    """`(capture directory, group, step)` of a file that is a final record or a sample of
    `case_id` by its place and name, else None. The step is None for a final record."""
    path = Path(path)
    if path.name == f"{case_id}{_SUFFIX}":
        return path.parent, FINAL, None
    match = _SAMPLE.fullmatch(path.name[:-len(_SUFFIX)]) if path.name.endswith(_SUFFIX) else None
    if match and match.group("case") == case_id and path.parent.name == _GROUP_DIR[HISTORY]:
        return path.parent.parent, HISTORY, int(match.group("step"))
    return None


def expected_layout(case_id: str = "<case_id>") -> dict[str, str]:
    """The name of each once-per-case file relative to the capture directory, by group — what a
    message tells a writer to produce."""
    return {group: record_path(Path(), group, case_id).as_posix()
            for group in (FINAL, INITIAL, SERIES)}
