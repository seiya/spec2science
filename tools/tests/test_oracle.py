"""The `oracle` of a node (issue #680): the declaration (`tools/oracle.py`), the case plan,
the build and validate keys' `oracle` member (`tools/orchestration_runtime.py`), the driver's
startup verdict over the oracle's libraries (`run_workflow._library_verdict`) (PR-1), and the
oracle's builds in `Build` (`Conductor._build_oracle`, PR-2).

The libraries are the ones `test_external_library.py` builds with the real compiler, installed
under a non-system directory; its fixture is reused.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
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
        # The `use` the workflow puts it to: a Build certified under issue #680 PR-1's member
        # (none) is not one that built the oracle.
        self.assertEqual(self.inputs("build")["oracle"]["use"], oracle.ORACLE_USE_VERSION)
        self.assertEqual(oracle.ORACLE_USE_VERSION, 2)
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
        # A stamp of a Build certified before Build built the oracle carries no `use`
        # (issue #680 PR-1's member): not copied, so the node's Validate re-derives.
        pr1_stamp = {k: v for k, v in probed.items() if k != "use"}
        for broken in (None, {**probed, "source": "0"},
                       {**probed, "builds": probed["builds"][:1]}, pr1_stamp):
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
        pr1_stamp = {k: v for k, v in probed.items() if k != "use"}
        for broken in (None, {**probed, "declaration": "0"},
                       {**probed, "builds": probed["builds"][::-1]}, other_library,
                       pr1_stamp, {**probed, "use": oracle.ORACLE_USE_VERSION - 1}):
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


#: The node the build rows drive: spec id `spec_x`, so the model is `spec_x_model.f90` and the
#: binary `spec_x_runner`.
_BUILD_SPEC = "spec/component/spec_x"
_BUILD_NODE = "component/spec_x@0.1.0"
#: A control file that honours the variables Build hands it (`execute.build_overrides`,
#: `library_overrides`) and compiles the model and the driver.
_MAKEFILE = """\
OBJDIR ?= build
BINDIR ?= bin
BIN ?= spec_x_runner
LIBINC ?=
LIBS ?=
all: $(BINDIR)/$(BIN)
$(BINDIR)/$(BIN): spec_x_model.f90 spec_x_runner.f90
\tmkdir -p $(OBJDIR) $(BINDIR)
\tgfortran $(LIBINC) -J$(OBJDIR) -c spec_x_model.f90 -o $(OBJDIR)/spec_x_model.o
\tgfortran $(LIBINC) -J$(OBJDIR) -c spec_x_runner.f90 -o $(OBJDIR)/spec_x_runner.o
\tgfortran $(OBJDIR)/spec_x_model.o $(OBJDIR)/spec_x_runner.o $(LIBS) -o $@
"""
_DRIVER = "program p\n  use m_model, only: m_run\n  call m_run()\nend program p\n"
#: A generated model that reaches no library.
_MODEL = """\
module m_model
  implicit none
contains
  subroutine m_run()
    print '(a)', 'model'
  end subroutine m_run
end module m_model
"""
#: An adapter that reaches `foo` alone.
_ADAPTER_FOO = """\
module m_model
  implicit none
contains
  subroutine m_run()
    real(8), external :: foo_add
    print '(f8.2)', foo_add(1d0)
  end subroutine m_run
end module m_model
"""
#: An adapter that reaches both libraries.
_ADAPTER_BOTH = """\
module m_model
  use vend, only: vend_scale
  implicit none
contains
  subroutine m_run()
    real(8) :: a(1)
    real(8), external :: foo_add
    a = [1d0]
    call vend_scale(a, 3d0)
    print '(2f8.2)', a, foo_add(1d0)
  end subroutine m_run
