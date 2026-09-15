---
name: hls-dataflow-infos
description: 'Get Vitis HLS dataflow process/channel informations for a given HLS component.'
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# hls-dataflow-infos

This skill collects **Vitis HLS dataflow** information (processes + channels) from an HLS component directory.

It is implemented by calling the script:

- [run.sh](./scripts/run.sh)

That script:
- takes exactly **one argument**: `<component_location>`
- prints a JSON array describing all detected dataflow modules, including per-process and per-channel fields

## Inputs

- `component_location` (string, required) — absolute path to the Vitis HLS component directory (the folder containing `vitis-comp.json`).
  - If the user specifies a component_location: use the user-specified component_location.
  - If the user does not specify component_location:
    - If inside Vitis Unified IDE: Call the `getActiveComponentLocation` tool to get the component_location.
    - If outside Vitis Unified IDE: Request the user to provide component_location.

## Output

- `stdout`: JSON (pretty-printed)
  - Top-level: a JSON array; each element corresponds to one dataflow module.

## How to invoke

Must `cd` into `./scripts/` first.

```bash
cd ./scripts
./run.sh <component_location>
```
