# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Synthesize the example design to a post-synthesis checkpoint. Nothing is
# placed or routed. Usage:
#   vivado -mode batch -source build.tcl -tclargs usp    <out.dcp>
#   vivado -mode batch -source build.tcl -tclargs versal <out.dcp> ?<project dir>?
#
# usp:    rtl/nd_top.v is the top on the KCU116 part xcku5p-ffvb676-2-e.
# versal: a block design on the VMK180 part xcvm1802-vsva2197-2MP-e-S. A CIPS
#         gives two PL clocks and one PL reset to nd_top, which is a module
#         reference. The skill needs a CIPS with a PL reset on Versal.
# Both builds keep everything in one Vivado process (no synthesis runs).

set here [file dirname [file normalize [info script]]]
if {[llength $argv] < 2} { error "usage: build.tcl usp|versal <out.dcp> ?<project dir>?" }
set target [lindex $argv 0]
set out [file normalize [lindex $argv 1]]
file mkdir [file dirname $out]
set_param general.maxThreads 2

if {$target eq "usp"} {
    read_verilog $here/rtl/nd_top.v
    read_xdc $here/constraints/nd_top_usp.xdc
    synth_design -top nd_top -part xcku5p-ffvb676-2-e
} elseif {$target eq "versal"} {
    set proj [file dirname $out]/nd_versal_proj
    if {[llength $argv] > 2} { set proj [file normalize [lindex $argv 2]] }
    set bp [lindex [get_board_parts -quiet -latest_file_version "*vmk180:part0*"] 0]
    if {$bp eq ""} { error "no vmk180 board part in this Vivado" }
    create_project -force nd_versal $proj -part xcvm1802-vsva2197-2MP-e-S
    set_property board_part $bp [current_project]
    add_files $here/rtl/nd_top.v
    update_compile_order -fileset sources_1
    create_bd_design bd
    create_bd_cell -type ip -vlnv xilinx.com:ip:versal_cips:* cips
    apply_bd_automation -rule xilinx.com:bd_rule:cips -config { board_preset {Yes} boot_device {Auto} debug_config {JTAG} design_flow {Full System} mc_type {None} num_mc {1} pl_clocks {2} pl_resets {1} } [get_bd_cells cips]
    create_bd_cell -type module -reference nd_top u_nd
    connect_bd_net [get_bd_pins cips/pl0_ref_clk] [get_bd_pins u_nd/clk_a]
    connect_bd_net [get_bd_pins cips/pl1_ref_clk] [get_bd_pins u_nd/clk_b]
    connect_bd_net [get_bd_pins cips/pl0_resetn] [get_bd_pins u_nd/rst_n]
    make_bd_pins_external [get_bd_pins u_nd/led]
    validate_bd_design
    save_bd_design
    set bdf [get_files bd.bd]
    set_property synth_checkpoint_mode None $bdf
    generate_target all $bdf
    make_wrapper -files $bdf -top
    add_files -norecurse [glob $proj/nd_versal.gen/sources_1/bd/bd/hdl/bd_wrapper.v]
    set_property top bd_wrapper [current_fileset]
    update_compile_order -fileset sources_1
    synth_design -top bd_wrapper -part xcvm1802-vsva2197-2MP-e-S
} else {
    error "unknown target '$target': use usp or versal"
}

write_checkpoint -force $out
puts "ND_BUILD_DONE $target $out"