end module m_model
"""


class OracleBuildTest(_OracleFixture):
    """`Build` builds the oracle (`Conductor._build_oracle`): each declared build from the
    adapter in the model source's place, with the closure's libraries and its own; the certified
    and checked builds with the closure's alone. Real `make` and `gfortran`; the post_build
    validator subprocess alone is stubbed."""

    def node(self, *, oracle_text: str | None, adapter: str = _ADAPTER_FOO,
             model: str = _MODEL) -> tuple[Path, Any, Any]:
        """A scratch repository holding the fixture libraries' declarations, `sites.yaml`, the
        node's generated `src/` and (unless `oracle_text` is None) its oracle."""
        import tools.workflow_conductor as wc
        from tools.tests.llm_samples import sample_config_with
        from tools.tests.test_external_library import write_declarations
        from tools.tests.test_workflow_conductor import _TargetedConductor
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        repo = Path(td.name)
        write_declarations(repo)
        write_sites(repo, self.dirs)
        spec = repo / _BUILD_SPEC
        spec.mkdir(parents=True)
        (spec / "deps.yaml").write_text(
            "dependencies:\n  components: []\n  profiles: []\n")
        if oracle_text is not None:
            d = spec / oracle.ORACLE_DIR
            d.mkdir()
            (d / oracle.DECLARATION_FILE).write_text(oracle_text)
            (d / ADAPTER).write_text(adapter)
        c = _TargetedConductor(repo_root=repo, orchestration_id="t",
                               orchestration_agent_run_id="x",
                               llm_config=sample_config_with("claude"), env={},
                               target_profile=FORTRAN_CPU)
        refs = wc.NodeRefs(target_id=FORTRAN_CPU.target_id, node_key=_BUILD_NODE,
                           spec_path=_BUILD_SPEC, ir_id="x_1", pipeline_id="x_1",
                           source_id="src_1", binary_id="bin_1")
        (repo / refs.ir_ref).mkdir(parents=True, exist_ok=True)
        src = repo / refs.source_dir() / "src"
        src.mkdir(parents=True)
        (src / "Makefile").write_text(_MAKEFILE)
        (src / "spec_x_model.f90").write_text(model)
        (src / "spec_x_runner.f90").write_text(_DRIVER)
        return repo, c, refs

    def build(self, c: Any, refs: Any, *, closure: tuple[str, ...] = (),
              record: list[dict] | None = None) -> Any:
        """`_run_deterministic_substep` on Build, with the closure's libraries `closure`
        (resolved at the fixture site) and every `tool_compile_project` call recorded."""
        import tools.workflow_conductor as wc
        from tools.tests.test_workflow_conductor import _build_runtime
        build_runtime = _build_runtime()
        real_compile = build_runtime.tool_compile_project
        calls = record if record is not None else []
        staged = self.staged = []
        resolved = xl.resolve_closure(c.repo_root, FORTRAN_CPU,
                                      [xl.load_declaration(c.repo_root, i) for i in closure])
        real_run = subprocess.run

        def run_but_the_post_build_validator(argv, *a, **k):
            if any("validate_pipeline_semantics" in str(w) for w in argv):
                return subprocess.CompletedProcess(argv, 0, "", "")
            return real_run(argv, *a, **k)

        def compile_and_record(args):
            calls.append(args)
            return real_compile(args)

        with mock.patch.object(build_runtime, "tool_compile_project", compile_and_record), \
                mock.patch.object(wc.Conductor, "_dependency_closure_nodes",
                                  lambda self_, refs_: []), \
                mock.patch.object(wc.Conductor, "_stage_dependency_sources",
                                  lambda self_, refs_, obj_dir, *, phase:
                                  staged.append((Path(obj_dir), phase)) or []), \
                mock.patch.object(wc.Conductor, "_closure_libraries",
                                  lambda self_, refs_: resolved), \
                mock.patch.object(subprocess, "run", run_but_the_post_build_validator):
            return c._run_deterministic_substep(refs, "build", None, "child-1", {})

    def meta(self, repo: Path, refs: Any) -> dict[str, Any]:
        return json.loads((repo / refs.binary_dir() / "binary_meta.json").read_text())

    def output(self, binary: Path) -> str:
        env = {**os.environ, "LD_LIBRARY_PATH": str(self.dirs["lib"])}
        return subprocess.run([str(binary)], capture_output=True, text=True, env=env,
                              check=True).stdout.strip()

    @staticmethod
    def libs(call: dict) -> list[str]:
        return [a for a in call["extra_args"] if a.startswith(("LIBINC=", "LIBS="))]

    def test_each_declared_build_is_built_from_the_adapter_with_its_own_libraries(
            self) -> None:
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\norigin: o\n"
                "builds:\n  - id: default\n    libraries: [foo]\n"
                "  - id: alt\n    libraries: [foo, vend]\n    cases: [c2]\n")
        repo, c, refs = self.node(oracle_text=text)
        calls: list[dict] = []
        result = self.build(c, refs, record=calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.meta(repo, refs)
        self.assertEqual(meta["verification_status"], "pass", meta.get("failure_excerpt"))
        bdir = repo / refs.binary_dir()
        # The certified binary is the generated model's; each oracle binary the adapter's.
        self.assertEqual(self.output(bdir / "bin" / "spec_x_runner"), "model")
        for build_id in ("default", "alt"):
            self.assertEqual(
                self.output(bdir / "bin_oracle" / build_id / "spec_x_runner"), "3.00")
        declared = oracle.load(repo, _BUILD_SPEC, "fortran")
        assert declared is not None
        record = meta["oracle"]
        self.assertEqual(
            (record["declaration_sha256"], record["source_sha256"], record["origin"]),
            (declared.declaration_sha256, declared.source_sha256, "o"))
        self.assertEqual([b["id"] for b in record["builds"]], ["default", "alt"])
        oracle_calls = [k for k in calls if "/oracle/" in k["project_dir"]]
        self.assertEqual(len(oracle_calls), 2)
        for build, call, ids in zip(record["builds"], oracle_calls,
                                    (("foo",), ("foo", "vend"))):
            own = self.resolved(ids)
            self.assertEqual(build["binary_ref"],
                             f"binary/bin_1/bin_oracle/{build['id']}/spec_x_runner")
            self.assertEqual(build["libraries"], xl.identity(own))
            self.assertIsNotNone(build["command_id"])
            inc, link = xl.make_values("gfortran", own)
            self.assertEqual(self.libs(call), [f"LIBINC={inc}", f"LIBS={link}"])
            # The node's log, as every other Build command's, not one under workspace/tmp/.
            self.assertEqual(call["command_log_path"],
                             str(repo / refs.source_dir() / "src" / "command_log.jsonl"))
            # The adapter took the model's name; the driver and the control file are the
            # certified ones; nothing else of the oracle directory was copied.
            build_src = Path(call["project_dir"])
            # The closure is staged into the build's own object directory, the one its
            # overrides name.
            overrides = dict(a.split("=", 1) for a in call["extra_args"])
            self.assertIn((Path(overrides["OBJDIR"]), "build"), self.staged)
            self.assertEqual(Path(overrides["OBJDIR"]), build_src.parent / "build")
            self.assertEqual((build_src / "spec_x_model.f90").read_text(), _ADAPTER_FOO)
            self.assertEqual(sorted(p.name for p in build_src.iterdir()),
                             ["Makefile", "spec_x_model.f90", "spec_x_runner.f90"])
        logged = [json.loads(line) for line in
                  (repo / refs.source_dir() / "src" / "command_log.jsonl").read_text()
                  .splitlines()]
        self.assertTrue({b["command_id"] for b in record["builds"]}
                        <= {e.get("command_id") for e in logged})

    def test_a_closure_library_and_an_oracle_library_are_both_on_the_oracle_s_link_line(
            self) -> None:
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: [vend]\n")
        repo, c, refs = self.node(oracle_text=text, adapter=_ADAPTER_BOTH)
        calls: list[dict] = []
        result = self.build(c, refs, closure=("foo",), record=calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.meta(repo, refs)["verification_status"], "pass")
        (call,) = [k for k in calls if "/oracle/" in k["project_dir"]]
        inc, link = xl.make_values("gfortran", self.resolved(("foo", "vend")))
        # ONE pair, over the closure's then the build's.
        self.assertEqual(self.libs(call), [f"LIBINC={inc}", f"LIBS={link}"])
        binary = repo / refs.binary_dir() / "bin_oracle" / "default" / "spec_x_runner"
        self.assertEqual(self.output(binary).split(), ["3.00", "3.00"])
        # The record's libraries are the build's own (the key member's), not the closure's.
        build = self.meta(repo, refs)["oracle"]["builds"][0]
        self.assertEqual(build["libraries"], xl.identity(self.resolved(("vend",))))

    def test_the_certified_and_checked_builds_are_not_handed_the_oracle_s_libraries(
            self) -> None:
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: [vend]\n")
        repo, c, refs = self.node(oracle_text=text, adapter=_ADAPTER_BOTH)
        calls: list[dict] = []
        result = self.build(c, refs, closure=("foo",), record=calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        src = str(repo / refs.source_dir() / "src")
        certified = [k for k in calls if k["project_dir"] == src]
        # The certified build and the checked one (the fixture target's compiler declares
        # checked flags).
        self.assertEqual(len(certified), 2)
        inc, link = xl.make_values("gfortran", self.resolved(("foo",)))
        for call in certified:
            self.assertEqual(self.libs(call), [f"LIBINC={inc}", f"LIBS={link}"])

    def test_a_generated_model_that_uses_an_oracle_library_module_fails_the_certified_build(
            self) -> None:
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: [vend]\n")
        model = _ADAPTER_BOTH.replace("    real(8), external :: foo_add\n", "").replace(
            "a, foo_add(1d0)", "a, 0d0")
        repo, c, refs = self.node(oracle_text=text, adapter=_ADAPTER_BOTH, model=model)
        calls: list[dict] = []
        result = self.build(c, refs, closure=("foo",), record=calls)
        # A content failure, routed as today (Generate); the oracle is not built.
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.meta(repo, refs)
        self.assertEqual(meta["verification_status"], "fail")
        self.assertIn("vend", meta["failure_excerpt"])
        self.assertIsNone(meta["oracle"])
        self.assertFalse([k for k in calls if "/oracle/" in k["project_dir"]])

    def test_an_oracle_library_installed_beside_a_closure_library_still_fails_the_certified_link(
            self) -> None:
        """`vend` (the closure's) and `foo` (the oracle's) sit in one directory, which the
        certified build's link words search: an external call of the oracle's procedure still
        does not link, because the build is handed no link word naming it."""
        self.assertEqual(self.dirs["lib"], Path(self.resolved(("vend",))[0].lib_dir))
        self.assertEqual(self.dirs["lib"], Path(self.resolved(("foo",))[0].lib_dir))
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: [foo]\n")
        repo, c, refs = self.node(oracle_text=text, model=_ADAPTER_FOO)
        result = self.build(c, refs, closure=("vend",))
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.meta(repo, refs)
        self.assertEqual(meta["verification_status"], "fail")
        self.assertIn("foo_add", meta["failure_excerpt"])
        self.assertIsNone(meta["oracle"])

    def test_an_oracle_build_failure_is_fail_closed_and_no_generate_retry(self) -> None:
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: [foo]\n")
        repo, c, refs = self.node(oracle_text=text, adapter="this is not fortran\n")
        result = self.build(c, refs)
        # A transport fail_closed (a non-zero rc out of the deterministic body), not a content
        # failure recorded for Generate to repair.
        self.assertEqual(result.returncode, 1)
        self.assertIn("deterministic_build_error: oracle_build_failed: oracle build 'default'",
                      result.stderr)
        self.assertIn("adapter.f90", result.stderr)
        self.assertFalse((repo / refs.binary_dir() / "binary_meta.json").exists())

    def test_an_oracle_build_that_leaves_no_binary_is_fail_closed_whatever_an_earlier_left(
            self) -> None:
        """A build that reports success and leaves no binary at the imposed path fails as one
        that exited non-zero, and a binary an earlier attempt left there does not stand in."""
        from tools.tests.test_workflow_conductor import _build_runtime
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: []\n")
        repo, c, refs = self.node(oracle_text=text)
        stale = repo / refs.binary_dir() / "bin_oracle" / "default" / "spec_x_runner"
        stale.parent.mkdir(parents=True)
        stale.write_text("an earlier attempt's\n")
        build_runtime = _build_runtime()
        real_compile = build_runtime.tool_compile_project

        def no_oracle_binary(args):
            if "/oracle/" in args["project_dir"]:
                return {"ok": True, "return_code": 0, "command_id": "c", "stderr": ""}
            return real_compile(args)

        with mock.patch.object(build_runtime, "tool_compile_project", no_oracle_binary):
            result = self.build(c, refs)
        self.assertEqual(result.returncode, 1)
        self.assertIn("oracle_build_failed: oracle build 'default'", result.stderr)
        self.assertIn("left no binary at bin_oracle/default/spec_x_runner", result.stderr)
        self.assertFalse(stale.exists())

    def test_an_oracle_build_that_exits_non_zero_is_fail_closed_though_it_left_a_binary(
            self) -> None:
        """A make that fails after linking (a later target) leaves a binary; the exit status,
        not the file, decides."""
        from tools.tests.test_workflow_conductor import _build_runtime
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: [foo]\n")
        repo, c, refs = self.node(oracle_text=text)
        build_runtime = _build_runtime()
        real_compile = build_runtime.tool_compile_project

        def fails_after_linking(args):
            result = real_compile(args)
            if "/oracle/" in args["project_dir"]:
                return {**result, "ok": False, "return_code": 2}
            return result

        with mock.patch.object(build_runtime, "tool_compile_project", fails_after_linking):
            result = self.build(c, refs)
        self.assertEqual(result.returncode, 1)
        self.assertIn("oracle_build_failed: oracle build 'default'", result.stderr)
        self.assertIn("exited 2", result.stderr)
        # The build did leave its binary: the exit status alone refused it.
        self.assertTrue(
            (repo / refs.binary_dir() / "bin_oracle" / "default" / "spec_x_runner").is_file())

    def test_an_oracle_at_a_remote_site_is_refused_before_anything_is_staged(self) -> None:
        import tools.workflow_conductor as wc
        from tools.execution_sites import Site
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: []\n")
        _repo, c, refs = self.node(oracle_text=text)
        remote = mock.Mock(spec=Site, is_local=False, site_id="far")
        staged: list[Any] = []
        with mock.patch.object(wc.Conductor, "site", new_callable=mock.PropertyMock,
                               return_value=remote), \
                mock.patch.object(wc.Conductor, "_stage_dependency_sources",
                                  lambda *a, **k: staged.append(a) or []):
            result = c._run_deterministic_substep(refs, "build", None, "child-1", {})
        self.assertEqual(result.returncode, 1)
        self.assertIn(oracle.REMOTE_SITE, result.stderr)
        self.assertEqual(staged, [])

    def test_a_node_without_an_oracle_records_none(self) -> None:
        repo, c, refs = self.node(oracle_text=None)
        calls: list[dict] = []
        result = self.build(c, refs, record=calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.meta(repo, refs)
        self.assertEqual(meta["verification_status"], "pass")
        self.assertIsNone(meta["oracle"])
        self.assertFalse((repo / refs.binary_dir() / "bin_oracle").exists())
        self.assertEqual(len(calls), 2)


class OracleRunPhaseTest(_OracleFixture):
    """The two places a Build deliverable is declared — the launch `run_substep` records and
    the step result `run_phase` writes, whose `required_outputs` the stamp hashes — each carry
    every oracle binary, driven through the real `run_phase` with the runtime's I/O stubbed."""

    def run_build(self, oracle_text: str | None) -> tuple[list[str], list[str], Any]:
        import tools.workflow_conductor as wc
        from tools.tests.llm_samples import sample_config_with
        from tools.tests.test_workflow_conductor import _TargetedConductor
        recorded: list[dict] = []
        results: list[dict] = []

        class _Stub(_TargetedConductor):
            def emit(self, *_a, **_k):
                return None

            def check_phase_certified(self, *_a, **_k):
                return {"certified": False}

            def workflow_launch_check(self, *_a, **_k):
                return None

            def _ensure_fresh_producer_id(self, *_a, **_k):
                return None

            def _write_lineage(self, *_a, **_k):
                return ()

            def _phase_derivation(self, refs, phase):
                return {"derivation_key": "sha256:" + "0" * 64,
                        "derivation_inputs": {}, "transformation": ["stub-1"]}

            def new_agent_run_id(self):
                return "ar_build"

            def record_launch(self, child_arid, request, entry):
                recorded.append(request)

            def _launch_instant(self, *_a, **_k):
                return 0.0

            def _run_deterministic_substep(self, *_a, **_k):
                return wc.ProcResult(returncode=0, stdout="", stderr="")

            def _persist_leaf_output(self, *_a, **_k):
                return None

            def determine_substep_status(self, refs, phase, substep, paths, **_k):
                return "pass", list(paths)

            def finalize_child(self, *_a, **_k):
                return None

            def _agent_run_json(self, *_a, **_k):
                return {}

            def write_step_result(self, node_key, step, executor, result):
                results.append(result)

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            spec = repo / _BUILD_SPEC
            spec.mkdir(parents=True)
            if oracle_text is not None:
                (spec / oracle.ORACLE_DIR).mkdir()
                (spec / oracle.ORACLE_DIR / oracle.DECLARATION_FILE).write_text(oracle_text)
                (spec / oracle.ORACLE_DIR / ADAPTER).write_text(_ADAPTER_FOO)
            stub = _Stub(repo_root=repo, orchestration_id="t", orchestration_agent_run_id="x",
                         llm_config=sample_config_with("claude"), env={},
                         target_profile=FORTRAN_CPU)
            refs = wc.NodeRefs(target_id=FORTRAN_CPU.target_id, node_key=_BUILD_NODE,
                               spec_path=_BUILD_SPEC, ir_id="x_1", pipeline_id="x_1",
                               source_id="src_1", binary_id="bin_1", run_id="r1",
                               source_binary_id="bin_1")
            outcome = stub.run_phase(refs, "build")
        self.assertEqual(outcome.status, "pass")
        (request,), (result,) = recorded, results
        return request["allowed_output_paths"], result["required_outputs"], refs

    def test_run_phase_build_passes_with_the_oracle_binaries_hashed_and_recorded(self) -> None:
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: []\n"
                "  - id: alt\n    libraries: []\n    cases: [c2]\n")
        allowed, required, refs = self.run_build(text)
        expected = [f"{refs.binary_dir()}/bin_oracle/{b}/spec_x_runner"
                    for b in ("default", "alt")]
        for listed in (allowed, required):
            self.assertEqual([p for p in listed if "/bin_oracle/" in p], expected)

    def test_run_phase_build_of_a_node_without_an_oracle_lists_what_it_listed(self) -> None:
        import tools.workflow_conductor as wc
        allowed, required, refs = self.run_build(None)
        exe = "spec_x_runner"
        bdir = refs.binary_dir()
        self.assertEqual(allowed, [f"{bdir}/bin/{exe}", f"{bdir}/bin_checked/{exe}",
                                   f"{bdir}/binary_meta.json", f"{bdir}/command_log.jsonl"])
        self.assertEqual(required, wc.phase_required_outputs(
            refs, "build", exe_name=exe, checked_build=True))
        self.assertEqual(required, [f"{bdir}/bin/{exe}", f"{bdir}/bin_checked/{exe}",
                                    f"{bdir}/binary_meta.json",
                                    f"{refs.source_dir()}/src/command_log.jsonl"])


if __name__ == "__main__":
    unittest.main()
