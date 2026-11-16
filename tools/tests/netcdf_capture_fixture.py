"""A writer of `netcdf` capture files for tests (issue #644, Part 3 P5), in the layout of
`docs/backends/output_format/netcdf/CAPTURE_LAYOUT.md`, as a column-major harness would leave
them: every value is given in its declared index order and written with its dimensions reversed.

`defects` seeds one writer defect each, by name (`DEFECTS`), so a row can show a reader refusing
what a defective harness would leave. Written with netCDF4-python, the reader's own binding; a
Fortran writer is the harness side's (P5b).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

SEP = "__"

#: The writer defects a row may seed.
DEFECTS = frozenset({
    "complete_unset",        # `complete` left at its fill value 0: the output never ended
    "complete_two",          # `complete` set to a value other than 1
    "no_complete",           # no `complete` variable at all
    "dims_unreversed",       # an array's dimensions defined in declared (not reversed) order
    "fill_not_nan",          # a double variable's _FillValue is 0, not NaN
    "no_fill",               # a double variable carries no _FillValue
    "count_short",           # a stacked group's count one less than its record dimension
    "final_count_two",       # final__count is 2
    "history_steps_repeat",  # a history variable's step column repeats a step
    "rp_steps_repeat",       # the restart points' step column repeats a step
    "rp_count_short",        # restart_point__count one less than its record dimension
    "series_count_short",    # series__count one less than its record dimension
    "history_t_disagree",    # two variables' history t differ at one step
    "unwritten_cell",        # a final value's last cell is never written (reads its fill)
    "nofill_mode",           # the file is written in no-fill mode (the attribute stays NaN)
    "value_float32",         # every value variable is single precision
    "step_double",           # the step columns are reals
    "t_fill_not_nan",        # only the `t` variables' _FillValue is 0
    "history_step_own_dim",  # history__<v>__step on a dimension of its own
    "rp_t_own_dim",          # restart_point__t on a dimension of its own
    "series_step_own_dim",   # series__step on a dimension of its own
    "series_float32",        # the series columns are single precision
    "series_column_own_dim", # each series column on a dimension of its own
    "complete_double",       # `complete` is a real (fill NaN) set to 1.0
    "not_netcdf",            # the file is not a netCDF file at all
})


def _dims(var: str, shape: tuple[int, ...]) -> tuple[str, ...]:
    """The spatial dimensions of ``var`` in netCDF order: `<v>__d<r>` … `<v>__d1`."""
    return tuple(f"{var}{SEP}d{k}" for k in range(len(shape), 0, -1))


def write_case(path: Path, *, shapes: Mapping[str, tuple[int, ...]],
               initial: Mapping[str, Any] | None = None, initial_step: int = 0,
               initial_t: float = 0.0,
               final: Mapping[str, Any] | None = None, final_step: int = 0,
               final_t: float = 0.0,
               history: Mapping[str, Iterable[tuple[int, float, Any]]] | None = None,
               restart_vars: Iterable[str] = (),
               restart_points: Iterable[tuple[int, float, Mapping[str, Any]]] = (),
               series_names: Iterable[str] = (),
               series: Iterable[tuple[int, float, Mapping[str, float]]] = (),
               defects: Iterable[str] = (),
               overrides: Mapping[str, tuple[str, Any]] | None = None) -> Path:
    """Write one case file and return its path. ``shapes`` declares every variable's extents in
    declared index order (`()` for a scalar). ``overrides`` defines one variable, by name, with
    another `(type, fill value)` than the layout's — a defect of that variable alone."""
    import netCDF4

    defects = set(defects)
    unknown = defects - DEFECTS
    if unknown:
        raise ValueError(f"unknown defects {sorted(unknown)}")
    path = Path(path)
    if "not_netcdf" in defects:
        path.write_text("not a netCDF file\n", encoding="utf-8")
        return path
    history = {v: list(rows) for v, rows in (history or {}).items()}
    restart_vars = list(restart_vars)
    restart_points = list(restart_points)
    series_names = list(series_names)
    series = list(series)
    nan = float("nan")
    fill = 0.0 if "fill_not_nan" in defects else nan
    overrides = dict(overrides or {})

    ds = netCDF4.Dataset(str(path), "w", format="NETCDF4")
    try:
        if "nofill_mode" in defects:
            ds.set_fill_off()

        def dvar(name: str, dims: tuple[str, ...], kind: str = "f8") -> Any:
            if name in overrides:
                other, other_fill = overrides.pop(name)
                endian = "big" if other.startswith(">") else "native"
                return ds.createVariable(name, np.dtype(other), dims, fill_value=other_fill,
                                         endian=endian)
            if "no_fill" in defects:
                return ds.createVariable(name, kind, dims)
            role_t = name.endswith(f"{SEP}t")
            this_fill = 0.0 if "t_fill_not_nan" in defects and role_t else fill
            return ds.createVariable(name, kind, dims, fill_value=this_fill)

        def vvar(name: str, dims: tuple[str, ...]) -> Any:
            """A value variable."""
            return dvar(name, dims, "f4" if "value_float32" in defects else "f8")

        def ivar(name: str, dims: tuple[str, ...] = ()) -> Any:
            return ds.createVariable(name, "i4", dims)

        def svar(name: str, dims: tuple[str, ...], own: str) -> Any:
            """A step column, on a dimension of its own under defect ``own``."""
            if own in defects:
                ds.createDimension(f"{name}{SEP}own", None)
                dims = (f"{name}{SEP}own",)
            if "step_double" in defects:
                return ds.createVariable(name, "f8", dims, fill_value=nan)
            return ivar(name, dims)

        for var, shape in shapes.items():
            for k, extent in enumerate(shape, start=1):
                ds.createDimension(f"{var}{SEP}d{k}", extent if extent else None)

        def array_dims(var: str) -> tuple[str, ...]:
            dims = _dims(var, tuple(shapes[var]))
            return tuple(reversed(dims)) if "dims_unreversed" in defects else dims

        def put(target: Any, value: Any, index: int | None = None) -> None:
            arr = np.asarray(value, dtype=np.float64)
            data = arr if "dims_unreversed" in defects else arr.transpose()
            if index is None:
                target[...] = data
            else:
                target[index] = data

        for group, doc, step, t in ((("initial", initial, initial_step, initial_t)),
                                    (("final", final, final_step, final_t))):
            for var in shapes:
                vvar(f"{group}{SEP}{var}", array_dims(var))
            ivar(f"{group}{SEP}step")
            dvar(f"{group}{SEP}t", ())
            ivar(f"{group}{SEP}count")
            count = 0
            if doc is not None:
                for var, value in doc.items():
                    target = ds.variables[f"{group}{SEP}{var}"]
                    if group == "final" and "unwritten_cell" in defects and np.ndim(value):
                        arr = np.array(value, dtype=np.float64)
                        flat = arr.reshape(-1)
                        put(target, arr)
                        if flat.size:
                            idx = np.unravel_index(flat.size - 1, arr.shape)
                            target[tuple(reversed(idx))] = fill
                        continue
                    put(target, value)
                ds.variables[f"{group}{SEP}step"][...] = step
                ds.variables[f"{group}{SEP}t"][...] = t
                count = 1
            if group == "final" and "final_count_two" in defects:
                count = 2
            ds.variables[f"{group}{SEP}count"][...] = count

        for var, rows in history.items():
            dim = f"history{SEP}{var}{SEP}n"
            ds.createDimension(dim, None)
            values = vvar(f"history{SEP}{var}", (dim, *array_dims(var)))
            steps = svar(f"history{SEP}{var}{SEP}step", (dim,), "history_step_own_dim")
            times = dvar(f"history{SEP}{var}{SEP}t", (dim,))
            for i, (step, t, value) in enumerate(rows):
                put(values, value, i)
                steps[i] = step
                times[i] = t
            if "history_steps_repeat" in defects and len(rows) >= 2:
                steps[len(rows) - 1] = rows[-2][0]
            if "history_t_disagree" in defects and rows and var == sorted(history)[-1]:
                times[0] = rows[0][1] + 0.5
            count = len(rows) - (1 if "count_short" in defects and rows else 0)
            ivar(f"history{SEP}{var}{SEP}count")[...] = count

        ds.createDimension(f"restart_point{SEP}n", None)
        rp_dim = (f"restart_point{SEP}n",)
        for var in restart_vars:
            vvar(f"restart_point{SEP}{var}", (*rp_dim, *array_dims(var)))
        rp_step = ivar(f"restart_point{SEP}step", rp_dim)
        if "rp_t_own_dim" in defects:
            ds.createDimension(f"restart_point{SEP}t{SEP}own", None)
            rp_t = dvar(f"restart_point{SEP}t", (f"restart_point{SEP}t{SEP}own",))
        else:
            rp_t = dvar(f"restart_point{SEP}t", rp_dim)
        for i, (step, t, doc) in enumerate(restart_points):
            for var, value in doc.items():
                put(ds.variables[f"restart_point{SEP}{var}"], value, i)
            rp_step[i] = step
            rp_t[i] = t
        if "rp_steps_repeat" in defects and len(restart_points) >= 2:
            rp_step[len(restart_points) - 1] = restart_points[-2][0]
        ivar(f"restart_point{SEP}count")[...] = len(restart_points) - (
            1 if "rp_count_short" in defects and restart_points else 0)

        ds.createDimension(f"series{SEP}n", None)
        s_dim = (f"series{SEP}n",)
        for name in series_names:
            dims = s_dim
            if "series_column_own_dim" in defects:
                dims = (f"series{SEP}{name}{SEP}own",)
                ds.createDimension(dims[0], None)
            dvar(f"series{SEP}{name}", dims, "f4" if "series_float32" in defects else "f8")
        s_step = svar(f"series{SEP}step", s_dim, "series_step_own_dim")
        s_t = dvar(f"series{SEP}t", s_dim)
        for i, (step, t, row) in enumerate(series):
            for name, value in row.items():
                ds.variables[f"series{SEP}{name}"][i] = value
            s_step[i] = step
            s_t[i] = t
        ivar(f"series{SEP}count")[...] = len(series) - (
            1 if "series_count_short" in defects and series else 0)

        if "no_complete" not in defects:
            done = (ds.createVariable("complete", "f8", (), fill_value=nan)
                    if "complete_double" in defects
                    else ds.createVariable("complete", "i4", (), fill_value=0))
            if "complete_two" in defects:
                done[...] = 2
            elif "complete_unset" not in defects:
                ds.sync()
                done[...] = 1
        if overrides:
            raise ValueError(f"no variable {sorted(overrides)} to override")
    finally:
        ds.close()
    return path


