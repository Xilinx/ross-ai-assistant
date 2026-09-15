<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ILA Debug — Reference Data

## ILA CONTROL Properties (UG912 — HW_ILA)

| Property | Type | Values | Description |
|----------|------|--------|-------------|
| CONTROL.DATA_DEPTH | int | 1–MAX_DATA_DEPTH (power of 2) | Samples per window. DATA_DEPTH × WINDOW_COUNT = MAX_DATA_DEPTH |
| CONTROL.WINDOW_COUNT | int | 1–N | Number of capture windows (multiple trigger events) |
| CONTROL.TRIGGER_POSITION | int | 0–(DATA_DEPTH-1) | Position of trigger mark. 0=first sample, 512=mid for 1024 depth |
| CONTROL.TRIGGER_MODE | enum | BASIC_ONLY, BASIC_OR_TRIG_IN, ADVANCED_ONLY, ADVANCED_OR_TRIG_IN, TRIG_IN_ONLY | Trigger source |
| CONTROL.TRIGGER_CONDITION | enum | AND, NAND, OR, NOR | Boolean equation across participating probes |
| CONTROL.CAPTURE_MODE | enum | ALWAYS, BASIC | ALWAYS=every cycle, BASIC=only when capture condition true |
| CONTROL.CAPTURE_CONDITION | enum | AND, NAND, OR, NOR | Boolean for capture qualification |
| CONTROL.TSM_FILE | string | file path | Path to Trigger State Machine (.tsm) file |
| CONTROL.TRIG_OUT_MODE | enum | DISABLED, TRIGGER_ONLY, TRIG_IN_ONLY, TRIGGER_OR_TRIG_IN | TRIG_OUT port behavior |

## ILA STATIC Properties (read-only)

| Property | Type | Description |
|----------|------|-------------|
| STATIC.MAX_DATA_DEPTH | int | Maximum samples (set at design time) |
| STATIC.IS_ADVANCED_TRIGGER_MODE_SUPPORTED | bool | Advanced TSM available? |
| STATIC.IS_BASIC_CAPTURE_MODE_SUPPORTED | bool | Storage qualifier available? |
| STATIC.IS_TRIG_IN_SUPPORTED | bool | TRIG_IN port present? |
| STATIC.IS_TRIG_OUT_SUPPORTED | bool | TRIG_OUT port present? |
| STATIC.TSM_COUNTER_0_WIDTH–3_WIDTH | int | TSM counter bit widths |

## ILA STATUS Properties (read-only)

| Property | Type | Description |
|----------|------|-------------|
| STATUS.CORE_STATUS | string | IDLE, ARMED, WAITING_FOR_TRIGGER, TRIGGER_CAPTURED, FULL |
| STATUS.SAMPLE_COUNT | int | Number of samples captured so far |
| STATUS.IS_TRIGGER_AT_STARTUP | bool | Trigger-at-startup configured? |

## ILA Probe Properties (HW_PROBE, TYPE == ila)

| Property | Type | R/W | Description |
|----------|------|-----|-------------|
| TRIGGER_COMPARE_VALUE | string | R/W | Trigger match pattern (see encoding below) |
| CAPTURE_COMPARE_VALUE | string | R/W | Capture qual match pattern |
| COMPARATOR_COUNT | int | R/O | Number of match units on this probe |
| PROBE_PORT | int | R/O | Physical probe port number |
| PROBE_PORT_BIT_COUNT | int | R/O | Bit width |
| NAME | string | R/O | HDL net name (from .ltx) |

## Compare Value Encoding

Format: `<operator><width>'<radix><value>`

**Operators:**

| Operator | Meaning |
|----------|---------|
| `eq` | Equal (=) |
| `neq` | Not equal (!=) |
| `lt` | Less than (<) |
| `lteq` | Less than or equal (≤) |
| `gt` | Greater than (>) |
| `gteq` | Greater than or equal (≥) |

**Radix:** `b` (binary), `h` (hex), `o` (octal), `u` (unsigned), `s` (signed)

**Special bit values (binary only):**

| Char | Meaning |
|------|---------|
| `X` | Don't care — probe does NOT participate in condition |
| `R` | Rising edge (0→1) |
| `F` | Falling edge (1→0) |
| `B` | Both edges (any transition) |
| `N` | No transition (stable) |
| `L` | Opposite of R |
| `S` | Opposite of F |

**Examples:**
- `eq1'b1` — probe equals 1
- `eq4'hA` — 4-bit probe equals 0xA
- `eq1'bR` — rising edge on 1-bit probe
- `neq8'hFF` — 8-bit probe not equal to 0xFF
- `gt16'u1000` — 16-bit unsigned probe > 1000
- `eq2'bXX` — don't care (probe not participating)

## Trigger State Machine (TSM) Syntax

