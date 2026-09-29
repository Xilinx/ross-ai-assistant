<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Summary
This explains how to see previous commands that have been run in Vivado.

# Inside Vivado
When running a command inside vivado use:
```tcl
report_design_analysis -qor_summary
```
**Example:**

```
1. Tool Option Summary
----------------------

+--------------+--------------------------------+------------+
|   Task Name  |             Options            | Directives |
+--------------+--------------------------------+------------+
| synth_design | xcvc1902-viva1596-1LP-e-S      |            |
| opt_design   |                                | Explore    |
| opt_design   | -muxf_remap -control_set_merge |            |
+--------------+--------------------------------+------------+
* Data is available only for the fields shown and not collected for all fields.
```

In this example the following commands were run: 
* `synth_design -part xcvc1902-viva1596-1LP-e-S` 
* `opt_design -directive Explore`
* `opt_design -muxf_remap -control_set_merge`

### DO:
✅ Understand the part being targeted
✅ Understand the default options for each part.
