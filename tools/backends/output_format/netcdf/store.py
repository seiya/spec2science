#!/usr/bin/env python3
"""The `capture_store` of the `netcdf` output format (issue #644, Part 3 P5): one netCDF-4 file
per case under a run's capture directory (`tools/capture_store.py`, which every reader reaches
this module through). `docs/backends/output_format/netcdf/CAPTURE_LAYOUT.md` states the layout as
a harness's author reads it; this module reads exactly that.

`<C>.nc` holds every record of case `<C>`. The file counts only when its writer ended the case's
output: `complete` is exactly 1 (issue #644 O12, measurements E11c / E11d — under `SIGKILL` a
netCDF-4 file's counts and data are not flushed in write order, so nothing short of the marker
written after a synchronization shows that what the file holds is what was committed). A file
that does not open or is not complete refuses every record of its case; nothing is read from
it, and a killed case keeps nothing (`read_series_prefix`).

A record is returned in the value domain every store returns (`tools/capture_store.py`): the
members as `json` would decode them — Python `float` / `int` and nested lists in the declared
index order (the file holds the reverse, `_value`), never an array. Values are read with
auto-masking off, so a cell equal to a fill value reads as that value and an unwritten cell as
the NaN every `double` variable's `_FillValue` must be: the neutral readers' finiteness check
then refuses it.

What is refused beyond a file that does not open or is not complete — each a defect a writer
can make that would otherwise read as a valid record: a `double` variable whose `_FillValue` is
not NaN; a group whose `count` is not the length of its record dimension (or, for `initial` and
`final`, is not 0 or 1); a variable whose dimensions are not the layout's names in the layout's
order (an array written with its dimensions unreversed among them); a history variable whose
steps are not strictly ascending, or whose times disagree at one step between variables.

Every reader opens the file per call; nothing is cached, so a file replaced between two calls
is read as it is now. `netCDF4` is imported where a file is opened, never at import: the host's
startup check reads `REQUIRED_PYTHON_MODULES` from this module on a host that may lack it.
"""

from __future__ import annotations

import math
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from tools.capture_store import (
    FINAL,
    HISTORY,
    INITIAL,
    RESTART_POINT,
    SERIES,
    CaptureError,
)

#: The suffix of this format's capture files.
_SUFFIX = ".nc"

#: The Python modules reading this format needs beyond the standard library: `(import name,
#: distribution)`, required at startup on every host (`tools/host_prerequisites.py`).
REQUIRED_PYTHON_MODULES: tuple[tuple[str, str], ...] = (("netCDF4", "netCDF4"),)

#: The reader's Python binding at the version the layout's measurements (issue #644 E2-E11d)
#: were taken on — it bundles netCDF-C 4.9.3; the writer side was netCDF-Fortran 4.5.4 over
#: netCDF-C 4.8.1. `requirements.txt` pins the binding to it and `docs/RUNBOOK.md` §0-1 states
#: it (`test_dependency_declaration.MeasuredVersionTests`).
MEASURED_PACKAGE_VERSIONS: dict[str, str] = {"netCDF4": "1.7.3"}

#: The separator the layout joins a group, a variable and a role with.
_SEP = "__"
#: The marker ending the case's output sets to exactly this.
_COMPLETE = "complete"
_COMPLETE_VALUE = 1
#: The groups with at most one record, and the stacked ones.
_SINGLE = (INITIAL, FINAL)
#: The roles a group's own variables take beside its values.
_ROLES = ("step", "t", "count")
#: A spatial dimension's name: `<v>__d<k>`.
_SPATIAL = re.compile(r"(?P<var>.+)__d(?P<axis>[1-9][0-9]*)")

#: This format's capture layout as a harness's author reads it: seeded after the runner-output
#: contract wherever that document is seeded whole.
CAPTURE_LAYOUT_DOCUMENT = (Path(__file__).resolve().parents[4]
                           / "docs" / "backends" / "output_format" / "netcdf" / "CAPTURE_LAYOUT.md")


def capture_layout_document() -> str:
    """The capture layout, whole. Raises `OSError` / `UnicodeError` when it cannot be read; the
    caller turns that into a named fail-closed outcome."""
    return CAPTURE_LAYOUT_DOCUMENT.read_text(encoding="utf-8")


