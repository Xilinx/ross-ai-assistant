# Dependencies

The only dependency required to run this skill is a sourced Vitis environment.

- **Vitis** — source the Vitis settings so `XILINX_VITIS` is set and `vivado` and `vitis` are on `PATH`:

  ```bash
  source <Vitis_install>/settings64.csh   # tcsh/csh
  # or
  source <Vitis_install>/settings64.sh    # bash/sh
  ```

**Preflight check** (it must print all three lines and exit 0; the checks are chained, so it stops
at the first one that is missing and exits non-zero):

```bash
test -n "$XILINX_VITIS" && echo "XILINX_VITIS=$XILINX_VITIS" && which vivado && which vitis
```

> **Pick the script that matches your shell**, not the session's reported shell:
> `.csh` for tcsh/csh, `.sh` for bash/sh/zsh. Source whichever matches the shell
> you launch the session from.

> **Source it, then relaunch the session.** Sourcing `settings64` sets many
> session-wide dependencies (`XILINX_VITIS`, `PATH`, `LD_LIBRARY_PATH`, etc.).
> Source it in your own shell and start the session from that shell so the
> environment carries in.
>
> Do **not** prepend `source ...` to individual commands: each command runs in a
> fresh shell, so it won't persist across the flow.

## Third-party simulators (only for non-`xsim` runs)

The default simulator is Xilinx **xsim**, which ships with Vitis and needs no
extra setup. To run with **questa**, **vcs**, **riviera**, or **xcelium**, that
simulator must be **installed and licensed** on the host, and its **precompiled
Xilinx simulation libraries** (`compile_simlib` / compxlib output) must already
exist for that simulator **and version**.

The skill does **not** locate, derive, or hardcode any of these paths — you supply
them at input time (gathered in [references/input.md](references/input.md)):

- `SIM_LIB_MAP_PATH` — precompiled Xilinx sim libraries (`-lib_map_path`)
- `SIM_GCC_INSTALL_PATH` — the simulator's gcc `bin` (`-gcc_install_path`; also VCS `GCC_PATH`)
- `SIM_INSTALL_PATH` — the simulator tool `bin` (`-install_path`)
- `SIM_TOOL_HOME` — the simulator install home (for the AIE `c_rts` compile)

If any path is missing or wrong, `launch_simulation` or the `c_rts` compile will
fail — the skill reports the tool's actual error rather than guessing an alternate
location.
