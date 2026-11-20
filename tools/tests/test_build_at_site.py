"""`Build` at an execution site (issue #333, PR-2): the conductor's wiring of
`tools/remote_execution.py` for the build.

The rows drive the production entry point — `Conductor._build_inproc`, and
`_run_deterministic_substep` where the routing of a failure is the subject — end to end through
the `ssh` / `scp` shims `tools/tests/test_remote_execution.py` uses: the "site" is a directory under
the test's temporary tree, reached by running the command string locally, and its PATH starts with
a directory holding a fake compiler that answers `--version` with a version no real host has, so
what the record says was answered at the site can only have come from there. Real `make` runs the
node's control file there. What is mocked is the post-build gate's process (every other
`subprocess.run` is the real one) and, in the local-site row, `tool_compile_project`.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import tools.workflow_conductor as wc
from tools.backends import registry
from tools.execution_sites import Site
from tools.host_execution import LOCAL_SITE
from tools.orchestration_runtime import (
    AUDIT_LOG_BASENAMES,
    _target_toolchain_identity,
    toolchain_version_argv,
)
from tools.tests.llm_samples import sample_config_with as _cfg
from tools.tests.target_fixtures import TARGET_ID, profile_with
from tools.tests.test_execute_at_site import _real_gate_free_run
from tools.tests.test_remote_execution import (
    _SBATCH_SHIM,
    _SCONTROL_SHIM,
    _SCP_SHIM,
    _SSH_SHIM,
    _reap_shim_jobs,
)
from tools.tests.test_workflow_conductor import _build_runtime, _TargetedConductor

#: The version the site's fake compiler answers: no real compiler prints it.
_SITE_VERSION = "ZZ Fortran (site build) 99.1.0"

#: The site's compiler: answers `--version`, fails naming the source by its absolute path when the
#: source says FAIL (as a real compiler does when handed one), and otherwise writes an executable
#: to its `-o` operand.
_FAKE_COMPILER = textwrap.dedent(f'''\
    #!/bin/sh
    if [ "$1" = --version ]; then echo "{_SITE_VERSION}"; exit 0; fi
    src=$1; out=
    while [ $# -gt 0 ]; do [ "$1" = -o ] && {{ out=$2; shift; }}; shift; done
    echo "compiling $PWD/$src"
    if grep -q FAIL "$src"; then echo "$PWD/$src:3:1: Error: no such thing" >&2; exit 1; fi
    printf '#!/bin/sh\\necho built at the site\\n' > "$out" && chmod +x "$out"
''')

#: The node's build control file: builds `$(BINDIR)/$(BIN)` from the node's source with `$(FC)`,
#: copies the staged dependency source into the object directory it was staged to, and writes
#: the flags variable as make expanded it there (issue #589 PR-0: the checked build's
#: command-line value carries an unexpanded object-directory reference).
_MAKEFILE = textwrap.dedent('''\
    FC := {compiler}
    $(BINDIR)/$(BIN): kernel.f90
    \tmkdir -p $(BINDIR) $(OBJDIR)
    \tcat $(OBJDIR)/depy_model.f90 > $(OBJDIR)/linked.txt
    \tprintf '%s\\n' '$({flags_variable})' > $(OBJDIR)/flags.txt
    \t$(FC) kernel.f90 -o $(BINDIR)/$(BIN)
''')


class _Node:
    """A scratch repository with one generated node, a shim-reached site, a fake compiler on the
    site's PATH, and a conductor for it."""

    def __init__(self, tmp: str, *, target=None, site: Site | None = None) -> None:
        self.root = Path(tmp)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.shims = self.root / "shims"
        self.shims.mkdir()
        for name, body in (("ssh", _SSH_SHIM), ("scp", _SCP_SHIM)):
            p = self.shims / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
        self.log = self.root / "shim.log"
        self.log.touch()
        self.workdir = self.root / "remote" / "jobs"
        self.workdir.mkdir(parents=True)
        self.site = site if site is not None else Site(
            site_id="box", executes=("cpu",), host="box", workdir=str(self.workdir))
        self.target = target if target is not None else profile_with()
        self.compiler = wc.build_compiler({
            "language": self.target.toolchain["language"], "standard": "",
            "build_system": self.target.toolchain["build_system"],
            "compiler": str(self.target.toolchain.get("compiler") or ""),
            "backend": self.target.parallel_backend})
        self.site_bin = self.root / "site_bin"
        self.site_bin.mkdir()
        fake = self.site_bin / self.compiler
        fake.write_text(_FAKE_COMPILER)
        fake.chmod(0o755)
        (self.site_bin / "hostname").write_text("#!/bin/sh\necho site-node-zz\n")
        (self.site_bin / "hostname").chmod(0o755)
        self.conductor = _TargetedConductor(
            repo_root=self.repo, orchestration_id="orch_1", orchestration_agent_run_id="x",
            llm_config=_cfg("claude"), env={}, target_profile=self.target, site=self.site)
        self.refs = wc.NodeRefs(
            target_id=TARGET_ID, node_key="component/spec_x@0.1.0",
            spec_path="spec/component/spec_x", ir_id="x_1", pipeline_id="x_1",
            source_id="src_1", binary_id="bin_1")
        (self.repo / self.refs.ir_ref).mkdir(parents=True)
        self.src = self.repo / self.refs.source_dir() / "src"
        self.src.mkdir(parents=True)
        from tools.checked_build import control_file_rules
        (self.src / "Makefile").write_text(_MAKEFILE.format(
            compiler=self.compiler,
            flags_variable=control_file_rules(self.target)["flags_variable"]), encoding="utf-8")
        (self.src / "kernel.f90").write_text("! the node's source\n", encoding="utf-8")
        # An audit log an earlier phase appended to: not shipped.
        (self.src / "command_log.jsonl").write_text('{"earlier": true}\n', encoding="utf-8")
        # A dependency source as staging leaves it in the object directory.
        self.obj = self.repo / "workspace" / "tmp" / "arid-1" / "build"
        self.obj.mkdir(parents=True)
        (self.obj / "depy_model.f90").write_text("! staged dependency\n", encoding="utf-8")
        # ... and in the bounds-checked build's own object directory (issue #589 PR-0), which
        # staging fills from the same bindings.
        self.obj_checked = self.obj.parent / "build_checked"
        self.obj_checked.mkdir(parents=True)
        (self.obj_checked / "depy_model.f90").write_text("! staged dependency\n",
                                                         encoding="utf-8")
        self.bin = self.repo / self.refs.binary_dir() / "bin"
        self.exe = self.bin / "spec_x_runner"
        self.job = f"{self.workdir}/orch_1/arid-1"

    def env(self, *, site_path: str | None = None, **knobs: str):
        path = site_path if site_path is not None else \
            f"{self.site_bin}{os.pathsep}{os.environ['PATH']}"
        env = {"PATH": f"{self.shims}{os.pathsep}{os.environ['PATH']}",
               "SHIM_LOG": str(self.log), "SHIM_SSH_PATH": path}
        env.update(knobs)
        return mock.patch.dict(os.environ, env)

    def build(self, **kw: str) -> dict:
        with self.env(**kw), mock.patch.object(
                subprocess, "run", side_effect=_real_gate_free_run(subprocess.run)):
            return self.conductor._build_inproc(self.refs, "arid-1")

    def substep(self, **kw: str):
        with self.env(**kw), mock.patch.object(
                subprocess, "run", side_effect=_real_gate_free_run(subprocess.run)):
            return self.conductor._run_deterministic_substep(
                self.refs, "build", None, "arid-1", {})

    def meta(self) -> dict:
        return json.loads((self.repo / self.refs.binary_dir() / "binary_meta.json").read_text())

    def log_entries(self) -> list[dict]:
        path = self.src / "command_log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]


class BuildAtARemoteSiteTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.n = _Node(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_binary_comes_home_and_the_record_says_where_it_was_built(self) -> None:
        n = self.n
        out = n.build()
        self.assertEqual(out["returncode"], 0, out)
        # The binary the SITE built is this node's binary.
        self.assertEqual(subprocess.run([str(n.exe)], capture_output=True, text=True,
                                        check=True).stdout, "built at the site\n")
        meta = n.meta()
        self.assertEqual(meta["verification_status"], "pass", meta)
        env = meta["environment"]
        self.assertEqual(env["build_site"]["site"], "box")
        self.assertEqual(env["build_site"]["remote_dir"], n.job)
        self.assertEqual(env["platform"]["site"], "box")
        self.assertEqual(env["platform"]["node"], "site-node-zz")
        # The compiler as the site answered it; the key's stays this host's.
        self.assertEqual(env["compiler_version"], _SITE_VERSION)
        self.assertEqual(meta["compiler_version"],
                         _target_toolchain_identity(n.target)["compiler_version"])
        self.assertNotEqual(meta["compiler_version"], _SITE_VERSION)
        # The checked binary came home beside it (issue #589 PR-0), and the site's make read
        # the flags variable from the quoted command line with the object-directory reference
        # expanded to the job's own checked object directory.
        self.assertTrue((n.bin.parent / "bin_checked" / "spec_x_runner").is_file())
        from tools.checked_build import checked_flags_for, control_file_rules
        collected = n.repo / "workspace" / "tmp" / "arid-1" / "site" / "collected"
        rules = control_file_rules(n.target)
        self.assertEqual(
            (collected / "build_checked" / "flags.txt").read_text().strip(),
            " ".join([rules["flags"], *checked_flags_for(n.target)]).replace(
                "$(OBJDIR)", f"{n.job}/build_checked").replace("$(LIBINC)", ""))
        self.assertEqual((collected / "build" / "flags.txt").read_text().strip(), "")
        # The checked build's output is localized too: the site's job-directory paths in it
        # name the local directories they stand for.
        log = (n.repo / n.refs.binary_dir() / "compile.stdout.log").read_text()
        checked_out = log.split("checked flags]\n", 1)[1]
        self.assertIn(f"compiling {n.src}/kernel.f90", checked_out)
        self.assertNotIn(n.job, checked_out)

    def test_the_log_entry_is_the_local_one_and_names_the_site_argv(self) -> None:
        n = self.n
        n.build()
        earlier, entry, checked = n.log_entries()
        self.assertEqual(earlier, {"earlier": True})
        self.assertEqual(entry["tool_name"], "compile_project")
        self.assertEqual(entry["cwd"], str(n.src))
        # `command` is what the local server would have run: the local paths.
        jobs = _build_runtime().default_build_jobs()
        self.assertEqual(entry["command"], _build_runtime().build_command(
            "make", None, jobs,
            # The library variables (issue #644) lead, empty: this closure declares none.
            ["LIBINC=", "LIBS=", f"OBJDIR={n.obj}", f"BINDIR={n.bin}", "BIN=spec_x_runner"]))
        self.assertEqual(entry["site"]["remote_command"], _build_runtime().build_command(
            "make", None, jobs,
            ["LIBINC=", "LIBS=", f"OBJDIR={n.job}/build", f"BINDIR={n.job}/bin",
             "BIN=spec_x_runner"]))
        self.assertEqual(entry["site"]["remote_cwd"], f"{n.job}/src")
        self.assertEqual(entry["timeout_sec"], _build_runtime().COMPILE_PROJECT_TIMEOUT_SEC)
        # The site's diagnostics are kept whole, as the local build keeps them.
        self.assertEqual(entry["capture_limit"], wc._FULL_CAPTURE_LIMIT)
        self.assertEqual(n.meta()["environment"]["build_site"]["host"], "box")
        self.assertEqual(n.meta()["command_id"], entry["command_id"])
        # The bounds-checked build (issue #589 PR-0) is the job's second command: its record
        # names the local directories too, with the site's beside them, and `checked_build`
        # records its command and argv.
        from tools.backends.build_system.make import execute
        from tools.checked_build import checked_flags_for, control_file_rules
        bin_checked = n.bin.parent / "bin_checked"
        rules, flags = control_file_rules(n.target), checked_flags_for(n.target)
        self.assertEqual(checked["tool_name"], "compile_project")
        self.assertEqual(checked["command"], _build_runtime().build_command(
            "make", None, jobs, [
                *execute.library_overrides("", ""), *execute.checked_build_overrides(
                str(n.obj_checked), str(bin_checked), "spec_x_runner", rules, flags)]))
        self.assertEqual(checked["site"]["remote_command"], _build_runtime().build_command(
            "make", None, jobs, [
                *execute.library_overrides("", ""), *execute.checked_build_overrides(
                f"{n.job}/build_checked", f"{n.job}/bin_checked", "spec_x_runner", rules,
                flags)]))
        record = n.meta()["checked_build"]
        self.assertEqual((record["command_id"], record["argv"], record["obj_dir"],
                          record["bin_dir"]),
                         (checked["command_id"], checked["command"], str(n.obj_checked),
                          str(bin_checked)))

    def test_the_whole_source_and_the_staged_sources_are_shipped_and_not_the_audit_logs(
            self) -> None:
        n = self.n
        n.build()
        stage = n.repo / "workspace" / "tmp" / "arid-1" / "site" / "stage"
        shipped = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*")
                         if p.is_file())
        # Everything in the object directory — the fixture's `depy` and whatever staging put
        # there (the target's harness) — and of `src/` everything but the audit log.
        staged = sorted(f"build/{p.name}" for p in n.obj.iterdir() if p.is_file())
        self.assertIn("build/depy_model.f90", staged)
        # ... and the bounds-checked build's object directory (issue #589 PR-0), staged from
        # the same bindings.
        staged += sorted(f"build_checked/{p.name}" for p in n.obj_checked.iterdir()
                         if p.is_file())
        self.assertEqual({p.split("/", 1)[1] for p in staged if p.startswith("build/")},
                         {p.split("/", 1)[1] for p in staged if p.startswith("build_checked/")})
        self.assertEqual(shipped, sorted([*staged, "src/Makefile", "src/kernel.f90"]))
        self.assertIn("command_log.jsonl", AUDIT_LOG_BASENAMES)
        # The staged source reached the build where the control file reads it.
        collected = n.repo / "workspace" / "tmp" / "arid-1" / "site" / "collected"
        self.assertEqual((collected / "build" / "linked.txt").read_text(),
                         "! staged dependency\n")

    def test_a_stale_file_in_the_nodes_bin_is_not_kept(self) -> None:
        n = self.n
        n.bin.mkdir(parents=True)
        (n.bin / "leftover").write_text("old")
        (n.bin.parent / "bin_checked").mkdir(parents=True)
        (n.bin.parent / "bin_checked" / "leftover").write_text("old")
        n.build()
        self.assertEqual(sorted(p.name for p in n.bin.iterdir()), ["spec_x_runner"])
        self.assertEqual(sorted(p.name for p in (n.bin.parent / "bin_checked").iterdir()),
                         ["spec_x_runner"])

    def test_a_site_without_the_build_compiler_is_the_hosts_failure_not_the_sources(
            self) -> None:
        """make, missing its compiler, would exit 2 and read as a failure of the source, which
        routes the node back to Generate; the job refuses before the command instead."""
        n = self.n
        # The site's PATH: what the job needs, and no compiler of that name.
        bare = n.root / "bare"
        bare.mkdir()
        for tool in ("sh", "uname", "hostname", "grep", "sed", "mkdir", "rm", "date", "env",
                     "timeout", "make", "cat", "chmod"):
            found = wc.shutil.which(tool)
            if found:
                (bare / tool).symlink_to(found)
        self.assertIsNone(wc.shutil.which(n.compiler, path=str(bare)))
        result = n.substep(site_path=str(bare))
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("deterministic_build_error", result.stderr)
        self.assertIn(f"{n.compiler} is missing or not executable", result.stderr)
        self.assertFalse((n.repo / n.refs.binary_dir() / "binary_meta.json").exists())
        self.assertFalse((Path(n.job) / "ctl" / "build.stdout").exists(), "make did not run")

    def test_a_source_the_site_compiler_refuses_is_the_sources_failure_named_locally(
            self) -> None:
        n = self.n
        (n.src / "kernel.f90").write_text("! FAIL\n", encoding="utf-8")
        out = n.build()
        self.assertEqual(out["returncode"], 0, "a content failure, not a transport one")
        meta = n.meta()
        self.assertEqual(meta["verification_status"], "fail")
        self.assertIsNotNone(meta["failure_category"])
        # The excerpt the Generate repair receives names the node's local source, not the job
        # directory's copy (issue #464: it is the repair's diagnosis now).
        self.assertIn(f"{n.src}/kernel.f90", meta["failure_excerpt"])
        self.assertNotIn(n.job, meta["failure_excerpt"])
        # The diagnostics name the node's local source, not the job directory's copy — on both
        # streams.
        log = (n.repo / n.refs.binary_dir() / "compile.stderr.log").read_text()
        self.assertIn(f"{n.src}/kernel.f90:3:1: Error", log)
        self.assertNotIn(n.job, log)
        out = (n.repo / n.refs.binary_dir() / "compile.stdout.log").read_text()
        self.assertIn(f"compiling {n.src}/kernel.f90", out)
        self.assertNotIn(n.job, out)
        self.assertIn(f"{n.src}/kernel.f90", meta["failure_excerpt"])
        self.assertNotIn(n.job, meta["failure_excerpt"])
        # Still recorded as built (and failed) at the site.
        self.assertEqual(meta["environment"]["build_site"]["site"], "box")

    def test_the_toolchain_probe_is_the_keys_version_argv(self) -> None:
        """The job asks the site the same question `_target_toolchain_identity` asks this host
        (`toolchain_version_argv`), so the two recorded versions are one question's answers."""
        import tools.remote_execution as rx

        n = self.n
        seen = {}
        real_execute = rx.execute_job

        def spy(request, **kw):
            seen["probe"] = request.toolchain_probe
            seen["required"] = request.required_programs
            return real_execute(request, **kw)

        with mock.patch.object(rx, "execute_job", spy):
            n.build()
        self.assertEqual(seen["probe"], toolchain_version_argv(n.target))
        self.assertEqual(seen["required"], (n.compiler,))