TSM files define multi-state triggers for `ADVANCED_ONLY` mode. Basic structure:

```
# Comments start with #
state <state_name>:
  if (<probe_condition>) then
    <action>;
  else
    goto <state_name>;
  endif
```

**Actions:** `trigger`, `goto <state>`, `increment_counter <n>`, `reset_counter <n>`

**Probe references:** Use full hierarchical probe names from the .ltx file.

**Example — AXI read transaction trigger:**
```
state wait_arvalid:
  if (axi_arvalid == 1'b1) then
    goto wait_rready;
  else
    goto wait_arvalid;
  endif

state wait_rready:
  if (axi_rready == 1'b1) then
    goto wait_rlast;
  else
    goto wait_rready;
  endif

state wait_rlast:
  if (axi_rlast == 1'b1) then
    trigger;
  else
    goto wait_rlast;
  endif
```

## ILA Design-Time Core Properties (for netlist insertion reference)

| Property | Values | Description |
|----------|--------|-------------|
| C_DATA_DEPTH | 1024–131072 | Sample buffer depth |
| C_NUM_OF_PROBES | 1–1024 | Number of probe ports |
| C_PROBE\<n\>_WIDTH | 1–4096 | Width of probe port n |
| C_ADV_TRIGGER | true/false | Enable advanced trigger / TSM |
| C_EN_STRG_QUAL | true/false | Enable capture condition (storage qualifier) |
| C_TRIGIN_EN | true/false | Enable TRIG_IN port |
| C_TRIGOUT_EN | true/false | Enable TRIG_OUT port |
| C_INPUT_PIPE_STAGES | 0–6 | Pipeline stages on probe inputs (timing) |
| C_ALL_PROBE_SAME_MU_CNT | 1–16 | Match units per probe |
| C_MEMORY_TYPE | 0 (BRAM), 1 (URAM) | Storage primitive (Versal only) |

## Tcl Command Quick Reference — ILA

| Command | Purpose |
|---------|---------|
| `get_hw_ilas` | List all ILA debug cores on current device |
| `current_hw_ila` | Get/set the current ILA |
| `get_hw_probes -of_objects $ila` | List probes on an ILA |
| `set_property CONTROL.* $ila` | Configure trigger/capture settings |
| `set_property TRIGGER_COMPARE_VALUE <val> $probe` | Set trigger match on a probe |
| `run_hw_ila $ila` | Arm ILA for trigger event |
| `run_hw_ila -trigger_now $ila` | Trigger immediately (aliveness check) |
| `run_hw_ila -compile_only $ila` | Compile-check TSM without arming |
| `run_hw_ila -file <path> $ila` | Export trigger config for startup |
| `wait_on_hw_ila $ila` | Block until capture complete |
| `upload_hw_ila_data $ila` | Pull captured data from device |
| `write_hw_ila_data -csv_file <file> $data` | Export as CSV |
| `write_hw_ila_data -vcd_file <file> $data` | Export as VCD |
| `write_hw_ila_data <file> $data` | Export as native .ila |
| `read_hw_ila_data <file>` | Load previously saved .ila file |
| `list_hw_samples $probe` | List sample values for a probe |
| `create_hw_probe -map {...} <name> $ila` | Create custom probe from physical bits |
| `reset_hw_ila $ila` | Reset all CONTROL properties to defaults |

## report_data.json Schema

```json
{
  "metadata": {
    "skill": "hw-ila-debug", "version": "1.0.0",
    "mode": "ila", "timestamp": "<ISO8601>",
    "device": "<part>", "ila_core": "hw_ila_1"
  },
  "ila_config": {
    "data_depth": 1024, "max_depth": 4096,
    "trigger_mode": "BASIC_ONLY", "trigger_condition": "AND",
    "trigger_position": 512, "window_count": 1,
    "capture_mode": "ALWAYS"
  },
  "probes": [
    { "name": "axi_arvalid", "width": 1, "type": "ila",
      "trigger_compare": "eq1'b1", "port": 3 }
  ],
  "capture": {
    "status": "TRIGGER_CAPTURED", "sample_count": 1024,
    "export_file": "capture_data.csv", "export_format": "csv"
  },
  "observations": ["axi_arvalid asserted at sample 512", "..."],
  "recommendations": []
}
```

## References

- **UG908**: Vivado Programming and Debugging — hw_ila, hw_probe Tcl commands
- **UG912**: Vivado Properties Reference — HW_ILA, HW_PROBE properties
- **UG835**: Vivado Tcl Commands Reference — run_hw_ila, write_hw_ila_data, etc.
- **UG936**: Vivado Tutorial: Programming and Debugging — step-by-step ILA lab
- **PG172**: Integrated Logic Analyzer v6.2 Product Guide — ILA core parameters, ports
- **PG357**: ILA with AXI4-Stream Interface Product Guide — Versal ILA
