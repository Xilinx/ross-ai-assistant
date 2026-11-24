# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
################################################################
# NoC Decode-Error Example — Project Creation Script
#
# Minimal VCK190 (xcvc1902) design that reliably reproduces a single Versal NoC
# write address-decode error (REG_ISR.addr_map_wr) for the hw-noc-debug skill.
#
# Contents:
#   - CIPS (PS) providing pl0_ref_clk (100 MHz), pl0_resetn, and an FPD AXI NoC port
#   - AXI NoC (2 SI / 1 MI)
#   - AXI BRAM Controller + Embedded Memory (the only valid slave @ 0x201_0000_0000)
#   - axi_decode_error_master: writes once to an UNMAPPED address (0x201_FFFF_0000)
#     -> the NMU has no route -> latches addr_map_wr and returns AXI DECERR.
#
# Usage:
#   vivado -mode tcl -source create_project.tcl
#   # then:
#   launch_runs impl_1 -to_step write_device_image -jobs 8
#   wait_on_run impl_1
#   # PDI: noc_decode_error.runs/impl_1/noc_decode_wrapper.pdi
#
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
################################################################

set script_dir [file dirname [file normalize [info script]]]
set project_name noc_decode_error
set project_dir [file join $script_dir $project_name]

create_project $project_name $project_dir -part xcvc1902-vsva2197-2MP-e-S -force
set_property BOARD_PART xilinx.com:vck190:part0:3.3 [current_project]

# Add RTL sources (must be added before BD creation for module_ref resolution)
add_files -norecurse [glob [file join $script_dir src *.v]]
update_compile_order -fileset sources_1

################################################################
# Block Design: noc_decode
################################################################
create_bd_design noc_decode

# --- CIPS ---
set versal_cips_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:versal_cips versal_cips_0]
set_property -dict [list \
  CONFIG.CLOCK_MODE {REF CLK 33.33 MHz} \
  CONFIG.PS_PMC_CONFIG { \
    CLOCK_MODE {REF CLK 33.33 MHz} \
    PMC_CRP_PL0_REF_CTRL_FREQMHZ {100} \
    PMC_CRP_NOC_REF_CTRL_FREQMHZ {960} \
    PS_NUM_FABRIC_RESETS {1} \
    PS_USE_FPD_AXI_NOC0 {1} \
    PS_USE_FPD_AXI_NOC1 {0} \
    PS_USE_FPD_CCI_NOC {0} \
    PS_USE_PMCPL_CLK0 {1} \
    PS_USE_PMCPL_IRO_CLK {1} \
  } \
] $versal_cips_0

# --- AXI NoC (2 SI / 1 MI) ---
set axi_noc_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_noc axi_noc_0]
set_property -dict [list \
  CONFIG.NUM_CLKS {2} \
  CONFIG.NUM_MI {1} \
  CONFIG.NUM_NMI {0} \
  CONFIG.NUM_NSI {0} \
  CONFIG.NUM_SI {2} \
] $axi_noc_0

# M00_AXI -> BRAM at 0x201_0000_0000 (the only mapped aperture)
set_property -dict [list CONFIG.APERTURES {{0x201_0000_0000 1G}}] \
  [get_bd_intf_pins $axi_noc_0/M00_AXI]

# S00_AXI <- PS FPD (present but inactive)
set_property -dict [list \
  CONFIG.CONNECTIONS {M00_AXI {read_bw {500} write_bw {500} read_avg_burst {4} write_avg_burst {4}}} \
  CONFIG.DEST_IDS {M00_AXI:0xc0} \
  CONFIG.CATEGORY {ps_nci} \
] [get_bd_intf_pins $axi_noc_0/S00_AXI]

# S01_AXI <- decode_error_master (writes to unmapped address -> addr_map_wr)
set_property -dict [list \
  CONFIG.CONNECTIONS {M00_AXI {read_bw {500} write_bw {500} read_avg_burst {4} write_avg_burst {4}}} \
  CONFIG.DEST_IDS {M00_AXI:0xc0} \
  CONFIG.CATEGORY {pl} \
] [get_bd_intf_pins $axi_noc_0/S01_AXI]