#: The site's launcher (issue #337): `<launcher> -n <N> <program>` starts the program N times.
#: `SHIM_LAUNCH` picks what it does: `one` (default) starts N processes of one run of N,
#: `independent` N one-process runs (a launcher of another installation, which exits 0), and
#: `refuse` refuses the count as a launcher with too few slots does.
_FAKE_LAUNCHER = textwrap.dedent('''\
    #!/bin/sh
    echo "$*" >> "${SHIM_LOG}.launches"
    n=$2; shift 2
    case "${SHIM_LAUNCH:-one}" in
      refuse) echo "There are not enough slots available in the system" >&2; exit 1 ;;
      independent) size=1 ;;
      *) size=$n ;;
    esac
    i=0
    while [ "$i" -lt "$n" ]; do SHIM_RUN_SIZE=$size "$@" || exit $?; i=$((i+1)); done
''')

#: The site's compiler wrapper: the launch canary's build writes a program that prints the size
#: of the run it was started in (`SHIM_RUN_SIZE`, which only the launcher sets; 1 bare). It
#: fails, as a real compiler would, when the source it is handed is not the backend's
#: `LAUNCH_CANARY_SOURCE` byte for byte (a copy written beside it) — so a job that ships
#: no source, or another one, fails its canary — and when `SHIM_CANARY_BUILD=fail`; its bare
#: start fails when `SHIM_BARE=fail`. Every other invocation is the node's fake compiler's.
_FAKE_WRAPPER = textwrap.dedent('''\
    #!/bin/sh
    case " $* " in
      *{canary}*)
        [ "${{SHIM_CANARY_BUILD:-}}" = fail ] && {{ echo "no mpi_f08 module" >&2; exit 1; }}
        out=
        while [ $# -gt 0 ]; do
          case "$1" in -o) out=$2; shift ;; *{canary}) src=$1 ;; esac; shift
        done
        cmp -s "$src" {expected} || {{ echo "$src: not the canary source" >&2; exit 1; }}
        cat > "$out" <<'PROG'
    #!/bin/sh
    if [ -z "${{SHIM_RUN_SIZE:-}}" ] && [ "${{SHIM_BARE:-}}" = fail ]; then
      echo "cannot start outside the launcher" >&2; exit 1
    fi
    echo "spec2science-mpi-size ${{SHIM_RUN_SIZE:-1}}"
    PROG
        chmod +x "$out"; exit 0 ;;
    esac
    exec "$0.node" "$@"
''')


