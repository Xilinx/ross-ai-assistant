# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Reads the saved design of one trial and checks the PL clock request against it.
# Run in batch mode, not through the agent:
#   vivado -mode batch -nojournal -nolog -source check_pl_clocks.tcl -tclargs <trial_dir>/proj/proj.xpr
# Prints one CHECK line per setting and a final RESULT line (PASS only if every check passes).
# Exits 0 on PASS, 1 on FAIL, 2 when the design or the cell cannot be found.

set xpr [lindex $argv 0]
if {$xpr eq "" || ![file isfile $xpr]} {puts "RESULT: ERROR no project at '$xpr'"; exit 2}
open_project $xpr
set bd [get_files -quiet design_1.bd]
if {$bd eq ""} {puts "RESULT: ERROR no block design design_1"; exit 2}
open_bd_design $bd
set cell [get_bd_cells -quiet ps_wizard_0]
if {$cell eq ""} {puts "RESULT: ERROR no cell ps_wizard_0"; exit 2}
puts "CELL: [get_property VLNV $cell]"

# A setting is read as a top-level CONFIG.<name> property when the IP has one, and otherwise as
# a key of the nested CONFIG.PS11_CONFIG parameter, where ps_wizard keeps its PS settings.
# PS11_CONFIG stores only keys that were written: on a new ps_wizard it is empty, and a key that
# is absent has the IP's default, listed here (ps_wizard 1.0, Vivado 2026.1).
set defaults {
    PS_USE_PMCPL_CLK0 0 PS_USE_PMCPL_CLK1 0 PS_USE_PMCPL_CLK2 0 PS_USE_PMCPL_CLK3 0
    PMC_CRP_PL0_REF_CTRL_FREQMHZ 334 PMC_CRP_PL1_REF_CTRL_FREQMHZ 334
    PMC_CRP_PL2_REF_CTRL_FREQMHZ 334 PS_NUM_FABRIC_RESETS 0
}
set nested {}
catch {set nested [get_property CONFIG.PS11_CONFIG $cell]}
proc setting {cell nested defaults name} {
    set flat [get_property -quiet CONFIG.$name $cell]
    if {$flat ne ""} {return $flat}
    if {![catch {dict get $nested $name} value]} {return $value}
    return "[dict get $defaults $name] (default, not written)"
}

# Numbers compare as numbers, so 100 and 100.000 MHz are the same value.
proc same {a b} {
    if {[string is double -strict $a] && [string is double -strict $b]} {return [expr {$a == $b}]}
    return [string equal -nocase $a $b]
}

set expected {
    PS_USE_PMCPL_CLK0            1
    PMC_CRP_PL0_REF_CTRL_FREQMHZ 100
    PS_USE_PMCPL_CLK1            1
    PMC_CRP_PL1_REF_CTRL_FREQMHZ 150
    PS_USE_PMCPL_CLK2            1
    PMC_CRP_PL2_REF_CTRL_FREQMHZ 134
    PS_USE_PMCPL_CLK3            0
    PS_NUM_FABRIC_RESETS         1
}
set failed 0
dict for {name want} $expected {
    set got [setting $cell $nested $defaults $name]
    set ok [same [lindex $got 0] $want]
    if {!$ok} {incr failed}
    puts [format "CHECK: %-4s %-30s expected %-5s got %s" [expr {$ok ? "PASS" : "FAIL"}] $name $want $got]
}
puts "RESULT: [expr {$failed ? "FAIL ($failed of [dict size $expected] checks failed)" : "PASS"}]"
exit [expr {$failed ? 1 : 0}]