# Clock associations
set_property -dict [list CONFIG.ASSOCIATED_BUSIF {S00_AXI}] \
  [get_bd_pins $axi_noc_0/aclk0]
set_property -dict [list CONFIG.ASSOCIATED_BUSIF {M00_AXI:S01_AXI}] \
  [get_bd_pins $axi_noc_0/aclk1]

# --- BRAM Controller + Memory ---
set axi_bram_ctrl_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl axi_bram_ctrl_0]
set_property CONFIG.SINGLE_PORT_BRAM {1} $axi_bram_ctrl_0
set emb_mem_gen_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:emb_mem_gen emb_mem_gen_0]

# --- Decode-error master ---
set decode_master_0 [create_bd_cell -type module -reference axi_decode_error_master decode_master_0]
set_property -dict [list \
  CONFIG.TARGET_ADDR {0x00000201FFFF0000} \
  CONFIG.STARTUP_DELAY {1000} \
] $decode_master_0

# --- Interface Connections ---
connect_bd_intf_net [get_bd_intf_pins axi_bram_ctrl_0/BRAM_PORTA] \
                    [get_bd_intf_pins emb_mem_gen_0/BRAM_PORTA]
connect_bd_intf_net [get_bd_intf_pins axi_noc_0/M00_AXI] \
                    [get_bd_intf_pins axi_bram_ctrl_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins decode_master_0/m_axi] \
                    [get_bd_intf_pins axi_noc_0/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins versal_cips_0/FPD_AXI_NOC_0] \
                    [get_bd_intf_pins axi_noc_0/S00_AXI]

# --- Clock/Reset Connections ---
connect_bd_net [get_bd_pins versal_cips_0/fpd_axi_noc_axi0_clk] \
               [get_bd_pins axi_noc_0/aclk0]
connect_bd_net [get_bd_pins versal_cips_0/pl0_ref_clk] \
               [get_bd_pins axi_noc_0/aclk1] \
               [get_bd_pins axi_bram_ctrl_0/s_axi_aclk] \
               [get_bd_pins decode_master_0/aclk]
connect_bd_net [get_bd_pins versal_cips_0/pl0_resetn] \
               [get_bd_pins axi_bram_ctrl_0/s_axi_aresetn] \
               [get_bd_pins decode_master_0/aresetn]

# --- Address Map ---
# Map the BRAM into both the PS FPD space and the master's space. The master's
# TARGET_ADDR (0x201_FFFF_0000) is intentionally left UNMAPPED -> decode error.
assign_bd_address -offset 0x020100000000 -range 0x00002000 \
  -target_address_space [get_bd_addr_spaces versal_cips_0/FPD_AXI_NOC_0] \
  [get_bd_addr_segs axi_bram_ctrl_0/S_AXI/Mem0] -force
assign_bd_address -offset 0x020100000000 -range 0x00002000 \
  -target_address_space [get_bd_addr_spaces decode_master_0/m_axi] \
  [get_bd_addr_segs axi_bram_ctrl_0/S_AXI/Mem0] -force

validate_bd_design
save_bd_design

################################################################
# Generate wrapper and prepare for implementation
################################################################
make_wrapper -files [get_files noc_decode.bd] -top
add_files -norecurse [file join $project_dir ${project_name}.gen sources_1 bd noc_decode hdl noc_decode_wrapper.v]
update_compile_order -fileset sources_1

puts "================================================================"
puts " Project created: $project_dir/${project_name}.xpr"
puts ""
puts " To build:"
puts "   launch_runs impl_1 -to_step write_device_image -jobs 8"
puts "   wait_on_run impl_1"
puts ""
puts " PDI output: noc_decode_error.runs/impl_1/noc_decode_wrapper.pdi"
puts "================================================================"
