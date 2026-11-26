"""The `oracle` of a node (issue #680): the declaration (`tools/oracle.py`), the case plan,
the build and validate keys' `oracle` member (`tools/orchestration_runtime.py`), the driver's
startup verdict over the oracle's libraries (`run_workflow._library_verdict`) (PR-1), the
oracle's builds in `Build` (`Conductor._build_oracle`, PR-2), and the run and comparison in
`Validate.execute` (`Conductor._compare_with_oracle`, PR-3).

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
from tools.tests.target_fixtures import FORTRAN_CPU, SAMPLE_ROOT, SCALE_FIXTURE_ROOT, profile_with
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

    def test_a_continuation_case_is_no_compared_case(self) -> None:
        """Issue #709 F-1: a case that carries a `restart` key is left out, and a build that
        names one (`alt` names c2) is accepted and is given nothing."""
        ir = _ir(_cross(["c1", "c2", "c3"]))
        ir["case"]["test_case_set"][1]["restart"] = {"from": "c1", "step": 2}
        self.assertEqual(oracle.compared_cases(ir), ["c1", "c3"])
        self.assertEqual(oracle.case_plan(ir, self.oracle),
                         {"default": ["c1", "c3"], "alt": []})
        # A malformed `restart` is still no base case: the first pass never runs it.
        ir["case"]["test_case_set"][1]["restart"] = "c1"
        self.assertEqual(oracle.compared_cases(ir), ["c1", "c3"])

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
                           autospec=True, return_value={}):
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
        # (none) is not one that built the oracle, and a Validate certified under PR-2's (2) is
        # not one that compared it.
        self.assertEqual(self.inputs("build")["oracle"]["use"], oracle.ORACLE_USE_VERSION)
        self.assertEqual(oracle.ORACLE_USE_VERSION, 3)
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
        targets = self.repo / "spec/targets"
        targets.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SAMPLE_ROOT / "spec/targets/fortran_cpu.yaml", targets)
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
    and checked builds with the closure's alone. Real `make` and `gfortran`; stubbed: the
    post_build validator subprocess, the dependency closure (none, and its staging recorded
    rather than done) and the closure's libraries (the row's `closure`)."""

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
        self.assertTrue(result.stderr.startswith("deterministic_build_error: "))
        self.assertIn("oracle_build_failed: oracle build 'default'", result.stderr)
        self.assertIn("adapter.f90", result.stderr)
        # The run's failure summary keeps the stderr's last 400 characters: the verdict and
        # its remedy are there, after the compiler's tail.
        import tools.workflow_conductor as wc
        summary = wc.Conductor._leaf_failure_summary(result)
        self.assertIn("oracle_build_failed: oracle build 'default'", summary)
        self.assertIn("--resume", summary)
        self.assertGreater(len(result.stderr), 400)
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

    def test_an_oracle_build_that_timed_out_says_so(self) -> None:
        from tools.tests.test_workflow_conductor import _build_runtime
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: [foo]\n")
        _repo, c, refs = self.node(oracle_text=text)
        build_runtime = _build_runtime()
        real_compile = build_runtime.tool_compile_project

        def times_out(args):
            if "/oracle/" in args["project_dir"]:
                return {"ok": False, "return_code": None, "stderr": "",
                        "error": "timeout: exceeded 3600 sec"}
            return real_compile(args)

        with mock.patch.object(build_runtime, "tool_compile_project", times_out):
            result = self.build(c, refs)
        self.assertEqual(result.returncode, 1)
        self.assertIn("oracle build 'default' of component/spec_x@0.1.0 did not finish "
                      "(timeout: exceeded 3600 sec)", result.stderr)

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

    def test_an_oracle_of_another_language_than_the_target_s_is_not_built(self) -> None:
        """The fortran target builds no oracle a node declares for another language: no oracle
        compile, no record, and no oracle binary among Build's deliverables."""
        text = (f"oracle_version: 1\nlanguage: {OTHER}\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: []\n")
        repo, c, refs = self.node(oracle_text=text)
        self.assertEqual(c._oracle_build_ids(refs), ())
        calls: list[dict] = []
        result = self.build(c, refs, record=calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.meta(repo, refs)
        self.assertEqual(meta["verification_status"], "pass")
        self.assertIsNone(meta["oracle"])
        self.assertFalse([k for k in calls if "/oracle/" in k["project_dir"]])
        self.assertFalse((repo / refs.binary_dir() / "bin_oracle").exists())
        # ... and the other way round: a fortran oracle on a target of the other language.
        from tools.tests.llm_samples import sample_config_with
        from tools.tests.test_workflow_conductor import _TargetedConductor
        repo2, c2, refs = self.node(oracle_text=text.replace(OTHER, "fortran"))
        other = _TargetedConductor(repo_root=repo2, orchestration_id="t",
                                   orchestration_agent_run_id="x",
                                   llm_config=sample_config_with("claude"), env={},
                                   target_profile=OTHER_LANGUAGE)
        self.assertEqual(other.target.toolchain["language"], OTHER)
        self.assertEqual(c2._oracle_build_ids(refs), ("default",))
        self.assertEqual(other._oracle_build_ids(refs), ())

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

    def launch_execute(self, oracle_text: str | None) -> tuple[list[str], Any]:
        """The launch `run_substep` records for validate.execute, through the real
        `run_substep` with the body and the runtime's I/O stubbed."""
        import tools.workflow_conductor as wc
        from tools.tests.llm_samples import sample_config_with
        from tools.tests.test_workflow_conductor import _TargetedConductor
        recorded: list[dict] = []

        class _Stub(_TargetedConductor):
            def emit(self, *_a, **_k):
                return None

            def new_agent_run_id(self):
                return "ar_execute"

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
            (repo / refs.ir_ref).mkdir(parents=True)
            (repo / refs.ir_ref / "spec.ir.yaml").write_text(_EXEC_IR + _EXEC_CROSS)
            outcome = stub.run_substep(refs, "validate", "execute")
        self.assertEqual(outcome.status, "pass")
        (request,) = recorded
        return request["allowed_output_paths"], refs

    def test_the_execute_launch_lists_the_record_and_validate_requires_it_not(self) -> None:
        import tools.workflow_conductor as wc
        with_oracle, refs = self.launch_execute(_EXEC_ORACLE)
        without, _refs = self.launch_execute(None)
        record = f"{refs.run_node_dir()}/{oracle.CHECK_RECORD}"
        self.assertEqual([p for p in with_oracle if p not in without], [record])
        self.assertEqual([p for p in without if p not in with_oracle], [])
        other, _refs = self.launch_execute(
            _EXEC_ORACLE.replace("language: fortran", f"language: {OTHER}"))
        self.assertEqual(other, without)
        # A record, as quality_check.json is: no certified artifact hashes it.
        self.assertNotIn(record, wc.phase_required_outputs(refs, "validate"))


#: Two cases, one `pass` test whose own predicate reads `final.v` and whose cross-target
#: predicate compares it with a comparand's, in both cases.
_EXEC_IR = """\
impl_defaults:
  toolchain: {language: fortran, build_system: make}
  target: {class: cpu}
case:
  test_case_set:
    - case_id: c_alpha
    - case_id: c_beta
io_contract:
  raw_requirements:
    required_evidence:
      - artifact: state_snapshots
        required: true
        schema:
          variables:
            - {name: v, shape_expr: scalar}
          time_variable: t
          time_shape_expr: scalar
  tests:
    - test_id: t_alpha
      expected_outcome: pass
      target_cases: [c_alpha, c_beta]
      quantities: [own, cross]
  predicates:
    - {test_id: t_alpha, quantity: own, target_cases: [c_alpha, c_beta], per_case: true,
       expr: final.v, op: le, value: 1.0e-12}
"""
_EXEC_CROSS = """\
    - {test_id: t_alpha, quantity: cross, target_cases: [c_alpha, c_beta], per_case: true,
       expr: "maxabs(final.v - comparand.final.v)", op: le, value: 1.0e-12}
"""
#: The default build takes c_alpha, the `alt` build names c_beta.
_EXEC_ORACLE = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\norigin: o\n"
                "builds:\n  - id: default\n    libraries: []\n"
                "  - id: alt\n    libraries: []\n    cases: [c_beta]\n")


