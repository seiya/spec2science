"""What a test needs to build and run against the `netcdf` harness's hand model (issue #644 P5b).

`data/driver_e2e/harness_fortran_cpu_netcdf_model.f90` is a hand-written realization of
`harness_fortran_cpu_netcdf`'s §5.1 that writes the `netcdf` capture layout through the
netCDF-Fortran binding. Building it takes the library the harness declares
(`spec/libraries/netcdf_fortran.yaml`), located as a site entry naming only its module directory
would locate it: the module file in `INCLUDE_DIR`, the library file through the compiler's own
search. Running it takes the product's loader overlay (`external_library.loader_env`), because
the operator's inherited loader path may name another build of the same library first (issue
#644 E1b; on the development host a program using the binding exits 127 without it, with
`/opt`'s 4.5.2 build first on the inherited loader path, measured 2026-11-16).

The rows that use this FAIL, never skip, on a host without the library (issue #498): CI installs
it (`.github/workflows/tests.yml`; `docs/DEVELOPMENT.md` §Fresh-machine setup step 6).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from tools import external_library as xl
from tools.checked_build import target_compiler
from tools.execution_sites import SiteLibrary
from tools.target_profile import load_target_profile

REPO = Path(__file__).resolve().parents[2]
HARNESS = "harness_fortran_cpu_netcdf"
MODEL = Path(__file__).parent / "data" / "driver_e2e" / f"{HARNESS}_model.f90"
LIBRARY = "netcdf_fortran"
#: Where the distribution's development package puts the binding's module file (Ubuntu 22.04's
#: `libnetcdff-dev`, the one CI installs). The compiler searches no such directory by itself.
INCLUDE_DIR = "/usr/include"
TARGET = load_target_profile(REPO, "fortran_cpu_netcdf")


@lru_cache(maxsize=1)
def resolved() -> xl.ResolvedLibrary:
    """The harness's library at the test's site; AssertionError when the host lacks it."""
    site = SimpleNamespace(site_id="local",
                           library=lambda _library_id: SiteLibrary(include=INCLUDE_DIR))
    try:
        return xl.resolve(xl.load_declaration(REPO, LIBRARY), site, target_compiler(TARGET))
    except xl.LibraryUnresolved as exc:
        raise AssertionError(
            f"`{LIBRARY}` is not installed where this test looks ({exc}); install it "
            "(docs/DEVELOPMENT.md §Fresh-machine setup step 6)") from exc


def compile_flags() -> list[str]:
    """The words a compile of a source that uses the binding's module takes."""
    return xl.make_values(target_compiler(TARGET), [resolved()])[0].split()


def link_words() -> list[str]:
    """The words a link of a program over the harness takes, after its objects."""
    return xl.make_values(target_compiler(TARGET), [resolved()])[1].split()


def run_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """``base`` (this process's environment when None) with the library directory first on the
    loader path, as every host run of a binary linking the library takes it."""
    env = dict(os.environ if base is None else base)
    env.update(xl.loader_env(TARGET, [resolved().lib_dir], env))
    return env
