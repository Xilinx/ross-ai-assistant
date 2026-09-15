---
name: hw-noc-debug
description: "Debug Versal NoC issues using chipscope-mcp and sysdbg_noc with Vivado MCP for design correlation. Autonomous 3-step pipeline: connect-program-scan → design-preparation → analysis-and-root-cause. Use when user mentions NoC errors, AXI failures, decode errors, timeouts, SLVERR/DECERR, or unexplained hangs on Versal."
compatibility: chipscope-mcp (chipscope_session, chipscope_device, chipscope_scan, chipscope_noc, sysdbg_noc), Vivado MCP (vivado_execute), sys-dbg-util + chipscope-xrdb, Vivado 2026.1+
maturity: early-access
metadata:
  author: amd-fpga-team
  version: "1.1-ea"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# NoC Debug (EA)

> **Early Access** — this skill may change before general availability.

Automate NoC debugging on AMD Versal devices. Produces a root-cause report identifying the exact AXI master instance, the error rule, and actionable fix guidance.

## Prerequisites

- chipscope-mcp server with `sysdbg_noc`
- Vivado MCP server (`vivado_execute`)
- `hw_server` + `cs_server` access to a Versal board
- PDI/LTX pair for the design
- Vivado project (.xpr) or routed checkpoint (.dcp)

## Tool Priority

```
chipscope_session, chipscope_device, chipscope_scan → hardware access
sysdbg_noc(action="analyze", output_format="json") → authoritative analyzer (do NOT manually decode registers)
vivado_execute → design correlation, artifact extraction, HSI API
```

---

## Preflight (Fail-Fast)

Before starting, verify both MCP servers respond. If either fails, STOP immediately and tell the user.

1. `chipscope_session(action="status")` — if not connected, connect with user-provided URLs
2. `vivado_execute("puts {ready}")` — if no session, start one with path from mcp.json `--vivado-path` arg + `/bin/vivado`

---

## PDI Programming Approval

**NEVER auto-program.** Ask once before programming:
```
🚨 PDI: /path/to/design.pdi → Proceed? (yes/no)
```
Exception: if the user's instruction explicitly authorizes programming (e.g., "program and debug", "test all designs"), treat as pre-authorized.

---

## Pipeline: 3 Steps

Run all steps sequentially. Do not pause between steps unless a hard failure blocks progress.

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

> **Why reset?** NoC NPI error registers (ISR, ERR_NUM, ERR_INFO) are sticky and survive
> reprogramming. Without a reset, stale errors from prior runs contaminate the analysis.

**PASS:** Programming succeeded and scan completed.
**FAIL:** Programming failed → STOP.

---

### Step 2: Design Preparation

**Goal:** Extract routed-design artifacts for correlation.

**Batch Vivado commands** — combine into minimal calls:

```tcl
# Call 1: Open project and run
vivado_execute("open_project <xpr_path>; set runs [get_runs impl_*]; puts $runs")

# If multiple impl runs, ask user which one. If single, use it.
vivado_execute("open_run <selected_run>")

# Call 2: Extract all artifacts (report_noc is a built-in Vivado 2026.1+ command)
vivado_execute("set outdir vivado-agentic-ai-reports/hw-noc-debug/<DESIGN_ID>/artifacts; file mkdir $outdir; report_noc -json $outdir/noc_report.json; write_noc_solution -force $outdir/noc_solution.ncr; write_hw_platform -fixed -force -file $outdir/design.xsa; puts DONE")
```

**Required artifacts:** noc_report.json (or .txt via `-file`), .ncr, .xsa, PDI, LTX (optional).

If design is not routed or PDI is missing → STOP, tell user to build first. Never run synthesis or implementation.

**PASS:** All artifacts extracted.
**FAIL:** Design not routed or artifacts cannot be generated.

---

### Step 3: Analysis and Root Cause

**Goal:** Run sysdbg_noc, correlate findings with design, identify the AXI master.

#### 3a: Run Analyzer

