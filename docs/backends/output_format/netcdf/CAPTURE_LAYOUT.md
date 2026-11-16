# Capture layout of the `netcdf` output format

Where a harness whose output format is `netcdf` puts each record its capture operations commit.
`docs/workflow/RUNNER_OUTPUT_CONTRACT.md` §3 states what a capture group holds; this document
states only where this format puts it. The host seeds it after that contract wherever the
contract is seeded whole, and reads a run's captures by exactly this layout.

One file per case, `<C>.nc` directly under `raw/state_snapshots/` of the run directory (`<C>` the
case id), opened by a path relative to the working directory: the netCDF-4 kind (HDF5 storage),
no compression, no groups. Every dimension and variable is defined when the case's output is
opened, every group's included whether or not the case commits a record of it; the file is
complete only when ending the case's output has set `complete` (below).

**Names.** `<v>` is a snapshot variable's name, `<s>` a series name, `<k>` an axis index from 1.
No name contains `__` (the host refuses one), so each name below has one reading.

**Dimensions.** A variable `<v>` of rank `r` has the dimensions `<v>__d1` … `<v>__d<r>`, the
extent of its index `<k>` each, shared by every group that holds `<v>`. An extent of 0 is declared
unlimited and never written, so it stays 0. Each stacked group below adds its own unlimited
record dimension.

**Dimension order.** In netCDF's own order (the first dimension varies slowest) an array is
`(<v>__d<r>, …, <v>__d1)`, so its first index varies fastest; a record dimension comes first. An
interface that lists dimensions fastest-first (a column-major binding) lists the same variable
as `(<v>__d1, …, <v>__d<r>)`, the record dimension last. The host reads the dimension names and
refuses a variable whose dimensions are not exactly these, in this order.

| group | variables (netCDF order) |
|---|---|
| `initial` | `initial__<v>(<v>__d<r>, …, <v>__d1)` per variable put; `initial__step`, `initial__t`, `initial__count` (scalars) |
| `final` | the same with `final__` |
| `history` | per sampled variable: the record dimension `history__<v>__n`, `history__<v>(history__<v>__n, <v>__d<r>, …, <v>__d1)`, `history__<v>__step(history__<v>__n)`, `history__<v>__t(history__<v>__n)`, `history__<v>__count` (scalar) |
| `restart_point` | the record dimension `restart_point__n`; `restart_point__<v>(restart_point__n, <v>__d<r>, …, <v>__d1)` per restart variable; `restart_point__step(restart_point__n)`, `restart_point__t(restart_point__n)`, `restart_point__count` (scalar) |
| `series` | the record dimension `series__n`; `series__<s>(series__n)` per series name, `series__step(series__n)`, `series__t(series__n)`, `series__count` (scalar) |

- **Types.** A value, `t` and every series column are 64-bit reals (`double`); a `step` and a
  `count` are 32-bit integers (`int`); a rank-0 value is a scalar variable. The host refuses a
  variable of another type; the byte order is free.
- **Fill values.** Every `double` variable is filled with NaN (the attribute `_FillValue` =
  NaN, fill mode on: no `nf90_set_fill(…, NF90_NOFILL, …)`), so a cell no commit wrote reads
  non-finite. `complete` is a scalar `int` with `_FillValue` = 0.
- **Counts.** Opening the case's output sets every `count` to 0. A commit writes its record's values at the next index of its group's record
  dimension (`initial` and `final` hold at most one record and have none), then its `step` and
  `t`, then sets the group's `count` to the records it now holds. A group's `count` equals the
  length of its record dimension; `initial__count` and `final__count` are 0 or 1. Each
  `history__<v>__step` and `restart_point__step` is strictly ascending.
- **A history record** at step `<k>` is every `history__<v>` whose `history__<v>__step` holds
  `<k>`, and its `history__<v>__t` there is the same for each.
- **Ending the case's output** writes everything held to disk (`nf90_sync` / `nc_sync`), then
  sets `complete` to 1, then closes the file. The host reads a file only when `complete` is
  exactly 1, and refuses the whole case otherwise: nothing a case committed is evidence before
  its output ended.
- No directory below `raw/state_snapshots/` is used; the host creates that directory before the
  program runs, and the harness creates none.
- `snapshot_schema.json` in the same directory is the host's, not a capture.