def _exec_ir(cases: tuple[str, ...], cross: bool = True,
             outcome: str = "pass") -> str:
    """`_EXEC_IR` (and `_EXEC_CROSS`) over `cases` instead of the two."""
    listed = "[" + ", ".join(cases) + "]"
    text = (_EXEC_IR + (_EXEC_CROSS if cross else "")).replace("[c_alpha, c_beta]", listed)
    text = text.replace("    - case_id: c_alpha\n    - case_id: c_beta\n",
                        "".join(f"    - case_id: {c}\n" for c in cases))
    return text.replace("expected_outcome: pass", f"expected_outcome: {outcome}")


def _write_captures(run_dir: Path, cases: list[str], value: float) -> None:
    sdir = run_dir / "raw" / "state_snapshots"
    (sdir / "initial").mkdir(parents=True, exist_ok=True)
    for case_id in cases:
        (sdir / "initial" / f"{case_id}.json").write_text(json.dumps({"v": 0.0, "t": 0.0}))
        (sdir / f"{case_id}.json").write_text(json.dumps({"v": value, "t": 1.0}))


class OracleExecuteTest(unittest.TestCase):
    """`Validate.execute` runs the oracle and compares it (`Conductor._compare_with_oracle`,
    issue #680 PR-3): the real `_execute_inproc` and the real predicates, with the library's
    two run tools and the post_execute gate subprocess stubbed, `_ir_unevaluable` answering
    False, and `_evaluate_execute_verdict` wrapped to count its calls. The stubbed run writes each
    case's captures; an oracle run (`bin_oracle/` in its argv) writes the values the row asks
    for, per build."""

    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.repo = Path(td.name)

    def execute(self, *, oracle_text: str | None = _EXEC_ORACLE, ir: str | None = None,
                run_value: float = 0.0, qc_value: float | None = None, oracle_value: dict[str, float] | None = None,
                oracle_rc: int = 0, oracle_skips: tuple[str, ...] = (),
                oracle_broken: tuple[str, ...] = (),
                cases: tuple[str, ...] = ("c_alpha", "c_beta"),
                binary_meta: dict[str, Any] | None = None, ir_hash: str | None = None,
                repo: Path | None = None) -> tuple[Any, dict[str, Any]]:
        """`_run_deterministic_substep` on validate.execute; returns the result and what ran:
        `oracle_runs` (each oracle run's argument dict) and `evaluations` (how many times the
        verdict was evaluated)."""
        import tools.workflow_conductor as wc
        from tools.tests.test_workflow_conductor import (
            _build_runtime,
            _cfg,
            _record_build_site,
            _TargetedConductor,
        )
        repo = repo or self.repo
        ir = ir if ir is not None else _EXEC_IR + _EXEC_CROSS
        values = oracle_value or {}
        qc_value = run_value if qc_value is None else qc_value
        c = _TargetedConductor(repo_root=repo, orchestration_id="t",
                               orchestration_agent_run_id="x", llm_config=_cfg("claude"),
                               env={})
        refs = self.refs()
        (repo / refs.ir_ref).mkdir(parents=True, exist_ok=True)
        (repo / refs.ir_ref / "spec.ir.yaml").write_text(ir, encoding="utf-8")
        (repo / refs.source_dir() / "src").mkdir(parents=True, exist_ok=True)
        spec = repo / refs.spec_path
        spec.mkdir(parents=True, exist_ok=True)
        if oracle_text is not None:
            (spec / oracle.ORACLE_DIR).mkdir(exist_ok=True)
            (spec / oracle.ORACLE_DIR / oracle.DECLARATION_FILE).write_text(oracle_text)
            (spec / oracle.ORACLE_DIR / ADAPTER).write_text(_ADAPTER_TEXT)
        seen: dict[str, Any] = {"oracle_runs": [], "evaluations": 0}
        build_runtime = _build_runtime()

        def fake_run_program(args):
            argv = args["command"]
            project = Path(args["project_dir"])
            threads = (args.get("env") or {}).get("OMP_NUM_THREADS")
            if threads is not None:
                (project / "perf.json").write_text(json.dumps(
                    {"parallelism": {"threads_per_rank": int(threads)}}))
            if "/bin_oracle/" in argv[0]:
                seen["oracle_runs"].append(args)
                build_id = Path(argv[0]).parent.name
                if oracle_rc:
                    return {"ok": False, "return_code": oracle_rc, "command_id": "O",
                            "stderr": "x" * 600 + "\nadapter aborted"}
                ran = [c_ for c_ in argv[3:] if c_ not in oracle_skips]
                _write_captures(project, ran, values.get(build_id, 0.0))
                for case_id in set(ran) & set(oracle_broken):
                    (project / "raw" / "state_snapshots" / f"{case_id}.json").write_text("{")
                return {"ok": True, "command_id": f"O-{build_id}"}
            _write_captures(project, list(cases), run_value)
            return {"ok": True, "command_id": "R"}

        def fake_qc(args):
            _write_captures(Path(args["env"]["RUNDIR"]), list(cases), qc_value)
            return {"ok": True, "command_id": "Q"}

        def fake_subprocess_run(argv, **kwargs):
            assert any(str(a).endswith("validate_pipeline_semantics.py") for a in argv), argv
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        real_evaluate = wc.Conductor._evaluate_execute_verdict

        def counted(self_, refs_, ir_):
            seen["evaluations"] += 1
            return real_evaluate(self_, refs_, ir_)

        if binary_meta is not None:
            meta_path = repo / refs.binary_dir() / "binary_meta.json"
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(binary_meta))
        if ir_hash is not None:
            # What `run_phase` binds before the substep: the validate key's inputs.
            c._phase_derivations[(refs.node_key, "validate")] = {
                "derivation_key": "sha256:" + "0" * 64, "transformation": ["t"],
                "derivation_inputs": {"ir": ir_hash}}
        with mock.patch.object(build_runtime, "tool_run_program", fake_run_program), \
                mock.patch.object(build_runtime, "tool_run_quality_checks", fake_qc), \
                mock.patch.object(wc.subprocess, "run", fake_subprocess_run), \
                mock.patch.object(wc.Conductor, "_evaluate_execute_verdict", counted), \
                mock.patch.object(wc.Conductor, "_ir_unevaluable",
                                  staticmethod(lambda *_a: False)):
            _record_build_site(repo, refs)
            # A fresh agent-run id per launch, as `new_agent_run_id` gives production.
            self.launches = getattr(self, "launches", 0) + 1
            child = f"child-{self.launches}"
            result = c._run_deterministic_substep(refs, "validate", "execute", child, {})
        seen["conductor"] = c
        seen["child"] = child
        return result, seen

    @staticmethod
    def refs() -> Any:
        import tools.workflow_conductor as wc
        from tools.tests.test_workflow_conductor import _TARGET_ID
        return wc.NodeRefs(target_id=_TARGET_ID, node_key="component/spec_x@0.1.0",
                           spec_path="spec/component/spec_x", ir_id="x_1",
                           pipeline_id="x_1", source_id="src_1", binary_id="bin_1",
                           run_id="run_1", source_binary_id="bin_1")

    def node_file(self, name: str, repo: Path | None = None) -> Path:
        return (repo or self.repo) / self.refs().run_node_dir() / name

    def read(self, name: str, repo: Path | None = None) -> dict[str, Any] | None:
        path = self.node_file(name, repo)
        return json.loads(path.read_text()) if path.exists() else None

    def summary(self, result: Any) -> str:
        import tools.workflow_conductor as wc
        return wc.Conductor._leaf_failure_summary(result)

    def test_an_agreeing_oracle_writes_the_record_and_the_verdict(self) -> None:
        result, _seen = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        check = self.read(oracle.CHECK_RECORD)
        assert check is not None
        self.assertEqual(check["status"], "agree")
        self.assertEqual([b["id"] for b in check["builds"]], ["default", "alt"])
        self.assertEqual([b["cases"] for b in check["builds"]], [["c_alpha"], ["c_beta"]])
        self.assertEqual([b["command_id"] for b in check["builds"]], ["O-default", "O-alt"])
        self.assertEqual(check["comparison"]["failures"], [])
        self.assertEqual(check["comparison"]["records_count"], 1)
        self.assertEqual(self.read("verdict.json")["self_verdict"], "pass")
        self.assertEqual(self.read("trial_meta.json")["status"], "pass")
        self.assertNotIn("failure_category", self.read("trial_meta.json"))

    def test_each_build_runs_its_own_cases_bare_and_serial_and_the_captures_are_merged(
            self) -> None:
        from tools.host_execution import serial_execution_env
        _result, seen = self.execute()
        refs = self.refs()
        runs = seen["oracle_runs"]
        child = seen["child"]
        ir_spec = str((self.repo / refs.ir_ref / "spec.ir.yaml").resolve())
        self.assertEqual(
            [r["command"] for r in runs],
            [[str((self.repo / refs.binary_dir() / "bin_oracle" / b / "spec_x_runner")
                  .resolve()), "--cases", ir_spec, case]
             for b, case in (("default", "c_alpha"), ("alt", "c_beta"))])
        target = seen["conductor"].target
        for r in runs:
            self.assertEqual(r["env"], serial_execution_env(target.parallel_backend,
                                                            target.block_size))
            self.assertEqual(r["command_log_path"],
                             str(self.node_file("command_log.jsonl")))
        merged = (self.repo / "workspace" / "tmp" / child / "oracle_run" / "merged" / "raw"
                  / "state_snapshots")
        self.assertEqual(sorted(p.name for p in merged.glob("*.json")),
                         ["c_alpha.json", "c_beta.json"])

    def test_a_disagreement_is_terminal_writes_the_record_and_no_verdict(self) -> None:
        import tools.workflow_conductor as wc
        result, _seen = self.execute(oracle_value={"alt": 0.5})
        self.assertEqual(result.returncode, 0, result.stderr)
        check = self.read(oracle.CHECK_RECORD)
        assert check is not None
        self.assertEqual(check["status"], "disagree")
        self.assertEqual([(f["case"], f["quantity"]) for f in check["comparison"]["failures"]],
                         [("c_beta", "cross")])
        self.assertIsNone(self.read("verdict.json"))
        meta = self.read("trial_meta.json")
        self.assertEqual((meta["status"], meta["failure_category"]),
                         ("fail", oracle.DISAGREEMENT))
        self.assertIn("case 'c_beta' 'cross'", meta["failure_excerpt"])
        # The operator's log (`dialogs/deterministic.stderr.log`) carries the report too.
        self.assertIn("[execute fail: oracle]", result.stderr)
        self.assertIn(oracle.DISAGREEMENT, result.stderr.splitlines()[-1])
        self.assertIn(oracle.DISAGREEMENT, meta["failure_excerpt"].splitlines()[-1])
        self.assertIn(oracle.DISAGREEMENT, wc.VALIDATE_EXECUTE_FAILURE_TERMINAL)

    def test_two_consecutive_disagreements_through_classify_failure_never_reopen_compile(
            self) -> None:
        import tools.workflow_conductor as wc
        c = self.execute(oracle_value={"default": 1.0})[1]["conductor"]
        refs = self.refs()
        # `execute` is validate's first substep since issue #724 PR-2 deleted `pre_judge`, so
        # a failed execute is the one-outcome list.
        self.assertEqual(wc.SUBSTEPS["validate"][0], "execute")
        outcomes = [wc.SubstepOutcome("ex", "fail", [], 0)]
        for _ in range(3):
            d = c.classify_failure(refs, "validate", outcomes)
            self.assertEqual((d.action, d.reason),
                             ("fail_closed",
                              f"{wc.VALIDATE_EXECUTE_REASON_PREFIX}{oracle.DISAGREEMENT}"))
        self.assertEqual(getattr(c, "_validate_execute_fail_count", {}).get(refs.node_key, 0),
                         0)
        self.assertIsNone(c._read_repair_findings(
            refs, f"{wc.VALIDATE_EXECUTE_REASON_PREFIX}{oracle.DISAGREEMENT}", "validate"))

    def test_the_written_verdict_is_the_evaluated_one_with_the_ir_hash(self) -> None:
        """The write `_execute_inproc` makes after the comparison (and on a node without an
        oracle): verdict.json is the evaluated document, `ir_hash` included, which another
        target's comparand selection reads."""
        import tools.workflow_conductor as wc
        ir_hash = "sha256:" + "1" * 64
        for oracle_text in (_EXEC_ORACLE, None):
            with self.subTest(oracle=oracle_text is not None), \
                    tempfile.TemporaryDirectory() as td:
                repo = Path(td)
                evaluated: list[dict] = []
                real = wc.Conductor._evaluate_execute_verdict

                def spy(self_, refs_, ir_, real=real, evaluated=evaluated):
                    doc = real(self_, refs_, ir_)
                    evaluated.append(json.loads(json.dumps(doc)))
                    return doc

                with mock.patch.object(wc.Conductor, "_evaluate_execute_verdict", spy):
                    result, _seen = self.execute(oracle_text=oracle_text, ir_hash=ir_hash,
                                                 repo=repo)
                self.assertEqual(result.returncode, 0, result.stderr)
                written = self.read("verdict.json", repo)
                self.assertEqual(written["ir_hash"], ir_hash)
                self.assertEqual([written], evaluated)

    def test_the_oracle_runs_on_an_xfail_verdict(self) -> None:
        result, seen = self.execute(ir=_exec_ir(("c_alpha", "c_beta"), outcome="xfail"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read("verdict.json")["self_verdict"], "xfail")
        self.assertEqual(len(seen["oracle_runs"]), 2)
        self.assertEqual(self.read(oracle.CHECK_RECORD)["status"], "agree")

    def test_the_oracle_is_compared_with_the_serial_reference_not_the_run(self) -> None:
        # The run and the serial reference differ by 9e-13 (they agree under the 1e-12 bound);
        # the oracle is 6e-13 from the reference and 1.5e-12 from the run.
        result, _seen = self.execute(run_value=0.0, qc_value=9e-13,
                                     oracle_value={"default": 1.5e-12, "alt": 1.5e-12})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read(oracle.CHECK_RECORD)["status"], "agree")
        result, _seen = self.execute(run_value=9e-13, qc_value=0.0,
                                     oracle_value={"default": 1.5e-12, "alt": 1.5e-12})
        self.assertEqual(self.read(oracle.CHECK_RECORD)["status"], "disagree")

    def test_the_loader_path_holds_the_closure_s_library_dirs_then_the_build_s(self) -> None:
        from tools.backends import registry
        from tools.checked_build import target_compiler
        closure, own = "/libs/closure/lib", "/libs/oracle_alt/lib"
        meta = {"library_dirs": [closure],
                "oracle": {"builds": [
                    {"id": "default", "libraries": []},
                    {"id": "alt", "libraries": [{"library_id": "x", "lib_dirs": [own]}]}]}}
        from tools.tests.target_fixtures import fixture_target
        variable = str(registry.capability_module(
            "compiler", target_compiler(fixture_target(self.repo)),
            "library_binary").LOADER_VARIABLE)
        inherited = "/operator/own/lib"
        with mock.patch.dict(os.environ, {variable: inherited}):
            _result, seen = self.execute(binary_meta=meta)
        paths = {Path(r["command"][0]).parent.name: r["env"][variable].split(os.pathsep)
                 for r in seen["oracle_runs"]}
        self.assertEqual(paths["default"][:1], [closure])
        self.assertEqual(paths["alt"][:2], [closure, own])
        # The value the run would otherwise inherit stays behind them.
        self.assertEqual(paths["alt"][-1], inherited)

    #: Case ids as long as the channel problem's (`chan_tc2_xpert_n032_dts100`), 30 of them.
    _LONG = tuple(f"chan_tc{i}_xpert_n032_dts100_ymir" for i in range(30))

    def test_each_refusal_keeps_its_reason_and_remedy_in_the_failure_summary(self) -> None:
        """The run's failure summary keeps the last 400 characters (`_leaf_failure_summary`):
        each refusal's reason and remedy are at that end, over a case list and a stderr that
        are longer than the cut."""
        cases = self._LONG
        self.assertGreater(len(str(list(cases))), 400)
        alt = _EXEC_ORACLE.replace("cases: [c_beta]", f"cases: [{cases[0]}]")
        # The run row's stderr straddles the cut; the comparison's `error` is bounded to 400
        # characters by `_shape_state_agreement`, and the two others are checked for their
        # remedy by cause.
        rows = {
            "run": ({"oracle_rc": 3}, oracle.RUN_FAILED, "Fix the adapter", True),
            "capture": ({"oracle_broken": cases[1:]}, oracle.UNEVALUABLE, "fix the adapter",
                        False),
            "no_cross": ({"ir": _exec_ir(cases, cross=False)}, oracle.UNEVALUABLE,
                         "remove the declaration", False),
            "declaration": ({"oracle_text": alt.replace(cases[0], "not_a_case")},
                            oracle.DECLARATION_INVALID, "Fix spec/", False),
        }
        for name, (kwargs, reason, remedy, long_) in rows.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as td:
                result, _seen = self.execute(**{"ir": _exec_ir(cases), "cases": cases,
                                                 "oracle_text": alt, "repo": Path(td),
                                                 **kwargs})
                self.assertEqual(result.returncode, 1, result.stderr)
                if long_:
                    self.assertGreater(len(result.stderr), 400, result.stderr)
                summary = self.summary(result)
                for needle in (reason, remedy, "--resume"):
                    self.assertIn(needle, summary)
                # Each cause carries its own remedy and not another's.
                for other in ({"fix the adapter", "remove the declaration"} - {remedy.lower()}
                              if reason == oracle.UNEVALUABLE else ()):
                    self.assertNotIn(other, summary.lower())

    def test_a_build_that_takes_no_compared_case_is_skipped_and_the_next_still_runs(
            self) -> None:
        # `first` names a case the IR holds and no cross-target predicate compares.
        # A second test reads c_gamma alone and applies no cross-target judgment.
        ir = (_EXEC_IR + _EXEC_CROSS).replace(
            "    - case_id: c_beta\n", "    - case_id: c_beta\n    - case_id: c_gamma\n"
        ).replace("  predicates:\n", "    - test_id: t_gamma\n      expected_outcome: pass\n"
                  "      target_cases: [c_gamma]\n      quantities: [own]\n  predicates:\n"
        ) + ("    - {test_id: t_gamma, quantity: own, target_cases: [c_gamma], per_case: true,\n"
             "       expr: final.v, op: le, value: 1.0e-12}\n")
        text = _EXEC_ORACLE.replace(
            "builds:\n", "builds:\n  - id: first\n    libraries: []\n    cases: [c_gamma]\n")
        result, seen = self.execute(ir=ir, oracle_text=text,
                                    cases=("c_alpha", "c_beta", "c_gamma"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([Path(r["command"][0]).parent.name for r in seen["oracle_runs"]],
                         ["default", "alt"])
        builds = self.read(oracle.CHECK_RECORD)["builds"]
        self.assertEqual([(b["id"], b["cases"], b["command_id"]) for b in builds],
                         [("first", [], None), ("default", ["c_alpha"], "O-default"),
                          ("alt", ["c_beta"], "O-alt")])

    def test_a_long_unevaluable_detail_keeps_its_verdict_at_the_tail(self) -> None:
        """The comparison's `error` reaches 400 characters (`_shape_state_agreement` clips it
        there); the message puts it first and the verdict and remedy last, so the run's failure
        summary, which keeps the last 400 characters, still holds them."""
        import tools.workflow_conductor as wc
        long_error = "capture file " + "x" * 380 + ".json is absent"
        self.assertGreaterEqual(len(long_error), 400)
        real = wc.Conductor._shape_state_agreement
        calls: list[str] = []

        def agreement(ir, reference_dir, label, *a, **k):
            # The oracle's comparison alone; the execution shapes compare as they do.
            if label != "oracle":
                return real(ir, reference_dir, label, *a, **k)
            calls.append(label)
            return {"status": "unevaluable", "error": long_error}

        with mock.patch.object(wc.Conductor, "_shape_state_agreement",
                               staticmethod(agreement)):
            result, _seen = self.execute()
        self.assertEqual(calls, ["oracle"])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertGreater(len(result.stderr), 400)
        summary = self.summary(result)
        for needle in (oracle.UNEVALUABLE, "fix the adapter", "--resume"):
            self.assertIn(needle, summary)

    def test_an_unevaluable_comparison_is_fail_closed(self) -> None:
        # The oracle's capture of c_beta is unreadable: the comparison cannot evaluate it.
        result, _seen = self.execute(oracle_broken=("c_beta",))
        self.assertEqual(result.returncode, 1)
        self.assertIn(oracle.UNEVALUABLE, self.summary(result))
        self.assertIsNone(self.read(oracle.CHECK_RECORD))
        self.assertIsNone(self.read("verdict.json"))

    def test_a_compared_case_without_an_oracle_capture_is_unevaluable_and_never_agree(
            self) -> None:
        # The oracle exits 0 and leaves no capture of a case its build was given: no filter
        # drops the case, and the run never reads `agree`.
        result, seen = self.execute(oracle_skips=("c_alpha",))
        self.assertEqual(result.returncode, 1)
        summary = self.summary(result)
        self.assertIn(oracle.UNEVALUABLE, summary)
        self.assertIn("--resume", summary)
        self.assertEqual(len(seen["oracle_runs"]), 2)
        self.assertIsNone(self.read(oracle.CHECK_RECORD))

    #: `_EXEC_IR` with a third case, c_gamma, under both predicates: the cross-target one
    #: compares it. `execute_continuation` makes it a continuation of c_alpha.
    _CONT_IR = (_EXEC_IR + _EXEC_CROSS).replace(
        "    - case_id: c_beta\n", "    - case_id: c_beta\n    - case_id: c_gamma\n").replace(
        "[c_alpha, c_beta]", "[c_alpha, c_beta, c_gamma]")

    def execute_continuation(self, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
        """`execute` over `_CONT_IR`'s three cases, with c_gamma a continuation of c_alpha in
        the IR the oracle comparison receives: the run is driven as a node without
        continuations (its second pass needs a rendered driver), and its captures of c_gamma
        are in the reference as a second pass leaves them. `seen["compared_ir"]` is that IR."""
        import copy

        import tools.workflow_conductor as wc
        real = wc.Conductor._compare_with_oracle
        got: dict[str, Any] = {}

        def compare(self_, refs_, arid, ir_, *a, **k):
            ir_ = copy.deepcopy(ir_)
            for case in ir_["case"]["test_case_set"]:
                if case["case_id"] == "c_gamma":
                    case["restart"] = {"from": "c_alpha", "step": 1}
            got["ir"] = ir_
            return real(self_, refs_, arid, ir_, *a, **k)

        kwargs.setdefault("ir", self._CONT_IR)
        with mock.patch.object(wc.Conductor, "_compare_with_oracle", compare):
            result, seen = self.execute(cases=("c_alpha", "c_beta", "c_gamma"), **kwargs)
        seen["compared_ir"] = got.get("ir")
        return result, seen

    def test_a_continuation_case_is_left_out_of_the_oracle_comparison(self) -> None:
        """Issue #709 F-1: the oracle runs the compared cases that are not continuation cases,
        the comparison reads those alone, and the record lists what ran. The run captured
        c_gamma, and the oracle never runs or is asked for it. A second cross-target
        predicate scoped to c_gamma alone is absent from the comparison's records."""
        alone = ('    - {test_id: t_alpha, quantity: cross, target_cases: [c_alpha, c_beta, '
                 'c_gamma], case: c_gamma,\n'
                 '       expr: "maxabs(final.v - comparand.final.v)", op: le, value: 1.0e-12}\n')
        result, seen = self.execute_continuation(ir=self._CONT_IR + alone)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(oracle.compared_cases(seen["compared_ir"]), ["c_alpha", "c_beta"])
        self.assertEqual([r["command"][3:] for r in seen["oracle_runs"]],
                         [["c_alpha"], ["c_beta"]])
        check = self.read(oracle.CHECK_RECORD)
        assert check is not None
        self.assertEqual(check["status"], "agree")
        self.assertEqual([(b["id"], b["cases"]) for b in check["builds"]],
                         [("default", ["c_alpha"]), ("alt", ["c_beta"])])
        self.assertEqual(check["comparison"]["records_count"], 1)
        self.assertEqual(self.read("verdict.json")["self_verdict"], "pass")

    def test_the_base_of_a_continuation_still_decides_the_comparison(self) -> None:
        """The base is compared: an oracle that disagrees on c_alpha alone (the default
        build) is `disagree` on c_alpha, whatever c_gamma would have read."""
        result, _seen = self.execute_continuation(oracle_value={"default": 1e-6})
        check = self.read(oracle.CHECK_RECORD)
        assert check is not None, result.stderr
        self.assertEqual(check["status"], "disagree")
        self.assertEqual([(f["case"], f["quantity"]) for f in check["comparison"]["failures"]],
                         [("c_alpha", "cross")])

    def test_a_build_that_names_the_continuation_case_changes_nothing(self) -> None:
        text = _EXEC_ORACLE.replace("cases: [c_beta]", "cases: [c_beta, c_gamma]")
        result, seen = self.execute_continuation(oracle_text=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([r["command"][3:] for r in seen["oracle_runs"]],
                         [["c_alpha"], ["c_beta"]])
        check = self.read(oracle.CHECK_RECORD)
        assert check is not None
        self.assertEqual([(b["id"], b["cases"]) for b in check["builds"]],
                         [("default", ["c_alpha"]), ("alt", ["c_beta"])])
        self.assertEqual(check["status"], "agree")

    def test_every_case_of_a_build_with_several_is_compared(self) -> None:
        """The comparison reads every case of every build (`compared`), not one per build: the
        default build takes c_alpha and c_delta, and its unreadable capture of the second is
        `unevaluable`, never `agree`."""
        cases = ("c_alpha", "c_beta", "c_delta")
        result, seen = self.execute(ir=_exec_ir(cases), cases=cases,
                                    oracle_broken=("c_delta",))
        self.assertEqual([r["command"][3:] for r in seen["oracle_runs"]],
                         [["c_alpha", "c_delta"], ["c_beta"]])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(oracle.UNEVALUABLE, self.summary(result))
        self.assertIsNone(self.read(oracle.CHECK_RECORD))

    def test_a_node_whose_every_compared_case_is_a_continuation_is_unevaluable(self) -> None:
        """Nothing is compared, so nothing agrees: the oracle does not run, the comparison
        stops `oracle_unevaluable`, and the remedy removes the declaration and never the
        spec's judgment, which also compares two declared targets."""
        ir = self._CONT_IR.replace(
            'quantity: cross, target_cases: [c_alpha, c_beta, c_gamma], per_case: true',
            'quantity: cross, target_cases: [c_alpha, c_beta, c_gamma], case: c_gamma')
        self.assertIn("case: c_gamma", ir)
        result, seen = self.execute_continuation(ir=ir)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(seen["oracle_runs"], [])
        summary = self.summary(result)
        for needle in (oracle.UNEVALUABLE, "remove the declaration", "--resume"):
            self.assertIn(needle, summary)
        self.assertIn("continuation case", result.stderr)
        self.assertNotIn("cross-target judgment", summary)
        self.assertNotIn("fix the adapter", summary.lower())
        self.assertIsNone(self.read(oracle.CHECK_RECORD))
        self.assertIsNone(self.read("verdict.json"))

    def test_an_oracle_on_an_ir_with_no_cross_target_predicate_is_fail_closed(self) -> None:
        result, seen = self.execute(ir=_EXEC_IR)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(seen["oracle_runs"], [])
        self.assertIn(oracle.UNEVALUABLE, self.summary(result))
        self.assertIn("not_applicable", result.stderr)
        self.assertIsNone(self.read("verdict.json"))

    def test_an_oracle_run_that_exits_non_zero_is_fail_closed(self) -> None:
        result, seen = self.execute(oracle_rc=3)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(seen["oracle_runs"]), 1)
        self.assertGreater(len(result.stderr), 400)
        summary = self.summary(result)
        self.assertIn(oracle.RUN_FAILED, summary)
        self.assertIn("--resume", summary)
        self.assertIn("adapter aborted", result.stderr)
        self.assertIsNone(self.read(oracle.CHECK_RECORD))
        self.assertIsNone(self.read("verdict.json"))

    def test_a_case_plan_the_declaration_breaks_is_oracle_declaration_invalid(self) -> None:
        text = _EXEC_ORACLE.replace("cases: [c_beta]", "cases: [c_gamma]")
        result, seen = self.execute(oracle_text=text)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(seen["oracle_runs"], [])
        self.assertIn(oracle.DECLARATION_INVALID, self.summary(result))

    def test_the_oracle_is_not_run_when_the_run_s_own_verdict_fails(self) -> None:
        result, seen = self.execute(run_value=1.0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(seen["oracle_runs"], [])
        self.assertIsNone(self.read(oracle.CHECK_RECORD))
        self.assertEqual(self.read("verdict.json")["self_verdict"], "fail")
        self.assertNotIn("failure_category", self.read("trial_meta.json"))

    def test_the_predicates_are_evaluated_once_for_the_verdict(self) -> None:
        for kwargs in ({}, {"oracle_value": {"default": 1.0}}, {"oracle_text": None}):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
                _result, seen = self.execute(**kwargs)
                self.assertEqual(seen["evaluations"], 1)

    def test_quality_check_json_and_trial_meta_are_unchanged_by_an_agreeing_oracle(
            self) -> None:
        with tempfile.TemporaryDirectory() as other:
            plain = Path(other)
            self.execute(oracle_text=None, repo=plain)
            self.execute()
            for name in ("quality_check.json", "trial_meta.json", "verdict.json"):
                with self.subTest(name=name):
                    self.assertEqual(self.node_file(name).read_bytes(),
                                     self.node_file(name, plain).read_bytes())
            self.assertIsNone(self.read(oracle.CHECK_RECORD, plain))

    def test_no_record_on_a_target_of_another_language(self) -> None:
        result, seen = self.execute(
            oracle_text=_EXEC_ORACLE.replace("language: fortran", f"language: {OTHER}"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(seen["oracle_runs"], [])
        self.assertIsNone(self.read(oracle.CHECK_RECORD))
        self.assertEqual(self.read("verdict.json")["self_verdict"], "pass")


#: A driver that writes, per case it is given, the initial and final captures of `v`, the
#: model's (or the adapter's) `m_value()`.
_CAPTURE_DRIVER = """\
program p
  use m_model, only: m_value
  implicit none
  integer :: i, u
  character(len=256) :: arg
  do i = 3, command_argument_count()
    call get_command_argument(i, arg)
    open(newunit=u, file='raw/state_snapshots/initial/'//trim(arg)//'.json', status='replace')
    write(u, '(a)') '{"v": 0.0, "t": 0.0}'
    close(u)
    open(newunit=u, file='raw/state_snapshots/'//trim(arg)//'.json', status='replace')
    write(u, '(a,es25.17e3,a)') '{"v": ', m_value(), ', "t": 1.0}'
    close(u)
  end do
end program p
"""
_VALUE_MODULE = """\
module m_model
  implicit none
contains
  function m_value() result(v)
    real(8) :: v
{body}
  end function m_value
end module m_model
"""
#: The existing code's answer, through the fixture library: foo_add(1) = 2*1 + 1.
_VALUE_ADAPTER = _VALUE_MODULE.format(
    body="    real(8), external :: foo_add\n    v = foo_add(1d0)")
#: The suite asks `v` within 1e-3 of 3; the cross-target judgment asks 1e-12 of the comparand.
_E2E_IR = """\
case:
  test_case_set:
    - case_id: c_alpha
    - case_id: c_beta
io_contract:
  raw_requirements:
    required_evidence:
      - artifact: state_snapshots
        required: true
        schema:
          variables:
            - {name: v, shape_expr: scalar}
          time_variable: t
          time_shape_expr: scalar
  tests:
    - {test_id: t, expected_outcome: pass, target_cases: [c_alpha, c_beta],
       quantities: [own, cross]}
  predicates:
    - {test_id: t, quantity: own, target_cases: [c_alpha, c_beta], per_case: true,
       expr: "abs(final.v - 3.0)", op: le, value: 1.0e-3}
    - {test_id: t, quantity: cross, target_cases: [c_alpha, c_beta], per_case: true,
       expr: "maxabs(final.v - comparand.final.v)", op: le, value: 1.0e-12}
"""


class OracleEndToEndTest(OracleBuildTest):
    """A model built and run, its oracle built and run, and the comparison, with the real
    compiler, `make` and the real run tool: `Build` builds both (`_build_oracle`), the test runs
    the certified binary as the serial reference, and `_compare_with_oracle` runs the oracle
    against the fixture library and compares. A wrong model that the node's own suite passes
    disagrees with the oracle; the correct one agrees."""

    def compare(self, model_body: str) -> dict[str, Any]:
        import dataclasses

        import yaml
        text = ("oracle_version: 1\nlanguage: fortran\nsource: adapter.f90\n"
                "builds:\n  - id: default\n    libraries: [foo]\n")
        repo, c, refs = self.node(oracle_text=text, adapter=_VALUE_ADAPTER,
                                  model=_VALUE_MODULE.format(body=model_body))
        (repo / refs.source_dir() / "src" / "spec_x_runner.f90").write_text(_CAPTURE_DRIVER)
        ir_spec = repo / refs.ir_ref / "spec.ir.yaml"
        ir_spec.write_text(_E2E_IR)
        ir = yaml.safe_load(_E2E_IR)
        result = self.build(c, refs)
        self.assertEqual(result.returncode, 0, result.stderr)
        refs = dataclasses.replace(refs, run_id="r1", source_binary_id=refs.binary_id)
        bin_dir = repo / refs.binary_dir() / "bin"
        raw_dirs = [("raw", "state_snapshots"), ("raw", "state_snapshots", "initial")]
        reference = repo / "reference"
        for raw in raw_dirs:
            reference.joinpath(*raw).mkdir(parents=True, exist_ok=True)
        subprocess.run([str(bin_dir / "spec_x_runner"), "--cases", str(ir_spec), "c_alpha",
                        "c_beta"], cwd=reference, check=True)
        declared = oracle.load(repo, _BUILD_SPEC, "fortran")
        cmd_log = repo / refs.run_node_dir() / "command_log.jsonl"
        cmd_log.parent.mkdir(parents=True)
        return c._compare_with_oracle(
            refs, "child-2", ir, declared, reference_dir=reference, bin_dir=bin_dir,
            exe="spec_x_runner", library_dirs=(), ir_spec=ir_spec, cmd_log=cmd_log,
            run_raw_dirs=raw_dirs)

    def test_the_correct_model_agrees(self) -> None:
        check = self.compare("    v = 2d0 * 1d0 + 1d0")
        self.assertEqual(check["status"], "agree", check)
        self.assertEqual(check["comparison"]["records_count"], 1)
        self.assertEqual(check["builds"][0]["cases"], ["c_alpha", "c_beta"])

    def test_a_wrong_model_the_suite_passes_disagrees_with_the_oracle(self) -> None:
        check = self.compare("    v = 3d0 + 1d-6")
        self.assertEqual(check["status"], "disagree", check)
        failures = check["comparison"]["failures"]
        self.assertEqual((failures[0]["case"], failures[0]["quantity"]), ("c_alpha", "cross"))
        self.assertAlmostEqual(failures[0]["value"], 1e-6, delta=1e-9)


class CorpusOracleTest(unittest.TestCase):
    """The corpus's oracle declarations (issue #680 PR-4), read without the site and without
    building anything. Every declaration loads for its own language and names each case once
    (the rest of the case plan needs the IR, which `Compile` fixes). The two microphysics
    nodes, whose `tests.md` §2 holds its cases as one YAML list, are checked against it: the
    original holds its constants at compile time, so the `alt` build takes exactly the
    `constants: alt` cases, every one a `pass` case, and names the library declared for those
    constants (which archive a library id resolves to is the site's). The declarations are
    the SCALE fixture's since issue #705 PR-3: no sample node declares an oracle."""

    REPO = SCALE_FIXTURE_ROOT
    _MICROPHYSICS = ("microphysics_saturation_liquid_1d_column",
                     "microphysics_kessler_warm_rain_1d_column")

    @staticmethod
    def _tests_md_cases(spec_dir: Path) -> dict[str, dict[str, Any]]:
        import re

        import yaml
        text = (spec_dir / "tests.md").read_text(encoding="utf-8")
        section = text.split("\n## 2.", 1)[1].split("\n## 3.", 1)[0]
        fences = re.findall(r"```yaml\n(.*?)```", section, flags=re.DOTALL)
        return {case["case_id"]: case for fence in fences
                for case in (yaml.safe_load(fence) or {}).get("cases", [])}

    def test_the_corpus_oracle_declarations_load_and_name_cases_of_their_tests_md(self) -> None:
        import yaml
        declared: dict[str, oracle.Oracle] = {}
        for path in sorted(self.REPO.glob(f"spec/**/{oracle.ORACLE_DIR}/"
                                          f"{oracle.DECLARATION_FILE}")):
            spec_dir = path.parent.parent
            spec_ref = spec_dir.relative_to(self.REPO).as_posix()
            language = yaml.safe_load(path.read_text(encoding="utf-8"))["language"]
            loaded = oracle.load(self.REPO, spec_ref, language)
            assert loaded is not None
            declared[spec_dir.name] = loaded
            named = [case_id for build in loaded.builds for case_id in build.cases or ()]
            self.assertEqual(len(named), len(set(named)), spec_ref)
        self.assertLessEqual(set(self._MICROPHYSICS), set(declared))
        for spec_id in self._MICROPHYSICS:
            loaded = declared[spec_id]
            cases = self._tests_md_cases(self.REPO / loaded.spec_ref)
            builds = {b.build_id: b for b in loaded.builds}
            self.assertEqual(set(builds), {"default", "alt"}, spec_id)
            self.assertIsNone(builds["default"].cases, spec_id)
            alt_cases = {i for i, c in cases.items() if c.get("constants") == "alt"}
            self.assertTrue(alt_cases, spec_id)
            self.assertEqual(set(builds["alt"].cases or ()), alt_cases, spec_id)
            self.assertTrue(all(cases[i].get("expected") == "pass" for i in alt_cases), spec_id)
            self.assertEqual([d.library_id for d in builds["default"].libraries],
                             ["scale_mp_oracle"], spec_id)
            self.assertEqual([d.library_id for d in builds["alt"].libraries],
                             ["scale_mp_oracle_alt"], spec_id)


if __name__ == "__main__":
    unittest.main()
