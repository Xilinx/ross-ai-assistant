---
name: hw-noc-perfmon
description: "Debug Versal NoC performance issues using chipscope-mcp (chipscope_noc, chipscope_noc_perfmon). Autonomous pipeline: connect-program-scan → collect user-supplied NoC artifacts → site selection → single-shot monitor setup → live measurement/plotting → analysis-and-correlation. Use when user mentions NoC, bandwidth, latency, QoS, Best Effort, Isochronous, low latency, or unexplained bottlenecks/slow-downs on Versal NoC designs."
license: MIT
compatibility: chipscope-mcp (chipscope_session, chipscope_device, chipscope_scan, chipscope_noc, chipscope_noc_perfmon, sysdbg_noc optional), sys-dbg-util + chipscope-xrdb (optional, for error sanity-check)
metadata:
  version: "3.0.0"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# NoC Performance Debug (EA)

> **Early Access** — this skill may change before general availability.

Automate NoC bandwidth/latency/QoS debugging on AMD Versal devices. Produces a report identifying the bottleneck NoC site(s), measured vs. configured bandwidth/latency, and actionable tuning guidance.

This skill talks to hardware only. It does **not** drive Vivado and never opens a project, runs a Tcl session, synthesizes, or implements anything. All design context comes from artifact files the user supplies.

## Prerequisites

- chipscope-mcp server with `chipscope_noc` and `chipscope_noc_perfmon` (`sysdbg_noc` optional, for an error sanity-check)
- `hw_server` + `cs_server` access to a Versal board
- PDI/LTX pair for the design
- Optional design artifacts supplied by the user (see Step 2): `noc_report.json`, `.ncr`, `.nts`

## Tool Priority

```
chipscope_session, chipscope_device, chipscope_scan → hardware access
chipscope_noc(action="discover")                    → enumerate NoC elements, get valid element_names
chipscope_noc_perfmon(action="measure")             → authoritative bandwidth/latency samples (do NOT estimate from raw register reads)
sysdbg_noc(action="analyze")                        → optional: rule out NoC *errors* masquerading as a performance issue
user-supplied noc_report.json / .ncr / .nts         → optional: hierarchy names, QoS class, configured bandwidth baseline
```

---

## Preflight (Fail-Fast)

Before starting, verify the chipscope-mcp server responds. If it fails, STOP immediately and tell the user.

1. `chipscope_session(action="status")` — if not connected, connect with user-provided URLs

---

## PDI Programming Approval

**NEVER auto-program.** Ask once before programming:
```
🚨 PDI: /path/to/design.pdi → Proceed? (yes/no)
```
Exception: if the user's instruction explicitly authorizes programming (e.g., "program and debug", "test all designs"), treat as pre-authorized.

---

## Perfmon Monitor Authorization

`chipscope_noc_perfmon` configures hardware performance monitors, which **changes device state**. Never call it without explicit approval:
```
🚨 Configure NoC performance monitors on: <site_labels> → Proceed? (yes/no)
```
Exception: if the user's instruction explicitly pre-authorizes measurement (e.g., "measure NoC bandwidth", "profile the NoC", "check throughput"), treat as pre-authorized.

Configure the full site-monitor set **once**. Do not split monitor setup across multiple perfmon calls or batches.

---

## Pipeline: 7 Steps

Run all steps sequentially. Do not pause between steps unless a hard failure blocks progress or an approval gate above requires it.

---

### Step 1: Connect, Program, and Scan

**Goal:** Establish hardware access, program the device, and discover debug cores.

```
chipscope_session(action="connect", hw_server_url="TCP:<host>:3121", cs_server_url="TCP:<host>:3042")
chipscope_device(action="select", device_selector="0")
```

Then obtain PDI approval and program:
```
chipscope_device(action="reset")          # ALWAYS reset before programming to clear sticky NPI error registers
chipscope_device(action="program", pdi_path="<pdi_path>")
chipscope_device(action="program_log")   # check for EAM errors: "EAM Interrupt", "NoC NMU Error", "NoC NSU Error"
chipscope_scan(action="scan", ltx_path="<ltx_path>")
```