```
sysdbg_noc(action="analyze", output_format="json")
```

If `findings_count > 0`, re-run with `verbose=true` for full register context.

Validate: `schema_version == "noc.1.0.0"`. If not → STOP, runtime issue.

If `findings_count == 0`: report "No NoC errors detected", skip correlation, write report, close project.

#### 3b: Correlate Each Finding

For EACH finding with `findings_count > 0`:

**Findings may appear at NMU or NSU components.** The correlation path differs:

##### NMU Findings (component_family == "NOC_NMU")

1. **Map NMU to design instance:** Match `module_base_address` against `noc_report.json` NMU entries to get the physical site (e.g., `NOC_NMU512_X0Y0`) and NoC port name (e.g., `S01_AXI`).
   ```tcl
   # Alternative: use netlist directly
   vivado_execute("foreach c [get_cells -hierarchical -filter {REF_NAME =~ NOC_NMU*}] { puts \"$c : [get_property SITE $c]\" }")
   ```

2. **Identify AXI master — try HSI first, then BD fallback:**
   ```tcl
   # Method 1: HSI (works for Xilinx IP masters)
   vivado_execute("hsi::open_hw_design <outdir>/design.xsa")
   vivado_execute("set pin [hsi::get_intf_pins axi_noc_0/<nmu_port>]; set net [hsi::get_intf_nets -of_objects $pin]; set cells [hsi::get_cells -of_objects $net]; foreach c $cells { puts \"Cell: $c IP_NAME=[common::get_property IP_NAME $c]\" }")
   vivado_execute("hsi::close_hw_design [hsi::current_hw_design]")
   ```
   ```tcl
   # Method 2: BD fallback (works for RTL module_ref cells where HSI returns empty)
   vivado_execute("open_bd_design [get_files *.bd]; set net [get_bd_intf_nets -of_objects [get_bd_intf_pins axi_noc_0/<nmu_port>]]; set src [get_bd_intf_pins -of_objects $net -filter {MODE == Master}]; set master [get_bd_cells -of_objects $src]; puts \"Master: $master IP=[get_property VLNV $master]\"")
   ```

3. **Error address from registers:** Combine `REG_1ST_ERR_INFO_5` (addr_l) and `REG_1ST_ERR_INFO_6` (addr_u) to reconstruct the 64-bit target address.

##### NSU Findings (component_family == "NOC_NSU")

NSU errors indicate the problem was caught at the **slave endpoint** (e.g., protocol violations like oversized AXSIZE). The NSU does not directly identify the master — trace backwards:

1. **Map NSU to design instance:** Match `module_base_address` against `noc_report.json` NSU entries to get the physical site and NoC master port (e.g., `M00_AXI`).
   ```tcl
   vivado_execute("foreach c [get_cells -hierarchical -filter {REF_NAME =~ NOC_NSU*}] { puts \"$c : [get_property SITE $c]\" }")
   ```

2. **Identify which master(s) route through this NSU:** Use the NCR file or address map to find all NMU→NSU paths. The `axid` field in `REG_ERR_LOG_INFO_3` encodes the source — cross-reference with the NMU port's AXID configuration.

3. **Extract protocol details from NSU error registers:**
   - `REG_ERR_LOG_INFO_3`: `axsize` (bits 24-26), `axid` (bits 0-15)
   - `REG_ERR_LOG_INFO_4`: `axburst` (bits 20-21)
   - `REG_ERR_LOG_INFO_6`: `addr_u` (upper address)

##### Error Details Extraction

4. **Extract from analyzer JSON:**
   - Always available: `node_name`, `module_base_address`, `isr.active_bits`, `err_num.decode`
   - When `err_num.rule.status == "decoded"`: use `rule.description` and `rule.corrective_action`
   - When `err_num.rule.status == "not_applicable"`: derive fix guidance from ISR bit name + ERR_NUM decode fields (see Fix Guidance table below)

##### Fix Guidance (when rule.status == "not_applicable")

