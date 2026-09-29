# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# create_project.tcl — Mixed-source project for revision-control demo
# Target: VCK190 (xcvc1902-vsva2197-2MP-e-S)
#
# Sources: RTL (top.v) + XCI (clk_wiz_0) + BD (bd_subsystem) + XDC (timing.xdc)
#
# Usage:
#   cd input
#   vivado -mode batch -source create_project.tcl

set script_dir [file dirname [file normalize [info script]]]
set project_name revctrl_demo
set project_dir [file join $script_dir $project_name]

create_project $project_name $project_dir -part xcvc1902-vsva2197-2MP-e-S -force
set_property BOARD_PART xilinx.com:vck190:part0:3.3 [current_project]

# --- 1. Add RTL source ---
add_files -norecurse [file join $script_dir src top.v]
update_compile_order -fileset sources_1

# --- 2. Create standalone XCI: Clocking Wizard (100 → 200 MHz) ---
create_ip -name clk_wizard -vendor xilinx.com -library ip -version 1.0 \
    -module_name clk_wiz_0

set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {100.0} \
    CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY {200.0} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_TYPE {ACTIVE_LOW} \
] [get_ips clk_wiz_0]

generate_target all [get_ips clk_wiz_0]

# --- 3. Create Block Design: CIPS providing pl0_ref_clk + pl0_resetn ---
create_bd_design "bd_subsystem"

set cips [create_bd_cell -type ip -vlnv xilinx.com:ip:versal_cips versal_cips_0]
set_property -dict [list \
    CONFIG.CLOCK_MODE {REF CLK 33.33 MHz} \
    CONFIG.PS_PMC_CONFIG { \
        CLOCK_MODE {REF CLK 33.33 MHz} \
        PMC_CRP_PL0_REF_CTRL_FREQMHZ {100} \
        PS_NUM_FABRIC_RESETS {1} \
        PS_USE_PMCPL_CLK0 {1} \
    } \
] $cips

# Create output ports on the BD
create_bd_port -dir O -type clk pl0_ref_clk
create_bd_port -dir O -type rst pl0_resetn

connect_bd_net [get_bd_pins versal_cips_0/pl0_ref_clk] [get_bd_ports pl0_ref_clk]
connect_bd_net [get_bd_pins versal_cips_0/pl0_resetn] [get_bd_ports pl0_resetn]

validate_bd_design
save_bd_design

# Generate BD wrapper
set bd_wrapper [make_wrapper -files [get_files bd_subsystem.bd] -top]
add_files -norecurse $bd_wrapper

# --- 4. Add XDC constraints ---
add_files -fileset constrs_1 -norecurse [file join $script_dir constraints timing.xdc]

# --- Set top module ---
set_property top top [current_fileset]
update_compile_order -fileset sources_1

# Generate all targets
generate_target all [get_files bd_subsystem.bd]

puts "============================================"
puts "Project created: $project_dir"
puts "Sources: RTL (top.v) + XCI (clk_wiz_0) + BD (bd_subsystem) + XDC (timing.xdc)"
puts "============================================"
puts "Next: Use /vivado-revision-control to export sources and generate build.tcl"