def run_dirs() -> tuple[str, ...]:
    """The directories below the capture directory a run needs created before it starts: the
    capture directory itself (`""`), where every case's file sits; the harness creates none."""
    return ("",)


def _case_file(snap_dir: Path, case_id: str) -> Path:
    return Path(snap_dir) / f"{case_id}{_SUFFIX}"


def record_path(snap_dir: Path, group: str, case_id: str, step: int | None = None) -> Path:
    """The file holding one record: the case's file, for every group. A sample and a restart
    point are named with their step, no other record is."""
    stepped = group in (HISTORY, RESTART_POINT)
    if stepped != (step is not None):
        raise ValueError(f"a {group} record is named {'with' if stepped else 'without'}"
                         " a step")
    return _case_file(snap_dir, case_id)


# ----------------------------------------------------------------------------- the file


class _Case:
    """One opened, complete and well-formed case file: what it holds, read on demand."""

    def __init__(self, ds: Any, label: str) -> None:
        self.ds = ds
        self.label = label

    def refuse(self, reason: str) -> CaptureError:
        return CaptureError(self.label, f"unreadable: {reason}")

    def scalar(self, name: str) -> Any:
        return self.ds.variables[name][...].item()

    def count(self, group: str, var: str | None = None) -> int:
        prefix = _SEP.join(p for p in (group, var) if p)
        return int(self.scalar(f"{prefix}{_SEP}count"))

    def values_of(self, group: str) -> list[str]:
        """The variables a group holds values of (initial, final, restart point, series): the
        names after `<group>__` that are no role."""
        out = []
        for name in self.ds.variables:
            head, sep, rest = name.partition(_SEP)
            if head == group and sep and _SEP not in rest and rest not in _ROLES:
                out.append(rest)
        return sorted(out)

    def history_vars(self) -> list[str]:
        """The sampled variables: those with a `history__<v>__count`."""
        out = []
        for name in self.ds.variables:
            parts = name.split(_SEP)
            if len(parts) == 3 and parts[0] == HISTORY and parts[2] == "count":
                out.append(parts[1])
        return sorted(out)

    def steps(self, group: str, var: str | None = None) -> list[int]:
        prefix = _SEP.join(p for p in (group, var) if p)
        n = self.count(group, var)
        return [int(x) for x in self.ds.variables[f"{prefix}{_SEP}step"][:n].tolist()]

    def column(self, name: str, n: int) -> list[float]:
        return [float(x) for x in self.ds.variables[name][:n].tolist()]

    def value(self, name: str, index: int | None = None) -> Any:
        """A value variable's record, in declared index order (the file holds it reversed)."""
        var = self.ds.variables[name]
        raw = var[...] if index is None else var[index]
        import numpy as np
        arr = np.asarray(raw)
        if arr.ndim == 0:
            return float(arr.item())
        return arr.transpose().tolist()


def _well_formed(case: _Case) -> None:
    """Refuse what makes the file's records ambiguous or a missing cell read as a value
    (module docstring): fill values, counts, dimension names and order, history steps."""
    import numpy as np
    ds = case.ds
    for name, var in ds.variables.items():
        if var.dtype == np.float64:
            fill = getattr(var, "_FillValue", None)
            if fill is None or not (isinstance(fill, (float, np.floating)) and math.isnan(fill)):
                raise case.refuse(f"variable {name} has _FillValue {fill!r}, not NaN")
    record_dims = {RESTART_POINT: f"{RESTART_POINT}{_SEP}n", SERIES: f"{SERIES}{_SEP}n"}
    for group in (INITIAL, FINAL, RESTART_POINT, SERIES):
        if f"{group}{_SEP}count" not in ds.variables:
            raise case.refuse(f"it has no {group}{_SEP}count")
        count = case.count(group)
        if group in _SINGLE:
            if count not in (0, 1):
                raise case.refuse(f"{group}{_SEP}count is {count}, not 0 or 1")
        else:
            dim = record_dims[group]
            length = len(ds.dimensions[dim]) if dim in ds.dimensions else None
            if length != count:
                raise case.refuse(f"{group}{_SEP}count is {count}, and its record dimension "
                                  f"{dim} has length {length}")
    for var in case.history_vars():
        dim = f"{HISTORY}{_SEP}{var}{_SEP}n"
        count = case.count(HISTORY, var)
        length = len(ds.dimensions[dim]) if dim in ds.dimensions else None
        if length != count:
            raise case.refuse(f"{HISTORY}{_SEP}{var}{_SEP}count is {count}, and its record "
                              f"dimension {dim} has length {length}")
    # A sample and a restart point are read by their step: a repeated step names two records.
    for group, var in [(HISTORY, v) for v in case.history_vars()] + [(RESTART_POINT, None)]:
        steps = case.steps(group, var)
        if any(b <= a for a, b in zip(steps, steps[1:])):
            name = _SEP.join(p for p in (group, var, "step") if p)
            raise case.refuse(f"{name} {steps} is not strictly ascending")
    for name, var in ds.variables.items():
        want = _expected_dims(name, var.dimensions)
        if want is not None and tuple(var.dimensions) != want:
            raise case.refuse(f"variable {name} has dimensions {tuple(var.dimensions)}, the "
                              f"layout {want}")


