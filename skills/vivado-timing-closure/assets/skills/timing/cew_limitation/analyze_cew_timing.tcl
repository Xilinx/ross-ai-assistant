# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
################################################################################
# analyze_cew_timing.tcl
#
# Analyzes timing-critical paths for CEW (Clock Expansion Window) overlap issues.
# Supports Versal (X0Y10) and Versal P80 (S1X2Y6) clock region formats.
#
# Usage (inside Vivado):
#   source analyze_cew_timing.tcl
#   run_cew_analysis -max_paths <N> -cr_threshold <Y> [-stage placer|router] [-slack_threshold <ns>] [-min_overlap <CRs>] [-output <file>]
#
# Arguments:
#   -max_paths       : Number of failing paths to analyse per path group (required)
#   -cr_threshold    : Clock-region distance threshold to flag (required, e.g. 2)
#   -stage           : "placer" or "router" (default: router)
#   -slack_threshold : Only consider paths with slack worse than this value in ns (default: 0.0)
#   -min_overlap     : Minimum CEW overlap area in CRs to consider healthy (default: 2)
#   -output          : Output report file (default: cew_timing_report.rpt)
################################################################################

proc run_cew_analysis {args} {

    # ---- Parse arguments -------------------------------------------------------
    array set opts {
        -max_paths      ""
        -cr_threshold   ""
        -stage          "router"
        -slack_threshold 0.0
        -min_overlap    2
        -output         "cew_timing_report.rpt"
    }

    set arglen [llength $args]
    for {set i 0} {$i < $arglen} {incr i} {
        set key [lindex $args $i]
        if {[info exists opts($key)]} {
            incr i
            set opts($key) [lindex $args $i]
        } else {
            puts "ERROR: Unknown option '$key'"
            puts "Usage: run_cew_analysis -max_paths <N> -cr_threshold <Y> \[-stage placer|router\] \[-slack_threshold <ns>\] \[-min_overlap <CRs>\] \[-output <file>\]"
            return
        }
    }

    set max_paths       $opts(-max_paths)
    set cr_threshold    $opts(-cr_threshold)
    set stage           $opts(-stage)
    set slack_threshold $opts(-slack_threshold)
    set min_overlap     $opts(-min_overlap)
    set output_file     $opts(-output)

    if {$max_paths eq "" || $cr_threshold eq ""} {
        puts "ERROR: -max_paths and -cr_threshold are required."
        return
    }

    puts "============================================================"
    puts " CEW Timing Analysis"
    puts "============================================================"
    puts " Stage              : $stage"
    puts " Max paths / group  : $max_paths"
    puts " CR distance thresh : $cr_threshold"
    puts " Min CEW overlap    : $min_overlap CRs"
    puts " Slack threshold    : $slack_threshold ns"
    puts " Output file        : $output_file"
    puts "============================================================"

    # ---- Open report -----------------------------------------------------------
    set fp [open $output_file w]
    # try/finally around the whole report-writing body below (~450 lines of
    # clock-region-name parsing across multiple device-family naming formats
    # and CR-distance arithmetic, with almost no internal error guarding) --
    # without this, an uncaught exception anywhere in that body used to skip
    # straight past both existing `close $fp` calls (one on the early-return
    # "no path groups" path, one at the report's natural end), leaking this
    # write handle open for the rest of the whole Vivado session. The
    # exception itself still propagates after `finally` runs (this doesn't
    # swallow it) -- detect.tcl's own caller now wraps this whole call in
    # catch, so an error here no longer aborts the run either.
    try {
    puts $fp "################################################################################"
    puts $fp "# CEW Timing Analysis Report"
    puts $fp "# Generated : [clock format [clock seconds]]"
    puts $fp "# Stage              : $stage"
    puts $fp "# Max paths / group  : $max_paths"
    puts $fp "# CR distance thresh : $cr_threshold"
    puts $fp "# Min CEW overlap    : $min_overlap CRs"
    puts $fp "# Slack threshold    : $slack_threshold ns"
    puts $fp "################################################################################\n"

    # ---- Collect failing path groups -------------------------------------------
    set all_groups [get_path_groups -quiet]
    if {[llength $all_groups] == 0} {
        puts "ERROR: No path groups found in the design."
        return
    }

    set total_paths_analysed 0
    set total_cew_limited    0
    set total_cew_narrow     0
    set total_cew_constrained 0
    set total_cr_far         0

    # Summary list: each element is a dict-like list
    set summary_list {}

    # CEW fix collection: net_name -> expanded {s_lo x_min y_min s_hi x_max y_max}
    array set cew_fix_nets {}

    set num_groups [llength $all_groups]
    for {set gi 0} {$gi < $num_groups} {incr gi} {
        set grp [lindex $all_groups $gi]
        # Use the group object directly (handles **default** etc.)
        set grp_name $grp

        # Get worst N paths in this group with negative slack
        set timing_paths [get_timing_paths \
            -group [get_path_groups -quiet $grp_name] \
            -max_paths $max_paths \
            -slack_lesser_than $slack_threshold \
            -quiet]

        if {[llength $timing_paths] == 0} {
            continue
        }

        puts $fp "=========================================================================="
        puts $fp " Path Group: $grp_name  ([llength $timing_paths] paths analysed)"
        puts $fp "=========================================================================="
        puts ""
        puts "Analysing group: $grp_name  ([llength $timing_paths] paths)"

        set grp_cew_limited 0
        set grp_cew_narrow  0
        set grp_cew_constrained 0
        set grp_cr_far      0
        set path_idx        0

        foreach tp $timing_paths {
            incr path_idx
            incr total_paths_analysed

            # ---- Extract basic timing info -----------------------------------------
            set slack       [get_property SLACK           $tp]
            set requirement [get_property REQUIREMENT     $tp]
            set datapath    [get_property DATAPATH_DELAY  $tp]
            set startpoint  [get_property STARTPOINT_PIN  $tp]
            set endpoint    [get_property ENDPOINT_PIN    $tp]
            set src_clk     [get_property STARTPOINT_CLOCK $tp]
            set dst_clk     [get_property ENDPOINT_CLOCK   $tp]

            # ---- Determine path type: cell-to-cell, port-to-cell, cell-to-port ----
            set src_is_port 0
            set dst_is_port 0
            set src_cell [get_cells -quiet -of_objects [get_pins -quiet $startpoint]]
            set dst_cell [get_cells -quiet -of_objects [get_pins -quiet $endpoint]]

            # Check if startpoint is a port (no cell resolved)
            if {[llength $src_cell] == 0} {
                set src_port [get_ports -quiet $startpoint]
                if {[llength $src_port] > 0} {
                    set src_is_port 1
                } else {
                    puts $fp "  Path $path_idx: Could not resolve source. Skipping."
                    continue
                }
            }
            # Check if endpoint is a port (no cell resolved)
            if {[llength $dst_cell] == 0} {
                set dst_port [get_ports -quiet $endpoint]
                if {[llength $dst_port] > 0} {
                    set dst_is_port 1
                } else {
                    puts $fp "  Path $path_idx: Could not resolve destination. Skipping."
                    continue
                }
            }

            # If both are ports (port-to-port), skip CEW analysis
            if {$src_is_port && $dst_is_port} {
                puts $fp "  Path $path_idx: Port-to-port path. Skipping CEW analysis."
                continue
            }

            # ---- Determine path type string ----------------------------------------
            if {$src_is_port && !$dst_is_port} {
                set path_type "PORT_TO_CELL"
            } elseif {!$src_is_port && $dst_is_port} {
                set path_type "CELL_TO_PORT"
            } else {
                set path_type "CELL_TO_CELL"
            }

            # ---- Get names and locations -------------------------------------------
            if {$src_is_port} {
                set src_cell_name [get_property -quiet NAME $src_port]
                set src_loc       [get_property -quiet LOC  $src_port]
            } else {
                set src_cell_name [get_property NAME $src_cell]
                set src_loc       [get_property LOC  $src_cell]
            }
            if {$dst_is_port} {
                set dst_cell_name [get_property -quiet NAME $dst_port]
                set dst_loc       [get_property -quiet LOC  $dst_port]
            } else {
                set dst_cell_name [get_property NAME $dst_cell]
                set dst_loc       [get_property LOC  $dst_cell]
            }

            # ---- Get clock regions -------------------------------------------------
            set src_cr [get_clock_region_of_site $src_loc]
            set dst_cr [get_clock_region_of_site $dst_loc]

            set cr_dist [compute_cr_distance $src_cr $dst_cr]

            set cr_far_flag [expr {$cr_dist >= $cr_threshold}]
            if {$cr_far_flag} { incr grp_cr_far; incr total_cr_far }

            # ---- Find clock pins of source and destination cells -------------------
            # Ports don't have clock pins — only query on real cells
            if {$src_is_port} {
                set src_clk_pin {}
                set src_clk_pin_name "N/A (port)"
            } else {
                set src_clk_pin [get_pins -quiet -of_objects $src_cell -filter {IS_CLOCK == 1}]
                if {[llength $src_clk_pin] > 0} {
                    set src_clk_pin_name [get_property -quiet NAME [lindex $src_clk_pin 0]]
                } else {
                    set src_clk_pin_name "N/A"
                }
            }
            if {$dst_is_port} {
                set dst_clk_pin {}
                set dst_clk_pin_name "N/A (port)"
            } else {
                set dst_clk_pin [get_pins -quiet -of_objects $dst_cell -filter {IS_CLOCK == 1}]
                if {[llength $dst_clk_pin] > 0} {
                    set dst_clk_pin_name [get_property -quiet NAME [lindex $dst_clk_pin 0]]
                } else {
                    set dst_clk_pin_name "N/A"
                }
            }

            # ---- Get CEW and determine CEW-limited status --------------------------
            set src_cew [get_cew_from_clk_pin $src_clk_pin]
            set dst_cew [get_cew_from_clk_pin $dst_clk_pin]

            # Get clock buffer output net names for XDC fix generation
            set src_clk_net [get_clock_buffer_net $src_clk_pin]
            set dst_clk_net [get_clock_buffer_net $dst_clk_pin]

            set cew_limited 0
            set cew_narrow  0
            set cew_constrained 0
            set cew_overlap "N/A"
            set cew_overlap_details ""
            set port_cew_msg ""
            set src_to_dst_min -1
            set dst_to_src_min -1

            if {$path_type eq "CELL_TO_CELL"} {
                # Both endpoints are cells — check overlap of their CEWs
                set cew_result [compute_cew_overlap $src_cew $dst_cew]
                set cew_overlap [lindex $cew_result 0]
                set cew_overlap_details [lindex $cew_result 1]
                if {$cew_overlap == 0} {
                    set cew_limited 1
                } elseif {$cew_overlap > 0 && $cew_overlap < $min_overlap} {
                    set cew_narrow 1
                }

                # ---- Check reachability: can each cell's CEW reach the other's CR? ----
                set src_to_dst_min [compute_min_distance_from_cew_to_cr $src_cew $dst_cr]
                set dst_to_src_min [compute_min_distance_from_cew_to_cr $dst_cew $src_cr]
                if {!$cew_limited && ($src_to_dst_min > 0 || $dst_to_src_min > 0)} {
                    set cew_constrained 1
                }

            } elseif {$path_type eq "CELL_TO_PORT"} {
                # Flop/SRL -> output port: CEW of the source cell must cover
                # at least 1 CR beyond the port's clock region
                set cell_cew $src_cew
                set port_cr  $dst_cr
                set cell_cr  $src_cr
                set cew_check [check_cew_covers_port_cr $cell_cew $cell_cr $port_cr]
                set cew_limited [lindex $cew_check 0]
                set port_cew_msg [lindex $cew_check 1]

            } elseif {$path_type eq "PORT_TO_CELL"} {
                # Input port -> flop/SRL: CEW of the destination cell must cover
                # at least 1 CR beyond the port's clock region
                set cell_cew $dst_cew
                set port_cr  $src_cr
                set cell_cr  $dst_cr
                set cew_check [check_cew_covers_port_cr $cell_cew $cell_cr $port_cr]
                set cew_limited [lindex $cew_check 0]
                set port_cew_msg [lindex $cew_check 1]
            }

            # ---- Collect CEW fix XDC commands ------------------------------------
            if {$path_type eq "CELL_TO_CELL" && ($cew_constrained || $cew_limited)} {
                # Expand source clock CEW to include destination CR if src can't reach dst
                if {$src_to_dst_min > 0 && $src_clk_net ne "" && $src_cew ne "N/A"} {
                    set src_cew_parsed [parse_cew_range $src_cew]
                    set dst_cr_parsed  [parse_clock_region $dst_cr]
                    if {[lindex $src_cew_parsed 0] ne "ERR" && [lindex $dst_cr_parsed 0] ne "ERR"} {
                        set expanded [expand_cew_to_include_cr $src_cew_parsed $dst_cr_parsed]
                        if {[info exists cew_fix_nets($src_clk_net)]} {
                            set cew_fix_nets($src_clk_net) [merge_cew_ranges $cew_fix_nets($src_clk_net) $expanded]
                        } else {
                            set cew_fix_nets($src_clk_net) $expanded
                        }
                    }
                }
                # Expand destination clock CEW to include source CR if dst can't reach src
                if {$dst_to_src_min > 0 && $dst_clk_net ne "" && $dst_cew ne "N/A"} {
                    set dst_cew_parsed [parse_cew_range $dst_cew]
                    set src_cr_parsed  [parse_clock_region $src_cr]
                    if {[lindex $dst_cew_parsed 0] ne "ERR" && [lindex $src_cr_parsed 0] ne "ERR"} {
                        set expanded [expand_cew_to_include_cr $dst_cew_parsed $src_cr_parsed]
                        if {[info exists cew_fix_nets($dst_clk_net)]} {
                            set cew_fix_nets($dst_clk_net) [merge_cew_ranges $cew_fix_nets($dst_clk_net) $expanded]
                        } else {
                            set cew_fix_nets($dst_clk_net) $expanded
                        }
                    }
                }
            }
            if {($path_type eq "CELL_TO_PORT" || $path_type eq "PORT_TO_CELL") && $cew_limited} {
                # Expand the sequential cell's clock CEW to include the port's CR
                if {$path_type eq "CELL_TO_PORT"} {
                    set fix_clk_net $src_clk_net
                    set fix_cew     $src_cew
                    set fix_target  $dst_cr
                } else {
                    set fix_clk_net $dst_clk_net
                    set fix_cew     $dst_cew
                    set fix_target  $src_cr
                }
                if {$fix_clk_net ne "" && $fix_cew ne "N/A"} {
                    set fix_cew_parsed [parse_cew_range $fix_cew]
                    set fix_cr_parsed  [parse_clock_region $fix_target]
                    if {[lindex $fix_cew_parsed 0] ne "ERR" && [lindex $fix_cr_parsed 0] ne "ERR"} {
                        set expanded [expand_cew_to_include_cr $fix_cew_parsed $fix_cr_parsed]
                        if {[info exists cew_fix_nets($fix_clk_net)]} {
                            set cew_fix_nets($fix_clk_net) [merge_cew_ranges $cew_fix_nets($fix_clk_net) $expanded]
                        } else {
                            set cew_fix_nets($fix_clk_net) $expanded
                        }
                    }
                }
            }

            if {$cew_limited}     { incr grp_cew_limited;     incr total_cew_limited }
            if {$cew_narrow}      { incr grp_cew_narrow;      incr total_cew_narrow  }
            if {$cew_constrained} { incr grp_cew_constrained; incr total_cew_constrained }

            # ---- Write per-path report ---------------------------------------------
            puts $fp "--------------------------------------------------------------------------"
            puts $fp "  Path $path_idx  \[$path_type\]"
            puts $fp "--------------------------------------------------------------------------"
            puts $fp "    Slack          : $slack ns"
            puts $fp "    Requirement    : $requirement ns"
            puts $fp "    Data Path Delay: $datapath ns"
            puts $fp "    Path Type      : $path_type"
            puts $fp "    Source Pin      : $startpoint"
            puts $fp "    Dest   Pin      : $endpoint"
            if {$src_is_port} {
                puts $fp "    Source Port     : $src_cell_name"
            } else {
                puts $fp "    Source Cell     : $src_cell_name"
            }
            if {$dst_is_port} {
                puts $fp "    Dest   Port     : $dst_cell_name"
            } else {
                puts $fp "    Dest   Cell     : $dst_cell_name"
            }
            puts $fp "    Source LOC      : $src_loc"
            puts $fp "    Dest   LOC      : $dst_loc"
            puts $fp "    Source Clock    : $src_clk"
            puts $fp "    Dest   Clock    : $dst_clk"
            puts $fp "    Source Clk Pin  : $src_clk_pin_name"
            puts $fp "    Dest   Clk Pin  : $dst_clk_pin_name"
            puts $fp "    Source CR       : $src_cr"
            puts $fp "    Dest   CR       : $dst_cr"
            if {$cr_far_flag} {
                set cr_dist_msg ">> EXCEEDS THRESHOLD <<"
            } else {
                set cr_dist_msg "OK"
            }
            puts $fp "    CR Distance    : $cr_dist  (threshold=$cr_threshold) $cr_dist_msg"
            puts $fp "    Source CEW      : \[$src_cew\]"
            puts $fp "    Dest   CEW      : \[$dst_cew\]"

            if {$path_type eq "CELL_TO_CELL"} {
                puts $fp "    CEW Overlap    : $cew_overlap CRs"
                if {$cew_overlap_details ne ""} {
                    puts $fp "    CEW Overlap Box: $cew_overlap_details"
                }
                if {$src_to_dst_min >= 0} {
                    if {$src_to_dst_min == 0} {
                        puts $fp "    Src CEW->Dst CR: reachable (0 CRs)"
                    } else {
                        puts $fp "    Src CEW->Dst CR: >> UNREACHABLE - min $src_to_dst_min CR(s) away <<"
                    }
                }
                if {$dst_to_src_min >= 0} {
                    if {$dst_to_src_min == 0} {
                        puts $fp "    Dst CEW->Src CR: reachable (0 CRs)"
                    } else {
                        puts $fp "    Dst CEW->Src CR: >> UNREACHABLE - min $dst_to_src_min CR(s) away <<"
                    }
                }
            }
            if {$cew_limited} {
                set cew_msg ">> YES - TIMING LIMITED BY CEW (no overlap) <<"
            } elseif {$cew_constrained} {
                set cew_msg ">> YES - CEW CONSTRAINED (overlap exists but cells cannot be placed close) <<"
            } elseif {$cew_narrow} {
                set cew_msg ">> WARNING - NARROW CEW OVERLAP ($cew_overlap CRs < min $min_overlap) <<"
            } else {
                set cew_msg "No"
            }
            puts $fp "    CEW Limited?   : $cew_msg"
            if {$port_cew_msg ne ""} {
                puts $fp "    Port CEW Check : $port_cew_msg"
            }
            puts $fp ""

            if {$cew_limited && $cr_far_flag} {
                puts $fp "    *** CRITICAL: Cells are $cr_dist CR apart AND CEW does not cover. ***"
                puts $fp "    ***           High net delay is expected due to placement + CEW constraints. ***"
                puts $fp ""
            }
            if {$cew_narrow && $cr_far_flag} {
                puts $fp "    *** WARNING: Cells are $cr_dist CR apart AND CEW overlap is narrow ($cew_overlap CRs). ***"
                puts $fp "    ***          Source and destination cannot be placed close — expect high net delay. ***"
                puts $fp ""
            }
            if {$cew_constrained} {
                set constrained_parts {}
                if {$src_to_dst_min > 0} {
                    lappend constrained_parts "Src CEW max reach is $src_to_dst_min CR(s) short of Dst CR $dst_cr"
                }
                if {$dst_to_src_min > 0} {
                    lappend constrained_parts "Dst CEW max reach is $dst_to_src_min CR(s) short of Src CR $src_cr"
                }
                puts $fp "    *** CEW-CONSTRAINED: [join $constrained_parts { | }] ***"
                puts $fp "    ***   CEWs overlap but cells cannot be placed near each other due to CEW limits. ***"
                puts $fp ""
            }
            if {$cew_limited && ($path_type eq "CELL_TO_PORT" || $path_type eq "PORT_TO_CELL")} {
                puts $fp "    *** CEW-LIMITED PORT PATH: Sequential cell CEW does not extend ***"
                puts $fp "    ***   at least 1 CR beyond the port's clock region.            ***"
                puts $fp ""
            }

            lappend summary_list [list \
                $grp_name $path_idx $slack $src_cr $dst_cr $cr_dist $cr_far_flag \
                $src_cew $dst_cew $cew_overlap $cew_limited $cew_narrow $cew_constrained \
                $src_to_dst_min $dst_to_src_min $src_cell_name $dst_cell_name]
        }

        puts $fp "  Group Summary: $grp_cr_far paths exceed CR threshold, $grp_cew_limited CEW-limited, $grp_cew_constrained CEW-constrained, $grp_cew_narrow CEW-narrow"
        puts $fp ""
    }

    # ---- Global Summary --------------------------------------------------------
    puts $fp "\n################################################################################"
    puts $fp "# GLOBAL SUMMARY"
    puts $fp "################################################################################"
    puts $fp "  Total paths analysed              : $total_paths_analysed"
    puts $fp "  Paths exceeding CR threshold ($cr_threshold) : $total_cr_far"
    puts $fp "  Paths CEW-limited (no overlap)    : $total_cew_limited"
    puts $fp "  Paths CEW-constrained (can't reach): $total_cew_constrained"
    puts $fp "  Paths CEW-narrow  (overlap < $min_overlap CRs): $total_cew_narrow"
    puts $fp ""

    if {$total_cew_limited > 0 || $total_cew_narrow > 0 || $total_cew_constrained > 0} {
        puts $fp "  CEW-Limited & Narrow Overlap Paths Detail:"
        puts $fp [format "  %-30s %-5s %-10s %-8s %-8s %-6s %-35s %-35s %-10s %-8s" \
            "PathGroup" "Path#" "Slack(ns)" "SrcCR" "DstCR" "CRDist" "SrcCEW" "DstCEW" "Overlap" "Status"]
        puts $fp "  [string repeat - 165]"

        foreach entry $summary_list {
            lassign $entry grp_name pidx slack src_cr dst_cr cr_dist cr_far src_cew dst_cew cew_ov cew_lim cew_nar cew_con src_dst_min dst_src_min src_cn dst_cn
            if {$cew_lim} {
                set status "NO-OVERLAP"
            } elseif {$cew_con} {
                set status "CONSTRAINED"
            } elseif {$cew_nar} {
                set status "NARROW"
            } else {
                continue
            }
            puts $fp [format "  %-30s %-5d %-10.3f %-8s %-8s %-6d %-35s %-35s %-10s %-8s" \
                $grp_name $pidx $slack $src_cr $dst_cr $cr_dist $src_cew $dst_cew $cew_ov $status]
        }
    }

    # ---- Dump XDC fix commands for CEW-constrained/limited clocks -----------
    set fix_net_names [array names cew_fix_nets]
    if {[llength $fix_net_names] > 0} {
        # Derive .xdc filename from report filename
        set xdc_file [regsub {\.[^.]+$} $output_file ""].xdc
        if {$xdc_file eq $output_file} {
            set xdc_file "${output_file}.xdc"
        }
        set xdc_fp [open $xdc_file w]
        puts $xdc_fp "################################################################################"
        puts $xdc_fp "# AUTO-GENERATED USER_CLOCK_EXPANSION_WINDOW constraints"
        puts $xdc_fp "# Generated by analyze_cew_timing.tcl on [clock format [clock seconds]]"
        puts $xdc_fp "# Apply these constraints to expand CEW for timing-constrained clock nets."
        puts $xdc_fp "################################################################################"
        puts $xdc_fp ""

        puts $fp "\n################################################################################"
        puts $fp "# CEW FIX COMMANDS (USER_CLOCK_EXPANSION_WINDOW)"
        puts $fp "################################################################################"
        puts $fp "# The following XDC commands expand the Clock Expansion Window for nets whose"
        puts $fp "# current CEW is too narrow to reach the destination/source clock regions."
        puts $fp "# These commands have also been written to: $xdc_file"
        puts $fp "################################################################################\n"

        foreach net_name [lsort $fix_net_names] {
            set expanded_range [format_cew_range $cew_fix_nets($net_name)]
            set xdc_cmd "set_property USER_CLOCK_EXPANSION_WINDOW $expanded_range \[get_nets $net_name\]"
            puts $fp "  $xdc_cmd"
            puts $xdc_fp $xdc_cmd
        }

        puts $fp ""
        puts $xdc_fp ""
        close $xdc_fp

        puts ""
        puts "  CEW fix XDC commands written to   : $xdc_file"
    }

    puts $fp "\n# End of Report"
    } finally {
        catch {close $fp}
    }

    puts ""
    puts "============================================================"
    puts " Analysis Complete"
    puts "  Total paths analysed              : $total_paths_analysed"
    puts "  Paths exceeding CR threshold ($cr_threshold) : $total_cr_far"
    puts "  Paths CEW-limited (no overlap)    : $total_cew_limited"
    puts "  Paths CEW-constrained (can't reach): $total_cew_constrained"
    puts "  Paths CEW-narrow  (overlap < $min_overlap CRs): $total_cew_narrow"
    puts "  Report written to                 : $output_file"
    if {[llength $fix_net_names] > 0} {
        puts "  CEW fix XDC file                  : $xdc_file  ([llength $fix_net_names] nets)"
    }
    puts "============================================================"

    # ---- additive, structured result.json (does not affect detection/fix logic) ----
    # Output dir here is the shared $run_dir (same dir as $output_file / cew_timing_report.rpt),
    # not a dedicated per-skill subdir, so use a prefixed filename to avoid colliding
    # with other skills' files in the same run directory.
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
        set _rj_counts [_rj_obj [list \
            total_paths_analysed n $total_paths_analysed \
            total_cr_far          n $total_cr_far \
            total_cew_limited     n $total_cew_limited \
            total_cew_constrained n $total_cew_constrained \
            total_cew_narrow      n $total_cew_narrow \
            num_groups            n $num_groups \
            fix_nets              n [llength $fix_net_names] \
        ]]
        set _rj_thresholds [_rj_obj [list \
            cr_threshold    n $cr_threshold \
            min_overlap     n $min_overlap \
            slack_threshold n $slack_threshold \
            max_paths       n $max_paths \
        ]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "[llength $fix_net_names] clock net(s) have CEW overlap issues (no-overlap=$total_cew_limited constrained=$total_cew_constrained narrow=$total_cew_narrow, of $total_cr_far paths exceeding CR-distance threshold $cr_threshold)"
        set _rj_changes [expr {[llength $fix_net_names] > 0 ? "Wrote $xdc_file expanding USER_CLOCK_EXPANSION_WINDOW on [llength $fix_net_names] net(s)" : "No changes applied"}]
        set _rj_top [_rj_obj [list schema_version n 1 skill s "cew_limitation" category s "timing" \
            detected b [expr {[llength $fix_net_names] > 0}] generated_at s $_rj_ts \
            trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts,\n  \"thresholds\": $_rj_thresholds\n\}\n"
        set _rj_fh [open [file join [file dirname $output_file] "cew_limitation_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "WARNING: could not write cew_limitation_result.json: $_rj_err"
    }
}

################################################################################
# Helper: Extract clock region from a site name.
#   Site names follow patterns like SLICE_X10Y20 -> we get the clock region
#   by querying Vivado.
################################################################################
proc get_clock_region_of_site {site_name} {
    if {$site_name eq ""} { return "UNKNOWN" }
    set site_obj [get_sites -quiet $site_name]
    if {[llength $site_obj] == 0} { return "UNKNOWN" }
    set cr [get_property CLOCK_REGION $site_obj]
    if {$cr eq ""} { return "UNKNOWN" }
    return $cr
}

################################################################################
# Helper: Parse a clock region name into {slr x y}.
#   Handles:
#     "X0Y10"       -> {-1 0 10}    (Versal, no SLR prefix)
#     "S1X2Y6"      -> {1 2 6}      (Versal P80 with SLR)
################################################################################
proc parse_clock_region {cr} {
    # Versal P80: S<n>X<n>Y<n>
    if {[regexp {S(\d+)X(\d+)Y(\d+)} $cr -> s x y]} {
        return [list $s $x $y]
    }
    # Standard Versal: X<n>Y<n>
    if {[regexp {X(\d+)Y(\d+)} $cr -> x y]} {
        return [list -1 $x $y]
    }
    return [list "ERR"]
}

################################################################################
# Helper: Compute Manhattan distance between two clock regions.
#   Clock regions: X0Y0, X1Y2 (Versal) or S1X2Y6 (Versal P80 with SLR).
#   Different SLRs are treated as infinitely far apart (returns 999).
################################################################################
proc compute_cr_distance {cr1 cr2} {
    if {$cr1 eq "UNKNOWN" || $cr2 eq "UNKNOWN"} { return -1 }
    set p1 [parse_clock_region $cr1]
    set p2 [parse_clock_region $cr2]
    if {[lindex $p1 0] eq "ERR" || [lindex $p2 0] eq "ERR"} { return -1 }
    lassign $p1 s1 x1 y1
    lassign $p2 s2 x2 y2
    # Different SLRs -> flag as very far
    if {$s1 != $s2} { return 999 }
    set dx [expr {abs($x1 - $x2)}]
    set dy [expr {abs($y1 - $y2)}]
    return [expr {$dx + $dy}]
}

################################################################################
# Helper: Get CEW for a cell given its clock pin.
#   Traces: clock_pin -> net -> leaf driver pin (OUT) -> net -> CLOCK_EXPANSION_WINDOW
#   Returns the CLOCK_EXPANSION_WINDOW string (e.g. "{min max}") or "N/A".
################################################################################
proc get_cew_from_clk_pin {clk_pin} {
    if {[llength $clk_pin] == 0} { return "N/A" }

    # Use the first clock pin if multiple exist
    set pin [lindex $clk_pin 0]

    # Step 1: Get the clock net connected to this clock pin
    set clk_net [get_nets -quiet -of_objects [get_pins -quiet $pin]]
    if {[llength $clk_net] == 0} { return "N/A" }

    # Step 2: Get the leaf driver pin of that clock net (direction == OUT)
    set driver_pin [get_pins -quiet -of_objects $clk_net -leaf -filter {DIRECTION == OUT}]
    if {[llength $driver_pin] == 0} { return "N/A" }

    # Step 3: Get the net connected to the driver pin
    set driver_net [get_nets -quiet -of_objects $driver_pin]
    if {[llength $driver_net] == 0} { return "N/A" }

    # Step 4: Query CLOCK_EXPANSION_WINDOW on that net
    set cew [get_property -quiet CLOCK_EXPANSION_WINDOW $driver_net]
    if {$cew eq ""} { return "N/A" }

    return $cew
}

################################################################################
# Helper: Compute overlap between two CEWs.
#   CEW format: "CLOCKREGION_XaYb:CLOCKREGION_XcYd" (Versal)
#           or: "CLOCKREGION_S1X2Y6:CLOCKREGION_S1X8Y3" (Versal P80, same SLR)
#           or: "CLOCKREGION_S0X2Y1:CLOCKREGION_S1X8Y6" (Versal P80, cross-SLR)
#   Returns a two-element list: {overlap_area_in_CRs  detail_string}
#     overlap_area = number of CRs in the overlap rectangle
#                    0 = no overlap, -1 = could not parse
################################################################################
proc compute_cew_overlap {cew1 cew2} {
    if {$cew1 eq "N/A" || $cew2 eq "N/A"} {
        return [list -1 ""]
    }

    # Parse CEW ranges — returns {s_lo x_min y_min s_hi x_max y_max}
    set parsed1 [parse_cew_range $cew1]
    set parsed2 [parse_cew_range $cew2]

    if {[lindex $parsed1 0] eq "ERR" || [lindex $parsed2 0] eq "ERR"} {
        return [list -1 "Could not parse CEW format: $cew1 / $cew2"]
    }

    lassign $parsed1 s1_lo x1_min y1_min s1_hi x1_max y1_max
    lassign $parsed2 s2_lo x2_min y2_min s2_hi x2_max y2_max

    # Check if SLR ranges overlap (both must be SLR-aware or both non-SLR)
    if {$s1_lo ne "-1" && $s2_lo ne "-1"} {
        # Both have SLR info — check SLR range overlap
        set slr_ov_lo [expr {max($s1_lo, $s2_lo)}]
        set slr_ov_hi [expr {min($s1_hi, $s2_hi)}]
        if {$slr_ov_lo > $slr_ov_hi} {
            return [list 0 "No overlap (no common SLR: S${s1_lo}-S${s1_hi} vs S${s2_lo}-S${s2_hi})"]
        }
        # SLRs overlap — build display prefix
        if {$slr_ov_lo == $slr_ov_hi} {
            set slr_pfx "S${slr_ov_lo}"
        } else {
            set slr_pfx "S${slr_ov_lo}-S${slr_ov_hi}"
        }
    } else {
        set slr_pfx ""
    }

    # Compute X/Y overlap rectangle
    set ov_x_min [expr {max($x1_min, $x2_min)}]
    set ov_y_min [expr {max($y1_min, $y2_min)}]
    set ov_x_max [expr {min($x1_max, $x2_max)}]
    set ov_y_max [expr {min($y1_max, $y2_max)}]

    if {$ov_x_min > $ov_x_max || $ov_y_min > $ov_y_max} {
        return [list 0 "No overlap in X/Y. Src=${slr_pfx}X${x1_min}Y${y1_min}:X${x1_max}Y${y1_max}, Dst=${slr_pfx}X${x2_min}Y${y2_min}:X${x2_max}Y${y2_max}"]
    }

    set ov_width  [expr {$ov_x_max - $ov_x_min + 1}]
    set ov_height [expr {$ov_y_max - $ov_y_min + 1}]
    set ov_area   [expr {$ov_width * $ov_height}]

    set detail "Overlap=${slr_pfx}X${ov_x_min}Y${ov_y_min}:${slr_pfx}X${ov_x_max}Y${ov_y_max} (${ov_width}x${ov_height}=${ov_area} CRs)"
    return [list $ov_area $detail]
}

################################################################################
# Helper: Parse a CEW range string into {s_lo x_min y_min s_hi x_max y_max}.
#   Returns both SLR endpoints to handle cross-SLR CEW ranges.
#   Accepts:
#     Versal:     "CLOCKREGION_X0Y1:CLOCKREGION_X9Y10"
#     Versal P80: "CLOCKREGION_S1X2Y6:CLOCKREGION_S1X8Y3"  (same SLR)
#                 "CLOCKREGION_S0X2Y1:CLOCKREGION_S1X8Y6"  (cross-SLR)
#     Plain:      "X0Y1:X9Y10" or "S1X2Y6:S1X8Y3"
#   Returns: {s_lo x_min y_min s_hi x_max y_max} or {ERR}
#     s_lo/s_hi = -1 if no SLR prefix
################################################################################
proc parse_cew_range {cew_str} {
    # Versal P80: CLOCKREGION_S<n>X<n>Y<n>:CLOCKREGION_S<n>X<n>Y<n>
    if {[regexp {CLOCKREGION_S(\d+)X(\d+)Y(\d+)\s*:\s*CLOCKREGION_S(\d+)X(\d+)Y(\d+)} $cew_str -> s1 x1 y1 s2 x2 y2]} {
        set s_lo [expr {min($s1, $s2)}]
        set s_hi [expr {max($s1, $s2)}]
        set x_min [expr {min($x1, $x2)}]
        set y_min [expr {min($y1, $y2)}]
        set x_max [expr {max($x1, $x2)}]
        set y_max [expr {max($y1, $y2)}]
        return [list $s_lo $x_min $y_min $s_hi $x_max $y_max]
    }
    # Versal: CLOCKREGION_X<n>Y<n>:CLOCKREGION_X<n>Y<n>
    if {[regexp {CLOCKREGION_X(\d+)Y(\d+)\s*:\s*CLOCKREGION_X(\d+)Y(\d+)} $cew_str -> x1 y1 x2 y2]} {
        set x_min [expr {min($x1, $x2)}]
        set y_min [expr {min($y1, $y2)}]
        set x_max [expr {max($x1, $x2)}]
        set y_max [expr {max($y1, $y2)}]
        return [list -1 $x_min $y_min -1 $x_max $y_max]
    }
    # Plain with SLR: S<n>X<n>Y<n>:S<n>X<n>Y<n>
    if {[regexp {S(\d+)X(\d+)Y(\d+)\s*:\s*S(\d+)X(\d+)Y(\d+)} $cew_str -> s1 x1 y1 s2 x2 y2]} {
        set s_lo [expr {min($s1, $s2)}]
        set s_hi [expr {max($s1, $s2)}]
        set x_min [expr {min($x1, $x2)}]
        set y_min [expr {min($y1, $y2)}]
        set x_max [expr {max($x1, $x2)}]
        set y_max [expr {max($y1, $y2)}]
        return [list $s_lo $x_min $y_min $s_hi $x_max $y_max]
    }
    # Plain: X<n>Y<n>:X<n>Y<n>
    if {[regexp {X(\d+)Y(\d+)\s*:\s*X(\d+)Y(\d+)} $cew_str -> x1 y1 x2 y2]} {
        set x_min [expr {min($x1, $x2)}]
        set y_min [expr {min($y1, $y2)}]
        set x_max [expr {max($x1, $x2)}]
        set y_max [expr {max($y1, $y2)}]
        return [list -1 $x_min $y_min -1 $x_max $y_max]
    }
    return [list "ERR"]
}

################################################################################
# Helper: Compute the minimum Manhattan CR distance from a CEW box to a target
#   clock region. Returns 0 if the target CR is inside the CEW box.
#   Returns -1 if inputs cannot be parsed. Returns 999 for cross-SLR mismatch.
################################################################################
proc compute_min_distance_from_cew_to_cr {cew_str target_cr} {
    if {$cew_str eq "N/A" || $target_cr eq "UNKNOWN"} { return -1 }

    set cew_parsed [parse_cew_range $cew_str]
    if {[lindex $cew_parsed 0] eq "ERR"} { return -1 }
    lassign $cew_parsed cew_s_lo cew_x_min cew_y_min cew_s_hi cew_x_max cew_y_max

    set p_target [parse_clock_region $target_cr]
    if {[lindex $p_target 0] eq "ERR"} { return -1 }
    lassign $p_target t_s t_x t_y

    # Check SLR compatibility
    if {$cew_s_lo ne "-1" && $t_s ne "-1"} {
        if {$t_s < $cew_s_lo || $t_s > $cew_s_hi} {
            return 999
        }
    }

    # Minimum X distance (X = row / horizontal)
    if {$t_x < $cew_x_min} {
        set dx [expr {$cew_x_min - $t_x}]
    } elseif {$t_x > $cew_x_max} {
        set dx [expr {$t_x - $cew_x_max}]
    } else {
        set dx 0
    }

    # Minimum Y distance (Y = column / vertical)
    if {$t_y < $cew_y_min} {
        set dy [expr {$cew_y_min - $t_y}]
    } elseif {$t_y > $cew_y_max} {
        set dy [expr {$t_y - $cew_y_max}]
    } else {
        set dy 0
    }

    return [expr {$dx + $dy}]
}

################################################################################
# Helper: For port paths (CELL_TO_PORT / PORT_TO_CELL), check whether the
#   sequential cell's CEW extends at least 1 CR beyond the port's clock region.
#
#   Returns: {limited_flag message}
#     limited_flag = 1 if CEW does NOT cover the port CR, 0 otherwise
################################################################################
proc check_cew_covers_port_cr {cell_cew cell_cr port_cr} {
    if {$cell_cew eq "N/A" || $port_cr eq "UNKNOWN" || $cell_cr eq "UNKNOWN"} {
        return [list 0 "Could not determine (CEW=$cell_cew, cell_cr=$cell_cr, port_cr=$port_cr)"]
    }


    # Parse cell and port clock region coordinates (handles both X0Y10 and S1X2Y6)
    set p_cell [parse_clock_region $cell_cr]
    set p_port [parse_clock_region $port_cr]
    if {[lindex $p_cell 0] eq "ERR"} {
        return [list 0 "Could not parse cell CR: $cell_cr"]
    }
    if {[lindex $p_port 0] eq "ERR"} {
        return [list 0 "Could not parse port CR: $port_cr"]
    }
    lassign $p_cell cell_s cell_x cell_y
    lassign $p_port port_s port_x port_y

    # Parse CEW range
    set cew_parsed [parse_cew_range $cell_cew]
    if {[lindex $cew_parsed 0] eq "ERR"} {
        return [list 0 "CEW format not recognized: $cell_cew"]
    }
    lassign $cew_parsed cew_s_lo cew_x_min cew_y_min cew_s_hi cew_x_max cew_y_max

    # Check if port's SLR falls within CEW's SLR range
    if {$cew_s_lo ne "-1" && $port_s ne "-1"} {
        if {$port_s < $cew_s_lo || $port_s > $cew_s_hi} {
            return [list 1 "Cell CEW spans SLR S${cew_s_lo}-S${cew_s_hi} but port is in SLR S${port_s} - outside CEW SLR range"]
        }
    }

    # Check if port CR falls within CEW range (with 1 CR margin)
    set port_in_cew [expr {
        $port_x >= ($cew_x_min - 1) && $port_x <= ($cew_x_max + 1) &&
        $port_y >= ($cew_y_min - 1) && $port_y <= ($cew_y_max + 1)
    }]

    # Build display strings
    if {$cew_s_lo ne "-1"} {
        if {$cew_s_lo == $cew_s_hi} {
            set cew_disp "S${cew_s_lo}X${cew_x_min}Y${cew_y_min}:S${cew_s_hi}X${cew_x_max}Y${cew_y_max}"
        } else {
            set cew_disp "S${cew_s_lo}X${cew_x_min}Y${cew_y_min}:S${cew_s_hi}X${cew_x_max}Y${cew_y_max}"
        }
    } else {
        set cew_disp "X${cew_x_min}Y${cew_y_min}:X${cew_x_max}Y${cew_y_max}"
    }

    if {!$port_in_cew} {
        return [list 1 "Cell CEW \[$cew_disp\] does NOT reach port CR $port_cr (need at least 1 CR beyond)"]
    } else {
        return [list 0 "Cell CEW \[$cew_disp\] covers port CR $port_cr"]
    }
}

################################################################################
# Helper: Get the clock buffer output net name from a cell clock pin.
#   Traces: clock_pin -> net -> leaf driver pin (OUT) -> net -> NAME
#   Returns the net name string or "" if not found.
################################################################################
proc get_clock_buffer_net {clk_pin} {
    if {[llength $clk_pin] == 0} { return "" }
    set pin [lindex $clk_pin 0]
    set clk_net [get_nets -quiet -of_objects [get_pins -quiet $pin]]
    if {[llength $clk_net] == 0} { return "" }
    set driver_pin [get_pins -quiet -of_objects $clk_net -leaf -filter {DIRECTION == OUT}]
    if {[llength $driver_pin] == 0} { return "" }
    set driver_net [get_nets -quiet -of_objects $driver_pin]
    if {[llength $driver_net] == 0} { return "" }
    return [get_property -quiet NAME $driver_net]
}

################################################################################
# Helper: Expand a parsed CEW range to include a target clock region.
#   cew_parsed   : {s_lo x_min y_min s_hi x_max y_max}
#   target_cr_parsed : {s x y}
#   Returns: {s_lo x_min y_min s_hi x_max y_max} with range expanded.
################################################################################
proc expand_cew_to_include_cr {cew_parsed target_cr_parsed} {
    lassign $cew_parsed  s_lo x_min y_min s_hi x_max y_max
    lassign $target_cr_parsed t_s t_x t_y
    set new_x_min [expr {min($x_min, $t_x)}]
    set new_y_min [expr {min($y_min, $t_y)}]
    set new_x_max [expr {max($x_max, $t_x)}]
    set new_y_max [expr {max($y_max, $t_y)}]
    if {$s_lo ne "-1" && $t_s ne "-1"} {
        set new_s_lo [expr {min($s_lo, $t_s)}]
        set new_s_hi [expr {max($s_hi, $t_s)}]
    } else {
        set new_s_lo $s_lo
        set new_s_hi $s_hi
    }
    return [list $new_s_lo $new_x_min $new_y_min $new_s_hi $new_x_max $new_y_max]
}

################################################################################
# Helper: Merge two parsed CEW ranges into a bounding box.
################################################################################
proc merge_cew_ranges {range1 range2} {
    lassign $range1 s1_lo x1_min y1_min s1_hi x1_max y1_max
    lassign $range2 s2_lo x2_min y2_min s2_hi x2_max y2_max
    set new_x_min [expr {min($x1_min, $x2_min)}]
    set new_y_min [expr {min($y1_min, $y2_min)}]
    set new_x_max [expr {max($x1_max, $x2_max)}]
    set new_y_max [expr {max($y1_max, $y2_max)}]
    if {$s1_lo ne "-1" && $s2_lo ne "-1"} {
        set new_s_lo [expr {min($s1_lo, $s2_lo)}]
        set new_s_hi [expr {max($s1_hi, $s2_hi)}]
    } else {
        set new_s_lo $s1_lo
        set new_s_hi $s1_hi
    }
    return [list $new_s_lo $new_x_min $new_y_min $new_s_hi $new_x_max $new_y_max]
}

################################################################################
# Helper: Format a parsed CEW range back to a CLOCKREGION string.
################################################################################
proc format_cew_range {cew_range} {
    lassign $cew_range s_lo x_min y_min s_hi x_max y_max
    if {$s_lo ne "-1"} {
        return "CLOCKREGION_S${s_lo}X${x_min}Y${y_min}:CLOCKREGION_S${s_hi}X${x_max}Y${y_max}"
    } else {
        return "CLOCKREGION_X${x_min}Y${y_min}:CLOCKREGION_X${x_max}Y${y_max}"
    }
}

################################################################################
# Print usage help
################################################################################
proc cew_help {} {
    puts ""
    puts "Usage:"
    puts "  source analyze_cew_timing.tcl"
    puts "  run_cew_analysis -max_paths <N> -cr_threshold <Y> \[-stage placer|router\] \[-slack_threshold <ns>\] \[-min_overlap <CRs>\] \[-output <file>\]"
    puts ""
    puts "Example:"
    puts "  run_cew_analysis -max_paths 10 -cr_threshold 2 -min_overlap 3 -stage router -slack_threshold 0.0 -output cew_report.rpt"
    puts ""
    puts "Arguments:"
    puts "  -max_paths       Number of worst paths to analyse per failing path group"
    puts "  -cr_threshold    Clock-region Manhattan distance to flag (e.g., 2 = 2 CR apart)"
    puts "  -stage           placer or router (default: router)"
    puts "  -slack_threshold Only analyse paths with slack worse than this in ns (default: 0.0)"
    puts "  -min_overlap     Minimum CEW overlap in CRs to consider healthy (default: 2)"
    puts "  -output          Output report file name (default: cew_timing_report.rpt)"
    puts ""
}

puts "analyze_cew_timing.tcl loaded. Run 'cew_help' for usage or call 'run_cew_analysis'."
