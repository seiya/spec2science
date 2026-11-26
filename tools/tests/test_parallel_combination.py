"""A `parallel.backend` value that combines several models (issue #700, Part 3 PR-1).

`mpi+openmp` names two members joined by `+`. The registry answers each question for it from the
members (`tools/backends/registry.py`: `members`, `package_members`, and the combination arms of
the membership, capability and module questions); the dispatch sites that a capability several
members declare ask each member. These rows drive a combination profile through every gate and
driver-side probe PR-1 names, and pin that a one-member value answers as before.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tools import host_execution as he
from tools import host_prerequisites as hp
from tools import orchestration_runtime as ort
from tools import target_profile as tp
from tools import validate_pipeline_semantics as vps
from tools.backends import registry
from tools.tests.target_fixtures import SAMPLE_ROOT, install_target_profile, profile_with

REPO_ROOT = Path(__file__).resolve().parents[2]
MPI = tp.load_target_profile(SAMPLE_ROOT, "fortran_cpu_mpi")
#: The checked-in MPI profile with the combination, one thread per rank.
HYBRID = profile_with(MPI, parallel={"backend": "mpi+openmp"},
                      execution={**MPI.doc["execution"], "threads_per_rank": 1})


class RegistryCombinationTests(unittest.TestCase):

    def test_only_the_parallel_axis_combines(self) -> None:
        self.assertEqual([a for a, spec in registry.AXES.items() if spec.combinable],
                         ["parallel"])
        self.assertEqual(registry.members("parallel", " MPI+OpenMP "), ("mpi", "openmp"))
        self.assertEqual(registry.members("parallel", "openmp"), ("openmp",))
        self.assertEqual(registry.members("language", "a+b"), ("a+b",))

    def test_membership_questions_answer_with_the_first_members_reason(self) -> None:
        for question in (registry.unsupported_reason, registry.unimplemented_reason,
                         registry.unavailable_reason):
            with self.subTest(question=question.__name__):
                self.assertIsNone(question("parallel", "mpi+openmp"))
                self.assertEqual(question("parallel", "mpi+zz_none_such"),
                                 question("parallel", "zz_none_such"))
                self.assertEqual(question("parallel", "zz_a+zz_b"), question("parallel", "zz_a"))
        # An empty member is not a value, on an open axis too.
        self.assertIsNotNone(registry.unsupported_reason("parallel", "mpi+"))
        # A member whose job is the neutral core's is not extracted.
        self.assertIsNone(registry.unimplemented_reason("parallel", "mpi+none"))
        self.assertEqual(registry.unavailable_reason("parallel", "mpi+none"),
                         registry.unavailable_reason("parallel", "none"))

    def test_provides_is_some_member_and_package_members_keeps_member_order(self) -> None:
        self.assertTrue(registry.provides("parallel", "openmp+mpi", "launcher"))
        self.assertFalse(registry.provides("parallel", "openmp+none", "launcher"))
        self.assertIsNone(registry.missing_capability_reason(
            "parallel", "mpi+openmp", "prompt_fragments"))
        self.assertIsNotNone(registry.missing_capability_reason(
            "parallel", "openmp+none", "launcher"))
        self.assertEqual(registry.package_members("parallel", "openmp+mpi", "execution_env"),
                         ("openmp", "mpi"))
        self.assertEqual(registry.package_members("parallel", "mpi+openmp", "launcher"), ("mpi",))
        self.assertEqual(registry.package_members("parallel", "mpi+none", "execution_env"),
                         ("mpi",))
        self.assertEqual(registry.package_members("parallel", "mpi+mpi", "launcher"), ("mpi",))
        self.assertEqual(registry.package_members("parallel", "zz_x", "launcher"), ())
        # One member: what its record declares in its package, as `get(...).backend_provides`.
        for backend in registry.backend_ids("parallel"):
            for capability, (axes, _d) in registry.CAPABILITIES.items():
                if "parallel" not in axes:
                    continue
                with self.subTest(backend=backend, capability=capability):
                    self.assertEqual(
                        registry.package_members("parallel", backend, capability),
                        (backend,) if capability in registry.get(
                            "parallel", backend).backend_provides else ())
        with self.assertRaises(registry.UnsupportedBackend):
            registry.package_members("parallel", "mpi+openmp", "no_such_capability")

    def test_capability_module_is_the_one_owner_and_refuses_otherwise(self) -> None:
        self.assertIs(registry.capability_module("parallel", "openmp+mpi", "launcher"),
                      registry.capability_module("parallel", "mpi", "launcher"))
        self.assertIs(registry.capability_module("parallel", "mpi+openmp", "compiler_wrapper"),
                      registry.capability_module("parallel", "mpi", "compiler_wrapper"))
        # Several members do the job: a site asks each, never "the" module.
        for capability in ("execution_env", "parallel_directives", "prompt_fragments"):
            with self.subTest(capability=capability), \
                    self.assertRaises(registry.UnsupportedBackend) as caught:
                registry.capability_module("parallel", "mpi+openmp", capability)
            self.assertIn("package_members", str(caught.exception))
        # No member does it.
        with self.assertRaises(registry.BackendNotExtracted):
            registry.capability_module("parallel", "mpi+openmp", "device_trace")
        # A combination has no record and no package, and the refusal says to ask the members
        # rather than to register the value.
        with self.assertRaises(registry.BackendNotExtracted) as caught:
            registry.get("parallel", "mpi+openmp")
        self.assertIn("ask each member", str(caught.exception))
        self.assertNotIn("register it", str(caught.exception))
        with self.assertRaises(registry.BackendNotExtracted):
            registry.load("parallel", "mpi+openmp")


class ProfileCombinationTests(unittest.TestCase):
    """The launch gate and the launch shape of a combination profile."""

    def test_the_gate_admits_it_at_one_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install_target_profile(tmp, HYBRID)
            self._catalog(Path(tmp))
            for phase in ("compile", "validate"):
                with self.subTest(phase=phase):
                    self.assertEqual(
                        [v for v in tp.target_profile_violations(
                            Path(tmp), HYBRID, until_phase=phase)
                         if not v.startswith("toolchain:")], [])

    @staticmethod
    def _catalog(root: Path) -> None:
        reg = root / "spec" / "registry"
        reg.mkdir(parents=True, exist_ok=True)
        (reg / "spec_catalog.yaml").write_text(
            "catalog_version: 0.2.0\nupdated_at: 2026-11-20\nspecs:\n"
            "  - spec_kind: infrastructure\n    spec_id: harness_fortran_cpu_mpi\n"
            "    spec_version: \"0.7.0\"\n    status: controlled_draft\n"
            "    deps_path: spec/infrastructure/harness_fortran_cpu_mpi/deps.yaml\n",
            encoding="utf-8")
        ort._load_spec_catalog.cache_clear()

    def test_a_thread_count_above_one_reaches_the_member_that_runs_threads(self) -> None:
        # Issue #700 PR-2: the count goes to the member that declares `RUNS_THREADS` and one to
        # every other, in both orders; the launch gate admits it. A combination with no such
        # member is refused by the gate alone, naming the value.
        for backend in ("mpi+openmp", "openmp+mpi"):
            with self.subTest(backend=backend):
                two = profile_with(MPI, parallel={"backend": backend},
                                   execution={"threads_per_rank": 2, "ranks": 4,
                                              "process_grid": [2, 2],
                                              "sweep": {"threads_per_rank": [3]}})
                self.assertEqual(tp._thread_count_violations(two), [])
                self.assertEqual(he.launch_shape(two).env,
                                 {"SPEC2SCIENCE_PROCESS_GRID": "2,2",
                                  "OMP_NUM_THREADS": "2", "OMP_THREAD_LIMIT": "2"})
                sweep = next(s for s in two.execution_shapes if s.threads_per_rank == 3)
                self.assertEqual(he.launch_shape(two, None, sweep).env["OMP_NUM_THREADS"], "3")
        for backend in ("mpi+cuda", "cuda+mpi"):
            with self.subTest(backend=backend):
                none_runs = profile_with(MPI, parallel={"backend": backend},
                                         execution={"threads_per_rank": 2, "ranks": 4})
                found = tp._thread_count_violations(none_runs)
                self.assertEqual(len(found), 1, found)
                self.assertIn(f"no member of parallel backend {backend} runs threads", found[0])
                self.assertNotIn("OMP_NUM_THREADS", he._execution_env(backend, 2, (2, 2), None))

    def test_each_imposed_value_reaches_the_member_that_takes_it(self) -> None:
        # The grid goes to the launcher's member and the block size to the member that declares
        # the sizes it admits, in either order; a member that takes neither is not handed them.
        for backend in ("mpi+cuda", "cuda+mpi"):
            with self.subTest(backend=backend):
                self.assertTrue(he.imposes_block_size(backend))
                self.assertEqual(he._execution_env(backend, 1, (2, 2), 128),
                                 {"SPEC2SCIENCE_PROCESS_GRID": "2,2",
                                  "SPEC2SCIENCE_CUDA_BLOCK": "128"})
        self.assertEqual(he._execution_env("openmp+mpi", 1, (2, 1), None),
                         {"SPEC2SCIENCE_PROCESS_GRID": "2,1",
                          "OMP_NUM_THREADS": "1", "OMP_THREAD_LIMIT": "1"})

    def test_the_launch_shape_merges_the_members_environments(self) -> None:
        launcher = registry.capability_module("parallel", "mpi", "launcher")
        shape = he.launch_shape(HYBRID)
        self.assertEqual(shape.argv_prefix, tuple(launcher.argv_prefix(4)))
        self.assertEqual(shape.runtime_probe, tuple(launcher.RUNTIME_PROBE))
        self.assertIsNone(shape.trace)
        self.assertEqual(shape.env, {"SPEC2SCIENCE_PROCESS_GRID": "2,2",
                                     "OMP_NUM_THREADS": "1", "OMP_THREAD_LIMIT": "1"})
        self.assertEqual(he.serial_execution_env("mpi+openmp"),
                         {"SPEC2SCIENCE_PROCESS_GRID": "1,1",
                          "OMP_NUM_THREADS": "1", "OMP_THREAD_LIMIT": "1"})
        self.assertTrue(he.declares_launcher("mpi+openmp"))
        self.assertFalse(he.imposes_block_size("mpi+openmp"))
        self.assertEqual(he.execution_executables("mpi+openmp"),
                         he.execution_executables("mpi"))
        self.assertEqual(he.launch_argv_prefix("mpi+openmp", 3), he.launch_argv_prefix("mpi", 3))
        # The order the operator writes does not change what runs.
        self.assertEqual(he.launch_shape(profile_with(
            HYBRID, parallel={"backend": "openmp+mpi"},
            execution=HYBRID.doc["execution"])).env, shape.env)
        # A sweep grid reaches the launcher's member.
        for sweep in HYBRID.execution_shapes:
            with self.subTest(shape=sweep.label):
                self.assertEqual(he.launch_shape(HYBRID, shape=sweep).env[
                    "SPEC2SCIENCE_PROCESS_GRID"], f"{sweep.process_grid[0]},"
                                                  f"{sweep.process_grid[1]}")

    def test_the_toolchain_identity_and_the_harness_capabilities(self) -> None:
        mpi = ort._target_toolchain_identity(MPI, probe=False)
        hybrid = ort._target_toolchain_identity(HYBRID, probe=False)
        self.assertEqual({**mpi, "backend": "mpi+openmp"}, hybrid)
        self.assertEqual(ort.toolchain_version_argv(HYBRID), ort.toolchain_version_argv(MPI))
        self.assertEqual(ort._host_probed_leaves(HYBRID), ort._host_probed_leaves(MPI))
        self.assertEqual(tp.harness_capabilities(HYBRID), tp.harness_capabilities(MPI))

    def test_the_build_rules_and_the_syntax_stage_take_the_threaded_members_switch(self) -> None:
        control = registry.capability_module("language", "fortran", "control_file")
        syntax = registry.capability_module("compiler", "gfortran", "syntax_check")
        for backend, threaded in (("mpi+openmp", True), ("openmp+mpi", True), ("openmp", True),
                                  ("mpi", False), ("none", False)):
            with self.subTest(backend=backend):
                self.assertEqual("-fopenmp" in control.flags("f2008", backend), threaded)
                self.assertEqual("-fopenmp" in syntax.argv(
                    standard="f2008", scratch_dir="/s", parallel_backend=backend,
                    promotions=(), architecture=None, sources=["a.f90"]), threaded)
        self.assertNotIn("-fopenmp", syntax.argv(
            standard="f2008", scratch_dir="/s", parallel_backend=None, promotions=(),
            architecture=None, sources=["a.f90"]))

    def test_a_device_member_beside_a_language_the_wrapper_does_not_compile_is_refused(
            self) -> None:
        gpu = tp.load_target_profile(SAMPLE_ROOT, "cpp_gpu")
        combined = profile_with(gpu, parallel={"backend": "mpi+cuda"},
                                execution={"threads_per_rank": 1})
        found = [v for v in tp.target_profile_violations(SAMPLE_ROOT, combined,
                                                         until_phase="compile")
                 if "compiler wrapper runs" in v]
        self.assertEqual(len(found), 1, found)


class TraceAndBlockCombinationTests(unittest.TestCase):
    """The sites that ask whether the value's package does a job one member may own: a
    combination has no record, so a site still reading `registry.get` would raise here."""

    def test_a_block_size_on_a_combination_with_no_device_member_is_refused(self) -> None:
        blocked = profile_with(HYBRID, execution={**HYBRID.doc["execution"], "block_size": 128})
        found = tp._block_size_violations(blocked)
        self.assertEqual(len(found), 1, found)
        self.assertIn("parallel backend mpi+openmp imposes none", found[0])

    def test_a_combination_is_traced_only_when_a_member_declares_the_trace(self) -> None:
        from tools import workflow_conductor as wc

        gpu = tp.load_target_profile(SAMPLE_ROOT, "cpp_gpu")
        self.assertTrue(wc.Conductor._traces_execution(mock.Mock(target=profile_with(
            gpu, parallel={"backend": "mpi+cuda"}, execution={"threads_per_rank": 1}))))
        self.assertFalse(wc.Conductor._traces_execution(mock.Mock(target=HYBRID)))
        self.assertFalse(wc.Conductor._traces_execution(mock.Mock(target=MPI)))
        with tempfile.TemporaryDirectory() as t:
            v: list[str] = []
            with mock.patch.object(vps, "_pipeline_target", return_value=HYBRID):
                vps._validate_device_kernel_execution(
                    Path(t), mock.Mock(), Path(t) / "src", v)
            self.assertEqual(v, [])


class HostPrerequisiteCombinationTests(unittest.TestCase):
    """The driver-side probes: a dispatch site that still asked the whole value for a record
    would raise `BackendNotExtracted` here, at every start of a combination target."""

    def test_the_wrapper_is_attributed_to_its_member(self) -> None:
        selection = hp.resolve_launch_axis_selection(HYBRID)
        self.assertEqual(selection["parallel"], "mpi+openmp")
        self.assertEqual(selection["build_compiler"],
                         hp.resolve_launch_axis_selection(MPI)["build_compiler"])
        hybrid = hp.required_host_executables(selection)
        mpi = hp.required_host_executables(hp.resolve_launch_axis_selection(MPI))
        self.assertEqual(hybrid, mpi)
        self.assertIn("parallel", {item.axis for item in hybrid})
        self.assertEqual({item.backend_id for item in hybrid if item.axis == "parallel"},
                         {"mpi"})
        self.assertEqual(hp.execution_executables(selection),
                         hp.execution_executables(hp.resolve_launch_axis_selection(MPI)))

    def test_the_three_probes_run(self) -> None:
        selection = hp.resolve_launch_axis_selection(HYBRID)
        mpi = hp.resolve_launch_axis_selection(MPI)
        self.assertEqual(hp.missing_host_executables(selection), hp.missing_host_executables(mpi))
        self.assertEqual([(v.axis, v.backend_id, v.executable)
                          for v in hp.unsupported_host_tool_versions(selection)],
                         [(v.axis, v.backend_id, v.executable)
                          for v in hp.unsupported_host_tool_versions(mpi)])
        # Not launched, so no rank count is started: the binding canary alone, which this host
        # may or may not resolve; the answer has the shape the one-member value's has.
        with mock.patch.object(hp, "_canary_run", return_value="skipped") as run:
            hybrid_found = hp.parallel_toolchain_problems(selection, launches=False, ranks=4)
            mpi_found = hp.parallel_toolchain_problems(mpi, launches=False, ranks=4)
        self.assertEqual([p.replace("mpi+openmp", "mpi") for p in hybrid_found], mpi_found)
        self.assertLessEqual(run.call_count, 2)


class PresenceFloorCombinationTests(unittest.TestCase):
    """Both floors on a combination target, and what declines one (§5.1 of the plan): a plan
    declines a floor only when every member's `lowering_plan_declines` says so."""

    _H = "harness_fortran_cpu_mpi"
    #: A distributed problem through the harness, with a counted loop and no OpenMP directive.
    _LOOPS = ("module dep_base_model\n  use harness_fortran_cpu_mpi_model, only: "
              "harness_fortran_cpu_mpi__exchange_halo_r1\ncontains\n"
              "  subroutine dep_base__step(u, n)\n    integer, intent(in) :: n\n"
              "    real(8), intent(inout) :: u(:)\n    integer :: i\n"
              "    call harness_fortran_cpu_mpi__exchange_halo_r1(u, 1, 1, .true.)\n"
              "    do i = 1, n\n      u(i) = 2.0d0 * u(i)\n    end do\n"
              "  end subroutine\nend module\n")
    _OMP = _LOOPS.replace("    do i = 1, n\n", "    !$omp parallel do\n    do i = 1, n\n")
    _OPENMP_FIRED = "counted `do` loop(s) and not one"

    def _run(self, model: str, parallelization: object, *, backend: str = "mpi+openmp",
             residency: str = "distributed") -> list[str]:
        with tempfile.TemporaryDirectory() as t:
            repo_root = Path(t)
            install_target_profile(repo_root, profile_with(
                parallel={"backend": backend}, harness={"infrastructure_id": self._H}))
            node_key = "problem/dep_base@0.1.0"
            ir_ref = "workspace/ir/problem__dep_base__0.1.0/ir_20260601_001"
            (repo_root / ir_ref).mkdir(parents=True)
            (repo_root / ir_ref / "spec.ir.yaml").write_text(yaml.safe_dump(
                {"meta": {"spec_kind": "problem", "spec_id": "dep_base"}}), encoding="utf-8")
            from tools.tests.target_fixtures import FORTRAN_CPU
            pipeline_dir = (repo_root / "workspace/pipelines/problem__dep_base__0.1.0"
                            / FORTRAN_CPU.target_id / "p1")
            src_dir = pipeline_dir / "source" / "src_20260601_001" / "src"
            src_dir.mkdir(parents=True)
            (pipeline_dir / "lineage.json").write_text(
                json.dumps({"node_key": node_key, "ir_ref": ir_ref}), encoding="utf-8")
            (src_dir.parent / "codegen_bundle.json").write_text(json.dumps(
                {"target_lowering_plan": {"precision": {}, "state_residency": residency,
                                          "parallelization": parallelization}}),
                encoding="utf-8")
            model_path = src_dir / "dep_base_model.f90"
            model_path.write_text(model, encoding="utf-8")
            v: list[str] = []
            vps._validate_parallel_presence_floor(
                repo_root, vps._stub_execution(pipeline_dir, node_key), src_dir, [model_path], v)
            return v

    def test_naming_one_member_does_not_decline_the_other_members_floor(self) -> None:
        for parallelization in ({"model": "mpi"}, {"method": "mpi"}, {"model": "MPI ranks"},
                                {"model": "openmp"}, {"model": "mpi+openmp"},
                                {"model": "mpi_openmp"}, {}):
            with self.subTest(parallelization=parallelization):
                found = self._run(self._LOOPS, parallelization)
                self.assertEqual(len(found), 1, found)
                self.assertIn(self._OPENMP_FIRED, found[0])
                self.assertIn("parallel.backend includes openmp", found[0])
                self.assertEqual(self._run(self._OMP, parallelization), [])

    def test_a_plan_that_names_no_member_declines_the_plan_dependent_floor(self) -> None:
        # `none` is the declared decline; `hybrid` is the limit the rule keeps — a word that
        # names no member of the target is a declaration the reviewer judges, as on a
        # one-member target.
        for model in ("none", "hybrid"):
            with self.subTest(model=model):
                self.assertEqual(self._run(self._LOOPS, {"model": model}), [])

    def test_the_whole_node_floor_is_asked_whatever_the_plan(self) -> None:
        # Both orders: a plan that declines the plan-dependent floor must not skip the floors
        # after it, wherever the operator writes the members.
        for backend in ("mpi+openmp", "openmp+mpi"):
            for model in (self._OMP, self._LOOPS):
                with self.subTest(backend=backend, directive=model is self._OMP):
                    found = self._run(model, {"model": "none"}, backend=backend,
                                      residency="host")
                    self.assertEqual(len(found), 1, found)
                    self.assertIn("parallel.backend includes mpi", found[0])
                    direct = self._run(model.replace("contains", "  use mpi_f08\ncontains"),
                                       {"model": "none"}, backend=backend)
                    self.assertTrue(any("mpi_f08" in v for v in direct), direct)
        direct = self._run(self._OMP.replace("contains", "  use mpi_f08\ncontains"),
                           {"model": "mpi+openmp"})
        self.assertTrue(any("parallel.backend includes mpi" in v and "mpi_f08" in v
                            for v in direct), direct)
        bare = self._OMP.replace(
            "    call harness_fortran_cpu_mpi__exchange_halo_r1(u, 1, 1, .true.)\n", "")
        # A problem that exchanges nothing is not refused (issue #716 F-1, decision O7).
        self.assertEqual(self._run(bare, {"model": "mpi"}), [])

    def test_one_member_targets_are_unchanged(self) -> None:
        # The same source on an `openmp` target: a plan naming MPI is another model there, and
        # declines the floor as it did before.
        self.assertEqual(self._run(self._LOOPS, {"model": "mpi"}, backend="openmp",
                                   residency="host"), [])
        self.assertEqual(len(self._run(self._LOOPS, {"model": "openmp"}, backend="openmp",
                                       residency="host")), 1)
        # On an `mpi` target only the whole-node floor exists.
        self.assertEqual(self._run(self._LOOPS, {"model": "mpi"}, backend="mpi"), [])