class LauncherTargetBuildAtARemoteSiteTests(unittest.TestCase):
    """Issue #337 (issue #700 PR-3): a launcher target builds at a remote site, and its job
    starts with the launch canary's three commands — built with the wrapper, started under the
    launcher with the profile's ranks, started bare — whose failures are the site's, raised
    before the build's result is read (`deterministic_build_error`, transport fail_closed: no
    Generate repair). Over the shims, with a fake launcher and wrapper at the site."""

    RANKS = 4

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.n = self._node(self._tmp.name)

    def tearDown(self) -> None:
        _reap_shim_jobs(self._tmp.name)
        self._tmp.cleanup()

    def _node(self, tmp: str, ranks: int | None = None) -> _Node:
        from tools.backends import registry

        n = _Node(tmp, target=profile_with(
            parallel={"backend": "mpi"},
            execution={"threads_per_rank": 1, "ranks": ranks or self.RANKS}))
        # The fixture conductor binds a certified harness closure first, which is keyed by the
        # target and so not this variant's: the build under test stages the fixture's own
        # object directory instead, declares no library, and finds the harness registered.
        from tools.tests.test_workflow_conductor import _register_target_harness

        n.conductor._bind_a_harness_only_closure = lambda *a: None
        n.conductor._stage_dependency_sources = lambda *a, **k: []
        n.conductor._closure_libraries = lambda *a: []
        _register_target_harness(n.repo.resolve())
        launcher = registry.capability_module("parallel", "mpi", "launcher")
        node_compiler = n.site_bin / n.compiler
        node_compiler.rename(n.site_bin / f"{n.compiler}.node")
        expected = n.site_bin / "expected_canary.f90"
        expected.write_text(str(launcher.LAUNCH_CANARY_SOURCE))
        node_compiler.write_text(_FAKE_WRAPPER.format(canary=launcher.LAUNCH_CANARY_FILENAME,
                                                      expected=expected))
        node_compiler.chmod(0o755)
        fake = n.site_bin / str(launcher.EXECUTABLE)
        fake.write_text(_FAKE_LAUNCHER)
        fake.chmod(0o755)
        self.launcher = launcher
        return n

    def canary_entries(self, n: _Node, arid: str = "arid-1") -> list[dict]:
        # The orchestration's log, which outlives the attempt's tmp directory; `arid` selects
        # the attempt's entries.
        path = n.repo / "workspace" / "orchestrations" / "orch_1" / "launch_canary.jsonl"
        if not path.exists():
            return []
        return [e for e in (json.loads(line) for line in path.read_text().splitlines())
                if e["agent_run_id"] == arid]

    def test_the_canary_leads_the_job_and_the_build_passes(self) -> None:
        n = self.n
        out = n.build()
        self.assertEqual(out["returncode"], 0, out)
        self.assertEqual(n.meta()["verification_status"], "pass", n.meta())
        entries = self.canary_entries(n)
        self.assertEqual([e["tool_name"] for e in entries], ["launch_canary"] * 3)
        cdir = f"{n.job}/launch_canary"
        exe = f"{cdir}/launch_canary"
        build, launch, bare = (e["site"]["remote_command"] for e in entries)
        self.assertEqual(build[0], n.compiler)
        self.assertIn(f"{n.job}/launch_canary/{self.launcher.LAUNCH_CANARY_FILENAME}", build)
        self.assertEqual(launch, [*self.launcher.argv_prefix(self.RANKS), exe])
        self.assertEqual(bare, [exe])
        self.assertTrue(all(e["ok"] for e in entries), entries)
        # The canary ran in its own directory, and its entries are not the node's: the node's
        # log holds the earlier entry, the build and the checked build.
        self.assertEqual({e["site"]["remote_cwd"] for e in entries}, {cdir})
        self.assertEqual([e.get("tool_name") for e in n.log_entries()],
                         [None, "compile_project", "compile_project"])
        launches = Path(f"{n.log}.launches").read_text().splitlines()
        self.assertEqual(launches, [f"-n {self.RANKS} {exe}"])

    def test_each_canary_failure_is_the_sites_and_records_no_build(self) -> None:
        under = f"under {self.launcher.EXECUTABLE} with {self.RANKS} processes"
        # Each needle reaches into the tail of what the site printed: the message is the
        # failure's only durable copy of it (the log entries hold no output).
        for knob, needle in ((("SHIM_CANARY_BUILD", "fail"),
                              "did not build the launch canary (rc=1: no mpi_f08 module)"),
                             (("SHIM_LAUNCH", "refuse"),
                              f"{under} did not complete (rc=1: There are not enough slots"),
                             (("SHIM_LAUNCH", "independent"),
                              (f"{under} did not run as one run: {self.RANKS} process(es) were "
                               f"started and they reported run sizes {['1'] * self.RANKS}")),
                             (("SHIM_BARE", "fail"),
                              ("started without the launcher did not complete "
                               "(rc=1: cannot start outside the launcher)"))):
            with self.subTest(knob=knob), tempfile.TemporaryDirectory() as tmp:
                n = self._node(tmp)
                result = n.substep(**dict([knob]))
                self.assertEqual(result.returncode, 1, result)
                self.assertIn("deterministic_build_error", result.stderr)
                self.assertIn("launch canary at site 'box'", result.stderr)
                self.assertIn(needle, result.stderr)
                # Nothing was recorded as the node's build, so no Generate repair reads one.
                self.assertFalse((n.repo / n.refs.binary_dir() / "binary_meta.json").exists())
                if knob[0] != "SHIM_LAUNCH" or knob[1] != "independent":
                    # A canary command that exited non-zero stops the job before the build.
                    self.assertEqual(len(n.log_entries()), 1, "the build did not run")
                _reap_shim_jobs(tmp)

    def test_the_build_result_is_the_builds_not_the_canarys(self) -> None:
        """The job's results are read by tag: behind a canary that passed, a source the site's
        compiler refuses is the node's content failure, its excerpt names the node's own source,
        and the record's command is the build's entry in the node's log — not the canary's
        build, which leads the job."""
        n = self.n
        (n.src / "kernel.f90").write_text("! FAIL\n", encoding="utf-8")
        out = n.build()
        self.assertEqual(out["returncode"], 0, "a content failure, not a transport one")
        meta = n.meta()
        self.assertEqual(meta["verification_status"], "fail")
        self.assertEqual(meta["failure_category"], "compile_error", meta)
        self.assertIn(f"{n.src}/kernel.f90:3:1: Error", meta["failure_excerpt"])
        self.assertEqual(len(self.canary_entries(n)), 3)
        build_entry = n.log_entries()[1]
        self.assertEqual(build_entry["tool_name"], "compile_project")
        self.assertEqual(meta["command_id"], build_entry["command_id"])

    def test_a_site_that_does_not_execute_the_class_ships_nothing(self) -> None:
        """The build seam's backstop of the driver's site gate (`launch_shape`): refused before
        anything is staged or shipped, a launcher target as any other."""
        import dataclasses

        n = self.n
        n.conductor.site = n.site = dataclasses.replace(n.site, executes=("gpu",))
        result = n.substep()
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("deterministic_build_error", result.stderr)
        self.assertIn("is not executed at site box", result.stderr)
        self.assertEqual(n.calls(), [])

    def test_a_one_rank_launcher_target_carries_no_canary(self) -> None:
        """One process is one run whichever installation starts it."""
        with tempfile.TemporaryDirectory() as tmp:
            n = self._node(tmp, ranks=1)
            out = n.build()
            self.assertEqual(out["returncode"], 0, out)
            self.assertEqual(self.canary_entries(n), [])
            self.assertFalse(Path(f"{n.log}.launches").exists())

    def test_a_target_without_a_launcher_composes_the_job_it_did_before(self) -> None:
        """V-h of issue #700 PR-3: the job of a target whose backend declares no launcher carries
        no canary command and ships no canary source, so its request — and the digest a batch
        job in flight was recorded under — is the one composed before PR-3."""
        import tools.remote_execution as rx

        with tempfile.TemporaryDirectory() as tmp:
            n = _Node(tmp)
            seen = {}
            real_execute = rx.execute_job

            def spy(request, **kw):
                seen["tags"] = tuple(c.tag for c in request.commands)
                seen["ship"] = sorted(request.ship)
                seen["dirs"] = request.dirs
                return real_execute(request, **kw)

            with mock.patch.object(rx, "execute_job", spy):
                n.build()
            self.assertEqual(seen["tags"], ("build", "build_checked"))
            self.assertFalse([p for p in seen["ship"] if p.startswith("launch_canary")])
            self.assertEqual(seen["dirs"], ("bin", "build", "bin_checked", "build_checked"))

    def test_at_a_batch_site_the_canary_runs_in_the_job_and_a_resumed_build_adopts_it(
            self) -> None:
        import dataclasses

        import tools.remote_execution as rx

        n = self.n
        n.conductor.site = n.site = dataclasses.replace(n.site, scheduler="slurm")
        n.conductor.emit = lambda event, **fields: None
        for name, body in (("sbatch", _SBATCH_SHIM), ("scontrol", _SCONTROL_SHIM)):
            path = n.site_bin / name
            path.write_text(body)
            path.chmod(0o755)
        slurm = n.root / "slurm"
        slurm.mkdir()
        for name in ("build", "build_checked"):
            dest = n.repo / "workspace" / "tmp" / "arid-2" / name
            dest.mkdir(parents=True)
            (dest / "depy_model.f90").write_text("! staged dependency\n", encoding="utf-8")
        real_wait = rx.wait_job

        def build(arid: str, interrupt: bool) -> dict:
            def wait(*a, **k):
                if interrupt:
                    raise KeyboardInterrupt
                return real_wait(*a, **k)

            with n.env(SHIM_SLURM_DIR=str(slurm)), mock.patch.object(
                    subprocess, "run", side_effect=_real_gate_free_run(subprocess.run)), \
                    mock.patch.object(rx, "wait_job", side_effect=wait), \
                    mock.patch.object(rx, "POLL_FIRST_SEC", 0.05), \
                    mock.patch.object(rx, "POLL_MAX_SEC", 0.2):
                return n.conductor._build_inproc(n.refs, arid)

        with self.assertRaises(KeyboardInterrupt):
            build("arid-1", True)
        out = build("arid-2", False)
        self.assertEqual(out["returncode"], 0, out)
        self.assertEqual(len([c for c in n.calls() if c[0] == "sbatch"]), 1,
                         "the second attempt adopted the job that holds the canary")
        entries = self.canary_entries(n, "arid-2")
        self.assertEqual(len(entries), 3, entries)
        self.assertEqual(entries[1]["site"]["remote_command"][:3],
                         list(self.launcher.argv_prefix(self.RANKS)))
        self.assertEqual(n.meta()["verification_status"], "pass")


