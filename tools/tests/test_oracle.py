"""The `oracle` of a node (issue #680, PR-1): the declaration (`tools/oracle.py`), the case plan,
the build and validate keys' `oracle` member (`tools/orchestration_runtime.py`), and the
driver's startup verdict over the oracle's libraries (`run_workflow._library_verdict`).

The libraries are the ones `test_external_library.py` builds with the real compiler, installed
under a non-system directory; its fixture is reused.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from tools import external_library as xl
from tools import oracle
from tools.tests.target_fixtures import FORTRAN_CPU, profile_with
from tools.tests.test_external_library import _Fixture, write_sites

NODE = "component/n@0.1.0"
SPEC = "spec/component/n"
ADAPTER = "adapter.f90"
_ADAPTER_TEXT = "! the adapter\n"
_VALID = """\
oracle_version: 1
language: fortran
source: adapter.f90
origin: "repo r1: a.f90 b.f90"
builds:
  - id: default
    libraries: [foo]
  - id: alt
    libraries: [vend]
    cases: [c2]
"""
#: A declared language other than the fixture libraries' (`registry.unsupported_reason`).
OTHER = "cuda_cpp"
OTHER_LANGUAGE = profile_with(toolchain={"language": OTHER})


def _cross(cases: list[str], **scope: Any) -> dict[str, Any]:
    return {"test_id": "t", "quantity": "q", "target_cases": cases,
            "expr": "maxabs(final.h - comparand.final.h)", "op": "le", "value": 1e-12,
            **(scope or {"per_case": True})}


def _ir(*predicates: dict[str, Any], cases=("c1", "c2", "c3", "c4")) -> dict[str, Any]:
    return {"case": {"test_case_set": [{"case_id": c} for c in cases]},
            "io_contract": {"predicates": list(predicates)}}


class _OracleFixture(_Fixture):

    def setUp(self) -> None:
        super().setUp()
        spec = self.repo / SPEC
        if spec.exists():
            shutil.rmtree(spec)
        spec.mkdir(parents=True)
        (spec / "deps.yaml").write_text(
            "dependencies:\n  components: []\n  profiles: []\n")

    def write_oracle(self, text: str = _VALID, spec: str = SPEC,
                     adapter: str | None = _ADAPTER_TEXT) -> Path:
        d = self.repo / spec / oracle.ORACLE_DIR
        d.mkdir(parents=True, exist_ok=True)
        (d / oracle.DECLARATION_FILE).write_text(text)
        if adapter is not None:
            (d / ADAPTER).write_text(adapter)
        return d

    def load(self, spec: str = SPEC, language: str = "fortran") -> oracle.Oracle | None:
        return oracle.load(self.repo, spec, language)


class OracleDeclarationTest(_OracleFixture):

    def test_a_declaration_loads_and_its_hashes_are_the_files_(self) -> None:
        d = self.write_oracle()
        got = self.load()
        assert got is not None
        self.assertEqual(got.declaration_sha256,
                         hashlib.sha256((d / oracle.DECLARATION_FILE).read_bytes()).hexdigest())
        self.assertEqual(got.source_sha256,
                         hashlib.sha256((d / ADAPTER).read_bytes()).hexdigest())
        self.assertEqual((got.source, got.origin), (ADAPTER, "repo r1: a.f90 b.f90"))
        self.assertEqual([(b.build_id, [x.library_id for x in b.libraries], b.cases)
                          for b in got.builds],
                         [("default", ["foo"], None), ("alt", ["vend"], ("c2",))])
        self.assertEqual([x.library_id for x in got.library_declarations()], ["foo", "vend"])

    def test_each_shape_defect_is_refused(self) -> None:
        good = {"oracle_version": 1, "language": "fortran", "source": ADAPTER,
                "builds": [{"id": "default", "libraries": []}]}
        defects: dict[str, Any] = {
            "not a mapping": [1],
            "unknown key": {**good, "extra": 1},
            "missing builds": {k: v for k, v in good.items() if k != "builds"},
            "missing source": {k: v for k, v in good.items() if k != "source"},
            "version": {**good, "oracle_version": 2},
            "bool version": {**good, "oracle_version": True},
            "no language": {**good, "language": None},
            "source absent": {**good, "source": "nope.f90"},
            # A file that exists outside the oracle directory, so only the path rule refuses it.
            "source a path": {**good, "source": "../deps.yaml"},
            "source the declaration": {**good, "source": oracle.DECLARATION_FILE},
            "origin not text": {**good, "origin": 3},
            "builds empty": {**good, "builds": []},
            "build not a mapping": {**good, "builds": ["default"]},
            "build key unknown": {**good, "builds": [{"id": "a", "libraries": [], "x": 1}]},
            # a valid prefix, so a `match` in place of `fullmatch` would take it
            "build id": {**good, "builds": [{"id": "a-1", "libraries": []}]},
            "build id twice": {**good, "builds": [{"id": "a", "libraries": [], "cases": ["c1"]},
                                                  {"id": "a", "libraries": []}]},
            "library undeclared": {**good, "builds": [{"id": "a", "libraries": ["zz"]}]},
            "library twice": {**good, "builds": [{"id": "a", "libraries": ["foo", "foo"]}]},
            "cases empty": {**good, "builds": [{"id": "a", "libraries": [], "cases": []}]},
            "case twice": {**good, "builds": [{"id": "a", "libraries": [],
                                               "cases": ["c1", "c1"]}]},
            "two builds without cases": {**good, "builds": [{"id": "a", "libraries": []},
                                                            {"id": "b", "libraries": []}]},
        }
        for name, doc in defects.items():
            with self.subTest(name):
                self.write_oracle(json.dumps(doc))
                with self.assertRaises(oracle.OracleDeclarationError):
                    self.load()
        with self.subTest("a directory without the declaration"):
            d = self.write_oracle()
            (d / oracle.DECLARATION_FILE).unlink()
            with self.assertRaises(oracle.OracleDeclarationError):
                self.load()
        with self.subTest("the good document loads"):
            self.write_oracle(json.dumps(good))
            self.assertIsNotNone(self.load())

    def test_a_node_without_the_directory_declares_none(self) -> None:
        self.assertIsNone(self.load())

    def test_an_oracle_of_another_language_is_not_read_beyond_its_language(self) -> None:
        self.write_oracle(f"language: {OTHER}\nbuilds: nonsense\n")
        self.assertIsNone(self.load())
        with self.assertRaises(oracle.OracleDeclarationError):
            self.load(language=OTHER)

    def test_a_library_of_another_language_is_refused(self) -> None:
        (self.repo / xl.LIBRARIES_DIR / "cfoo.yaml").write_text(textwrap.dedent("""\
            library_version: 1
            library_id: cfoo
            language: cuda_cpp
            modules: []
            procedures: [cfoo]
            link: [cfoo]
            """))
        self.write_oracle(_VALID.replace("libraries: [vend]", "libraries: [cfoo]"))
        self.assertIsNotNone(xl.load_declaration(self.repo, "cfoo"))   # declared, of another language
        with self.assertRaisesRegex(oracle.OracleDeclarationError, "is for language 'cuda_cpp'"):
            self.load()


class CasePlanTest(_OracleFixture):

    def setUp(self) -> None:
        super().setUp()
        self.write_oracle()
        self.oracle = self.load()
        assert self.oracle is not None

    def test_the_build_without_cases_takes_every_other_compared_case(self) -> None:
        ir = _ir(_cross(["c1", "c2"]), _cross(["c3", "c4"], case="c4"),
                 {**_cross(["c3"]), "expr": "maxabs(final.h)"})
        self.assertEqual(oracle.compared_cases(ir), ["c1", "c2", "c4"])
        self.assertEqual(oracle.case_plan(ir, self.oracle),
                         {"default": ["c1", "c4"], "alt": ["c2"]})
        # An IR without a cross-target predicate compares nothing.
        self.assertEqual(oracle.case_plan(_ir(), self.oracle), {"default": [], "alt": []})

    def test_a_compared_case_no_build_takes_is_refused(self) -> None:
        self.write_oracle(_VALID.replace("libraries: [foo]",
                                         "libraries: [foo]\n    cases: [c1]"))
        declared = self.load()
        assert declared is not None
        with self.assertRaisesRegex(oracle.OracleDeclarationError, "'c3'"):
            oracle.case_plan(_ir(_cross(["c1", "c2", "c3"])), declared)

    def test_an_unknown_or_twice_named_case_is_refused(self) -> None:
        with self.assertRaisesRegex(oracle.OracleDeclarationError, "does not hold"):
            oracle.case_plan(_ir(_cross(["c1"]), cases=("c1",)), self.oracle)
        self.write_oracle(_VALID.replace("libraries: [foo]",
                                         "libraries: [foo]\n    cases: [c2]"))
        declared = self.load()
        assert declared is not None
        with self.assertRaisesRegex(oracle.OracleDeclarationError, "named by builds"):
            oracle.case_plan(_ir(_cross(["c2"])), declared)


class OracleKeyTest(_OracleFixture):
    """The build key's `oracle` member, its comparand form, and the validate key's copy."""

    def resolver(self, target=FORTRAN_CPU, comparand: bool = False):
        from tools.orchestration_runtime import DerivationResolver
        return DerivationResolver(self.repo, target=target, comparand=comparand,
                                  spec_refs={NODE: SPEC})

    def member(self, *, probe: bool = True, target=FORTRAN_CPU, comparand: bool = False):
        from tools.orchestration_runtime import _oracle_member
        return _oracle_member(self.repo, NODE, self.resolver(target, comparand), probe=probe)

    def inputs(self, step: str, target=FORTRAN_CPU) -> dict[str, Any]:
        import tools.orchestration_runtime as ort
        with mock.patch.object(ort, "_derived_closure_graph", return_value={}), \
                mock.patch.object(ort, "_closure_signature", return_value=[]), \
                mock.patch.object(ort, "_spec_file_hash", return_value="t"), \
                mock.patch.object(ort, "_component_direct_deps", return_value=[]), \
                mock.patch.object(ort, "_certified_output_hash", return_value="h"), \
                mock.patch.object(ort, "_pipeline_closure_for_key", return_value=[]), \
                mock.patch.object(ort, "_harness_for_key", return_value="h@1"), \
                mock.patch.object(ort, "_target_toolchain_identity", return_value={}), \
                mock.patch("tools.codegen_bundle.harness_capability_manifest_document_for",
                           return_value={}):
            return ort.phase_derivation_inputs(
                self.repo, node_key=NODE, step=step, spec_ref=SPEC, ir_ref="workspace/i",
                source_ref="workspace/s", binary_ref="workspace/b",
                resolver=self.resolver(target))

    def test_the_build_key_carries_the_member_only_on_a_target_of_its_language(self) -> None:
        self.write_oracle()
        declared = self.load()
        assert declared is not None
        self.assertEqual(self.inputs("build")["oracle"],
                         oracle.member(self.repo, declared, FORTRAN_CPU))
        self.assertNotIn("oracle", self.inputs("build", OTHER_LANGUAGE))
        self.assertIsNone(self.member(target=OTHER_LANGUAGE))

    def test_the_member_moves_with_the_adapter_the_declaration_and_each_library_file(
            self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td) / "lib"
            shutil.copytree(self.dirs["lib"], lib, symlinks=True)
            write_sites(self.repo, {**self.dirs, "lib": lib})
            d = self.write_oracle()
            base = self.member()
            (d / ADAPTER).write_text(_ADAPTER_TEXT + "! changed\n")
            adapter_moved = self.member()
            self.assertNotEqual(adapter_moved["source"], base["source"])
            self.write_oracle(_VALID + "# a comment\n", adapter=_ADAPTER_TEXT)
            declaration_moved = self.member()
            self.assertNotEqual(declaration_moved["declaration"], base["declaration"])
            self.assertEqual(declaration_moved["builds"], base["builds"])
            self.write_oracle(adapter=_ADAPTER_TEXT)
            self.assertEqual(self.member(), base)
            for name in ("libfoo.so.1", "libvend.a"):
                with self.subTest(name):
                    before = self.member()
                    with open(lib / name, "ab") as handle:
                        handle.write(b"\0")
                    self.assertNotEqual(self.member()["builds"], before["builds"])

    def test_the_compile_and_generate_keys_do_not_move_with_the_oracle(self) -> None:
        without = {step: self.inputs(step) for step in ("compile", "generate")}
        self.write_oracle()
        for step, inputs in without.items():
            with self.subTest(step=step):
                self.assertEqual(self.inputs(step), inputs)

    def test_the_validate_key_copies_the_member_from_the_build_stamp(self) -> None:
        import tools.orchestration_runtime as ort
        self.write_oracle()
        probed = self.member()
        binary = self.repo / "workspace/b"
        binary.mkdir(parents=True, exist_ok=True)

        def inputs(stamped: Any) -> dict[str, Any]:
            (binary / "binary_meta.json").write_text(json.dumps(
                {"derivation_inputs": {} if stamped is None else {"oracle": stamped}}))
            return self.inputs("validate")

        self.assertEqual(inputs(probed)["oracle"], probed)
        swapped = json.loads(json.dumps(probed))
        swapped["builds"][0]["libraries"][0]["files"]["soname"]["libfoo.so.1"] = "0" * 64
        self.assertNotEqual(ort._derivation_key("validate", inputs(probed)),
                            ort._derivation_key("validate", inputs(swapped)))
        for broken in (None, {**probed, "source": "0"},
                       {**probed, "builds": probed["builds"][:1]}):
            with self.subTest(broken=broken), \
                    self.assertRaises(ort.DerivationInputsUnresolvable):
                inputs(broken)
        # A node without an oracle copies nothing, whatever the stamp holds.
        shutil.rmtree(self.repo / SPEC / oracle.ORACLE_DIR)
        self.assertNotIn("oracle", inputs(probed))

    def test_a_comparand_resolver_matches_a_build_that_carries_the_member(self) -> None:
        from tools.orchestration_runtime import (
            _comparand_substituted_inputs, _host_probed_leaves, _target_toolchain_identity)
        self.write_oracle()
        probed = self.member()
        declared = self.member(comparand=True)
        self.assertEqual(declared, self.member(probe=False))
        self.assertEqual(declared, oracle.declared_member(self.load()))
        toolchain = _target_toolchain_identity(FORTRAN_CPU, probe=False)
        leaves = {leaf: "v" for leaf in _host_probed_leaves(FORTRAN_CPU)}
        inputs = {"source": "s", "toolchain": toolchain, "oracle": declared}

        def substituted(stamped: Any) -> dict[str, Any] | None:
            recorded: dict[str, Any] = {"toolchain": {**toolchain, **leaves}}
            if stamped is not None:
                recorded["oracle"] = stamped
            return _comparand_substituted_inputs({"derivation_inputs": recorded}, inputs,
                                                 FORTRAN_CPU)

        out = substituted(probed)
        assert out is not None
        self.assertEqual(out["oracle"], probed)
        other_library = json.loads(json.dumps(probed))
        other_library["builds"][1]["libraries"][0]["library_id"] = "zz"
        for broken in (None, {**probed, "declaration": "0"},
                       {**probed, "builds": probed["builds"][::-1]}, other_library):
            with self.subTest(broken=broken):
                self.assertIsNone(substituted(broken))

    def test_a_key_without_an_oracle_carries_no_member(self) -> None:
        self.assertIsNone(self.member())
        self.assertNotIn("oracle", self.inputs("build"))

    def test_a_malformed_declaration_is_unresolvable(self) -> None:
        from tools.orchestration_runtime import DerivationInputsUnresolvable
        self.write_oracle("language: fortran\n")
        with self.assertRaisesRegex(DerivationInputsUnresolvable, "oracle of"):
            self.member()


class OracleVerdictTest(_OracleFixture):
    """The driver's decision before any phase (`run_workflow._library_verdict`)."""

    def verdict(self, spec: str = SPEC, ordered: list[str] = (), until: str = "Validate",
                target=FORTRAN_CPU) -> dict[str, Any] | None:
        from tools import run_workflow
        with mock.patch.object(run_workflow, "_resolve_dependency_closure",
                               return_value=([{"spec_ref": r} for r in ordered], None)):
            return run_workflow._library_verdict(self.repo, spec, target, until)

    def test_an_oracle_library_the_site_does_not_list_ends_library_unavailable(self) -> None:
        self.write_oracle()
        self.assertIsNone(self.verdict())
        write_sites(self.repo, self.dirs, libraries="      vend:\n")
        self.assertIsNone(self.verdict(until="Generate"))
        self.assertEqual(self.verdict(until="Build")["status"], "library_unavailable")
        verdict = self.verdict()
        assert verdict is not None
        self.assertEqual((verdict["status"], verdict["reason"], verdict["library_id"],
                          verdict["oracle_of"]),
                         ("library_unavailable", "library_not_at_site", "foo", SPEC))

    def _member_with_oracle(self) -> str:
        dep = "spec/component/dep"
        (self.repo / dep).mkdir(parents=True, exist_ok=True)
        (self.repo / dep / "deps.yaml").write_text(
            "dependencies:\n  components: []\n  profiles: []\n")
        self.write_oracle(spec=dep)
        write_sites(self.repo, self.dirs, libraries="      vend:\n")
        return dep

    def test_a_member_unavailable_by_its_oracle_makes_its_consumer_unavailable_under_with_deps(
            self) -> None:
        """The two verdicts `_run_with_dependency_closure` asks: the member's own, and the
        target's over a closure holding it."""
        dep = self._member_with_oracle()
        for spec, ordered in ((dep, []), (SPEC, [dep])):
            with self.subTest(spec=spec):
                verdict = self.verdict(spec, ordered)
                assert verdict is not None
                self.assertEqual((verdict["status"], verdict["oracle_of"]),
                                 ("library_unavailable", dep))

    def test_a_consumer_without_with_deps_is_unavailable_by_its_dependency_s_oracle(
            self) -> None:
        dep = self._member_with_oracle()
        verdict = self.verdict(SPEC, [dep])
        assert verdict is not None
        self.assertEqual((verdict["status"], verdict["library_id"], verdict["oracle_of"]),
                         ("library_unavailable", "foo", dep))
        # A library the closure declares itself is the closure's, not the oracle's.
        (self.repo / SPEC / "deps.yaml").write_text(
            "dependencies:\n  components: []\n  profiles: []\n  libraries:\n"
            "    - {library_id: foo}\n")
        verdict = self.verdict(SPEC, [dep])
        assert verdict is not None
        self.assertNotIn("oracle_of", verdict)

    def test_an_oracle_at_a_remote_site_is_refused_whatever_it_links(self) -> None:
        """`oracle_at_remote_site` names the oracle's node — the node's own, or a closure
        member's — whether the oracle links a library or none."""
        repo_root = Path(__file__).resolve().parents[2]
        targets = self.repo / "spec/targets"
        targets.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / "spec/targets/fortran_cpu.yaml", targets)
        try:
            (self.repo / "sites.yaml").write_text(textwrap.dedent("""\
                sites_version: 1
                sites:
                  box:
                    host: box
                    workdir: /w
                    executes: [cpu]
                    scheduler: none
                targets:
                  fortran_cpu: box
                """))
            self.assertIsNone(self.verdict())
            for name, text in (("no library", _VALID.split("builds:")[0]
                                + "builds:\n  - id: default\n    libraries: []\n"),
                               ("a library", _VALID)):
                with self.subTest(name):
                    self.write_oracle(text)
                    verdict = self.verdict()
                    assert verdict is not None
                    self.assertEqual((verdict["status"], verdict["reason"],
                                      verdict["oracle_of"]),
                                     ("fail", oracle.REMOTE_SITE, SPEC))
            shutil.rmtree(self.repo / SPEC / oracle.ORACLE_DIR)
            dep = "spec/component/dep"
            (self.repo / dep).mkdir(parents=True, exist_ok=True)
            self.write_oracle(spec=dep)
            verdict = self.verdict(SPEC, [dep])
            assert verdict is not None
            self.assertEqual((verdict["reason"], verdict["oracle_of"]),
                             (oracle.REMOTE_SITE, dep))
            # A closure member's oracle is read by a Generate run too: its members are driven
            # to Validate, and their Build keys carry it.
            verdict = self.verdict(SPEC, [dep], until="Generate")
            assert verdict is not None
            self.assertEqual((verdict["reason"], verdict["oracle_of"]),
                             (oracle.REMOTE_SITE, dep))
            self.assertIsNone(self.verdict(SPEC, [dep], until="Compile"))
        finally:
            shutil.rmtree(targets)

    def test_the_host_tools_are_required_for_an_oracle_s_library_alone(self) -> None:
        from tools import host_prerequisites
        self.write_oracle()
        real_which = shutil.which
        with mock.patch.object(host_prerequisites.shutil, "which",
                               lambda name: None if name == "objdump" else real_which(name)), \
                mock.patch("tools.backends.compiler.gfortran.library_binary._run",
                           side_effect=FileNotFoundError("objdump")):
            verdict = self.verdict()
        assert verdict is not None
        self.assertEqual((verdict["status"], verdict["reason"], verdict["missing"]),
                         ("fail", "missing_required_host_tools", ["objdump"]))

    def test_an_oracle_of_another_language_than_the_target_s_is_not_read(self) -> None:
        self.write_oracle(_VALID.replace("language: fortran", f"language: {OTHER}"))
        write_sites(self.repo, self.dirs, libraries="      vend:\n")
        self.assertIsNone(self.verdict())
        self.write_oracle(f"language: {OTHER}\nbuilds: nonsense\n")
        self.assertIsNone(self.verdict())

    def test_a_malformed_oracle_declaration_is_read_only_by_a_run_that_reaches_build(
            self) -> None:
        self.write_oracle("language: fortran\nbuilds: nonsense\n")
        for until in ("Compile", "Generate"):
            with self.subTest(until=until):
                self.assertIsNone(self.verdict(until=until))
        for until in ("Build", "Validate"):
            with self.subTest(until=until):
                verdict = self.verdict(until=until)
                assert verdict is not None
                self.assertEqual((verdict["status"], verdict["reason"], verdict["oracle_of"]),
                                 ("fail", oracle.DECLARATION_INVALID, SPEC))

    def test_a_closure_member_s_malformed_declaration_names_the_member(self) -> None:
        """Read by every run past Compile (the member is driven to Validate), and the event
        names the member's directory, not the run's node."""
        dep = "spec/component/dep"
        (self.repo / dep).mkdir(parents=True, exist_ok=True)
        self.write_oracle("language: fortran\nbuilds: nonsense\n", spec=dep)
        self.assertIsNone(self.verdict(SPEC, [dep], until="Compile"))
        for until in ("Generate", "Validate"):
            with self.subTest(until=until):
                verdict = self.verdict(SPEC, [dep], until=until)
                assert verdict is not None
                self.assertEqual((verdict["reason"], verdict["spec_ref"], verdict["oracle_of"]),
                                 (oracle.DECLARATION_INVALID, SPEC, dep))


if __name__ == "__main__":
    unittest.main()
