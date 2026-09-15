# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
################################################################################
# helper_scripts.tcl - Vivado Revision Control Procedure Library
#  
# This library provides core procedures for executing the 5-step revision
# control workflow. Each procedure can be used independently or as part of
# the complete pipeline: detect → analyze → export → capture → generate
#
# Vivado Compatibility: 2025.1 (backwards compatible to 2023.2+)
# Usage: source helper_scripts.tcl
#
# TABLE OF CONTENTS
# =================
# Line ~31   PROCEDURE 1: detect_project_flow
#            → Determines project type (Standard/DFX/Segmented Config/BDC)
# Line ~163  PROCEDURE 2: analyze_source_locations
#            → Classifies source locations (Push Button/Remote/Mixed)
# Line ~237  PROCEDURE 3: export_all_sources
#            → Exports RTL, constraints, IP, BD, sim, data files
# Line ~647  PROCEDURE 4: capture_project_settings
#            → Saves non-default project properties to Tcl script
# Line ~772  PROCEDURE 5: generate_build_script
#            → Generates automated build.tcl for project recreation
# Line ~984  UTILITY: pr_verify
#            → Validates DFX configuration integrity
################################################################################

#══════════════════════════════════════════════════════════════════════════════
# PROCEDURE 1: detect_project_flow
# Purpose: Determine project type (Standard, DFX, Segmented Config, Combined)
#══════════════════════════════════════════════════════════════════════════════
proc detect_project_flow {} {
    set proj [current_project]
    set device [get_property PART $proj]
    
    # Initialize return structure
    set flow_info [dict create]
    dict set flow_info "project_name" [get_property NAME $proj]
    dict set flow_info "device" $device
    
    #────────────────────────────────────────────────────────────────────────────
    # Check for DFX (Dynamic Function eXchange) - Partial Reconfiguration
    #────────────────────────────────────────────────────────────────────────────
    set pr_flow [get_property PR_FLOW $proj]
    set has_pr_flow [expr {$pr_flow eq "true" || $pr_flow eq "1"}]
    
    # Check for reconfigurable cells (indicators of PR design)
    set dfx_indicators 0
    set fileset [get_filesets sources_1]
    set cells_list [get_cells -quiet -hier]
    foreach cell $cells_list {
        set hd [get_property HD.RECONFIGURABLE $cell]
        if {[string length $hd] > 0 && ($hd eq "true" || $hd eq "1")} {
            set dfx_indicators 1
            break
        }
    }
    
    # Check for Pblocks (Physical blocks = PR regions)
    set pblock_count [llength [get_pblocks -quiet]]
    set has_dfx [expr {$has_pr_flow || ($dfx_indicators && $pblock_count > 0)}]
    
    dict set flow_info "is_dfx" $has_dfx
    dict set flow_info "pr_flow_property" $pr_flow
    dict set flow_info "pr_indicators_found" $dfx_indicators
    dict set flow_info "pblock_count" $pblock_count
    
    #────────────────────────────────────────────────────────────────────────────
    # Check for Segmented Configuration - Versal-specific feature
    #────────────────────────────────────────────────────────────────────────────
    set seg_cfg [get_property SEGMENTED_CONFIGURATION $proj]
    set has_seg_cfg [expr {$seg_cfg eq "true" || $seg_cfg eq "1"}]
    
    # Detect Versal devices (implicit segmented config on Gen2)
    set is_versal 0
    if {[string match "*versal*" [string tolower $device]] || \
        [string match "vek*" $device] || \
        [string match "vck*" $device] || \
        [string match "vmk*" $device] || \
        [string match "vhk*" $device]} {
        set is_versal 1
    }
    
    dict set flow_info "is_segmented_config" $has_seg_cfg
    dict set flow_info "is_versal" $is_versal
    dict set flow_info "segmented_cfg_property" $seg_cfg
    
    #────────────────────────────────────────────────────────────────────────────
    # Check for IPI BDC (Block Design Container) designs
    # BDC = BD instantiated inside another BD, identified by CONFIG.ACTIVE_SYNTH_BD
    # Regular hierarchical blocks exist but don't have this property
    #────────────────────────────────────────────────────────────────────────────
    set has_bdc 0
    set bd_files [get_files -quiet -filter {FILE_TYPE == "Block Designs"}]
    set bdc_count 0
    
    # Check each BD to see if it contains BDC instances
    foreach bd_file $bd_files {
        if {[catch {
            open_bd_design $bd_file
            set current_bd [current_bd_design]
            
            # Get all cells in this BD
            set bd_cells [get_bd_cells -quiet]
            
            # Check if any cell is a true BDC
            # BDC cells have CONFIG.ACTIVE_SYNTH_BD or CONFIG.LIST_SYNTH_BD properties
            foreach cell $bd_cells {
                # Check if this is a BDC by looking for child BD references
                set active_synth_bd [get_property -quiet CONFIG.ACTIVE_SYNTH_BD $cell]
                set list_synth_bd [get_property -quiet CONFIG.LIST_SYNTH_BD $cell]
                
                # If either property exists and is not empty, this is a BDC
                if {$active_synth_bd ne "" || $list_synth_bd ne ""} {
                    incr bdc_count
                    set has_bdc 1
                }
            }
        } err]} {
            # Error opening BD, skip it
        }
    }
    
    dict set flow_info "is_ipi_bdc" $has_bdc
    dict set flow_info "bdc_count" $bdc_count
    dict set flow_info "bd_file_count" [llength $bd_files]
    
    #────────────────────────────────────────────────────────────────────────────
    # Determine combined vs single flow type
    #────────────────────────────────────────────────────────────────────────────
    if {$has_dfx && $has_seg_cfg} {
        dict set flow_info "flow_type" "DFX + Segmented Configuration"
    } elseif {$has_dfx} {
        dict set flow_info "flow_type" "DFX (Partial Reconfiguration)"
    } elseif {$has_seg_cfg} {
        dict set flow_info "flow_type" "Segmented Configuration"
    } else {
        dict set flow_info "flow_type" "Standard"
    }
    
    # Append BDC note if present
    if {$has_bdc} {
        dict append flow_info "flow_type" " + IPI BDC"
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Report findings
    #────────────────────────────────────────────────────────────────────────────
    puts "─────────────────────────────────────────────────────────────────────"
    puts "PROJECT FLOW DETECTION"
    puts "─────────────────────────────────────────────────────────────────────"
    puts "Project: [dict get $flow_info project_name]"
    puts "Device:  [dict get $flow_info device]"
    puts "Flow Type: [dict get $flow_info flow_type]"
    puts "─────────────────────────────────────────────────────────────────────"
    
    return $flow_info
}

#══════════════════════════════════════════════════════════════════════════════
# PROCEDURE 2: analyze_source_locations
# Purpose: Determine source organization (Push Button, Remote, or Mixed)
#══════════════════════════════════════════════════════════════════════════════
proc analyze_source_locations {} {
    set proj [current_project]
    set proj_dir [get_property DIRECTORY $proj]
    set proj_dir_norm [file normalize $proj_dir]
    
    set scenario_info [dict create]
    dict set scenario_info "project_dir" $proj_dir
    dict set scenario_info "local_count" 0
    dict set scenario_info "remote_count" 0
    dict set scenario_info "remote_paths" {}
    
    #────────────────────────────────────────────────────────────────────────────
    # Analyze each source file location
    #────────────────────────────────────────────────────────────────────────────
    set all_files [get_files -quiet]
    foreach file $all_files {
        # Skip generated and special files
        set is_generated [get_property IS_GENERATED $file]
        if {$is_generated eq "true" || $is_generated eq "1"} {
            continue
        }
        
        set file_path [file normalize $file]

        # Treat files already under .srcs as local even if normalize resolves
        # through a symlinked workspace path.
        if {[string match "*.srcs/*" $file]} {
            dict incr scenario_info "local_count"
            continue
        }
        
        # Check if file is within project directory
        if {[string first $proj_dir_norm $file_path] == 0} {
            # Local file
            dict incr scenario_info "local_count"
        } else {
            # Remote file (outside project directory)
            dict incr scenario_info "remote_count"
            dict lappend scenario_info "remote_paths" $file_path
        }
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Determine scenario type
    #────────────────────────────────────────────────────────────────────────────
    set local_files [dict get $scenario_info "local_count"]
    set remote_files [dict get $scenario_info "remote_count"]
    set total_files [expr {$local_files + $remote_files}]
    
    if {$remote_files == 0} {
        dict set scenario_info "scenario" "Push Button"
        dict set scenario_info "strategy" "Export all sources (complete portability)"
    } elseif {$local_files == 0} {
        dict set scenario_info "scenario" "Remote"
        dict set scenario_info "strategy" "Export only project metadata (faster)"
    } else {
        dict set scenario_info "scenario" "Mixed"
        dict set scenario_info "strategy" "Export local sources + reference remote"
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Report findings
    #────────────────────────────────────────────────────────────────────────────
    puts "─────────────────────────────────────────────────────────────────────"
    puts "SOURCE LOCATION ANALYSIS"
    puts "─────────────────────────────────────────────────────────────────────"
    puts "Scenario: [dict get $scenario_info scenario]"
    puts "Local Sources: $local_files"
    puts "Remote Sources: $remote_files"
    puts "Strategy: [dict get $scenario_info strategy]"
    puts "─────────────────────────────────────────────────────────────────────"
    
    return $scenario_info
}

#══════════════════════════════════════════════════════════════════════════════
# UTILITY: _rc_is_exportable_local
# Purpose: Determine if a file is a genuine local project source (under the
# project directory) as opposed to Vivado-managed generated/build output
# (e.g. <proj>.gen, <proj>.cache, <proj>.sim, <proj>.hw, <proj>.ip_user_files,
# <proj>.runs). Files under those directories are transient build artifacts,
# not sources that should be exported/checked into revision control — and
# they may not physically exist (stale references), which would crash a
# plain file copy.
#══════════════════════════════════════════════════════════════════════════════
proc _rc_is_exportable_local {file proj_dir} {
    set norm_file [file normalize $file]
    set norm_proj [file normalize $proj_dir]
    if {[string first $norm_proj $norm_file] != 0} {
        return 0
    }
    foreach pat {"*.gen/*" "*.cache/*" "*.sim/*" "*.hw/*" "*.ip_user_files/*" "*.runs/*" "*/.Xil/*"} {
        if {[string match $pat $norm_file]} {
            return 0
        }
    }
    return 1
}

#══════════════════════════════════════════════════════════════════════════════
# UTILITY: _validate_ip_blocks
# Purpose: Preflight validation for IP blocks referenced by the project
# Returns dict with counts and missing-IP names (for reporting / warnings)
#══════════════════════════════════════════════════════════════════════════════
proc _validate_ip_blocks {} {
    set summary [dict create]
    dict set summary total_ips 0
    dict set summary locked_ips 0
    dict set summary upgraded_ips 0
    dict set summary missing_ips 0
    dict set summary missing_ip_names {}

    set ips [get_ips -quiet]
    dict set summary total_ips [llength $ips]

    puts "IP PRECHECK VALIDATION"
    puts "─────────────────────────────────────────────────────────────────────"

    foreach ip $ips {
        set ip_name [get_property NAME $ip]

        set is_locked [get_property -quiet IS_LOCKED $ip]
        if {$is_locked eq "1" || $is_locked eq "true"} {
            dict incr summary locked_ips
        }

        # Some Vivado versions expose UPGRADE_VERSIONS as list/string.
        catch {
            set upgrade_versions [get_property -quiet UPGRADE_VERSIONS $ip]
            if {[llength $upgrade_versions] > 0 && $upgrade_versions ne ""} {
                dict incr summary upgraded_ips
            }
        }

        # Validate generated outputs / model availability for early warning.
        # If generation artifacts are absent, rebuild may fail later.
        set output_ok 0
        catch {
            set outs [get_files -quiet -of_objects $ip]
            if {[llength $outs] > 0} {
                set output_ok 1
            }
        }
        if {!$output_ok} {
            dict incr summary missing_ips
            dict lappend summary missing_ip_names $ip_name
            puts "  WARNING: IP appears unresolved or has no generated files: $ip_name"
        }
    }

    puts "Total IPs: [dict get $summary total_ips]"
    puts "Locked IPs: [dict get $summary locked_ips]"
    puts "Upgradeable IPs: [dict get $summary upgraded_ips]"
    puts "IPs missing generated artifacts: [dict get $summary missing_ips]"
    if {[dict get $summary missing_ips] > 0} {
        puts "  Missing/Unresolved IP list: [dict get $summary missing_ip_names]"
    }
    puts "─────────────────────────────────────────────────────────────────────"

    return $summary
}

#══════════════════════════════════════════════════════════════════════════════
# PROCEDURE 3: export_all_sources
# Purpose: Export all project components needed to rebuild
# Depends on: detect_project_flow output (flow_info dict)
# Includes special handling for DFX and Segmented Configuration projects
#══════════════════════════════════════════════════════════════════════════════
proc export_all_sources {export_dir {flow_info {}}} {
    set proj [current_project]
    set proj_dir [get_property DIRECTORY $proj]
    
    # Declare globals so they can be accessed by generate_build_script
    global bd_name_array
    global bd_source_order
    global dfx_pr_configs
    global dfx_partition_defs
    global dfx_reconfig_modules
    global dfx_partition_cells
    global dfx_rm_file_tails
    global fileset_compile_order_map
    
    # Initialize
    array unset bd_name_array
    set bd_source_order {}
    set dfx_pr_configs {}
    set dfx_partition_defs {}
    set dfx_reconfig_modules {}
    set dfx_partition_cells {}
    set dfx_rm_file_tails {}
    array unset fileset_compile_order_map
    
    # Create export directory structure
    file mkdir $export_dir/Sources/RTL
    file mkdir $export_dir/Sources/Constraints
    file mkdir $export_dir/Sources/IP
    file mkdir $export_dir/Sources/BD
    file mkdir $export_dir/Sources/Simulation
    file mkdir $export_dir/Sources/Data
    
    set export_stats [dict create]
    dict set export_stats "rtl_files" 0
    dict set export_stats "constraint_files" 0
    dict set export_stats "ip_files" 0
    dict set export_stats "bd_files" 0
    dict set export_stats "sim_files" 0
    dict set export_stats "data_files" 0
    dict set export_stats "hook_scripts" 0
    dict set export_stats "dfx_dcp_files" 0
    dict set export_stats "pblock_files" 0
    dict set export_stats "noc_files" 0
    
    #────────────────────────────────────────────────────────────────────────────
    # Use project type from detect_project_flow output
    # If flow_info not provided, assume standard project (backwards compatibility)
    #────────────────────────────────────────────────────────────────────────────
    if {[dict exists $flow_info is_dfx]} {
        set is_dfx [dict get $flow_info is_dfx]
    } else {
        set is_dfx 0
    }
    
    if {[dict exists $flow_info is_segmented_config]} {
        set is_segmented_cfg [dict get $flow_info is_segmented_config]
    } else {
        set is_segmented_cfg 0
    }
    
    if {$is_dfx} {
        puts "DFX project detected - will export DCP checkpoints and pblocks under Sources/"
        file mkdir $export_dir/Sources/Checkpoints
    }
    
    if {$is_segmented_cfg} {
        puts "Segmented Configuration project detected - will export NoC solution under Sources/"
        file mkdir $export_dir/Sources/NoC
    }

    #────────────────────────────────────────────────────────────────────────────
    # IP preflight validation (early warning for missing/out-of-date IP blocks)
    #────────────────────────────────────────────────────────────────────────────
    set ip_validation [_validate_ip_blocks]
    dict set export_stats "ip_missing_files" [dict get $ip_validation missing_ips]
    
    #────────────────────────────────────────────────────────────────────────────
    # Export RTL Sources
    #────────────────────────────────────────────────────────────────────────────
    puts "Exporting RTL sources..."
    set rtl_files [get_files -quiet -filter {(FILE_TYPE == "Verilog" || FILE_TYPE == "Verilog HDL" || FILE_TYPE == "VHDL" || FILE_TYPE == "VHDL 2008" || FILE_TYPE == "VHDL 2019" || FILE_TYPE == "SystemVerilog" || FILE_TYPE == "SystemVerilog HDL" || FILE_TYPE == "Verilog Header" || FILE_TYPE == "SystemVerilog Header") && USED_IN =~ "*synthesis*"}]
    foreach file $rtl_files {
        set is_generated [get_property IS_GENERATED $file]
        if {$is_generated eq "true" || $is_generated eq "1"} {
            continue
        }
        
        # Export local sources (in .srcs or elsewhere under project directory,
        # excluding Vivado-managed generated/build directories).
        # For DFX projects, also capture remote RM-attached sources so rebuilds
        # remain self-contained.
        set src_file [file normalize $file]
        set in_srcs [expr {[string first ".srcs" $file] > 0}]
        set in_project [_rc_is_exportable_local $file $proj_dir]
        if {$in_srcs || $in_project || $is_dfx} {
            if {![file exists $src_file]} { continue }
            set dest [file join $export_dir/Sources/RTL [file tail $src_file]]
            if {[file exists $dest]} { continue }
            file copy -force $src_file $dest
            dict incr export_stats "rtl_files"
        }
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # DFX: Also export files attached to reconfig_modules (may live outside .srcs)
    # so that the rebuilt project can reattach them to the per-RM filesets.
    # Track basenames so build.tcl can skip them when populating sources_1.
    #────────────────────────────────────────────────────────────────────────────
    if {$is_dfx} {
        foreach rm [get_reconfig_modules -quiet] {
            set rm_files [get_files -quiet -of_objects $rm]
            foreach f $rm_files {
                set is_gen [get_property -quiet IS_GENERATED $f]
                if {$is_gen eq "1" || $is_gen eq "true"} { continue }
                set src [file normalize $f]
                set tail [file tail $src]
                if {![file exists $src]} { continue }
                set dest [file join $export_dir/Sources/RTL $tail]
                if {![file exists $dest]} {
                    file copy -force $src $dest
                    dict incr export_stats "rtl_files"
                }
                lappend dfx_rm_file_tails $tail
            }
        }
        set dfx_rm_file_tails [lsort -unique $dfx_rm_file_tails]
        if {[llength $dfx_rm_file_tails] > 0} {
            puts "  Exported [llength $dfx_rm_file_tails] RM file(s) under Sources/RTL/ (will be attached to per-RM filesets in build.tcl)"
        }
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Export Constraint Files
    #────────────────────────────────────────────────────────────────────────────
    puts "Exporting constraint files..."
    set constraint_files [get_files -quiet -filter {FILE_TYPE == "XDC"}]
    foreach file $constraint_files {
        set is_generated [get_property IS_GENERATED $file]
        if {$is_generated eq "true" || $is_generated eq "1"} {
            continue
        }
        
        # Skip out-of-context XDC files for DFX
        if {[string match "*_ooc.xdc" $file]} {
            continue
        }
        
        # In DFX mode, pblock XDCs are consolidated under Sources/Constraints.
        # Skip them in this generic pass to avoid duplicate copies; they are
        # handled by the DFX consolidation block below.
        if {$is_dfx && [string match -nocase "*pblock*.xdc" [file tail $file]]} {
            continue
        }
        
        set src_file [file normalize $file]
        set in_srcs [expr {[string first ".srcs" $file] > 0}]
        set in_project [_rc_is_exportable_local $file $proj_dir]
        if {($in_srcs || $in_project) && [file exists $src_file]} {
            set dest [file join $export_dir/Sources/Constraints [file tail $src_file]]
            file copy -force $src_file $dest
            dict incr export_stats "constraint_files"
        }
    }
    
    #════════════════════════════════════════════════════════════════════════════
    # Export IP Cores - Generate IP TCL scripts
    # CRITICAL: Skip IPs that are part of Block Designs - they will be exported via write_bd_tcl
    #════════════════════════════════════════════════════════════════════════════
    puts "Exporting IP cores..."
    set ip_cores [get_ips -quiet]
    set bd_ip_count 0
    foreach ip $ip_cores {
        set ip_name [get_property NAME $ip]
        
        # Check if IP is part of a Block Design using IS_BD_CONTEXT property
        # This is more reliable than path matching
        set is_bd_context [get_property -quiet IS_BD_CONTEXT $ip]
        
        if {$is_bd_context eq "1" || $is_bd_context eq "true"} {
            # Skip - this IP is part of a BD and will be exported via write_bd_tcl
            incr bd_ip_count
            continue
        }
        
        puts "  Exporting standalone IP: $ip_name"
        
        # Generate IP creation script (only for standalone IPs outside BDs)
        write_ip_tcl -force [get_ips $ip_name] \
            "$export_dir/Sources/IP/${ip_name}.tcl"
        dict incr export_stats "ip_files"
    }
    
    if {$bd_ip_count > 0} {
        puts "  (Skipped $bd_ip_count IPs that are part of Block Designs)"
    }
    
    #════════════════════════════════════════════════════════════════════════════
    # Export Block Designs - Generate BD TCL scripts
    # IPI BDC Intelligence: Skip auto-generated BDs, only export user-created BDs
    #════════════════════════════════════════════════════════════════════════════
    puts "Exporting Block Designs..."
    set bds [get_files -quiet -filter {FILE_TYPE == "Block Designs"}]
    
    set proj_dir [get_property DIRECTORY $proj]
    set proj_dir_norm [file normalize $proj_dir]
    
    #────────────────────────────────────────────────────────────────────────────
    # Pass 1: Collect exportable BD names (filter out generated/.gen/remote)
    #────────────────────────────────────────────────────────────────────────────
    set exportable_bd_names {}
    set exportable_bd_files [dict create]
    set bd_skipped_count 0
    foreach bd $bds {
        set bd_name [file rootname [file tail $bd]]
        set bd_path [file normalize $bd]
        
        # SKIP: Generated BDs (auto-produced wrapper files, IP-generated BDs)
        set is_generated [get_property IS_GENERATED $bd]
        if {$is_generated eq "true" || $is_generated eq "1"} {
            puts "  Skipping generated BD: $bd_name (IS_GENERATED=true)"
            incr bd_skipped_count
            continue
        }
        
        # SKIP: BDs in .gen/ directory (auto-generated by IP cores and NoC)
        if {[string match "*/.gen/*" $bd_path]} {
            puts "  Skipping .gen/ BD: $bd_name (auto-generated by IP)"
            incr bd_skipped_count
            continue
        }
        
        # SKIP: BDs outside project directory (remote sources - already version controlled)
        if {[string first $proj_dir_norm $bd_path] != 0} {
            puts "  Skipping remote BD: $bd_name (outside project directory)"
            incr bd_skipped_count
            continue
        }
        
        lappend exportable_bd_names $bd_name
        dict set exportable_bd_files $bd_name $bd
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Determine BDC dependency order (children before parents)
    #────────────────────────────────────────────────────────────────────────────
    set bd_source_order [_build_bd_dependency_order $exportable_bd_names]
    
    #────────────────────────────────────────────────────────────────────────────
    # Pass 2: Export BDs in dependency order
    #────────────────────────────────────────────────────────────────────────────
    set bd_count 0
    foreach bd_name $bd_source_order {
        set bd [dict get $exportable_bd_files $bd_name]
        incr bd_count
        
        puts "  Exporting BD ($bd_count): $bd_name"
        if {[catch {
            open_bd_design $bd
            write_bd_tcl -force -include_layout "$export_dir/Sources/BD/${bd_name}.tcl"
            dict incr export_stats "bd_files"
            # Check if BD wrapper exists and where it is located
            set bd_wrapper_present [get_files -quiet *${bd_name}_wrapper*]
            if {$bd_wrapper_present eq ""} {
                puts "  No wrapper found for BD $bd_name"
                # No physical wrapper file was ever generated (Vivado's "auto-managed
                # wrapper" mode - e.g. wrapper created via GUI default "Let Vivado
                # manage wrapper and auto-update" but never yet built). If this BD is
                # still the project's top module, the recreated project needs an
                # explicit make_wrapper/add_files in build.tcl, otherwise synth_design
                # fails with "ERROR: [Synth 8-439] module '<bd>_wrapper' not found"
                # because no source resolves to that module name.
                if {[catch {get_property top [current_fileset]} cur_top]} { set cur_top "" }
                if {$cur_top eq "${bd_name}_wrapper"} {
                    set bd_name_array($bd_count) ${bd_name}_wrapper
                    set bd_name_array(${bd_count}_loc) "gen"
                    puts "  BD $bd_name is project top (auto-managed wrapper, never generated) — will create via make_wrapper"
                }
            } else {
                set wrapper_path [file normalize [lindex $bd_wrapper_present 0]]
                if {[string match "*/.gen/*" $wrapper_path]} {
                    # Wrapper is in .gen/ — will use make_wrapper + add_files in build.tcl
                    set bd_name_array($bd_count) ${bd_name}_wrapper
                    set bd_name_array(${bd_count}_loc) "gen"
                    puts "  Wrapper for BD $bd_name is in .gen/ — will regenerate via make_wrapper"
                } elseif {[string match "*.srcs/*" $wrapper_path]} {
                    # Wrapper is in .srcs/ — already exported with RTL sources
                    puts "  Wrapper for BD $bd_name is in .srcs/ — already exported with RTL"
                } else {
                    # Wrapper in unusual location — flag for make_wrapper
                    set bd_name_array($bd_count) ${bd_name}_wrapper
                    set bd_name_array(${bd_count}_loc) "other"
                    puts "  Wrapper for BD $bd_name at $wrapper_path — will regenerate via make_wrapper"
                }
            }
        } err]} {
            puts "    ERROR exporting BD: $err"
        }
    }
    
    if {$bd_skipped_count > 0} {
        puts "  (Skipped $bd_skipped_count auto-generated/remote BDs)"
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Export Simulation Files
    #────────────────────────────────────────────────────────────────────────────
    puts "Exporting simulation files..."
    set sim_files [get_files -quiet -filter {USED_IN == "simulation"}]
    foreach file $sim_files {
        set is_generated [get_property -quiet IS_GENERATED $file]
        if {$is_generated eq "true" || $is_generated eq "1"} {
            continue
        }
        set src_file [file normalize $file]
        set in_srcs [expr {[string first ".srcs" $file] > 0}]
        set in_project [_rc_is_exportable_local $file $proj_dir]
        if {($in_srcs || $in_project) && [file exists $src_file]} {
            set dest [file join $export_dir/Sources/Simulation [file tail $src_file]]
            file copy -force $src_file $dest
            dict incr export_stats "sim_files"
        }
    }

    #────────────────────────────────────────────────────────────────────────────
    # Preserve per-fileset source ordering for deterministic rebuilds
    #────────────────────────────────────────────────────────────────────────────
    puts "Capturing fileset compile order..."
    foreach fs [get_filesets -quiet] {
        set fs_name [get_property NAME $fs]
        set ordered_files {}
        catch {
            set fs_files [get_files -quiet -of_objects [get_filesets $fs_name]]
            foreach f $fs_files {
                set f_tail [file tail $f]
                if {$f_tail ne ""} {
                    lappend ordered_files $f_tail
                }
            }
        }
        # Keep only meaningful lists to avoid noise.
        if {[llength $ordered_files] > 0} {
            set fileset_compile_order_map($fs_name) $ordered_files
            puts "  Captured fileset order: $fs_name ([llength $ordered_files] file(s))"
        }
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Export Data Files (memory init, config data, ELF, etc.)
    #────────────────────────────────────────────────────────────────────────────
    puts "Exporting data files..."
    set data_exts {.mem .mif .coe .hex .elf}
    set data_files {}
    catch {
        foreach f [get_files -quiet -all] {
            set is_gen [get_property -quiet IS_GENERATED $f]
            if {$is_gen eq "1" || $is_gen eq "true"} { continue }
            set ext [string tolower [file extension $f]]
            if {$ext in $data_exts && [_rc_is_exportable_local $f $proj_dir]} {
                lappend data_files [file normalize $f]
            }
        }
    }
    # Fallback: also pick up loose data files sitting directly in the project
    # root that were never registered as project sources (e.g. dropped in by
    # hand for reference, not added via add_files/import_files).
    catch {lappend data_files {*}[glob -nocomplain -types f $proj_dir/*.{mem,mif,coe,hex,elf}]}
    set data_files [lsort -unique $data_files]
    foreach file $data_files {
        set dest [file join $export_dir/Sources/Data [file tail $file]]
        file copy -force $file $dest
        dict incr export_stats "data_files"
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Export Tcl Hook Scripts (pre/post hooks on synth and impl runs)
    #────────────────────────────────────────────────────────────────────────────
    puts "Exporting hook scripts..."
    file mkdir $export_dir/Sources/Scripts
    set exported_hooks [dict create]
    foreach run_name {synth_1 impl_1} {
        if {[llength [get_runs -quiet $run_name]] == 0} { continue }
        # Dynamically discover all STEPS.*.TCL.PRE/POST properties on this run
        set all_props [list_property [get_runs $run_name]]
        set hook_props [lsearch -all -inline $all_props STEPS.*.TCL.PRE]
        lappend hook_props {*}[lsearch -all -inline $all_props STEPS.*.TCL.POST]
        foreach prop $hook_props {
            catch {
                set val [get_property $prop [get_runs $run_name]]
                if {$val ne "" && [file exists $val]} {
                    set norm_val [file normalize $val]
                    set norm_proj [file normalize $proj_dir]
                    # Only copy local hooks (inside project directory)
                    if {[string match "${norm_proj}/*" $norm_val]} {
                        set fname [file tail $val]
                        if {![dict exists $exported_hooks $fname]} {
                            file copy -force $val [file join $export_dir/Sources/Scripts $fname]
                            dict set exported_hooks $fname 1
                            dict incr export_stats "hook_scripts"
                        }
                    } else {
                        puts "  Skipping remote hook (not copied): $val"
                    }
                }
            }
        }
    }
    
    #════════════════════════════════════════════════════════════════════════════
    # DFX-Specific Exports (if PR_FLOW enabled)
    #════════════════════════════════════════════════════════════════════════════
    if {$is_dfx} {
        puts ""
        puts "════════════════════════════════════════════════════════════════════"
        puts "DFX-SPECIFIC EXPORTS"
        puts "════════════════════════════════════════════════════════════════════"

        #────────────────────────────────────────────────────────────────────────
        # Preflight: Validate that DFX project has RMs and partition definitions
        #────────────────────────────────────────────────────────────────────────
        set pds [get_partition_defs -quiet]
        set rms [get_reconfig_modules -quiet]
        if {[llength $pds] == 0 || [llength $rms] == 0} {
            puts "WARNING: DFX project detected but partition_defs or reconfig_modules are missing or empty"
            puts "  partition_defs: [llength $pds]"
            puts "  reconfig_modules: [llength $rms]"
            puts "  This may indicate an incomplete DFX setup. Build may fail."
        }
        
        #────────────────────────────────────────────────────────────────────────
        # Consolidate pblock XDC files into Sources/Constraints (NOT separate)
        # This ensures unified constraint management in build.tcl
        #────────────────────────────────────────────────────────────────────────
        puts "Consolidating pblock constraints..."
        set pblock_files [get_files -quiet -filter {FILE_TYPE == "XDC" && "pblock" in [file tail [get_property NAME {*}]]}]
        
        # If no pblock file found by name, try to find any XDC with pblock definitions
        if {[llength $pblock_files] == 0} {
            set xdc_files [get_files -quiet -filter {FILE_TYPE == "XDC"}]
            foreach xdc $xdc_files {
                if {[catch {
                    set fd [open $xdc r]
                    set content [read $fd]
                    close $fd
                    if {[string match "*pblock*" $content]} {
                        lappend pblock_files $xdc
                    }
                }]} {
                    # Ignore read errors, just skip this file
                }
            }
        }
        
        # Export ALL XDC files (including pblocks) into Sources/Constraints for unified management
        if {[llength $pblock_files] > 0} {
            foreach pblock_file $pblock_files {
                set src_file [file normalize $pblock_file]
                # Check if already exported to Constraints
                set tail [file tail $src_file]
                set dest_constraints [file join $export_dir/Sources/Constraints $tail]
                if {![file exists $dest_constraints]} {
                    file copy -force $src_file $dest_constraints
                    dict incr export_stats "pblock_files"
                    puts "  Consolidated pblock to Sources/Constraints: $tail"
                }
            }
        } else {
            puts "  INFO: No additional pblock XDC files found (may already be in Constraints)"
        }
        
        #────────────────────────────────────────────────────────────────────────
        # Capture Partition Definitions (needed by generate_build_script)
        #────────────────────────────────────────────────────────────────────────
        puts "Capturing partition definitions..."
        foreach pd [get_partition_defs -quiet] {
            set pd_name    [get_property NAME $pd]
            set pd_module  [get_property -quiet MODULE_NAME $pd]
            set pd_library [get_property -quiet LIBRARY $pd]
            set pd_default [get_property -quiet DEFAULT_RM $pd]
            lappend dfx_partition_defs [dict create \
                name $pd_name \
                module $pd_module \
                library $pd_library \
                default_rm $pd_default]
            puts "  Captured partition_def: $pd_name (module=$pd_module, default_rm=$pd_default)"
        }
        
        #────────────────────────────────────────────────────────────────────────
        # Capture Reconfigurable Modules (needed by generate_build_script)
        #────────────────────────────────────────────────────────────────────────
        puts "Capturing reconfigurable modules..."
        set dfx_rm_owned_bd_names {}
        foreach rm [get_reconfig_modules -quiet] {
            set rm_name [get_property NAME $rm]
            set rm_pd   [get_property -quiet PARTITION_DEF $rm]
            set rm_mod  [get_property -quiet MODULE_NAME $rm]
            set rm_gate [get_property -quiet IS_GATE_LEVEL $rm]
            set rm_files [get_files -quiet -of_objects $rm]
            set rm_tails {}
            foreach f $rm_files {
                set tail [file tail $f]
                lappend rm_tails $tail
                # Track if this is a BD file that belongs to an RM
                # (should not be sourced independently in Step 7)
                if {[string tolower [file extension $tail]] eq ".bd"} {
                    set bd_basename [file rootname $tail]
                    lappend dfx_rm_owned_bd_names $bd_basename
                    puts "    RM-owned BD detected: $bd_basename"
                }
            }
            lappend dfx_reconfig_modules [dict create \
                name $rm_name \
                partition_def $rm_pd \
                module $rm_mod \
                gate_level $rm_gate \
                files $rm_tails]
            puts "  Captured reconfig_module: $rm_name (PD=$rm_pd, module=$rm_mod, [llength $rm_tails] file(s))"
        }
        set dfx_rm_owned_bd_names [lsort -unique $dfx_rm_owned_bd_names]
        
        #────────────────────────────────────────────────────────────────────────
        # Capture HD.RECONFIGURABLE cells (partition cells in the static design)
        # Cells are only visible if the design is elaborated; safe to skip if not.
        #────────────────────────────────────────────────────────────────────────
        puts "Capturing HD.RECONFIGURABLE partition cells..."
        catch {
            foreach cell [get_cells -hierarchical -quiet -filter {HD.RECONFIGURABLE==1}] {
                lappend dfx_partition_cells $cell
                puts "  Captured partition cell: $cell"
            }
        }
        
        #────────────────────────────────────────────────────────────────────────
        # Capture PR Configurations (needed by generate_build_script)
        #────────────────────────────────────────────────────────────────────────
        puts "Capturing PR configurations..."
        set pr_configs [get_pr_configurations -quiet]
        foreach cfg $pr_configs {
            set cfg_name [get_property NAME $cfg]
            set partition_cell_rms [get_property PARTITION_CELL_RMS $cfg]
            set greybox_cells [get_property GREYBOX_CELLS $cfg]
            lappend dfx_pr_configs [dict create \
                name $cfg_name \
                partition_cell_rms $partition_cell_rms \
                greybox_cells $greybox_cells]
            puts "  Captured PR config: $cfg_name"
            if {$partition_cell_rms ne ""} {
                puts "    PARTITION_CELL_RMS: $partition_cell_rms"
            }
            if {$greybox_cells ne ""} {
                puts "    GREYBOX_CELLS: $greybox_cells"
            }
        }
        
        # Capture child implementation runs
        # Child DFX runs have PARENT pointing to an impl run (e.g., impl_1),
        # while impl_1 itself has PARENT pointing to a synth run (e.g., synth_1).
        global dfx_child_runs
        set dfx_child_runs {}
        set impl_runs [get_runs -quiet -filter {IS_IMPLEMENTATION}]
        foreach run $impl_runs {
            set run_name [get_property NAME $run]
            set pr_config [get_property -quiet PR_CONFIGURATION $run]
            set parent_run [get_property -quiet PARENT $run]
            if {$pr_config eq ""} { continue }
            
            # Check if PARENT is an implementation run (= this is a child DFX run)
            # vs a synthesis run (= this is the parent impl_1)
            set parent_is_impl 0
            if {$parent_run ne ""} {
                set parent_run_obj [get_runs -quiet $parent_run]
                if {$parent_run_obj ne ""} {
                    catch {
                        set parent_is_impl_flag [get_property IS_IMPLEMENTATION $parent_run_obj]
                        if {$parent_is_impl_flag eq "1" || $parent_is_impl_flag eq "true"} {
                            set parent_is_impl 1
                        }
                    }
                }
            }
            
            if {$parent_is_impl} {
                # This is a child DFX run (parent is impl_1)
                set strategy [get_property -quiet STRATEGY $run]
                # Capture the FLOW property for create_run -flow
                set flow [get_property -quiet FLOW $run]
                lappend dfx_child_runs [dict create \
                    name $run_name \
                    pr_configuration $pr_config \
                    parent $parent_run \
                    strategy $strategy \
                    flow $flow]
                puts "  Captured child run: $run_name (config=$pr_config, parent=$parent_run, flow=$flow)"
            } else {
                # This is the parent impl run (e.g., impl_1)
                global dfx_parent_impl_config
                set dfx_parent_impl_config $pr_config
                puts "  Captured parent impl config: $run_name -> $pr_config"
            }
        }
        
        dict set export_stats "pr_configs" [llength $dfx_pr_configs]
        dict set export_stats "child_runs" [llength $dfx_child_runs]
        
        #────────────────────────────────────────────────────────────────────────
        # DFX Validation: Ensure partition_defs and RM files are present
        #────────────────────────────────────────────────────────────────────────
        puts "Validating DFX completeness..."
        if {[llength $dfx_partition_defs] == 0} {
            puts "  WARNING: DFX project has PR_FLOW=true but no partition_defs found"
            puts "           Build may fail or DFX features will not be preserved"
        }
        if {[llength $dfx_reconfig_modules] == 0} {
            puts "  WARNING: DFX project has no reconfigurable modules defined"
        }
        if {[llength $dfx_rm_file_tails] == 0 && [llength $dfx_reconfig_modules] > 0} {
            puts "  WARNING: DFX RMs found but no RM source files were exported"
            puts "           This may indicate missing RTL in per-RM filesets"
        }
        
        #────────────────────────────────────────────────────────────────────────
        # Export DCP Checkpoints (locked static DCP)
        #────────────────────────────────────────────────────────────────────────
        puts "Looking for DCP checkpoints..."
        
        # Check standard checkpoint locations
        set checkpoint_dirs [list \
            [file join $proj_dir checkpoints] \
            [file join $proj_dir Checkpoints] \
            [file join $proj_dir checkPoint] \
            [file join $proj_dir CheckPoint] \
        ]
        
        set dcp_files {}
        foreach chk_dir $checkpoint_dirs {
            if {[file exists $chk_dir]} {
                set found_dcps [glob -nocomplain -types f [file join $chk_dir *.dcp]]
                set dcp_files [concat $dcp_files $found_dcps]
            }
        }
        
        # Also check runs directory for locked DCPs
        set runs_dir [file join $proj_dir [get_property NAME $proj].runs]
        if {[file exists $runs_dir]} {
            set found_dcps [glob -nocomplain -types f [file join $runs_dir *locked*.dcp]]
            set dcp_files [concat $dcp_files $found_dcps]
        }
        
        if {[llength $dcp_files] > 0} {
            foreach dcp $dcp_files {
                set dest [file join $export_dir/Sources/Checkpoints [file tail $dcp]]
                file copy -force $dcp $dest
                dict incr export_stats "dfx_dcp_files"
                puts "  Exported checkpoint: [file tail $dcp]"
            }
        } else {
            puts "  INFO: No DCP checkpoints found yet"
            puts "  NOTE: After implementation, export locked static DCP with:"
            puts "        write_checkpoint -force Sources/Checkpoints/static_locked.dcp"
        }
    }
    
    #════════════════════════════════════════════════════════════════════════════
    # Segmented Configuration-Specific Exports (Versal)
    #════════════════════════════════════════════════════════════════════════════
    if {$is_segmented_cfg} {
        puts ""
        puts "════════════════════════════════════════════════════════════════════"
        puts "SEGMENTED CONFIGURATION-SPECIFIC EXPORTS"
        puts "════════════════════════════════════════════════════════════════════"
        
        #────────────────────────────────────────────────────────────────────────
        # Export NoC Solution file (if it exists)
        #────────────────────────────────────────────────────────────────────────
        puts "Looking for NoC solution..."
        
        set noc_search_dirs [list \
            [file join $proj_dir NoC] \
            [file join $proj_dir noc] \
            [file join $proj_dir [get_property NAME $proj].runs] \
        ]
        
        set noc_files {}
        foreach noc_dir $noc_search_dirs {
            if {[file exists $noc_dir]} {
                set found_nocs [glob -nocomplain -types f [file join $noc_dir *.ncr]]
                set noc_files [concat $noc_files $found_nocs]
            }
        }
        
        if {[llength $noc_files] > 0} {
            foreach noc $noc_files {
                set dest [file join $export_dir/Sources/NoC [file tail $noc]]
                file copy -force $noc $dest
                dict incr export_stats "noc_files"
                puts "  Exported NoC solution: [file tail $noc]"
            }
        } else {
            puts "  INFO: No NoC solution (.ncr) files found"
            puts "  NOTE: After implementation, can export with:"
            puts "        write_noc_solution -force NoC/noc_solution.ncr"
        }
        
        #────────────────────────────────────────────────────────────────────────
        # Verify SEGMENTED_CONFIGURATION property presence
        #────────────────────────────────────────────────────────────────────────
        set seg_cfg [get_property SEGMENTED_CONFIGURATION $proj]
        puts ""
        puts "SEGMENTED_CONFIGURATION property verified: $seg_cfg"
        if {$seg_cfg ne "true" && $seg_cfg ne "1"} {
            puts "  WARNING: SEGMENTED_CONFIGURATION not fully enabled"
        }
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Report export statistics
    #────────────────────────────────────────────────────────────────────────────
    puts "─────────────────────────────────────────────────────────────────────"
    puts "EXPORT SUMMARY"
    puts "─────────────────────────────────────────────────────────────────────"
    puts "Export Directory: $export_dir"
    puts "RTL Files: [dict get $export_stats rtl_files]"
    puts "Constraint Files: [dict get $export_stats constraint_files]"
    puts "IP Cores: [dict get $export_stats ip_files]"
    puts "Block Designs: [dict get $export_stats bd_files]"
    puts "Simulation Files: [dict get $export_stats sim_files]"
    puts "Data Files: [dict get $export_stats data_files]"
    puts "Hook Scripts: [dict get $export_stats hook_scripts]"
    if {[dict exists $export_stats ip_missing_files]} {
        puts "IP precheck missing artifacts: [dict get $export_stats ip_missing_files]"
    }
    
    if {$is_dfx} {
        puts ""
        puts "DFX-Specific Exports:"
        puts "  Pblock Files: [dict get $export_stats pblock_files]"
        puts "  DCP Checkpoints: [dict get $export_stats dfx_dcp_files]"
        if {[dict exists $export_stats pr_configs]} {
            puts "  PR Configurations: [dict get $export_stats pr_configs]"
        }
        if {[dict exists $export_stats child_runs]} {
            puts "  Child Impl Runs: [dict get $export_stats child_runs]"
        }
    }
    
    if {$is_segmented_cfg} {
        puts ""
        puts "Segmented Configuration Exports:"
        puts "  NoC Solution Files: [dict get $export_stats noc_files]"
    }
    
    puts "─────────────────────────────────────────────────────────────────────"
    
    return $export_stats
}

#══════════════════════════════════════════════════════════════════════════════
# PROCEDURE 4: capture_project_settings
# Purpose: Export non-default project properties to TCL script
#══════════════════════════════════════════════════════════════════════════════
proc capture_project_settings {output_file} {
    set proj [current_project]
    
    set fd [open $output_file w]
    
    # Write header
    puts $fd "################################################################################"
    puts $fd "# project_settings.tcl - Captured Project Configuration"
    puts $fd "# Generated: [clock format [clock seconds] -format {%a %b %d %H:%M:%S %Y}]"
    puts $fd "# Project: [get_property NAME $proj]"
    puts $fd "# Device: [get_property PART $proj]"
    puts $fd "################################################################################"
    puts $fd ""
    puts $fd "# Derive base_dir for referencing exported files (e.g., hook scripts)"
    puts $fd "set base_dir \[file dirname \[file dirname \[file normalize \[info script\]\]\]\]"
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # PROJECT PROPERTIES
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    puts $fd "# PROJECT SETTINGS"
    puts $fd "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    set part [get_property PART $proj]
    puts $fd "set_property PART $part \[current_project\]"
    
    set board [get_property BOARD_PART $proj]
    if {[string length $board] > 0} {
        puts $fd "set_property BOARD_PART {$board} \[current_project\]"
    }
    
    set lang [get_property TARGET_LANGUAGE $proj]
    if {[string length $lang] > 0} {
        puts $fd "set_property TARGET_LANGUAGE {$lang} \[current_project\]"
    }
    
    set ip_cache_perms [get_property IP_CACHE_PERMISSIONS $proj]
    if {[string length $ip_cache_perms] > 0} {
        puts $fd "set_property IP_CACHE_PERMISSIONS {$ip_cache_perms} \[current_project\]"
    }
    
    # Critical: DFX support
    set pr_flow [get_property PR_FLOW $proj]
    if {$pr_flow eq "true" || $pr_flow eq "1"} {
        puts $fd "set_property PR_FLOW true \[current_project\]"
    }
    
    # Critical: Segmented Configuration
    set seg_cfg [get_property SEGMENTED_CONFIGURATION $proj]
    if {$seg_cfg eq "true" || $seg_cfg eq "1"} {
        puts $fd "set_property SEGMENTED_CONFIGURATION true \[current_project\]"
    }
    
    # NOTE: IP_REPO_PATHS is set in build.tcl Step 1b (with relative paths)
    # before BD sourcing — not duplicated here to avoid absolute path overwrite.
    
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # FILESET PROPERTIES (source, simulation, constraints, utils)
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    puts $fd "# FILESET SETTINGS"
    puts $fd "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    set src_fileset "sources_1"
    if {[llength [get_filesets $src_fileset]] > 0} {
        set top [get_property TOP [get_filesets $src_fileset]]
        if {[string length $top] > 0} {
            puts $fd "set_property TOP {$top} \[get_filesets $src_fileset\]"
        }
        
        set verilog_defs [get_property VERILOG_DEFINE [get_filesets $src_fileset]]
        if {[llength $verilog_defs] > 0} {
            puts $fd "set_property VERILOG_DEFINE {$verilog_defs} \[get_filesets $src_fileset\]"
        }
        
        set vhdl_defs [get_property VHDL_DEFINE [get_filesets $src_fileset]]
        if {[llength $vhdl_defs] > 0} {
            puts $fd "set_property VHDL_DEFINE {$vhdl_defs} \[get_filesets $src_fileset\]"
        }
        
        set generics [get_property GENERIC [get_filesets $src_fileset]]
        if {[string length $generics] > 0} {
            puts $fd "set_property GENERIC {$generics} \[get_filesets $src_fileset\]"
        }
    }
    
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # SYNTHESIS PROPERTIES (main synth_1 only — OOC runs are auto-created)
    # Only capture non-default settings
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    puts $fd "# SYNTHESIS SETTINGS (non-default only)"
    puts $fd "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    set synth_run_name "synth_1"
    if {[llength [get_runs -quiet $synth_run_name]] > 0} {
        set synth_has_nondefault 0
        
        # Check STRATEGY (non-default if not "*Defaults*")
        catch {
            set strategy [get_property STRATEGY [get_runs $synth_run_name]]
            if {$strategy ne "" && ![string match "*Defaults*" $strategy]} {
                puts $fd "set_property STRATEGY {$strategy} \[get_runs $synth_run_name\]"
                set synth_has_nondefault 1
            }
        }
        
        # Always capture individual STEPS properties — users may customize
        # STEPS values on top of a custom strategy.
        foreach prop {
            STEPS.SYNTH_DESIGN.ARGS.DIRECTIVE
            STEPS.SYNTH_DESIGN.ARGS.FLATTEN_HIERARCHY
            STEPS.SYNTH_DESIGN.ARGS.KEEP_EQUIVALENT_REGISTERS
            STEPS.SYNTH_DESIGN.ARGS.NO_LC
            STEPS.SYNTH_DESIGN.ARGS.SHREG_MIN_SIZE
            STEPS.SYNTH_DESIGN.ARGS.CONTROL_SET_OPT_THRESHOLD
        } {
            catch {
                set val [get_property $prop [get_runs $synth_run_name]]
                # Skip empty, default, or "rebuilt" (default for FLATTEN_HIERARCHY)
                if {$val ne "" && $val ne "Default" && $val ne "default" \
                    && $val ne "rebuilt" && $val ne "0" && $val ne "1"} {
                    puts $fd "set_property $prop {$val} \[get_runs $synth_run_name\]"
                    set synth_has_nondefault 1
                }
            }
        }
        
        if {!$synth_has_nondefault} {
            puts $fd "# All synthesis settings are at defaults — nothing to restore"
        }
    }
    
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # IMPLEMENTATION PROPERTIES (main impl_1 only — OOC runs are auto-created)
    # Only capture non-default settings
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    puts $fd "# IMPLEMENTATION SETTINGS (non-default only)"
    puts $fd "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    set impl_run_name "impl_1"
    if {[llength [get_runs -quiet $impl_run_name]] > 0} {
        set impl_has_nondefault 0
        
        # Check STRATEGY (non-default if not "*Defaults*")
        catch {
            set strategy [get_property STRATEGY [get_runs $impl_run_name]]
            if {$strategy ne "" && ![string match "*Defaults*" $strategy]} {
                puts $fd "set_property STRATEGY {$strategy} \[get_runs $impl_run_name\]"
                set impl_has_nondefault 1
            }
        }
        
        # Always capture individual STEPS properties — users may customize
        # STEPS values on top of a custom strategy.
        foreach prop {
            STEPS.OPT_DESIGN.ARGS.DIRECTIVE
            STEPS.PLACE_DESIGN.ARGS.DIRECTIVE
            STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE
            STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE
            STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE
        } {
            catch {
                set val [get_property $prop [get_runs $impl_run_name]]
                # Skip empty or default
                if {$val ne "" && $val ne "Default" && $val ne "default"} {
                    puts $fd "set_property $prop {$val} \[get_runs $impl_run_name\]"
                    set impl_has_nondefault 1
                }
            }
        }
        
        if {!$impl_has_nondefault} {
            puts $fd "# All implementation settings are at defaults — nothing to restore"
        }
    }
    
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # TCL HOOK SCRIPTS (pre/post hooks on synth and impl runs)
    # Local hooks reference exported scripts in Sources/Scripts/ via $base_dir
    # Remote hooks preserve their original absolute path
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    puts $fd "# TCL HOOK SCRIPTS (pre/post hooks)"
    puts $fd "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    set proj_dir [get_property DIRECTORY $proj]
    set norm_proj [file normalize $proj_dir]
    set has_hooks 0
    foreach run_name {synth_1 impl_1} {
        if {[llength [get_runs -quiet $run_name]] == 0} { continue }
        # Dynamically discover all STEPS.*.TCL.PRE/POST properties on this run
        set all_props [list_property [get_runs $run_name]]
        set hook_props [lsearch -all -inline $all_props STEPS.*.TCL.PRE]
        lappend hook_props {*}[lsearch -all -inline $all_props STEPS.*.TCL.POST]
        foreach prop $hook_props {
            catch {
                set val [get_property $prop [get_runs $run_name]]
                if {$val ne ""} {
                    set norm_val [file normalize $val]
                    if {[string match "${norm_proj}/*" $norm_val]} {
                        # Local hook — reference from exported Sources/Scripts/
                        set fname [file tail $val]
                        puts $fd "set_property $prop \[file normalize \$base_dir/Sources/Scripts/$fname\] \[get_runs $run_name\]"
                    } else {
                        # Remote hook — preserve original absolute path
                        puts $fd "set_property $prop {$norm_val} \[get_runs $run_name\]"
                    }
                    set has_hooks 1
                }
            }
        }
    }
    if {!$has_hooks} {
        puts $fd "# No hook scripts configured"
    }
    
    puts $fd ""
    puts $fd "puts \"Project settings restored from saved configuration\""
    
    close $fd
    
    puts "Settings exported to: $output_file"
    
    #════════════════════════════════════════════════════════════════════════════
    # If DFX, emit a companion project_settings_dfx.tcl alongside the main file.
    # build.tcl will source it AFTER static elaboration and call:
    #   dfx_define_partition         (BEFORE RM OOC synth)
    #   dfx_define_configurations    (AFTER RM OOC synth, before impl)
    #════════════════════════════════════════════════════════════════════════════
    global dfx_partition_defs
    global dfx_reconfig_modules
    global dfx_partition_cells
    global dfx_pr_configs
    global dfx_child_runs
    global dfx_parent_impl_config
    
    set pr_flow_val [get_property -quiet PR_FLOW $proj]
    set is_dfx_proj [expr {$pr_flow_val eq "true" || $pr_flow_val eq "1"}]
    
    if {$is_dfx_proj && [info exists dfx_partition_defs] && [llength $dfx_partition_defs] > 0} {
        set dfx_file [file join [file dirname $output_file] "project_settings_dfx.tcl"]
        set fd2 [open $dfx_file w]
        puts $fd2 "################################################################################"
        puts $fd2 "# project_settings_dfx.tcl - DFX Hierarchy Definitions"
        puts $fd2 "# Generated: [clock format [clock seconds] -format {%a %b %d %H:%M:%S %Y}]"
        puts $fd2 "#"
        puts $fd2 "# Provides two procs invoked from build.tcl at the correct DFX phases:"
        puts $fd2 "#   dfx_define_partition       - create partition_defs, reconfig_modules,"
        puts $fd2 "#                                attach RM files, mark HD.RECONFIGURABLE cells."
        puts $fd2 "#                                Call BEFORE OOC synth of RMs."
        puts $fd2 "#   dfx_define_configurations  - create pr_configurations and PR-configured"
        puts $fd2 "#                                child impl runs."
        puts $fd2 "#                                Call AFTER OOC synth of RMs."
        puts $fd2 "################################################################################"
        puts $fd2 ""
        puts $fd2 "set base_dir \[file dirname \[file dirname \[file normalize \[info script\]\]\]\]"
        puts $fd2 ""
        
        #─────────── proc dfx_define_partition ───────────
        puts $fd2 "proc dfx_define_partition {} {"
        puts $fd2 "    global base_dir"
        puts $fd2 ""
        puts $fd2 "    # Partition Definitions"
        foreach pd_dict $dfx_partition_defs {
            set pd_name   [dict get $pd_dict name]
            set pd_module [dict get $pd_dict module]
            puts $fd2 "    if {\[llength \[get_partition_defs -quiet $pd_name\]\] == 0} {"
            puts $fd2 "        create_partition_def -name $pd_name -module $pd_module"
            puts $fd2 "        puts \"    Created partition_def: $pd_name (module $pd_module)\""
            puts $fd2 "    }"
        }
        puts $fd2 ""
        puts $fd2 "    # Reconfigurable Modules — first file used with -define_from_file"
        foreach rm_dict $dfx_reconfig_modules {
            set rm_name   [dict get $rm_dict name]
            set rm_pd     [dict get $rm_dict partition_def]
            set rm_module [dict get $rm_dict module]
            set rm_gate   [dict get $rm_dict gate_level]
            set rm_files  [dict get $rm_dict files]
            if {[llength $rm_files] == 0} {
                puts $fd2 "    # WARNING: reconfig_module $rm_name has no source files"
                continue
            }
            set gate_arg ""
            if {$rm_gate eq "1" || $rm_gate eq "true"} { set gate_arg " -gate_level" }
            # Create RM bare, then add files and set TOP — this pattern is
            # robust across Vivado versions; -define_from_file has been seen
            # to spuriously report missing files on remote-mounted paths.
            puts $fd2 "    if {\[llength \[get_reconfig_modules -quiet $rm_name\]\] == 0} {"
            puts $fd2 "        create_reconfig_module -name $rm_name \\"
            puts $fd2 "            -partition_def \[get_partition_defs $rm_pd\]$gate_arg"
            puts $fd2 "        puts \"    Created reconfig_module: $rm_name (PD=$rm_pd, module=$rm_module)\""
            puts $fd2 "    }"
            # Separate BD files from other sources — BDs need to be sourced in RM context
            foreach f $rm_files {
                set file_ext [string tolower [file extension $f]]
                if {$file_ext eq ".bd"} {
                    # For BD files within RMs, source the BD TCL if available
                    set bd_basename [file rootname $f]
                    puts $fd2 "    # Source BD (owned by RM) in RM fileset context"
                    puts $fd2 "    set _bd_tcl \[file normalize \$base_dir/Sources/BD/${bd_basename}.tcl\]"
                    puts $fd2 "    if {\[file exists \$_bd_tcl\]} {"
                    puts $fd2 "        catch {source \$_bd_tcl}"
                    puts $fd2 "        puts \"    Sourced RM-owned BD in RM fileset: $bd_basename\""
                    puts $fd2 "    }"
                }
            }
            # Attach all RM files (RTL + BD sources) to the per-RM fileset
            foreach f $rm_files {
                set file_ext [string tolower [file extension $f]]
                # Skip .bd files as they are handled by sourcing their TCL above
                if {$file_ext eq ".bd"} { continue }
                puts $fd2 "    set _rm_file \[file normalize \$base_dir/Sources/RTL/$f\]"
                puts $fd2 "    if {\[file exists \$_rm_file\]} {"
                puts $fd2 "        catch {import_files -quiet -of_objects \[get_reconfig_modules $rm_name\] \$_rm_file}"
                puts $fd2 "    } else {"
                puts $fd2 "        puts \"    WARNING: RM file not found: \$_rm_file\""
                puts $fd2 "    }"
            }
            puts $fd2 "    catch {set_property TOP $rm_module \[get_filesets $rm_name\]}"
            puts $fd2 "    catch {update_compile_order -fileset $rm_name}"
        }
        puts $fd2 ""
        puts $fd2 "    # HD.RECONFIGURABLE cells (requires elaborated/loaded static design;"
        puts $fd2 "    # set after the static fileset top has been resolved)"
        foreach cell $dfx_partition_cells {
            puts $fd2 "    if {\[catch {set_property HD.RECONFIGURABLE 1 \[get_cells -quiet $cell\]} _err\]} {"
            puts $fd2 "        puts \"    INFO: HD.RECONFIGURABLE on $cell deferred (\$_err)\""
            puts $fd2 "    } else {"
            puts $fd2 "        puts \"    Set HD.RECONFIGURABLE on cell: $cell\""
            puts $fd2 "    }"
        }
        puts $fd2 "    return"
        puts $fd2 "}"
        puts $fd2 ""
        
        #─────────── proc dfx_define_configurations ───────────
        puts $fd2 "proc dfx_define_configurations {} {"
        puts $fd2 "    # PR Configurations"
        foreach cfg_dict $dfx_pr_configs {
            set cfg_name [dict get $cfg_dict name]
            set parts    [dict get $cfg_dict partition_cell_rms]
            set greybox  [dict get $cfg_dict greybox_cells]
            puts $fd2 "    if {\[llength \[get_pr_configurations -quiet $cfg_name\]\] == 0} {"
            if {$greybox ne ""} {
                puts $fd2 "        create_pr_configuration -name $cfg_name -greyboxes \[list $greybox\]"
            } elseif {$parts ne ""} {
                puts $fd2 "        create_pr_configuration -name $cfg_name -partitions \[list $parts\]"
            }
            puts $fd2 "        puts \"    Created pr_configuration: $cfg_name\""
            puts $fd2 "    }"
        }
        puts $fd2 ""
        
        # Parent impl config
        if {[info exists dfx_parent_impl_config] && $dfx_parent_impl_config ne ""} {
            puts $fd2 "    # Assign PR configuration to parent impl_1"
            puts $fd2 "    catch {set_property PR_CONFIGURATION $dfx_parent_impl_config \[get_runs impl_1\]}"
            puts $fd2 ""
        }
        
        # Child PR runs
        if {[info exists dfx_child_runs] && [llength $dfx_child_runs] > 0} {
            puts $fd2 "    # Child implementation runs (one per PR configuration)"
            foreach child_dict $dfx_child_runs {
                set child_name   [dict get $child_dict name]
                set child_cfg    [dict get $child_dict pr_configuration]
                set child_parent [dict get $child_dict parent]
                set child_flow   [dict get $child_dict flow]
                set flow_arg ""
                if {$child_flow ne ""} { set flow_arg " -flow {$child_flow}" }
                puts $fd2 "    if {\[llength \[get_runs -quiet $child_name\]\] == 0} {"
                puts $fd2 "        create_run $child_name -parent_run $child_parent$flow_arg -pr_config $child_cfg"
                puts $fd2 "        puts \"    Created child run: $child_name (pr_config $child_cfg)\""
                puts $fd2 "    }"
            }
        }
        puts $fd2 "    return"
        puts $fd2 "}"
        
        close $fd2
        puts "DFX hierarchy exported to: $dfx_file"
    }
    
    return $output_file
}

#══════════════════════════════════════════════════════════════════════════════
# UTILITY: _relative_path
# Purpose: Compute a relative path from base directory to target path
# Example: _relative_path /a/b/c /a/d/e → ../../d/e
#══════════════════════════════════════════════════════════════════════════════
proc _relative_path {base target} {
    set base_parts [file split [file normalize $base]]
    set target_parts [file split [file normalize $target]]
    
    # Find common prefix length
    set common 0
    set max [expr {min([llength $base_parts], [llength $target_parts])}]
    for {set i 0} {$i < $max} {incr i} {
        if {[lindex $base_parts $i] ne [lindex $target_parts $i]} {
            break
        }
        set common [expr {$i + 1}]
    }
    
    # Build relative path: ".." for each remaining base component + target remainder
    set rel_parts {}
    for {set i $common} {$i < [llength $base_parts]} {incr i} {
        lappend rel_parts ".."
    }
    for {set i $common} {$i < [llength $target_parts]} {incr i} {
        lappend rel_parts [lindex $target_parts $i]
    }
    
    if {[llength $rel_parts] == 0} {
        return "."
    }
    return [file join {*}$rel_parts]
}

#══════════════════════════════════════════════════════════════════════════════
# UTILITY: _build_bd_dependency_order
# Purpose: Determine correct sourcing order for BD Tcl scripts when BDCs exist
# BDC child BDs must be sourced BEFORE parent BDs that instantiate them.
# Handles: multiple children per parent, multiple nesting levels, BDC variants
# (CONFIG.LIST_SYNTH_BD may contain multiple comma-separated BD names)
#══════════════════════════════════════════════════════════════════════════════
proc _build_bd_dependency_order {bd_names_list} {
    if {[llength $bd_names_list] <= 1} {
        return $bd_names_list
    }

    # Build dependency map: parent_bd_name -> {child_bd_name ...}
    set children [dict create]
    foreach bd_name $bd_names_list {
        dict set children $bd_name {}
    }

    foreach bd_name $bd_names_list {
        set bd_file [get_files -quiet ${bd_name}.bd]
        if {$bd_file eq ""} { continue }
        if {[catch {
            open_bd_design $bd_file
            # Check all cells (including hierarchical) for BDC references
            set bd_cells [get_bd_cells -quiet -hier]
            foreach cell $bd_cells {
                # CONFIG.LIST_SYNTH_BD contains all BD variants for a BDC cell
                set list_synth [get_property -quiet CONFIG.LIST_SYNTH_BD $cell]
                if {$list_synth ne ""} {
                    # Handle comma-separated and/or space-separated formats
                    set child_list [split [string map {"," " "} $list_synth]]
                    foreach child_entry $child_list {
                        set child_entry [string trim $child_entry]
                        if {$child_entry eq ""} { continue }
                        # Remove .bd extension if present
                        set child_name [file rootname $child_entry]
                        if {$child_name in $bd_names_list} {
                            set current [dict get $children $bd_name]
                            if {$child_name ni $current} {
                                dict lappend children $bd_name $child_name
                            }
                        }
                    }
                }
                # Also check ACTIVE_SYNTH_BD as fallback for single-variant BDCs
                set active_synth [get_property -quiet CONFIG.ACTIVE_SYNTH_BD $cell]
                if {$active_synth ne ""} {
                    set child_name [file rootname [string trim $active_synth]]
                    if {$child_name ne "" && $child_name in $bd_names_list} {
                        set current [dict get $children $bd_name]
                        if {$child_name ni $current} {
                            dict lappend children $bd_name $child_name
                        }
                    }
                }
            }
        } err]} {
            puts "  Warning: Could not analyze BDC dependencies for $bd_name: $err"
        }
    }

    # Report detected BDC relationships
    dict for {parent child_list} $children {
        if {[llength $child_list] > 0} {
            puts "  BDC dependency: $parent requires children: $child_list"
        }
    }

    # Topological sort (Kahn's algorithm) — children before parents
    # in_degree = number of BDC children a BD depends on
    set in_degree [dict create]
    foreach name $bd_names_list {
        dict set in_degree $name 0
    }
    dict for {parent child_list} $children {
        dict set in_degree $parent [llength $child_list]
    }

    # Start with BDs that have no BDC children (leaf BDs)
    set queue {}
    dict for {name degree} $in_degree {
        if {$degree == 0} {
            lappend queue $name
        }
    }

    set ordered {}
    while {[llength $queue] > 0} {
        set current [lindex $queue 0]
        set queue [lrange $queue 1 end]
        lappend ordered $current

        # For each BD that lists 'current' as a child, decrement its in-degree
        dict for {parent child_list} $children {
            if {$current in $child_list} {
                dict incr in_degree $parent -1
                if {[dict get $in_degree $parent] == 0} {
                    lappend queue $parent
                }
            }
        }
    }

    if {[llength $ordered] != [llength $bd_names_list]} {
        puts "WARNING: Circular BD dependencies detected - using original order"
        return $bd_names_list
    }

    puts "  BD source order: $ordered"
    return $ordered
}

#══════════════════════════════════════════════════════════════════════════════
# PROCEDURE 5: generate_build_script
# Purpose: Auto-generate build.tcl that recreates project from exports
#══════════════════════════════════════════════════════════════════════════════
proc generate_build_script {output_file output_dir {scenario_info {}}} {
    set proj [current_project]
    set proj_name [get_property NAME $proj]
    set device [get_property PART $proj]
    set board [get_property BOARD_PART $proj]
    
    # Declare globals so we can access them from export_all_sources
    global bd_name_array
    global bd_source_order
    global fileset_compile_order_map
    
    # Determine source scenario for import_files vs add_files decision
    set scenario "Push Button"
    set remote_paths {}
    if {[dict exists $scenario_info scenario]} {
        set scenario [dict get $scenario_info scenario]
    }
    if {[dict exists $scenario_info remote_paths]} {
        set remote_paths [dict get $scenario_info remote_paths]
    }
    
    set fd [open $output_file w]
    
    # Write header
    puts $fd "#*******************************************************************************"
    puts $fd "# build.tcl - Project Recreation Script"
    puts $fd "# Generated: [clock format [clock seconds] -format {%a %b %d %H:%M:%S %Y}]"
    puts $fd "# Original Project: $proj_name"
    puts $fd "#*******************************************************************************"
    puts $fd ""
    puts $fd "set script_dir \[file dirname \[file normalize \[info script\]\]\]"
    puts $fd "set base_dir \[file dirname \$script_dir\]"
    puts $fd "set proj_name $proj_name"
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 1: Create Project
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 1: Create Project"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "create_project $proj_name . -part $device -force"
    if {[string length $board] > 0} {
        puts $fd "set_property BOARD_PART {$board} \[current_project\]"
    }
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 1b: Set IP Repository Paths (MUST be before BD sourcing)
    #────────────────────────────────────────────────────────────────────────────
    set ip_repos [get_property IP_REPO_PATHS $proj]
    if {[llength $ip_repos] > 0} {
        puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
        puts $fd "# Step 1b: Set Custom IP Repository Paths (required before BD recreation)"
        puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
        puts $fd "set_property IP_REPO_PATHS \[list \\"
        foreach repo $ip_repos {
            set rel_repo [_relative_path $output_dir $repo]
            puts $fd "    \[file normalize \$base_dir/$rel_repo\] \\"
        }
        puts $fd "\] \[current_project\]"
        puts $fd "update_ip_catalog"
        puts $fd ""
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 2: Add RTL Sources (import_files for local, add_files for remote)
    #         For DFX projects, exclude files belonging to reconfig_modules —
    #         those are attached to per-RM filesets in dfx_define_partition.
    #────────────────────────────────────────────────────────────────────────────
    global dfx_rm_file_tails
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 2: Add RTL Sources"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    if {[info exists dfx_rm_file_tails] && [llength $dfx_rm_file_tails] > 0} {
        puts $fd "# DFX: exclude RM files from sources_1 (they are attached to per-RM filesets)"
        puts $fd "set _rm_tails \[list [join $dfx_rm_file_tails " "]\]"
        puts $fd "set rtl_files {}"
        puts $fd "catch {"
        puts $fd "    foreach _f \[glob \$base_dir/Sources/RTL/*\] {"
        puts $fd "        if {\[lsearch -exact \$_rm_tails \[file tail \$_f\]\] >= 0} { continue }"
        puts $fd "        lappend rtl_files \$_f"
        puts $fd "    }"
        puts $fd "}"
    } else {
        puts $fd "# Local sources — import into project for full portability"
        puts $fd "set rtl_files {}"
        puts $fd "catch {set rtl_files \[glob \$base_dir/Sources/RTL/*\]}"
    }
    puts $fd "if {\[llength \$rtl_files\] > 0} {"
    puts $fd "    import_files \$rtl_files"
    puts $fd "}"
    puts $fd ""
    # If Mixed/Remote scenario: also reference remote sources in-place
    # NOTE: remote files live outside the project/export directory (e.g. a shared
    # testcase or IP repo elsewhere on disk) and are never copied. Their relative
    # offset to the export dir is NOT guaranteed to stay the same when the
    # exported RevisionControl dir is cloned/copied elsewhere, so reference them
    # by their original absolute path rather than a computed relative path.
    if {($scenario eq "Mixed" || $scenario eq "Remote") && [llength $remote_paths] > 0} {
        puts $fd "# Remote sources — referenced in-place at their original absolute location"
        foreach rpath $remote_paths {
            # Only add RTL-type remote files
            set ext [string tolower [file extension $rpath]]
            if {$ext in {.v .sv .vhd .vhdl .vh .svh}} {
                puts $fd "catch {add_files -quiet \[file normalize \"$rpath\"\]}"
            }
        }
        puts $fd ""
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 3: Add Constraint Files (unified from Sources/Constraints)
    # NOTE: All XDC files including pblock constraints are consolidated into
    #       Sources/Constraints for clarity and deterministic tool referencing.
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 3: Add Constraint Files"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "set xdc_files {}"
    puts $fd "catch {set xdc_files \[glob \$base_dir/Sources/Constraints/*.xdc\]}"
    puts $fd "# Include NoC constraints if present"
    puts $fd "catch {lappend xdc_files {*}\[glob \$base_dir/Sources/NoC/*.xdc\]}"
    puts $fd "if {\[llength \$xdc_files\] > 0} {"
    puts $fd "    import_files -fileset constrs_1 \$xdc_files"
    puts $fd "}"
    if {($scenario eq "Mixed" || $scenario eq "Remote") && [llength $remote_paths] > 0} {
        puts $fd "# Remote constraints — referenced in-place at their original absolute location"
        foreach rpath $remote_paths {
            set ext [string tolower [file extension $rpath]]
            if {$ext eq ".xdc"} {
                puts $fd "catch {add_files -quiet -fileset constrs_1 \[file normalize \"$rpath\"\]}"
            }
        }
        puts $fd ""
    }
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 4: Add Simulation Files
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 4: Add Simulation Files"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "set sim_files {}"
    puts $fd "catch {set sim_files \[glob \$base_dir/Sources/Simulation/*\]}"
    puts $fd "if {\[llength \$sim_files\] > 0} {"
    puts $fd "    import_files -fileset sim_1 \$sim_files"
    puts $fd "}"
    if {($scenario eq "Mixed" || $scenario eq "Remote") && [llength $remote_paths] > 0} {
        puts $fd "# Remote simulation files — referenced in-place at their original absolute location"
        foreach rpath $remote_paths {
            set ext [string tolower [file extension $rpath]]
            if {$ext in {.sv .svh .v .vh .vhd .vhdl .vp .mem .hex .coe .dat .wcfg .tcl .txt}} {
                puts $fd "catch {add_files -quiet -fileset sim_1 \[file normalize \"$rpath\"\]}"
            }
        }
        puts $fd ""
    }
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 5: Add Data Files
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 5: Add Data Files (Memory init, config, etc.)"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "set data_files {}"
    puts $fd "catch {set data_files \[glob \$base_dir/Sources/Data/*\]}"
    puts $fd "if {\[llength \$data_files\] > 0} {"
    puts $fd "    import_files \$data_files"
    puts $fd "}"
    if {($scenario eq "Mixed" || $scenario eq "Remote") && [llength $remote_paths] > 0} {
        puts $fd "# Remote data files — referenced in-place at their original absolute location"
        foreach rpath $remote_paths {
            set ext [string tolower [file extension $rpath]]
            if {$ext in {.mem .hex .coe .dat .bin .elf .txt .csv .json .xml .yaml .yml}} {
                puts $fd "catch {add_files -quiet \[file normalize \"$rpath\"\]}"
            }
        }
        puts $fd ""
    }
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 6: Recreate IP Cores
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 6: Recreate IP Cores"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "set ip_tcls {}"
    puts $fd "catch {set ip_tcls \[glob \$base_dir/Sources/IP/*.tcl\]}"
    puts $fd "foreach ip_tcl \$ip_tcls {"
    puts $fd "    source \$ip_tcl"
    puts $fd "}"
    puts $fd ""
    puts $fd "# Create OOC synthesis runs for standalone IPs (outside Block Designs)"
    puts $fd "set standalone_ips \[get_ips\]"
    puts $fd "foreach ip \$standalone_ips {"
    puts $fd "    set is_bd \[get_property IS_BD_CONTEXT \$ip\]"
    puts $fd "    if {\$is_bd ne \"1\" && \$is_bd ne \"true\"} {"
    puts $fd "        catch {create_ip_run \[get_ips \[get_property NAME \$ip\]\]}"
    puts $fd "    }"
    puts $fd "}"
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 7: Recreate Block Designs
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 7: Recreate Block Designs"
    puts $fd "# CRITICAL: BDC child BDs must be sourced BEFORE parent BDs that instantiate them."
    puts $fd "# For DFX: RM Block Designs are skipped here — they are added to RM filesets in"
    puts $fd "#          Step 12 (dfx_define_partition) to prevent them from becoming independent BDs."
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    # List of RM-owned BD names (should not be sourced independently)
    if {[info exists dfx_rm_owned_bd_names] && [llength $dfx_rm_owned_bd_names] > 0} {
        puts $fd "# DFX: Skip these BD names as they belong to reconfig_modules"
        puts $fd "set _rm_owned_bd_names [list [join $dfx_rm_owned_bd_names " "]]"
    }
    # Source BDs in explicit dependency order (children before parents)
    if {[info exists bd_source_order] && [llength $bd_source_order] > 0} {
        foreach bd_name $bd_source_order {
            # Check if this BD is RM-owned (DFX) — if so, skip it
            puts $fd "set bd_is_rm_owned 0"
            puts $fd "if {\[info exists _rm_owned_bd_names\]} {"
            puts $fd "    set bd_is_rm_owned \[expr {\[lsearch \$_rm_owned_bd_names \"$bd_name\"\] >= 0}\]"
            puts $fd "}"
            puts $fd "if {\$bd_is_rm_owned} {"
            puts $fd "    puts \"  Skipping BD (RM-owned): ${bd_name} — will be added to RM fileset in Step 12\""
            puts $fd "} else {"
            puts $fd "    set bd_tcl \$base_dir/Sources/BD/${bd_name}.tcl"
            puts $fd "    if {\[file exists \$bd_tcl\]} {"
            puts $fd "        puts \"  Sourcing BD: ${bd_name}\""
            puts $fd "        source \$bd_tcl"
            puts $fd "        # Validate immediately: BDC parents that reference this BD as a"
            puts $fd "        # container (create_bd_cell -type container -reference) require it"
            puts $fd "        # to be validated first, otherwise cell creation fails."
            puts $fd "        if {\[catch {validate_bd_design -force -design ${bd_name}} _vbd_err\]} {"
            puts $fd "            puts \"  WARNING: validate_bd_design reported an error for ${bd_name}: \$_vbd_err\""
            puts $fd "            puts \"  This is usually caused by locked/out-of-date IPs in the BD.\""
            puts $fd "            puts \"  On the ORIGINAL project: open_bd_design ${bd_name}.bd; report_ip_status\""
            puts $fd "            puts \"  then upgrade_ip the flagged IPs and re-run this export.\""
            puts $fd "            puts \"  NOTE: Vivado now treats this session as having a logged error --\""
            puts $fd "            puts \"  later commands (e.g. make_wrapper) may fail with 'failed due to\""
            puts $fd "            puts \"  earlier errors' even though THIS is the real root cause.\""
            puts $fd "        }"
            puts $fd "        # Persist the validated state — without this, a sibling BD sourced"
            puts $fd "        # later that references this BD as a BDC container may see it as"
            puts $fd "        # not validated (validate_bd_design alone is not enough)."
            puts $fd "        catch {save_bd_design \[get_bd_designs -quiet ${bd_name}\]}"
            puts $fd "    } else {"
            puts $fd "        puts \"  WARNING: BD tcl not exported, skipping: ${bd_name}\""
            puts $fd "    }"
            puts $fd "}"
            puts $fd ""
        }
    } else {
        # Fallback: glob (no dependency info available)
        puts $fd "set bd_tcls {}"
        puts $fd "catch {set bd_tcls \[glob \$base_dir/Sources/BD/*.tcl\]}"
        puts $fd "foreach bd_tcl \$bd_tcls {"
        puts $fd "    set bd_basename \[file rootname \[file tail \$bd_tcl\]\]"
        puts $fd "    set bd_is_rm_owned 0"
        puts $fd "    if {\[info exists _rm_owned_bd_names\]} {"
        puts $fd "        set bd_is_rm_owned \[expr {\[lsearch \$_rm_owned_bd_names \$bd_basename\] >= 0}\]"
        puts $fd "    }"
        puts $fd "    if {\$bd_is_rm_owned} {"
        puts $fd "        puts \"  Skipping BD (RM-owned): \$bd_basename\""
        puts $fd "    } else {"
        puts $fd "        source \$bd_tcl"
        puts $fd "        if {\[catch {validate_bd_design -force -design \$bd_basename} _vbd_err\]} {"
        puts $fd "            puts \"  WARNING: validate_bd_design reported an error for \$bd_basename: \$_vbd_err\""
        puts $fd "            puts \"  This is usually caused by locked/out-of-date IPs in the BD.\""
        puts $fd "            puts \"  On the ORIGINAL project: open_bd_design \${bd_basename}.bd; report_ip_status\""
        puts $fd "            puts \"  then upgrade_ip the flagged IPs and re-run this export.\""
        puts $fd "            puts \"  NOTE: later commands (e.g. make_wrapper) may fail with 'failed due to\""
        puts $fd "            puts \"  earlier errors' even though THIS is the real root cause.\""
        puts $fd "        }"
        puts $fd "        catch {save_bd_design \[get_bd_designs -quiet \$bd_basename\]}"
        puts $fd "    }"
        puts $fd "}"
    }
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 8: Create BD Wrapper 
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 8: Recreate Block Design wrappers (only if wrapper was present in project)"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    # Iterate over array values safely using array names (skip _loc metadata keys)
    if {[array exists bd_name_array] && [array size bd_name_array] > 0} {
        foreach idx [lsort [array names bd_name_array]] {
            # Skip metadata keys (e.g., "1_loc")
            if {[string match "*_loc" $idx]} { continue }
            set bd_wrapper_name $bd_name_array($idx)
            # Strip _wrapper suffix to get the BD name
            set bd_base_name [string range $bd_wrapper_name 0 end-[string length "_wrapper"]]
            puts $fd "# Create and add wrapper for BD: $bd_base_name"
            puts $fd "set bd_obj \[get_files -quiet ${bd_base_name}.bd\]"
            puts $fd "if {\[llength \$bd_obj\] > 0} {"
            puts $fd "    if {\[catch {set wrapper_path \[make_wrapper -files \$bd_obj -top\]} _mw_err\]} {"
            puts $fd "        puts \"  ERROR: make_wrapper failed for $bd_base_name: \$_mw_err\""
            puts $fd "        puts \"  This almost always means an EARLIER step already logged an error\""
            puts $fd "        puts \"  for this BD (search this log above for WARNING/ERROR mentioning\""
            puts $fd "        puts \"  $bd_base_name -- commonly locked/out-of-date IPs from Step 7).\""
            puts $fd "        puts \"  Fix on the ORIGINAL project (report_ip_status / upgrade_ip), then\""
            puts $fd "        puts \"  re-run the export/build pipeline.\""
            puts $fd "    } else {"
            puts $fd "        add_files -norecurse \$wrapper_path"
            puts $fd "    }"
            puts $fd "}"
            puts $fd ""
        }
    } else {
        puts $fd "# No BD wrappers were present in the original project - skipping"
    }
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 9: Generate BD Targets and Create BD IP Runs
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 9: Generate BD Targets and Create BD IP OOC Runs"
    puts $fd "# NOTE: Only operate on top-level user BDs from sources_1 fileset."
    puts $fd "#       Nested sub-designs (e.g., inside Versal CIPS) are auto-generated by"
    puts $fd "#       their parent IP and MUST NOT be generated independently."
    puts $fd "#       Using FILE_TYPE filter + .srcs path check to exclude them."
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "set bd_files {}"
    puts $fd "catch {set bd_files \[get_files -filter {FILE_TYPE == \"Block Designs\"} -of_objects \[get_filesets sources_1\]\]}"
    puts $fd "foreach bd \$bd_files {"
    puts $fd "    set bd_norm \[file normalize \$bd\]"
    puts $fd "    # Skip nested sub-designs in .gen/ (auto-generated by parent IPs like Versal CIPS)"
    puts $fd "    if {\[string match \"*/.gen/*\" \$bd_norm\]} {"
    puts $fd "        puts \"  Skipping nested sub-design: \[file tail \$bd\]\""
    puts $fd "        continue"
    puts $fd "    }"
    puts $fd "    # Skip generated BDs"
    puts $fd "    if {\[get_property IS_GENERATED \$bd\] eq \"1\"} {"
    puts $fd "        puts \"  Skipping generated BD: \[file tail \$bd\]\""
    puts $fd "        continue"
    puts $fd "    }"
    puts $fd "    puts \"  Generating targets for: \[file tail \$bd\]\""
    puts $fd "    if {\[catch {generate_target all \$bd} _gt_err\]} {"
    puts $fd "        puts \"  ERROR: generate_target failed for \[file tail \$bd\]: \$_gt_err\""
    puts $fd "        puts \"  This is commonly a Vivado limitation regenerating debug-net\""
    puts $fd "        puts \"  (mark_debug/ILA) connections routed through a Block Design\""
    puts $fd "        puts \"  Container on a freshly recreated design (see 'Could not find\""
    puts $fd "        puts \"  the cell' errors above) rather than an issue with this export.\""
    puts $fd "        puts \"  Workaround: remove debug cores from the BD before export, or\""
    puts $fd "        puts \"  manually regenerate targets for \[file tail \$bd\] in the GUI.\""
    puts $fd "    }"
    puts $fd "}"
    puts $fd ""
    puts $fd "# Create OOC runs for IPs within top-level Block Designs only"
    puts $fd "foreach bd \$bd_files {"
    puts $fd "    set bd_norm \[file normalize \$bd\]"
    puts $fd "    if {\[string match \"*/.gen/*\" \$bd_norm\]} { continue }"
    puts $fd "    if {\[get_property IS_GENERATED \$bd\] eq \"1\"} { continue }"
    puts $fd "    catch {create_ip_run \[get_ips -of_objects \[get_files \$bd\]\]}"
    puts $fd "}"
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 10: Add Hook Scripts to utils_1 fileset (BEFORE project settings,
    #          so TCL.PRE/POST properties reference files already in the project)
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 10: Add Hook Scripts to utils_1 fileset"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "set hook_scripts {}"
    puts $fd "catch {set hook_scripts \[glob \$base_dir/Sources/Scripts/*.tcl\]}"
    puts $fd "if {\[llength \$hook_scripts\] > 0} {"
    puts $fd "    import_files -fileset utils_1 \$hook_scripts"
    puts $fd "}"
    puts $fd ""
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 11: Apply Captured Project Settings (BEFORE finalize — settings
    #          like TOP, PR_FLOW affect compile order resolution)
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 11: Apply Captured Project Settings"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "source \$script_dir/project_settings.tcl"
    puts $fd "update_compile_order -fileset sources_1"
    puts $fd ""

    #────────────────────────────────────────────────────────────────────────────
    # Step 11b: Restore fileset ordering from captured project snapshot
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 11b: Restore fileset source ordering (if captured)"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "proc _rc_restore_fileset_order {fileset_name ordered_tails} {"
    puts $fd "    if {\[llength \[get_filesets -quiet \$fileset_name\]\] == 0} { return }"
    puts $fd "    set last_file \"\""
    puts $fd "    foreach tail \$ordered_tails {"
    puts $fd "        set match {}"
    puts $fd "        catch {set match \[get_files -quiet -of_objects \[get_filesets \$fileset_name\] -filter \"NAME =~ *\${tail}\"\]}"
    puts $fd "        if {\[llength \$match\] == 0} { continue }"
    puts $fd "        set f \[lindex \$match 0\]"
    puts $fd "        if {\$last_file eq \"\"} {"
    puts $fd "            catch {reorder_files -fileset \$fileset_name -front \$f}"
    puts $fd "        } else {"
    puts $fd "            catch {reorder_files -fileset \$fileset_name -after \$last_file \$f}"
    puts $fd "        }"
    puts $fd "        set last_file \$f"
    puts $fd "    }"
    puts $fd "    catch {update_compile_order -fileset \$fileset_name}"
    puts $fd "}"
    puts $fd ""
    if {[info exists fileset_compile_order_map]} {
        foreach fs_name [lsort [array names fileset_compile_order_map]] {
            set ordered_tails $fileset_compile_order_map($fs_name)
            puts $fd "_rc_restore_fileset_order $fs_name [list $ordered_tails]"
        }
        puts $fd ""
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 12: DFX Hierarchy + OOC Synth + PR Configurations
    #   12a) source project_settings_dfx.tcl  (defines the two procs)
    #   12b) dfx_define_partition             (PD, RMs, HD.RECONFIGURABLE)
    #   12c) launch_runs synth_1 + each <rm>_synth_1 with wait_on_run
    #   12d) dfx_define_configurations        (PR configurations, child impl runs)
    # Order matters: PR configurations require synthesized RMs to be valid.
    #────────────────────────────────────────────────────────────────────────────
    global dfx_pr_configs
    global dfx_child_runs
    global dfx_parent_impl_config
    global dfx_partition_defs
    global dfx_reconfig_modules
    
    set has_dfx_emit [expr {([info exists dfx_partition_defs] && [llength $dfx_partition_defs] > 0) || \
                            ([info exists dfx_pr_configs]     && [llength $dfx_pr_configs] > 0)}]
    
    if {$has_dfx_emit} {
        puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
        puts $fd "# Step 12: DFX hierarchy + OOC synthesis + PR configurations"
        puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
        puts $fd "if {\[file exists \$script_dir/project_settings_dfx.tcl\]} {"
        puts $fd "    source \$script_dir/project_settings_dfx.tcl"
        puts $fd ""
        puts $fd "    puts \"  (Step 12b) Defining DFX partitions and reconfigurable modules...\""
        puts $fd "    dfx_define_partition"
        puts $fd "    update_compile_order -fileset sources_1"
        
        # Per-RM compile order updates
        if {[info exists dfx_reconfig_modules]} {
            foreach rm_dict $dfx_reconfig_modules {
                set rm_name [dict get $rm_dict name]
                puts $fd "    catch {update_compile_order -fileset $rm_name}"
            }
        }
        
        puts $fd ""
        puts $fd "    puts \"  (Step 12c) Launching OOC synthesis runs (static + RMs)...\""
        puts $fd "    # Static synth first"
        puts $fd "    if {\[get_property STATUS \[get_runs synth_1\]\] ne \"synth_design Complete!\"} {"
        puts $fd "        launch_runs synth_1 -jobs 4"
        puts $fd "        wait_on_run synth_1"
        puts $fd "    }"
        puts $fd "    # Then each RM OOC synth run"
        if {[info exists dfx_reconfig_modules]} {
            foreach rm_dict $dfx_reconfig_modules {
                set rm_name [dict get $rm_dict name]
                set rm_synth "${rm_name}_synth_1"
                puts $fd "    if {\[llength \[get_runs -quiet $rm_synth\]\] > 0} {"
                puts $fd "        if {\[get_property STATUS \[get_runs $rm_synth\]\] ne \"synth_design Complete!\"} {"
                puts $fd "            launch_runs $rm_synth -jobs 4"
                puts $fd "            wait_on_run $rm_synth"
                puts $fd "        }"
                puts $fd "    }"
            }
        }
        puts $fd ""
        puts $fd "    puts \"  (Step 12d) Defining PR configurations and child implementation runs...\""
        puts $fd "    dfx_define_configurations"
        puts $fd "} else {"
        puts $fd "    puts \"  WARNING: project_settings_dfx.tcl not found — skipping DFX hierarchy setup\""
        puts $fd "}"
        puts $fd ""
    }
    
    #────────────────────────────────────────────────────────────────────────────
    # Step 13: Finalize Project
    #────────────────────────────────────────────────────────────────────────────
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "# Step 13: Finalize Project"
    puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
    puts $fd "update_compile_order -fileset sources_1"
    puts $fd "update_compile_order -fileset sim_1"
    puts $fd ""

    #────────────────────────────────────────────────────────────────────────────
    # Append DFX rebuild validation step: fail if DFX was expected but not found
    #────────────────────────────────────────────────────────────────────────────
    global dfx_partition_defs
    if {[info exists dfx_partition_defs] && [llength $dfx_partition_defs] > 0} {
        puts $fd ""
        puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
        puts $fd "# Post-Build DFX Validation"
        puts $fd "#─────────────────────────────────────────────────────────────────────────────────"
        puts $fd "if {\[llength \[get_partition_defs -quiet\]\] == 0} {"
        puts $fd "    puts \"ERROR: DFX export expected partition_defs, but rebuild created none.\""
        puts $fd "    puts \"  This indicates missing project_settings_dfx.tcl or RM files.\""
        puts $fd "    error \"DFX rebuild validation failed\""
        puts $fd "}"
    }

    puts $fd "puts \"──────────────────────────────────────────────────────────────────────────\""
    puts $fd "puts \"Project recreated successfully!\""
    puts $fd "puts \"──────────────────────────────────────────────────────────────────────────\""
    
    close $fd

    set gitignore_file [file join $output_dir .gitignore]
    set gid [open $gitignore_file w]
    puts $gid ".Xil/"
    puts $gid "*.jou"
    puts $gid "*.log"
    puts $gid "vivado*.backup.*"
    puts $gid "${proj_name}.cache/"
    puts $gid "${proj_name}.gen/"
    puts $gid "${proj_name}.hw/"
    puts $gid "${proj_name}.ip_user_files/"
    puts $gid "${proj_name}.runs/"
    puts $gid "${proj_name}.sim/"
    puts $gid "${proj_name}.srcs/"
    close $gid

    set readme_file [file join $output_dir README.md]
    set rid [open $readme_file w]
    puts $rid "# Vivado Revision Control Export"
    puts $rid ""
    puts $rid "This directory is organized for git check-in."
    puts $rid ""
    puts $rid "## Check In"
    puts $rid "- ${proj_name}.xpr"
    puts $rid "- Sources/"
    puts $rid "- Scripts/"
    puts $rid "- .gitignore"
    puts $rid "- README.md"
    puts $rid "- Pblocks/ if present"
    puts $rid "- Checkpoints/ if present and intentionally versioned"
    puts $rid "- NoC/ if present"
    puts $rid ""
    puts $rid "## Do Not Check In"
    puts $rid "- .Xil/"
    puts $rid "- *.jou, *.log, vivado*.backup.*"
    puts $rid "- ${proj_name}.cache/"
    puts $rid "- ${proj_name}.gen/"
    puts $rid "- ${proj_name}.hw/"
    puts $rid "- ${proj_name}.ip_user_files/"
    puts $rid "- ${proj_name}.runs/"
    puts $rid "- ${proj_name}.sim/"
    puts $rid "- ${proj_name}.srcs/"
    puts $rid ""
    puts $rid "## Recreate"
    puts $rid "Run Vivado in batch mode with Scripts/build.tcl to recreate the project from the checked-in sources."
    close $rid
    
    puts "Build script generated: $output_file"
    puts "Git ignore generated: $gitignore_file"
    puts "README generated: $readme_file"
    return $output_file
}

#══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL UTILITY PROCEDURES
#══════════════════════════════════════════════════════════════════════════════

#──────────────────────────────────────────────────────────────────────────────
# Procedure: pr_verify - Verify DFX configuration integrity (used with DFX skill)
#──────────────────────────────────────────────────────────────────────────────
proc pr_verify {} {
    set proj [current_project]
    
    set pr_flow [get_property PR_FLOW $proj]
    if {$pr_flow ne "true" && $pr_flow ne "1"} {
        error "PR_FLOW not enabled on project"
    }
    
    # Check for RMs
    set rms [get_cells -quiet -filter {HD.RECONFIGURABLE == 1}]
    if {[llength $rms] == 0} {
        error "No reconfigurable modules (RMs) found"
    }
    
    # Check for Pblocks
    set pblocks [get_pblocks -quiet]
    if {[llength $pblocks] == 0} {
        error "No Pblocks defined for PR regions"
    }
    
    puts "DFX configuration verified - RMs: [llength $rms], Pblocks: [llength $pblocks]"
    return 1
}

#══════════════════════════════════════════════════════════════════════════════
# PROCEDURE 6 (optional): capture_verification_manifest
# Purpose: Snapshot the ORIGINAL project's expected structure (BD names, top
#          module, run names, IP count) so a later verify_rebuild call can
#          diff the recreated project against it. Run this in the ORIGINAL
#          project session, after export_all_sources (so Sources/BD/*.tcl
#          already reflects which BDs were actually exported).
#══════════════════════════════════════════════════════════════════════════════
proc capture_verification_manifest {output_file export_dir} {
    set proj [current_project]

    # Expected BD names = whatever export_all_sources actually wrote out
    # (single source of truth: the exported Sources/BD/*.tcl files).
    set bd_names {}
    catch {
        foreach f [glob -nocomplain "$export_dir/Sources/BD/*.tcl"] {
            lappend bd_names [file rootname [file tail $f]]
        }
    }

    # Top module + auto-managed-wrapper detection (the bug class fixed in
    # Round 4: top references "<bd>_wrapper" but no physical wrapper file
    # was ever generated/tracked).
    set top ""
    set top_is_wrapper 0
    set top_wrapper_was_auto_managed 0
    catch {
        set top [get_property TOP [get_filesets sources_1]]
    }
    if {[string length $top] > 0 && [string match "*_wrapper" $top]} {
        set top_is_wrapper 1
        set wrapper_files [get_files -quiet *${top}*]
        if {[llength $wrapper_files] == 0} {
            set top_wrapper_was_auto_managed 1
        }
    }

    # Expected run names: only the deterministic, guaranteed-by-build.tcl ones —
    # `synth_1`/`impl_1` (every project has these) plus each top-level BD's own
    # `<bd>_synth_1` if it exists as a distinct run (RM/BDC OOC synth runs).
    # Deliberately EXCLUDES leaf IP OOC runs and DFX per-configuration child
    # runs (e.g. "child_0_impl_1") — their names/numbering reflect the original
    # project's build history and are not a reliable rebuild signal; a missing
    # leaf IP or BD is already caught by the BD-presence and IP-count checks.
    set run_names {}
    catch {
        if {[llength [get_runs -quiet synth_1]] > 0} { lappend run_names synth_1 }
        if {[llength [get_runs -quiet impl_1]] > 0} { lappend run_names impl_1 }
        foreach bd $bd_names {
            set r "${bd}_synth_1"
            if {[llength [get_runs -quiet $r]] > 0} { lappend run_names $r }
        }
    }

    # IP count (sanity bound, not an exact-match requirement — legitimate
    # IP count drift can happen between Vivado versions)
    set ip_count 0
    catch {set ip_count [llength [get_ips -quiet]]}

    set fd [open $output_file w]
    puts $fd "################################################################################"
    puts $fd "# verification_manifest.tcl - Captured from original project for rebuild checks"
    puts $fd "# Generated: [clock format [clock seconds] -format {%a %b %d %H:%M:%S %Y}]"
    puts $fd "# Project: [get_property NAME $proj]"
    puts $fd "################################################################################"
    puts $fd "set _vm_proj_name  {[get_property NAME $proj]}"
    puts $fd "set _vm_top        {$top}"
    puts $fd "set _vm_top_is_wrapper $top_is_wrapper"
    puts $fd "set _vm_top_wrapper_was_auto_managed $top_wrapper_was_auto_managed"
    puts $fd "set _vm_bd_names   [list $bd_names]"
    puts $fd "set _vm_run_names  [list $run_names]"
    puts $fd "set _vm_ip_count   $ip_count"
    close $fd

    puts "Verification manifest captured: $output_file"
    puts "  Expected BDs: [llength $bd_names]  Expected runs: [llength $run_names]  Expected IPs: $ip_count"
    if {$top_wrapper_was_auto_managed} {
        puts "  NOTE: top ($top) is an auto-managed wrapper with no physical file —"
        puts "        verify_rebuild will check that the rebuild actually created it."
    }
    return $output_file
}

#══════════════════════════════════════════════════════════════════════════════
# PROCEDURE 7 (optional): verify_rebuild
# Purpose: Compare a RECREATED project (after sourcing build.tcl) against the
#          manifest captured from the original project. Run this in the
#          recreated project's session. Prints a PASS/FAIL report per check
#          category and returns 1 if everything passed, 0 otherwise.
#══════════════════════════════════════════════════════════════════════════════
proc verify_rebuild {manifest_file} {
    if {![file exists $manifest_file]} {
        error "Manifest file not found: $manifest_file"
    }

    # Sourced into this proc's local scope — sets _vm_* vars above.
    source $manifest_file

    set all_ok 1
    puts "─────────────────────────────────────────────────────────────────────"
    puts "REBUILD VERIFICATION: $_vm_proj_name"
    puts "─────────────────────────────────────────────────────────────────────"

    # 1) Block Designs present
    set missing_bds {}
    foreach bd $_vm_bd_names {
        set found [get_files -quiet "*${bd}.bd"]
        if {[llength $found] == 0} {
            lappend missing_bds $bd
        }
    }
    if {[llength $missing_bds] == 0} {
        puts "  \[PASS\] Block Designs: all [llength $_vm_bd_names] present"
    } else {
        puts "  \[FAIL\] Block Designs: missing $missing_bds"
        set all_ok 0
    }

    # 2) Top module resolves (catches the auto-managed-wrapper regression)
    set cur_top ""
    catch {set cur_top [get_property TOP [get_filesets sources_1]]}
    if {$cur_top ne $_vm_top} {
        puts "  \[FAIL\] Top module: expected '$_vm_top', got '$cur_top'"
        set all_ok 0
    } elseif {$_vm_top_is_wrapper} {
        set wrapper_files [get_files -quiet *${_vm_top}*]
        if {[llength $wrapper_files] == 0} {
            puts "  \[FAIL\] Top module '$_vm_top' is a wrapper but no matching file exists in the rebuild"
            set all_ok 0
        } else {
            puts "  \[PASS\] Top module: '$_vm_top' resolves to [llength $wrapper_files] file(s)"
        }
    } else {
        puts "  \[PASS\] Top module: '$cur_top'"
    }

    # 3) Expected runs present
    set missing_runs {}
    foreach r $_vm_run_names {
        if {[llength [get_runs -quiet $r]] == 0} {
            lappend missing_runs $r
        }
    }
    if {[llength $missing_runs] == 0} {
        puts "  \[PASS\] Runs: all [llength $_vm_run_names] present"
    } else {
        puts "  \[FAIL\] Runs: missing $missing_runs"
        set all_ok 0
    }

    # 4) IP count sanity (warn only — some drift across Vivado versions is
    #    expected; a shortfall usually means a BD failed to regenerate)
    set cur_ip_count 0
    catch {set cur_ip_count [llength [get_ips -quiet]]}
    if {$cur_ip_count < $_vm_ip_count} {
        puts "  \[WARN\] IP count: expected >= $_vm_ip_count, got $cur_ip_count (possible missing IP/BD)"
    } else {
        puts "  \[PASS\] IP count: $cur_ip_count (expected >= $_vm_ip_count)"
    }

    puts "─────────────────────────────────────────────────────────────────────"
    if {$all_ok} {
        puts "RESULT: PASS — recreated project matches expected structure"
    } else {
        puts "RESULT: FAIL — see above for details"
    }
    puts "─────────────────────────────────────────────────────────────────────"

    return $all_ok
}

#══════════════════════════════════════════════════════════════════════════════
# END OF HELPER SCRIPTS LIBRARY
#══════════════════════════════════════════════════════════════════════════════
