# Issue #643 step 2 — classification brief (checkout ec2ddda3)

READ-ONLY on the checkout: no edit, branch, commit or push.

Read first: `gh issue view 643` (§The criterion, §Scope, §Method); AGENTS.md §Development premises (especially "No excessive defenses, no needless complexity" and the decision criterion); `.claude/skills/spec2science-enforcement-change/SKILL.md` lines 34-300 (rules 1, 1-b, 1-e). Inventory format: `BRIEF.md`.

Your input is one or more inventory tables in `` (facts already collected). Verify facts against the code where a classification depends on them; the inventory can be wrong (line drift, unopened pins, unverified reachability).

## Classes (one per row)
- `keep:leaf` — name the leaf gain: "without it, a leaf is closer to ___" (a gate cleared, a verdict green, a substep reported done), and the path the leaf-authored value takes.
- `keep:break` — name the concrete breakage without it: a correct run fails, or a wrong result is certified/recorded.
- `remove` — state (a) what the leaf would gain without it and why that is nothing, AND (b) why no breakage follows. State the evidence kind: `dead` (no in-tree caller/reader — give the grep), `masked` (an earlier check on the same value always fires first — name it and how you know), `operator-only` (reads only operator/host values on EVERY path — list them), `no-reader record`, `host-twin` (re-checks a value the host itself wrote/decided). Per rule 1-b, "unreachable" claimed from reading only is marked `needs-run`.
- `merge→<id>` — duplicate of another row's check on the same value; name the survivor (prefer the earliest / the one the leaf-authored value crosses first) and confirm the survivor covers every path the removed copy covers.
- `standing:#N` — #172 / #173 / #174 / #169 surface. Not a removal candidate.
- `defect` — the inventory exposes a real hole or fail-open (a leaf gain or a wrong certification), not a removal. Describe it.
- `operator-diag?` — a record whose only reader is a human operator (docs say so). Removal is the operator's call; say what it shows.
- `undecided` — say what is missing.

Rules:
- A check that polices only the operator's own inputs/environment (sites.yaml, llm.yaml, target profile, deps.yaml, spec files, directory names, env vars, files changing between phases) is a removal candidate UNLESS on some path it bounds a leaf-authored value, or its absence breaks a correct run (e.g. it gives a clear early error where otherwise a run fails late with a misleading leaf-blamed category — that is a breakage: misattribution charges the leaf's retry budget). Hashing into a derivation key is the accepted defense.
- A check whose input is host-written but CONTAINS a leaf-authored value (exit code of a leaf-built binary, a verdict decision, case ids) is not a host-twin; trace it.
- Mark if removing the row touches a method digested by `tools/tests/test_derivation_transformation_drift.py` (removal then needs a re-pin decision).
- Note the tests that would be deleted or changed with each `remove`/`merge`.
- Do not argue for removal you cannot evidence; `undecided` is fine.

## Output
Write `cls_<group>.md`: a header with counts per class, then a table `| id | class | reason & evidence | tests affected |`. Keep reasons tight (1-3 sentences). Then a short section "Removal groups": cluster the remove/merge rows into units that would make sense as one PR (by mechanism), with an estimated line count removed. Final reply: counts per class, the defects, the removal groups, path.
