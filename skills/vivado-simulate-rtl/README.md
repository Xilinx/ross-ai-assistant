<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# vivado-simulate-rtl

Runs and diagnoses FPGA RTL simulation through Vivado. It launches a bounded
compile–elaborate–simulate flow, requires an explicit test contract and
terminal PASS, classifies failures, and preserves transcripts, waveforms, and
coverage or activity artifacts. A clean compile or simulator exit is not
functional proof.

## Supported simulators

Vivado backends (canonical `export_simulation` / `target_simulator` IDs):

| Simulator | ID | Host |
|---|---|---|
| AMD XSim | `xsim` | Linux, Windows |
| Siemens Questa | `questa` | Linux, Windows |
| Siemens ModelSim DE | `modelsim` | Linux, Windows |
| Synopsys VCS | `vcs` | Linux |
| Cadence Xcelium | `xcelium` | Linux |
| Aldec Riviera-PRO | `riviera` | Linux, Windows |
| Aldec Active-HDL | `activehdl` | Windows |

XSim is the default. Do not silently substitute another backend. Third-party
tools need a site install, license, and version-matched `compile_simlib`
libraries. Per-backend setup is in [references/](references/).

On the Ubuntu 24.04 validation host, behavioral smoke passed for XSim, Questa, VCS, and
Xcelium. ModelSim and Riviera-PRO were license-blocked. Active-HDL is
Windows-only. Ubuntu VCS needs a bash `VCS_HOME` overlay; see
[references/vcs.md](references/vcs.md).

## Example prompts

**Behavioral simulation**
> Open this Vivado project, run `sim_1` with XSim, and prove whether the
> testbench passes. Quote the terminal marker and give the transcript and
> waveform paths.

**Third-party backend**
> Use the configured VCS install and compiled libraries to run `sim_1`. Do not
> fall back to XSim. Report `TEST_PASS` or the first causal error.

**Failure diagnosis**
> This testbench is failing. Diagnose the first assertion or mismatch, classify
> it as design, testbench, environment, or timeout, and do not edit the DUT.

**Self-checking testbench**
> Add a bounded, self-checking testbench for this DUT with a watchdog and a
> single `TEST_PASS` / `TEST_FAIL` marker, then run it in XSim.

**Coverage closure**
> Close XSim statement, branch, condition, and toggle coverage on this
> regression. Do not exclude reachable logic. Show before/after reports.

**UVM and seeded regression**
> Run the selected UVM test across these seeds. Replay any failing seed exactly
> before diagnosing. Keep a manifest of plusargs and results per seed.

**Assertions and functional coverage**
> Run the assertion-aware test, report passed/failed/disabled assertions, and
> show which functional coverage bins remain uncovered.

**Post-implementation timing**
> After implementation completes, run post-implementation timing simulation of
> `sim_1` with SDF. Prove the functional result; do not treat timing closure as
> simulation PASS.

**Activity capture**
> Capture SAIF (or VCD) for this hierarchy after reset, over a bounded
> measurement window. Require an independent functional PASS before treating
> the dump as valid.

**Portable stimulus**
> Generate a portable sub-design testbench from this simulation, add an oracle
> and watchdog, and prove replay PASS.

**Guided interactive repair**
> Debug from the waveform first. Stop for an explicit repair / more-evidence /
> stop decision, and do not change DUT RTL until I approve.

**Missing third-party setup**
> I want Questa for `sim_1`, but the runtime or compiled libraries are not
> configured. Stop before changing `target_simulator` and tell me what is
> missing.

**Simulator crash with a documented workaround**
> `xelab` dies with `[XSIM 43-3316] Signal SIGSEGV received` but Questa
> elaborates the same sources. Find the Answer Record, try the workaround, and
> tell me whether it actually fixed it and what it costs.

**Design defect or simulator defect**
> XSim and VCS disagree on this testbench. Run both on identical sources and
> settings and tell me which one is wrong, with a citation.

**Reportable defect package**
> There is no workaround. Reduce this to the smallest case that keeps the same
> signature and give me a package I can file.

**Host preflight**
> Simulation will not start on this machine. Check the host, libraries,
> toolchain, and license before running anything.

**Regression bisect**
> This passed in an older Vivado. Find the release that broke it and give me a
> fallback.

**Performance**
> Our XSim run is slow. Measure where the time goes per stage before changing
> anything.

**Headless evidence**
> No display on this CI box. Prove the regression from the transcript and a
> VCD diff, and tell me the first divergence time.

## Host check

```bash
python3 <skill-dir>/scripts/sim_env_doctor.py \
  --vivado <path-to-vivado> --clibs <compiled-library-dir> --simulator <id>
```

Read-only. Reports Vivado and XSim tool presence, simulator-kernel library
resolution, DPI toolchain, `/bin/sh` implementation, compiled-library
contents, license variables, workspace writability, and display availability.
Exits nonzero on a hard failure.

## Evaluations

The evaluation suite (scenarios, fixtures, and graders) is maintained with the
skill's test suites in its source repository, not in the distributed skill.