**Optional error sanity-check:** performance complaints are sometimes actually NoC *errors* (timeouts, decode errors) stalling traffic rather than a bandwidth/latency/QoS shortfall. If `sysdbg_noc` is available:
```
sysdbg_noc(action="analyze", output_format="json")
```
If `findings_count > 0`, tell the user NoC errors are present and may explain the perceived slowdown (the `hw-noc-debug` skill root-causes these). Ask whether to address those first or continue with performance measurement regardless — do not silently skip this warning.

**PASS:** Programming succeeded and scan completed.
**FAIL:** Programming failed → STOP.

---

### Step 2: Request Design Artifacts From the User

**Goal:** Obtain optional design context for labeling and baselines. Never generate these files — the user must supply them.

Ask once:
```
Optional NoC design artifacts (press Enter to skip any):
  1. noc_report.json  — NoC report (hierarchy names, physical sites, ports)
  2. *.ncr            — NoC compiler results (NMU→NSU routing solution)
  3. *.nts            — NoC traffic specification (bandwidth / QoS requirements)
Supply paths, or reply `none` to continue with hardware site names only.
```

Read whatever the user provides and build the design context:

- **From `noc_report.json`:** map each physical site (e.g., `NOC_NMU512_X0Y0`, `DDRMC_X0Y0`) to its design hierarchy name and NoC port (e.g., `S01_AXI`).
- **From `.ncr`:** NMU→NSU routing, so you can identify which masters share an NSU or a path.
- **From `.nts`:** configured read/write bandwidth requirement and QoS class (Best Effort / Isochronous / Low Latency) per connection. This is the only valid "expected" baseline.

When a site resolves to a hierarchy name, label it with the hardware site in parentheses:
```
<hierarchy_name> (<hw_site_name>)
```
Examples:
```
chipscopy_i/axi_noc_0/S01_AXI (NOC_NMU512_X0Y0)
chipscopy_i/ddrmc_0 (DDRMC_X0Y0)
```

**If the user supplies nothing (or a lookup fails):** do **not** attempt to resolve design instance/hierarchy names by any other means. Use the Vivado site names reported by ChipScoPy exactly as-is (e.g., `NOC_NMU512_X0Y0`), and state in the report that no design artifacts were available, so no configured bandwidth/QoS baseline exists.

**PASS:** Artifacts loaded, or the user explicitly chose to continue without them.
**FAIL:** A supplied path is unreadable → tell the user and ask for a corrected path or permission to continue without it.

---

### Step 3: Discover Enabled Sites

**Goal:** Build the command-line selection list of observable sites.

```
chipscope_noc(action="discover", limit=50)
```

Cross-reference discovered `element_names` against the design context from Step 2 (by base address / site) so each measurable element gets a user-friendly label when possible.

- Keep only enabled / observable sites returned by discovery. Do not offer disabled or unresolved non-measurable entries.
- Refer to sites by the resolved hierarchy label when artifacts were supplied. Otherwise use the ChipScoPy site name as the label.
- If the user named a suspect path or IP, sort matching sites first.
- `chipscope_noc_perfmon` accepts **1–8 element_names per call**. If discovery finds more than 8 viable sites, you may show more than 8 in the selection list, but the user must choose a final set of **≤8** before configuration.
- For any element classified as **DDRMC**, ask the user for `ddrmc_freq_mhz` unless it is unambiguously available in a supplied artifact — never guess a frequency.

---

### Step 4: Ask the User Which Sites to Observe

**Goal:** Let the user choose the final monitor set before configuration.

Present all enabled observable sites as a command-line friendly numbered list:
```text
Available observable sites:
[1] chipscopy_i/axi_noc_0/S01_AXI (NOC_NMU512_X0Y0)
[2] chipscopy_i/axi_noc_0/M00_AXI (NOC_NSU512_X0Y1)
[3] chipscopy_i/ddrmc_0 (DDRMC_X0Y0)
...
Reply with comma-separated numbers or ranges (example: 1,3,5-7), or `all`.
```

Without artifacts the same list uses raw site names:
```text
Available observable sites:
[1] NOC_NMU512_X0Y0
[2] NOC_NSU512_X0Y1
[3] DDRMC_X0Y0
```

- Do **not** auto-select sites unless the user already provided an explicit site list.
- Real terminal checkboxes are not required; use the numbered multi-select format above because it works reliably in plain CLI chat.
- If the user selects more than 8 sites, ask them to narrow the list to **≤8**. Never silently trim a user selection.
- Preserve the user's chosen display labels for the rest of the session and in the final report.

