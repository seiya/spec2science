"""The MPI parallel backend package (`tools/backends/parallel/mpi/`, issue #316 R4-c PR-1).

What the neutral core reads from it through the registry: the launcher's prefix and programs,
the wrapper and the argv it replaces, the binding canary the launch probe compiles, and the
(empty) launch environment. The seams that compose these are tested beside them
(`test_host_execution.py`, `test_host_prerequisites.py`, `test_build_runtime.py`).
"""

from __future__ import annotations

import unittest

from tools.backends import registry
from tools.backends.parallel.mpi import (
    directives,
    execution,
    launcher,
    prompts,
    wrapper,
)


class RecordTests(unittest.TestCase):
    def test_the_record_declares_its_five_capabilities(self) -> None:
        record = registry.get("parallel", "mpi")
        self.assertEqual(record.backend_provides,
                         frozenset({"execution_env", "launcher", "compiler_wrapper",
                                    "parallel_directives", "prompt_fragments"}))
        self.assertEqual(record.core_provides, frozenset())
        # Each capability resolves to the submodule its CAPABILITY_MODULE_ATTR row names.
        for capability, module in (("execution_env", execution), ("launcher", launcher),
                                   ("compiler_wrapper", wrapper),
                                   ("parallel_directives", directives),
                                   ("prompt_fragments", prompts)):
            with self.subTest(capability=capability):
                self.assertIs(registry.capability_module("parallel", "mpi", capability), module)


class LauncherTests(unittest.TestCase):
    def test_the_prefix_carries_the_rank_count_it_is_given(self) -> None:
        for ranks in (1, 2, 4, 48):
            with self.subTest(ranks=ranks):
                prefix = launcher.argv_prefix(ranks)
                self.assertEqual(prefix, (launcher.EXECUTABLE, "-n", str(ranks)))

    def test_a_count_that_is_not_a_positive_int_is_refused(self) -> None:
        for bad in (0, -1, True, False, 2.0, "4", None):
            with self.subTest(ranks=bad), self.assertRaises(ValueError):
                launcher.argv_prefix(bad)  # type: ignore[arg-type]

    def test_the_programs_and_the_probe_are_the_launcher(self) -> None:
        self.assertEqual(launcher.EXECUTABLES, (launcher.EXECUTABLE,))
        self.assertEqual(launcher.RUNTIME_PROBE[0], launcher.EXECUTABLE)


class LaunchCanaryTests(unittest.TestCase):
    """`launch_canary_problem`: one run of the rank count it is asked, and nothing else, reads as
    one run — `LAUNCH_CANARY_RANKS` at the local startup probe, the profile's ranks and 1 (the
    bare start) in a remote `Build` job's canary (issue #337)."""

    def _out(self, *sizes: object, noise: str = "") -> str:
        return noise + "".join(f"spec2science-mpi-size {n}\n" for n in sizes)

    def test_one_run_of_the_canary_count_passes_with_runtime_noise_around_it(self) -> None:
        n = launcher.LAUNCH_CANARY_RANKS
        self.assertGreater(n, 1, "a canary of one process cannot tell one run from several")
        self.assertIsNone(launcher.launch_canary_problem(0, self._out(*[n] * n), n))
        self.assertIsNone(launcher.launch_canary_problem(
            0, self._out(*[n] * n, noise="Authorization required, but no protocol\n"), n))

    def test_the_count_is_the_callers(self) -> None:
        self.assertIsNone(launcher.launch_canary_problem(0, self._out(4, 4, 4, 4), 4))
        self.assertIsNone(launcher.launch_canary_problem(0, self._out(1), 1))
        for out, ranks in ((self._out(2, 2), 4), (self._out(4, 4, 4, 4), 1),
                           (self._out(1, 1, 1, 1), 4), (self._out(2), 1)):
            with self.subTest(out=out, ranks=ranks):
                self.assertIsNotNone(launcher.launch_canary_problem(0, out, ranks))

    def test_every_other_shape_is_a_problem(self) -> None:
        n = launcher.LAUNCH_CANARY_RANKS
        for rc, out, needle in (
                (0, self._out(*[1] * n), "reported run sizes ['1', '1']"),
                (0, self._out(n), "reported run sizes ['2']"),
                (0, self._out(*[n] * (n + 1)), "reported run sizes"),
                (0, "", "reported run sizes none"),
                (0, "spec2science-mpi-size 2 extra\nspec2science-mpi-size 2\n", "reported run sizes"),
                (1, self._out(*[n] * n), "the launch exited 1")):
            with self.subTest(rc=rc, out=out):
                problem = launcher.launch_canary_problem(rc, out, n)
                self.assertIsNotNone(problem)
                self.assertIn(needle, problem)


