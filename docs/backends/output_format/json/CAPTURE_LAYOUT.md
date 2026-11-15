# Capture layout of the `json` output format

The files a run's captures are held in when the target profile's `output.format` is `json`
(the default). `docs/workflow/RUNNER_OUTPUT_CONTRACT.md` §3 is canonical for what a capture
group holds; this document states only where the `json` format puts it. The writer is the
target's harness, whose `controlled_spec.md` §3.2 states the same layout as its own contract;
the reader is `tools/backends/output_format/json/store.py`.

Every file is one UTF-8 JSON object under `raw/state_snapshots/` of the run directory, written
once and never rewritten. `<C>` is a case id, `<k>` a committed step and `<i>` a count from 0,
each in decimal digits with no leading zero.

| group | file | content |
|---|---|---|
| `final` | `<C>.json` | every value put, each under its name, then `step` and `t` |
| `initial` | `initial/<C>.json` | the same |
| `history` | `history/<C>__<k>.json` | the same, one file per committed step `<k>` |
| `restart_point` | `restart_point/<C>__<k>.json` | the same |
| `series` | `series/<C>__<i>.json` | `step`, `t` and each series name, each a list with one entry per row |

- An array is a nested JSON array, the leading index outermost. A zero extent closes as the
  nesting does, so the extents after it are not spelled: `[0, 4]` is `[]`.
- A non-finite real is the token `NaN`, `Infinity` or `-Infinity`
  (`docs/workflow/RUNNER_OUTPUT_CONTRACT.md` §4).
- The series is written in chunks: the rows committed since the previous chunk, before each
  `restart_point` record and at the end of the case. A case that committed no row has chunk
  `0` with empty lists. The store joins the chunks in the order of `<i>` and refuses a series
  with a chunk missing before a later one.
- The host creates `initial/`, `history/`, `restart_point/` and `series/` before the run.
- `snapshot_schema.json` in the same directory is the host's, not a capture.
