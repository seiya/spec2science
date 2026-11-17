# Issue #643 step 2 — adversarial verification brief (checkout ec2ddda3)

READ-ONLY on the checkout: no edit, branch, commit or push. Scratch probes go under `verify/` (this directory), run python with `-B` so no .pyc lands in the tree; check `git status --porcelain` at the end.

Read: `gh issue view 643`; AGENTS.md §Development premises; `.claude/skills/spec2science-enforcement-change/SKILL.md` lines 34-300 (rules 1, 1-b, 1-e). Inventory format `BRIEF.md`, classification classes `CLASSIFY.md`.

An opus classifier proposed `remove` / `merge→X` for the rows assigned to you. Your job is to BREAK those claims. A classifier briefed to find removable checks will find them; assume each claim is wrong until the code says otherwise. For each assigned row:

1. Read the code at the row's location and the evidence the classifier gave.
2. Attack it:
   - **Leaf gain**: is there any path on which a value an LLM leaf authors (IR incl. case ids / drive / tests block, generated sources and file names, the binary built from them and what it writes at run time, verify/judge documents, reply text, transcripts) reaches this check, and without the check the leaf gets closer to a gate cleared / a verdict green / a substep done? Try at least two association paths (rule 1-b: one counterexample is not enough): fresh run, `--resume`, cold adopt, warm repair, judge_replay, run_configuration, remote site, codex / HTTP providers.
   - **Breakage**: without it, does a correct run fail, or a wrong result get certified/recorded, or a failure get charged to the leaf's retry budget (misattribution) instead of an operator/host error?
   - **Merge**: does the survivor really run on EVERY path the removed copy runs on, at a stage that still blocks? Is the survivor itself proposed for removal in another group's file (`cls_*.md`)? — grep the survivor id across all cls files.
   - **Masked / dead**: is the earlier check really always first? Is the "no caller" grep complete (getattr, CLI dispatch tables, skills/, tools/tests/data, docs commands)?
3. Where a claim rests on reading only and a cheap run would decide it, run it.

Verdict per row: `CONFIRMED` (claim holds; say what you checked), `REFUTED` (name the leaf gain or breakage, with path and evidence), `NEEDS-RUN` (name the exact run that would decide), or `PARTIAL` (which arm holds).

Output `verify/ver_<batch>.md`: header with counts per verdict, table `| id | proposed | verdict | evidence |`, then "Defects found" (any real hole you hit while attacking, even outside your rows). Final reply: counts, every REFUTED with its one-line reason, defects, path.