class BuildAdoptedAtABatchSiteTests(unittest.TestCase):
    """Issue #696 §6.6 and §6.7: a build whose first attempt was interrupted while its batch job
    waited is adopted by the next `_build_inproc` under another `agent_run_id`, and everything
    after the job reads the directory the job RAN in."""

    def setUp(self) -> None:
        import dataclasses
        self._tmp = tempfile.TemporaryDirectory()
        self.n = n = _Node(self._tmp.name)
        n.conductor.site = n.site = dataclasses.replace(n.site, scheduler="slurm")
        self.events: list[tuple[str, dict]] = []
        n.conductor.emit = lambda event, **fields: self.events.append((event, fields))
        # The scheduler's programs, where the site's login finds them.
        for name, body in (("sbatch", _SBATCH_SHIM), ("scontrol", _SCONTROL_SHIM)):
            path = n.site_bin / name
            path.write_text(body)
            path.chmod(0o755)
        self.slurm = n.root / "slurm"
        self.slurm.mkdir()
        # The second attempt's object directories, as staging leaves them for it.
        for name in ("build", "build_checked"):
            dest = n.repo / "workspace" / "tmp" / "arid-2" / name
            dest.mkdir(parents=True)
            (dest / "depy_model.f90").write_text("! staged dependency\n", encoding="utf-8")

    def tearDown(self) -> None:
        _reap_shim_jobs(self._tmp.name)
        self._tmp.cleanup()

    def build(self, arid: str, *, interrupt: bool = False) -> dict:
        import tools.remote_execution as rx

        real_wait = rx.wait_job

        def wait(*a, **k):
            if interrupt:
                raise KeyboardInterrupt
            return real_wait(*a, **k)

        with self.n.env(SHIM_SLURM_DIR=str(self.slurm)), mock.patch.object(
                subprocess, "run", side_effect=_real_gate_free_run(subprocess.run)), \
                mock.patch.object(rx, "wait_job", side_effect=wait), \
                mock.patch.object(rx, "POLL_FIRST_SEC", 0.05), \
                mock.patch.object(rx, "POLL_MAX_SEC", 0.2):
            return self.n.conductor._build_inproc(self.n.refs, arid)

    def record(self) -> Path:
        return (self.n.repo / "workspace" / "orchestrations" / "orch_1" / "site_jobs"
                / "build.json")

    def resumed(self) -> dict:
        with self.assertRaises(KeyboardInterrupt):
            self.build("arid-1", interrupt=True)
        self.assertEqual(json.loads(self.record().read_text())["remote_dir"], self.n.job)
        return self.build("arid-2")

    def test_the_resumed_build_adopts_the_first_attempts_job(self) -> None:
        n = self.n
        out = self.resumed()
        self.assertEqual(out["returncode"], 0, out)
        sbatch = [c for c in n.calls() if c[0] == "sbatch"]
        self.assertEqual(len(sbatch), 1, "one job, submitted by the first attempt")
        # The operator finds the adopted job by the conductor's event.
        adopted = [f for e, f in self.events if e == "site_job_adopted"]
        self.assertEqual([(f["job_id"], f["remote_dir"]) for f in adopted], [("4242", n.job)])
        meta = n.meta()
        self.assertEqual(meta["verification_status"], "pass", meta)
        self.assertEqual(meta["environment"]["build_site"]["job_id"], "4242")
        self.assertEqual(meta["environment"]["build_site"]["remote_dir"], n.job)
        self.assertEqual(subprocess.run([str(n.exe)], capture_output=True, text=True,
                                        check=True).stdout, "built at the site\n")
        _earlier, entry, _checked = n.log_entries()
        # Recorded under the attempt that collected it; the site's directory is the first's.
        self.assertEqual(entry["agent_run_id"], "arid-2")
        self.assertEqual(entry["site"]["remote_cwd"], f"{n.job}/src")
        self.assertEqual(entry["site"]["job_id"], "4242")
        # The record names the second attempt's own object directory locally: the argv the
        # local library would have run for this attempt.
        self.assertIn(f"OBJDIR={n.repo}/workspace/tmp/arid-2/build", entry["command"])
        self.assertFalse(self.record().exists())

    def test_an_adopted_build_that_fails_names_the_nodes_own_source(self) -> None:
        n = self.n
        (n.src / "kernel.f90").write_text("! FAIL\n", encoding="utf-8")
        out = self.resumed()
        self.assertEqual(out["returncode"], 0, "a content failure, not a transport one")
        self.assertEqual(len([c for c in n.calls() if c[0] == "sbatch"]), 1)
        meta = n.meta()
        self.assertEqual(meta["verification_status"], "fail")
        self.assertEqual(meta["environment"]["build_site"]["remote_dir"], n.job)
        # The first attempt's job directory is what the diagnostics name at the site; the
        # excerpt the Generate repair receives names the node's own `src/` instead.
        self.assertIn(f"{n.src}/kernel.f90", meta["failure_excerpt"])
        self.assertNotIn(str(n.workdir), meta["failure_excerpt"])
        for log in ("compile.stdout.log", "compile.stderr.log"):
            self.assertNotIn(str(n.workdir), (n.repo / n.refs.binary_dir() / log).read_text())


