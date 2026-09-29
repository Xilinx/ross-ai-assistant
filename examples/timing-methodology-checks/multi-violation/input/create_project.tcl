# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# create_project.tcl — Dual-clock design with deliberate timing violations
# Target: xcvc1902-vsva2197-2MP-e-S (VCK190)
#
# Usage:
#   cd input
#   vivado -mode batch -source create_project.tcl
# Then synthesize:
#   launch_runs synth_1 -jobs 8
#   wait_on_run synth_1

set script_dir [file dirname [file normalize [info script]]]
set project_name timing_violations
set project_dir [file join $script_dir $project_name]

create_project $project_name $project_dir -part xcvc1902-vsva2197-2MP-e-S -force

# Add RTL
add_files -norecurse [file join $script_dir src timing_violation_top.v]
set_property top timing_violation_top [current_fileset]
update_compile_order -fileset sources_1

# Add (broken) constraints
add_files -fileset constrs_1 -norecurse [file join $script_dir constraints timing_broken.xdc]

puts "============================================"
puts "Project created: $project_dir"
puts "Design: timing_violation_top (dual-clock with CDC)"
puts "Constraints: timing_broken.xdc (intentional methodology violations)"
puts "============================================"
puts "Next: launch_runs synth_1 -jobs 8 ; wait_on_run synth_1"
puts "Then: Use /timing-methodology-checks to resolve violations"
