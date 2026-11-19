#!/usr/bin/env python3
"""The neutral seam between the host and whichever language backend renders a node's runner glue.

On an M3c node the host — not a leaf — authors the runner that drives the physics kernel over
the certified harness. The TEXT of that runner is language knowledge and lives in the language
backend (`tools/backends/<axis>/<backend_id>/`, `docs/BACKEND_BOUNDARY.md`); the DECISION to
author it, and the routing to the value that can, are neutral and live here.

Every function takes the node's `language` (the `runner_render` capability's axis, read from
the run's target profile, `toolchain.language`) and dispatches through
`registry.capability_module`. A
value that does not declare `runner_render` is REFUSED with the registry's own
`missing_capability_reason` wording, verbatim — the alternative, falling through to whichever
backend happens to be extracted, is the authorship flip the capability question exists to
prevent.

`RenderError` is defined HERE rather than in the backend, and that is deliberate. The neutral
core catches it in `workflow_conductor._write_runner`, and a class obtained through
`registry.load` is not the class an `except` clause in a neutral module names — the clause would
have to be `except Exception`, which is the same as no clause at all. (The backend has a second
`except RenderError` inside `ir_content_violations`, where it catches its own raise and class
identity is trivial; it is not what this placement is for. An earlier version of this note said
"three call sites", which was never true — there are two clauses and one of them is that one.)
The backend imports the class from here and raises it.

ONE PREDICATE, asked once. `runner_render_refusal` is defined as "what `_module` refuses with",
not as a second question that happens to agree: they disagreed, and the disagreement was a real
defect. The guard asked `provides` — the UNION of `core_provides` and `backend_provides`, which
is the authorship question — while the dispatch asked `capability_module`, which requires the
package half. For a record carrying `runner_render` in `core_provides` WITH a module (a state
the registry explicitly permits, and the shape the live language backend already has for `control_file`) the
guard said "renderable" and the dispatch raised `BackendNotExtracted` — inside
`_validate_compile_stage_impl`, where an uncaught raise discards every violation the sibling
gates collected, which is exactly what the gate's own comment promises cannot happen.
"""

from __future__ import annotations

from collections.abc import Collection
from types import ModuleType
from typing import Any

from tools.backends import registry



class RenderError(RuntimeError):
    """A physics IR that cannot be faithfully rendered into runner glue.

    Raised for a structural impossibility (an unrenderable shape, a rank the harness has no
    emitter for, a reserved-key collision, unsupported verdict fields, >1 infra dep, an
    over-long identifier). The conductor routes it to transport fail_closed — it is a spec/IR
    defect, not content a Generate retry could repair by re-authoring the model.

    ``identity=True`` marks the subset whose offending value is the node's IDENTITY (spec_id / a
    derived symbol's length, or >1 infra dep) rather than authored IR *content*. Re-authoring the
    IR cannot repair an identity defect, so the compile.static mirror (``ir_content_violations``)
    excludes it — it belongs to spec-input validation (`tools/spec_input_gates.py`), NOT a
    compile.generate warm-resume retry. Every other RenderError (``identity=False``) is
    Compile-authored content the compile gate hoists so a defect routes to compile.generate
    instead of killing the workflow at conductor render time."""

    def __init__(self, message: str, *, identity: bool = False) -> None:
        super().__init__(message)
        self.identity = identity


class RunnerRenderUnavailable(RuntimeError):
    """The node's language does not declare `runner_render`, so no backend can author its runner.

    Distinct from `RenderError`: nothing was found wrong with the IR. A caller that reached here
    asked a language that has no host renderer to produce one, which means the dispatch that
    decides authorship (`_conductor_authors_runner`) and this seam disagreed — a host bug, or a
    node that should have taken the leaf-authored path.
    """


def _module(language: Any) -> ModuleType:
    """The backend module that implements `runner_render` for `language`, or raise.

    Two grounds, and both must be asked HERE rather than split across this function and its
    guard. `missing_capability_reason` answers whether the job is the host's at all — the
    authorship question, over the union of both capability sets. `capability_module` answers
    whether THIS record's package implements it, and refuses a value whose backend is not
    extracted, one whose record does not claim the job, and one whose package does not carry
    what the record claims. A value can pass the first and fail the second; see the module
    docstring for the defect that shape produced.

    Every refusal leaves as `RunnerRenderUnavailable`, so a caller has one exception type to
    handle and `runner_render_refusal` can be defined as "what this refuses with".
    """
    # `or ""` and `.strip()` are INTENT MARKERS here, not live guards, and saying so beats
    # leaving a reader to take them for load bearing: measured, `str(language)` alone leaves the
    # suite green, because `missing_capability_reason` and `capability_module` both normalize
    # internally. They stay so the value this seam passes on reads as the token it names.
    lang = str(language or "").strip()
    # The axis and the capability are spelled as LITERALS, not module constants, so the
    # neutral-core scan in `test_backend_boundary` that inventories which capabilities are
    # dispatched on can see this seam. A constant here would make the one real dispatch of
    # `runner_render` invisible to the instrument that pins dispatches against declarations.
    reason = registry.missing_capability_reason("language", lang, "runner_render")
    if reason is not None:
        raise RunnerRenderUnavailable(reason)
    try:
        return registry.capability_module("language", lang, "runner_render")
    except (registry.UnsupportedBackend, registry.BackendNotExtracted) as exc:
        # The registry's own wording, re-typed rather than re-worded: the gates below catch one
        # class, and letting the registry's exception through would be the uncaught raise this
        # seam exists to prevent.
        raise RunnerRenderUnavailable(str(exc)) from exc


