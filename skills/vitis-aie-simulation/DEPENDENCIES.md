# Dependencies

The only dependency required to run this skill is a sourced Vitis environment.

- **Vitis** — source the Vitis settings so `XILINX_VITIS` is set and
  `aiesimulator` is on `PATH`:

  ```bash
  source <Vitis_install>/settings64.csh   # tcsh/csh
  # or
  source <Vitis_install>/settings64.sh    # bash/sh
  ```

  **Preflight check** (it must print both lines and exit 0; the checks are chained, so it stops at
  the first one that is missing and exits non-zero):

  ```bash
  test -n "$XILINX_VITIS" && echo "XILINX_VITIS=$XILINX_VITIS" && which aiesimulator
  ```

> **Source it, then relaunch the session.** Pick the script that matches the shell
> you launch the session from, not the session's reported shell: `.csh` for
> tcsh/csh, `.sh` for bash/sh/zsh. Sourcing `settings64` is all the environment
> setup this skill needs, so source it in your own shell and start the session
> from that shell so the environment carries in.
>
> Do **not** prepend `source ...` to individual commands: each command runs in a
> fresh shell, so it won't persist across the flow.
