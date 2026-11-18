"""The bounds-checked second build and its `checked` row (issue #589 PR-0).

Three layers: the seam that resolves what the build is (`tools/checked_build.py`, and the `make`
backend's `checked_build_overrides`), the Build that makes it (`Conductor._build_inproc`, its
deliverables), and a real `make` reading the composed variables. (The post-execute gate's
re-binding of the row to the binary went with issue #643: Build and Validate.execute are the
conductor's, and the row's comparison is `_author_quality_check`'s.) Validate.execute's run of the row is pinned beside the
other shapes in `test_workflow_conductor.py` and, at a site, `test_execute_at_site.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.workflow_conductor as wc
from tools import checked_build
from tools.backends import registry
from tools.backends.build_system.make import execute
from tools.orchestration_runtime import _target_toolchain_identity
from tools.target_profile import load_target_profile
from tools.tests.llm_samples import sample_config_with as _cfg
from tools.tests.target_fixtures import (
    FORTRAN_CPU,
    TARGET_ID,
    profile_with,
)
from tools.tests.test_workflow_conductor import _build_runtime, _TargetedConductor

REPO = Path(__file__).resolve().parents[2]
PROFILES = {t: load_target_profile(REPO, t) for t in ("fortran_cpu", "fortran_cpu_mpi",
                                                      "cpp_gpu")}
MPI = PROFILES["fortran_cpu_mpi"]
GPU = PROFILES["cpp_gpu"]


def _conductor_tc(profile) -> dict[str, str]:
    """`Conductor._read_toolchain`'s shape for `profile`."""
    tc = profile.toolchain
    return {"language": str(tc["language"]), "standard": str(tc["standard"]),
            "build_system": str(tc["build_system"]), "compiler": str(tc.get("compiler") or ""),
            "backend": profile.parallel_backend}


