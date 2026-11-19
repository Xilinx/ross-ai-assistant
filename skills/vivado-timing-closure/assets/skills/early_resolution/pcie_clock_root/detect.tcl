# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# PCIE/QDMA Clock Root Optimization
# =============================================================================
# Detects PCIE/QDMA IPs and applies optimal USER_CLOCK_ROOT on their
# BUFG_GT output clock nets. The clock root is placed at the nearest
# odd-X clock region from the PCIE hard block's location.
#
# Logic:
#   1. Find PCIE hard blocks (REF_NAME =~ PCIE*)
#   2. Walk up hierarchy to find IP boundary (name contains qdma_ or pcie_)
#   3. Find BUFG_GT cells under that IP hierarchy
#   4. Get PCIE's clock region → compute nearest odd-X, same Y
#   5. Apply USER_CLOCK_ROOT on BUFG_GT output nets
#
# Sourced by early_flow_fix.tcl
# Sets $_pcie_cr_moves variable for reporting
# =============================================================================

set _pcie_cr_moves 0

# Find all PCIE hard blocks
set _pcie_cells [get_cells -quiet -hier -filter {REF_NAME =~ PCIE*}]

if {[llength $_pcie_cells] == 0} {
    return
}

puts "\[DETECT\] PCIE Clock Root: Found [llength $_pcie_cells] PCIE hard block(s)"

# Track processed IPs to avoid duplicates
catch {array unset _pcie_processed_ips}
array set _pcie_processed_ips {}

