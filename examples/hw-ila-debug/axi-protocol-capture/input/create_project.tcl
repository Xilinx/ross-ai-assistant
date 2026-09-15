# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# create_project.tcl — AXI-Lite + AXI-Stream debug design with ILA + VIO
# Target: VCK190 (xcvc1902-vsva2197-2MP-e-S)
#
# Usage:
#   cd input
#   vivado -mode batch -source create_project.tcl
# Then build:
#   launch_runs impl_1 -to_step write_device_image -jobs 8
#   wait_on_run impl_1

set script_dir [file dirname [file normalize [info script]]]
set project_name axi_debug
set project_dir [file join $script_dir $project_name]

create_project $project_name $project_dir -part xcvc1902-vsva2197-2MP-e-S -force
set_property BOARD_PART xilinx.com:vck190:part0:3.3 [current_project]

# Add RTL sources
add_files -norecurse [glob [file join $script_dir src *.v]]
update_compile_order -fileset sources_1

# Create block design
create_bd_design "axi_debug_bd"

# --- CIPS (clock + reset) ---
set cips [create_bd_cell -type ip -vlnv xilinx.com:ip:versal_cips versal_cips_0]
set_property -dict [list \
  CONFIG.CLOCK_MODE {REF CLK 33.33 MHz} \
  CONFIG.PS_PMC_CONFIG { \
    CLOCK_MODE {REF CLK 33.33 MHz} \
    PMC_CRP_PL0_REF_CTRL_FREQMHZ {100} \
    PS_NUM_FABRIC_RESETS {1} \
    PS_USE_PMCPL_CLK0 {1} \
    PS_USE_PMCPL_IRO_CLK {1} \
  } \
] $cips

# --- AXI-Lite Master (VIO-controlled) ---
set lite_master [create_bd_cell -type module -reference axi_lite_master lite_master_0]

# --- AXI-Stream Packet Generator (VIO-controlled) ---
set axis_gen [create_bd_cell -type module -reference axis_pkt_gen axis_pkt_gen_0]

# --- AXI BRAM Controller + BRAM (slave for AXI-Lite) ---
set bram_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl axi_bram_ctrl_0]
set_property -dict [list \
  CONFIG.SINGLE_PORT_BRAM {1} \
  CONFIG.PROTOCOL {AXI4LITE} \
] $bram_ctrl
set emb_mem [create_bd_cell -type ip -vlnv xilinx.com:ip:emb_mem_gen emb_mem_gen_0]

# --- AXI-Stream FIFO (sink for streaming path) ---
set axis_fifo [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_data_fifo axis_fifo_0]
set_property -dict [list \
  CONFIG.FIFO_DEPTH {256} \
  CONFIG.FIFO_MODE {1} \
] $axis_fifo

# --- VIO Core (axis_vio for Versal) ---
set vio [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_vio:1.0 vio_0]
set_property -dict [list \
  CONFIG.C_NUM_PROBE_IN {5} \
  CONFIG.C_NUM_PROBE_OUT {8} \
  CONFIG.C_PROBE_IN0_WIDTH {32} \
  CONFIG.C_PROBE_IN1_WIDTH {1} \
  CONFIG.C_PROBE_IN2_WIDTH {1} \
  CONFIG.C_PROBE_IN3_WIDTH {1} \
  CONFIG.C_PROBE_IN4_WIDTH {16} \
  CONFIG.C_PROBE_OUT0_WIDTH {1} \
  CONFIG.C_PROBE_OUT1_WIDTH {1} \
  CONFIG.C_PROBE_OUT2_WIDTH {32} \
  CONFIG.C_PROBE_OUT3_WIDTH {32} \
  CONFIG.C_PROBE_OUT4_WIDTH {32} \
  CONFIG.C_PROBE_OUT5_WIDTH {1} \
  CONFIG.C_PROBE_OUT6_WIDTH {8} \
  CONFIG.C_PROBE_OUT7_WIDTH {1} \
] $vio
# VIO OUT: 0=start_wr, 1=start_rd, 2=wr_addr, 3=wr_data, 4=rd_addr,
#          5=start_stream, 6=pkt_length, 7=sink_read_en
# VIO IN:  0=rd_data, 1=busy, 2=done, 3=stream_busy, 4=pkt_count

# --- System ILA (axis_ila for Versal — monitors AXI-Lite + AXI-Stream) ---
set sysila [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_ila:1.3 system_ila_0]
set_property -dict [list \
  CONFIG.C_NUM_MONITOR_SLOTS {2} \
  CONFIG.C_MON_TYPE {Mixed} \
  CONFIG.C_SLOT_0_INTF_TYPE {xilinx.com:interface:aximm_rtl:1.0} \
  CONFIG.C_SLOT_0_AXI_PROTOCOL {AXI4LITE} \
  CONFIG.C_SLOT_1_INTF_TYPE {xilinx.com:interface:axis_rtl:1.0} \
  CONFIG.C_NUM_OF_PROBES {2} \
  CONFIG.C_PROBE0_WIDTH {1} \
  CONFIG.C_PROBE1_WIDTH {1} \
  CONFIG.C_DATA_DEPTH {1024} \
] $sysila

