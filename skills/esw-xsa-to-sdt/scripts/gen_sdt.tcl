#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
# =============================================================================
# gen_sdt.tcl — SDTGen Agent Template
# =============================================================================
# Standalone template that drives sdtgen via its set_dt_param/generate_sdt API.
# Board/overlay DTS inputs are supplied through -user_dts with .dtsi filenames
# (preferred over the deprecated -board_dts; -include_dts remains an alias).
# Omit -user_dts to skip Linux board DTSI input. SDTGen may still copy a
# Zephyr board DTSI automatically from the XSA board metadata.
#
# Arguments:
#   -xsa_path     <path>       Path to XSA file (required)
#   -outdir       <path>       Output directory for SDT (default: ./sdt_output)
#   -user_dts     <dtsi ...>   (Optional) One or more board/overlay .dtsi files,
#                              e.g. -user_dts zcu102-rev1.0.dtsi ./overlay.dtsi
#   -zocl         <0|1>        (Optional) Enable the Zocl device-tree node
#   -debug        <0|1>        Enable debug/trace output
#
# Usage:
#   # eval board
#   sdtgen gen_sdt.tcl -xsa_path ./design.xsa -outdir ./sdt_output \
#          -user_dts zcu102-rev1.0.dtsi -debug 1
#   # board + custom overlay
#   sdtgen gen_sdt.tcl -xsa_path ./design.xsa -outdir ./sdt_output \
#          -user_dts zcu102-rev1.0.dtsi ./my_overlay.dtsi -zocl 0 -debug 1
# =============================================================================

# --- Parse arguments ---
set xsa_path ""
set outdir "./sdt_output"
set user_dts [list]
set zocl_enable 0
set debug_mode 0

for { set i 0 } { $i < $argc } { incr i } {
    switch -- [lindex $argv $i] {
        "-xsa_path" {
            incr i
            set xsa_path [lindex $argv $i]
        }
        "-outdir" {
            incr i
            set outdir [lindex $argv $i]
        }
        "-user_dts" {
            # Collect all following tokens until the next -flag (supports
            # multiple .dtsi files: -user_dts board.dtsi overlay.dtsi)
            while { $i + 1 < $argc && ![string match "-*" [lindex $argv [expr {$i + 1}]]] } {
                incr i
                lappend user_dts [lindex $argv $i]
            }
        }
        "-zocl" {
            incr i
            set zocl_enable [lindex $argv $i]
        }
        "-debug" {
            incr i
            set debug_mode [lindex $argv $i]
        }
    }
}

# --- Validate inputs ---
if { $xsa_path eq "" } {
    puts "ERROR: -xsa_path is required"
    exit 1
}
if { ![file exists $xsa_path] } {
    puts "ERROR: XSA file not found: $xsa_path"
    exit 1
}
# Only path-like -user_dts entries (containing a slash) are validated on disk;
# bare board names like zcu102-rev1.0.dtsi are resolved by sdtgen from its repo.
foreach d $user_dts {
    if { [string match "*/*" $d] && ![file exists $d] } {
        puts "ERROR: user_dts file not found: $d"
        exit 1
    }
}

# --- Clean output directory ---
if { [file exists $outdir] } {
    puts "INFO: Cleaning existing output directory: $outdir"
    file delete -force $outdir
}

# --- Configure SDTGen ---
puts "INFO: Configuring SDTGen..."
puts "  XSA:        $xsa_path"
puts "  Output:     $outdir"
if { [llength $user_dts] > 0 } { puts "  user_dts:    $user_dts" }

set_dt_param -xsa $xsa_path
set_dt_param -dir $outdir

if { $zocl_enable } {
    set_dt_param -zocl enable
    puts "  Zocl: enabled"
}

if { [llength $user_dts] > 0 } {
    set_dt_param -user_dts $user_dts
}

if { $debug_mode } {
    set_dt_param -debug enable
    set_dt_param -trace enable
    puts "  Debug: enabled"
    puts "  Trace: enabled"
}

# --- Generate SDT ---
puts ""
puts "INFO: Generating System Device Tree..."
set start_time [clock seconds]

generate_sdt

set end_time [clock seconds]
set duration [expr {$end_time - $start_time}]
puts ""
puts "INFO: SDT generation completed in ${duration}s"
puts "INFO: Output at: $outdir"

# --- List output files ---
puts ""
puts "Generated files:"
foreach f [glob -nocomplain "${outdir}/*"] {
    set size [file size $f]
    puts "  [file tail $f] (${size} bytes)"
}
