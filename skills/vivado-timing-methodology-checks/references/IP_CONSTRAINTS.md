<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# IP_CONSTRAINTS
IP constraints are important because users can not easily edit them.
They are also scoped. Searching for hierarchy names needs to account for this.

## Identifying IP constraints
There are a few ways to confirm a file is from an IP and not a user source.

### Scoping
IP constraints will have their own constraints file.
IP constraint files are scoped to an instance using `current_instance <hierarchy>`
Ignore `current_instance -quiet` where no hierarchy is specified.

In constraints.xdc this will have a header section and a current_instance defined inside the section
```xdc
# The next 3 lines are the header
####################################################################################
# Constraints from file : 'hwtest_harness_top_clk_wizard_0_0.xdc'
####################################################################################

# The next line is NOT the current instance definition
current_instance -quiet
# The next line IS the current instance definition
current_instance hwtest_harness_top_i/Clock_Gen/clk_wizard_0/inst
create_clock -period 3.000 [get_ports -scoped_to_current_instance clk_in1]
set_input_jitter [get_clocks -of_objects [get_ports -scoped_to_current_instance clk_in1]] 0.100

```

### Hierarchy
Another way of telling if a cell is an IP is to look at the hierarchy:
```
hwtest_harness_top_i/Clock_Gen/clk_wizard_0/inst/clock_primitive_inst/MMCME5_inst
```
`clk_wizard_0` is the default name of the IP instance
`inst` is the hierarchy wrapper of the IP when genertated in Verilog
`u1` is the hierarchy wrapper of the IP when genertated in VHDL
`clock_primitive_inst` is next wrapper level in the clocking wizard IP

### Compile Order
IP runs run OOC will have their own fileset.
Run `report_compile_order`

```
# The following has fileset clk_wizard_0
# The constraints files are scoped to ref and scoped to cells
Constraint evaluation order for 'synthesis' with fileset 'clk_wizard_0':
Index  File Name               Used_In       Scoped_To_Ref  Scoped_To_Cells  Processing_Order  Out_Of_Context  Full Path Name
-----  ----------------------  ------------  -------------  ---------------  ----------------  --------------  ----------------------------------------------------------------------------------
1      clk_wizard_0_ooc.xdc    Synth & Impl  clk_wizard_0   inst             EARLY             yes             c:/Temp/project_28/project_28.gen/sources_1/ip/clk_wizard_0/clk_wizard_0_ooc.xdc
2      clk_wizard_0_board.xdc  Synth & Impl  clk_wizard_0   inst             EARLY                             c:/Temp/project_28/project_28.gen/sources_1/ip/clk_wizard_0/clk_wizard_0_board.xdc
3      clk_wizard_0.xdc        Synth & Impl  clk_wizard_0   inst             EARLY                             c:/Temp/project_28/project_28.gen/sources_1/ip/clk_wizard_0/clk_wizard_0.xdc
4      clk_wizard_0_late.xdc   Synth & Impl  clk_wizard_0   inst             LATE                              c:/Temp/project_28/project_28.gen/sources_1/ip/clk_wizard_0/clk_wizard_0_late.xdc
```


## IP Specific Resolutions

### Clocking Wizard
Configuring the ip differently can remove the constraint file

**Resolution:**
1. Remove the input buffer
```tcl
# Example clocking wizard configuration change to remove the create clock IP constraint
create_ip -name clk_wizard -vendor xilinx.com -library ip -version 1.0 -module_name clk_wizard_0
set_property CONFIG.PRIM_SOURCE {No_buffer} [get_ips clk_wizard_0]

```
