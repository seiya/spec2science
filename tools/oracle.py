"""The `oracle` of a node (issue #680): existing code a person declares for one node, for its
generated variant to be compared with (`docs/SPEC.md` requirement 17, `docs/GLOSSARY.md`
§oracle).

The declaration is Project data a person writes, beside the node's three files:

    <spec_ref>/oracle/oracle.yaml
    <spec_ref>/oracle/<adapter source>

It names the language whose targets build the oracle, the adapter source (the person-written
file that publishes the node's §5.1 surface and calls the existing code), and one or more
`builds`, each with the external libraries (`tools/external_library.py`) it links and,
optionally, the compared cases it runs. A node with no `oracle/` directory declares none.

This module loads and shape-checks the declaration, plans which build runs which compared case,
and computes the build key's `oracle` member. It holds `language` as an opaque token: it names
no file extension, asks the registry nothing and imports no backend. No gate reads the adapter:
no leaf writes it (`AGENTS.md` §Development premises).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tools import external_library as xl

ORACLE_DECLARATION_VERSION = 1
#: The directory under a node's spec directory that holds the declaration and the adapter.
ORACLE_DIR = "oracle"
DECLARATION_FILE = "oracle.yaml"
_KEYS = frozenset({"oracle_version", "language", "source", "origin", "builds"})
_REQUIRED = frozenset({"oracle_version", "language", "source", "builds"})
_BUILD_KEYS = frozenset({"id", "libraries", "cases"})
_BUILD_ID = re.compile(r"[a-z0-9_]+")

#: The startup refusal of a declaration that does not have the closed shape.
DECLARATION_INVALID = "oracle_declaration_invalid"
#: The startup refusal of an applying oracle at a remote site, whatever it links: the host builds
#: and runs an oracle at the local site only, as it does an external library.
REMOTE_SITE = "oracle_at_remote_site"


class OracleDeclarationError(ValueError):
    """A declaration (or the case plan over it) that does not have the closed shape."""


@dataclass(frozen=True)
class OracleBuild:
    build_id: str
    libraries: tuple[xl.LibraryDeclaration, ...]
    #: The cases this build runs, as declared; None for the one build that takes every other
    #: compared case.
    cases: tuple[str, ...] | None


@dataclass(frozen=True)
class Oracle:
    spec_ref: str
    language: str
    #: The adapter's file name, in the oracle directory.
    source: str
    origin: str | None
    builds: tuple[OracleBuild, ...]
    #: sha256 of `oracle.yaml`'s bytes and of the adapter's.
    declaration_sha256: str
    source_sha256: str

    def library_declarations(self) -> list[xl.LibraryDeclaration]:
        """Every library a build links, in declared order, each once."""
        seen: dict[str, xl.LibraryDeclaration] = {}
        for build in self.builds:
            for declaration in build.libraries:
                seen.setdefault(declaration.library_id, declaration)
        return list(seen.values())


def oracle_dir(repo_root: str | Path, spec_ref: str) -> Path:
    return Path(repo_root) / spec_ref / ORACLE_DIR


def _distinct_strings(value: Any, what: str, *, nonempty: bool) -> tuple[str, ...]:
    if (not isinstance(value, list) or (nonempty and not value)
            or not all(isinstance(v, str) and v for v in value)
            or len(set(value)) != len(value)):
        raise OracleDeclarationError(
            f"{what} must be a list of distinct non-empty strings"
            + (" with at least one entry" if nonempty else "") + f", got {value!r}")
    return tuple(value)


def load(repo_root: str | Path, spec_ref: str, language: str) -> Oracle | None:
    """The oracle `spec_ref` declares for targets of `language`; None when the node has no
    `oracle/` directory, or declares its oracle for another language (whose declaration is
    then not read beyond its `language`). `OracleDeclarationError` when the declaration does
    not have the closed shape: `oracle_version`, a string `language`, `source` naming a file
    in the oracle directory, an optional string `origin`, and a non-empty `builds` list of
    `{id, libraries, cases?}` — distinct `[a-z0-9_]+` ids, libraries declared under
    `spec/libraries/` for the oracle's language, and at most one build without `cases`."""
    directory = oracle_dir(repo_root, spec_ref)
    if not directory.exists():
        return None
    path = directory / DECLARATION_FILE
    try:
        raw = path.read_bytes()
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise OracleDeclarationError(f"{path}: cannot be read ({exc})") from exc
    try:
        if not isinstance(doc, dict):
            raise OracleDeclarationError("the document must be a mapping")
        declared_language = doc.get("language")
        if not isinstance(declared_language, str) or not declared_language:
            raise OracleDeclarationError(
                f"language must be a non-empty string, got {declared_language!r}")
        if declared_language != language:
            return None
        unknown = sorted(set(doc) - _KEYS, key=str)
        missing = sorted(_REQUIRED - set(doc))
        if unknown or missing:
            raise OracleDeclarationError(
                f"keys must be {sorted(_REQUIRED)} and optionally 'origin' (unknown {unknown}, "
                f"missing {missing})")
        version = doc["oracle_version"]
        if isinstance(version, bool) or version != ORACLE_DECLARATION_VERSION:
            raise OracleDeclarationError(
                f"oracle_version must be {ORACLE_DECLARATION_VERSION}, got {version!r}")
        source = doc["source"]
        if (not isinstance(source, str) or "/" in source or source == DECLARATION_FILE
                or not (directory / source).is_file()):
            raise OracleDeclarationError(
                f"source must name a file in {directory} other than {DECLARATION_FILE}, "
                f"got {source!r}")
        origin = doc.get("origin")
        if origin is not None and not isinstance(origin, str):
            raise OracleDeclarationError(f"origin must be a string, got {origin!r}")
        builds = _builds(repo_root, doc["builds"], language)
        return Oracle(
            spec_ref=spec_ref, language=language, source=source, origin=origin,
            builds=builds, declaration_sha256=hashlib.sha256(raw).hexdigest(),
            source_sha256=xl._file_sha256(directory / source))
    except OracleDeclarationError as exc:
        raise OracleDeclarationError(f"{path}: {exc}") from None