def _working_pair() -> str | None:
    """A directory holding a wrapper and a launcher of one installation that compile the
    binding canary, or None. Searched on PATH, then in /usr/bin (where a distribution installs
    them); a development host may have several installations."""
    import os
    import shutil
    import subprocess
    import tempfile
    for directory in [*os.environ.get("PATH", "").split(os.pathsep), "/usr/bin"]:
        exe = shutil.which(wrapper.COMPILER_WRAPPER, path=directory)
        if exe is None or shutil.which(launcher.EXECUTABLE, path=directory) is None:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            src = f"{tmp}/{wrapper.BINDING_CANARY_FILENAME}"
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(wrapper.BINDING_CANARY_SOURCE)
            argv = [exe, *(a.format(scratch=tmp, source=src)
                           for a in wrapper.BINDING_CANARY_ARGV)]
            if subprocess.run(argv, capture_output=True, check=False, timeout=120).returncode == 0:
                return directory
    return None


_PAIR = _working_pair()


def _require_mpi_pair() -> str:
    """The directory `_working_pair` found, or a failure that says what to install (#498: a
    missing dependency fails; CI installs one)."""
    if _PAIR is None:
        raise AssertionError(
            "no MPI installation whose wrapper compiles the binding canary was found on PATH or "
            f"in /usr/bin; install an MPI whose {wrapper.COMPILER_WRAPPER} compiles a program "
            "using the binding module with this host's compiler — or, if one is installed, "
            "install the compiler its wrapper calls (docs/RUNBOOK.md#0-1, "
            "docs/backends/parallel/mpi/LAUNCHER.md §3)")
    return _PAIR


class RealToolchainCanaryTests(unittest.TestCase):
    """The canary sources are this package's Fortran, and nothing else compiles them: run them
    with a real installation, so a source that stops compiling (or stops printing what
    `launch_canary_problem` reads) fails here rather than refusing every launch."""

    @classmethod
    def setUpClass(cls) -> None:
        _require_mpi_pair()

    def test_the_launch_canary_builds_and_reads_as_one_run(self) -> None:
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = f"{tmp}/{launcher.LAUNCH_CANARY_FILENAME}"
            exe = f"{tmp}/canary"
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(launcher.LAUNCH_CANARY_SOURCE)
            build = subprocess.run(
                [f"{_PAIR}/{wrapper.COMPILER_WRAPPER}",
                 *(a.format(scratch=tmp, source=src, exe=exe)
                   for a in launcher.LAUNCH_CANARY_BUILD_ARGV)],
                capture_output=True, text=True, check=False, timeout=120, cwd=tmp)
            self.assertEqual(build.returncode, 0, build.stderr)
            prefix = list(launcher.argv_prefix(launcher.LAUNCH_CANARY_RANKS))
            prefix[0] = f"{_PAIR}/{prefix[0]}"
            run = subprocess.run([*prefix, exe], capture_output=True, text=True, check=False,
                                 timeout=120, cwd=tmp, stdin=subprocess.DEVNULL)
            self.assertIsNone(launcher.launch_canary_problem(
                run.returncode, run.stdout, launcher.LAUNCH_CANARY_RANKS), run.stdout + run.stderr)
            # Started without the launcher it is one process: the quality check's premise.
            alone = subprocess.run([exe], capture_output=True, text=True, check=False,
                                   timeout=120, cwd=tmp, stdin=subprocess.DEVNULL)
            self.assertIsNone(launcher.launch_canary_problem(alone.returncode, alone.stdout, 1),
                              alone.stdout + alone.stderr)


