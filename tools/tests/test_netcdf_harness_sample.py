"""Issue #644 P5b: the `netcdf` harness `harness_fortran_cpu_netcdf` and its target, without a
leaf.

The hand model (`data/driver_e2e/harness_fortran_cpu_netcdf_model.f90`, `netcdf_harness`) is
what a generated harness model meets on the target `fortran_cpu_netcdf` before it runs: the
library its `deps.yaml` declares, the launch gate's format check, the static lint rule set, the
syntax stage with the library's module directory, and Build's library symbol checks over the
harness and over a physics node built on it. Its runs are judged elsewhere
(`test_harness_capture_groups`, `test_driver_render.NetcdfHarnessEndToEndTest`,
`test_run_configuration.NetcdfRunConfigurationTest`). The rows

* pin that the node declares `netcdf_fortran`, whose one module and one link name resolve on
  this host;
* pin that the checked-in target passes the launch gate for its harness, and that the manifest
  row is what passes it: a target reading `netcdf` over a harness that provides
  `capture_format_json@1` is refused, and so is the converse;
* pin that the hand model is clean under the declared lint rules and the syntax stage (a
  generated model is told to `use` the module with an `only:` list,
  `tools/prompt_templates/backends/language/fortran/generate_generate.txt`
  `external_library_use`), and that the syntax stage without the module directory fails;
* pin Build's symbol checks on this target (plan §4.5, the in-scope hole on a `netcdf` target:
  a physics kernel that opened the case's capture file could rewrite a record a predicate
  judges): the harness object passes; a harness object that reaches no symbol of the library
  is `library_declared_unused`; a physics object on the same link line passes; one that calls
  the library is `library_symbol_not_declared`.

These rows FAIL, never skip, on a host without the library (issue #498).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tools import build_runtime
from tools import external_library as xl
from tools import target_profile as tp
from tools.backends.linter.fortitude import lint
from tools.tests import netcdf_harness as nh

REPO = Path(__file__).resolve().parents[2]
SPEC_REF = f"spec/infrastructure/infra/harness/{nh.HARNESS}"
DATA = Path(__file__).parent / "data" / "driver_e2e"
FLAGS = ["-std=f2008", "-O2", "-fopenmp"]

#: A physics source on the netcdf target that calls the library itself.
CALLS_LIBRARY = """module kernel_model
  use netcdf, only: nf90_open, nf90_write
  implicit none
  private
  public :: kernel__poke
contains
  subroutine kernel__poke(ok)
    logical, intent(out) :: ok
    integer :: ncid

    ok = nf90_open('raw/state_snapshots/c.nc', nf90_write, ncid) == 0
  end subroutine kernel__poke