| ISR Bit | Error Type | Typical Root Cause | Corrective Action |
|---------|-----------|-------------------|-------------------|
| `addr_map_wr` | Write decode error | Write to unmapped NoC address | Verify target address is within a mapped slave region in the NoC address editor. Check VALID_ADDR parameter or software pointer. |
| `addr_map_rd` | Read decode error | Read from unmapped NoC address | Same as addr_map_wr but for read path. Check read address generation logic. |
| `xlx_infos_wr` | Protocol/info write | AXSIZE exceeds port data width, or other protocol violation | Check AXSIZE vs NoC port data width. Ensure master's burst parameters match the NoC port configuration. |
| `xlx_infos_rd` | Protocol/info read | Read protocol violation | Same as xlx_infos_wr but for read path. |
| `timeout` | Transaction timeout | Slave not responding within timeout period | Check slave readiness, clock domain crossings, or increase NoC timeout via `sysdbg_noc_timeout`. |

#### 3c: Close Vivado

```
vivado_execute("close_project")
```

---

## Output

Generate ONE report file at:
```
<workspace>/vivado-agentic-ai-reports/hw-noc-debug/<DESIGN_ID>/noc_debug_report.md
```

Also save the raw analyzer JSON at:
```
<workspace>/vivado-agentic-ai-reports/hw-noc-debug/<DESIGN_ID>/sysdbg_noc.json
```

### Report Template

```markdown
# {{DESIGN_ID}} — NoC Debug Report

## Environment

| Item | Value |
|------|-------|
| Device | {{part}} |
| Board | {{hw_server_url}} |
| PDI | `{{pdi_path}}` |
| Analyzer | sysdbg_noc {{schema_version}} |

## Findings ({{findings_count}} total)

{{REPEAT for each finding}}
### Finding {{index}}: {{node_name}}

| Field | Value |
|-------|-------|
| Component | {{component_family}} |
| Physical Site | {{nmu/nsu_site}} |
| Module Base | {{module_base_address}} |
| Error Type | {{isr_active_bit_name}} |
| Error Rule | {{rule_description OR derived from ISR bit (see Fix Guidance table)}} |
| Severity | {{severity OR "Error" if rule not available}} |
| Burst Type | {{burst_type}} |
| AXID | {{axid}} |
| AXLEN / AXSIZE | {{axlen}} / {{axsize}} |
| Error Address | {{reconstructed from err_info_5 + err_info_6}} |

#### Source AXI Master

| Field | Value |
|-------|-------|
| Instance | {{master_instance}} |
| IP Type | {{ip_name}} |
| VLNV | {{vlnv}} |
| NMU Port | {{nmu_port}} |

#### Connectivity Path

```
{{master}}/M_AXI → {{noc}}/{{port}} → NMU ({{nmu_site}}) → NoC → NSU ({{nsu_site}}) → {{slave}}
```

#### Fix

{{corrective_action from analyzer, or derived from rule + design context}}

{{/REPEAT}}

## Status: {{PASS|FAIL}} — {{one-line summary}}
```

---

## Key Constraints

- `sysdbg_noc` owns all error decoding. Never manually decode ERR_NUM or ISR registers.
- Use HSI API for master identification first. If HSI returns empty (common for RTL `module_ref` cells), fall back to BD-level `get_bd_intf_nets` or netlist-level `get_nets`.
- Never run synthesis or implementation.
- Extract NCR only from routed design, never from post-synthesis.
- Use `hsi::open_hw_design` for XSA — never unzip.
- Always `close_project` when done.
- `program_log` is boot-time context only, not a substitute for `sysdbg_noc`.
- Always reset device before programming (`chipscope_device(action="reset")`) to clear sticky NPI error registers.
- Findings can appear at NMU (master-side) or NSU (slave-side). Handle both in correlation.
- When `err_num.rule.status == "not_applicable"`, derive corrective action from the ISR bit name using the Fix Guidance table above.