def _expected_dims(name: str, dims: tuple[str, ...]) -> tuple[str, ...] | None:
    """The dimensions the layout gives ``name`` — a value's spatial rank taken from the
    dimensions it was written with — or None for a name outside the layout, which no reader
    takes."""
    parts = name.split(_SEP)
    group = parts[0]
    if name == _COMPLETE:
        return ()
    if group == HISTORY and len(parts) == 2:
        lead, var = (f"{HISTORY}{_SEP}{parts[1]}{_SEP}n",), parts[1]
    elif group == HISTORY and len(parts) == 3 and parts[2] in _ROLES:
        return () if parts[2] == "count" else (f"{HISTORY}{_SEP}{parts[1]}{_SEP}n",)
    elif len(parts) != 2 or group not in (INITIAL, FINAL, RESTART_POINT, SERIES):
        return None
    elif parts[1] == "count" or (group in _SINGLE and parts[1] in _ROLES):
        return ()
    elif group == SERIES:
        return (f"{SERIES}{_SEP}n",)
    elif group == RESTART_POINT and parts[1] in _ROLES:
        return (f"{RESTART_POINT}{_SEP}n",)
    else:
        lead = (f"{RESTART_POINT}{_SEP}n",) if group == RESTART_POINT else ()
        var = parts[1]
    rank = max(len(dims) - len(lead), 0)
    return (*lead, *(f"{var}{_SEP}d{k}" for k in range(rank, 0, -1)))


@contextmanager
def _open(path: Path) -> Iterator[_Case]:
    """The case file, opened, complete and well formed; `CaptureError` otherwise."""
    if not path.is_file():
        raise CaptureError(path.name, "absent", absent=True)
    import netCDF4
    try:
        ds = netCDF4.Dataset(str(path), "r")
    except (OSError, RuntimeError, ValueError) as exc:
        raise CaptureError(path.name, f"unreadable: {exc}") from None
    try:
        ds.set_auto_mask(False)
        case = _Case(ds, path.name)
        try:
            marker = ds.variables[_COMPLETE][...].item() if _COMPLETE in ds.variables else None
        except (OSError, RuntimeError, ValueError, IndexError) as exc:
            raise case.refuse(f"{_COMPLETE} cannot be read ({exc})") from None
        if marker != _COMPLETE_VALUE:
            raise CaptureError(path.name, f"incomplete: {_COMPLETE} is {marker!r}, not "
                               f"{_COMPLETE_VALUE} (the case's output did not end)")
        try:
            _well_formed(case)
        except CaptureError:
            raise
        except (KeyError, OSError, RuntimeError, ValueError, IndexError) as exc:
            raise case.refuse(f"{type(exc).__name__}: {exc}") from None
        yield case
    finally:
        ds.close()


def _with_record(read):
    """Turn a decoding error inside an opened file into the store's refusal."""
    def wrapped(case: _Case, *args: Any) -> Any:
        try:
            return read(case, *args)
        except CaptureError:
            raise
        except (KeyError, OSError, RuntimeError, ValueError, IndexError) as exc:
            raise case.refuse(f"{type(exc).__name__}: {exc}") from None
    return wrapped


