# SPDX-License-Identifier: MIT
# Zynq-7000 A9 FSBL -> optional DTB -> U-Boot. No PL programming.
# Usage: xsdb zynq_jtag_boot.tcl SERVER CABLE FSBL UBOOT [DTB [DTB_ADDR]]
# FSBL must keep its symbol table: completion is detected at FsblHandoffJtagExit.
# Print each command as Tcl, preserving spaces in file paths.
proc run {args} {
    puts $args
    return [uplevel 1 $args]
}

proc select_target {cable pattern} {
    set reason "target not discovered"
    for {set attempt 0} {$attempt < 30} {incr attempt} {
        set matches {}
        foreach target [targets -target-properties] {
            if {![dict exists $target jtag_cable_ctx]} { continue }
            if {[dict get $target jtag_cable_ctx] ne $cable} { continue }
            if {[string match $pattern [dict get $target name]]} {
                lappend matches $target
            }
        }
        if {[llength $matches] > 1} {
            error "Ambiguous $pattern target on $cable; found [llength $matches]"
        }
        if {[llength $matches] == 1} {
            set target [lindex $matches 0]
            if {![catch {run targets -set [dict get $target target_id]} reason options]} {
                puts "Selected: [dict get $target name] on $cable"
                return
            }
            if {![string match -nocase "*invalid context*" $reason]} {
                return -options $options $reason
            }
        }
        if {$attempt < 29} { after 500 }
    }
    error "Timed out selecting $pattern on $cable after 30 polls: $reason"
}

proc check_address {value} {
    if {![regexp {^(0x[0-9a-fA-F]+|[0-9]+)$} $value]} {
        error "Expected a numeric address, got: $value"
    }
    if {[catch {expr {$value + 0}}]} { error "Invalid address: $value" }
}


# Read through the APU (DAP) target: physical access that works even when software
# on the A9 has the MMU on without mapping SLCR. Reselects A9 #0 afterwards.
proc check_jtag_mode {cable} {
    select_target $cable "APU"
    set mode [run mrd -value 0xF800025C]
    select_target $cable "*Cortex-A9*#0"
    if {($mode & 7) != 0} { error "Board is not in JTAG boot mode (Zynq-7000 BOOT_MODE=$mode); set the boot switches to JTAG and power-cycle" }
}

proc main {arguments} {
    if {[llength $arguments] ni {4 5 6}} {
        error "Usage: zynq_jtag_boot.tcl SERVER CABLE FSBL UBOOT ?DTB ?DTB_ADDR??"
    }
    lassign $arguments server cable fsbl uboot dtb dtb_addr
    if {$server eq "" || $cable eq ""} { error "Server and cable identity are required" }
    # Shared APU-load convention: separate DTB at 1 MiB.
    if {$dtb_addr eq ""} { set dtb_addr 0x100000 }
    if {[llength $arguments] > 4 && $dtb eq ""} { error "DTB path is required" }
    if {$dtb ne ""} { check_address $dtb_addr }
    foreach value [list $fsbl $uboot] {
        if {$value eq ""} { error "Firmware paths must not be empty" }
    }
    if {$server in {prepare boot}} { error "Legacy prepare/boot mode removed; see usage" }
    if {[info commands connect] eq ""} { error "Run with xsdb" }
    if {$server eq "local"} {run connect} else {run connect -url $server}
    set breakpoint ""
    try {
        check_jtag_mode $cable
        run rst -system
        run after 1000
        check_jtag_mode $cable
        catch {run stop}
        run dow $fsbl
        # dow loads FSBL symbols. The FSBL calls FsblHandoffJtagExit only after
        # successful JTAG-mode initialization, so its entry marks completion.
        set ready [run print &FsblHandoffJtagExit]
        if {![regexp {:\s*(0x[0-9a-fA-F]+|[0-9]+)\s*$} $ready -> ready_addr]} {
            error "Cannot resolve FsblHandoffJtagExit in FSBL symbols: $ready"
        }
        set breakpoint [run bpadd -addr $ready_addr -type hw]
        run con -block -timeout 60
        set registers [run rrd -nvlist pc]
        set pc_hex [dict get $registers pc]
        if {![regexp {^[0-9a-fA-F]+$} $pc_hex]} {error "Cannot read FSBL PC: $pc_hex"}
        set pc [expr "0x$pc_hex"]
        if {$pc != $ready_addr} {error "FSBL stopped at $pc instead of FsblHandoffJtagExit ($ready_addr)"}
        run bpremove $breakpoint
        set breakpoint ""
        if {$dtb ne ""} {run dow -data $dtb $dtb_addr}
        run dow $uboot
        run con
        puts "IMAGES_LOADED: Zynq-7000 firmware downloaded and execution started"
    } finally {
        if {$breakpoint ne ""} {catch {run bpremove $breakpoint}}
        catch {run disconnect}
    }
}
if {[catch {main $argv} message]} {
    puts stderr "ERROR: $message"
    exit 1
}
