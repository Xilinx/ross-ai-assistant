<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# IP_CONSTRAINTS
IP constraints are important because users can not easily edit them.
They are also scoped. Searching for hierarchy names needs to account for this.

## Identifying IP constraints
Read `constraints_profile.json` (`./methodology_results/tmp/`) — the single source of truth for IP vs
user RTL, produced in Step 1 by `<skill_dir>/scripts/run_methodology_setup.tcl` (via its
`methodology_check::profile_constraints` proc).

* Per constraint: `constraints["<id>"]["is_ip"]` and `["ip_instance"]` (the IP hierarchy it is scoped to).
  The ID is the "constraint position" in the methodology message.
* Per hierarchy: the top-level `ip_instances` list.

```json
{"ip_instances": ["hwtest_harness_top_i/Clock_Gen/clk_wizard_0/inst"]}
```

A hierarchy is IP **iff** it equals, or is nested under (`"<entry>/"`), one of the
listed `ip_instances`. Any hierarchy not matched that way is user RTL. A constraint is IP when its
`scope` (the `current_instance` in effect) is such a hierarchy. The rule: the last path segment
of the `current_instance` is `inst` (Verilog IP) or `U0` (VHDL IP).

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