---

### Step 5: Configure the Monitor Set

Get Perfmon Monitor Authorization (see above) using the resolved site labels, then configure the final ≤8-site set in one go. Keep the selected `element_names` fixed for the rest of the live session.

```
chipscope_noc_perfmon(action="measure", element_names=[...], sample_count=8, timeout_seconds=30, ddrmc_freq_mhz={...if applicable...}, authorize_monitor_configuration=true)
```

- This first call establishes the monitor configuration for the chosen sites and may also return the first sample window.
- Do **not** reconfigure mid-session, and do **not** split site-monitor setup across multiple calls.

---

### Step 6: Collect and Live-Plot

**Goal:** Stream measurements in the terminal for up to 30 seconds.

After configuration, continue measuring the same site set and redraw a terminal view on each refresh:

```text
Time  Site Label                                      Read BW   Write BW   Latency
00.0  chipscopy_i/axi_noc_0/S01_AXI (NOC_NMU512_X0Y0)  ######     ##        ###
00.0  chipscopy_i/ddrmc_0 (DDRMC_X0Y0)                 ###        #####     ####
```

- Use simple ASCII bars/sparklines or aligned numeric columns; optimize for a readable terminal, not rich graphics.
- Refresh until either:
  - the user presses **Escape**, or
  - 30 seconds of wall-clock time have elapsed.
- If raw Escape-key capture is unavailable in the client, fall back to a clear typed-stop instruction such as `Type esc and press Enter to stop early.`
- Note: the burst ends when the fastest element completes, so slower elements may return fewer samples than requested. Record the actual sample count returned per element — flag any element that returned noticeably fewer samples than requested as low-confidence.

**PASS:** Live data displayed for the configured monitor set.
**FAIL:** Timeout or empty samples → retry once, then report as inconclusive for that element rather than fabricating a value.

---

### Step 7: Analyze and Correlate

**Goal:** Identify the bottleneck site(s), correlate to the design when artifacts allow, and produce tuning guidance.

#### 7a: Compute Metrics

For each measured site, compute avg/peak read & write bandwidth and avg/peak latency across returned samples.

#### 7b: Flag Bottlenecks

Flag a site as a bottleneck when any of:
- Measured bandwidth is materially below its configured requirement from the `.nts` traffic spec (under-delivering).
- Measured bandwidth is near the NoC port's physical capacity (data width × frequency) — i.e., saturated, which explains why *other* traffic on the same path is starved.
- Latency is high for a connection the `.nts` declares **Isochronous** or **Low Latency** (these classes have tight latency budgets; Best Effort connections tolerate more).

If no `.nts` was supplied, report the raw measured numbers and say explicitly that no baseline was available for comparison — do not invent a target.

#### 7c: Correlate Each Flagged Site

**With artifacts:**
- Use `noc_report.json` to name the design instance and NoC port behind the flagged site.
- Use the `.ncr` routing solution to find the NMU→NSU paths that share the flagged endpoint, since contention from a second master is a common cause of an otherwise well-provisioned connection underperforming.
- Use the `.nts` entry for that connection to state the configured requirement and QoS class.

**Without artifacts:** report the ChipScoPy site name, the measured numbers, and stop there. Do not speculate about which IP or hierarchy instance drives the site.

#### 7d: Tuning Guidance

Base guidance on what was actually measured — pick from (do not apply mechanically, match to the finding):
- **Under-delivering vs. requirement:** review the NoC bandwidth/QoS allocation for this connection; confirm the connection isn't sharing an NMU/NSU with contending traffic.
- **Saturated port:** rebalance traffic across NMU/NSU paths, increase burst size to improve efficiency, or reduce contending traffic on the same path.
- **High latency on Isochronous/Low Latency connection:** verify the QoS class is actually applied on the NoC path (not silently downgraded to Best Effort); check for a shared NSU with bursty Best Effort traffic starving it.
- **DDRMC-adjacent bottleneck:** check DDR page policy/bank conflicts and whether `ddrmc_freq_mhz` used matches the actual running frequency.

---

## Output