end module kernel_model
"""


def _compile(directory: Path, name: str, text: str, extra: list[str] = ()) -> Path:
    (directory / name).write_text(text, encoding="utf-8")
    obj = directory / (Path(name).stem + ".o")
    subprocess.run(["gfortran", *FLAGS, *extra, "-c", name, "-o", obj.name], cwd=directory,
                   check=True, capture_output=True, text=True)
    return obj


class NetcdfHarnessSampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for program in ("gfortran", "nm", "objdump", lint.EXECUTABLE):
            assert shutil.which(program) is not None, f"{program} is not installed"
        cls.resolved = nh.resolved()

    def test_the_node_declares_the_library_and_it_resolves(self) -> None:
        self.assertEqual(xl.node_library_ids(REPO, SPEC_REF), [nh.LIBRARY])
        declaration = xl.load_declaration(REPO, nh.LIBRARY)
        self.assertEqual((declaration.language, declaration.modules, declaration.procedures,
                          declaration.link), ("fortran", ("netcdf",), (), ("netcdff",)))
        self.assertEqual([m for m, _p in self.resolved.module_files], ["netcdf"])
        self.assertEqual([n for n, _p in self.resolved.link_files], ["netcdff"])

    def test_the_target_passes_the_launch_gate_for_its_harness_and_only_for_it(self) -> None:
        profile = nh.TARGET
        self.assertEqual(profile.output_format, "netcdf")
        harness = tp.harness_node_key_for_target(REPO, profile)
        self.assertEqual(harness, f"infrastructure/{nh.HARNESS}@0.1.0")
        self.assertEqual(tp.target_profile_violations(REPO, profile, node_key=harness), [])
        json_harness = tp.harness_node_key_for_target(
            REPO, tp.load_target_profile(REPO, "fortran_cpu"))
        self.assertEqual(tp._capture_format_violations(profile, json_harness), [
            f"output.format: target fortran_cpu_netcdf reads its captures as netcdf, and "
            f"harness {json_harness} does not provide capture_format_netcdf@1"])
        as_json = replace(profile, doc={k: v for k, v in profile.doc.items() if k != "output"})
        self.assertEqual(len(tp._capture_format_violations(as_json, harness)), 1)

    def test_the_hand_model_is_clean_under_the_lint_rules_and_the_syntax_stage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            shutil.copy(nh.MODEL, td)
            proc = subprocess.run(list(lint.check_argv()), cwd=td, capture_output=True,
                                  text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            def syntax(include_dirs: list[str]) -> dict:
                return build_runtime.tool_run_syntax_check({
                    "project_dir": td, "compiler": "gfortran", "std": "f2008",
                    "sources": [nh.MODEL.name], "include_dirs": include_dirs})
            got = syntax(list(xl.include_dirs([self.resolved])))
            self.assertTrue(got["ok"] and not got.get("skipped"), got)
            self.assertFalse(syntax([])["ok"])

    def test_the_producer_is_given_the_layout_its_spec_names(self) -> None:
        """Surface 12: the harness producer is seeded §1-§4 and §6 of this spec, whose §3.2
        names the `netcdf` layout as the one ending its runner-output contract; on this target
        that contract ends with exactly that document, and the seeded sections name it."""
        import types

        from tools import workflow_conductor as wc
        from tools.backends.output_format.netcdf import store
        seeded = wc._harness_controlled_spec_sections(
            (REPO / SPEC_REF / "controlled_spec.md").read_text(encoding="utf-8"))
        self.assertIn("docs/backends/output_format/netcdf/CAPTURE_LAYOUT.md", seeded)
        self.assertNotIn("real_token", seeded)
        document = wc.Conductor._runner_output_contract_with_binding(
            types.SimpleNamespace(target=nh.TARGET), "contract")
        self.assertTrue(document.endswith(store.capture_layout_document()))

    def test_builds_symbol_checks_on_the_netcdf_target(self) -> None:
        declared = xl.node_library_ids(REPO, SPEC_REF)
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            harness = _compile(d, nh.MODEL.name, nh.MODEL.read_text(encoding="utf-8"),
                               nh.compile_flags())
            record, violation = xl.symbol_check(nh.TARGET, [harness], [self.resolved],
                                                declared)
            self.assertIsNone(violation, record)
            # the json harness's model under this harness's names reaches no NetCDF symbol
            unused = _compile(d, "unused_model.f90", (DATA / "harness_fortran_cpu_model.f90")
                              .read_text(encoding="utf-8").replace("harness_fortran_cpu",
                                                                   nh.HARNESS))
            _record, violation = xl.symbol_check(nh.TARGET, [unused], [self.resolved],
                                                 declared)
            self.assertIn("library_declared_unused", violation or "")
            # a physics node built over the harness links the library and declares none
            physics = _compile(d, "coriolis_model.f90", (
                DATA / "dynamics_shallow_water_source_2d_coriolis_model.f90")
                .read_text(encoding="utf-8"))
            record, violation = xl.symbol_check(nh.TARGET, [physics], [self.resolved], [])
            self.assertIsNone(violation, record)
            poke = _compile(d, "kernel_model.f90", CALLS_LIBRARY, nh.compile_flags())
            _record, violation = xl.symbol_check(nh.TARGET, [poke], [self.resolved], [])
            self.assertIn("library_symbol_not_declared", violation or "")


if __name__ == "__main__":
    unittest.main()
