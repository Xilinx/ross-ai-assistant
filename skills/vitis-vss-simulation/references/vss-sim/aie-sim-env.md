---
name: aie-sim-env
description: >
  Sets the AIE simulation environment variables (AIE_WORK_DIR,
  ENABLE_AIE_DBG_TRACE, AIESIM_OPTIONS) that must be live in the same shell that
  launches the simulation. Consumes WORK_DIR and COMPILED_OUT_DIRECTORY.
metadata:
  version: "1.1.0"
---

# AIE Simulation Environment

Your job is to set the environment variables the AIE simulation needs, so the
simulate step picks them up.

## Inputs

- `WORK_DIR` — the AIE `Work` directory.
- `COMPILED_OUT_DIRECTORY` — the project/out path where `aiesim_options.txt` lives.

---

## STEP 1 — Set AIE Simulation Environment

The variables must be set in the **same shell** that launches the simulation (they do not
persist across separate shells):

- `AIE_WORK_DIR` — the AIE `Work` directory (`WORK_DIR`, e.g. `<component>/build/hw/Work`).
- `ENABLE_AIE_DBG_TRACE` — `true` to enable AIE debug trace.
- `AIESIM_OPTIONS` — path to the options file `<COMPILED_OUT_DIRECTORY>/aiesim_options.txt`.
  In the VSS flow the AIE array is simulated by a model inside the RTL simulator that
  `simulate.sh` launches, not by an `aiesimulator` process. The model reads this file, and only
  its `KEY=VALUE` directives take effect (see [vss-sim-options.md](vss-sim-options.md)). Create
  the file in `COMPILED_OUT_DIRECTORY` and, **by default, seed it to dump a VCD** (the one option
  the flow enables on its own; the exact directive is in the options reference). Add any other
  option only when the user asks.

> **Tell the user before launching.** State that the run uses the AIE VCD dump by
> default: "Running the simulation with the AIE VCD dump on (`AIE_DUMP_VCD=foo`,
> the default in this flow): `foo.vcd` is written to `SIM_SCRIPTS_DIR` and can be
> very large for big or long designs. Tell me if you want it left out." If the
> user declines, leave the dump line out of `aiesim_options.txt`.

Use the syntax of the shell that actually runs `simulate.sh` — `export` for
bash/sh/zsh, `setenv` for tcsh/csh. Do not use the Makefile `:=` form.

In bash/sh/zsh:

```bash
export AIE_WORK_DIR=<WORK_DIR>
export ENABLE_AIE_DBG_TRACE=true
export AIESIM_OPTIONS=<COMPILED_OUT_DIRECTORY>/aiesim_options.txt
```

Equivalent in tcsh/csh:

```tcsh
setenv AIE_WORK_DIR <WORK_DIR>
setenv ENABLE_AIE_DBG_TRACE true
setenv AIESIM_OPTIONS <COMPILED_OUT_DIRECTORY>/aiesim_options.txt
```

Because each command runs in a fresh shell, prepend these to the simulation
launch command so they are live when the simulator runs.
