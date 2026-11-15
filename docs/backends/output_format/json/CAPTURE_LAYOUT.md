# Capture layout of the `json` output format

Where a harness whose output format is `json` puts each record its capture operations commit.
`docs/workflow/RUNNER_OUTPUT_CONTRACT.md` §3 states what a capture group holds; this document
states only where this format puts it. The host seeds it after that contract wherever the
contract is seeded whole, and reads a run's captures by exactly this layout.

Every file is one UTF-8 JSON object under `raw/state_snapshots/` of the run directory, opened by a
path relative to the working directory, written once and never rewritten or appended to. `<C>`
is the case id, `<k>` the committed step and `<i>` a count from 0, each in decimal digits with no
leading zero.

| group | file | content |
|---|---|---|
| `final` | `<C>.json` | every value put, each under its name as a top-level key, then `step` (an integer token) and `t` |
| `initial` | `initial/<C>.json` | the same |
| `history` | `history/<C>__<k>.json` | the same, one file per committed step `<k>` |
| `restart_point` | `restart_point/<C>__<k>.json` | the same |
| `series` | `series/<C>__<i>.json` | `step`, `t` and each series name, each a list with one entry per row, in commit order |

- An array is a nested JSON array, the leading index outermost. A zero extent closes as the
  nesting does, so the extents after it are not spelled: extents `[0, 4]` are `[]` and
  `[3, 0]` are `[[], [], []]`.
- A real is written as `docs/workflow/RUNNER_OUTPUT_CONTRACT.md` §4 states, a non-finite one as
  the token `NaN`, `Infinity` or `-Infinity`.
- The series is written in chunks, `<i>` counting the chunks the case has written. A
  `restart_point` commit first writes the rows committed since the previous chunk as the next
  chunk, when there is such a row, and then its own file, so a restart point that is in its
  file has every earlier row of the series in a file. Ending the case's output writes the rows
  not yet written as the next chunk, and writes a chunk with empty lists when the case wrote
  none, so every case has chunk `0`. The host joins the chunks in the order of `<i>` and
  refuses a series with a chunk missing before a later one.
- The directories `initial/`, `history/`, `restart_point/` and `series/` exist before the
  program runs: the host creates them on every node, and the harness creates none.
- `snapshot_schema.json` in the same directory is the host's, not a capture.