def _builds(repo_root: str | Path, value: Any, language: str) -> tuple[OracleBuild, ...]:
    if not isinstance(value, list) or not value:
        raise OracleDeclarationError(f"builds must be a non-empty list, got {value!r}")
    builds: list[OracleBuild] = []
    for index, entry in enumerate(value):
        where = f"builds[{index}]"
        if not isinstance(entry, dict) or not {"id", "libraries"} <= set(entry) \
                or set(entry) - _BUILD_KEYS:
            raise OracleDeclarationError(
                f"{where} must be a mapping of 'id', 'libraries' and optionally 'cases', got "
                f"{entry!r}")
        build_id = entry["id"]
        if not isinstance(build_id, str) or not _BUILD_ID.fullmatch(build_id):
            raise OracleDeclarationError(f"{where}.id must match [a-z0-9_]+, got {build_id!r}")
        if any(b.build_id == build_id for b in builds):
            raise OracleDeclarationError(f"{where}.id {build_id!r} is declared twice")
        declarations: list[xl.LibraryDeclaration] = []
        for library_id in _distinct_strings(entry["libraries"], f"{where}.libraries",
                                            nonempty=False):
            try:
                declaration = xl.load_declaration(repo_root, library_id)
            except xl.LibraryDeclarationError as exc:
                raise OracleDeclarationError(f"{where}.libraries: {exc}") from None
            if declaration.language != language:
                raise OracleDeclarationError(
                    f"{where}.libraries: library {library_id!r} is for language "
                    f"{declaration.language!r}, and the oracle is for {language!r}")
            declarations.append(declaration)
        cases = (_distinct_strings(entry["cases"], f"{where}.cases", nonempty=True)
                 if "cases" in entry else None)
        builds.append(OracleBuild(build_id=build_id, libraries=tuple(declarations),
                                  cases=cases))
    if sum(b.cases is None for b in builds) > 1:
        raise OracleDeclarationError("at most one build may omit 'cases'")
    return tuple(builds)


def compared_cases(ir: dict[str, Any]) -> list[str]:
    """The cases the IR's cross-target predicates compare a comparand in, in the order of the
    IR's `test_case_set`: the scope of every cross-target predicate (`per_case` over its
    `target_cases`, or its one `case`). A predicate whose scope does not parse contributes
    none: the Compile gate refuses it, and the comparison reports it."""
    from tools.primary_evidence import (
        PrimaryEvidenceError,
        _cases_by_id,
        _predicate_scope,
        cross_target_predicates,
    )
    scoped: set[str] = set()
    for index, pred in enumerate(cross_target_predicates(ir)):
        try:
            scoped.update(_predicate_scope(pred, f"predicates[{index}]")[1])
        except (PrimaryEvidenceError, KeyError, TypeError, AttributeError):
            continue
    return [case_id for case_id in _cases_by_id(ir) if case_id in scoped]


def case_plan(ir: dict[str, Any], oracle: Oracle) -> dict[str, list[str]]:
    """`{build_id: [case_id, ...]}` over every build, in declared order: each compared case
    (`compared_cases`) goes to the build that names it, else to the one build without `cases`.
    `OracleDeclarationError` for a named case the IR does not hold, a case named twice, and a
    compared case no build takes."""
    from tools.primary_evidence import _cases_by_id
    known = set(_cases_by_id(ir))
    owner: dict[str, str] = {}
    for build in oracle.builds:
        for case_id in build.cases or ():
            if case_id not in known:
                raise OracleDeclarationError(
                    f"build {build.build_id!r} names case {case_id!r}, which the IR does not "
                    f"hold")
            if case_id in owner:
                raise OracleDeclarationError(
                    f"case {case_id!r} is named by builds {owner[case_id]!r} and "
                    f"{build.build_id!r}")
            owner[case_id] = build.build_id
    default = next((b.build_id for b in oracle.builds if b.cases is None), None)
    plan: dict[str, list[str]] = {b.build_id: [] for b in oracle.builds}
    for case_id in compared_cases(ir):
        build_id = owner.get(case_id, default)
        if build_id is None:
            raise OracleDeclarationError(
                f"compared case {case_id!r} is taken by no build (every build names its "
                f"cases)")
        plan[build_id].append(case_id)
    return plan


def declared_member(oracle: Oracle) -> dict[str, Any]:
    """The build key's `oracle` member without the site's files: the declaration's and the
    adapter's hashes, and per build its id and its libraries' ids and declaration hashes. What
    a key computed without probing the host holds."""
    return {"declaration": oracle.declaration_sha256, "source": oracle.source_sha256,
            "builds": [{"id": b.build_id, "libraries": xl.declared_identity(b.libraries)}
                       for b in oracle.builds]}


def member(repo_root: str | Path, oracle: Oracle, target: Any) -> dict[str, Any]:
    """The build key's `oracle` member: `declared_member` with each build's libraries located
    at the site `target` runs at and hashed (`external_library.identity`). Raises what
    `external_library.resolve_closure` raises."""
    return {**declared_member(oracle),
            "builds": [{"id": b.build_id,
                        "libraries": xl.identity(xl.resolve_closure(repo_root, target,
                                                                    b.libraries))}
                       for b in oracle.builds]}