class SeamTests(unittest.TestCase):
    """What the build is, resolved once for its three readers."""

    def test_each_checked_in_profile_resolves_its_compiler_and_flags(self) -> None:
        """The compiler is the compiler AXIS value — under the MPI backend's wrapper the
        compiler the wrapper runs, not the wrapper program the control file names — and the
        same one the build key records (`_target_toolchain_identity`)."""
        expected = {"fortran_cpu": ("gfortran", ("-fcheck=bounds",)),
                    "fortran_cpu_mpi": ("gfortran", ("-fcheck=bounds",)),
                    "cpp_gpu": ("nvcc", ())}
        for target_id, profile in PROFILES.items():
            with self.subTest(target=target_id):
                self.assertEqual((checked_build.target_compiler(profile),
                                  checked_build.checked_flags_for(profile)),
                                 expected[target_id])
                self.assertEqual(checked_build.target_compiler(profile),
                                 _target_toolchain_identity(profile, probe=False)["compiler"])
        # The program the MPI control file names is the wrapper, which no compiler record is.
        self.assertNotEqual(wc.build_compiler(_conductor_tc(MPI)),
                            checked_build.target_compiler(MPI))

    def test_a_pin_that_is_not_the_language_s_default_decides(self) -> None:
        """The profile's `toolchain.compiler` pin, not the language's default, names the
        compiler whose flags apply: every checked-in profile pins its language's default, so
        none of them can tell the two apart (round 0: ignoring the pin survived)."""
        pinned = profile_with(toolchain={**FORTRAN_CPU.toolchain, "compiler": "nvcc"})
        self.assertNotEqual(pinned.toolchain["compiler"],
                            checked_build.target_compiler(FORTRAN_CPU))
        self.assertEqual(checked_build.target_compiler(pinned), "nvcc")
        self.assertEqual(checked_build.checked_flags_for(pinned), ())
        self.assertIsNone(checked_build.checked_shape(pinned))

    def test_the_flag_rules_are_the_control_file_s(self) -> None:
        """`control_file_rules` hands the language's `rules` what the conductor's control-file
        writer hands it, so the gate's expected flag string is the rendered one."""
        for target_id, profile in PROFILES.items():
            with self.subTest(target=target_id):
                arch = (profile.doc.get("hardware") or {}).get("architecture")
                self.assertEqual(
                    checked_build.control_file_rules(profile),
                    wc.Conductor._control_file_rules(_conductor_tc(profile),
                                                     str(arch) if arch else None))

    def test_a_compiler_that_does_not_declare_the_capability_is_refused(self) -> None:
        """An absent declaration is not an empty one: the build and the gate refuse rather than
        skip the row."""
        record = registry._BACKENDS[("compiler", "gfortran")]
        stripped = record._replace(backend_provides=record.backend_provides - {"checked_flags"})
        with mock.patch.dict(registry._BACKENDS, {("compiler", "gfortran"): stripped}):
            with self.assertRaises(checked_build.CheckedBuildUnavailable) as caught:
                checked_build.checked_flags_for(FORTRAN_CPU)
            self.assertIn("checked_flags", str(caught.exception))
            with self.assertRaises(checked_build.CheckedBuildUnavailable):
                checked_build.checked_shape(FORTRAN_CPU)

    def test_the_checked_shape(self) -> None:
        """One process, one thread, no imposed grid, the certified block size; None when the
        compiler declares no flags."""
        shape = checked_build.checked_shape(MPI)
        self.assertEqual((shape.label, shape.threads_per_rank, shape.ranks, shape.process_grid,
                          shape.block_size), ("checked", 1, 1, None, None))
        gfortran_on_a_block = profile_with(GPU, toolchain={**GPU.toolchain,
                                                           "language": "fortran",
                                                           "compiler": "gfortran"})
        self.assertEqual(checked_build.checked_shape(gfortran_on_a_block).block_size,
                         GPU.block_size)
        self.assertIsNone(checked_build.checked_shape(GPU))
        self.assertNotIn(checked_build.CHECKED_SHAPE_LABEL,
                         {s.label for p in PROFILES.values() for s in p.execution_shapes})

    def test_the_overrides_append_the_flags_to_the_control_file_s_string_verbatim(self) -> None:
        """The flag string is `rules['flags']` byte for byte — the module-directory pair with
        the object-directory reference unexpanded — followed by the flags; the directories
        are the checked build's own."""
        rules = checked_build.control_file_rules(FORTRAN_CPU)
        out = execute.checked_build_overrides("/o", "/b", "x_runner", rules, ("-fA", "-fB"))
        self.assertEqual(out[:3], execute.build_overrides("/o", "/b", "x_runner"))
        self.assertEqual(out[3], f"{rules['flags_variable']}={rules['flags']} -fA -fB")
        self.assertEqual(len(out), 4)
        # The variable and the string are the mapping's, not a spelling of this function.
        self.assertEqual(
            execute.checked_build_overrides("/o", "/b", "x", {"flags_variable": "ZZ",
                                                              "flags": "q r"}, ("-s",))[3],
            "ZZ=q r -s")
        for empty in ((), ("",), (" ",)):
            with self.subTest(empty=empty), self.assertRaises(ValueError):
                execute.checked_build_overrides("/o", "/b", "x", rules, empty)


def _refs(**over) -> wc.NodeRefs:
    return wc.NodeRefs(target_id=TARGET_ID, node_key="component/spec_x@0.1.0",
                       spec_path="spec/component/spec_x", ir_id="x_1", pipeline_id="x_1",
                       source_id="src_1", binary_id="bin_1", **over)