@_with_record
def _single(case: _Case, group: str) -> dict[str, Any]:
    if case.count(group) != 1:
        raise CaptureError(f"{case.label} ({group})", "absent", absent=True)
    doc = {v: case.value(f"{group}{_SEP}{v}") for v in case.values_of(group)}
    doc["step"] = int(case.scalar(f"{group}{_SEP}step"))
    doc["t"] = float(case.scalar(f"{group}{_SEP}t"))
    return doc


@_with_record
def _history(case: _Case, step: int) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    times: list[float] = []
    for var in case.history_vars():
        steps = case.steps(HISTORY, var)
        if step in steps:
            i = steps.index(step)
            doc[var] = case.value(f"{HISTORY}{_SEP}{var}", i)
            times.append(float(case.ds.variables[f"{HISTORY}{_SEP}{var}{_SEP}t"][i].item()))
    if not doc:
        raise CaptureError(f"{case.label} ({HISTORY} at step {step})", "absent", absent=True)
    if any(not _same(times[0], t) for t in times[1:]):
        raise case.refuse(f"its history variables at step {step} carry different t {times}")
    doc["step"] = int(step)
    doc["t"] = times[0]
    return doc


def _same(a: float, b: float) -> bool:
    return a == b or (math.isnan(a) and math.isnan(b))


@_with_record
def _restart_point(case: _Case, step: int) -> dict[str, Any]:
    steps = case.steps(RESTART_POINT)
    if step not in steps:
        raise CaptureError(f"{case.label} ({RESTART_POINT} at step {step})", "absent",
                           absent=True)
    i = steps.index(step)
    doc = {v: case.value(f"{RESTART_POINT}{_SEP}{v}", i) for v in case.values_of(RESTART_POINT)}
    doc["step"] = int(step)
    doc["t"] = float(case.ds.variables[f"{RESTART_POINT}{_SEP}t"][i].item())
    return doc


@_with_record
def _series(case: _Case) -> dict[str, Any]:
    n = case.count(SERIES)
    doc = {s: case.column(f"{SERIES}{_SEP}{s}", n) for s in case.values_of(SERIES)}
    doc["step"] = [int(x) for x in case.steps(SERIES)]
    doc["t"] = case.column(f"{SERIES}{_SEP}t", n)
    return doc


def read_record(snap_dir: Path, group: str, case_id: str,
                step: int | None = None) -> dict[str, Any]:
    """One record as its members. Raises `CaptureError` — labelled with the case file's name —
    when the file is absent, does not open, is not complete or not well formed (module
    docstring), or holds no such record (`absent`)."""
    path = record_path(snap_dir, group, case_id, step)
    with _open(path) as case:
        if group in _SINGLE:
            return _single(case, group)
        if group == HISTORY:
            return _history(case, int(step))
        if group == RESTART_POINT:
            return _restart_point(case, int(step))
        if group == SERIES:
            return _series(case)
    raise ValueError(f"no capture group {group!r}")


def has_record(snap_dir: Path, group: str, case_id: str, step: int | None = None) -> bool:
    """Whether the case's file holds a record of ``group``: True for a file that cannot be read
    as complete, so the caller reads it and is refused rather than taking the record as never
    written."""
    path = record_path(snap_dir, group, case_id, step)
    if not path.is_file():
        return False
    try:
        with _open(path) as case:
            if group in _SINGLE:
                return case.count(group) == 1
            if group == SERIES:
                return True
            if group == HISTORY:
                return any(int(step) in case.steps(HISTORY, v) for v in case.history_vars())
            return int(step) in case.steps(RESTART_POINT)
    except (CaptureError, KeyError, OSError, RuntimeError, ValueError, IndexError):
        return True


def _files(snap_dir: Path) -> list[Path]:
    snap_dir = Path(snap_dir)
    if not snap_dir.is_dir():
        return []
    return sorted(p for p in snap_dir.glob(f"*{_SUFFIX}") if p.is_file())


