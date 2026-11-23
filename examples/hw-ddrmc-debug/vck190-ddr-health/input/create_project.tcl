# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# create_project.tcl — ChipScoPy example design (CED) for VCK190, used by the
# hw-ddrmc-debug example
# Target: VCK190 (xcvc1902-vsva2197-2MP-e-S)
#
# Instantiates the Vivado configurable example design xilinx.com:design:chipscopy,
# which ships with Vivado. Its NoC connects a DDR4 memory controller (DDRMC) that
# the hw-ddrmc-debug skill checks for calibration health and margins.
#
# The CED supports one VCK190 board-file version per Vivado release, so this
# script reads the supported version from the CED instead of hard-coding it.
#
# Usage:
#   cd input
#   vivado -mode batch -source create_project.tcl
# Then build:
#   launch_runs impl_1 -to_step write_device_image -jobs 8
#   wait_on_run impl_1

set script_dir [file dirname [file normalize [info script]]]
set project_name ddr_debug
set project_dir [file join $script_dir $project_name]
set bd_name chipscopy

create_project $project_name $project_dir -part xcvc1902-vsva2197-2MP-e-S -force

# Pick the newest chipscopy CED this Vivado ships, and the VCK190 board-file
# version it supports.
set ced [lindex [lsort -dictionary [lsearch -all -inline -glob [get_example_designs] xilinx.com:design:chipscopy:*]] end]
if {$ced eq ""} {
  error "this Vivado has no xilinx.com:design:chipscopy example design"
}
set vck190 [lsearch -inline -glob [get_property SUPPORTED_BOARDS $ced] xilinx.com:vck190:*]
if {$vck190 eq ""} {
  error "$ced does not support VCK190 (SUPPORTED_BOARDS: [get_property SUPPORTED_BOARDS $ced])"
}
set board_part "xilinx.com:vck190:part0:[lindex [split $vck190 :] end]"
set_property BOARD_PART $board_part [current_project]
puts "Using $ced with board part $board_part"

# The CED script builds into the block design named "chipscopy" and creates
# the HDL wrapper (chipscopy_wrapper) as the top.
create_bd_design $bd_name
instantiate_example_design -template $ced -design $bd_name
# CED scripts may leave Vivado in their own install directory.
cd $script_dir
open_bd_design [get_files $bd_name.bd]
validate_bd_design
save_bd_design