class ToolchainVersionArgvTests(unittest.TestCase):

    def test_the_key_probes_the_argv_the_build_job_is_handed(self) -> None:
        """The build key's `compiler_version` is asked with `toolchain_version_argv` — the argv
        the remote build job's toolchain probe runs — for every checked-in profile, the one
        whose parallel backend puts a compiler wrapper in the compiler's place included."""
        from tools.target_profile import list_target_ids, load_target_profile
        repo = Path(__file__).resolve().parents[2]
        server = _build_runtime()
        ids = sorted(list_target_ids(repo))
        self.assertIn("fortran_cpu_mpi", ids)
        for target_id in ids:
            with self.subTest(target=target_id):
                target = load_target_profile(repo, target_id)
                asked: list[tuple[str, ...]] = []
                with mock.patch.object(server, "_syntax_compiler_version",
                                       side_effect=lambda argv, asked=asked:
                                       asked.append(argv) or "v"):
                    identity = _target_toolchain_identity(target)
                self.assertEqual(asked, [toolchain_version_argv(target)])
                self.assertEqual(identity["compiler_version"], "v")
                # And that argv asks the program the build runs: through the parallel backend's
                # wrapper when it declares one, which runs the configured compiler.
                compiler = str(target.toolchain.get("compiler") or "") or str(
                    registry.capability_module("language", target.toolchain["language"],
                                               "bundle_facts").DEFAULT_COMPILER)
                expected: tuple[str, ...] = (compiler, "--version")
                if registry.provides("parallel", target.parallel_backend, "compiler_wrapper"):
                    wrapper = registry.capability_module("parallel", target.parallel_backend,
                                                         "compiler_wrapper")
                    expected = tuple(str(a) for a in wrapper.wrap(expected))
                    self.assertNotEqual(expected, (compiler, "--version"))
                self.assertEqual(toolchain_version_argv(target), expected)


class BuildAtTheLocalSiteTests(unittest.TestCase):

    def test_the_local_build_records_the_local_site_and_the_keys_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for site in (None, Site(site_id=LOCAL_SITE, executes=("cpu",))):
                with self.subTest(site=site):
                    n = _Node(tempfile.mkdtemp(dir=tmp), site=site)
                    if site is None:
                        n.conductor.site = None

                    def fake_compile(args, n=n):
                        n.bin.mkdir(parents=True, exist_ok=True)
                        n.exe.write_text("x")
                        fake_compile.args = args
                        return {"ok": True, "return_code": 0, "command_id": "cid"}

                    with mock.patch.object(_build_runtime(), "tool_compile_project",
                                           fake_compile):
                        n.build()
                    self.assertEqual(fake_compile.args["project_dir"], str(n.src))
                    meta = n.meta()
                    env = meta["environment"]
                    self.assertEqual(env["build_site"], wc._local_site_record())
                    self.assertEqual(env["platform"]["site"], LOCAL_SITE)
                    self.assertIsNone(env["platform"]["gpu"])
                    self.assertEqual(env["compiler_version"], meta["compiler_version"])
                    self.assertEqual(n.calls(), [], "nothing reached for a site")


if __name__ == "__main__":
    unittest.main()
