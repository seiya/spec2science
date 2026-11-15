#!/usr/bin/env python3
"""The `json` output format backend.

Submodules, by capability:

* `store` — the `capture_store` capability: which files under a run's capture directory hold a
  case's records, how one is decoded, and how a run's captures are copied and moved (issue #644,
  Part 3 P3). Imported below so a caller that reached this package through
  `registry.capability_module` finds it as an attribute rather than composing a module name.
"""

from tools.backends.output_format.json import store as store  # noqa: F401  (re-export)
