# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

namespace eval module_ref_test {}
proc module_ref_test::assert {expression} {
    if {![uplevel 1 [list expr $expression]]} {error "assertion failed: $expression"}
}
proc module_ref_test::read_text {path} {
    set channel [open $path r]
    try {return [read $channel]} finally {close $channel}
}
proc module_ref_test::run {root source_file output_dir} {
    if {[current_project -quiet] ne ""} {error "Refusing to change an open project"}
    if {[file exists $output_dir]} {error "Refusing to overwrite test evidence: $output_dir"}
    set source_file [file normalize $source_file]
    if {![file isfile $source_file]} {error "Missing reference HDL: $source_file"}
    set source_hash [lindex [exec sha256sum -- $source_file] 0]
    source [file join $root .claude/skills/vivado-ip-configurator/lib/ipcfg/ipcfg.tcl]
    set library [file join $root .claude/skills/vivado-ipi-assembler/lib/ipiasm.tcl]
    source $library
    file mkdir $output_dir
    create_project module_reference_regression [file join $output_dir project] -part xc2ve3558-sfva1440-2MP-e-S
    set_param board.repoPaths [list [file join $root inputs/rave2_board/board_files]]
    set_property BOARD_PART amd.com:rave2:part0:1.2 [current_project]
    create_bd_design scratch_design
    ipiasm::init
    # This test drives create_module_ref directly, as a compiled plan would after opening the plan gate.
    ipiasm::_gate_set plan 1 "test: direct helper call"
    set board [ipiasm::require_board_preset amd.com:rave2:part0:1.2]
    module_ref_test::assert {[string match READY:* $board]}
    set started [clock milliseconds]
    set first [ipiasm::create_module_ref axi_addr_translator reference_translator [list $source_file] {CONFIG.DWIDTH 128 CONFIG.REMAP_CACHE_PROT 0}]
    module_ref_test::assert {[string match SUCCESS:* $first]}
    set object [get_bd_cells reference_translator]
    set identity [get_property VLNV $object]
    set ledger_path [file join [get_property DIRECTORY [current_project]] .ipcfg attempts.tcldict]
    set before [module_ref_test::read_text $ledger_path]
    set files_before [lsort [get_files -quiet]]
    set count [llength [get_bd_cells -quiet]]
    set second [ipiasm::create_module_ref axi_addr_translator reference_translator [list $source_file] {CONFIG.DWIDTH 128 CONFIG.REMAP_CACHE_PROT 0}]
    module_ref_test::assert {[string match SUCCESS:* $second]}
    module_ref_test::assert {[module_ref_test::read_text $ledger_path] eq $before}
    source $library
    set reloaded [ipiasm::create_module_ref axi_addr_translator /reference_translator [list $source_file] {CONFIG.DWIDTH 128 CONFIG.REMAP_CACHE_PROT 0}]
    module_ref_test::assert {[string match SUCCESS:* $reloaded]}
    set rejected [ipiasm::create_module_ref different_module reference_translator [list $source_file]]
    module_ref_test::assert {[string match CONFIGURE_FAIL:MODULE_REF:* $rejected]}
    module_ref_test::assert {[module_ref_test::read_text $ledger_path] eq $before}
    module_ref_test::assert {[lsort [get_files -quiet]] eq $files_before}
    module_ref_test::assert {[llength [get_bd_cells -quiet]] == $count && $count == 1}
    set interfaces {}
    foreach {name mode} {S_AXI Slave M_AXI Master} {
        set interface [get_bd_intf_pins reference_translator/$name]
        module_ref_test::assert {[get_property MODE $interface] eq $mode}
        module_ref_test::assert {[get_property VLNV $interface] eq "xilinx.com:interface:aximm_rtl:1.0"}
        foreach {signal width} {AWADDR 64 ARADDR 64 WDATA 128 RDATA 128} {
            set pin [get_bd_pins reference_translator/${name}_${signal}]
            set actual [expr {abs([get_property LEFT $pin] - [get_property RIGHT $pin]) + 1}]
            module_ref_test::assert {$actual == $width}
        }
        dict set interfaces $name [dict create mode $mode address_width 64 data_width 128]
    }
    module_ref_test::assert {[get_property CONFIG.DWIDTH $object] == 128}
    module_ref_test::assert {[lindex [exec sha256sum -- $source_file] 0] eq $source_hash}
    set result [dict create status PASS vivado [version -short] source $source_file source_sha256 $source_hash library_sha256 [lindex [exec sha256sum -- $library] 0] identity $identity type [get_property TYPE $object] first $first second $second after_reload $reloaded wrong_module $rejected cell_count $count attempts [dict get $before [list scratch_design /reference_translator] count] ledger_unchanged true interfaces $interfaces elapsed_ms [expr {[clock milliseconds] - $started}]]
    set channel [open [file join $output_dir result.tcldict] w]
    try {puts $channel $result} finally {close $channel}
    puts "MODULE_REFERENCE_LIVE:PASS $result"
    return $result
}