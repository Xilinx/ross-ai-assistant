<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Export Mixed-Source IPI Project — Quick Start Prompts

The example ships as source only (RTL + Tcl). Create the project first, then
run the revision-control skill on it.

## Step 1 — Create the project from source

Either run it yourself:

```bash
cd input
vivado -mode batch -source create_project.tcl
```

or ask the agent:

```
Open Vivado and source input/create_project.tcl to create the project.
```

## Step 2 — Export sources and generate a portable build script

```
Use /vivado-revision-control to make this project portable for version control.
Export all sources (RTL, XDC, IP, Block Design) and generate a self-contained
build.tcl that can recreate the project from scratch on any machine.
```

## Step 3 — Verify the generated build script

```
Close the current project. Then run the generated build.tcl in a fresh
Vivado session to verify it recreates the project correctly. Confirm the
top module, IP, and Block Design are all present.
```