def runner_render_refusal(language: Any) -> str | None:
    """`None` when `language` has a host runner renderer; else the refusal clause.

    For the deterministic gates, which append a violation string rather than raising. DEFINED as
    what `_module` refuses with — not as a second question that happens to agree today. The two
    were separate predicates for one review round and they disagreed on a state the registry
    permits, which turned a documented "violation, never an exception" into an uncaught raise.
    The wording is the registry's, unaltered: one owner for the sentence that names what would
    have had to be implemented.
    """
    try:
        _module(language)
    except RunnerRenderUnavailable as exc:
        return str(exc)
    return None


def render_runner(language: Any, ir: dict[str, Any], spec_id: str, harness_spec_id: str,
                  *, target: dict[str, Any], harness_provides: Collection[str]) -> str:
    """The complete text of the node's runner source, rendered from the IR and the target.

    `harness_provides` is the capability set of the target's harness
    (`target_profile.harness_capabilities`, issue #690). It decides the interface block the
    driver is written against — the process model is `distributed_state` among it — so it is
    required on all four functions of this seam that take a harness: a caller that omitted it
    would render the single-process form over a distributed harness.

    `target` is the run's target profile document (`TargetProfile.doc`, issue #284): the
    runner's performance record names the hardware class, threads per rank and devices the run
    is executed with, which are the target's and not the IR's (the rank count is the run's own,
    issue #316, and so is the thread count where the harness observes it at run time, issue
    #512). Deterministic and pure. Raises
    `RenderError` for an IR the backend cannot faithfully render, and `RunnerRenderUnavailable`
    when `language` declares no renderer.
    """
    return _module(language).render_runner(ir, spec_id, harness_spec_id, target=target,
                                           harness_provides=frozenset(harness_provides))


def assert_harness_pin(
    language: Any,
    ir: dict[str, Any],
    spec_id: str,
    harness_spec_id: str,
    harness_signatures: Any,
    harness_source: str,
    *,
    harness_provides: Collection[str],
) -> None:
    """Fail-closed guard run BEFORE rendering: the certified harness the consumer will link
    against must still publish, under its own `<harness_spec_id>__` names, exactly the interface
    block the backend's template is written against for the tokens of `harness_provides`, in
    both its IR ``public_api.signatures`` and its generated model source. The block is the framework's;
    the harness's name, version and every operation beyond the block are its Project's (issue
    #690). Drift raises `RenderError`, never a Generate content retry."""
    _module(language).assert_harness_pin(
        ir, spec_id, harness_spec_id, harness_signatures, harness_source,
        harness_provides=frozenset(harness_provides))


def ir_content_violations(
    language: Any, ir: dict[str, Any], spec_id: str, harness_spec_id: str, *,
    harness_provides: Collection[str],
) -> list[str]:
    """The Compile-authored render preconditions, as violation messages (``[]`` when the IR
    renders, or when the only defect is a node-identity one — see `RenderError.identity`).

    The compile.static gate calls this so an IR defect routes back to compile.generate instead of
    surfacing only as a conductor-time fail_closed that kills the run.
    """
    return _module(language).ir_content_violations(
        ir, spec_id, harness_spec_id, harness_provides=frozenset(harness_provides))


def physics_harness_uses(language: Any, harness_spec_id: str, *,
                         harness_provides: Collection[str]) -> dict[str, frozenset[str]]:
    """`{module: names}` a physics source of `language` may `use` from the harness
    `harness_spec_id` — empty unless that harness runs the program as several ranks
    (`distributed_state` among `harness_provides`), whose
    distributed-state operations a physics source reaches the runtime through (issue #316). The
    backend that renders the runner over the harness is the one that knows which of its
    operations are the runner's own."""
    return dict(_module(language).physics_harness_uses(
        harness_spec_id, harness_provides=frozenset(harness_provides)))


def harness_block_drift(language: Any, harness_spec_id: str, section51: str, *,
                        harness_provides: Collection[str]) -> list[str]:
    """How a harness's controlled_spec §5.1 (`section51`, the body of its one fenced block)
    differs from the interface block the `language` backend's driver is written against for
    `harness_provides`, one message per symbol; ``[]`` when §5.1 publishes the block under
    `<harness_spec_id>__` names (issue #690).

    `assert_harness_pin` compares a CERTIFIED harness at its first consumer's render, and only
    on the symbols that consumer's driver uses. This one reads the `spec` and compares the whole
    block, so a Project harness that does not publish it is seen before it is certified
    (`skills/spec-input-check`)."""
    return list(_module(language).harness_block_drift(
        harness_spec_id, section51, harness_provides=frozenset(harness_provides)))
