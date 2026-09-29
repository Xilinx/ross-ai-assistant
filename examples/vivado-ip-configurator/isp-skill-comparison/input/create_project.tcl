# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# Source through Vivado MCP in a fresh session whose working directory is the
# new trial directory. This creates only the empty starting point, not an ISP.

if {[llength [get_projects -quiet]] != 0} {
    error "Use a fresh Vivado session with no project open."
}
set trial_project_dir [file join [pwd] isp_trial]
if {[file exists $trial_project_dir]} {
    error "isp_trial already exists; choose a new empty trial directory."
}
create_project benchmark $trial_project_dir -part xc2ve3558-sfva1440-2MP-e-S
create_bd_design benchmark_bd
save_bd_design
puts "READY: empty benchmark_bd; Vivado [version -short]"
