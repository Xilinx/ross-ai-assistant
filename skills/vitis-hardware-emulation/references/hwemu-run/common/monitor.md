---
name: hwemu-monitor
description: >
  Shared launch handling for every hw_emu flow (EDF, PetaLinux, baremetal): keep the previous
  log, run the launch as a background job, watch the log for the flow's markers, check for
  leftover processes, and the optional AIE VCD dump. The flow's own launch file gives the
  launch command and its markers.
metadata:
  version: "1.2.0"
---

# HW Emulation — Launch handling shared by every flow

Each flow has its own launch file (`edf/launch.md`, `petalinux/launch.md`,
`baremetal/launch.md`) with the launch command, the device node and the pass / fail
**markers** of that flow. This file holds what is the same for all of them.

## Before launching — CRITICAL WARNING and a yes / no question if AIE debug halt is enabled

The package's boot image description (a `.bif` in `PACKAGE_DIR`) names `libadf/sw/aie.cdo.debug.bin`
when the package was built with `--package.enable_aie_debug`. Check for it before every launch,
including a relaunch:

```bash
for B in "<PACKAGE_DIR>"/*.bif; do
  [ -e "$B" ] && grep -q 'libadf/sw/aie.cdo.debug.bin' "$B" && echo "AIE DEBUG ENABLED: $B"
done
```

If it prints a line, give the user a **CRITICAL WARNING** (in chat, before the launch), in these words:

> "**CRITICAL WARNING:** this package was built with `--package.enable_aie_debug`, which is on. When enabled, the
> tool generates CDO commands that halt the AI Engine cores during the PDI load, forcing them into
> debug halt mode, so the AIE cores will stall until a debugger is connected to step through them.
> Vitis disables this option by default. Disable it and repackage to run without the stall."

Then **ask the user a yes / no question** and wait for the answer:

> "Do you want to proceed with `--package.enable_aie_debug` on? **yes** — launch this package as it is;
> **no** — stop now, so you can repackage without the option."

- **yes** — launch as usual. The critical warning stays in the report: say in `Detail` that the package
  was built with the option and that the user chose to proceed (see
  [../../report/report.md](../../report/report.md)), above all if the run stalls or times out.
- **no** — **do not launch.** Tell the user to repackage without `--package.enable_aie_debug` and to
  re-run with the new `PACKAGE_DIR` (this skill does not package). The flow ends here: run the report
  stage, which writes `hw-emu_report.txt` with status NO-GO, `Stopped at: AIE debug check` and the
  note that the user chose to repackage.
- **No one to ask** (a non-interactive run): do not block. Treat it as **yes** and keep the critical warning in the report.

Ask it before **every** launch, including a relaunch. No line printed means the option is off: say
nothing and ask nothing.

## Before launching — CRITICAL WARNING and a yes / no question if the AIE cores are enabled twice

Two things are checked together. The boot image description (a `.bif` in `PACKAGE_DIR`) names
`libadf/sw/aie.cdo.enable.bin` when the package was built **without** `--package.defer_aie_run`: the
boot image then enables the AI Engine cores at PDI load. And the host (`HOST_EXE`) starts the graph itself
when it calls the graph run API. Check both before every launch, including a relaunch:

```bash
cdo=0; host=0
for B in "<PACKAGE_DIR>"/*.bif; do
  [ -e "$B" ] && grep -q 'libadf/sw/aie.cdo.enable.bin' "$B" && cdo=1
done
readelf -W -s "<HOST_EXE>" 2>/dev/null | awk '{print $NF}' | grep -q -E '5graph3run|9graph_api3run' && host=1
[ "$cdo" = 1 ] && [ "$host" = 1 ] && echo "AIE CORES ENABLED AT PDI LOAD AND STARTED BY THE HOST"
```

The `readelf` line looks for a graph run function in the host's symbol tables, so it needs no source code:
`xrt::graph::run` in an XRT host (its dynamic symbols) and the graph API's `run` in a baremetal ELF (a
statically linked ELF has no dynamic symbols, so the full table is read). A host that never calls a graph run
function does not match. The check is the same for every flow. If the command prints a line, give the user a
**CRITICAL WARNING** (in chat, before the launch), in these words:

> "**CRITICAL WARNING:** the boot image enables the AI Engine cores at PDI load (`aie.cdo.enable.bin` is
> in it, so this package was built without `--package.defer_aie_run`), and the host starts the graph itself
> (it calls a graph run function). With both, the host has stalled in `graph::end()` with no error in earlier
> runs. Build the package with `--package.defer_aie_run`, so that the host enables the cores."

Then **ask the user a yes / no question** and wait for the answer:

> "Do you want to launch with the cores enabled at PDI load? **yes** — launch this package as it is;
> **no** — stop now, so you can repackage with `--package.defer_aie_run`."

- **yes** — launch as usual. Keep the critical warning in the report (see
  [../../report/report.md](../../report/report.md)), above all if the run stalls or times out.
- **no** — **do not launch.** Tell the user to repackage with `--package.defer_aie_run` and re-run with
  the new `PACKAGE_DIR` (this skill does not package). The flow ends here: run the report stage, which writes
  `hw-emu_report.txt` with status NO-GO, `Stopped at: AIE enable check` and the note that the user chose to repackage.
- **No one to ask** (a non-interactive run): do not block. Treat it as **yes** and keep the critical warning in the report.

Ask it before **every** launch, including a relaunch. If nothing is printed, say nothing and ask nothing. The
check only tells that the two conditions are met; it does not prove the run will stall.

## Before launching — keep the previous log

The launch writes `embedded_run.log` with `tee`, which overwrites any earlier log.
If one exists, preserve it first (a rename only — no deletion):

