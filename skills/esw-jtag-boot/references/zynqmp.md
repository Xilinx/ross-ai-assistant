<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ZynqMP firmware loading

## Supported flow and inputs

This helper loads a standard Zynq UltraScale+ MPSoC A53 boot chain through a local or
remote hw_server. It uses PMU firmware, an A53 FSBL, U-Boot and TF-A component ELFs.
It does not program a ZynqMP `boot.bin` directly. Zynq-7000 and R5-hosted FSBL flows are
not covered by this helper.

Use matching PMU firmware, A53 FSBL, U-Boot and TF-A ELFs from the same board-machine
build, found as described in [firmware discovery](firmware-discovery.md). Check
architectures: MicroBlaze for PMU, AArch64 for the A53 chain. A user may supply just
`boot.bin`; discover the companion files before asking for paths. This workflow does not
extract firmware from the boot image and never renames raw partitions to `.elf`.

### DTB and domain scope

Use the package's `devicetree/cortexa53-linux.dtb` when present, or resolve a unique
appropriate separate DTB. `--dtb FILE` loads it at the hardcoded DDR address
`0x100000`. This is the EDF ZynqMP APU convention used by this flow, not an address
encoded in the DTB. An explicit `--dtb FILE ADDRESS` overrides the default.
Do not inspect BIF to rediscover this address. Resolve conflicting known memory
requirements before loading; do not overwrite another component.

This helper loads the APU boot chain and its PMU support firmware. It does not load
R5 applications or PL, including for multidomain packages. A whole-system reset
still resets the device; APU-only describes the images loaded, not reset isolation.

## FSBL run/wait/stop sequence

Follow the sequence used by Xilinx's
[`embpf-bootfw-update-tool` ZynqMP helper](https://github.com/Xilinx/embpf-bootfw-update-tool/blob/cd40ccbf31ab889b09f7f6da4a97dbc78c9593c8/zynqmp/jtag_boot.tcl#L25-L36):

```tcl
# After downloading the matching FSBL to A53 #0:
con
after 4000
stop
catch {stop}
```

The first stop must succeed; the second is best effort, matching the upstream
sequence. The helper then downloads the optional DTB, U-Boot and TF-A. There are
no FSBL breakpoints or PC comparisons and no runtime ESW source dependency.

This fixed wait assumes the board-matched FSBL initializes within four seconds.
It does not prove DDR initialization succeeded. If later downloads fail or console
output reports an FSBL error, preserve the failure and diagnose the firmware/board
before retrying. Do not repeatedly reset or silently lengthen the delay to hide errors.
The helper does not claim U-Boot readiness without console evidence.

## Board and target prerequisites

Set the board to JTAG boot mode before running. Enumerate `targets -target-properties`
on the specified server. Record the exact `jtag_cable_ctx` of the intended board, and
confirm it has one `PSU` and one `*Cortex-A53*#0` target, plus one `MicroBlaze PMU*`
after enabling PMU debug visibility as described below. Every
selection is constrained to that cable and must be unique, including after reset.
If these matches are ambiguous, stop before loading and ask the user which device to use.

Target enumeration is asynchronous. The helper polls up to 30 times, 500 ms apart,
for each required target and retries selection only for an `Invalid context` discovery
race. Each attempt enumerates fresh properties and constrains selection to the cable.
Ambiguity and other selection errors fail immediately; the helper never retries reset.

The helper reads `BOOT_MODE_USER` at `0xFF5E0200` and requires the active boot mode,
`BOOT_MODE_USER & 0xF` as the ZynqMP FSBL reads it (`CRL_APB_BOOT_MODE_USER_BOOT_MODE_MASK`
in `xfsbl_hw.h`), to be JTAG (0). This accepts the switch-selected JTAG mode and a software
override to JTAG. It does not change boot mode. On the selected PSU, it enables PMU debug
visibility using the operation in Vitis
`scripts/vitis/util/zynqmp_utils.tcl:disable_pmu_gate`: read `0xFFCA0038`, OR with
`0x1C0`, write only if necessary, and verify those bits. Other bits are preserved.
This is normal ZynqMP debug setup; a denied access or failed readback stops the flow.
Do not bypass device security restrictions or confuse the PMU container with its
MicroBlaze processor.

System reset clears PMU visibility and can leave stale processor IDs. After its one
system reset, the helper reacquires PSU, checks boot mode, enables the PMU gate again,
and discovers the new MicroBlaze PMU target before downloading. Enabling the gate only
before reset is insufficient. If a run fails after reset, inspect the log and current
targets before deciding how to resume; do not blindly rerun the helper and reset again.

## Invocation

```text
xsdb  scripts/zynqmp_jtag_boot.tcl SERVER CABLE PMUFW FSBL UBOOT BL31 [--dtb DTB [DTB_ADDR]]
```

Validate artifacts before invoking XSDB. Example (replace the
cable and firmware paths with the discovered values):

```bash
xsdb <skill-dir>/scripts/zynqmp_jtag_boot.tcl \
  tcp:board-host:3121 '<observed-cable-context>' \
  /deploy/pmufw.elf /deploy/fsbl.elf /deploy/u-boot.elf /deploy/bl31.elf
```

The sequence checks targets and JTAG mode, resets PSU, restores PMU debug visibility,
loads and starts PMU firmware, loads and runs FSBL on A53 #0 for four seconds,
then stops A53 #0. It loads the optional DTB, U-Boot,
and TF-A in that order. Loading TF-A last sets the PC to its ELF entry point; the final
`con` starts the firmware handoff. Do not reset A53 again after FSBL initialization.

The helper prints the exact Tcl commands as it runs. Always display the actual shell
invocation, component `dow` commands and final `con` in the response, plus the XSDB log.
`IMAGES_LOADED` establishes completed downloads and successful resume only. It does not
establish that U-Boot reached a prompt.

## Failure and validation

Check missing files and invalid ELF headers during artifact validation. Malformed
options and DTB addresses are rejected by Tcl before connecting.
Ambiguous/missing targets or unsuitable boot mode fail before reset when detectable.
An FSBL resume or first-stop error prevents the later DTB/U-Boot/TF-A downloads.
Preserve the error and log and disconnect without retrying resets automatically.

The APU-only helper completed downloads and resume on ZCU104 (`xczu7`), EDF 2026.2,
through a remote hw_server, including the Linux DTB
at the default `0x100000` (`IMAGES_LOADED`). U-Boot console arrival was not checked.