foreach _pcie $_pcie_cells {
    set _pcie_name [get_property NAME $_pcie]

    # Walk up hierarchy to find IP boundary (qdma_ or pcie_ in name)
    # Pick the FIRST (outermost) match — that's the IP root
    set _ip_root ""
    set _hier_parts [split $_pcie_name /]
    set _path ""
    foreach _part $_hier_parts {
        if {$_path eq ""} {
            set _path $_part
        } else {
            set _path "${_path}/${_part}"
        }
        if {$_ip_root eq "" && [regexp -nocase {qdma_|pcie_} $_part]} {
            set _ip_root $_path
            break
        }
    }

    if {$_ip_root eq ""} {
        # Try matching on the full path
        if {[regexp -nocase {qdma_|pcie_} $_pcie_name]} {
            # Use the cell two levels above PCIE as IP root
            set _nparts [llength $_hier_parts]
            if {$_nparts >= 3} {
                set _ip_root [join [lrange $_hier_parts 0 end-2] /]
            } else {
                set _ip_root [lindex $_hier_parts 0]
            }
        } else {
            continue
        }
    }

    # Skip if already processed this IP
    if {[info exists _pcie_processed_ips($_ip_root)]} { continue }
    set _pcie_processed_ips($_ip_root) 1

    puts "\[DETECT\]   IP: $_ip_root"
    puts "\[DETECT\]   PCIE: $_pcie_name"

    # Get PCIE clock region
    set _pcie_site [get_sites -quiet -of_objects [get_cells -quiet $_pcie]]
    if {[llength $_pcie_site] == 0} {
        puts "\[DETECT\]   WARNING: PCIE has no site (unplaced?), skipping"
        continue
    }
    set _pcie_cr [get_property -quiet CLOCK_REGION $_pcie_site]
    if {$_pcie_cr eq ""} {
        puts "\[DETECT\]   WARNING: Cannot get clock region for PCIE, skipping"
        continue
    }
    puts "\[DETECT\]   PCIE clock region: $_pcie_cr"

    # Parse clock region coordinates (X<n>Y<m> or S<s>X<n>Y<m>)
    set _cr_prefix ""
    if {[regexp {(S\d+)X(\d+)Y(\d+)} $_pcie_cr -> _cr_prefix _cr_x _cr_y]} {
        # P80 style with SLR prefix
    } elseif {[regexp {X(\d+)Y(\d+)} $_pcie_cr -> _cr_x _cr_y]} {
        # Standard style
    } else {
        puts "\[DETECT\]   WARNING: Cannot parse clock region $_pcie_cr, skipping"
        continue
    }

    # Compute optimal clock root: nearest odd X, same Y
    if {$_cr_x % 2 == 1} {
        # Already odd
        set _root_x $_cr_x
    } else {
        # Even → go to next odd (X+1)
        set _root_x [expr {$_cr_x + 1}]
    }

    if {$_cr_prefix ne ""} {
        set _optimal_cr "${_cr_prefix}X${_root_x}Y${_cr_y}"
    } else {
        set _optimal_cr "X${_root_x}Y${_cr_y}"
    }
    puts "\[DETECT\]   Optimal clock root: $_optimal_cr (from PCIE CR=$_pcie_cr, nearest odd X)"

    # Find BUFG_GT cells under this IP hierarchy
    set _ip_bufgts [get_cells -quiet -hier -filter "NAME =~ ${_ip_root}/* && REF_NAME == BUFG_GT"]
    if {[llength $_ip_bufgts] == 0} {
        # Try broader search — BUFG_GTs might be at a different hierarchy level
        # Look for BUFG_GTs whose I pin is driven by GTs under this IP
        set _ip_gts [get_cells -quiet -hier -filter "NAME =~ ${_ip_root}/* && REF_NAME =~ GT*"]
        if {[llength $_ip_gts] > 0} {
            # Get GT output pins → trace to BUFG_GT I pins
            # Explicit grouping -- Vivado filter expressions follow normal
            # operator precedence (&& binds tighter than ||), so without
            # the parens this parsed as
            # "(DIRECTION==OUT && TXOUTCLK) || RXOUTCLK", leaving the
            # RXOUTCLK branch completely ungated by DIRECTION and able to
            # match input pins too.
            set _gt_out_pins [get_pins -quiet -of_objects $_ip_gts -filter {DIRECTION == OUT && (REF_PIN_NAME =~ *TXOUTCLK* || REF_PIN_NAME =~ *RXOUTCLK*)}]
            if {[llength $_gt_out_pins] > 0} {
                set _gt_nets [get_nets -quiet -of_objects $_gt_out_pins]
                if {[llength $_gt_nets] > 0} {
                    set _driven_pins [get_pins -quiet -leaf -of_objects $_gt_nets -filter {DIRECTION == IN && REF_PIN_NAME == I}]
                    set _ip_bufgts [get_cells -quiet -of_objects $_driven_pins -filter {REF_NAME == BUFG_GT}]
                }
            }
        }
    }

    if {[llength $_ip_bufgts] == 0} {
        puts "\[DETECT\]   No BUFG_GT cells found for this IP, skipping"
        continue
    }
    puts "\[DETECT\]   Found [llength $_ip_bufgts] BUFG_GT(s) in IP"

    # Print driver info and group BUFG_GTs by shared driver
    catch {array unset _bufgt_drv_groups}
    array set _bufgt_drv_groups {}
    foreach _bufgt $_ip_bufgts {
        set _bg_name [get_property NAME $_bufgt]
        set _drv_pin [get_pins -quiet -of_objects [get_nets -quiet -of_objects [get_pins -quiet -of_objects $_bufgt -filter {REF_PIN_NAME == I}]] -leaf -filter {DIRECTION == OUT}]
        if {$_drv_pin ne ""} {
            set _dname [get_property NAME $_drv_pin]
            puts "\[DETECT\]     BUFG_GT: $_bg_name -> driver: $_dname"
            if {![info exists _bufgt_drv_groups($_dname)]} {
                set _bufgt_drv_groups($_dname) [list]
            }
            lappend _bufgt_drv_groups($_dname) $_bufgt
        } else {
            puts "\[DETECT\]     BUFG_GT: $_bg_name -> driver: (not found)"
        }
    }

    # Pick the largest group (shared driver = same GT clock output)
    set _target_bufgts [list]
    set _max_group_size 0
    set _target_driver ""
    foreach _drv [array names _bufgt_drv_groups] {
        set _grp $_bufgt_drv_groups($_drv)
        if {[llength $_grp] > $_max_group_size} {
            set _max_group_size [llength $_grp]
            set _target_bufgts $_grp
            set _target_driver $_drv
        }
    }

    if {[llength $_target_bufgts] < 2} {
        puts "\[DETECT\]   No shared-driver BUFG_GT group found (need >= 2), skipping"
        continue
    }
    puts "\[DETECT\]   Target group: [llength $_target_bufgts] BUFG_GTs sharing driver: $_target_driver"

    # Apply USER_CLOCK_ROOT to target BUFG_GT output nets only
    foreach _bufgt $_target_bufgts {
        set _o_pin [get_pins -quiet -of_objects $_bufgt -filter {REF_PIN_NAME == O}]
        if {$_o_pin eq ""} { continue }
        set _o_net [get_nets -quiet -of_objects $_o_pin]
        if {$_o_net eq ""} { continue }
        set _net_name [get_property NAME $_o_net]

        # Check if net already has USER_CLOCK_ROOT set
        set _existing_root [get_property -quiet USER_CLOCK_ROOT $_o_net]
        if {$_existing_root ne ""} {
            puts "\[DETECT\]     Net $_net_name already has USER_CLOCK_ROOT=$_existing_root, skipping"
            continue
        }

        # Only report/count this as applied if set_property actually
        # succeeded -- `catch` alone swallows the result and used to print
        # "Applied"/increment the count unconditionally even on failure.
        if {![catch { set_property USER_CLOCK_ROOT $_optimal_cr $_o_net }]} {
            puts "\[DETECT\]     Applied USER_CLOCK_ROOT=$_optimal_cr on net: $_net_name"
            incr _pcie_cr_moves
        } else {
            puts "\[DETECT\]     WARNING: could not apply USER_CLOCK_ROOT=$_optimal_cr on net: $_net_name"
        }
    }
}

