<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VCK190 NoC Bandwidth

The ChipScoPy example design on a VCK190, measured with the `hw-noc-perfmon`
skill. A script starts the design's NoC traffic generator streaming to DDR.
The agent then finds which NoC master carries the traffic and measures its
bandwidth with the NoC performance monitors.

The design source in `input/` is a Tcl script that instantiates the Vivado
configurable example design `xilinx.com:design:chipscopy`, which ships with
Vivado. The measurement depends on the **`hw-noc-perfmon` skill**, which lives
at [`skills/hw-noc-perfmon/`](../../../skills/hw-noc-perfmon/) in this repo.

> **Companion:** The same design is used by
> [`examples/hw-ddrmc-debug/vck190-ddr-health/`](../../hw-ddrmc-debug/vck190-ddr-health/),
> which checks the DDR controller's calibration and margins.

## Goal

Measure real NoC traffic on live hardware:

1. **Find the traffic.** Discover the NoC sites, choose at most 8 to monitor,
   and find the NoC master unit (NMU) that carries the traffic.
2. **Measure it.** Report steady-state read and write bandwidth and latency at
   that NMU and at the DDR controller.
3. **Compare with the design.** With the build's NoC artifacts, label the sites
   with their design names and compare the measured bandwidth with the
   configured requirement and QoS class.
4. **See a change.** Switch the traffic from reads to writes, and measure again.

## Skills Used

- **hw-noc-perfmon** enumerates the NoC with `chipscope_noc`, configures the
  NoC performance monitors and samples them with `chipscope_noc_perfmon`, and
  writes the report files. It can also run `sysdbg_noc` to rule out NoC errors
  that look like a slowdown. It uses the ChipScoPy MCP server only and never
  drives Vivado.

## Prerequisites

- A **VCK190** board (`xcvc1902-vsva2197-2MP-e-S`) with its DDR4 UDIMM fitted.
- **`hw_server`** running on the board host, e.g. `TCP:<host>:3121`.
- **ChipScoPy MCP server**, chipscope-mcp 1.5.2 or newer. Without a
  `cs_server_url`, chipscope-mcp starts a local `cs_server` when `cs_server` is
  on `PATH`; it ships in Vivado's `bin` directory.
- **Python with the `chipscopy` package**, installed with chipscope-mcp, and a
  running **`cs_server`**, for `scripts/start_traffic.py`.
- The **`hw-noc-perfmon` skill** installed, invoked as `/hw-noc-perfmon`.
- **Vivado 2026.1+**, only to build the design from source.

## Starting Point

Input files in `input/`:
- `create_project.tcl` creates a VCK190 project, picks the newest
  `xilinx.com:design:chipscopy` example design this Vivado ships, and
  instantiates it as the block design `chipscopy`. It is the same script as the
  hw-ddrmc-debug example's, with a different project name. The block design
  has:
  - CIPS;
  - an AXI NoC (`axi_noc_0`) with one DDR4 memory controller;
  - a NoC traffic generator (`noc_tg_bc`) on NoC port `S00_AXI`, placed at
    `NOC_NMU512_X0Y0`, configured for 1,720 MB/s read and 1,720 MB/s write to
    DDR, Best Effort;
  - counters with ILA and VIO cores;
  - GTY quads with IBERT.
- `.gitignore` excludes the generated project (`noc_perf/`) and logs.

`scripts/start_traffic.py` starts the traffic. The traffic generator comes up
held in reset with an empty instruction memory, and chipscope-mcp has no tool to
load it. The script programs the PDI, releases the generator through the
design's VIO, and loads a pattern that streams 4 KB bursts over the first 2 GB
of DDR and repeats forever. The traffic keeps running after the script exits.

You do not have to build anything. The `chipscopy` Python package carries a
prebuilt copy of the same design. Run `chipscopy-get-examples -y` and use
`chipscopy-examples/designs/vck190/production/chipscopy_ced/chipscopy_wrapper.{pdi,ltx}`.
The prebuilt copy has no NoC artifacts (`.ncr`, `.nts`), so Step 4 needs the
build.

## How to Run

**Follow the prompts in [`prompt.md`](prompt.md).** There are six ordered steps:

1. **Get the design.** Build it from `input/create_project.tcl`, or use the
   prebuilt PDI and LTX from `chipscopy-get-examples`.
2. **Program and start the traffic.** Run `scripts/start_traffic.py`.
3. **Find and measure.** The agent picks the sites, finds the NMU carrying
   the traffic, and measures it.
4. **Compare with the design.** Give the agent the build's `.nts` and `.ncr`.
5. **Switch to writes** with the script, and measure again.
6. **Stop the traffic** with the script.

The skill's own pipeline programs the device first. The prompts tell the agent
the board is already programmed and not to program or reset it, because
reprogramming stops the traffic.

## Expected Behavior

Measured on a VCK190 (Board Farm `systemlab-5`) on 2026-11-13 with Claude Code
(Sonnet) and this skill loaded. The setup was:

- hw_server 2027.1;
- chipscope-mcp 1.5.2 with chipscopy 2027.1.0.0.

Steps 2–4 ran on the design built from `create_project.tcl` with Vivado 2027.1
(40–50 minutes). Steps 2, 3 and 5 ran on the prebuilt design. Steps 2, 3 and
6 were also run several times by hand. The bandwidth numbers were the same on
both designs.

