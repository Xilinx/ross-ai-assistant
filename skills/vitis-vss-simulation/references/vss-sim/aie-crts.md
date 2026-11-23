---
name: aie-crts
description: >
  Compiles the AIE C runtime (c_rts) that the AIE compiler emitted into the Work
  directory, simulator-specific (XSim uses Makefile.xsim). Consumes WORK_DIR.
  Must succeed before running the simulation.
metadata:
  version: "1.0.0"
---

# AIE c_rts Compile

Your job is to compile the AIE C runtime (`c_rts`) so the simulation can run.

## Inputs

- `WORK_DIR` — the AIE `Work` directory.
- `SIMULATOR` — `xsim | questa | vcs | riviera | xcelium`.
- `SIM_TOOL_HOME` — the simulator install home (non-xsim; `N/A` for xsim).
- `SIM_GCC_INSTALL_PATH` — the simulator gcc `bin` (VCS only; `N/A` otherwise).

---

## STEP 1 — Compile the AIE c_rts (simulator-specific)

Before running the simulation, compile the AIE C runtime (`c_rts`) that the AIE
compiler emitted into the `Work` directory. This build is **simulator-specific** —
the AIE compiler emitted one `Makefile.<simulator>` per supported simulator
(`Makefile.xsim`, `Makefile.questa`, `Makefile.vcs`, `Makefile.riviera`,
`Makefile.xcelium`). Run the one for `SIMULATOR`, passing that simulator's tool
home from the **user-supplied** paths (never hardcode or guess them).

The sources live under `<WORK_DIR>/ps/c_rts/systemC/` (`WORK_DIR` is the `Work`
directory itself; use its absolute path). This compile builds in place, so it is
the one stage that writes under `WORK_DIR` instead of `COMPILED_OUT_DIRECTORY`:

```bash
cd <WORK_DIR>/ps/c_rts/systemC/
```

| `SIMULATOR` | c_rts compile command |
|---|---|
| xsim    | `make -f Makefile.xsim` |
| questa  | `make -f Makefile.questa QUESTA_HOME=<SIM_TOOL_HOME>` |
| riviera | `make -f Makefile.riviera RIVIERA_HOME=<SIM_TOOL_HOME>` |
| vcs     | `make -f Makefile.vcs VCS_HOME=<SIM_TOOL_HOME> GCC_PATH=<SIM_GCC_INSTALL_PATH>` |
| xcelium | `make -f Makefile.xcelium XCELIUM_HOME=<SIM_TOOL_HOME>` |

Report whether the `c_rts` compile completed successfully. If it fails, do not
proceed to launching the simulation.
