<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# VIO Debug — Reference Data

## VIO Properties (UG912 — HW_VIO)

| Property | Type | R/W | Description |
|----------|------|-----|-------------|
| CORE_REFRESH_RATE_MS | int | R/W | Auto-refresh interval (ms). 0=disabled. Recommended ≥500 |
| INSTANCE_NAME | string | R/O | RTL instance name |
| IS_ACTIVITY_SUPPORTED | bool | R/O | Activity detection available? |

## VIO Probe Properties

**Input probes (TYPE == vio_input):**

| Property | R/W | Description |
|----------|-----|-------------|
| INPUT_VALUE | R/O | Current value (after `refresh_hw_vio`) |
| ACTIVITY_VALUE | R/O | Transition indicators (↑↓↕) |
| ACTIVITY_PERSISTENCE | R/W | INFINITE, LONG (80 samples), SHORT (8 samples) |
| INPUT_VALUE_RADIX | R/W | BIN, OCT, HEX, UNSIGNED, SIGNED |

**Output probes (TYPE == vio_output):**

| Property | R/W | Description |
|----------|-----|-------------|
| OUTPUT_VALUE | R/W | Value to drive (commit with `commit_hw_vio`) |
| OUTPUT_VALUE_RADIX | R/W | BIN, OCT, HEX, UNSIGNED, SIGNED |

## Tcl Command Quick Reference — VIO

| Command | Purpose |
|---------|---------|
| `get_hw_vios` | List all VIO debug cores on current device |
| `get_hw_probes -of_objects $vio -filter {TYPE == vio_input}` | List input probes |
| `get_hw_probes -of_objects $vio -filter {TYPE == vio_output}` | List output probes |
| `refresh_hw_vio $vio` | Read input values from hardware |
| `refresh_hw_vio -update_output_values $vio` | Sync output values from hardware |
| `set_property OUTPUT_VALUE <val> $probe` | Set value on output probe |
| `commit_hw_vio $vio` | Drive output values to hardware |
| `reset_hw_vio_outputs $vio` | Reset outputs to design-time initial values |
| `reset_hw_vio_activity $vio` | Clear activity detectors |
| `set_property CORE_REFRESH_RATE_MS <ms> $vio` | Set auto-refresh interval |

## vio_snapshot.json Schema

```json
{
  "metadata": {
    "skill": "hw-vio-debug", "version": "1.0.0",
    "mode": "vio", "timestamp": "<ISO8601>",
    "device": "<part>", "vio_core": "hw_vio_1"
  },
  "input_probes": [
    { "name": "status_led", "value": "0011", "activity": "both", "width": 4 }
  ],
  "output_probes": [
    { "name": "rst_n", "value": "0", "width": 1 }
  ],
  "actions_taken": ["Set rst_n=1, committed", "Set rst_n=0, committed"],
  "observations": ["status_led toggling (activity detected)"]
}
```

## References

- **UG908**: Vivado Programming and Debugging — hw_vio, hw_probe Tcl commands
- **UG912**: Vivado Properties Reference — HW_VIO, HW_PROBE properties
- **UG835**: Vivado Tcl Commands Reference — commit_hw_vio, refresh_hw_vio, etc.
- **UG936**: Vivado Tutorial: Programming and Debugging — step-by-step VIO lab
- **PG159**: Virtual Input/Output v3.0 Product Guide — VIO core parameters, ports