class BuildTests(unittest.TestCase):
    """`Conductor._build_inproc` makes the checked binary as a second deliverable."""

    def _build(self, repo: Path, *, target=None, checked_ok: bool = True,
               checked_writes: bool = True, certified_writes: bool = True):
        build_runtime = _build_runtime()
        c = _TargetedConductor(repo_root=repo, orchestration_id="t",
                               orchestration_agent_run_id="x", llm_config=_cfg("claude"), env={},
                               target_profile=target)
        refs = _refs()
        (repo / refs.ir_ref).mkdir(parents=True, exist_ok=True)
        (repo / refs.source_dir() / "src").mkdir(parents=True, exist_ok=True)
        calls: list[dict] = []
        staged: list[Path] = []
        real_stage = wc.Conductor._stage_dependency_sources

        def spy_stage(self_, refs_, obj_dir, *, phase):
            staged.append(Path(obj_dir))
            return real_stage(self_, refs_, obj_dir, phase=phase)

        def fake_compile(args):
            calls.append(args)
            overrides = dict(a.split("=", 1) for a in args["extra_args"])
            checked = len(calls) == 2
            if (checked_writes if checked else certified_writes):
                Path(overrides["BINDIR"]).mkdir(parents=True, exist_ok=True)
                (Path(overrides["BINDIR"]) / overrides["BIN"]).write_text("bin\n")
            ok = checked_ok if checked else True
            return {"ok": ok, "return_code": 0 if ok else 2, "command_id": f"cid{len(calls)}",
                    "command": ["make", "-j1", *args["extra_args"]],
                    "stderr": "" if ok else "checked: Error: boom"}

        with mock.patch.object(build_runtime, "tool_compile_project", fake_compile), \
                mock.patch.object(wc.Conductor, "_stage_dependency_sources", spy_stage), \
                mock.patch.object(subprocess, "run",
                                  return_value=subprocess.CompletedProcess([], 0, "", "")):
            out = c._build_inproc(refs, "child-1")
        meta = json.loads((repo / refs.binary_dir() / "binary_meta.json").read_text())
        return c, refs, out, meta, calls, staged

    def test_the_second_build_is_the_control_file_s_flags_plus_the_compiler_s(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            c, refs, out, meta, calls, staged = self._build(repo)
            self.assertEqual(out["returncode"], 0)
            self.assertEqual(meta["verification_status"], "pass", meta)
            self.assertEqual(len(calls), 2)
            obj = repo / "workspace" / "tmp" / "child-1" / "build"
            obj_checked = obj.parent / "build_checked"
            bin_checked = repo / refs.binary_dir() / "bin_checked"
            target = c.target
            expected = list(execute.checked_build_overrides(
                str(obj_checked), str(bin_checked), "spec_x_runner",
                checked_build.control_file_rules(target),
                checked_build.checked_flags_for(target)))
            # The library variables (issue #644) lead; the tail is the checked overrides.
            self.assertEqual(calls[1]["extra_args"], [*execute.library_overrides("", ""),
                                                      *expected])
            self.assertEqual(calls[0]["extra_args"], [
                *execute.library_overrides("", ""), *execute.build_overrides(
                    str(obj), str(repo / refs.binary_dir() / "bin"), "spec_x_runner")])
            # The closure is staged into each object directory, the certified one first.
            self.assertEqual(staged, [obj, obj_checked])
            self.assertTrue(any(obj_checked.iterdir()), "nothing staged into build_checked")
            self.assertEqual(sorted(p.name for p in obj_checked.iterdir()),
                             sorted(p.name for p in obj.iterdir()))
            self.assertEqual(meta["checked_build"], {
                "binary_ref": "binary/bin_1/bin_checked/spec_x_runner",
                "obj_dir": str(obj_checked), "bin_dir": str(bin_checked),
                "flags": list(checked_build.checked_flags_for(target)),
                "command_id": "cid2",
                "argv": ["make", "-j1", *execute.library_overrides("", ""), *expected]})
            self.assertEqual(meta["binary_artifact_ref"], "binary/bin_1/bin/spec_x_runner")
            self.assertEqual(meta["command_id"], "cid1")

    def test_a_failed_checked_build_fails_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _c, _r, out, meta, calls, _s = self._build(Path(td), checked_ok=False,
                                                       checked_writes=False)
            self.assertEqual(out["returncode"], 0)  # a content failure, routed to Generate
            self.assertEqual(len(calls), 2)
            self.assertEqual(meta["verification_status"], "fail")
            self.assertIn("checked: Error: boom", meta["failure_excerpt"])
            self.assertIn("[checked build: the build above succeeded", meta["failure_excerpt"])

    def test_a_checked_build_that_left_no_binary_fails_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _c, _r, _o, meta, calls, _s = self._build(Path(td), checked_writes=False)
            self.assertEqual(len(calls), 2)
            self.assertEqual(meta["verification_status"], "fail")
            self.assertEqual(meta["failure_category"], execute.BINARY_MISSING[0])
            self.assertIn("bin_checked/spec_x_runner", meta["failure_excerpt"])

    def test_no_checked_build_after_a_failed_build(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _c, _r, _o, meta, calls, _s = self._build(Path(td), certified_writes=False)
            self.assertEqual(len(calls), 1)
            self.assertEqual(meta["verification_status"], "fail")
            self.assertIn("bin/spec_x_runner", meta["failure_excerpt"])
            self.assertNotIn("bin_checked", meta["failure_excerpt"])

    def test_a_compiler_that_declares_no_flags_builds_once_and_records_null(self) -> None:
        none_declared = profile_with(toolchain={**FORTRAN_CPU.toolchain})
        record = registry._BACKENDS[("compiler", "gfortran")]
        module = registry.capability_module("compiler", "gfortran", "checked_flags")
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(module, "CHECKED_FLAGS", ()):
            c, _r, _o, meta, calls, staged = self._build(Path(td), target=none_declared)
            self.assertIn("checked_flags", record.backend_provides)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(staged), 1)
            self.assertIsNone(meta["checked_build"])
            self.assertEqual(meta["verification_status"], "pass")
            self.assertFalse(c._builds_checked())

    def test_the_checked_binary_is_a_deliverable_when_the_compiler_declares_flags(self) -> None:
        refs = _refs()
        common = {"step": "build", "substep": None, "orchestration_id": "o",
                  "orchestration_agent_run_id": "a", "child_agent_run_id": "c",
                  "agent_model": "m", "workflow_mode": "dev", "exe_name": "spec_x_runner"}
        on = wc.build_launch_request(refs, checked_build=True, **common)["allowed_output_paths"]
        off = wc.build_launch_request(refs, **common)["allowed_output_paths"]
        bdir = refs.binary_dir()
        self.assertEqual(on, [f"{bdir}/bin/spec_x_runner", f"{bdir}/bin_checked/spec_x_runner",
                              f"{bdir}/binary_meta.json", f"{bdir}/command_log.jsonl"])
        self.assertEqual(off, [p for p in on if "/bin_checked/" not in p])
        with tempfile.TemporaryDirectory() as td:
            c = _TargetedConductor(repo_root=Path(td), orchestration_id="t",
                                   orchestration_agent_run_id="x", llm_config=_cfg("claude"),
                                   env={})
            self.assertTrue(c._builds_checked())
            gpu = _TargetedConductor(repo_root=Path(td), orchestration_id="t",
                                     orchestration_agent_run_id="x", llm_config=_cfg("claude"),
                                     env={}, target_profile=GPU)
            self.assertFalse(gpu._builds_checked())

    def test_the_runtime_admits_the_checked_deliverable_and_the_stamp_pins_it(self) -> None:
        """Round 1 (the blocker): the launch `run_phase` records for a declaring compiler's Build
        passes the runtime's own validation (`_validate_launch_request_payload`, which
        `record-launch` runs before the in-process body), and the step result's
        `required_outputs` carry the same path, so `_certifiable_artifact_refs` (the set
        `artifact_hashes` is taken over) pins it. Driven over a recorded real build request.
        (The phase-contract matcher that also refused a path one level deeper, or beside it under
        another name, went in issue #643.)"""
        import glob

        from tools.orchestration_runtime import (
            _certifiable_artifact_refs,
            _validate_launch_request_payload,
        )
        from tools.tests.test_workflow_conductor import _FIXTURE_DIR, _refs_from_request
        requests = [json.loads(Path(f).read_text()) for f in
                    sorted(glob.glob(str(_FIXTURE_DIR / "*.request.json")))]
        req = next(r for r in requests if isinstance(r, dict) and r.get("step") == "build"
                   and "allowed_output_paths" in r)
        refs = _refs_from_request(req)
        common = {"step": "build", "substep": req.get("substep"),
                  "orchestration_id": req["orchestration_id"],
                  "orchestration_agent_run_id": req["parent_agent_run_id"],
                  "child_agent_run_id": req["agent_run_id"], "agent_model": req["agent_model"],
                  "workflow_mode": req["workflow_mode"], "exe_name": f"{refs.spec_id}_runner"}
        built = wc.build_launch_request(refs, checked_build=True, **common)
        checked = f"{refs.binary_dir()}/bin_checked/{refs.spec_id}_runner"
        self.assertIn(checked, built["allowed_output_paths"])
        _validate_launch_request_payload(built)
        required = wc.phase_required_outputs(refs, "build", exe_name=f"{refs.spec_id}_runner",
                                             checked_build=True)
        def binaries(paths):
            return [p for p in paths if "/bin/" in p or "/bin_checked/" in p]

        self.assertEqual(binaries(required), binaries(built["allowed_output_paths"]))
        self.assertEqual(len(binaries(required)), 2)
        self.assertIn(checked, _certifiable_artifact_refs(
            required, f"{refs.binary_dir()}/binary_meta.json"))
        self.assertNotIn(checked, wc.phase_required_outputs(
            refs, "build", exe_name=f"{refs.spec_id}_runner"))


class RunPhaseCallSiteTests(unittest.TestCase):
    """Round 2: the two places the Conductor computes `checked_build` — the deterministic
    launch `run_substep` records and the step result `run_phase` writes — each carry the
    checked binary for a declaring compiler and leave it out for one that declares none.
    Driven through the real `run_phase` / `run_substep`, with the runtime's I/O stubbed (the
    rows above call `build_launch_request` and `phase_required_outputs` directly, which a
    `checked_build=False` at either call site survived)."""

    def _run(self, target):
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
            stub = _Stub(repo_root=Path(td), orchestration_id="t",
                         orchestration_agent_run_id="x", llm_config=_cfg("claude"), env={},
                         target_profile=target)
            refs = _refs(run_id="r1", source_binary_id="bin_1")
            outcome = stub.run_phase(refs, "build")
        self.assertEqual(outcome.status, "pass")
        (request,), (result,) = recorded, results
        checked = f"{refs.binary_dir()}/bin_checked/spec_x_runner"
        return (checked in request["allowed_output_paths"],
                checked in result["required_outputs"])

    def test_a_declaring_compiler_s_build_carries_the_checked_binary_at_both(self) -> None:
        self.assertEqual(self._run(FORTRAN_CPU), (True, True))

    def test_a_compiler_that_declares_no_flag_carries_it_at_neither(self) -> None:
        pinned = profile_with(toolchain={**FORTRAN_CPU.toolchain, "compiler": "nvcc"})
        self.assertEqual(self._run(pinned), (False, False))


class RepairExcerptTests(unittest.TestCase):

    def test_a_checked_row_that_timed_out_is_not_blamed_on_a_bounds_abort(self) -> None:
        """The excerpt names an out-of-bounds abort only for a run that exited on its own; a
        timeout (`error` set, no return code) is not attributed to one (round 2)."""
        def lines(failure):
            row = {"label": "checked", "status": "fail", "run_ok": False,
                   "run_failure": failure}
            with tempfile.TemporaryDirectory() as td:
                (Path(td) / "quality_check.json").write_text(json.dumps({"shapes": [row]}))
                return "\n".join(wc.Conductor._quality_check_failure_lines(Path(td)))

        aborted = lines({"return_code": 2, "error": None, "stderr_head": "Fortran runtime error"})
        timed_out = lines({"return_code": None, "error": "timeout: exceeded 3600 sec",
                           "stderr_head": ""})
        for text in (aborted, timed_out):
            self.assertIn("the checked row (the bounds-checked build", text)
            self.assertNotIn("the same binary", text)
        self.assertIn("declared bounds aborts", aborted)
        self.assertNotIn("declared bounds aborts", timed_out)
        self.assertIn("timeout: exceeded 3600 sec", timed_out)


class ExecuteRefusalTests(unittest.TestCase):

    def test_a_binary_with_no_checked_binary_is_a_host_refusal(self) -> None:
        """A declaring compiler's binary whose `bin_checked/<exe>` is absent is refused before
        anything runs, as the host's inconsistency (transport), not run as a missing program
        and routed to Generate (round 1: the refusal had no row)."""
        from tools.tests.test_workflow_conductor import _record_build_site
        build_runtime = _build_runtime()
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            c = _TargetedConductor(repo_root=repo, orchestration_id="t",
                                   orchestration_agent_run_id="x", llm_config=_cfg("claude"),
                                   env={})
            refs = _refs(run_id="run_1", source_binary_id="bin_1")
            (repo / refs.ir_ref).mkdir(parents=True)
            (repo / refs.ir_ref / "spec.ir.yaml").write_text(
                "case:\n  test_case_set:\n    - case_id: c_alpha\n", encoding="utf-8")
            (repo / refs.source_dir() / "src").mkdir(parents=True)
            (repo / refs.binary_dir() / "bin").mkdir(parents=True)
            (repo / refs.binary_dir() / "bin" / "spec_x_runner").write_text("x")
            _record_build_site(repo, refs)
            (repo / refs.binary_dir() / "bin_checked" / "spec_x_runner").unlink()
            ran: list = []
            with mock.patch.object(build_runtime, "tool_run_program",
                                   lambda args: ran.append(args) or {"ok": True}), \
                    self.assertRaises(RuntimeError) as caught:
                c._execute_inproc(refs, "child-1")
            self.assertIn("has no checked binary", str(caught.exception))
            self.assertIn("--rederive build,validate", str(caught.exception))
            self.assertEqual(ran, [])


class RealMakeTests(unittest.TestCase):
    """The composed variables as `make` reads them: the command-line flag string replaces the
    control file's `?=` value whole, and its unexpanded object-directory reference is expanded
    by make at use, to the checked build's own directory."""

    def test_the_flag_string_reaches_make_with_its_reference_expanded(self) -> None:
        # Fails rather than skips without the program: this suite skips only for what
        # GitHub-hosted CI cannot install (docs/DEVELOPMENT.md, round 2's Codex pass).
        self.assertIsNotNone(shutil.which("make"), "make is not installed: install it as "
                             "docs/backends/build_system/make/CONTROL_FILE.md §3 says")
        build_runtime = _build_runtime()
        rules = checked_build.control_file_rules(FORTRAN_CPU)
        var = rules["flags_variable"]
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir()
            (src / "Makefile").write_text(
                f"OBJDIR ?= .\nBINDIR ?= .\nBIN ?= x\n{var} ?= {rules['flags']}\n"
                f"all:\n\tmkdir -p $(OBJDIR) $(BINDIR)\n"
                f"\tprintf '%s\\n' '$({var})' > $(OBJDIR)/flags.txt\n"
                f"\ttouch $(BINDIR)/$(BIN)\n")
            obj, bin_ = Path(td) / "obj_checked", Path(td) / "bin_checked"
            result = build_runtime.tool_compile_project({
                "project_dir": str(src), "language": "fortran", "build_system": "make",
                "extra_args": list(execute.checked_build_overrides(
                    str(obj), str(bin_), "x", rules,
                    checked_build.checked_flags_for(FORTRAN_CPU))),
                "command_log_path": str(Path(td) / "log.jsonl")})
            self.assertTrue(result["ok"], result)
            flags = (obj / "flags.txt").read_text().strip()
            # `$(LIBINC)` (issue #644) is not on this command line, so make expands it empty.
            self.assertEqual(flags, f"{rules['flags']} -fcheck=bounds".replace(
                "$(OBJDIR)", str(obj)).replace("$(LIBINC)", ""))
            self.assertEqual(flags.count(f"-J{obj}"), 1)
            self.assertTrue((bin_ / "x").is_file())


if __name__ == "__main__":
    unittest.main()