class PromptCompositionTests(unittest.TestCase):
    """A combination's prompt: the neutral lead, then each member's section in member order."""

    def _compose(self, template_file: str, backend: str) -> str:
        template = (REPO_ROOT / "tools" / "prompt_templates" / template_file).read_text(
            encoding="utf-8")
        return ort._compose_parallel_fragments(template, template_file, backend)

    def test_the_lead_precedes_every_members_section(self) -> None:
        for template_file, section in (("workshop_generate_generate.txt", "target_lowering_floor"),
                                       ("workshop_generate_verify.txt",
                                        "review_target_scope")):
            stem = ort._fragment_template_stem(template_file)
            lead = ort._parallel_combination_lead(stem, "mpi+openmp", ("mpi", "openmp"))[section]
            mpi = registry.capability_module("parallel", "mpi", "prompt_fragments").fragments(
                stem)[section]
            openmp = registry.capability_module(
                "parallel", "openmp", "prompt_fragments").fragments(stem)[section]
            with self.subTest(template=template_file):
                composed = self._compose(template_file, "mpi+openmp")
                self.assertIn(f"{lead}\n\n{mpi}\n\n{openmp}", composed)
                self.assertIn("`mpi+openmp`", lead)
                self.assertIn("`mpi`, `openmp`", lead)
                self.assertNotIn("{{value}}", composed)
                self.assertNotIn("{{members}}", composed)
                reordered = self._compose(template_file, "openmp+mpi")
                self.assertIn(f"{openmp}\n\n{mpi}", reordered)

    def test_each_target_is_told_what_the_driver_hands_a_call(self) -> None:
        """Issue #727 (issue #724 PR-3, decision O4 (a)): the sentence on what the driver hands
        a call lives in the two base templates, neutral, with an exception clause that points at
        the parallel backend's paragraph. It reaches the producer and the model reviewer on
        every target kind — no parallel model, `openmp`, `mpi` and `mpi+openmp` — and where it
        is excepted (an `mpi` member) the composed prompt carries the paragraph that says the
        driver partitions the case."""
        sentence = {
            "workshop_generate_generate.txt":
                "The driver hands every call the whole case unless the paragraph for the "
                "target's parallel backend under rule 7 says it partitions the case",
            "workshop_generate_verify.txt":
                "The driver hands every call the whole case unless a paragraph for this "
                "target's parallel backend at the end of this scope says it partitions the case",
        }
        partitions = {"workshop_generate_generate.txt": "The host-rendered driver distributes "
                                                         "each case",
                      "workshop_generate_verify.txt": "On this target the host-rendered driver "
                                                      "distributes each case"}
        for template_file, text in sentence.items():
            for backend in ("none", "openmp", "mpi", "mpi+openmp"):
                with self.subTest(template=template_file, backend=backend):
                    composed = self._compose(template_file, backend)
                    self.assertEqual(composed.count(text), 1)
                    self.assertEqual(partitions[template_file] in composed,
                                     "mpi" in backend.split("+"))
        # The base sentence is the ONE statement of the whole-case default: an `openmp`
        # paragraph that restated it would be false on `mpi+openmp`, composed beside the `mpi`
        # one (decision O4; round 1 found the `openmp` reviewer paragraph saying it).
        for stem in ("generate_generate", "generate_verify"):
            for name, body in registry.capability_module(
                    "parallel", "openmp", "prompt_fragments").fragments(stem).items():
                with self.subTest(stem=stem, section=name):
                    self.assertNotIn("whole case", body)

    def test_the_reviewer_finds_a_region_pinned_to_one_thread(self) -> None:
        """Issue #724, after PR-3 (`generate-verify-16`, `-17`): a region pinned to one thread or
        switched off on the tested cases makes no returned value wrong, and hides a race from
        the thread comparison, so the `openmp` reviewer paragraph names it as a finding. It
        reaches the reviewer on every target with an `openmp` member, and the base template's
        "Not findings" list leaves room for it. `generate-verify-17` (after R-V): the region is one
        switched off on EVERY tested case, and the paragraph says an `if(...)` clause true on one
        tested case is not the finding: R-V's replay rejected a correct source on that reading."""
        sentence = ("One construct is a finding here although it makes no value wrong: a region "
                    "pinned to one thread or switched off on every case the tests run")
        for backend in ("none", "openmp", "mpi", "mpi+openmp"):
            with self.subTest(backend=backend):
                composed = self._compose("workshop_generate_verify.txt", backend)
                self.assertEqual(composed.count(sentence), int("openmp" in backend.split("+")))
                if "openmp" in backend.split("+"):
                    for construct in ("`num_threads(1)`", "`omp_set_num_threads(1)`",
                                      "an `if(...)` clause false on all of those cases",
                                      "an `if(...)` clause true on at least one case the tests "
                                      "run"):
                        self.assertIn(construct, composed)
                self.assertIn("and one that a paragraph for this target's parallel backend "
                              "names)", composed)

    def test_a_one_member_value_composes_as_before(self) -> None:
        for template_file in ("workshop_generate_generate.txt", "workshop_generate_verify.txt"):
            stem = ort._fragment_template_stem(template_file)
            for backend in ("openmp", "mpi"):
                with self.subTest(template=template_file, backend=backend):
                    sections = registry.capability_module(
                        "parallel", backend, "prompt_fragments").fragments(stem)
                    template = (REPO_ROOT / "tools" / "prompt_templates" / template_file
                                ).read_text(encoding="utf-8")
                    expected = ort._PARALLEL_FRAGMENT_RE.sub(
                        lambda m, s=sections: s[m.group(1)], template)
                    self.assertEqual(self._compose(template_file, backend), expected)
                    self.assertNotIn("This target's `parallel.backend`",
                                     self._compose(template_file, backend))

    def test_a_member_without_fragments_contributes_nothing(self) -> None:
        stem = "generate_generate"
        mpi = registry.capability_module("parallel", "mpi", "prompt_fragments").fragments(stem)
        composed = self._compose("workshop_generate_generate.txt", "mpi+none")
        self.assertIn(mpi["target_lowering_floor"], composed)
        self.assertIn("`mpi`, `none`", composed)

    def test_a_lead_that_defines_no_section_for_the_marker_raises(self) -> None:
        # An empty lead must not compose the members alone, and a lead naming another section
        # must not surface as a KeyError the caller misreports.
        for sections in ({}, {"other": "x"}):
            with self.subTest(sections=sections), \
                    mock.patch.object(ort, "_parallel_combination_lead",
                                      return_value=sections), \
                    self.assertRaises(ValueError) as caught:
                self._compose("workshop_generate_generate.txt", "mpi+openmp")
            self.assertIn("combination lead defines no fragment", str(caught.exception))

    def test_a_missing_lead_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ort, "_PARALLEL_COMBINATION_LEAD_DIR", Path(tmp)), \
                self.assertRaises(ValueError):
            self._compose("workshop_generate_verify.txt", "mpi+openmp")


if __name__ == "__main__":
    unittest.main()