Generate ONE report file at:
```
<workspace>/chipscope-agentic-ai-reports/hw-noc-perfmon/<DESIGN_ID>/noc_perfmon_report.md
```

Also save the raw perfmon samples (and, if run, the `sysdbg_noc` JSON) at:
```
<workspace>/chipscope-agentic-ai-reports/hw-noc-perfmon/<DESIGN_ID>/chipscope_noc_perfmon.json
<workspace>/chipscope-agentic-ai-reports/hw-noc-perfmon/<DESIGN_ID>/sysdbg_noc.json   (if run)
```

### Report Template

```markdown
# {{DESIGN_ID}} — NoC Performance Debug Report

## Environment

| Item | Value |
|------|-------|
| Device | {{part}} |
| Board | {{hw_server_url}} |
| PDI | `{{pdi_path}}` |
| Design artifacts | {{noc_report.json / .ncr / .nts supplied, or "none — hardware site names only"}} |
| Error sanity-check | {{sysdbg_noc findings_count, or "not run"}} |

## Measured Sites ({{count}} total, {{deferred_count}} deferred — see below)

{{REPEAT for each measured site}}
### Site {{index}}: {{site_label}}

| Field | Value |
|-------|-------|
| Hierarchy Label | {{hierarchy_name, or "unresolved — no artifacts"}} |
| Hardware Site | {{hw_site_name}} |
| Component | {{NMU/NSU/DDRMC}} |
| Connection | {{source}} → {{destination}}, or "unknown" |
| QoS Class | {{from .nts, or "unknown"}} |
| Configured Requirement | {{read/write BW from .nts, or "no baseline available"}} |
| Measured Bandwidth (avg/peak) | R: {{...}} / W: {{...}} |
| Measured Latency (avg/peak) | {{...}} |
| Samples Returned | {{n}} / {{requested}} {{note if truncated}} |
| Bottleneck? | {{yes/no + reason}} |

{{IF bottleneck AND artifacts supplied}}
#### Connectivity Path

```
{{master}} → {{noc}}/{{port}} → NMU ({{nmu_site_label}}) → NoC → NSU ({{nsu_site_label}}) → {{slave}}
```
{{/IF}}

#### Tuning Guidance

{{guidance from 7d, tailored to this finding}}
{{/REPEAT}}

## Deferred Sites

{{list of sites not measured due to the 8-per-call limit, with reason for deprioritization}}

## Status: {{PASS|FAIL}} — {{one-line summary}}
```

---

## Key Constraints

- This skill never drives Vivado. Do not open projects, run Tcl, use HSI, or read an XSA — design context comes only from user-supplied `noc_report.json`, `.ncr`, and `.nts` files.
- Ask the user for the NoC artifacts once, and continue gracefully when they decline.
- Without artifacts, use the ChipScoPy Vivado site names verbatim and never guess hierarchy or instance names.
- `chipscope_noc_perfmon` owns all bandwidth/latency measurement. Never estimate throughput or latency from raw register reads.
- Never call `chipscope_noc_perfmon` without explicit Perfmon Monitor Authorization (or clear pre-authorization) — it changes device state.
- Always ask the user which enabled sites to observe unless they already supplied an explicit site list.
- Max 8 `element_names` per `measure` call — choose the final monitor set up front, configure all site monitors in that single call, and report any deferred sites instead of silently dropping them.
- DDRMC elements require `ddrmc_freq_mhz`; take it from a supplied artifact or ask the user — never guess.
- Keep one fixed configured monitor set for the whole live-plot session. Do not reconfigure or re-batch once streaming begins.
- Live plotting is terminal-oriented and time-boxed: stop on Escape when supported, otherwise typed `esc`, or after 30 seconds.
- Sample counts can differ per element because the burst ends when the fastest element finishes; flag any element with a truncated sample count as lower-confidence.
- Use the `.nts` configured bandwidth/QoS requirements as the only "expected" baseline. If no `.nts` is supplied, say so — don't invent a target.
- `sysdbg_noc` is an optional sanity-check for errors masquerading as performance issues, not a substitute for perfmon measurement. If it does turn up findings, surface them prominently — a NoC error is a more likely root cause than a bandwidth/QoS shortfall.
- Always reset device before programming (`chipscope_device(action="reset")`) to clear sticky NPI error registers.