class WrapperTests(unittest.TestCase):
    def test_wrap_replaces_argv0_and_keeps_the_rest(self) -> None:
        argv = ["gfortran", "-fsyntax-only", "-std=f2008", "a.f90"]
        self.assertEqual(wrapper.wrap(argv),
                         (wrapper.COMPILER_WRAPPER, "-fsyntax-only", "-std=f2008", "a.f90"))
        self.assertEqual(argv[0], "gfortran", "wrap must not mutate its input")

    def test_an_empty_argv_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            wrapper.wrap([])

    def test_the_wrapped_compiler_is_a_registered_syntax_adapter(self) -> None:
        # The build-runtime server compares a stage's compiler id against this value; one that
        # names no adapter would silently never wrap.
        self.assertTrue(registry.provides("compiler", wrapper.WRAPPED_COMPILER, "syntax_check"))

    def test_the_canary_uses_the_binding_and_its_argv_takes_the_two_placeholders(self) -> None:
        self.assertIn("use mpi_f08", wrapper.BINDING_CANARY_SOURCE)
        filled = [a.format(scratch="S", source="F") for a in wrapper.BINDING_CANARY_ARGV]
        self.assertIn("S", filled)
        self.assertEqual(filled[-1], "F")
        self.assertTrue(wrapper.BINDING_CANARY_FILENAME.endswith(".f90"))
        self.assertEqual(wrapper.SHOW_ARGV[0], wrapper.COMPILER_WRAPPER)


class ExecutionTests(unittest.TestCase):
    def test_no_environment_and_the_thread_count_is_validated(self) -> None:
        self.assertEqual(execution.environment(1), {})
        for bad in (0, True, 1.0):
            with self.subTest(threads=bad), self.assertRaises(ValueError):
                execution.environment(bad)  # type: ignore[arg-type]

    def test_an_imposed_process_grid_is_the_variable_the_harness_reads(self) -> None:
        """Issue #512: the grid a profile imposes travels as `PROCESS_GRID_VARIABLE`, spelled
        `p1,p2` as `harness_fortran_cpu_mpi` §3.3 parses it; a malformed grid is refused."""
        self.assertEqual(execution.PROCESS_GRID_VARIABLE, "SPEC2SCIENCE_PROCESS_GRID")
        self.assertEqual(execution.environment(1, process_grid=(2, 3)),
                         {"SPEC2SCIENCE_PROCESS_GRID": "2,3"})
        self.assertEqual(execution.environment(1, process_grid=None), {})
        for bad in ((2,), (2, 0), (2.0, 2), (True, 2), "2,2", (1, 2, 3)):
            with self.subTest(grid=bad), self.assertRaises(ValueError):
                execution.environment(1, process_grid=bad)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()


class PresenceFloorTests(unittest.TestCase):
    """`directives` (R4-c PR-4): the floor is stated for Fortran on a CPU only, as a whole-node
    judgment; the validator's rows (`test_validate_pipeline_semantics.MpiPresenceFloorGateTests`)
    drive it through `_validate_parallel_presence_floor`."""

    def test_the_floor_is_stated_for_fortran_on_a_cpu_alone(self) -> None:
        floor = directives.presence_floor(language="fortran", hardware_class="cpu")
        self.assertIsNotNone(floor)
        self.assertTrue(callable(floor.node_violations))
        for language, hardware_class in (("cuda_cpp", "cpu"), ("fortran", "gpu")):
            self.assertIsNone(directives.presence_floor(language=language,
                                                        hardware_class=hardware_class))

    def test_only_an_explicit_other_model_declines(self) -> None:
        for plan, declines in (
                (None, False), ({}, False), ({"parallelization": "none"}, False),
                ({"parallelization": {}}, False),
                ({"parallelization": {"model": 3}}, False),
                ({"parallelization": {"apply_to": "none"}}, False),
                ({"parallelization": {"model": "mpi"}}, False),
                ({"parallelization": {"model": "MPI_block_1d"}}, False),
                ({"parallelization": {"model": "none"}}, True),
                ({"parallelization": {"scheme": "serial"}}, True),
                ({"parallelization": {"model": "openmp"}}, True),
                ({"parallelization": {"model": "openmp", "kind": "mpi"}}, False)):
            with self.subTest(plan=plan):
                self.assertIs(directives.lowering_plan_declines(plan), declines)