Bandwidth is reported in bytes per second; 1 GB/s here is 10^9 bytes/s. The DDR
controller frequency is 800 MHz, the value ChipScoPy's own NoC perfmon example
uses for this design. With it, the DDR controller's bandwidth matches the NMU's.

**Step 2, start the traffic.** The script prints
`Traffic running: continuous DDR reads through the NoC`.

**Step 3, find and measure.** Discovery returns 10 sites:
- `NOC_NMU512_X0Y0`;
- six `NOC_NMU128` sites (`X0Y2`, `X0Y3`, `X0Y6`–`X0Y9`);
- two `NOC_NSU512` sites, whose locations differ between builds (`X1Y1` and
  `X1Y2` in the prebuilt design, `X0Y2` and `X1Y3` in the 2027.1 build);
- `DDRMC_X0Y0`.

The agent monitors 8 of them, always including `NOC_NMU512_X0Y0` and the DDR
controller, and reports the other two as deferred. Which two it defers varies
from run to run.

| Site | Read | Write | Notes |
| --- | --- | --- | --- |
| `NOC_NMU512_X0Y0` | up to 15.24 GB/s | 0 | The traffic generator's NMU. 2–5 of 8 samples, often only one full window, so its figure varies from run to run (9–15 GB/s). |
| `DDRMC_X0Y0` | **15.24 GB/s** steady | 0 | 8 of 8 samples. The reliable steady-state figure. |
| Other NMUs and NSUs | 0 | 0 | `NOC_NMU128_X0Y2` sometimes shows about 100 bytes/s |

- The agent names `NOC_NMU512_X0Y0` as the NMU carrying the traffic, and the
  traffic as read-only, and takes the steady-state bandwidth from
  `DDRMC_X0Y0`.
- The agent does not trust the NMU's latency. The monitor flags
  `r_accumulated_latency_overflow` on every NMU sample, and the agent says so
  rather than quoting the number as a measurement (see Key Lessons).
- Without NoC artifacts, the agent uses the hardware site names, and says there
  is no configured baseline to compare with.
- The skill writes `noc_perfmon_report.md` and `chipscope_noc_perfmon.json` to
  `chipscope-agentic-ai-reports/hw-noc-perfmon/chipscopy_wrapper/`. If it runs
  the optional NoC error check, it also writes `sysdbg_noc.json`; the check
  reports one informational finding (an NMU that downsizes wide transactions),
  not an error.

**Step 4, compare with the design.** The agent reads the `.nts` and `.ncr`,
measures the generator's NMU and the DDR controller again, and labels them:

| Site | Design name | Configured in the `.nts` | Measured |
| --- | --- | --- | --- |
| `NOC_NMU512_X0Y0` | `chipscopy_i/axi_noc_0/inst/S00_AXI_nmu` | 1,720 MB/s read, 1,720 MB/s write, Best Effort | **15.24 GB/s** read, 0 write |
| `DDRMC_X0Y0` | `chipscopy_i/axi_noc_0/inst/MC0_ddrc`, port `PORT0` | — | **15.24 GB/s** read |

- The reads run at about 9 times the configured requirement. The agent reports
  the requirement as met, since Best Effort traffic may exceed it, and finds no
  bottleneck.
- The `.nts` also asks for 1,720 MB/s of writes, but the generator only reads.
  The agent says so, rather than calling the NoC under-delivering.
- It gives no pass or fail on latency. The `.nts` target (300) and the
  monitor's raw cycle count are in different units, and the NMU's counter
  overflowed.
- The `.nts` has no unit field; the agent assumes MB/s.

**Step 5, writes.** After `start_traffic.py --pattern write`, the same NMU
carries the traffic, now write-only. `DDRMC_X0Y0` reads **14.69 GB/s** of
writes, steady, and 0 reads. The NMU again returns only a partial window
(5.6–11.5 GB/s across runs; see Key Lessons).

Each agent prompt took 50–180 s, at $0.23–0.77 per prompt with Claude Sonnet.

## Key Lessons

- **Take the steady-state bandwidth from the DDR controller.** The DDR
  controller samples every 2.1 s. The NMU samples every 7.2 s, and a
  measurement stops when the fastest monitor has its samples. So the NMU
  returns only 2–5 of 8 samples, and its first one often covers a partial
  window. The first measurement after programming also starts with a few
  seconds of zero samples, even though the traffic is already running.
- **NMU latency is flagged as overflowed at this traffic level.** chipscope-mcp
  configures the NMU monitors without latency scaling (ChipScoPy's `tslide`
  setting), so every NMU sample carries a latency-overflow flag. Measured with
  ChipScoPy directly, with `tslide` 3 and a 447 ms sampling period, the samples
  are clean: 151 clock cycles per burst for reads and 366 for writes. The
  flagged samples show the same values, but only the clean measurement
  confirms them.
- **Do not reprogram while measuring.** Reprogramming resets the traffic
  generator. Measure an already-running design with the program step skipped.
- **Restart writes by programming again.** After the generator has been
  stopped while writing, later write traffic never starts, with no done or
  error flag, until the device is programmed again. Reads are not affected.
  The likely cause is a write burst cut off halfway. That is why
  `start_traffic.py` programs the device every time it starts traffic.