```bash
cd <COMPILED_OUT_DIRECTORY>
[ -f embedded_run.log ] && mv embedded_run.log embedded_run.prev.log
```

This keeps only the most recent previous run (an older `embedded_run.prev.log` is
replaced).

## Monitor the run (no timeout, no auto-kill)

Run the launch as a background job and note its PID (`$!`) as `LAUNCH_PID`; the
leftover-process check below needs it.

Do **not** add `-timeout` — the application's run time is unknown, so a time limit
could kill a healthy run. While the launch runs, watch `embedded_run.log` (strip ANSI codes
first) for the markers in the **"Markers" section of the flow's launch file**, and act on
each class:

| Class | Action |
|---|---|
| **PASS** | Report the pass. The launch exits by itself — nothing to stop. |
| **App failure** | **Report immediately** per [../../rules.md](../../rules.md) (issue / reason with the quoted log lines and any return code / suggestions), then **wait for `launch_hw_emu.sh` to exit by itself** and confirm no process started by this launch remains (see "Checking for leftover processes"). Kill only as a fallback (see below). |
| **Boot trouble** | **Report immediately**, same format. If the guest stays stuck in `emergency mode` (no `login:` after it) for **more than 10 minutes**, **notify the user and kill the run** (see below). |
| **Quiet log** | No new output in `embedded_run.log` for 10 minutes (**not for baremetal**: it has no quiet-log limit, see its launch file): tell the user "no new log output for 10 minutes — the run may be hung or just slow" and **ask** whether to keep waiting. Never classify it as hung on your own. |

- **Pass:** the launch exits by itself — do not stop anything.
- **App failure:** report the failure with the quoted failure line and, where the flow's
  markers give one, the return code, then **wait for the launch to exit by itself** — the
  markers the flow prints make `launch_hw_emu.sh` shut down QEMU and the simulator.
  Afterward confirm no process started by this launch remains (see "Checking for leftover
  processes" below).
  **Fallback kill (tell the user when you do it):** if the launch has not exited
  after the app-failure markers appeared, or processes remain after it exits, stop
  the launch job and those processes. Leave unrelated processes alone.
- **Boot stuck in emergency mode for more than 10 minutes:** after reporting it, when
  `emergency mode` has been in the log for more than 10 minutes with no `login:`
  after it, **notify the user and kill the run** — no need to ask first. Confirm no
  process started by this launch remains (see "Checking for leftover processes" below;
  leave unrelated processes alone). Within the 10 minutes, leave the run alone.
- **Other boot trouble or a quiet log:** report and **leave the emulation running**;
  **ask the user** whether to stop it. Never stop it on your own — a hang cannot be
  told apart from a slow simulation. Until the user answers, do not debug, change
  files, or re-run anything. When they say stop, stop the launch job and confirm no
  process started by this launch remains (see "Checking for leftover processes" below).
- **Debugging is done by re-launching, not by poking the live guest.** After a failure
  and the stop, **ask the user** before re-launching (and before changing any file the
  re-launch depends on); on their OK, re-run the launch step (and the earlier stage
  the fix touches). Never re-launch unasked.
- Either way, run the report stage next (STEP 6).

## Checking for leftover processes

The processes that belong to this run are the descendants of `LAUNCH_PID`. List them with:

```bash
ps -u "$USER" -o pid=,ppid=,args= | awk -v root="$LAUNCH_PID" '
  { ppid[$1]=$2; line[$1]=$0 }
  END { for (p in ppid) { q=p; while (q in ppid) { if (ppid[q]==root) { print line[p]; break } q=ppid[q] } } }'
```

No output means nothing from this launch remains. It follows the process tree, not
process names, so it works for every `SIMULATOR` (the simulator process is `xsim`,
`vsim`, `simv`, `xmsim` or another). Do **not** match by process name or by the build
directory path: another emulation of the same product can be running, and a process
that is not a descendant of `LAUNCH_PID` is not yours to stop. Processes re-parented to
PID 1 after the launch script exited do not appear; if the log points to one, tell the
user instead of killing it.

## Optional — AIE VCD dump (`-aie-sim-options`)

Only when `CAPTURE_VCD` is `yes`; otherwise skip this section.

Create `aiesim_options.txt` in `COMPILED_OUT_DIRECTORY` from the bundled template
([../../../scripts/aiesim_options.template.txt](../../../scripts/aiesim_options.template.txt)).
The `AIE_PKG_DIR` line of the options file takes the user's `AIE_WORK_DIR` (the AIE compile
Work directory), and is removed when `AIE_WORK_DIR` is blank. The directory's name and location vary, so never
assume one:

```bash
cd <COMPILED_OUT_DIRECTORY>
cp <skill-dir>/scripts/aiesim_options.template.txt aiesim_options.txt
# AIE_WORK_DIR given:
sed -i 's#^AIE_PKG_DIR=.*#AIE_PKG_DIR=<AIE_WORK_DIR>#' aiesim_options.txt
# AIE_WORK_DIR blank:
sed -i '/^AIE_PKG_DIR=/d' aiesim_options.txt
```

Then **append** `-aie-sim-options ${PWD}/aiesim_options.txt` to the flow's launch command
(before the `| tee`); this is the same in every flow. The VCD
(`AIE_DUMP_VCD` basename, e.g. `foo.vcd`) is written to the simulator directory of the
package, `<SIM_DIR>` (see the report stage).

Without `AIE_WORK_DIR` the VCD is still written, but the emulator prints
`AIE_PKG_DIR option missing in -aie-sim-options file` and
`Can't find aie compiler summary file`, and the run summary has no AIE compiler summary
and no GMIO throughput.
