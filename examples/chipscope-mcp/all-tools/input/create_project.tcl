# ==============================================================================
# ChipScoPy MCP All-Tools Example Design for VCK190
# ==============================================================================
# This script creates a single Vivado block design that exercises ALL successfully
# validated chipscope MCP tools on VCK190 (Versal xcvc1902).
#
# Tools validated by this design (11 chipscope + 2 sysdbg):
#   chipscope_session     - connect/disconnect/status/tree
#   chipscope_scan        - reset/scan/status (CRITICAL: must reset after reprogram)
#   chipscope_device      - list/select/program
#   chipscope_memory      - read (via NoC → BRAM)
#   chipscope_vio         - read/write output probes, read input probes
#   chipscope_ila_core    - list/status/probes/trigger_immediate/arm/set_trigger
#   chipscope_ila_capture - get_data/export (CSV/VCD)
#   chipscope_sysmon      - read_all (built-in, no IP needed)
#   chipscope_noc         - discover (NMU/NSU elements from axi_noc)
#   chipscope_ddr         - health/calibration/status (LPDDR4 via board automation)
#   chipscope_ddr_eye_scan - read/write eye scan with margin data
#   sysdbg_noc            - analyze (NoC subsystem error scan)
#   sysdbg_noc_timeout    - show/set/enable/disable timeout registers
#
# Not included (requires parent GT controller IP for active data path):
#   chipscope_ibert, chipscope_ibert_eye_scan, chipscope_ibert_yk_scan
#
# Architecture:
#   CIPS ─┬─ FPD_AXI_NOC0 → axi_noc_0 → LPDDR4 (chipscope_ddr, chipscope_ddr_eye_scan,
#         │                                        chipscope_noc, chipscope_memory, sysdbg_noc)
#         └─ pl0_ref_clk  → system clocks
#
#   axi_lite_master (module_ref) → BRAM (AXI4-Lite direct)
#     ├─ VIO controls: start_wr, start_rd, wr_addr, wr_data, rd_addr (out)
#     │                rd_data, busy, done (in)
#     └─ ILA monitors: S_AXI bus (AW/W/B/AR/R channels)
#
# Board: xilinx.com:vck190:part0:3.3
# Part:  xcvc1902-vsva2197-2MP-e-S
#
# Key Operational Notes:
#   1. After programming, ALWAYS call chipscope_scan(action='reset') before rescanning
#   2. DDR calibration takes ~2s after boot; check chipscope_ddr(action='health')
#   3. SysMon is always available (built into Versal silicon, no IP needed)
#
# Usage:
#   source <path>/all_tools_example.tcl
#   # Wait for build to complete (~10 min)
#   # PDI: all_tools_demo/all_tools_demo.runs/impl_1/all_tools_bd_wrapper.pdi
#   # LTX: all_tools_demo/all_tools_demo.runs/impl_1/all_tools_bd_wrapper.ltx
# ==============================================================================

set script_dir [file dirname [file normalize [info script]]]
set proj_dir [file join $script_dir all_tools_demo]

# Clean previous build
if {[file exists $proj_dir]} {
    file delete -force $proj_dir
}

# ==============================================================================
# Create Project
# ==============================================================================
create_project all_tools_demo $proj_dir -part xcvc1902-vsva2197-2MP-e-S
set_property board_part xilinx.com:vck190:part0:3.3 [current_project]

# Add RTL source
file mkdir [file join $proj_dir src]
file copy -force [file join $script_dir src/axi_lite_master.v] [file join $proj_dir src/axi_lite_master.v]
add_files -norecurse [file join $proj_dir src/axi_lite_master.v]
update_compile_order -fileset sources_1

# ==============================================================================
# Create Block Design
# ==============================================================================
create_bd_design "all_tools_bd"

# --- CIPS (Versal PS) ---
set cips [create_bd_cell -type ip -vlnv xilinx.com:ip:versal_cips:3.4 versal_cips_0]
# Enable 2 FPD AXI NoC ports: one for BRAM/NoC, one for DDR
apply_bd_automation -rule xilinx.com:bd_rule:cips -config { \
    board_preset {Yes} \
    boot_config {Custom} \
    configure_noc {Add new AXI NoC} \
    debug_config {JTAG} \
    design_flow {Full System} \
    mc_type {LPDDR} \
    num_mc_ddr {None} \
    num_mc_lpddr {2} \
    pl_clocks {1} \
    pl_resets {1} \
} [get_bd_cells versal_cips_0]

# --- DDR NoC is already created by board automation (axi_noc_0) ---
# It provides: chipscope_noc (NMU/NSU elements), chipscope_ddr, chipscope_ddr_eye_scan,
# chipscope_memory (read DDR addresses), sysdbg_noc, sysdbg_noc_timeout

# --- AXI Lite Master (module_ref RTL) ---
set lite_master [create_bd_cell -type module -reference axi_lite_master axi_lite_master_0]

# Second BRAM for lite_master
set bram_ctrl_1 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 axi_bram_ctrl_1]
set_property -dict [list CONFIG.SINGLE_PORT_BRAM {1} CONFIG.PROTOCOL {AXI4LITE}] $bram_ctrl_1
set bram_mem_1 [create_bd_cell -type ip -vlnv xilinx.com:ip:emb_mem_gen:1.0 emb_mem_gen_1]
set_property CONFIG.MEMORY_TYPE {True_Dual_Port_RAM} $bram_mem_1