def from_json_run(json_snap_dir: Path, nc_snap_dir: Path, *,
                  shapes: Mapping[str, tuple[int, ...]] | None = None,
                  defects: Mapping[str, Iterable[str]] | None = None) -> list[Path]:
    """Write, under ``nc_snap_dir``, the `netcdf` twin of every case the `json` store finds
    under ``json_snap_dir``: the same records, value for value. ``shapes`` overrides the
    extents read off the final record (a zero extent loses its rank in a JSON list);
    ``defects`` seeds writer defects per case id. Returns the files written."""
    from tools import capture_store as cs

    json_store = cs.store("json")
    nc_snap_dir = Path(nc_snap_dir)
    nc_snap_dir.mkdir(parents=True, exist_ok=True)
    written = []
    roles = {"step", "t"}
    for case_id in json_store.case_ids(json_snap_dir):
        final = json_store.read_record(json_snap_dir, cs.FINAL, case_id)
        initial = (json_store.read_record(json_snap_dir, cs.INITIAL, case_id)
                   if json_store.has_record(json_snap_dir, cs.INITIAL, case_id) else None)
        case_shapes = {k: tuple(np.shape(v)) for k, v in final.items() if k not in roles}
        case_shapes.update(shapes or {})
        history: dict[str, list[tuple[int, float, Any]]] = {}
        for step in json_store.history_steps(json_snap_dir, case_id):
            doc = json_store.read_record(json_snap_dir, cs.HISTORY, case_id, step)
            for var, value in doc.items():
                if var not in roles:
                    history.setdefault(var, []).append((step, doc["t"], value))
        points = []
        restart_vars: list[str] = []
        for step in json_store.restart_point_steps(json_snap_dir, case_id):
            doc = json_store.read_record(json_snap_dir, cs.RESTART_POINT, case_id, step)
            values = {k: v for k, v in doc.items() if k not in roles}
            restart_vars = sorted(values)
            points.append((step, doc["t"], values))
        series_names: list[str] = []
        rows: list[tuple[int, float, dict[str, float]]] = []
        if json_store.has_record(json_snap_dir, cs.SERIES, case_id):
            series = json_store.read_record(json_snap_dir, cs.SERIES, case_id)
            series_names = sorted(k for k in series if k not in roles)
            rows = [(series["step"][i], series["t"][i],
                     {n: series[n][i] for n in series_names})
                    for i in range(len(series.get("step", [])))]
        written.append(write_case(
            nc_snap_dir / f"{case_id}.nc", shapes=case_shapes,
            initial={k: v for k, v in initial.items() if k not in roles} if initial else None,
            initial_step=initial["step"] if initial else 0,
            initial_t=initial["t"] if initial else 0.0,
            final={k: v for k, v in final.items() if k not in roles},
            final_step=final["step"], final_t=final["t"], history=history,
            restart_vars=restart_vars, restart_points=points,
            series_names=series_names, series=rows,
            defects=(defects or {}).get(case_id, ())))
    return written
