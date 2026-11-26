#!/usr/bin/env python3
"""Helpers for workflow orchestration artifacts."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

# fcntl serializes agent_runs.jsonl duplicate-check + append against concurrent
# finalizers (Adv-24). The product runs on POSIX: every leaf launch already imports
# fcntl unconditionally (`tools/workshop_tree.py`).
import fcntl as _fcntl

import yaml

try:
    from tools import leaf_netns
    from tools.framework_root import FRAMEWORK_ROOT
    from tools.backends import registry as backend_registry
    from tools.prompt_fragments import parse_fragments
    from tools.operator_private_root import (
        WORKFLOW_HOMES_ROOT_ENV,
        _resolve_lenient,
        backend_credential_home_paths as _backend_credential_home_paths,
        workflow_homes_root as _hooks_workflow_homes_root,
    )
    from tools.derivation import (
        DERIVATION_STEPS,
        canonical_json_bytes as _canonical_json_bytes,
        derivation_key as _derivation_key,
        output_hash as _output_hash,
        sha256_hex as _sha256_hex,
    )
    from tools.meta_contracts import (
        CERTIFYING_META_FILENAME_BY_STEP,
        STAGE_META_FILENAME_BY_STEP,
        missing_required_meta_keys,
        stage_meta_type_violations,
    )
    from tools.target_profile import (
        TargetProfile,
        TargetProfileError,
        is_target_id,
        load_pipeline_target,
        load_target_profile,
        pipelines_dir,
    )
    from tools.leaf_contract import (
        PURE_DOC_FENCE_BEGIN,
        PURE_DOC_FENCE_END,
        LEAF_PROMPT_CONTRACT_VERSION,
        WORKSHOP_CLI_ENDPOINTS,
        WORKSHOP_LAUNCH_PAIRS,
        WORKSHOP_READER_TOOL_SURFACE_TEXT,
        WORKSHOP_TOOL_SURFACE_TEXT,
        is_host_rendered_request as _leaf_contract_is_host_rendered_request,
        is_workshop_reading_pair as _leaf_contract_is_workshop_reading_pair,
        is_workshop_request as _leaf_contract_is_workshop_request,
    )
except ModuleNotFoundError:  # pragma: no cover - import bootstrap for direct CLI execution
    _THIS_FILE = Path(__file__).resolve()
    _REPO_ROOT = _THIS_FILE.parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from tools import leaf_netns
    from tools.framework_root import FRAMEWORK_ROOT
    from tools.backends import registry as backend_registry
    from tools.prompt_fragments import parse_fragments
    from tools.operator_private_root import (
        WORKFLOW_HOMES_ROOT_ENV,
        _resolve_lenient,
        backend_credential_home_paths as _backend_credential_home_paths,
        workflow_homes_root as _hooks_workflow_homes_root,
    )
    from tools.derivation import (
        DERIVATION_STEPS,
        canonical_json_bytes as _canonical_json_bytes,
        derivation_key as _derivation_key,
        output_hash as _output_hash,
        sha256_hex as _sha256_hex,
    )
    from tools.meta_contracts import (
        CERTIFYING_META_FILENAME_BY_STEP,
        STAGE_META_FILENAME_BY_STEP,
        missing_required_meta_keys,
        stage_meta_type_violations,
    )
    from tools.target_profile import (
        TargetProfile,
        TargetProfileError,
        is_target_id,
        load_pipeline_target,
        load_target_profile,
        pipelines_dir,
    )
    from tools.leaf_contract import (
        PURE_DOC_FENCE_BEGIN,
        PURE_DOC_FENCE_END,
        LEAF_PROMPT_CONTRACT_VERSION,
        WORKSHOP_CLI_ENDPOINTS,
        WORKSHOP_LAUNCH_PAIRS,
        WORKSHOP_READER_TOOL_SURFACE_TEXT,
        WORKSHOP_TOOL_SURFACE_TEXT,
        is_host_rendered_request as _leaf_contract_is_host_rendered_request,
        is_workshop_reading_pair as _leaf_contract_is_workshop_reading_pair,
        is_workshop_request as _leaf_contract_is_workshop_request,
    )

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_rel_posix(path_token: str) -> str:
    """Normalize a repo-relative path into a stable POSIX token."""
    token = path_token.strip().replace("\\", "/").lstrip("/")
    while "//" in token:
        token = token.replace("//", "/")
    return token.rstrip("/")


TERMINAL_STATUSES = {"pass", "fail", "blocked", "timeout", "cancel"}
# Phases that run via substep agents (the orchestration agent aggregates and is the
# step_result executor). Build is the only no-substep phase (step agent is the executor).
SUBSTEP_AWARE_STEPS = frozenset({"compile", "generate", "validate"})
# Idempotency target of `set-status`. A status once in this set rejects both a same-value and an
# other-terminal transition, except for the permitted promotion (fail -> fail_closed). Appending the
# failure narrative is done in failure_analysis.json.
# A same-value re-call permits a cleanup retry only when the cleanup_committed marker is absent (F2).
IDEMPOTENT_TERMINAL_STATUSES = TERMINAL_STATUSES | {"fail_closed"}


_DEPENDENCY_READINESS_STAGES: tuple[str, ...] = (
    "ir_ref",
    "pipeline_ref",
    "aggregate_verdict",
)


def _read_deps_yaml(repo_root: Path, spec_ref: Any) -> dict[str, Any] | None:
    if not isinstance(spec_ref, str) or not spec_ref.strip():
        return None
    deps_path = (repo_root / spec_ref.strip() / "deps.yaml").resolve()
    try:
        deps_path.relative_to(repo_root.resolve())
    except ValueError:
        return None
    if not deps_path.is_file():
        return None
    try:
        return yaml.safe_load(deps_path.read_text(encoding="utf-8"))
    except Exception:
        return None


class SpecCatalogCorruption(Exception):
    """Codex round 33 F2: distinct failure mode for `spec_catalog.yaml`
    parse / top-level schema corruption. Distinguishes a repository-wide
    catalog outage from an ordinary "no matching dep" verification result
    so observability tooling and the CLI exit can fail loud instead of
    silently flipping every readiness flag to False."""


@lru_cache(maxsize=16)
def _spec_catalog_entries_from_bytes(content_bytes: bytes) -> tuple[dict[str, Any], ...]:
    """The mapping entries of `spec_catalog.yaml`'s `specs:` list, as parsed (callers must not
    mutate them). The one parse of the catalog: `_load_spec_catalog` (versions),
    `_spec_ref_candidates` (spec directories) and `_catalog_family_index` (families) each read
    these entries with their own tolerances.

    Codex round 26 F1: cache keyed on FILE CONTENT bytes (not mtime).
    Restore/copy workflows that preserve mtime while changing content
    previously left the cache returning the OLD parsed dict while every
    other reader of the file saw the new bytes. Keying on content makes
    cache hits semantic: same bytes → same parsed dict, no drift possible.
    """
    # Codex round 35 F2: zero-byte catalog is corruption, not "no specs
    # yet". The previous lenient early-return collapsed a truncated or
    # partially restored `spec_catalog.yaml` into an ordinary readiness=
    # false dep miss instead of surfacing the repo-wide outage. The
    # missing-file case was already promoted to `SpecCatalogCorruption`
    # by `_load_spec_catalog` (round 34 F2); empty file content reaches
    # this layer only when the file exists but holds zero bytes, which
    # is just as broken.
    if not content_bytes:
        raise SpecCatalogCorruption(
            "spec_catalog.yaml is empty (zero-byte file). Dependency "
            "resolution requires the canonical registry; this is a "
            "repository-wide outage, not a normal dependency miss."
        )
    # Codex round 33 F2: catalog parse/schema problems propagate as a
    # distinct `SpecCatalogCorruption` exception rather than being
    # downgraded to `{}` (which made downstream resolution treat
    # repository-wide catalog corruption as "no matching dep" — a normal
    # negative verification — and let `mark_dependency_readiness` complete
    # with all readiness flags false, hiding an outage as an ordinary
    # dependency miss). Distinct failure mode matches the treatment of
    # malformed deps.yaml (`deps_yaml_malformed_schema`).
    try:
        doc = yaml.safe_load(content_bytes.decode("utf-8"))
    except Exception as exc:
        raise SpecCatalogCorruption(
            f"spec_catalog.yaml could not be parsed as YAML: {exc}"
        ) from exc
    if not isinstance(doc, dict):
        raise SpecCatalogCorruption(
            f"spec_catalog.yaml top-level must be a mapping; got {type(doc).__name__}"
        )
    specs = doc.get("specs")
    if not isinstance(specs, list):
        raise SpecCatalogCorruption(
            "spec_catalog.yaml is missing the canonical `specs:` list "
            f"(found {type(specs).__name__})"
        )
    return tuple(entry for entry in specs if isinstance(entry, dict))


@lru_cache(maxsize=16)
def _load_spec_catalog_from_bytes(
    content_bytes: bytes,
) -> dict[tuple[str, str], tuple[str, ...]]:
    """Parse `spec_catalog.yaml` bytes into `(spec_kind, spec_id) → versions`: entries whose
    three fields are strings, stripped, an entry with an empty one dropped."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for entry in _spec_catalog_entries_from_bytes(content_bytes):
        kind = entry.get("spec_kind")
        sid = entry.get("spec_id")
        ver = entry.get("spec_version")
        if not (isinstance(kind, str) and isinstance(sid, str) and isinstance(ver, str)):
            continue
        kind_token = kind.strip()
        sid_token = sid.strip()
        ver_token = ver.strip()
        if not (kind_token and sid_token and ver_token):
            continue
        grouped.setdefault((kind_token, sid_token), []).append(ver_token)
    out: dict[tuple[str, str], tuple[str, ...]] = {}
    for key, versions in grouped.items():
        deduped = sorted(set(versions), key=_parse_semver, reverse=True)
        out[key] = tuple(deduped)
    return out


def _load_spec_catalog(repo_root_str: str) -> dict[tuple[str, str], tuple[str, ...]]:
    """`(spec_kind, spec_id) → versions` of the catalog (`_load_spec_catalog_from_bytes`)."""
    return _load_spec_catalog_from_bytes(_spec_catalog_bytes(repo_root_str))


def _spec_catalog_entries(repo_root: Path | str) -> tuple[dict[str, Any], ...]:
    """The catalog's entries (`_spec_catalog_entries_from_bytes`)."""
    return _spec_catalog_entries_from_bytes(_spec_catalog_bytes(str(repo_root)))


def _spec_catalog_bytes(repo_root_str: str) -> bytes:
    """Read catalog bytes each call; the parsers above cache on the content.

    Codex round 26 F1: previously keyed on `(repo_root, mtime_ns)`, which
    could be bypassed by edits that preserve mtime (restore-from-cache,
    `cp -p`, etc.). Hashing on content (via the bytes themselves as the
    `lru_cache` key) guarantees a cache hit only when the file contents
    are byte-identical, so dependency resolution and the derivation key's own
    read of the registry (`_derived_closure_graph`) cannot diverge.
    """
    # Codex round 34 F2: missing / unreadable catalog is now a HARD failure
    # (`SpecCatalogCorruption`). Previously it returned `{}` which downstream
    # dep resolution treated as "no matching versions" — making a repo-wide
    # registry outage look like an ordinary readiness=false dependency miss
    # and sending operators to the wrong layer for recovery. A leaf orchestration
    # (which needs no catalog) never reaches this raise: this module's readiness
    # callers invoke it only AFTER deps.yaml entries are confirmed non-empty, and
    # the closure builders load it lazily and handle the error themselves. (The
    # count of callers this comment used to carry was wrong twice — issue #178
    # round 1 — so it states the property and no number.)
    catalog_path = Path(repo_root_str) / "spec" / "registry" / "spec_catalog.yaml"
    if not catalog_path.is_file():
        raise SpecCatalogCorruption(
            f"spec_catalog.yaml is missing at {catalog_path}. Dependency "
            "resolution requires the canonical registry; this is a "
            "repository-wide outage, not a normal dependency miss."
        )
    try:
        return catalog_path.read_bytes()
    except OSError as exc:
        raise SpecCatalogCorruption(
            f"spec_catalog.yaml at {catalog_path} is unreadable: {exc}"
        ) from exc


def _clear_spec_catalog_caches() -> None:
    _spec_catalog_entries_from_bytes.cache_clear()
    _load_spec_catalog_from_bytes.cache_clear()


# Preserve the existing `_load_spec_catalog.cache_clear()` call surface used by
# tests; delegate to the content-keyed inner caches.
_load_spec_catalog.cache_clear = _clear_spec_catalog_caches  # type: ignore[attr-defined]


def resolve_spec_ref_for(
    repo_root: Path, spec_kind: Any, spec_id: Any
) -> str | None:
    """Map a catalog `(spec_kind, spec_id)` to its spec_ref (spec directory).

    The dependency closure driver (`tools/run_workflow.py --with-deps`) needs
    the spec_ref PATH of each dependency node so it can run that node's own
    workflow. `_load_spec_catalog` only retains versions per `(kind, spec_id)`
    and drops paths, so this reads the paths of the same parsed entries.

    The spec_ref is the dirname of the entry's `deps_path`
    (e.g. `spec/.../advdiff1d_linear/deps.yaml` → `spec/.../advdiff1d_linear`);
    `controlled_spec_path` is used as a fallback. Returns the repo-relative
    spec_ref string, or None when no catalog entry matches.

    Assumes one spec directory per `(spec_kind, spec_id)` (version lives inside
    `controlled_spec.md`, not in the directory path). When multiple entries for
    the same `(kind, spec_id)` resolve to DIFFERENT directories, that ambiguity
    is fail-closed (returns None) rather than silently picking one.
    """
    found = _spec_ref_candidates(repo_root, spec_kind, spec_id)
    if len(found) != 1:
        # No match → None. Multiple distinct dirs for one (kind, spec_id) →
        # ambiguous; fail closed rather than silently choosing. Callers that need to tell
        # 0 (absent) from >1 (ambiguous) apart call `_spec_ref_candidates` directly.
        return None
    return next(iter(found))


def _spec_ref_candidates(
    repo_root: Path, spec_kind: Any, spec_id: Any
) -> set[str]:
    """The set of distinct, repo-confined spec directories the catalog resolves
    ``(spec_kind, spec_id)`` to. Empty when the id has no catalog entry with a usable path;
    a single element on a healthy registry; more than one when the catalog is AMBIGUOUS.

    ``resolve_spec_ref_for`` collapses 0 and >1 both to ``None``; this exposes the
    distinction so `DerivationResolver.spec_ref` can refuse an ambiguous entry (a definitive
    registry defect, `spec_ref_unresolved`) while a genuinely-absent one is answered as such. Raises ``SpecCatalogCorruption``
    on a missing/unparseable registry, matching ``resolve_spec_ref_for``."""
    if not (_is_identifier_token(spec_kind) and _is_identifier_token(spec_id)):
        return set()
    entries = _spec_catalog_entries(repo_root)
    repo_root_resolved = Path(repo_root).resolve()
    found: set[str] = set()
    for entry in entries:
        if entry.get("spec_kind") != spec_kind or entry.get("spec_id") != spec_id:
            continue
        ref_source = entry.get("deps_path") or entry.get("controlled_spec_path")
        if not isinstance(ref_source, str) or not ref_source.strip():
            continue
        spec_ref = str(Path(ref_source.strip()).parent).replace("\\", "/").strip("/")
        if not spec_ref:
            continue
        # Confine to the repo tree (reject traversal) before trusting the path.
        candidate = (repo_root_resolved / spec_ref).resolve()
        try:
            candidate.relative_to(repo_root_resolved)
        except ValueError:
            continue
        found.add(spec_ref)
    return found


_SEMVER_RE = re.compile(
    r"^(?P<core>\d+(?:\.\d+)*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


def _parse_semver(
    v: str,
) -> tuple[tuple[int, ...], int, tuple[tuple[int, Any], ...]]:
    """Parse a semver-style version string into a 3-tuple sort key.

    Returns `(numeric_core, no_prerelease_flag, prerelease_key)`:
    - `numeric_core`: dot-separated leading digits as ints (e.g. `1.0.0` → `(1, 0, 0)`).
    - `no_prerelease_flag`: `1` when no `-prerelease` suffix is present, `0` otherwise.
      Per SemVer §11, a version without prerelease has HIGHER precedence than the
      same numeric core with prerelease (`1.0.0 > 1.0.0-rc1`). Tuple comparison
      gives that ordering because `1 > 0`.
    - `prerelease_key`: tuple of per-token sort keys for the prerelease segment
      (numeric tokens sort before alpha; `(0, int)` < `(1, str)`).

    Build metadata (`+xxx`) is parsed but **excluded from ordering** (SemVer §10).

    Codex round 13 F2: the previous parser only accepted `[\\d.]` and coerced
    every non-numeric component to `0`, breaking `1.0.0-rc1` and friends.
    """
    s = v.strip()
    m = _SEMVER_RE.match(s)
    if not m:
        # Malformed: sort below any well-formed value.
        return ((0,), 0, ())
    core_str = m.group("core")
    nums = tuple(int(tok) for tok in core_str.split("."))
    pre = m.group("pre")
    if pre is None:
        return (nums, 1, ())
    pre_key: list[tuple[int, Any]] = []
    for tok in pre.split("."):
        if tok.isdigit():
            pre_key.append((0, int(tok)))
        else:
            pre_key.append((1, tok))
    return (nums, 0, tuple(pre_key))


# Accept any version syntax `_parse_semver` can handle: numeric core plus
# optional `-prerelease` / `+build` suffix.
_CONSTRAINT_OP_RE = re.compile(
    r"^\s*(>=|<=|==|!=|>|<)?"
    r"\s*(\d+(?:\.\d+)*(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)\s*$"
)


def _matches_version_constraint(version: str, constraint: str | None) -> bool:
    """Evaluate `version` against an AND-joined constraint expression.

    Supported operators: `>=`, `>`, `<=`, `<`, `==`, `!=`. Bare versions (no
    operator) are treated as `==`. Whitespace-separated terms are AND-combined.
    Empty / None constraint → always true.

    Operator semantics (Codex round 14 F2):
    - Ordering operators (`>`, `>=`, `<`, `<=`) use SemVer-numeric precedence:
      build metadata (`+xxx`) is ignored (SemVer §10), prerelease ranks below
      its release (SemVer §11).
    - Equality operators (`==`, `!=`) compare **normalized full strings**,
      including any `+build` suffix. This prevents `==1.0.0+cpu` from
      silently matching `1.0.0+gpu` — workspace artifact roots are keyed by
      the full version string, so equality must distinguish build variants.
    """
    if not constraint or not constraint.strip():
        return True
    v_norm = version.strip()
    v_key = _parse_semver(v_norm)
    for term in constraint.split():
        m = _CONSTRAINT_OP_RE.match(term)
        if not m:
            return False
        op = m.group(1) or "=="
        rhs = m.group(2).strip()
        if op == "==":
            if v_norm != rhs:
                return False
            continue
        if op == "!=":
            if v_norm == rhs:
                return False
            continue
        rv = _parse_semver(rhs)
        if op == ">=" and not (v_key >= rv):
            return False
        if op == ">" and not (v_key > rv):
            return False
        if op == "<=" and not (v_key <= rv):
            return False
        if op == "<" and not (v_key < rv):
            return False
    return True


def _constraint_is_exact_string_match_only(constraint: str | None) -> bool:
    """True iff every term in `constraint` is an exact `==<full-version>` clause.

    Used to gate range-constraint resolution across build-metadata variants:
    if a constraint contains any inequality (`>=`, `<`, etc.) it must not
    silently match different build variants of the same numeric release.
    """
    if not constraint or not constraint.strip():
        return False
    for term in constraint.split():
        m = _CONSTRAINT_OP_RE.match(term)
        if not m:
            return False
        op = m.group(1)
        if op != "==":
            return False
    return True


def _matching_dep_versions(
    catalog: dict[tuple[str, str], tuple[str, ...]],
    kind: str,
    spec_id: str,
    constraint: str | None,
) -> tuple[str, ...]:
    """Return every catalog version of `(kind, spec_id)` satisfying `constraint`,
    in descending semver order. Empty tuple → unresolvable dep (fail-closed).

    Codex round 13 F1: returning ALL matching versions (not just the max) lets
    `_certify_and_collect_dep_artifacts` evaluate per-stage artifact presence across
    ANY of them. A newer catalog entry without artifacts no longer blocks
    parents whose older matching version is fully verified.

    Codex round 16 F2: when a range/inequality constraint matches catalog
    versions that share the same ordering key (i.e., differ only by build
    metadata, e.g. `1.0.0+cpu` vs `1.0.0+gpu`), the resolution is ambiguous
    — distinct workspace artifact roots cannot be silently selected. Return
    an empty tuple in that case so the caller fails closed. Exact-string
    constraints (`==1.0.0+cpu`) are unaffected: the operator already pins a
    specific full version.
    """
    versions = catalog.get((kind, spec_id))
    if not versions:
        return ()
    matched = tuple(v for v in versions if _matches_version_constraint(v, constraint))
    if len(matched) > 1 and not _constraint_is_exact_string_match_only(constraint):
        ordering_keys = {_parse_semver(v) for v in matched}
        if len(ordering_keys) < len(matched):
            # Two or more matched versions share the same numeric+prerelease
            # ordering key, differing only by build metadata. Range resolution
            # is ambiguous; fail closed (caller treats as unresolvable dep).
            return ()
    return matched


# Required deps.yaml `dependencies:` keys (present in every deps.yaml). `infrastructure` is still
# PARSED, so that a declaration is refused by name at spec-input
# (`spec_input_gates.infra_dep_declared_violation`, issue #284: a harness is a target attribute)
# rather than reported as an unknown key; no deps.yaml may carry an entry under it.
# `problems` is ALLOWED and not required (the system tier, Part 1): a `problem` may compose
# other `problem`s, and a deps.yaml without the key stays well-formed with its bytes unmoved. An
# entry under it is admitted on a `problem` only, and only for a member whose §5.1 publishes
# exactly `<member>__step`, or `<member>__step` and `<member>__init` with a composite that
# publishes `__init` (`spec_input_gates.problem_dep_on_non_problem_violation`,
# `problem_member_violation`).
_DEPS_YAML_REQUIRED_KEYS: frozenset[str] = frozenset({"components", "profiles"})
# The deps.yaml keys that name no closure node (issue #644, Part 3 P1): `libraries` lists the
# external libraries the node's sources reach (`spec/libraries/<id>.yaml`). `_parse_dep_entries`
# admits the key and skips it, so no closure code sees a library; `_parse_library_entries`
# reads it.
_DEPS_NON_NODE_KEYS: frozenset[str] = frozenset({"libraries"})
# (deps key, per-item id field). `kind = key.rstrip("s")` yields
# component/problem/profile/infrastructure.
# A `profile` entry is expanded into the components it selects by
# `expand_profile_dependencies` and never becomes a node of any closure (issue #175); the two
# readers that deliberately see the RAW declaration are `_dependency_readiness_state`'s
# trivial-leaf test and `workflow_conductor._direct_infra_dep_count` (the spec-input refusal).
_DEPS_KEY_KIND_FIELDS: tuple[tuple[str, str], ...] = (
    ("components", "component_id"),
    ("problems", "problem_id"),
    ("profiles", "profile_id"),
    ("infrastructure", "infrastructure_id"),
)
# The parse loop of `_parse_dep_entries` iterates `_DEPS_KEY_KIND_FIELDS` alone, so a key in
# `_DEPS_YAML_ALLOWED_KEYS` that neither the pair table nor `_DEPS_NON_NODE_KEYS` names would
# parse as well-formed with its entries DROPPED. The allowed set is therefore built as the
# union of the two.
_PAIRED_DEPS_KEYS = frozenset(key for key, _field in _DEPS_KEY_KIND_FIELDS)
_DEPS_YAML_ALLOWED_KEYS: frozenset[str] = _PAIRED_DEPS_KEYS | _DEPS_NON_NODE_KEYS


# Codex round 15 F2: identifiers from deps.yaml are interpolated into
# workspace/<kind>/<safe>/ paths. ANY of `spec_kind`, `spec_id`, `spec_version`
# containing path separators or traversal sequences would let the verifier walk
# out of the dependency subtree and treat unrelated files as readiness evidence.
# Reject anything outside this strict safe-token grammar before path construction.
_SAFE_ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


def _is_safe_path_token(s: Any) -> bool:
    """Strict whitelist for spec identifiers used in workspace paths.

    Rejects empty / non-str values, `..` traversal substrings, and any
    character outside `[A-Za-z0-9._+-]`. Path separators (`/`, `\\`), null
    bytes, and shell metacharacters are all rejected. Gates the `deps.yaml` ids
    (`_parse_dep_entries`, `_parse_library_entries`) and `_verify_dep_stage_detail`'s
    tokens.
    """
    if not isinstance(s, str) or not s:
        return False
    if ".." in s:
        return False
    return bool(_SAFE_ID_TOKEN_RE.match(s))


def _is_identifier_token(s: Any) -> bool:
    """A spec identifier read from the catalog or composed from it is a non-empty string."""
    return isinstance(s, str) and bool(s)


def _parse_dep_entries(
    deps_doc: dict[str, Any]
) -> tuple[list[tuple[str, str, str | None]], bool]:
    """Parse direct dependencies from a deps.yaml document.

    Returns `(entries, well_formed)`:
    - `entries`: list of `(spec_kind, spec_id, version_constraint)` triples.
      Constraint is None when the entry omits it (bare-string or dict without
      `version_constraint`).
    - `well_formed`: False when ANY structural problem is detected. Caller
      MUST treat well_formed=False as fail-closed.

    Strict schema enforced (Codex round 12 F1): the `dependencies` block must
    be a dict containing the REQUIRED keys `{"components", "profiles"}`, MAY also
    carry the optional `problems` key (a `problem` composing `problem`s, the system tier's
    Part 1) and the optional `infrastructure` key (the R1 harness dependency), and
    NOTHING else. An unknown key (e.g. typoed `component:`) or a missing required
    key marks the document as malformed — previously these silently yielded an
    empty entry list which readiness collapsed to
    vacuous-true. `infrastructure` is optional so the pre-R1 corpus
    (components + profiles only) stays well-formed with no migration.
    """
    entries: list[tuple[str, str, str | None]] = []
    deps = deps_doc.get("dependencies")
    if not isinstance(deps, dict):
        return entries, False
    keys = set(deps.keys())
    if keys - _DEPS_YAML_ALLOWED_KEYS:  # unknown key
        return entries, False
    if _DEPS_YAML_REQUIRED_KEYS - keys:  # a required key is missing
        return entries, False
    well_formed = True
    for kind_key, id_field in _DEPS_KEY_KIND_FIELDS:
        if kind_key not in deps:
            # Optional key (problems / infrastructure) omitted — skip without penalty.
            continue
        items = deps.get(kind_key)
        if not isinstance(items, list):
            well_formed = False
            continue
        # components->component, problems->problem, profiles->profile,
        # infrastructure->infrastructure
        kind = kind_key.rstrip("s")
        for item in items:
            if not isinstance(item, dict):
                # Codex round 22 F1: only canonical dict form is accepted.
                # Bare string items were silently normalized by taking the
                # final `/`-segment — that lets entries like
                # `"profile/foo"`, `"x/y/z"`, or `"../dep_a"` be rewritten
                # to different IDs and pass the gate against the wrong dep.
                # Require explicit `{component_id|profile_id, version_constraint}`.
                well_formed = False
                continue
            sid = item.get(id_field)
            constraint = item.get("version_constraint")
            if not (isinstance(sid, str) and sid.strip()):
                well_formed = False
                continue
            sid_token = sid.strip()
            # Codex round 15 F2: reject path-traversal in spec_id before
            # any workspace path is composed downstream.
            if not _is_safe_path_token(sid_token):
                well_formed = False
                continue
            if constraint is not None and not isinstance(constraint, str):
                well_formed = False
                continue
            c = constraint.strip() if isinstance(constraint, str) and constraint.strip() else None
            entries.append((kind, sid_token, c))
    return entries, well_formed


def _parse_library_entries(deps_doc: dict[str, Any]) -> tuple[list[str], bool]:
    """The external libraries a deps.yaml declares under `dependencies.libraries` (issue #644,
    Part 3 P1), as `(library_ids, well_formed)` in declared order. Absent key: `([], True)`.
    Each entry is `{library_id: <token>}`; anything else, or an id declared twice, is malformed.
    Whether the id names a declaration under `spec/libraries/` is the spec-input gate's
    question (`external_library.declaration_violation`)."""
    deps = deps_doc.get("dependencies") if isinstance(deps_doc, dict) else None
    if not isinstance(deps, dict):
        return [], False
    items = deps.get("libraries", [])
    if not isinstance(items, list):
        return [], False
    ids: list[str] = []
    for item in items:
        sid = item.get("library_id") if isinstance(item, dict) else None
        if (not isinstance(item, dict) or set(item) != {"library_id"}
                or not _is_safe_path_token(sid) or sid in ids):
            return [], False
        ids.append(sid)
    return ids, True


# The named `{reason}` values `expand_profile_dependencies` returns through
# `_profile_expansion_failure` mean the registry WAS read and the declared closure does not
# resolve — a spec-author defect no leaf can repair. `spec_catalog_corrupt`, which the expansion
# can also surface, is the registry-read failure and is reported as itself. (Until issue #250
# PR-2 the two classes had opposite freshness dispositions; the derivation key refuses every one
# of them the same way, as an unresolvable compile input.)
def _profile_expansion_failure(reason: str, detail: str) -> dict[str, str]:
    """The constructor of an `expand_profile_dependencies` profile error. Every `reason` is a
    literal at its call site; the reason a caller reads decides its message
    (`_derived_closure_graph` names it in the unresolvable compile input; the launch-time
    closure resolver reports it to the operator)."""
    return {"reason": reason, "detail": detail}


def expand_profile_dependencies(
    repo_root: Path,
    spec_ref: Any,
    entries: list[tuple[str, str, str | None]],
    catalog: dict[tuple[str, str], tuple[str, ...]],
) -> tuple[list[tuple[str, str, str | None]], list[dict[str, Any]], dict[str, str] | None]:
    """Resolve every `profile` dependency entry into the `component` entries it SELECTS.

    A `profile` is a compile-time component-selection policy resolved by the host, not a
    certified code node (issue #175; `docs/SPEC.md` / `docs/GLOSSARY.md`). It therefore must
    never appear as a node in any dependency closure: not in `all_nodes`, not in the
    `--with-deps` schedule, not in the staged sources, the Makefile objects, the closure
    bindings, or the pre_judge DAG gate. Every one of those layers walks the closure
    kind-blind, so the single place that decides it is HERE — the moment `_parse_dep_entries`
    has produced the raw declaration and before any consumer treats an entry as a node.

    Takes `_parse_dep_entries`'s output and returns
    `(expanded_entries, profiles_record, error)`:

    - `expanded_entries`: the same list with each `("profile", id, constraint)` triple replaced
      by the `("component", …)` triples that profile's own `deps.yaml` declares. Non-profile
      entries keep their order and their constraints.
    - `profiles_record`: one dict per adopted profile, in declaration order —
      `{node_key, profile_id, profile_version, version_constraint, components:[{component_id,
      version_constraint}]}`. The adopted profile's own version is pinned here by the SAME rule
      the graph builder uses for a node (`_matching_dep_versions`' descending head). The
      components carry only the constraint the profile declares, never a resolved version: the
      version each component resolves to is decided by the graph builder intersecting every
      requiring edge, and writing it here too would make it a fact two layers read.
    - `error`: `None`, or `{reason, detail}`. Fail-closed with NO partial expansion — a caller
      that receives an error must not use `expanded_entries`.

    The reasons are the `profile_*` literals passed to `_profile_expansion_failure`, plus
    `spec_catalog_corrupt`. Nesting (a profile
    adopting a profile) is refused by name rather than recursed: no spec needs it today, and a
    named refusal is one line to relax if one ever does.

    A component may be selected by two adopted profiles: the two constraints are AND-joined
    (the same whitespace-joined grammar `_matches_version_constraint` evaluates), which is the
    edge-intersection rule `build_dependency_graph` applies between requiring edges. When the
    join matches no catalog version the two profiles disagree — `profile_component_conflict`.
    A component declared BOTH directly by the adopting node and by one of its profiles is
    `profile_component_declared_twice`: the component set has exactly one source per component
    (issue #175 D5), so the adopting node declares only what no profile selects for it.
    """
    # Imported lazily, like every other `tools.*` import in this module: it also runs as a
    # SCRIPT (`python3 tools/orchestration_runtime.py`), where the repository root is not on
    # `sys.path` and a top-level `from tools...` import aborts the CLI at load.
    from tools.spec_input_gates import infra_dep_declared_violation

    expanded: list[tuple[str, str, str | None]] = []
    profiles_record: list[dict[str, Any]] = []
    own_component_ids = {sid for kind, sid, _c in entries if kind == "component"}
    # component_id -> index into `expanded`, for the two-profile merge.
    profile_component_slot: dict[str, int] = {}

    def _fail(reason: str, detail: str) -> tuple[list, list, dict[str, str]]:
        return [], [], _profile_expansion_failure(reason, detail)

    for kind, sid, constraint in entries:
        if kind != "profile":
            expanded.append((kind, sid, constraint))
            continue
        matched = _matching_dep_versions(catalog, "profile", sid, constraint)
        if not matched:
            return _fail(
                "profile_unresolvable",
                f"{spec_ref}: profile/{sid} constraint {constraint!r} has no matching "
                f"catalog version",
            )
        profile_version = matched[0]
        try:
            profile_ref = resolve_spec_ref_for(repo_root, "profile", sid)
        except SpecCatalogCorruption as exc:
            # NOT a `profile_*` reason on purpose: this one IS the
            # registry-read failure, reported under the name every other catalog reader uses.
            return [], [], {"reason": "spec_catalog_corrupt", "detail": str(exc)}
        if not profile_ref:
            return _fail(
                "profile_spec_ref_unresolved",
                f"{spec_ref}: no unique spec directory in catalog for profile/{sid}",
            )
        profile_deps = _read_deps_yaml(repo_root, profile_ref)
        if not isinstance(profile_deps, dict):
            return _fail(
                "profile_deps_unreadable",
                f"{spec_ref}: {profile_ref}/deps.yaml (profile/{sid}) is missing or unparseable",
            )
        p_entries, p_well_formed = _parse_dep_entries(profile_deps)
        if not p_well_formed:
            return _fail(
                "profile_deps_malformed",
                f"{spec_ref}: {profile_ref}/deps.yaml (profile/{sid}) has a malformed "
                f"dependency schema",
            )
        nested = [s for k, s, _c in p_entries if k == "profile"]
        if nested:
            return _fail(
                "profile_nesting_unsupported",
                f"{spec_ref}: profile/{sid} adopts profile(s) {sorted(nested)}; a profile "
                f"selects components only, and profile nesting is not supported",
            )
        infra_count = sum(1 for k, _s, _c in p_entries if k == "infrastructure")
        infra_violation = infra_dep_declared_violation(infra_count)
        if infra_violation:
            return _fail(
                "profile_declares_infrastructure",
                f"{spec_ref}: profile/{sid}: {infra_violation}",
            )
        # A profile selects COMPONENTS (issue #175). Since `problems` is an allowed deps.yaml key
        # (the system tier, Part 1), a profile's `problems:` entry would parse well-formed and
        # the component loop below would drop it silently; it is refused by name instead.
        declared_problems = sorted(s for k, s, _c in p_entries if k == "problem")
        if declared_problems:
            return _fail(
                "profile_declares_problem",
                f"{spec_ref}: profile/{sid} declares problem(s) {declared_problems}; a profile "
                f"selects components only, and a `problem` dependency is declared by the "
                f"composing `problem` itself",
            )
        # The same for `libraries` (issue #644): an allowed key no expansion reads, so a
        # profile's declaration would be dropped silently. A library is declared by the node
        # whose sources reach it.
        if "libraries" in (profile_deps.get("dependencies") or {}):
            return _fail(
                "profile_declares_libraries",
                f"{spec_ref}: profile/{sid} declares `libraries`; a profile selects components "
                f"only, and an external library is declared by the spec whose sources reach it",
            )
        record_components: list[dict[str, Any]] = []
        for p_kind, cid, c_constraint in p_entries:
            if p_kind != "component":
                continue
            record_components.append(
                {"component_id": cid, "version_constraint": c_constraint}
            )
            if cid in own_component_ids:
                return _fail(
                    "profile_component_declared_twice",
                    f"{spec_ref}: component/{cid} is declared directly AND selected by "
                    f"profile/{sid}. A component has exactly one source: declare it directly "
                    f"only when no adopted profile selects it (issue #175).",
                )
            slot = profile_component_slot.get(cid)
            if slot is None:
                profile_component_slot[cid] = len(expanded)
                expanded.append(("component", cid, c_constraint))
                continue
            # Selected by an earlier profile too — AND-join the two constraints, which is the
            # same intersection `build_dependency_graph` performs between requiring edges.
            _k, _cid, prior_constraint = expanded[slot]
            terms = [t for t in (prior_constraint, c_constraint) if t]
            merged = " ".join(terms) if terms else None
            if not _matching_dep_versions(catalog, "component", cid, merged):
                return _fail(
                    "profile_component_conflict",
                    f"{spec_ref}: component/{cid} is selected by more than one adopted "
                    f"profile with incompatible constraints ({prior_constraint!r} vs "
                    f"{c_constraint!r}); no catalog version satisfies both",
                )
            expanded[slot] = ("component", cid, merged)
        profiles_record.append(
            {
                "node_key": f"profile/{sid}@{profile_version}",
                "profile_id": sid,
                "profile_version": profile_version,
                "version_constraint": constraint,
                "components": record_components,
            }
        )
    return expanded, profiles_record, None


# Codex round 31 F2: the freshness reader takes only the canonical grammar the conductor
# mints ids in. The previous reader regex (`^.+_(\d{8})_(\d{3})$`) accepted ANY prefix —
# uppercase letters, underscores between slug parts, slashes in the path
# tail, etc. — so a planted `FAKE__20991231_999/binary_meta.json` outranked
# legitimate artifacts despite never having been issued by any writer.
# Anchoring both ends to the strict slug grammar closes that trust gap. (A writer-side copy,
# `_SLUG_DATE_SEQ3_PATTERN`, went in issue #643 PR-3 with the reservation and launch refusals
# that read it: the ids are the conductor's own, and a non-canonical one is never selected.)
_FRESHNESS_CANONICAL_ID_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*_([0-9]{8})_([0-9]{3})$"
)


def _freshness_key_from_id(name: str) -> tuple[str, int] | None:
    """Parse a canonical runtime-issued id (`<slug>_<YYYYMMDD>_<seq3>`) and
    return an ordering key `(date_str, seq_int)`. None when the name does
    not match the strict canonical grammar.

    The ordering key does not include the slug (Codex round 35 F1): two artifacts with the
    same `<YYYYMMDD>_<seq3>` under one node root are ordered by the selection policy's
    tie-breaker (`select_eligible`, the meta ref), never silently by slug. Since issue #250
    PR-2 this is the ordering token of `_stage_meta_candidates`, and the "latest" it used
    to pick is no longer what certifies — the derivation key is.

    Codex round 31 F2: the matcher takes only the canonical grammar the conductor mints.
    """
    m = _FRESHNESS_CANONICAL_ID_RE.match(name)
    if m is None:
        return None
    return (m.group(1), int(m.group(2)))


def _closure_signature(
    graph: Mapping[str, Any],
) -> tuple[list[list[Any]], list[str], list[str]]:
    """Canonical comparable form of a dependency graph: the sorted `all_nodes`
    `[[node_key, topo_level], ...]`, the sorted `transitive_deps` node_key list, AND the
    sorted `profiles[].node_key` list. Its one caller passes `build_dependency_graph`'s
    in-process result, whose shape that builder fixes; the shape is not re-checked here
    (issue #643).

    `all_nodes` alone is NOT a faithful signature. `topo_level` is a node's height, so two
    genuinely different closures can share every `(node_key, topo_level)` pair: with nodes
    a/b/c, the shapes `a→b, a→c, b→c` and `a→b→c` both give heights 2/1/0. They differ only
    in whether `c` is a DIRECT dep of `a` or a transitive one — exactly what
    `transitive_deps` records, and exactly the kind of `deps.yaml` edit that must re-certify
    `a`. Including the transitive set also pins the direct set, which is
    `all_nodes − {self} − transitive`.

    `via` paths are deliberately excluded: they are derived from the same edges, and the
    compile-key derivation (`_derived_closure_graph`) skips computing them
    (`include_via=False`) because the enumeration is exponential on a wide diamond.

    The signature is injective for the property that matters, not for the whole graph: it pins
    the SUBJECT's own direct-dep set exactly, so any edit to the subject's `deps.yaml` (an edge
    added or removed, a version bump renaming a node_key) always changes it. Two closures can
    still collide by reshaping edges DEEPER in the transitive subgraph while preserving every
    height — but that edit belongs to a deeper node's own `deps.yaml`, and it changes THAT
    node's signature, which enters THAT node's compile key (every closure node's is
    recomputed under `--with-deps`).

    Sorting on `node_key` alone is total: a graph names each `(kind, spec_id)` at exactly one
    version (`build_dependency_graph.node_key_of`), so node_keys are unique. It also avoids
    ordering on `topo_level`, whose value may be any JSON
    type — a mixed-type sort key would raise, and a `str()` cast would order 10 before 2. The
    level still participates in the EQUALITY comparison, which is all the caller needs."""
    out = sorted(([node["node_key"].strip(), node.get("topo_level")]
                  for node in graph["all_nodes"]), key=lambda item: item[0])
    trans = [node["node_key"].strip() for node in graph["transitive_deps"]]
    trans.sort()
    # Issue #175: the adopted `profile` set is part of the resolution, and it is the ONLY part
    # that does not show up in `all_nodes` — the profile is not a node. Without it, bumping a
    # profile's `spec_version` (its §3 parameter/compatibility constraints are what an adopter's
    # `algorithm` is written to honour, and what `_leaf_profile_spec_document` inlines) left
    # every adopter's signature byte-identical, so the R6-lite comparison this signature was
    # written for called it fresh — while its IR's `profile_selection` went on naming the
    # retired version. Measured on this tree before the fix: identical signature across a
    # 0.1.1 -> 0.9.0 bump of the adopted profile.
    #
    # Since issue #250 PR-2 this signature is hashed into the COMPILE DERIVATION KEY of the
    # target (`dependency_graph`) and compared with nothing else. The one-level asymmetry
    # issue #175 recorded (a profile bump restaled the adopter but not the adopter's
    # dependents) is closed by construction there: a dependent's key binds the adopter's
    # SELECTED IR by output hash, so the bump reaches it through the adopter's re-derivation
    # exactly when the adopter's IR changed.
    #
    # A MISSING key normalizes to `[]` rather than to `None`: a node adopting no profile
    # derives `[]`, and a sidecar written before issue #175 reads the same way.
    profile_keys = sorted(entry["node_key"].strip() for entry in graph.get("profiles") or [])
    return (out, trans, profile_keys)


# --- derivation-key certification (issue #250 PR-2) -----------------------------------
#
# "Certified" is decided by the DERIVATION KEY (`tools/derivation.py`): a phase output of
# `(node_key, step)` is ELIGIBLE when its certifying stage meta records `verification_status:
# pass`, is not revoked, still hashes to what the certifying run stamped (`artifact_hashes`),
# and carries a `derivation_key` EQUAL to the key recomputed NOW over the phase's contract
# inputs (`phase_derivation_inputs`). Among the eligible outputs of one key the selection
# policy (`select_eligible`, v1 = the latest attempt) chooses ONE, and what binds an output binds
# that one's `output_hash` — the node's own next phase, and a dependent node's Build.
#
# The recomputation is recursive by construction: a node's compile and generate keys ask each
# closure member for a certified output and bind its published surface, the build key binds its
# generate output, so a dependency re-derived within its version changes the keys its change
# reaches with no bookkeeping, and a dependency re-derived to byte-identical output changes
# nobody's (`output_hash` is stage-relative). One
# `DerivationResolver` memoises the selections of ONE evaluation, because nothing on disk
# changes during it and the closure is a DAG in which the same member is reached many times.
#
# What the key does NOT decide is kept beside it and stated where it applies: the current
# `--stage compile` validator's verdict on the selected IR (`_ir_certification`, issue #238,
# a rule-drift defense the key cannot carry because the rule is not an input), and the
# reservation, which says where THIS orchestration writes and is no longer consulted for
# whether an artifact is certified (`ir_not_latest` / `pipeline_not_latest` are gone with it).
#
# What this replaced (PR-2 of issue #250): the three comparisons of `docs/ORCHESTRATION.md`
# 13a / 13b — `_dependency_resolution_freshness` (the node_key set of the sidecar against the
# registry), `_dependency_binding_freshness` (`closure_bindings[].model_source_sha256` against
# the staged selection) — and the id chain of `_phase_certified` (`source_ir_id`,
# `source_source_id`, `trial_meta.source_binary_id`, "latest under the root"). Each of them
# is a projection of the key: the sidecar signature is `dependency_graph`, the binding is
# `closure[].source`, the id chain is the upstream `output_hash`. A meta with no key — every
# meta stamped before PR-1 — is not eligible (`derivation_key_missing`), by decision 6 of the
# plan: no backfill, the corpus re-derives once.

#: The certifying phase each phase binds to, in the node's own chain.
_UPSTREAM_STEP: dict[str, str | None] = {
    "compile": None, "generate": "compile", "build": "generate", "validate": "build"}

#: The refusal reason when a phase has NO output at all (no stage directory carries its
#: certifying meta), per phase.
_NO_OUTPUT_REASON: dict[str, str] = {
    "compile": "ir_not_found", "generate": "source_not_found",
    "build": "binary_not_found", "validate": "verdict_not_found"}


class DerivationSelection:
    """The answer of `DerivationResolver.select` for ONE `(node_key, step)`: whether a
    certified output stands under the key recomputed now, which one, and — cumulatively —
    the ids of every upstream phase's selected output (`ir_ref` for all, `pipeline_ref` /
    `source_id` from generate, `binary_id` from build, `run_id` from validate).

    `reason` is `None` when `ok`, else the FIRST clause that refused, spelled like the
    predicate's other reasons: an upstream's reason when the chain stops above this phase,
    `derivation_inputs_unresolvable:<input>` when the key cannot be computed,
    `<step>_not_found` when nothing was ever produced, `derivation_key_missing` when no
    output carries a key, `derivation_key_mismatch:<first differing input>` when the keyed
    outputs carry other keys (diffed against the one closest to today's inputs), and the stage meta's own reason (`revoked`,
    `verification_status_not_pass`, `artifact_hash_mismatch:<ref>`) when an output carries
    THIS key but is not eligible — read off the latest such output, with its `revoked` /
    `last_fail_reason` / `revocation_severity` / `revocation_repair_strategy`, which the
    conductor seeds a repair from."""

    __slots__ = ("binary_id", "derivation_inputs", "derivation_key", "ir_id", "ir_ref",
                 "last_fail_reason", "meta_path", "node_key", "ok", "output_hash",
                 "pipeline_ref", "reason", "revocation_repair_strategy",
                 "revocation_severity", "revoked", "run_id", "source_id", "step")

    def __init__(self, node_key: str, step: str) -> None:
        self.ok = False
        self.reason: str | None = None
        self.node_key = node_key
        self.step = step
        self.derivation_key: str | None = None
        self.derivation_inputs: dict[str, Any] | None = None
        self.meta_path: Path | None = None
        self.output_hash: str | None = None
        self.ir_ref: str | None = None
        self.ir_id: str | None = None
        self.pipeline_ref: str | None = None
        self.source_id: str | None = None
        self.binary_id: str | None = None
        self.run_id: str | None = None
        self.revoked = False
        self.last_fail_reason: str | None = None
        self.revocation_severity: str | None = None
        self.revocation_repair_strategy: str | None = None

    def inherit(self, upstream: DerivationSelection) -> None:
        for name in ("ir_ref", "ir_id", "pipeline_ref", "source_id", "binary_id", "run_id"):
            setattr(self, name, getattr(upstream, name))

    def stage_dir(self) -> Path | None:
        return None if self.meta_path is None else self.meta_path.parent

    def detail(self) -> dict[str, Any]:
        """The `detail` mapping `_phase_certified` returns (and `check-phase-certified`
        prints): the reason, the resolved refs, and the repair-seed fields."""
        return {
            "reason": self.reason, "ir_ref": self.ir_ref, "ir_id": self.ir_id,
            "pipeline_ref": self.pipeline_ref, "source_id": self.source_id,
            "binary_id": self.binary_id, "run_id": self.run_id,
            "derivation_key": self.derivation_key, "output_hash": self.output_hash,
            "revoked": self.revoked, "last_fail_reason": self.last_fail_reason,
            "revocation_severity": self.revocation_severity,
            "revocation_repair_strategy": self.revocation_repair_strategy,
        }


def _stage_meta_candidates(
    repo_root: Path, node_key: str, step: str, target_id: str | None = None,
) -> list[tuple[tuple[Any, ...], Path]]:
    """Every certifying stage meta of `(node_key, step)` in the workspace, each with the
    ordering token the selection policy ranks by: the canonical `(date, seq)` of the stage
    id, preceded by the pipeline's for a pipeline stage — so outputs are ordered across
    pipelines too, and a `src_<date>_<seq>` that repeats in two pipelines of one node is
    not a tie. A directory whose name is not a canonical runtime id is not a candidate
    (the same rule `_select_max_by_id_extracted` applied).

    Compile output is target-free (`workspace/ir/<safe>/<ir_id>/`); every later phase's is
    read from ONE target's tree, `workspace/pipelines/<safe>/<target_id>/<pipeline_id>/`
    (issue #284). A pipeline directory directly under `<safe>/` — the pre-R4-a layout — is
    never a candidate: nothing here lists `<safe>/` itself, and a target id cannot be spelled
    as a store id (`target_profile.is_target_id`). `target_id` None answers no candidate for
    a pipeline phase; the resolver refuses that case by name before asking."""
    safe = _node_key_to_safe(node_key)
    meta_name = CERTIFYING_META_FILENAME_BY_STEP[step]
    out: list[tuple[tuple[Any, ...], Path]] = []
    if step == "compile":
        root = repo_root / "workspace" / "ir" / safe
        if root.is_dir():
            for d in root.iterdir():
                key = _freshness_key_from_id(d.name)
                if key is not None and (d / meta_name).is_file():
                    out.append(((key,), d / meta_name))
        return out
    if not is_target_id(target_id):
        return out
    pipes = repo_root / pipelines_dir(safe, str(target_id))
    if not pipes.is_dir():
        return out
    for pipe in pipes.iterdir():
        pkey = _freshness_key_from_id(pipe.name)
        if pkey is None or not pipe.is_dir():
            continue
        if step == "validate":
            runs = pipe / "runs"
            for run in (runs.iterdir() if runs.is_dir() else ()):
                rkey = _freshness_key_from_id(run.name)
                meta = run / safe / meta_name
                if rkey is not None and meta.is_file():
                    out.append(((pkey, rkey), meta))
            continue
        sub = pipe / ("source" if step == "generate" else "binary")
        for d in (sub.iterdir() if sub.is_dir() else ()):
            skey = _freshness_key_from_id(d.name)
            if skey is not None and (d / meta_name).is_file():
                out.append(((pkey, skey), d / meta_name))
    return out


def _selection_refs_from_meta(sel: DerivationSelection, meta_path: Path, repo_root: Path) -> None:
    """Fill the ids of the SELECTED output into `sel` from where its meta lives:
    `workspace/ir/<safe>/<ir_id>/ir_meta.json`, `<pipeline>/source/<source_id>/...`,
    `<pipeline>/binary/<binary_id>/...`, `<pipeline>/runs/<run_id>/<safe>/...`."""
    rel = _normalize_rel_posix(str(meta_path.parent.relative_to(repo_root)))
    if sel.step == "compile":
        sel.ir_ref = rel
        sel.ir_id = meta_path.parent.name
        return
    if sel.step == "validate":
        pipe_dir = meta_path.parent.parent.parent.parent
        sel.run_id = meta_path.parent.parent.name
    else:
        pipe_dir = meta_path.parent.parent.parent
        if sel.step == "generate":
            sel.source_id = meta_path.parent.name
        else:
            sel.binary_id = meta_path.parent.name
    sel.pipeline_ref = _normalize_rel_posix(str(pipe_dir.relative_to(repo_root)))


def _twin_in_pipeline(repo_root: Path, pipe_dir: Path, stage: str, output_hash: str | None
                      ) -> str | None:
    """The id of a certified `stage` output under `pipe_dir` whose output hash is
    `output_hash` — the byte-identical twin of a selected output that lives in another
    pipeline — or None. Newest first, so two twins answer the newer."""
    if not output_hash:
        return None
    meta_name = "source_meta.json" if stage == "source" else "binary_meta.json"
    root = pipe_dir / stage
    found: list[tuple[tuple[str, int], str]] = []
    for d in (root.iterdir() if root.is_dir() else ()):
        key = _freshness_key_from_id(d.name)
        meta_path = d / meta_name
        if key is None or not meta_path.is_file():
            continue
        ok, detail = _stage_meta_certification(repo_root, meta_path)
        if not ok:
            continue
        meta_ref = _normalize_rel_posix(str(meta_path.relative_to(repo_root)))
        if _meta_output_hash(detail["meta"], meta_ref) == output_hash:
            found.append((key, d.name))
    return max(found)[1] if found else None


class DerivationResolver:
    """ONE evaluation's memo of certified selections over the workspace, for ONE target.

    `select(node_key, step)` answers `DerivationSelection` for the node's chain up to
    `step`, recursing through the node's own upstream phases and — inside
    `phase_derivation_inputs` — through every closure member's selections. Memoised per
    `(node_key, step)`; a resolver is created per predicate evaluation and never outlives
    it, so a later write to the workspace is seen by the next evaluation. `spec_refs` pins
    the spec directory of a node the caller already resolved (the subject of a run, whose
    `spec_ref` the conductor holds); every other node's is read from the catalog, which
    must resolve it to exactly one directory.

    `target` is the target profile every pipeline phase is selected FOR (issue #284): a
    Generate / Build / Validate output is a `node_key × target` fact, read from that target's
    tree and keyed with its profile, and a closure member is selected for the same target as
    its consumer (one run is one target). Compile is target-free and answers without one. A
    resolver with no target refuses every pipeline phase by name (`target_unresolved`) rather
    than guessing which target the caller meant.

    The one exception to "one resolver, one target" is `resolve_comparands` (issue #324): the
    cross-target agreement (`cross_target_agreement`, issue #383) reads OTHER targets' certified
    builds, and the resolver it builds for each lives and dies inside that function. Only that function passes
    `comparand=True` (issue #345): the build key's host-probed leaves
    (`HOST_PROBED_TOOLCHAIN_LEAVES`) are then not probed but read off each candidate's own stamp
    (`_comparand_candidate_key`), because this host's probe of another target's compiler — which
    a run of this target need not have, and which is not the compiler that built a site-built
    variant — says nothing about the variant. `derivation_key` / `derivation_inputs` on such a
    resolver's CERTIFIED Build selection are the chosen candidate's stamped key and its substituted
    inputs, and on a refused one the key computed without the probed leaves, which no stamp
    carries; `resolve_comparands` reads neither."""

    def __init__(self, repo_root: Path, *, spec_refs: Mapping[str, str] | None = None,
                 target: TargetProfile | None = None, comparand: bool = False) -> None:
        self.repo_root = Path(repo_root)
        self.target = target
        self.comparand = comparand
        self._memo: dict[tuple[str, str], DerivationSelection] = {}
        self._in_progress: set[tuple[str, str]] = set()
        self._spec_refs: dict[str, str] = {
            str(k).strip(): _normalize_rel_posix(str(v).strip())
            for k, v in (spec_refs or {}).items() if isinstance(v, str) and v.strip()}

    def spec_ref(self, node_key: str) -> str | None:
        node_key = node_key.strip()
        if node_key in self._spec_refs:
            return self._spec_refs[node_key]
        try:
            kind, spec_id, _version = _parse_node_key_strict(node_key)
            candidates = _spec_ref_candidates(self.repo_root, kind, spec_id)
        except (ValueError, SpecCatalogCorruption):
            return None
        if len(candidates) != 1:
            return None
        ref = _normalize_rel_posix(next(iter(candidates)))
        self._spec_refs[node_key] = ref
        return ref

    def select(self, node_key: str, step: str) -> DerivationSelection:
        node_key = node_key.strip()
        step_token = step.strip().lower()
        memo_key = (node_key, step_token)
        cached = self._memo.get(memo_key)
        if cached is not None:
            return cached
        sel = DerivationSelection(node_key, step_token)
        if memo_key in self._in_progress:
            # A closure that reaches itself. `build_dependency_graph` refuses a cycle before
            # any key is computed, so this is a guard against a malformed sidecar rather than
            # a path a healthy registry reaches; it must not recurse without bound.
            sel.reason = "dependency_cycle"
            return sel
        self._in_progress.add(memo_key)
        try:
            self._select_into(sel)
        finally:
            self._in_progress.discard(memo_key)
        self._memo[memo_key] = sel
        return sel

    def _select_into(self, sel: DerivationSelection) -> None:
        node_key, step_token = sel.node_key, sel.step
        if step_token not in DERIVATION_STEPS:
            sel.reason = f"unsupported_step:{step_token}"
            return
        try:
            _parse_node_key_strict(node_key)
        except ValueError:
            sel.reason = "node_key_invalid"
            return
        upstream_step = _UPSTREAM_STEP[step_token]
        if upstream_step is not None and self.target is None:
            sel.reason = "target_unresolved"
            return
        if upstream_step is not None:
            upstream = self.select(node_key, upstream_step)
            sel.inherit(upstream)
            if not upstream.ok:
                sel.reason = upstream.reason
                sel.revoked = upstream.revoked
                sel.last_fail_reason = upstream.last_fail_reason
                sel.revocation_severity = upstream.revocation_severity
                sel.revocation_repair_strategy = upstream.revocation_repair_strategy
                return
        candidates = _stage_meta_candidates(
            self.repo_root, node_key, step_token,
            self.target.target_id if self.target is not None else None)
        # A pipeline is ONE chain. The build key binds the source's OUTPUT hash, not its
        # pipeline, so a byte-identical source in another pipeline carries the same build
        # key — and the same holds for a verdict over a byte-identical binary. Such an
        # output is selectable ONLY when its own pipeline holds the whole upstream chain as
        # byte-identical twins (a certified source with the selected source's output hash;
        # for a verdict, a certified binary with the selected binary's too): the selection
        # then re-points to that pipeline's twins, so the refs a run adopts are one chain.
        # Without the twins the output is not a candidate: adopting it would hand the run a
        # `pipeline_ref` from one pipeline beside a `source_id` from another — a lineage
        # naming a source directory that is not there, a Validate reading it, a Generate
        # revocation resolving to a meta that does not exist (correctness round 1, F1).
        # With them, a `--rederive` whose attempts reproduce their outputs into a fresh
        # pipeline leaves Build and Validate certified, as 13a says (correctness round 2).
        twins: dict[Path, dict[str, str]] = {}
        if step_token in ("build", "validate") and sel.pipeline_ref:
            home = self.repo_root / sel.pipeline_ref
            wanted = {"source": self.select(node_key, "generate").output_hash}
            if step_token == "validate":
                wanted["binary"] = upstream.output_hash
            kept: list[tuple[tuple[Any, ...], Path]] = []
            for order, meta_path in candidates:
                pipe_dir = meta_path.parents[3 if step_token == "validate" else 2]
                if pipe_dir == home:
                    kept.append((order, meta_path))
                    continue
                if pipe_dir not in twins:
                    found = {stage: _twin_in_pipeline(self.repo_root, pipe_dir, stage, h)
                             for stage, h in wanted.items()}
                    twins[pipe_dir] = ({k: v for k, v in found.items() if v is not None}
                                       if all(found.values()) else {})
                if twins[pipe_dir]:
                    kept.append((order, meta_path))
            candidates = kept
        if not candidates:
            # Nothing was ever produced: say so before computing a key nobody stamped (a
            # never-derived node's spec files may not even exist yet — that is the closure
            # driver's ordinary "not built" answer, not a defect of its inputs).
            sel.reason = _NO_OUTPUT_REASON[step_token]
            return
        spec_ref = self.spec_ref(node_key)
        if spec_ref is None:
            sel.reason = "spec_ref_unresolved"
            return
        source_ref = (f"{sel.pipeline_ref}/source/{sel.source_id}"
                      if sel.pipeline_ref and sel.source_id else None)
        binary_ref = (f"{sel.pipeline_ref}/binary/{sel.binary_id}"
                      if sel.pipeline_ref and sel.binary_id else None)
        try:
            inputs = phase_derivation_inputs(
                self.repo_root, node_key=node_key, step=step_token, spec_ref=spec_ref,
                ir_ref=sel.ir_ref, source_ref=source_ref, binary_ref=binary_ref,
                resolver=self)
        except DerivationInputsUnresolvable as exc:
            sel.reason = str(exc)
            return
        key = _derivation_key(step_token, inputs)
        sel.derivation_key = key
        sel.derivation_inputs = inputs
        # A comparand resolver matches each Build candidate under the host-probed toolchain
        # leaves ITS stamp carries (issue #345): `inputs` was computed without them.
        per_candidate = self.comparand and step_token == "build"
        assert self.target is not None or not per_candidate

        from tools.derivation import (
            Candidate,
            differing_inputs,
            select_eligible,
            transformation_versions,
        )
        eligible: list[Candidate] = []
        refused: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        latest: tuple[tuple[Any, ...], Path, Any] | None = None
        # The diagnosis of a mismatch is read off an output that CARRIES a key (a newer
        # failed attempt has its stamp keys stripped — `write-step-result` non-pass — and
        # reading the diff off it would report `derivation_key_missing` for a node whose
        # certified output simply moved; correctness round 1, F6), and among those off the
        # one CLOSEST to today's inputs — fewest differing leaves, the latest on a tie. The
        # latest keyed output is not always the standing one: the real corpus holds a stale
        # sibling stamped under other closure hashes beside the selected IR, and diffing
        # against it reported an operator's own `deps.yaml` edit as `closure[0].ir`, whose
        # documented remedy sends them after a dependency that did not move (security
        # round 2, F1).
        keyed: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for order, meta_path in candidates:
            doc = _read_json_or_none(meta_path)
            if latest is None or order > latest[0]:
                latest = (order, meta_path, doc)
            if isinstance(doc, dict) and "derivation_key" in doc:
                keyed.append((order, doc))
            if not isinstance(doc, dict):
                continue
            wanted_key = (_comparand_candidate_key(doc, inputs, self.target)
                          if per_candidate else key)
            if wanted_key is None or doc.get("derivation_key") != wanted_key:
                continue
            ok, detail = _stage_meta_certification(self.repo_root, meta_path)
            if ok:
                meta_ref = _normalize_rel_posix(str(meta_path.relative_to(self.repo_root)))
                eligible.append(Candidate(
                    attempt_id=meta_ref, order=order,
                    output_hash=_meta_output_hash(detail["meta"], meta_ref), ref=meta_ref))
            else:
                refused.append((order, {**detail, "meta_path": meta_path}))
        # A REVOCATION shadows every older eligible output of the key. `revoke-artifact` is a
        # decision that the derivation must run again (an execute's `structural_violation` — a
        # finding no input carries), taken against the output
        # that was standing; an older output of the same key produced under the same inputs
        # is not an answer to it, and selecting it would let the decision fall through to a
        # skip. A newer FAILED attempt shadows nothing (a failed attempt is a record, never a
        # cache hit, and never a decision); an output produced AFTER the revocation — the
        # re-derivation it asked for — is selected as usual. For a comparand resolver the
        # candidates matched under their OWN stamped toolchain leaves, so a revocation shadows
        # older eligible Builds across compiler versions too, not only those of one key. That
        # drops the other target from the comparands (the cross-target agreement then reads
        # none from it) rather than comparing against a variant older than one the operator
        # decided must be re-derived; the revocation is resolved by that re-derivation.
        revoked_orders = [order for order, detail in refused if detail.get("revoked")]
        if revoked_orders:
            newest_revoked = max(revoked_orders)
            eligible = [c for c in eligible if c.order > newest_revoked]
        chosen = select_eligible(eligible)
        if chosen is not None:
            sel.ok = True
            sel.meta_path = self.repo_root / chosen.ref
            if per_candidate:
                chosen_doc = _read_json_or_none(sel.meta_path)
                assert isinstance(chosen_doc, dict)  # it matched a key above
                sel.derivation_key = str(chosen_doc["derivation_key"])
                sel.derivation_inputs = _comparand_substituted_inputs(
                    chosen_doc, inputs, self.target)
            sel.output_hash = chosen.output_hash
            _selection_refs_from_meta(sel, sel.meta_path, self.repo_root)
            for stage, stage_id in twins.get(self.repo_root / str(sel.pipeline_ref), {}).items():
                setattr(sel, "source_id" if stage == "source" else "binary_id", stage_id)
            return
        if refused:
            # An output of THIS key exists and is not eligible: say why — from the newest
            # revoked one when there is one (its repair seed is what the conductor needs),
            # else from the latest refused one.
            revoked = [item for item in refused if item[1].get("revoked")]
            _order, detail = max(revoked or refused, key=lambda item: item[0])
            sel.reason = str(detail.get("reason"))
            sel.revoked = bool(detail.get("revoked"))
            sel.last_fail_reason = detail.get("last_fail_reason")
            sel.revocation_severity = detail.get("revocation_severity")
            sel.revocation_repair_strategy = detail.get("revocation_repair_strategy")
            return
        assert latest is not None  # `candidates` was non-empty
        if not keyed:
            _order, _path, doc = latest
            sel.reason = ("stage_meta_unreadable" if not isinstance(doc, dict)
                          else "derivation_key_missing")
            return
        # A stamp under another transformation tuple is diagnosed by that tuple before any input
        # is compared: one PR's key change would otherwise name a member only one side has, and
        # send the operator after a dependency that did not move. It ranks after every stamp under
        # the current tuple, whose remedy is an input.
        current_tuple = list(transformation_versions()[step_token])
        closest: tuple[tuple[bool, int], tuple[Any, ...], list[str] | None] | None = None
        for order, doc in keyed:
            stale_tuple = doc.get("derivation_transformation") != current_tuple
            recorded = doc.get("derivation_inputs")
            diff = None
            if stale_tuple:
                diff = ["transformation"]
            elif isinstance(recorded, dict):
                # A comparand resolver diffs each stamp against the inputs substituted with
                # ITS OWN host-probed leaves, so the diagnosis names what moved rather than a
                # probe this host never ran; a stamp lacking an expected leaf names that leaf.
                against: Mapping[str, Any] | None = inputs
                missing: list[str] = []
                if per_candidate:
                    stamped = recorded.get("toolchain")
                    stamped = stamped if isinstance(stamped, Mapping) else {}
                    leaves = _host_probed_leaves(self.target)
                    missing = [f"toolchain.{leaf}" for leaf in leaves if leaf not in stamped]
                    against = {**inputs, "toolchain": {
                        **inputs["toolchain"],
                        **{leaf: stamped[leaf] for leaf in leaves if leaf in stamped}}}
                    if "libraries" in inputs:
                        # The libraries' files, off the same stamp (issue #644). A diagnosis
                        # only (the selection is `_comparand_substituted_inputs`, pinned); no
                        # row drives this branch, so it is not pinned.
                        stamped_libraries = _stamped_library_files(
                            recorded, inputs["libraries"])
                        if stamped_libraries is None:
                            missing.append("libraries")
                        else:
                            against["libraries"] = stamped_libraries
                    if "oracle" in inputs:
                        # The oracle's library files, off the same stamp (issue #680). A
                        # diagnosis only, as the libraries' above; no row drives it, so it is
                        # not pinned.
                        stamped_oracle = _stamped_oracle(recorded, inputs["oracle"])
                        if stamped_oracle is None:
                            missing.append("oracle")
                        else:
                            against["oracle"] = stamped_oracle
                diff = missing + differing_inputs(recorded, against)
            rank = (stale_tuple, len(diff) if diff is not None else sys.maxsize)
            if closest is None or rank < closest[0] or (rank == closest[0]
                                                        and order > closest[1]):
                closest = (rank, order, diff)
        assert closest is not None
        diff = closest[2]
        differing = (diff[0] if diff else "key_version") if diff is not None else "derivation_inputs"
        sel.reason = f"derivation_key_mismatch:{differing}"


def _certified_ir_dir(
    repo_root: Path, kind: str, spec_id: str, version: str,
    *, resolver: DerivationResolver | None = None,
) -> Path | None:
    """The IR directory of the SELECTED certified Compile output of `(kind, id, version)` —
    the IR every reader of a dependency's IR binds to (its published surface, its signature
    pins, the harness pin). `None` when no Compile output is eligible under the key
    recomputed now. Before issue #250 PR-2 this was the LATEST ir dir regardless of status."""
    if not (
        _is_identifier_token(kind)
        and _is_identifier_token(spec_id)
        and _is_identifier_token(version)
    ):
        return None
    resolver = resolver or DerivationResolver(repo_root)
    sel = resolver.select(f"{kind}/{spec_id}@{version}", "compile")
    return sel.stage_dir() if sel.ok else None


#: The certifying phase each dependency-readiness stage asks of a dependency.
_READINESS_STAGE_STEP: dict[str, str] = {
    "ir_ref": "compile", "pipeline_ref": "build", "aggregate_verdict": "validate"}


def _verify_dep_stage_detail(
    repo_root: Path, kind: str, spec_id: str, version: str, stage: str,
    *, resolver: DerivationResolver | None = None, target: TargetProfile | None = None,
) -> tuple[bool, str | None]:
    """Whether a dependency `(kind, id, version)` evidences `stage` completion, WITH the
    reason when it does not.

    This is the ONE primitive that decides whether a dependency stage certifies (issue
    #178). The launch gate (`_certify_and_collect_dep_artifacts`) and the closure driver
    (`run_workflow._dependency_node_readiness`) both go through it; neither re-derives the
    judgment.

    Since issue #250 PR-2 each stage is a derivation-key question of the dependency's own
    chain (`DerivationResolver.select`): `ir_ref` — its Compile has an eligible output under
    the key recomputed now; `pipeline_ref` — its Build has (which requires its Generate and
    Compile to); `aggregate_verdict` — its Validate has. The key carries what the readiness
    stages used to compare beside the status — the dependency resolution (13a, now the
    compile key's `dependency_graph`), the staged sources (13b, now the build
    key's `closure[].source`) — and what they could not see: the spec text, the IR content,
    the toolchain, the transformation version. A dependency whose Validate meta was written
    before PR-1 carries no key and is not ready (`derivation_key_missing`); the closure
    re-derives it once.

    `detail` is `None` when ready and the selection's reason otherwise, prefixed with the
    node_key so `_stale_dependency_details`'s reader — the launch gate's reject message and
    the closure driver's `dependency_node_begin.detail` — names the node.

    `pipeline_ref` and `aggregate_verdict` are questions about ONE target (issue #284): the
    resolver's, or `target` when no resolver is passed. With neither, they answer
    `target_unresolved` — never ready — and `ir_ref`, which is target-free, still answers.

    `aggregate_verdict` also asks the dependency's cross-target agreement once its Validate is
    certified (`cross_target_agreement`, issue #383): a dependency whose variant disagrees with
    another target's, or whose comparison cannot be evaluated, is not ready
    (`<node> validate: cross_target_disagreement:<targets>` /
    `cross_target_unevaluable:<cause>`), though its Validate stays certified on its own key.
    This one clause reaches the launch gate, `_stale_dependency_details` and the closure driver.
    """
    if not (
        _is_safe_path_token(kind)
        and _is_safe_path_token(spec_id)
        and _is_safe_path_token(version)
    ):
        return (False, f"{kind}/{spec_id}@{version}: unsafe identifier token")
    step = _READINESS_STAGE_STEP.get(stage)
    if step is None:
        raise ValueError(f"unknown readiness stage: {stage!r}")
    node_key = f"{kind}/{spec_id}@{version}"
    resolver = resolver or DerivationResolver(repo_root, target=target)
    sel = resolver.select(node_key, step)
    if not sel.ok:
        return (False, f"{node_key} {step}: {sel.reason}")
    if step == "validate":
        not_ready = cross_target_not_ready_reason(
            cross_target_agreement(repo_root, node_key, resolver=resolver))
        if not_ready is not None:
            return (False, f"{node_key} {step}: {not_ready}")
    return (True, None)


def _target_harness_entries_for_spec_ref(
    repo_root: Path, spec_ref: Any, target: TargetProfile | None,
) -> list[tuple[str, str, str]]:
    """`target_profile.target_harness_entries` for the node at `spec_ref`, its kind read from
    the CATALOG (never from `deps.yaml`'s self-declared `spec_kind`, which no schema carries): a
    spec_id the catalog registers under exactly one kind has that kind; any other answer is
    treated as a non-`infrastructure` node, which asks for the harness — the stricter
    question. The catalog is read only under a target. Raises `SpecCatalogCorruption`."""
    if target is None:
        return []
    from tools.target_profile import target_harness_entries
    spec_id = Path(str(spec_ref or "").strip().rstrip("/")).name
    kinds = {k for (k, sid) in _load_spec_catalog(str(repo_root.resolve())) if sid == spec_id}
    return target_harness_entries(target, next(iter(kinds)) if len(kinds) == 1 else None)


def _stale_dependency_details(
    repo_root: Path, spec_ref: Any, *, resolver: DerivationResolver | None = None,
    target: TargetProfile | None = None,
) -> list[str]:
    """Actionable readiness reports for the direct dependencies of `spec_ref`: for each one
    the FIRST readiness stage that refuses it and why (a key that moved names the input that
    moved: `derivation_key_mismatch:<input>`). Used to turn an opaque
    `direct_dependency_*_readiness_not_pass` into a message that names the node and the
    remedy."""
    deps_doc = _read_deps_yaml(repo_root, spec_ref)
    if not isinstance(deps_doc, dict):
        return []
    entries, well_formed = _parse_dep_entries(deps_doc)
    if not well_formed:
        return []
    try:
        entries = list(entries) + _target_harness_entries_for_spec_ref(
            repo_root, spec_ref, target)
        if not entries:
            return []
        catalog = _load_spec_catalog(str(repo_root.resolve()))
    except SpecCatalogCorruption:
        return []
    # Issue #175: `profile` entries are policies, not dependency nodes — expand them into the
    # components they select so the report is about the nodes readiness actually verifies.
    # An expansion failure is reported as a detail rather than dropped: readiness itself
    # fails closed on it, and this line is what names the offending profile to the operator.
    entries, _profiles_record, expand_error = expand_profile_dependencies(
        repo_root, spec_ref, entries, catalog
    )
    if expand_error is not None:
        return [f"{spec_ref}: {expand_error['reason']}: {expand_error['detail']}"]
    resolver = resolver or DerivationResolver(repo_root, target=target)
    details: list[str] = []
    for kind, spec_id, constraint in entries:
        for version in _matching_dep_versions(catalog, kind, spec_id, constraint):
            for stage in _DEPENDENCY_READINESS_STAGES:
                ok, detail = _verify_dep_stage_detail(
                    repo_root, kind, spec_id, version, stage, resolver=resolver)
                if not ok:
                    if detail:
                        details.append(detail)
                    break
    return details


# --- phase certification (issue #177) -------------------------------------------------
#
# "Certified" is a property of the ARTIFACTS a phase produced, not of a ledger entry
# recording that it ran. A phase is certified when its deliverable exists, its stage meta
# records `verification_status: pass`, the meta is BOUND to the current artifact of the phase
# above it, the deliverable still hashes to what the certifying run recorded
# (`artifact_hashes`, stamped by `write_step_result`), and the same freshness conditions the
# dependency-readiness stages apply are met. That is what replaces the checkpoint ledger:
# `orchestration_checkpoint.json` recorded a run, could not see a later edit of anything it
# did not itself hash, and was consulted only on a resume, so a cold re-run of an already
# certified node re-derived every phase from scratch.
#
# The predicate is READ IDENTICALLY on a cold run and on a resume — there is no
# resume-only gate — so "this phase is already done" has one answer in the codebase.


def _certification_hash_mismatch(repo_root: Path, meta_doc: dict[str, Any]) -> str | None:
    """`None` when every `artifact_hashes` entry re-computes to the recorded digest.

    Otherwise the refusal reason: `artifact_hashes_missing` for a meta written before the
    stamp existed (or by a writer that skipped it — an unstamped meta is NOT certified, or
    the stamp could be evaded by omission), and `artifact_hash_mismatch:<ref>` naming the
    first deliverable whose bytes moved. `sha256:missing` is NOT excused here (unlike the
    ledger's integrity check, which skipped it): a deliverable that has since been deleted
    must re-run the phase.
    """
    hashes = meta_doc.get("artifact_hashes")
    if not isinstance(hashes, dict) or not hashes:
        return "artifact_hashes_missing"
    for ref in sorted(hashes):
        expected = hashes.get(ref)
        if not isinstance(ref, str) or not ref.strip() or not isinstance(expected, str):
            return f"artifact_hash_mismatch:{ref}"
        if _compute_sha256(repo_root / _normalize_rel_posix(ref.strip())) != expected:
            return f"artifact_hash_mismatch:{ref}"
    return None


def _stage_meta_certification(repo_root: Path, meta_path: Path) -> tuple[bool, dict[str, Any]]:
    """The per-meta half of the predicate, shared by the certifying phases:
    readable object, `verification_status == "pass"`, and hashes that re-compute.

    The second element always carries `revoked`, `last_fail_reason` and
    `revocation_severity` — even on refusal — because the conductor seeds a repair from
    exactly those fields when a resume finds a revoked artifact. A refusal that dropped the
    findings would leave the repair with nothing to repair from; one that dropped the severity
    would silently downgrade a `critical` to the `major` default, which under the G5 policy is
    the difference between discarding the producer's context and reusing it.
    """
    detail: dict[str, Any] = {"revoked": False, "last_fail_reason": None,
                              "revocation_severity": None,
                              "revocation_repair_strategy": None}
    try:
        doc = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return (False, {**detail, "reason": "stage_meta_unreadable"})
    if not isinstance(doc, dict):
        return (False, {**detail, "reason": "stage_meta_unreadable"})
    fail_reason = doc.get("last_fail_reason")
    detail["last_fail_reason"] = fail_reason if isinstance(fail_reason, str) and fail_reason.strip() else None
    sev = doc.get("revocation_severity")
    detail["revocation_severity"] = sev if sev in REVOCATION_SEVERITIES else None
    strat = doc.get("revocation_repair_strategy")
    detail["revocation_repair_strategy"] = (
        strat if strat in REVOCATION_REPAIR_STRATEGIES else None)
    status = str(doc.get("verification_status", "")).strip().lower()
    if status == "revoked":
        detail["revoked"] = True
        return (False, {**detail, "reason": "revoked"})
    if status != "pass":
        return (False, {**detail, "reason": "verification_status_not_pass"})
    mismatch = _certification_hash_mismatch(repo_root, doc)
    if mismatch is not None:
        return (False, {**detail, "reason": mismatch})
    return (True, {**detail, "reason": None, "meta": doc})


def _certified_ir_violations(repo_root: Path, ir_ref: str) -> list[str]:
    """The current `Compile.static` verdict on an already-certified IR: the violations the
    deterministic compile-stage validator reports against `ir_ref` TODAY, `[]` when it passes.

    This is the SAME function `Conductor._compile_static_inproc` runs through the validator's
    CLI at the `compile.static` gate, so certification and the gate cannot disagree on the
    rule: an IR that the gate would reject is not one a skip may stand on. It is a READ of the
    IR directory only — nothing is written, nothing is revoked. Isolated here so a test can
    patch one name; the production body is one call. The import runs runtime → validator,
    the permitted direction (`validate_pipeline_semantics` must not import this module).
    """
    from tools.validate_pipeline_semantics import validate_compile_stage
    return validate_compile_stage(repo_root, "workspace", ir_ref)


def _certified_ir_candidate(
    repo_root: Path, node_key: str, *, spec_ref: str | None = None,
) -> str | None:
    """The ir_id of the SELECTED certified IR of `node_key` when it satisfies the compile
    clause of `_phase_certified` (the key recomputed now, and the current `--stage compile`
    validator), else `None`.

    Used by the conductor's `prepare_node` on a COLD run to ADOPT the standing IR instead of
    minting a new ir_id. Takes no orchestration: the artifacts, not this run's records,
    decide. An IR the current validator rejects is not adopted (issue #238): the cold run
    mints a fresh ir_id and Compile re-derives, with no revocation record written.
    `spec_ref` pins the subject's spec directory when the caller holds it."""
    resolver = DerivationResolver(
        repo_root, spec_refs={node_key: spec_ref} if spec_ref else None)
    ok, detail = _ir_certification(repo_root, node_key, resolver=resolver)
    return detail.get("ir_id") if ok else None


def _ir_certification(
    repo_root: Path, node_key: str, *, resolver: DerivationResolver,
) -> tuple[bool, dict[str, Any]]:
    """The compile clause: the selected Compile output under the key recomputed now
    (`DerivationResolver.select`), AND that IR passing the CURRENT `--stage compile`
    validator (issue #238).

    The validator clause is the one thing the key cannot carry: a `spec.ir.yaml` certified
    before a validator rule change keeps its `pass` status, its hashes and its key — the
    rule is not an input — so key + hashes alone would skip Compile onto an IR that
    `generate.gate` then rejects on every attempt, a verdict the Generate leaf cannot
    change, charged to its budget. It runs LAST, only on an otherwise-certified IR, once per
    evaluation (measured 1.4–2.9 s at issue #238, not memoised). A refusal here
    (`ir_rejected_by_current_validator:…`) is the readiness-side twin of the gate's
    `compile_static_violation`; a validator exception is a refusal too
    (`ir_validator_raised:<type>`), never a raise — this is an evaluator whose callers hold
    no handler. A refused IR re-derives under the SAME key, and the selection policy then
    prefers the newer attempt.
    """
    sel = resolver.select(node_key, "compile")
    detail = sel.detail()
    if not sel.ok:
        return (False, detail)
    try:
        stale = _certified_ir_violations(repo_root, str(sel.ir_ref))
    except Exception as exc:  # evaluator contract: a verdict, never a raise (issue #153 shape)
        return (False, {**detail, "reason": f"ir_validator_raised:{type(exc).__name__}"})
    if stale:
        # The validator spells its subject as an ABSOLUTE path; quote the finding relative to
        # the checkout so the 200-character head carries the rule and not the prefix.
        head = str(stale[0]).replace(f"{Path(repo_root).resolve()}/", "", 1)[:200]
        return (False, {**detail,
                        "reason": f"ir_rejected_by_current_validator:{len(stale)}:{head}"})
    return (True, detail)


def _target_of_orchestration_meta(repo_root: Path, meta: Any) -> TargetProfile | None:
    """The target profile an orchestration was launched for — `invocation.target.target_id`
    (recorded by `run_workflow.py` since issue #284), loaded — or None when the record names
    none or its profile no longer loads. None is never a default: every pipeline-phase reader
    it reaches refuses it (`target_unresolved`), so an orchestration whose target cannot be
    told is not certified for anything past Compile."""
    invocation = meta.get("invocation") if isinstance(meta, dict) else None
    record = invocation.get("target") if isinstance(invocation, dict) else None
    target_id = record.get("target_id") if isinstance(record, dict) else None
    if not is_target_id(target_id):
        return None
    try:
        return load_target_profile(repo_root, str(target_id))
    except TargetProfileError:
        return None


def _orchestration_target(repo_root: Path, orchestration_id: str) -> TargetProfile | None:
    """`_target_of_orchestration_meta` over the orchestration's own `orchestration_meta.json`."""
    meta = _read_json_or_none(
        _orchestration_root(repo_root, orchestration_id) / "orchestration_meta.json")
    return _target_of_orchestration_meta(repo_root, meta)


def _phase_certified(
    repo_root: Path,
    orchestration_id: str,
    node_key: str,
    step: str,
    *,
    spec_ref: str | None = None,
    target: TargetProfile | None = None,
) -> tuple[bool, dict[str, Any]]:
    """`_phase_certification` without its resolver."""
    certified, detail, _resolver = _phase_certification(
        repo_root, orchestration_id, node_key, step, spec_ref=spec_ref, target=target)
    return (certified, detail)


def _phase_certification(
    repo_root: Path,
    orchestration_id: str,
    node_key: str,
    step: str,
    *,
    spec_ref: str | None = None,
    target: TargetProfile | None = None,
) -> tuple[bool, dict[str, Any], DerivationResolver]:
    """Is `(node_key, step)` certified? Returns `(certified, detail, resolver)` — the resolver
    that answered, so a caller asking the cross-target agreement of the same selection
    (`cross_target_agreement`) shares its memo.

    "Certified" keeps meaning derived on its own key: a Validate whose variant disagrees with
    another target's is still certified here (issue #383); the agreement is asked beside it.

    `detail` always carries `reason` (the FIRST clause that refused, `None` on success), the
    refs of the SELECTED outputs that did resolve (`ir_ref` / `pipeline_ref` / `source_id`
    / `binary_id` / `run_id`, each `None` when the chain stopped before it), the key the
    phase was asked under, and `revoked` / `last_fail_reason` / `revocation_severity` /
    `revocation_repair_strategy` read from the deepest meta the chain reached — the
    conductor seeds a repair from those.

    The chain is cumulative by construction of the key: `build` is certified only if
    `generate` is (the build key binds the selected generate's `output_hash`), which is
    certified only if `compile` is. Re-deriving one phase to a different output changes
    every downstream key with no downstream bookkeeping; re-deriving it to a byte-identical
    output changes none.

    Reads only the workspace artifacts. It never reads `agent_runs.jsonl`, a step_result,
    the phase_state or — since issue #250 PR-2 — this orchestration's reservation: what a
    run RECORDED about itself is not evidence that the artifact on disk is the one it
    certified, and where THIS run writes is not what makes an artifact certified. The
    `orchestration_id` is kept for the callers that record the answer per orchestration
    (`check_phase_certified`). For the IR, "certified" includes passing the current
    `--stage compile` validator (`_ir_certification`, issue #238). `spec_ref` pins the
    subject's spec directory when the caller holds it (the conductor does; `--with-deps`'s
    readiness reads the catalog).

    Every phase past Compile is certified for ONE target (issue #284): `target`, else the
    target the orchestration was launched for (`_orchestration_target`). With neither, those
    phases answer `target_unresolved`.
    """
    step_token = step.strip().lower()
    if target is None and step_token != "compile":
        target = _orchestration_target(repo_root, orchestration_id)
    resolver = DerivationResolver(
        repo_root, spec_refs={node_key.strip(): spec_ref} if spec_ref else None,
        target=target)
    ok, detail = _ir_certification(repo_root, node_key.strip(), resolver=resolver)
    if not ok:
        return (False, detail, resolver)
    if step_token == "compile":
        return (True, detail, resolver)
    sel = resolver.select(node_key.strip(), step_token)
    return (sel.ok, sel.detail(), resolver)


def _certifiable_artifact_refs(required_outputs: Sequence[str], meta_ref: str) -> list[str]:
    """The deliverables of a phase whose bytes the certification pins: its
    `required_outputs`, minus the stage meta the hashes are written INTO (it cannot hash
    itself) and minus the audit logs (`AUDIT_LOG_BASENAMES`), which later phases append to.
    """
    meta_norm = _normalize_rel_posix(meta_ref)
    refs: list[str] = []
    for ref in required_outputs:
        if not isinstance(ref, str) or not ref.strip():
            continue
        norm = _normalize_rel_posix(ref.strip())
        if norm == meta_norm or norm.rsplit("/", 1)[-1] in AUDIT_LOG_BASENAMES:
            continue
        if norm not in refs:
            refs.append(norm)
    return refs


def _stamp_certification(
    repo_root: Path,
    *,
    step: str,
    required_outputs: Sequence[str],
    derivation: Any,
) -> dict[str, Any] | None:
    """Write `artifact_hashes`, `output_hash` and the derivation record (`derivation_key`,
    `derivation_inputs`, `derivation_transformation`) into the phase's stage meta. Returns the
    stamped document, or `None` for a
    step outside `CERTIFYING_META_FILENAME_BY_STEP` (every phase is in it since issue #250).

    `derivation` is the record the conductor computed at PHASE START (`phase_derivation`,
    before any substep ran) and carried in the step_result payload: the inputs the phase was
    actually run under, not the inputs at stamp time. It is REQUIRED on a pass — a pass
    without one raises like a pass whose deliverable is missing — because a certified meta
    with no key is, from PR-2 of issue #250 on, a meta no lookup can ever select, and failing
    open here would silently re-derive that phase on every later run.

    Called from `write_step_result` on a `pass`, after `_validate_step_result_payload` has
    proved every `required_outputs` entry exists and before the result file is written. That
    position is the whole design:

      - it is the ONE host-side point both the pure path (host writes the meta) and the
        agentic path (the verify leaf writes it) pass through, so there is one writer of the
        hashes rather than one per meta author;
      - the anti-mock-green check has just established the deliverables are on disk, so the
        hashes are taken of artifacts that were proved to exist;
      - it is itself an enforcement gate a later `set-status pass` trusts, so a phase that
        could not be stamped must not get a passing step_result.

    Failure raises, never a warning. The ledger this replaces updated on a
    best-effort basis (`update_checkpoint` was WARN-only), which was affordable because a
    missing entry only cost a re-run; an unstamped meta is instead indistinguishable from a
    meta whose author skipped the stamp, and `_phase_certified` refuses both — so failing
    open here would silently disable the certification of that phase forever.
    """
    step_token = step.strip().lower()
    meta_filename = CERTIFYING_META_FILENAME_BY_STEP.get(step_token)
    if meta_filename is None:
        return None
    # `required_outputs` and the stage meta are the conductor's (`phase_required_outputs`
    # declares exactly one certifying meta per phase and at least one deliverable beside it,
    # and the meta is host-written), and `derivation` is `phase_derivation`'s record, carried
    # in process: neither is re-checked here (issue #643). The record is INDEXED, so a pass
    # without one raises rather than stamping a key no lookup selects.
    refs = [r.strip() for r in required_outputs if isinstance(r, str) and r.strip()]
    meta_ref = _normalize_rel_posix(next(
        r for r in refs if _normalize_rel_posix(r).rsplit("/", 1)[-1] == meta_filename))
    meta_path = repo_root / meta_ref
    doc = json.loads(meta_path.read_text(encoding="utf-8"))

    artifact_refs = _certifiable_artifact_refs(refs, meta_ref)
    hashes: dict[str, str] = {}
    for ref in artifact_refs:
        digest = _compute_sha256(repo_root / ref)
        if digest == "sha256:missing":
            raise RuntimeError(
                f"certification stamp: declared deliverable is absent on disk: {ref}"
            )
        hashes[ref] = digest
    doc["artifact_hashes"] = hashes
    # The three identities of A1 (issue #250): the output hash over the deliverables just
    # hashed — relative to the stage directory, so a byte-identical re-derivation under a
    # fresh id is the same output — and the derivation this output was produced under. The
    # attempt id is the step_result's own `executor_agent_run_id` / the launch rows, not
    # repeated here.
    doc["output_hash"] = _output_hash(hashes, stage_dir=meta_ref.rsplit("/", 1)[0])
    doc["derivation_key"] = derivation["derivation_key"]
    doc["derivation_inputs"] = derivation["derivation_inputs"]
    doc["derivation_transformation"] = list(derivation["transformation"])

    _write_json(meta_path, doc)
    return doc


def _reserved_pipeline_dir(repo_root: Path, res_dir: Path) -> Path | None:
    """The pipeline directory this orchestration reserved for the node whose reservations
    live in `res_dir`: `workspace/pipelines/<safe>/<target_id>/<pipeline_id>`, composed from
    `generate.json`'s `reserved_ir_id` and `target_id`. None when either is absent — a
    reservation written before issue #284 names no target, so it names no directory."""
    pipeline_id = _reserved_id(res_dir, "generate")
    if pipeline_id is None:
        return None
    doc = _read_json_or_none(res_dir / "generate.json")
    target_id = doc.get("target_id") if isinstance(doc, dict) else None
    if not is_target_id(target_id):
        return None
    return repo_root / pipelines_dir(res_dir.name, str(target_id)) / pipeline_id


def _reserved_id(res_dir: Path, step: str) -> str | None:
    """The id this orchestration reserved for `step` (`compile.json` names the ir_id,
    `generate.json` the pipeline_id), or `None` when no reservation was made.

    `_read_json` RAISES on a missing file; a node whose reservation does not exist is an
    ordinary state (nothing has run yet, or the record belongs to another node_key), and
    both readers here answer it as "not certified" / "nothing to revoke"."""
    path = res_dir / f"{step}.json"
    if not path.is_file():
        return None
    doc = _read_json(path)
    if not isinstance(doc, dict):
        return None
    value = doc.get("reserved_ir_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strip_certification(
    repo_root: Path,
    *,
    step: str,
    required_outputs: Sequence[str],
) -> str | None:
    """Remove the certification keys from a NON-passing phase's stage meta. Returns the meta
    ref when something was removed, else `None`.

    The stamp is host-written and always was; on the deleted agentic path the stage meta also
    sat inside the verify leaf's write_root, so a leaf could put `artifact_hashes` there
    itself. That half is gone with the leaf's write authority (Z4, issue
    #171 — a pure leaf authors no file), and the function is not: the HOST stamps the keys
    before it knows the phase's outcome, so a phase that ends NON-pass would otherwise leave a
    meta claiming a certification no host ever issued, and the skip decision reads exactly
    that claim. Erasing the keys when the phase terminates non-pass makes "certified" mean "a
    passing `write-step-result` stamped it" for every phase that reaches this function.

    Best effort by design: an absent or unreadable meta is the ordinary shape of a failed
    phase (nothing was authored), and there is nothing to strip.

    This is the ONE strip: `write_step_result` is its only caller (the strip at the child's
    own terminalization went with the FS-diff window in Z4, issue #171 PR-2; a pure leaf
    authors no meta, so there is nothing of a child's to erase). A phase that fail-closes
    without writing a step_result at all leaves its meta as the host last wrote it — never
    stamped, since the stamp is written only here on a pass — so the keys exist only where a
    passing `write-step-result` put them.
    """
    meta_filename = CERTIFYING_META_FILENAME_BY_STEP.get(step.strip().lower())
    if meta_filename is None:
        return None
    meta_refs = [
        _normalize_rel_posix(r.strip()) for r in required_outputs
        if isinstance(r, str) and r.strip()
        and _normalize_rel_posix(r.strip()).rsplit("/", 1)[-1] == meta_filename
    ]
    if len(meta_refs) != 1:
        return None
    return meta_refs[0] if _strip_certification_keys(repo_root / meta_refs[0]) else None


#: Every key `_stamp_certification` writes; `_strip_certification_keys` removes exactly these.
_CERTIFICATION_STAMP_KEYS: tuple[str, ...] = (
    "artifact_hashes", "output_hash", "derivation_key", "derivation_inputs",
    "derivation_transformation")


def _strip_certification_keys(meta_path: Path) -> bool:
    """Remove every certification stamp key (`_CERTIFICATION_STAMP_KEYS`) from one stage
    meta, preserving every other key. `True` when something was removed. Never raises: an absent or unreadable meta is the
    ordinary shape of a phase that authored nothing, and there is nothing to strip."""
    try:
        doc = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(doc, dict):
        return False
    removed = [k for k in _CERTIFICATION_STAMP_KEYS if k in doc]
    if not removed:
        return False
    for key in removed:
        doc.pop(key)
    try:
        _write_json(meta_path, doc)
    except OSError:
        return False
    return True


# --- derivation keys (Z5, issue #250) -------------------------------------------------
#
# The per-phase CONTRACT INPUTS of a node, resolved from disk, and the derivation key over
# them (`tools/derivation.py` hashes; this section says what each phase hashes). The key is
# RECORDED — in the certifying stage meta on a pass (`_stamp_certification`), on every launch
# row (`agent_runs.jsonl`) and on every terminal step_result (PR-1 of issue #250) — and it
# DECIDES: `_phase_certified` and the dependency-readiness stages select a certified output by
# comparing its stamped key with the one recomputed here (PR-2, `DerivationResolver`). Every
# resolver here therefore answers the question the predicate asks ("what are the inputs NOW"),
# and the conductor asks the same function at phase start, so the stamp and the later
# recomputation are one rule.
#
# What each phase hashes (`inputs`; hashes and identifiers only, no bodies):
#
#   compile   spec.{controlled_spec,tests,deps} — the three spec files' bytes;
#             profiles[] — each adopted profile's `controlled_spec.md`, as the compile
#               producer is shown it (`_leaf_profile_spec_document`);
#             dependency_graph — the derived closure's canonical signature
#               (`_closure_signature` of `build_dependency_graph(include_via=False)`: node
#               set, heights, direct/transitive split and the adopted profile set);
#             dependency_surface — the published surface (`_published_surface`) of each
#               direct dep a consumer calls, a `component` or a `problem`
#               (`_component_direct_deps`). A closure member's IR or source body is not a member: no
#               compile leaf reads one, and a dep's non-interface edit must not re-derive its
#               consumers (issue #574).
#             Nothing here names a target: the graph is `deps.yaml`'s alone (no harness since
#             R4-a PR-3), and the admissible-toolchain document the producer was shown until
#             then is gone with the IR's `impl_defaults` (issue #284).
#   generate  ir — this node's certified compile output hash (`spec.ir.yaml`: the case set,
#               the algorithm, the contracts);
#             spec.{controlled_spec,tests} — the reviewer reads the spec, the producer the
#               tests;
#             target — `{target_id, profile}`: the target the source is generated for and the
#               hash of its profile, which the producer is shown (issue #284);
#             harness — the ONE harness the producer negotiates against — the target's
#               (`harness_node_key_for`) — and the manifest document it is shown
#               (`harness_capability_manifest_document_for` over the set the target
#               implies, `target_profile.harness_capabilities`);
#             closure[] — every PIPELINE closure node (`pipeline_closure_nodes`: the sidecar's
#               closure plus the target's harness) in pipeline order, each `{node_key, surface}`
#               (`_published_surface`). The members and their order are what the control file
#               lists; a language declaring `interface_header` seeds each member's surface as a
#               header; and `assert_harness_pin` runs only inside a Generate, so a harness whose
#               surface moved must re-run it. A member's IR or source output hash is not a
#               member: no generate leaf reads an IR or source body, and Build binds the sources.
#               A member with no certified compile or generate output is still unresolvable.
#             NOT hashed, because they are functions of the members above and of the
#             transformation version: the host-rendered runner and control file
#             (`RENDER_VERSION`), and the bundle shape (the node kind and the target's
#             toolchain).
#   build     source — this node's certified generate output hash (model, checks or runner,
#               the control file, all host- or leaf-authored deliverables of Generate);
#             closure[] — the certified generate output hash of every pipeline closure node
#               Build stages (`_stage_dependency_sources` copies exactly these sources);
#             toolchain — `_target_toolchain_identity`: `{target_id, language, standard,
#               build_system, backend, compiler, compiler_version}` read off the target
#               profile, with the compiler the control-file writer would pin and the first
#               versioned line of its `--version`; plus `architecture` when the language's
#               control-file rules read it, and `compiler_wrapper` / `parallel_runtime` under
#               a parallel backend with a compiler wrapper. `compiler_version` and
#               `parallel_runtime` are probed on THIS host (`HOST_PROBED_TOOLCHAIN_LEAVES`);
#               a comparand resolver instead matches another target's Build under the values
#               its own stamp carries (issue #345, `_comparand_candidate_key`).
#   validate  binary — this node's certified build output hash;
#             ir — the compile output hash (the case set and the predicates);
#             spec.tests — the `--stage pre_judge` gate `post_judge` runs reads `tests.md`
#               (`_validate_tests_verdict_summary_consistency`);
#             run_policy — the execution policy `_execute_inproc` imposes: `{target_id,
#               profile, threads_per_rank, preset}`.
#             Nothing of another target is an input (issue #383, reversing #324's
#             `comparand[]` member): the cross-target comparison is `cross_target_agreement`,
#             evaluated on demand beside this key, never stored in it.
#
# Generate, Build and Validate are `node_key × target` facts (issue #284): their outputs live
# under `workspace/pipelines/<safe>/<target_id>/`, every closure member is selected for the
# same target (`DerivationResolver.target`), and the target's id is in each of the three
# keys. Compile is target-free in its store AND its key (R4-a PR-3): one Compile serves
# every target.
#
# The transformation tuple's prompt contract is the READER's (`derivation.transformation_versions`):
# a producer's templates are advisory, because validity is decided by the gates and the readers.
#
# An UPSTREAM output hash is always the `output_hash` of the SELECTED certified output —
# never a derivation key — so that when the selection among several certified outputs of one
# key changes, every key that binds it changes with it (A1's completion condition 4). The
# selection is `DerivationResolver.select` for a dependency (its own chain under its own keys)
# and, for this node's own upstream phases, the ref the caller passes: the conductor passes
# the attempt's reserved / adopted ids, which `_adopt_certified_refs` keeps equal to the
# selection; the resolver passes the selection itself. A dependency with no eligible output
# is an unresolvable input (`_dependency_output_hash`): the consumer is not certified, and
# readiness refuses the dependency for the same reason.


class DerivationInputsUnresolvable(RuntimeError):
    """A contract input of the phase cannot be resolved from disk — an upstream phase that is
    not certified, a dependency with no certified output, a spec file that cannot be read.
    The message names the input. The conductor turns it into a `fail_closed` transport
    outcome (`derivation_inputs_unresolvable`), the same disposition as a pure context that
    cannot be assembled: nothing a leaf could repair."""


def harness_node_key_for(repo_root: Path, node_key: str, target: TargetProfile) -> str:
    """The ONE harness a node built for `target` runs over and a pure generate producer
    negotiates against.

    An `infrastructure` node negotiates against ITSELF (its self-test bundle declares the
    execution model it implements); any other node against the harness its TARGET names
    (`target_profile.harness_node_key_for_target`, issue #284 — the IR's `infrastructure` direct
    dependency until R4-a PR-3). `infrastructure` is read off the NODE_KEY — the host's identity
    for the node — never off the IR's self-declared `meta.spec_kind`. Shared by the conductor's
    context assembly, runner render and acceptance layer (`_leaf_harness_node_key` delegates
    here), the generate derivation inputs and the pipeline closure (`pipeline_closure_nodes`).
    Raises `TargetProfileError` when the target's harness does
    not resolve in the catalog (the launch gate refused that before the run started)."""
    if node_key.split("/", 1)[0].strip() == "infrastructure":
        return node_key
    from tools.target_profile import harness_node_key_for_target
    return harness_node_key_for_target(repo_root, target)


def with_target_harness(
    dep_block: Any, repo_root: Path, node_key: str, target: TargetProfile | None,
) -> dict[str, Any]:
    """A copy of a dependency block — the IR's `dependency`, the `dependency_graph.json`
    sidecar, or the validator's merge of the two — as a PIPELINE phase of `node_key` sees it
    for `target`: the target's harness added as a direct dependency (to `direct_deps` and, at
    `topo_level` 0, to `all_nodes`, each only where that list is present) unless the node is
    itself `infrastructure` or the block already names it (issue #284).

    The target-free Compile writes blocks that no longer carry the harness (`deps.yaml` does
    not declare it since R4-a PR-3); this is where every post-Compile reader of a node's
    dependency SET gets it back, so the dependency facts, `lineage.json`, the pre_judge DAG
    check, the derived `aggregate_verdict` and the validator's DAG-completeness gate ask about
    the harness exactly as they did when `deps.yaml` declared it. Without a target, or on a
    block that is not a mapping, the block is returned unchanged (as a mapping)."""
    block = dict(dep_block) if isinstance(dep_block, dict) else {}
    if target is None:
        return block
    harness_nk = harness_node_key_for(repo_root, node_key, target)
    if harness_nk == node_key:
        return block

    def _names(items: Any) -> set[str]:
        out: set[str] = set()
        for item in items if isinstance(items, list) else []:
            nk = item.get("node_key") if isinstance(item, dict) else item
            if isinstance(nk, str):
                out.add(nk.strip())
        return out

    if isinstance(block.get("direct_deps"), list) and harness_nk not in _names(block["direct_deps"]):
        block["direct_deps"] = [*block["direct_deps"],
                                {"node_key": harness_nk, "kind": "infrastructure",
                                 "operations": []}]
    if isinstance(block.get("all_nodes"), list) and harness_nk not in _names(block["all_nodes"]):
        block["all_nodes"] = [{"node_key": harness_nk, "topo_level": 0}, *block["all_nodes"]]
    return block


def pipeline_closure_nodes(
    repo_root: Path, ir_ref: str, node_key: str, target: TargetProfile,
) -> list[str]:
    """The dependency closure a PIPELINE phase of `node_key` works over for `target`, in compile
    order (deepest first): the target's harness, then the closure the certified IR's
    `dependency_graph.json` sidecar records (`_closure_nodes_from_graph`).

    The sidecar is the target-free Compile's graph — `deps.yaml`'s alone since R4-a PR-3 (issue
    #284) — so the harness, which every non-`infrastructure` node's runner is linked against, is
    added here, first: it depends on nothing, and `topo_level` 0 is where the sidecar would have
    put it. An `infrastructure` node's closure is its sidecar's. A harness the sidecar already
    lists (an IR compiled before PR-3, which no derivation key selects) is not listed twice.

    ONE definition for every reader of the pipeline closure: the generate and build keys'
    `closure[]` and the conductor's staging and control-file order (`_dependency_closure_nodes`)."""
    graph = _read_json_or_none(repo_root / ir_ref / "dependency_graph.json")
    closure = _closure_nodes_from_graph(graph, node_key)
    harness_nk = harness_node_key_for(repo_root, node_key, target)
    if harness_nk == node_key or harness_nk in closure:
        return closure
    return [harness_nk, *closure]


def toolchain_version_argv(target: TargetProfile) -> tuple[str, ...]:
    """The argv whose answer is the version of the compiler `target`'s build runs: the profile's
    pin, else the language backend's `DEFAULT_COMPILER`, asked for `--version` — through the
    parallel backend's compiler wrapper when it declares one, which runs the compiler it is
    configured with. `_target_toolchain_identity` probes it on this host for the build key, and
    a build at a remote site (issue #333) hands the same argv to the job as its
    `toolchain_probe`, so the two versions a build records are asked alike."""
    tc = target.toolchain
    compiler = str(tc.get("compiler") or "") or str(backend_registry.capability_module(
        "language", tc["language"], "bundle_facts").DEFAULT_COMPILER)
    argv: tuple[str, ...] = (compiler, "--version")
    if backend_registry.provides("parallel", target.parallel_backend, "compiler_wrapper"):
        wrapper = backend_registry.capability_module(
            "parallel", target.parallel_backend, "compiler_wrapper")
        argv = tuple(str(a) for a in wrapper.wrap(argv))
    return argv


def _probe_compiler_version(target: TargetProfile) -> Any:
    from tools import build_runtime

    return build_runtime._syntax_compiler_version(toolchain_version_argv(target))


def _probe_parallel_runtime(target: TargetProfile) -> Any:
    from tools.host_execution import probe_first_line

    wrapper = backend_registry.capability_module(
        "parallel", target.parallel_backend, "compiler_wrapper")
    return probe_first_line(tuple(str(a) for a in wrapper.SHOW_ARGV))


#: The leaves of `_target_toolchain_identity` read off THIS process's host (a subprocess run
#: through PATH) rather than off the profile or the backend registry, each with its probe (issue
#: #345). `_target_toolchain_identity` probes through this table, and
#: `DerivationInputsTests.test_every_leaf_that_moves_with_the_path_is_a_declared_host_probed_leaf`
#: requires every member `_host_probed_leaves` names to be in the probed identity, so an entry added
#: here without a place in the identity is red rather than a leaf every stamp lacks.
_HOST_PROBES: dict[str, Callable[[TargetProfile], Any]] = {
    "compiler_version": _probe_compiler_version,
    "parallel_runtime": _probe_parallel_runtime,
}
HOST_PROBED_TOOLCHAIN_LEAVES: tuple[str, ...] = tuple(_HOST_PROBES)


def _host_probed_leaves(target: TargetProfile) -> tuple[str, ...]:
    """The members of `HOST_PROBED_TOOLCHAIN_LEAVES` that `target`'s identity carries:
    `compiler_version` always, `parallel_runtime` under a parallel backend that declares
    `compiler_wrapper`."""
    if backend_registry.provides("parallel", target.parallel_backend, "compiler_wrapper"):
        return HOST_PROBED_TOOLCHAIN_LEAVES
    return ("compiler_version",)


def _comparand_candidate_key(doc: Mapping[str, Any], inputs: Mapping[str, Any],
                             target: TargetProfile) -> str | None:
    """The build key a stamped Build `doc` of ANOTHER target is matched against by a comparand
    resolver (issue #345): `inputs` — computed with `probe=False`, so its toolchain lacks the
    host-probed leaves — with those leaves read off the stamp's own `derivation_inputs.toolchain`.
    `None` when the stamp does not carry EVERY leaf of `_host_probed_leaves(target)` (a stamp
    from before a leaf joined the identity, a hand-edited meta): substituting only what is
    present would let a stamp without `compiler_version` match a key computed without it."""
    substituted = _comparand_substituted_inputs(doc, inputs, target)
    return None if substituted is None else _derivation_key("build", substituted)


def _comparand_substituted_inputs(doc: Mapping[str, Any], inputs: Mapping[str, Any],
                                  target: TargetProfile) -> dict[str, Any] | None:
    recorded = doc.get("derivation_inputs")
    stamped = recorded.get("toolchain") if isinstance(recorded, Mapping) else None
    leaves = _host_probed_leaves(target)
    if not isinstance(stamped, Mapping) or any(leaf not in stamped for leaf in leaves):
        return None
    out = {**inputs, "toolchain": {**inputs["toolchain"],
                                   **{leaf: stamped[leaf] for leaf in leaves}}}
    if "libraries" in inputs:
        stamped_libraries = _stamped_library_files(recorded, inputs["libraries"])
        if stamped_libraries is None:
            return None
        out["libraries"] = stamped_libraries
    if "oracle" in inputs:
        stamped_oracle = _stamped_oracle(recorded, inputs["oracle"])
        if stamped_oracle is None:
            return None
        out["oracle"] = stamped_oracle
    return out


def _stamped_library_files(recorded: Any, computed: Sequence[Mapping[str, Any]]
                           ) -> list[Any] | None:
    """The libraries a stamp's `derivation_inputs.libraries` records — each with the site's
    directories and files a key computed without probing lacks (issue #644) — when the stamp
    names the same libraries with the same declarations, in the same order; None otherwise,
    and when it records none."""
    stamped = recorded.get("libraries") if isinstance(recorded, Mapping) else None
    if not isinstance(stamped, list) or len(stamped) != len(computed):
        return None
    for entry, mine in zip(stamped, computed):
        if not (isinstance(entry, Mapping)
                and entry.get("library_id") == mine.get("library_id")
                and entry.get("declaration_sha256") == mine.get("declaration_sha256")):
            return None
    return [dict(entry) for entry in stamped]


def _stamped_oracle(recorded: Any, computed: Mapping[str, Any]) -> dict[str, Any] | None:
    """The `oracle` member a stamp's `derivation_inputs` records — each build's libraries with
    the site's files a key computed without probing lacks (issue #680) — when it holds every
    member but `builds` equal (the declaration, the adapter and the `use`), and each build the
    same libraries with the same declarations (`_stamped_library_files`); None otherwise, and
    when it records none. The declaration's hash fixes the build ids and their libraries' ids
    and order, so only each library's declaration is compared beside it."""
    stamped = recorded.get("oracle") if isinstance(recorded, Mapping) else None
    if not (isinstance(stamped, Mapping)
            and {k: v for k, v in stamped.items() if k != "builds"}
            == {k: v for k, v in computed.items() if k != "builds"}
            and isinstance(stamped.get("builds"), list)
            and len(stamped["builds"]) == len(computed["builds"])):
        return None
    for entry, own in zip(stamped["builds"], computed["builds"]):
        if _stamped_library_files(entry, own["libraries"]) is None:
            return None
    return dict(stamped)


def _oracle_member(repo_root: Path, node_key: str, resolver: DerivationResolver, *,
                   probe: bool) -> dict[str, Any] | None:
    """The build key's `oracle` member (issue #680): None when the node declares no oracle for
    the target's language (the member is then absent, so no such key moves); else the
    declaration's and the adapter's hashes and, per build, the content hash of every library
    file at the site (`oracle.member`). A comparand resolver, and `probe=False`, take the
    declared form (`oracle.declared_member`); `_comparand_substituted_inputs` fills the files
    off each candidate's stamp. Only the node's own oracle: a closure member's is in that
    member's key. A malformed declaration and a library the site cannot serve are
    unresolvable: the driver refuses each before any phase."""
    from tools import external_library as xl
    from tools import oracle as oracle_mod
    from tools.execution_sites import SitesConfigError
    from tools.target_profile import TargetProfileError
    spec = resolver.spec_ref(node_key)
    if spec is None:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: {node_key} resolves to no spec directory, so "
            f"its oracle cannot be read")
    try:
        declared = oracle_mod.load(repo_root, spec, str(resolver.target.toolchain["language"]))
    except oracle_mod.OracleDeclarationError as exc:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: oracle of {node_key}: {exc}") from exc
    if declared is None:
        return None
    if not probe or resolver.comparand:
        return oracle_mod.declared_member(declared)
    try:
        return oracle_mod.member(repo_root, declared, resolver.target)
    except (xl.LibraryUnusable, SitesConfigError, TargetProfileError, OSError) as exc:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: oracle of {node_key}: {exc}") from exc


def _own_library_declarations(repo_root: Path, node_key: str,
                               spec: str) -> list[dict[str, Any]] | None:
    """The compile and generate keys' `libraries` member (issue #644): the ids and declaration
    hashes of the external libraries the node's own deps.yaml declares, whose modules and
    procedures its leaves are told they may use; None (the member absent, so no key moves)
    when it declares none."""
    from tools import external_library as xl
    try:
        declarations = [xl.load_declaration(repo_root, i)
                        for i in xl.node_library_ids(repo_root, spec)]
    except xl.LibraryDeclarationError as exc:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: libraries of {node_key}: {exc}") from exc
    return xl.declared_identity(declarations) or None


def _closure_libraries_member(repo_root: Path, node_key: str, closure_nodes: Sequence[str],
                              resolver: DerivationResolver, *, probe: bool
                              ) -> list[dict[str, Any]] | None:
    """The build key's `libraries` member (issue #644): None when the node and its pipeline
    closure declare no external library (the member is then absent, so no such key moves);
    else, per library in closure order, its id, its declaration's hash and the content hash of
    every declared file at the site the target runs at (`external_library.identity`). A
    comparand resolver probes nothing and records ids and declaration hashes alone
    (`external_library.declared_identity`); `_comparand_substituted_inputs` fills the files off
    each candidate's stamp; `probe=False` asks for that form too. The site is read here, from the operator's `sites.yaml`
    (`external_library.site_for_target`), the one resolution the conductor's Build uses too;
    an unreadable one, a remote site and a library the site cannot serve are unresolvable:
    the driver decides each before any phase (`library unavailable`)."""
    from tools import external_library as xl
    from tools.execution_sites import SitesConfigError
    from tools.target_profile import TargetProfileError
    spec = resolver.spec_ref(node_key)
    if spec is None:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: {node_key} resolves to no spec directory, so "
            f"its libraries cannot be read")
    try:
        declarations = xl.closure_declarations(repo_root, spec, closure_nodes)
    except xl.LibraryDeclarationError as exc:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: libraries of {node_key}: {exc}") from exc
    if not declarations:
        return None
    if not probe or resolver.comparand:
        return xl.declared_identity(declarations)
    try:
        return xl.identity(xl.resolve_closure(repo_root, resolver.target, declarations))
    except (xl.LibraryUnusable, SitesConfigError, TargetProfileError, OSError) as exc:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: libraries of {node_key}: {exc}") from exc


def _target_toolchain_identity(target: TargetProfile, *, probe: bool = True) -> dict[str, Any]:
    """The build toolchain identity of a target, for the build key and for `binary_meta.json`:
    the target id, the profile's `language` / `standard` / `build_system` / parallel `backend`,
    and the compiler with the first versioned line of its `--version` (issue #284; before R4-a PR-2 the
    same fields were read off the IR's `impl_defaults`).

    The compiler is the profile's pin, else the language backend's `DEFAULT_COMPILER`
    (`bundle_facts`) — the compiler the conductor's control-file writer pins in the same case,
    and the one the mandatory syntax stage runs. The VERSION is probed from the executable,
    because a profile names a compiler and not the build of it on this machine;
    `compiler_version` is `None` when it cannot be probed (recorded, not refused).

    The target's `hardware.architecture` joins it when the language's control-file rules read it
    (`READS_ARCHITECTURE`; the CUDA C++ rules compile with `-arch=`, issue #289 R4-b PR-4): the
    target id and the profile's other fields do not move when only the architecture does, and the
    source the build reads may be byte-identical across the change, so without it a build for
    the old architecture was reused. A language whose rules do not read it keeps the key it had.

    A parallel backend that declares `compiler_wrapper` (issue #316) adds two members — the
    wrapper, which is the program the build runs in the compiler's place, and the first line of
    its `SHOW_ARGV` output (`parallel_runtime`, `None` when it cannot be read), which names the
    runtime installation the binary links against — and asks `compiler_version` through the
    wrapper, which runs the compiler it is configured with — neither the compiler's version nor the
    target's fields change when only that installation does. A backend that declares none keeps
    the key it had.

    `compiler_version` and `parallel_runtime` are the HOST-PROBED leaves
    (`HOST_PROBED_TOOLCHAIN_LEAVES`); every other member is read off the profile or the backend
    registry. With `probe=False` they are ABSENT (not `None`, which is a probe's answer) and no
    subprocess runs: a comparand resolver reads them off each candidate's own stamp instead
    (issue #345, `_comparand_candidate_key`)."""
    tc = target.toolchain
    probed = ({leaf: _HOST_PROBES[leaf](target) for leaf in _host_probed_leaves(target)}
              if probe else {})
    compiler = str(tc.get("compiler") or "") or str(backend_registry.capability_module(
        "language", tc["language"], "bundle_facts").DEFAULT_COMPILER)
    identity: dict[str, Any] = {
        "target_id": target.target_id,
        "language": tc["language"],
        "standard": tc["standard"],
        "build_system": tc["build_system"],
        "backend": target.parallel_backend,
        "compiler": compiler,
    }
    if "compiler_version" in probed:
        identity["compiler_version"] = probed["compiler_version"]
    # Asked of the backend's PACKAGE only: a language whose control-file rules the neutral core
    # still carries (`core_provides`) has no module to ask, and reads no architecture; nor does
    # a language no backend declares (the launch gate refuses its profile; a reader of a
    # recorded one still gets the identity it always got).
    if backend_registry.unsupported_reason("language", tc["language"]) is None and \
            "control_file" in backend_registry.get("language", tc["language"]).backend_provides and \
            backend_registry.capability_module(
                "language", tc["language"], "control_file").READS_ARCHITECTURE:
        identity["architecture"] = (target.doc.get("hardware") or {}).get("architecture")
    if backend_registry.provides("parallel", target.parallel_backend, "compiler_wrapper"):
        wrapper = backend_registry.capability_module(
            "parallel", target.parallel_backend, "compiler_wrapper")
        identity["compiler_wrapper"] = str(wrapper.COMPILER_WRAPPER)
        # `compiler_version` above is the version of the compiler the WRAPPER runs, asked
        # through it (`toolchain_version_argv`): a wrapper configured with a compiler other than
        # the one `compiler` resolves to on PATH would otherwise key the build by the wrong
        # compiler, and an in-place upgrade of the wrapped one would move neither that value nor
        # `parallel_runtime`.
        if "parallel_runtime" in probed:
            identity["parallel_runtime"] = probed["parallel_runtime"]
    return identity


def _certified_output_hash(repo_root: Path, meta_path: Path, *, what: str) -> str:
    """The output hash of the certified phase whose certifying meta is `meta_path`, or a
    `DerivationInputsUnresolvable` naming `what` when that meta is not certified (absent,
    unreadable, not `pass`, revoked, or with a deliverable whose bytes moved)."""
    ok, detail = _stage_meta_certification(repo_root, meta_path)
    meta_ref = _normalize_rel_posix(str(meta_path.relative_to(repo_root)))
    if not ok:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: {what}: {meta_ref} is not certified "
            f"({detail.get('reason')})")
    return _meta_output_hash(detail["meta"], meta_ref)


def _meta_output_hash(meta_doc: Mapping[str, Any], meta_ref: str) -> str:
    """The output hash a certifying meta at `meta_ref` STANDS FOR: `output_hash` over its
    `artifact_hashes`, relative to the meta's own directory. Recomputed rather than read off
    the stamped `output_hash` key, so the value every reader binds to has one definition and
    a legacy meta (stamped before the key existed) answers the same way as a new one."""
    return _output_hash(meta_doc["artifact_hashes"], stage_dir=meta_ref.rsplit("/", 1)[0])


def _dependency_output_hash(
    resolver: DerivationResolver, dep_node_key: str, step: str
) -> str:
    """A dependency's `step` output as a consumer's key asks for it: the `output_hash` of the
    SELECTED certified output of that step under the dependency's own key recomputed now
    (`DerivationResolver.select`). A dependency with no eligible output — never derived,
    revoked, or stamped under another key (every meta written before issue #250 PR-1) — is
    unresolvable: the consumer's key cannot be computed, and the consumer is not certified
    for the same reason readiness refuses the dependency. The labelled bindings PR-1 used
    (`unstamped:` / `unverified:` / `uncertified:<id>`) are gone with the decision they
    deferred."""
    sel = resolver.select(dep_node_key, step)
    if sel.ok and sel.output_hash is not None:
        return sel.output_hash
    reason = str(sel.reason or "")
    if reason.startswith("derivation_inputs_unresolvable:"):
        # The dependency's own key could not be computed because one of ITS dependencies has
        # no certified output: that message already names the deepest node, so pass it
        # through rather than nesting one "dependency X has no ..." per level of the closure.
        raise DerivationInputsUnresolvable(reason)
    raise DerivationInputsUnresolvable(
        f"derivation_inputs_unresolvable: dependency {dep_node_key} has no certified "
        f"{step} output ({reason}); build the dependency closure first, e.g. "
        f"run_workflow.py --with-deps")


def _spec_file_hash(repo_root: Path, spec_ref: str, name: str) -> str:
    digest = _compute_sha256(repo_root / spec_ref / name)
    if digest == "sha256:missing":
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: spec file {spec_ref}/{name} is missing")
    return digest


def _derived_closure_graph(repo_root: Path, node_key: str, spec_ref: str) -> dict[str, Any]:
    """The dependency closure of `node_key` as the registry derives it NOW (the same pure
    builder the compile sidecar uses, without `via` paths). A
    closure that does not build is unresolvable: the compile key cannot be computed for a
    node whose `deps.yaml` + catalog yield no valid closure."""
    from tools.dependency_graph import build_dependency_graph
    try:
        graph, error = build_dependency_graph(
            repo_root, target_spec_ref=spec_ref, target_node_key=node_key, include_via=False)
    except RecursionError as exc:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: dependency closure of {node_key} is too deep "
            f"to derive ({type(exc).__name__})") from exc
    if error is not None or not isinstance(graph, dict):
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: dependency closure of {node_key} does not "
            f"resolve from deps.yaml + spec_catalog.yaml "
            f"({(error or {}).get('reason')}: {(error or {}).get('detail')})")
    return graph


def _harness_for_key(repo_root: Path, node_key: str, target: TargetProfile) -> str:
    """`harness_node_key_for`, with a harness the catalog cannot resolve reported as the
    unresolvable input it is (the launch gate refused it before the run; a catalog edited
    since is what reaches this)."""
    from tools.target_profile import TargetProfileError
    try:
        return harness_node_key_for(repo_root, node_key, target)
    except TargetProfileError as exc:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: the harness of target {target.target_id} does "
            f"not resolve ({exc.detail})") from exc


def _published_surface(
    repo_root: Path, dep_node_key: str, resolver: DerivationResolver,
) -> str | None:
    """What a consumer's compile and generate keys bind a dependency BY: the hash of the
    canonical structured form of §5.1 of the dependency's `controlled_spec.md`, the form
    `Compile.static` pins its IR's `public_api` to. It rests on a file no leaf writes rather
    than on what a leaf authored, so a re-derived dependency keeps its surface, and a prose edit
    outside §5.1 moves nothing. A dependency whose spec has no §5.1 answers `None`, which still
    pins its membership and its position; every `component` and `problem` spec of the corpus
    carries one (a `problem` publishes `__step` since issue #589).

    The dependency must have a certified compile output, whose hash is not bound. A §5.1 that is
    present and does not load raises: the surface cannot be stated."""
    from tools.structured_signatures import load_structured_signatures
    from tools.validate_pipeline_semantics import (
        _extract_controlled_spec_section, _extract_subsection_51, _section51_fence_body)
    _dependency_output_hash(resolver, dep_node_key, "compile")
    kind, spec_id, _version = _parse_node_key_strict(dep_node_key)
    spec_ref = resolve_spec_ref_for(repo_root, kind, spec_id)
    if not spec_ref:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: dependency {dep_node_key} does not resolve to one "
            "spec directory")
    path = repo_root / spec_ref / "controlled_spec.md"
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")  # as `_section51_fence_body`
    except OSError as exc:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: {path} is unreadable ({exc})") from exc
    section = _extract_controlled_spec_section(text, "5")
    if section is None or _extract_subsection_51(section) is None:
        return None
    body, err = _section51_fence_body(path)
    struct: dict[str, Any] = {}
    if body is not None:
        struct, err = load_structured_signatures(body)
    if err:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: dependency {dep_node_key} publishes no readable "
            f"surface: {err}")
    return _sha256_hex(_canonical_json_bytes(struct))


def _generate_closure_entry(
    repo_root: Path, member: str, resolver: DerivationResolver,
) -> dict[str, Any]:
    """One `closure[]` entry of a generate key. The member's certified generate output is what
    the attempt staged and its Build will link, so a member without one is unresolvable; its
    hash is Build's input, not this key's."""
    surface = _published_surface(repo_root, member, resolver)
    _dependency_output_hash(resolver, member, "generate")
    return {"node_key": member, "surface": surface}


def _pipeline_closure_for_key(
    repo_root: Path, ir_ref: str, node_key: str, target: TargetProfile,
) -> list[str]:
    """`pipeline_closure_nodes` for a derivation key (the same unresolvable-harness report
    as `_harness_for_key`)."""
    from tools.target_profile import TargetProfileError
    try:
        return pipeline_closure_nodes(repo_root, ir_ref, node_key, target)
    except TargetProfileError as exc:
        raise DerivationInputsUnresolvable(
            f"derivation_inputs_unresolvable: the harness of target {target.target_id} does "
            f"not resolve ({exc.detail})") from exc


#: The `self_verdict` values that make a run another target's reference (issue #324; read off
#: `self_verdict` since issue #383, which took the comparison out of the verdict).
COMPARAND_VERDICTS: frozenset[str] = frozenset({"pass", "xfail"})


class CrossTargetUnevaluable(RuntimeError):
    """`resolve_comparands` could not establish another target's reference: a declared target
    does not list or load, or an eligible run holds no capture (issue #383). Not a phase-start
    failure: `cross_target_agreement` turns it into an `unevaluable` agreement, which is not
    ready, and names the cause."""


def resolve_comparands(
    repo_root: Path, *, node_key: str, ir_hash: str, target: TargetProfile,
    spec_refs: Mapping[str, str] | None = None,
) -> Any:
    """The comparands of `node_key`'s cross-target agreement on `target` (issue #324, R4-d;
    read by `cross_target_agreement` since issue #383): for every OTHER
    declared target B (`list_target_ids`, in target-id order), B's certified variant of the
    same node over the same IR, as a `primary_evidence.ComparandEvidence` — or, when B has
    none, B's entry in `absent` with the reason (issue #345). Returns a
    `primary_evidence.ComparandResolution`.

    B contributes when `DerivationResolver(target=B, comparand=True).select(node_key, "build")`
    is certified — B's Build matched under the toolchain identity B was stamped with, so this
    host's probe of B's compiler (which a run of another target need not have, and which since
    issue #333 is not the compiler that built a site-built B) decides nothing (issue #345) —
    and B's certified Compile output is `ir_hash` (the IR this Validate runs; Compile is
    target-free, so a B built from the current IR shares it — the check keeps a B built from
    another IR out when this run's IR is not the standing one). Its comparand is the LATEST
    run (`_freshness_key_from_id`) under the selected pipeline's `runs/` whose node directory
    holds a `verdict.json` (the execute gate passed) that
      - was evaluated over THIS IR (`verdict.json#ir_hash == ir_hash`: a Build can stay
        certified across an IR re-derivation that reproduced its sources, so the binary alone
        does not say which IR's cases and predicates a run was judged on — Codex, round 2;
        a verdict written before R4-d has no `ir_hash` and is never a comparand),
      - has a `self_verdict` in `COMPARAND_VERDICTS`, and
      - finished Validate: the post-judge gate passed it (`post_judge_meta.json#status` is
        `pass`). The gate runs on every run whose verdict passes, so one without it was
        rejected or never finished. Until issue #724 PR-2 this also asked that `validate.judge`
        had passed it (`semantic_review.json` decided `pass`); the judge is deleted, so a run
        holding no such file is a comparand, and an older run that holds one is read exactly as
        it is (the judge ran before the gate, so an older run whose judge failed has no passing
        `post_judge_meta.json` and stays excluded).
    A run's verdict carries no comparison since issue #383, so a variant that passes its own
    tests is a reference whatever another target's captures say; a disagreement between two
    such variants is then seen by BOTH targets' agreements, which is what stops both (plan
    decision 6 of issue #324). A verdict-8 run that failed only a cross-target record has a
    failing `self_verdict` and is no reference;
    whose `trial_meta.json#source_binary_id` is the selected binary; and whose
    `validate_meta.json`, when present, is not revoked (`verification_status: revoked`, which
    `_revoke_stage_meta` writes; post_judge writes `pass` before its gate runs, so the meta
    says nothing else about the run). A B with no such run contributes nothing.

    The reason recorded for a B that contributes nothing is the first clause above that
    refused: the comparand resolver's Build `reason`, `compile_ir_mismatch`, or
    `no_eligible_run`.

    Cycle-free by construction: B is selected up to BUILD, and whether B's own Validate is
    certified is never asked. This is the one place a resolver for a target other than the
    run's is built, and it never leaves this function.

    Raises `CrossTargetUnevaluable` when the declared targets cannot be listed or one of them
    does not load (a broken profile must not quietly drop a reference). An eligible run holding
    no capture is the evaluation's to find (a `structural` record, `unevaluable`)."""
    from tools.primary_evidence import ComparandEvidence, ComparandResolution
    from tools.target_profile import list_target_ids
    node_key = node_key.strip()
    try:
        target_ids = list_target_ids(repo_root)
    except TargetProfileError as exc:
        raise CrossTargetUnevaluable(
            f"comparand: the declared targets do not list ({exc.detail})") from exc
    safe = _node_key_to_safe(node_key)
    out: list[Any] = []
    absent: list[dict[str, str]] = []
    for tid in target_ids:
        if tid == target.target_id:
            continue
        try:
            other = load_target_profile(repo_root, tid)
        except TargetProfileError as exc:
            raise CrossTargetUnevaluable(
                f"comparand: target {tid} does not load ({exc.detail})") from exc
        resolver = DerivationResolver(repo_root, spec_refs=spec_refs, target=other,
                                      comparand=True)
        build = resolver.select(node_key, "build")
        if not (build.ok and build.pipeline_ref and build.binary_id):
            absent.append({"target_id": tid, "reason": build.reason or "binary_not_found"})
            continue
        if resolver.select(node_key, "compile").output_hash != ir_hash:
            absent.append({"target_id": tid, "reason": "compile_ir_mismatch"})
            continue
        runs = repo_root / build.pipeline_ref / "runs"
        best: tuple[tuple[str, int], str, Path] | None = None
        for run in (runs.iterdir() if runs.is_dir() else ()):
            rkey = _freshness_key_from_id(run.name)
            node_dir = run / safe
            if rkey is None or not node_dir.is_dir():
                continue
            verdict = _read_json_or_none(node_dir / "verdict.json")
            trial = _read_json_or_none(node_dir / "trial_meta.json")
            if not (isinstance(verdict, dict) and isinstance(trial, dict)):
                continue
            if trial.get("source_binary_id") != build.binary_id:
                continue
            if verdict.get("ir_hash") != ir_hash:
                continue
            if verdict.get("self_verdict") not in COMPARAND_VERDICTS:
                continue
            gate = _read_json_or_none(node_dir / "post_judge_meta.json")
            if not (isinstance(gate, dict) and gate.get("status") == "pass"):
                continue
            meta = _read_json_or_none(node_dir / "validate_meta.json")
            if isinstance(meta, dict) and str(
                    meta.get("verification_status", "")).strip().lower() == "revoked":
                continue
            if best is None or rkey > best[0]:
                best = (rkey, run.name, node_dir)
        if best is None:
            absent.append({"target_id": tid, "reason": "no_eligible_run"})
            continue
        _rkey, run_id, node_dir = best
        # The comparand's run is read through the store of ITS target's output format
        # (issue #644), whatever format the asking target writes.
        out.append(ComparandEvidence(
            target_id=tid, pipeline_ref=str(build.pipeline_ref), run_id=run_id,
            run_dir=node_dir, output_format=other.output_format))
    return ComparandResolution(comparands=out, absent=absent)


#: The longest cause an `unevaluable` agreement carries into a readiness reason.
_CROSS_TARGET_CAUSE_MAX_CHARS = 200


def cross_target_agreement(
    repo_root: Path, node_key: str, *, resolver: DerivationResolver,
) -> Any:
    """Whether `node_key`'s certified Validate on `resolver.target` agrees, NOW, with every
    other declared target's eligible run under the IR's cross-target predicates (issue #383).
    Returns a `primary_evidence.CrossTargetAgreement`; never raises.

    The comparison is a deterministic host computation over two runs' captures, so it is not
    keyed and not stored: it is asked beside `DerivationResolver.select(node_key, "validate")`
    — which keeps meaning "derived on its own key" — wherever "is this node ready" or "may this
    run pass" is asked (`_verify_dep_stage_detail`, the completion vouch,
    `check_phase_certified`). Another target's certification therefore moves no key of this
    one (#383's race), and a disagreement makes the node not ready on BOTH targets, since each
    target's agreement reads the other's run.

    Steps: the certified Validate selection (its run node directory holds this target's
    captures) → its IR → `cross_target_predicates` (none: `not_applicable`) →
    `resolve_comparands` over the IR hash the selection's key carries (none: `no_comparand`,
    with `absent`) → `evaluate_primary_predicates(..., cross_target_only=True)`. A `physics`
    record against a target is `disagree` naming it; a `structural` record (a comparand run
    without captures, this run's own captures gone), and any exception (a target that does not
    load, an IR that does not read), is `unevaluable` — not ready, with the cause. A Validate
    that is not certified is `unevaluable` too; the callers ask only after it is.

    Recursion-free: `resolve_comparands` selects other targets up to Build only, and no key
    reads a dependency's Validate."""
    from tools.primary_evidence import (
        CrossTargetAgreement,
        cross_target_predicates,
        evaluate_primary_predicates,
    )
    node_key = node_key.strip()

    def unevaluable(error: str, *, comparands: list[dict[str, str]] | None = None,
                    absent: list[dict[str, str]] | None = None,
                    records: list[dict[str, Any]] | None = None,
                    own_run: dict[str, str] | None = None) -> Any:
        return CrossTargetAgreement("unevaluable", comparands or [], absent or [], [],
                                    records or [], own_run, error)

    try:
        sel = resolver.select(node_key, "validate")
        if not sel.ok:
            return unevaluable(f"validate_not_certified:{sel.reason}")
        run_dir = sel.stage_dir()
        if run_dir is None or not sel.ir_ref:
            return unevaluable("validate_selection_has_no_run")
        ir = yaml.safe_load((repo_root / sel.ir_ref / "spec.ir.yaml").read_text(
            encoding="utf-8"))
        ir = ir if isinstance(ir, dict) else {}
        if not cross_target_predicates(ir):
            return CrossTargetAgreement("not_applicable", [], [], [], [], None, None)
        if resolver.target is None:
            return unevaluable("target_unresolved")
        ir_hash = (sel.derivation_inputs or {}).get("ir")
        if not isinstance(ir_hash, str) or not ir_hash:
            return unevaluable("validate_key_has_no_ir")
        spec = resolver.spec_ref(node_key)
        resolution = resolve_comparands(
            repo_root, node_key=node_key, ir_hash=ir_hash, target=resolver.target,
            spec_refs={node_key: spec} if spec else None)
        own_format = resolver.target.output_format
        own_run = {"pipeline_ref": str(sel.pipeline_ref), "run_id": str(sel.run_id)}
        comparands = [c.detail() for c in resolution.comparands]
        absent = list(resolution.absent)
        if not resolution.comparands:
            return CrossTargetAgreement("no_comparand", [], absent, [], [], own_run, None)
        records = evaluate_primary_predicates(
            ir, run_dir, output_format=own_format, comparands=resolution.comparands,
            cross_target_only=True)
        structural = [r for r in records if r.get("kind") == "structural"]
        if structural:
            errors = [str(ev.get("error")) for r in structural
                      for ev in (r.get("evaluated") or [])
                      if isinstance(ev, dict) and ev.get("error")]
            return unevaluable(
                f"structural:{structural[0].get('quantity')}: "
                f"{errors[0] if errors else 'not evaluated'}".replace(f"{repo_root}/", ""),
                comparands=comparands, absent=absent, records=records, own_run=own_run)
        disagreeing = sorted({
            str(ev.get("comparand")) for r in records
            for ev in (r.get("evaluated") or [])
            if isinstance(ev, dict) and ev.get("comparand") is not None
            and not ev.get("satisfied")})
        return CrossTargetAgreement("disagree" if disagreeing else "agree", comparands,
                                    absent, disagreeing, records, own_run, None)
    except Exception as exc:  # noqa: BLE001 - an agreement that cannot be evaluated is not ready
        # Repo-relative: an absolute workspace path would spend the readiness reason's budget
        # before the message says why.
        return unevaluable(f"{type(exc).__name__}: {exc}".replace(f"{repo_root}/", ""))


def cross_target_not_ready_reason(agreement: Any) -> str | None:
    """The readiness reason a not-ready agreement reports (`cross_target_disagreement:<target
    ids>` / `cross_target_unevaluable:<cause>`), or None when it is ready."""
    if agreement.status == "disagree":
        return "cross_target_disagreement:" + ",".join(agreement.disagreeing)
    if agreement.status == "unevaluable":
        cause = str(agreement.error or "unknown").replace("\n", " ")
        if len(cause) > _CROSS_TARGET_CAUSE_MAX_CHARS:
            # both ends: the head names what failed, the tail says why
            head = _CROSS_TARGET_CAUSE_MAX_CHARS // 3
            cause = f"{cause[:head]}...{cause[-(_CROSS_TARGET_CAUSE_MAX_CHARS - head - 3):]}"
        return "cross_target_unevaluable:" + cause
    return None


def phase_derivation_inputs(
    repo_root: Path,
    *,
    node_key: str,
    step: str,
    spec_ref: str | None = None,
    ir_ref: str | None = None,
    source_ref: str | None = None,
    binary_ref: str | None = None,
    resolver: DerivationResolver | None = None,
    target: TargetProfile | None = None,
) -> dict[str, Any]:
    """The contract inputs of `(node_key, step)` as they are NOW — the mapping
    `tools.derivation.derivation_key` hashes and `_stamp_certification` records as
    `derivation_inputs`. The section comment above lists each phase's members.

    `spec_ref` is the node's spec directory; `ir_ref` / `source_ref` / `binary_ref` name this
    node's OWN upstream artifacts (the reserved or adopted ones) whose certified output hashes
    the phase binds to — `generate` needs the IR, `build` the IR (for its closure and
    toolchain) and the source, `validate` the IR and the binary. A missing required ref, an
    upstream that is not certified, a dependency with no certified output and an unreadable
    spec file all raise `DerivationInputsUnresolvable`. `resolver` is the evaluation's
    memo of dependency selections (`DerivationResolver`); one is created for `target` when
    none is passed. A pipeline phase's inputs are the resolver's target's (issue #284). No
    caller reaches here for one with a target-less resolver: `select` answers
    `target_unresolved` first, and the conductor's resolver always carries its target."""
    step_token = step.strip().lower()
    if step_token not in DERIVATION_STEPS:
        raise ValueError(f"unsupported step for a derivation: {step!r}")
    node_key = node_key.strip()
    resolver = resolver or DerivationResolver(repo_root, target=target)

    def need(name: str, value: str | None) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DerivationInputsUnresolvable(
                f"derivation_inputs_unresolvable: {step_token} derivation of {node_key} "
                f"needs {name}")
        return _normalize_rel_posix(value.strip())

    if step_token == "compile":
        spec = need("spec_ref", spec_ref)
        graph = _derived_closure_graph(repo_root, node_key, spec)
        signature = _closure_signature(graph)
        profiles: list[dict[str, str]] = []
        # `build_dependency_graph`'s profile expansion has already resolved each adopted
        # profile to one spec directory (it refuses `profile_spec_ref_unresolved` otherwise).
        for entry in graph.get("profiles") or []:
            profile_ref = resolve_spec_ref_for(repo_root, "profile", entry["profile_id"])
            profiles.append({
                "node_key": entry["node_key"],
                "controlled_spec": _spec_file_hash(repo_root, profile_ref, "controlled_spec.md"),
            })
        profiles.sort(key=lambda p: p["node_key"])
        compile_inputs: dict[str, Any] = {
            "spec": {
                "controlled_spec": _spec_file_hash(repo_root, spec, "controlled_spec.md"),
                "tests": _spec_file_hash(repo_root, spec, "tests.md"),
                "deps": _spec_file_hash(repo_root, spec, "deps.yaml"),
            },
            "profiles": profiles,
            "dependency_graph": _sha256_hex(_canonical_json_bytes(signature)),
            # Not the document the leaves are shown (`dependency_surface.json`, which copies each
            # dependency IR's `public_api`): two IRs its gate accepts as equal differ there as
            # text, so a re-authored dependency would re-derive its consumers.
            "dependency_surface": _sha256_hex(_canonical_json_bytes([
                {"node_key": nk, "surface": _published_surface(repo_root, nk, resolver)}
                for nk in _component_direct_deps(graph, node_key)])),
        }
        own_libraries = _own_library_declarations(repo_root, node_key, spec)
        if own_libraries is not None:
            compile_inputs["libraries"] = own_libraries
        return compile_inputs

    ir = need("ir_ref", ir_ref)
    target = resolver.target
    ir_hash = _certified_output_hash(
        repo_root, repo_root / ir / "ir_meta.json", what=f"{node_key} compile")
    closure_nodes = _pipeline_closure_for_key(repo_root, ir, node_key, target)
    if step_token == "generate":
        spec = need("spec_ref", spec_ref)
        harness_nk = _harness_for_key(repo_root, node_key, target)
        from tools.codegen_bundle import harness_capability_manifest_document_for
        from tools.target_profile import harness_capabilities
        # The set is a function of `target.profile`, already a member; the document is kept as
        # a member so that its derivation from the profile (issue #690) moved no key.
        manifest = harness_capability_manifest_document_for(
            harness_nk, harness_capabilities(target))
        generate_inputs: dict[str, Any] = {
            "ir": ir_hash,
            "spec": {
                "controlled_spec": _spec_file_hash(repo_root, spec, "controlled_spec.md"),
                "tests": _spec_file_hash(repo_root, spec, "tests.md"),
            },
            # The target the source is generated FOR: the producer is shown the profile
            # (`Conductor._leaf_target_profile_document`), so the profile's content is an
            # input. The harness below is the one the profile names (issue #284), so a
            # profile that names another harness moves both members.
            "target": {"target_id": target.target_id, "profile": target.sha256},
            "harness": {
                "node_key": harness_nk,
                "manifest": _sha256_hex(_canonical_json_bytes(manifest)),
            },
            "closure": [_generate_closure_entry(repo_root, nk, resolver) for nk in closure_nodes],
        }
        own_libraries = _own_library_declarations(repo_root, node_key, spec)
        if own_libraries is not None:
            generate_inputs["libraries"] = own_libraries
        return generate_inputs

    if step_token == "build":
        source = need("source_ref", source_ref)
        build_inputs: dict[str, Any] = {
            "source": _certified_output_hash(
                repo_root, repo_root / source / "source_meta.json",
                what=f"{node_key} generate"),
            "closure": [
                {"node_key": nk, "source": _dependency_output_hash(resolver, nk, "generate")}
                for nk in sorted(closure_nodes)],
            # A comparand resolver reads the host-probed leaves off each candidate's stamp
            # (issue #345), so it probes nothing here; the stamp path (`phase_derivation`)
            # never passes one, so a stamp always carries the probed identity.
            "toolchain": _target_toolchain_identity(target, probe=not resolver.comparand),
        }
        # The external libraries the build links (issue #644): present only when the closure
        # declares one, so no other key moves.
        libraries = _closure_libraries_member(repo_root, node_key, closure_nodes, resolver,
                                              probe=True)
        if libraries is not None:
            build_inputs["libraries"] = libraries
        # The node's oracle (issue #680): present only when it declares one for the target's
        # language. Without it a changed adapter or a rebuilt oracle library would leave
        # standing a certification that states agreement with an oracle no longer declared.
        oracle_member = _oracle_member(repo_root, node_key, resolver, probe=True)
        if oracle_member is not None:
            build_inputs["oracle"] = oracle_member
        return build_inputs

    spec = need("spec_ref", spec_ref)
    binary = need("binary_ref", binary_ref)
    inputs: dict[str, Any] = {
        "binary": _certified_output_hash(
            repo_root, repo_root / binary / "binary_meta.json", what=f"{node_key} build"),
        "ir": ir_hash,
        "spec": {"tests": _spec_file_hash(repo_root, spec, "tests.md")},
        # The execution policy `_execute_inproc` imposes: the target it runs for (the launch
        # shape it hands `run_program` is composed from the profile, `tools/host_execution.py`,
        # and the profile hash binds it), and the quality-check preset it re-runs the binary
        # through — the target build system's (`build_execute`, issue #424 PR-2).
        "run_policy": {
            "target_id": target.target_id,
            "profile": target.sha256,
            "threads_per_rank": target.threads_per_rank,
            "preset": backend_registry.capability_module(
                "build_system", target.toolchain["build_system"],
                "build_execute").QUALITY_CHECK_PRESET,
        },
    }
    # The external libraries the binary loads (issue #644): the Build stamp's own record,
    # copied rather than probed. A library swapped under an unchanged binary leaves the
    # binary's bytes — the `binary` member — identical, so without this a Validate never run on
    # the new library would be reused. Present only when the closure declares one.
    declares = _closure_libraries_member(repo_root, node_key, closure_nodes, resolver,
                                         probe=False)
    if declares is not None:
        build_meta = _read_json_or_none(repo_root / binary / "binary_meta.json")
        stamped = (build_meta or {}).get("derivation_inputs") if isinstance(
            build_meta, dict) else None
        recorded = stamped.get("libraries") if isinstance(stamped, dict) else None
        if not isinstance(recorded, list) or _stamped_library_files(stamped, declares) is None:
            raise DerivationInputsUnresolvable(
                f"derivation_inputs_unresolvable: validate derivation of {node_key}: the "
                f"closure declares libraries and the Build stamp at {binary} records none "
                f"that match")
        inputs["libraries"] = recorded
    # The oracle (issue #680), copied off the Build stamp as the libraries are: an oracle
    # library swapped under an unchanged oracle binary moves no binary byte.
    oracle_declared = _oracle_member(repo_root, node_key, resolver, probe=False)
    if oracle_declared is not None:
        build_meta = _read_json_or_none(repo_root / binary / "binary_meta.json")
        stamped = (build_meta or {}).get("derivation_inputs") if isinstance(
            build_meta, dict) else None
        oracle_recorded = _stamped_oracle(stamped, oracle_declared)
        if oracle_recorded is None:
            raise DerivationInputsUnresolvable(
                f"derivation_inputs_unresolvable: validate derivation of {node_key}: the "
                f"node declares an oracle and the Build stamp at {binary} records none that "
                f"matches")
        inputs["oracle"] = oracle_recorded
    return inputs


def phase_derivation(repo_root: Path, *, node_key: str, step: str, **refs: str | None) -> dict[str, Any]:
    """`{"derivation_key", "derivation_inputs", "transformation"}` of `(node_key, step)` NOW.
    The one record the conductor stamps on a launch, a step_result and (on pass) the
    certifying stage meta; `refs` are `phase_derivation_inputs`'s keyword refs."""
    from tools.derivation import transformation_versions
    inputs = phase_derivation_inputs(repo_root, node_key=node_key, step=step, **refs)
    step_token = step.strip().lower()
    return {
        "derivation_key": _derivation_key(step_token, inputs),
        "derivation_inputs": inputs,
        "transformation": list(transformation_versions()[step_token]),
    }


def _revocable_stage_meta_path(
    repo_root: Path,
    orchestration_id: str,
    *,
    node_key: str,
    step: str,
) -> Path | None:
    """The stage meta a re-derivation of `(node_key, step)` must revoke, resolved from THIS
    orchestration's records — the compile reservation for the ir_id, and
    `<pipeline_ref>/lineage.json` for the source_id / binary_id / run_id.

    Resolved from the records rather than by re-selecting the latest artifact: a retry is a
    statement about the artifact this run produced, and the latest one under the root may
    already belong to a different orchestration. `None` when the record naming the meta does
    not exist — the caller reports that as a no-op rather than an error, because a phase that
    never produced a meta has nothing to revoke.
    """
    step_token = step.strip().lower()
    meta_filename = CERTIFYING_META_FILENAME_BY_STEP.get(step_token)
    if meta_filename is None:
        return None
    node_safe = _node_key_to_safe(node_key.strip())
    res_dir = _orchestration_root(repo_root, orchestration_id) / "reservations" / node_safe
    if step_token == "compile":
        ir_id = _reserved_id(res_dir, "compile")
        if ir_id is None:
            return None
        return repo_root / "workspace" / "ir" / node_safe / ir_id / meta_filename
    pipe_dir = _reserved_pipeline_dir(repo_root, res_dir)
    if pipe_dir is None:
        return None
    # `_read_json` RAISES, and this function's contract — stated in its own docstring and in
    # `docs/CLI_REFERENCE_RARE.md` — is `None`, which the caller reports as a `noop`. The
    # documented RUNBOOK recipe (`revoke-artifact --step <phase>` against a node whose pipeline
    # directory was never written) returned a raw errno without this.
    #
    # `_read_json_or_none` rather than an `.is_file()` guard, because the first fix covered ONE
    # spelling: a MISSING lineage answered `noop` while a CORRUPT one still raised
    # `JSONDecodeError`, against a doc sentence written in the same commit that says "never an
    # error". Rule 1-b's "try at least one more different spelling" applied to a fix rather than
    # to a deletion. A non-dict is treated the same way — it names no stage id either.
    lineage = _read_json_or_none(pipe_dir / "lineage.json")
    if not isinstance(lineage, dict):
        return None
    key = {"generate": "source_id", "build": "binary_id", "validate": "run_id"}[step_token]
    stage_id = lineage.get(key)
    if not (isinstance(stage_id, str) and stage_id.strip()):
        return None
    if step_token == "validate":
        # `runs/<run_id>/<node_safe>/validate_meta.json`: the run's node dir, the same place
        # `_phase_certified` reads the verdict and `phase_required_outputs` declares the meta.
        return pipe_dir / "runs" / stage_id.strip() / node_safe / meta_filename
    sub = "source" if step_token == "generate" else "binary"
    return pipe_dir / sub / stage_id.strip() / meta_filename


def _revoke_stage_meta(
    repo_root: Path,
    meta_path: Path,
    *,
    reason: str,
    trigger_agent_run_id: str,
    last_fail_reason: str | None = None,
    severity: str | None = None,
    repair_strategy: str | None = None,
) -> dict[str, Any]:
    """Rewrite a stage meta as `verification_status: "revoked"`, in place, preserving every
    other key. Returns `{status, meta_ref, prior_verification_status}`.

    Revocation is what makes a re-derivation decision reach the ARTIFACT. The prior model
    recorded the decision in this orchestration's own ledger (a checkpoint entry dropped, a
    phase state reset), so a COLD re-run — which reads no ledger — would have adopted the very
    artifact a previous run rejected. `prior_verification_status` keeps the audit trail: what
    was revoked is visible, not merely that the meta is now non-passing.
    """
    doc = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise RuntimeError(f"revoke-artifact: {meta_path} is not a JSON object")
    prior = doc.get("verification_status")
    # Re-revoking must not overwrite the audit trail with `revoked`. This function exists to
    # keep "what was revoked" visible rather than merely "the meta is non-passing", and a
    # second revocation of the same meta is REACHABLE by a documented route: the dev F1
    # rollback revokes automatically before terminalizing, and the operator then follows
    # `docs/RUNBOOK.md` §3-1 and revokes by hand. Writing `prior_verification_status:
    # "revoked"` there destroys the one fact the field is for.
    if prior != "revoked":
        doc["prior_verification_status"] = prior
    # What is REPORTED is what the meta now records, not the local `prior` — on a second
    # revocation those differ, and reporting the local one would tell the caller the audit
    # trail had been overwritten when it has just been protected.
    reported_prior = doc.get("prior_verification_status")
    doc["verification_status"] = "revoked"
    doc["revoked_at"] = _utc_now_iso()
    doc["revoked_by_agent_run_id"] = trigger_agent_run_id.strip()
    doc["revocation_reason"] = reason
    # `revocation_severity` takes THIS decision's value, INCLUDING clearing it when the decision
    # carries none. It used to be written only when in-vocabulary, which made it sticky where
    # every sibling field is refreshed: a second revocation carrying no grade left the FIRST
    # one's `critical` standing beside the SECOND one's findings, and the resumed repair then
    # paired mismatched halves and chose `restart`. Every non-escalate route passes
    # `decision.severity=None`, so that pairing was the common case rather than the corner.
    #
    # `last_fail_reason` is deliberately NOT cleared the same way, and the asymmetry is the
    # point: `revocation_severity` is revocation state with exactly one writer, while
    # `last_fail_reason` is the PHASE's own field — the verify substep and the meta authors
    # write it too — so clearing it here would destroy a failure reason this revocation knows
    # nothing about. It is overwritten when given and left alone otherwise, which is what this
    # function's docstring has always said.
    if last_fail_reason is not None and last_fail_reason.strip():
        doc["last_fail_reason"] = last_fail_reason
    doc["revocation_severity"] = severity if severity in REVOCATION_SEVERITIES else None
    doc["revocation_repair_strategy"] = (
        repair_strategy if repair_strategy in REVOCATION_REPAIR_STRATEGIES else None)
    _write_json(meta_path, doc)
    return {
        "status": "revoked",
        "meta_ref": _normalize_rel_posix(str(meta_path.relative_to(repo_root)))
        if meta_path.is_relative_to(repo_root) else str(meta_path),
        "prior_verification_status": reported_prior,
    }


def _certified_by_ref(step: str, detail: dict[str, Any]) -> str:
    """The one id that identifies what a skipped phase adopted, per phase."""
    key = {"compile": "ir_ref", "generate": "source_id",
           "build": "binary_id", "validate": "run_id"}[step]
    return str(detail.get(key) or "")


def check_phase_certified(
    repo_root: Path,
    orchestration_id: str,
    *,
    node_key: str,
    step: str,
    agent_run_id: str | None = None,
    record: bool = True,
    target_id: str | None = None,
) -> dict[str, Any]:
    """`_phase_certified` plus its DURABLE record: a certified phase is transitioned to
    `skipped_certified` in `phase_state.json` (event `skip_certified`).

    `target_id` (`--target`) names the target a pipeline phase is asked for; without it the
    orchestration's recorded target is used (`_orchestration_target`). It is loaded whenever
    it is given — an undeclared id raises `TargetProfileError` even for `compile`, whose
    answer does not read it — so a misspelled target is refused rather than silently unused.

    `cross_target` is the node's cross-target agreement (`cross_target_agreement`, issue #383)
    as a document when `step` is `validate` and it is certified, else None. `certified` keeps
    its meaning — derived on its own key — and the agreement is reported beside it.

    `record=False` (`--no-record`) answers WITHOUT the transition: the conductor asks that way
    for a phase named in `--rederive`, which runs although certified — recording it skipped
    would be a false record (`skip_certified` is "a skipped step carrying the adopted artifact",
    rule 43), and one that outlives the run when the forced attempt stops before
    `record_launch` (correctness round 1, F2).

    That transition is the durable record that the phase was certified rather than run. It is
    what will let the completion vouch accept a node whose earlier attempt in this same
    orchestration left a `fail` step_result behind — the phase became certified afterwards and
    was skipped, and the phase state is what says so — clause (e) of the completion vouch reads
    it to exempt a `(node, phase)` whose earlier attempt in this orchestration left a `fail`
    step_result. Idempotent — `run_phase` may ask again after `conduct` already asked.

    NOT read-only, and that is why it is the wrong thing to probe with: it WRITES
    `skipped_certified`. A caller that only wants the answer asks `_phase_certified`.
    """
    _require_preflight_launchable(repo_root, orchestration_id, enforce_live_probe=False)
    target = (load_target_profile(repo_root, target_id.strip())
              if isinstance(target_id, str) and target_id.strip() else None)
    certified, detail, resolver = _phase_certification(
        repo_root, orchestration_id, node_key, step, target=target)
    step_token = step.strip().lower()
    cross_target = (
        cross_target_agreement(repo_root, node_key, resolver=resolver).document()
        if certified and step_token == "validate" else None)
    if certified and record:
        _transition_node_step_phase_state(
            repo_root,
            orchestration_id,
            node_key=node_key,
            step=step_token,
            new_state="skipped_certified",
            event="skip_certified",
            agent_run_id=agent_run_id,
            # The artifact THIS phase adopted, not the IR every phase happens to stand on:
            # an operator reading `phase_state_log.jsonl` to see which binary a skipped Build
            # took must not be handed the ir_ref.
            reason=f"certified:{_certified_by_ref(step_token, detail)}",
        )
    phase_state_doc = _load_phase_state(repo_root, orchestration_id) or {}
    node_states = phase_state_doc.get("node_states")
    current_state = None
    if isinstance(node_states, dict):
        inner = node_states.get(_node_key_to_safe(node_key.strip()))
        if isinstance(inner, dict):
            current_state = inner.get(step_token)
    return {
        "certified": certified,
        "node_key": node_key.strip(),
        "step": step_token,
        "reason": detail.get("reason"),
        "ir_ref": detail.get("ir_ref"),
        "ir_id": detail.get("ir_id"),
        "pipeline_ref": detail.get("pipeline_ref"),
        "source_id": detail.get("source_id"),
        "binary_id": detail.get("binary_id"),
        "run_id": detail.get("run_id"),
        "derivation_key": detail.get("derivation_key"),
        "output_hash": detail.get("output_hash"),
        "revoked": bool(detail.get("revoked")),
        "last_fail_reason": detail.get("last_fail_reason"),
        "revocation_severity": detail.get("revocation_severity"),
        "revocation_repair_strategy": detail.get("revocation_repair_strategy"),
        "phase_state": current_state,
        "cross_target": cross_target,
    }


def _certified_model_source(
    repo_root: Path, node_key: str, *, resolver: DerivationResolver | None = None,
    target: TargetProfile | None = None,
) -> Path | None:
    """Resolve the certified model source of a dependency — the EXACT file Build stages/links,
    named by the target language's ``bundle_facts.model_basename`` — or ``None`` if it cannot
    be resolved.

    Single-sources the load-bearing selection every reader of a dependency's source depends
    on: the orientation hint (``_resolve_dependency_facts``), the compile-time published
    surface (``_resolve_component_dep_surface``), the harness pin
    (``workflow_conductor._write_runner``) and the deterministic Build staging
    (``_resolve_certified_closure_binding``), so the interface a consumer is SHOWN at Generate
    and the source its Build COMPILES each come from the selection of their moment. The
    selection is the dependency's SELECTED certified Generate output under its key
    recomputed now (``DerivationResolver.select``) — never the latest directory, never the
    binary's ``source_source_id``.

    Pure and NEVER raises: any unresolvable selection or missing source file yields
    ``None``. Callers decide the policy — the orientation hint skips the dep; Build re-raises
    its fail-closed precondition. The source is the one certified for the resolver's target
    (else ``target``); with neither there is none (issue #284).
    """
    try:
        spec_id = _parse_node_key_strict(node_key)[1]
        resolver = resolver or DerivationResolver(repo_root, target=target)
        sel = resolver.select(node_key, "generate")
        stage_dir = sel.stage_dir()
        if not sel.ok or stage_dir is None:
            return None
        # Named as the target's language names a model source (issue #289): the selection above
        # succeeded, so the resolver holds the target it selected for.
        language = resolver.target.toolchain["language"]
        facts = backend_registry.capability_module("language", language, "bundle_facts")
        model_src = stage_dir / "src" / facts.model_basename(spec_id)
        return model_src if model_src.is_file() else None
    except Exception:
        return None


def _closure_nodes_from_graph(graph: Any, self_node_key: str) -> list[str]:
    """The build closure of `self_node_key` from a `dependency_graph.json` document, in
    compile order (deepest first).

    The complete closure is the sidecar's `all_nodes` (the conductor-authored derived graph;
    see `workflow_conductor._write_dependency_graph`). `all_nodes` includes the subject
    itself, so self is excluded here; the remainder — direct + transitive deps — is ordered by
    `topo_level` ascending, deepest first, because a deeper dep provides what the shallower
    ones consume and must be built first.

    A document that is not a mapping — the ABSENT sidecar, which `_read_json_or_none` reads as
    `None` — yields `[]`; without that arm `select` would raise out of a readiness evaluator.
    Single-sourced here so the conductor's staging order and the derivation inputs
    (`pipeline_closure_nodes`, the `closure[]` of the generate and build keys) cannot disagree on
    WHICH nodes the closure holds or in what order — the caller adds its own policy (the
    conductor keeps the L6 spec_id-collision guard, which is a build-naming rule and not part of
    the closure derivation).

    The sidecar is host-written (`json.dumps(build_dependency_graph(...))`), so its entries are
    `{node_key: str, topo_level: int}` by construction and their types are not re-checked here
    (issue #643)."""
    if not isinstance(graph, dict):
        return []
    levels: dict[str, int] = {}
    closure: list[str] = []
    seen: set[str] = set()
    for n in graph["all_nodes"]:
        nk = n["node_key"].strip()
        if nk == self_node_key or nk in seen:
            continue
        seen.add(nk)
        levels[nk] = n["topo_level"]
        closure.append(nk)
    closure.sort(key=lambda nk: levels.get(nk, 0))
    return closure


def _resolve_certified_closure_binding(
    repo_root: Path, node_key: str, *, resolver: DerivationResolver | None = None,
    target: TargetProfile | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """The binding of ONE closure node: which certified artifact of `node_key` a build of a
    consumer stages and links right now, identified and hashed.

    Returns `({node_key, pipeline_ref, source_id, model_source_ref, model_source_sha256,
    output_hash}, None)` on success — plus `interface_header_ref` / `interface_header_sha256` for
    a language that declares `interface_header` — and `(None, <reason>)` otherwise. Pure and NEVER
    raises; the reason is phrased as the build precondition it is (the dependency closure
    must be certified first), because the conductor re-raises it verbatim as a transport
    fail_closed.

    The selection is the dependency's SELECTED certified Generate output under its key
    (`DerivationResolver.select`) — the same selection the consumer's build key binds by
    `closure[].source` (`phase_derivation_inputs`), so what a consumer's Build is CERTIFIED AGAINST
    and what it STAGES are one output by construction, not two lookups that must agree. The hash is taken over the same immutable file the stage copies (a
    `source_id` directory's content is written once and never rewritten), so hashing and
    copying cannot observe different bytes."""
    try:
        _parse_node_key_strict(node_key)
    except Exception:
        return (None, f"unparseable dependency node_key {node_key!r}")
    resolver = resolver or DerivationResolver(repo_root, target=target)
    sel = resolver.select(node_key, "generate")
    stage_dir = sel.stage_dir()
    if not sel.ok or stage_dir is None:
        reason = (
            f"{node_key} has no certified model source to stage ({sel.reason}); build the "
            f"dependency closure first, e.g. run_workflow.py --with-deps"
        )
        return (None, reason)
    # ONE composer of the model-source path (`_certified_model_source`): the file name is
    # the target stack's and is spelled there once, not repeated here.
    model_src = _certified_model_source(repo_root, node_key, resolver=resolver)
    if model_src is None:
        rel = _normalize_rel_posix(stage_dir.relative_to(repo_root).as_posix())
        return (None, f"cannot resolve the certified model source under {rel} (the selected "
                      f"source carries no model source file; dependency not built ready — "
                      f"run_workflow.py --with-deps first)")
    try:
        digest = hashlib.sha256(model_src.read_bytes()).hexdigest()
    except Exception as exc:  # noqa: BLE001 - unreadable staged source is a precondition failure
        return (None, f"cannot read the certified model source of {node_key}: {exc}")
    binding: dict[str, Any] = {
        "node_key": node_key,
        "pipeline_ref": sel.pipeline_ref,
        "source_id": sel.source_id,
        "model_source_ref": _normalize_rel_posix(model_src.relative_to(repo_root).as_posix()),
        "model_source_sha256": digest,
        "output_hash": sel.output_hash,
    }
    # A language whose consumers compile against a host-rendered HEADER of the dependency's
    # published surface (`interface_header`, issue #289) binds that header too: it is the file
    # beside the certified model source, rendered when the dependency was generated — the
    # declarations the dependency's own Build compiled — and it is COPIED, never re-rendered, so
    # what a consumer compiles against is what the dependency was certified with (R4-b PR-6). A
    # language without one reads a published surface off the defining source itself, and binds
    # no key, so its binding is unchanged.
    language = resolver.target.toolchain["language"]
    if backend_registry.provides("language", language, "interface_header"):
        header_module = backend_registry.capability_module(
            "language", language, "interface_header")
        header = model_src.parent / header_module.basename(_parse_node_key_strict(node_key)[1])
        try:
            header_digest = hashlib.sha256(header.read_bytes()).hexdigest()
        except Exception as exc:  # noqa: BLE001 - an absent header is a precondition failure
            return (None, (f"cannot read the certified interface header of {node_key} beside "
                           f"its model source ({exc}); the dependency's Generate writes it — "
                           f"re-certify the dependency (run_workflow.py --with-deps)"))
        binding["interface_header_ref"] = _normalize_rel_posix(
            header.relative_to(repo_root).as_posix())
        binding["interface_header_sha256"] = header_digest
    return (binding, None)


def _resolve_dependency_facts(
    repo_root: Path, ir_ref: Any, *, resolver: DerivationResolver | None = None,
    target: TargetProfile | None = None,
) -> list[dict[str, Any]]:
    """Resolve, host-side, the on-disk pipeline / run / aggregate_verdict each direct
    dependency of ``<ir_ref>/spec.ir.yaml`` was certified to. It gates no launch; what it decides is
    the ``post_judge`` aggregate fold below, where a ready dependency with no fact folds as
    ``blocked``.

    Mirrors ``_verify_dep_stage_detail``'s execution-readiness selection EXACTLY so the operations a
    leaf reads come from the artifacts readiness actually accepted: the dependency's SELECTED
    certified Validate output under its key (``DerivationResolver.select``), whose run
    directory holds the ``aggregate_verdict.json`` the fact names.

    Returns a list of ``{node_key, pipeline_ref, run_id, aggregate_verdict_ref}`` (repo-
    relative POSIX paths) for each direct dep that resolves to a bound verdict; deps that
    do not resolve (a leaf with no closure artifact yet, a missing pipeline dir / binary /
    bound verdict) are silently skipped.

    When the CONSUMER's language declares ``signatures`` (issue #289, R4-b PR-3: its backend is
    what reads a published interface out of a certified source), each fact additionally carries
    ``published_operations`` — ``[{operation, interface, argument_order}]`` extracted from
    the dependency's CERTIFIED model source (the exact source Build links, via
    the shared ``_certified_model_source``) for every op listed in that dep's
    ``operations``. This pins the dependency call-site argument order so the consumer's
    Generate need not guess it (a wrong positional order otherwise fails the build with a
    type/rank mismatch). Best-effort: a dep whose source/op cannot be resolved simply omits
    ``published_operations`` and keeps its pipeline/run/verdict fact. A consumer whose language
    declares no ``signatures`` gets no interfaces — never another language's reading of them.

    FALLBACK: when a ``component/`` or ``problem/`` dep's ``operations`` is empty / absent —
    an IR authoring wobble the ``_validate_component_dep_operations`` compile gate is meant to
    catch, but which a resume can still step over on an already-certified IR — the
    interfaces of ALL ``<dep_spec_id>__``-prefixed public subroutines in the certified
    source are surfaced instead. Scoped to the called kinds (``_SURFACED_DEPENDENCY_KINDS``):
    a ``problem`` composed by a ``problem`` is called and lists its operations as a component
    is (the system tier, Part 1), matching the ``_validate_component_dep_operations`` scope; an
    ``infrastructure`` dep is never called. Without this, the ``use``/``call`` the generate-side
    gate unconditionally requires for such a dep has no facts to build against, and the
    pure closed-context leaf must invent symbol names / argument orders that ``Generate.gate``
    rejects every retry — the closure fail_closed this fallback closes. The empty-list
    fallback fires ONLY when ``operations`` is empty/absent; when the IR enumerates real
    operations, only that enumerated surface is injected (IR is the sole carrier; never
    unioned).

    L4 (silent-drop abolition, ``component/`` and ``problem/`` deps only): when the IR enumerates operations
    but one or more of the enumerated names do not exist in the certified source (a name
    drift the compile gate is meant to catch but a resume on an already-certified IR steps
    over), the enumeration forfeits its authority WHOLESALE — it is replaced (never unioned)
    by the certified ``<dep_spec_id>__`` public surface, and the dropped declared names are
    recorded on ``fact["declared_operations_unresolved"]`` so the render can WARN the leaf its
    IR-declared symbol is unreal. This is what lets a resume over a mis-named certified IR
    converge on the first retry instead of the leaf re-authoring the invented ``call`` (which
    the syntax/static gate pincer rejects) every attempt from the same silently-dropped fact.

    The dependency ``@version`` is taken from the IR ``direct_deps[].node_key`` (a well-
    formed IR pins the resolved version, consistent with how readiness resolves it from
    ``deps.yaml`` + catalog). ``Compile.static`` refuses an IR whose ``direct_deps`` key differs
    from the graph's, ``@version`` included (issue #661), so a certified IR carries the resolved
    version. This output decides no launch either way. It has two readers: the leaf's
    ``DEPENDENCIES.md`` (Generate), and the conductor's ``post_judge`` aggregate fold
    (``Conductor._author_derived_validate_artifacts``). The fold reads each fact's
    ``aggregate_verdict_ref`` to choose a READY dependency's contribution, ``xfail`` or
    ``pass``, to the node's ``aggregate_verdict``. A dependency readiness admits but with no
    fact here folds as NOT ready (``blocked``): its certified verdict does not resolve through
    ``select``. Readiness itself is read from the readiness predicate, not from these facts.

    Best-effort: NEVER raises. A leaf node (empty ``direct_deps``), a missing/unparseable
    IR, or a malformed dep ``node_key`` yields ``[]`` / a skipped entry.

    The facts are for ONE target (issue #284) — the resolver's, else ``target``: a dependency's
    verdict and source are per-target artifacts, and the consumer's language is the target's.
    With no target every dependency is skipped (``target_unresolved``).
    """
    facts: list[dict[str, Any]] = []
    try:
        # Imported here, as `_published_surface` imports its validator helpers: the module also
        # runs as a script, where a top-level `tools.*` import aborts the CLI at load. Inside the
        # `try`, so the function keeps its never-raises contract.
        from tools.validate_pipeline_semantics import _called_dep_key
        ir_ref_token = str(ir_ref or "").strip().rstrip("/")
        if not ir_ref_token:
            return []
        ir_path = repo_root / ir_ref_token / "spec.ir.yaml"
        if not ir_path.is_file():
            return []
        try:
            ir_doc = yaml.safe_load(ir_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(ir_doc, dict):
            return []
        dep = ir_doc.get("dependency")
        direct_deps = dep.get("direct_deps") if isinstance(dep, dict) else None
        if not isinstance(direct_deps, list):
            return []
        resolver = resolver or DerivationResolver(repo_root, target=target)
        # The target's harness is a direct dependency of the node (issue #284; the IR listed
        # it until R4-a PR-3). The consumer's node_key is the IR's own, which the compile
        # gate pins to the host-derived graph (`_validate_compile_dependency_consistency`).
        self_nk = dep.get("node_key") if isinstance(dep, dict) else None
        if isinstance(self_nk, str) and self_nk.strip():
            direct_deps = with_target_harness(
                {"direct_deps": direct_deps}, repo_root, self_nk.strip(),
                resolver.target)["direct_deps"]
        # Surface dependency call-site interfaces only when the CONSUMER's language can read
        # them: its `signatures` backend is what extracts a published interface from a
        # certified source (issue #289, R4-b PR-3). A consumer language without one gets the
        # pipeline / run / verdict facts only — never another language's reading of the source.
        consumer_language = (resolver.target.toolchain["language"]
                             if resolver.target is not None else None)
        consumer_signatures = (
            backend_registry.capability_module("language", consumer_language, "signatures")
            if consumer_language
            and backend_registry.provides("language", consumer_language, "signatures")
            else None)
        for entry in direct_deps:
            node_key = entry.get("node_key") if isinstance(entry, dict) else entry
            if not isinstance(node_key, str) or not node_key.strip():
                continue
            node_key = node_key.strip()
            # A called dependency (`component` / `problem`) is read by its CANONICAL key, the
            # spelling the dependency gates read (`_called_dep_key`, which agrees with V4's
            # `_versioned_node_key_token`): a key respelled with whitespace around its kind, id or
            # version is the member to those gates, so its call is required at Generate, and the
            # leaf is shown its interface here under the same reading (review round 2, sec-L1).
            called = _called_dep_key(node_key)
            if called is not None:
                node_key = called[2]
            try:
                _node_key_to_safe(node_key)
            except Exception:
                # Malformed dep node_key: skip rather than raise (orientation-only).
                continue
            sel = resolver.select(node_key, "validate")
            run_dir = sel.stage_dir()
            if not sel.ok or run_dir is None or not sel.pipeline_ref:
                continue
            verdict_path = run_dir / "aggregate_verdict.json"
            if not verdict_path.is_file():
                continue
            fact: dict[str, Any] = {
                "node_key": node_key,
                "pipeline_ref": sel.pipeline_ref,
                "run_id": str(sel.run_id or ""),
                "aggregate_verdict_ref": _normalize_rel_posix(
                    verdict_path.relative_to(repo_root).as_posix()),
            }
            # Pin each called op's certified Fortran interface (argument order) so the
            # consumer's Generate need not guess it. Extracted from the SAME certified
            # source Build stages (`_certified_model_source`). Best-effort, never raises.
            # R1/M3c-β: an `infrastructure` (harness) dependency publishes NO call-site
            # interface to the leaf — the physics leaf never calls the harness API (the
            # host-rendered runner is the sole caller), so surfacing the harness surface
            # would only tempt a `use harness_*` the checks/model contract forbids. Skip it
            # (its entry — the target's harness, `with_target_harness` — carries `operations: []`).
            dep_kind = node_key.split("/", 1)[0].strip()
            is_infra_dep = dep_kind == "infrastructure"
            # A `problem` composed by a `problem` is called as a component is (the system tier,
            # Part 1), so it takes this branch too.
            is_component_dep = dep_kind in _SURFACED_DEPENDENCY_KINDS
            ops = entry.get("operations") if isinstance(entry, dict) else None
            if consumer_signatures is not None and not is_infra_dep:
                try:
                    dep_spec_id = _parse_node_key_strict(node_key)[1]
                    model_src = _certified_model_source(repo_root, node_key, resolver=resolver)
                except Exception:
                    dep_spec_id = None
                    model_src = None
                if model_src is not None:
                    try:
                        source_text = model_src.read_text(encoding="utf-8")
                    except Exception:
                        source_text = None
                    if source_text is not None:
                        enumerated = (
                            [op.strip() for op in ops if isinstance(op, str) and op.strip()]
                            if isinstance(ops, list) else []
                        )
                        op_names = enumerated
                        declared_unresolved: list[str] = []
                        if is_component_dep and dep_spec_id:
                            if not enumerated:
                                # FALLBACK (empty-list-only, called deps only): the IR authored
                                # no operations for this `component` / `problem` dep. Surface every
                                # `<dep_spec_id>__` subroutine from the certified source so the
                                # leaf's mandatory call of a `<dep>__*` operation has a real
                                # interface to build against. Scoped to the called kinds because the
                                # empty-ops fail_closed loop this closes is driven by the generate
                                # gate over those kinds (the language's
                                # `validate_dependency_operations`); an `infrastructure` dep is
                                # never called. See `_validate_component_dep_operations`.
                                op_names = consumer_signatures.prefixed_procedures(
                                    source_text, f"{dep_spec_id}__"
                                )
                            else:
                                # L4 (called deps only): silent-drop abolition. When the IR
                                # ENUMERATES operations but one or more do not exist in the certified
                                # source (a name-drift authoring wobble the compile gate is meant to
                                # catch, but which a resume on an already-certified IR steps over),
                                # the enumeration forfeits its authority WHOLESALE: replace it with
                                # the certified `<dep_spec_id>__` public surface (never union — an
                                # invented name must not survive) and record the dropped declared
                                # names so the render WARNs the leaf its IR symbol is unreal. Without
                                # this the resolver silently drops the bad name and the pure generate
                                # leaf — re-injected the same stale fact by the repair prompt — calls
                                # the nonexistent symbol every retry (the syntax-gate/static-gate
                                # pincer the closure fail_closed on). Only fires on a NON-empty
                                # enumeration; empty-ops took the fallback above. IR remains the sole
                                # carrier — the enumerated surface is used verbatim UNLESS it is
                                # unreal, in which case the certified source (not a union) replaces it.
                                declared_unresolved = [
                                    op for op in enumerated
                                    if consumer_signatures.published_interface(
                                        source_text, op) is None
                                ]
                                if declared_unresolved:
                                    op_names = consumer_signatures.prefixed_procedures(
                                        source_text, f"{dep_spec_id}__"
                                    )
                        published: list[dict[str, Any]] = []
                        for op in op_names:
                            iface = consumer_signatures.published_interface(source_text, op)
                            if iface is None:
                                continue
                            # The interface a consumer is SHOWN is the one extracted from the
                            # certified SOURCE, because that is what Build stages and links (issue
                            # #153 PR-1 made the consumer's binding to those bytes durable); the
                            # dependency's own §5.1 == IR == SOURCE is pinned by its Compile and
                            # Generate gates (PR-2). The canary that re-reported a disagreement
                            # between the IR pin and the source to the consumer went in issue #643.
                            published.append({"operation": op, **iface})
                        if published:
                            fact["published_operations"] = published
                        if declared_unresolved:
                            fact["declared_operations_unresolved"] = declared_unresolved
                        if published or declared_unresolved:
                            # The language whose reader produced these facts, and so the one
                            # whose words the renderer shows them in (issue #289, R4-b PR-4):
                            # the renderer runs from the request alone, and a `validate`
                            # request names no language of its own.
                            fact["interface_language"] = consumer_language
            facts.append(fact)
    except Exception:
        return facts
    return facts


# The dependency kinds whose published surface a consumer is shown and bound by: a `component`,
# and a `problem` composed by a `problem` (the system tier, Part 1). An `infrastructure` node is
# the target's harness, which the host-rendered runner alone calls.
_SURFACED_DEPENDENCY_KINDS: frozenset[str] = frozenset({"component", "problem"})


def _component_direct_deps(graph: Mapping[str, Any], node_key: str) -> list[str]:
    """The direct dependencies of `node_key` in the derived graph whose kind publishes a surface a
    consumer calls (`_SURFACED_DEPENDENCY_KINDS`: `component` and `problem`), in `all_nodes`
    order: `all_nodes − {self} − transitive_deps`. A graph without a list of nodes has none.
    The name predates `problem` dependencies; its two readers are the compile key's
    `dependency_surface` and `_resolve_component_dep_surface`."""
    all_nodes = graph.get("all_nodes")
    if not isinstance(all_nodes, list):
        return []
    self_nk = str(graph.get("node_key") or node_key or "").strip()
    transitive = graph.get("transitive_deps")
    transitive_nks: set[str] = set()
    if isinstance(transitive, list):
        for t in transitive:
            tk = t.get("node_key") if isinstance(t, dict) else None
            if isinstance(tk, str) and tk.strip():
                transitive_nks.add(tk.strip())
    direct: list[str] = []
    seen: set[str] = set()
    for n in all_nodes:
        nk = n.get("node_key") if isinstance(n, dict) else None
        if not isinstance(nk, str) or not nk.strip():
            continue
        nk = nk.strip()
        if nk == self_nk or nk in transitive_nks or nk in seen:
            continue
        seen.add(nk)
        if nk.split("/", 1)[0].strip() in _SURFACED_DEPENDENCY_KINDS:
            direct.append(nk)
    return direct


def _resolve_component_dep_surface(
    repo_root: Path, node_key: str, graph: Any,
    *, resolver: DerivationResolver | None = None,
) -> list[dict[str, Any]]:
    """Resolve, host-side at COMPILE phase start, the published surface (operation names and
    ``public_api`` ABI) of each direct dependency of ``node_key`` that a consumer calls — a
    ``component`` or a ``problem`` (``_component_direct_deps``) — for the consumer, whose own IR
    does not yet exist, for the conductor to author into ``<ir_ref>/dependency_surface.json``
    and inject into ``compile.generate``.

    The consumer's IR is absent at compile start, so the direct-dep set is read from the
    conductor-authored ``dependency_graph.json`` ``graph`` — ``all_nodes − {self} −
    transitive_deps`` — a pure function of ``deps.yaml`` + ``spec_catalog.yaml``, NOT from a
    not-yet-existing consumer IR (this is the L2 correction: ``_resolve_dependency_facts`` is
    keyed on the consumer IR's ``direct_deps`` and cannot run before compile authors it).

    ``component/`` and ``problem/`` deps are surfaced: a ``problem`` composed by a ``problem``
    is called and lists its operations as a component is (the system tier, Part 1). An
    ``infrastructure`` dep is never called (the runner harness is host-rendered). Each entry is
    ``{node_key,
    published_operations: [op_name, ...], source}`` where ``source`` is:
      - ``ir_public_api``   — names read from the dep's CERTIFIED IR
        ``public_api.published_operations[].operation_id`` (the L1 name pin), and the entry
        also carries ``public_api``: that IR's ``module_parameters`` / ``interfaces`` /
        ``signatures``, verbatim, only the keys the IR has (an IR whose §5.1 declares no
        prototype has no ``interfaces``; one with no ``signatures`` yields names only);
      - ``unresolved``      — no certified IR, or one with no ``public_api``
        (``published_operations == []``, no ``public_api`` key). The ``certified_source``
        fallback that read a dependency's model source went with issue #284: a source is a
        per-target artifact, and this surface is target-free.

    This is the document the leaves READ. The keys bind `_published_surface` instead.

    The ``public_api`` copy is how a consumer's Compile reads the contract its controlled_spec
    delegates to a dependency's §5.1 — a local procedure passed to a procedure-typed argument
    takes its arguments from the named prototype in ``interfaces`` (issue #486; before it the
    sidecar carried names only, and a leaf told to "match the prototype" was never shown it).
    The truth source stays the dependency's IR, which its own ``Compile.static`` pins == its §5.1;
    this is a snapshot of what the consumer's Compile was shown. Generate is unchanged: what a
    consumer is shown THERE is still the certified SOURCE interface, because that is what Build
    stages and links. Best-effort: NEVER raises; a malformed / missing graph yields ``[]``.
    """
    try:
        if not isinstance(graph, dict):
            return []
        resolver = resolver or DerivationResolver(repo_root)
        out: list[dict[str, Any]] = []
        for nk in _component_direct_deps(graph, node_key):
            entry: dict[str, Any] = {
                "node_key": nk, "published_operations": [], "source": "unresolved",
            }
            try:
                kind, spec_id, version = _parse_node_key_strict(nk)
            except Exception:
                out.append(entry)
                continue
            # The dep's certified IR public_api (the L1 name pin). A public_api that is PRESENT
            # (even with an empty operation list) is authoritative. A dep IR with NO
            # public_api is `unresolved`: the certified-SOURCE fallback that used to stand here
            # read a Generate output, which is a per-target artifact (issue #284), and this
            # surface must answer the same for every target.
            # Measured when it went (R4-a PR-2): every keyed certified component IR under
            # `workspace/ir/` carries `public_api.published_operations`, and the compile gate
            # refuses a component IR without `public_api`, so no key it fed changes.
            pa = _ir_public_api(repo_root, kind, spec_id, version, resolver=resolver)
            if pa is not None:
                entry["published_operations"] = _published_operation_names(pa)
                entry["source"] = "ir_public_api"
                entry["public_api"] = {
                    k: pa[k] for k in _SURFACE_PUBLIC_API_KEYS if k in pa}
            out.append(entry)
        return out
    except Exception:
        return []


# The dependency `public_api` keys copied into a consumer's dependency surface, in render order
# (issue #486): `published_types` repeats the type entries of `signatures`, and `exercised_by`
# names test ids the consumer has no use for.
_SURFACE_PUBLIC_API_KEYS = ("module_parameters", "interfaces", "signatures")


def _ir_public_api(
    repo_root: Path, kind: str, spec_id: str, version: str,
    *, resolver: DerivationResolver | None = None,
) -> dict[str, Any] | None:
    """The ``public_api`` mapping of the CURRENT certified IR for ``(kind, spec_id, version)``,
    or ``None`` when the IR is absent or has NO ``public_api`` block carrying a
    ``published_operations`` key (a legacy IR — the caller reports the dependency
    `unresolved`). A PRESENT ``published_operations`` that is empty/malformed is still a
    ``public_api`` (authoritative empty names), NOT ``None``. NEVER raises."""
    try:
        ir_dir = _certified_ir_dir(repo_root, kind, spec_id, version, resolver=resolver)
        if ir_dir is None:
            return None
        doc = yaml.safe_load(
            (ir_dir / "spec.ir.yaml").read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return None
        pa = doc.get("public_api")
        if not isinstance(pa, dict) or "published_operations" not in pa:
            return None
        return pa
    except Exception:
        return None


def _published_operation_names(pa: dict[str, Any]) -> list[str]:
    """The stripped ``published_operations[].operation_id`` strings of a ``public_api``
    mapping; a malformed list or entry contributes nothing."""
    pub = pa.get("published_operations")
    names: list[str] = []
    if isinstance(pub, list):
        for it in pub:
            oid = it.get("operation_id") if isinstance(it, dict) else None
            if isinstance(oid, str) and oid.strip():
                names.append(oid.strip())
    return names


def _catalog_family_index(repo_root: Path) -> list[dict[str, str]]:
    """Return ``[{spec_kind, spec_id, spec_version, family}, ...]`` from the spec catalog,
    or ``[]`` on any read/parse error (best-effort, never raises). Used by the R5 exemplar
    selector to group nodes by ``(spec_kind, family)`` — a dimension ``_load_spec_catalog``
    (versions only) drops."""
    try:
        entries = _spec_catalog_entries(repo_root)
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for entry in entries:
        kind = str(entry.get("spec_kind") or "").strip()
        spec_id = str(entry.get("spec_id") or "").strip()
        version = str(entry.get("spec_version") or "").strip()
        family = str(entry.get("family") or "").strip()
        if kind and spec_id and family:
            out.append({"spec_kind": kind, "spec_id": spec_id,
                        "spec_version": version, "family": family})
    return out


def _exemplar_contract_version_matches(model_src: Path) -> bool:
    """True iff the certified pure source at ``model_src`` was authored under the CURRENT
    ``LEAF_PROMPT_CONTRACT_VERSION``, read from its ``bundle_meta.json#prompt_contract_version``.

    ``model_src`` is ``.../source/<source_id>/src/<id>_model.f90``, so the pure ``bundle_meta.json``
    is two parents up. This gates the R5 M3c model exemplar: a sibling's model is authored against
    the operations the host-rendered driver of its contract calls, and when that surface has since
    changed (issue #589 moving the time loop to the driver), an older-vintage sibling carries the
    RETIRED shape. Injecting it as concrete prior art can steer the doc-blind leaf into a bundle
    the acceptance gate — which checks only names, not arity — admits, and which then
    fails ``Generate.gate`` against the current host-rendered runner (an unrepairable-until-
    reroll waste). The bundle gate cannot see the arity, so gate the exemplar HERE on the vintage.

    Fail-SAFE polarity: any missing / unreadable / mismatched version returns ``False`` (the
    exemplar is dropped, and Generate proceeds without one), never keeps a possibly-stale exemplar.
    An exemplar is advisory (never a gate), so dropping a stale-vintage one only forgoes a hint —
    the correct surface still reaches the leaf verbatim through the inlined host-rendered runner."""
    try:
        doc = json.loads((model_src.parent.parent / "bundle_meta.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    return (isinstance(doc, dict)
            and str(doc.get("prompt_contract_version", "")).strip() == LEAF_PROMPT_CONTRACT_VERSION)


def _resolve_exemplar_source(
    repo_root: Path, ir_ref: Any, *, target: TargetProfile | None = None,
) -> dict[str, Any] | None:
    """R5: resolve a previously-certified SIBLING node's source as a ``generate.generate``
    exemplar — a cacheable, known-good implementation from the SAME ``(family, spec_kind)``
    as the subject and built for the SAME ``target`` (issue #284), EXCLUDING the subject
    node itself. The target is what closes the language: a sibling is read from the target's
    own pipeline tree (``DerivationResolver(target=…)``), so a sibling certified for another
    target — another language included — is never offered. No target, no exemplar.

    The exemplar trades cheap input tokens for the expensive per-node re-derivation of
    cross-node-identical plumbing, raising first-attempt pass rate; the corpus is
    self-bootstrapping (every certified node becomes an exemplar for its siblings). Pre-R1 the
    injected sources are the certified ``<spec_id>_model.f90`` + ``<spec_id>_runner.f90``; once
    R1 lands the harness node, the runner leaves the physics node; since issue #589 the exemplar
    is the model alone.

    Discovery mirrors the certified-source selection used for dependencies (``_verify_dep_stage_detail``
    / ``_certified_model_source``): for each sibling catalog spec, its SELECTED certified
    Validate output under its key (a genuinely certified node, not merely built —
    ``DerivationResolver.select``) and the certified Generate output that verdict stands on.
    Across siblings the most-recent certified pipeline (by canonical ``(date, seq)``) wins.
    ORIENTATION/PRIOR-ART ONLY — never a gate, and never the node's OWN prior source (self is
    excluded, so it cannot leak a past attempt of itself).

    Returns ``{node_key, spec_id, sources: [{filename, text}, ...], source_ref}`` (the last
    is the repo-relative ``source/<source_id>`` directory the sources were read from) or
    ``None``. Best-effort: NEVER raises (a missing catalog / IR / source yields ``None``)."""
    try:
        ir_ref_token = str(ir_ref or "").strip().rstrip("/")
        if not ir_ref_token or target is None:
            return None
        ir_path = repo_root / ir_ref_token / "spec.ir.yaml"
        if not ir_path.is_file():
            return None
        try:
            ir_doc = yaml.safe_load(ir_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(ir_doc, dict):
            return None
        meta = ir_doc.get("meta") if isinstance(ir_doc.get("meta"), dict) else {}
        self_kind = str(meta.get("spec_kind") or "").strip()
        self_id = str(meta.get("spec_id") or "").strip()
        if not self_kind or not self_id:
            return None
        # R1/M3c-β: an M3c target (a physics node whose runner the host renders over its
        # target's harness) authors model + checks, so its exemplar is a sibling's model +
        # checks — NOT the pre-M3c model + runner (injecting a 600-line self-authored runner
        # would be misleading prior art). Both files must be present (a pre-M3c sibling without
        # a sibling without its model is skipped, not partially injected). Mirror the conductor's
        # `_conductor_authors_runner` predicate (a control file and a runner the host authors ∧
        # non-infra) so the exemplar shape matches what the leaf actually authors. Both halves
        # are the TARGET's, the question the conductor asks of the same profile
        # (`_core_authors_control_file` + `runner_render`); the harness is the target's too
        # (issue #284), so no dependency count enters it.
        tc = target.toolchain
        host_renders = all(
            backend_registry.provides(axis, value, capability)
            for axis, value, capability in (
                ("build_system", tc["build_system"], "control_file"),
                ("language", tc["language"], "control_file"),
                ("language", tc["language"], "runner_render")))
        target_is_m3c = self_kind != "infrastructure" and host_renders
        facts = backend_registry.capability_module("language", tc["language"], "bundle_facts")

        catalog = _catalog_family_index(repo_root)
        self_family = next(
            (e["family"] for e in catalog
             if e["spec_kind"] == self_kind and e["spec_id"] == self_id), None)
        if not self_family:
            return None
        # Sibling (kind, family) specs, self excluded — deduped to (spec_id, version) pairs.
        siblings: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for e in catalog:
            if (e["spec_kind"] == self_kind and e["family"] == self_family
                    and e["spec_id"] != self_id):
                key = (e["spec_id"], e["spec_version"])
                if key not in seen:
                    seen.add(key)
                    siblings.append(key)

        best: tuple[tuple[str, int], dict[str, Any]] | None = None
        resolver = DerivationResolver(repo_root, target=target)
        for cand_id, cand_version in siblings:
            if not (_is_identifier_token(self_kind) and _is_identifier_token(cand_id)
                    and (not cand_version or _is_identifier_token(cand_version))):
                continue
            # Scan every catalog version's pipeline dir (a spec may have >1); pick the
            # certified pipeline with the freshest canonical id across them.
            safe_glob = (f"{self_kind}__{cand_id}__{cand_version}" if cand_version
                         else f"{self_kind}__{cand_id}__*")
            for safe_root in (repo_root / "workspace" / "pipelines").glob(safe_glob):
                if not safe_root.is_dir():
                    continue
                cand_nk = f"{self_kind}/{cand_id}@{safe_root.name.rsplit('__', 1)[-1]}"
                if not resolver.select(cand_nk, "validate").ok:
                    continue
                model_src = _certified_model_source(repo_root, cand_nk, resolver=resolver)
                if model_src is None:
                    continue
                # `<pipe>/source/<source_id>/src/<id>_model.f90` -> the pipeline directory.
                pipe_dir = model_src.parent.parent.parent.parent
                # R5 surface-drift guard (M3c only): the model exemplar must be authored under the
                # CURRENT contract, else it may carry a retired operation shape the acceptance
                # gate (names only) admits but Generate.gate then rejects. The non-M3c
                # model+runner exemplar calls its own operations, so it is not gated here.
                if target_is_m3c and not _exemplar_contract_version_matches(model_src):
                    continue
                exemplar_files = (
                    (facts.model_basename(cand_id),)
                    if target_is_m3c
                    else (facts.model_basename(cand_id), facts.runner_basename(cand_id)))
                sources: list[dict[str, str]] = []
                missing = False
                for fname in exemplar_files:
                    fpath = model_src.parent / fname
                    if fpath.is_file():
                        try:
                            sources.append({"filename": fname,
                                            "text": fpath.read_text(encoding="utf-8")})
                        except Exception:
                            missing = True
                    else:
                        missing = True
                # M3c: require the model — a sibling lacking it has nothing to show, and its
                # legacy runner must not be injected, so skip it entirely.
                if target_is_m3c and missing:
                    continue
                if not sources:
                    continue
                key = _freshness_key_from_id(pipe_dir.name)
                if key is None:
                    continue
                cand = {"node_key": f"{self_kind}/{cand_id}"
                        + (f"@{cand_version}" if cand_version else ""),
                        "spec_id": cand_id, "sources": sources,
                        # WHICH certified source directory the exemplar was read from, so the
                        # attempt record can name it (`bundle_meta.json#per_attempt[].exemplar_ref`,
                        # issue #250): an advisory input is recorded per attempt, never keyed.
                        "source_ref": _normalize_rel_posix(
                            model_src.parent.parent.relative_to(repo_root).as_posix())}
                if best is None or key > best[0]:
                    best = (key, cand)
        return best[1] if best is not None else None
    except Exception:
        return None


def _certify_and_collect_dep_artifacts(
    repo_root: Path, spec_ref: Any, *, target: TargetProfile | None = None,
) -> dict[str, Any]:
    """Single-pass: judge every candidate dep version through `_verify_dep_stage_detail`
    and select the certified version per dep (one primitive since issue #178; one
    `DerivationResolver` for the whole direct-dependency set since issue #250 PR-2, so a
    closure member two dependencies share is selected once).

    Returns a dict with:
      - `deps_doc_valid` (bool): True iff deps.yaml parsed as a dict.
      - `entries_well_formed` (bool): True iff the deps.yaml schema is strict.
      - `has_entries` (bool): True iff the direct set is non-empty: deps.yaml's entries plus,
      under a `target`, that target's harness (`target_harness_entries`).
      - `certified_entries`: list of `(kind, spec_id, certified_version, level)`
        in deps.yaml order. `certified_version` is the HIGHEST matching
        catalog version that achieved the MAX level (any of {0,1,2,3}).
        When no version was matched / no artifacts existed, level is 0 and
        `certified_version` is None.

    The `dep_set_fingerprint` this pass used to feed — the bytes of each selected stage
    file, hashed as a cheap stale-detector beside the booleans — went with issue #250 PR-2:
    every stage is now itself a content-hash comparison (the dependency's derivation key
    over its inputs and the `artifact_hashes` of its output), recomputed live at every
    gate, so there is nothing a byte fingerprint of the selected files would detect
    earlier.
    """
    snap: dict[str, Any] = {
        "deps_doc_valid": False,
        "entries_well_formed": False,
        "has_entries": False,
        "certified_entries": [],
    }
    deps_doc = _read_deps_yaml(repo_root, spec_ref)
    if not isinstance(deps_doc, dict):
        return snap
    snap["deps_doc_valid"] = True
    entries, well_formed = _parse_dep_entries(deps_doc)
    snap["entries_well_formed"] = well_formed
    if not well_formed:
        return snap
    # The target's harness is a direct dependency of every node that is not itself
    # `infrastructure` (issue #284; `deps.yaml` declared it until R4-a PR-3).
    entries = list(entries) + _target_harness_entries_for_spec_ref(repo_root, spec_ref, target)
    if not entries:
        snap["has_entries"] = False
        return snap
    snap["has_entries"] = True
    catalog = _load_spec_catalog(str(repo_root.resolve()))
    # Issue #175: expand `profile` entries into the components they select. A failure is the
    # same class as a malformed schema — the declared dependency set does not resolve — so it
    # takes the same fail-closed snapshot rather than a vacuous one.
    entries, _profiles_record, expand_error = expand_profile_dependencies(
        repo_root, spec_ref, entries, catalog
    )
    if expand_error is not None:
        snap["entries_well_formed"] = False
        return snap
    # The `pipeline_ref` / `aggregate_verdict` levels are asked for `target` — the
    # orchestration's (issue #284); without one they read `target_unresolved`, level 1 at most.
    resolver = DerivationResolver(repo_root, target=target)
    for kind, spec_id, constraint in entries:
        matched = _matching_dep_versions(catalog, kind, spec_id, constraint)
        if not matched:
            snap["certified_entries"].append((kind, spec_id, None, 0))
            continue
        best_v: str | None = None
        best_level = -1
        # matched is descending; iterate so ties prefer the higher version.
        for v in matched:
            # Cumulative readiness level of this version (0 = no ir / ir fail, 1 = ir pass,
            # 2 = ir + pipeline pass, 3 = ir + pipeline + verdict pass). The level is the INDEX
            # of the first stage the primitive refuses, which relies on
            # `_DEPENDENCY_READINESS_STAGES` being ordered (ir_ref, pipeline_ref,
            # aggregate_verdict) and on each stage's selection requiring the one before it.
            level = len(_DEPENDENCY_READINESS_STAGES)
            for idx, stage in enumerate(_DEPENDENCY_READINESS_STAGES):
                ok, _detail = _verify_dep_stage_detail(
                    repo_root, kind, spec_id, v, stage, resolver=resolver)
                if not ok:
                    level = idx
                    break
            if level > best_level:
                best_level = level
                best_v = v
        if best_v is None:
            snap["certified_entries"].append((kind, spec_id, None, 0))
            continue
        snap["certified_entries"].append((kind, spec_id, best_v, best_level))
    return snap


def _compute_dep_readiness(
    repo_root: Path, spec_ref: Any, *, target: TargetProfile | None = None,
) -> tuple[dict[str, bool] | None, list[dict[str, str]], str | None]:
    """Single-pass: derive the readiness booleans, the certified
    `(spec_kind, spec_id, spec_version)` per dep, and any verification-error reason from
    ONE evaluation of the dependency set.

    Per-dep certified version is the HIGHEST matching catalog version that
    achieved the max readiness level (ir → ir+pipeline → ir+pipeline+verdict).

    Returns `(verified_dict, certified_deps, fail_reason)`:
      - `verified_dict`: None when verification cannot run. Else a dict of
        stage booleans.
      - `certified_deps`: list of {"spec_kind", "spec_id", "spec_version"} in
        deps.yaml order.
      - `fail_reason`: None on success; otherwise one of
        `"deps_yaml_missing_or_unparseable"` (deps.yaml file absent or YAML
        parse failed), `"deps_yaml_malformed_schema"` (deps.yaml parsed
        but the dependency-block schema is invalid — unknown keys, missing
        canonical lists, list items in wrong shape, etc.) or
        `"spec_catalog_corrupt"`. Callers that write persisted state
        (`mark_dependency_readiness`) treat all of them as hard verification failures and
        fail closed with the specific reason so the audit trail records WHICH defect
        occurred.
    """
    # Codex round 33 F2: catalog corruption is a distinct hard failure,
    # not a normal "no matching dep" verification result. Convert the
    # exception into a specific fail_reason so observability tooling, the
    # CLI exit, and persisted state all record WHICH defect occurred.
    try:
        snap = _certify_and_collect_dep_artifacts(repo_root, spec_ref, target=target)
    except SpecCatalogCorruption:
        return (None, [], "spec_catalog_corrupt")
    if not snap["deps_doc_valid"]:
        return None, [], "deps_yaml_missing_or_unparseable"
    if not snap["entries_well_formed"]:
        # Codex round 21 F2: malformed deps.yaml schema is a HARD verification
        # failure, not a normal negative result. Distinct reason so observability
        # tooling (and the CLI exit) can differentiate spec defects from
        # ordinary "no artifacts" cases.
        return None, [], "deps_yaml_malformed_schema"
    if not snap["has_entries"]:
        return (
            {f"{s}_verified": True for s in _DEPENDENCY_READINESS_STAGES},
            [],
            None,
        )
    results: dict[str, bool] = {f"{s}_verified": True for s in _DEPENDENCY_READINESS_STAGES}
    certified_deps: list[dict[str, str]] = []
    for kind, spec_id, cert_v, level in snap["certified_entries"]:
        if cert_v is None:
            for s in _DEPENDENCY_READINESS_STAGES:
                results[f"{s}_verified"] = False
            certified_deps.append({"spec_kind": kind, "spec_id": spec_id, "spec_version": ""})
            continue
        if level < 1:
            results["ir_ref_verified"] = False
        if level < 2:
            results["pipeline_ref_verified"] = False
        if level < 3:
            results["aggregate_verdict_verified"] = False
        certified_deps.append({"spec_kind": kind, "spec_id": spec_id, "spec_version": cert_v})
    return results, certified_deps, None


def _compute_initial_dependency_readiness(
    repo_root: Path, spec_ref: Any, *, target: TargetProfile | None = None,
) -> dict[str, Any]:
    """Compute the canonical `dependency_readiness` payload for a fresh orchestration.

    Replaces the previous all-true default which fail-opens `workflow-launch-check`
    when no real readiness builder is wired up. Semantics:

    - If `spec_ref` is missing or the target `deps.yaml` cannot be parsed, return
      a fail-closed payload (all flags false) so the gate refuses to launch until
      a real verifier writes verified state.
    - If `deps.yaml` declares no dependency entry at all, dependency readiness
      is vacuously satisfied — return all flags true (matches the audit's empty-
      dependency case where launches should proceed). This reader looks at the RAW
      declaration, before `expand_profile_dependencies`: a node declaring only a
      `profile` is not a trivial leaf, and expanding here would be the one place that
      turned a declared dependency into none.
    - Otherwise, return fail-closed. A future readiness builder must explicitly
      flip these flags after verifying each direct dependency's `ir_meta.json` /
      `binary_meta.json` / `aggregate_verdict`. The fail-closed default ensures
      that gate behaviour does not silently trust unverified state.

    Under a `target` (the orchestration's, issue #284) a node that is not itself
    `infrastructure` has the target's harness as a direct dependency even with an empty
    `deps.yaml` (`target_harness_entries`), so it is not a trivial leaf. Asking that reads the
    catalog, whose failure propagates (out of `write_preflight` too, which ends the run's
    preflight; issue #643 removed its degraded mode).
    """
    if _target_harness_entries_for_spec_ref(repo_root, spec_ref, target):
        return {
            "direct_dependency_compile_readiness": False,
            "direct_dependency_execution_readiness": False,
            "detail": {
                "ir_ref_verified": False,
                "pipeline_ref_verified": False,
                "aggregate_verdict_verified": False,
            },
        }
    trivial: dict[str, Any] = {
        "direct_dependency_compile_readiness": True,
        "direct_dependency_execution_readiness": True,
        "detail": {
            "ir_ref_verified": True,
            "pipeline_ref_verified": True,
            "aggregate_verdict_verified": True,
        },
        # `certified_deps: []` is the record that this is a leaf (no dependency to
        # certify); `write_preflight` refreshes such a record from the current deps.yaml.
        "certified_deps": [],
    }
    fail_closed: dict[str, Any] = {
        "direct_dependency_compile_readiness": False,
        "direct_dependency_execution_readiness": False,
        "detail": {
            "ir_ref_verified": False,
            "pipeline_ref_verified": False,
            "aggregate_verdict_verified": False,
        },
        # Fail-closed payloads explicitly omit `certified_deps`: there is
        # no proof of any verification state to record.
    }
    deps_doc = _read_deps_yaml(repo_root, spec_ref)
    if not isinstance(deps_doc, dict):
        return fail_closed
    # Codex round 12 F1: route trivial-leaf detection through the strict
    # schema validator instead of a loose `len() == 0` check. Unknown
    # dependency keys (e.g. typoed `component:`) or missing canonical keys
    # now fail-closed rather than silently producing vacuous-true readiness.
    entries, well_formed = _parse_dep_entries(deps_doc)
    if not well_formed:
        return fail_closed
    if not entries:
        return trivial
    return fail_closed
SUPPORTED_BACKENDS = {"codex", "claude"}
PREFLIGHT_TTL_DEFAULT_SECONDS: int = 1800

# Strict per-component validators for node_key in the canonical
# `<spec_kind>/<spec_id>@<spec_version>` form. Used to reject malicious or
# malformed values before they flow into capability write_roots / path prefixes
# (a node_key like `../etc/passwd@1.0.0` would otherwise produce a write_root
# of `releases/../etc/passwd/`, escaping the intended release subtree).
_NODE_KEY_KIND_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_NODE_KEY_ID_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_NODE_KEY_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z._-]*$")


def _parse_node_key_strict(node_key: Any) -> tuple[str, str, str]:
    """Validate `node_key` and return `(spec_kind, spec_id_dotted, spec_version)`.

    Enforces the canonical form `<spec_kind>/<spec_id>@<spec_version>` so that
    components downstream (write_roots, release path prefixes, node_key_safe
    derivation) cannot be tricked into path traversal via segments like `..`,
    embedded slashes, null bytes, or whitespace.

    Raises `ValueError` on any deviation from the canonical form.
    """
    if not isinstance(node_key, str):
        raise ValueError("node_key must be a string")
    token = node_key.strip()
    if not token:
        raise ValueError("node_key must be non-empty")
    if "\x00" in token:
        raise ValueError(f"node_key contains null byte: {node_key!r}")
    if "/" not in token or "@" not in token:
        raise ValueError(
            f"node_key must match '<spec_kind>/<spec_id>@<spec_version>': {node_key!r}"
        )
    spec_kind, tail = token.split("/", 1)
    spec_id_dotted, spec_version = tail.rsplit("@", 1)
    spec_kind = spec_kind.strip()
    spec_id_dotted = spec_id_dotted.strip()
    spec_version = spec_version.strip()
    if not spec_kind or not spec_id_dotted or not spec_version:
        raise ValueError(
            f"node_key must match '<spec_kind>/<spec_id>@<spec_version>': {node_key!r}"
        )
    if not _NODE_KEY_KIND_RE.match(spec_kind):
        raise ValueError(f"node_key spec_kind is invalid: {node_key!r}")
    if "/" in spec_id_dotted or "\\" in spec_id_dotted:
        raise ValueError(f"node_key spec_id must not contain path separators: {node_key!r}")
    id_segments = spec_id_dotted.split(".")
    if any(not seg for seg in id_segments):
        # rejects '..', leading '.', trailing '.'
        raise ValueError(f"node_key spec_id has empty dot-segment: {node_key!r}")
    for seg in id_segments:
        if not _NODE_KEY_ID_SEGMENT_RE.match(seg):
            raise ValueError(f"node_key spec_id segment is invalid: {node_key!r}")
    if not _NODE_KEY_VERSION_RE.match(spec_version):
        raise ValueError(f"node_key spec_version is invalid: {node_key!r}")
    return spec_kind, spec_id_dotted, spec_version

# Safe agent_run_id characters: alphanumerics, hyphens, underscores.
# Rejects path separators (/, \), dots (..), null bytes, and other traversal vectors.
_AGENT_RUN_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
DEFAULT_BACKEND_COMMANDS = {
    "codex": "codex",
    "claude": "claude",
}

# Every token a launch may be RECORDED under. `SUPPORTED_BACKENDS` stays exactly what it was —
# the CLI backends a leaf is spawned as a child process of — because that is what the sandbox,
# hook and session machinery keys on. The HTTP providers (issue #28) are answered from the
# conductor's own process and never spawn anything, so they are not backends in that sense; but
# they DO appear in `record-launch` responses, `agent_runs` rows and `--backend` arguments, and
# every validator that used to accept only `SUPPORTED_BACKENDS` has to admit them or the first
# HTTP leaf fails closed on its own provenance.
_HTTP_PROVIDER_TOKENS = frozenset({"openai_compatible", "anthropic_api"})
SUPPORTED_PROVIDER_TOKENS = frozenset(SUPPORTED_BACKENDS) | _HTTP_PROVIDER_TOKENS

# Escape hatch for an air-gapped or offline preflight: the unauthenticated reachability GET is
# skipped, the key-presence and URL-shape checks are NOT (those cost nothing and catch the two
# mistakes an operator actually makes).
_HTTP_PREFLIGHT_SKIP_REACHABILITY_ENV = "SPEC2SCIENCE_HTTP_PREFLIGHT_SKIP_REACHABILITY"

# The documents the HOST inlines into a pure launch prompt (see docs/workflow/). No leaf
# reads one off disk since Z4 (issue #171): `skill_must_read_refs` is declared empty on every
# launch and `docs/AGENT_CONTRACT.md`, which carried the leaf-actionable invariants and the
# stage-meta keys, is deleted — `docs/workflow/WORKFLOW_CORE.md` §"Workflow common invariants"
# and §"Stage meta keys" are canonical for both, for the host. Canonical rationale for the
# restructure this ended: docs/design/leaf_must_read_restructure.md.
# Consolidated runner-output contract, INLINED into the prompts that need it: whole to the
# `harness` shape's producer and reviewer — the runner-authoring leaf (its §3 slice went to
# `validate.judge` until issue #724 PR-2 deleted that substep). An `m3c` physics producer does not receive it (it authors the model under
# a host-rendered runner).
# The node-aware SELECTION used to be `leaf_contract_doc_refs`, deleted with the force-read
# set in Z4 (issue #171); `Conductor._build_leaf_*_context` makes the same choice now.
RUNNER_OUTPUT_CONTRACT_REF = "docs/workflow/RUNNER_OUTPUT_CONTRACT.md"
# Canonical step -> phase-doc map. Of these, only `compile` reaches a leaf — inlined whole
# into both compile prompts as `phase_contract_document`; it was the one force-read phase doc
# before Z4 (issue #171) and the selection lived in `leaf_contract_doc_refs`. The rest are the
# canonical reference mapping (readable under docs/, reaching no leaf).
WORKFLOW_PHASE_DOC_BY_STEP: dict[str, str] = {
    "compile": "docs/workflow/phases/phase_01_compile.md",
    "generate": "docs/workflow/phases/phase_02_generate.md",
    "build": "docs/workflow/phases/phase_03_build.md",
    "validate": "docs/workflow/phases/phase_04_validate.md",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_or_none(path: Path) -> Any:
    """`_read_json` that swallows a missing/unreadable/malformed file into None.

    For best-effort readers (e.g. the leaf-transport resume deriver) that must DECLINE — never
    raise — on incomplete evidence. `ValueError` covers both `json.JSONDecodeError` (malformed
    JSON) and `UnicodeDecodeError` (a non-UTF-8 file that `read_text` chokes on), so no decode
    failure escapes."""
    try:
        return _read_json(path)
    except (OSError, ValueError):
        return None


def _atomic_write_text(path: Path, body: str) -> None:
    """Adv-27: write `body` to `path` atomically via temp-file + os.replace.

    Plain `path.write_text()` truncates and rewrites in place, so a concurrent
    reader (e.g. `validate_workspace_root` polling `orchestration_meta.json`)
    can transiently observe an empty or truncated file mid-write and treat the
    orchestration as not-active — falsely flagging sanctioned tmp scripts.
    Writing to a sibling temp file then renaming guarantees readers see
    either the previous full content or the new full content, never partial.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use NamedTemporaryFile in the same directory so os.replace is atomic
    # (cross-device renames are not). delete=False because we hand the file
    # off to os.replace explicitly.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    # L-NEW-3: cleanup MUST run on BaseException too (KeyboardInterrupt /
    # SystemExit), otherwise SIGINT during a long write campaign accumulates
    # `.tmp` litter under workspace/orchestrations/. try/finally with a
    # `replaced` flag distinguishes successful rename (no cleanup) from any
    # failure path including signal-driven exits.
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(str(tmp_path), str(path))
        replaced = True
    finally:
        if not replaced:
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


_JSON_TRANSACTIONS_DIRNAME = ".json_transactions"
_JSON_TRANSACTION_VERSION = 1
_JSON_TRANSACTION_KIND = "codex_thread_registration"


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes used by the crash-recovery protocol."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(str(path), flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _codex_registration_transaction_targets(
    transaction_dir: Path,
    agent_run_id: str,
) -> tuple[Path, Path]:
    """The launch response and the session index, committed together. A third target, the
    `agents/<arid>/dialogs/child.response.json` mirror, went with the mirror in issue #447
    (D3); a journal a pre-upgrade crash left with three entries is refused as invalid rather
    than replayed onto two targets."""
    if _AGENT_RUN_ID_RE.fullmatch(agent_run_id) is None:
        raise RuntimeError(
            f"invalid agent_run_id in Codex registration transaction: {agent_run_id!r}"
        )
    return (
        transaction_dir / "launches" / f"{agent_run_id}.response.json",
        transaction_dir / "session_run_index.json",
    )


def _read_session_run_index_from_path(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"entries": []}
    try:
        payload = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {"entries": []}
    if not isinstance(payload, dict):
        return {"entries": []}
    if not isinstance(payload.get("entries"), list):
        payload["entries"] = []
    return payload


@contextlib.contextmanager
def _json_transaction_exclusive_lock(transaction_dir: Path) -> Iterator[None]:
    """Serialize every session-index writer with registration/recovery transactions."""
    lock_path = transaction_dir / "session_run_index.json.lock"
    with _fcntl_exclusive_lock(lock_path):
        yield


def _remove_transaction_tree(tx_dir: Path, tx_root: Path) -> None:
    shutil.rmtree(tx_dir)
    _fsync_directory(tx_root)


def _recover_json_transactions_unlocked(transaction_dir: Path) -> None:
    """Roll back journaled commits and remove pre-journal staging directories."""
    tx_root = transaction_dir / _JSON_TRANSACTIONS_DIRNAME
    if not tx_root.is_dir():
        return
    if tx_root.is_symlink():
        raise RuntimeError(f"JSON transaction root must not be a symlink: {tx_root}")
    for tx_dir in sorted(tx_root.iterdir()):
        if (
            not tx_dir.is_dir()
            or tx_dir.is_symlink()
            or re.fullmatch(r"[0-9a-f]{32}", tx_dir.name) is None
        ):
            raise RuntimeError(f"invalid JSON transaction staging entry: {tx_dir}")
        journal_path = tx_dir / "journal.json"
        if not journal_path.exists():
            _remove_transaction_tree(tx_dir, tx_root)
            continue
        if journal_path.is_symlink():
            raise RuntimeError(
                f"JSON transaction journal must not be a symlink: {journal_path}"
            )
        try:
            journal = _read_json(journal_path)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"cannot recover malformed json transaction journal {journal_path}: {exc}"
            ) from exc
        if (
            not isinstance(journal, dict)
            or set(journal) != {
                "version", "kind", "agent_run_id", "old_exists", "old_sha256"
            }
            or journal.get("version") != _JSON_TRANSACTION_VERSION
            or journal.get("kind") != _JSON_TRANSACTION_KIND
        ):
            raise RuntimeError(
                f"cannot recover invalid json transaction journal {journal_path}"
            )
        agent_run_id = journal.get("agent_run_id")
        old_exists = journal.get("old_exists")
        old_sha256 = journal.get("old_sha256")
        if (
            not isinstance(agent_run_id, str)
            or not isinstance(old_exists, list)
            or not isinstance(old_sha256, list)
            or len(old_exists) != 2
            or len(old_sha256) != 2
            or any(type(value) is not bool for value in old_exists)
            or any(
                (exists and (
                    not isinstance(digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                ))
                or (not exists and digest is not None)
                for exists, digest in zip(old_exists, old_sha256)
            )
        ):
            raise RuntimeError(
                f"cannot recover invalid json transaction metadata {journal_path}"
            )
        targets = _codex_registration_transaction_targets(
            transaction_dir, agent_run_id
        )
        for idx in reversed(range(len(targets))):
            target = targets[idx]
            old_path = tx_dir / f"{idx}.old"
            if not old_exists[idx]:
                target.unlink(missing_ok=True)
                _fsync_directory(target.parent)
                continue
            expected_sha = old_sha256[idx]
            if old_path.exists():
                if not old_path.is_file() or old_path.is_symlink():
                    raise RuntimeError(
                        f"invalid JSON transaction backup: {old_path}"
                    )
                if (
                    not isinstance(expected_sha, str)
                    or hashlib.sha256(old_path.read_bytes()).hexdigest() != expected_sha
                ):
                    raise RuntimeError(
                        f"JSON transaction backup hash mismatch: {old_path}"
                    )
                os.replace(str(old_path), str(target))
                _fsync_directory(target.parent)
                _fsync_directory(tx_dir)
                continue
            if (
                not target.is_file()
                or target.is_symlink()
                or not isinstance(expected_sha, str)
                or hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha
            ):
                raise RuntimeError(
                    f"json transaction backup missing before recovery completed: "
                    f"{journal_path} target={target}"
                )
        journal_path.unlink()
        _fsync_directory(tx_dir)
        _remove_transaction_tree(tx_dir, tx_root)


def _recover_json_transactions(transaction_dir: Path) -> None:
    """Serialize restart recovery against live registration transactions."""
    if not transaction_dir.is_dir():
        return
    with _json_transaction_exclusive_lock(transaction_dir):
        _recover_json_transactions_unlocked(transaction_dir)


def _read_session_run_index_consistent(
    repo_root: Path,
    orchestration_id: str,
) -> dict[str, Any]:
    """Read the index after serializing with registration and recovering journals."""
    transaction_dir = _orchestration_root(repo_root, orchestration_id)
    with _json_transaction_exclusive_lock(transaction_dir):
        _recover_json_transactions_unlocked(transaction_dir)
        return _read_session_run_index_from_path(
            _session_run_index_path(repo_root, orchestration_id)
        )


def _write_json_transaction(
    *,
    transaction_dir: Path,
    agent_run_id: str,
    thread_id: str,
    context_id: str | None,
    agent_role: str,
    status: str,
) -> None:
    """Durably commit JSON files together, with rollback after errors or restart.

    Every new payload and prior target is staged before a durable journal is written.
    Rename failures roll back immediately without allocating more space.  If the process
    dies between renames, the surviving journal and backups are recovered on the next
    transaction or when a conductor is reconstructed.
    """
    expected_targets = _codex_registration_transaction_targets(
        transaction_dir, agent_run_id
    )
    tx_root = transaction_dir / _JSON_TRANSACTIONS_DIRNAME

    with _json_transaction_exclusive_lock(transaction_dir):
        _recover_json_transactions_unlocked(transaction_dir)
        response = _read_json(expected_targets[0])
        if not isinstance(response, dict):
            raise RuntimeError("Codex launch response must be a JSON object")
        recorded = response.get("agent_session_id")
        if (
            isinstance(recorded, str)
            and recorded.strip() not in {agent_run_id, thread_id}
        ):
            raise RuntimeError(
                "codex emitted a thread_id conflicting with the launch record"
            )
        response["agent_session_id"] = thread_id
        response["session_id"] = thread_id
        transaction_updates = {expected_targets[0]: response}
        session_index = _read_session_run_index_from_path(expected_targets[1])
        _upsert_session_run_index_entry(
            session_index,
            agent_run_id=agent_run_id,
            agent_session_id=thread_id,
            context_id=context_id,
            agent_role=agent_role,
            status=status,
        )
        transaction_updates[expected_targets[1]] = session_index
        targets = list(expected_targets)
        tx_root.mkdir(parents=True, exist_ok=True)
        if tx_root.is_symlink():
            raise RuntimeError(f"JSON transaction root must not be a symlink: {tx_root}")
        _fsync_directory(transaction_dir)
        tx_dir = tx_root / uuid.uuid4().hex
        tx_dir.mkdir()
        _fsync_directory(tx_root)
        journal_path = tx_dir / "journal.json"
        journal_written = False
        try:
            old_exists: list[bool] = []
            old_sha256: list[str | None] = []
            for idx, target in enumerate(targets):
                new_path = tx_dir / f"{idx}.new"
                new_body = json.dumps(
                    transaction_updates[target], ensure_ascii=False, indent=2
                ).encode("utf-8") + b"\n"
                new_path.write_bytes(new_body)
                with new_path.open("rb") as fh:
                    os.fsync(fh.fileno())
                exists = target.exists()
                old_exists.append(exists)
                if exists:
                    old_path = tx_dir / f"{idx}.old"
                    old_body = target.read_bytes()
                    old_path.write_bytes(old_body)
                    with old_path.open("rb") as fh:
                        os.fsync(fh.fileno())
                    old_sha256.append(hashlib.sha256(old_body).hexdigest())
                else:
                    old_sha256.append(None)
            _fsync_directory(tx_dir)
            journal_written = True
            _atomic_write_text(
                journal_path,
                json.dumps({
                    "version": _JSON_TRANSACTION_VERSION,
                    "kind": _JSON_TRANSACTION_KIND,
                    "agent_run_id": agent_run_id,
                    "old_exists": old_exists,
                    "old_sha256": old_sha256,
                }, ensure_ascii=False, indent=2) + "\n",
            )
            # `_atomic_write_text` deliberately treats fsync as best-effort for
            # ordinary artifacts.  The rollback journal is different: target
            # renames must not start unless its contents are durably persisted.
            with journal_path.open("rb") as journal_fh:
                os.fsync(journal_fh.fileno())
            _fsync_directory(tx_dir)

            for idx, target in enumerate(targets):
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(tx_dir / f"{idx}.new"), str(target))
                _fsync_directory(target.parent)
                _fsync_directory(tx_dir)
        except BaseException as exc:
            if journal_written and journal_path.exists():
                try:
                    _recover_json_transactions_unlocked(transaction_dir)
                except BaseException as rollback_exc:
                    raise RuntimeError(
                        f"json transaction failed and durable rollback remains pending "
                        f"in {journal_path}: {rollback_exc}"
                    ) from exc
            elif tx_dir.exists():
                _remove_transaction_tree(tx_dir, tx_root)
            raise
        else:
            journal_path.unlink()
            _fsync_directory(tx_dir)
            _remove_transaction_tree(tx_dir, tx_root)


def _write_text(path: Path, text: str) -> None:
    body = text if text.endswith("\n") else f"{text}\n"
    _atomic_write_text(path, body)


def _orchestration_root(repo_root: Path, orchestration_id: str) -> Path:
    return repo_root / "workspace" / "orchestrations" / orchestration_id


def _active_child_agent_run_id_path(repo_root: Path, orchestration_id: str) -> Path:
    """The active child `agent_run_id` management file specific to the Claude backend."""
    return _orchestration_root(repo_root, orchestration_id) / "active_child_agent_run_id.txt"


def _active_children_dir(repo_root: Path, orchestration_id: str) -> Path:
    """Backend-neutral per-arid active-child marker directory (Adv-16).

    Each launched child writes `active_children/<agent_run_id>.txt` here.
    deactivate_child / record_agent_run terminal remove the marker.
    `finalize_child` refuses an arid whose marker is gone.
    """
    return _orchestration_root(repo_root, orchestration_id) / "active_children"


def _active_child_marker_path(repo_root: Path, orchestration_id: str, agent_run_id: str) -> Path:
    return _active_children_dir(repo_root, orchestration_id) / f"{agent_run_id}.txt"


def _clear_stale_active_child_markers(
    repo_root: Path, orchestration_id: str
) -> list[str]:
    """Clear a stale active_child window left by a host that died mid-launch.

    When the host process exits while a child launch is in flight (interrupted /
    hung child, or a token-limit kill), no live host is left to finalize the child, and
    nobody has yet run deactivate-child by hand, so `active_child_agent_run_id.txt` and the
    `active_children/<arid>.txt` markers persist. The Claude-backend sequential
    check in `record_launch` then rejects the next launch, permanently wedging the
    documented recovery (`launch_incomplete_active_child` / `llm_launch_interrupted`).

    THE PRECONDITION IS NO LONGER "the status is terminal", and the difference matters because
    this function verifies nothing — it deletes the legacy active-child file and every per-arid
    marker unconditionally. What has to be true is that NO DRIVER IS RUNNING THIS ORCHESTRATION,
    and since issue #177's PR-3 that is established by the caller holding the run's EXCLUSIVE
    CLAIM (an advisory `flock` under `~/.spec2science/start_claims/`, released by the OS when the
    holding process dies) rather than by the recorded status. A terminal status still implies it;
    a `running` status no longer contradicts it, which is why `resume_orchestration` now
    reconciles a `running` prior too.

    Calling it WITHOUT that claim held is the unsafe case, and it is unsafe in a specific way:
    a live leaf loses its active-child marker mid-run. `docs/RUNBOOK.md`
    §3-1 records where the claim can degrade and what that costs.

    Removes the legacy active-child file and every per-arid marker, returning the cleared arids
    for audit.
    """
    cleared: list[str] = []
    active_path = _active_child_agent_run_id_path(repo_root, orchestration_id)
    try:
        pointed = active_path.read_text(encoding="utf-8").strip()
    except OSError:
        pointed = ""
    if pointed:
        cleared.append(pointed)
    active_path.unlink(missing_ok=True)
    markers_dir = _active_children_dir(repo_root, orchestration_id)
    if markers_dir.is_dir():
        for marker in sorted(markers_dir.glob("*.txt")):
            arid = marker.stem
            if arid and arid not in cleared:
                cleared.append(arid)
            marker.unlink(missing_ok=True)
    return cleared


def _prune_orphan_agent_graph_edges(
    repo_root: Path, orchestration_id: str
) -> list[str]:
    """Remove agent_graph.json edges left by an ABANDONED child launch.

    `record_launch` writes the parent→child graph edge after the four `launches/`
    files, which follow every check of the launch (issue #481), but BEFORE the
    active-child marker,
    so a launch abandoned mid-flight (the dangling-launch case) leaves an
    orphan edge with no terminal `agent_runs.jsonl` row for the child. On resume the
    agent re-launches under a fresh agent_run_id (never reusing the abandoned one),
    so that edge stays orphaned forever — and `_validate_orchestration_completion_for_pass`
    rejects `set-status pass` with "agent_graph edge child_agent_run_id missing from
    agent_runs.jsonl". Prune those orphan edges so the recovered run can reach pass.

    Scope is deliberately narrow on BOTH sides so this does NOT hide unrelated
    integrity problems. An edge is pruned only when its child:

    (1) WAS genuinely launched via record_launch — proven by a durable
        `launches/<child>.request.json` (the same `is_owner_via_launch` signal). This
        excludes an arbitrarily-corrupted graph edge whose child_agent_run_id was never
        launched: that edge has no launch artifact, so it is kept and
        `_validate_orchestration_completion_for_pass` still rejects the corruption; AND

    (2) appears in NONE of these "completed/attempted" sources:
          - an `agent_runs.jsonl` row (terminalized normally);
          - a `step_result.json` reference (executor_agent_run_id / substep_agent_run_ids)
            — a child a step_result vouches for, whose missing run row is corruption;
          - an `agent_runs_invalid.jsonl` row — a child diverted there by terminal-payload
            validation (sandbox / session-id / output-manifest). It has no
            `agent_runs.jsonl` row, but its edge must be kept so validation surfaces the
            invalid terminal attempt.

    The remaining set — launched but with no record of returning/attempting to
    terminalize — is exactly the abandoned dangling launch. Both criteria derive from
    durable artifacts only, so this is idempotent and does not depend on the active-child
    markers the resume reset clears just before this runs.

    Only meaningful from the terminal-reset path (a terminal status proves no child
    run is still pending a row). Launch artifacts (launches/<arid>.*) are kept for
    forensics — only the spurious graph edge is removed.
    Returns the pruned child arids.
    """
    root = _orchestration_root(repo_root, orchestration_id)
    graph_path = root / "agent_graph.json"
    if not graph_path.is_file():
        return []
    # A corrupt agent_graph.json must not abort resume (recovery is exactly for such
    # runs); degrade to no-op pruning.
    try:
        graph = _load_graph(graph_path)
    except (OSError, json.JSONDecodeError):
        return []
    edges = graph.get("edges")
    if not isinstance(edges, list) or not edges:
        return []

    protected = _protected_child_arids(repo_root, orchestration_id)
    launches_dir = root / "launches"
    kept: list[Any] = []
    pruned: list[str] = []
    for edge in edges:
        child = edge.get("child_agent_run_id") if isinstance(edge, dict) else None
        child_id = child.strip() if isinstance(child, str) and child.strip() else None
        was_launched = (
            child_id is not None and (launches_dir / f"{child_id}.request.json").is_file()
        )
        if child_id is not None and was_launched and child_id not in protected:
            pruned.append(child_id)
        else:
            kept.append(edge)
    if pruned:
        graph["edges"] = kept
        _write_json(graph_path, graph)
    return pruned


def _protected_child_arids(repo_root: Path, orchestration_id: str) -> set[str]:
    """Child arids with durable evidence they reached / completed / attempted a run.

    Any child appearing here is NOT an abandoned launch: it has a terminal
    `agent_runs.jsonl` row, is vouched for by a `step_result.json`, has an
    `agent_runs_invalid.jsonl` entry (terminal-payload validation diverted it). Used to
    keep `_prune_orphan_agent_graph_edges` from pruning such edges.
    """
    root = _orchestration_root(repo_root, orchestration_id)
    protected: set[str] = set(_load_run_records(root).keys())
    for result_path in _iter_step_result_paths(root):
        # A malformed / partially written step_result must NOT block resume — resume is
        # the recovery path for exactly such failed/corrupt runs. Skip it: a corrupt
        # step_result still makes _validate_orchestration_completion_for_pass reject the
        # eventual set-status(pass), so no bad pass can slip through from skipping here.
        try:
            doc = _read_json(result_path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        executor = doc.get("executor_agent_run_id")
        if isinstance(executor, str) and executor.strip():
            protected.add(executor.strip())
        substeps = doc.get("substep_agent_run_ids")
        if isinstance(substeps, list):
            for sub in substeps:
                if isinstance(sub, str) and sub.strip():
                    protected.add(sub.strip())
    invalid_path = root / "agent_runs_invalid.jsonl"
    if invalid_path.is_file():
        try:
            for line in invalid_path.read_text(encoding="utf-8").splitlines():
                token = line.strip()
                if not token:
                    continue
                try:
                    rec = json.loads(token)
                except json.JSONDecodeError:
                    continue
                rid = rec.get("agent_run_id") if isinstance(rec, dict) else None
                if isinstance(rid, str) and rid.strip():
                    protected.add(rid.strip())
        except OSError:
            pass
    # A `child_returns/<arid>.txt` ack used to protect a child here too: it was written
    # when the host observed the leaf return and consumed by deactivate-child, so it
    # covered a host death between the two. Issue #447 (D2) retired the handshake. A child
    # in that window has no recorded reply and no run row, so resume re-runs its substep
    # under a fresh arid exactly as it does for an abandoned launch, and its edge is
    # pruned like one. A `child_returns/` directory a past orchestration left behind is
    # no longer read.
    return protected


def _runs_jsonl_lock_path(repo_root: Path, orchestration_id: str) -> Path:
    """Sidecar lock file used by Adv-24 to serialize concurrent finalizers.

    fcntl.flock is acquired on this file for the duration of
    record_agent_run's read-existing-IDs → append region, eliminating the
    race where two finalizers would both miss each other's in-flight write
    (one tolerated as a truncated trailing line by Adv-21) and commit
    contradictory terminal entries for the same arid.
    """
    return _orchestration_root(repo_root, orchestration_id) / "agent_runs.jsonl.lock"


def _orchestration_meta_lock_path(repo_root: Path, orchestration_id: str) -> Path:
    """Sidecar lock for orchestration_meta.json terminal-status serialization.

    Protects the read-check-write-cleanup-marker critical section in
    `update_orchestration_status` against concurrent terminalizers that would
    otherwise both pass the same-terminal guard and produce nondeterministic
    final `reason_code` / `reason_detail` (last writer wins).
    """
    return _orchestration_root(repo_root, orchestration_id) / "orchestration_meta.json.lock"


@contextlib.contextmanager
def _fcntl_exclusive_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


@contextlib.contextmanager
def _runs_jsonl_exclusive_lock(repo_root: Path, orchestration_id: str) -> Iterator[None]:
    with _fcntl_exclusive_lock(_runs_jsonl_lock_path(repo_root, orchestration_id)):
        yield


@contextlib.contextmanager
def _orchestration_meta_exclusive_lock(
    repo_root: Path, orchestration_id: str
) -> Iterator[None]:
    """Serialize the terminal-status critical section in update_orchestration_status."""
    with _fcntl_exclusive_lock(_orchestration_meta_lock_path(repo_root, orchestration_id)):
        yield


def _cleanup_committed_dir(repo_root: Path, orchestration_id: str) -> Path:
    """Adv-35: per-arid cleanup-committed marker directory.

    Two-phase finalization invariant: validate_workspace_root may revoke a
    tmp dir's exemption ONLY if both
      (a) a terminal entry for the arid exists in agent_runs.jsonl, AND
      (b) cleanup_committed/<arid>.json exists.
    The committed marker is written *after* the destructive
    `_cleanup_agent_tmp_root` call has succeeded, so a partial failure
    (terminal entry written but cleanup failed mid-way) keeps the run in a
    "cleanup pending" state — recovery scratch under workspace/tmp/<arid>/
    stays exempt for diagnostics rather than getting silently flagged.
    """
    return _orchestration_root(repo_root, orchestration_id) / "cleanup_committed"


def _cleanup_committed_marker_path(
    repo_root: Path, orchestration_id: str, agent_run_id: str
) -> Path:
    return _cleanup_committed_dir(repo_root, orchestration_id) / f"{agent_run_id}.json"


def _write_cleanup_committed_marker(
    repo_root: Path, orchestration_id: str, agent_run_id: str
) -> None:
    d = _cleanup_committed_dir(repo_root, orchestration_id)
    d.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        _cleanup_committed_marker_path(repo_root, orchestration_id, agent_run_id),
        json.dumps({
            "agent_run_id": agent_run_id,
            "committed_at": _utc_now_iso(),
        }, ensure_ascii=False) + "\n",
    )


def _session_run_index_path(repo_root: Path, orchestration_id: str) -> Path:
    return _orchestration_root(repo_root, orchestration_id) / "session_run_index.json"


def _read_session_run_index(repo_root: Path, orchestration_id: str) -> dict[str, Any]:
    return _read_session_run_index_from_path(
        _session_run_index_path(repo_root, orchestration_id)
    )


def _append_session_run_index_entry(
    repo_root: Path,
    orchestration_id: str,
    *,
    agent_run_id: str,
    agent_session_id: str,
    context_id: str | None,
    agent_role: str,
    status: str,
    workshop_lineage_id: str | None = None,
) -> None:
    transaction_dir = _orchestration_root(repo_root, orchestration_id)
    with _json_transaction_exclusive_lock(transaction_dir):
        _recover_json_transactions_unlocked(transaction_dir)
        doc = _read_session_run_index(repo_root, orchestration_id)
        _upsert_session_run_index_entry(
            doc,
            agent_run_id=agent_run_id,
            agent_session_id=agent_session_id,
            context_id=context_id,
            agent_role=agent_role,
            status=status,
            workshop_lineage_id=workshop_lineage_id,
        )
        _write_json(_session_run_index_path(repo_root, orchestration_id), doc)


def _upsert_session_run_index_entry(
    doc: dict[str, Any],
    *,
    agent_run_id: str,
    agent_session_id: str,
    context_id: str | None,
    agent_role: str,
    status: str,
    workshop_lineage_id: str | None = None,
) -> None:
    """Update ``doc`` with one normalized session/run binding without writing it.

    A workshop leaf's lineage id (`workshop_lineage_id`, issue #534) is recorded when given and
    kept when an update omits it: the launches of one lineage are the rows carrying its id. (A
    row written before issue #534 PR-J4-b may carry the pure codex leaf's `codex_lineage_id`,
    which an update keeps as it keeps any field it does not name.)"""
    entries_obj = doc.get("entries")
    entries = entries_obj if isinstance(entries_obj, list) else []
    normalized_run_id = agent_run_id.strip()
    normalized_session_id = agent_session_id.strip()
    normalized_context_id = context_id.strip() if isinstance(context_id, str) and context_id.strip() else None
    normalized_role = agent_role.strip().lower()
    normalized_status = status.strip().lower()
    for item in entries:
        if not isinstance(item, dict):
            continue
        if str(item.get("agent_run_id", "")).strip() != normalized_run_id:
            continue
        item["agent_session_id"] = normalized_session_id
        item["session_id"] = normalized_session_id
        item["context_id"] = normalized_context_id
        item["agent_role"] = normalized_role
        item["status"] = normalized_status
        if workshop_lineage_id is not None:
            item["workshop_lineage_id"] = workshop_lineage_id
        item["updated_at"] = _utc_now_iso()
        return
    entry: dict[str, Any] = {
            "agent_run_id": normalized_run_id,
            "agent_session_id": normalized_session_id,
            "session_id": normalized_session_id,
            "context_id": normalized_context_id,
            "agent_role": normalized_role,
            "status": normalized_status,
            "recorded_at": _utc_now_iso(),
        }
    if workshop_lineage_id is not None:
        entry["workshop_lineage_id"] = workshop_lineage_id
    entries.append(entry)
    doc["entries"] = entries


# --- Phase 1: access policy / phase state artifact layout (Item 10) ---

STEP_REQUIRED_CHILD_AGENT: dict[str, str] = {
    "compile": "substep",
    "generate": "substep",
    "build": "step",
    "validate": "substep",
}

# The escalate diagnostician's substep token. It is not a phase substep — no phase runs it,
# `SUBSTEPS` does not list it — but a pure launch is keyed by `(step, substep)` in the launch tables
# that key one, so the diagnostician of each escalatable phase gets its own pair rather than a branch
# in the code path. The phases are exactly the ones that launch a child agent.
DIAGNOSE_SUBSTEP = "diagnose"
DIAGNOSE_LAUNCH_PAIRS: frozenset[tuple[str, str]] = frozenset(
    (step, DIAGNOSE_SUBSTEP) for step in STEP_REQUIRED_CHILD_AGENT)


# The COMPLETE `agent_role` vocabulary of `agent_runs.jsonl`. Canonical prose:
# docs/ORCHESTRATION.md (the capability table), docs/CLI_REFERENCE.md (record-agent-run).
#
# Named once because the terminal-payload checks key on membership: a role outside this set
# used to make the FS-diff write audit return without validating (that audit went in issue
# #171 PR-2), and `record_agent_run` accepted any string at all, so a misspelling silently
# disabled it.
#
# Every member owns a filesystem write window, so the write-audited subset and this set are
# ONE set since issue #177 removed `skipped_by_checkpoint` (a role for a step that was never
# launched, which had no production writer: a skipped phase is recorded in `phase_state.json`
# as `skipped_certified` and appends no run at all). `WRITE_AUDITED_AGENT_ROLES` was that
# subset and is gone with it; its two readers ask this set.
#
# A pair of set literals with these same three members used to sit in
# `build_capability_document`, deliberately unfolded because they answered a DIFFERENT question
# ("which roles may a CAPABILITY document name"). That function went in issue #171 PR-2; this
# set is now the only one.
AGENT_RUN_ROLES: frozenset[str] = frozenset({"orchestration", "step", "substep"})

# The G5 severity vocabulary, as recorded ON a revocation. `resolve_severity_directive` in the
# conductor is canonical for what each value MEANS for a repair (minor -> reuse,
# critical -> restart); this set exists so the value survives the resume boundary. Without it a
# revoked artifact carries its findings but not how bad they were, and the resumed repair has
# no choice but to assume `major` — which turns every `critical` into a warm reuse of the very
# producer context the `critical` judged untrustworthy.
REVOCATION_SEVERITIES: frozenset[str] = frozenset({"minor", "major", "critical"})

# The repair strategy a revocation records ALONGSIDE its severity. Both are needed and neither
# implies the other: the G5 policy forces `minor -> reuse` and `critical -> restart`, but `major`
# DEFAULTS to reuse while honouring an explicit `restart` (an escalate-to-discard). A resume that
# recovered only the grade therefore turned every `major` + `restart` decision back into a warm
# `reuse` of the producer session the decision had said to throw away.
REVOCATION_REPAIR_STRATEGIES: frozenset[str] = frozenset({"reuse", "restart", "re_execute"})


def _normalized_until_phase(token: Any) -> str | None:
    """`token` as a member of `STEP_KEYS_FOR_NODE_STATE`, or None.

    ONE normalizer, because the writers and the reader drifted apart the first time this was
    fixed and the drift made the fix inert. `run_workflow.py`'s `PHASE_ORDER` is CAPITALIZED
    (`"Validate"`), which is the spelling every real orchestration records; a comparison that
    forgot `.lower()` therefore refused only the all-lowercase spelling — the one a test would
    naturally use — and allowed the product's own.
    """
    if not isinstance(token, str):
        return None
    normalized = token.strip().lower()
    return normalized if normalized in STEP_KEYS_FOR_NODE_STATE else None


def _record_until_phase_high_water(invocation_block: dict[str, Any]) -> None:
    """Raise `invocation.until_phase_high_water` to the furthest end-phase this orchestration
    has ever been driven to. Never lowers it.

    The completion vouch reads this rather than `until_phase`, because `until_phase` describes
    the CURRENT invocation and every writer of it WAS reachable from a leaf, which held a
    `Bash(python3 tools/orchestration_runtime.py *)` grant until Z4 (issue #171). The reason
    survives the grant: `until_phase` still describes one invocation, and the vouch must not
    read a bar the run itself can move.
    A run started for `validate`, re-inited or resumed `--until-phase compile`, would otherwise
    report `pass` with three phases never run — the vouch's own bar moved by the thing it judges.

    A high-water mark rather than a refusal: a `--with-deps` closure legitimately drives a
    dependency to an earlier end-phase than a previous run did (`run_workflow` derives
    `dep_until_phase` from the TARGET's end-phase), and refusing that would abort an honest
    operator workflow. Lowering the current end-phase stays allowed; what cannot be lowered is
    what the run must have CERTIFIED to call itself complete.
    """
    candidates = [
        _normalized_until_phase(invocation_block.get("until_phase_high_water")),
        _normalized_until_phase(invocation_block.get("until_phase")),
    ]
    reached = [STEP_KEYS_FOR_NODE_STATE.index(c) for c in candidates if c is not None]
    if reached:
        invocation_block["until_phase_high_water"] = STEP_KEYS_FOR_NODE_STATE[max(reached)]


STEP_KEYS_FOR_NODE_STATE: tuple[str, ...] = (
    "compile",
    "generate",
    "build",
    "validate",
)

# Output basenames that are audit/process logs rather than deliverables. Two rules read this
# one definition: the conductor's "all deliverables written" check (which must not require a
# log whose placement varies by build system), and `_certifiable_artifact_refs` (which must not
# HASH one). The second is why the definition lives here rather than in the conductor: Build's
# `required_outputs` carries `<source>/src/command_log.jsonl` and Validate.execute APPENDS to
# that same file (`run_quality_checks`'s command log), so hashing it would make every Validate
# attempt read as a tampered Build. Measured on the conductor-authored Makefile: `make test` has
# no build prerequisite and runs the binary with `cwd=RUNDIR`, so nothing else under
# `source/*/src/` or `binary/*/bin/` changes.
# The two readers pull in OPPOSITE safety directions, so adding a basename here is not a
# neutral edit: it makes the hash exclusion safer (one fewer false tamper) and the conductor's
# "all deliverables written" check weaker (one fewer required output). Weigh both.
AUDIT_LOG_BASENAMES: frozenset[str] = frozenset({
    "command_log.jsonl", "stdout.log", "stderr.log",
    "compile.stdout.log", "compile.stderr.log",
})


def _violations_dir(repo_root: Path, orchestration_id: str) -> Path:
    return _orchestration_root(repo_root, orchestration_id) / "violations"


def _sandbox_profiles_dir(repo_root: Path, orchestration_id: str) -> Path:
    return _orchestration_root(repo_root, orchestration_id) / "sandbox_profiles"


def _phase_state_path(repo_root: Path, orchestration_id: str) -> Path:
    return _orchestration_root(repo_root, orchestration_id) / "phase_state.json"


def _phase_state_log_path(repo_root: Path, orchestration_id: str) -> Path:
    return _orchestration_root(repo_root, orchestration_id) / "phase_state_log.jsonl"


def _phase_state_log_has_set_status(
    repo_root: Path, orchestration_id: str, status: str
) -> bool:
    """Return True iff `phase_state_log.jsonl` already contains a canonical
    `set_status` event whose `to` matches `status`.

    Used by `update_orchestration_status` same-terminal replay to detect when
    the original forward-transition call committed meta+cleanup but its
    audit append failed (e.g., write-permission glitch). Replay then backfills
    the canonical event from the persisted meta instead of returning a silent
    no-op that loses the transition record forever.
    """
    path = _phase_state_log_path(repo_root, orchestration_id)
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if (
            isinstance(obj, dict)
            and obj.get("event") == "set_status"
            and obj.get("to") == status
        ):
            return True
    return False


def _ensure_orchestration_audit_dirs(repo_root: Path, orchestration_id: str) -> None:
    root = _orchestration_root(repo_root, orchestration_id)
    # ONE directory. Four more were created here unconditionally — `access_policies/`,
    # `access_logs/`, `violations/` and `capabilities/` — and three of them now have no
    # writer at all (issue #171 PR-2). `violations/` has ONE live writer FUNCTION,
    # `_write_sandbox_enforcement_violation`, reached from one call site in `record_launch`
    # (the sandbox profile could not be built). The five `record_agent_run` sites re-read the
    # host-written launch response and went in issue #643. Derive the count by name rather
    # than from this comment: grep `_write_sandbox_enforcement_violation`.
    # The directory is deliberately NOT pre-created: an empty `violations/` is a record that
    # something is expected to write there, and the completion criterion for this change is
    # that a clean run leaves neither the directory nor its contents. `_write_json` creates the parent when a violation actually happens.
    (root / "sandbox_profiles").mkdir(parents=True, exist_ok=True)


def _new_phase_state_document(orchestration_id: str) -> dict[str, Any]:
    return {
        "orchestration_id": orchestration_id,
        "current_state": "initialized",
        "node_states": {},
    }


def _append_phase_state_log(
    repo_root: Path,
    orchestration_id: str,
    entry: dict[str, Any],
) -> None:
    path = _phase_state_log_path(repo_root, orchestration_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _write_phase_state(repo_root: Path, orchestration_id: str, doc: dict[str, Any]) -> None:
    _write_json(_phase_state_path(repo_root, orchestration_id), doc)


def _load_phase_state(repo_root: Path, orchestration_id: str) -> dict[str, Any] | None:
    """The host-written `phase_state.json` (sole writer `_write_phase_state`), or None."""
    path = _phase_state_path(repo_root, orchestration_id)
    if not path.exists():
        return None
    return _read_json(path)


def _current_hostname_for_record() -> str:
    """This host's name, for the resume record. Never raises and never blocks a run: an
    unresolvable hostname records the empty string, which simply cannot trigger the cross-host
    warning."""
    try:
        import socket

        return socket.gethostname().strip()
    except Exception:  # noqa: BLE001 - a provenance field must not fail a run
        return ""


def reconcile_phase_state_for_resume(
    repo_root: Path,
    orchestration_id: str,
) -> dict[str, Any]:
    """Prepare `phase_state.json` for a run that is about to be driven again.

    Two cases, and NEITHER of them is a merge from anywhere:

      * **The file is missing.** Create it, inferring `current_state` from the recorded
        preflight rather than starting at `initialized` — an orchestration that passed
        preflight and lost its phase state must not come back unable to launch.
      * **The file exists.** Keep `current_state` and `node_states` as they are, normalizing
        the node states so every step key is present.

    Issue #177 renamed this from `merge_phase_state_for_resume` and rewrote its docstring,
    and the rename is the point: the old name described reconciling against
    `orchestration_checkpoint.json`'s completion information, and the plan for #177's PR-3
    listed the function for DELETION on the strength of that name. Reading the body says
    otherwise — the preflight inference and the node-state normalization are real behaviour
    that never touched the ledger, and `TestPhase1RuleSourceAudit` pins the inference. What
    the ledger's deletion removed is the second source; what remains is this file, which is
    now the only record of where a node's phases stand.
    """
    _ensure_orchestration_audit_dirs(repo_root, orchestration_id)
    existing = _load_phase_state(repo_root, orchestration_id)
    if existing is None:
        inferred = _initial_current_state_when_phase_state_missing(repo_root, orchestration_id)
        doc = _new_phase_state_document(orchestration_id)
        doc["current_state"] = inferred
        _write_phase_state(repo_root, orchestration_id, doc)
        _append_phase_state_log(
            repo_root,
            orchestration_id,
            {
                "ts": _utc_now_iso(),
                "event": "resume_missing_phase_state",
                "from": None,
                "to": inferred,
                "note": "created for resume; inferred from preflight when possible",
            },
        )
        return doc
    merged = dict(existing)
    merged["node_states"] = _merge_node_states(merged.get("node_states"), orchestration_id)
    _write_phase_state(repo_root, orchestration_id, merged)
    _append_phase_state_log(
        repo_root,
        orchestration_id,
        {
            "ts": _utc_now_iso(),
            "event": "resume_phase_state_preserved",
            "from": merged.get("current_state"),
            "to": merged.get("current_state"),
            "note": "phase_state preserved for resume",
        },
    )
    return merged


def _merge_node_states(
    existing: Any,
    orchestration_id: str,
) -> dict[str, dict[str, str]]:
    """Normalize `node_states`: keep every recorded value, fill every missing step key with
    `not_started`. Reads nothing but its argument."""
    merged: dict[str, dict[str, str]] = {}
    if isinstance(existing, dict):
        for node_key, steps in existing.items():
            if not isinstance(node_key, str) or not node_key.strip():
                continue
            if not isinstance(steps, dict):
                continue
            inner: dict[str, str] = {}
            for sk in STEP_KEYS_FOR_NODE_STATE:
                v = steps.get(sk)
                if isinstance(v, str) and v.strip():
                    inner[sk] = v.strip()
                else:
                    inner[sk] = "not_started"
            merged[node_key.strip()] = inner
    return merged


def init_phase_state_json(
    repo_root: Path,
    orchestration_id: str,
    *,
    reason: str = "init",
) -> dict[str, Any]:
    """Write out `phase_state.json` for a new orchestration."""
    _ensure_orchestration_audit_dirs(repo_root, orchestration_id)
    doc = _new_phase_state_document(orchestration_id)
    _write_phase_state(repo_root, orchestration_id, doc)
    _append_phase_state_log(
        repo_root,
        orchestration_id,
        {
            "ts": _utc_now_iso(),
            "event": reason,
            "from": None,
            "to": doc["current_state"],
        },
    )
    return doc


def _initial_current_state_when_phase_state_missing(
    repo_root: Path,
    orchestration_id: str,
) -> str:
    """The estimated `current_state` when `phase_state.json` is absent in a legacy orchestration."""
    path = _preflight_path(repo_root, orchestration_id)
    if not path.exists():
        return "initialized"
    try:
        payload = _read_json(path)
    except (json.JSONDecodeError, OSError):
        return "initialized"
    if isinstance(payload, dict) and _preflight_allows_agent_launch(payload):
        return "preflight_passed"
    return "initialized"


def _transition_phase_state(
    repo_root: Path,
    orchestration_id: str,
    *,
    new_state: str,
    event: str,
) -> dict[str, Any]:
    doc = _load_phase_state(repo_root, orchestration_id)
    if doc is None:
        doc = _new_phase_state_document(orchestration_id)
    prev = doc.get("current_state")
    doc["current_state"] = new_state
    if doc.get("orchestration_id") != orchestration_id:
        doc["orchestration_id"] = orchestration_id
    if not isinstance(doc.get("node_states"), dict):
        doc["node_states"] = _merge_node_states({}, orchestration_id)
    _write_phase_state(repo_root, orchestration_id, doc)
    _append_phase_state_log(
        repo_root,
        orchestration_id,
        {
            "ts": _utc_now_iso(),
            "event": event,
            "from": prev,
            "to": new_state,
        },
    )
    return doc


def _transition_node_step_phase_state(
    repo_root: Path,
    orchestration_id: str,
    *,
    node_key: str,
    step: str,
    new_state: str,
    event: str,
    agent_run_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update `node_states[node_key_safe][step]` of `phase_state.json`.

    `reason` is recorded on the log entry only. The two transitions that carry one are the
    ones a person reads back to understand why a phase did not run: `skip_certified` (which
    artifact was adopted) and `phase_reset` (the routing reason that re-derived it).
    """
    node_safe = _node_key_to_safe(node_key.strip())
    step_key = step.strip().lower()

    doc = _load_phase_state(repo_root, orchestration_id)
    if doc is None:
        doc = _new_phase_state_document(orchestration_id)
    doc["orchestration_id"] = orchestration_id
    ns_any = doc.get("node_states")
    ns: dict[str, Any] = ns_any if isinstance(ns_any, dict) else {}
    inner_any = ns.get(node_safe)
    inner: dict[str, str]
    if isinstance(inner_any, dict):
        inner = {}
        for sk in STEP_KEYS_FOR_NODE_STATE:
            v = inner_any.get(sk)
            inner[sk] = v.strip() if isinstance(v, str) and v.strip() else "not_started"
    else:
        inner = {sk: "not_started" for sk in STEP_KEYS_FOR_NODE_STATE}
    prev = inner.get(step_key, "not_started")
    inner[step_key] = new_state
    ns[node_safe] = inner
    doc["node_states"] = ns
    _write_phase_state(repo_root, orchestration_id, doc)

    log_entry: dict[str, Any] = {
        "ts": _utc_now_iso(),
        "event": event,
        "node_key_safe": node_safe,
        "step": step_key,
        "from": prev,
        "to": new_state,
    }
    if agent_run_id:
        log_entry["agent_run_id"] = agent_run_id
    if reason:
        log_entry["reason"] = reason
    _append_phase_state_log(repo_root, orchestration_id, log_entry)
    return doc


def _phase_state_allows_write_step_result(
    repo_root: Path,
    orchestration_id: str,
    *,
    node_key: str,
    step: str,
) -> None:
    """`write_step_result` permits only a step that has reached `child_finished`."""
    doc = _load_phase_state(repo_root, orchestration_id)
    if doc is None:
        raise RuntimeError("write_step_result phase gate: phase_state.json missing")
    node_safe = _node_key_to_safe(node_key.strip())
    step_key = step.strip().lower()
    ns = doc.get("node_states")
    if not isinstance(ns, dict):
        raise RuntimeError("write_step_result phase gate: phase_state.node_states missing")
    inner = ns.get(node_safe)
    if not isinstance(inner, dict):
        raise RuntimeError(f"write_step_result phase gate: phase_state missing node {node_safe!r}")
    st = inner.get(step_key)
    if not isinstance(st, str):
        raise RuntimeError(
            "write_step_result phase gate: phase_state missing node step "
            f"(node_key_safe={node_safe!r}, step={step_key!r})"
        )
    token = st.strip()
    if token == "child_finished":
        return
    raise RuntimeError(
        "write_step_result phase gate: node step must be child_finished "
        f"(node_key_safe={node_safe!r}, step={step_key!r}, current={token!r})"
    )


def _write_sandbox_enforcement_violation(
    repo_root: Path,
    orchestration_id: str,
    *,
    agent_run_id: str,
    reason: str,
    detail: dict[str, Any] | None = None,
) -> Path:
    _ensure_orchestration_audit_dirs(repo_root, orchestration_id)
    out = _violations_dir(repo_root, orchestration_id) / f"{agent_run_id}.sandbox_enforcement_violation.json"
    payload: dict[str, Any] = {
        "kind": "sandbox_enforcement_violation",
        "agent_run_id": agent_run_id,
        "reason": reason,
        "evaluated_at": _utc_now_iso(),
    }
    if isinstance(detail, dict):
        payload["detail"] = detail
    _write_json(out, payload)
    return out


def _required_child_agent_kind(step: str) -> str:
    step_token = step.strip().lower()
    required = STEP_REQUIRED_CHILD_AGENT.get(step_token)
    if required is None:
        raise ValueError(
            f"unsupported workflow step {step!r}; valid steps: "
            f"{sorted(STEP_REQUIRED_CHILD_AGENT)}"
        )
    return required


def _normalized_agent_role(value: Any) -> str:
    """The canonical token for an `agent_role` value: stripped and lower-cased, else "".

    Every reader that DECIDES on the field normalizes this way, and the two chokepoints
    canonicalize the payload itself so the rest cannot disagree. Not every reader does it
    for itself, and an earlier version of this docstring wrongly claimed they all did:
    `_write_allowed_output_manifest` uses `.strip()` with no `.lower()` (so did
    `_build_task_card`, deleted with the agentic prompt in Z4, issue #171), and
    `init_orchestration` / `_rewrite_orchestration_run_row` compare
    `.strip()`-only against `"orchestration"`. That is exactly why canonicalizing at the
    chokepoints — rather than trusting each reader — is what closes the family.
    """
    return value.strip().lower() if isinstance(value, str) and value.strip() else ""


def _dependency_ready(
    repo_root: Path, orchestration_id: str, *, step: str
) -> tuple[bool, str | None]:
    meta_path = _orchestration_root(repo_root, orchestration_id) / "orchestration_meta.json"
    if not meta_path.exists():
        return False, "orchestration_meta_missing"
    step_token = step.strip().lower()
    if step_token not in {"compile", "generate", "build", "validate"}:
        return False, f"unsupported_step_for_dependency_readiness:{step_token}"
    # Codex round 27 F2: serialize the read + recompute against meta writers under the same
    # exclusive lock that `mark_dependency_readiness` / `write_preflight` /
    # `update_orchestration_status` use, so the read is a snapshot of the record they write.
    with _orchestration_meta_exclusive_lock(repo_root, orchestration_id):
        meta = _read_json(meta_path)
        if not isinstance(meta, dict):
            return False, "orchestration_meta_invalid"
        readiness = meta.get("dependency_readiness")
        if not isinstance(readiness, dict):
            return False, "dependency_readiness_missing"
        # Codex round 18 F1: do NOT trust persisted booleans. A direct edit of
        # orchestration_meta.json that flips dependency_readiness flags would otherwise pass
        # the gate. Recompute readiness from live workspace state at launch time and use the
        # recomputed booleans as authoritative; the persisted booleans are audit-only. The
        # `dep_set_fingerprint` that used to run first as a cheap stale detector went with
        # issue #250 PR-2: the recompute IS a content-hash comparison (each dependency's
        # derivation key over its inputs, its output's `artifact_hashes`), so nothing a
        # fingerprint of the selected files saw is invisible to it.
        spec_ref = meta.get("spec_ref")
        target = _target_of_orchestration_meta(repo_root, meta)
        recomputed, _certified, fail_reason = _compute_dep_readiness(
            repo_root, spec_ref, target=target)
        if recomputed is None:
            # Codex round 20 F1: launch checks fail closed when live recomputation cannot
            # run — there is no way to re-verify artifacts, and the persisted booleans are
            # audit-only. A missing/unparseable OR malformed-schema deps.yaml at gate time is
            # refused with the specific fail_reason (Codex round 25 F2: don't collapse
            # distinct verification failures into one generic reason).
            return False, fail_reason or "deps_yaml_missing_or_unparseable"
        # A dependency that is not ready is named with the stage that refused it and why —
        # a key that moved names the input that moved (`derivation_key_mismatch:<input>`) —
        # instead of leaving the operator with an opaque `..._readiness_not_pass`. Computed
        # only on the reject path, so the happy path pays nothing.
        def _reject(reason: str) -> tuple[bool, str]:
            details = _stale_dependency_details(repo_root, spec_ref, target=target)
            if not details:
                return False, reason
            disagree = [d for d in details if ": cross_target_disagreement:" in d]
            unevaluable = [d for d in details if ": cross_target_unevaluable:" in d]
            remedies = []
            if len(disagree) + len(unevaluable) < len(details):
                remedies.append("re-run with `--with-deps` to certify the dependency closure")
            if disagree:
                # Re-running does not resolve a disagreement: both variants stand certified
                # on their own keys (issue #383).
                remedies.append(
                    "a cross-target disagreement is not resolved by re-running; see "
                    "docs/RUNBOOK.md §3-0 (certifying one node on several targets)")
            if unevaluable:
                remedies.append(
                    "a cross-target comparison that cannot be evaluated is repaired at the "
                    "cause it names; see docs/RUNBOOK.md §3-0 (certifying one node on several "
                    "targets)")
            return False, (
                f"{reason}; dependency not ready: " + "; ".join(details)
                + " — " + "; ".join(remedies)
            )

        if step_token == "compile":
            if not recomputed.get("ir_ref_verified"):
                return _reject("direct_dependency_compile_readiness_not_pass")
            return True, None
        # generate / build / validate
        required = ("ir_ref_verified", "pipeline_ref_verified", "aggregate_verdict_verified")
        for required_key in required:
            if not recomputed.get(required_key):
                return _reject(f"dependency_readiness_detail_not_pass:{required_key}")
        return True, None


def workflow_launch_check(
    repo_root: Path,
    *,
    orchestration_id: str,
    node_key: str,
    step: str,
    backend: str,
) -> dict[str, Any]:
    required_by_step = _required_child_agent_kind(step)

    blocking_scope = "default_allow"
    reason_code: str | None = None
    reason_detail: str | None = None

    try:
        preflight = _require_preflight_launchable(repo_root, orchestration_id, enforce_live_probe=False)
    except RuntimeError as exc:
        return {
            "status": "fail_closed",
            "orchestration_id": orchestration_id,
            "node_key": node_key,
            "step": step.strip().lower(),
            "required_child_agent_by_step": required_by_step,
            "execution_platform_launchable": False,
            "reason_code": "child_agent_unavailable_on_execution_platform",
            "reason_detail": str(exc),
            "blocking_policy_scope": "preflight",
            "next_action": "stop_before_phase_body",
        }
    # The launching leaf's own provider must be one preflight PROBED — membership, not
    # equality. Equality was right while a run had one backend; with a per-substep
    # configuration the `providers` map is the authority and the top-level `backend` describes
    # only `defaults`. A payload without a `providers` map is a pre-#28 preflight, where the two
    # readings coincide, so the old equality test is exactly what membership degrades to.
    wanted = backend.strip().lower()
    preflight_backend = preflight.get("backend")
    providers = preflight.get("providers")
    if isinstance(providers, dict) and providers:
        probed = providers.get(wanted)
        if not (isinstance(probed, dict) and probed.get("launchable") is True):
            reason_code = reason_code or "backend_not_probed"
            reason_detail = reason_detail or (
                f"preflight probed no launchable provider {wanted!r}; probed: "
                f"{', '.join(sorted(k for k, v in providers.items() if isinstance(v, dict) and v.get('launchable') is True)) or '(none launchable)'}"
            )
    elif (isinstance(preflight_backend, str)
            and preflight_backend.strip().lower() != wanted):
        reason_code = reason_code or "child_agent_unavailable_on_execution_platform"
        reason_detail = reason_detail or (
            f"preflight backend mismatch: expected {wanted!r}, "
            f"got {preflight_backend.strip().lower()!r}"
        )

    # `_require_preflight_launchable` above refused unless BOTH `can_launch_*` flags are true.
    execution_platform_launchable = True

    dep_ready, dep_detail = _dependency_ready(repo_root, orchestration_id, step=step)
    if not dep_ready and reason_code is None:
        reason_code = "dependency_not_ready"
        reason_detail = dep_detail

    status = "pass" if reason_code is None else "fail_closed"
    return {
        "status": status,
        "orchestration_id": orchestration_id,
        "node_key": node_key,
        "step": step.strip().lower(),
        "required_child_agent_by_step": required_by_step,
        "execution_platform_launchable": execution_platform_launchable,
        "reason_code": reason_code,
        "reason_detail": reason_detail,
        "blocking_policy_scope": blocking_scope,
        "next_action": "proceed_phase_body" if status == "pass" else "stop_before_phase_body",
    }


def pre_phase_launch(
    repo_root: Path,
    *,
    orchestration_id: str,
    node_key: str,
    step: str,
    backend: str,
) -> dict[str, Any]:
    """The `workflow_launch_check` hook. It also held the downstream artifact start condition
    (`downstream_artifact_not_ready`), which checked `validate.judge`'s run directory alone and
    went with that substep in issue #724 PR-2."""
    base = workflow_launch_check(
        repo_root,
        orchestration_id=orchestration_id,
        node_key=node_key,
        step=step,
        backend=backend,
    )
    merged: dict[str, Any] = dict(base)
    merged["hook"] = "pre_phase_launch"
    return merged


# An id interpolated directly into a repo-relative path is constrained to an explicit
# allowlist rather than a denylist: only `[A-Za-z0-9_-]` (the superset of the canonical `run_` /
# `src_` id forms). This rejects not just path separators / traversal (`/`, `\`, `.`, `..`) but
# every other metacharacter (space, `~`, `*`, `?`, glob/shell chars) in one place — a guard, not
# a format check.
_SAFE_PATH_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

def _is_safe_path_id(tok: str) -> bool:
    """A path segment safe to interpolate into a per-substep write_root pin.

    A file pin embeds the id directly in a repo-relative path; anything but a plain
    `[A-Za-z0-9_-]` token could widen the pin (a separator making a sibling dir writable),
    escape the intended subtree (`.` / `..`), or introduce a shell/glob metacharacter. Reject
    everything outside that allowlist so the caller fails closed (`[]` ->
    capability_invalid_empty_write_roots).
    """
    return bool(_SAFE_PATH_ID_RE.fullmatch(tok))

def _pipeline_target_toolchain(repo_root: Path, pipeline_ref: str) -> dict[str, Any]:
    """The toolchain of the target a launch's pipeline is built for, read off the store
    coordinate of its `pipeline_ref` (`target_profile.load_pipeline_target`, issue #284).

    Before R4-a PR-2 this pair of readers (`_impl_resolved_build_system` /
    `_impl_resolved_language`) read `impl_defaults.toolchain` out of the IR, with a `make` /
    language default for an absent value. The profile carries every field (its loader refuses
    one missing), so there is nothing to default; a `pipeline_ref` that carries no target, or
    whose profile no longer loads, RAISES `TargetProfileError` (a `ValueError`), which
    `record_launch` reports as a refused launch — the conductor mints every `pipeline_ref`
    under a target, so the only way to reach it is a request the conductor did not build."""
    return load_pipeline_target(repo_root, pipeline_ref).toolchain


def _allowed_output_paths_for_launch(
    *,
    request_payload: dict[str, Any],
) -> list[str]:
    """The paths a DETERMINISTIC launch declares it will produce, as its request lists them.

    It took a `write_roots` argument until issue #171 PR-2 and checked each declared path for
    containment in the capability's roots as well; there is no capability any more. Until
    issue #643 it also matched every path against the phase's contract (where that phase's
    output belongs), refused a malformed or empty list, required one source / binary / run id,
    and injected the canonical command logs and the snapshot schema. None of that decided
    anything: the conductor builds the list from host constants for every deterministic
    substep, `determine_substep_status` reads the conductor's own in-memory list, the
    persisted request is the conductor's, and the one reader of what this returns
    (`_canonical_command_log_paths_for_request`) derives the cross-phase log from `source_id`.

    The reserved `tune` / `promote` flows (issue #174) keep their contract match: no launcher
    builds their requests yet, and their output layout is stated here and nowhere else.
    """
    raw_candidates = (
        request_payload.get("allowed_output_paths")
        or request_payload.get("required_outputs")
        or request_payload.get("output_refs")
        or []
    )
    allowed: list[str] = []
    for item in raw_candidates:
        token = _normalize_rel_posix(str(item).strip().replace("\\", "/"))
        if token not in allowed:
            allowed.append(token)
    step_token = str(request_payload.get("step") or "").strip().lower()
    pipeline_ref = _normalize_rel_posix(str(request_payload.get("pipeline_ref") or ""))
    node_key = str(request_payload.get("node_key") or "").strip()
    tune_prefix = f"{pipeline_ref}/tune/" if pipeline_ref else ""

    def _matches_phase_contract(path: str) -> bool:
        # NOTE: `tune` / `promote` step branches below are out-of-scope for the
        # core 5-phase workflow. They remain to satisfy the optional flows' contract.
        if step_token == "tune":
            if not tune_prefix or not path.startswith(tune_prefix):
                return False
            rel_under_tune = path[len(tune_prefix):]
            rel_parts = [part for part in rel_under_tune.split("/") if part]
            # tune contract must be tune/<trial_id>/<artifact>; deeper nesting is forbidden.
            if len(rel_parts) != 2:
                return False
            allowed_files = {
                "spec.ir.yaml",
                "diagnostics.json",
                "perf.json",
                "verdict.json",
                "trial_meta.json",
                "evaluation.json",
                "tune_meta.json",
            }
            base = rel_parts[1]
            return (
                base in allowed_files
                or base.endswith("_meta.json")
            )
        if step_token == "promote":
            # Promote output shape (reserved; the flow's phase document is not yet written —
            # docs/design/simplification_program.md §Premise statements):
            #   releases/<spec_kind>/<domain>/<family>/<spec_id>/
            #     <target_id>/<release_id>/<artifact_path...>
            #   spec/registry/spec_catalog.yaml (exact file)
            #
            # A release is of one node built for one target (issue #284): the target id
            # replaced the `<target_architecture>/<toolchain_language>` pair, which named two
            # of a target's axes and not the target.
            #
            # The release tree is constrained to THIS node's spec to prevent
            # cross-spec writes, AND requires the full target/release_id structure under
            # the spec prefix. A bare prefix-match
            # would let a promote launch declare ad-hoc files like
            # `releases/.../spec_x/README.md` which fall outside the canonical
            # release artifact layout.
            if path == "spec/registry/spec_catalog.yaml":
                return True
            if not node_key:
                return False
            try:
                _spec_kind, _spec_id_dotted, _ = _parse_node_key_strict(node_key)
            except ValueError:
                return False
            _spec_id_slashed = _spec_id_dotted.replace(".", "/")
            required_prefix = f"releases/{_spec_kind}/{_spec_id_slashed}/"
            if not path.startswith(required_prefix):
                return False
            tail = path[len(required_prefix):]
            tail_segments = [seg for seg in tail.split("/") if seg]
            # Tail must be <target_id>/<release_id>/<artifact_path...>
            # — at least 3 non-empty segments: a well-formed target id, then a
            # well-formed single-segment identifier (no path-traversal or control
            # characters).
            if len(tail_segments) < 3 or not is_target_id(tail_segments[0]):
                return False
            for ident in tail_segments[1:2]:
                # Identifier rule: alphanumeric, underscore, hyphen, dot. No
                # leading/trailing dot or slash. Empty already rejected above.
                if ident.startswith(".") or ident.endswith("."):
                    return False
                if not all(c.isalnum() or c in "_-." for c in ident):
                    return False
            return True
        return False

    if step_token in ("tune", "promote"):
        for idx, path in enumerate(allowed):
            if not _matches_phase_contract(path):
                raise ValueError(
                    f"allowed_output_paths[{idx}] is outside phase contract outputs for "
                    f"step={step_token!r}: {path!r}"
                )
    return allowed


# Integrity-protected audit logs written exclusively by the build-runtime library as evidence
# of tool execution. `validate_pipeline_semantics.py` reads these files and
# trusts their JSONL records (e.g. `tool_name`, `ok`, `command`) as the source
# of truth that a build-runtime entry point actually ran. Their canonical placements lie
# outside every read-write bind of a leaf's sandbox profile, so the library is their only
# writer; the placements are computed by
# `_canonical_command_log_paths()` so the validators trust only those.
#
# Protection is scoped to canonical placements only — a non-canonical file
# that happens to share this basename (e.g. an unrelated source asset under a
# nested subdirectory) is treated as a normal file. This avoids both
# (a) over-trusting any manifest entry whose basename happens to match
# (b) over-blocking legitimate project files with this name.
_COMMAND_LOG_BASENAME: str = "command_log.jsonl"


def _canonical_command_log_paths(
    *,
    step_token: str,
    pipeline_ref: str,
    node_safe: str,
    listed_paths: Sequence[str],
    source_id: str = "",
    build_system: str = "",
    substep_token: str = "",
) -> list[str]:
    """Derive canonical command log paths from the listed allowed_output_paths.

    Canonical placements (per docs/workflow/phases/phase_*.md and
    docs/workflow/phases/phase_04_validate.md):
      - generate: `<pipeline_ref>/source/<source_id>/src/command_log.jsonl`
      - build:    `<pipeline_ref>/binary/<binary_id>/command_log.jsonl`
      - validate.execute (in-phase): `<pipeline_ref>/runs/<run_id>/<node_safe>/command_log.jsonl`
      - validate.execute (cross-phase quality_check): `<pipeline_ref>/source/<source_id>/src/command_log.jsonl`
        — `run_quality_checks` runs with `project_dir=source/<source_id>/src/`
        for `toolchain.build_system=make` + Fortran/C-family pipelines per
        `docs/workflow/phases/phase_04_validate.md`, so the build-runtime library's
        default `command_log_path` (resolved as
        `project_dir/command_log.jsonl`) lands in the source tree even
        though the substep is `validate.execute`.

    Only paths matching these structures are returned. A path like
    `<source_id>/src/notes/command_log.jsonl` is **not** canonical and is
    treated as a normal file (writable subject to the usual file-tool /
    apply-patch rules).
    """
    if not pipeline_ref:
        return []
    canonical: set[str] = set()
    if step_token == "generate":
        prefix = f"{pipeline_ref}/source/"
        for tok in listed_paths:
            if not isinstance(tok, str) or not tok.startswith(prefix):
                continue
            tail = tok[len(prefix):]
            parts = [p for p in tail.split("/") if p]
            # Canonical: <source_id>/src/...
            if len(parts) >= 2 and parts[1] == "src":
                canonical.add(f"{prefix}{parts[0]}/src/{_COMMAND_LOG_BASENAME}")
    elif step_token == "build":
        prefix = f"{pipeline_ref}/binary/"
        for tok in listed_paths:
            if not isinstance(tok, str) or not tok.startswith(prefix):
                continue
            tail = tok[len(prefix):]
            parts = [p for p in tail.split("/") if p]
            # Canonical (in-phase, e.g. CMake/Meson out-of-source builds with
            # project_dir=<binary_id>/): <binary_id>/command_log.jsonl
            # alongside binary_meta.json.
            if parts:
                canonical.add(f"{prefix}{parts[0]}/{_COMMAND_LOG_BASENAME}")
        # Cross-phase placement is reserved for in-source Make builds
        # (Fortran/C-family per docs/workflow/phases/phase_03_build.md): compile_project runs
        # with `project_dir=<pipeline>/source/<source_id>/src/` (where the
        # Makefile lives), so the build-runtime library's default command_log_path
        # resolves under the source tree. Gate on a build system that builds IN SOURCE
        # (`_builds_in_source`; `make` is the one today) —
        # CMake/Meson/Ninja and other out-of-source toolchains do not
        # legitimately write into the source tree, and unconditional
        # cross-phase authorization would let those phases mutate source
        # provenance.
        if source_id and _builds_in_source(build_system):
            gen_prefix = f"{pipeline_ref}/source/"
            canonical.add(
                f"{gen_prefix}{source_id}/src/{_COMMAND_LOG_BASENAME}"
            )
    elif step_token == "validate" and substep_token == "execute" and node_safe:
        # Only the Validate.execute substep emits command logs (run_program
        # / run_quality_checks); it is the phase's only substep that runs a program.
        prefix = f"{pipeline_ref}/runs/"
        for tok in listed_paths:
            if not isinstance(tok, str) or not tok.startswith(prefix):
                continue
            tail = tok[len(prefix):]
            parts = [p for p in tail.split("/") if p]
            # Canonical (in-phase): <run_id>/<node_safe>/command_log.jsonl
            if len(parts) >= 2 and parts[1] == node_safe:
                canonical.add(
                    f"{prefix}{parts[0]}/{node_safe}/{_COMMAND_LOG_BASENAME}"
                )
        # Cross-phase quality_check log placement: derive ONLY from the
        # explicit `source_id` field AND only when the toolchain is
        # `build_system=make` (the documented Make-only exception per
        # docs/workflow/phases/phase_04_validate.md). For non-Make runs
        # (run_program against a CMake/Meson out-of-source binary), the log
        # belongs in-phase and cross-phase authorization must not be
        # granted — otherwise a child could steer command logging into the
        # source tree and contaminate verified provenance files.
        if source_id and _builds_in_source(build_system):
            gen_prefix = f"{pipeline_ref}/source/"
            canonical.add(
                f"{gen_prefix}{source_id}/src/{_COMMAND_LOG_BASENAME}"
            )
    return sorted(canonical)


def _builds_in_source(build_system: str | None) -> bool:
    """Whether the target build system builds in the source tree, so its build and quality-check
    command logs legitimately land beside the control file (the cross-phase placement above).

    Asked of the build system's `control_file` backend (`BUILDS_IN_SOURCE`, issue #289 R4-b
    PR-3) rather than compared against one spelling. A build system this repository has no
    control-file backend for answers False: the cross-phase grant is the exception, and an
    unknown build system does not earn it."""
    value = str(build_system or "")
    if not backend_registry.provides("build_system", value, "control_file"):
        return False
    try:
        module = backend_registry.capability_module("build_system", value, "control_file")
    except (backend_registry.UnsupportedBackend, backend_registry.BackendNotExtracted):
        return False
    return module.BUILDS_IN_SOURCE is True


def _canonical_command_log_paths_for_request(
    request_payload: dict[str, Any],
    allowed_output_paths: Sequence[str],
) -> list[str]:
    step_token = str(request_payload.get("step") or "").strip().lower()
    pipeline_ref = _normalize_rel_posix(str(request_payload.get("pipeline_ref") or ""))
    node_key = str(request_payload.get("node_key") or "").strip()
    node_safe = _node_key_to_safe(node_key) if node_key else ""
    source_id = str(request_payload.get("source_id") or "").strip()
    # toolchain.build_system: `_resolved_build_system`, which record_launch sets from the
    # target profile of the request's pipeline whenever the request carries a `pipeline_ref`
    # (and refuses the launch when that profile does not load). Without it, build_system=""
    # disables the cross-phase canonical placement (Make-only exception).
    build_system = ""
    bs_pre = request_payload.get("_resolved_build_system")
    if isinstance(bs_pre, str) and bs_pre.strip():
        build_system = bs_pre.strip().lower()
    substep_token = str(request_payload.get("substep") or "").strip().lower()
    return _canonical_command_log_paths(
        step_token=step_token,
        pipeline_ref=pipeline_ref,
        node_safe=node_safe,
        source_id=source_id,
        listed_paths=list(allowed_output_paths),
        build_system=build_system,
        substep_token=substep_token,
    )


def _runtime_ro_bind_paths() -> list[str]:
    runtime_paths = ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"]
    paths = [p for p in runtime_paths if Path(p).exists()]
    # DNS: `/etc/resolv.conf` is frequently a symlink to a file OUTSIDE /etc — WSL2
    # points it at `/mnt/wsl/resolv.conf`, systemd-resolved at
    # `/run/systemd/resolve/stub-resolv.conf`. ro-binding `/etc` alone leaves that
    # symlink dangling inside the sandbox, so name resolution fails and the leaf
    # `claude -p` cannot reach the API (it surfaces as "Unable to connect to API
    # (ConnectionRefused)"). Bind the resolved target at its own path so the symlink
    # resolves in-sandbox. A plain (non-symlink) resolv.conf resolves to itself under
    # the already-bound /etc and needs nothing extra.
    try:
        resolv = Path("/etc/resolv.conf")
        real = resolv.resolve()
        if (
            real != resolv
            and real.exists()
            and not any(real == Path(p) or real.is_relative_to(Path(p)) for p in paths)
        ):
            paths.append(str(real))
    except OSError:
        pass
    return paths


# --- the leaf's declared environment (issue #63, the process-environment face) -------
#
# A leaf's environment is a DECLARED ALLOWLIST, not an inheritance. The host's process
# environment decides things the launch record cannot describe: measured on CLI 2.1.235,
# `ANTHROPIC_BASE_URL` redirects 100% of a leaf's traffic to whatever host it names, and
# `ANTHROPIC_MODEL` decides the model for every launch that does not pass `--model` —
# which is the repo's default launch, since `--model` is passed only for a model the
# configuration FILE declares. Neither appears in any artifact. So the rule is the same
# one the configuration surface already follows: what the leaf gets is what this file
# names, and a name absent here is absent from the child. What this closes is the PROCESS
# environment only. A claude leaf's `HOME` is the operator's, and an `env` block in the
# settings file under it set both names again (measured; issue #453) until the leaf
# launched with `--restricted`, which ignores that file (`leaf_contract.workshop_leaf_flags`). An
# `env` block in the CLI's GLOBAL config under it still sets them: that channel is not
# closed but refused, by preflight (`claude_global_config_env_absent`).
#
# Division of labour, stated once. The conductor's `_child_env` is the single AUTHOR of
# a leaf's environment (it calls `leaf_env_from` and adds the per-run values); the bwrap
# profile / `render_bwrap_command` layer is the single DELIVERER and the single RECORD
# (`--clearenv` then one `--setenv` per entry, and `profile["env"]` persisted). The names
# the deliverer adds on its own are `_BACKEND_HOME_ENV_VARS` — and `TMPDIR`, which the
# profile builders set and `render_bwrap_command` supplies from the resolved bind when the
# record omits it. (This named the backend homes alone until a census caught the third.)

# name -> why this name is on the list. The value is the justification, so an entry
# cannot be added without writing one down (a test pins that every value is non-empty).
LEAF_ENV_ALLOWLIST: dict[str, str] = {
    "PATH": (
        "resolves the backend CLI and every subprocess a leaf runs. Absent from the host "
        "env -> the default below."
    ),
    "HOME": (
        "the CLI's own fallbacks, and for a claude leaf its credentials and the session "
        "transcript a warm `--resume` reads. Not its settings: `--restricted` ignores the "
        "settings files under it, and preflight refuses an `env` block in its global "
        "config (`_claude_global_config_paths`; issue #453). A codex "
        "leaf's home arrives as CODEX_HOME."
    ),
    "LANG": (
        "text-I/O encoding for the leaf and its subprocesses. Passed together with "
        "LC_ALL — forwarding one and dropping the other changes the resolved locale."
    ),
    "LC_ALL": "the LANG pair; see LANG.",
    "TERM": "the CLI's terminal-capability probe; kept from the pre-allowlist set.",
    "USER": "identity the CLI and git may read; kept from the pre-allowlist set.",
    "LOGNAME": "the USER pair; kept from the pre-allowlist set.",
    "PYTHONPATH": (
        "`run_workflow.py` prepends the framework root (`tools/framework_root.py`; it was "
        "the repository root before issue #705) so a child's `tools.*` imports resolve to "
        "THIS checkout rather than an installed copy or a Project's own `tools/` directory. "
        "The leaf's sandbox holds no path it names."
    ),
    "PYTHONDONTWRITEBYTECODE": (
        "keeps a grandchild python from writing __pycache__ under tools/ (issue #5; the "
        "unauthorized-write validation that first refused it went in issue #171 PR-2)."
    ),
}

# The default applied when the host environment has no PATH at all. Same value the
# pre-allowlist `_safe_host_env_for_child` used.
LEAF_ENV_PATH_DEFAULT = "/usr/bin:/bin"

# `SPEC2SCIENCE_*` is passed by PREFIX, not by enumeration. The namespace is repo-owned BY
# CONVENTION — nothing enforces it, and the honest statement is the weaker one: the
# names that are known to redirect a leaf (`ANTHROPIC_*`, stranger `CLAUDE_CODE_*`,
# `AWS_*`, `LD_*`) are outside the prefix BY CONSTRUCTION, which is what makes the
# prefix safe to pass. An enumeration instead would be a list that grows by one every
# time a variable is added and fails closed on the day someone forgets. That
# is the same allowlist-polarity argument the build-runtime library's orchestrated `env`
# allowlist rested on until it was retired with the capability gate (issue #171), reached
# for the same reason: a denylist over environment names
# does not terminate. The dangerous direction (`ANTHROPIC_*`, stranger `CLAUDE_CODE_*`,
# `AWS_*`, `LD_*`) is outside the prefix by construction, so the prefix does not weaken
# the closure.
#
# TWO justifications this comment has carried and neither survived review, recorded so a
# third is not written. (1) "every member has an in-tree reader" — the rule must not rest
# on that whether or not it holds, because the inventory moves: it was false when written
# (`SPEC2SCIENCE_MISSING_ORCHESTRATION_ID_POLICY` was seeded into every leaf by
# `run_workflow.py` and read by nothing) and issue #82 has since given that very name a
# reader in the leaf hook entrypoint (itself since deleted — Z4, issue #171, which moves the
# inventory a THIRD time and is exactly the point). A justification that has to be re-measured whenever a
# reader is added or dropped is what this comment is refusing. (2) "the namespace is
# repo-owned, so nothing an
# operator put in the environment lands inside it" — also false, and by one command:
# `leaf_env_from({"SPEC2SCIENCE_ANYTHING": "x"})` forwards it. Nothing stops an operator
# exporting a name under this prefix.
#
# ACCEPTED RESIDUE, therefore, stated rather than argued away: a `SPEC2SCIENCE_*` value an
# operator exports IS forwarded to every CLI leaf and IS persisted verbatim into
# `sandbox_profiles/<arid>.json` and its `rendered_command`, which every agentic leaf can
# read (`Bash(cat workspace/orchestrations/*)`). If that value is a secret, it is exposed.
# The one credential-shaped case the repository can recognise — a name the loaded
# configuration declares as an `api_key_env` — is dropped by `_child_env` before anything
# else. Beyond that this is an operator putting a secret inside the repository's own
# namespace, a path only the operator can reach; enumerating instead would trade it for a
# list that silently drops a variable the day someone adds one and forgets, which is the
# failure this project has actually had. Revisit if a leaf ever gains a way to set one.
LEAF_ENV_ALLOWED_PREFIXES = ("SPEC2SCIENCE_",)

# Inside the prefix but deliberately dropped. `SPEC2SCIENCE_HOME` is the deprecated alias for
# codex's configuration home; like `CODEX_HOME` it must reach the leaf through the bwrap
# profile's `--setenv` alone, so that the home the leaf reads is the home whose settings
# were SHA-pinned. The preflight still READS it (`_probe_codex_home_writable` refuses a
# conflict with CODEX_HOME) — no leaf launch forwards it.
LEAF_ENV_PREFIX_EXCEPTIONS = ("SPEC2SCIENCE_HOME",)

# Conductor-AUTHORED claude values (not host passthrough): the leaf's output ceiling and the
# closure of the CLI's nonessential traffic (telemetry, update checks; issue #534 step 4 —
# inside the leaf's network namespace each would be a refused route, logged as one).
CLAUDE_LEAF_ENV_EXTRAS = ("CLAUDE_CODE_MAX_OUTPUT_TOKENS",
                          "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")

# NAMED EXCLUSIONS — decided, not overlooked (2026-08-20). The proxy / TLS families
# (`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` / `ALL_PROXY` in both cases, `SSL_CERT_FILE`,
# `SSL_CERT_DIR`, `NODE_EXTRA_CA_CERTS`) are NOT forwarded. Two reasons: a proxy URL
# routinely carries credentials, and this list's values are persisted verbatim into
# `sandbox_profiles/<arid>.json` and the rendered argv; and their absence fails LOUDLY
# rather than silently redirecting, which is the failure mode the whole closure exists to
# prevent. For the proxy members that means at egress for every leaf (it cannot reach the
# API at all) and, for an HTTP provider, already at preflight, whose reachability probe
# takes the leaf's route (issue #83). The TLS members bind a CLI leaf only: an HTTP leaf
# and that probe run in the conductor's own process, where OpenSSL reads `SSL_CERT_FILE` /
# `SSL_CERT_DIR` from the process environment. An operator on a proxied host moves the
# names from this tuple to `LEAF_ENV_ALLOWLIST` with a justification (removing them here
# alone forwards nothing) — this comment is the record that the decision was made and what
# it costs.
# The OPERATOR's values stay excluded; the profile builder AUTHORS one proxy value of its own
# (issue #534 step 4): `HTTPS_PROXY` / `NO_PROXY` from `leaf_netns.PROXY_ENV`, naming the
# leaf's network namespace's own loopback relay. Neither reason above applies to it: the value
# is host-authored and carries no credential, and a namespace with no route out has no silent
# redirect for it to cause. `render_bwrap_command` admits those two names with exactly those
# values and refuses a workshop profile without them.
LEAF_ENV_NAMED_EXCLUSIONS = (
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    # A SECOND named exclusion, for a different reason, and it has a real cost:
    # these three are how the claude CLI authenticates on a host with no
    # `~/.claude/.credentials.json`. Excluding them means that configuration
    # launches and then fails at authentication. It is still the right call,
    # because the alternative is worse and was measured. Everything on the
    # ALLOWLIST — which is where these names would have to go to reach a leaf, and
    # not this exclusion list, whose members are precisely the ones that do NOT
    # travel — is persisted verbatim into `sandbox_profiles/<arid>.json` and into
    # that file's `rendered_command`. So allowlisting a live API key here writes it
    # into a record that lives under `workspace/`, which every later run reads — the
    # same hole the `api_key_env` rule above closes. Since Z4 (issue #171) no leaf
    # holds a tool with which to read that record itself, and since issue #227 no
    # leaf's sandbox holds the checkout at all, which narrows the reader to the
    # operator and to whatever else reads `workspace/`, and does not change the
    # call: a credential belongs in no record. The supported route is the
    # credentials FILE, which is bound into the sandbox and never copied into one.
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
)


def leaf_env_from(host_env: Mapping[str, str]) -> dict[str, str]:
    """THE filter: the declared subset of ``host_env`` a leaf may inherit.

    Both layers call this — the conductor's `_child_env` to AUTHOR the child environment,
    and the bwrap profile builders to stay allowlist-true for conductor-less callers.

    It returns the declared host names AND ONE VALUE OF ITS OWN: `LEAF_ENV_PATH_DEFAULT`
    when the host has no usable `PATH`, which `test_path_falls_back_when_the_host_has_none`
    pins. (This said "only host passthrough" until a census caught it.) Everything else
    per-run — the ids, TMPDIR, the backend home, the claude extras — is added by the
    caller that owns it.
    """
    body: dict[str, str] = {}
    for key, value in host_env.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            continue
        if key in LEAF_ENV_ALLOWLIST:
            body[key] = value
            continue
        if key in LEAF_ENV_PREFIX_EXCEPTIONS:
            continue
        if any(key.startswith(prefix) for prefix in LEAF_ENV_ALLOWED_PREFIXES):
            body[key] = value
    body.setdefault("PATH", LEAF_ENV_PATH_DEFAULT)
    return body


# The environment variables that relocate a backend CLI's configuration home.
# `render_bwrap_command` declares them through the sandbox — that `--setenv` is the ONLY
# route by which a prepared home reaches a leaf; `leaf_env_from` keeps the host's
# spellings out (these names are outside the allowlist and outside the prefix), so a
# second spelling cannot name a different home than the one whose settings were
# SHA-pinned.
# `CLAUDE_CONFIG_DIR` left this tuple with the agentic leaf (Z4, issue #171) and is back with
# the `workshop leaf` (issue #534): a workshop lineage has a private claude home, declared
# through the sandbox exactly as a codex lineage home is. A PURE claude leaf still takes
# `--safe-mode` and is prepared no home, and `render_bwrap_command` admits the name only on a
# workshop profile, naming that profile's own rw-bound `config/`.
_BACKEND_HOME_ENV_VARS = ("CODEX_HOME", "CLAUDE_CONFIG_DIR")

#: The profile `leaf_mode` of a `workshop leaf` (`leaf_contract.WORKSHOP_LEAF_MODE`). A profile
#: without the key is a pure leaf's, which every profile written before issue #534 is.
WORKSHOP_PROFILE_LEAF_MODE = "workshop"


def _resolve_backend_type(backend_type: str, backend_command: str) -> str:
    """The backend family (``claude`` / ``codex``), preferring the explicit type and
    falling back to the launch command string. A configured ``command:`` wrapper means
    the command is not literally ``claude``/``codex``, so the explicit type (recorded as
    the launch response ``backend``) is authoritative; the command is only a fallback."""
    bt = (backend_type or "").strip().lower()
    if bt in {"claude", "codex"}:
        return bt
    cmd = (backend_command or "").lower()
    if "claude" in cmd:
        return "claude"
    if "codex" in cmd:
        return "codex"
    return ""


def _install_root_for(path: Path, home: str) -> Path:
    """The directory a backend CLI's install lives under, delimited by shape, not by name.

    Under ``$HOME`` the root is the ancestor that is a direct child of ``$HOME``: a tool
    manager such as volta keeps its shims (``bin/``), its layout file and the real tools
    under one such root, and the claude installer keeps ``bin/`` and ``share/claude/`` under
    ``~/.local``. Outside ``$HOME`` it is the path's parent directory, as before issue #226
    (the system dirs are already in `_runtime_ro_bind_paths`, so that bind is a harmless
    duplicate). A path equal to ``$HOME``, or whose parent is ``$HOME``, has no install
    root: refuse rather than bind the operator's whole home read-only. ``path`` and
    ``home`` must both be absolute; the caller passes `shutil.which`'s answer and its
    `os.path.realpath`. Both are normalised lexically first (``os.path.normpath``): a
    PATH entry spelled ``$HOME/../<user>/.local/bin`` otherwise yields ``..`` as the
    "$HOME child" and binds the parent of the home (measured, issue #226 round 1).
    """
    home_path = Path(os.path.normpath(home))
    path = Path(os.path.normpath(path))
    if path == home_path:
        raise ValueError(
            f"backend CLI path is $HOME itself, which is not an install root: {path}"
        )
    try:
        rel = path.relative_to(home_path)
    except ValueError:
        return path.parent
    if len(rel.parts) < 2:
        raise ValueError(
            "backend CLI executable sits directly under $HOME, which has no install root "
            f"to bind (an executable is expected under a $HOME-child directory): {path}"
        )
    root = home_path / rel.parts[0]
    # The lexical answer can still be the whole home PHYSICALLY: a $HOME child that is a
    # symlink to the home's parent (`~/up -> /home`) binds every dotdir of the home at the
    # alias `~/up/<user>/` (measured, issue #226 round 2). bwrap resolves the source, so the
    # refusal has to compare realpaths.
    if Path(os.path.realpath(home_path)).is_relative_to(Path(os.path.realpath(root))):
        raise ValueError(
            f"backend CLI install root {root} resolves to {os.path.realpath(root)}, which "
            f"contains the whole home {home_path}; an install root must not cover $HOME"
        )
    return root


def _backend_runtime_bind_paths(
    backend_type: str, backend_command: str
) -> tuple[list[str], list[str]]:
    """Absolute host paths the backend CLI needs that live outside ``repo_root``.

    Returns ``(ro_paths, rw_paths)``:
    - ro: the backend CLI's install root, for both the command's first token as
      `shutil.which` finds it and that path's `os.path.realpath` (a custom wrapper
      resolves to the wrapper binary). The root is delimited by SHAPE, by
      `_install_root_for`, with no per-backend literal (issue #226): under ``$HOME`` it is
      the ``$HOME``-child directory the path lives under (``~/.volta`` for a volta shim,
      whose start-up needs ``tools/`` and ``layout.*`` beside ``bin/``; ``~/.local`` for the
      claude installer's ``bin/`` + ``share/<cli>/versions/``); outside ``$HOME`` it is the
      parent directory. An executable directly under ``$HOME``, or a path equal to
      ``$HOME``, is refused with `ValueError` (`record_launch` routes a profile-build
      failure to transport `fail_closed`), since the only root that would cover it is the
      operator's whole home; so is a root that covers the home PHYSICALLY (a ``$HOME``
      child symlinked to the home's parent), and — at `build_lineage_bwrap_profile`,
      which knows ``repo_root`` — a root that is, holds, or lies inside the checkout under
      a path the artifact overlays do not cover, by inode and by the mount table
      (`_refuse_backend_ro_alias_of_repo`). With ``HOME`` unset there is nothing to delimit against and
      the parent directories are bound, as before #226.
      Cost of the polarity, measured on the planning host: the claude bind widens from
      the CLI's own data dir under ``~/.local/share/`` (+ ``~/.local/bin``, + its
      ``versions/``) to ``~/.local``, which also holds ``share/keyrings``, ``state/`` and
      ``lib/``. The same widening applies to WHATEVER ``$HOME`` child holds the command or
      its realpath — a per-entry ``command:`` wrapper under ``~/work/wrappers/`` binds
      ``~/work``, and a codex installed with an npm prefix of ``~/.local`` binds
      ``~/.local`` (on the planning host it is ``~/.volta``, a measurement rather than a
      property). A pure claude leaf holds no tool (``--tools ""``) and cannot read any of
      it; a tool-bearing pure codex leaf can, and reading the operator's data is outside
      the defended set (`AGENTS.md` §Development premises). A SIBLING checkout kept under
      such a root stays readable and is that data too: what issue #227 closed is the leaf's
      OWN checkout, absent from the sandbox (`render_bwrap_command` puts an empty tmpfs at
      `repo_root`) and refused under any second name by `_refuse_backend_ro_alias_of_repo`;
      another clone holds neither this run's artifacts nor anything the prompt's rules were
      derived from at this commit. Unmodelled shapes, stated rather than guessed (none is
      measurable on the planning host): an ``/opt/<x>/bin`` install whose realpath needs a
      sibling ``lib/``; a realpath that is a ``#!/usr/bin/env <interp>`` script whose
      interpreter lives under a different root; a per-entry ``command:`` wrapper that execs
      the CLI BY NAME (the wrapper's root is bound, the CLI it names is not — rc 127 at
      launch, measured, and the same before #226); and a claude executable outside
      ``~/.local`` that still reads the CLI's data dir under ``~/.local/share/`` (the
      literal bound that directory for every claude leaf; the shape rule binds it only
      when the executable resolves under ``~/.local``).
    - rw: the backend's config/credential home (``~/.claude`` + ``~/.claude.json``
      for claude; ``~/.codex`` for codex), keyed on the backend *type* (not the command
      string, which may be a wrapper), and resolved by the canonical
      ``tools.operator_private_root.backend_credential_home_paths`` so this profile and the Bash
      read guard that forbids reading these paths cannot drift. Writable because the CLI
      refreshes auth and writes
      its session transcript there (the latter is also what Phase 4's ``--session-id`` /
      ``--resume`` rely on). An unconfined leaf already had full access to these, so
      binding them is strictly less permissive than today.
    """
    btype = _resolve_backend_type(backend_type, backend_command)
    ro: set[str] = set()
    rw: set[str] = set()
    first_token = (backend_command or "").split()
    exe = shutil.which(first_token[0]) if first_token else None
    home = (os.environ.get("HOME") or "").strip()
    if exe:
        for candidate in (Path(exe), Path(os.path.realpath(exe))):
            if home:
                ro.add(str(_install_root_for(candidate, home)))
            else:
                ro.add(str(candidate.parent))
    if home:
        # The credential-home paths come from the single canonical resolver in
        # `tools/operator_private_root.py`, so there is no second spelling of where a backend's
        # config/credential home is.
        #
        # SCOPE, since Z4 (issue #171): what this function returns is the DEFAULT rw set,
        # and a caller may pass `backend_rw_override`, which REPLACES it wholesale. A codex
        # leaf does exactly that — it is bound `{<its lineage home>}` and nothing else. A claude
        # leaf has no private home any more and takes this default: `~/.claude` writable so
        # the CLI can refresh auth and write the `--session-id` transcript a warm `--resume`
        # reads back. That transcript directory is the one repository-external surface a
        # claude leaf can write, and it holds the leaf's own conversation.
        #
        # The Bash read guard that used to forbid READING these same paths was part of the
        # leaf hook layer and went with it. It is not a lost defense against a leaf: a pure
        # leaf holds no tool at all (`--tools ""`), so it can neither read nor write them
        # from inside the sandbox — the binds exist for the CLI process itself.
        cred_dirs, cred_files = _backend_credential_home_paths(btype)
        for cred_dir in cred_dirs:  # config dir (creatable if absent)
            rw.add(str(cred_dir))
        # auth FILE: include only if present — it cannot be fabricated, and gating it
        # here means every *missing* rw entry the caller sees is a creatable dir (no
        # fragile dir-vs-file suffix guessing during materialization).
        for cred_file in cred_files:
            if cred_file.exists():
                rw.add(str(cred_file))
    ro_paths = sorted(p for p in ro if p and Path(p).exists())
    # rw (backend config/credential home) is returned unfiltered by existence; the
    # caller (build_lineage_bwrap_profile) materializes a missing config *dir* before binding,
    # so a fresh environment whose home does not yet exist (but is creatable — preflight
    # verifies the parent is writable) still gets a writable bind.
    rw_paths = sorted(p for p in rw if p)
    return ro_paths, rw_paths


def _mount_table(source: Path = Path("/proc/self/mountinfo")) -> list[tuple[str, str, str]]:
    """`(mount_point, device, root_within_device)` per entry of `/proc/self/mountinfo`.

    Empty where the file does not exist (a non-Linux host; bwrap is Linux-only, so nothing
    is lost there) — the callers then keep only the inode comparison. Octal escapes in the
    two path fields (``\040`` for a space) are decoded.
    """
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return []
    def _unescape(field: str) -> str:
        return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)
    table: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        table.append((_unescape(fields[4]), fields[2], _unescape(fields[3])))
    return table


def _fs_identity(physical: Path, table: Sequence[tuple[str, str, str]]) -> tuple[str, Path] | None:
    """Where `physical` lives on its filesystem: `(device, path within that device)`.

    The mount whose point is the longest prefix of `physical` owns it; a bind mount reports
    the SOURCE subtree it was taken from as its root-within-device, which is what makes two
    mount points of one subtree comparable. None when no entry covers the path. Among
    STACKED mounts at one point the LAST entry wins (`>=`): mountinfo lists them in mount
    order and the visible one is the most recent — with `>` the comparison read the hidden
    bottom mount, so a checkout subtree mounted on top of an innocuous one at the same point
    passed (issue #227 round 2, measured under real bwrap).
    """
    best: tuple[str, Path] | None = None
    best_len = -1
    for point, device, root_within in table:
        point_path = Path(point)
        if not physical.is_relative_to(point_path):
            continue
        if len(point_path.parts) >= best_len:
            best_len = len(point_path.parts)
            best = (device, Path(root_within) / physical.relative_to(point_path))
    return best


def sandbox_hidden_roots(repo_root: str | Path) -> list[str]:
    """The roots a leaf sandbox and a wrapper run hide with an empty tmpfs (issue #705): the
    Project root `repo_root`, as spelled, and the framework root `FRAMEWORK_ROOT` when it is
    another directory. The defended content sits under both — the `spec` nodes and every run's
    artifacts under the Project, the deterministic gates' implementation (and, with the sample
    Project nested in the framework checkout, the sample's State) under the framework — so a
    leaf that reads either aims at the checker, or reads certified sources, instead of doing
    its task: issue #227's two gains, measured open for the framework root with two distinct
    roots (issue #705, X1).

    Each root is treated as the single checkout was: the alias refusal runs for each, and its
    tmpfs is emitted after every read-only bind of the system directories and install roots and
    before every bind-back and read-write bind. No order between the two tmpfs is required
    (measured under real bwrap in either order, nested and disjoint); outer first is only a
    stable spelling."""
    project = str(repo_root)
    roots = [project]
    framework = str(FRAMEWORK_ROOT)
    if Path(framework).resolve() != Path(project).resolve():
        roots.append(framework)
    return sorted(roots, key=lambda r: (len(Path(r).parts), r))


def _refuse_backend_ro_alias_of_repo(repo_root: Path, backend_ro: Sequence[str],
                                     system_ro: Sequence[str] = ()) -> None:
    """Refuse a read-only bind through which the checkout is reachable under ANOTHER path.

    `render_bwrap_command` hides the whole checkout with an empty tmpfs at the `repo_root`
    path the caller passes — which every production caller resolves first (`main` and
    `run_workflow.py` both `.resolve()`; the conductor passes `--repo-root .`). bwrap
    resolves a bind's SOURCE on the host and mounts it at the spelled destination, so an
    install root that is the checkout, one of its ancestors, or a tree INSIDE it under a
    different name — a symlinked `$HOME` (`/home/x -> /data/x`) with the checkout and the
    CLI under the same `$HOME` child; a `$HOME` child that is a symlink to the parent of the
    home; a data disk bind-mounted into `~/work` while the workflow was started from the
    disk's own spelling; a `$HOME` child symlinked or bind-mounted onto
    `<checkout>/workspace` with the wrapper kept there — exposes the checkout at the alias
    with nothing overlaid: a VERIFY leaf reads the producer's `dialogs/leaf.stdout.jsonl`
    there, and since issue #227 the gate's implementation under `tools/` too, the gains the
    tmpfs exists to remove. Every form named was measured under real bwrap before its
    refusal landed (issue #226 rounds 2-5).

    Two comparisons, neither by path. (1) INODE: `os.path.samestat` between the root and
    each ancestor of the resolved checkout (the checkout itself included), and between the
    checkout and each strict ancestor of the root's realpath — sees a symlink in either
    direction and a bind mount whose root IS an ancestor. (2) MOUNT TABLE: the root's and
    the checkout's `(device, path within the device)` from `/proc/self/mountinfo`, for the
    root itself and for every mount point beneath it — sees a bind mount of a tree inside
    the checkout, and a bind mount BELOW the root, which `realpath` and a single inode
    cannot. A root whose SPELLING (normalised) contains the checkout, or is under it, is
    exempt from the inode walks: the tmpfs is emitted later at that path and stacks on top
    (measured under real bwrap: the planted dialogs and `tools/` file stay hidden — the
    accepted control row of `test_a_bind_mounted_install_root_that_aliases_the_checkout_is_refused`).
    It is NOT exempt from the mount table: a bind mount of the checkout's subtree BELOW an
    exempt root and outside the checkout's own path (`~/work/alias` beside `~/work/spec2science`,
    with the CLI under `~/work`) is carried in by the recursive ro-bind with nothing on top
    (issue #227 round 1, measured readable by the review before the table ran for such
    roots). The comparison is keyed on WHERE THE CHECKOUT APPEARS through each mount, the
    root's own identity included, and refuses unless that place is at or under the
    checkout's path (round 2: a rule keyed on the mount POINT's path both missed a checkout
    that is itself a bind of a tree under the root — its source exposed beside it — and
    refused a foreign mount BETWEEN the root and the checkout, which exposes the checkout
    only at its own path). Pinned by the exempt rows of
    `test_a_bind_mount_of_a_hidden_tree_at_or_below_the_install_root_is_refused`. Same
    shape as the rw refusal below.

    ``system_ro`` is the OTHER recursive ro-bind set the profile emits — `_runtime_ro_bind_paths`
    (`/usr`, `/bin`, `/sbin`, `/lib`, `/lib64`, `/etc`, the resolv.conf target) — and it takes
    the same checks (issue #227 round 3: both rounds before it attacked the install root
    while a checkout kept under `/usr/local/src` and bind-mounted into the working tree, or
    the mirror, rode in through `/usr` with nothing on top — measured readable under the
    rendered profile by the review). A system directory is exempt by spelling exactly when
    the checkout physically lives under it and the workflow was started from there, and
    then the tmpfs covers it like any other exempt root.
    """
    resolved_repo = repo_root.resolve()
    try:
        repo_stat = os.stat(resolved_repo)
    except OSError:
        repo_stat = None
    ancestors = [resolved_repo, *resolved_repo.parents]
    table = _mount_table()
    repo_identity = _fs_identity(resolved_repo, table) if table else None

    def _overlaps(identity: tuple[str, Path] | None) -> bool:
        if identity is None or repo_identity is None or identity[0] != repo_identity[0]:
            return False
        return (repo_identity[1].is_relative_to(identity[1])
                or identity[1].is_relative_to(repo_identity[1]))

    def _refuse_mounts_below(label: str, root: str, spelled: Path, physical: Path) -> None:
        # The root's own identity and every mount point strictly below it. bwrap binds the
        # root's SOURCE tree at the root's SPELLED path, recursively, so a subtree found at
        # host path P under `physical` appears in the sandbox at `spelled / P.relative_to(
        # physical)`. For each overlapping mount, compute where the CHECKOUT'S OWN subtree
        # appears through it, and refuse unless that place is at or under `resolved_repo`
        # — the one path the tmpfs covers. Keyed on where the checkout APPEARS rather than
        # on where the mount point IS (issue #227 round 2): a mount between the root and the
        # checkout (a data disk at `~/work/proj` holding `~/work/proj/spec2science`) exposes it
        # only at its own path and is accepted; a checkout that is itself a bind of a tree
        # under the root (`~/work/src/spec2science` bound at `~/work/spec2science`) exposes its source
        # at `~/work/src/spec2science` and is refused; a bind of the root itself below the root,
        # with the checkout spelled through the bind, exposes it at the root's own path and
        # is refused. Both directions of overlap: a mount whose subtree CONTAINS the checkout
        # shows it at `<appears>/<checkout within the subtree>`; a mount whose subtree lies
        # INSIDE the checkout shows that part at the mount's own place.
        mounts_under = [physical, *(Path(point) for point, _dev, _root in table
                                    if Path(point).is_relative_to(physical) and Path(point) != physical)]
        for mounted in mounts_under:
            identity = _fs_identity(mounted, table)
            if identity is None or repo_identity is None or not _overlaps(identity):
                continue
            appears_base = spelled / mounted.relative_to(physical)
            if repo_identity[1].is_relative_to(identity[1]):
                appears = appears_base / repo_identity[1].relative_to(identity[1])
                what = "the checkout"
            else:
                appears = appears_base
                what = f"the checkout's {identity[1].relative_to(repo_identity[1])}"
            if appears == resolved_repo or appears.is_relative_to(resolved_repo):
                continue
            raise ValueError(
                f"{label} {root!r} carries a mount ({mounted}) of the same "
                f"filesystem subtree as the checkout {resolved_repo}, through which "
                f"{what} appears at {appears} inside the sandbox, a path the tmpfs at "
                f"{resolved_repo} does not cover (a bind mount gives it the second name); "
                "move the CLI (or its wrapper) out of that tree, or remove the second name "
                "(start the workflow from the path the checkout physically lives at, and "
                "keep no other mount of it under the root)"
            )

    labelled = [*(("backend install root", r) for r in backend_ro),
                *(("system directory bound read-only", r) for r in system_ro)]
    for label, root in labelled:
        spelled = Path(os.path.normpath(root))
        if resolved_repo.is_relative_to(spelled) or spelled.is_relative_to(resolved_repo):
            # Exempt from the inode walks (the root IS an ancestor of the checkout, or lies
            # inside it, by its own spelling), not from the mount table.
            _refuse_mounts_below(label, root, spelled, Path(os.path.realpath(root)))
            continue
        try:
            root_stat = os.stat(root)
        except OSError:
            continue  # the existence filter in `_backend_runtime_bind_paths` already dropped it
        for ancestor in ancestors:
            try:
                if not os.path.samestat(root_stat, os.stat(ancestor)):
                    continue
            except OSError:
                continue
            raise ValueError(
                f"{label} {root!r} is the same directory as {ancestor}, which "
                f"contains the checkout {resolved_repo} under a path the sandbox does not "
                "overlay (a symlink or a bind mount gives it the second name); move the CLI "
                "(or its wrapper) out of the directory that holds the checkout, or spell HOME "
                "and the PATH entry the way the checkout is spelled, so the root's own path "
                "contains it"
            )
        physical = Path(os.path.realpath(root))
        # Strict ancestors only: a root that IS the checkout is the forward walk's first hit.
        for above in physical.parents:
            try:
                if repo_stat is None or not os.path.samestat(os.stat(above), repo_stat):
                    continue
            except OSError:
                continue
            raise ValueError(
                f"{label} {root!r} lies inside the checkout {resolved_repo} "
                f"(it resolves to {physical}) under a path the sandbox does not overlay; "
                "move the CLI (or its wrapper) out of the checkout, or reach it through the "
                "checkout's own path"
            )
        _refuse_mounts_below(label, root, spelled, physical)


def _resolve_backend_rw_binds(repo_root: Path, backend_rw_desired: Sequence[str]) -> list[str]:
    """Validate + materialize the backend config/credential home rw binds.

    Each rw bind (the backend's auth/session home) must be ENTIRELY OUTSIDE repo_root.
    render_bwrap_command emits rw binds after the `--tmpfs` at repo_root (bwrap
    later-overrides-earlier), so any rw path with a containment relationship to
    repo_root mounts the host's checkout back over the tmpfs, writable: covering
    repo_root (repo root, ~, /) exposes the whole repo, and an in-repo home (e.g.
    SPEC2SCIENCE_HOME=$repo/workspace) makes that subtree — including other agents'
    artifacts/audit logs — writable beyond the child's declared write_roots. Reject
    both. Both lineage directories exist before the build (the home preparer creates
    them); a missing one fails the launch at bwrap.
    Used by build_lineage_bwrap_profile, the one profile builder left.
    """
    resolved_repo = repo_root.resolve()
    backend_rw: list[str] = []
    for _rwp in backend_rw_desired:
        _pp = Path(_rwp)
        _rw_resolved = _pp.resolve()
        if (resolved_repo == _rw_resolved
                or resolved_repo.is_relative_to(_rw_resolved)
                or _rw_resolved.is_relative_to(resolved_repo)):
            raise ValueError(
                f"runtime rw bind {_rwp!r} is not outside repo_root {resolved_repo}; a "
                f"backend config home inside/covering the repo would grant writes beyond "
                f"the child write_roots and defeat the sandbox")
        backend_rw.append(_rwp)
    return backend_rw


#: The env name that declares a workshop profile's CLI home, by backend family: exactly one of
#: them is set on a workshop profile, and which one says whose profile it is (issue #534 step 2
#: PR-2). Both name `<lineage home>/config`.
WORKSHOP_PROFILE_HOME_ENV: dict[str, str] = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}


def workshop_profile_family(env: Mapping[str, Any]) -> str:
    """The backend family of a WORKSHOP profile, read from the home name its environment
    declares (`WORKSHOP_PROFILE_HOME_ENV`). Raises unless exactly one is declared: a profile
    naming both, or neither, is no family's, and every check keyed on the family would then
    answer for the wrong CLI."""
    declared = sorted(family for family, name in WORKSHOP_PROFILE_HOME_ENV.items()
                      if isinstance(env.get(name), str) and env.get(name))
    if len(declared) != 1:
        raise ValueError(
            "a workshop profile declares exactly one CLI home "
            f"({' or '.join(WORKSHOP_PROFILE_HOME_ENV.values())}); got {declared or 'none'}")
    return declared[0]


def build_lineage_bwrap_profile(
    *,
    repo_root: Path,
    orchestration_id: str,
    agent_run_id: str,
    backend_command: str,
    backend_type: str = "",
    backend_ro_extra: Sequence[str] = (),
    backend_ro_mappings: Sequence[tuple[str, str]] = (),
    backend_rw_override: Sequence[str] | None = None,
    env_overrides: Mapping[str, str] | None = None,
    child_env: Mapping[str, str] | None = None,
    readonly: bool = True,
    workdir: str | Path | None = None,
    leaf_mode: str | None = None,
    credential_rw_mapping: tuple[str, str] | None = None,
    network: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The bwrap profile of a `workshop leaf` lineage (issue #534) — the one profile any leaf
    runs under since issue #534 PR-J4-b. (The function keeps the name of the READ-ONLY profile
    the pure leaf ran under, `readonly: True` and no `leaf_mode`; that profile is refused at
    `render_bwrap_command` and by `Conductor.spawn_leaf`.)

    Four parameters make it a lineage's: `leaf_mode="workshop"`, `readonly=False`,
    `workdir=<the lineage's tree>` and, for claude, `credential_rw_mapping=(<the operator's
    claude credentials file>, <config>/.credentials.json)`, with `backend_rw_override=[<config>,
    <tree>]` and `env_overrides` naming the CLI home (`claude_workshop_profile_kwargs`,
    `codex_workshop_profile_kwargs`). Both rw binds are OUTSIDE the checkout (the lineage home),
    so `write_roots` stays empty and the checkout is an empty tmpfs (issue #227): what a
    workshop leaf can write is its own tree and its own CLI home, and the one file mapping is
    the claude credential, bound read-write so a leaf's token refresh reaches the host. The CLI
    writes its credential store by staging and rename; a rename onto the bound file fails
    `EBUSY` and the writer then writes in place onto the bound inode (read off the CLI 2.1.319
    binary). Measured on CLI 2.1.309 with `.claude.json` bound the same way, which the CLI
    writes with the same file writer: the write landed on the host file — consistent with that
    path, which was not measured on 2.1.309; the credential write itself happens only on a
    token refresh, which an unbilled run cannot provoke. A bind pins the inode: a HOST-side
    rename during a launch replaces the host file's inode and the leaf's view is stale from
    then on (issue #616; the conductor detects it by the file's identity and re-launches once,
    `Conductor._stale_credential_relaunch`).
    `Conductor.spawn_leaf` admits it only with exactly those two rw paths.

    `network` (issue #534 step 4) is the leaf's network namespace, `leaf_netns.network_block`:
    required, recorded as the profile's `network`, and the reason the builder authors the
    leaf's `HTTPS_PROXY` / `NO_PROXY` (`leaf_netns.PROXY_ENV`) — the in-namespace relay is the
    only route out, so the profile that records the namespace records the variables that name
    the route, whoever built it. What it emits is stated at `render_bwrap_command`.

    Its sole production caller is `record_launch`'s child-process branch. The checkout is NOT
    bound: an empty tmpfs sits at `repo_root`, so a leaf finds neither the artifact trees nor
    `tools/` / `docs/` / `spec/`; its documents are seed files of its tree. What IS bound: the
    system directories and the backend CLI's install root (ro, `runtime_ro_bind_paths`), the
    lineage's two rw directories, and the leaf's own two scratch roots (rw):
    `workspace/tmp/<arid>` and this profile's `tmp_dir`, `sandboxes/<arid>/tmp` under the
    orchestration. There are NO write_roots and NO read-root file pins. Nothing inside the
    repository is reachable EXCEPT those two scratch roots — bound rw below, under the tmpfs,
    so the leaf's `TMPDIR` resolves — and nothing an artifact could be written to is writable,
    so an artifact write the leaf attempts is refused by bwrap itself. The per-orchestration
    `hooks/` and `audit/` binds that used to sit here were for an agentic leaf's own hooks —
    the leaf runs no hook now, so binding them writable would open a repository-writable
    surface for nothing.
    """
    sandbox_root = _orchestration_root(repo_root, orchestration_id) / "sandboxes" / agent_run_id
    tmp_root = sandbox_root / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    workspace_tmp_host = (repo_root / "workspace" / "tmp" / agent_run_id).resolve()
    workspace_tmp_host.mkdir(parents=True, exist_ok=True)
    child_env = _profile_child_env(child_env, orchestration_id=orchestration_id,
                                   agent_run_id=agent_run_id)
    child_env["TMPDIR"] = str(workspace_tmp_host)
    backend_ro, backend_rw_desired = _backend_runtime_bind_paths(backend_type, backend_command)
    backend_ro.extend(str(p) for p in backend_ro_extra)
    system_ro = _runtime_ro_bind_paths()
    for hidden in sandbox_hidden_roots(repo_root):
        _refuse_backend_ro_alias_of_repo(Path(hidden), backend_ro, system_ro)
    if backend_rw_override is not None:
        backend_rw_desired = list(backend_rw_override)
    backend_rw = _resolve_backend_rw_binds(repo_root, backend_rw_desired)
    if env_overrides:
        child_env.update(env_overrides)
    # The shape (`leaf_mode`, `readonly`, `workdir`, `network`, the credential mapping) is
    # recorded as passed; the renderer and `Conductor.spawn_leaf` (`_admissible_leaf_profile`,
    # the last check before a leaf process exists) admit it.
    workshop_profile_family(env_overrides or {})
    child_env.update(leaf_netns.PROXY_ENV)
    # The claude CLI's token refresh renames onto the bound credential, fails `EBUSY`, and
    # writes in place onto the bound inode, so the credential is bound read-write and the
    # refresh reaches the host (a host-side rename leaves the leaf a stale inode: issue #616,
    # detected by the conductor); codex reads `auth.json`, remounted read-only (`codex_workshop_profile_
    # kwargs`), and binds no credential read-write.
    profile_extra: dict[str, Any] = {"leaf_mode": leaf_mode, "network": dict(network)}
    if credential_rw_mapping is not None:
        profile_extra["runtime_rw_credential_mapping"] = [str(credential_rw_mapping[0]),
                                                          str(credential_rw_mapping[1])]
    return {
        "orchestration_id": orchestration_id,
        "agent_run_id": agent_run_id,
        "sandbox_runtime": "bwrap",
        "backend_command": backend_command.strip(),
        "repo_root": str(repo_root),
        "read_roots": [],
        "write_roots": [],
        "runtime_ro_bind_paths": system_ro + backend_ro,
        "runtime_ro_bind_mappings": [list(pair) for pair in backend_ro_mappings],
        "runtime_rw_bind_mappings": [],
        "runtime_rw_bind_paths": backend_rw,
        "tmp_dir": str(tmp_root),
        "workspace_tmp_rw_abs": str(workspace_tmp_host),
        # NOTHING under the repository is writable to this leaf.
        "runtime_rw_rel_paths": [],
        "runtime_rw_file_paths": [],
        "workdir": str(workdir),
        "env": child_env,
        "generated_at": _utc_now_iso(),
        "readonly": readonly,
        **profile_extra,
    }


def _profile_child_env(child_env: Mapping[str, str] | None, *,
                       orchestration_id: str, agent_run_id: str) -> dict[str, str]:
    """The base environment a bwrap profile carries, before TMPDIR / `env_overrides`.

    ``child_env`` is the dict the conductor's ``_child_env`` AUTHORED for this leaf,
    threaded in so the profile records and delivers the environment that is actually
    used rather than a second, independently-derived one: record and reality are the same
    object. `_child_env` sets both per-launch ids from the same values this builder is
    called with.

    ``None`` — a conductor-less caller (a test fixture, the standalone CLI) — falls back
    to filtering the host environment, so those callers stay allowlist-true too.

    Whichever path is taken, the returned dict CARRIES BOTH per-launch ids: an absence is
    filled, and on the fallback a stale inherited value is overwritten. Under
    ``--clearenv`` the profile is the only route into the leaf, so "the ids are present"
    has to be a property of this function rather than of its callers.
    """
    per_launch = (("SPEC2SCIENCE_ORCHESTRATION_ID", orchestration_id),
                  ("SPEC2SCIENCE_CHILD_AGENT_RUN_ID", agent_run_id))
    if child_env is None:
        # THE FALLBACK RECONSTRUCTS; IT DOES NOT VALIDATE. The two paths answer
        # different questions and must not share an answer. Here the source is the
        # BUILDING process's own environment, so a per-launch id in it belongs to
        # whatever launched that process — for a `record-launch` run from inside a leaf
        # (documented in `docs/CLI_REFERENCE.md`, and granted to an agentic leaf until Z4 —
        # issue #171 — by `leaf_config/claude/settings.json`) that is the PARENT's id, and under the
        # workflow `run_workflow.py` puts `SPEC2SCIENCE_ORCHESTRATION_ID` in every node's
        # environment, so it is present essentially always. Those values are STALE, not
        # a contradiction: overwrite them with the ids this profile is actually for.
        #
        # This was briefly a `raise`, and that was a bad fix for a real finding —
        # measured, exporting one of the two names made 152 tests fail and, in
        # production, turned an unreachable stale record into
        # `record-launch sandbox enforcement failed` -> `fail_closed`, i.e. a killed run.
        # The finding was right; refusing was the wrong remedy for a path whose whole
        # job is to rebuild an environment from an unrelated one.
        body = leaf_env_from(os.environ)
        body.update(per_launch)
    else:
        body = dict(child_env)
    # BOTH PATHS END WITH BOTH IDS PRESENT. `--clearenv` is what made that
    # consequential: the profile is now the ONLY route into the leaf, so a
    # profile missing `SPEC2SCIENCE_ORCHESTRATION_ID` used to produce a leaf whose build-runtime
    # MCP server read "not under a run" and stopped requiring a capability token — an ungated
    # server, from an omission. That gate is retired (issue #171 PR-2) and no leaf carries an
    # MCP server at all, so what an omission costs now is ATTRIBUTION in `command_log.jsonl`.
    #
    # FILLED, not refused. Refusing would be the fourth over-refusal on this branch and
    # the second on this very function: a caller that omits an id has told us nothing
    # that contradicts these values, and the ids are the one thing this builder knows for
    # certain, since they are its own arguments.
    for name, expected in per_launch:
        body.setdefault(name, expected)
    return body


def render_bwrap_command(
    *,
    profile: dict[str, Any],
    command_argv: Sequence[str],
) -> list[str]:
    repo_root = str(profile.get("repo_root") or "").strip()
    tmp_dir = str(profile.get("tmp_dir") or "").strip()
    # NOTHING UNDER THE CHECKOUT IS GRANTED: `read_roots`, `write_roots`,
    # `runtime_rw_bind_mappings`, `runtime_rw_rel_paths` and `runtime_rw_file_paths` are
    # recorded empty by `build_lineage_bwrap_profile`, and nothing below reads them (their
    # mounting arms went with the agentic leaf, issue #445).
    # THE WORKSHOP PROFILE (issue #534) differs in its renderings, each refused on any other
    # profile: its working directory is its tree, and its CLI home is one of its rw binds — a
    # claude one declares `CLAUDE_CONFIG_DIR` and binds one credential file read-write, a codex
    # one declares `CODEX_HOME` and remounts its two host files read-only into it (step 2
    # PR-2). Each names a path the profile's own rw binds hold, so none can reach a path the
    # mount set above does not already grant.
    # Since issue #534 PR-J4-b it is the ONLY profile: the read-only one the pure leaf ran
    # under (no `leaf_mode`, `readonly: True`, workdir at `repo_root`) is refused.
    leaf_mode = profile.get("leaf_mode")
    workshop = leaf_mode == WORKSHOP_PROFILE_LEAF_MODE
    if not workshop:
        raise ValueError(f"profile leaf_mode must be {WORKSHOP_PROFILE_LEAF_MODE!r} (got "
                         f"{leaf_mode!r}): every leaf runs in a workshop lineage since issue "
                         "#534 PR-J4-b")
    rw_paths = [str(item).strip() for item in profile.get("runtime_rw_bind_paths", [])
                if isinstance(item, str) and item.strip()]
    workdir = str(profile.get("workdir") or repo_root).strip()
    if profile.get("readonly") is not False or workdir not in rw_paths:
        raise ValueError(
            "a workshop profile is writable and its workdir is one of its rw binds "
            f"(readonly={profile.get('readonly')!r}, workdir={workdir!r})")
    credential = profile.get("runtime_rw_credential_mapping")
    cred_src = cred_dst = ""
    env_of_profile = profile.get("env") if isinstance(profile.get("env"), dict) else {}
    family = workshop_profile_family(env_of_profile)
    cli_home = str(env_of_profile.get(WORKSHOP_PROFILE_HOME_ENV[family]) or "")
    if cli_home not in rw_paths or cli_home == workdir:
        raise ValueError(
            f"a {family} workshop profile's {WORKSHOP_PROFILE_HOME_ENV[family]} is one of its "
            f"rw binds and not its tree (got {cli_home!r})")
    if family == "claude":
        if not (isinstance(credential, list) and len(credential) == 2
                and all(isinstance(part, str) and part.strip() for part in credential)):
            raise ValueError("a workshop profile carries one runtime_rw_credential_mapping "
                             "[source, destination]")
        cred_src, cred_dst = credential[0].strip(), credential[1].strip()
        if not (Path(cred_src).is_file() and Path(cred_dst).is_file()
                and not Path(cred_dst).is_symlink()
                and Path(cred_dst) == Path(cli_home) / ".credentials.json"):
            raise ValueError(
                "runtime_rw_credential_mapping must map an existing file onto the existing "
                "regular file `.credentials.json` of the profile's CLAUDE_CONFIG_DIR, one of its "
                f"rw binds — never into the tree the host snapshots (got {credential!r})")
    elif family == "codex":
        # Codex's two host files are remounted read-only INTO its home, and nothing is bound
        # read-write over a file: a mapping that landed in the tree would be snapshotted as the
        # leaf's work, and one onto any other name would be a file the profile does not
        # describe.
        if credential is not None:
            raise ValueError("a codex workshop profile binds no credential read-write (got "
                             f"{credential!r})")
        destinations = sorted(
            str(m[1]).strip() for m in profile.get("runtime_ro_bind_mappings", [])
            if isinstance(m, list) and len(m) == 2)
        expected = sorted(str(Path(cli_home) / name) for name in ("auth.json", "config.toml"))
        if destinations != expected:
            raise ValueError(
                "a codex workshop profile remounts exactly auth.json and config.toml of its "
                f"CODEX_HOME read-only (got {destinations!r})")
    # THE NETWORK NAMESPACE (issue #534 step 4). `--unshare-net` leaves the leaf `lo` alone: no
    # route out and no name resolution. Its two routes back are path-bound Unix sockets in the
    # per-launch directory the conductor serves (`leaf_netns.HostRoutes`), bound read-only at
    # the fixed `leaf_netns.NET_MOUNT` — a socket in a read-only bind stays connectable, and the
    # leaf can neither add nor replace one. The sandboxed command is the in-namespace relay
    # (`leaf_netns.relay_argv`), which listens on the proxy port the leaf's `HTTPS_PROXY` names
    # and on the wrapper's own port number, and runs `command_argv` as its child.
    # `--unshare-pid`: bwrap is the namespace's init, so its death ends every process of the
    # launch — the CLI behind the relay included (the relay also ties its child to itself,
    # `PR_SET_PDEATHSIG`; `--die-with-parent` reaches only the relay). The block is a RECORD
    # checked here against the family's constant endpoint set; the host half reads the
    # constant, never the block.
    net_problems = leaf_netns.network_block_violations(
        profile.get("network"), endpoints=WORKSHOP_CLI_ENDPOINTS[family], repo_root=repo_root)
    if net_problems:
        raise ValueError("a workshop profile's network block is refused: "
                         + "; ".join(net_problems))
    network = profile["network"]
    cmd: list[str] = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
        "--unshare-pid",
        # The leaf's environment is DECLARED, not inherited (issue #63). `--clearenv`
        # drops the host's environment inside the sandbox and every name the leaf gets
        # arrives as an explicit `--setenv` below, so `profile["env"]` — which is
        # persisted to `sandbox_profiles/<arid>.json` — is the environment the leaf
        # actually ran under rather than a partial record of it. Position is
        # load-bearing and measured on bubblewrap 0.11.0: `--clearenv` wipes what
        # EARLIER `--setenv`s set, so it belongs in the fixed head, ahead of all of
        # them. (bwrap still sets `PWD` itself from `--chdir`; that is the one name in
        # the child that this dict does not name.)
        "--clearenv",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--chdir",
        workdir,
    ]
    for item in profile.get("runtime_ro_bind_paths", []):
        if isinstance(item, str) and item.strip():
            cmd.extend(["--ro-bind", item.strip(), item.strip()])
    # THE CHECKOUT IS NOT BOUND. An empty tmpfs sits at `repo_root` — the same path, so
    # `--chdir` and the private `CODEX_HOME`'s `[projects."<repo>"]` trust key are unchanged
    # — and nothing of
    # the repository is mounted under it except the leaf's OWN two scratch roots,
    # `workspace/tmp/<arid>` and the profile's `tmp_dir` (`sandboxes/<arid>/tmp`), bound rw
    # further down (bwrap creates each mountpoint under the tmpfs, as it did under the
    # `workspace/` tmpfs this replaces). What the sandbox holds, in full: the system
    # directories and the backend CLI's install root (`runtime_ro_bind_paths`, above), the
    # backend's credential home (rw, outside the checkout, below), and those two roots.
    # The tmpfs itself is writable, as bwrap's are: a write at the checkout's path lands on
    # it and is gone with the process, never on the host (measured:
    # `test_readonly_profile_hides_the_checkout_and_keeps_own_tmp_writable`).
    #
    # THE SECOND NAME OF THE SAME GAIN, and how it was closed (issue #227 round 1 found it,
    # issue #245 closed it; both measured under real bwrap with the real CLI): the codex
    # credential home is bound rw, and the CLI writes each launch's whole rollout — prompt
    # and every response item — under `$CODEX_HOME/sessions/` AND into the sqlite state at
    # the home's root (`state_*.sqlite-wal`, `thread_history_*.sqlite-wal`; measured: the
    # marker prompt in all three, codex-cli 0.154.0). While that home was per
    # ORCHESTRATION, a later leaf of the same orchestration (the VERIFY leaf, for the
    # producer it reviews; a repair turn, for the previous verdict) could read it through
    # its own shell tool, inside this profile AND inside codex's own read-only sandbox. The
    # home must stay writable (session state) and shared across the attempts of ONE thread
    # (warm `exec resume` reads the resumed thread's rollout from `sessions/`), so the
    # closure is not in the mount set's polarity but in its SUBJECT: the rw bind is the
    # per-LINEAGE home `<container>/<lineage_id>/` (`_prepare_codex_workflow_home`), one
    # per thread, and a sibling lineage is not mounted at all — not listable, not readable
    # (`test_bwrap_simulation.test_a_second_codex_leaf_cannot_read_the_first_leafs_rollout_anywhere_under_its_home`
    # walks the whole container, so a CLI version that adds a fourth file is caught too).
    #
    # WHY (issue #227). A CLAUDE pure leaf is tool-free (`--tools ""`) and could read
    # nothing either way. A CODEX pure leaf is `codex exec --sandbox read-only`: tool-BEARING,
    # and until Z4 (issue #171) its reads were refused by the leaf hook layer against the
    # empty `allowed_read_roots` this profile still records. Deleting that layer left the
    # read boundary to the mount set alone. Issue #171 PR-1 hid the artifact trees
    # (`workspace/`, the `workspace_*/` archives, `releases/`) with per-tree tmpfs overlays
    # over a whole-checkout ro-bind — a VERIFY leaf reading the producer's own
    # `dialogs/leaf.stdout.jsonl`, sibling certified sources, other orchestrations — and left
    # `tools/`, `docs/` and `spec/` readable, so a leaf could read the deterministic gate's
    # IMPLEMENTATION and aim at the checker rather than the rule the prompt quotes: a
    # presence floor's exact signal is cheaper to satisfy than the work the floor stands for,
    # and a wrong certification is the severity. A pure leaf needs no repository file at all —
    # its whole context is inlined in the launch prompt — so the checkout is simply absent,
    # and the per-tree overlays are gone with it (nothing under a tmpfs needs hiding twice;
    # `test_bwrap_simulation.py` pins each formerly overlaid tree, and the three source
    # trees, as HIDDEN under real bwrap). The codex launch pays one flag for the empty cwd:
    # `--skip-git-repo-check` (`leaf_command`), measured on codex-cli 0.154.0.
    #
    # Neither an install root nor a system directory can smuggle the checkout back in under
    # another name: `_refuse_backend_ro_alias_of_repo` runs over BOTH recursive ro-bind sets
    # (`runtime_ro_bind_paths` is the two concatenated) and refuses a root that is, holds, or
    # lies inside the checkout by inode and by the mount table — keyed on where the checkout
    # would APPEAR in the sandbox, so the one place the tmpfs covers is the one place accepted.
    #
    # Since issue #705 the framework root is hidden the same way when it is not the Project
    # root (`sandbox_hidden_roots`): the gates under its `tools/` are the gain named above.
    for hidden in sandbox_hidden_roots(repo_root):
        cmd.extend(["--tmpfs", hidden])
    # Writable runtime binds (backend config/credential home) — emitted AFTER the tmpfs
    # at repo_root. A home INSIDE repo_root is
    # refused by `_resolve_backend_rw_binds`, and for a home outside it the order is
    # immaterial; what the position records is that a later `--bind` wins over an earlier
    # mount at the same path (bwrap applies binds in order, later overriding earlier
    # overlaps) — which is why that refusal exists.
    for item in profile.get("runtime_rw_bind_paths", []):
        if isinstance(item, str) and item.strip():
            cmd.extend(["--bind", item.strip(), item.strip()])
    if family == "claude":
        # After its parent `config/` bind, which would otherwise cover it.
        cmd.extend(["--bind", cred_src, cred_dst])
    # Apply file mappings AFTER their parent writable homes.  In particular,
    # the isolated CODEX_HOME is rw for session state, while its auth.json is
    # a read-only mount of the operator's original credential file.
    for mapping in profile.get("runtime_ro_bind_mappings", []):
        if not (isinstance(mapping, list) and len(mapping) == 2
                and all(isinstance(part, str) and part.strip() for part in mapping)):
            raise ValueError("runtime_ro_bind_mappings entries must be [source, destination]")
        source, destination = mapping[0].strip(), mapping[1].strip()
        if not (Path(source).is_file() and Path(destination).is_file()):
            raise ValueError("runtime_ro_bind_mapping source and destination must be existing files")
        cmd.extend(["--ro-bind", source, destination])
    # Indexed, not defaulted: an absent field fails here rather than resolving to the
    # process's working directory and binding that read-write.
    ws_abs = str(Path(profile["workspace_tmp_rw_abs"]).resolve())
    cmd.extend(["--bind", ws_abs, ws_abs])
    cmd.extend(["--ro-bind", network["socket_dir"], leaf_netns.NET_MOUNT])
    # Every name the leaf gets, one `--setenv` each, after the head's `--clearenv`. The
    # env is the one `workshop_profile_family` read above, so it is a dict declaring a CLI
    # home. Which names it may carry is decided where it is authored (`leaf_env_from`, and
    # the host-named additions of `Conductor._child_env` and the builder).
    profile_env = env_of_profile
    claude_home = profile_env.get("CLAUDE_CONFIG_DIR")
    if claude_home is not None and not (family == "claude" and claude_home in rw_paths):
        raise ValueError(
            "profile env CLAUDE_CONFIG_DIR is declared only on a claude workshop profile, naming "
            f"one of its own rw binds (got {claude_home!r})")
    # The proxy names are the builder's, with exactly its values, and REQUIRED: a leaf in a
    # namespace whose only route is the relay, told no proxy, reaches nothing — detectable
    # here rather than as the CLI's transport failure mid-run.
    for name, value in leaf_netns.PROXY_ENV.items():
        if profile_env.get(name) != value:
            raise ValueError(
                f"profile env {name} must be {value!r}, the leaf's own in-namespace relay (got "
                f"{profile_env.get(name)!r}); the operator's proxy values are never forwarded")
    # TMPDIR is the sandbox's, not whatever a caller put in the dict: the bind above is
    # what makes it writable, so it is set to that bind below.
    # The both-ids floor, again, on the DELIVERER. `_profile_child_env` guarantees it for
    # profiles built in this process, but this function also renders a profile READ OFF
    # DISK (`spawn_leaf` -> `_sandbox_profile_for`), which that guarantee never touched —
    # including one persisted before the environment became declared, whose `env` predates
    # the ids entirely. Under `--clearenv` such a profile delivers a leaf with no
    # `SPEC2SCIENCE_ORCHESTRATION_ID`, and its build-runtime MCP server (deleted in issue #444)
    # then read "not under a run" and stopped requiring a capability token.
    # FILLED from the profile's OWN recorded ids, not refused: refusing here would reject
    # exactly those older profiles, i.e. break `--resume` of a run started before this
    # branch — the fourth over-refusal on a change whose failures have all been in that
    # direction. The profile already records both ids as top-level fields, so the right
    # values are in hand and nothing has to be guessed.
    delivered = {**profile_env, "TMPDIR": ws_abs}
    for _id_name, _id_field in (("SPEC2SCIENCE_ORCHESTRATION_ID", "orchestration_id"),
                                ("SPEC2SCIENCE_CHILD_AGENT_RUN_ID", "agent_run_id")):
        _recorded = str(profile.get(_id_field) or "").strip()
        if _recorded and not delivered.get(_id_name):
            delivered[_id_name] = _recorded
    for name, value in sorted(delivered.items()):
        # An empty value is dropped rather than declared. Under `--clearenv` the two are
        # not the same thing: `--setenv CODEX_HOME ""` would point the CLI at the empty
        # path, where absent lets its own resolution run. The pre-`--clearenv` code skipped
        # empty backend-home values for the same reason; the rule now covers every name.
        if value:
            cmd.extend(["--setenv", name, value])
    cmd.extend(["--bind", tmp_dir, tmp_dir])
    cmd.append("--")
    cmd.extend(leaf_netns.relay_argv([str(part) for part in command_argv],
                                     wrapper_port=network.get("wrapper_port")))
    return cmd


# --- the wrapper's runs (a `workshop leaf`'s second sandbox row, issue #534) ---------------
#
# `docs/design/producer_workshop_leaf.md` §Sandbox, row 2: the toolchain, the lint tool and the
# built binary a `workshop leaf`'s wrapper runs are the LEAF's actions, and the binary is
# leaf-authored code, so each runs confined to what the leaf is confined to and no more. The
# conductor renders the head below and hands it to the build-runtime library as
# `sandbox_argv_prefix`; the library splices it in front of the command it composes.

def wrapper_toolchain_ro_roots(executables: Sequence[str],
                               repo_root: Path | None = None) -> list[str]:
    """The host directories a wrapper run binds read-only so that `executables` (the compiler,
    the build tool, the linter, the launcher, as named on `PATH`) start inside it: for each one
    `shutil.which` finds, the install root of that path and of its `os.path.realpath`. Inside
    the checkout (`repo_root`; a virtualenv kept in it, as `docs/DEVELOPMENT.md` suggests) the
    root is the checkout's top-level directory holding the program, which
    `render_wrapper_sandbox_prefix` binds over its checkout tmpfs. Elsewhere it is
    `_install_root_for`'s answer (the rule the leaf's own CLI root uses), except that an install
    outside `$HOME` whose parent is a `bin/` directory binds the prefix above it, because such
    an installer keeps the libraries the program loads beside `bin/` (`<prefix>/lib`). Roots
    the system binds already cover are dropped. An executable `PATH` does not hold contributes
    nothing: the run then fails as it would on the host, inside the sandbox; so does a program
    under any other directory of the checkout (`_bindable_checkout_root`), whose root is
    dropped rather than refused.

    Since issue #705 "the checkout" is each of the two roots a run hides
    (`sandbox_hidden_roots`): the Project root and the framework root. A program under either
    takes that root's rule, and under both (a Project nested in the framework checkout) the rule
    of the innermost root that holds it. Keyed on the Project root alone, a linter in the
    framework checkout's virtualenv would bind the whole framework checkout (the home-install
    rule's answer), which the framework tmpfs then covers, and the linter would not start."""
    home = (os.environ.get("HOME") or "").strip()
    system = [Path(p) for p in _runtime_ro_bind_paths()]
    checkouts = ([] if repo_root is None
                 else [Path(r).resolve() for r in sandbox_hidden_roots(Path(repo_root).resolve())])
    roots: set[str] = set()
    for name in executables:
        found = shutil.which(name)
        if not found:
            continue
        for candidate in (Path(found), Path(os.path.realpath(found))):
            normal = Path(os.path.normpath(candidate))
            inside_home = bool(home) and normal.is_relative_to(Path(os.path.normpath(home)))
            holders = [c for c in checkouts if normal.is_relative_to(c) and normal != c]
            if holders:
                repo = max(holders, key=lambda c: len(c.parts))
                root = repo / normal.relative_to(repo).parts[0]
                if not _bindable_checkout_root(root, repo):
                    # Bound by no run: the program is then absent inside it, which fails the
                    # one operation that starts it rather than refusing every operation.
                    continue
            elif inside_home:
                root = _install_root_for(candidate, home)
            elif candidate.parent.name == "bin" and candidate.parent.parent != Path("/"):
                root = candidate.parent.parent
            else:
                root = candidate.parent
            if any(root == s or root.is_relative_to(s) for s in system):
                continue
            roots.add(str(root))
    return sorted(roots)


#: The top-level checkout directories a wrapper run may be bound back over its checkout tmpfs,
#: as an install root: the virtualenv names this repository's `.gitignore` lists. No other
#: part of the checkout is bound into a wrapper run.
WRAPPER_CHECKOUT_TOOL_DIRS: frozenset[str] = frozenset({".venv", "venv", "env"})


def _bindable_checkout_root(root: Path, repo: Path) -> bool:
    """Whether `root` (inside the checkout `repo`) may be bound back over a wrapper run's
    checkout tmpfs: a real directory (not a link) directly under the checkout, named in
    `WRAPPER_CHECKOUT_TOOL_DIRS`."""
    return (root.parent == repo and root.name in WRAPPER_CHECKOUT_TOOL_DIRS
            and root.is_dir() and not root.is_symlink())


def render_wrapper_sandbox_prefix(
    *,
    repo_root: Path,
    project_dir: Path,
    rw_binds: Sequence[Path],
    ro_binds: Sequence[Path] = (),
    toolchain_ro_roots: Sequence[str] = (),
    env: Mapping[str, str],
) -> list[str]:
    """The `bwrap ... --` head of one wrapper run, ending in `--`.

    `--unshare-net` (no interface but loopback, and no route out), `--unshare-pid` (every
    process of the run dies with the launcher, so a timeout or a lineage-end kill of the
    launcher's process group stops the whole run), `--clearenv` with one
    `--setenv` per entry of `env` (the caller passes the SAME dict to the library as `env`, so
    the command log's `env_override_keys` names what the run was given), the system
    directories and `toolchain_ro_roots` read-only, an empty tmpfs at `repo_root` so nothing of
    the checkout — `tools/` among it — is in reach of a leaf-authored program, except a
    virtualenv directory of the checkout given as an install root (`WRAPPER_CHECKOUT_TOOL_DIRS`),
    which is bound back read-only over it; then exactly `rw_binds` read-write and `ro_binds`
    read-only, each at its own host path, and `--chdir project_dir`.

    Refuses (`ValueError`): a bind that has a containment relation with the checkout (a bind
    at or under it would mount the checkout back over the tmpfs; one covering it would expose
    all of it); an install root inside the checkout other than one of its virtualenv
    directories; an install root that aliases the checkout
    (`_refuse_backend_ro_alias_of_repo`).

    "The checkout" is each root `sandbox_hidden_roots` names (issue #705): the tmpfs, the
    virtualenv bind-back and the alias refusal apply to the Project root and to the framework
    root alike. The containment refusal of `rw_binds` and `ro_binds` stays keyed on the
    Project root, the root the run writes under; a bind inside the framework checkout is
    mounted after both tmpfs and shows only itself."""
    resolved_repo = repo_root.resolve()

    def _checked(paths: Sequence[Path], kind: str) -> list[str]:
        out: list[str] = []
        for raw in paths:
            resolved = Path(raw).resolve()
            if (resolved == resolved_repo or resolved.is_relative_to(resolved_repo)
                    or resolved_repo.is_relative_to(resolved)):
                raise ValueError(
                    f"wrapper {kind} bind {str(raw)!r} is not outside the checkout "
                    f"{resolved_repo}; a wrapper run is granted no path of the checkout")
            out.append(str(resolved))
        return out

    rw = _checked(rw_binds, "rw")
    ro = _checked(ro_binds, "ro")
    project = str(Path(project_dir).resolve())
    system_ro = _runtime_ro_bind_paths()
    # The two roots a run hides (issue #705): the Project root and the framework root. An
    # install root under either is that root's virtualenv directory or refused, judged against
    # the innermost root that holds it, as `wrapper_toolchain_ro_roots` chose it.
    hidden = [Path(r) for r in sandbox_hidden_roots(resolved_repo)]
    toolchain: list[str] = []
    in_checkout: list[str] = []
    for raw in toolchain_ro_roots:
        root = Path(os.path.normpath(str(raw)))
        holders = [h for h in hidden if root.is_relative_to(h) and root != h]
        if holders:
            holder = max(holders, key=lambda h: len(h.parts))
            if not _bindable_checkout_root(root, holder):
                raise ValueError(
                    f"wrapper install root {str(raw)!r} lies in the checkout and is not one of "
                    f"its virtualenv directories {sorted(WRAPPER_CHECKOUT_TOOL_DIRS)}; a "
                    f"wrapper run is bound no other part of the checkout")
            in_checkout.append(str(root))
        else:
            toolchain.append(str(raw))
    for root in hidden:
        _refuse_backend_ro_alias_of_repo(root, toolchain, system_ro)
    cmd: list[str] = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
        # A pid namespace of its own: `--new-session` puts the program in a session apart from
        # the launcher's, so a kill of the launcher's process group reached only the launcher
        # and a background descendant survived a timeout holding the output pipe (measured,
        # issue #534 round 1). In its own pid namespace every process of the run dies with the
        # namespace's init, which `--die-with-parent` ties to the launcher.
        "--unshare-pid",
        "--clearenv",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
    ]
    for item in system_ro + toolchain:
        cmd.extend(["--ro-bind", item, item])
    for root in hidden:
        cmd.extend(["--tmpfs", str(root)])
    for item in in_checkout:
        cmd.extend(["--ro-bind", item, item])
    for item in rw:
        cmd.extend(["--bind", item, item])
    for item in ro:
        cmd.extend(["--ro-bind", item, item])
    for name, value in sorted(env.items()):
        cmd.extend(["--setenv", name, value])
    cmd.extend(["--chdir", project, "--"])
    return cmd


def _compute_sha256(path: Path) -> str:
    """Return the file's SHA-256 hash in the form "sha256:<hex>".

    When the file does not exist, return "sha256:missing" (not an error).
    """
    if not path.exists():
        return "sha256:missing"
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _cleanup_agent_tmp_root(
    repo_root: Path,
    orchestration_id: str,
    *,
    agent_run_id: str,
) -> bool:
    """Remove `workspace/tmp/<agent_run_id>/` recursively at terminal status.

    The per-agent tmp directory accumulates scratch files written with the
    file tool, helper `*.py` scripts, request/reply payloads, etc. Without cleanup the next
    invocation of `validate_workspace_root` flags every leftover `*.py` as
    "python script under workspace/ is forbidden", failing downstream phases.

    Returns:
      True  — cleanup succeeded OR the tmp dir was already absent. The caller
              may now write the cleanup_committed marker (Adv-36).
      False — cleanup was REFUSED (a symlink, or a target outside
              `workspace/tmp/`) or the destructive `rmtree` raised. The
              committed marker MUST NOT be
              written; the run remains in cleanup-pending state so the
              validator keeps tmp scratch exempt for diagnostics.

    Safety:
    - Only deletes the exact path `workspace/tmp/<agent_run_id>/` derived from
      the agent_run_id argument. The orchestration runtime only ever calls this
      for terminal-status agents, so there is no in-flight risk.
    - Validates that the target lives under repo_root/workspace/tmp/ before
      unlinking — refuses to traverse symlinks pointing elsewhere.
    - rmtree errors are not raised (cleanup must not fail the calling
      record-agent-run flow), but they are surfaced via
      the False return so callers can suppress the cleanup_committed marker.
    """
    if not isinstance(agent_run_id, str) or not agent_run_id.strip():
        return False
    arid = agent_run_id.strip()
    # Defensive: reject path-traversal-style values.
    if "/" in arid or ".." in arid or arid in {".", ""}:
        return False
    if not isinstance(orchestration_id, str) or not orchestration_id.strip():
        return False
    workspace_tmp = repo_root / "workspace" / "tmp"
    target = workspace_tmp / arid
    if not target.exists():
        # Already absent — counts as "tmp is gone" for the committed marker.
        return True
    # Reject symlink redirection — never follow a symlink at <agent_run_id>.
    try:
        if target.is_symlink():
            return False
        # Confirm the resolved path stays under workspace/tmp/.
        resolved_target = target.resolve()
        resolved_parent = workspace_tmp.resolve()
        if resolved_target.parent != resolved_parent:
            return False
    except OSError:
        return False
    import shutil
    try:
        shutil.rmtree(target)
    except OSError:
        return False
    # Successful rmtree (or post-rmtree absence). Confirm directory is gone.
    return not target.exists()


def _preflight_path(repo_root: Path, orchestration_id: str) -> Path:
    return _orchestration_root(repo_root, orchestration_id) / "preflight.json"


# The probe checks a launchable `preflight.json` must carry as `pass: true`, per backend, read
# by `_preflight_launch_refusals` — ONE source for the gate, the validator and these sets.
# The three hook checks — `hooks_enabled`, `codex_project_hooks_validated`,
# `codex_project_hook_trust_bypass` — went with the leaf's hook layer in Z4 (issue #171).
# The `multi_agent` feature state, its `multi_agent_enabled` check and the codex
# `features list` probe that was its only producer went in issue #447 (D1, D4): the conductor
# spawns independent CLI processes and never uses a platform's own subagents, so the feature
# said nothing about launchability. The claude `--version`, `--help` and `--restricted` rows
# went in issue #643 with the option-set probes of both CLIs: a CLI that cannot run the leaf
# argv refuses it before any request, and the launch ends `leaf_transport_error`.
CODEX_REQUIRED_LAUNCH_CHECKS = frozenset({
    "codex_version_available",
    "codex_home_writable", "sandbox_bwrap_available", "sandbox_bwrap_userns",
    "sandbox_bwrap_exec", "codex_exec_json_streaming",
    "codex_exec_resume", "codex_prompt_stdin",
    # The leaf's network namespace (issue #534 step 4), on both CLI families.
    "sandbox_netns_python", "sandbox_netns_route",
})
CLAUDE_REQUIRED_LAUNCH_CHECKS = frozenset({
    "claude_prompt_stdin", "claude_global_config_env_absent",
    "sandbox_bwrap_available", "sandbox_bwrap_userns", "sandbox_bwrap_exec",
    "sandbox_netns_python", "sandbox_netns_route",
})
REQUIRED_LAUNCH_CHECKS_BY_BACKEND: dict[str, frozenset[str]] = {
    "codex": CODEX_REQUIRED_LAUNCH_CHECKS,
    "claude": CLAUDE_REQUIRED_LAUNCH_CHECKS,
}

# Every option `Conductor.leaf_command` puts on a codex warm-resume argv (a workshop lineage's
# resume, the one codex resume since issue #534 PR-J4-b), checked against
# `codex exec resume --help` by the `codex_exec_resume` probe. The `exec` and `exec resume`
# subcommands do NOT take the same options — notably `resume` has no `--sandbox`, so the
# `workspace-write` policy is re-pinned there via `--config sandbox_mode=...` instead. Probing
# the `exec` help alone would pass on a CLI where the resume argv aborts at parsing, before any
# `thread.started`, turning every repair turn into a transport death. Keep this in sync with
# the resume branch of `leaf_command` (a suite row asserts the emitted option set equals this
# one).
CODEX_EXEC_RESUME_REQUIRED_FLAGS = (
    "--model", "--json", "--ignore-rules", "--config",
    # The sandbox holds no checkout (issue #227: an empty tmpfs at `repo_root`), and codex
    # refuses an untrusted non-git cwd before any API call without this flag — measured on
    # codex-cli 0.154.0: `Not inside a trusted directory and --skip-git-repo-check was not
    # specified.`, rc 1. Both subcommands take it; a repair turn that lacked it would die at
    # start-up, before `thread.started`.
    "--skip-git-repo-check",
)


def _codex_check_pass_values(checks: Sequence[Any]) -> dict[str, Any]:
    """`{check_name: pass}` for a codex preflight document."""
    return {
        str(item.get("name")): item.get("pass")
        for item in checks
        if isinstance(item, dict)
    }


def _preflight_launch_refusals(payload: dict[str, Any]) -> list[str]:
    """Every reason this preflight document does not allow a launch, in a fixed order; an empty
    list means launchable. The ONE statement of the launch conditions: the read-time gate
    (`_preflight_allows_agent_launch`) is "this list is empty", and every reader of a stored
    document asks it."""
    reasons: list[str] = []
    checks = payload.get("checks")
    if not isinstance(checks, list):
        reasons.append("checks must be a list of capability probe results")
        checks = []
    check_values = _codex_check_pass_values(checks)
    if payload.get("status") != "pass":
        reasons.append("status must be pass")
    for flag in ("can_launch_step_agents", "can_launch_substep_agents"):
        if payload.get(flag) is not True:
            reasons.append(f"{flag} must be true")
    if payload.get("sandbox_enforced") is not True:
        reasons.append("sandbox_enforced must be true")
    backend = str(payload.get("backend", "")).strip().lower()
    required = REQUIRED_LAUNCH_CHECKS_BY_BACKEND.get(backend)
    if required is None:
        reasons.append(f"backend must be one of {sorted(REQUIRED_LAUNCH_CHECKS_BY_BACKEND)}")
    else:
        missing = sorted(name for name in required if check_values.get(name) is not True)
        if missing:
            reasons.append(
                f"{backend} launchable preflight is missing required capabilities: "
                + ", ".join(missing))
    # With a `providers` map (issue #28), the top-level verdict above describes `defaults`
    # only. A run whose config also names a provider that did NOT probe launchable would reach
    # that provider's first substep and fail there, phases in — so require every probed
    # provider, fail-closed on a malformed entry. A payload without the map is a pre-#28
    # preflight and is unchanged.
    providers = payload.get("providers")
    if isinstance(providers, dict) and providers:
        unlaunchable = sorted(
            str(token) for token, entry in providers.items()
            if not (isinstance(entry, dict) and entry.get("launchable") is True))
        if unlaunchable:
            reasons.append(
                f"probed provider(s) {', '.join(unlaunchable)} are not launchable")
    return reasons


def _preflight_allows_agent_launch(payload: dict[str, Any]) -> bool:
    """True when `_preflight_launch_refusals` finds nothing."""
    return not _preflight_launch_refusals(payload)


def _live_preflight_mode() -> str:
    """Return the operation mode from the value of SPEC2SCIENCE_ORCHESTRATION_ENFORCE_LIVE_PREFLIGHT.

    Return value: 'never' | 'always' | 'ttl'
    - 'never' : skip the probe
    - 'always': probe every time (ignore TTL, backward compatible)
    - 'ttl'   : probe with TTL cache (default)
    """
    raw = os.environ.get("SPEC2SCIENCE_ORCHESTRATION_ENFORCE_LIVE_PREFLIGHT", "").strip().lower()
    if raw in {"0", "false", "no"}:
        return "never"
    if raw == "1":
        return "always"
    return "ttl"


def _live_preflight_ttl_seconds() -> int:
    """Read SPEC2SCIENCE_PREFLIGHT_TTL_SECONDS and return a non-negative integer.

    Return PREFLIGHT_TTL_DEFAULT_SECONDS when unset or an invalid value.
    """
    raw = os.environ.get("SPEC2SCIENCE_PREFLIGHT_TTL_SECONDS", "").strip()
    if not raw:
        return PREFLIGHT_TTL_DEFAULT_SECONDS
    try:
        value = int(raw)
        return max(0, value)
    except ValueError:
        return PREFLIGHT_TTL_DEFAULT_SECONDS


def _is_within_preflight_ttl(probed_at_iso: str, ttl_seconds: int) -> bool:
    """True if the elapsed seconds from probed_at_iso is less than ttl_seconds.

    Always False when ttl_seconds == 0 (no cache).
    False on a parse failure (err on the safe side and run the probe).
    """
    if ttl_seconds <= 0:
        return False
    try:
        probed_at = datetime.fromisoformat(probed_at_iso.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - probed_at).total_seconds()
        return elapsed < ttl_seconds
    except (ValueError, TypeError):
        return False


def _update_preflight_probed_at(
    repo_root: Path,
    orchestration_id: str,
    probed_at_iso: str,
    providers: dict[str, Any] | None = None,
) -> None:
    """Update the probed_at field of preflight.json, and the `providers` map when re-probed.

    Do not change other fields (status / can_launch_* etc.).
    Do nothing when preflight.json does not exist (not an error).

    `probed_at` is the freshness claim for the WHOLE document, so a re-probe that produced new
    provider rows must persist them with it: leaving the stale rows while advancing the
    timestamp is precisely the "treated as fresh indefinitely" state this guards against.
    """
    path = _preflight_path(repo_root, orchestration_id)
    if not path.exists():
        return
    try:
        file_payload = _read_json(path)
    except json.JSONDecodeError:
        return
    if not isinstance(file_payload, dict):
        return
    file_payload["probed_at"] = probed_at_iso
    if providers is not None:
        file_payload["providers"] = providers
    _write_json(path, file_payload)


def _reprobe_recorded_providers(
    cached_providers: Mapping[str, Any],
    repo_root: Path | None,
) -> dict[str, Any]:
    """Re-probe every provider row a preflight recorded, from the row itself.

    The rows carry everything a probe needs — `provider_type`, and the `probe_command` or the
    `base_url` / `api_key_env` — so this does not reload the configuration file (which the
    preflight deliberately does not record: `orchestration_meta.json#invocation` already pins
    its path and hash). A row that is not a mapping is carried through as unlaunchable rather
    than dropped, so a corrupted document cannot become a launchable one by re-probing."""
    fresh: dict[str, Any] = {}
    for token, row in cached_providers.items():
        if not isinstance(row, dict):
            fresh[str(token)] = {"launchable": False, "surfaces": [],
                                 "checks": [{"name": "provider_row_unreadable",
                                             "pass": False, "detail": repr(row)[:200]}]}
            continue
        # EVERY surface recorded under the token, AND-ed exactly as the initial probe did. A
        # pre-adoption row (or one written before `surfaces` existed) degrades to the single
        # surface its top-level fields name, which is what it always described.
        surfaces = row.get("surfaces")
        if not isinstance(surfaces, list) or not surfaces:
            surfaces = [{"provider_type": row.get("provider_type", ""),
                         "probe_command": row.get("probe_command", ""),
                         "base_url": row.get("base_url", ""),
                         "api_key_env": row.get("api_key_env", "")}]
        merged: dict[str, Any] | None = None
        for surface in surfaces:
            surface = surface if isinstance(surface, dict) else {}
            provider_type = str(surface.get("provider_type") or row.get("provider_type") or "")
            if provider_type in ("claude_cli", "codex_cli"):
                command = str(surface.get("probe_command") or "").strip()
                probe = probe_execution_platform(
                    backend=str(token), agent_command=command or None, repo_root=repo_root)
                probed = {
                    "provider_type": provider_type,
                    "probe_command": probe.get("probe_command", ""),
                    "checks": probe.get("checks", []),
                    "launchable": _preflight_allows_agent_launch(probe),
                }
            else:
                probed = _probe_http_provider({
                    "backend": str(token), "provider": provider_type,
                    "base_url": surface.get("base_url", ""),
                    "api_key_env": surface.get("api_key_env", ""),
                })
            probed["surfaces"] = [{
                "provider_type": provider_type,
                "probe_command": str(surface.get("probe_command") or ""),
                "base_url": str(surface.get("base_url") or ""),
                "api_key_env": str(surface.get("api_key_env") or ""),
                **({"workshop": True}
                   if provider_type in ("claude_cli", "codex_cli") else {}),
            }]
            if merged is None:
                merged = probed
            else:
                merged["checks"] = list(merged.get("checks", [])) + list(probed.get("checks", []))
                merged["surfaces"] = list(merged["surfaces"]) + probed["surfaces"]
                merged["launchable"] = (bool(merged.get("launchable"))
                                        and bool(probed.get("launchable")))
        fresh[str(token)] = merged or {"launchable": False, "checks": [], "surfaces": []}
    return fresh


def _run_live_probe_and_update(
    repo_root: Path,
    orchestration_id: str,
    cached_payload: dict[str, Any],
) -> None:
    """Run a live probe and, on success, update the probed_at of preflight.json.

    Raise a RuntimeError on failure (the caller transitions the orchestration to fail).
    """
    # `_preflight_launch_refusals` already refused this document unless its backend, so
    # normalised, names a required set; normalise the same way.
    backend = str(cached_payload.get("backend", "")).strip().lower()
    command = cached_payload.get("probe_command")
    probe_command = command.strip() if isinstance(command, str) and command.strip() else None

    live_probe = probe_execution_platform(
        backend=backend, agent_command=probe_command, repo_root=repo_root
    )
    # EVERY recorded provider, not just `defaults` (issue #28): the `probed_at` written below
    # is the freshness claim for the whole document, so re-probing one provider and refreshing
    # the timestamp would leave a mixed configuration's other providers treated as fresh
    # indefinitely — and `record-launch` would then create durable child state for a provider
    # that has since become unavailable, discovering it only when the spawn or request fails.
    # This runs on TTL EXPIRY, not per launch (`_live_preflight_mode` / `_live_preflight_ttl_seconds`),
    # so the cost is one probe per provider per TTL window.
    cached_providers = cached_payload.get("providers")
    fresh_providers: dict[str, Any] | None = None
    if isinstance(cached_providers, dict) and cached_providers:
        fresh_providers = _reprobe_recorded_providers(cached_providers, repo_root)
        live_probe["providers"] = fresh_providers
    refusals = _preflight_launch_refusals(live_probe)
    if refusals:
        raise RuntimeError(
            "live preflight gate failed: execution platform required launch capabilities are "
            "unavailable: " + "; ".join(refusals)
        )
    probed_at = live_probe.get("checked_at") or _utc_now_iso()
    _update_preflight_probed_at(repo_root, orchestration_id, probed_at,
                                providers=fresh_providers)


def _require_preflight_launchable(
    repo_root: Path,
    orchestration_id: str,
    *,
    enforce_live_probe: bool = True,
) -> dict[str, Any]:
    path = _preflight_path(repo_root, orchestration_id)
    if not path.exists():
        raise RuntimeError(f"preflight missing: {path}")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"preflight must be object: {path}")
    refusals = _preflight_launch_refusals(payload)
    if refusals:
        raise RuntimeError(
            "preflight gate failed: launchable preflight with required capabilities is required: "
            + "; ".join(refusals)
        )

    if not enforce_live_probe:
        return payload

    mode = _live_preflight_mode()

    if mode == "never":
        return payload

    if mode == "always":
        _run_live_probe_and_update(repo_root, orchestration_id, payload)
        return payload

    ttl_seconds = _live_preflight_ttl_seconds()
    probed_at = payload.get("probed_at")

    if isinstance(probed_at, str) and _is_within_preflight_ttl(probed_at, ttl_seconds):
        return payload

    _run_live_probe_and_update(repo_root, orchestration_id, payload)
    return payload


def _launch_refs(orchestration_id: str, agent_run_id: str) -> tuple[str, str]:
    prefix = f"workspace/orchestrations/{orchestration_id}/launches/{agent_run_id}"
    return f"{prefix}.request.json", f"{prefix}.response.json"


def _launch_dialog_refs(orchestration_id: str, agent_run_id: str) -> tuple[str, str]:
    prefix = f"workspace/orchestrations/{orchestration_id}/launches/{agent_run_id}"
    return f"{prefix}.prompt.txt", f"{prefix}.reply.txt"


def _agent_result_refs(orchestration_id: str, agent_run_id: str) -> tuple[str, str]:
    prefix = f"workspace/orchestrations/{orchestration_id}/agents/{agent_run_id}/dialogs/agent"
    return f"{prefix}.result.json", f"{prefix}.summary.txt"


def _coerce_launch_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, (bool, int, float)):
        return str(value)
    return None


def _coerce_nested_launch_text(payload: dict[str, Any], path: tuple[str, ...]) -> str | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _coerce_launch_text(current)


# The conductor-consumed launch-prompt templates: one plain-text file per template type
# under tools/prompt_templates/. These are the ONLY machine-parsed launch-prompt material.
# The human-facing reference lives in docs/workflow/LAUNCH_PROMPT_REFERENCE.md. The templates
# are host-rendered by record-launch, and the leaf never sees this directory — it is handed one
# rendered prompt and holds no read path of its own.
#
# The `step agent` / `substep agent` / `common boilerplate` trio that stood at the head of this
# table was the AGENTIC leaf's prompt, and went with it in Z4 (issue #171); the PURE leaf's
# `pure_*.txt` rows went with it in issue #534 PR-J4-b. Everything left is a WORKSHOP leaf
# prompt, plus the deterministic identity prompt, which is assembled in code rather than from a
# file.
_PROMPT_TEMPLATE_DIR = Path(__file__).resolve().parent / "prompt_templates"
_PROMPT_TEMPLATE_FILES = {
    # The `workshop leaf` (issue #534): the producer's rules and output contract, the documents
    # as seed files of its work tree, and the tree contract. Line 0 is WORKSHOP_PROMPT_SENTINEL.
    # A SHAPE-keyed name (`.harness`, issue #169) is the template of a request that declares
    # `pure_shape`.
    "workshop generate.generate": "workshop_generate_generate.txt",
    "workshop generate.generate.harness": "workshop_generate_generate_harness.txt",
    "workshop bundle repair": "workshop_bundle_repair.txt",
    # Step 2 of the same issue: the `compile.generate` producer authors the WHOLE IR in its tree,
    # and has its own repair turn.
    "workshop compile.generate": "workshop_compile_generate.txt",
    "workshop ir repair": "workshop_ir_repair.txt",
    # Step 3: the judging substeps as READING workshop leaves (adopted 2026-10-25; the third,
    # `validate.judge`, went with the substep in issue #724 PR-2). The reviewer's rules and
    # output contract verbatim, each document a seed file of its tree, and one output document
    # at the tree root. One repair turn for them.
    "workshop compile.verify": "workshop_compile_verify.txt",
    "workshop generate.verify": "workshop_generate_verify.txt",
    "workshop generate.verify.harness": "workshop_generate_verify_harness.txt",
    "workshop verdict repair": "workshop_verdict_repair.txt",
    # The escalate diagnostician as a READING workshop leaf (issue #534 PR-J4-a): one template,
    # four (step, substep) keys, because the pair is what the renderer looks up and the prompt
    # does not vary with the failed phase (the phase is a field of the diagnosis document, seeded
    # as `diagnosis.json`). One output document (`directive.json`), one launch, no repair turn.
    **{f"workshop {step}.{substep}": "workshop_escalate_diagnose.txt"
       for step, substep in sorted(DIAGNOSE_LAUNCH_PAIRS)},
}


@lru_cache(maxsize=1)
def _load_launch_prompt_templates() -> dict[str, str]:
    """Load every registered launch-prompt template from tools/prompt_templates/, verbatim.

    Keyed by `_PROMPT_TEMPLATE_FILES`; a registered file that is missing stops the read with
    its OSError, so a key and its file are added or removed in one change.
    """
    return {key: (_PROMPT_TEMPLATE_DIR / fname).read_text(encoding="utf-8")
            for key, fname in _PROMPT_TEMPLATE_FILES.items()}


def _build_dependency_surface_facts(surface: Any) -> str:
    """Render the L2 ``compile.generate`` dependency-surface catalog: for each direct dependency
    the consumer calls (a ``component`` or a ``problem``), its published operation NAMES and, under each resolved one, a fenced YAML block
    of its IR ``public_api`` ``module_parameters`` / ``interfaces`` / ``signatures`` (the keys the
    entry carries; conductor-resolved into ``<ir_ref>/dependency_surface.json`` at compile-phase
    start). The block is what lets a consumer's IR state a local procedure it passes to a
    dependency's procedure-typed argument by the prototype its spec delegates to (issue #486).
    This is the COMPILE view; at Generate a consumer is still shown the certified source
    interface (``_published_operations_lines``), because that is what Build stages and links.

    This is the authoring leaf's ONLY source of the real dependency op names. It must copy each
    listed name VERBATIM into that dependency's ``direct_deps[].operations``; a name NOT in this
    catalog is rejected by the deterministic L3 compile gate. For a dependency shown as
    ``unresolved`` (its surface could not be resolved), the leaf uses the operation_id(s) that
    dependency's controlled_spec §5 declares. Returns ``""`` when no surface was injected."""
    if not isinstance(surface, list) or not surface:
        return ""
    lines = [
        "**Published dependency operations (conductor-resolved from each direct dependency's "
        "certified public API, `component` or `problem` — the AUTHORITATIVE op-name catalog):** "
        "these are the ONLY valid operation names for your dependencies. Copy each listed name "
        "VERBATIM into that dependency's `dependency.direct_deps[].operations`; a name NOT in this "
        "catalog is rejected by a deterministic compile gate (never invent one). SEPARATELY, "
        "author your OWN `public_api.published_operations` by transcribing YOUR controlled_spec "
        "§5 operation_id(s) verbatim, AND its §5.1 signatures / module_parameters (a node pins "
        "its full ABI, not only names). For a "
        "dependency shown as `unresolved` below, use the operation_id(s) that dependency's "
        "controlled_spec §5 declares, verbatim. Under each resolved dependency is its published "
        "ABI, copied verbatim from its certified IR `public_api` (the §5.1 block that "
        "dependency's own Compile pinned): `signatures` (each published operation's argument "
        "list, in neutral form), `interfaces` (the named procedure prototypes an argument with "
        "`spec: {type: procedure, interface: <name>}` refers to) and `module_parameters` (the "
        "kinds those signatures name). Where YOUR controlled_spec passes a procedure of this "
        "node to such a procedure-typed argument, that prototype IS its argument contract: the "
        "IR step that defines the procedure states its arguments by the prototype (type, kind, "
        "rank, explicit shape, `intent`; the names are yours). Read them here; do not infer "
        "them from prose, and do not declare a `Compile fail` for their absence when they are "
        "listed here.",
    ]
    for entry in surface:
        if not isinstance(entry, dict):
            continue
        node_key = str(entry.get("node_key", "")).strip()
        if not node_key:
            continue
        source = str(entry.get("source", "")).strip() or "unknown"
        ops = entry.get("published_operations")
        names = (
            [str(o).strip() for o in ops if isinstance(o, str) and o.strip()]
            if isinstance(ops, list) else []
        )
        # The ABI block goes under every entry the reviewer's file carries it on — a resolved
        # entry with an empty name list included — so producer and reviewer read one snapshot.
        pa = entry.get("public_api") if source != "unresolved" else None
        block = (
            {k: pa[k] for k in _SURFACE_PUBLIC_API_KEYS if k in pa}
            if isinstance(pa, dict) else {})
        if source == "unresolved":
            lines.append(
                f"- {node_key}: (surface unresolved — use the operation_id(s) this dependency's "
                "controlled_spec §5 declares, verbatim)")
        elif not names:
            lines.append(
                f"- {node_key}: (publishes no operation names — use the operation_id(s) this "
                "dependency's controlled_spec §5 declares, verbatim"
                + ("; its ABI follows)" if block else ")"))
        else:
            lines.append(f"- {node_key}: {', '.join(names)} (source: {source})")
        if block:
            lines.append("  ```yaml")
            lines.extend(
                f"  {ln}" for ln in yaml.safe_dump(
                    block, sort_keys=False, allow_unicode=True).rstrip("\n").split("\n"))
            lines.append("  ```")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _build_dependency_facts(request_payload: dict[str, Any]) -> str:
    """Return a conductor-resolved "Dependency facts" block listing the published operations
    of each direct dependency.

    The conductor resolves these host-side (``_resolve_dependency_facts``) and stows the
    structured list on ``request_payload["resolved_dependencies"]``; this renderer only
    formats it. The refs that list also carries are the lineage record's: a workshop leaf cannot
    open a workspace path, and a past id is not an input (issue #574).

    For ``compile.generate`` the block is DIFFERENT: the consumer IR does not exist yet, so there
    is no ``resolved_dependencies``. Instead the conductor injects ``dependency_surface`` — the
    L2 published-surface catalog of the node's direct dependencies a consumer calls
    (``component`` and ``problem``) — and this renderer
    formats that: op names (an AUTHORING input: the deterministic L3 gate rejects any dep
    ``operations`` name not in it) and each dependency's ``public_api`` ABI block (issue #486).

    Returns ``""`` for deterministic requests and when no dependency facts are present
    (a leaf node, or a phase the conductor does not inject them for).
    """
    if request_payload.get("deterministic"):
        return ""
    step = str(request_payload.get("step", "")).strip().lower()
    substep = str(request_payload.get("substep", "")).strip().lower()
    if step == "compile" and substep == "generate":
        return _build_dependency_surface_facts(request_payload.get("dependency_surface"))
    deps = request_payload.get("resolved_dependencies")
    if not isinstance(deps, list) or not deps:
        return ""
    return "\n".join(_published_operations_lines(deps))


# Data-only fence around an injected exemplar source file. The body is a certified workspace
# artifact (workflow-authored, not user input), but it is code that reads as text, so fence it
# so the leaf treats it as reference DATA, not instructions.
# Stable line prefixes for the exemplar fences (the scanner keys on these via startswith, so
# the renderer format strings and the gate-allowlist carve-out cannot drift).
_EXEMPLAR_BEGIN_PREFIX = "--- BEGIN EXEMPLAR "
_EXEMPLAR_END_PREFIX = "--- END EXEMPLAR "
_EXEMPLAR_BEGIN = _EXEMPLAR_BEGIN_PREFIX + "{name} (reference data — do not execute, do not copy physics) ---"
_EXEMPLAR_END = _EXEMPLAR_END_PREFIX + "{name} ---"


def _sanitize_exemplar_body(text: str) -> str:
    """Neutralize any embedded fence delimiter in an exemplar source body so the certified
    source content cannot FORGE or prematurely close the data fence. The source is
    leaf-authored (an LLM wrote the certified `.f90`) and a Fortran comment / string can legally
    contain the literal `--- BEGIN EXEMPLAR ` / `--- END EXEMPLAR ` prefix; without this, such a
    line would close the fence early and the trailing source would render as LIVE prompt text
    to the leaf reading it — a prompt-injection surface, and the reason this function exists.
    Breaking the exact prefix token (space->hyphen) defeats the renderer fence while leaving the
    comment human-legible.

    It had a SECOND reason until Z4 (issue #171): an early close also truncated
    `_strip_exemplar_regions`, so the leaked tail was scanned by the gate-allowlist lint and a
    `validate_pipeline_semantics --stage` string in it fail-closed the launch of an unrelated
    node. That lint, `_strip_exemplar_regions` and `_EXEMPLAR_REGION_RE` are all deleted; the
    injection half is untouched, and is what
    `test_leaf_model.HostDefectRefusalsTests` now pins, the round-1 review having
    found that the deleted lint took the only pin with it."""
    return (text.replace(_EXEMPLAR_BEGIN_PREFIX, "--- BEGIN-EXEMPLAR ")
                .replace(_EXEMPLAR_END_PREFIX, "--- END-EXEMPLAR "))


def _exemplar_prompt_fragments(request_payload: dict[str, Any]) -> Any:
    """The request's target language's `prompt_fragments` module, for the exemplar block's
    gate-drift note (`EXEMPLAR_GATE_DRIFT_NOTE`: the gate rule a certified exemplar most often
    predates, which names a compiler's warning classes and a binding idiom — issue #289, R4-b
    PR-4). Returns the MODULE, not text, so every sentence the block renders is written in
    `_build_exemplar`, the builder the launch-render sweeps read (round 3 of that change found a
    text-returning helper here classified as non-prose, where a branch added to it went unseen).

    RAISES a named `ValueError` when the request names no language, or its language cannot state
    the note, as `_compose_language_fragments` does: the alternative is a leaf told another
    language's idiom."""
    language = str(request_payload.get("pure_language") or "").strip().lower()
    if not language:
        raise ValueError("an exemplar block is rendered for a request that names no "
                         "`pure_language`; the host must name the target language")
    try:
        module = backend_registry.capability_module("language", language, "prompt_fragments")
        module.EXEMPLAR_GATE_DRIFT_NOTE  # noqa: B018 - asked here so a gap is named, not raised later
    except (backend_registry.UnsupportedBackend, backend_registry.BackendNotExtracted,
            AttributeError) as exc:
        raise ValueError(
            f"the exemplar block cannot be composed for language {language!r}: {exc}") from None
    return module


def _build_exemplar(request_payload: dict[str, Any]) -> str:
    """R5: render a conductor-injected "Certified exemplar" block — a previously-certified
    SIBLING node's source (model + runner) resolved host-side by ``_resolve_exemplar_source``
    and stowed on ``request_payload["exemplar"]``. Rendered only for ``generate.generate``
    (the sole authoring leaf); this renderer just formats.

    PRIOR ART, NOT this node's spec: the exemplar is a known-good structural reference for the
    cross-node-identical plumbing (runner argv/case loop/JSON emission), which the leaf would
    otherwise re-derive from scratch. The leaf authors THIS node's physics/logic from ITS spec —
    the exemplar's physics must not be copied. Returns ``""`` for deterministic requests, a
    non-generate.generate step, or when no exemplar was injected."""
    if request_payload.get("deterministic"):
        return ""
    if (str(request_payload.get("step", "")).strip().lower() != "generate"
            or str(request_payload.get("substep", "")).strip().lower() != "generate"):
        return ""
    exemplar = request_payload.get("exemplar")
    if not isinstance(exemplar, dict):
        return ""
    sources = exemplar.get("sources")
    if not isinstance(sources, list) or not sources:
        return ""
    node_key = str(exemplar.get("node_key", "")).strip()
    lines = [
        "**Certified exemplar (conductor-injected PRIOR ART — a previously-certified sibling "
        f"node in the same family/kind: {node_key}):** use it as a STRUCTURAL reference for the "
        "runner plumbing and code/layout conventions this workflow expects (argv/`--cases` "
        "parsing, the case loop, JSON/snapshot/perf emission). Author THIS node's own physics "
        "and per-test logic from ITS `spec.ir.yaml` (the algorithm + `io_contract` "
        "sections) and `tests.md` — do NOT "
        "copy the exemplar's physics or checks. It is orientation, never a gate and never this "
        "node's spec. It was certified under the gates in force AT ITS TIME, so it may predate "
        "a rule now in your contracts: where the exemplar and a contract disagree, the contract "
        "wins. " + str(_exemplar_prompt_fragments(request_payload).EXEMPLAR_GATE_DRIFT_NOTE),
    ]
    for src in sources:
        if not isinstance(src, dict):
            continue
        name = str(src.get("filename", "")).strip()
        text = src.get("text")
        if not name or not isinstance(text, str):
            continue
        lines.append("")
        lines.append(_EXEMPLAR_BEGIN.format(name=name))
        lines.append(_sanitize_exemplar_body(text).rstrip("\n"))
        lines.append(_EXEMPLAR_END.format(name=name))
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _dependency_signatures(deps: list[Any]) -> Any:
    """The `signatures` module of the language the resolved interface facts in `deps` were read
    in (`interface_language`, stamped by `_resolve_dependency_facts`), or None when no dependency
    carries any. The call-site guidance around those facts is that language's
    (issue #289, R4-b PR-4), so it is asked here rather than spelled.

    RAISES a named `ValueError` when facts are present and their language is absent, several,
    or has no `signatures` backend: the alternative is a leaf shown another language's
    guidance, which is the defect this resolves. `_compose_language_fragments` refuses the same
    way, and a launch whose prompt cannot be rendered is refused, not sent."""
    carriers = [dep for dep in deps if isinstance(dep, dict)
                and str(dep.get("node_key", "")).strip()
                and (dep.get("published_operations") or dep.get("declared_operations_unresolved"))]
    if not carriers:
        return None
    languages = {str(dep.get("interface_language") or "").strip() for dep in carriers}
    if len(languages) != 1 or "" in languages:
        cause = ("some carry none" if "" in languages
                 else "they were read in different languages")
        raise ValueError(
            "resolved dependency facts carry published-interface facts without one "
            f"`interface_language` to render them in ({cause}: got {sorted(languages)}); "
            "`_resolve_dependency_facts` stamps its consumer's language on every fact it reads "
            "an interface for, so one resolution names exactly one")
    (language,) = languages
    try:
        return backend_registry.capability_module("language", language, "signatures")
    except (backend_registry.UnsupportedBackend, backend_registry.BackendNotExtracted) as exc:
        raise ValueError(
            f"resolved dependency facts were read in language {language!r}, which cannot "
            f"render them: {exc}") from None


def _published_operations_lines(deps: list[Any]) -> list[str]:
    """Render the conductor-resolved published-operation interfaces (argument order) for
    each direct dependency, or ``[]`` when none were resolved.

    The argument order is a correctness input to AUTHORING — the consumer's Generate must call
    each op with exactly this positional order or the build fails on a type/rank mismatch and
    is routed back to Generate. It is conductor-resolved orientation, never itself a gate.
    """
    signatures = _dependency_signatures(deps)
    rows: list[str] = []
    any_detail = False
    any_procedure_arg = False  # the header's procedure sentence keys on the ARGUMENT, not on
    #                            whether its prototype could be read (round 2 of issue #266)
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        node_key = str(dep.get("node_key", "")).strip()
        if not node_key:
            continue
        # L4 WARNING: an IR-declared operation name that does not exist in the certified
        # source was dropped (the resolver replaced the enumeration with the real surface).
        # Surface it so the pure repair leaf — re-injected the same stale facts — stops
        # authoring the invented `call` the syntax/static gate pincer rejects every retry.
        # Emitted BEFORE the pub-op guard so it survives even a degenerate empty surface.
        unresolved = dep.get("declared_operations_unresolved")
        if isinstance(unresolved, list):
            bad = [str(u).strip() for u in unresolved if isinstance(u, str) and u.strip()]
            if bad:
                rows.append(
                    f"- {node_key} :: WARNING — the IR-declared operation name(s) "
                    f"{', '.join(bad)} do NOT exist in this dependency's certified source and "
                    "were dropped. Do NOT call them. The operations listed for this dependency "
                    "are the ONLY real public surface; call those instead."
                )
        pub_ops = dep.get("published_operations")
        if not isinstance(pub_ops, list) or not pub_ops:
            continue
        for op in pub_ops:
            if not isinstance(op, dict):
                continue
            iface = str(op.get("interface", "")).strip()
            if not iface:
                continue
            rows.append(f"- {node_key} :: {iface}")
            prototypes = op.get("procedure_interfaces")
            detail = _argument_detail_lines(
                op.get("arguments"), signatures,
                carried_prototypes=frozenset(prototypes) if isinstance(prototypes, dict) else frozenset())
            if detail:
                any_detail = True
                rows.extend(detail)
                if any(signatures.procedure_interface(a) for a in op.get("arguments")
                       if isinstance(a, dict)):
                    any_procedure_arg = True
            # The prototype each procedure-typed argument references (issue #266), verbatim
            # from the dependency's certified source, so the consumer's leaf writes a procedure
            # of exactly that shape rather than inferring one from the dummy's name.
            if isinstance(prototypes, dict):
                for proto_name, proto_lines in prototypes.items():
                    spelled = [str(x).strip() for x in proto_lines
                               if isinstance(x, str) and x.strip()] \
                        if isinstance(proto_lines, list) else []
                    if not isinstance(proto_name, str) or not proto_name.strip() or not spelled:
                        continue
                    rows.append(signatures.prototype_heading(proto_name.strip()))
                    rows.extend(f"      {line}" for line in spelled)
    if not rows:
        return []
    # The rank/shape guidance is only added when per-argument detail lines are actually
    # rendered — otherwise (older lineage without `arguments`, or all-unresolved ops) the
    # header would promise a per-argument list that does not follow. The paragraph itself names
    # the language's argument passing, so the language states it.
    header = signatures.dependency_operations_header(
        detailed=any_detail, procedure_argument=any_procedure_arg)
    return [header, *rows]


def _argument_detail_lines(
    arguments: Any, signatures: Any, *, carried_prototypes: frozenset[str] = frozenset()
) -> list[str]:
    """The indented per-dummy-argument lines of one published operation, in the words of the
    language the facts were read in (`signatures.argument_detail_lines`), or ``[]``. Kept as
    this module's function so the launch-render sweep (`test_leaf_wiring`) wraps it as the
    prose builder it is; the prose is the backend's since issue #289 (R4-b PR-4)."""
    if signatures is None:
        return []
    return list(signatures.argument_detail_lines(arguments, carried_prototypes=carried_prototypes))


DETERMINISTIC_PROMPT_SENTINEL = "Conductor-executed deterministic step"


def _render_deterministic_launch_prompt(request_payload: dict[str, Any]) -> str:
    """Minimal launch prompt for a step the conductor runs IN-PROCESS (Build /
    Validate.execute). No leaf agent reads it; it exists only so the launch bookkeeping
    (capability / phase_state / agent_runs) is well-formed. It carries no skill section
    and no leaf instructions — only the sentinel the validator keys on plus the core
    identifiers."""
    p = request_payload
    lines = [
        f"{DETERMINISTIC_PROMPT_SENTINEL} (no leaf agent runs this body; the conductor "
        "runs it in-process).",
        f"Target node_key: {p.get('node_key', '')}",
        f"Target step: {p.get('step', '')}",
    ]
    if str(p.get("substep", "")).strip():
        lines.append(f"Target substep: {p.get('substep')}")
    lines += [
        f"orchestration_id: {p.get('orchestration_id', '')}",
        f"agent_run_id: {p.get('agent_run_id', '')}",
        f"parent_agent_run_id: {p.get('parent_agent_run_id', '')}",
        f"ir_ref: {p.get('ir_ref', '')}",
        f"pipeline_ref: {p.get('pipeline_ref', '')}",
    ]
    return "\n".join(lines) + "\n"


# The five `SLIM_REPAIR_*` literals — the sentinel, the findings header, the untrusted-excerpt
# warning and its BEGIN/END fence — stood here until Z4 (issue #171). They belonged to the
# warm-resume slim repair prompt, which only an AGENTIC leaf could receive, and nothing in this
# module renders one now. `tools/validate_pipeline_semantics.py` kept copies of two of them to
# classify pre-cut records until issue #643 removed the launch-prompt marker audit.


# --------------------------------------------------------------------------------------
# Z2 pure-function leaf (host-mediated closed context). Claude uses `claude -p` with tools
# disabled; Codex runs inside a read-only sandbox. A pure leaf receives a
# fully closed context and returns ONE JSON document; the host validates and writes it. Its launch
# prompt has no skill section, no gate runbook, or write-authorization constraints — the leaf has
# no repository write authority to constrain — so it needs its own renderer, marker set, and
# record-launch branch, all keyed off `leaf_mode == "pure"`.
# --------------------------------------------------------------------------------------

# Migrated (step, substep) pairs a pure leaf may serve, and the required `pure_context` keys the
# host inlines for each. A pure request outside this map is rejected at validation
# (`_validate_launch_request_payload`); a missing context key on a cold launch is rejected too.
PURE_CONTEXT_REQUIRED_KEYS: dict[tuple[str, str], tuple[str, ...]] = {
    ("compile", "generate"): ("controlled_spec_document", "tests_document", "deps_document",
                              "profile_spec_document", "dependency_graph_document",
                              "phase_contract_document", "ir_algorithm_example_document",
                              "ir_algorithm_2d_example_document"),
    ("compile", "verify"): ("controlled_spec_document", "tests_document", "deps_document",
                            "ir_document", "dependency_surface_document",
                            "phase_contract_document", "ir_algorithm_example_document",
                            "ir_algorithm_2d_example_document"),
    ("generate", "generate"): ("harness_capabilities", "target_profile",
                               "ir_document", "controlled_spec_document",
                               "tests_document", "runner_document"),
    ("generate", "verify"): ("controlled_spec_document", "tests_document",
                             "target_profile", "severity_rubric_document",
                             "bundle_document"),
    # The escalate diagnostician, one pair per escalatable phase. Its whole closed context is
    # the failure-artifact document the host composes (`_diagnosis_document`); the persona, the
    # directive schema and the decision criteria are static template body.
    **{pair: ("diagnosis_document",) for pair in sorted(DIAGNOSE_LAUNCH_PAIRS)},
}

#: The non-default SHAPES a launch request may declare (`pure_shape`). The default shape
#: names itself with the empty string and uses the tables keyed by `(step, substep)` above; a
#: request that declares one of these uses the by-shape tables instead. A closed vocabulary: an
#: unknown value is refused rather than falling back to the default, because the shape decides
#: which template the leaf is sent and which context keys it must carry.
#: Kept equal to the non-default values of `codegen_bundle.BUNDLE_SHAPES` — the acceptance
#: layer's vocabulary — by `test_leaf_wiring`. (`verification`, the `compile.generate` part
#: of issue #526, went with the pure part split in issue #534 PR-J4-b.)
PURE_LAUNCH_SHAPES: frozenset[str] = frozenset({"harness"})

#: Required `pure_context` keys for a `(step, substep, pure_shape)` that declares a shape. The
#: pairs listed here are exactly the pairs a shape may be declared on; a `pure_shape` on any
#: other pair is refused.
PURE_CONTEXT_REQUIRED_KEYS_BY_SHAPE: dict[tuple[str, str, str], tuple[str, ...]] = {
    # The harness self-test producer. Same four host-resolved documents as the default generate
    # producer, with the runner-output contract in place of the host-rendered runner: on this
    # shape there is no host-rendered runner, and the leaf authors the executable entry itself.
    # `gate_guards_document` is the `Legality and gate guards` section of the target language's
    # generate rules (`workflow_conductor._gate_guards_section`) — the deterministic gate's rule
    # set for every leaf-authored source. The AGENTIC leaf force-read it; a pure leaf force-reads
    # nothing, so it is inlined here or the leaf is held to rules no document it receives states.
    # `controlled_spec_sections_document` is §1 to §4 and §6 of the node's own `controlled_spec.md`
    # (`workflow_conductor._harness_controlled_spec_sections`, issue #644 P4b).
    ("generate", "generate", "harness"): ("harness_capabilities", "target_profile",
                                          "ir_document", "tests_document",
                                          "controlled_spec_sections_document",
                                          "runner_output_contract_document",
                                          "gate_guards_document",
                                          "lint_rules_document"),
    # Its reviewer: the default generate reviewer's documents and the runner-output contract (a
    # harness bundle carries the executable entry itself).
    ("generate", "verify", "harness"): ("controlled_spec_document", "tests_document",
                                        "ir_document", "target_profile",
                                        "runner_output_contract_document",
                                        "severity_rubric_document", "bundle_document"),
}


def _sanitize_pure_doc_body(text: str) -> str:
    """Neutralize any embedded pure-doc fence marker in an inlined document body so an
    untrusted document (tests.md, the IR, the bundle under review) cannot FORGE or prematurely
    close the data fence. Mirrors `_sanitize_exemplar_body`: an early close renders the document
    tail as live prompt text, which is an injection surface on the one span of a pure prompt the
    host does not author. (It had the same second reason as that function — truncating the
    gate-allowlist scan carve-out — and that lint is deleted in Z4, issue #171.) Breaking the
    exact marker token leaves the body human-legible."""
    return (text.replace(PURE_DOC_FENCE_BEGIN, PURE_DOC_FENCE_BEGIN.replace("BEGIN", "BEGIN-"))
                .replace(PURE_DOC_FENCE_END, PURE_DOC_FENCE_END.replace("END", "END-")))


def _fence_pure_doc(text: str) -> str:
    """Wrap an inlined pure-context document in the data-only fence (sanitized body). The fence
    tells the leaf that everything between the markers is DATA — the one span of its prompt the
    host did not author — and the sanitized body is what keeps the document from closing it
    early. (The fenced region was also the gate-allowlist scan's carve-out, which is what
    `_gate_allowlist_scan_text` removed before linting; that lint is deleted in Z4, issue #171.)"""
    return "\n".join([PURE_DOC_FENCE_BEGIN, _sanitize_pure_doc_body(text).rstrip("\n"),
                      PURE_DOC_FENCE_END])


_LEAF_PLACEHOLDER_RE = re.compile(r"<(\w+)>")


def _substitute_leaf_placeholders(template: str, subs: dict[str, str]) -> str:
    """Replace each `<key>` placeholder in `template` EXACTLY ONCE, in a single pass over the
    template, using `subs`.

    A single-pass `re.sub` — NOT a `for key: rendered.replace(...)` loop — because the
    substituted values include untrusted inlined document bodies (the IR, tests.md, the bundle
    under verify review) that can legitimately contain a literal `<step>` / `<ir_document>`
    token. A replace-loop re-scans already-inserted text and would corrupt such a token (splice
    an id into the data, or leave an unprocessed `<key>` behind depending on key order); the
    single pass leaves inserted content untouched. An unknown `<key>` (not in `subs`) is left
    verbatim, so a template typo surfaces rather than silently vanishing."""
    return _LEAF_PLACEHOLDER_RE.sub(
        lambda m: subs.get(m.group(1), m.group(0)), template)


def _leaf_launch_template_name(request_payload: dict[str, Any]) -> str:
    """The template key for a workshop launch: `workshop <step>.<substep>`, with the declared
    `pure_shape` appended when the request carries one. A name with no template (an unknown
    shape, or a shape on a pair that has none) is refused by `_render_leaf_launch_prompt`. The
    `pure <step>.<substep>` family went with the pure leaf in issue #534 PR-J4-b."""
    step = str(request_payload.get("step", "")).strip().lower()
    substep = str(request_payload.get("substep", "")).strip().lower()
    shape = str(request_payload.get("pure_shape", "")).strip().lower()
    return f"workshop {step}.{substep}.{shape}" if shape else f"workshop {step}.{substep}"


def _fragment_template_stem(template_file: str) -> str:
    """The stem a backend's prompt fragments are keyed by: the template file without its family
    prefix and suffix — `workshop_generate_generate.txt` asks for `generate_generate`. (The
    pure templates, which shared those fragments, went in issue #534 PR-J4-b.)"""
    return template_file.removesuffix(".txt").removeprefix("workshop_")


#: A neutral template's marker for a place the target LANGUAGE's rules go (issue #289, R4-b
#: PR-2). Composed away before any `<key>` substitution, so a composed template is exactly the
#: text the leaf reads and the cold repair's re-statement of the body (`_leaf_launch_body`)
#: sees it too. A distinct spelling from `<key>` on purpose: a marker left in a rendered prompt
#: is a composer defect, and it must not be mistaken for an unfilled data slot.
_LANGUAGE_FRAGMENT_RE = re.compile(r"\{\{language:([a-z0-9_]+)\}\}")


def _compose_language_fragments(template: str, template_file: str, language: Any) -> str:
    """`template` with every `{{language:<name>}}` marker replaced by the target language's
    fragment of that name (`prompt_fragments`, asked of the registry). A template with no marker
    is returned unchanged and asks nothing.

    Every failure RAISES a named `ValueError`: no language on the request, a language that does
    not declare `prompt_fragments`, a marker its fragments do not define. The alternative to a
    refusal is a prompt that silently omits the rules the gates hold the leaf to — or carries
    another language's."""
    markers = set(_LANGUAGE_FRAGMENT_RE.findall(template))
    if not markers:
        return template
    lang = str(language or "").strip().lower()
    if not lang:
        raise ValueError(
            f"pure launch request carries no `pure_language`, and its template {template_file} "
            f"holds language fragments ({', '.join(sorted(markers))}) — the host must name the "
            f"target language the prompt is composed for")
    try:
        module = backend_registry.capability_module("language", lang, "prompt_fragments")
        sections = module.fragments(_fragment_template_stem(template_file))
    except (backend_registry.UnsupportedBackend, backend_registry.BackendNotExtracted,
            ValueError) as exc:
        raise ValueError(
            f"pure launch prompt {template_file} cannot be composed for language {lang!r}: "
            f"{exc}") from None
    missing = sorted(markers - set(sections))
    if missing:
        raise ValueError(
            f"pure launch prompt {template_file}: language {lang!r} defines no fragment for "
            f"{', '.join(missing)}")
    return _LANGUAGE_FRAGMENT_RE.sub(lambda m: sections[m.group(1)], template)


#: A neutral template's marker for a place the target's PARALLEL MODEL's rules go (issue #316,
#: R4-c PR-2). Composed after the language's markers, and for the same reasons.
_PARALLEL_FRAGMENT_RE = re.compile(r"\{\{parallel:([a-z0-9_]+)\}\}")


def _compose_parallel_fragments(template: str, template_file: str, parallel: Any) -> str:
    """`template` with every `{{parallel:<name>}}` marker replaced by the target parallel
    backend's fragment of that name (`prompt_fragments` on the `parallel` axis). A template with
    no marker is returned unchanged and asks nothing.

    A backend that does not declare `prompt_fragments` — `none`, and a model whose rules are not
    (yet) in its own fragments — composes every marker to the EMPTY string. That is the one difference from
    `_compose_language_fragments`, and it is deliberate: a parallel model's text is an ADDED
    rule, stated for the model whose gate enforces it, where a language's text is the rules the
    gates hold every node to, which a prompt without them contradicts. So "declares nothing"
    here means "says nothing", and is not a refusal.

    Every other failure RAISES a named `ValueError`: no parallel backend on the request, a value
    the registry has no record for, a backend that declares `prompt_fragments` and defines no
    fragment for a marker (or has no fragment file for the template). The alternative to those
    refusals is a prompt composed for a model the host did not name."""
    markers = set(_PARALLEL_FRAGMENT_RE.findall(template))
    if not markers:
        return template
    backend = str(parallel or "").strip().lower()
    if not backend:
        raise ValueError(
            f"pure launch request carries no `pure_parallel`, and its template {template_file} "
            f"holds parallel fragments ({', '.join(sorted(markers))}) — the host must name the "
            f"target parallel backend the prompt is composed for")
    # `unimplemented_reason`, not `unsupported_reason`: the axis is open-vocabulary, so a token
    # with no record is "accepted" by membership, and composing it to nothing would say nothing
    # for a model nobody has described.
    reason = backend_registry.unimplemented_reason("parallel", backend)
    if reason is not None:
        raise ValueError(
            f"pure launch prompt {template_file} cannot be composed for parallel backend "
            f"{backend!r}: {reason}")
    owners = backend_registry.package_members("parallel", backend, "prompt_fragments")
    if not owners:
        return _PARALLEL_FRAGMENT_RE.sub("", template)
    stem = _fragment_template_stem(template_file)
    member_sections: list[dict[str, str]] = []
    for member in owners:
        try:
            module = backend_registry.capability_module("parallel", member, "prompt_fragments")
            sections = module.fragments(stem)
        except (backend_registry.UnsupportedBackend, backend_registry.BackendNotExtracted,
                ValueError) as exc:
            raise ValueError(
                f"pure launch prompt {template_file} cannot be composed for parallel backend "
                f"{member!r}: {exc}") from None
        missing = sorted(markers - set(sections))
        if missing:
            raise ValueError(
                f"pure launch prompt {template_file}: parallel backend {member!r} defines no "
                f"fragment for {', '.join(missing)}")
        member_sections.append(sections)
    members = backend_registry.members("parallel", backend)
    lead: dict[str, str] = {}
    if len(members) > 1:
        lead = _parallel_combination_lead(stem, backend, members)
        missing = sorted(markers - set(lead))
        if missing:
            # Refused, not skipped: an empty lead would otherwise compose the members' paragraphs
            # alone — each telling the producer to declare its own model — and a lead naming
            # another section would surface as a KeyError the caller reports as a missing
            # template.
            raise ValueError(
                f"pure launch prompt {template_file}: the combination lead defines no fragment "
                f"for {', '.join(missing)}")
    return _PARALLEL_FRAGMENT_RE.sub(
        lambda m: "\n\n".join([*([lead[m.group(1)]] if lead else []),
                                *(sections[m.group(1)] for sections in member_sections)]),
        template)


#: Where the lead a combination's members' fragments are composed under lives (issue #700): one
#: file per neutral template stem, in the fragment format, named as a member's fragment file is
#: so that a reader's digest attributes it the same way. The text is the `parallel` axis's, not a
#: backend's, so it is outside `backends/` (`docs/BACKEND_BOUNDARY.md`).
_PARALLEL_COMBINATION_LEAD_DIR = Path(__file__).resolve().parent / "prompt_templates" / \
    "parallel_combination"


def _parallel_combination_lead(stem: str, value: str, members: tuple[str, ...]) -> dict[str, str]:
    """The lead sections for template `stem` on a target whose parallel value `value` combines
    `members`, with the file's `{{value}}` and `{{members}}` filled. A missing file raises: the
    composer asks only for a template with a parallel marker, on a combination."""
    path = _PARALLEL_COMBINATION_LEAD_DIR / f"{stem}.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"no parallel combination lead for {stem!r}: {path} ({exc})") from exc
    named = ", ".join(f"`{m}`" for m in members)
    return {name: body.replace("{{value}}", value).replace("{{members}}", named)
            for name, body in parse_fragments(text, source=str(path)).items()}


def _compose_fragments(template: str, template_file: str, request_payload: dict[str, Any]) -> str:
    """`template` composed for the request's target: its language's fragments, then its parallel
    backend's."""
    return _compose_parallel_fragments(
        _compose_language_fragments(template, template_file, request_payload.get("pure_language")),
        template_file, request_payload.get("pure_parallel"))


def _leaf_launch_template(request_payload: dict[str, Any]) -> str:
    """The launch template this pure request renders, composed for its `pure_language` and
    `pure_parallel`.

    The ONE place a pure launch template is read by name, so the cold launch and the cold
    repair's re-statement of its body (`_leaf_launch_body`) see the same composed text. `KeyError`
    when no template matches the request (each caller decides what that means)."""
    name = _leaf_launch_template_name(request_payload)
    template = _load_launch_prompt_templates()[name]
    return _compose_fragments(template, _PROMPT_TEMPLATE_FILES[name], request_payload)


def _render_leaf_launch_prompt(request_payload: dict[str, Any]) -> str:
    """Render a cold workshop launch prompt from its `workshop_{step}_{substep}.txt` template.

    Byte-stable order (static persona/output-contract prefix first, variable ids last): the
    template holds the static body and `<key>` tokens; this fills the `<tool_surface>` row of
    the request's provider and the identity block. The documents are NOT inlined: they are seed
    files of the lineage's tree (`Conductor._workshop_seeds`), and no workshop template has a
    document slot. (The pure leaf's renderer inlined each `pure_context` value fenced as data,
    and the dependency facts and exemplar beside them; that went in issue #534 PR-J4-b.)"""
    name = _leaf_launch_template_name(request_payload)
    try:
        template = _leaf_launch_template(request_payload)
    except KeyError:
        # A NAMED refusal, not a KeyError. `prepare_launch_request_payload` force-renders a
        # pure request BEFORE `_validate_launch_request_payload` runs, so a payload missing
        # `step` (or naming a pair with no template) reaches here first and used to escape as
        # an unnamed KeyError naming a half-built template key — hiding the field the caller
        # actually has to fix. Until Z4 (issue #171) it did not: such a payload fell to the
        # agentic renderer, which tolerated it, and the named refusal arrived from the
        # validator a few lines later. Deleting that renderer moved the failure here, so the
        # naming moves with it.
        raise ValueError(
            f"launch request names no rendered prompt: no template for {name!r} "
            f"(step={request_payload.get('step')!r} substep={request_payload.get('substep')!r} "
            f"pure_shape={request_payload.get('pure_shape')!r})") from None
    subs: dict[str, str] = {}
    subs["node_key"] = str(request_payload.get("node_key", ""))
    subs["step"] = str(request_payload.get("step", ""))
    subs["substep"] = str(request_payload.get("substep", ""))
    subs["orchestration_id"] = str(request_payload.get("orchestration_id", ""))
    subs["agent_run_id"] = str(request_payload.get("agent_run_id", ""))
    subs["prompt_contract_version"] = str(request_payload.get("prompt_contract_version", ""))
    # The one sentence of a workshop contract that differs by transport: what the leaf holds
    # besides the wrapper (issue #534 step 2). A NAMED refusal for a provider with no row, for
    # the reason a missing template is one above.
    provider = request_payload.get("workshop_provider")
    surface = _workshop_tool_surface_table(request_payload)
    if not (isinstance(provider, str) and provider in surface):
        raise ValueError(
            f"workshop launch request must name its provider in workshop_provider, one of "
            f"{', '.join(sorted(surface))}; got {provider!r}")
    subs["tool_surface"] = surface[provider]
    return _substitute_leaf_placeholders(template, subs)


# The pure pairs whose leaf returns a VERDICT — a conclusion about someone else's artifact —
# rather than the artifact itself. Only these render `<repair_scope>` (issue #209 round 5).
#
# Why the paragraph is conditional rather than shared: for these two, EVERY repairable class is
# a violation of the verdict document's SHAPE, and the schema's joint invariant couples the
# conclusion to the evidence (`verification_status` / `decision` against findings, severity and
# reason). That makes "flip the conclusion" a one-token correction of a shape violation, and an
# accepted one — a `leaf shortcut` whose outcome is a wrong certification. The PRODUCER's repair
# is about content (an outer reopen carries a deterministic gate's finding about the document
# itself), so the same paragraph would be false for it: a producer IS being told to change what
# its document says. A first attempt at one persona-neutral sentence was measured to make two
# reviewer routes WORSE than saying nothing, which is why this is a slot.
PURE_VERDICT_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("generate", "verify"), ("compile", "verify"),
})

#: The `<repair_scope>` text. States the invariant in BOTH directions, because either side of it
#: can be the half that is wrong and only the leaf knows which: a `fail` that lost its evidence is
#: repaired by supplying the evidence, and a `pass` that carries a finding is repaired by becoming
#: a `fail`. What is forbidden is neither edit but the DISHONEST one — dropping a finding that was
#: made, or recording a conclusion that was not reached.
PURE_REPAIR_SCOPE_PARAGRAPH = (
    "These violations are about the SHAPE of your verdict document, not about your review. "
    "Repair it so that it states COMPLETELY what your review actually found: a `fail` carries its "
    "findings, its severity and its reason; a `pass` carries none of them. Either half may be the "
    "one to change — supply the evidence a `fail` is missing, or record the `fail` that the "
    "evidence you already listed calls for. What you must NEVER do is take whichever edit is "
    "smaller: do not drop a finding you made, and do not record a conclusion you did not reach."
)

#: The `repair_reason` of a cut-read repair turn (`workflow_conductor.WORKSHOP_CUT_READ_REPAIR_REASON`,
#: issue #547), which selects `WORKSHOP_CUT_READ_SCOPE_PARAGRAPH` in place of the shape paragraph.
WORKSHOP_CUT_READ_REPAIR_REASON = "workshop_cut_read_repair"

#: The verdict repair template's inline instruction slot, `<repair_instruction>`, by refusal
#: kind. The SHAPE text is the template's own before issue #547, so a schema repair renders as
#: it did (the adopted claude A/B ran on it); the cut-read text says what a cold re-review from
#: the seeds is, where the shape text's "rewriting your document file in place" would point at
#: a document that is not there. One slot, inside the first line: the template's other lines
#: stay literal, which the host-line sweep of `test_leaf_wiring` subtracts by.
WORKSHOP_VERDICT_REPAIR_SHAPE_SLOTS: dict[str, str] = {
    "repair_instruction": (
        "Correct EXACTLY the violations described below by rewriting your document file at "
        "the tree root in place — the WHOLE document, not a diff or a fragment — and end your "
        "turn. If this is a new session, the whole contract follows the findings, and your "
        "previous tree's document file is already in place in a fresh tree beside fresh seeds "
        "when the host still holds it: correct that file, or write the whole document anew "
        "when there is none."),
}
WORKSHOP_VERDICT_REPAIR_CUT_READ_SLOTS: dict[str, str] = {
    "repair_instruction": (
        "Review again as the paragraph just before the findings says, write your document "
        "file at the tree root — the WHOLE document, not a diff or a fragment — and end your "
        "turn. This is a new session, in a fresh tree beside fresh seeds, and the whole "
        "contract follows the findings."),
}

#: The `<repair_scope>` text of a cut-read repair turn: the refusal is about the leaf's READING,
#: and the shape paragraph above would tell it the opposite. Part of the workshop contract digest
#: (`tools/tests/test_workshop_producer.py`).
WORKSHOP_CUT_READ_SCOPE_PARAGRAPH = (
    "This refusal is about READING, not the shape of a document. In a previous session of this "
    "review, each read listed below printed more than one output can carry, reached that "
    "session cut, or was delegated to a sub-agent, so rows of what you judge were never shown "
    "to it, and the host set aside the document it wrote. This is a new session in a fresh "
    "tree, and no document of that session is in it: review again from the seeds, keeping "
    "every output under the budget — everything one command prints is one output, however "
    "many files it reads or programs it chains, and so is everything one `exec` prints: read "
    "one file per command through the host's `python3 read.py <file>`, each in an `exec` of "
    "its own, and a script's result in pieces, one `exec` each — and reading for yourself, "
    "and write the whole document from what this session sees. A session in which any output "
    "exceeds the budget, reaches you cut, or is a sub-agent's comes back the same way."
)


def _leaf_launch_body(request_payload: dict[str, Any]) -> str:
    """The launch prompt this request would have been sent cold, minus its persona line and its
    identity block: everything between them, in template order, with every document slot
    filled under the label paragraph the launch template gives it. A cold repair re-states the
    launch prompt whole rather than lifting paragraphs from it by prefix (issue #510). The slice
    is byte-for-byte the cold launch's text — an empty slot's blank lines included — so a
    document body reaches the repair exactly as it reaches the launch. It is rendered WITHOUT
    the certified exemplar: a repair turn corrects the leaf's own document and carries no prior
    art, so the renderer drops a stray `exemplar` here rather than relying on the conductor
    never to attach one."""
    full = _render_leaf_launch_prompt(
        {k: v for k, v in request_payload.items() if k != "exemplar"})
    body = full.split("\n\n", 1)[1] if "\n\n" in full else ""
    cut = body.rfind("\n\nTarget node_key: ")
    if cut != -1:
        body = body[:cut]
    return body.strip()


def _workshop_tool_surface_table(request_payload: dict[str, Any]) -> dict[str, str]:
    """The `<tool_surface>` table a workshop request is rendered and validated against, by the
    KIND its pair is: a judging pair's reading lineage holds no wrapper
    (`WORKSHOP_READER_TOOL_SURFACE_TEXT`), a producing pair's does (`WORKSHOP_TOOL_SURFACE_TEXT`).
    Both are keyed by provider."""
    if _leaf_contract_is_workshop_reading_pair(request_payload.get("step"),
                                           request_payload.get("substep")):
        return WORKSHOP_READER_TOOL_SURFACE_TEXT
    return WORKSHOP_TOOL_SURFACE_TEXT


def _leaf_repair_template_name(request_payload: dict[str, Any]) -> str:
    """The template key of a host-rendered repair turn. A producer's repair turn is its
    substep's — the IR's on `compile`, the bundle's otherwise (issue #534 step 2), and a
    REVIEWER's is the verdict repair whatever its step (step 3)."""
    if _leaf_contract_is_workshop_reading_pair(request_payload.get("step"),
                                           request_payload.get("substep")):
        return "workshop verdict repair"
    if str(request_payload.get("step", "")).strip().lower() == "compile":
        return "workshop ir repair"
    return "workshop bundle repair"


def _render_leaf_repair_prompt(request_payload: dict[str, Any]) -> str:
    """Render a bounded repair turn (`workshop_{bundle,ir,verdict}_repair.txt`).

    The findings excerpt is wrapped in the data-only fence. On a WARM repair the resumed
    lineage already holds the launch prompt and its document is in the tree, so `<launch_body>`
    is empty. On a COLD repair (a fresh lineage seeded from the prior one's snapshot, the
    document under repair among its files) the repair re-states the launch prompt's whole body
    (`_leaf_launch_body`; issue #510) beside the `<tool_surface>` row. (The pure leaf's cold
    repair re-inlined its prior document as `<prior_document>`; that went in issue #534
    PR-J4-b.)"""
    repair_key = _leaf_repair_template_name(request_payload)
    template = _load_launch_prompt_templates()[repair_key]
    findings = str(request_payload.get("repair_findings", "")).strip()
    subs: dict[str, str] = {
        "node_key": str(request_payload.get("node_key", "")),
        "step": str(request_payload.get("step", "")),
        "substep": str(request_payload.get("substep", "")),
        "orchestration_id": str(request_payload.get("orchestration_id", "")),
        "agent_run_id": str(request_payload.get("agent_run_id", "")),
        "prompt_contract_version": str(request_payload.get("prompt_contract_version", "")),
        "findings": _fence_pure_doc(findings),
        # Rendered only for a verdict-bearing pair; empty for the producer, whose document
        # carries no conclusion and whose repair legitimately changes what the document says.
        # `.strip().lower()` for the reason `_leaf_launch_template_name` normalizes the same two
        # fields: a spelling this membership test does not recognize renders the slot EMPTY while
        # the template still resolves, so the omission would be silent. Only host constants reach
        # these fields today, which is what keeps it a fail-open by omission rather than a hole.
        # A cut-read repair (issue #547) is about the leaf's reading, whatever its pair.
        "repair_scope": (
            WORKSHOP_CUT_READ_SCOPE_PARAGRAPH
            if str(request_payload.get("repair_reason", "")).strip()
            == WORKSHOP_CUT_READ_REPAIR_REASON
            else PURE_REPAIR_SCOPE_PARAGRAPH
            if (str(request_payload.get("step", "")).strip().lower(),
                str(request_payload.get("substep", "")).strip().lower())
            in PURE_VERDICT_PAIRS else ""),
    }
    if repair_key == "workshop verdict repair":
        subs.update(WORKSHOP_VERDICT_REPAIR_CUT_READ_SLOTS
                    if subs["repair_scope"] == WORKSHOP_CUT_READ_SCOPE_PARAGRAPH
                    else WORKSHOP_VERDICT_REPAIR_SHAPE_SLOTS)
    if not request_payload.get("warm_resume"):
        subs["launch_body"] = _leaf_launch_body(request_payload)
        # The launch body starts after the persona line, which carries a workshop prompt's
        # `<tool_surface>`; every workshop repair template states it beside the body — the
        # verdict repair since step 3 PR-J2 (a reader row carries its transport's paging rule),
        # the bundle and IR repairs since PR-J3-b (an HTTP producer row carries what to do about
        # a cut reply, which the findings' fence may only diagnose). `_leaf_launch_body` has
        # already refused a provider with no row.
        subs["tool_surface"] = (
            _workshop_tool_surface_table(request_payload)[request_payload["workshop_provider"]])
    else:
        subs["launch_body"] = ""
        subs["tool_surface"] = ""
    return _substitute_leaf_placeholders(template, subs)


def _render_launch_prompt_template(request_payload: dict[str, Any]) -> str:
    """Render the launch prompt template.

    Two shapes exist since Z4 (issue #171), and a request is one or the other:

    * DETERMINISTIC — the minimal identity prompt for an in-process substep, which no leaf
      reads (it exists so a deterministic launch records the same kind of artifact as any
      other).
    * WORKSHOP — the host-rendered contract of a `workshop leaf` (issue #534), per
      `(step, substep)` and declared shape, with its own repair variants.

    Anything else is refused — a PURE request (`leaf_mode: pure`) included: the pure leaf and
    its templates went in issue #534 PR-J4-b, so a request still naming it is a host defect. The `step agent` / `substep agent` / `common boilerplate`
    templates, the slim warm-resume repair renderer, the Gate Runbook and the Task Card all
    served the agentic leaf and went with it; a request that reaches this line is neither
    shape, which is a host defect rather than a third prompt to invent.
    """
    if request_payload.get("deterministic"):
        return _render_deterministic_launch_prompt(request_payload)
    if _leaf_contract_is_workshop_request(request_payload):
        if str(request_payload.get("repair_findings", "")).strip():
            return _render_leaf_repair_prompt(request_payload)
        return _render_leaf_launch_prompt(request_payload)
    raise ValueError(
        "launch request is neither deterministic nor workshop, and there is no other leaf "
        f"model to render a prompt for (leaf_mode={request_payload.get('leaf_mode')!r} "
        f"step={request_payload.get('step')!r} substep={request_payload.get('substep')!r}); "
        "the pure leaf went in issue #534 PR-J4-b")


def render_launch_prompt_text(request_payload: dict[str, Any]) -> str:
    return _render_launch_prompt_template(request_payload)


def prepare_launch_request_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(request_payload)
    # Canonicalize agent_role FIRST — before anything below renders the launch prompt.
    # The episode: this function force-renders `launch_prompt_full`, and the AGENTIC render
    # embedded `_build_task_card`, which read the role with `.strip()` and NO `.lower()`.
    # Canonicalizing only in `_validate_launch_request_payload` (which record_launch calls
    # AFTER this function) fixed nothing: the prompt had already been rendered from `"SUBSTEP"`
    # and shipped without its Task Card, while the persisted request recorded `"substep"` —
    # a durable record positively disagreeing with the prompt beside it. That renderer is
    # deleted (Z4, issue #171) and the ORDER is kept: it is the general rule, and the next
    # renderer to read a request field before validation gets it for free. Normalization only: an
    # unknown or absent role is left as it is (the conductor writes it from the step table).
    _role_raw = payload.get("agent_role")
    if isinstance(_role_raw, str) and _role_raw.strip():
        payload["agent_role"] = _role_raw.strip().lower()
    # Deterministic (in-process Build / Validate.execute) requests carry no skill: no
    # leaf runs them, and their SKILL.md files do not exist. Leave skill_name/skill_ref
    # stripped and skill_must_read_refs empty (mirror build_launch_request) so the
    # persisted request stays self-consistent and references no deleted SKILL.
    # A pure-function leaf reads no SKILL (its whole context is host-inlined) and has no
    # skill section in its prompt: force the skill fields empty so the persisted request is
    # self-consistent. A deterministic request never carries them in the first place.
    pure = _leaf_contract_is_host_rendered_request(payload)
    if pure:
        payload["skill_name"] = ""
        payload["skill_ref"] = ""
    payload.setdefault("issue_severity", "none")
    payload.setdefault("workflow_mode", os.environ.get("SPEC2SCIENCE_WORKFLOW_EXEC_MODE", "dev"))
    payload.setdefault("repair_strategy", "none")
    payload.setdefault("repair_target_agent_run_id", "none")
    payload.setdefault("repair_reason", "none")
    # NO leaf force-reads a document since Z4 (issue #171): a deterministic substep has no
    # leaf, and a pure leaf is handed its whole context inlined. Kept as an explicit empty in
    # BOTH assembly paths (here + build_launch_request).
    payload["skill_must_read_refs"] = ""
    explicit_prompt_present = any(
        _coerce_nested_launch_text(payload, path) is not None
        for path in (
            ("launch_prompt_full",),
            ("execution_prompt",),
            ("prompt",),
            ("task",),
            ("instruction",),
            ("instructions",),
            ("message",),
            ("spawn_request", "prompt"),
            ("spawn_request", "task"),
            ("spawn_request", "instruction"),
            ("spawn_request", "instructions"),
            ("spawn_request", "message"),
            ("launch_prompt",),
        )
    )
    # A pure launch is HOST-MEDIATED: the host fully controls the closed context, so the prompt
    # is ALWAYS the canonical render of THIS request — never a caller-supplied body. Force-render
    # (overwriting any explicit prompt field) so a supplied `launch_prompt_full` cannot inject a
    # mismatched identity/context (the child would act on one context while its output is
    # attributed to another request). For the
    # legacy path, keep the existing behavior (render only when no explicit prompt is supplied).
    if pure or not explicit_prompt_present:
        payload["launch_prompt_full"] = render_launch_prompt_text(payload)
    return payload


def _extract_launch_prompt_text(request_payload: dict[str, Any]) -> str:
    # Prefer explicit full execution prompts, then fall back to short launch summaries.
    for path in (
        ("launch_prompt_full",),
        ("execution_prompt",),
        ("prompt",),
        ("task",),
        ("instruction",),
        ("instructions",),
        ("message",),
        ("spawn_request", "prompt"),
        ("spawn_request", "task"),
        ("spawn_request", "instruction"),
        ("spawn_request", "instructions"),
        ("spawn_request", "message"),
        ("launch_prompt",),
    ):
        text = _coerce_nested_launch_text(request_payload, path)
        if text is not None:
            return text
    return json.dumps(request_payload, ensure_ascii=False, indent=2)


def _extract_launch_reply_text(response_payload: dict[str, Any]) -> str:
    for key in ("launch_reply", "reply", "response_text", "message", "result"):
        text = _coerce_launch_text(response_payload.get(key))
        if text is not None:
            return text
    return json.dumps(response_payload, ensure_ascii=False, indent=2)


def _extract_response_agent_session_id(response_payload: dict[str, Any]) -> str | None:
    candidate_paths: tuple[tuple[str, ...], ...] = (
        ("agent_session_id",),
        ("agent_id",),
        ("session_id",),
        ("child_agent_id",),
        ("child_agent_session_id",),
        ("id",),
        ("agent", "id"),
        ("agent", "session_id"),
        ("child_agent", "id"),
        ("child_agent", "session_id"),
        ("data", "id"),
        ("data", "session_id"),
    )
    for path in candidate_paths:
        current: Any = response_payload
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if isinstance(current, str) and current.strip():
            return current.strip()
    return None


def _validate_response_agent_session_id(response_payload: dict[str, Any]) -> str:
    session_id = _extract_response_agent_session_id(response_payload)
    if session_id is None:
        raise ValueError("launch response must include the child agent identifier")
    return session_id


# The per-(step, substep) allowed `validate_pipeline_semantics --stage` map, and the scanner
# that read a rendered prompt for an actionable invocation of one, stood here until Z4
# (issue #171). Both existed because an AGENTIC leaf ran the gate itself out of its prompt:
# `_lint_launch_prompt_gate_allowlist` refused a launch whose prompt named a stage outside
# that substep's allow-set, and `SPEC2SCIENCE_ENFORCE_GATE_ALLOWLIST=0` was its rollback. A pure
# leaf holds no shell and invokes nothing; every gate is run by the conductor in its own
# process, per `docs/workflow/phases/phase_0{1,2,4}_*.md`. The prose half went with them:
# `docs/workflow/LAUNCH_PROMPT_REFERENCE.md`'s "substep <-> allowed validator gate
# correspondence table", which this map was the code side of.


def _extract_agent_summary_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for key in (
        "agent_run_id",
        "agent_role",
        "node_key",
        "step",
        "substep",
        "status",
        "agent_backend",
        "agent_model",
        "context_id",
        "agent_session_id",
        "started_at",
        "finished_at",
        "result_summary",
    ):
        value = payload.get(key)
        if value is None:
            continue
        lines.append(f"{key}: {value}")

    output_refs = payload.get("output_refs")
    if isinstance(output_refs, list) and output_refs:
        lines.append("output_refs:")
        for item in output_refs:
            if isinstance(item, str) and item.strip():
                lines.append(f"- {item.strip()}")

    if lines:
        return "\n".join(lines)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _validate_agent_summary_text(payload: dict[str, Any], summary_text: str) -> None:
    text = summary_text.strip()
    if not text:
        raise ValueError("agent.summary.txt must be non-empty")
    non_empty_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(non_empty_lines) < 2:
        raise ValueError("agent.summary.txt must not be single-line summary")

    status = payload.get("status")
    if isinstance(status, str) and status.strip():
        marker = f"status: {status.strip()}"
        if marker not in text:
            raise ValueError("agent.summary.txt must include final status line")

    output_refs = payload.get("output_refs")
    if isinstance(output_refs, list) and any(isinstance(item, str) and item.strip() for item in output_refs):
        if "output_refs:" not in text:
            raise ValueError("agent.summary.txt must include output_refs section for pass result")
    elif (
        isinstance(status, str)
        and status.strip().lower() in TERMINAL_STATUSES
        and not any(token in text for token in ("result_summary:", "summary:", "reason:", "failure_reason:"))
    ):
        raise ValueError("agent.summary.txt must include summary or failure reason")


def _node_key_to_safe(node_key: str) -> str:
    spec_kind, spec_id, spec_version = _parse_node_key_strict(node_key)
    return f"{spec_kind}__{spec_id}__{spec_version}"


def resume_orchestration(
    repo_root: Path,
    orchestration_id: str,
    *,
    spec_ref: str | None = None,
    source_dependency_ref: str | None = None,
    closure_until_phase: str | None = None,
    until_phase: str | None = None,
    wait_usage_reset: bool = False,
) -> dict[str, Any]:
    """Prepare an existing orchestration to be driven again, and reconcile what the prior
    driver left behind.

    Named for what it does since issue #177 deleted the checkpoint ledger: there is no
    `resume_enabled` flag to set and no ledger to consult, so "enable checkpoint resume" named
    neither the input nor the effect.

    Update the meta only when `spec_ref` / `source_dependency_ref` is specified
    (to reflect the value overridden via the CLI at resume time into the meta so that
    the restoration source of the next resume does not become stale). When unspecified, keep the existing meta.

    When resuming an orchestration already terminated with a terminal status
    (pass / fail / fail_closed / blocked / timeout / cancel), return the live status to `running`.
    Because `update_orchestration_status` rejects a terminal → other status transition except `fail` → `fail_closed`,
    without a reset the resumed agent could not record `pass` even if it completed,
    and the resume would not hold. The prior terminal narrative is saved to `resumed_from_*`,
    and the history remains in failure_analysis.json / phase_state_log.

    A missing or unreadable `orchestration_meta.json` raises from the read (issue #643: no
    refusal of its own). Every recorded status is reconciled, whatever it is.
    """
    meta_path = _orchestration_root(repo_root, orchestration_id) / "orchestration_meta.json"
    meta = _read_json(meta_path)
    meta["resumed_at"] = _utc_now_iso()
    if isinstance(spec_ref, str) and spec_ref.strip():
        meta["spec_ref"] = spec_ref.strip()
    if isinstance(source_dependency_ref, str) and source_dependency_ref.strip():
        meta["source_dependency_ref"] = source_dependency_ref.strip()
    # A resume that RETARGETS the orchestration to a different spec (the run_workflow
    # single-node escape hatch, `--resume <other_spec>`) invalidates any recorded
    # closure back-link: this orchestration is no longer a node of that closure. Drop
    # the closure_* fields (and realign invocation.spec_ref) so a later plain
    # `--resume` does not read the stale link and re-drive the OLD closure target.
    # A normal resume — including a closure-driven node resume — passes the node's own
    # spec, which equals invocation.spec_ref, so nothing is dropped and the closure
    # linkage survives.
    invocation_block = meta.get("invocation")
    if (
        isinstance(spec_ref, str)
        and spec_ref.strip()
        and isinstance(invocation_block, dict)
        and isinstance(invocation_block.get("spec_ref"), str)
        and invocation_block.get("spec_ref") != spec_ref.strip()
    ):
        for _closure_key in ("closure_id", "closure_target_spec_ref", "closure_until_phase"):
            invocation_block.pop(_closure_key, None)
        invocation_block["spec_ref"] = spec_ref.strip()
    # Persist the effective closure end-phase onto THIS node when the closure is
    # resumed with a phase override. Each node of a --with-deps closure carries a
    # copy of the target's end-phase (invocation.closure_until_phase); on an override
    # resume the driver passes the new value so the copy stays current. This makes the
    # override durable on the dependency nodes themselves — so a later plain --resume
    # recovers the overridden phase even if the target orchestration was never created
    # (the closure kept failing at a dependency, leaving no target prompt to read).
    # Only applies to a node that still carries a closure link (not a retargeted one).
    if (
        isinstance(closure_until_phase, str)
        and closure_until_phase.strip()
        and isinstance(invocation_block, dict)
        and invocation_block.get("closure_id")
    ):
        invocation_block["closure_until_phase"] = closure_until_phase.strip()
    # `wait_usage_reset` is a PER-INVOCATION preference that is NOT recovered from the record (the
    # operator re-passes `--wait-usage-reset` on resume; see run_workflow). The conductor's actual
    # behavior on this resume is driven by the RE-PASSED flag, so refresh the recorded field to that
    # effective value — otherwise a run started without the flag and resumed WITH it (or vice-versa)
    # would leave the provenance reporting the opposite of the behavior that produced the resumed
    # result. Mirrors the closure_until_phase refresh above (a per-invocation override made durable).
    # Always written (not gated on truthiness) so a resume that DROPS the flag also resets it.
    if isinstance(invocation_block, dict):
        invocation_block["wait_usage_reset"] = bool(wait_usage_reset)
    # The end-phase THIS resume runs to. A resume may EXTEND the run (`--resume <spec>
    # validate` over a run started `--until-phase compile`), and the completion vouch reads
    # this field to decide which phases must be certified — left stale, it would vouch a
    # four-phase run against one phase. Refreshed only when the caller passes one, so a
    # resume that does not know its end-phase leaves the record alone rather than clearing it.
    #
    # Lowering it is allowed and does not lower the VOUCH's bar: the completion vouch reads
    # `until_phase_high_water`, which only ever rises. See `_record_until_phase_high_water`.
    if isinstance(until_phase, str) and until_phase.strip():
        if not isinstance(invocation_block, dict):
            # A legacy orchestration carries no `invocation` block at all. Refreshing only an
            # EXISTING dict left it permanently unvouchable — the completion vouch reads this
            # field and raises when it is absent — so a resume that KNOWS its end-phase
            # records one rather than requiring the operator to hand-edit the meta.
            invocation_block = {}
            meta["invocation"] = invocation_block
        _record_until_phase_high_water(invocation_block)
        invocation_block["until_phase"] = until_phase.strip()
    if isinstance(invocation_block, dict):
        _record_until_phase_high_water(invocation_block)
    prior_status = meta.get("status")
    # `running` reconciles too, and that is the whole point of issue #177's PR-3. Before it, a
    # `running` prior meant "possibly a live driver", so the resume path refused rather than
    # reconciled — and the thing that decided which it was probed the recorded pid through
    # `/proc`, answering `unknown` for every run started on another host, in another PID
    # namespace, or under a `hidepid` mount. An `unknown` refused the recovery it existed to
    # enable. The exclusive claim answers it directly instead: a driver that is gone does not
    # hold its claim, so a resume that GOT here already knows nothing else is driving this run.
    # The crash reconciliations below therefore have to run for a `running` prior as well, or a
    # resume recovers the status and leaves the dead child's markers and orphan edges behind
    # it. A status the runtime does not write (only a
    # hand-run `init --status` / `set-status --status` stores one) reconciles the same way; its
    # refusal went in issue #643.
    #
    # Archive the prior terminal narrative, then hand the resumed run a fresh
    # in-progress lifecycle so its eventual set-status(pass/fail) is a valid
    # forward transition from `running` rather than a rejected terminal-to-terminal one.
    meta["resumed_from_status"] = prior_status
    for live_field, archive_field in (
        ("reason_code", "resumed_from_reason_code"),
        ("reason_detail", "resumed_from_reason_detail"),
        ("blocking_policy_scope", "resumed_from_blocking_policy_scope"),
    ):
        if meta.get(live_field) is not None:
            meta[archive_field] = meta.get(live_field)
            meta.pop(live_field, None)
    # A `resume_directive` left by a pre-#177 run names a `reopen-phase` that no longer
    # exists; drop it so nothing reads it. A resumed run needs no directive: it re-asks
    # `check-phase-certified` for every phase, and a phase whose artifact a retry revoked
    # is refused there — the revocation IS the instruction, and it lives on the artifact
    # rather than in this record.
    meta.pop("resume_directive", None)
    meta.pop("finished_at", None)
    meta.pop("detected_at", None)
    meta["status"] = "running"
    # Drop the prior terminalization's cleanup_committed marker so the resumed
    # run's eventual terminalization performs a clean two-phase commit and the
    # validator does not treat reused orch tmp as already revoked.
    orch_arid = meta.get("orchestration_agent_run_id")
    if isinstance(orch_arid, str) and orch_arid.strip():
        marker = _cleanup_committed_marker_path(
            repo_root, orchestration_id, orch_arid.strip()
        )
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
    # Re-open the orchestration's own agent_runs row (terminalized in place by
    # the prior set-status) so the resumed run's row is `running` again, its
    # eventual set-status can re-terminalize it, and validate_workspace_root
    # does not age the live tmp root out as cleanup-pending. Done BEFORE the
    # meta commit below so the still-terminal on-disk meta gates recovery: if
    # this is interrupted, a retry re-enters `terminal_reset` and re-runs it
    # (idempotent — the helper no-ops once the row is already `running`).
    _reset_orchestration_run_row_to_running(repo_root, orchestration_id)
    # A host that died mid-launch leaves the active_child window open (no live
    # host finalized the child, and nobody ran deactivate-child). The terminal status proves
    # no child is actually running, so clear the stale markers here — otherwise
    # the resumed agent's first record-launch hits the Claude-backend sequential
    # check and is rejected, permanently wedging recovery for
    # launch_incomplete_active_child / llm_launch_interrupted. Captured before the
    # meta commit so a retry re-enters terminal_reset and re-runs it (idempotent).
    cleared_active_child = _clear_stale_active_child_markers(repo_root, orchestration_id)
    # The abandoned child's agent_graph edge was written by record_launch before
    # the marker and never gets a terminal agent_runs row; prune it so the
    # resumed run's eventual set-status(pass) is not rejected by the orphan-edge
    # check in _validate_orchestration_completion_for_pass. Idempotent.
    pruned_graph_children = _prune_orphan_agent_graph_edges(repo_root, orchestration_id)
    # A stale `child_running` the abandoned launch left in phase_state is NOT reset: no reader
    # tells it from `not_started` (the completion vouch and the pre_judge audit read
    # `skipped_certified`, write_step_result reads `child_finished`, and a transition reads no
    # from-state), and the re-launch overwrites it. The reset and its
    # `resume_reset_stale_child_running` log event went in issue #643 PR-5.
    # The `driver` block is GONE (issue #177): nothing reads it any more, and a record that
    # nothing reads is a record that drifts. A stale one was actively harmful — a reused pid
    # made a probe call a dead run alive — so a resume drops it rather than refreshing it.
    meta.pop("driver", None)
    # ONE fact from that block survives it, and only one: the HOST. The exclusive claim that
    # replaced the driver probe lives under the operator's own `~/.spec2science/start_claims/`, so
    # it is host-scoped — with host-local homes (a container and its host bind-mounting the
    # checkout, a cluster with local `/home` and shared `/work`) two drivers each take their
    # own claim and both proceed. Nothing else in the tree can notice that.
    #
    # Recorded to WARN, never to refuse. Refusing on a differing hostname would reinstate the
    # failure this issue exists to delete: an operator who legitimately moves a checkout to a
    # new host, or out of a container, could never resume — `unknown ⇒ refuse` under a new
    # name. The previous host is reported and the resume proceeds.
    previous_host = meta.get("last_driver_host")
    meta["last_driver_host"] = _current_hostname_for_record()
    _write_json(meta_path, meta)
    reconcile_phase_state_for_resume(repo_root, orchestration_id)
    # Said where an operator will find it: the phase-state log is what they read back to
    # understand a run, and it is the durable record the claim itself cannot leave.
    if previous_host and previous_host != meta["last_driver_host"]:
        _append_phase_state_log(
            repo_root,
            orchestration_id,
            {
                "ts": _utc_now_iso(),
                "event": "resume_crossed_hosts",
                "from": previous_host,
                "to": meta["last_driver_host"],
                "note": (
                    "this orchestration was last driven on another host; the start claim is "
                    "host-scoped (~/.spec2science/start_claims/), so it cannot prove the other "
                    "host is not still running it — see docs/RUNBOOK.md §3-1"
                ),
            },
        )
    _append_phase_state_log(
        repo_root,
        orchestration_id,
        {
            "ts": _utc_now_iso(),
            "event": "resume_status_reset",
            "from": prior_status,
            "to": "running",
            "note": f"status reset to running for resume (from {prior_status})",
        },
    )
    if pruned_graph_children:
        _append_phase_state_log(
            repo_root,
            orchestration_id,
            {
                "ts": _utc_now_iso(),
                "event": "resume_pruned_orphan_graph_edges",
                "note": "orphan agent_graph edges (no agent_runs row) pruned for checkpoint resume",
                "pruned_child_agent_run_ids": pruned_graph_children,
            },
        )
    if cleared_active_child:
        _append_phase_state_log(
            repo_root,
            orchestration_id,
            {
                "ts": _utc_now_iso(),
                "event": "resume_cleared_stale_active_child",
                "note": "stale active_child markers cleared for checkpoint resume",
                "cleared_active_child_arids": cleared_active_child,
            },
        )
    return meta


# Unpinned Claude model alias used as the spec-side default. It is deliberately
# NOT a pinned version (e.g. "claude-opus-4-8"): the configured model alias maps
# to whatever the current default version is, so it never goes stale as versions
# update. The EXACT version that actually ran is recorded separately on each
# step/substep agent_runs row, resolved by the conductor from the leaf's own CLI
# result envelope (`--output-format json`) — never from the ~/.claude transcript,
# which is outside the workflow's access boundary.
DEFAULT_CLAUDE_MODEL_ALIAS = "opus"


def default_agent_model_for_backend(backend: str) -> str:
    """The spec-side default agent_model stamped on a LEAF launch whose configuration
    entry declares no `model:`. For claude this is DEFAULT_CLAUDE_MODEL_ALIAS; for codex
    the backend's own unpinned alias ("codex"). Other/unknown backends get "" (left to
    sibling backfill). Never returns a pinned version.

    It is a PREDICTED label, not a measurement — the conductor passes no `--model` for an
    undeclared model, so what actually ran is decided by the CLI. The measured value
    replaces it after the fact from the leaf's own result envelope (`_agent_run_json`),
    which is the only reading the row can rely on.

    WHAT DECIDES A CLAUDE LEAF'S MODEL — the one statement of it; other sites point here.
    The declared `--model` when the entry names one, else the CLI's own default. A
    leaf launches with `--restricted` (`leaf_contract.workshop_leaf_flags`), so no user, project or
    local settings file applies: neither a settings `model` key, nor an `env` block's
    `ANTHROPIC_MODEL`, nor an `env.ANTHROPIC_DEFAULT_OPUS_MODEL` remap of a declared
    `opus` reaches the leaf (measured, with the endpoint as a fourth row; the flag ignores
    the whole file). The CLI's global config (`_claude_global_config_paths`) is not a
    settings file and its `env` block is NOT ignored; preflight refuses one
    (`claude_global_config_env_absent`).
    `ANTHROPIC_MODEL` in the operator's PROCESS environment takes no part either (it is not
    on `LEAF_ENV_ALLOWLIST`). Before issue #453 each of those
    settings channels decided or remapped the model; the measurements, with and without the
    flag, are issue #446 comment 6053241634 and the issue #453 comments (CLI 2.1.294,
    loopback capture). Admin-managed settings still apply; they are the operator's machine
    and out of scope. A declared alias stays the CLI's alias: what it resolves to is read
    back from the envelope, not required to be a full id.

    Nothing in the run reads the operator's `~/.claude` for a model: the orchestration row
    is stamped with DEFAULT_CLAUDE_MODEL_ALIAS too (`tools/run_workflow.py`)."""
    b = (backend or "").strip().lower()
    if b == "claude":
        return DEFAULT_CLAUDE_MODEL_ALIAS
    if b == "codex":
        return "codex"
    return ""


def _validate_leaf_launch_request_payload(request_payload: dict[str, Any]) -> None:
    """Validate the leaf-specific shape of a host-rendered launch request.

    Enforced (fail-closed): the pair is one a workshop leaf serves (`WORKSHOP_LAUNCH_PAIRS`);
    a declared `pure_language` / `pure_parallel` is a non-empty
    string; and the required `pure_context` keys are present on a cold launch. A warm-resume
    repair (`warm_resume` + `repair_findings`) may omit `pure_context` — the resumed lineage
    already holds it — but a cold repair still requires it.

    The rest of the request (`leaf_mode`, `deterministic`, `workshop_provider`,
    `workshop_lineage_id` (`record_launch` checks its token shape first), `pure_shape`,
    `prompt_contract_version`, `allowed_output_paths`, the skill fields) is written by the
    conductor's `build_launch_request` from its own phase logic, and a leaf authors no launch
    request; the refusals that re-checked those fields went in issue #643. An unknown
    `pure_shape` has no template, so the renderer refuses it on a cold launch."""
    step = str(request_payload.get("step", "")).strip().lower()
    substep = str(request_payload.get("substep", "")).strip().lower()
    key = (step, substep)
    if key not in WORKSHOP_LAUNCH_PAIRS:
        admissible = ", ".join(f"({s}, {ss})" for s, ss in sorted(WORKSHOP_LAUNCH_PAIRS))
        raise ValueError(
            f"leaf_mode=workshop is only valid for {admissible}; "
            f"got (step={step!r}, substep={substep!r})")
    # A declared bundle SHAPE selects a different required-key set.
    shape = request_payload.get("pure_shape")
    shape_key: tuple[str, str, str] | None = (
        (step, substep, shape.strip().lower())
        if isinstance(shape, str) and shape.strip() else None)
    # The target language the template is composed for (issue #289). Refused when malformed:
    # an absent value means "this template carries no language fragment", and a blank one read
    # as absent would render a prompt missing the rules the gates hold the leaf to. Whether a template NEEDS one is the composer's question.
    language = request_payload.get("pure_language")
    if language is not None and not (isinstance(language, str) and language.strip()):
        raise ValueError(
            f"pure launch request pure_language must be a non-empty string when present; "
            f"got {language!r}")
    # The target parallel backend its `{{parallel:<name>}}` markers are composed for (issue
    # #316), refused when malformed on the same ground.
    parallel = request_payload.get("pure_parallel")
    if parallel is not None and not (isinstance(parallel, str) and parallel.strip()):
        raise ValueError(
            f"pure launch request pure_parallel must be a non-empty string when present; "
            f"got {parallel!r}")
    # `pure_context` may be omitted ONLY on a genuine warm-resume REUSE repair — the resumed
    # producer session already holds the schema, the prior document, and the input context, so
    # re-inlining them is redundant. A `restart` (or any non-reuse) repair has no such session,
    # and the cold fallback renderer needs the context re-inlined, so it is NOT exempt: require
    # `repair_strategy == "reuse"` alongside warm_resume + findings, or the context stays
    # mandatory. The same (warm + reuse + findings) shape the slim repair turn used.
    warm = bool(request_payload.get("warm_resume"))
    reuse = str(request_payload.get("repair_strategy", "")).strip() == "reuse"
    findings = str(request_payload.get("repair_findings", "")).strip()
    context_required = not (warm and reuse and findings)
    if context_required:
        ctx = request_payload.get("pure_context")
        if not isinstance(ctx, dict):
            raise ValueError(
                "pure launch request must include a pure_context object (except on a "
                "warm-resume repair, where the resumed session already holds it)"
            )
        required = (PURE_CONTEXT_REQUIRED_KEYS_BY_SHAPE[shape_key] if shape_key is not None
                    else PURE_CONTEXT_REQUIRED_KEYS[key])
        missing = [
            k for k in required
            if not (isinstance(ctx.get(k), str) and ctx.get(k).strip())
        ]
        if missing:
            raise ValueError(
                "pure launch request pure_context missing required key(s) for "
                f"{shape_key or key}: {', '.join(missing)}"
            )


def _validate_launch_request_payload(request_payload: dict[str, Any]) -> None:
    """Validate a launch request before `record_launch` writes it.

    The request is assembled by the conductor (`workflow_conductor.build_launch_request`),
    never by a leaf: its refs come from the conductor's reservations, its role, model, repair
    block and `deterministic` flag from the conductor's own phase logic and the operator's
    configuration. The refusals that re-checked those host values went in issue #643. What
    stays is what `record_launch` needs to place the launch (`node_key`, `step`) and the shape
    of a workshop request (`_validate_leaf_launch_request_payload`)."""
    node_key = request_payload.get("node_key")
    step = request_payload.get("step")
    if not isinstance(node_key, str) or not node_key.strip():
        raise ValueError("launch request must include non-empty node_key")
    if not isinstance(step, str) or not step.strip():
        raise ValueError("launch request must include non-empty step")
    # The gate is KEY PRESENCE: any request that names a `leaf_mode` is held to the workshop
    # request's shape.
    if "leaf_mode" in request_payload:
        _validate_leaf_launch_request_payload(request_payload)


def _load_run_records(
    orchestration_root: Path,
    *,
    caller_holds_lock: bool = False,
) -> dict[str, dict[str, Any]]:
    """Load `agent_runs.jsonl` into a dict keyed by agent_run_id: the one parser of the
    ledger (`_read_existing_run_ids` reads through it).

    Adv-21 trailing-line tolerance combined with Adv-40 in-flight detection. A truncated
    last line whose corruption is plausibly a concurrent appender's mid-write is silently
    skipped; any other malformed line raises `RuntimeError("...durable corruption...")`.
    Without this, callers like `_validate_orchestration_completion_for_pass`
    would crash on `JSONDecodeError` whenever `record_agent_run` is appending
    concurrently.

    H-FOURTH-1 / NEW-H1: when the
    caller already holds the runs-jsonl fcntl lock (typically via
    `_runs_jsonl_exclusive_lock` inside `record_agent_run`), the
    writer-active probe would self-contend and falsely report in-flight,
    silently masking durable trailing-line corruption. Pass True to force
    every malformed line to surface.
    """
    runs_path = orchestration_root / "agent_runs.jsonl"
    records: dict[str, dict[str, Any]] = {}
    if not runs_path.exists():
        return records
    non_empty_lines = [s for s in (raw.strip() for raw in runs_path.read_text(encoding="utf-8").splitlines()) if s]
    n_lines = len(non_empty_lines)
    for idx, line in enumerate(non_empty_lines):
        is_last = idx == n_lines - 1
        in_flight = (
            is_last
            and not caller_holds_lock
            and _agent_runs_writer_active(runs_path)
        )
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            if in_flight:
                continue
            raise RuntimeError(
                f"agent_runs.jsonl has malformed JSON at line {idx + 1}: {exc} "
                f"(no active writer detected — durable corruption; quarantine "
                f"the ledger and roll forward explicitly)"
                if is_last else
                f"agent_runs.jsonl has malformed non-trailing JSON at line {idx + 1}: {exc}"
            ) from exc
        if not isinstance(item, dict):
            if in_flight:
                continue
            raise RuntimeError(
                f"agent_runs.jsonl line {idx + 1} is not a JSON object: {item!r}"
            )
        run_id = item.get("agent_run_id")
        if isinstance(run_id, str) and run_id.strip():
            records[run_id.strip()] = item
    return records


def _load_invalid_run_records(
    orchestration_root: Path,
) -> dict[str, dict[str, Any]]:
    """Load `agent_runs_invalid.jsonl` into a dict keyed by agent_run_id.

    Mirrors `_load_run_records` but reads the SEPARATE invalid log that
    `record_agent_run` diverts terminal payloads to when terminal-write
    validation rejects them (e.g. an unauthorized write — see the `except
    ValueError` handler in `record_agent_run`). Unlike the canonical log this
    file is not the durable agent ledger, so a malformed line is skipped rather
    than raised: it is a best-effort recovery source consulted only when a run
    is absent from `agent_runs.jsonl`. On a duplicate arid the LAST entry wins
    (a re-rejected retry overwrites the earlier diagnostic row).
    """
    invalid_path = orchestration_root / "agent_runs_invalid.jsonl"
    records: dict[str, dict[str, Any]] = {}
    if not invalid_path.is_file():
        return records
    try:
        lines = invalid_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for raw in lines:
        token = raw.strip()
        if not token:
            continue
        try:
            item = json.loads(token)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        run_id = item.get("agent_run_id")
        if isinstance(run_id, str) and run_id.strip():
            records[run_id.strip()] = item
    return records


def _validate_terminal_run_payload(
    repo_root: Path,
    orchestration_id: str,
    payload: dict[str, Any],
    *,
    caller_holds_lock: bool = False,
) -> None:
    role = payload.get("agent_role")
    status = payload.get("status")
    if not isinstance(role, str):
        return
    role_token = _normalized_agent_role(role)
    # Same membership test, same single definition, as the terminal write audit this used to
    # guard (deleted in issue #171 PR-2). The two carried independent copies of the set literal
    # once, which is how one field ended up with two places to widen.
    if role_token not in AGENT_RUN_ROLES:
        return
    # The terminal FILESYSTEM AUDIT used to run here: a diff of the repository against the
    # per-agent write baseline, with every changed path required to fall inside the
    # capability's `write_roots`. It was the enforcement half of a write authority no leaf
    # holds any more — a pure leaf runs under a read-only bwrap profile with `write_roots: []`
    # and the host writes every artifact after the child window — so what it measured was the
    # HOST's own writes against a grant the host is not bound by. Retired in issue #171 PR-2
    # with the baseline, the capability and the violation record it wrote.
    if not isinstance(status, str) or status.strip().lower() != "pass":
        return

    output_refs = payload.get("output_refs")
    if role_token in {"step", "substep"}:
        # Z2 pure leaf (M-C): a pure launch writes NO output manifest, and the host writes the
        # leaf's artifacts AFTER the child window closes (run_substep's pure branch), so at the
        # terminal-record instant the pure arid has produced nothing under its own authority. Its
        # passing row therefore carries `output_refs == []`, ENFORCED BIDIRECTIONALLY: an empty
        # list is required (the bundle's provenance is `bundle_meta.json`, not this row), and a
        # NON-empty one is rejected — a leaf with no write authority that claims outputs is a
        # forged provenance, fail-closed. The non-empty `output_refs` check below is skipped for
        # pure.
        # Detected from the launch request (`leaf_mode == "pure"`), the single source the
        # producer stamps, not a field on this payload the leaf could set.
        agent_run_id = str(payload.get("agent_run_id") or "").strip()
        pure_launch = False
        if agent_run_id:
            launch_req = _read_launch_request_payload(
                repo_root, orchestration_id, agent_run_id=agent_run_id)
            # A workshop leaf (issue #534) holds no write authority under the checkout either.
            pure_launch = (isinstance(launch_req, dict)
                           and _leaf_contract_is_host_rendered_request(launch_req))
        if pure_launch:
            # Require EXACTLY an empty list. A missing field (`output_refs is None`) is NOT
            # equivalent: the contract is that a pure pass row carries `output_refs: []`, so a
            # malformed or tampered record that OMITS the field must be rejected here, not read
            # as "empty" and waved through (and likewise a non-empty list is forged provenance).
            if output_refs != []:
                raise ValueError(
                    "pure pass status for step/substep requires output_refs to be exactly [] (a "
                    "pure leaf holds no write authority; the host writes the bundle artifacts "
                    f"after the child window closes); got {output_refs!r}")
            return
        if not isinstance(output_refs, list) or not output_refs:
            raise ValueError("pass status for step/substep requires non-empty output_refs")
        for idx, item in enumerate(output_refs):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"output_refs[{idx}] must be non-empty string")
        # A non-workshop step/substep pass row is a DETERMINISTIC one: its `output_refs` are the
        # conductor's own subset of the request's `allowed_output_paths`, so the prefix check
        # against the request's `ir_ref` / `pipeline_ref` that stood here re-read a host value.
        # P2-7 retired the apply_patch_writes gate, and issue #171 PR-2 retired the FS-diff
        # that replaced it. A leaf writes no artifact at all: the host writes every one after
        # the child window, so there is nothing about this row's writes left to verify.
        return

    # orchestration role (the trusted, unconfined conductor): output_refs are optional
    # bookkeeping, and no orchestration row carries them.


def _read_launch_request_payload(
    repo_root: Path,
    orchestration_id: str,
    *,
    agent_run_id: str,
) -> dict[str, Any] | None:
    """Return the parsed `launches/<arid>.request.json` payload, or None
    when absent / malformed.

    `ValueError` rather than `json.JSONDecodeError` for the reason `_read_json_or_none` states
    beside it: `ValueError` covers BOTH malformed JSON and `UnicodeDecodeError` (a damaged file
    `read_text` chokes on), so no decode failure escapes. It was the narrower spelling until
    issue #209 gave this helper its first caller in a frame that must not raise — the pure
    producer loop's reopen seed, which runs outside the loop's context-assembly guard, so a
    raise here takes the conductor down mid-run instead of failing the substep closed."""
    request_path = (
        _orchestration_root(repo_root, orchestration_id)
        / "launches"
        / f"{agent_run_id.strip()}.request.json"
    )
    if not request_path.exists():
        return None
    try:
        payload = _read_json(request_path)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _validate_step_or_substep_launch_refs(repo_root: Path, payload: dict[str, Any]) -> None:
    for key in (
        "launch_request_ref",
        "launch_response_ref",
        "launch_prompt_ref",
        "launch_reply_ref",
    ):
        ref = payload.get(key)
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError(f"{key} must be non-empty string")
        target = repo_root / ref.strip()
        if not target.exists():
            raise ValueError(f"{key} target not found: {ref}")
        if key in {"launch_prompt_ref", "launch_reply_ref"}:
            text = target.read_text(encoding="utf-8", errors="ignore")
            if not text.strip():
                raise ValueError(f"{key} target must be non-empty: {ref}")


def _iter_step_result_paths(root: Path) -> list[Path]:
    steps_root = root / "steps"
    if not steps_root.exists():
        return []
    return sorted(steps_root.glob("*/*/*/step_result.json"))


def _until_phase_index(repo_root: Path, orchestration_id: str) -> int:
    """The index in `STEP_KEYS_FOR_NODE_STATE` of the phase this invocation was asked to
    reach, from `orchestration_meta.invocation.until_phase`.

    Raises when the record is missing or names a phase the state machine does not know: the
    completion vouch uses it to decide WHICH phases must be certified, so guessing would
    either demand a phase the operator never asked for or skip one they did.
    """
    meta_path = _orchestration_root(repo_root, orchestration_id) / "orchestration_meta.json"
    meta = _read_json(meta_path) if meta_path.is_file() else None
    invocation = meta.get("invocation") if isinstance(meta, dict) else None
    # The HIGH WATER, not the current end-phase: `until_phase` describes this invocation and
    # every writer of it is reachable from a leaf, so reading it lets the judged thing move the
    # bar. Falls back to `until_phase` for a record written before the high water existed.
    token = None
    if isinstance(invocation, dict):
        high_water = _normalized_until_phase(invocation.get("until_phase_high_water"))
        current = _normalized_until_phase(invocation.get("until_phase"))
        # The FURTHER of the two, not "the high water if it parses". Falling back to
        # `until_phase` when the high water is unusable would hand the pre-high-water behaviour
        # back to anyone who can make it unusable; taking the max means an unparseable or
        # absent high water can only ever make the bar HIGHER, never lower. A record written
        # before the high water existed carries only `until_phase` and is unaffected.
        reached = [STEP_KEYS_FOR_NODE_STATE.index(t)
                   for t in (high_water, current) if t is not None]
        token = STEP_KEYS_FOR_NODE_STATE[max(reached)] if reached else None
    if not isinstance(token, str) or token.strip().lower() not in STEP_KEYS_FOR_NODE_STATE:
        raise RuntimeError(
            "cannot mark orchestration pass: orchestration_meta.invocation.until_phase is "
            f"missing or unknown ({token!r}); it decides which phases must be certified. "
            "A run started by tools/run_workflow.py records it; record one on an "
            "orchestration that has none with `init --resume "
            f"--until-phase <{'|'.join(STEP_KEYS_FOR_NODE_STATE)}>`"
        )
    return STEP_KEYS_FOR_NODE_STATE.index(token.strip().lower())


def _reserved_node_keys(repo_root: Path, orchestration_id: str) -> list[str]:
    """The node_keys this orchestration reserved a phase root for, read from
    `reservations/<node_safe>/compile.json`.

    `prepare_node` writes it before the first phase runs, on every route — so it is present
    whether the phases then ran or were skipped as certified, which is what makes it the
    right replacement for the completion vouch's old "the agent graph has edges" rule.
    """
    res_root = _orchestration_root(repo_root, orchestration_id) / "reservations"
    if not res_root.is_dir():
        return []
    keys: list[str] = []
    for path in sorted(res_root.glob("*/compile.json")):
        doc = _read_json(path) if path.is_file() else None
        if not isinstance(doc, dict):
            continue
        node_key = doc.get("node_key")
        if isinstance(node_key, str) and node_key.strip():
            keys.append(node_key.strip())
    return keys


def _validate_orchestration_completion_for_pass(
    repo_root: Path,
    orchestration_id: str,
) -> None:
    root = _orchestration_root(repo_root, orchestration_id)
    graph_path = root / "agent_graph.json"
    runs = _load_run_records(root)
    if not runs:
        raise RuntimeError("cannot mark orchestration pass without agent_runs.jsonl records")

    orchestration_runs = [
        payload
        for payload in runs.values()
        if isinstance(payload.get("agent_role"), str)
        and payload.get("agent_role") == "orchestration"
    ]
    if not orchestration_runs:
        raise RuntimeError("cannot mark orchestration pass without orchestration agent run record")

    graph = _load_graph(graph_path)
    # An orchestration that launched no child has no edges, and since issue #177 that is a
    # LEGITIMATE terminal state: a re-run over a node whose every phase is already certified
    # skips all four, so nothing is launched. The edges requirement existed to refuse an
    # orchestration that did nothing at all; what distinguishes "did nothing" from "had
    # nothing to do" is whether a node was ever RESERVED — `prepare_node` reserves the ir and
    # pipeline roots before the first phase, on every run, skipped or not. So the reservation
    # is the replacement guard, and it refuses exactly what the edge rule refused.
    reserved_nodes = _reserved_node_keys(repo_root, orchestration_id)
    if not reserved_nodes:
        raise RuntimeError("cannot mark orchestration pass: no node reservations")
    # A reservation proves PREPARATION, not completion (Codex round 2, P1): with no children,
    # no edges and no step_results, every loop below is empty and would accept the run. So the
    # edgeless case is carried by the artifacts instead — each reserved node must be CERTIFIED
    # through the phase this invocation was asked to reach. That is the same predicate the
    # skip decision uses, which is what makes "skipped because certified" and "passed" one
    # statement rather than two.
    for node_key in reserved_nodes:
        for phase in STEP_KEYS_FOR_NODE_STATE[:_until_phase_index(repo_root, orchestration_id) + 1]:
            certified, detail, resolver = _phase_certification(
                repo_root, orchestration_id, node_key, phase)
            if not certified:
                raise RuntimeError(
                    f"cannot mark orchestration pass: {node_key}/{phase} is not certified: "
                    f"{detail.get('reason')}"
                )
            # A certified Validate whose variant disagrees with another target's, or whose
            # comparison cannot be evaluated, does not pass (issue #383). Evaluated HERE, not
            # only by the conductor's pre-pass ask, so a path that misses that ask cannot
            # record `pass` over a disagreement.
            if phase == "validate":
                not_ready = cross_target_not_ready_reason(
                    cross_target_agreement(repo_root, node_key, resolver=resolver))
                if not_ready is not None:
                    raise RuntimeError(
                        f"cannot mark orchestration pass: {node_key}/validate: {not_ready}")
    # EMPTY edges is the new legitimate case; MALFORMED is not. The rule this replaced refused
    # both together — not by detecting malformation, but because `_load_graph` NORMALIZES a
    # corrupt graph to `{"edges": []}` and the old rule refused empty. So the raw file is what
    # has to be read here: reading the normalization would skip the whole per-edge parent/child
    # validation below on a record `tools/audit_orchestration.py` and `docs/ORCHESTRATION.md`
    # read back as the run's agent tree.
    raw_graph = _read_json(graph_path) if graph_path.is_file() else None
    if not (isinstance(raw_graph, dict) and isinstance(raw_graph.get("edges"), list)):
        raise RuntimeError(
            "cannot mark orchestration pass: agent_graph.json is missing or has no `edges` list"
        )
    edges = graph.get("edges")
    # EMPTY edges is legitimate only when NOTHING LAUNCHED. A run that recorded a step or
    # substep and has no edge lost its parent-child record, and the per-edge validation below
    # then iterates nothing — so the vouch could not tell "launched nothing" from "launched and
    # lost the record", which is the false record the `edges` rule used to refuse. Measured
    # red-then-GREEN by the disclosure axis on a real one-edge run whose graph was emptied.
    if not edges and any(
        _normalized_agent_role(str(payload.get("agent_role") or "")) in {"step", "substep"}
        for payload in runs.values()
        if isinstance(payload, dict)
    ):
        raise RuntimeError(
            "cannot mark orchestration pass: agent_graph.json records no edges while "
            "agent_runs.jsonl records step/substep runs (the agent tree is not traceable)"
        )

    # (c-2) An outstanding UNAUTHORIZED WRITE used to be refused here — the marker
    # `_validate_actual_write_paths` wrote when a child's terminal filesystem diff showed paths
    # outside its `write_roots`, which is not rolled back, so a `pass` over one would report a
    # clean verdict on a workspace a leaf had written into. PR-2 of issue #171 deleted that
    # audit with the per-agent write baseline it diffed against: a pure leaf holds no write
    # authority, so the diff compared the HOST's own writes against a grant the host is not
    # bound by. Nothing writes the marker now, so nothing can be outstanding, and keeping a
    # check that can only ever pass would read as coverage of a class nothing covers.
    #
    # THE CLASS ITSELF is what changed, not merely the mechanism: a leaf reaches the filesystem
    # only through a `bwrap` profile that holds no checkout at all (an empty tmpfs at its path,
    # issue #227) and whose `write_roots` is empty, so the write it refused cannot occur from
    # the child window at all. That is a
    # structural closure, not an unaudited hole — and it is the reason the deletion is a
    # narrowing of scope rather than of defense. `docs/ORCHESTRATION.md` §"Capability /
    # Manifest contract (removed in issue #171 PR-2)" is canonical.

    # (c) Every edge's parent and child are RECORDED — in `agent_runs.jsonl`, or in
    # `agent_runs_invalid.jsonl` for a terminal payload `record_agent_run` refused. A child in
    # NEITHER is an arbitrarily corrupt edge and fails closed.
    #
    # This clause is about graph INTEGRITY and nothing else, and the distinction cost a round to
    # get right. An earlier cut of issue #177 made it refuse the invalid-log child outright, to
    # catch an unacknowledged unauthorized write. It caught that — and everything else diverted
    # by the same `except ValueError`, which is far wider than the write audit: an empty
    # `output_refs` on a pass payload, and at the time a malformed launch response or a
    # missing sandbox stamp in it. None of those implies a write outside the window,
    # and each would have wedged an honest run permanently. It also did not actually close the
    # hole: `_prune_orphan_agent_graph_edges` keeps the dangling edge only BECAUSE the child is in
    # the invalid log, so deleting that log prunes the edge and the clause has nothing left to
    # refuse — and `agent_runs_invalid.jsonl` is one of the runtime-owned paths exempted from the
    # terminal write-audit diff, so a leaf could delete it and leave no trace.
    #
    # The landed-write case is clause (c-2) ABOVE, anchored where the evidence cannot be removed
    # quietly.
    invalid_runs = _load_invalid_run_records(root)
    for idx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise RuntimeError(f"agent_graph edge must be object: index={idx}")
        parent_id = edge.get("parent_agent_run_id")
        child_id = edge.get("child_agent_run_id")
        if not isinstance(parent_id, str) or not parent_id.strip() or parent_id.strip() not in runs:
            raise RuntimeError(
                f"agent_graph edge parent_agent_run_id missing from agent_runs.jsonl: index={idx}"
            )
        child_norm = child_id.strip() if isinstance(child_id, str) and child_id.strip() else None
        if child_norm is None or (child_norm not in runs and child_norm not in invalid_runs):
            raise RuntimeError(
                "agent_graph edge child_agent_run_id missing from agent_runs.jsonl and "
                f"agent_runs_invalid.jsonl: index={idx}"
            )

    # (b) Every step / substep run is terminal and its launch refs resolve. What is NO LONGER
    # required is that each one be vouched by a step_result: an attempt that ended without one
    # (a transport error, a validate gate fail-close, a consumed escalate, a dead transient
    # retry) is simply a failed attempt, and the certification the pass rests on comes from the
    # artifact chain (d) plus the latest attempt's own result (e) — not from an arid census.
    # What a leaf gains from a terminal arid that no step_result vouches: nothing. It certifies
    # nothing; the metas are host-written or host-stamped after the child window closes, and
    # (e) reads the LATEST attempt, which a leaf cannot make itself be.
    for run_id, payload in runs.items():
        role = payload.get("agent_role")
        if not isinstance(role, str) or role not in {"step", "substep"}:
            continue
        status = payload.get("status")
        if not isinstance(status, str) or status.strip().lower() not in TERMINAL_STATUSES:
            raise RuntimeError(f"{role} agent_run_id must be terminal before pass: {run_id}")
        _validate_step_or_substep_launch_refs(repo_root, payload)
        node_key = payload.get("node_key")
        step = payload.get("step")
        if not isinstance(node_key, str) or not node_key.strip():
            raise RuntimeError(f"{role} node_key missing: {run_id}")
        if not isinstance(step, str) or not step.strip():
            raise RuntimeError(f"{role} step missing: {run_id}")

    # (e) For each (node, phase) this orchestration wrote a step_result for, the LATEST attempt
    # passed and its declared deliverables are on disk. "Latest" is the executor's position in
    # `agent_runs.jsonl` — the append order of the run that wrote it — never mtime, which a
    # copy or a restore forges. An executor with no recorded run cannot be ordered, so it is
    # refused rather than guessed at.
    #
    # A phase recorded as `skipped_certified` is EXEMPT: it did not run in this orchestration,
    # so whatever step_result an earlier attempt of it left is not this run's account of it.
    # Without the exemption a phase that failed, became certified, and was then skipped would
    # be blocked by its own stale failure.
    phase_state_doc = _load_phase_state(repo_root, orchestration_id) or {}
    node_states = phase_state_doc.get("node_states")
    node_states = node_states if isinstance(node_states, dict) else {}
    run_order = {run_id: position for position, run_id in enumerate(runs)}
    attempts_by_phase: dict[tuple[str, str], list[tuple[int, str, Path, dict[str, Any]]]] = {}
    for result_path in _iter_step_result_paths(root):
        try:
            result = _read_json(result_path)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid step_result.json: {result_path}") from exc
        if not isinstance(result, dict):
            raise RuntimeError(f"step_result.json must be object: {result_path}")
        executor_run_id = result.get("executor_agent_run_id")
        if not isinstance(executor_run_id, str) or not executor_run_id.strip():
            raise RuntimeError(f"executor_agent_run_id missing: {result_path}")
        substep_run_ids = result.get("substep_agent_run_ids")
        if not isinstance(substep_run_ids, list):
            raise RuntimeError(f"substep_agent_run_ids must be list: {result_path}")
        executor = executor_run_id.strip()
        if executor not in run_order:
            raise RuntimeError(
                "cannot mark orchestration pass: step_result executor_agent_run_id is not "
                f"recorded in agent_runs.jsonl, so its attempt cannot be ordered: {result_path}"
            )
        node_safe, step_token = result_path.parts[-4], result_path.parts[-3]
        attempts_by_phase.setdefault((node_safe, step_token), []).append(
            (run_order[executor], executor, result_path, result))

    for (node_safe, step_token), attempts in sorted(attempts_by_phase.items()):
        recorded_state = node_states.get(node_safe)
        if (isinstance(recorded_state, dict)
                and str(recorded_state.get(step_token) or "") == "skipped_certified"):
            continue
        _, executor, result_path, result = max(attempts, key=lambda item: item[0])
        status = str(result.get("status") or "").strip().lower()
        if status != "pass":
            raise RuntimeError(
                f"cannot mark orchestration pass: the latest attempt of {node_safe}/{step_token} "
                f"is {status!r} (executor {executor}, {result_path})"
            )
        required = result.get("required_outputs")
        missing = [
            ref for ref in (required if isinstance(required, list) else [])
            if isinstance(ref, str) and ref.strip()
            and not (repo_root / _normalize_rel_posix(ref.strip())).exists()
        ]
        if missing:
            raise RuntimeError(
                f"cannot mark orchestration pass: {node_safe}/{step_token} declares "
                f"required_outputs that are not on disk: {missing}"
            )


_STEP_META_FILENAME = STAGE_META_FILENAME_BY_STEP

STEP_REQUIRED_VALIDATION_STAGES: dict[str, frozenset[str]] = {
    "compile": frozenset({"compile"}),
    # Generate accepts only `post_generate` (the validator stage that
    # checks generation outputs). `post_build` is Build's
    # responsibility — its semantics are "validation after the build
    # artifact exists" and applying it to a Generate terminal status
    # would conflate phase boundaries. No production code or test
    # exercises `generate + post_build`; the previous permissive set was
    # a tolerance with no legitimate use case.
    "generate": frozenset({"post_generate"}),
    "build": frozenset({"post_build"}),
    "validate": frozenset({"post_execute", "pre_judge"}),
}

_RETRY_DECISION_REQUIRED_KEYS: tuple[str, ...] = (
    "issue_severity",
    "repair_strategy",
    "repair_target_agent_run_id",
    "new_agent_run_id",
    "repair_reason",
)


def _validate_step_meta_payload(meta_data: dict[str, Any], *, step_token: str, meta_ref: str) -> None:
    meta_filename = _STEP_META_FILENAME[step_token]
    missing_keys = missing_required_meta_keys(meta_data, step_token=step_token)
    if missing_keys:
        raise ValueError(
            f"{meta_filename} missing required keys: {missing_keys} "
            f"(phase={step_token} substep=verify ref={meta_ref})"
        )
    # Value types come from the one canonical definition (tools/meta_contracts). The first
    # violation in canonical key order is raised.
    violations = stage_meta_type_violations(meta_data, step_token=step_token)
    if violations:
        raise ValueError(f"{meta_filename} {violations[0]}: {meta_ref}")


def _effective_pass_substep_run_ids(
    payload: dict[str, Any],
    *,
    run_records: dict[str, dict[str, Any]],
    node_key: str,
    step_token: str,
) -> tuple[list[str], dict[str, set[str]]]:
    substep_run_ids = payload.get("substep_agent_run_ids")
    if not isinstance(substep_run_ids, list) or not substep_run_ids:
        raise ValueError(f"pass step_result for {step_token} requires non-empty substep_agent_run_ids")

    listed_run_ids: list[str] = []
    listed_run_id_set: set[str] = set()
    for idx, substep_run_id in enumerate(substep_run_ids):
        if not isinstance(substep_run_id, str) or not substep_run_id.strip():
            raise ValueError(f"substep_agent_run_ids[{idx}] must be non-empty string")
        token = substep_run_id.strip()
        if token in listed_run_id_set:
            raise ValueError(f"substep_agent_run_ids must not contain duplicates: {token}")
        listed_run_ids.append(token)
        listed_run_id_set.add(token)

        substep_record = run_records.get(token)
        if not isinstance(substep_record, dict):
            raise ValueError(f"missing substep run record: {token}")
        role = str(substep_record.get("agent_role") or "").strip().lower()
        if role != "substep":
            raise ValueError(f"listed run must be substep role: {token}")
        record_node_key = str(substep_record.get("node_key") or "").strip()
        if record_node_key != node_key:
            raise ValueError(f"listed substep run node_key mismatch: {token}")
        record_step = str(substep_record.get("step") or "").strip().lower()
        if record_step != step_token:
            raise ValueError(f"listed substep run step mismatch: {token}")

    failed_substeps = payload.get("failed_substeps", [])
    if not isinstance(failed_substeps, list):
        raise ValueError("step_result.failed_substeps must be list")
    explicit_failed_run_ids: set[str] = set()
    for idx, failed_run_id in enumerate(failed_substeps):
        if not isinstance(failed_run_id, str) or not failed_run_id.strip():
            raise ValueError(f"failed_substeps[{idx}] must be non-empty string")
        token = failed_run_id.strip()
        if token not in listed_run_id_set:
            raise ValueError(f"failed_substeps[{idx}] must be listed in substep_agent_run_ids: {token}")
        failed_status = str(run_records[token].get("status") or "").strip().lower()
        if failed_status == "pass":
            raise ValueError(f"failed_substeps[{idx}] must reference actual non-pass run: {token}")
        explicit_failed_run_ids.add(token)

    retry_decisions = payload.get("retry_decisions", [])
    if retry_decisions is None:
        retry_decisions = []
    if not isinstance(retry_decisions, list):
        raise ValueError("step_result.retry_decisions must be list when provided")
    replaced_run_ids: set[str] = set()
    adopted_run_ids: set[str] = set()
    for idx, item in enumerate(retry_decisions):
        if not isinstance(item, dict):
            raise ValueError(f"retry_decisions[{idx}] must be object")
        missing_keys = [
            key for key in _RETRY_DECISION_REQUIRED_KEYS
            if not isinstance(item.get(key), str) or not str(item.get(key)).strip()
        ]
        if missing_keys:
            raise ValueError(
                f"retry_decisions[{idx}] missing required string keys: {missing_keys}"
            )
        repair_target = str(item["repair_target_agent_run_id"]).strip()
        new_run_id = str(item["new_agent_run_id"]).strip()
        if repair_target not in listed_run_id_set:
            raise ValueError(
                f"retry_decisions[{idx}].repair_target_agent_run_id must be listed in substep_agent_run_ids: {repair_target}"
            )
        if new_run_id not in listed_run_id_set:
            raise ValueError(
                f"retry_decisions[{idx}].new_agent_run_id must be listed in substep_agent_run_ids: {new_run_id}"
            )
        if repair_target == new_run_id:
            raise ValueError(f"retry_decisions[{idx}] must replace a different run_id")
        if repair_target in replaced_run_ids:
            raise ValueError(f"retry_decisions must not replace the same run twice: {repair_target}")
        repair_target_status = str(run_records[repair_target].get("status") or "").strip().lower()
        if repair_target_status == "pass":
            raise ValueError(
                f"retry_decisions[{idx}].repair_target_agent_run_id must reference actual non-pass run: {repair_target}"
            )
        replaced_run_ids.add(repair_target)
        adopted_run_ids.add(new_run_id)

    effective_run_ids: list[str] = []
    for run_id in listed_run_ids:
        if run_id in replaced_run_ids or run_id in explicit_failed_run_ids:
            continue
        substep_record = run_records[run_id]
        substep_status = str(substep_record.get("status") or "").strip().lower()
        if substep_status != "pass":
            raise ValueError(
                f"non-pass substep {run_id} must be excluded by failed_substeps or retry_decisions before step_result can pass"
            )
        effective_run_ids.append(run_id)

    for run_id in adopted_run_ids:
        substep_status = str(run_records[run_id].get("status") or "").strip().lower()
        if substep_status != "pass":
            raise ValueError(f"retry_decisions new_agent_run_id must be pass for step_result pass: {run_id}")
    if not effective_run_ids:
        raise ValueError(f"pass step_result for {step_token} requires at least one effective pass substep")

    return effective_run_ids, {
        "listed_run_ids": listed_run_id_set,
        "explicit_failed_run_ids": explicit_failed_run_ids,
        "replaced_run_ids": replaced_run_ids,
        "adopted_run_ids": adopted_run_ids,
    }


def _validate_step_result_payload(
    repo_root: Path,
    orchestration_id: str,
    *,
    node_key: str,
    step: str,
    agent_run_id: str,
    payload: dict[str, Any],
) -> None:
    step_token = step.strip().lower()
    status = payload.get("status")
    status_token = status.strip().lower() if isinstance(status, str) else ""

    # validation_stage check (at terminal of generate/build/validate)
    if step_token in STEP_REQUIRED_VALIDATION_STAGES and status_token in TERMINAL_STATUSES:
        allowed = STEP_REQUIRED_VALIDATION_STAGES[step_token]
        validation_stage = payload.get("validation_stage")
        if not isinstance(validation_stage, str) or validation_stage.strip() not in allowed:
            raise ValueError(
                f"terminal step_result for {step_token} requires validation_stage in "
                f"{sorted(allowed)}; status={status_token!r} validation_stage={validation_stage!r}"
            )

    # `_pre_phase_complete_judge_checks`, which read `validate.judge`'s `semantic_review.json`
    # against a terminal `validate` step result, went with that substep in issue #724 PR-2.

    # the following is the existing substep verification (compile/generate/validate only)
    if step_token not in SUBSTEP_AWARE_STEPS:
        return
    if status_token != "pass":
        return

    run_records = _load_run_records(_orchestration_root(repo_root, orchestration_id))
    effective_run_ids, _ = _effective_pass_substep_run_ids(
        payload,
        run_records=run_records,
        node_key=node_key,
        step_token=step_token,
    )
    required_outputs = payload.get("required_outputs")
    declared_outputs = {item.strip() for item in required_outputs if isinstance(item, str) and item.strip()}

    # Z2 pure substeps (leaf_mode==pure, the M-C producer + M-D verify reviewer) hold NO write
    # authority: each finalizes with output_refs==[] (enforced bidirectionally by
    # _validate_terminal_run_payload) and the HOST authors the phase's deliverables AFTER the child
    # window closes. So a pure substep is exempt from the non-empty-output_refs requirement and its
    # (empty) refs contribute nothing to substep_outputs. When ANY effective substep is pure the
    # whole phase is host-authored: the deliverables in required_outputs (model/runner/source_meta)
    # are not published by any substep, so they are vouched by ON-DISK EXISTENCE instead of substep
    # coverage below (anti-mock-green: a declared-but-absent deliverable still fails). Detected from
    # the launch request (the single source the producer stamps), mirroring the pure carve-out in
    # _validate_terminal_run_payload — the two per-record/per-step contracts would otherwise be
    # mutually contradictory for a pure phase (empty refs required there, non-empty required here).
    substep_outputs: set[str] = set()
    phase_is_pure = False
    for substep_run_id in effective_run_ids:
        substep_record = run_records[substep_run_id]
        launch_req = _read_launch_request_payload(
            repo_root, orchestration_id, agent_run_id=substep_run_id)
        if isinstance(launch_req, dict) and _leaf_contract_is_host_rendered_request(launch_req):
            phase_is_pure = True
            continue
        output_refs = substep_record.get("output_refs")
        if not isinstance(output_refs, list) or not output_refs:
            raise ValueError(f"substep {substep_run_id} must publish non-empty output_refs")
        for output_ref in output_refs:
            if isinstance(output_ref, str) and output_ref.strip():
                substep_outputs.add(output_ref.strip())

    # meta file verification (only on pass of plan/generate)
    if step_token in _STEP_META_FILENAME:
        meta_filename = _STEP_META_FILENAME[step_token]
        meta_refs = [ref for ref in declared_outputs if ref.endswith(meta_filename)]
        if not meta_refs:
            raise ValueError(
                f"pass step_result for {step_token} requires required_outputs to include final {meta_filename}"
            )
        if len(meta_refs) != 1:
            raise ValueError(
                f"pass step_result for {step_token} requires exactly one final {meta_filename} in required_outputs"
            )
        meta_ref = meta_refs[0]
        # A pure phase's final meta is host-authored (not in any substep's output_refs); its vouch
        # is on-disk existence + the schema check below, which run unconditionally.
        if meta_ref not in substep_outputs and not phase_is_pure:
            raise ValueError(
                f"step_result.required_outputs must reference final {meta_filename} from effective substep output_refs: {meta_ref}"
            )
        meta_path = repo_root / meta_ref
        if not meta_path.exists():
            raise ValueError(
                f"{meta_filename} not found at output_ref: {meta_ref}"
            )
        try:
            meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{meta_filename} is not valid JSON: {meta_ref}"
            ) from exc
        if not isinstance(meta_data, dict):
            raise ValueError(f"{meta_filename} must be a JSON object: {meta_ref}")
        _validate_step_meta_payload(
            meta_data,
            step_token=step_token,
            meta_ref=meta_ref,
        )

    missing_outputs = sorted(ref for ref in declared_outputs if ref not in substep_outputs)
    if missing_outputs:
        if phase_is_pure:
            # Pure phase: the host authors the deliverables after the child windows close, so they
            # are absent from every substep's output_refs. Vouch them by on-disk existence instead
            # (a declared-but-unauthored deliverable still fails — the host must actually have
            # written it). Any that a non-pure substep DID publish are already covered above.
            absent = sorted(ref for ref in missing_outputs
                            if not (repo_root / _normalize_rel_posix(ref)).exists())
            if absent:
                raise ValueError(
                    "pure step_result.required_outputs were not authored on disk by the host: "
                    + ", ".join(absent)
                )
        else:
            raise ValueError(
                "step_result.required_outputs must be satisfied by substep output_refs: "
                + ", ".join(missing_outputs)
            )


def _probe_existing_directory_writable(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"{path} does not exist"
    if not path.is_dir():
        return False, f"{path} is not a directory"
    try:
        with tempfile.NamedTemporaryFile(
            dir=str(path),
            prefix=".codex-orchestration-preflight-",
            delete=True,
        ) as handle:
            handle.write(b"probe")
            handle.flush()
    except OSError as exc:
        return False, f"{path}: {exc}"
    return True, str(path)


def _probe_codex_home_writable() -> dict[str, Any]:
    """Check the canonical Codex state home without silently accepting conflicts."""
    canonical = os.environ.get("CODEX_HOME")
    legacy = os.environ.get("SPEC2SCIENCE_HOME")
    if (isinstance(canonical, str) and canonical.strip()
            and isinstance(legacy, str) and legacy.strip()
            and Path(canonical).expanduser().resolve() != Path(legacy).expanduser().resolve()):
        return {
            "name": "codex_home_writable", "pass": False,
            "detail": "CODEX_HOME and deprecated SPEC2SCIENCE_HOME conflict",
        }
    raw = canonical if isinstance(canonical, str) and canonical.strip() else legacy
    source = (
        "env:CODEX_HOME" if isinstance(canonical, str) and canonical.strip()
        else ("env:SPEC2SCIENCE_HOME (deprecated)" if isinstance(legacy, str) and legacy.strip()
              else "default:~/.codex")
    )
    codex_home = (
        Path(raw).expanduser()
        if isinstance(raw, str) and raw.strip()
        else (Path.home() / ".codex")
    )
    if codex_home.exists():
        ok, detail = _probe_existing_directory_writable(codex_home)
        return {
            "name": "codex_home_writable",
            "pass": ok,
            "detail": f"{source} path={codex_home} detail={detail}",
        }
    parent = codex_home.parent
    ok, detail = _probe_existing_directory_writable(parent)
    detail_text = (
        f"{source} path={codex_home} parent={parent} "
        + ("parent writable; codex_home can be created" if ok else f"parent not writable: {detail}")
    )
    return {"name": "codex_home_writable", "pass": ok, "detail": detail_text}


# `WORKFLOW_HOMES_ROOT_ENV` and the resolver below are IMPORTED from
# `tools/operator_private_root.py`, not defined here. TWO subtrees of the operator-private root are
# relocatable, and each name moves exactly one of them:
#
#   * `SPEC2SCIENCE_WORKFLOW_HOMES_ROOT`  -> `~/.spec2science/homes` (this module writes it)
#   * `SPEC2SCIENCE_START_CLAIM_ROOT`     -> `~/.spec2science/start_claims` (`tools/run_workflow.py`)
#
# Both exist for the same reason: a test, or an operator with a reason, needs the
# tree somewhere other than the real `~/.spec2science`. (`SPEC2SCIENCE_HOME` is deliberately NOT
# reused — it already means "the operator's `~/.codex`" on the codex auth path, and one
# name with two meanings is how two resolutions silently drift apart.) The ROOT itself is
# deliberately not relocatable: it is the Bash read guard's anchor.
#
# The homes one resolves in the hooks module because the side that CREATES that tree and
# the side that FORBIDS a leaf from reading it have to resolve the same location — the
# arrangement `backend_credential_home_paths` already uses. When `WORKFLOW_HOMES_ROOT_ENV`
# lived here, the Bash guard knew only `~/.spec2science`, so setting the override moved the
# homes out from under the one protected root that covers every orchestration and a leaf
# could read a SIBLING run's transcript (measured). Codex found that; the fix is that
# there is now one resolver. (A third relocatable subtree, the dismiss-violation operator
# token store, was resolved the same way from issue #132 until issue #176 deleted the
# subcommand it served.)
#
# `start_claims` is the one that is NOT a protected read root, and that is a judgment
# rather than an omission: the files are 0-byte advisory flocks, and a leaf that reads one
# is no closer to reporting a substep done (`AGENTS.md` §Development premises, rule 1-e).
# It still resolves through `operator_secret_root()` so the default follows the root.

# The per-backend subdirectory names under `<homes-root>/<orchestration_id>/`. One
# orchestration may use BOTH backends — `llm.yaml` selects per phase / per substep —
# so the backend must be a directory level rather than part of the orchestration
# directory itself.
#
# `http` holds the lineage homes of an HTTP `workshop leaf` (issue #534 PR-J3-b): one directory
# for both HTTP providers, whose host loop is one transport (`leaf_contract.WORKSHOP_LINEAGE_BACKENDS`).
WORKFLOW_BACKEND_HOME_DIRNAMES = ("claude", "codex", "http")

# The owner marker's filename, written in the orchestration directory that is the
# PARENT of the bound backend homes. `tools/prune_workflow_homes.py` reads it.
WORKFLOW_HOME_OWNER_FILENAME = "owner.json"


def _workflow_homes_root() -> Path:
    """`~/.spec2science/homes` — the durable root of every isolated backend home.

    A thin alias over `tools/operator_private_root.py::workflow_homes_root`, kept as a name because
    this module's tests and `tools/prune_workflow_homes.py` patch it, and because the
    module-local spelling is what the preparers read. The RESOLUTION is not duplicated,
    which used to matter twice over: the leaf's Bash read guard listed whatever this
    returns as a protected read root, so the tree that got created was by construction the
    tree that got guarded, including when the override moved it. That guard and
    `protected_host_read_roots` went with the leaf hook layer in Z4 (issue #171) — a pure
    leaf holds no tool to read with — so what single resolution buys now is that the
    preparers, the pruner and this module cannot disagree about where the homes live.

    Under the operator-secret root by default, which is where the single protected-root
    entry used to cover every orchestration's home rather than only the one the
    per-orchestration resolver can name from metadata.
    """
    return _hooks_workflow_homes_root()


def _require_usable_private_root_override(env_name: str, root: Path, subject: str,
                                          repo_root: Path | None = None) -> None:
    """Refuse an override that names a location this process cannot honour.

    `subject` names the tree in the operator's words ("isolated claude home root"); it is a
    parameter because the backend label varies. A second parameter named what the writing
    side was about to create, for the operator-token store that shared this refusal until
    issue #176 deleted it; with the homes root the only caller, the sentence is fixed.

    The resolvers in `tools/operator_private_root.py` stay TOTAL — they feed the Bash read guard,
    and a hook that raises while deciding a read is worse than one that guards a path
    nobody writes to. This is the creation side, which is where failing closed is
    available, and it is shared by every writer under the operator-private root so the two
    refusals cannot drift apart.

    Four conditions, in the order they are checked:

      * ABSOLUTE OR NOTHING. A relative override resolves against whoever asks, and the
        two sides that must agree about the tree do not share a working directory: the
        writer is the conductor, the Bash read guard is a hook process the CLI spawns.
        `.absolute()` in the resolver makes the value usable, which is NOT the same as
        making it one tree — it silently becomes a different absolute path per caller.
      * NO CONTAINMENT WITH THE CHECKOUT, in EITHER direction. The two halves are
        refused for different reasons and both are measured.

        INSIDE was a leaf-facing rule and the others are not.
        It was written when a leaf held tools and two layers answered for the tree: the
        Bash read guard covered the homes root wherever it went, while the READ TOOL was
        a different layer — `_write_read_access_manifest` granted every agentic leaf
        `docs/` and `spec/` unconditionally and never consulted
        `protected_host_read_roots` — so leaf transcripts under a homes root at
        `<repo>/spec/homes/<oid>/<backend>/projects/` were Read-tool reachable, and
        reading an earlier leaf's transcript is the past-run state the workflow forbids.
        Measured for this caller at the time. Z4 (issue #171) deleted both layers with
        the agentic leaf, and a claude pure leaf holds no tool to reach the tree with;
        issue #227 then took the checkout itself out of the sandbox (an empty tmpfs at
        `repo_root`), so the one leaf that does hold tools — a codex pure leaf — cannot
        reach a homes root inside the checkout either. The rule is KEPT because a tree
        inside the checkout pollutes the FS-diff and the operator's `git status` whatever
        reads it, and because it refuses at launch what the mount set would otherwise
        merely hide. The rule was written for TWO
        relocatable trees, and the other one — the dismiss-violation operator token
        store, whose measured instance was a leaf reading the token and approving the
        `unauthorized_write_violation` its own `substep` produced — was deleted by issue
        #176 along with the subcommand.

        CONTAINING the checkout is refused because the run cannot work at all. These
        roots are exempt from the containment drop (`_command_reads_protected_host_path`
        keeps them so the guard is not lost), so a root ABOVE the checkout makes every
        in-repo path a path under a protected root — and the guard matches the command's
        tokens, not only its read targets. MEASURED through `evaluate_common_policy`, the
        leaf hook policy deleted in Z4 (issue #171) — so this is a record of what the guard
        did, not a claim about today: with either root set to the checkout's parent, `cat README.md`, `ls`, `python3
        tools/x.py` and even `echo hi` all BLOCK. There is no working configuration to
        preserve, so refusing costs nothing and turns a total, unexplained failure at the
        first leaf into one refusal naming the variable. `docs/RUNBOOK.md` used to
        describe this as costing "every recursive in-repo read", which understated it by
        a wide margin; found by a round-5 reviewer.
      * THE PARENT MUST EXIST. With an override in play only the root itself is created:
        the override names a location the caller chose, and silently building a deep tree
        under a typo'd path is the failure mode that costs most.
      * A REAL DIRECTORY. An override naming an existing regular file passes every check
        above (its parent exists), and `mkdir(exist_ok=True)` then raises
        `FileExistsError` AFTER `workspace/orchestrations/<oid>/` and a `running` meta
        are on disk — exactly the half-initialized run the POSITION of this call is
        supposed to prevent. Also found by Codex, and also measured.
    """
    raw_override = os.environ.get(env_name, "").strip()
    if not Path(raw_override).expanduser().is_absolute():
        raise ValueError(
            f"{env_name} must be an absolute path (got "
            f"{raw_override!r}): a relative one resolves against each process's "
            "working directory, so the conductor that creates the home and the hook "
            "that forbids reading it would resolve different trees"
        )
    if repo_root is not None:
        repo_resolved = _resolve_lenient(Path(repo_root))
        root_resolved = _resolve_lenient(root)
        if root_resolved == repo_resolved or repo_resolved in root_resolved.parents:
            where = ("IS the repository root" if root_resolved == repo_resolved
                     else f"is under {repo_resolved}")
            raise ValueError(
                f"{subject} must not be inside the repository: {root_resolved} {where} "
                f"(set by {env_name}). Every agentic leaf's read "
                "manifest grants docs/ and spec/ unconditionally and the Read tool does "
                "not consult the Bash guard's protected roots, so anything the workflow "
                "keeps outside the checkout stops being out of reach the moment it moves "
                "inside it. Point it at a directory outside the checkout"
            )
        if root_resolved in repo_resolved.parents:
            raise ValueError(
                f"{subject} must not contain the repository: {repo_resolved} is under "
                f"{root_resolved} (set by {env_name}). These roots are exempt from the "
                "containment rule so the Bash guard is not lost when one of them "
                "overlaps the checkout — which means a root ABOVE the checkout makes "
                "every in-repo path a protected path, and every Bash command in workflow "
                "mode fails closed (measured: `cat README.md`, `ls`, even `echo hi`). "
                "There is no working configuration here to preserve. Point it at a "
                "directory that neither contains nor sits inside the checkout"
            )
    # Load-bearing for the MESSAGE: without it the ancestors loop of
    # `_create_workflow_backend_home` fails on a non-directory at the same path with an
    # `OSError` that names neither the variable nor the reason. It WAS unpinned for this
    # caller (a round-3 census measured `test_orchestration_runtime.py` staying green while
    # only the token-side subtests failed); issue #176 ported that token-side row to the
    # homes caller, so `test_operator_private_root.py::WorkflowHomesRootOverrideTests::
    # test_an_override_naming_an_existing_file_is_refused_before_the_first_write` now goes
    # red for both spellings when this check is removed. There is no survivor left to record.
    parent = root.parent
    if not parent.exists():
        raise ValueError(
            f"{subject}'s parent does not exist: {parent} "
            f"(set by {env_name})"
        )
    if (root.exists() or root.is_symlink()) and not root.is_dir():
        raise ValueError(
            f"{subject} is not a directory: {root} (set by {env_name}). Refused HERE, "
            "before anything is created: the creation call would otherwise raise after "
            "the orchestration directory and its running metadata are already on disk"
        )


def _workflow_backend_home_path(orchestration_id: str, backend: str) -> Path:
    """`<homes-root>/<orchestration_id>/<backend>` — deterministic, and checked.

    DETERMINISTIC ON PURPOSE, replacing `tempfile.mkdtemp`. The randomness the mkdtemp
    names carried was a defense for `/tmp`, which is world-writable: any local user could
    pre-create a predictable name and win the race for the directory the home is about to
    become. Under a 0700 root owned by this uid that threat is gone, and each lineage home
    inside the backend directory is created exclusively by its cold launch.

    The orchestration id is the operator's (`--orchestration-id`) or host-minted, and the
    backend a host constant (`WORKFLOW_BACKEND_HOME_DIRNAMES`); neither is checked here
    (issue #643). `tools/prune_workflow_homes.py` keeps its own id rule.
    """
    oid = (orchestration_id or "").strip()
    return _workflow_homes_root() / oid / backend


def _create_workflow_backend_home(repo_root: Path, orchestration_id: str,
                                  backend: str, label: str) -> Path:
    """Create `<homes-root>/<oid>/<backend>` and return it.

    Each missing ancestor, and the backend directory itself, is created 0700 (`mkdir`'s mode
    argument is masked by the umask, so a `chmod` follows); the operator-private tree is the
    operator's own, and its state is not policed beyond that (issue #643). A backend directory
    that is already there — a run that crashed between creating it and recording it, or a
    reused orchestration id — is adopted: each lineage home inside it is created exclusively
    by its cold launch. Then the owner marker is written in the PARENT
    (`_write_workflow_home_owner`).

    Called under the caller's `_orchestration_meta_exclusive_lock`, so two concurrent child
    launches of the same orchestration do not both create it.
    """
    home = _workflow_backend_home_path(orchestration_id, backend)
    root = _workflow_homes_root()
    override_used = bool(os.environ.get(WORKFLOW_HOMES_ROOT_ENV, "").strip())
    # With an override in play, only the root itself is created: the override names a
    # location the caller chose, and silently building a deep tree under a typo'd path is
    # the failure mode that costs most. Without one, `~/.spec2science` may legitimately not
    # exist yet (a host that has never run `init_orchestration`).
    ancestors: list[Path] = []
    if override_used:
        # The conditions live in a shared helper. The `subject` WORDING is per-caller so
        # the refusal names the operator's own tree: it carries the backend label, and the
        # helper served a second relocatable tree (the operator token store) until issue
        # #176 deleted it.
        _require_usable_private_root_override(
            WORKFLOW_HOMES_ROOT_ENV, root, f"isolated {label} home root",
            repo_root=repo_root)
        ancestors.append(root)
    else:
        ancestors.append(root.parent)  # ~/.spec2science
        ancestors.append(root)         # ~/.spec2science/homes
    ancestors.append(home.parent)      # <homes-root>/<oid>
    for path in (*ancestors, home):
        if path.is_dir():
            continue
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    _write_workflow_home_owner(repo_root, orchestration_id, home.parent, label)
    return home


def _refresh_workflow_home_owner_on_reuse(repo_root: Path, orchestration_id: str,
                                          home: Path, label: str) -> None:
    """Re-write the owner marker for a home that ALREADY exists.

    The marker's whole reason for being refreshed — a checkout that moved, so the recorded
    path locates nothing — is a RESUME scenario, and the refresh lived only on the
    creation path once. A moved checkout's run then stayed `unverifiable` to the prune tool
    forever, and `--allow-unverifiable` deletes such a home without the locked status
    re-read: `prune --all --allow-unverifiable --delete` removed the home of an orchestration
    whose recorded status was `running` (reproduced end to end).

    SCOPED TO OUR OWN LAYOUT. A home recorded before issue #64 lives wherever it lived — a
    `mkdtemp` directory directly under `/tmp`, whose parent is nobody's orchestration
    directory — and writing a marker there would litter a shared tmpfs with a file claiming
    an orchestration id. So the refresh runs only when the home sits exactly where this code
    puts one.
    """
    expected = _workflow_backend_home_path(orchestration_id, home.name)
    try:
        if home.resolve() != expected.resolve():
            return
    except (OSError, RuntimeError, ValueError):
        return
    _write_workflow_home_owner(repo_root, orchestration_id, home.parent, label)


def _write_workflow_home_owner(repo_root: Path, orchestration_id: str,
                               owner_dir: Path, label: str) -> None:
    """Record which checkout owns `<homes-root>/<oid>/`, for the prune tool.

    Written in the PARENT of the backend directory, which is what makes it tamper-proof
    against a leaf: the bwrap profile binds a codex leaf's LINEAGE home
    `<oid>/codex/<lineage_id>` (issue #245) and nothing above it — two levels below this
    file — and the scaffolding bwrap creates for that bind target lives on the namespace's
    own tmpfs, so a leaf writing next to its home writes into the sandbox, not onto the
    host.

    A LOCATOR, NOT AN AUTHORITY. It answers "which checkout should I ask about this
    orchestration", and the answer to "may this be deleted" comes from that checkout's
    `orchestration_meta.json` — the file the host writes under a lock. Treating the marker
    as the authority would let anything that could write it decide a deletion.

    A marker naming another checkout is REPLACED by this one (the operator who moved a
    checkout and resumed is the ordinary case). Two checkouts running one orchestration id
    on one homes root is the operator's own configuration and is not policed (issue #643).
    """
    marker_path = owner_dir / WORKFLOW_HOME_OWNER_FILENAME
    resolved_repo = str(Path(repo_root).resolve())
    payload = json.dumps(
        {
            "schema": 1,
            "orchestration_id": orchestration_id,
            "repo_root": resolved_repo,
            "created_at": _utc_now_iso(),
        },
        sort_keys=True,
    ).encode("utf-8")
    # A marker naming another checkout is REPLACED, not kept: leaving the stale path in place
    # would make the locator locate nothing forever — the prune tool would answer `unverifiable` for a run whose owner is
    # perfectly identifiable, which is fail-closed but useless. A marker that AGREES is
    # left alone rather than rewritten, so `created_at` keeps meaning "when this
    # orchestration directory was created" instead of "when a backend last launched".
    if marker_path.is_file():
        try:
            current = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            current = None
        stale_owner = (
            isinstance(current, dict)
            and isinstance(current.get("repo_root"), str)
            and current.get("repo_root") != resolved_repo
        )
        if stale_owner:
            marker_path.unlink()
    _secure_backend_home_file(
        marker_path,
        payload,
        label=label,
        verify_existing=False,
    )


def _orchestration_meta_path_exists(repo_root: Path, orchestration_id: str) -> bool:
    """Whether `repo_root` still records this orchestration — the "is it live" question.

    Deliberately existence-only and deliberately not locked: this decides whether a
    previous owner is still THERE, not what its status is, and taking a lock on a path
    a marker named would create files under it (the mistake the prune tool's report-only
    mode already made once).
    """
    try:
        return (repo_root / "workspace" / "orchestrations" / orchestration_id
                / "orchestration_meta.json").is_file()
    except (OSError, ValueError):
        return False

def _secure_backend_home_file(
    path: Path,
    data: bytes | None = None,
    *,
    label: str = "Codex",
    verify_existing: bool = True,
) -> None:
    """Create or validate a private regular file without following symlinks.

    ``verify_existing=False`` seeds ``data`` when the file is absent but accepts
    whatever an already-present file contains. That is required for a file the
    BACKEND itself rewrites: the Claude private home's ``.claude.json`` (gone with the
    agentic leaf in Z4, issue #171) gained cached feature flags and a machine id on
    first launch (measured, CLI 2.1.235), so a warm resume re-preparing the same home would otherwise fail with
    "differs from its verified source". Files the workflow PINS (``settings.json``,
    Codex's ``hooks.json`` / ``config.toml``) keep the default and are verified.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileExistsError:
        try:
            info = path.lstat()
        except OSError as exc:
            raise ValueError(f"isolated {label} file is inaccessible: {path}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError(f"isolated {label} file must be a regular file: {path}")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError(f"isolated {label} file has unsafe ownership or mode: {path}")
        if verify_existing and data is not None and path.read_bytes() != data:
            raise ValueError(f"isolated {label} file differs from its verified source: {path}")
        return
    try:
        # Write the WHOLE payload (os.write may be short) and remove the file if any
        # part of it fails: a half-written or empty leftover would otherwise be a
        # permanent poison pill — every later call takes the FileExistsError branch
        # above and raises "differs from its verified source" forever.
        try:
            offset = 0
            while data is not None and offset < len(data):
                offset += os.write(fd, data[offset:])
            os.fchmod(fd, 0o600)
        except BaseException:
            # Clear the descriptor BEFORE closing it. `os.close` does not retry EINTR
            # and the kernel frees the descriptor even when it reports an error, so
            # closing first and clearing after would leave `fd >= 0` and let `finally`
            # close a number another thread may already have reused — in this module
            # that number can be the `orchestration_meta.json` flock held by
            # `_prepare_codex_workflow_home`'s caller, silently dropping the lock
            # mid-critical-section.
            victim, fd = fd, -1
            # Both cleanups are best-effort and independently guarded: neither may
            # replace the write error the caller needs to see, and a failing close must
            # not skip the unlink — that would leave the half-written file behind, i.e.
            # reinstate the permanent poison pill this branch exists to prevent. Unlink
            # first for the same reason; the inode survives until the close anyway.
            try:
                path.unlink()
            except OSError:
                pass
            try:
                os.close(victim)
            except OSError:
                pass
            raise
    finally:
        if fd >= 0:
            os.close(fd)


def codex_workshop_profile_kwargs(isolation: Mapping[str, str]) -> dict[str, Any]:
    """The bwrap profile kwargs a prepared codex WORKSHOP lineage home implies — ONE spelling,
    the codex twin of `claude_workshop_profile_kwargs` (issue #534 step 2 PR-2). `config/` (the
    `CODEX_HOME`) and `tree/` are the two rw binds; the host-authored `config.toml` and the
    operator's `auth.json` are remounted read-only INTO `config/`, as the pure lineage remounts
    them into its home, and no credential is bound read-write (codex reads `auth.json`; the pure
    leaf has always run with it read-only)."""
    return {
        "backend_ro_mappings": [
            (isolation["auth"], isolation["auth_destination"]),
            (isolation["config_toml"], isolation["config_toml"]),
        ],
        "backend_rw_override": [isolation["config"], isolation["tree"]],
        "env_overrides": {"CODEX_HOME": isolation["config"]},
        "readonly": False,
        "workdir": isolation["tree"],
        "leaf_mode": WORKSHOP_PROFILE_LEAF_MODE,
    }


def _prepare_codex_workflow_home(repo_root: Path, orchestration_id: str, lineage_id: str,
                                 *, resume: bool) -> dict[str, str] | None:
    """Prepare the private Codex home of ONE `workshop leaf` lineage of an orchestration (issue
    #534 step 2 PR-2): the lineage home is `workshop_tree.LineagePaths(backend="codex").home` —
    `config/` (0700, the leaf's `CODEX_HOME`, holding the `config.toml` that marks the TREE
    untrusted and the `auth.json` placeholder) beside the work directories `make_work_dirs`
    creates (`tree/`, `staged/`, `syntax/`, `snapshot/`). A cold preparation is the CONDUCTOR's
    (`Conductor._open_workshop_lineage`, before the launch is recorded, as for claude);
    `record_launch` prepares it warm on both of the lineage's launches. Returns `{home, config,
    tree, auth, auth_destination, config_toml, lineage_id, homes_root}`
    (`_prepare_codex_workshop_lineage`).

    Two levels, and the leaf sees only the lower one:

      * `<homes-root>/<oid>/codex/` is the orchestration's CONTAINER. It is recorded in
        `orchestration_meta.json#codex_workflow_home`, carries the owner marker in its
        parent, and goes with the whole `<oid>/` entry when `tools/prune_workflow_homes.py`
        removes one. It is NOT bound into any leaf and is NOT `CODEX_HOME`.
      * `<container>/<lineage_id>/` is the LINEAGE HOME of one lineage: its cold launch plus
        every warm `codex exec resume` of it. A second lineage's leaf cannot list, let alone
        read, a sibling: the sibling is simply not mounted. This is what closed the shared
        per-orchestration home TODO recorded from issue #227 (issue #245).

    `lineage_id` is the `agent_run_id` of the lineage's cold launch (a host-minted id; a
    recorded one is refused by `record_launch` unless it is a plain token), and it becomes a
    path segment outside the repository.

    ``resume`` decides what a lineage directory's presence means: cold, the lineage home is
    created EXCLUSIVELY (one already there is refused, not adopted); warm, it must ALREADY
    exist, and when it does not this returns ``None`` — the sentinel `record_launch` turns into
    `workshop_lineage_home_missing` BEFORE any durable launch mutation. It is never created on
    a warm preparation: a `codex exec resume` against an empty home fails before its first
    request (measured: `no rollout found for thread id`).

    The container part is unchanged from the per-orchestration design: its path is persisted
    so the same tree is reused across `--resume`, a vanished container (an operator prune, a
    lost filesystem) is re-created at the same path — with no lineage inside, so every
    recorded lineage is cold-fallback only — and an existing one has its owner marker
    refreshed on every preparation. Authentication is deliberately *not*
    copied: the bwrap profile binds the operator's `auth.json` read-only over the placeholder.
    """
    lineage_token = (lineage_id or "").strip() if isinstance(lineage_id, str) else ""
    meta_path = _orchestration_root(repo_root, orchestration_id) / "orchestration_meta.json"
    # The container is one 0700 directory per orchestration, named DETERMINISTICALLY
    # since issue #64 (`<homes-root>/<oid>/codex`): the randomness it replaced was a
    # defense for `/tmp`, which is world-writable, and under a 0700 root owned by this
    # uid the exclusive `os.mkdir` replaces it. The metadata lock serializes concurrent
    # child launches that would otherwise both reach that creation — and both reach the
    # lineage creation below, which is why the whole function runs under it.
    with _orchestration_meta_exclusive_lock(repo_root, orchestration_id):
        meta = _read_json(meta_path)
        raw_home = meta.get("codex_workflow_home")
        container: Path | None = None
        if isinstance(raw_home, str) and raw_home.strip():
            container = Path(raw_home)
            try:
                container.lstat()
            except FileNotFoundError:
                # The container is durable, so this is the FAIL-SAFE for one the operator
                # PRUNED or otherwise lost. It is re-created at the SAME path below, empty:
                # no lineage survives it, so a warm launch for any recorded thread takes
                # the `resume=True` missing branch and the conductor goes cold.
                container = None
            else:
                # The CONTAINER path is what the marker refresh takes: it is keyed on
                # `home.name` being a backend dirname, and would silently return on a
                # lineage path.
                _refresh_workflow_home_owner_on_reuse(
                    repo_root, orchestration_id, container, "Codex")
        if container is None:
            # ``run_workflow.py`` deliberately sets TMPDIR beneath repo_root for leaf
            # scratch. This tree holds writable backend-state binds and must never
            # inherit that value: backend rw binds inside the repository are rejected to
            # preserve the write-root boundary. `<homes-root>/<oid>/codex` satisfies that
            # and is durable, which `/tmp` was not — the rollouts it holds are the run's
            # only leaf-side record.
            container = _create_workflow_backend_home(
                repo_root, orchestration_id, "codex", "Codex")
            meta["codex_workflow_home"] = str(container)
            _write_json(meta_path, meta)
        home = container / lineage_token
        return _prepare_codex_workshop_lineage(
            repo_root, orchestration_id, container, home, lineage_token, resume=resume)

def _prepare_codex_workshop_lineage(repo_root: Path, orchestration_id: str, container: Path,
                                    home: Path, lineage_token: str, *, resume: bool
                                    ) -> dict[str, str] | None:
    """The lineage half of `_prepare_codex_workflow_home`, run under its
    metadata lock with the container already established. The claude preparer's lineage half
    (`_prepare_claude_workshop_home`), with codex's two files in `config/`."""
    from tools.workshop_tree import LineagePaths
    config = home / "config"
    if resume:
        try:
            home.lstat()
        except FileNotFoundError:
            return None
    else:
        try:
            os.mkdir(home, 0o700)
        except FileExistsError as exc:
            raise ValueError(
                f"isolated Codex lineage home already exists: {home}. A lineage is created "
                f"once, by the cold launch whose agent_run_id names it ({lineage_token}); a "
                "directory already there was made by hand or left by a run that crashed "
                "between creating it and recording the launch. Inspect it; if it is debris, "
                "remove that one directory and re-run.") from exc
        except OSError as exc:
            raise ValueError(f"cannot create isolated Codex lineage home {home}: {exc}") from exc
        os.chmod(home, 0o700)
        os.mkdir(config, 0o700)
        os.chmod(config, 0o700)
        LineagePaths(homes_root=container.parent.parent, orchestration_id=orchestration_id,
                     lineage_id=lineage_token,
                     orchestration_dir=_orchestration_root(repo_root, orchestration_id),
                     backend="codex").make_work_dirs()
    tree = home / "tree"
    # The trust key is the leaf's working directory, the tree (the pure lineage's is the
    # checkout path, its cwd): codex reads project-scoped configuration — `.codex/` and the
    # hooks in it — only from a TRUSTED project, so the tree, which the leaf writes, is marked
    # untrusted. `config.toml` is remounted read-only, so the leaf cannot re-mark it.
    config_text = ("[projects." + json.dumps(str(tree.resolve()))
                   + "]\ntrust_level = \"untrusted\"\n")
    config_toml = config / "config.toml"
    _secure_backend_home_file(config_toml, config_text.encode("utf-8"))
    auth = workshop_credential_source("codex")
    assert auth is not None
    if not auth.is_file():
        raise ValueError(f"Codex auth.json not found for isolated home: {auth}")
    auth_destination = config / "auth.json"
    _secure_backend_home_file(auth_destination)
    return {
        "home": str(home.resolve()),
        "config": str(config.resolve()),
        "tree": str(tree.resolve()),
        "auth": str(auth.resolve()),
        "auth_destination": str(auth_destination.resolve()),
        "config_toml": str(config_toml.resolve()),
        "lineage_id": lineage_token,
        "homes_root": str(container.resolve().parent.parent),
    }


def claude_workshop_profile_kwargs(isolation: Mapping[str, str]) -> dict[str, Any]:
    """The bwrap profile kwargs a prepared claude workshop lineage home implies — ONE spelling,
    the twin of `codex_workshop_profile_kwargs`."""
    return {
        "backend_rw_override": [isolation["config"], isolation["tree"]],
        "env_overrides": {"CLAUDE_CONFIG_DIR": isolation["config"]},
        "readonly": False,
        "workdir": isolation["tree"],
        "leaf_mode": WORKSHOP_PROFILE_LEAF_MODE,
        "credential_rw_mapping": (isolation["credentials"], isolation["credentials_destination"]),
    }


def _prepare_claude_workshop_home(repo_root: Path, orchestration_id: str, lineage_id: str,
                                  *, resume: bool) -> dict[str, str] | None:
    """Prepare the private claude home of ONE `workshop leaf` LINEAGE (issue #534).

    The claude twin of `_prepare_codex_workflow_home`, and the same two levels: the container
    `<homes-root>/<oid>/claude/` (recorded in `orchestration_meta.json#claude_workflow_home`,
    bound into no leaf) and the LINEAGE HOME `<container>/<lineage_id>/`, which is
    `workshop_tree.LineagePaths.home`. `lineage_id` is the `agent_run_id` of the cold launch
    that started the lineage. `resume` means what it means there: cold creates the lineage
    home EXCLUSIVELY, 0700 (one already there is refused, not adopted) — to be called by the
    CONDUCTOR (`Conductor._open_workshop_lineage`, the workshop loop's cold launch), in its own
    process, before it records the launch, because the tree is seeded and
    the wrapper's context frozen under the home first; warm requires it and answers `None` when
    it is gone. `record_launch` calls it warm for both launches: on a warm one a `None` becomes
    `workshop_lineage_home_missing`, answered before any launch record (a resume against a
    home without its transcript cannot continue the session, so the conductor falls back to a
    cold reuse); on a cold one it is a host defect. Either way the orchestration's `claude/`
    container is established (created, recorded in `orchestration_meta.json`, its owner marker
    written) as on any preparation, as the codex preparer does; only the lineage home is never
    created warm.

    What the lineage home holds that this function places: `config/` (0700), the leaf's
    `CLAUDE_CONFIG_DIR`, created empty but for a `.credentials.json` placeholder over which
    the operator's credentials file is bound READ-WRITE (the CLI writes its credential store by
    staging and rename; the rename onto the bound file fails `EBUSY` and the writer writes in
    place onto the bound inode, read off CLI 2.1.319, so a leaf's refresh reaches the host
    while both hold the same inode; a read-only bind would leave the refreshed token in no file
    the next launch reads). A host-side rename during a launch replaces the host inode and the
    leaf's view is stale from then on (issue #616; the conductor detects it by identity and
    re-launches once). The bind source is `workshop_credential_source("claude")`. The CLI creates its own `.claude.json`, `projects/<slug>/<sid>.jsonl`,
    `backups/`, caches and telemetry there (measured, CLI 2.1.309, unbilled: an empty
    `CLAUDE_CONFIG_DIR` runs `-p` with no onboarding state). Nothing in `config/` is
    host-authored but the placeholder, so nothing needs a read-only remount; the leaf's file
    tools cannot write there (`--restricted` confines them to the tree, measured). The work
    directories (`tree/`, `staged/`, `syntax/`, `snapshot/`) are `LineagePaths.make_work_dirs`'s.

    Returns `{home, config, tree, credentials, credentials_destination, lineage_id,
    homes_root}`; `homes_root` is the root `LineagePaths` takes, derived from the container
    actually used (the recorded one on a reuse, so a relocated `SPEC2SCIENCE_WORKFLOW_HOMES_ROOT`
    does not split one orchestration's lineages across two roots)."""
    lineage_token = (lineage_id or "").strip() if isinstance(lineage_id, str) else ""
    meta_path = _orchestration_root(repo_root, orchestration_id) / "orchestration_meta.json"
    with _orchestration_meta_exclusive_lock(repo_root, orchestration_id):
        meta = _read_json(meta_path)
        raw_home = meta.get("claude_workflow_home")
        container: Path | None = None
        if isinstance(raw_home, str) and raw_home.strip():
            container = Path(raw_home)
            try:
                container.lstat()
            except FileNotFoundError:
                container = None
            else:
                _refresh_workflow_home_owner_on_reuse(
                    repo_root, orchestration_id, container, "Claude")
        if container is None:
            container = _create_workflow_backend_home(
                repo_root, orchestration_id, "claude", "Claude")
            meta["claude_workflow_home"] = str(container)
            _write_json(meta_path, meta)
        home = container / lineage_token
        config = home / "config"
        if resume:
            try:
                home.lstat()
            except FileNotFoundError:
                return None
        else:
            try:
                os.mkdir(home, 0o700)
            except FileExistsError as exc:
                raise ValueError(
                    f"isolated Claude lineage home already exists: {home}. A lineage is "
                    "created once, by the cold launch whose agent_run_id names it "
                    f"({lineage_token}); a directory already there was made by hand or left by "
                    "a run that crashed between creating it and recording the launch. Inspect "
                    "it; if it is debris, remove that one directory and re-run.") from exc
            except OSError as exc:
                raise ValueError(
                    f"cannot create isolated Claude lineage home {home}: {exc}") from exc
            os.chmod(home, 0o700)
            os.mkdir(config, 0o700)
            os.chmod(config, 0o700)
            from tools.workshop_tree import LineagePaths
            LineagePaths(homes_root=container.parent.parent,
                         orchestration_id=orchestration_id, lineage_id=lineage_token,
                         orchestration_dir=_orchestration_root(repo_root, orchestration_id),
                         ).make_work_dirs()
        credentials = workshop_credential_source("claude")
        assert credentials is not None
        if not credentials.is_file():
            raise ValueError(
                f"Claude credentials file not found for the workshop home: {credentials}. A "
                "workshop leaf authenticates through the CLI's credential store; log in with "
                "the claude CLI on this machine first")
        destination = config / ".credentials.json"
        # bwrap needs an existing destination for a file bind; the placeholder is covered by
        # the operator's file before the CLI starts.
        _secure_backend_home_file(destination, label="Claude", verify_existing=False)
    return {
        "home": str(home.resolve()),
        "config": str(config.resolve()),
        "tree": str((home / "tree").resolve()),
        "credentials": str(credentials.resolve()),
        "credentials_destination": str(destination.resolve()),
        "lineage_id": lineage_token,
        "homes_root": str(container.resolve().parent.parent),
    }


def _prepare_codex_workshop_home(repo_root: Path, orchestration_id: str, lineage_id: str,
                                 *, resume: bool) -> dict[str, str] | None:
    """`_prepare_codex_workflow_home`, in the claude preparer's signature."""
    return _prepare_codex_workflow_home(repo_root, orchestration_id, lineage_id,
                                        resume=resume)


def _prepare_http_workshop_home(repo_root: Path, orchestration_id: str, lineage_id: str,
                                *, resume: bool) -> dict[str, str] | None:
    """Prepare the lineage home of ONE HTTP `workshop leaf` lineage (issue #534 PR-J3-b).

    `_prepare_claude_workshop_home` minus what a CLI needs: no `config/` and no credentials —
    the host loop runs in the conductor's process and reads the API key from the leaf
    environment per request (`llm_http_leaf._api_key`). The container
    `<homes-root>/<oid>/http/` is recorded in `orchestration_meta.json#http_workflow_home`; the
    lineage home `<container>/<lineage_id>/` is `workshop_tree.LineagePaths(backend="http")
    .home`. `resume` means what it means for the claude preparer: cold creates the home
    EXCLUSIVELY, 0700, and its work directories; warm answers `None` when it is gone.

    Returns `{home, tree, lineage_id, homes_root}`."""
    lineage_token = (lineage_id or "").strip() if isinstance(lineage_id, str) else ""
    meta_path = _orchestration_root(repo_root, orchestration_id) / "orchestration_meta.json"
    with _orchestration_meta_exclusive_lock(repo_root, orchestration_id):
        meta = _read_json(meta_path)
        raw_home = meta.get("http_workflow_home")
        container: Path | None = None
        if isinstance(raw_home, str) and raw_home.strip():
            container = Path(raw_home)
            try:
                container.lstat()
            except FileNotFoundError:
                container = None
            else:
                _refresh_workflow_home_owner_on_reuse(
                    repo_root, orchestration_id, container, "HTTP")
        if container is None:
            container = _create_workflow_backend_home(
                repo_root, orchestration_id, "http", "HTTP")
            meta["http_workflow_home"] = str(container)
            _write_json(meta_path, meta)
        home = container / lineage_token
        if resume:
            try:
                home.lstat()
            except FileNotFoundError:
                return None
        else:
            try:
                os.mkdir(home, 0o700)
            except FileExistsError as exc:
                raise ValueError(
                    f"HTTP workshop lineage home already exists: {home}. A lineage is created "
                    f"once, by the cold launch whose agent_run_id names it ({lineage_token}); a "
                    "directory already there was made by hand or left by a run that crashed "
                    "between creating it and recording the launch. Inspect it; if it is "
                    "debris, remove that one directory and re-run.") from exc
            except OSError as exc:
                raise ValueError(
                    f"cannot create HTTP workshop lineage home {home}: {exc}") from exc
            os.chmod(home, 0o700)
            from tools.workshop_tree import LineagePaths
            LineagePaths(homes_root=container.parent.parent,
                         orchestration_id=orchestration_id, lineage_id=lineage_token,
                         orchestration_dir=_orchestration_root(repo_root, orchestration_id),
                         backend="http").make_work_dirs()
    return {
        "home": str(home.resolve()),
        "tree": str((home / "tree").resolve()),
        "lineage_id": lineage_token,
        "homes_root": str(container.resolve().parent.parent),
    }


#: A `workshop leaf`'s lineage-home preparer and the profile kwargs a prepared home implies, by
#: LINEAGE backend (`leaf_contract.WORKSHOP_LINEAGE_BACKENDS` maps a provider to it; issue #534 step
#: 2 PR-2, `http` PR-J3-b). The conductor and `record_launch` read the same tables, so the home a
#: launch is recorded against is the one the conductor seeded. `http` has no profile row: an
#: HTTP launch runs no sandboxed process.
WORKSHOP_HOME_PREPARERS: dict[str, Callable[..., dict[str, str] | None]] = {
    "claude": _prepare_claude_workshop_home,
    "codex": _prepare_codex_workshop_home,
    "http": _prepare_http_workshop_home,
}
WORKSHOP_PROFILE_KWARGS: dict[str, Callable[[Mapping[str, str]], dict[str, Any]]] = {
    "claude": claude_workshop_profile_kwargs,
    "codex": codex_workshop_profile_kwargs,
}


def codex_origin_home() -> Path:
    """The operator's ORIGIN Codex home: `$CODEX_HOME`, else the deprecated `$SPEC2SCIENCE_HOME`,
    else `~/.codex`. ONE resolver, because two places must name the same home: the isolated
    lineage home binds this home's `auth.json` (`_prepare_codex_workflow_home`), and the
    host-side reset probe (`tools/usage_reset.py`, issue #405) reads the account's windows with
    it. A conflict between the two variables is refused by the conductor's `_child_env`
    before either runs."""
    raw = os.environ.get("CODEX_HOME", "").strip() or os.environ.get("SPEC2SCIENCE_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def workshop_credential_source(cli_family: str) -> Path | None:
    """The operator's credential file a workshop lineage of `cli_family` binds, or None for a
    family that binds none (`http`: the key is read from the leaf environment per request).

    ONE source for two readers that must name the same file: the preparers' bind source
    (`_prepare_claude_workshop_home`, and `_prepare_codex_workshop_lineage` under
    `_prepare_codex_workflow_home`) and the conductor's
    launch-time identity (`Conductor._credential_identity`, issue #616), so the inode the host
    stats is the inode the sandbox binds. Resolved at CALL time: `claude_credentials_file` is
    looked up on its module here, never bound at import, so a test that patches
    `operator_private_root.claude_credentials_file` reaches both readers; `codex_origin_home`
    reads `$CODEX_HOME` / `$SPEC2SCIENCE_HOME` on each call."""
    if cli_family == "claude":
        from tools import operator_private_root
        return operator_private_root.claude_credentials_file()
    if cli_family == "codex":
        return codex_origin_home() / "auth.json"
    return None


#: The two preflight rows of the leaf's network namespace (issue #534 step 4).
_NETNS_PROBE_ROWS = ("sandbox_netns_python", "sandbox_netns_route")

#: What the route probe runs inside the namespace: a direct connect to the host's stand-in and
#: to an outside address must both fail, and a CONNECT through the relay must reach the stand-in.
_NETNS_ROUTE_PROBE = """
import errno, socket, sys
port = int(sys.argv[1])
s = socket.socket()
try:
    s.connect(("127.0.0.1", port)); print("direct connect to the host stand-in succeeded"); sys.exit(2)
except OSError:
    pass
try:
    socket.create_connection(("1.1.1.1", 443), timeout=5); print("a route out exists"); sys.exit(3)
except OSError as exc:
    if exc.errno != errno.ENETUNREACH:
        print("outside address: %r" % (exc,)); sys.exit(4)
c = socket.create_connection(("127.0.0.1", 3128), timeout=10)
c.sendall(("CONNECT localhost:%d HTTP/1.1\\r\\n\\r\\nping" % port).encode())
buf = b""
while b"echo:ping" not in buf:
    data = c.recv(4096)
    if not data:
        break
    buf += data
print(buf[:80]); sys.exit(0 if b" 200 " in buf and b"echo:ping" in buf else 5)
"""


def _netns_probe_bwrap_argv(socket_dir: str, inner: Sequence[str]) -> list[str]:
    """A minimal namespaced bwrap over the system binds and the socket directory."""
    cmd = ["bwrap", "--die-with-parent", "--new-session", "--unshare-net", "--unshare-pid",
           "--clearenv", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for path in _runtime_ro_bind_paths():
        cmd += ["--ro-bind", path, path]
    cmd += ["--ro-bind", socket_dir, leaf_netns.NET_MOUNT, "--", *inner]
    return cmd


def _probe_leaf_netns() -> list[dict[str, Any]]:
    """The two network-namespace rows: `sandbox_netns_python` (the relay's interpreter starts
    isolated inside a namespaced bwrap) and `sandbox_netns_route` (the real `leaf_netns`: a
    host-half proxy admitting a loopback stand-in, the in-namespace relay, and inside the
    namespace no direct route — to the stand-in or out — while a CONNECT through the relay
    reaches it). No provider is contacted."""
    import socket as _socket
    import threading as _threading

    rows: list[dict[str, Any]] = []
    socket_dir = ""
    try:
        socket_dir = leaf_netns.make_socket_dir()
        python = subprocess.run(
            _netns_probe_bwrap_argv(socket_dir, [leaf_netns.RELAY_PYTHON, "-I", "-c",
                                                 "import socket, subprocess"]),
            text=True, capture_output=True, check=False, timeout=60)
        rows.append({"name": "sandbox_netns_python", "pass": python.returncode == 0,
                     "detail": (f"{leaf_netns.RELAY_PYTHON} starts under --unshare-net"
                                if python.returncode == 0 else
                                (python.stderr.strip() or f"exit={python.returncode}"))})
        server = _socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        port = server.getsockname()[1]

        def echo() -> None:
            try:
                conn, _ = server.accept()
                with conn:
                    data = conn.recv(4096)
                    conn.sendall(b"echo:" + data)
            except OSError:
                pass
        _threading.Thread(target=echo, daemon=True).start()
        log = Path(socket_dir).parent / (Path(socket_dir).name + ".routes.jsonl")
        routes = leaf_netns.HostRoutes(socket_dir, [f"localhost:{port}"], str(log),
                                       "preflight").start()
        try:
            route = subprocess.run(
                _netns_probe_bwrap_argv(socket_dir, leaf_netns.relay_argv(
                    [leaf_netns.RELAY_PYTHON, "-I", "-c", _NETNS_ROUTE_PROBE, str(port)])),
                text=True, capture_output=True, check=False, timeout=60)
        finally:
            routes.stop()
            server.close()
            log.unlink(missing_ok=True)
        rows.append({"name": "sandbox_netns_route", "pass": route.returncode == 0,
                     "detail": ("no direct route; the relay reaches an admitted host"
                                if route.returncode == 0 else
                                (route.stdout.strip() + " " + route.stderr.strip()).strip()
                                or f"exit={route.returncode}")})
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        names = {row["name"] for row in rows}
        rows.extend({"name": name, "pass": False, "detail": f"{type(exc).__name__}: {exc}"}
                    for name in _NETNS_PROBE_ROWS if name not in names)
    finally:
        if socket_dir:
            shutil.rmtree(socket_dir, ignore_errors=True)
    return rows


def _probe_bwrap_sandbox() -> tuple[list[dict[str, Any]], bool]:
    """Preflight probe: confirm the host can sandbox a leaf (bwrap present + user
    namespaces + a dry-run exec). bwrap enforcement is mandatory (Linux+userns only), so
    a host that fails this probe is unsupported and the run fails closed.

    `SPEC2SCIENCE_ORCHESTRATION_ASSUME_BWRAP=1` is a **test-only** affordance: it bypasses the
    real probe and reports the sandbox as available, so unit/integration tests can
    exercise the enforced launch path without bwrap actually installed in the test host.
    It must not be set in production — if bwrap is in fact missing, the probe lies here
    and the actual `bwrap` exec fails closed later at leaf launch (see the
    sandbox-unavailable fail-closed handling in the conductor)."""
    checks: list[dict[str, Any]] = []
    assume = os.environ.get("SPEC2SCIENCE_ORCHESTRATION_ASSUME_BWRAP", "").strip().lower()
    if assume in {"1", "true", "yes"}:
        checks.extend(
            [
                {"name": "sandbox_bwrap_available", "pass": True, "detail": "assumed via env override"},
                {"name": "sandbox_bwrap_userns", "pass": True, "detail": "assumed via env override"},
                {"name": "sandbox_bwrap_exec", "pass": True, "detail": "assumed via env override"},
                {"name": "sandbox_netns_python", "pass": True, "detail": "assumed via env override"},
                {"name": "sandbox_netns_route", "pass": True, "detail": "assumed via env override"},
            ]
        )
        return checks, True

    bwrap_path = shutil.which("bwrap")
    bwrap_available = bool(bwrap_path)
    checks.append(
        {
            "name": "sandbox_bwrap_available",
            "pass": bwrap_available,
            "detail": bwrap_path if bwrap_path else "bwrap not found in PATH",
        }
    )
    if not bwrap_available:
        checks.append(
            {
                "name": "sandbox_bwrap_userns",
                "pass": False,
                "detail": "skipped because bwrap is unavailable",
            }
        )
        checks.append(
            {
                "name": "sandbox_bwrap_exec",
                "pass": False,
                "detail": "skipped because bwrap is unavailable",
            }
        )
        for name in _NETNS_PROBE_ROWS:
            checks.append({"name": name, "pass": False,
                           "detail": "skipped because bwrap is unavailable"})
        return checks, False

    proc = subprocess.run(["bwrap", "--version"], text=True, capture_output=True, check=False)
    userns_ok = proc.returncode == 0
    checks.append(
        {
            "name": "sandbox_bwrap_userns",
            "pass": userns_ok,
            "detail": (proc.stdout.strip() or proc.stderr.strip() or f"exit={proc.returncode}"),
        }
    )
    dry_run = subprocess.run(
        ["bwrap", "--ro-bind", "/", "/", "--", "sh", "-lc", "true"],
        text=True,
        capture_output=True,
        check=False,
    )
    checks.append(
        {
            "name": "sandbox_bwrap_exec",
            "pass": dry_run.returncode == 0,
            "detail": (dry_run.stdout.strip() or dry_run.stderr.strip() or f"exit={dry_run.returncode}"),
        }
    )
    checks.extend(_probe_leaf_netns())
    required_names = {"sandbox_bwrap_available", "sandbox_bwrap_userns", "sandbox_bwrap_exec",
                      *_NETNS_PROBE_ROWS}
    by_name = {
        str(item.get("name")): item.get("pass")
        for item in checks
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    sandbox_enforced = all(by_name.get(name) is True for name in required_names)
    return checks, sandbox_enforced


def _probe_codex_backend(
    backend_token: str,
    command: str | Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[list[dict[str, Any]], str]:
    """Run the codex backend probe and return (checks, agent_version)."""
    command_argv = (
        list(command) if not isinstance(command, str) else shlex.split(command)
    )
    if not command_argv:
        raise ValueError("codex command must be non-empty")
    version_proc = runner([*command_argv, "--version"], text=True, capture_output=True, check=False)
    try:
        exec_help_proc = runner(
            [*command_argv, "exec", "--help"], text=True, capture_output=True, check=False
        )
    except Exception as exc:  # test runners and unavailable CLIs are capability failures
        exec_help_proc = subprocess.CompletedProcess(
            [*command_argv, "exec", "--help"], 1, "", str(exc)
        )
    try:
        resume_help_proc = runner(
            [*command_argv, "exec", "resume", "--help"], text=True, capture_output=True, check=False
        )
    except Exception as exc:  # test runners and unavailable CLIs are capability failures
        resume_help_proc = subprocess.CompletedProcess(
            [*command_argv, "exec", "resume", "--help"], 1, "", str(exc)
        )
    exec_help_text = (exec_help_proc.stdout or "") + "\n" + (exec_help_proc.stderr or "")
    exec_help_detail = exec_help_text.strip() or f"exit={exec_help_proc.returncode}"
    resume_help_text = (resume_help_proc.stdout or "") + "\n" + (resume_help_proc.stderr or "")
    resume_help_detail = resume_help_text.strip() or f"exit={resume_help_proc.returncode}"
    checks = [
        {
            "name": f"{backend_token}_version_available",
            "pass": version_proc.returncode == 0,
            "detail": version_proc.stdout.strip() or version_proc.stderr.strip(),
        },
        {
            "name": "codex_exec_json_streaming",
            "pass": (
                exec_help_proc.returncode == 0
                and "--json" in exec_help_text
                and "--model" in exec_help_text
            ),
            "detail": exec_help_detail,
        },
        {
            "name": "codex_exec_resume",
            "pass": (
                resume_help_proc.returncode == 0
                and all(
                    flag in resume_help_text
                    for flag in CODEX_EXEC_RESUME_REQUIRED_FLAGS
                )
            ),
            "detail": (resume_help_proc.stdout.strip() or resume_help_proc.stderr.strip()
                       or f"exit={resume_help_proc.returncode}"),
        },
        {
            # The leaf prompt is NOT an argv element (a single one is capped at 128 KiB and
            # a node's prompt exceeds it); `leaf_command` ends both codex argvs with the `-`
            # stdin sentinel. A CLI that stopped honouring it would read the leaf's whole
            # prompt as an empty instruction set and answer nothing, so the sentinel is
            # certified like a flag — on BOTH subcommands, which document it separately.
            "name": "codex_prompt_stdin",
            "pass": (
                exec_help_proc.returncode == 0
                and resume_help_proc.returncode == 0
                and "read from stdin" in exec_help_text
                and "read from stdin" in resume_help_text
            ),
            # BOTH helps, because the predicate spans both: reporting only the `exec` one
            # would answer a resume-side failure with a help text that visibly documents
            # stdin support.
            "detail": f"exec: {exec_help_detail}\nexec resume: {resume_help_detail}",
        },
    ]
    return checks, version_proc.stdout.strip()


def _all_strict_boolean_probe_checks_pass(checks: list[dict[str, Any]]) -> bool:
    """The probe-time gate on both backends. The `pass` key is required, an explicit False
    `pass` is a failure, at least one non-None `pass` must exist, and all of those must be True.

    A check with `pass: None` is excluded as an unrun probe, so "a check added to a prober
    later gates by default" holds for a boolean `pass` only. No prober emits `None` since the
    claude skipped features-list row went (issue #447); one that did would not gate here, and
    would gate on the stored document only if its backend's required set names it.
    """
    evaluated_any = False
    for item in checks:
        if not isinstance(item, dict):
            return False
        if "pass" not in item:
            return False
        p = item["pass"]
        if p is None:
            continue
        evaluated_any = True
        if p is not True:
            return False
    return evaluated_any

# The two claude MCP remediation strings — the enablement one and the tool-permission one — stood
# here until Z4 (issue #171). They were the `detail` a failing `_probe_claude_mcp_registry` handed the
# operator, and that probe is deleted: a pure leaf calls no MCP tool, so there is no leaf-session
# enablement to certify. The MCP server itself was deleted in issue #444; the conductor calls the
# build-runtime library in-process (`tools/build_runtime.py`).


def _probe_claude_backend(
    backend_token: str,
    command: str | Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[list[dict[str, Any]], str]:
    """A probe specific to the claude backend; returns (checks, agent_version).

    `claude features list` is not a CLI subcommand (claude answers it as a chat prompt), so it
    is not run. `--version` is run for the recorded `agent_version` and does not gate. The
    CLI's option set is not probed (issue #643, reversing issues #453 and #534): a CLI that
    lacks an option the leaf argv carries refuses it before any request, and the launch ends
    `fail_closed` / `leaf_transport_error` with the CLI's message in the failure summary.
    """
    # SPLIT, like the codex prober and like `Conductor._provider_command_base`: a configured
    # command may carry flags (`mywrap --flag`), and the leaf is launched with it split. Passing
    # the whole string as argv[0] probes an executable of that literal name, which does not
    # exist — so every wrapper failed preflight before any leaf could run.
    command_argv = list(command) if not isinstance(command, str) else shlex.split(command)
    version_proc = runner([*command_argv, "--version"], text=True, capture_output=True,
                          check=False)

    # The leaf prompt travels on stdin, not argv (a single argv element is capped at
    # 128 KiB and a node's prompt exceeds it), and the claude contract is the ABSENCE of a
    # positional prompt — which `claude --help` states nowhere, so there is no help
    # substring to match the way the codex sentinel is matched. Instead ask the CLI to
    # refuse an empty `-p`: it answers with the one message that names its input channels,
    # before any model turn (measured: exit 1 in ~1.8 s, zero tokens, no API call).
    stdin_probe_proc = runner([*command_argv, "-p"], text=True, capture_output=True,
                              check=False, input="")
    stdin_probe_text = (stdin_probe_proc.stdout or "") + "\n" + (stdin_probe_proc.stderr or "")

    checks = [
        {
            "name": f"{backend_token}_prompt_stdin",
            # The REFUSAL's own wording, and a non-zero exit to prove it refused. A bare
            # "stdin" would be satisfied by the help text — `--replay-user-messages`
            # mentions it — so any build that dumps usage on an argument error would pass
            # while supporting no stdin input at all. "through stdin" appears in the
            # refusal and not in the help. The non-zero exit is what keeps the probe free:
            # a build that instead ACCEPTED the empty prompt would spend a model turn.
            "pass": (stdin_probe_proc.returncode != 0
                     and "through stdin" in stdin_probe_text),
            "detail": stdin_probe_text.strip() or f"exit={stdin_probe_proc.returncode}",
        },
        _claude_global_config_env_check(backend_token),
    ]
    return checks, version_proc.stdout.strip()


def _claude_global_config_paths() -> tuple[Path, ...]:
    """The files the claude CLI may read as its GLOBAL config, both under the leaf's `HOME`.

    `~/.claude.json`, and the legacy `~/.claude/.config.json`: measured on CLI 2.1.294, when
    the legacy file exists the CLI reads IT and ignores `~/.claude.json` (whose `env` then
    has no effect), and otherwise reads `~/.claude.json` (issue #453). Both are named from
    `backend_credential_home_paths`, the resolver that names the sandbox's rw binds (the
    file itself, and the `~/.claude` directory that holds the legacy one), so the check reads
    the files the leaf can see. Which one wins is the CLI's internal rule; the check does
    not copy it and reads both."""
    dirs, files = _backend_credential_home_paths("claude")
    return (*files, *(d / ".config.json" for d in dirs))


def _claude_global_config_env_check(backend_token: str) -> dict[str, Any]:
    """Refuse a claude leaf whose global config carries an `env` block (issue #453).

    `--restricted` ignores the user, project and local SETTINGS files; the CLI's global
    config (`_claude_global_config_paths`) is not one of them, and its `env` block still
    reaches every leaf — `ANTHROPIC_MODEL` (and an `ANTHROPIC_DEFAULT_OPUS_MODEL` remap of a
    declared `opus`), `ANTHROPIC_BASE_URL`, effort, and `CLAUDE_CODE_MAX_OUTPUT_TOKENS`,
    which OVERRIDES the ceiling the conductor sets in the leaf's own environment — under
    `--restricted` and under `--setting-sources ""` alike (measured on CLI 2.1.294 against a
    loopback endpoint, issue #453). The file cannot be withheld: it is the credential/state
    file the sandbox binds read-write, and the CLI writes it. So the channel is refused
    rather than closed: the check passes when each file is absent, or parses to an object
    whose `env` is absent or empty, and fails if EITHER carries one.

    An unreadable or unparseable file FAILS: what the CLI makes of it is not something this
    check can name, and the operator can always fix it."""
    name = f"{backend_token}_global_config_env_absent"
    seen: list[str] = []
    for path in _claude_global_config_paths():
        if not path.exists():
            seen.append(f"{path}: absent")
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"name": name, "pass": False,
                    "detail": f"{path}: unreadable or not JSON ({exc}); fix the file"}
        if not isinstance(doc, dict):
            return {"name": name, "pass": False,
                    "detail": f"{path}: not a JSON object; fix the file"}
        env = doc.get("env")
        if env in (None, {}):
            seen.append(f"{path}: no `env` block")
            continue
        keys = sorted(env) if isinstance(env, dict) else [type(env).__name__]
        # The remedy on the first line: an operator is shown that line alone, cut at 240
        # characters with the other reasons around it (`run_workflow._ensure_preflight_pass`).
        return {"name": name, "pass": False,
                "detail": (f"remove the `env` key ({', '.join(keys)}) from {path}\n"
                           "the CLI's global config is not a settings file, so its `env` "
                           "block reaches every claude leaf despite `--restricted` "
                           "(issue #453)")}
    return {"name": name, "pass": True, "detail": "; ".join(seen)}


_BACKEND_PROBERS: dict[
    str,
    Callable[
        [str, str, Callable[..., subprocess.CompletedProcess[str]]],
        tuple[list[dict[str, Any]], str],
    ],
] = {
    "codex": _probe_codex_backend,
    "claude": _probe_claude_backend,
}


def _probe_http_provider(
    provider_row: Mapping[str, Any],
    *,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Probe one HTTP leaf provider (issue #28). Fail-closed on every check.

    An HTTP provider spawns no process, so none of the CLI probe's questions (version, feature
    flags, hooks, sandbox) apply. Three things CAN be established before a run spends tokens
    finding out, and all three are the mistakes operators actually make:

      1. the environment variable named by `api_key_env` is set and non-empty. Its VALUE is
         never read into the payload, logged, or hashed — only the fact that it is there.
      2. `base_url` is a well-formed http(s) URL with a host.
      3. the endpoint answers at all. Any HTTP status counts as reachable — a 401 or a 404
         proves a server is there, which is what this check is for; only DNS failure, a refused
         connection or a timeout mean "nothing is listening". The request takes the LEAF's
         route — `llm_http_leaf._default_opener` over `leaf_env_from(os.environ)`: no proxy,
         redirects refused and reported as their status — so what this check certifies
         reachable is what the leaf will reach (issue #83); on a host that reaches the
         endpoint only through a proxy it fails, naming the proxy variables. The request is
         UNAUTHENTICATED deliberately: preflight must not send the operator's key to a URL it
         has not yet validated, and must not spend a token on a probe.

    `SPEC2SCIENCE_HTTP_PREFLIGHT_SKIP_REACHABILITY=1` drops only (3), for an offline preflight."""
    import urllib.error
    import urllib.request
    from urllib.parse import urlparse

    token = str(provider_row.get("backend") or "").strip().lower()
    base_url = str(provider_row.get("base_url") or "").strip()
    api_key_env = str(provider_row.get("api_key_env") or "").strip()
    checks: list[dict[str, Any]] = []

    key_present = bool(api_key_env) and bool(os.environ.get(api_key_env, "").strip())
    checks.append({
        "name": f"{token}_api_key_env_set",
        "pass": key_present,
        # Names the VARIABLE, never its value.
        "detail": (f"{api_key_env} is set" if key_present else
                   f"{api_key_env or '(no api_key_env)'} is unset or empty"),
    })

    parsed = urlparse(base_url)
    url_ok = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    checks.append({
        "name": f"{token}_base_url_well_formed",
        "pass": url_ok,
        "detail": base_url if url_ok else f"not an http(s) URL with a host: {base_url!r}",
    })

    skip = os.environ.get(_HTTP_PREFLIGHT_SKIP_REACHABILITY_ENV, "").strip().lower() in {
        "1", "true", "yes"}
    if skip:
        checks.append({
            "name": f"{token}_endpoint_reachable",
            "pass": True,
            "detail": f"skipped ({_HTTP_PREFLIGHT_SKIP_REACHABILITY_ENV} set)",
        })
    elif not url_ok:
        checks.append({
            "name": f"{token}_endpoint_reachable",
            "pass": False,
            "detail": "not attempted: base_url is not well-formed",
        })
    else:
        # The LEAF's route, not the process's: an HTTP leaf opens its request with
        # `_default_opener` over the filtered environment (`workflow_conductor._child_env`
        # starts from `leaf_env_from`), which carries no proxy family and refuses redirects.
        # Bare `urlopen` read the operator's proxy instead, so on a proxied host this check
        # passed through the proxy and the leaf it vouched for then could not connect
        # (issue #83). `None` is not the same thing: `_default_opener(None)` installs
        # urllib's environment-reading ProxyHandler (measured).
        if opener is None:
            # A private name, imported here by its one caller outside its module.
            from tools.llm_http_leaf import _default_opener
            opener = _default_opener(leaf_env_from(os.environ))
        open_url = opener
        reachable, detail = True, "responded"
        try:
            open_url(urllib.request.Request(base_url, method="GET"),
                     timeout=_HTTP_PREFLIGHT_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            detail = f"HTTP {exc.code}"        # a server answered: reachable
        except Exception as exc:               # noqa: BLE001 - DNS/TLS/refused/timeout
            reachable = False
            detail = f"{type(exc).__name__}: {exc}"
        if not reachable:
            # NAMES only: a proxy URL carries credentials and this payload is persisted.
            # `NO_PROXY` names no route, so it alone cannot explain a direct failure.
            shadowed = [name for name in LEAF_ENV_NAMED_EXCLUSIONS
                        if name.lower().endswith("_proxy") and name.lower() != "no_proxy"
                        and os.environ.get(name, "").strip()]
            if shadowed:
                detail += (f"; the probe went direct, as a leaf does — {', '.join(shadowed)} "
                           f"{'is' if len(shadowed) == 1 else 'are'} set on this host and "
                           "not forwarded to a leaf (LEAF_ENV_NAMED_EXCLUSIONS)")
        checks.append({
            "name": f"{token}_endpoint_reachable",
            "pass": reachable,
            "detail": detail,
        })

    return {
        "provider_type": str(provider_row.get("provider") or ""),
        "base_url": base_url,
        "api_key_env": api_key_env,
        "checks": checks,
        "launchable": all(item.get("pass") is True for item in checks),
    }


# Short on purpose: preflight is not a health check, and a hung endpoint must not hold a run at
# the starting line.
_HTTP_PREFLIGHT_TIMEOUT_SECONDS = 10


def probe_all_providers(
    *,
    llm_config_path: str | Path | None = None,
    config: Any = None,
    repo_root: Path | None = None,
    # The resolved `defaults` of the configuration the caller will launch, so a reload here
    # cannot lose a value that came from a deprecated flag rather than from the file.
    model_override: str = "",
    command_override: str = "",
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """The `providers` map of a preflight payload: one entry per DISTINCT provider a config can
    launch.

    Keyed by the backend TOKEN a launch is recorded under, so `workflow_launch_check` and
    `record_launch` can look a launch's own token up directly. Two entries can share a token
    (the same backend behind two wrapper commands); they are AND-ed, because a token is
    launchable only if every command recorded under it is.

    Deliberately NOT merged into `probe_execution_platform`: that function answers "can this
    host spawn agents at all", produces the top-level payload the existing gates read, and is
    called for `defaults` exactly as before. This adds the per-provider layer above it."""
    from tools.llm_config import apply_defaults_overrides, describe_providers, load_llm_config

    # `config` is an ALREADY-LOADED snapshot: the caller parsed one set of bytes, and
    # re-reading the file here would probe a version the caller did not load.
    # `llm_config_path` remains for callers that have no snapshot to hand.
    #
    # The overrides are the `defaults` the CALLER resolved. This runs in the `preflight`
    # SUBPROCESS, which reloads the file and would otherwise re-derive them independently, so
    # applying them keeps ONE authority for what gets probed rather than two derivations that
    # can drift. Today they always equal what the file declares — nothing in `run_workflow`
    # overrides it since the run-wide flag trio was removed — so this is a no-op in value; it
    # is not a no-op in structure, and that is deliberate: a caller that ever resolves
    # `defaults` differently must not silently probe a command the run will not launch.
    if config is None:
        config = load_llm_config(llm_config_path)
    cfg = apply_defaults_overrides(
        config, model=model_override, command=command_override)
    providers: dict[str, dict[str, Any]] = {}
    # Every CLI surface is a workshop surface since issue #534 PR-J4-b (`defaults` included —
    # it serves the `escalate` diagnostician); the recorded surface keeps its `workshop` stamp.
    for row in describe_providers(cfg):
        token = row["backend"]
        workshop = row["provider"] in ("claude_cli", "codex_cli")
        if workshop:
            probe = probe_execution_platform(
                backend=token, agent_command=row["command"] or None,
                runner=runner, repo_root=repo_root)
            entry = {
                "provider_type": row["provider"],
                "probe_command": probe.get("probe_command", ""),
                "checks": probe.get("checks", []),
                "launchable": _preflight_allows_agent_launch(probe),
            }
        else:
            entry = _probe_http_provider(row, opener=opener)
        # Every surface is recorded, not just the first. The row is what the TTL re-probe
        # reconstructs its probes from (`_reprobe_recorded_providers`), so keeping one
        # command/endpoint would silently stop checking the others after the first expiry —
        # and an unavailable one would then be certified fresh until its leaf launch failed.
        entry["surfaces"] = [{
            "provider_type": row["provider"],
            "probe_command": row["command"],
            "base_url": row["base_url"],
            "api_key_env": row["api_key_env"],
            **({"workshop": True} if workshop else {}),
        }]
        prior = providers.get(token)
        if prior is None:
            providers[token] = entry
        else:
            # Same token, different command/endpoint: AND them, and keep both check lists so
            # the operator can see WHICH one failed.
            prior["checks"] = list(prior.get("checks", [])) + list(entry.get("checks", []))
            prior["surfaces"] = list(prior.get("surfaces", [])) + entry["surfaces"]
            prior["launchable"] = bool(prior.get("launchable")) and bool(entry.get("launchable"))
    return providers


def probe_execution_platform(
    *,
    backend: str,
    agent_command: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    backend_token = backend.strip().lower()
    default_command = DEFAULT_BACKEND_COMMANDS[backend_token]
    command = (
        agent_command.strip()
        if isinstance(agent_command, str) and agent_command.strip()
        else default_command
    )

    prober = _BACKEND_PROBERS[backend_token]
    checks, agent_version = prober(backend_token, command, runner)

    # Every probe check gates, on both backends — a check added to a prober later fails
    # closed by default rather than silently becoming non-gating.
    # What a claude leaf needs of its host is only that the CLI takes its prompt on stdin
    # and finds no `env` block in its global config (issue #453): a pure leaf loads no
    # customization (`--safe-mode`), is launched with no MCP configuration and no tools, and
    # runs no hook. The three probes that stood here — the committed leaf configuration, the MCP
    # registry, and the live tool-roster measurement of issue #71 — each measured a surface
    # the agentic leaf had and this one does not (Z4, issue #171).
    can_launch_agents = _all_strict_boolean_probe_checks_pass(checks)
    if backend_token == "codex":
        # The codex hooks FEATURE check, and the committed-hook-file check beside it, went
        # with the leaf's hook layer in Z4 (issue #171). A pure codex leaf is confined by the
        # read-only bwrap profile plus `--sandbox read-only` / `sandbox_mode="read-only"`;
        # it carries no hooks, so a CLI with the feature off changes
        # nothing about it. The home-writable probe stays — the private CODEX_HOME still holds
        # the untrusted marker and the bound credential.
        codex_home_check = _probe_codex_home_writable()
        checks.append(codex_home_check)
        can_launch_agents = can_launch_agents and (codex_home_check.get("pass") is True)
    sandbox_checks, sandbox_enforced = _probe_bwrap_sandbox()
    checks.extend(sandbox_checks)
    can_launch_agents = can_launch_agents and sandbox_enforced
    return {
        "checked_at": _utc_now_iso(),
        "backend": backend_token,
        "probe_command": command,
        "agent_version": agent_version,
        "checks": checks,
        "sandbox_runtime": "bwrap",
        "sandbox_enforced": sandbox_enforced,
        "can_launch_step_agents": can_launch_agents,
        "can_launch_substep_agents": can_launch_agents,
        "status": "pass" if can_launch_agents else "fail",
    }


def _capture_repo_revision(
    repo_root: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any] | None:
    """Best-effort capture of the git HEAD SHA and working-tree dirty state.

    Records the repository version at run time so a run's artifacts can be
    traced back to the exact source that produced them.  The value is resolved
    at run time (not pinned to a constant), mirroring the model-alias
    convention.  Returns None when the directory is not a git checkout or git
    is unavailable, so an absent revision stays distinguishable from a
    clean/dirty one.
    """
    try:
        head = runner(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if head.returncode != 0:
        return None
    commit = (head.stdout or "").strip()
    if not commit:
        return None
    try:
        status = runner(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return {"commit": commit, "dirty": None}
    if status.returncode != 0:
        # HEAD is known but the dirty state could not be determined; record the
        # commit with dirty=None rather than dropping the revision entirely.
        return {"commit": commit, "dirty": None}
    dirty = any(line.strip() for line in (status.stdout or "").splitlines())
    return {"commit": commit, "dirty": dirty}


def init_orchestration(
    repo_root: Path,
    orchestration_id: str,
    *,
    spec_ref: str | None = None,
    source_dependency_ref: str | None = None,
    status: str = "running",
    # claude is the primary/default backend: it is what `docs/examples/llm_claude.example.yaml`
    # — the sample the first-run instructions copy — names in `defaults`. codex remains a fully
    # supported choice, selected in the leaf-LLM configuration rather than here.
    agent_backend: str = "claude",
    agent_model: str | None = None,
    invocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = _orchestration_root(repo_root, orchestration_id)
    root.mkdir(parents=True, exist_ok=True)
    (repo_root / "workspace" / "tmp").mkdir(parents=True, exist_ok=True)
    (root / "launches").mkdir(parents=True, exist_ok=True)
    (root / "agents").mkdir(parents=True, exist_ok=True)
    (root / "steps").mkdir(parents=True, exist_ok=True)
    _ensure_orchestration_audit_dirs(repo_root, orchestration_id)
    init_phase_state_json(repo_root, orchestration_id, reason="init_orchestration")

    meta = {
        "orchestration_id": orchestration_id,
        "status": status,
        "started_at": _utc_now_iso(),
    }
    if spec_ref:
        meta["spec_ref"] = spec_ref
    if source_dependency_ref:
        meta["source_dependency_ref"] = source_dependency_ref
    meta_path = root / "orchestration_meta.json"
    orchestration_agent_run_id: str | None = None
    existing: dict[str, Any] | None = None
    if meta_path.is_file():
        try:
            existing = _read_json(meta_path)
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict):
            # repo_revision is captured once at the initial start and preserved
            # across re-init / resume, so the recorded SHA stays the source that
            # produced the run rather than being overwritten by a later HEAD.
            for key in (
                "repo_revision",
                "invocation",
            ):
                if key in existing:
                    meta.setdefault(key, existing[key])
            existing_run_id = existing.get("orchestration_agent_run_id")
            if isinstance(existing_run_id, str) and existing_run_id.strip():
                orchestration_agent_run_id = existing_run_id.strip()
    if "repo_revision" not in meta:
        # The FRAMEWORK's commit — the code that ran — not the Project's (issue #705;
        # `docs/GLOSSARY.md`): every reader means that commit, and a Project's spec files are
        # identified in every derivation key by their hash.
        repo_revision = _capture_repo_revision(FRAMEWORK_ROOT)
        if repo_revision:
            meta["repo_revision"] = repo_revision
    # Reproduction/provenance record: raw command + resolved params (+ closure
    # back-link). UNLIKE repo_revision (pure provenance, immutable), invocation is
    # load-bearing for closure-aware resume, so it must always describe the run that
    # currently (re)starts this orchestration. A cold (re-)init that supplies a fresh
    # invocation therefore OVERWRITES any prior block — e.g. reusing an
    # --orchestration-id for a different --with-deps target must not leave the old
    # closure back-link behind (which would misdirect a later --resume). The
    # preservation loop above keeps the existing block only when no fresh invocation
    # is supplied (an internal re-init). Real --resume goes through
    # resume_orchestration, which never re-supplies invocation and preserves it.
    if isinstance(invocation, dict) and invocation:
        # ... but the end-phase HIGH WATER survives the overwrite. This is the second writer of
        # `invocation.until_phase`, and it was not covered when the first was: re-initing an
        # EXISTING orchestration with `--invocation-json '{"until_phase":"compile"}'` replaced
        # the block wholesale and let a run started for `validate` reach `pass` with three
        # phases never run. The provenance argument above is about the CLOSURE BACK-LINK, which
        # must describe the current run; what the vouch requires to have been certified is not
        # provenance and is not this invocation's to lower.
        # A caller-supplied `until_phase_high_water` cannot lower the bar: the loop below
        # ASSIGNS rather than `setdefault`s, so a prior record with a usable phase overwrites
        # it, and when the prior has none `_record_until_phase_high_water` takes the max with
        # this invocation's own `until_phase`, so a supplied value can only raise it.
        prior_invocation = meta.get("invocation")
        if isinstance(prior_invocation, dict):
            for key in ("until_phase_high_water", "until_phase"):
                reached = _normalized_until_phase(prior_invocation.get(key))
                if reached is not None:
                    invocation["until_phase_high_water"] = reached
                    break
        meta["invocation"] = invocation
        _record_until_phase_high_water(invocation)
    # No `driver` block: issue #177 deleted the liveness probe that read it, and the exclusive
    # claim (`~/.spec2science/start_claims/`, released by the OS when the process dies) answers
    # "is another driver running this?" without a recorded identity to go stale.
    meta.pop("driver", None)
    if not orchestration_agent_run_id:
        orchestration_agent_run_id = str(uuid.uuid4())
    backend_token = str(agent_backend).strip().lower()
    meta["orchestration_agent_run_id"] = orchestration_agent_run_id
    _write_json(meta_path, meta)
    # NO read / output manifest for the orchestration agent either. The pair described what a
    # LEAF was permitted to read and write, for a hook layer to enforce and an audit to
    # re-read; the orchestration agent is the conductor's own process, which is not confined by
    # anything it writes about itself, so its copies said only that the host may do what the
    # host does. Retired with the leaf-facing set in issue #171 PR-2.
    (repo_root / "workspace" / "tmp" / orchestration_agent_run_id).mkdir(parents=True, exist_ok=True)

    graph_path = root / "agent_graph.json"
    if not graph_path.exists():
        _write_json(graph_path, {"edges": []})

    runs_path = root / "agent_runs.jsonl"
    if not runs_path.exists():
        runs_path.write_text("", encoding="utf-8")
    # H-NEW-1: serialize the read+append against any concurrent
    # record_agent_run finalizer. Without this lock, init_orchestration's
    # plain `open("a")` could interleave bytes with a finalizer's locked
    # append, defeating Adv-24 serialization. The lock protects both the
    # idempotency scan (read existing entries) and the running-entry
    # append.
    with _runs_jsonl_exclusive_lock(repo_root, orchestration_id):
        has_orchestration_running_entry = False
        with runs_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                if (
                    str(item.get("agent_run_id", "")).strip() == orchestration_agent_run_id
                    and str(item.get("agent_role", "")).strip() == "orchestration"
                    and str(item.get("status", "")).strip() == "running"
                ):
                    has_orchestration_running_entry = True
                    break
        if not has_orchestration_running_entry:
            orchestration_row: dict[str, Any] = {
                "agent_run_id": orchestration_agent_run_id,
                "agent_role": "orchestration",
                "agent_backend": backend_token,
                "status": "running",
                "started_at": _utc_now_iso(),
            }
            # Record the orchestration agent's own model when supplied so the
            # top-level row is not a blind spot for cost attribution / repro.
            # Children record agent_model at record-launch; the orchestration row
            # has no launch, so it is threaded in here.
            if isinstance(agent_model, str) and agent_model.strip():
                orchestration_row["agent_model"] = agent_model.strip()
            with runs_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(orchestration_row, ensure_ascii=False) + "\n")
    _append_session_run_index_entry(
        repo_root,
        orchestration_id,
        agent_run_id=orchestration_agent_run_id,
        agent_session_id=orchestration_agent_run_id,
        context_id=orchestration_agent_run_id,
        agent_role="orchestration",
        status="running",
    )

    return meta


def write_preflight(
    repo_root: Path,
    orchestration_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    root = _orchestration_root(repo_root, orchestration_id)
    root.mkdir(parents=True, exist_ok=True)

    stored = dict(payload)
    if "probed_at" not in stored:
        stored["probed_at"] = stored.get("checked_at") or _utc_now_iso()

    _write_json(root / "preflight.json", stored)
    meta_path = root / "orchestration_meta.json"
    if meta_path.exists():
        # Codex round 11 F1: the orchestration_meta.json read-check-write region
        # here mutates the same file mark_dependency_readiness() guards under
        # _orchestration_meta_exclusive_lock. Without acquiring the same lock,
        # a concurrent mark-dependency-readiness call can have its verified
        # `true` flags overwritten by this preflight pass with a stale snapshot.
        with _orchestration_meta_exclusive_lock(repo_root, orchestration_id):
            meta = _read_json(meta_path)
            if isinstance(meta, dict):
                # The persisted `dependency_readiness` is an AUDIT record: the launch gate
                # (`_dependency_ready`) recomputes readiness live from the workspace and
                # decides from the recomputation, never from these booleans. Branches:
                #
                #   - existing missing            → initialize from computed.
                #   - leaf (now, or the existing  → idempotent refresh from computed (a
                #     record is a leaf's)            spec_ref repointed to a non-leaf resets
                #                                   the leaf's trivial-true to fail-closed).
                #   - CLI-verified non-leaf       → preserve the verified flags.
                #
                # The `dep_set_fingerprint` whose mismatch used to reset a non-leaf's record
                # went with issue #250 PR-2: the gate's live recompute is a content-hash
                # comparison of its own (each dependency's derivation key), so a byte
                # fingerprint of the selected files detected nothing it does not, and the
                # record it reset is not what the gate decides from.
                existing = meta.get("dependency_readiness")
                # A corrupt catalog or a missing PyYAML raises out of the preflight, and the
                # run stops before any launch (issue #643: the gate recomputes live anyway).
                computed = _compute_initial_dependency_readiness(
                    repo_root, meta.get("spec_ref"),
                    target=_target_of_orchestration_meta(repo_root, meta),
                )
                if not isinstance(existing, dict) or (
                        existing != computed
                        and (computed.get("direct_dependency_compile_readiness") is True
                             or existing.get("certified_deps") == [])):
                    meta["dependency_readiness"] = computed
                    _write_json(meta_path, meta)
    if _preflight_allows_agent_launch(stored):
        _transition_phase_state(
            repo_root,
            orchestration_id,
            new_state="preflight_passed",
            event="preflight_written",
        )
    else:
        _ensure_orchestration_audit_dirs(repo_root, orchestration_id)
        _append_phase_state_log(
            repo_root,
            orchestration_id,
            {
                "ts": _utc_now_iso(),
                "event": "preflight_written_not_launchable",
                "from": None,
                "to": None,
            },
        )
    return stored


def _load_graph(graph_path: Path) -> dict[str, Any]:
    if graph_path.exists():
        graph = _read_json(graph_path)
        if isinstance(graph, dict) and isinstance(graph.get("edges"), list):
            return graph
    return {"edges": []}


#: The `sandbox_network` value of a CLI leaf's launch record: it ran in a network namespace.
SANDBOX_NETWORK_NAMESPACE = "namespace"


def _launch_network_block(repo_root: Path, socket_dir: str | None, family: str,
                          response_payload: Mapping[str, Any]) -> dict[str, Any]:
    """The `network` block of one CLI launch's profile (issue #534 step 4), from the socket
    directory the conductor serves for it (`--netns-socket-dir`) and the wrapper endpoint the
    launch's own setting surface records (`<family>_mcp_config`; a reading launch records
    none). Both are the conductor's: the directory is the private one `leaf_netns.make_socket_dir`
    made for this launch, the URL its own wrapper's loopback endpoint. The block's own refusals
    (outside the checkout, short enough for a socket path) are `network_block_violations`'. A
    malformed value raises here, inside `record_launch`'s fail-closed sandbox catch."""
    path = socket_dir.strip()
    servers = response_payload.get(f"{family}_mcp_config")
    wrapper_port: int | None = None
    if servers is not None:
        url = str(servers[0].get("url") or "")
        wrapper_port = int(url[len("http://127.0.0.1:"):].split("/", 1)[0])
    block = leaf_netns.network_block(path, WORKSHOP_CLI_ENDPOINTS[family], wrapper_port)
    problems = leaf_netns.network_block_violations(
        block, endpoints=WORKSHOP_CLI_ENDPOINTS[family], repo_root=str(repo_root))
    if problems:
        raise ValueError("; ".join(problems))
    return block


def record_launch(
    repo_root: Path,
    orchestration_id: str,
    *,
    parent_agent_run_id: str,
    child_agent_run_id: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    relation_type: str = "launch",
    child_env: Mapping[str, str] | None = None,
    workshop_lineage_id: str | None = None,
    netns_socket_dir: str | None = None,
) -> dict[str, Any]:
    # Both ids are the conductor's (the orchestration's arid; a fresh uuid per child).
    parent_agent_run_id = parent_agent_run_id.strip()
    child_agent_run_id = child_agent_run_id.strip()
    preflight_payload: dict[str, Any] | None = None
    try:
        preflight_payload = _require_preflight_launchable(
            repo_root,
            orchestration_id,
            enforce_live_probe=True,
        )
    except RuntimeError:
        # Launch gate failure must terminate orchestration immediately.
        try:
            update_orchestration_status(
                repo_root,
                orchestration_id,
                status="fail",
            )
        except Exception:
            pass
        raise
    preflight_backend = (
        str(preflight_payload.get("backend", "")).strip().lower()
        if isinstance(preflight_payload, dict)
        else ""
    )
    # THIS launch's provider, not the run's. Two consumers used to disagree: the codex-home
    # transaction, the claude sequential-child gate and `pre_phase_launch` read the preflight's
    # single `backend`, while the sandbox-profile branch below read the conductor's own response
    # payload. Under a mixed configuration those are different answers for the same launch, so
    # the response payload — which the conductor stamps per leaf — becomes authoritative, and
    # the preflight's `providers` map is what authorizes it.
    _launch_backend = response_payload.get("backend") if isinstance(response_payload, dict) else None
    _launch_backend = (_launch_backend.strip().lower()
                       if isinstance(_launch_backend, str) and _launch_backend.strip() else "")
    _probed = preflight_payload.get("providers") if isinstance(preflight_payload, dict) else None
    if _launch_backend and isinstance(_probed, dict) and _probed:
        # Whether preflight probed this provider as launchable is the launch check's question
        # (`pre_phase_launch` -> `workflow_launch_check`, `backend_not_probed`), asked below.
        backend_token = _launch_backend
    elif _launch_backend in SUPPORTED_BACKENDS:
        # No `providers` map: a pre-#28 preflight, where the run has one backend and the
        # response payload restates it. Accept it, and fall back to the preflight's own value
        # for a legacy payload that carries no backend at all.
        backend_token = _launch_backend
    else:
        backend_token = preflight_backend if preflight_backend in SUPPORTED_BACKENDS else "claude"

    # Defense in depth, and BEFORE any durable launch/session/graph mutation — the same rule
    # the build-recurrence guard and the codex-home transaction check obey. `llm_config` refuses
    # an HTTP provider on an agentic substep and the conductor refuses one on a node with no
    # pure path, but this is the authorization gate and must not be the only layer assuming the
    # other two ran: a non-pure HTTP launch would receive allowed_output_paths, file-tool pins
    # and a write-authorized capability with no sandbox at all. Raising here leaves no
    # agent-graph edge and no session-index row behind.
    #
    # A WORKSHOP request is what it admits (issue #534 PR-J3-b; the pure leaf it also admitted
    # went in PR-J4-b): its tools are the host loop's, run in the conductor's process on the
    # lineage's tree, and it holds no write authority under the checkout (the host writes every
    # artifact from the snapshot).
    if (backend_token in _HTTP_PROVIDER_TOKENS
            and not _leaf_contract_is_workshop_request(request_payload)):
        raise RuntimeError(
            f"record-launch: provider {backend_token!r} runs no confined child process and is "
            f"admissible only for a workshop leaf; this launch is not one")

    # A WORKSHOP leaf's lineage (issue #534): a claude or codex launch in the lineage's private
    # home,
    # whose id the request names (`workshop_lineage_id`). A COLD launch starts the lineage —
    # its id is the child's own, and no `--workshop-lineage-id` is passed; a WARM one resumes it
    # and passes the id.
    #
    # The HOST prepares a cold lineage's home before this launch is recorded, not this function:
    # the conductor seeds the tree, stages the closure and freezes the wrapper's context — all
    # under the home — and starts the wrapper endpoint whose URL this launch records, before it
    # records the launch (its backend's `WORKSHOP_HOME_PREPARERS` entry, `resume=False`, in its own
    # process, from `Conductor._open_workshop_lineage`). So here the home must exist on BOTH
    # launches, and the two absences mean
    # different things: a warm launch whose home is gone returns
    # `workshop_lineage_home_missing` and the conductor falls back to a cold reuse, as the codex
    # lineage does above; a cold launch whose home was never prepared is a host defect, refused.
    # Both are answered before any launch record, session-index row or active-child marker.
    is_workshop = _leaf_contract_is_workshop_request(request_payload)
    workshop_isolation: dict[str, str] | None = None
    #: The lineage-home backend of a workshop launch (`leaf_contract.WORKSHOP_LINEAGE_BACKENDS`,
    #: keyed by provider): the CLI's own token on a CLI launch, `http` for both HTTP providers,
    #: whose `backend_token` is the provider token (issue #534 PR-J3-b).
    lineage_backend: str | None = None
    if is_workshop:
        # The request's provider, lineage id and the warm `--workshop-lineage-id` are all
        # written by the conductor from one leaf entry and one lineage (issue #643 removed the
        # refusals that re-checked them against each other). The lineage id becomes the home's
        # path segment, so its token shape is checked (OR4-52).
        from tools.leaf_contract import WORKSHOP_LINEAGE_BACKENDS
        named = request_payload.get("workshop_provider")
        lineage_backend = WORKSHOP_LINEAGE_BACKENDS.get(named if isinstance(named, str) else "")
        requested = request_payload.get("workshop_lineage_id")
        if not (isinstance(requested, str) and _AGENT_RUN_ID_RE.match(requested.strip())):
            raise ValueError(
                "record-launch: a workshop launch request must name its lineage as a plain "
                f"token in workshop_lineage_id (got {requested!r})")
        requested = requested.strip()
        workshop_isolation = WORKSHOP_HOME_PREPARERS[lineage_backend](
            repo_root, orchestration_id, requested, resume=True)
        if workshop_isolation is None:
            if workshop_lineage_id is None:
                raise RuntimeError(
                    f"record-launch: the cold workshop launch {child_agent_run_id!r} has no "
                    "lineage home; the host prepares it before recording the launch "
                    "(`Conductor._prepare_workshop_home(..., resume=False)`, the backend's "
                    "`WORKSHOP_HOME_PREPARERS` entry)")
            return {
                "workshop_lineage_home_missing": True,
                "workshop_lineage_id": workshop_lineage_id,
            }

    # The Claude backend enforces sequential child launch via the active file. The existence
    # check here and the marker write at the end of this function are deliberately NOT under one
    # lock (issue #481). Serializing DRIVERS is the run's exclusive claim
    # (`run_workflow._exclusive_claim`, `("orch", orchestration_id)` held for the life of the
    # driver), and within one driver the conductor issues every `record-launch` as a blocking
    # subprocess, one at a time; what this guard defends against is a conductor bug launching a
    # second claude leaf in one driver. The concurrent cases that reach the gap are operator-side
    # (a hand-run `record-launch`, a degraded claim, two hosts with host-local homes);
    # `docs/RUNBOOK.md` §3-1 is canonical for when the claim does not serialize drivers, and its
    # §"Incomplete launch recovery" for what a second driver of one orchestration costs.
    if backend_token == "claude":
        active_path = _active_child_agent_run_id_path(repo_root, orchestration_id)
        if active_path.exists():
            existing_id = active_path.read_text(encoding="utf-8").strip()
            try:
                update_orchestration_status(
                    repo_root,
                    orchestration_id,
                    status="fail_closed",
                    reason_code="parallel_nodes_not_explicitly_allowed",
                )
            except Exception:
                pass
            raise RuntimeError(
                "Claude backend sequential violation: "
                f"active child agent {existing_id!r} is still running. "
                "Parallel child agent launch is not permitted on Claude backend."
            )

    step_raw = request_payload.get("step")
    node_key_raw = request_payload.get("node_key")
    # The build-relaunch guard that used to stand here is gone with issue #177. It refused a
    # second build while a prior terminal build agent lacked a step_result, because the
    # completion vouch demanded one per terminal arid and an unvouched arid deadlocked the
    # pass. The vouch no longer asks that: it reads the artifact chain and the LATEST attempt's
    # result, so an attempt that ended without a step_result is simply a failed attempt. With
    # the requirement gone the guard refuses a relaunch for a condition that no longer blocks
    # anything — and `write-step-result --backfill`, which existed to break the deadlock it
    # created, goes with it.
    if isinstance(step_raw, str) and step_raw.strip() and isinstance(node_key_raw, str) and node_key_raw.strip():
        check = pre_phase_launch(
            repo_root,
            orchestration_id=orchestration_id,
            node_key=node_key_raw.strip(),
            step=step_raw.strip(),
            backend=backend_token,
        )
        if check.get("status") == "fail_closed":
            reason_code = str(check.get("reason_code") or "child_agent_unavailable_on_execution_platform")
            try:
                update_orchestration_status(
                    repo_root,
                    orchestration_id,
                    status="fail_closed",
                    reason_code=reason_code,
                    reason_detail=str(check.get("reason_detail") or ""),
                    blocking_policy_scope=str(check.get("blocking_policy_scope") or ""),
                )
            except Exception:
                pass
            raise RuntimeError(
                "record-launch blocked by pre_phase_launch / workflow-launch-check: "
                f"reason_code={reason_code}"
            )
    # The executable this leaf is launched through, which is what
    # `_backend_runtime_bind_paths` resolves the sandbox's read-only bind of the CLI install
    # root from. Three sources, most specific first:
    #   1. the launch RESPONSE's `backend_command` — THIS leaf's own, host-authored by the
    #      conductor from the entry it is about to spawn. Since issue #28 an entry can carry
    #      its own `command:`, so the run has no single answer; binding a different executable
    #      than the one in argv puts the leaf's binary outside its own sandbox.
    #   2. `preflight.json#probe_command` — the run's `defaults`, which is what every launch
    #      used before per-substep configuration and what a legacy payload still carries.
    #   3. the default command for the preflight's own backend (not a fixed backend), so a
    #      codex preflight without a probe_command still binds ~/.codex.
    # `.get`, not `[...]`: an HTTP provider token names no CLI at all (issue #28). The bwrap
    # profile built from it is skipped for those launches — an HTTP leaf runs no
    # model-directed tool and so has nothing to confine.
    backend_command = DEFAULT_BACKEND_COMMANDS.get(backend_token, "")
    if isinstance(preflight_payload, dict):
        probe_command = preflight_payload.get("probe_command")
        if isinstance(probe_command, str) and probe_command.strip():
            backend_command = probe_command.strip()
    _launch_command = response_payload.get("backend_command") if isinstance(
        response_payload, dict) else None
    if isinstance(_launch_command, str) and _launch_command.strip():
        backend_command = _launch_command.strip()
    root = _orchestration_root(repo_root, orchestration_id)
    launches_root = root / "launches"
    launches_root.mkdir(parents=True, exist_ok=True)
    child_dialog_root = root / "agents" / child_agent_run_id / "dialogs"
    child_dialog_root.mkdir(parents=True, exist_ok=True)

    request_payload = dict(request_payload)
    request_payload.setdefault("orchestration_id", orchestration_id)
    request_payload.setdefault("agent_run_id", child_agent_run_id)
    request_payload.setdefault("parent_agent_run_id", parent_agent_run_id)
    request_payload = prepare_launch_request_payload(request_payload)
    response_payload = dict(response_payload)
    response_agent_session_id = _validate_response_agent_session_id(response_payload)
    response_payload.setdefault("agent_session_id", response_agent_session_id)
    launch_role_obj = request_payload.get("agent_role")
    if isinstance(launch_role_obj, str) and launch_role_obj.strip():
        launch_role = launch_role_obj.strip().lower()
    else:
        try:
            launch_role = _required_child_agent_kind(str(request_payload.get("step", "") or ""))
        except ValueError:
            launch_role = "unknown"
    context_obj = request_payload.get("context_id")
    launch_context_id = context_obj.strip() if isinstance(context_obj, str) and context_obj.strip() else None

    _validate_launch_request_payload(request_payload)

    prompt_text = _extract_launch_prompt_text(request_payload)
    reply_text = _extract_launch_reply_text(response_payload)

    request_ref, response_ref = _launch_refs(orchestration_id, child_agent_run_id)
    prompt_ref, reply_ref = _launch_dialog_refs(orchestration_id, child_agent_run_id)
    # The `agents/<arid>/dialogs/child.{request,response,prompt,reply}` mirrors of the four
    # `launches/` files, and the `child_launch_*_ref` fields naming them, went in issue #447
    # (D3): nothing read them, and `child.reply.txt` was not even a mirror — `record_reply_text`
    # rewrites only the `launches/` copy. `launches/` is the one launch record.
    request_payload.setdefault("launch_prompt_ref", prompt_ref)
    response_payload.setdefault("launch_reply_ref", reply_ref)

    request_path = launches_root / f"{child_agent_run_id}.request.json"
    response_path = launches_root / f"{child_agent_run_id}.response.json"
    prompt_path = launches_root / f"{child_agent_run_id}.prompt.txt"
    reply_path = launches_root / f"{child_agent_run_id}.reply.txt"

    nk = request_payload.get("node_key")
    st = request_payload.get("step")
    out_refs: dict[str, Any] = {
        "launch_request_ref": request_ref,
        "launch_response_ref": response_ref,
        "launch_prompt_ref": prompt_ref,
        "launch_reply_ref": reply_ref,
        # The exact prompt text record-launch rendered and wrote to
        # launches/<child_arid>.prompt.txt. Returned so the conductor passes it
        # verbatim to the leaf rather than re-reading the template or the written
        # prompt file. The leaf prompt is then identical in content to
        # the recorded artifact by construction (audit 1-to-1); the .prompt.txt
        # file only differs by a trailing newline the text writer appends.
        # Retained in terse output.
        "launch_prompt_text": prompt_text,
    }
    if isinstance(nk, str) and nk.strip() and isinstance(st, str) and st.strip():
        # NO ACCESS POLICY, READ MANIFEST, CAPABILITY OR OUTPUT MANIFEST. Four documents used
        # to be written here, one per question about what the child was allowed to do: which
        # roots it might read, which paths it might write, which MCP tools it might call, and
        # the secret it would present to prove all three. Every one of them described a LEAF
        # holding tools. Since Z4 (issue #171) the launch shapes are a PURE leaf — no
        # tools, no MCP configuration, a read-only sandbox, and `write_roots: []` by
        # construction, with the host writing every artifact after the child window — a
        # DETERMINISTIC substep that is the conductor's own process and is bound by nothing it
        # writes about itself, and (issue #534) a WORKSHOP leaf, whose file tools reach its own
        # work tree outside the checkout and whose one MCP server is the host's wrapper. PR-2 of that issue stopped writing them.
        #
        # What still decides something is recorded below: the read-only sandbox profile (which
        # the kernel enforces through bwrap) and the launch request itself, whose
        # `allowed_output_paths` is what `determine_substep_status` reads for a deterministic
        # substep.
        # A workshop leaf writes nothing under the checkout either — the host writes every
        # artifact from its snapshot — so it declares no output path, as a pure leaf does.
        is_pure = _leaf_contract_is_host_rendered_request(request_payload)
        # The toolchain of the target the request's pipeline is built for (issue #284; read
        # off the IR's `impl_defaults` until R4-a PR-2), so the canonical-placement helper can
        # gate the cross-phase placement on the build system (the documented Make-only
        # exception). A request whose `pipeline_ref` carries no target is refused here
        # (`TargetProfileError`).
        _pipe_ref_for_tc = str(request_payload.get("pipeline_ref") or "").strip()
        if _pipe_ref_for_tc:
            _tc_resolved = _pipeline_target_toolchain(repo_root, _pipe_ref_for_tc)
            request_payload = dict(request_payload)
            request_payload["_resolved_build_system"] = str(_tc_resolved["build_system"])
        if is_pure:
            # A pure leaf authors nothing in its window, so it declares no output path. The
            # empty list flows through the (inert for generate) lineage / cross-phase blocks
            # below.
            allowed_output_paths = []
        else:
            # A DETERMINISTIC substep. The list is not an authority — nothing enforces it
            # against the conductor's own process — it is the record of what this substep is
            # contracted to produce; `determine_substep_status` reads the conductor's own copy.
            # Only the cross-phase log below is derived from it (from `source_id`).
            allowed_output_paths = _allowed_output_paths_for_launch(
                request_payload=request_payload,
            )
        (repo_root / "workspace" / "tmp" / child_agent_run_id).mkdir(parents=True, exist_ok=True)
        # Execute step lineage bind (mandatory for ALL execute launches, not
        # only when cross-phase quality_check log is authorized): every
        # execute run must declare `source_binary_id` in the launch request,
        # and the referenced build's `binary_meta.json` must record
        # `source_source_id` matching the request's `source_id`.
        # Without this binding, an execute could run binaries from build A
        # while attributing evidence (e.g. trial_meta) to a different
        # sibling build's generation — a mixed-build forge that purely
        # in-phase logging would not catch elsewhere.
        _step_token_for_bind = str(request_payload.get("step") or "").strip().lower()
        _pipe_ref_for_bind = _normalize_rel_posix(
            str(request_payload.get("pipeline_ref") or "")
        )
        _substep_token_for_bind = str(request_payload.get("substep") or "").strip().lower()
        if (
            _step_token_for_bind == "validate"
            and _substep_token_for_bind == "execute"
            and _pipe_ref_for_bind
        ):
            _gen_id_for_bind = str(
                request_payload.get("source_id") or ""
            ).strip()
            _source_binary_id = str(
                request_payload.get("source_binary_id") or ""
            ).strip()
            if not _gen_id_for_bind:
                raise ValueError(
                    "validate.execute launch requires `source_id` in the launch "
                    "request to bind provenance to a specific source."
                )
            if not _source_binary_id:
                raise ValueError(
                    "validate.execute launch requires `source_binary_id` in the launch "
                    "request to bind provenance to a specific build. "
                    "Without this binding, evidence could be forged across "
                    "sibling builds even when in-phase logging is used."
                )
            _bm_path = (
                repo_root / _pipe_ref_for_bind / "binary" / _source_binary_id / "binary_meta.json"
            )
            if not _bm_path.is_file():
                raise ValueError(
                    f"validate.execute launch source_binary_id={_source_binary_id!r} "
                    f"does not resolve to an existing binary_meta.json at "
                    f"{_bm_path!s}. The referenced build must exist on disk "
                    "before validate.execute can attribute provenance to it."
                )
            try:
                _bm_doc = _read_json(_bm_path)
            except (OSError, json.JSONDecodeError):
                _bm_doc = None
            if not isinstance(_bm_doc, dict):
                raise ValueError(
                    f"validate.execute launch source_binary_id={_source_binary_id!r}: "
                    "binary_meta.json could not be parsed as a JSON object."
                )
            _bm_src_gen = _bm_doc.get("source_source_id")
            if (
                not isinstance(_bm_src_gen, str)
                or not _bm_src_gen.strip()
            ):
                raise ValueError(
                    f"validate.execute launch source_binary_id={_source_binary_id!r}: "
                    "binary_meta.json must record `source_source_id` to "
                    "bind validate.execute provenance. Migrate the build's metadata "
                    "before launching validate.execute against it."
                )
            if _bm_src_gen.strip() != _gen_id_for_bind:
                raise ValueError(
                    f"validate.execute launch source_id={_gen_id_for_bind!r} "
                    f"does not match build {_source_binary_id!r}'s "
                    f"source_source_id={_bm_src_gen.strip()!r}. Validate.execute "
                    "must run against the binary produced by the source "
                    "it claims provenance for."
                )
        canonical_audit_logs = _canonical_command_log_paths_for_request(
            request_payload, allowed_output_paths
        )
        # Validate cross-phase canonical placements:
        #   - Execute → source/<source_id>/ for run_quality_checks
        #   - Build → source/<source_id>/ for in-source compile_project
        #     (Make for Fortran/C-family runs project_dir=<source_id>/src/)
        # The `source_id` from the request payload is otherwise free-form
        # and could authorize writes to an unrelated generation's audit log
        # under the same pipeline. Require the referenced generate run to
        # actually exist on disk (source_meta.json must be present) and to
        # have reached pass state before granting cross-phase write authority.
        _step_token_xpv = str(request_payload.get("step") or "").strip().lower()
        _pipe_ref_xpv = _normalize_rel_posix(
            str(request_payload.get("pipeline_ref") or "")
        )
        _substep_xpv = str(request_payload.get("substep") or "").strip().lower()
        _xpv_is_validate_execute = (
            _step_token_xpv == "validate" and _substep_xpv == "execute"
        )
        if (_xpv_is_validate_execute or _step_token_xpv == "build") and _pipe_ref_xpv:
            _gen_prefix_xpv = f"{_pipe_ref_xpv}/source/"
            for _log_path in canonical_audit_logs:
                if not _log_path.startswith(_gen_prefix_xpv):
                    continue
                _tail_xpv = _log_path[len(_gen_prefix_xpv):]
                _parts_xpv = [p for p in _tail_xpv.split("/") if p]
                if not _parts_xpv:
                    continue
                _gen_id_xpv = _parts_xpv[0]
                _gen_meta = (
                    repo_root
                    / _gen_prefix_xpv
                    / _gen_id_xpv
                    / "source_meta.json"
                )
                if not _gen_meta.exists():
                    raise ValueError(
                        f"{_step_token_xpv} launch references unknown "
                        f"cross-phase source_id={_gen_id_xpv!r}: "
                        f"source_meta.json not found at {_gen_meta!s}. "
                        "Cross-phase command log authorization requires the "
                        "referenced generation to have actually run."
                    )
                # Verify the generation reached pass state BEFORE granting
                # write authority into its tree. Authorizing writes against a
                # failed/stale generation would let an Execute run mutate or
                # append to provenance files that later validators trust,
                # contaminating cross-phase artifacts irreversibly.
                try:
                    _gen_meta_doc = _read_json(_gen_meta)
                except (OSError, json.JSONDecodeError):
                    _gen_meta_doc = None
                _gen_status_raw = (
                    _gen_meta_doc.get("verification_status")
                    if isinstance(_gen_meta_doc, dict)
                    else None
                )
                _gen_status = (
                    _gen_status_raw.strip().lower()
                    if isinstance(_gen_status_raw, str)
                    else None
                )
                if _gen_status != "pass":
                    raise ValueError(
                        f"{_step_token_xpv} launch references cross-phase "
                        f"source_id={_gen_id_xpv!r} with "
                        f"verification_status={_gen_status!r} (expected "
                        "'pass'). Cannot grant build-runtime-owned write authority to "
                        "a failed/stale generation tree; this would "
                        "contaminate provenance files trusted by later "
                        "validators."
                    )
                # NOTE: execute step source_binary_id / source_id lineage
                # bind is enforced unconditionally above (see "Execute step
                # lineage bind" block before canonical_audit_logs). The
                # cross-phase loop here only handles existence + pass-state
                # of the generation referenced from the cross-phase audit
                # log path (build step's Make-only path).
        # An HTTP leaf (issue #28; a workshop leaf's host loop since issue #534 PR-J3-b) runs in
        # the conductor's own process over HTTPS: there is no child process to confine, no CLI
        # home to isolate, and no `backend_command` to pin. The launch record written BELOW — the `launches/` files, the session-index row,
        # the hook entry, the agent-graph edge — is written for it too, because those describe
        # the LAUNCH, which is as real as any other. Only the process-sandbox layer is
        # skipped, and it is marked so
        # the audit validators (which key on `sandbox_profile` presence) can tell a skipped
        # profile from a missing one.
        if is_workshop:
            # Every workshop record, on any transport, names its lineage and the home the
            # launch ran in (`<lineage backend>_workflow_home`; issue #534 PR-J3-b moved the
            # two stamps out of the CLI arm below so an HTTP workshop record carries them too).
            assert workshop_isolation is not None and lineage_backend is not None
            response_payload[f"{lineage_backend}_workflow_home"] = workshop_isolation["home"]
            response_payload["workshop_lineage_id"] = workshop_isolation["lineage_id"]
        if backend_token in _HTTP_PROVIDER_TOKENS:
            request_payload.setdefault("leaf_transport", "http")
            response_payload.setdefault("leaf_transport", "http")
            response_payload.setdefault("sandbox_runtime", "none")
            response_payload.setdefault("sandbox_enforced", False)
        elif request_payload.get("deterministic"):
            # A DETERMINISTIC substep runs in the conductor's own process
            # (`_run_deterministic_substep`): there is no child to confine and no backend home
            # to isolate. Recorded in the same shape as the HTTP arm above, and for the same
            # reason — a bwrap profile and a private CLAUDE_CONFIG_DIR built for a process that
            # never exists are a record of a confinement that never happened.
            # `_launch_setting_surface` already returns `{}` for exactly this launch; this is
            # the sandbox half of the same statement.
            #
            # `record_agent_run` stamps `sandbox_runtime: "bwrap"` on a non-HTTP row unless this
            # marks it in-process, so it takes this as its second exemption. Stamped on the RESPONSE,
            # like the HTTP one and for the same reason: the response is host-authored and
            # outside every read-write bind of a leaf's sandbox, so a leaf cannot claim it.
            request_payload.setdefault("leaf_transport", "in_process")
            response_payload.setdefault("leaf_transport", "in_process")
            response_payload.setdefault("sandbox_runtime", "none")
            response_payload.setdefault("sandbox_enforced", False)
        else:
            try:
                _resp_backend = response_payload.get("backend")
                # Every CLI leaf is a WORKSHOP leaf (issue #534; the pure leaf went in
                # PR-J4-b): the renderer refuses any other LLM request. Its lineage home was
                # found above, cold and warm alike, and stamped.
                assert workshop_isolation is not None
                profile_kwargs = WORKSHOP_PROFILE_KWARGS[backend_token](workshop_isolation)
                network = _launch_network_block(repo_root, netns_socket_dir, backend_token,
                                                response_payload)
                # One profile builder. What the profile binds and why — the checkout is NOT
                # among it (issue #227) — is stated once, in `build_lineage_bwrap_profile`'s
                # docstring and at the `--tmpfs` emission in `render_bwrap_command`.
                profile = build_lineage_bwrap_profile(
                    repo_root=repo_root,
                    orchestration_id=orchestration_id,
                    agent_run_id=child_agent_run_id,
                    backend_command=backend_command,
                    backend_type=_resp_backend if isinstance(_resp_backend, str) else "",
                    child_env=child_env,
                    network=network,
                    **profile_kwargs,
                )
                command_argv = [backend_command]
                rendered = render_bwrap_command(profile=profile, command_argv=command_argv)
                profile["rendered_command"] = rendered
                profile_path = _sandbox_profiles_dir(
                    repo_root,
                    orchestration_id,
                ) / f"{child_agent_run_id}.json"
                _write_json(profile_path, profile)
                sandbox_ref = (
                    f"workspace/orchestrations/{orchestration_id}/sandbox_profiles/{child_agent_run_id}.json"
                )
                out_refs["sandbox_profile_ref"] = sandbox_ref
                request_payload.setdefault("sandbox_profile_ref", sandbox_ref)
                response_payload.setdefault("sandbox_runtime", "bwrap")
                response_payload.setdefault("sandbox_enforced", True)
                response_payload.setdefault("sandbox_profile_ref", sandbox_ref)
                response_payload.setdefault("sandbox_command", rendered)
                # The leaf ran in a network namespace (issue #534 step 4).
                response_payload["sandbox_network"] = SANDBOX_NETWORK_NAMESPACE
            except Exception as exc:
                _write_sandbox_enforcement_violation(
                    repo_root,
                    orchestration_id,
                    agent_run_id=child_agent_run_id,
                    reason="sandbox_profile_build_failed",
                    detail={"error": str(exc)},
                )
                update_orchestration_status(
                    repo_root,
                    orchestration_id,
                    status="fail_closed",
                    reason_code="sandbox_enforcement_violation",
                    reason_detail=str(exc),
                    blocking_policy_scope="sandbox",
                )
                raise RuntimeError(f"record-launch sandbox enforcement failed: {exc}") from exc
    _write_json(request_path, request_payload)
    _write_json(response_path, response_payload)
    _write_text(prompt_path, prompt_text)
    _write_text(reply_path, reply_text)
    # THE BOOKKEEPING THAT NAMES THIS LAUNCH comes after the launch record and after every
    # check of the launch above (issue #481). The four `launches/` files are the launch record;
    # the session-index row and the graph edge each name it,
    # so by position a launch refused by any of those checks leaves none of them, and none of
    # them names a launch with no `request.json`. Before the move a late refusal left an edge
    # with no request file, which `_prune_orphan_agent_graph_edges` keeps and the completion
    # check then refuses `pass` over. What can still raise AFTER this block is the phase-state
    # transition below, on a `phase_state.json` that is unreadable or names another
    # orchestration — a damaged host record, not a check of this launch. It leaves the whole
    # launch record behind, `request.json` included, so its edge stays prunable on resume.
    _append_session_run_index_entry(
        repo_root,
        orchestration_id,
        agent_run_id=child_agent_run_id,
        agent_session_id=response_agent_session_id,
        context_id=launch_context_id,
        agent_role=launch_role,
        status="running",
        workshop_lineage_id=(workshop_isolation["lineage_id"]
                             if workshop_isolation is not None else None),
    )
    graph_path = root / "agent_graph.json"
    graph = _load_graph(graph_path)
    edge = {
        "parent_agent_run_id": parent_agent_run_id,
        "child_agent_run_id": child_agent_run_id,
        "relation_type": relation_type,
    }
    if edge not in graph["edges"]:
        graph["edges"].append(edge)
    _write_json(graph_path, graph)
    if isinstance(nk, str) and nk.strip() and isinstance(st, str) and st.strip():
        step_tok = st.strip().lower()
        _transition_node_step_phase_state(
            repo_root,
            orchestration_id,
            node_key=nk.strip(),
            step=step_tok,
            new_state="launch_recorded",
            event="record_launch",
            agent_run_id=child_agent_run_id,
        )
        _transition_node_step_phase_state(
            repo_root,
            orchestration_id,
            node_key=nk.strip(),
            step=step_tok,
            new_state="child_running",
            event="child_launched",
            agent_run_id=child_agent_run_id,
        )
    # L-NEW-2: route active-child marker writes through _atomic_write_text
    # for consistency with Adv-27. The marker file is queried only for
    # existence (not contents), but a concurrent reader observing a partial
    # write would still log a confusing half-empty file; atomic writes
    # eliminate that observability gap.
    if backend_token == "claude":
        _atomic_write_text(
            _active_child_agent_run_id_path(repo_root, orchestration_id),
            child_agent_run_id,
        )
    # Adv-16: backend-neutral per-arid active-child marker. Codex lack
    # the single-active-child constraint that the Claude marker enforces;
    # `finalize-child` requires this marker on every backend. Marker is created
    # here for ALL backends and removed by deactivate-child / record-agent-run terminal.
    marker_dir = _active_children_dir(repo_root, orchestration_id)
    marker_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        _active_child_marker_path(repo_root, orchestration_id, child_agent_run_id),
        child_agent_run_id,
    )

    return out_refs


def _agent_runs_writer_active(runs_path: Path) -> bool:
    """Adv-40: probe whether a writer currently holds the agent_runs.jsonl
    fcntl lock. True = lock contended (writer is appending), False = lock
    free (no active writer). Used to distinguish an in-flight concurrent
    append (which legitimately leaves the trailing line truncated) from
    durable crash corruption that masquerades as the same shape.
    """
    lock_path = runs_path.parent / (runs_path.name + ".lock")
    if not lock_path.exists():
        # No lock file means no record_agent_run has ever held the lock here.
        # A truncated tail is therefore not an in-flight append.
        return False
    try:
        fd = os.open(str(lock_path), os.O_RDWR)
    except OSError:
        # Permission / race; cannot determine — fall back to "active" so we
        # do not raise on a possibly-recoverable in-flight append.
        return True
    try:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError:
            # Contention — writer is active.
            return True
        # We acquired the lock → no writer was holding it. Release.
        try:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _read_existing_run_ids(path: Path, *, caller_holds_lock: bool = False) -> set[str]:
    """Return the set of agent_run_ids recorded in agent_runs.jsonl (`path`).

    The one parser is `_load_run_records`, which this reads through: a truncated last line
    is skipped only while a writer holds the record_agent_run fcntl lock (Adv-21 / Adv-40),
    and any other malformed line raises `RuntimeError("...durable corruption...")`, which
    is what keeps a duplicate terminal entry from slipping past `record_agent_run`'s
    duplicate-id check. `caller_holds_lock=True` (the locked re-check inside
    record_agent_run, NEW-H1) treats every malformed line as durable corruption.
    """
    return set(_load_run_records(path.parent, caller_holds_lock=caller_holds_lock))


def record_agent_run(
    repo_root: Path,
    orchestration_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    root = _orchestration_root(repo_root, orchestration_id)
    root.mkdir(parents=True, exist_ok=True)
    runs_path = root / "agent_runs.jsonl"

    agent_run_id = payload.get("agent_run_id")
    if not isinstance(agent_run_id, str) or not agent_run_id.strip():
        raise ValueError("agent_run_id must be non-empty string")
    agent_run_id = agent_run_id.strip()

    role = payload.get("agent_role") or payload.get("agent_type") or payload.get("role")
    role_token = _normalized_agent_role(role) or None
    if role_token is None:
        raise ValueError("agent_role must be non-empty string")
    # Fail closed on a role outside the vocabulary. Previously any string was accepted here and
    # simply fell through both branches below — and the terminal write audit (reached via
    # `_validate_terminal_run_payload`) returned early for it, so recording a terminal status
    # under a misspelled role SWITCHED OFF the unauthorized-write check rather than being
    # rejected; measured on this checkout at the time. That audit is deleted (issue #171 PR-2),
    # and the refusal stays: `_validate_terminal_run_payload`'s surviving checks — the pure
    # leaf's empty `output_refs` and the pass row's declared outputs — key on the same
    # membership, so a role outside the vocabulary would switch THOSE off instead.
    if role_token not in AGENT_RUN_ROLES:
        raise ValueError(
            f"agent_role must be one of {sorted(AGENT_RUN_ROLES)}; got {role_token!r}"
        )
    if role_token in {"step", "substep"}:
        _require_preflight_launchable(
            repo_root,
            orchestration_id,
            enforce_live_probe=False,
        )

    # `agent_backend` is the conductor's `entry.backend_token`, authorized per launch by
    # `record_launch`; it is normalized here, not re-checked.
    backend_token = str(payload.get("agent_backend") or "").strip().lower()
    payload["agent_backend"] = backend_token

    existing = _read_existing_run_ids(runs_path)
    if agent_run_id in existing:
        raise ValueError(f"duplicate agent_run_id: {agent_run_id}")

    payload = dict(payload)
    payload["agent_run_id"] = agent_run_id
    payload["agent_role"] = role_token
    payload.setdefault("started_at", _utc_now_iso())

    if role_token in {"step", "substep"}:
        payload.setdefault("context_isolated", True)
        request_ref, response_ref = _launch_refs(orchestration_id, agent_run_id)
        prompt_ref, reply_ref = _launch_dialog_refs(orchestration_id, agent_run_id)
        payload.setdefault("launch_request_ref", request_ref)
        payload.setdefault("launch_response_ref", response_ref)
        payload.setdefault("launch_prompt_ref", prompt_ref)
        payload.setdefault("launch_reply_ref", reply_ref)
        _validate_step_or_substep_launch_refs(repo_root, payload)
        # Auto-derive identity fields the orchestration agent need not re-supply.
        # `parent_agent_run_id` and `agent_model` are recorded into the launch
        # request by record-launch (parent unconditionally; agent_model required
        # for step/substep launches), so we backfill them here rather than rely
        # on the caller's record-agent-run payload — which the canonical field
        # tables historically omitted, leaving every step/substep entry without
        # them and failing pre_judge. Read defensively: if the
        # request is missing/unreadable, leave the fields absent so the terminal
        # validator and pre_judge still fail loud rather than silently passing.
        try:
            _launch_req = _read_json(repo_root / str(payload["launch_request_ref"]))
        except (OSError, json.JSONDecodeError, KeyError):
            _launch_req = None
        if isinstance(_launch_req, dict):
            # `node_key` / `step` / `substep` / `derivation_key` join the two above (issue
            # #250): the row is the attempt record of A1, and an attempt is identified by
            # the derivation it ran under, whatever its outcome — a FAILED attempt keeps its
            # key here when no stage meta ever records it.
            for _field in ("parent_agent_run_id", "agent_model", "node_key", "step",
                           "substep", "derivation_key"):
                _val = _launch_req.get(_field)
                if isinstance(_val, str) and _val.strip():
                    payload.setdefault(_field, _val.strip())

    status = payload.get("status")
    if isinstance(status, str) and status.strip().lower() in TERMINAL_STATUSES:
        payload.setdefault("finished_at", _utc_now_iso())

    dialogs_root = root / "agents" / agent_run_id / "dialogs"
    dialogs_root.mkdir(parents=True, exist_ok=True)
    result_ref, summary_ref = _agent_result_refs(orchestration_id, agent_run_id)
    payload.setdefault("agent_result_ref", result_ref)
    payload.setdefault("agent_summary_ref", summary_ref)

    # Adv-24/H2: serialize the duplicate-recheck + terminal validation +
    # dialog/append commit phase against any concurrent finalizer for the
    # same orchestration. _validate_terminal_run_payload runs inside the
    # lock (H2 fix) because its violation path calls _cleanup_agent_tmp_root —
    # a destructive operation that two unsynchronized finalizers must not race.
    with (
        _json_transaction_exclusive_lock(root),
        _runs_jsonl_exclusive_lock(repo_root, orchestration_id),
    ):
        _recover_json_transactions_unlocked(root)
        # NEW-H1: caller holds the lock, so writer-active probe would
        # self-contend. Pass the flag so durable corruption is surfaced
        # rather than masked.
        existing_locked = _read_existing_run_ids(runs_path, caller_holds_lock=True)
        if agent_run_id in existing_locked:
            raise ValueError(
                f"duplicate agent_run_id: {agent_run_id} "
                f"(race detected on locked re-check)"
            )
        try:
            if role_token in {"step", "substep"}:
                launch_response_path = repo_root / payload["launch_response_ref"]
                launch_response_payload = _read_json(launch_response_path)
                sandbox_ref = launch_response_payload.get("sandbox_profile_ref")
                # An HTTP leaf (issue #28) is answered from the conductor's own process: there
                # is no child process, so there is nothing for bwrap to confine and
                # `record_launch` records `leaf_transport: "http"` with no profile. The
                # sandbox stamps below name a profile that, for such a launch, correctly does
                # not exist. The exemption is granted on the LAUNCH RESPONSE — host-authored,
                # and outside every read-write bind of a leaf's sandbox — and only when it names a genuine HTTP
                # provider token, so a leaf cannot claim it. Everything else about the terminal
                # payload, including the unauthorized-write check below, still applies.
                _launch_backend = launch_response_payload.get("backend")
                _http_leaf = (
                    str(launch_response_payload.get("leaf_transport") or "").strip().lower()
                    == "http"
                    and isinstance(_launch_backend, str)
                    and _launch_backend.strip().lower() in _HTTP_PROVIDER_TOKENS)
                # A DETERMINISTIC substep (Z4, issue #171) runs in the conductor's own process
                # for the same structural reason, and `record_launch` marks it the same way:
                # `leaf_transport: "in_process"` with no profile. Both documents the exemption
                # reads are HOST-AUTHORED and outside every write root: the launch response,
                # and — since the conjunct below — the launch request, reached through the row's
                # `launch_request_ref`. That ref is the row's, so the row does participate; what
                # `_validate_step_or_substep_launch_refs` checks of it is that the target
                # exists, not that it is the canonical path for this arid. Nothing a leaf writes
                # is read here, which is the property that matters, and the round-2 review is
                # why this says so precisely rather than "never off the row".
                #
                # TWO conditions, symmetric with the HTTP arm above, which requires the
                # transport string AND a genuine provider token. The round-1 review found this
                # arm taking the string alone. No leaf can reach either document — both are
                # host-authored and outside every write root, so nothing was exploitable; what
                # was missing is that the exemption did not read the fact it rests on, and the
                # HTTP arm beside it does (`validate_pipeline_semantics` re-checks both there).
                #
                # WHAT IS PINNED, stated because the halves differ. Neutering this whole arm
                # kills 16 tests in `test_orchestration_runtime.py`, so the exemption itself is
                # well witnessed. The `deterministic` conjunct added here is NOT driven by a
                # case of its own: constructing one needs a whole orchestration on disk with a
                # response saying `in_process` and a request that does not, and the finding it
                # answers is a symmetry rather than a reachable hole. Recorded rather than
                # pinned with a source-text assertion, which would pin the spelling and not the
                # behaviour.
                _launch_request_ref = payload.get("launch_request_ref")
                _launch_request_payload: Any = None
                if isinstance(_launch_request_ref, str) and _launch_request_ref.strip():
                    try:
                        _launch_request_payload = _read_json(
                            repo_root / _launch_request_ref.strip())
                    except (OSError, ValueError, json.JSONDecodeError):
                        # An unreadable request leaves the exemption UNCLAIMED: a row that
                        # cannot prove it was a deterministic launch is recorded as a sandboxed
                        # one.
                        _launch_request_payload = None
                _in_process_leaf = (
                    str(launch_response_payload.get("leaf_transport") or "").strip().lower()
                    == "in_process"
                    and isinstance(_launch_request_payload, dict)
                    and _launch_request_payload.get("deterministic") is True)
                if _http_leaf or _in_process_leaf:
                    payload.setdefault("sandbox_runtime", "none")
                    payload.setdefault("sandbox_enforced", False)
                    payload.setdefault(
                        "leaf_transport", "http" if _http_leaf else "in_process")
                else:
                    # `record_launch` stamps bwrap, enforcement, the network namespace and the
                    # profile on every CLI launch or fails closed, and `spawn_leaf` refuses to
                    # launch without the profile; the response is host-written.
                    payload.setdefault("sandbox_runtime", "bwrap")
                    payload.setdefault("sandbox_enforced", True)
                    payload.setdefault("sandbox_profile_ref", str(sandbox_ref).strip())
            _validate_terminal_run_payload(
                repo_root, orchestration_id, payload, caller_holds_lock=True,
            )
        except ValueError as exc:
            # Terminal validation refused the payload. Persist a fail entry to a SEPARATE log so audit
            # tools can see that this agent_run_id reached record-agent-run
            # with an invalid payload — without polluting agent_runs.jsonl
            # (which the duplicate-detection check would otherwise refuse on
            # retry). The retry path (typically with the same agent_run_id
            # after fixing the payload) therefore remains operational.
            invalid_path = root / "agent_runs_invalid.jsonl"
            fail_entry: dict[str, Any] = dict(payload)
            fail_entry["status"] = "fail"
            fail_entry["finished_at"] = _utc_now_iso()
            fail_entry["fail_reason"] = "terminal_payload_validation_error"
            fail_entry["fail_message"] = str(exc)
            with invalid_path.open("a", encoding="utf-8") as _h:
                _h.write(json.dumps(fail_entry, ensure_ascii=False) + "\n")
            raise
        # Summary text needs the (possibly-mutated) payload.
        summary_text = _extract_agent_summary_text(payload)
        _validate_agent_summary_text(payload, summary_text)
        _write_json(dialogs_root / "agent.result.json", payload)
        _write_text(dialogs_root / "agent.summary.txt", summary_text)
        # Adv-35: append the durable terminal record FIRST. The destructive
        # tmp cleanup runs after this append (outside the lock) and only
        # then is the cleanup_committed marker written. Validator treats an
        # arid as truly terminated only when BOTH the terminal entry AND
        # the committed marker exist — so a partial failure (cleanup fails
        # silently, or process dies between append and commit) preserves
        # the recovery scratch under workspace/tmp/<arid>/ for diagnostics
        # rather than losing it ahead of a durable state transition.
        with runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    status_token = str(payload.get("status", "")).strip().lower()
    if status_token in TERMINAL_STATUSES:
        session_obj = payload.get("agent_session_id")
        session_value = session_obj.strip() if isinstance(session_obj, str) and session_obj.strip() else agent_run_id
        context_obj = payload.get("context_id")
        context_value = context_obj.strip() if isinstance(context_obj, str) and context_obj.strip() else None
        _append_session_run_index_entry(
            repo_root,
            orchestration_id,
            agent_run_id=agent_run_id,
            agent_session_id=session_value,
            context_id=context_value,
            agent_role=role_token,
            status=status_token,
        )

    if role_token in {"step", "substep"}:
        status_raw = payload.get("status")
        status_lower = status_raw.strip().lower() if isinstance(status_raw, str) else ""
        if status_lower in TERMINAL_STATUSES:
            nk_done = payload.get("node_key")
            st_done = payload.get("step")
            if isinstance(nk_done, str) and nk_done.strip() and isinstance(st_done, str) and st_done.strip():
                _transition_node_step_phase_state(
                    repo_root,
                    orchestration_id,
                    node_key=nk_done.strip(),
                    step=st_done.strip().lower(),
                    new_state="child_finished",
                    event="record_agent_run_terminal",
                    agent_run_id=agent_run_id,
                )
            if backend_token == "claude":
                _active_child_agent_run_id_path(repo_root, orchestration_id).unlink(missing_ok=True)
            # Adv-16: also clear backend-neutral per-arid marker.
            _active_child_marker_path(
                repo_root, orchestration_id, agent_run_id
            ).unlink(missing_ok=True)

    # Adv-35/36: terminal record is now durable in agent_runs.jsonl. Run the
    # destructive tmp cleanup, then write the cleanup_committed marker ONLY
    # IF the cleanup actually completed (rmtree succeeded or tmp was already
    # absent). Adv-36: a refusal (ownership / collision / symlink) or a
    # silent rmtree OSError must NOT publish the committed marker —
    # otherwise validator would revoke exemption while leftover scratch
    # remains, exactly the "broken two-phase invariant" Codex flagged.
    final_status = str(payload.get("status", "")).strip().lower()
    if final_status in TERMINAL_STATUSES or final_status == "fail_closed":
        cleanup_done = _cleanup_agent_tmp_root(
            repo_root, orchestration_id, agent_run_id=agent_run_id
        )
        if cleanup_done:
            _write_cleanup_committed_marker(
                repo_root, orchestration_id, agent_run_id=agent_run_id
            )

    return payload


def deactivate_child_agent(
    repo_root: Path,
    orchestration_id: str,
    *,
    child_run_id: str,
) -> dict[str, Any]:
    """Clear active-child markers for a SUBSTEP / STEP child run.

    **M3 design note**: This function is intentionally asymmetric vs the
    orchestration agent's own arid. The orch agent has no `record-launch`
    handshake (no per-arid `launches/<arid>.request.json`), so it has no
    active-child marker to clear and is finalized via
    `update_orchestration_status` directly. The orch's own scratch is cleaned at terminal `set-status` time and is
    guarded by the `is_owner_via_orchestration` proof in
    `_cleanup_agent_tmp_root` (orchestration_meta.json identity).
    """
    # Adv-18: validate-first, unlink-second. The previous implementation
    # unlinked the per-arid backend-neutral marker (Adv-16) BEFORE checking
    # the legacy Claude marker, which meant a stray/misrouted deactivate-child
    # call could clear a still-running child's per-arid marker, after which
    # that child's own finalize-child is refused.
    # Both marker checks now run before any state mutation.
    active_path = _active_child_agent_run_id_path(repo_root, orchestration_id)
    legacy_marker_present = active_path.is_file()
    if legacy_marker_present:
        active_value = active_path.read_text(encoding="utf-8").strip()
        if active_value != child_run_id:
            raise ValueError(
                "active child run mismatch: "
                f"expected={child_run_id!r}, actual={active_value!r}"
            )
    per_arid_marker = _active_child_marker_path(
        repo_root, orchestration_id, child_run_id
    )
    per_arid_present = per_arid_marker.is_file()
    if not legacy_marker_present and not per_arid_present:
        # Idempotent: already deactivated (or never launched).
        return {
            "deactivated_child_run_id": child_run_id,
            "orchestration_id": orchestration_id,
            "deactivated_at": _utc_now_iso(),
            "already_inactive": True,
        }
    # A CHILD-AUTHORED PATH SNAPSHOT used to be captured here, freezing the FS-diff against
    # the write baseline before any later host write could contaminate it. It fed the terminal
    # write audit, which issue #171 PR-2 retired along with the baseline: the diff measured the
    # host's own writes against a grant no leaf holds.
    # Validation passed and at least one marker exists — atomic unlink phase.
    per_arid_marker.unlink(missing_ok=True)
    active_path.unlink(missing_ok=True)
    return {
        "deactivated_child_run_id": child_run_id,
        "orchestration_id": orchestration_id,
        "deactivated_at": _utc_now_iso(),
        "already_inactive": False,
    }


def record_reply_text(
    repo_root: Path,
    orchestration_id: str,
    *,
    agent_run_id: str,
    reply_text: str,
) -> dict[str, Any]:
    _, reply_ref = _launch_dialog_refs(orchestration_id, agent_run_id)
    reply_path = repo_root / reply_ref
    _write_text(reply_path, reply_text)
    return {
        "orchestration_id": orchestration_id,
        "agent_run_id": agent_run_id,
        "reply_ref": reply_ref,
        "recorded_at": _utc_now_iso(),
    }


def finalize_child(
    repo_root: Path,
    orchestration_id: str,
    *,
    agent_run_id: str,
    reply_text: str,
    agent_run_payload: dict[str, Any],
) -> dict[str, Any]:
    """One-call child finalization: collapse the finalize CLI round-trips into one.

    Performs, in the mandated order and in a single process,
    `deactivate_child_agent` -> `record_reply_text` -> `record_agent_run`, reusing those
    functions unchanged so every guard is preserved (the active_child ordering).

    `agent_run_payload` is the `record-agent-run` payload; the conductor writes its
    `agent_run_id` and `--agent-run-id` from one value.
    """
    arid = agent_run_id.strip() if isinstance(agent_run_id, str) else ""
    if not arid:
        raise ValueError("finalize-child requires non-empty --agent-run-id")
    # The payload flags come in two forms (inline / file), and the conductor always uses
    # the file one — so every message here names both, or an operator is sent to inspect
    # an argument that was never passed.
    if not isinstance(reply_text, str) or not reply_text.strip():
        raise ValueError(
            "finalize-child requires a non-empty reply (--reply-text or --reply-from-stdin)")
    # The launch must still be open: `record_launch` writes this marker last and the first
    # finalization consumes it. Without the check a second finalization of the same arid, or
    # one for an arid never launched, would overwrite `launches/<arid>.reply.txt` before
    # `record_agent_run` refused it, leaving a reply that disagrees with the recorded row.
    # The retired `record_child_return` used to refuse here (issue #447, D2).
    if not _active_child_marker_path(repo_root, orchestration_id, arid).is_file():
        raise ValueError(
            f"finalize-child: no active_children/{arid}.txt marker — the run was never "
            f"launched, is already finalized, or was released by a manual "
            f"`deactivate-child`. Nothing was written. A run released by hand and not yet "
            f"finalized is recovered with `--resume`, which prunes the unfinalized child."
        )
    deactivation = deactivate_child_agent(
        repo_root,
        orchestration_id,
        child_run_id=arid,
    )
    reply = record_reply_text(
        repo_root,
        orchestration_id,
        agent_run_id=arid,
        reply_text=reply_text,
    )
    # No usage backfill here. It used to reconstruct the child's token usage from the
    # ~/.claude transcript when the caller left `usage` absent, and it could not succeed:
    # the workflow does not read ~/.claude (access boundary), and the transcripts are
    # machine-local and ephemeral besides — so every agentic leaf recorded
    # `{"status": "unavailable", "reason": "no locatable child transcript"}`, a marker
    # describing a lookup that policy would never let succeed (issue #47). The conductor now
    # supplies `usage` on EVERY path from the leaf's own output — a normalized dict, or an
    # explicit `not_measured` / `unavailable` marker naming why there is none
    # (`workflow_conductor._leaf_usage_row`) — so there is nothing left to back-fill.
    run_record = record_agent_run(
        repo_root,
        orchestration_id,
        agent_run_payload,
    )

    result: dict[str, Any] = {
        "orchestration_id": orchestration_id,
        "agent_run_id": arid,
        "status": run_record.get("status"),
        "deactivated_child_run_id": deactivation.get("deactivated_child_run_id", arid),
        "reply_ref": reply.get("reply_ref"),
        "finalized_at": _utc_now_iso(),
    }
    return result


def write_step_result(
    repo_root: Path,
    orchestration_id: str,
    *,
    node_key: str,
    step: str,
    agent_run_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    _require_preflight_launchable(
        repo_root,
        orchestration_id,
        enforce_live_probe=False,
    )
    node_safe = _node_key_to_safe(node_key)
    step_token = step.strip().lower()
    root = _orchestration_root(repo_root, orchestration_id)
    result_path = root / "steps" / node_safe / step_token / agent_run_id / "step_result.json"

    _phase_state_allows_write_step_result(
        repo_root,
        orchestration_id,
        node_key=node_key,
        step=step,
    )
    result = dict(payload)
    result.setdefault("executor_agent_run_id", agent_run_id)
    result.setdefault("required_outputs", [])
    result.setdefault("failed_substeps", [])

    _validate_step_result_payload(
        repo_root,
        orchestration_id,
        node_key=node_key,
        step=step,
        agent_run_id=agent_run_id,
        payload=result,
    )

    # Stamp the phase's certification into its stage meta. Placed AFTER the payload
    # validation (which has just proved every declared deliverable exists) and BEFORE the
    # step_result is written, so a phase that cannot be certified never gets a passing
    # step_result to certify it. The child windows are all closed by now — the phase gate
    # above requires `child_finished` — so this host write lands in no leaf's FS-diff.
    _declared_outputs = [
        r for r in (result.get("required_outputs") or [])
        if isinstance(r, str) and r.strip()
    ]
    if str(result.get("status", "")).strip().lower() == "pass":
        _stamp_certification(
            repo_root,
            step=step_token,
            required_outputs=_declared_outputs,
            derivation=result.get("derivation"),
        )
    else:
        # A non-passing phase leaves no certification behind — including one a leaf wrote
        # into its own stage meta.
        _strip_certification(repo_root, step=step_token, required_outputs=_declared_outputs)

    # A pre-existing step_result means this (node, phase) is on a LATER ATTEMPT: for a
    # substep-aware phase the path is keyed by the constant orchestration executor arid, so
    # every attempt writes to the same file. Move the prior one aside under a distinct
    # `.overwritten.<N>.` name — unconditionally, because an attempt's own account of itself is
    # a record and the next one must not silently replace it.
    #
    # What used to stand here as well was a tombstone of the prior attempt's substep arids.
    # That existed for the completion vouch's "every terminal arid is vouched by a
    # step_result" rule, which issue #177 removed: the vouch now reads the LATEST attempt's
    # result and the artifact chain, so a prior attempt's arids need no consumer. With the
    # rule gone the whole apparatus behind it goes — the recorded-substep reader, the
    # this-phase filter, the vouched-elsewhere sweep and the superseded set.
    archived_prior_path: Path | None = None
    if result_path.exists():
        seq = 1
        while (archived_prior_path := result_path.with_name(
                f"step_result.overwritten.{seq}.json")).exists():
            seq += 1
        result_path.rename(archived_prior_path)

    try:
        _write_json(result_path, result)
    except BaseException:
        # Roll the overwrite back whole, so a retry sees the exact pre-write state.
        # `_write_json` must stay inside this guard — after the rename the prior attempt
        # exists ONLY under its archive name, so letting a failed write escape would
        # leave result_path absent and the next attempt blind to the prior substep ids.
        # BaseException: a KeyboardInterrupt or an OSError mid-write must restore too.
        # `_write_json` is atomic (temp file + os.replace), so when it raised nothing new
        # was committed at result_path; only the archive is moved back.
        if archived_prior_path is not None:
            try:
                archived_prior_path.rename(result_path)
            except OSError:
                pass
        raise

    _transition_node_step_phase_state(
        repo_root,
        orchestration_id,
        node_key=node_key,
        step=step_token,
        new_state="step_result_written",
        event="write_step_result",
        agent_run_id=agent_run_id,
    )

    return result


def revoke_artifact(
    repo_root: Path,
    orchestration_id: str,
    *,
    node_key: str,
    step: str,
    reason: str,
    trigger_agent_run_id: str,
    last_fail_reason: str | None = None,
    severity: str | None = None,
    repair_strategy: str | None = None,
) -> dict[str, Any]:
    """Revoke the stage meta of `(node_key, step)` — the half of a re-derivation decision that
    reaches the ARTIFACT.

    The conductor calls this the moment it decides to re-run a phase. Everything else a retry
    does (resetting the phase state, rotating the producer id) is this orchestration's own
    bookkeeping, and a COLD re-run reads none of it; the revocation is what `check-phase-certified`
    consults, so it is what carries the decision into every later run.

    Downstream phases need no revocation of their own: each binds the output hash of the phase
    above it in its derivation key, so a re-derived phase whose output differs moves their
    keys. A phase whose meta was never written is a `noop` — there is
    nothing to revoke, which is not an error. (Validate certified no meta until issue #250
    PR-1 gave it `validate_meta.json`; every phase certifies one now.)

    `last_fail_reason` overwrites the meta's own when given: on the routes that carry findings
    it is what the resumed run seeds the repair from. `severity` travels with it for the same
    reason — the G5 policy turns `critical` into a context-discarding `restart` and everything
    else into a warm `reuse`, and a resume that read the findings without the grade would
    re-enter a `critical` as a `major`.
    """
    _require_preflight_launchable(repo_root, orchestration_id, enforce_live_probe=False)
    step_token = step.strip().lower()
    meta_path = _revocable_stage_meta_path(
        repo_root, orchestration_id, node_key=node_key, step=step_token)
    result: dict[str, Any] = {
        "status": "noop",
        "orchestration_id": orchestration_id,
        "node_key": node_key.strip(),
        "step": step_token,
        "meta_ref": None,
        "prior_verification_status": None,
        "reason": "no_meta",
        "still_certified": False,
        "certified_by": None,
        "certified_pipeline_ref": None,
    }
    if meta_path is None or not meta_path.is_file():
        # `noop` is the one answer that looks identical in the good case (none was written
        # yet) and the bad one (the decision did not reach the artifact).
        # Answer the distinguishing question HERE, where it is a pure read: `_phase_certified`
        # writes nothing. `check_phase_certified` would have been the obvious thing for the
        # conductor to ask instead, and it is the wrong one — it TRANSITIONS the phase to
        # `skipped_certified` whenever certified, which is precisely the state clause (e) of the
        # completion vouch reads as an EXEMPTION. Probing through it recorded "this phase was
        # skipped because it was certified" at the instant the conductor declared the
        # re-derivation lost.
        #
        # `certified_by` names WHAT certifies the phase now (the id `check_phase_certified`
        # records, `_certified_by_ref`). The pair alone cannot tell the #177 failure from the
        # documented state of a failed `--rederive` attempt (issue #369): there the attempt
        # wrote no meta, and the STANDING output — whose id is not the attempt's — stays
        # selected (`docs/RUNBOOK.md`). Only the conductor holds the attempt's id, so it is the
        # one that decides; this answers the fact. A source / binary / run id is numbered per
        # pipeline, so `certified_pipeline_ref` names the pipeline it is one of (a forced
        # Compile can open a fresh pipeline whose first source repeats the standing one's id).
        # Compile names none — its `certified_by` is already a path, and its chain stops at the
        # IR.
        certified, detail = _phase_certified(repo_root, orchestration_id, node_key, step_token)
        result["still_certified"] = bool(certified)
        result["certified_by"] = (_certified_by_ref(step_token, detail) or None
                                  if certified else None)
        result["certified_pipeline_ref"] = (detail.get("pipeline_ref") or None
                                            if certified else None)
        return result
    revoked = _revoke_stage_meta(
        repo_root,
        meta_path,
        reason=reason,
        trigger_agent_run_id=trigger_agent_run_id,
        last_fail_reason=last_fail_reason,
        severity=severity,
        repair_strategy=repair_strategy,
    )
    result.update(revoked)
    result.pop("reason", None)
    return result


def reset_phase(
    repo_root: Path,
    orchestration_id: str,
    *,
    node_key: str,
    from_phase: str,
    reason: str,
    trigger_agent_run_id: str,
) -> dict[str, Any]:
    """Reset `from_phase` and every phase downstream of it to `not_started` in
    `phase_state.json`, recording a `phase_reset` event carrying the routing reason.

    This is a RECORD, not a gate: `record_launch` has no phase-state precondition, so a phase
    can be re-run without it. It is kept because the phase state is what an operator and the
    completion vouch read back — a phase left at `step_result_written` while its artifact has
    been revoked describes a run that is not happening.
    """
    _require_preflight_launchable(repo_root, orchestration_id, enforce_live_probe=False)
    from_token = from_phase.strip().lower()
    affected = list(STEP_KEYS_FOR_NODE_STATE[STEP_KEYS_FOR_NODE_STATE.index(from_token):])
    node_safe = _node_key_to_safe(node_key.strip())
    transitions: list[dict[str, str]] = []
    for phase in affected:
        doc = _load_phase_state(repo_root, orchestration_id)
        inner = (doc or {}).get("node_states", {}).get(node_safe)
        previous = inner.get(phase) if isinstance(inner, dict) else None
        _transition_node_step_phase_state(
            repo_root,
            orchestration_id,
            node_key=node_key,
            step=phase,
            new_state="not_started",
            event="phase_reset",
            agent_run_id=trigger_agent_run_id.strip() or None,
            reason=reason,
        )
        transitions.append({
            "step": phase,
            "from": previous if isinstance(previous, str) and previous else "not_started",
            "to": "not_started",
        })
    return {
        "status": "reset",
        "orchestration_id": orchestration_id,
        "node_key": node_key.strip(),
        "from_phase": from_token,
        "affected_phases": affected,
        "transitions": transitions,
    }


def _rewrite_orchestration_run_row(
    repo_root: Path,
    orchestration_id: str,
    *,
    should_rewrite: Callable[[dict[str, Any]], bool],
    apply_mutation: Callable[[dict[str, Any]], None],
) -> bool:
    """Rewrite the orchestration's own agent_runs.jsonl row in place.

    `init` appends a `{agent_role:orchestration, status:running}` row but
    agent_runs.jsonl is append-only and a second `record-agent-run` for the same
    arid is rejected as `duplicate agent_run_id`, so there is no forward path to
    update it. This helper rewrites the single row matching the meta's
    `orchestration_agent_run_id` with `agent_role == "orchestration"` IN PLACE
    (not append) under the agent_runs.jsonl exclusive lock rather than appending,
    so it never trips the duplicate guard. `should_rewrite` decides whether the matched row needs changing (for
    idempotency); `apply_mutation` mutates it. Returns True iff a row was rewritten.
    """
    root = _orchestration_root(repo_root, orchestration_id)
    runs_path = root / "agent_runs.jsonl"
    if not runs_path.is_file():
        return False

    orch_arid: str | None = None
    try:
        meta = _read_json(root / "orchestration_meta.json")
    except (OSError, json.JSONDecodeError):
        meta = None
    if isinstance(meta, dict):
        v = meta.get("orchestration_agent_run_id")
        if isinstance(v, str) and v.strip():
            orch_arid = v.strip()
    if orch_arid is None:
        return False

    changed = False
    with _runs_jsonl_exclusive_lock(repo_root, orchestration_id):
        raw = runs_path.read_text(encoding="utf-8")
        ends_nl = raw.endswith("\n")
        raw_lines = raw.splitlines()
        for idx, line in enumerate(raw_lines):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if (
                str(obj.get("agent_run_id", "")).strip() == orch_arid
                and str(obj.get("agent_role", "")).strip() == "orchestration"
                and should_rewrite(obj)
            ):
                apply_mutation(obj)
                raw_lines[idx] = json.dumps(obj, ensure_ascii=False)
                changed = True
                break
        if changed:
            body = "\n".join(raw_lines)
            if ends_nl:
                body += "\n"
            _atomic_write_text(runs_path, body)
    return changed


def _sync_orchestration_session_index_status(
    repo_root: Path,
    orchestration_id: str,
    status: str,
) -> bool:
    """Reconcile the orchestration's own session_run_index.json row to `status`.

    The session index orchestration row is seeded `running` by `init` and — unlike
    the agent_runs.jsonl row, which `_finalize_orchestration_run_row` rewrites — had
    no forward path to a terminal status, leaving session-index-based audits seeing
    the orchestration `running` even after `orchestration_meta.json` reached
    `pass`/`fail`/`fail_closed`. Called alongside `_finalize_orchestration_run_row`
    (terminal) and `_reset_orchestration_run_row_to_running` (`running`, resume inverse) so both
    audit surfaces stay in sync at every call site. Idempotent: a no-op once the row
    already equals `status`, so same-terminal set-status replays do not rewrite it
    (preserving `updated_at`). Returns True iff the row was updated.
    """
    root = _orchestration_root(repo_root, orchestration_id)
    orch_arid: str | None = None
    try:
        meta = _read_json(root / "orchestration_meta.json")
    except (OSError, json.JSONDecodeError):
        meta = None
    if isinstance(meta, dict):
        v = meta.get("orchestration_agent_run_id")
        if isinstance(v, str) and v.strip():
            orch_arid = v.strip()
    if orch_arid is None:
        return False
    target = status.strip().lower()
    doc = _read_session_run_index_consistent(repo_root, orchestration_id)
    entries = doc.get("entries")
    if isinstance(entries, list):
        for item in entries:
            if (
                isinstance(item, dict)
                and str(item.get("agent_run_id", "")).strip() == orch_arid
                and str(item.get("status", "")).strip().lower() == target
            ):
                return False
    _append_session_run_index_entry(
        repo_root,
        orchestration_id,
        agent_run_id=orch_arid,
        agent_session_id=orch_arid,
        context_id=orch_arid,
        agent_role="orchestration",
        status=status,
    )
    return True


def _finalize_orchestration_run_row(
    repo_root: Path,
    orchestration_id: str,
    *,
    status: str,
    finished_at: str | None = None,
) -> bool:
    """Terminalize the orchestration's own agent_runs.jsonl row.

    Called on terminal `set-status` so agent_runs-based audits and
    `validate_workspace_root` no longer see the orchestration row stuck `running`
    even after `orchestration_meta.json` reaches `pass`/`fail`/`fail_closed`.
    Rewrites whenever the row's status differs from `status`, so it (a)
    terminalizes a `running` row, (b) follows the permitted `fail -> fail_closed`
    promotion (the row was already rewritten to `fail` by the first set-status),
    and (c) repairs a row left `running` by a half-committed prior finalize on
    replay. Idempotent: a no-op once the row already equals `status`, so
    same-terminal set-status replays do not rewrite it (preserving `finished_at`).
    Only invoked after update_orchestration_status' own transition guard accepts
    the meta status change, so a non-`running`→terminal rewrite here only ever
    reflects the allowed `fail -> fail_closed` case. Returns True iff the row was
    transitioned. See `_reset_orchestration_run_row_to_running` for the resume inverse.
    """
    finished = (
        finished_at if isinstance(finished_at, str) and finished_at.strip() else _utc_now_iso()
    )

    def _mutate(obj: dict[str, Any]) -> None:
        obj["status"] = status
        obj["finished_at"] = finished

    row_changed = _rewrite_orchestration_run_row(
        repo_root,
        orchestration_id,
        should_rewrite=lambda obj: str(obj.get("status", "")).strip() != status,
        apply_mutation=_mutate,
    )
    # Mirror the terminalization into session_run_index.json (idempotent), so
    # session-index-based audits do not see the orchestration row stuck `running`.
    _sync_orchestration_session_index_status(repo_root, orchestration_id, status)
    return row_changed


def _reset_orchestration_run_row_to_running(
    repo_root: Path,
    orchestration_id: str,
) -> bool:
    """Re-open the orchestration's own agent_runs.jsonl row for a resumed run.

    Inverse of `_finalize_orchestration_run_row`, symmetric with
    `resume_orchestration` resetting `orchestration_meta.status` to `running`
    and dropping the cleanup_committed marker. When a terminal orchestration is
    resumed, its agent_runs row (terminalized in place by the prior set-status)
    must also be reset to `running` with `finished_at` cleared. Otherwise the live
    resumed run carries a stale terminal row, which (a) lets
    `validate_workspace_root` treat the live orchestration tmp root as
    cleanup-pending/terminated after the TTL, and (b) leaves the resumed run's
    eventual set-status unable to re-terminalize it (the finalizer only matches
    `running` rows). Targets only a non-`running` row (idempotent). Returns True
    iff the row was reset.
    """

    def _mutate(obj: dict[str, Any]) -> None:
        obj["status"] = "running"
        obj.pop("finished_at", None)

    row_changed = _rewrite_orchestration_run_row(
        repo_root,
        orchestration_id,
        should_rewrite=lambda obj: str(obj.get("status", "")).strip() != "running",
        apply_mutation=_mutate,
    )
    # Symmetric reset of the session_run_index.json orchestration row (idempotent).
    _sync_orchestration_session_index_status(repo_root, orchestration_id, "running")
    return row_changed


def update_orchestration_status(
    repo_root: Path,
    orchestration_id: str,
    *,
    status: str,
    reason_code: str | None = None,
    reason_detail: str | None = None,
    blocking_policy_scope: str | None = None,
) -> dict[str, Any]:
    meta_path = _orchestration_root(repo_root, orchestration_id) / "orchestration_meta.json"
    # Kept before the lock: taking the lock creates the orchestration directory, so a
    # mistyped id would otherwise leave a `workspace/orchestrations/<id>/` holding only the lock.
    if not meta_path.exists():
        raise FileNotFoundError(meta_path)
    # F3: serialize the full read-check-write-cleanup-marker section. Without this
    # lock, two concurrent terminalizers can both observe a non-terminal status,
    # both pass the same-terminal guard below, and race the meta write — the
    # final reason_code/reason_detail becomes nondeterministic.
    with _orchestration_meta_exclusive_lock(repo_root, orchestration_id):
        meta = _read_json(meta_path)
        current_status_pre = meta.get("status")
        is_same_terminal_replay = (
            isinstance(current_status_pre, str)
            and current_status_pre in IDEMPOTENT_TERMINAL_STATUSES
            and status == current_status_pre
        )
        # Codex round 15 F1: status-specific preconditions (pass needs
        # preflight launchability + completion validation) MUST run only for FORWARD
        # transitions. A same-terminal
        # replay reflects a defensive retry after the first call already
        # committed — re-running pass validation can fail on expired preflight
        # or drifted workspace even though the orchestration is already done.
        if not is_same_terminal_replay and status == "pass":
            _require_preflight_launchable(
                repo_root,
                orchestration_id,
                enforce_live_probe=False,
            )
            _validate_orchestration_completion_for_pass(repo_root, orchestration_id)
        current_status = meta.get("status")
        orch_arid_obj = meta.get("orchestration_agent_run_id")
        orch_arid = (
            orch_arid_obj.strip()
            if isinstance(orch_arid_obj, str) and orch_arid_obj.strip()
            else None
        )
        if isinstance(current_status, str) and current_status in IDEMPOTENT_TERMINAL_STATUSES:
            if status == current_status:
                # F2: same-terminal re-invocation is the only built-in recovery
                # path for a half-committed terminalization. Two-phase commit:
                # (a) terminal meta written, (b) tmp cleaned, (c) committed
                # marker. If (b) or (c) failed previously, the cleanup_committed
                # marker is missing — accept the call as a cleanup-retry that
                # touches ONLY the cleanup/marker phase. Narrative fields stay
                # frozen at their first-write values; failure_analysis.json is
                # the canonical place for additional context.
                marker_present = False
                if orch_arid is not None:
                    marker_present = _cleanup_committed_marker_path(
                        repo_root, orchestration_id, orch_arid
                    ).is_file()
                if marker_present:
                    # F1 (Codex round 4): fully-committed same-terminal replay is a
                    # safe no-op. Defensive retries after ambiguous network/IO
                    # failures (the caller may have lost the response even though
                    # the first call succeeded) must not error — the system is
                    # already in the requested state. Narrative updates still
                    # belong in failure_analysis.json (written by the driver on
                    # the dev path, annotated by the operator), but reissuing set-status with the
                    # same status returns the existing meta unchanged.
                    #
                    # Codex round 18 F2: if the canonical `set_status` audit
                    # event for this terminal status is missing (e.g., the
                    # original forward-transition call committed meta + marker
                    # but its log append failed), backfill it from the persisted
                    # meta state before recording the no-op replay. Otherwise
                    # the canonical transition record stays lost forever.
                    if not _phase_state_log_has_set_status(
                        repo_root, orchestration_id, status,
                    ):
                        _append_phase_state_log(
                            repo_root,
                            orchestration_id,
                            {
                                "ts": _utc_now_iso(),
                                "event": "set_status",
                                "to": status,
                                "reason_code": meta.get("reason_code"),
                                "reason_detail": meta.get("reason_detail"),
                                "blocking_policy_scope": meta.get("blocking_policy_scope"),
                                "detected_at": meta.get("detected_at"),
                                "backfilled": True,
                                "backfill_reason": "missing_canonical_set_status_event",
                            },
                        )
                    # Idempotent backfill: terminalize the orchestration's
                    # agent_runs row if a pre-fix run left it `running`.
                    _finalize_orchestration_run_row(
                        repo_root,
                        orchestration_id,
                        status=status,
                        finished_at=meta.get("finished_at"),
                    )
                    _append_phase_state_log(
                        repo_root,
                        orchestration_id,
                        {
                            "ts": _utc_now_iso(),
                            "event": "set_status_noop_replay",
                            "to": status,
                        },
                    )
                    return meta
                # Cleanup retry path: re-run only the destructive cleanup and
                # marker write. Do NOT mutate meta narrative fields.
                # Codex round 18 F2: also backfill the canonical set_status
                # event if it's missing (original call may have failed before
                # logging).
                _finalize_orchestration_run_row(
                    repo_root,
                    orchestration_id,
                    status=status,
                    finished_at=meta.get("finished_at"),
                )
                if orch_arid is not None:
                    cleanup_done = _cleanup_agent_tmp_root(
                        repo_root,
                        orchestration_id,
                        agent_run_id=orch_arid,
                    )
                    if cleanup_done:
                        _write_cleanup_committed_marker(
                            repo_root, orchestration_id, agent_run_id=orch_arid,
                        )
                if not _phase_state_log_has_set_status(
                    repo_root, orchestration_id, status,
                ):
                    _append_phase_state_log(
                        repo_root,
                        orchestration_id,
                        {
                            "ts": _utc_now_iso(),
                            "event": "set_status",
                            "to": status,
                            "reason_code": meta.get("reason_code"),
                            "reason_detail": meta.get("reason_detail"),
                            "blocking_policy_scope": meta.get("blocking_policy_scope"),
                            "detected_at": meta.get("detected_at"),
                            "backfilled": True,
                            "backfill_reason": "missing_canonical_set_status_event",
                        },
                    )
                _append_phase_state_log(
                    repo_root,
                    orchestration_id,
                    {
                        "ts": _utc_now_iso(),
                        "event": "set_status_cleanup_retry",
                        "to": status,
                    },
                )
                return meta
            if not (current_status == "fail" and status == "fail_closed"):
                raise ValueError(
                    "terminal-to-terminal status transition rejected: "
                    f"'{current_status}' -> '{status}'. Only 'fail' -> 'fail_closed' is permitted."
                )
        meta["status"] = status
        if isinstance(reason_code, str) and reason_code.strip():
            meta["reason_code"] = reason_code.strip()
        if isinstance(reason_detail, str) and reason_detail.strip():
            meta["reason_detail"] = reason_detail.strip()
        if isinstance(blocking_policy_scope, str) and blocking_policy_scope.strip():
            meta["blocking_policy_scope"] = blocking_policy_scope.strip()
        if status == "fail_closed":
            meta["detected_at"] = _utc_now_iso()
        if status in TERMINAL_STATUSES:
            meta["finished_at"] = _utc_now_iso()
        if status == "fail_closed":
            meta["finished_at"] = _utc_now_iso()
        # Adv-35: write the durable terminal status FIRST, then cleanup tmp,
        # then write the cleanup_committed marker. Validator's exemption-revoke
        # decision requires BOTH the terminal meta status AND the committed
        # marker (Adv-35 two-phase commit), so a partial failure between meta
        # write and cleanup keeps orch tmp scratch exempt for diagnostics
        # rather than orphaning recovery artifacts.
        _write_json(meta_path, meta)
        if status in TERMINAL_STATUSES or status == "fail_closed":
            # Terminalize the orchestration's own agent_runs.jsonl row in place so
            # agent_runs-based audits no longer see it stuck `running`. Done before
            # tmp cleanup; the append-only duplicate guard is bypassed by rewrite.
            _finalize_orchestration_run_row(
                repo_root,
                orchestration_id,
                status=status,
                finished_at=meta.get("finished_at"),
            )
            if orch_arid is not None:
                cleanup_done = _cleanup_agent_tmp_root(
                    repo_root,
                    orchestration_id,
                    agent_run_id=orch_arid,
                )
                # Adv-36: only publish committed marker on confirmed cleanup.
                if cleanup_done:
                    _write_cleanup_committed_marker(
                        repo_root, orchestration_id, agent_run_id=orch_arid,
                    )
        # Codex round 21 F1: build the audit event from the COMMITTED meta
        # rather than raw call arguments. Raw args bypass the .strip()
        # normalization and the "preserve existing if unchanged" logic above,
        # so the log could record a different reason_code/reason_detail than
        # the persisted state. Reading back from `meta` ensures the audit
        # trail matches the canonical orchestration_meta.json and that the
        # forward write has the same shape as the replay backfill (which
        # also sources from meta).
        _append_phase_state_log(
            repo_root,
            orchestration_id,
            {
                "ts": _utc_now_iso(),
                "event": "set_status",
                "to": meta.get("status"),
                "reason_code": meta.get("reason_code"),
                "reason_detail": meta.get("reason_detail"),
                "blocking_policy_scope": meta.get("blocking_policy_scope"),
                "detected_at": meta.get("detected_at"),
            },
        )
        return meta


def mark_dependency_readiness(
    repo_root: Path,
    orchestration_id: str,
) -> dict[str, Any]:
    """Re-verify `orchestration_meta.dependency_readiness` from real workspace
    artifacts and overwrite ALL detail flags with the freshly computed values.

    Codex round 6 F2 fix: every call performs a full re-verification of all
    three stages. Selective updates would let stale `true` flags survive
    dependency regressions (an older `ir_ref_verified=true` could persist
    even after a newer ir_meta.json went `verification_status=fail`). By
    overwriting all flags from current artifact state, the persisted booleans
    that `_dependency_ready` consumes always reflect the live workspace.

    The runtime resolves every direct dependency in `<spec_ref>/deps.yaml`
    (via `spec/registry/spec_catalog.yaml` + version_constraint matching),
    then asks each stage of `_verify_dep_stage_detail` — since issue #250 PR-2 a
    derivation-key question of the dependency's own chain:

      - ir_ref: the dependency's Compile has an eligible output under its key recomputed now
      - pipeline_ref: its Build has (which requires Generate and Compile to)
      - aggregate_verdict: its Validate has

    A stage flag is True only when EVERY direct dep passes its per-stage check
    (empty deps → trivially true). Top-level flags derived:
      compile_readiness   = ir_ref_verified
      execution_readiness = ir_ref_verified AND pipeline_ref_verified AND aggregate_verdict_verified

    The CLI is NOT a caller assertion — it triggers runtime artifact
    verification. A caller cannot mark deps "verified" unless the workspace
    actually contains passing artifacts.
    """
    meta_path = _orchestration_root(repo_root, orchestration_id) / "orchestration_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(meta_path)
    with _orchestration_meta_exclusive_lock(repo_root, orchestration_id):
        meta = _read_json(meta_path)
        if not isinstance(meta, dict):
            raise ValueError(f"invalid orchestration_meta.json: {meta_path}")
        spec_ref = meta.get("spec_ref")
        # Codex round 17 F1+F2: also persist certified_deps (one canonical
        # version per dep) so downstream consumers see the same version that
        # satisfied readiness.
        verified, certified_deps, fail_reason = _compute_dep_readiness(
            repo_root, spec_ref, target=_target_of_orchestration_meta(repo_root, meta))
        if verified is None:
            # Codex round 8 F2 + round 21 F2: persist fail-closed BEFORE
            # raising and record the SPECIFIC fail_reason so observability
            # tooling can differentiate `deps_yaml_missing_or_unparseable`
            # (file absent / YAML parse failed) from
            # `deps_yaml_malformed_schema` (file parses but schema invalid).
            fail_closed_payload: dict[str, Any] = {
                "direct_dependency_compile_readiness": False,
                "direct_dependency_execution_readiness": False,
                "detail": {
                    "ir_ref_verified": False,
                    "pipeline_ref_verified": False,
                    "aggregate_verdict_verified": False,
                },
            }
            meta["dependency_readiness"] = fail_closed_payload
            _write_json(meta_path, meta)
            _append_phase_state_log(
                repo_root,
                orchestration_id,
                {
                    "ts": _utc_now_iso(),
                    "event": "mark_dependency_readiness_failed",
                    "reason": fail_reason or "deps_yaml_missing_or_unparseable",
                    "spec_ref": spec_ref,
                },
            )
            if fail_reason == "deps_yaml_malformed_schema":
                raise ValueError(
                    "mark-dependency-readiness: deps.yaml schema is malformed "
                    f"for spec_ref={spec_ref!r}. Persisted dependency_readiness "
                    "reset to fail-closed. Inspect dependencies.components / "
                    "dependencies.profiles for unknown keys, missing canonical "
                    "lists, or malformed list items, then re-run "
                    "mark-dependency-readiness."
                )
            if fail_reason == "spec_catalog_corrupt":
                # Codex round 33 F2: surface catalog corruption distinctly
                # so wrappers can route this to a repo-wide outage alert
                # rather than treating it like an ordinary dep miss.
                raise ValueError(
                    "mark-dependency-readiness: spec_catalog.yaml is "
                    "corrupt or missing the canonical schema. Persisted "
                    f"dependency_readiness for spec_ref={spec_ref!r} reset "
                    "to fail-closed. This is a repository-wide outage, "
                    "not a normal dependency miss — fix the catalog file "
                    "(top-level `specs:` list) and re-run."
                )
            raise ValueError(
                "mark-dependency-readiness: cannot verify readiness — "
                f"deps.yaml missing or unparseable for spec_ref={spec_ref!r}. "
                "Persisted dependency_readiness reset to fail-closed. "
                "Populate spec_ref in orchestration_meta and ensure deps.yaml exists, "
                "then re-run mark-dependency-readiness."
            )
        detail = {
            "ir_ref_verified": bool(verified.get("ir_ref_verified", False)),
            "pipeline_ref_verified": bool(verified.get("pipeline_ref_verified", False)),
            "aggregate_verdict_verified": bool(verified.get("aggregate_verdict_verified", False)),
        }
        compile_ok = detail["ir_ref_verified"]
        execution_ok = (
            detail["ir_ref_verified"]
            and detail["pipeline_ref_verified"]
            and detail["aggregate_verdict_verified"]
        )
        new_readiness: dict[str, Any] = {
            "direct_dependency_compile_readiness": compile_ok,
            "direct_dependency_execution_readiness": execution_ok,
            "detail": detail,
            # Codex round 17 F1: persist the per-dep certified version so
            # downstream lineage / launch-request templating uses the SAME
            # version that satisfied readiness, not a separately-resolved
            # "highest matching version".
            "certified_deps": certified_deps,
        }
        meta["dependency_readiness"] = new_readiness
        _write_json(meta_path, meta)
        _append_phase_state_log(
            repo_root,
            orchestration_id,
            {
                "ts": _utc_now_iso(),
                "event": "mark_dependency_readiness",
                "verified": verified,
                "detail": detail,
                "direct_dependency_compile_readiness": compile_ok,
                "direct_dependency_execution_readiness": execution_ok,
            },
        )
        return new_readiness


def reserve_phase_root(
    repo_root: Path,
    *,
    orchestration_id: str,
    node_key: str,
    step: str,
    reserved_id: str,
    reserved_by_agent_run_id: str,
    target_id: str | None = None,
) -> dict[str, Any]:
    """Reserve the ir_id (`step=compile`) or the pipeline_id (`step=generate`) a node's run
    writes under. A pipeline reservation also records the `target_id` its pipeline is built
    for (issue #284), which the conductor passes for a generate one alone: a pipeline id names a
    directory only together with its target (`workspace/pipelines/<safe>/<target_id>/<id>`),
    and the readers that turn a reservation back into a directory (`resume_node_refs`,
    `_revocable_stage_meta_path`, `audit_orchestration`) read it from here."""
    step_key = step.strip().lower()
    _required_child_agent_kind(step_key)
    target_token = target_id.strip() if isinstance(target_id, str) else ""
    reserved_id_clean = reserved_id.strip()
    node_safe = _node_key_to_safe(node_key.strip())
    out = (
        _orchestration_root(repo_root, orchestration_id)
        / "reservations"
        / node_safe
        / f"{step_key}.json"
    )
    payload = {
        "node_key": node_key.strip(),
        "step": step_key,
        "reserved_ir_id": reserved_id_clean,
        "reserved_by_agent_run_id": reserved_by_agent_run_id.strip(),
        "status": "reserved",
        "reserved_at": _utc_now_iso(),
    }
    if step_key == "generate":
        payload["target_id"] = target_token
    _write_json(out, payload)
    return payload


def _json_arg(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("json payload must be object")
    return value


def _json_file_arg(raw: str) -> dict[str, Any]:
    """Read a JSON object payload from a file path instead of an argv element.

    Linux caps a single argv element at MAX_ARG_STRLEN (128 KiB); an inlined launch
    request (IR + controlled spec + dependency facts) exceeds it for larger nodes and
    execve fails with E2BIG before the process starts. The caller writes the payload to
    `workspace/orchestrations/<oid>/launches/<arid>.{request,agent_run}.input.json`
    (kept as evidence) and passes that path here.
    """
    try:
        text = Path(raw).read_text(encoding="utf-8")
    except OSError as exc:
        raise argparse.ArgumentTypeError(f"cannot read json payload file {raw!r}: {exc}") from exc
    value = json.loads(text)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("json payload must be object")
    return value


# Default (terse) result projections for the high-frequency bookkeeping
# subcommands. Each maps a subcommand to the result fields the conductor
# parses downstream. Anything not listed is dropped from the default stdout (the
# default dates from an LLM orchestrator whose context the full payload
# inflated). The full payload
# is still written to its canonical artifact files and recoverable with
# --verbose. Commands absent from this map are emitted unprojected.
_TERSE_RESULT_FIELDS: dict[str, tuple[str, ...]] = {
    "record-launch": (
        "sandbox_profile_ref",
        "launch_prompt_ref",
        # The rendered prompt text the orchestration must pass verbatim to the
        # child leaf (it cannot read the template or the written prompt file).
        "launch_prompt_text",
    ),
    # record_agent_run returns the run record; it carries started_at/finished_at,
    # not a `recorded_at` field.
    "record-agent-run": ("agent_run_id", "status", "started_at", "finished_at"),
    # finalize-child composes deactivate/record-reply/record-agent-run;
    # the orchestration only needs the terminal status + the deactivation confirmation.
    "finalize-child": (
        "agent_run_id",
        "status",
        "deactivated_child_run_id",
        "reply_ref",
        "finalized_at",
    ),
    "deactivate-child": (
        "deactivated_child_run_id",
        "orchestration_id",
        "deactivated_at",
        "already_inactive",
    ),
    "record-reply": ("orchestration_id", "agent_run_id", "reply_ref", "recorded_at"),
    # `step` is a CLI arg, not part of the result payload; the status/executor/
    # failed_substeps fields are what the orchestration consumes.
    "write-step-result": ("status", "executor_agent_run_id", "failed_substeps"),
}

# Fields preserved in a terse result whenever present and non-empty, even if not
# in the per-command list above, so a terse success can never silently hide a
# soft-failure signal carried in the payload.
_TERSE_ALWAYS_KEEP: tuple[str, ...] = ("violations", "error", "errors", "warning", "warnings")


def _project_terse_result(command: str, result: Any) -> Any:
    """Project a bookkeeping subcommand result to its terse field subset.

    Returns ``result`` unchanged when the command has no projection or the
    result is not a dict. Hard failures already exit non-zero via stderr before
    reaching here; this only trims successful-path payloads.
    """
    fields = _TERSE_RESULT_FIELDS.get(command)
    if fields is None or not isinstance(result, dict):
        return result
    # A missing workshop lineage home is not a completed launch: return its sentinel intact
    # so the conductor can rebuild the request cold (the pure codex leaf's
    # `codex_lineage_home_missing` went with it in issue #534 PR-J4-b).
    if command == "record-launch" and result.get("workshop_lineage_home_missing"):
        return result
    projected: dict[str, Any] = {key: result[key] for key in fields if key in result}
    for key in _TERSE_ALWAYS_KEEP:
        if key not in projected and result.get(key):
            projected[key] = result[key]
    return projected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--repo-root", required=True)
    init_parser.add_argument("--orchestration-id", required=True)
    init_parser.add_argument("--spec-ref")
    init_parser.add_argument("--source-dependency-ref")
    init_parser.add_argument("--status", default="running")
    init_parser.add_argument("--agent-backend", default="claude", choices=sorted(SUPPORTED_BACKENDS))
    init_parser.add_argument(
        "--agent-model",
        default=None,
        help="Model id (or unpinned alias, e.g. 'opus') of the orchestration agent itself; recorded on the orchestration agent_runs row for cost attribution / reproducibility. Prefer an alias over a pinned version so it does not go stale.",
    )
    init_parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Drive an EXISTING orchestration again instead of creating one: preserve its "
            "orchestration_agent_run_id and invocation, reset a terminal (or `running`) status "
            "back to `running`, and run the crash reconciliations for whatever the prior driver "
            "left behind. The caller is expected to hold this orchestration's exclusive claim; "
            "that claim, not a recorded driver identity, is what says no one else is driving it."
        ),
    )
    init_parser.add_argument(
        "--invocation-json",
        default=None,
        help=(
            "JSON object recording how this run was invoked (raw command + resolved "
            "params + closure back-link), persisted to orchestration_meta.json#invocation "
            "for reproduction and closure-aware resume. Only used on a cold init; on "
            "--resume the existing block is preserved."
        ),
    )
    init_parser.add_argument(
        "--until-phase",
        default=None,
        help=(
            "On --resume, refresh invocation.until_phase to the phase THIS "
            "resume runs to. A resume may extend the run, and the completion vouch reads "
            "that record to decide which phases must be certified — a stale one would vouch "
            "a four-phase run against one phase. Ignored on a cold init, where the phase "
            "comes from --invocation-json."
        ),
    )
    init_parser.add_argument(
        "--closure-until-phase",
        default=None,
        help=(
            "On --resume of a --with-deps closure node, refresh this "
            "node's invocation.closure_until_phase to the effective closure end-phase, "
            "so an operator phase override persists on the dependency nodes themselves "
            "(recoverable by a later plain --resume even if the target never started)."
        ),
    )
    init_parser.add_argument(
        "--wait-usage-reset",
        action="store_true",
        help=(
            "On --resume, refresh invocation.wait_usage_reset to the "
            "effective (re-passed) value of the opt-in usage-limit wait, so the recorded "
            "provenance matches the behavior of THIS resumed run (the flag is not recovered "
            "from the record — it is re-passed per invocation). Absent on resume records False."
        ),
    )

    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--repo-root", required=True)
    preflight_parser.add_argument("--orchestration-id", required=True)
    preflight_parser.add_argument("--backend", default="claude", choices=sorted(SUPPORTED_BACKENDS))
    preflight_parser.add_argument("--agent-command")
    preflight_parser.add_argument(
        "--llm-config",
        default=None,
        help=(
            "Leaf-LLM configuration file. When given, EVERY distinct provider it can launch is "
            "probed and recorded under preflight.json#providers (in addition to the top-level "
            "fields, which keep describing --backend / defaults)."
        ),
    )
    # The `defaults` the CALLER resolved, so this subprocess probes what the run will launch
    # instead of re-deriving them from the file it reloads. Since the run-wide flag trio was
    # removed nothing overrides the file, so they always equal what it declares — sent
    # unconditionally, because re-applying a declared value is a no-op and nothing then has to
    # track which values came from where.
    preflight_parser.add_argument("--llm-config-defaults-model", default=None)
    preflight_parser.add_argument("--llm-config-defaults-command", default=None)
    # The hash of the snapshot the CALLER loaded. This runs in a subprocess and reloads the
    # file, so an edit in between would certify commands the conductor will never launch —
    # it keeps the object it already has.
    preflight_parser.add_argument("--codex-command", default="codex")
    preflight_parser.add_argument("--claude-command", default="claude")


    _NODE_KEY_HELP = (
        "node_key in '<spec_kind>/<spec_id>@<spec_version>' format "
        "(e.g. 'component/dynamics_shallow_water_flux_2d_rusanov_p0@0.1.0'). "
        "Derived from deps.yaml (spec_kind, spec_id) and controlled_spec.md (spec_version). "
        "NOT a filesystem path."
    )
    _RECORD_LAUNCH_REQUEST_HELP = (
        "JSON object with launch parameters. Required fields: "
        "agent_role ('step'|'substep'), node_key (<spec_kind>/<spec_id>@<spec_version>), "
        "step, substep (for substep agents), orchestration_id, agent_run_id, "
        "parent_agent_run_id, workflow_mode ('dev'|'prod'), "
        "ir_ref (workspace/ir/<node_key_safe>/<ir_id>), "
        "pipeline_ref (workspace/pipelines/<node_key_safe>/<target_id>/<pipeline_id> -- required for ALL "
        "phases including Plan; reserve via reserve-phase-root --step generate if not yet created), "
        "dependency_ref (phase rule: Plan => spec/.../deps.yaml; Generate+ => workspace phase root). "
        "skill_name / skill_ref / skill_must_read_refs are NOT required and must be empty: no leaf "
        "reads a SKILL (issue #171). "
        "For a DETERMINISTIC step/substep launch, one of allowed_output_paths|required_outputs|output_refs "
        "lists the files it produces; the runtime matches each path against the phase contract's outputs "
        "for the reserved tune and promote flows only (issue #643). A PURE launch must leave allowed_output_paths empty -- the host "
        "writes every artifact after the child window closes. allowed_file_tool_paths is accepted and IGNORED: "
        "it named a leaf's Edit/Write grant, and no leaf holds either tool. "
        "ir_id/pipeline_id format: <slug>_<YYYYMMDD>_<seq3> where slug uses hyphens only "
        "(e.g. 'flux-rsn-p0_20260425_001'); an id of another form is never selected by the readers. "
        "Execute step extra-required fields: run_id, "
        "source_id (the source whose <source_id>/src/ run_quality_checks uses; record_launch verifies "
        "verification_status=pass), and source_binary_id (the binary_id whose binary execute uses; "
        "record_launch reads <pipeline_ref>/binary/<source_binary_id>/binary_meta.json and verifies "
        "source_source_id == request.source_id to prevent mixed-build forge). "
        "The cross-phase command log placement (`<source_id>/src/command_log.jsonl`) exists only when "
        "the target profile of the request's pipeline_ref records `toolchain.build_system: make` (in-source builds)."
    )
    _RECORD_LAUNCH_RESPONSE_HELP = (
        "JSON object with child agent response. For Claude Code backend use: "
        '{"agent_run_id": "<uuid>", "agent_session_id": "<same uuid>", '
        '"started_at": "<ISO8601>", "backend": "claude"}. '
        "sandbox_runtime/sandbox_enforced/sandbox_profile_ref are added automatically. "
        "Call record-launch BEFORE launching the child leaf, then overwrite "
        "launches/<child_agent_run_id>.reply.txt with the actual child leaf response."
    )
    _STEP_RESULT_HELP = (
        "JSON object for step_result. Required: status, required_outputs (list[str]), "
        "executor_agent_run_id, substep_agent_run_ids (list[str], empty list allowed for step-only phases), "
        "failed_substeps (list[str], optional), retry_decisions (list[object], optional). "
        "retry_decisions items require: issue_severity, repair_strategy, repair_target_agent_run_id, "
        "new_agent_run_id, repair_reason. "
        "When step in {compile,generate,build,validate} and status is terminal "
        "(pass/fail/blocked/timeout/cancel), validation_stage is required: "
        "compile=>compile|full, generate=>post_generate|full, "
        "build=>post_build|full, validate=>post_execute|pre_judge|full. "
        "For compile/generate pass, required_outputs must be covered by effective substep output_refs. "
        "For status=pass, derivation ({derivation_key, derivation_inputs, transformation}, the "
        "record phase_derivation computed at phase start) is required and is stamped into the "
        "phase's certifying stage meta (issue #250)."
    )

    launch_parser = subparsers.add_parser(
        "record-launch",
        description=(
            "Record a child agent launch: runs live preflight, builds the read-only sandbox "
            "profile for a CLI leaf, and writes launches/<child_id>.* artifacts. Call it "
            "BEFORE launching the child leaf."
        ),
    )
    launch_parser.add_argument("--repo-root", required=True)
    launch_parser.add_argument("--orchestration-id", required=True)
    launch_parser.add_argument("--parent-agent-run-id", required=True,
                               help="UUID of the orchestration (parent) agent.")
    launch_parser.add_argument("--child-agent-run-id", required=True,
                               help="UUID pre-generated for the child agent. "
                                    "For Claude Code this also becomes agent_session_id.")
    launch_request_group = launch_parser.add_mutually_exclusive_group(required=True)
    launch_request_group.add_argument("--request-json", type=_json_arg,
                                      help=_RECORD_LAUNCH_REQUEST_HELP)
    launch_request_group.add_argument("--request-json-file", type=_json_file_arg,
                                      help=("Path to a file holding the same payload the inline "
                                            "request-json form takes. Use this form always: a "
                                            "single argv element is capped at MAX_ARG_STRLEN "
                                            "(128 KiB) and an inlined request exceeds it for "
                                            "larger nodes (execve E2BIG). The conductor writes "
                                            "workspace/orchestrations/<oid>/launches/"
                                            "<child_arid>.request.input.json and keeps it as evidence."))
    launch_parser.add_argument("--response-json", required=True, type=_json_arg,
                               help=_RECORD_LAUNCH_RESPONSE_HELP)
    launch_parser.add_argument("--relation-type", default="launch")
    launch_parser.add_argument(
        "--child-env-from-stdin", action="store_true",
        help=("Read the child leaf's environment (a JSON object of str->str) from stdin "
              "and build the sandbox profile from it. This is the environment the "
              "conductor's `_child_env` AUTHORED for this leaf, so the profile records "
              "and delivers the same dict the leaf actually runs under. On STDIN, not "
              "argv: the values would otherwise be readable in `ps` for every process "
              "on the host."))
    launch_parser.add_argument(
        "--workshop-lineage-id",
        help=("For a workshop leaf's warm resume (issue #534), the lineage whose private "
              "claude home the resumed session lives in (the agent_run_id of the cold launch "
              "that started it). A missing lineage home returns the "
              "`workshop_lineage_home_missing` sentinel before recording a launch; it is "
              "never created here. A cold workshop launch passes none: its lineage is the "
              "child's own id."),
    )
    launch_parser.add_argument(
        "--netns-socket-dir",
        help=("For a CLI leaf (issue #534 step 4), the per-launch directory whose Unix sockets "
              "are the leaf's network namespace's two routes out, served by the conductor "
              "(`tools/leaf_netns.py`): a 0700 directory of this user's, outside the checkout. "
              "Recorded in the profile's `network` block and bound read-only into the sandbox; "
              "a CLI launch without it is refused."),
    )

    run_parser = subparsers.add_parser(
        "record-agent-run",
        description=(
            "Append one agent run record to agent_runs.jsonl. "
            "For step/substep roles also writes agent.result.json and agent.summary.txt."
        ),
    )
    run_parser.add_argument("--repo-root", required=True)
    run_parser.add_argument("--orchestration-id", required=True)
    run_parser.add_argument(
        "--agent-run-json", required=True, type=_json_arg,
        help=(
            "JSON object for the agent run record. "
            "Always required: agent_run_id (UUID), agent_role ('orchestration'|'step'|'substep'), "
            "status ('running'|'pass'|'fail'|...), started_at (ISO8601). "
            "agent_backend names the provider ('claude'|'codex'|an HTTP provider token). "
            "Required for step/substep: agent_session_id (for Claude Code = agent_run_id), "
            "context_id (unique UUID per run), context_isolated (true), node_key "
            "(<spec_kind>/<spec_id>@<spec_version>). "
            "Required when status is a terminal state (pass/fail/blocked/timeout/cancel): "
            "finished_at (ISO8601). "
            "Required on pass: output_refs (list of written artifact paths)."
        ),
    )

    finalize_parser = subparsers.add_parser(
        "finalize-child",
        description=(
            "One-call child finalization: deactivate-child -> record-reply -> "
            "record-agent-run, in that order, reusing each guard. --agent-run-json is the "
            "record-agent-run payload (its agent_run_id is --agent-run-id); "
            "--reply-text is the child's verbatim final message."
        ),
    )
    finalize_parser.add_argument("--repo-root", required=True)
    finalize_parser.add_argument("--orchestration-id", required=True)
    finalize_parser.add_argument("--agent-run-id", required=True)
    finalize_parser.add_argument("--reply-text")
    finalize_parser.add_argument("--reply-from-stdin", action="store_true")
    finalize_agent_run_group = finalize_parser.add_mutually_exclusive_group(required=True)
    finalize_agent_run_group.add_argument("--agent-run-json", type=_json_arg)
    finalize_agent_run_group.add_argument(
        "--agent-run-json-file", type=_json_file_arg,
        help=("Path to a file holding the same payload the inline agent-run-json form takes. "
              "A single argv element is capped at MAX_ARG_STRLEN (128 KiB); the conductor writes "
              "workspace/orchestrations/<oid>/launches/<arid>.agent_run.input.json and keeps it "
              "as evidence. Pair it with the reply-from-stdin flag for the reply text."))

    step_parser = subparsers.add_parser(
        "write-step-result",
        description=(
            "Write step_result.json for one step run and validate required fields, "
            "retry semantics, and required_outputs coverage."
        ),
    )
    step_parser.add_argument("--repo-root", required=True)
    step_parser.add_argument("--orchestration-id", required=True)
    step_parser.add_argument("--node-key", required=True)
    step_parser.add_argument("--step", required=True)
    step_parser.add_argument("--agent-run-id", required=True)
    step_parser.add_argument("--result-json", required=True, type=_json_arg, help=_STEP_RESULT_HELP)

    deactivate_child_parser = subparsers.add_parser("deactivate-child")
    deactivate_child_parser.add_argument("--repo-root", required=True)
    deactivate_child_parser.add_argument("--orchestration-id", required=True)
    deactivate_child_parser.add_argument("--child-run-id", required=True)

    record_reply_parser = subparsers.add_parser("record-reply")
    record_reply_parser.add_argument("--repo-root", required=True)
    record_reply_parser.add_argument("--orchestration-id", required=True)
    record_reply_parser.add_argument("--agent-run-id", required=True)
    record_reply_parser.add_argument("--reply-text")
    record_reply_parser.add_argument("--reply-from-stdin", action="store_true")

    status_parser = subparsers.add_parser("set-status")
    status_parser.add_argument("--repo-root", required=True)
    status_parser.add_argument("--orchestration-id", required=True)
    status_parser.add_argument("--status", required=True)
    status_parser.add_argument("--reason-code")
    status_parser.add_argument("--reason-detail")
    status_parser.add_argument("--blocking-policy-scope")

    mark_dep_parser = subparsers.add_parser(
        "mark-dependency-readiness",
        description=(
            "Re-verify orchestration_meta.dependency_readiness from real "
            "workspace artifacts and OVERWRITE all detail flags with the "
            "computed result. The runtime resolves every direct dep in "
            "<spec_ref>/deps.yaml (via spec/registry/spec_catalog.yaml + "
            "version_constraint matching) and inspects the latest (mtime) "
            "ir_meta.json / binary_meta.json / aggregate_verdict.json. A "
            "flag is True only when every dep passes its per-stage check. "
            "Top-level compile/execution readiness are derived from detail "
            "flags. The CLI takes no per-stage args: every call performs a "
            "full re-verification (selective updates would let stale `true` "
            "flags survive dependency regressions)."
        ),
    )
    mark_dep_parser.add_argument("--repo-root", required=True)
    mark_dep_parser.add_argument("--orchestration-id", required=True)

    launch_check_parser = subparsers.add_parser(
        "workflow-launch-check",
        description=(
            "Pre-phase gate: checks execution platform availability, "
            "dependency readiness, and required child agent kind. "
            "Returns JSON with status ('pass'|'fail_closed') and next_action. "
            "Run once before the first phase; fail_closed must stop the orchestration."
        ),
    )
    launch_check_parser.add_argument("--repo-root", required=True)
    launch_check_parser.add_argument("--orchestration-id", required=True)
    launch_check_parser.add_argument(
        "--node-key", required=True,
        help=_NODE_KEY_HELP,
    )
    launch_check_parser.add_argument("--step", required=True,
                                     help="Workflow step name: compile, generate, build, validate, etc.")
    # Provider tokens, not just spawnable backends: the conductor passes the LAUNCHING leaf's
    # own token, which for an HTTP pure leaf is not a CLI backend at all.
    launch_check_parser.add_argument("--backend", default="claude",
                                     choices=sorted(SUPPORTED_PROVIDER_TOKENS))

    reserve_root_parser = subparsers.add_parser(
        "reserve-phase-root",
        description=(
            "Reserve an ir_id or pipeline_id before the conductor creates the directory. "
            "Writes a reservation marker only; does NOT create workspace/ir/ or "
            "workspace/pipelines/ directories. "
            "Use --step compile to reserve an ir_id; --step generate to reserve a pipeline_id. "
            "Both reservations are typically needed before launching Compile substeps, "
            "because record-launch requires a valid pipeline_ref even for Compile."
        ),
    )
    reserve_root_parser.add_argument("--repo-root", required=True)
    reserve_root_parser.add_argument("--orchestration-id", required=True)
    reserve_root_parser.add_argument(
        "--node-key", required=True,
        help=_NODE_KEY_HELP,
    )
    reserve_root_parser.add_argument("--step", required=True,
                                     help="'compile' to reserve an ir_id; 'generate' to reserve a pipeline_id.")
    reserve_root_parser.add_argument(
        "--reserved-id", required=True,
        help=(
            "The ir_id or pipeline_id to reserve. "
            "Format: <slug>_<YYYYMMDD>_<seq3> where slug is hyphen-separated lowercase alphanumeric "
            "(regex: ^[a-z0-9]+(?:-[a-z0-9]+)*_[0-9]{8}_[0-9]{3}$). "
            "Example: 'flux-rsn-p0_20260425_001'. "
            "Nothing refuses another form, but no reader selects it (an underscore inside the "
            "slug makes it non-canonical; use hyphens)."
        ),
    )
    reserve_root_parser.add_argument("--reserved-by-agent-run-id", required=True,
                                     help="UUID of the agent that will use this reserved ID.")
    reserve_root_parser.add_argument(
        "--target", default=None,
        help=("Target id the reserved pipeline is built for (spec/targets/<id>.yaml). "
              "The conductor passes it with --step generate, where the pipeline lives at "
              "workspace/pipelines/<node_key_safe>/<target_id>/<pipeline_id>; it is not "
              "checked against --step (an omitted one records an empty target_id)."))

    check_phase_certified_parser = subparsers.add_parser(
        "check-phase-certified",
        help=(
            "Is this (node, phase) already certified by the artifacts on disk? Answers the "
            "same way on a cold run and on a resume: the phase's stage meta must record "
            "verification_status=pass, be bound to the current artifact of the phase above "
            "it, and its deliverables must still hash to the recorded artifact_hashes. On "
            "yes, the phase state is recorded as skipped_certified."
        ),
    )
    check_phase_certified_parser.add_argument("--repo-root", required=True)
    check_phase_certified_parser.add_argument("--orchestration-id", required=True)
    check_phase_certified_parser.add_argument("--node-key", required=True)
    check_phase_certified_parser.add_argument(
        "--step", required=True, choices=list(STEP_KEYS_FOR_NODE_STATE))
    check_phase_certified_parser.add_argument(
        "--no-record", action="store_true",
        help=("Answer only: do not record a certified phase as skipped_certified. The "
              "conductor passes it for a phase named in --rederive, which runs although "
              "certified."))
    check_phase_certified_parser.add_argument(
        "--agent-run-id",
        help="Agent run id recorded on the skip_certified phase-state event (the orchestration arid).",
    )
    check_phase_certified_parser.add_argument(
        "--target",
        default=None,
        help=("Target id (spec/targets/<id>.yaml) a generate/build/validate phase is asked "
              "for; default: the orchestration's recorded invocation.target. Loaded whenever "
              "given (an undeclared id exits 1), and not read for --step compile, whose "
              "output is target-free."),
    )

    revoke_artifact_parser = subparsers.add_parser(
        "revoke-artifact",
        help=(
            "Revoke the stage meta of a phase the conductor has decided to re-derive: rewrite "
            "its verification_status to `revoked`, keeping every other key and recording "
            "prior_verification_status / revoked_at / revoked_by_agent_run_id / "
            "revocation_reason. This is the half of a retry that reaches the ARTIFACT — the "
            "phase-state reset beside it is this orchestration's own bookkeeping, which a cold "
            "re-run does not read. Downstream phases need no revocation: each binds to the id "
            "of the phase above it. `noop` when no meta was written; a `noop` over a phase "
            "that is still certified exits 1 (its answer names `certified_by`) unless "
            "--caller-decides-still-certified."
        ),
    )
    revoke_artifact_parser.add_argument("--repo-root", required=True)
    revoke_artifact_parser.add_argument("--orchestration-id", required=True)
    revoke_artifact_parser.add_argument("--node-key", required=True, help=_NODE_KEY_HELP)
    revoke_artifact_parser.add_argument(
        "--step", required=True, choices=list(STEP_KEYS_FOR_NODE_STATE),
        help="The phase whose artifact is being re-derived.",
    )
    revoke_artifact_parser.add_argument(
        "--reason", required=True,
        help="Routing reason for the re-derivation (recorded as revocation_reason).",
    )
    revoke_artifact_parser.add_argument(
        "--trigger-agent-run-id", required=True,
        help="agent_run_id of the attempt whose failure drove the re-derivation.",
    )
    revoke_artifact_parser.add_argument(
        "--last-fail-reason", default=None,
        help="Findings excerpt to record on the meta, overwriting its own.",
    )
    revoke_artifact_parser.add_argument(
        "--severity", default=None,
        help=(
            "G5 severity of the finding that drove the re-derivation, recorded as "
            "revocation_severity. A resumed repair reads it to choose reuse vs restart; "
            "without it every revocation re-enters as `major`."
        ),
    )
    revoke_artifact_parser.add_argument(
        "--repair-strategy", default=None,
        help=(
            "Repair strategy of the decision that drove the re-derivation, recorded as "
            "revocation_repair_strategy. Needed BESIDE --severity: `major` defaults to reuse "
            "but honours an explicit restart, so the grade alone cannot reconstruct it."
        ),
    )
    revoke_artifact_parser.add_argument(
        "--last-fail-reason-from-stdin", action="store_true",
        help=(
            "Read --last-fail-reason from stdin instead. The excerpt can reach several "
            "thousand characters, which does not belong on an argv."
        ),
    )
    revoke_artifact_parser.add_argument(
        "--caller-decides-still-certified", action="store_true",
        help=(
            "Answer a `noop` over a phase that is still certified with exit 0 and the JSON "
            "answer, instead of refusing it. For the conductor, which holds the attempt id "
            "the answer's `certified_by` (within `certified_pipeline_ref`, the pipeline a "
            "source / binary / run id is numbered in) is compared with (a failed --rederive "
            "attempt leaves "
            "the standing output certifying the phase, issue #369) and refuses the rest "
            "itself (`revocation_not_landed`). Not for the manual recipe."
        ),
    )

    reset_phase_parser = subparsers.add_parser(
        "reset-phase",
        help=(
            "Reset --from-phase and every phase downstream of it to `not_started` in "
            "phase_state.json, recording a `phase_reset` event with the routing reason. A "
            "RECORD, not a gate: record-launch has no phase-state precondition. It is what an "
            "operator and the completion vouch read back, so a phase left at "
            "step_result_written while its artifact is revoked would describe a run that is "
            "not happening."
        ),
    )
    reset_phase_parser.add_argument("--repo-root", required=True)
    reset_phase_parser.add_argument("--orchestration-id", required=True)
    reset_phase_parser.add_argument("--node-key", required=True, help=_NODE_KEY_HELP)
    reset_phase_parser.add_argument(
        "--from-phase", required=True, choices=list(STEP_KEYS_FOR_NODE_STATE),
        help="The earliest phase to reset; it and all downstream phases are reset.",
    )
    reset_phase_parser.add_argument(
        "--reason", required=True, help="Routing reason for the reset.")
    reset_phase_parser.add_argument(
        "--trigger-agent-run-id", required=True,
        help="agent_run_id of the attempt whose failure drove the reset.",
    )

    # The bookkeeping subcommands default to a terse result projection (see
    # _project_terse_result), a default dating from an LLM orchestrator whose
    # context the full payload (up to ~50KB for record-agent-run) inflated; the
    # conductor parses only the projected fields. --verbose restores the full JSON
    # for debugging/audit. The flag lives on each terse subparser so it can be
    # appended after the subcommand (e.g. `record-launch ... --verbose`).
    for _terse_parser in (
        launch_parser,
        run_parser,
        step_parser,
        finalize_parser,
        deactivate_child_parser,
        record_reply_parser,
    ):
        _terse_parser.add_argument(
            "--verbose",
            action="store_true",
            help="Emit the full result JSON instead of the default terse projection.",
        )

    args = parser.parse_args(argv)
    repo_root = Path(getattr(args, "repo_root")).resolve()

    if args.command == "init":
        if getattr(args, "resume", False):
            result = resume_orchestration(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                spec_ref=args.spec_ref,
                source_dependency_ref=args.source_dependency_ref,
                closure_until_phase=getattr(args, "closure_until_phase", None),
                until_phase=getattr(args, "until_phase", None),
                wait_usage_reset=bool(getattr(args, "wait_usage_reset", False)),
            )
        else:
            invocation_record: dict[str, Any] | None = None
            invocation_json = getattr(args, "invocation_json", None)
            if isinstance(invocation_json, str) and invocation_json.strip():
                invocation_record = json.loads(invocation_json)
            result = init_orchestration(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                spec_ref=args.spec_ref,
                source_dependency_ref=args.source_dependency_ref,
                status=args.status,
                agent_backend=args.agent_backend,
                agent_model=args.agent_model,
                invocation=invocation_record,
            )
    elif args.command == "preflight":
        agent_command = args.agent_command
        if not isinstance(agent_command, str) or not agent_command.strip():
            if args.backend == "codex":
                # Keep backward compatibility only for codex backend.
                agent_command = args.codex_command
            elif args.backend == "claude":
                agent_command = args.claude_command
        preflight_payload = probe_execution_platform(
            backend=args.backend,
            agent_command=agent_command,
            repo_root=repo_root,
        )
        llm_config_snapshot = None
        if getattr(args, "llm_config", None):
            # ONE snapshot: read the bytes and parse THOSE.
            from tools.llm_config import load_llm_config as _load_llm_config
            _raw = Path(args.llm_config).read_bytes()
            llm_config_snapshot = _load_llm_config(args.llm_config, content=_raw)
        if getattr(args, "llm_config", None):
            # Back-compatible SUPERSET: the top-level fields keep describing `--backend`
            # (i.e. `defaults`), and `providers` adds one entry per distinct provider the
            # configuration can launch — including the one just probed, so the launch check has
            # a single place to look. The configuration's own path and hash are NOT recorded
            # here: `orchestration_meta.json#invocation` already pins both, for the same file
            # and the same orchestration, and that is what the resume gate compares.
            preflight_payload["providers"] = probe_all_providers(
                config=llm_config_snapshot, repo_root=repo_root,
                model_override=getattr(args, "llm_config_defaults_model", "") or "",
                command_override=getattr(args, "llm_config_defaults_command", "") or "")
            # A provider the config names but the host cannot launch must fail the preflight
            # itself, not the substep that first reaches it.
            if not _preflight_allows_agent_launch(preflight_payload):
                preflight_payload["status"] = "fail"
                preflight_payload["can_launch_step_agents"] = False
                preflight_payload["can_launch_substep_agents"] = False
        result = write_preflight(
            repo_root=repo_root,
            orchestration_id=args.orchestration_id,
            payload=preflight_payload,
        )
    elif args.command == "record-launch":
        request_payload = (
            args.request_json if args.request_json is not None else args.request_json_file
        )
        try:
            child_env_payload: dict[str, str] | None = None
            if getattr(args, "child_env_from_stdin", False):
                # The conductor's `_child_env`, serialized by the conductor.
                child_env_payload = json.loads(sys.stdin.read())
            result = record_launch(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                parent_agent_run_id=args.parent_agent_run_id,
                child_agent_run_id=args.child_agent_run_id,
                request_payload=request_payload,
                response_payload=args.response_json,
                relation_type=args.relation_type,
                workshop_lineage_id=args.workshop_lineage_id,
                netns_socket_dir=args.netns_socket_dir,
                child_env=child_env_payload,
            )
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    elif args.command == "record-agent-run":
        try:
            result = record_agent_run(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                payload=args.agent_run_json,
            )
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    elif args.command == "finalize-child":
        agent_run_payload = (
            args.agent_run_json if args.agent_run_json is not None else args.agent_run_json_file
        )
        if args.reply_from_stdin:
            reply_text = sys.stdin.read()
        else:
            reply_text = args.reply_text
        try:
            result = finalize_child(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                agent_run_id=args.agent_run_id,
                reply_text=reply_text,
                agent_run_payload=agent_run_payload,
            )
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    elif args.command == "write-step-result":
        try:
            result = write_step_result(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                node_key=args.node_key,
                step=args.step,
                agent_run_id=args.agent_run_id,
                payload=args.result_json,
            )
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    elif args.command == "deactivate-child":
        try:
            result = deactivate_child_agent(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                child_run_id=args.child_run_id,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    elif args.command == "record-reply":
        if args.reply_from_stdin:
            reply_text = sys.stdin.read()
        else:
            reply_text = args.reply_text
        try:
            result = record_reply_text(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                agent_run_id=args.agent_run_id,
                reply_text=reply_text,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    elif args.command == "check-phase-certified":
        try:
            result = check_phase_certified(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                node_key=args.node_key,
                step=args.step,
                agent_run_id=args.agent_run_id,
                record=not args.no_record,
                target_id=args.target,
            )
        except TargetProfileError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    elif args.command == "revoke-artifact":
        try:
            last_fail_reason = args.last_fail_reason
            if args.last_fail_reason_from_stdin:
                last_fail_reason = sys.stdin.read()
            result = revoke_artifact(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                node_key=args.node_key,
                step=args.step,
                reason=args.reason,
                trigger_agent_run_id=args.trigger_agent_run_id,
                last_fail_reason=last_fail_reason,
                severity=args.severity,
                repair_strategy=args.repair_strategy,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"revoke-artifact: {exc}", file=sys.stderr)
            return 1
        # A `noop` over a phase that is STILL certified is a FAILED revocation, and the CLI is
        # the route where that mattered most: the conductor fails closed on it, but the
        # documented manual recipe (`docs/RUNBOOK.md` §3-1) and any other caller got
        # `{"status": "noop"}` and exit 0 while the phase stayed certified — an operator
        # following the recipe would then `--resume` and watch the phase be skipped. The
        # conductor asks with --caller-decides-still-certified: it decides from the answer
        # (issue #369), and a refusal here reached it as a bare `runtime revoke-artifact
        # failed` that ended the run `conductor_error` before its own reading could run.
        if (result.get("status") == "noop" and result.get("still_certified")
                and not args.caller_decides_still_certified):
            print(
                f"revoke-artifact: resolved no stage meta for {args.node_key}/{args.step} "
                f"({result.get('reason')}), and the phase is still certified (by "
                f"{result.get('certified_by')}) — the "
                "re-derivation decision did not reach the artifact, so a resume would skip "
                "this phase. Check that the pipeline's lineage.json names the stage this "
                "orchestration produced.",
                file=sys.stderr,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
    elif args.command == "reset-phase":
        try:
            result = reset_phase(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
                node_key=args.node_key,
                from_phase=args.from_phase,
                reason=args.reason,
                trigger_agent_run_id=args.trigger_agent_run_id,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"reset-phase: {exc}", file=sys.stderr)
            return 1
    elif args.command == "set-status":
        result = update_orchestration_status(
            repo_root=repo_root,
            orchestration_id=args.orchestration_id,
            status=args.status,
            reason_code=args.reason_code,
            reason_detail=args.reason_detail,
            blocking_policy_scope=args.blocking_policy_scope,
        )
    elif args.command == "mark-dependency-readiness":
        # Codex round 26 F2: convert expected verification failures
        # (malformed/missing deps.yaml, missing orchestration_meta.json) into
        # a clean stderr message + exit code 1 rather than letting a
        # traceback escape the CLI surface — orchestration wrappers treat
        # tracebacks as infrastructure crashes, not dependency rejections.
        try:
            result = mark_dependency_readiness(
                repo_root=repo_root,
                orchestration_id=args.orchestration_id,
            )
        except (ValueError, FileNotFoundError) as exc:
            print(f"mark-dependency-readiness: {exc}", file=sys.stderr)
            return 1
    elif args.command == "workflow-launch-check":
        # Convert expected validation failures (e.g. an unsupported --step like
        # 'plan') into a clean stderr message + exit 1 rather than letting a
        # Python traceback escape — orchestration wrappers classify tracebacks
        # as infrastructure crashes, not request rejections. Mirrors the
        # mark-dependency-readiness dispatch below.
        try:
            result = pre_phase_launch(
                repo_root,
                orchestration_id=args.orchestration_id,
                node_key=args.node_key,
                step=args.step,
                backend=args.backend,
            )
        except ValueError as exc:
            print(f"workflow-launch-check: {exc}", file=sys.stderr)
            return 1
    elif args.command == "reserve-phase-root":
        result = reserve_phase_root(
            repo_root,
            orchestration_id=args.orchestration_id,
            node_key=args.node_key,
            step=args.step,
            reserved_id=args.reserved_id,
            reserved_by_agent_run_id=args.reserved_by_agent_run_id,
            target_id=args.target,
        )

    if not getattr(args, "verbose", False):
        result = _project_terse_result(args.command, result)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
