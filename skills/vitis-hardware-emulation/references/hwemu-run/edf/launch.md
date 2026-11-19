---
name: hwemu-edf-launch
description: >
  EDF flow launch — boot QEMU and run the app in-guest; capture embedded_run.log.
  Consumes PACKAGE_DIR. The package's launch_hw_emu.sh has -enable-edf and a BOOT.bin.
metadata:
  version: "2.1.1"
---

# HW Emulation (EDF flow) — Launch the emulation

Boot QEMU via `launch_hw_emu.sh` and run the app inside the guest.
The user's runner script `RUN_SCRIPT` (already staged into the rootfs) runs the staged host
executable with the staged `.xclbin`; it sets `XCL_EMULATION_MODE=hw_emu` itself. Run from
`COMPILED_OUT_DIRECTORY`.

The AIE debug and AIE enable critical warnings and questions, keeping the previous log, monitoring the run, the leftover-process check and the optional
AIE VCD dump are shared by every flow: see [../common/monitor.md](../common/monitor.md).
Run the launch as a background job, as described there.

### Relaunch emulation (user request) — already-built product only

If the user asks to **relaunch the emulation**, run **only this stage** (then the
report stage). Do not rebuild or re-stage anything — skip every earlier stage. This
works **only for an already-staged product** (STEPs 0–4 of the EDF flow already ran).

A relaunch skips the input stage, so ask the user for just these four values — one
at a time (the first three as absolute paths) — unless they were already given in this conversation:

- `COMPILED_OUT_DIRECTORY` — the build dir of the already-built product (where the
  launch runs and `embedded_run.log` lands).
- `PACKAGE_DIR` — the packaged hw_emu directory that holds `launch_hw_emu.sh`.
- `PLATFORM` — the platform `.xpfm`; it selects `<ROOTFS_DEV>` (see the table
  below). Echo it back for confirmation.
- `RUN_SCRIPT` — the **file name** of the runner script already staged in the rootfs (the launch
  runs it as `./<RUN_SCRIPT file name>`).

Always ask the user for `PLATFORM`, `PACKAGE_DIR` and the `RUN_SCRIPT` file name — never infer them. Everything else
comes from the built product.

**Relaunch DRC (run after asking, before launching).** Read-only checks in
`<COMPILED_OUT_DIRECTORY>`; print each as PASS / FAIL and a GO / NO-GO verdict:

| Check | Command | PASS |
|---|---|---|
| Built product present | `ls <PACKAGE_DIR>/launch_hw_emu.sh base_camp_images/combined.qemuboot.conf` | both exist |
| Platform file exists | `ls <PLATFORM>` | the `.xpfm` exists |
| Platform matches the staged images | `grep '^machine' base_camp_images/combined.qemuboot.conf` | the board token in `machine =` (e.g. `vrk160`) appears in the `<PLATFORM>` file name |

- **No built product** — say plainly that there is nothing to relaunch and that the
  full flow must run first; do not build it on your own. This is a NO-GO too: write the report as
  below.
- **A platform mismatch (FAIL)** — print both values (the user's `PLATFORM` and what
  the build/staged images show) and ask for the right platform. **Do not launch** on a
  NO-GO: run the report stage, which writes `hw-emu_report.txt` with status NO-GO and the failing
  rows (see [../../report/report.md](../../report/report.md)).
- **GO** — keep the previous log as in the section above, then launch as below.

### Host-side env vars (set in the same shell as `launch_hw_emu.sh`)

**The launch always turns the AIE debug trace on** — no question is asked. It sets
`ENABLE_AIE_DBG_TRACE=true`, which writes `<SIM_DIR>/aie_log/` (it can be large for big or
long designs; tell the user it is on). This variable, and any other host-side AIE simulation
env vars, must be set in the **same shell** that runs `launch_hw_emu.sh` — prepend them to
the launch command. Use the syntax matching the shell that actually runs the command:

In bash/sh (Bash tool always uses bash):
```bash
export ENABLE_AIE_DBG_TRACE=true
```

In tcsh/csh (user's interactive shell):
```tcsh
setenv ENABLE_AIE_DBG_TRACE true
```

Because the Bash tool spawns a fresh shell for every command, **prepend** the
export in the same compound command as the launch (not a separate tool call):

```bash
cd <COMPILED_OUT_DIRECTORY>
set -o pipefail
export ENABLE_AIE_DBG_TRACE=true && \
<PACKAGE_DIR>/launch_hw_emu.sh \
  -qemu-config ${PWD}/base_camp_images/combined.qemuboot.conf \
  -login "amd-edf" -password "amd-edf" \
  -run-app "mount <ROOTFS_DEV> /media; cd /media; ./<RUN_SCRIPT file name>" \
  | tee embedded_run.log; exit $?
```

`set -o pipefail` makes the pipeline's exit code the launch's own, in both bash and
zsh. The exit code only tells you the launch ended; the outcome is decided from the
log markers (see the report stage).

- **`<ROOTFS_DEV>` — select from `PLATFORM`.** The guest's device node for the
  rootfs (partition 2, where `copy-wic` staged the app files) depends on the rootfs
  **storage type**, not the boot mode — same partition, different device node:

  | `PLATFORM` | rootfs storage | `<ROOTFS_DEV>` |
  |---|---|---|
  | **VEK385** | UFS (enumerates as `/dev/sda`) | `/dev/sda2` |
  | **VRK160**, **vck190**, **vek280** | mmcblk (VRK160 is OSPI boot but still mmcblk) | `/dev/mmcblk0p2` |
- **`XCL_EMULATION_MODE=hw_emu`** is set inside the runner script (in the
  guest). If you ever launch the host directly on the host machine instead, set it
  in the **same shell** with `export` (the Bash tool is bash; `setenv` no-ops in
  bash — verify with `echo $XCL_EMULATION_MODE`).
- QEMU boot is **slow** — monitor `embedded_run.log` / the console for the login
  prompt then the host output. Do **not** pipe through `tail`/`head` and do not
  kill the run for being slow.
- On any **ERROR**, report it per the error-reporting convention in
  [../../rules.md](../../rules.md) (interactive vs non-interactive handling); never
  terminate the emulation on your own initiative in interactive runs.

### Markers (EDF flow)

Strip ANSI codes first. The runner script (`RUN_SCRIPT`) prints the `ERROR:` / `INFO:` lines; without them the launch does not exit by itself.

| Class | Markers |
|---|---|
| **PASS** | `TEST PASSED`, or `INFO: Embedded host run completed.` with no `ERROR: Embedded host run failed` before it. |
| **App failure** | `ERROR: Embedded host run failed, RC=<rc>`, `terminate called`, `Aborted`, `core dumped`, `No such device`, or `TEST FAILED`. The runner's markers make `launch_hw_emu.sh` exit by itself. |
| **Boot trouble** | `emergency mode`, `Timed out waiting for device`, `Dependency failed`. |
| **Quiet log** | no new output in `embedded_run.log` for 10 minutes. |

What to do for each class is in [../common/monitor.md](../common/monitor.md).

### With the AIE VCD dump

Add `-aie-sim-options ${PWD}/aiesim_options.txt` to the launch command (before the `| tee`):

```bash
cd <COMPILED_OUT_DIRECTORY>
set -o pipefail
export ENABLE_AIE_DBG_TRACE=true && \
<PACKAGE_DIR>/launch_hw_emu.sh \
  -qemu-config ${PWD}/base_camp_images/combined.qemuboot.conf \
  -login "amd-edf" -password "amd-edf" \
  -run-app "mount <ROOTFS_DEV> /media; cd /media; ./<RUN_SCRIPT file name>" \
  -aie-sim-options ${PWD}/aiesim_options.txt \
  | tee embedded_run.log; exit $?
```