def case_ids(snap_dir: Path, group: str = FINAL) -> list[str]:
    """The ids that have a record of `group` (final, initial or series), in file-name order: a
    case file holding one, and every case file that cannot be read as complete (its reader
    refuses it)."""
    if group in (HISTORY, RESTART_POINT):
        raise ValueError(f"a {group} record's name does not determine its case; ask "
                         "the steps of a case")
    stems = [p.name[:-len(_SUFFIX)] for p in _files(snap_dir)]
    return [c for c in stems if has_record(snap_dir, group, c)]


def _stepped(snap_dir: Path, case_id: str, group: str) -> list[int]:
    """The steps a complete case file holds records of `group` at, ascending; none for a file
    that is absent or cannot be read as complete (its final record's reader refuses the
    case)."""
    path = _case_file(snap_dir, case_id)
    if not path.is_file():
        return []
    try:
        with _open(path) as case:
            if group == RESTART_POINT:
                return sorted(set(case.steps(RESTART_POINT)))
            return sorted({s for v in case.history_vars() for s in case.steps(HISTORY, v)})
    except (CaptureError, KeyError, OSError, RuntimeError, ValueError, IndexError):
        return []


def history_steps(snap_dir: Path, case_id: str) -> list[int]:
    """The steps `case_id` has a sample at, ascending."""
    return _stepped(snap_dir, case_id, HISTORY)


def restart_point_steps(snap_dir: Path, case_id: str) -> list[int]:
    """The steps `case_id` has a restart point at, ascending."""
    return _stepped(snap_dir, case_id, RESTART_POINT)


def read_series_prefix(snap_dir: Path, case_id: str) -> tuple[dict[str, Any], int | None]:
    """What a case that did not finish kept of its series: nothing, since a file whose output
    did not end is refused whole (issue #644 O12) — and the whole series of a complete file."""
    try:
        doc = read_record(snap_dir, SERIES, case_id)
    except CaptureError:
        return {}, None
    steps = doc.get("step") or []
    return doc, (steps[-1] if steps else None)


def case_paths(snap_dir: Path, case_id: str) -> list[Path]:
    """Every file of one case that exists: its one case file."""
    path = _case_file(snap_dir, case_id)
    return [path] if path.is_file() else []


def all_case_paths(snap_dir: Path) -> list[Path]:
    """Every file under the capture directory with this format's suffix, at any depth. A file
    of a case no IR declares is one too."""
    snap_dir = Path(snap_dir)
    if not snap_dir.is_dir():
        return []
    return sorted(p for p in snap_dir.rglob(f"*{_SUFFIX}") if p.is_file())


def copy_run(src_dir: Path, dst_dir: Path) -> list[str]:
    """Copy a run's captures from one capture directory into another (`dst_dir` exists): the
    case files directly in it, and no other — a runner's file elsewhere is dropped. A case file
    overwrites the one of its name. Returns the names copied, in name order."""
    copied: list[str] = []
    for f in _files(src_dir):
        shutil.copy2(f, Path(dst_dir) / f.name)
        copied.append(f.name)
    return copied


def move_case(src_dir: Path, dst_dir: Path, case_id: str) -> str | None:
    """Move the file of `case_id` from one capture directory into another. A name that exists
    there is refused: None when it moved (or there was none), else the reason, and nothing is
    moved."""
    moves = []
    for f in case_paths(src_dir, case_id):
        dest = Path(dst_dir) / f.name
        if dest.exists():
            return f"its capture {f.name} names a file the first pass wrote ({f.name})"
        moves.append((f, dest))
    for f, dest in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(dest))
    return None


def locate(path: Path, case_id: str) -> tuple[Path, str, int | None] | None:
    """`(capture directory, group, step)` of the case file of `case_id` — its final record,
    which holds every restart variable — else None. A restart point inside the file is not
    named by a path (`TODO.md`: a restart from one needs the step given)."""
    path = Path(path)
    if path.name == f"{case_id}{_SUFFIX}":
        return path.parent, FINAL, None
    return None


def expected_layout(case_id: str = "<case_id>") -> dict[str, str]:
    """The name of each once-per-case file relative to the capture directory, by group — what a
    message tells a writer to produce: the case file for each."""
    name = f"{case_id}{_SUFFIX}"
    return {FINAL: name, INITIAL: name, SERIES: name}
