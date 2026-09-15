---
name: hls-run-flow
description:  Run HLS Flow including C Simulation (csim), C Synthesis (csynth), C/RTL Co-Simulation (cosim), Implementation (impl).
argument-hint: < hls config path $CONFIG >
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Skill: hls-run-flow

## Description

This skill runs one of the four HLS flows based on the keyword provided by the user:

| User keyword | Flow triggered |
|---|---|
| `run csim` | C Simulation |
| `run csynth` | C Synthesis |
| `run cosim` | C/RTL Co-Simulation |
| `run impl` | Implementation |

**Resolving `$CONFIG` and `$WORK_DIR` before execution:**

1. **Locate the HLS config file (`$CONFIG`):**
   - If the user provides a config path explicitly, use it.
   - Otherwise, search the workspace for a `.cfg` file (e.g., `hls_config.cfg`) and use the first one found.
   - `$CONFIG` should be a path relative to the workspace root.

2. **Resolve `$WORK_DIR`:**
   - If the user provides `$WORK_DIR` explicitly, use it directly.
   - Otherwise, read the config file and extract the value of `syn.top` (e.g., `syn.top=top_module_name`), then use that as `$WORK_DIR`.
   - This matches the directory HLS creates for its outputs (e.g., `top_module_name/hls/`).
   - Do **not** use `.` or `hls` or the workspace root as `$WORK_DIR`.

**Prerequisite checks (enforced before execution):**

- Before running **cosim**, verify that csynth has already been executed by checking whether the csynth output directory (`$WORK_DIR/hls/syn/`) exists. If it does not exist, stop and inform the user:
  > "C Synthesis (csynth) must be completed before running Co-Simulation. Please run csynth first."
- Before running **cosim**, always perform a `cosim.argv` preflight check in `$CONFIG`:
  1. Parse `cosim.argv` from `$CONFIG`.
  2. If `cosim.argv` exists and its value is non-empty, continue.
  3. If `cosim.argv` is missing or empty, try fallback from `csim.argv`.
  4. If `csim.argv` exists and its value is non-empty, write the same value to `cosim.argv` in `$CONFIG`, save the file, and continue.
  5. If both are missing or empty, continue. (some HLS versions do not require `cosim.argv` to be set, so we allow it to be empty).

- Before running **impl**, verify that cosim has already been executed by checking whether the cosim output directory (`$WORK_DIR/hls/sim/`) exists. If it does not exist, stop and inform the user:
  > "C/RTL Co-Simulation (cosim) must be completed before running Implementation. Please run cosim first."

If the user's input does not match any of the four keywords above, ask the user to clarify which flow they want to run.

**Execution method selection:**

- Detect whether the skill is running inside the **Vitis Unified IDE** by checking if the `runHlsActiveComponentByType` function is available in the current environment.
- **If inside Vitis Unified IDE** (function is available): prefer calling `runHlsActiveComponentByType` directly.
- **If outside Vitis Unified IDE** (e.g., plain terminal): fall back to the corresponding `bash` command.

## How to run

### Run C Simulation (csim)

**If inside Vitis Unified IDE** (preferred): call `runHlsActiveComponentByType` with type `"0"`.

**If outside Vitis Unified IDE** (fallback):

```bash
vitis-run --mode hls --config $CONFIG --work_dir $WORK_DIR --csim
```


### Run C Synthesis (csynth)

**If inside Vitis Unified IDE** (preferred): call `runHlsActiveComponentByType` with type `"1"`.

**If outside Vitis Unified IDE** (fallback):

```bash
v++ --compile --mode hls --config $CONFIG --work_dir $WORK_DIR
```


### Run C/RTL Co-Simulation (cosim)

Preflight (mandatory): complete the **cosim.argv preflight check** in the prerequisite section before launching cosim.

**If inside Vitis Unified IDE** (preferred): call `runHlsActiveComponentByType` with type `"2"`.

**If outside Vitis Unified IDE** (fallback):

```bash
vitis-run --mode hls --config $CONFIG --work_dir $WORK_DIR --cosim
```


### Run Implementation (impl)

**If inside Vitis Unified IDE** (preferred): call `runHlsActiveComponentByType` with type `"3"`.

**If outside Vitis Unified IDE** (fallback):

```bash
vitis-run --mode hls --config $CONFIG --work_dir $WORK_DIR --impl
```