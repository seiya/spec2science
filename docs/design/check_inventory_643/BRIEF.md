# Issue #643 inventory — shared brief (checkout ec2ddda3)

READ-ONLY. Do not edit, branch, commit or push anything in the checkout.

Read first: `gh issue view 643`; AGENTS.md §Development premises; `.claude/skills/spec2science-enforcement-change/SKILL.md` lines 34-300 (rules 1, 1-b, 1-e). The pilot table over the small modules is `pilot_small.md` in this directory — read its first 30 table rows as the worked example of the format.

## What to record
Every check, gate, refusal, validation, record (a file / log / field written for a later reader) and audit in YOUR line range. FACTS ONLY: do not decide keep/remove, do not argue for removal.

## Granularity
- One row per DISTINCT refusal or record (distinct message / reason / record), not per `if`.
- The same type check repeated verbatim across several entry points: ONE row with N locations.
- One reader's family of schema checks sharing a message pattern: one row.
- **Owner rule**: inventory a check in the module that raises or decides it. If your range only CALLS a check owned elsewhere (another module, or another range of the same file), write one `[pointer]` row naming the owner and do not re-describe it. A shared constant/regex gets its row at its definition, listing every consumer.

## Columns (markdown table)
1. **id** — prefix given in your task, numbered.
2. **location** — `file:line` at ec2ddda3 plus the enclosing function.
3. **kind** — one of `refusal` (raise / refused result), `skip-ok` (returns ok-but-skipped, a later reader decides), `guard` (silent normalisation/drop/rewrite), `record` (file/field written for a later reader), `audit` (reads records and produces a verdict/finding), `import-check`, `catch-all` (exception turned into a verdict; state polarity: fail-closed / fail-open), `pointer`.
4. **what** — quote the message string or name the record/field.
5. **inputs & author** — for each value the check reads: `leaf` (LLM leaf output or tool-call argument: IR incl. case ids, generated sources, verify/judge documents, transcripts), `operator` (sites.yaml, llm.yaml, target profile, deps.yaml, spec files, directory names, environment, CLI args), `host` (host constant or host-computed), `dev-session`, `none (no caller)`, or `mixed` with paths listed. For any value you call `operator`/`host`, list EVERY caller/path reaching the check and give the grep you ran — a check that looks like operator-input policing may be the only bound on a leaf-authored value arriving by another path. Authorship can differ per caller; say so.
6. **re-checked by / duplicate of** — another check that runs on the same value (upstream or downstream), with location, or `—`.
7. **pins** — test file::test name with a strength tag: `(msg)` asserts message text/fragment, `(type)` asserts exception type/result category only, `(sym)` names the symbol without driving this branch, `(cat-masked)` asserts a category a later check also returns so removing this check leaves it green. OPEN each candidate test; do not trust a message grep alone (tests assert fragments/regexes). `none found` if none.
8. **notes** — dead code (no in-tree caller — show the grep), records with no in-tree reader (show the grep), a standing tag where applicable, anything a classifier needs.

## Standing / history
- Standing non-candidates (inventory them, tag `standing: #N`): an alternative LLM provider #172, another target language's surface #173, Tune/Promote #174, the --mode prod diagnostician #169.
- Already deleted, do not re-report: build_runtime's argv/env denylist and character rules (#457), the MCP layer (#444).

## Mechanics
- The DEV hook refuses a Bash command whose TEXT contains the hard-reset git string or `sleep` with a duration; spell such greps as regexes (e.g. `reset --h[a]rd`).
- Use `rg`/`grep` and `sed -n`; the tree is large, read your range in pieces.
- Tests live in `tools/tests/`.

## Output
Write the table to the file path given in your task, with a 5-10 line header: range covered, row count, and any rows you could not finish (say so explicitly rather than omitting them). Final reply: row count, counts per kind, the 5-10 most notable facts for a classifier (duplicates, unpinned refusals, no-reader records, operator-looking checks that bound a leaf value), and the path.