class PromptFragmentTests(unittest.TestCase):
    def test_each_generate_template_has_its_section(self) -> None:
        self.assertEqual(set(prompts.fragments("generate_generate")), {"target_lowering_floor"})
        # The reviewer's marker is `review_target_scope` since issue #724 PR-3 (decision O3 (a)).
        self.assertEqual(set(prompts.fragments("generate_verify")), {"review_target_scope"})
        with self.assertRaises(ValueError):
            prompts.fragments("compile_generate")

    def test_the_fragments_state_what_the_distributed_driver_does_and_leaves(self) -> None:
        """Issue #610 (plan B §3.4): the host-rendered driver decomposes, slices, gathers and
        makes the stop uniform, so the producer is told no decomposition of its own, no partition
        call and no declining plan, and is told what reaches its operations (the block's count,
        the case's through `derived`, the offset through `block.<axis>.first`) and the wall rule
        by global index; the reviewer is told what the driver does and what makes a returned
        value wrong. Neither says the node is refused, which it was until PR-B2.
        Issue #724 PR-3 (decision O3 (a)): the reviewer is spec-relative and holds no IR, so its
        paragraph names `tests.md`'s `distribute` line where it named the IR's
        `drive.distribute`, `derived` bindings and `block.<axis>.first`, and states the block's
        first global index in words; those IR names are pinned absent from it."""
        producer = prompts.fragments("generate_generate")["target_lowering_floor"]
        reviewer = prompts.fragments("generate_verify")["review_target_scope"]
        for text in (producer, reviewer):
            self.assertNotIn("driver_distributed_pending", text)
            self.assertNotIn("refused at preflight", text)
            self.assertNotIn("decomposition", text)
            self.assertNotIn("__partition(", text)
            self.assertIn("GLOBAL index", text)
        self.assertIn("block.<axis>.first", producer)
        self.assertIn("`derived` binding", producer)
        for ir_name in ("drive.", "derived", "block.<axis>", "spec.ir.yaml", "the IR"):
            self.assertNotIn(ir_name, reviewer)
        self.assertIn("partitions the spatial axes that `tests.md`'s `distribute` line names, "
                      "hands each call its rank's block", reviewer)
        self.assertIn("carries the block's count on each call", reviewer)
        self.assertIn("walls are decided by GLOBAL index — the block's first global index and "
                      "the case's count", reviewer)
        self.assertIn("a stencil read that crosses a partitioned axis with no halo exchange "
                      "before it", reviewer)
        self.assertIn("a refusal that precedes a collective and is not first made the same on "
                      "every rank by a reduction", reviewer)
        self.assertNotIn('"model": "none"', producer)
        self.assertIn("receives the BLOCK's count", producer)
        self.assertIn("the partition, the writers, the capture operations, the gathers", producer)
        self.assertIn("decides a wall by `__grid_coord`", reviewer)
        self.assertIn("partitions again", reviewer)
        # Round 1 of PR-B2: a sum over the ranks counts a cell once per rank holding it (a
        # replicated case, a block along a dimension no axis takes), and the kernel cannot tell
        # without the grid, so neither fragment offers a bare `__reduce_sum` for a whole-field
        # value nor a fix by the grid coordinate. Round 2: every cell is held by the same number
        # of ranks, so the reduced sum over the reduced count of the same cells is the field's
        # mean on every grid (`DistributedDriverEndToEndTest.test_a_whole_field_mean_divides_by_
        # the_reduced_count_of_the_cells_it_summed`): the producer is told that form and the
        # total from it, the reviewer's finding is the UN-normalised sum, and the round-1 "no
        # whole-field sum is formable" statement is gone from both.
        for text in (producer, reviewer):
            self.assertNotIn("coordinate 0", text)
            self.assertNotIn("not one a kernel can form", text)
            self.assertNotIn("sound only as a count compared against zero", text)
            self.assertNotIn("serves a count compared against zero", text)
        self.assertNotIn("goes through `<harness>__reduce_sum`", producer)
        self.assertIn("an extreme goes through `<harness>__reduce_max` / `__reduce_min`", producer)
        self.assertIn("a mean is the `<harness>__reduce_sum` of the block's sum divided by the "
                      "`<harness>__reduce_sum_int` of the block's cell count", producer)
        self.assertIn("a total is that mean times the case's cell count from a `derived` "
                      "binding", producer)
        self.assertIn("forms a whole-field sum or mean from an un-normalised reduced sum — a "
                      "`__reduce_sum` / `__reduce_sum_int` not divided by the `__reduce_sum_int` "
                      "of the cell count of the cells it summed", reviewer)
        self.assertIn("the reduced sum over the reduced count of the same cells is the field's "
                      "mean on every grid", reviewer)


