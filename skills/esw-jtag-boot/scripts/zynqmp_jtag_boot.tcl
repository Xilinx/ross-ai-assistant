# SPDX-License-Identifier: MIT
# Load ZynqMP firmware through hw_server. Board must be in JTAG boot mode.
# Usage: xsdb zynqmp_jtag_boot.tcl SERVER CABLE PMUFW FSBL UBOOT BL31 [--dtb FILE [ADDRESS]]
# Validate artifacts using the skill before running.

proc boot_firmware {server cable pmufw fsbl uboot bl31 dtb dtb_addr} {
    if {$server eq "local"} { run connect } else { run connect -url $server }
    try {
        puts "Checking targets and JTAG boot mode"
        check_targets_before_reset $cable

        puts "Resetting system"
        select_target $cable "PSU"
        run rst -system
        run after 1000

        # Reset invalidates target IDs and closes the PMU debug gate.
        select_target $cable "PSU"
        check_jtag_mode
        enable_pmu_debug

        puts "Loading PMU firmware"
        select_target $cable "MicroBlaze PMU*"
        catch {run stop}
        run dow $pmufw
        run con
        run after 1000

        puts "Loading FSBL and waiting for initialization"
        select_target $cable "*Cortex-A53*#0"
        run rst -processor -clear-registers
        run dow $fsbl
        run con
        # Upstream fixed wait assumes a compatible FSBL; it does not prove DDR readiness.
        run after 4000
        run stop
        catch {run stop}

        puts "Loading device tree, U-Boot and TF-A"
        if {$dtb ne ""} { run dow -data $dtb $dtb_addr }
        run dow $uboot
        # Last ELF sets the entry point for the final resume.
        run dow $bl31
        run con
        puts "IMAGES_LOADED: firmware downloaded and execution started"
    } finally {
        catch {run disconnect}
    }
}

proc check_targets_before_reset {cable} {
    select_target $cable "PSU"
    check_jtag_mode
    select_target $cable "*Cortex-A53*#0"
    select_target $cable "PSU"
    enable_pmu_debug
    select_target $cable "MicroBlaze PMU*"
}

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

# The FSBL reads the active boot mode as BOOT_MODE_USER & 0xF
# (CRL_APB_BOOT_MODE_USER_BOOT_MODE_MASK); 0 is JTAG.
proc check_jtag_mode {} {
    set mode [run mrd -value 0xFF5E0200]
    set active [expr {$mode & 0xF}]
    if {$active != 0} {
        error "Board is not in JTAG boot mode (active mode $active, BOOT_MODE_USER=$mode); set the boot switches to JTAG and power-cycle"
    }
}

proc enable_pmu_debug {} {
    set value [run mrd -value 0xFFCA0038]
    set enabled [expr {$value | 0x1C0}]
    if {$enabled != $value} { run mwr 0xFFCA0038 $enabled }
    set actual [run mrd -value 0xFFCA0038]
    if {($actual & 0x1C0) != 0x1C0} {
        error "PMU debug gate did not enable; resolve board debug access before loading"
    }
}

proc check_address {value} {
    if {![regexp {^(0x[0-9a-fA-F]+|[0-9]+)$} $value]} {
        error "Expected a numeric address, got: $value"
    }
    if {[catch {expr {$value + 0}}]} { error "Invalid address: $value" }
}

proc main {arguments} {
    if {[llength $arguments] ni {6 8 9}} {
        error "Usage: zynqmp_jtag_boot.tcl SERVER CABLE PMUFW FSBL UBOOT BL31 ?--dtb FILE ?ADDRESS??"
    }
    lassign $arguments server cable pmufw fsbl uboot bl31 flag dtb dtb_addr
    # EDF ZynqMP APU flow: separate DTB at 1 MiB unless explicitly overridden.
    if {$dtb_addr eq ""} { set dtb_addr 0x100000 }
    if {[llength $arguments] > 6 && ($flag ne "--dtb" || $dtb eq "")} {
        error "Expected --dtb FILE with optional ADDRESS; R5 loading is not part of the APU flow"
    }
    check_address $dtb_addr
    foreach value [list $server $cable $pmufw $fsbl $uboot $bl31] {
        if {$value eq ""} { error "Required arguments must not be empty" }
    }
    if {$server in {prepare boot}} { error "Legacy prepare/boot mode removed; see usage" }
    if {[info commands connect] eq ""} { error "Run with xsdb" }

    boot_firmware $server $cable $pmufw $fsbl $uboot $bl31 $dtb $dtb_addr
}

if {[catch {main $argv} message]} {
    puts stderr "ERROR: $message"
    exit 1
}