# --- Connections ---

# Clock and Reset
connect_bd_net [get_bd_pins versal_cips_0/pl0_ref_clk] \
  [get_bd_pins lite_master_0/aclk] \
  [get_bd_pins axis_pkt_gen_0/aclk] \
  [get_bd_pins axi_bram_ctrl_0/s_axi_aclk] \
  [get_bd_pins axis_fifo_0/s_axis_aclk] \
  [get_bd_pins vio_0/clk] \
  [get_bd_pins system_ila_0/clk]

connect_bd_net [get_bd_pins versal_cips_0/pl0_resetn] \
  [get_bd_pins lite_master_0/aresetn] \
  [get_bd_pins axis_pkt_gen_0/aresetn] \
  [get_bd_pins axi_bram_ctrl_0/s_axi_aresetn] \
  [get_bd_pins axis_fifo_0/s_axis_aresetn]

# VIO outputs -> lite_master control
connect_bd_net [get_bd_pins vio_0/probe_out0] [get_bd_pins lite_master_0/start_wr]
connect_bd_net [get_bd_pins vio_0/probe_out1] [get_bd_pins lite_master_0/start_rd]
connect_bd_net [get_bd_pins vio_0/probe_out2] [get_bd_pins lite_master_0/wr_addr]
connect_bd_net [get_bd_pins vio_0/probe_out3] [get_bd_pins lite_master_0/wr_data]
connect_bd_net [get_bd_pins vio_0/probe_out4] [get_bd_pins lite_master_0/rd_addr]

# VIO outputs -> axis_pkt_gen control
connect_bd_net [get_bd_pins vio_0/probe_out5] [get_bd_pins axis_pkt_gen_0/start_stream]
connect_bd_net [get_bd_pins vio_0/probe_out6] [get_bd_pins axis_pkt_gen_0/pkt_length]

# lite_master status -> VIO inputs
connect_bd_net [get_bd_pins lite_master_0/rd_data] [get_bd_pins vio_0/probe_in0]
connect_bd_net [get_bd_pins lite_master_0/busy] [get_bd_pins vio_0/probe_in1]
connect_bd_net [get_bd_pins lite_master_0/done] [get_bd_pins vio_0/probe_in2]

# axis_pkt_gen status -> VIO inputs
connect_bd_net [get_bd_pins axis_pkt_gen_0/stream_busy] [get_bd_pins vio_0/probe_in3]
connect_bd_net [get_bd_pins axis_pkt_gen_0/pkt_count] [get_bd_pins vio_0/probe_in4]

# AXI-Lite: lite_master -> bram_ctrl (make AXI-Lite connection)
connect_bd_intf_net [get_bd_intf_pins lite_master_0/m_axi] \
  [get_bd_intf_pins axi_bram_ctrl_0/S_AXI]

# BRAM ctrl -> BRAM
connect_bd_intf_net [get_bd_intf_pins axi_bram_ctrl_0/BRAM_PORTA] \
  [get_bd_intf_pins emb_mem_gen_0/BRAM_PORTA]

# AXI-Stream: pkt_gen -> axis_fifo
connect_bd_intf_net [get_bd_intf_pins axis_pkt_gen_0/m_axis] \
  [get_bd_intf_pins axis_fifo_0/S_AXIS]

# System ILA slot 0: monitor AXI-Lite bus
connect_bd_intf_net [get_bd_intf_pins system_ila_0/SLOT_0_AXI] \
  [get_bd_intf_pins axi_bram_ctrl_0/S_AXI]

# System ILA slot 1: monitor AXI-Stream bus
connect_bd_intf_net [get_bd_intf_pins system_ila_0/SLOT_1_AXIS] \
  [get_bd_intf_pins axis_fifo_0/S_AXIS]

# System ILA extra probes: done + stream_busy
connect_bd_net [get_bd_pins lite_master_0/done] [get_bd_pins system_ila_0/probe0]
connect_bd_net [get_bd_pins axis_pkt_gen_0/stream_busy] [get_bd_pins system_ila_0/probe1]

# --- Address map ---
assign_bd_address [get_bd_addr_segs axi_bram_ctrl_0/S_AXI/Mem0] \
  -offset 0x00000000 -range 0x00002000

# --- Validate and generate ---
validate_bd_design
save_bd_design

# Generate wrapper
set wrapper [make_wrapper -files [get_files axi_debug_bd.bd] -top]
add_files -norecurse $wrapper
set_property top axi_debug_bd_wrapper [current_fileset]
update_compile_order -fileset sources_1

# Generate targets
generate_target all [get_files axi_debug_bd.bd]

puts "Project created: $project_dir"
puts "Next: launch_runs impl_1 -to_step write_device_image -jobs 8"