class HarnessUseStatementTests(unittest.TestCase):
    """Issue #316 round 1: the prohibition on a physics source naming the harness was stated
    absolutely to the producer and the reviewer while the MPI rules required a harness `use`. Each
    statement now carries the exception, and this holds them to it: a reversal to the absolute
    form reddens it."""

    @staticmethod
    def _composed(key: str) -> str:
        from tools import orchestration_runtime as ort
        return ort._compose_fragments(
            ort._load_launch_prompt_templates()[key], ort._PROMPT_TEMPLATE_FILES[key],
            {"pure_language": "fortran", "pure_parallel": "mpi"})

    def test_the_producer_is_told_the_exception_where_it_is_told_the_rule(self) -> None:
        composed = self._composed("workshop generate.generate")
        opening = "(a) No source of yours may `use` the harness module"
        rule_a = composed[composed.index(opening):]
        rule_a = rule_a[:rule_a.index("(b) ")]
        self.assertIn("with ONE exception", rule_a)
        self.assertIn("parallel rules in rule (7) name harness operations", rule_a)
        # ...and rule (7), which it points at, precedes it and names the operations.
        self.assertLess(composed.index("For a `cpu` target whose `parallel.backend` is `mpi`"),
                        composed.index(opening))

    def test_the_reviewer_is_told_no_absolute_prohibition(self) -> None:
        """The checks contract and its Fortran binding, where the reviewer's two statements stood,
        went with the checks module; what a reviewer reads now must not bring the absolute form
        back beside the producer's exception."""
        composed = self._composed("workshop generate.verify").lower()
        for absolute in ("must not `use` the harness", "may not `use` the harness",
                         "never `use`s the harness", "no source may `use` the harness"):
            self.assertNotIn(absolute, composed)

    def test_the_rules_the_gate_is_documented_by_carry_the_exception(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        rules = (root / "docs/backends/language/fortran/GENERATE_RULES.md").read_text(
            encoding="utf-8")
        statement = rules[rules.index("- **Source isolation (every M3c node).**"):]
        statement = statement[:statement.index("\n")]
        self.assertIn("refuses a `use` of a harness module (a distributed harness's own operations "
                      "through `only:` excepted, `docs/backends/parallel/mpi/GENERATE_RULES.md`)",
                      statement)
        table = (root / "docs/backends/parallel/mpi/GENERATE_RULES.md").read_text(encoding="utf-8")
        self.assertIn("a physics `use` of the harness module without `only:`, or naming a harness "
                      "name other than the distributed-state operations", table)
