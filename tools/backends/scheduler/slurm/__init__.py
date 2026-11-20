#!/usr/bin/env python3
"""The Slurm scheduler backend.

Submodules, by capability:

* `submit` — the `job_submit` capability: the `sbatch` command that submits a job script and the
  job id it prints, the `scontrol` command that states the job's condition and what its answer
  means, and the programs both need at the site (issues #293, #696). Imported below so a caller
  that reached this package through `registry.capability_module` finds it as an attribute rather
  than composing a module name.
"""

from tools.backends.scheduler.slurm import submit as submit  # noqa: F401  (re-export)
