# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

set audit_root [file normalize [file join [file dirname [info script]] ../../../../..]]
set expected_project [file join $audit_root outputs rave2_delivery]
if {[file normalize [get_property DIRECTORY [current_project]]] ne $expected_project} {
    error "Wrong delivery project"
}
source [file join $audit_root .claude/skills/vivado-ip-configurator/lib/ipcfg/ipcfg.tcl]
source [file join $audit_root .claude/skills/vivado-ipi-assembler/lib/ipiasm.tcl]
ipiasm::init
set started [clock microseconds]
set before [ipiasm::_delivery_fingerprint]
set inventory [ipiasm::gate_inventory {ps_wizard 1 mipi_csi2_rx_subsystem 1 visp_ss 1 axi_noc2 1 axi_gpio 8 axi_iic 4 axi_uartlite 2 axi_quad_spi 1 clkx5_wiz 1 axi_intc 1 smartconnect 2 proc_sys_reset 5 xlconcat 3 xlconstant 1 axi_bram_ctrl 1 blk_mem_gen 1 ai_engine 1}]
set cells {}
foreach cell [get_bd_cells -quiet] {
    dict set cells $cell [get_property VLNV $cell]
}
set result [dict create project [get_property DIRECTORY [current_project]] bd [current_bd_design] version [version -short] cells $cells inventory $inventory scalar_ports [llength [get_bd_ports -quiet]] interface_ports [llength [get_bd_intf_ports -quiet]] address_spaces [llength [get_bd_addr_spaces -quiet]] mapped_segments [ipiasm::_mapped_seg_count] clock_check [ipiasm::check_interface_clock_domains] gates [ipiasm::gate_status] elapsed_us [expr {[clock microseconds] - $started}] complete_stage_performance NOT_MEASURED]
dict set result unchanged [expr {$before eq [ipiasm::_delivery_fingerprint]}]
dict set result validation SKIPPED_INVENTORY_INCOMPLETE
file mkdir [file join $audit_root tests evidence issue-closure]
set channel [open [file join $audit_root tests/evidence/issue-closure/delivery_path_audit.tcldict] w]
puts $channel $result
close $channel
set result