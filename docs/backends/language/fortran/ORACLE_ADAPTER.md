# Oracle adapter — the Fortran binding

> **Audience: the person who writes the `oracle adapter` of a node (`docs/SPEC.md` requirement 17)
> whose oracle declares `language: fortran`.** No leaf reads this document, and no gate reads an
> adapter. `skills/spec-from-code` is the procedure that leads here. The worked examples are the
> `oracle/adapter.f90` of the two microphysics nodes converted from SCALE, which live in the SCALE
> Project since [issue #705](https://github.com/seiya/spec2science/issues/705); the suite keeps
> them as a fixture under `tools/tests/data/scale_project/`.

## 1. What the build does with it

`Build` copies the node's certified source directory, puts the adapter in place of the model
source under the model's name (`<spec_id>_model.f90`, [BUNDLE_BINDING.md](BUNDLE_BINDING.md) §1),
and builds it with the certified control file, the host-rendered driver, the target's harness and
the node's staged dependencies, with the target's flags and with the include and link words of
the build's libraries added (`docs/workflow/phases/phase_03_build.md`). So the adapter is

- **one free-form file** under the target's `toolchain.standard` (`f2008` on the declared
  Fortran targets): a non-standard construct in it fails the oracle build, not a gate;
- **one module named `<spec_id>_model`**, the module the driver `use`s;
- free to `use` the modules its build's libraries declare (`spec/libraries/<library_id>.yaml`
  `modules:`), whose module files the startup verdict and the build key resolve and hash; the
  include directories handed to the oracle build are the closure's and its build's.

## 2. The surface it publishes

The module publishes every procedure of the node's `controlled_spec.md` §5.1 under the same name,
with the same arguments in the same order and the same type, kind, rank and `intent` each: the
driver is rendered from the IR's `public_api` and calls them as a generated model publishes them.
A `module_parameters` entry the signatures name (`dp`) is the kind the adapter declares the
arguments with; a derived type of §5.1 is declared with the same components.

## 3. The array form

The existing code's arrays seldom have the node's shape. Copy into a local array of the existing
code's shape, call, and copy back, as the microphysics adapters do for one column of a
`(KA,IA,JA)` routine with `IA = JA = 1`. Copy an `intent(inout)` argument back whole,
halo cells included, so that a halo the existing code does not write keeps its input value.
Initialise an `intent(out)` array before the call to the value the Controlled Spec gives a cell
the existing code leaves unwritten (`0` at a halo cell on both microphysics nodes).

An assignment converts a kind (`real(dp)` to the existing code's `real(RP)`); an actual argument does not, so pass `real(x, RP)`.

## 4. Setup and module state

Existing code that reads its constants and switches from module state set by a `_setup` routine
is set up by the adapter before it runs. Where the setup reads nothing the cases vary, it runs on
the first call, once, behind a module-level flag (`setup_once` in both microphysics examples).
Where the setup reads what the cases vary (extents, grid metrics, coefficients derived from
them), it runs on every call: the cases of one oracle build run in one process, so the adapter calls
the setups before the existing code and its finalize procedures after it, and the next call sets
up from the state the finalizes left (a finalize that deallocates without a check stops the
program when it runs before the first setup). The
Stage 1 nodes of [issue #709](https://github.com/seiya/spec2science/issues/709) in the SCALE
Project are the example: the `oracle/adapter.f90` of `dynamics_dry_heve_rk4_3d_cd4` calls ten
setups in the order the existing code calls them, the step, and five finalizes. Its measurement
(`docs/design/dyn_fvm_measurement/README.md` §Adapter notes) found a run finalized and set up
again halfway bit-identical to the uninterrupted one. A setup that reads a
namelist reads it from a unit the adapter opens: an empty scratch file keeps every default. A
setup that rewinds the existing code's configuration unit needs that unit open, even where the
boundary stubbed the module that holds it.

A value the cases vary that the existing code reads from a module variable its own setup would
assign (constants a setup reads from a namelist, extents and index ranges of a grid module) is
assigned by the adapter from the case's arguments before each call, in place of that setup;
where the cases do not vary them, the setup may assign them once, as the microphysics adapters'
`setup_once` calls `CONST_setup`. List every such variable the code path reads: one left unassigned keeps its default
and need not stop the program (on the Stage 1 short step, two index bounds left unassigned
returned zero horizontal momenta). One library then serves cases with different values.

A constant the node takes as an argument and the existing code holds at compile time is not read
by the adapter. A case whose constants differ goes to a second `builds:` entry of `oracle.yaml`,
linked with a library built with those constants: the microphysics nodes' `alt` build, linked
with an archive of the existing code built with the suites' alternative constants, is the
example.

## 5. Outputs the existing code does not have

A node's guard flag (`guard_pass`) or convergence flag has no counterpart in code that has no
input check and stops the program on non-convergence. The oracle runs the compared cases only,
which are `pass` cases, so the adapter sets such a flag to the value a `pass` case reads: a call
that returned did not stop the program. A refusal of the existing code — an `error stop`, a
`PRC_abort` — ends the oracle run, which `Validate.execute` reports as `oracle_run_failed`.

A value the existing code writes elsewhere than to an argument (a history output, a log) is
captured by a stub of that output module in the library, as `pcsat` is by the measurement's
`scale_file_history` stub, and copied to the output argument.