# ---- additive, structured result.json (does not affect detection/fix logic) ----
# This file writes no other output of its own — it only sets $_pcie_cr_moves for
# whatever sourced it (normally early_flow_fix.tcl). Guarded so we don't clobber
# an already-inherited $run_dir when sourced from a caller that set it.
if {![info exists run_dir]} {
    if {[info exists env(RUN_DIR)]} {
        set run_dir $env(RUN_DIR)
    } else {
        set run_dir [pwd]
    }
}
if {[catch {
    proc _rj_esc {s} { return [string map {\\ \\\\ \" \\\" \n \\n \r \\r \t \\t} $s] }
    proc _rj_str {v} { return "\"[_rj_esc $v]\"" }
    proc _rj_bool {v} { return [expr {$v ? "true" : "false"}] }
    proc _rj_obj {pairs} {
        set m {}
        foreach {k t v} $pairs {
            set vv [expr {$t eq "s" ? [_rj_str $v] : ($t eq "b" ? [_rj_bool $v] : $v)}]
            lappend m "\"$k\": $vv"
        }
        return "\{[join $m ", "]\}"
    }
    set _rj_counts [_rj_obj [list moves n $_pcie_cr_moves pcie_cells n [llength $_pcie_cells]]]
    set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
    set _rj_cond "[llength $_pcie_cells] PCIE hard block(s) found with a shared-driver BUFG_GT group (>=2) lacking USER_CLOCK_ROOT"
    set _rj_changes [expr {$_pcie_cr_moves > 0 ? "Applied USER_CLOCK_ROOT directly on $_pcie_cr_moves BUFG_GT output net(s) (live design mutation, no XDC file)" : "No changes applied"}]
    set _rj_top [_rj_obj [list schema_version n 1 skill s "pcie_clock_root" category s "early_resolution" \
        detected b [expr {$_pcie_cr_moves > 0}] generated_at s $_rj_ts \
        trigger_condition s $_rj_cond design_changes s $_rj_changes]]
    set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts\n\}\n"
    set _rj_fh [open [file join $run_dir "pcie_clock_root_result.json"] w]
    puts -nonewline $_rj_fh $_rj_json
    close $_rj_fh
} _rj_err]} {
    puts "\[DETECT\] WARNING: could not write pcie_clock_root_result.json: $_rj_err"
}
