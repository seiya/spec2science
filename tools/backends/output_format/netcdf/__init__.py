#!/usr/bin/env python3
"""The `netcdf` output format backend.

Submodules, by capability:

* `store` — the `capture_store` capability: the one netCDF-4 file per case under a run's
  capture directory, how its records are found and decoded, and how a run's captures are
  copied and moved (issue #644, Part 3 P5). Imported below so a caller that reached this package
  through `registry.capability_module` finds it as an attribute rather than composing a module
  name.
"""

from tools.backends.output_format.netcdf import store as store  # noqa: F401  (re-export)