# Connect lite_master → BRAM1
connect_bd_intf_net [get_bd_intf_pins axi_lite_master_0/m_axi] [get_bd_intf_pins axi_bram_ctrl_1/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_bram_ctrl_1/BRAM_PORTA] [get_bd_intf_pins emb_mem_gen_1/BRAM_PORTA]

# Clock and reset for lite_master path
connect_bd_net [get_bd_pins versal_cips_0/pl0_ref_clk] [get_bd_pins axi_lite_master_0/aclk]
connect_bd_net [get_bd_pins versal_cips_0/pl0_ref_clk] [get_bd_pins axi_bram_ctrl_1/s_axi_aclk]
connect_bd_net [get_bd_pins versal_cips_0/pl0_resetn] [get_bd_pins axi_lite_master_0/aresetn]
connect_bd_net [get_bd_pins versal_cips_0/pl0_resetn] [get_bd_pins axi_bram_ctrl_1/s_axi_aresetn]

# --- VIO (axis_vio) monitoring lite_master control signals ---
set vio [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_vio:1.0 axis_vio_0]
set_property -dict [list \
    CONFIG.C_NUM_PROBE_IN {3} \
    CONFIG.C_NUM_PROBE_OUT {5} \
    CONFIG.C_PROBE_OUT0_WIDTH {1} \
    CONFIG.C_PROBE_OUT1_WIDTH {1} \
    CONFIG.C_PROBE_OUT2_WIDTH {32} \
    CONFIG.C_PROBE_OUT3_WIDTH {32} \
    CONFIG.C_PROBE_OUT4_WIDTH {32} \
    CONFIG.C_PROBE_IN0_WIDTH {32} \
    CONFIG.C_PROBE_IN1_WIDTH {1} \
    CONFIG.C_PROBE_IN2_WIDTH {1} \
] $vio

connect_bd_net [get_bd_pins versal_cips_0/pl0_ref_clk] [get_bd_pins axis_vio_0/clk]

# VIO outputs → lite_master inputs
connect_bd_net [get_bd_pins axis_vio_0/probe_out0] [get_bd_pins axi_lite_master_0/start_wr]
connect_bd_net [get_bd_pins axis_vio_0/probe_out1] [get_bd_pins axi_lite_master_0/start_rd]
connect_bd_net [get_bd_pins axis_vio_0/probe_out2] [get_bd_pins axi_lite_master_0/wr_addr]
connect_bd_net [get_bd_pins axis_vio_0/probe_out3] [get_bd_pins axi_lite_master_0/wr_data]
connect_bd_net [get_bd_pins axis_vio_0/probe_out4] [get_bd_pins axi_lite_master_0/rd_addr]

# VIO inputs ← lite_master outputs
connect_bd_net [get_bd_pins axi_lite_master_0/rd_data] [get_bd_pins axis_vio_0/probe_in0]
connect_bd_net [get_bd_pins axi_lite_master_0/busy]    [get_bd_pins axis_vio_0/probe_in1]
connect_bd_net [get_bd_pins axi_lite_master_0/done]    [get_bd_pins axis_vio_0/probe_in2]

# --- ILA (axis_ila) monitoring AXI bus ---
set ila [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_ila:1.3 axis_ila_0]
set_property -dict [list \
    CONFIG.C_NUM_MONITOR_SLOTS {1} \
    CONFIG.C_SLOT_0_INTF_TYPE {xilinx.com:interface:aximm_rtl:1.0} \
    CONFIG.C_MON_TYPE {Interface_Monitor} \
    CONFIG.C_DATA_DEPTH {1024} \
] $ila

connect_bd_net [get_bd_pins versal_cips_0/pl0_ref_clk] [get_bd_pins axis_ila_0/clk]
connect_bd_intf_net [get_bd_intf_pins axi_bram_ctrl_1/S_AXI] [get_bd_intf_pins axis_ila_0/SLOT_0_AXI] -boundary_type upper

# ==============================================================================
# Validate and Generate
# ==============================================================================
validate_bd_design
save_bd_design

# Generate output products
generate_target all [get_files all_tools_bd.bd]

# Create HDL wrapper
make_wrapper -files [get_files all_tools_bd.bd] -top
add_files -norecurse [file join $proj_dir all_tools_demo.gen/sources_1/bd/all_tools_bd/hdl/all_tools_bd_wrapper.v]
update_compile_order -fileset sources_1

# ==============================================================================
# Build: Synthesis → Implementation → PDI
# ==============================================================================
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {
    error "Synthesis failed!"
}

launch_runs impl_1 -to_step write_device_image -jobs 8
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "Implementation failed!"
}

puts "============================================================"
puts " BUILD COMPLETE"
puts " PDI: [glob [file join $proj_dir all_tools_demo.runs/impl_1/*.pdi]]"
puts " LTX: [glob [file join $proj_dir all_tools_demo.runs/impl_1/*.ltx]]"
puts "============================================================"
