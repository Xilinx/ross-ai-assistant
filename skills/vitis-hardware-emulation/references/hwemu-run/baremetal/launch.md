---
name: hwemu-baremetal-launch
description: >
  Baremetal flow launch — boot QEMU from the staged boot image and let the ELF in the boot PDI
  run; capture embedded_run.log. Consumes PACKAGE_DIR. The package has a .pdi and no BOOT.bin;
  there is no login and no -run-app.
metadata:
  version: "1.1.0"
---

# HW Emulation (baremetal flow) — Launch the emulation

The ELF is inside the boot `.pdi` that STEP 3 put into the board boot image, so the guest runs it
by itself: there is **no login, no password and no `-run-app`**. The package's `launch_hw_emu.sh`
also has `-enable-edf` and no `-qemu-config`; the command adds the staged one.

The AIE debug and AIE enable critical warnings and questions, keeping the previous log, monitoring the run, the leftover-process check and the optional AIE VCD
dump are shared by every flow: see [../common/monitor.md](../common/monitor.md). Run the launch as
a background job, as described there. Run from `COMPILED_OUT_DIRECTORY`.

## Relaunch emulation (user request) — already-staged product only

If the user asks to **relaunch the emulation**, run only this stage and then the report stage. Do
not stage again, unless the check below says the package changed. A relaunch skips the input stage,
so ask for just these values, one at a time, as absolute paths, unless they were already given in
this conversation: `COMPILED_OUT_DIRECTORY`, `PACKAGE_DIR`, `PLATFORM` (echo it back) and
`HOST_EXE` (for the ELF check below). Never infer them.

**Relaunch DRC (run after asking, before launching).** Read-only checks in
`<COMPILED_OUT_DIRECTORY>`; print each as PASS / FAIL and a GO / NO-GO verdict:

| Check | Command | PASS |
|---|---|---|
| Built product present | `ls <PACKAGE_DIR>/launch_hw_emu.sh base_camp_images/combined.qemuboot.conf` | both exist |
| Platform file exists | `ls <PLATFORM>` | the `.xpfm` exists |
| Platform matches the staged images | `grep '^machine' base_camp_images/combined.qemuboot.conf` | the board token in `machine =` (e.g. `vrk160`) appears in the `<PLATFORM>` file name |
| Staged image is current | `[ ! "$(ls <PACKAGE_DIR>/*.pdi)" -nt "$(readlink -f base_camp_images/BOOT-versal-*-multidomain.bin)" ]` | the `.pdi` is **not** newer than the staged boot image |

- **No built product** — say plainly that there is nothing to relaunch and that the full flow must
  run first; do not build it on your own. This is a NO-GO too: write the report as below.
- **A platform mismatch** — print both values and ask for the right platform.
- **The `.pdi` is newer than the staged boot image** — the package was rebuilt after staging, so a
  relaunch would run the old design. Tell the user and run STEPs 2 and 3 first
  ([stage.md](stage.md)), then launch.
- Do **not** launch on a NO-GO: run the report stage, which writes `hw-emu_report.txt` with status
  NO-GO and the failing rows (see [../../report/report.md](../../report/report.md)).
- On GO, run the pre-launch checks below, keep the previous log as in
  [../common/monitor.md](../common/monitor.md), then launch.

## Pre-launch checks (run these before the launch command)

The DRC already checks that `PACKAGE_DIR` has a `.pdi` and no `BOOT.bin` (DRC-04). Before launching,
check the boot recipe (the `.bif` names an ELF for the processor core, and its file name is the `HOST_EXE` file name) and the staging:

```bash
B=$(ls "<PACKAGE_DIR>"/*.bif 2>/dev/null | head -1)
[ -n "$B" ] || { echo "NO .bif IN <PACKAGE_DIR>"; exit 1; }
ELF_IN_BIF=$(grep -iE 'core *= *a[0-9]+-[0-9]+.*\.elf' "$B" | head -1 | sed -E 's/.*file *= *"?([^" }]+\.elf)"?.*/\1/I')
[ -n "$ELF_IN_BIF" ] || { echo "NO ELF FOR THE PROCESSOR CORE IN $B"; exit 1; }
[ "$(basename "$ELF_IN_BIF")" = "$(basename "<HOST_EXE>")" ] || { echo "ELF MISMATCH: $B names $ELF_IN_BIF, HOST_EXE is $(basename "<HOST_EXE>")"; exit 1; }
[ -f "<COMPILED_OUT_DIRECTORY>/base_camp_images/combined.qemuboot.conf" ] || { echo "NOT STAGED: base_camp_images/combined.qemuboot.conf is missing (STEP 2 did not run)"; exit 1; }
echo "OK: $B names $ELF_IN_BIF for the processor core (matches HOST_EXE); base_camp_images/ is staged"
```

If it fails, **do not launch**. Report in chat, in the issue / reason / suggestions format of
[../../rules.md](../../rules.md); the launch has not started; the report stage then writes `hw-emu_report.txt` with status NO-GO
and the printed list.

## Launch command

```bash
cd <COMPILED_OUT_DIRECTORY>
set -o pipefail
export ENABLE_AIE_DBG_TRACE=true && \
<PACKAGE_DIR>/launch_hw_emu.sh \
  -qemu-config ${PWD}/base_camp_images/combined.qemuboot.conf \
  | tee embedded_run.log; exit $?
```

**The launch always turns the AIE debug trace on** — no question is asked. It writes
`<SIM_DIR>/aie_log/` (it can be large for big or long designs; tell the user it is on). The
`-aie-sim-options` option for the AIE VCD dump is the same in every flow: see
[../common/monitor.md](../common/monitor.md).

## Markers (baremetal flow)

Strip ANSI codes first. The ELF prints its result on the UART; the simulator and QEMU then print
`end of simulation invoked!` and `Exiting QEMU!!`, and the launch exits by itself.

| Class | Markers |
|---|---|
| **PASS** | a line with `test passed` (any letter case), no `test failed` and no `ERROR:` line, and the launch exits by itself |
| **FAIL** | `test failed`, or an `ERROR:` line (the ELF prints one per wrong sample) |
| **INCOMPLETE** | the launch exits by itself but there is no pass or fail line: the simulation ended before the ELF finished (for example the run time in the simulator `.tcl` is too short) |
| **Quiet log** | **no time limit.** Baremetal runs can be quiet for a long time (the simulator run time sets it), so a quiet log is never reported or asked about. Wait for the launch to exit, or for the user to stop it |

What to do for each class is in [../common/monitor.md](../common/monitor.md), except the quiet log,
which does not apply to this flow.
