"""Unit tests for the leaf-side helpers of `tools/leaf_contract.py` that outlived the pure leaf.

Every LLM leaf is a `workshop leaf` since issue #534 PR-J4-b, and the pure leaf's own transport
(`pure_leaf_flags`, the reply-text extractor, the pure argv branches of
`Conductor.leaf_command`) went with it. What stays here: the workshop flag set and its preflight
option census, the result-envelope parser (fed a null `result`, an absent `usage`, `is_error`,
non-JSON and empty stdout, so a present `null` is never read as an absent key — `_MISSING`), the
verdict validator (each joint-invariant violation, and an uppercase `"PASS"` to prove an enum is
exact, not case-folded), the repair text, the record predicates old pure
records are still read through, and the codex resume argv's preflight certification.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import leaf_contract as pl
from tools import workflow_conductor as wc


def _envelope(**overrides) -> str:
    """A well-formed result envelope with `overrides` applied; keys mapped to the sentinel
    `DROP` are omitted entirely (so absence, not a null, is tested)."""
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "{}",
        "session_id": "sess-1",
        "model": "claude-opus-4-8",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    base.update(overrides)
    payload = {k: v for k, v in base.items() if v is not DROP}
    return json.dumps(payload)


DROP = object()


class WorkshopLeafFlagsTest(unittest.TestCase):
    """The workshop leaf's flag set (issue #534): the golden list is the M4 result — the
    permission flags that let the wrapper's MCP tools run with nobody to approve them."""

    MCP = '{"mcpServers": {"wrapper": {"type": "http", "url": "http://127.0.0.1:1/mcp"}}}'

    def test_flag_set_golden(self):
        self.assertEqual(
            pl.workshop_leaf_flags(self.MCP),
            ["--restricted", "--system-prompt", pl.WORKSHOP_SYSTEM_PROMPT,
             "--tools", "Read,Write,Edit,Glob,Grep",
             "--mcp-config", self.MCP, "--strict-mcp-config",
             "--permission-mode", "acceptEdits", "--permission-prompts", "none",
             "--allowedTools", "mcp__wrapper",
             "--disable-slash-commands", "--output-format", "json"])

    def test_it_drops_what_disables_mcp_and_every_tool(self):
        flags = pl.workshop_leaf_flags(self.MCP)
        self.assertNotIn("--safe-mode", flags)
        self.assertNotIn("", flags)
        self.assertEqual(flags[0], "--restricted")

    def test_an_endpoint_is_required_and_the_list_is_fresh(self):
        for bad in ("", "  ", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pl.workshop_leaf_flags(bad)  # type: ignore[arg-type]
        a = pl.workshop_leaf_flags(self.MCP)
        a.append("--mutated")
        self.assertNotIn("--mutated", pl.workshop_leaf_flags(self.MCP))

    def test_the_mcp_config_names_the_wrapper_server(self):
        doc = json.loads(pl.workshop_mcp_config_json("http://127.0.0.1:9/mcp"))
        self.assertEqual(doc, {"mcpServers": {pl.WORKSHOP_MCP_SERVER_NAME: {
            "type": "http", "url": "http://127.0.0.1:9/mcp"}}})
        self.assertEqual(f"mcp__{pl.WORKSHOP_MCP_SERVER_NAME}",
                         pl.workshop_leaf_flags(self.MCP)[
                             pl.workshop_leaf_flags(self.MCP).index("--allowedTools") + 1])

    def test_the_detection_predicates(self):
        for payload, pure, workshop in (
                ({"leaf_mode": "pure"}, True, False), ({"leaf_mode": " WORKSHOP "}, False, True),
                ({"leaf_mode": "agentic"}, False, False), ({}, False, False),
                ("workshop", False, False)):
            with self.subTest(payload=payload):
                self.assertEqual(pl.is_pure_request(payload), pure)
                self.assertEqual(pl.is_workshop_request(payload), workshop)
                self.assertEqual(pl.is_host_rendered_request(payload), pure or workshop)

    def test_the_sentinels_are_distinct(self):
        self.assertNotEqual(pl.WORKSHOP_PROMPT_SENTINEL, pl.PURE_PROMPT_SENTINEL)
        self.assertFalse(pl.WORKSHOP_PROMPT_SENTINEL.startswith(pl.PURE_PROMPT_SENTINEL))
        self.assertFalse(pl.PURE_PROMPT_SENTINEL.startswith(pl.WORKSHOP_PROMPT_SENTINEL))

    def test_the_option_set_of_both_argvs(self):
        """The options of the two claude workshop argvs, producing and reading (issue #534
        PR-J4-b, MJ2). Until issue #643 this set was also the preflight's `--help` census
        (`CLAUDE_WORKSHOP_REQUIRED_OPTIONS`); that probe went, and a CLI missing one of these
        options now refuses the argv at launch (`leaf_transport_error`). The row still pins
        which options the reading argv carries, which `test_flag_set_golden` does not."""
        emitted = {flag for argv in (pl.workshop_leaf_flags("{}"),
                                     pl.workshop_leaf_flags(None, reading=True))
                   for flag in argv if flag.startswith("--")}
        self.assertEqual(emitted, {
            "--restricted", "--system-prompt", "--tools", "--mcp-config", "--strict-mcp-config",
            "--permission-mode", "--permission-prompts", "--allowedTools",
            "--disable-slash-commands", "--output-format"})


class ParseResultEnvelopeTest(unittest.TestCase):
    def test_well_formed(self):
        env = pl.parse_result_envelope(_envelope())
        self.assertTrue(env.parsed)
        self.assertIsNone(env.parse_error)
        self.assertEqual(env.result, "{}")
        self.assertIs(env.is_error, False)
        self.assertEqual(env.model, "claude-opus-4-8")
        self.assertEqual(env.usage, {"input_tokens": 10, "output_tokens": 20})

    def test_result_null_is_present_not_missing(self):
        # "result": null is a PRESENT null (None), never conflated with an absent key.
        env = pl.parse_result_envelope(_envelope(result=None))
        self.assertTrue(env.parsed)
        self.assertIsNone(env.result)
        self.assertIsNot(env.result, pl._MISSING)

    def test_absent_usage_is_missing(self):
        env = pl.parse_result_envelope(_envelope(usage=DROP))
        self.assertIs(env.usage, pl._MISSING)

    def test_is_error_true_surfaced(self):
        env = pl.parse_result_envelope(_envelope(is_error=True))
        self.assertIs(env.is_error, True)

    def test_model_from_model_usage_fallback(self):
        env = pl.parse_result_envelope(
            _envelope(model=DROP, modelUsage={"claude-sonnet-5": {"output_tokens": 1}}))
        self.assertEqual(env.model, "claude-sonnet-5")

    def test_model_missing_when_ambiguous(self):
        env = pl.parse_result_envelope(
            _envelope(model=DROP, modelUsage={"a": {}, "b": {}}))
        self.assertIs(env.model, pl._MISSING)

    def test_the_primary_model_is_resolved_from_the_usage_row(self):
        """The ordinary CLI shape: no top-level `model`, and TWO `modelUsage` rows because a
        helper model ran alongside the one that answered. The top-level `usage` reports the
        PRIMARY model alone, so the row equal to it names the model that answered. Of the 136
        result envelopes recorded in this repository none carries a top-level `model` and 78
        carry two rows — without this they record no exact model at all."""
        env = pl.parse_result_envelope(_envelope(
            model=DROP,
            usage={"input_tokens": 2, "output_tokens": 4,
                   "cache_read_input_tokens": 14278, "cache_creation_input_tokens": 5849},
            modelUsage={
                "claude-opus-5[1m]": {"inputTokens": 2, "outputTokens": 4,
                                      "cacheReadInputTokens": 14278,
                                      "cacheCreationInputTokens": 5849},
                "claude-haiku-4-5-20251001": {"inputTokens": 6, "outputTokens": 480,
                                              "cacheReadInputTokens": 13000,
                                              "cacheCreationInputTokens": 0}}))
        self.assertEqual(env.model, "claude-opus-5[1m]")

    def test_an_uncountable_row_is_never_named_the_primary_model(self):
        """`None == None` is True four times over, so an EMPTY row would "match" a `usage`
        block with no counts and be named the model that answered. At least one real count
        has to have agreed."""
        env = pl.parse_result_envelope(_envelope(
            model=DROP, usage={}, modelUsage={"a": {}, "b": "not-a-row"}))
        self.assertIs(env.model, pl._MISSING)

    def test_a_flag_where_a_count_belongs_never_matches(self):
        """`True == 1` in Python: a row reporting a flag where a token count belongs would
        otherwise be named the primary whenever `usage` reported 1."""
        env = pl.parse_result_envelope(_envelope(
            model=DROP, usage={"input_tokens": 1},
            modelUsage={"a": {"inputTokens": True}, "b": {"inputTokens": 9}}))
        self.assertIs(env.model, pl._MISSING)

    def test_two_rows_that_both_match_stay_ambiguous(self):
        """A provenance gap is recorded as absent, never guessed: with two identical rows
        there is no fact about which one answered."""
        env = pl.parse_result_envelope(_envelope(
            model=DROP, usage={"input_tokens": 5},
            modelUsage={"a": {"inputTokens": 5}, "b": {"inputTokens": 5}}))
        self.assertIs(env.model, pl._MISSING)

    def test_model_empty_top_level_falls_back_to_model_usage(self):
        env = pl.parse_result_envelope(
            _envelope(model="  ", modelUsage={"claude-x": {}}))
        self.assertEqual(env.model, "claude-x")

    def test_the_two_model_usage_key_maps_stay_in_step(self):
        """The CLI's `modelUsage` row names are written down TWICE — here, to identify the
        primary model, and in `workflow_conductor._MODEL_USAGE_KEYS`, to sum the tokens. A
        rename in the CLI breaks both, silently and differently: the conductor understates
        the tokens, this module degrades model provenance to the unpinned alias. Nothing else
        ties them."""
        self.assertEqual(
            [reported for reported, _canonical in pl._PRIMARY_MODEL_MATCH],
            list(wc._MODEL_USAGE_KEYS))
        self.assertEqual(
            [canonical for _reported, canonical in pl._PRIMARY_MODEL_MATCH],
            list(wc._MODEL_USAGE_KEYS.values()))

    def test_deeply_nested_stdout_does_not_raise(self):
        # RecursionError (not a JSONDecodeError) must not escape the "never raises" contract.
        env = pl.parse_result_envelope("[" * 200000)
        self.assertFalse(env.parsed)
        self.assertIs(env.result, pl._MISSING)

    def test_non_json_stdout(self):
        env = pl.parse_result_envelope("not json at all")
        self.assertFalse(env.parsed)
        self.assertIsNotNone(env.parse_error)
        self.assertIs(env.result, pl._MISSING)

    def test_empty_stdout(self):
        env = pl.parse_result_envelope("")
        self.assertFalse(env.parsed)
        self.assertIs(env.result, pl._MISSING)
        self.assertIs(env.usage, pl._MISSING)

    def test_json_array_is_not_an_object(self):
        env = pl.parse_result_envelope("[1, 2, 3]")
        self.assertFalse(env.parsed)
        self.assertIs(env.result, pl._MISSING)

    def test_never_raises_on_hostile_input(self):
        for bad in (None, 42, {"a": 1}, b"bytes"):
            env = pl.parse_result_envelope(bad)  # type: ignore[arg-type]
            self.assertFalse(env.parsed)


def _verdict(**overrides) -> dict:
    base = {
        "verification_status": "pass",
        "issue_severity": "none",
        "last_fail_reason": None,
        "findings": [],
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not DROP}


class VerifyVerdictTest(unittest.TestCase):
    def test_valid_pass(self):
        self.assertEqual(pl.verify_verdict_violations(_verdict()), [])

    def test_valid_fail(self):
        v = _verdict(verification_status="fail", issue_severity="minor",
                     last_fail_reason="io contract mismatch",
                     findings=[{"summary": "wrong intent shape"}])
        self.assertEqual(pl.verify_verdict_violations(v), [])

    def test_uppercase_status_rejected_not_folded(self):
        # The status enum is EXACT: "PASS" is a violation, never folded into a pass.
        violations = pl.verify_verdict_violations(_verdict(verification_status="PASS"))
        self.assertTrue(any("verification_status must be one of" in c for c in violations))

    def test_unknown_severity_rejected(self):
        violations = pl.verify_verdict_violations(_verdict(issue_severity="blocker"))
        self.assertTrue(any("issue_severity must be one of" in c for c in violations))

    def test_missing_key(self):
        violations = pl.verify_verdict_violations(_verdict(findings=DROP))
        self.assertIn("findings is required", violations)

    def test_unknown_key_closed(self):
        v = _verdict()
        v["extra"] = 1
        violations = pl.verify_verdict_violations(v)
        self.assertTrue(any("unknown key" in c for c in violations))

    def test_last_fail_reason_object_rejected(self):
        violations = pl.verify_verdict_violations(
            _verdict(verification_status="fail", issue_severity="major",
                     last_fail_reason={"detail": "x"}, findings=[{"summary": "s"}]))
        self.assertIn("last_fail_reason must be a string or null", violations)

    # --- joint invariants ---
    def test_pass_with_severity_violation(self):
        violations = pl.verify_verdict_violations(_verdict(issue_severity="minor"))
        self.assertTrue(any("requires issue_severity 'none'" in c for c in violations))

    def test_pass_with_findings_violation(self):
        violations = pl.verify_verdict_violations(
            _verdict(findings=[{"summary": "s"}]))
        # pass + non-none-forcing findings: severity stays none so the findings-empty
        # invariant is what fires.
        self.assertTrue(any("empty findings array" in c for c in violations))

    def test_pass_with_reason_violation(self):
        violations = pl.verify_verdict_violations(_verdict(last_fail_reason="oops"))
        self.assertTrue(any("last_fail_reason null" in c for c in violations))

    def test_fail_requires_severity(self):
        violations = pl.verify_verdict_violations(
            _verdict(verification_status="fail", last_fail_reason="x",
                     findings=[{"summary": "s"}]))
        self.assertTrue(any("non-'none' issue_severity" in c for c in violations))

    def test_fail_requires_findings_and_reason(self):
        violations = pl.verify_verdict_violations(
            _verdict(verification_status="fail", issue_severity="major"))
        self.assertTrue(any("at least one finding" in c for c in violations))
        self.assertTrue(any("non-empty last_fail_reason" in c for c in violations))

    def test_finding_without_summary(self):
        violations = pl.verify_verdict_violations(
            _verdict(verification_status="fail", issue_severity="major",
                     last_fail_reason="x", findings=[{"note": "no summary"}]))
        self.assertTrue(any("findings[0].summary" in c for c in violations))

    def test_findings_not_an_array(self):
        violations = pl.verify_verdict_violations(
            _verdict(verification_status="fail", issue_severity="major",
                     last_fail_reason="x", findings="oops"))
        self.assertIn("findings must be an array", violations)

    def test_finding_element_not_an_object(self):
        violations = pl.verify_verdict_violations(
            _verdict(verification_status="fail", issue_severity="major",
                     last_fail_reason="x", findings=["a string finding"]))
        self.assertIn("findings[0] must be an object", violations)

    def test_non_dict_verdict(self):
        self.assertEqual(pl.verify_verdict_violations([1, 2]),
                         ["verdict must be a JSON object"])


class VerifyRepairTextTest(unittest.TestCase):
    """`verify_repair_text` is what the host projects as the meta's `last_fail_reason`, and so
    the whole of what a producer repair learns from a verify verdict (issue #347). A reviewer
    whose one-line reason names one defect while `findings[]` names five must hand the repair
    all five."""

    @staticmethod
    def _fail(reason: str, *summaries: str) -> dict:
        v = _verdict(verification_status="fail", issue_severity="minor",
                     last_fail_reason=reason, findings=[{"summary": s} for s in summaries])
        assert pl.verify_verdict_violations(v) == [], v  # only schema-valid verdicts are folded
        return v

    def test_pass_is_null(self):
        self.assertIsNone(pl.verify_repair_text(_verdict()))

    def test_one_finding_follows_the_reason(self):
        self.assertEqual(pl.verify_repair_text(self._fail("flux sign", "mass not conserved")),
                         "flux sign\n1. mass not conserved")

    def test_every_finding_in_order_and_numbered(self):
        self.assertEqual(
            pl.verify_repair_text(self._fail("three defects", "first", "second", "third")),
            "three defects\n1. first\n2. second\n3. third")

    def test_outer_whitespace_is_stripped_and_inner_newlines_kept(self):
        self.assertEqual(
            pl.verify_repair_text(self._fail("  reason  \n", "  line a\nline b  ", "\tc\n")),
            "reason\n1. line a\nline b\n2. c")

    def test_a_summary_repeating_the_reason_is_kept(self):
        # No deduplication: the reviewer copying its only finding into the reason costs a line.
        self.assertEqual(pl.verify_repair_text(self._fail("quoted unescaped", "quoted unescaped")),
                         "quoted unescaped\n1. quoted unescaped")

    def test_nothing_is_clipped(self):
        long = "x" * 5000
        text = pl.verify_repair_text(self._fail("r", long, long))
        self.assertEqual(text, f"r\n1. {long}\n2. {long}")


class VerdictVocabParityTest(unittest.TestCase):
    """Guard the verdict enums against drift from the conductor's severity router — the two
    must agree or the model could author a severity the router mishandles (or vice versa)."""

    def test_status_vocab_pinned(self):
        self.assertEqual(pl.VERDICT_STATUSES, ("pass", "fail"))

    def test_severity_vocab_pinned(self):
        self.assertEqual(pl.VERDICT_SEVERITIES, ("none", "minor", "major", "critical"))

    def test_every_severity_routes_consistently(self):
        # 'none' advances; every non-'none' verdict severity must route to a repair action
        # (never 'advance') in classify_verify_severity.
        for sev in pl.VERDICT_SEVERITIES:
            decision = wc.classify_verify_severity(sev, "prod")
            if sev == "none":
                self.assertEqual(decision.action, "advance")
            else:
                self.assertNotEqual(
                    decision.action, "advance",
                    f"severity {sev!r} must route to a repair, not advance")


class PurePromptConstantsTest(unittest.TestCase):
    """M-B: the pure prompt sentinel + doc fence are the single source imported by
    orchestration_runtime and validate_pipeline_semantics; pin their presence and shape so a
    rename is a deliberate, test-visible event."""

    def test_sentinel_present_and_nonempty(self):
        self.assertTrue(isinstance(pl.PURE_PROMPT_SENTINEL, str) and pl.PURE_PROMPT_SENTINEL.strip())

    def test_doc_fence_markers_distinct_and_nonempty(self):
        self.assertTrue(pl.PURE_DOC_FENCE_BEGIN.strip())
        self.assertTrue(pl.PURE_DOC_FENCE_END.strip())
        self.assertNotEqual(pl.PURE_DOC_FENCE_BEGIN, pl.PURE_DOC_FENCE_END)


class ClaudeArgvCarriesTheWholeFlagSetTest(unittest.TestCase):
    def test_every_claude_launch_carries_its_kinds_whole_flag_set(self):
        """Every claude `leaf_command` argv carries its kind's whole `workshop_leaf_flags`
        list, contiguous, with `-p` last and nothing after it — cold and warm, producing and
        reading — so no argument of `leaf_command` can turn part of it off.

        The pure leaf's row pinned the same property of `pure_leaf_flags` (Z4, issue #171:
        "the whole set is unconditional"); that flag set went in issue #534 PR-J4-b, and the
        workshop one is what every claude launch now carries. `-p` last: the prompt goes on
        stdin, because a single argv element is capped at 128 KiB and a node's prompt
        exceeds it.
        """
        from tools.tests.llm_samples import sample_config
        mcp = pl.workshop_mcp_config_json("http://127.0.0.1:5/mcp")
        with tempfile.TemporaryDirectory() as tmp:
            c = wc.Conductor(repo_root=Path(tmp), orchestration_id="o",
                             orchestration_agent_run_id="orch", env={},
                             llm_config=sample_config("claude"))
            for pair, kwargs, flags in (
                    (("generate", "generate"), {"mcp_config_json": mcp},
                     pl.workshop_leaf_flags(mcp)),
                    (("compile", "verify"), {"reading": True},
                     pl.workshop_leaf_flags(None, reading=True))):
                entry = c.entry_for(*pair)
                self.assertEqual(entry.provider, "claude_cli")
                for resume in (None, "arid-0"):
                    with self.subTest(pair=pair, resume=resume):
                        argv = c.leaf_command(entry, session_id="arid-1",
                                              resume_session_id=resume, **kwargs)
                        self.assertEqual(argv[-1], "-p")
                        self.assertEqual(argv[-1 - len(flags):-1], flags)
                        if resume:
                            i = argv.index("--resume")
                            self.assertEqual(argv[i:i + 3], ["--resume", resume,
                                                             "--fork-session"])


class CodexResumeArgvCertificationTest(unittest.TestCase):
    def test_codex_resume_argv_options_are_all_preflight_certified(self):
        """Every option a codex resume argv emits must be one the resume probe checks.

        The `exec` and `exec resume` subcommands do not accept the same options, so a
        flag added to the resume branch without extending
        CODEX_EXEC_RESUME_REQUIRED_FLAGS would leave the preflight green on a CLI that
        rejects the command — exactly how the `--sandbox` regression passed review.

        Two resume argvs exist since issue #534 PR-J4-b deleted the pure one: the workshop
        producing argv (step 2 PR-2) and the reading one (step 3 PR-J2). The equality holds on
        each.
        """
        from tools import leaf_contract
        from tools.orchestration_runtime import CODEX_EXEC_RESUME_REQUIRED_FLAGS
        from tools.tests.test_workshop_producer import (
            _codex_config as _producing_config,
        )
        from tools.tests.test_workshop_reviewer import _codex_config as _reading_config
        required = set(CODEX_EXEC_RESUME_REQUIRED_FLAGS)
        with tempfile.TemporaryDirectory() as tmp:
            for kind, load, pair, kwargs in (
                    ("producing", lambda r: _producing_config(r), ("generate", "generate"),
                     {"mcp_config_json": leaf_contract.workshop_mcp_config_json(
                         "http://127.0.0.1:5/mcp")}),
                    ("reading", lambda r: _reading_config(r, "compile", "verify"),
                     ("compile", "verify"), {"reading": True})):
                with self.subTest(workshop=kind):
                    c = wc.Conductor(repo_root=Path(tmp), orchestration_id="o",
                                     orchestration_agent_run_id="orch", env={},
                                     llm_config=load(Path(tmp)))
                    argv = c.leaf_command(c.entry_for(*pair), session_id="a",
                                          resume_session_id="thread-1", **kwargs)
                    self.assertEqual(argv[:3], ["codex", "exec", "resume"])
                    # `leaf_command` writes nothing under `workspace/tmp` (issue #230: the
                    # schema file it once wrote there is gone), cold or warm.
                    c.leaf_command(c.entry_for(*pair), session_id="a", **kwargs)
                    self.assertFalse((Path(tmp) / "workspace" / "tmp").exists())
                    # No short-form aliasing: the argv must emit exactly the spellings the
                    # probe asserts, so a CLI that drops an alias cannot pass the preflight.
                    # The bare `-` is excluded: it is the value of the positional prompt
                    # (codex's stdin sentinel), not an option, and it is certified by its own
                    # preflight check `codex_prompt_stdin` rather than by a flag-name
                    # substring. EQUALITY, not subset, in both directions: subset alone would
                    # let the constant grow a flag the argv never emits, making the preflight
                    # stricter than the command and false-failing the backend on a CLI that
                    # dropped an option nothing uses.
                    self.assertEqual(
                        {part for part in argv if part.startswith("-") and part != "-"},
                        required)


if __name__ == "__main__":
    unittest.main()
