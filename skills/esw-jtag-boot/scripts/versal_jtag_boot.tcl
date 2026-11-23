# SPDX-License-Identifier: MIT
# Load a boot image on Versal / Versal Net / Versal Gen 2 (versal-2ve-2vm) through hw_server.
# The board must already be configured for JTAG boot.
#
# Usage:

proc run {args} {
    puts $args
    return [uplevel 1 $args]
}

proc select_device {target_filter} {
    set devices [targets -target-properties -filter $target_filter]
    set count [llength $devices]
    if {$count != 1} {
        error "Expected one device, found $count. Refine the target filter."
    }

    set name [dict get [lindex $devices 0] name]
    if {![string match -nocase "Versal*" $name]} {
        error "Expected a Versal device, found: $name"
    }

    run targets -set -filter $target_filter
    puts "Selected device: $name"
}

proc check_jtag_mode {family} {
    set user_addr 0xF1260200
    set por_addr 0xF1260204
    puts [list mrd -value $user_addr]
    set user_mode [mrd -value $user_addr]
    puts [list mrd -value $por_addr]
    set por_mode [mrd -value $por_addr]
    # The PLM reads the active boot mode as BOOT_MODE_USER & 0xF; 0 is JTAG.
    # POR holds the switch setting and is printed for information only.
    set active [expr {$user_mode & 0xF}]
    puts "Boot mode ($family): USER=$user_mode POR=$por_mode active=$active"
    if {$active != 0} {
        error "Board is not in JTAG boot mode for $family (active mode $active); set the boot switches to JTAG and power-cycle"
    }
}

proc load_image {family image server target_filter} {
    if {$server eq "local"} {
        run connect
    } else {
        run connect -url $server
    }

    try {
        select_device $target_filter
        check_jtag_mode $family
        run rst -system
        run after 1000

        select_device $target_filter
        check_jtag_mode $family
        run device program $image
        puts "IMAGE_DOWNLOADED: device programming completed"
    } finally {
        catch {run disconnect}
    }
}

proc main {arguments} {
    if {[llength $arguments] != 4} {
        error "Usage: versal_jtag_boot.tcl versal|versal-net|versal-2ve-2vm IMAGE SERVER TARGET_FILTER"
    }
    lassign $arguments family image server target_filter

    if {$family ni {versal versal-net versal-2ve-2vm}} {
        error "Family must be versal, versal-net or versal-2ve-2vm"
    }
    if {$server eq "" || $target_filter eq ""} {
        error "Server and target filter are required"
    }
    foreach value [list $image] {
        if {$value eq ""} { error "Firmware paths must not be empty" }
    }
    if {$server in {prepare boot}} { error "Legacy prepare/boot mode removed; see usage" }
    if {[info commands connect] eq ""} {
        error "Run with xsdb"
    }
    load_image $family $image $server $target_filter
}

if {[catch {main $argv} message]} {
    puts stderr "ERROR: $message"
    exit 1
}
