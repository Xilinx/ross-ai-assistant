# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# ============================================================
# ipiasm.tcl - assembly layer for the vivado-ipi-assembler skill
#
# Sits ON TOP of vivado-ip-configurator's ipcfg.tcl. Source order:
#     source <vivado-ip-configurator>/lib/ipcfg/ipcfg.tcl
#     source <vivado-ipi-assembler>/lib/ipiasm.tcl
#     ipiasm::init            ;# puts ipcfg into persistent "assemble" mode
#
# ipcfg::* still owns per-CELL work (create + configure + verify + discover +
# cache). ipiasm::* adds everything BETWEEN cells: connections, clock/reset
# domains, address map, multi-instance orchestration, and a validate + assembly
# coverage report. Every proc returns a single structured line the agent parses,
# mirroring the ipcfg convention:
#     SUCCESS:<detail> | SKIP:<detail> | <THING>_FAIL:<detail> | REPORT:<...>
# Procs are IP-agnostic: cell names, pins, VLNVs, rules are passed in by the
# caller (discovered from the manifest + doc search + the rules dump).
#
# AUTOMATION-FIRST CONTRACT (matches vivado-ip-configurator):
#   For EVERY between-cell operation, try apply_bd_automation FIRST, fall back to
#   manual wiring only when no Designer-Assistance rule applies.
#     * clocks/resets  -> clkrst rule auto-builds proc_sys_reset + wires the
#                         wizard  (ipiasm::wire_clk_rst_auto). Manual
#                         build_clocks/fanout_domain are the FALLBACK only.
#     * AXI master->slave -> axi4 rule auto-inserts smartconnect + assigns the
#                         address (ipiasm::connect_axi_auto). Manual connect_intf
#                         + auto_address are the FALLBACK only.
#     * board intf pins -> board rule. ipiasm::connect_automation is the generic
#                         entry the auto wrappers call.
#   Rule IDs come from `::bd::util_cmd rules dump` + ipcfg::rule_for_vlnv, exactly
#   like the per-cell subsystem config (ipcfg::run_automation).
# ============================================================

namespace eval ipiasm {}
set ::ipiasm::source_candidate [apply {{path} {
    set channel [open $path r]
    try {set content [read $channel]} finally {close $channel}
    set companion_crc {}
    foreach name {feasibility.tcl clocks.tcl} {
        set companion [file join [file dirname $path] $name]
        set checksum missing
        if {[file isfile $companion]} {
            set channel [open $companion r]
            try {set checksum [zlib crc32 [read $channel]]} finally {close $channel}
        }
        lappend companion_crc $name $checksum
    }
    return [list [file normalize $path] [zlib crc32 $content] $companion_crc]
}} [info script]]
if {[info exists ::ipiasm::loaded_signature]} {
    if {$::ipiasm::loaded_signature ne $::ipiasm::source_candidate} {
        unset ::ipiasm::source_candidate
        error "LIBRARY_FAIL:ipiasm changed; do not hot-reload a live design"
    }
    unset ::ipiasm::source_candidate
    return "SKIP:ipiasm already loaded"
}

namespace eval ipiasm {
    variable library_dir [file dirname [file normalize [info script]]]
    variable clk_wizard ""              ;# remembered clock-wizard cell (set by build_clocks)
    variable last_validate_console ""   ;# last validate_bd_design console text (for DRC scan)
    variable last_reset_sync_findings {} ;# critical reset findings from latest validation
    variable gates [dict create plan 0 board 0 inventory 0 qos 0]   ;# enforced build-order gates
    variable qos_sub [dict create qos 0 noc 0]              ;# the two halves of the qos gate
    variable policy attended                                ;# attended|unattended (see ipiasm::policy)
    variable stage_t [dict create]                          ;# current stage name + start ms
    variable stage_log [dict create]                        ;# stage -> seconds (ipiasm::timings)
    variable initialized 0                                  ;# init enables build-order enforcement
    variable design_context {}
    # Set when ipiasm::validate passes. Registered clock contracts read values Vivado sets
    # only on validation, so the stage-boundary re-checks of them wait for it.
    variable design_validated 0
    # Pins the design leaves open on purpose (ipiasm::declare_open): pin -> reason.
    # assembly_report lists them as declared rather than as wiring gaps.
    variable declared_open [dict create]
    # Design fingerprint as of the last validate_bd_design -force, so a second
    # forced validate on an unchanged design can be skipped.
    variable forced_fp ""
    # State of the design at the last passing ipiasm::validate (see there).
    variable validated_state {}
    variable validate_reuse_count 0
    # Full structural/configuration fingerprint captured only by
    # final_contract_gate. delivery_guard refuses exports after any mutation.
    variable delivery_fp ""
    # The normalized path + SHA-256 of the last plan that reached
    # ASSEMBLY_PLAN:PASS. final_contract_gate compares this with the plan still
    # on disk, so creating or editing an unexecuted binder artifact cannot pass.
    variable executed_plan [dict create]
    variable stage_completion {}
    variable stage_result {}
    variable plan_request {}
    variable compiled_plans {}
    # Metadata-aware interface contracts registered by the bound assembly plan.
    variable interface_contracts {}
    # Interrupt sink -> source table registered by set_irq_contracts.
    variable irq_contracts {}
    # {label {categories result fingerprint} ...} from ipiasm::record_evidence.
    variable recorded_evidence {}
}

# Keep direct library consumers working while preserving the documented source
# order. The sibling is sourced only when the shared runtime is not loaded yet.
if {[llength [info procs ::ipcfg::python_exec]] == 0} {
    set ipcfg_library [file normalize \
        [file join $::ipiasm::library_dir .. .. vivado-ip-configurator lib ipcfg ipcfg.tcl]]
    if {[file isfile $ipcfg_library]} {
        source $ipcfg_library
    }
    unset -nocomplain ipcfg_library
}

# --- bring up: require ipcfg, switch to persistent assemble mode ---
proc ipiasm::init {} {
    variable initialized
    variable design_context
    variable delivery_fp
    variable executed_plan
    variable interface_contracts
    if {[llength [info procs ::ipcfg::set_mode]] == 0} {
        return "ASM_FAIL:ipcfg not loaded (source vivado-ip-configurator/lib/ipcfg/ipcfg.tcl first)"
    }
    set observed [ipiasm::context_identity]
    if {$initialized && $design_context ne {}} {
        if {$observed ne $design_context} {
            return "ASM_FAIL:design context changed; call ipiasm::begin_design explicitly"
        }
        return "SUCCESS:ipiasm resumed [ipiasm::gate_status]"
    }
    set design_context $observed
    ipcfg::set_mode assemble
    ipiasm::gate_reset
    set delivery_fp ""
    set executed_plan [dict create]
    set ::ipiasm::stage_completion {}
    set ::ipiasm::stage_result {}
    set interface_contracts {}
    set ::ipiasm::clock_contracts {}
    set ::ipiasm::verified_clocks {}
    set ::ipiasm::irq_contracts {}
    set ::ipiasm::recorded_evidence {}
    set initialized 1
    ipiasm::_install_native_guards
    ipcfg::ledger_load
    ipiasm::_ledger_restore
    ipiasm::stage init
    return "SUCCESS:ipiasm ready (ipcfg mode=[ipcfg::get_mode]) [ipiasm::gate_status] policy=[ipiasm::policy]"
}

# The open project directory, project name and BD name as a list; empty if none is open.
proc ipiasm::context_identity {} {
    if {[catch {
        set project [current_project]
        set directory [get_property DIRECTORY $project]
        set design [current_bd_design]
    }] || $project eq "" || $directory eq "" || $design eq ""} {return {}}
    return [encoding convertfrom utf-8 [encoding convertto utf-8 \
        [list [file normalize $directory] $project $design]]]
}

# Restore registered contracts and evidence from the design ledger (ipcfg::ledger_entry) into a
# process that holds none of them: a stage resumed in a new Vivado process keeps what earlier
# stages registered. Recorded evidence keeps its fingerprint, so a changed design marks it stale.
proc ipiasm::_ledger_restore {} {
    set entry [ipcfg::ledger_entry]
    foreach name {clock_contracts irq_contracts interface_contracts recorded_evidence} {
        if {[set ::ipiasm::$name] eq {} && [dict exists $entry $name]} {
            set ::ipiasm::$name [dict get $entry $name]
        }
    }
}

# Start a fresh assembly context for the currently open project/BD, clearing all in-memory state from another design.
proc ipiasm::begin_design {} {
    variable design_context
    set observed [ipiasm::context_identity]
    # Agents read this as "create the BD first", and the plan's own create_bd_design then failed
    # on the name they took.
    if {$observed eq {}} {
        return "ASM_FAIL:begin_design requires an open project and BD; a plan whose board stage has\
            create_bd_design needs neither: open the project only and run ipiasm::assemble_from_plan"
    }
    if {$observed eq $design_context} {return [ipiasm::init]}
    foreach {name value} {
        initialized 0 design_context {} stage_t {} stage_log {} delivery_fp {}
        executed_plan {} stage_completion {} stage_result {} plan_request {}
    } {set ::ipiasm::$name $value}
    ipiasm::_forget_design_state
    return [ipiasm::init]
}

# Clear what this process learned about a block design: contracts, evidence, the validated flag
# and ipcfg's pinned requirements. Plan execution bookkeeping is left alone, so _execute_plan
# calls this right after its own create_bd_design without losing its place.
proc ipiasm::_forget_design_state {} {
    foreach {name value} {
        clk_wizard {} last_validate_console {} last_reset_sync_findings {} forced_fp {}
        interface_contracts {} clock_contracts {} irq_contracts {} recorded_evidence {}
        verified_clocks {} design_validated 0 declared_open {}
    } {set ::ipiasm::$name $value}
    unset -nocomplain ::ipiasm::unused_qos_routes
    foreach name {req board_owned board_override bd_file ledger_loaded} {set ::ipcfg::$name {}}
}

# Dict of every public ipcfg::/ipiasm:: proc matching a glob to its argument names and defaults.
proc ipiasm::api_inventory {{pattern *}} {
    set inventory {}
    foreach space {ipcfg ipiasm} {
        foreach procedure [lsort [info procs ::${space}::*]] {
            set name [string trimleft $procedure :]
            if {[string match _* [namespace tail $procedure]] ||
                ![string match $pattern $name]} {continue}
            set arguments {}
            foreach argument [info args $procedure] {
                set entry [dict create name $argument]
                if {[info default $procedure $argument default]} {
                    dict set entry default $default
                }
                lappend arguments $entry
            }
            dict set inventory $name $arguments
        }
    }
    return $inventory
}

# The build flow on one screen: each stage's gate token and the helpers its
# SKILL.md section uses, in order. With a pattern, the matching ipiasm::
# helpers' call shapes and results from ipcfg::help, generated from the code.
proc ipiasm::api {{pat ""}} {
    set steps {STEPS (one notebook stage at a time; ipiasm::stage <name> first, ipiasm::checkpoint <tag> last)
 0  spec       -> SPEC_OK       Stage 2 only: preflight_connect sync_intf_freq_hz reconcile_spec,
                                then assemble_from_plan <abs-plan> <stage> for every later stage
 0a plan       -> PLAN_OK       scratch: binding probe, ipcfg::create_cell_cfg experiments,
                                ipcfg::export_proven; compile the plan to ASSEMBLY_PLAN:OK; close scratch;
                                then assemble_from_plan <abs-plan> opens the 'plan' gate (create_cell
                                and the build stages refuse until it is open)
 0b board      -> BOARD_OK      register_board_repo discover_board_parts require_board_preset
                                board_interfaces; create the processing system, apply its
                                board preset, ipcfg::preset_drift; attach_board_memory
 1  cells      -> CELLS_OK      create_cell region_selectors ensure_semantic_cell_name
                                check_board_hooks check_memory_regions check_cell_config
 1b inventory  -> INVENTORY_OK  gate_inventory check_ps_fabric_irqs set_irq_contracts
                                check_irq_contracts
 2  clocks     -> CLOCKS_OK     preflight_clock_plan wire_clk_rst_auto set_clock_contracts
                                save_clock_contracts load_clock_contracts
                                ensure_reset_domain wire_mem_noc_sysclk check_mem_noc_sysclk
                                check_interface_clock_domains set_interface_contracts
                                preflight_interface_contracts check_mipi_video_clocks
                                check_undriven_clocks create_port check_required_ports
 3  connect                     externalize_interface create_port connect_external_axi connect_axi_auto
                                check_ps_noc_masters name_port
                                build_control_fanout add_noc_si attach_aie_config
                                check_aie_config check_required_reachability check_irq_paths
                                check_camera_isp_format attach_noc_port_clocks
                                check_undriven_clocks
 3b qos        -> QOS_OK        bw_video bw_mipi bw_axi qos_classes set_unused_qos_routes
                                apply_qos_plan set_qos check_noc_pre_address check_memory_subsystem
 4  address    -> ADDRESS_OK    place_named_windows auto_address restrict_master
                                enforce_window_scope check_noc_post_address check_overmap
                                check_noc_legality check_visibility check_required_dram_masters
 5  validate   -> VALIDATE_OK   normalize_sim_model gate_inventory close_gaps declare_open
                                check_wrapper_boundary check_required_ports validate_bundle,
                                then check_clock_contracts (needs a validated design)
                                final_contract_gate_from_plan (plan-built) or
                                final_contract_gate; assembly_report
 6  deliver    -> DELIVERED     delivery_guard assert_delivery_context
Each plan stage carries the checks its own design needs: an IP check only when the design
has that IP, a contract check only when the spec states that contract, and never in a
stage before the one that does the work it checks. The plan compiler enforces this when
given the spec.
Status at any point: gate_status, timings, stage_execution_status.
ipiasm::api <word> for call shapes and results; the rules stay in SKILL.md.}
    if {$pat eq ""} {return $steps}
    return [ipcfg::help "ipiasm::*$pat*"]
}

# --- compile and execute a resolved, whitelisted assembly plan ---------------
# The natural-language DesignSpec is not executable. Its technology-binding
# phase emits assembly_plan.json; this operation compiles only known structured
# operations to Tcl and stops at the first unexpected return.

# Check a list of {procedure argument-list} calls exist with legal arity before running any; returns a dict with status PASS/FAIL, checks and failures.
proc ipiasm::api_preflight {calls} {
    set failures {}
    set inventory [ipiasm::api_inventory]
    foreach call $calls {
        if {[llength $call] != 2} {
            lappend failures "expected {procedure argument-list}"
            continue
        }
        lassign $call name arguments
        set name [string trimleft $name :]
        if {![dict exists $inventory $name]} {
            lappend failures "missing public helper $name"
            continue
        }
        set formals [dict get $inventory $name]
        set minimum 0
        set maximum [llength $formals]
        set position 0
        foreach formal $formals {
            incr position
            if {$position == $maximum && [dict get $formal name] eq "args"} {
                set maximum -1
            } elseif {![dict exists $formal default]} {
                set minimum $position
            }
        }
        set count [llength $arguments]
        if {$count < $minimum || ($maximum >= 0 && $count > $maximum)} {
            lappend failures "$name got $count arguments; expected minimum=$minimum maximum=$maximum"
        }
    }
    return [dict create status [expr {[llength $failures] ? "FAIL" : "PASS"}] \
        checks [llength $calls] failures $failures]
}

# Result dict of the last assemble_from_plan stage: status, operations, receipts and timings.
proc ipiasm::stage_execution_status {} {
    variable stage_result
    if {$stage_result eq {}} {
        set stage_result [ipiasm::_new_stage_result {} {}]
        dict set stage_result status PENDING
    }
    return $stage_result
}

proc ipiasm::_plan_context {{strict 1}} {
    set project [current_project]
    set directory [get_property DIRECTORY $project]
    if {$project eq "" || $directory eq ""} {error "PLAN_ENV_FAIL:no project"}
    set libraries {}
    foreach name {ipiasm ipcfg} {
        if {[info exists ::${name}::loaded_signature]} {
            lappend libraries [set ::${name}::loaded_signature]
        }
    }
    set release unavailable
    if {$strict || [llength [info commands version]]} {set release [version -short]}
    return [encoding convertfrom utf-8 [encoding convertto utf-8 [list [file normalize $directory] $project [ipiasm::context_identity] \
        $release [get_property PART $project] \
        [get_property BOARD_PART $project] $libraries]]]
}

proc ipiasm::_plan_result_status {raw prefixes} {
    return [ipcfg::result_status $raw $prefixes]
}

proc ipiasm::_new_stage_result {stage evidence} {
    return [dict create schema_version 1 status FAIL stage $stage operation {} \
        checks 0 skipped_operations 0 warnings {} mutation_delta unknown attempt_delta unknown elapsed_ms 0 \
        warning_count 0 critical_count 0 error_count 0 \
        evidence_path [expr {$evidence eq "" ? "" : "$evidence.result.tcldict"}] \
        compiled_path $evidence operations {} completed_stages {} resumed_after {} error_info {} error_code {}]
}

proc ipiasm::_resume_configuration {cell desired body} {
    variable stage_result
    if {[ipcfg::_config_satisfied $cell $desired]} {
        dict incr stage_result skipped_operations
        return "SUCCESS:verified configuration unchanged"
    }
    return [uplevel 1 $body]
}

proc ipiasm::_run_plan_operation {stage operation} {
    variable stage_result
    variable plan_request
    variable stage_completion
    set op_id [dict get $operation id]
    dict set stage_result operation $op_id
    set body [dict get $operation body]
    if {[dict exists $stage_completion operations $op_id] &&
        [dict get $stage_completion operations $op_id] eq $body &&
        [dict exists $operation call]} {
        lassign [dict get $operation call] procedure arguments
        if {$procedure eq "ipcfg::apply_dict"} {
            set body [list ipiasm::_resume_configuration [lindex $arguments 0] [lindex $arguments 1] $body]
        }
    }
    if {[dict exists $stage_completion operations $op_id]} {dict unset stage_completion operations $op_id}
    set started [clock milliseconds]
    set capture {}
    if {$plan_request eq {}} {
        set code [catch {eval $body} raw options]
    } else {
        set path "[dict get $stage_result compiled_path].op[dict size [dict get $stage_result operations]].tcldict"
        set setup_code [catch {
            set capture [ipcfg::capture $path $body [dict get $operation expect]]
            set channel [open $path r]
            try {
                fconfigure $channel -encoding utf-8
                set record [dict get [read $channel] data]
            } finally {close $channel}
        } setup_error setup_options]
        if {$setup_code} {
            set code $setup_code
            set raw $setup_error
            set options $setup_options
        } else {
            set code [dict get $record code]
            set raw [dict get $record raw]
            set options [dict get $record options]
            if {[dict exists $record capture_error]} {
                set code 1
                set raw [dict get $record capture_error]
                set options [dict get $record capture_options]
            }
        }
    }
    set status FAIL
    if {!$code} {set status [ipiasm::_plan_result_status $raw [dict get $operation expect]]}
    set warnings {}
    if {$capture ne {}} {
        if {[dict get $capture status] ne "OK"} {set status FAIL}
        if {[info exists record]} {set warnings [dict get $record messages]}
        foreach key {warning_count critical_count error_count} {
            dict incr stage_result $key [dict get $capture $key]
        }
    }
    foreach line [split $raw \n] {
        if {[regexp -nocase {WARNING} $line]} {lappend warnings $line}
    }
    dict set stage_result operations $op_id [dict create stage $stage operation $op_id \
        status $status raw $raw options $options warnings $warnings capture $capture \
        elapsed_ms [expr {[clock milliseconds] - $started}] mutation_delta unknown attempt_delta unknown]
    dict set stage_result warnings [concat [dict get $stage_result warnings] $warnings]
    if {$code} {
        if {$code != 1} {error "PLAN_FAIL:unexpected Tcl completion code $code: $raw"}
        return -options $options $raw
    }
    if {$status eq "FAIL"} {
        # Lead with the native messages that failed a passing result: the summary keeps
        # only the first 600 characters, and a run lost minutes to a PLAN_FAIL that read
        # BUNDLE:OK VALIDATE:PASS because the dropped CONFIG writes came after them.
        set why ""
        if {[info exists record] && [dict exists $record unsafe_lines]} {
            set why "[ipiasm::_native_failure_note [dict get $record unsafe_lines]] | result: "
        }
        error "PLAN_FAIL:$stage/$op_id:$why$raw [join $warnings {; }]"
    }
    if {$stage_completion ne {}} {dict set stage_completion operations $op_id [dict get $operation body]}
    dict incr stage_result checks
}

# Summary of the native messages that failed a plan operation: error and critical message ids
# first, with the repair for clock/reset ids, then the lines, most severe first. A run read
# three overridden-parameter warnings in the 300-character summary while the three critical
# asynchronous-reset warnings that failed the stage were cut off.
proc ipiasm::_native_failure_note {lines} {
    set severe {}
    set rest {}
    foreach line $lines {
        if {[regexp -nocase {^\s*(ERROR|CRITICAL[ _]WARNING)} $line]} {lappend severe $line} else {lappend rest $line}
    }
    set ids {}
    set async {}
    foreach line $severe {
        if {[regexp {\[([A-Za-z_]+ [0-9]+-[0-9]+)\]} $line -> id]} {dict incr ids $id}
        if {[regexp {Reset pin (\S+) \(associated clock \S+\) is connected to asynchronous reset source (\S+?)\.?$} \
                 [string trim $line] -> pin source]} {
            dict lappend async $source $pin
        }
    }
    set repairs [ipiasm::clock_reset_msg_ids]
    set named {}
    dict for {id count} $ids {
        set entry "$id x$count"
        if {$id in {{BD 41-1348} {BD 41-1343} {BD 41-1347}}} {
            set calls {}
            if {$id eq {BD 41-1348}} {
                dict for {source pins} $async {
                    lappend calls "ipiasm::ensure_reset_domain <reset-cell> <clock-pin> $source {$pins}"
                }
            }
            if {![llength $calls]} {
                set calls {"ipiasm::ensure_reset_domain <reset-cell> <clock-pin> <async-reset-pin> {<reset pins>}"}
            }
            append entry " ([dict get $repairs $id]; [join $calls {; }] in the clocks stage)"
        } elseif {[dict exists $repairs $id]} {
            append entry " ([dict get $repairs $id])"
        }
        lappend named $entry
    }
    set note "native messages fail it ([llength $lines], [llength $severe] error/critical)"
    if {[llength $named]} {append note ": [join $named {; }]"}
    return "$note: [join [lrange [concat $severe $rest] 0 2] {; }]"
}

# The gate a read-only plan operation opens, or "" for any other procedure.
proc ipiasm::_regate_gate {procedure} {
    set gates {ipiasm::require_board_preset board ipiasm::gate_inventory inventory
               ipiasm::check_noc_pre_address qos ipiasm::check_qos qos ipiasm::check_noc_solution qos}
    if {[dict exists $gates $procedure]} {return [dict get $gates $procedure]}
    return ""
}

proc ipiasm::_dispatch_plan {digest names stages environment_script} {
    variable plan_request
    set selected ""
    if {[dict exists $plan_request selected]} {set selected [dict get $plan_request selected]}
    if {$selected ne "" && $selected ni $names} {error "PLAN_FAIL:unknown stage $selected"}
    # Gate-opening operations of the stages before the selected one, for _execute_plan to
    # re-run if their gate is closed (a stage resumed in a new Vivado process).
    set regates {}
    if {$selected ne ""} {
        foreach entry $stages {
            if {[dict get $entry name] eq $selected} {break}
            foreach operation [dict get $entry operations] {
                if {![dict exists $operation call]} {continue}
                set gate [ipiasm::_regate_gate [lindex [dict get $operation call] 0]]
                if {$gate eq ""} {continue}
                lappend regates [dict create stage [dict get $entry name] gate $gate operation $operation]
            }
        }
    }
    set chosen {}
    foreach entry $stages {
        if {$selected ne "" && [dict get $entry name] ne $selected} {continue}
        set operations {}
        foreach operation [dict get $entry operations] {
            if {$selected ne "" && [dict exists $operation selected_body]} {
                dict set operation body [dict get $operation selected_body]
            }
            lappend operations $operation
        }
        dict set entry operations $operations
        lappend chosen $entry
    }
    return [ipiasm::_execute_plan $digest $names $selected $chosen $environment_script $regates]
}

proc ipiasm::_execute_plan {digest names selected stages environment_script {regates {}}} {
    variable stage_completion
    variable stage_result
    variable executed_plan
    variable design_context
    variable plan_request
    variable delivery_fp
    set started [clock milliseconds]
    set evidence ""
    if {$plan_request ne {}} {set evidence [dict get $plan_request evidence]}
    set stage_result [ipiasm::_new_stage_result $selected $evidence]
    dict set stage_result plan_hash $digest
    set executed_plan {}
    set delivery_fp ""
    set context_known 0
    set code [catch {
        set strict [expr {$selected ne "" || $plan_request ne {}}]
        set context [ipiasm::_plan_context $strict]
        eval $environment_script
        set context_known 1
        ipcfg::ledger_load
        ipiasm::_ledger_restore
        set request_identity [dict remove $plan_request evidence selected]
        dict set stage_result request_identity $request_identity
        dict set stage_result plan_names $names
        dict set stage_result context $context
        set key [list $digest $context $request_identity]
        # Rerun whole after a late stage failed, the same plan on the BD it built resumes after
        # the stages that completed: starting over failed on the BD's taken name and discarded
        # the stage record, and agents rebuilt the project. An emptied BD starts over.
        set skip {}
        if {$selected eq "" && $stage_completion ne {} && [dict get $stage_completion key] eq $key
            && ![catch {get_bd_cells -quiet} built] && [llength $built]} {
            set done [dict get $stage_completion completed]
            if {[llength $done] && [lrange $names 0 [expr {[llength $done] - 1}]] eq $done} {set skip $done}
        }
        dict set stage_result resumed_after $skip
        if {$stage_completion eq {} || [dict get $stage_completion key] ne $key || ($selected eq "" && ![llength $skip])} {
            # A new Vivado process resumes from the receipt the last one wrote, not from scratch.
            set resumed {}
            set seeded 0
            if {$selected ne "" && [dict exists $request_identity path]} {
                set resumed [ipiasm::_staged_completion [dict get $request_identity path] \
                    [dict create plan_hash $digest context $context \
                        request_identity $request_identity plan_names $names]]
            }
            set seeded [expr {$resumed ne {}}]
            set stage_completion [dict create key $key completed $resumed]
        }
        set start_index [expr {$selected eq "" ? [llength $skip] : [lsearch -exact $names $selected]}]
        set completed [dict get $stage_completion completed]
        set kept {}
        foreach prior [lrange $names 0 [expr {$start_index - 1}]] {
            if {$prior in $completed} {lappend kept $prior}
        }
        dict set stage_completion completed $kept
        if {$selected ne ""} {
            if {$start_index < 0 || [ipiasm::context_identity] eq {}} {
                error "PLAN_ENV_FAIL:selected stage requires an open BD and known stage"
            }
            if {$design_context ne {} && [ipiasm::context_identity] ne $design_context} {
                error "PLAN_ENV_FAIL:design context changed; use begin_design explicitly"
            }
            foreach prior [lrange $names 0 [expr {$start_index - 1}]] {
                if {$prior ni $kept} {error "PLAN_PREREQUISITE_FAIL:$selected requires $prior"}
            }
            # A design reopened in a new process reads back default values for anything IP
            # integrator propagates (a port's FREQ_HZ, for one) until validation runs:
            # "parameter propagation takes place when you choose to run Validate Design"
            # (UG994). Run it once for its propagation; its findings belong to later checks.
            if {[info exists seeded] && $seeded && [llength [info commands validate_bd_design]]} {
                catch {validate_bd_design -quiet}
                dict set stage_result repropagated 1
            }
            # Completion can come from a receipt an earlier Vivado process wrote, but the
            # gates those stages opened lived in that process's memory. Re-derive each closed
            # one from the live design with the plan's own read-only gate operation.
            foreach regate $regates {
                if {[dict get $regate stage] ni $kept || [ipiasm::_gate_get [dict get $regate gate]]} {continue}
                set operation [dict get $regate operation]
                dict set operation id "@regate:[dict get $operation id]"
                ipiasm::_run_plan_operation [dict get $regate stage] $operation
            }
        }
        set calls {}
        foreach entry $stages {
            foreach operation [dict get $entry operations] {
                if {[dict exists $operation call]} {lappend calls [dict get $operation call]}
            }
            if {[dict get $entry checkpoint]} {
                lappend calls [list ipiasm::checkpoint [list plan_[dict get $entry name]]]
            }
        }
        set api [ipiasm::api_preflight $calls]
        if {[dict get $api status] ne "PASS"} {error "API_FAIL:[dict get $api failures]"}
        foreach entry $stages {
            set name [dict get $entry name]
            if {$name in $skip} {continue}
            dict set stage_result stage $name
            # Build timing: time from here on belongs to this plan stage.
            if {[llength [info commands ::ipiasm::trace_label]]} {ipiasm::trace_label $name}
            foreach operation [dict get $entry operations] {
                ipiasm::_run_plan_operation $name $operation
                set observed [ipiasm::_plan_context $strict]
                if {[dict get $operation op] eq "create_bd_design" && $selected eq ""} {
                    if {[lreplace $observed 2 2 {}] ne [lreplace $context 2 2 {}]} {
                        error "PLAN_ENV_FAIL:project changed during bootstrap"
                    }
                    set context $observed
                    # A BD the plan just created has no history: drop any entry a removed
                    # BD of the same name left behind, and what this process remembers of
                    # an earlier design. Kept, a scratch cell's pinned requirement failed
                    # delivery, and a stale validated flag let the clocks checkpoint judge
                    # sink frequencies that had not been propagated yet.
                    ipiasm::_forget_design_state
                    ipcfg::ledger_clear
                    dict set stage_result context $context
                    dict set stage_completion key [list $digest $context $request_identity]
                } elseif {$observed ne $context} {
                    # The board stage sets the project's board part, which may start unset
                    # (the environment check allows that). Unset -> exactly the plan's board,
                    # with nothing else changed, is that operation's result, not drift.
                    if {[info exists __expected_board] && [lindex $context 5] eq ""
                        && [lindex $observed 5] eq $__expected_board
                        && [lreplace $observed 5 5 {}] eq [lreplace $context 5 5 {}]} {
                        set context $observed
                        dict set stage_result context $context
                        dict set stage_completion key [list $digest $context $request_identity]
                    } else {
                        set stage_completion {}
                        error "PLAN_ENV_FAIL:context changed during operation"
                    }
                }
            }
            if {[dict get $entry checkpoint]} {
                set tag plan_$name
                ipiasm::_run_plan_operation $name [dict create id @checkpoint:$name \
                    body [list ipiasm::checkpoint $tag] expect [list CHECKPOINT:$tag]]
            }
            dict lappend stage_completion completed $name
        }
        dict set stage_result context $context
        dict set stage_result status PARTIAL
        if {[dict get $stage_completion completed] eq $names} {dict set stage_result status PASS}
    } result options]
    dict set stage_result elapsed_ms [expr {[clock milliseconds] - $started}]
    if {!$context_known} {set stage_completion {}}
    if {$stage_completion ne {}} {
        dict set stage_result completed_stages [dict get $stage_completion completed]
    }
    if {$code} {
        dict set stage_result status FAIL
        dict set stage_result error_code [dict get $options -errorcode]
        dict set stage_result error_info [dict get $options -errorinfo]
        return -options $options $result
    }
    set resumed [dict get $stage_result resumed_after]
    return "ASSEMBLY_PLAN:[dict get $stage_result status] stages=[llength [dict get $stage_result completed_stages]] operations=[dict get $stage_result checks][expr {[llength $resumed] ? " resumed_after={$resumed}" : ""}]"
}

# Compile an assembly_plan.json and execute one named stage (or all), recording a capture receipt per operation.
proc ipiasm::assemble_from_plan {plan_path {stage ""}} {
    variable library_dir
    variable executed_plan
    variable stage_completion
    variable stage_result
    variable plan_request
    variable compiled_plans
    set plan [file normalize $plan_path]
    set stem [file rootname [file tail $plan]]
    set log_dir [ipiasm::_plan_log_dir $plan]
    file mkdir $log_dir
    set generated [file join $log_dir ".${stem}.[pid].[clock clicks].compiled.tcl"]
    set stage_result [ipiasm::_new_stage_result $stage $generated]
    set executed_plan {}
    set executing 0
    set code [catch {
        if {![file isfile $plan]} {error "no plan $plan"}
        set before_sha [ipiasm::_file_sha256 $plan]
        set compiler [file normalize [file join $library_dir .. scripts compile_assembly_plan.py]]
        set spec [file join [file dirname $plan] design_spec.json]
        set spec_sha ""
        set spec_args {}
        # A plan is compiled against its DesignSpec: without one the plan gate would open on a
        # plan nothing checks. Only a user-approved hand build (gate_override plan) skips it.
        set why [expr {[dict exists $::ipiasm::gates plan_why] ? [dict get $::ipiasm::gates plan_why] : ""}]
        if {![file isfile $spec] && ![string match "OVERRIDE:*" $why]} {
            error "PLAN_SPEC_FAIL:no design_spec.json beside [file tail $plan] in [file dirname $plan]; write it with the vivado-spec-generator skill (validate_spec.py, audit_spec.py) and compile the plan with --spec"
        }
        if {[file isfile $spec]} {
            # The DesignSpec's sources must record the reference-design search: a matching AMD
            # reference design or IP example as a source ("reference:<doc>"), or the queries that
            # found none ("reference-search"). Built from the request's words alone, an SCU35
            # I/O expander lost XD332's interrupt controller, timer and processor configuration.
            set manifest [file join [file dirname $spec] spec_sources.json]
            set text ""
            catch {set channel [open $manifest r]; try {set text [read $channel]} finally {close $channel}}
            # A CED cited as the reference does not replace the document search: the SCU35 runs
            # that cited only the MicroBlaze V CED lost XD332's 200 MHz clock and its timer.
            set documented [expr {[regexp {"id"\s*:\s*"reference-search"} $text]
                || ([regexp {"id"\s*:\s*"reference:[^"]+"} $text] && [regexp {"url"\s*:\s*"https?://} $text])}]
            # A cited document must carry its clocking, not only its overview: the SCU35 run that
            # quoted XD332's Hardware Platform page kept the CED's 100 MHz instead of XD332's 200 MHz.
            if {$documented && ![regexp {"id"\s*:\s*"reference-search"} $text]
                && ![regexp -nocase {[0-9]+(\.[0-9]+)?\s*MHz} $text] && ![string match "OVERRIDE:*" $why]} {
                error "PLAN_SPEC_FAIL:the reference document cited in [file tail $manifest] states no clock frequency; read its clocking section (for example the clocks table of a TRD user guide) and add those excerpts to the reference's text, so the DesignSpec's clocks come from the document, then recompile"
            }
            if {!$documented && ![string match "OVERRIDE:*" $why]} {
                error "PLAN_SPEC_FAIL:[file tail $manifest] beside the DesignSpec records no reference-design search; search doc search for an AMD reference design or IP example for this board and function (for an IP example also ipiasm::example_reference), add it as a \"reference:<doc>\" source with its document \"url\" (or a \"reference-search\" entry listing the queries that found none), rerun the spec audit and recompile; a CED from ipiasm::reference_search is cited in addition, not instead"
            }
            # The release's own example designs are part of that search, whatever the documents
            # turned up: ipiasm::reference_search ranks its CEDs and writes reference_search.json.
            if {![file isfile [file join [file dirname $spec] reference_search.json]] && ![string match "OVERRIDE:*" $why]} {
                error "PLAN_SPEC_FAIL:no reference_search.json beside the DesignSpec; in the scratch project run ipiasm::reference_search [file dirname $spec] {<the request>} and record its best CED (or that none matched) in spec_sources.json, then recompile"
            }
            set spec_sha [ipiasm::_file_sha256 $spec]
            set spec_args [list --spec $spec]
        }
        set cache_key [list $plan $before_sha [ipiasm::_file_sha256 $compiler] $spec_sha]
        set compiled $generated
        set cache_hit 0
        if {[dict exists $compiled_plans $cache_key]} {
            set cached [dict get $compiled_plans $cache_key]
            if {[file isfile [dict get $cached path]] &&
                [ipiasm::_file_sha256 [dict get $cached path]] eq [dict get $cached sha256]} {
                set compiled [dict get $cached path]
                set cache_hit 1
            }
        }
        if {!$cache_hit} {
            ipcfg::python_script $compiler $plan --out $compiled --dispatch {*}$spec_args
            dict set compiled_plans $cache_key [dict create path $compiled sha256 [ipiasm::_file_sha256 $compiled]]
        }
        if {[ipiasm::_file_sha256 $plan] ne $before_sha} {error "plan changed during compilation"}
        if {$spec_sha ne "" && (![file isfile $spec] || [ipiasm::_file_sha256 $spec] ne $spec_sha)} {
            error "DesignSpec changed during compilation"
        }
        set channel [open $compiled r]
        try {
            set source_header [gets $channel]
            set stage_header [gets $channel]
        } finally {close $channel}
        if {$source_header ne "# plan-source-sha256: $before_sha" ||
            $stage_header ne "# selected-stage: *"} {error "stale or malformed compiled plan"}
        set plan_request [dict create path $plan sha256 $before_sha evidence $generated selected $stage]
        if {$spec_sha ne ""} {dict set plan_request spec_sha256 $spec_sha}
        ipiasm::_gate_set plan 1 "PLAN:[file tail $plan] sha256=[string range $before_sha 0 11]"
        set executing 1
        if {[llength [info commands write_messages]]} {
            set result [source -notrace $compiled]
        } else {
            set result [source $compiled]
        }
        dict set stage_result compiled_artifact $compiled
        dict set stage_result compilation_cache_hit $cache_hit
        set executing 0
        if {![regexp {^ASSEMBLY_PLAN:(PASS|PARTIAL) stages=[0-9]+ operations=[0-9]+( resumed_after=\{[A-Za-z0-9_.: -]+\})?$} $result] ||
            [dict get $stage_result status] ni {PASS PARTIAL}} {error "unexpected final result: $result"}
        set after_sha [ipiasm::_file_sha256 $plan]
        if {$after_sha ne $before_sha} {error "plan changed during execution"}
        if {[dict get $stage_result context] ne [ipiasm::_plan_context]} {error "context changed after execution"}
        if {[dict get $stage_result status] eq "PASS"} {
            set executed_plan [dict create path $plan sha256 $after_sha context [ipiasm::_plan_context]]
        }
    } failure options]
    set plan_request {}
    if {$code} {
        if {!$executing} {set stage_completion {}; dict set stage_result completed_stages {}}
        dict set stage_result status FAIL
        dict set stage_result error_info [dict get $options -errorinfo]
        dict set stage_result error_code [dict get $options -errorcode]
        # 600 characters: room for the native-failure note's ids and repair call before its lines.
        set summary "ASSEMBLY_PLAN_FAIL:[string range [string map [list \n { } \r { }] $failure] 0 600] stage=$stage operation=[dict get $stage_result operation]"
    } else {
        set summary $result
    }
    set evidence [dict get $stage_result evidence_path]
    dict set stage_result recorded_us [clock microseconds]
    if {[catch {ipcfg::write_evidence $evidence $stage_result} archive_error archive_options]} {
        set stage_completion {}
        set executed_plan {}
        dict set stage_result status FAIL
        dict set stage_result completed_stages {}
        dict set stage_result archive_error $archive_error
        dict set stage_result archive_options $archive_options
        set summary "ASSEMBLY_PLAN_FAIL:EVIDENCE_WRITE:[string range $archive_error 0 300]"
    }
    append summary " warnings=[dict get $stage_result warning_count] critical=[dict get $stage_result critical_count] errors=[dict get $stage_result error_count] evidence=[list $evidence]"
    puts $summary
    if {[dict get $stage_result status] ne "PASS"} {return $summary}
    return "$summary\nPLAN_EXECUTION:RECORDED path=$plan sha256=$after_sha"
}

# SHA-256 of files, remembered by content. A traced build spent most of its 355 Python calls
# (about 0.45 s each, the cost of forking Vivado) hashing the same plan, spec and board files
# again. The memo key is the path, size and CRC-32 of the content (Tcl's zlib, well under a
# millisecond), so an edited file is always hashed again even within the same second; files
# not yet known are hashed together in one call of Vivado's bundled Python.
namespace eval ipiasm { variable sha_memo [dict create] }
proc ipiasm::_file_sha256 {path} {
    return [lindex [ipiasm::_file_sha256s [list $path]] 0]
}
# SHA-256 of each file, in order; unknown files are hashed in one Python call.
proc ipiasm::_file_sha256s {paths} {
    variable sha_memo
    set keys {}
    set need {}
    foreach path $paths {
        set channel [open $path rb]
        try {set data [read $channel]} finally {close $channel}
        set key [list [file normalize $path] [string length $data] [zlib crc32 $data]]
        lappend keys $key
        if {![dict exists $sha_memo $key] && [lindex $key 0] ni $need} {lappend need [lindex $key 0]}
    }
    if {[llength $need]} {
        set digests [split [string trim [ipcfg::python_exec -c {
import hashlib, sys
for name in sys.argv[1:]:
    with open(name, "rb") as stream:
        print(hashlib.sha256(stream.read()).hexdigest())
} {*}$need]] \n]
        if {[llength $digests] != [llength $need]} {error "SHA256_FAIL:expected [llength $need] digests, got [llength $digests]"}
        foreach name $need digest $digests {
            foreach key $keys {if {[lindex $key 0] eq $name} {dict set sha_memo $key [string trim $digest]}}
        }
    }
    return [lmap key $keys {dict get $sha_memo $key}]
}

# The newest assemble_from_plan receipt for this plan as {path <file> record <data>}, or {}.
# Each receipt records stage completion exactly as its Vivado process left it, including
# completion a failure or an invalidation wiped, so the newest one is the carried-over state.
proc ipiasm::_latest_plan_receipt {plan} {
    set plan [file normalize $plan]
    set pattern [file join [ipiasm::_plan_log_dir $plan] \
        ".[file rootname [file tail $plan]].*.result.tcldict"]
    set latest {}
    set latest_time {}
    foreach path [lsort [glob -nocomplain $pattern]] {
        set record {}
        catch {
            set channel [open $path r]
            try {
                fconfigure $channel -encoding utf-8 -translation lf
                set raw [read $channel]
            } finally {close $channel}
            if {[dict get $raw schema_version] == 1} {set record [dict get $raw data]}
        }
        # An unreadable receipt still counts, as a wipe: never fall back past it to an older success.
        if {[dict exists $record recorded_us] && [string is wide -strict [dict get $record recorded_us]]} {
            set time [dict get $record recorded_us]
        } elseif {[catch {expr {[file mtime $path] * 1000000}} time]} {
            continue
        }
        if {$latest_time eq {} || $time >= $latest_time} {
            set latest_time $time
            set latest [dict create path $path record $record]
        }
    }
    return $latest
}

# Where a plan's compiled form, per-operation captures and completion receipts are kept: one
# logs/ directory beside the plan, so debugging evidence does not sit among the deliverables.
# Naming it never creates it; only the writers do, so a read of a missing plan stays harmless.
proc ipiasm::_plan_log_dir {plan} {
    return [file join [file dirname [file normalize $plan]] logs]
}

# Completion a new Vivado process may resume: the newest receipt's, when it was written for
# exactly the expected plan request and context. Anything else resumes nothing.
proc ipiasm::_staged_completion {plan expected} {
    set latest [ipiasm::_latest_plan_receipt $plan]
    if {$latest eq {}} {return {}}
    set record [dict get $latest record]
    dict for {field value} $expected {
        if {![dict exists $record $field] || [dict get $record $field] ne $value} {return {}}
    }
    if {![dict exists $record completed_stages]} {return {}}
    return [dict get $record completed_stages]
}

# Record that carried-over completion for this plan is void, so no later process resumes it.
proc ipiasm::_invalidate_plan_receipts {plan reason} {
    set plan [file normalize $plan]
    set path [file join [ipiasm::_plan_log_dir $plan] \
        ".[file rootname [file tail $plan]].[pid].[clock clicks].invalidated.result.tcldict"]
    catch {
        file mkdir [file dirname $path]
        ipcfg::write_evidence $path [dict create status INVALIDATED reason $reason \
            completed_stages {} recorded_us [clock microseconds]]
    }
}

# Report whether an assembly plan reached ASSEMBLY_PLAN:PASS and still matches the plan on disk.
# A build run across Vivado processes passes when its newest receipt shows every stage complete.
proc ipiasm::plan_execution_status {{plan_path ""}} {
    variable executed_plan
    variable stage_completion
    variable stage_result
    if {(![dict exists $executed_plan path] || ![dict exists $executed_plan sha256]) && $plan_path ne ""} {
        return [ipiasm::_staged_execution_status $plan_path]
    }
    if {![dict exists $executed_plan path] || ![dict exists $executed_plan sha256]} {
        return "PLAN_EXECUTION_FAIL:no assembly plan reached ASSEMBLY_PLAN:PASS"
    }
    set recorded [dict get $executed_plan path]
    set plan [expr {$plan_path eq "" ? $recorded : [file normalize $plan_path]}]
    if {$plan ne $recorded} {
        return "PLAN_EXECUTION_FAIL:executed=$recorded requested=$plan"
    }
    set recorded_sha [dict get $executed_plan sha256]
    set failure ""
    if {![file isfile $plan]} {
        set failure "executed plan no longer exists $plan"
    } elseif {[catch {set current [ipiasm::_file_sha256 $plan]} hash_error]} {
        set failure "hash: $hash_error"
    } elseif {$current ne $recorded_sha} {
        set failure "plan changed after execution recorded=$recorded_sha current=$current"
    } elseif {[dict exists $executed_plan context] &&
        ([catch {ipiasm::_plan_context} context] || $context ne [dict get $executed_plan context])} {
        set failure "project/BD/environment changed after execution"
    }
    if {$failure ne ""} {
        set executed_plan {}
        set stage_completion {}
        dict set stage_result status FAIL
        dict set stage_result completed_stages {}
        ipiasm::_invalidate_plan_receipts $recorded $failure
        return "PLAN_EXECUTION_FAIL:$failure"
    }
    ipiasm::_gate_set plan 1 "PLAN:[file tail $plan] sha256=[string range $current 0 11] executed"
    return "PLAN_EXECUTION:OK path=$plan sha256=$current"
}

# The staged half of plan_execution_status: the same plan, spec and context checks, made
# against the newest receipt rather than this process's memory, and labelled staged=1.
proc ipiasm::_staged_execution_status {plan_path} {
    set plan [file normalize $plan_path]
    set latest [ipiasm::_latest_plan_receipt $plan]
    if {$latest eq {}} {
        return "PLAN_EXECUTION_FAIL:no assembly plan reached ASSEMBLY_PLAN:PASS and no assemble_from_plan receipt exists for $plan; run its stages with ipiasm::assemble_from_plan"
    }
    set receipt [file tail [dict get $latest path]]
    set record [dict get $latest record]
    foreach field {request_identity plan_names context completed_stages} {
        if {![dict exists $record $field]} {
            return "PLAN_EXECUTION_FAIL:the newest receipt $receipt carries no stage completion (a failed or invalidated run); rerun the plan's stages with ipiasm::assemble_from_plan"
        }
    }
    set identity [dict get $record request_identity]
    set failure ""
    if {![file isfile $plan]} {
        set failure "executed plan no longer exists $plan"
    } elseif {![dict exists $identity path] || [dict get $identity path] ne $plan} {
        set failure "$receipt records a different plan than $plan"
    } elseif {[catch {set current [ipiasm::_file_sha256 $plan]} hash_error]} {
        set failure "hash: $hash_error"
    } elseif {$current ne [dict get $identity sha256]} {
        set failure "plan changed after its stages ran recorded=[dict get $identity sha256] current=$current"
    } elseif {[dict exists $identity spec_sha256] &&
        ([catch {ipiasm::_file_sha256 [file join [file dirname $plan] design_spec.json]} spec] ||
         $spec ne [dict get $identity spec_sha256])} {
        set failure "DesignSpec changed after the plan's stages ran"
    } elseif {[catch {ipiasm::_plan_context} context] || $context ne [dict get $record context]} {
        set failure "project/BD/environment changed after the plan's stages ran"
    }
    if {$failure ne ""} {
        ipiasm::_invalidate_plan_receipts $plan $failure
        return "PLAN_EXECUTION_FAIL:$failure"
    }
    set names [dict get $record plan_names]
    set completed [dict get $record completed_stages]
    if {$completed ne $names} {
        set missing {}
        foreach name $names {if {$name ni $completed} {lappend missing $name}}
        return "PLAN_EXECUTION_FAIL:stage receipts show {$completed} of {$names} complete; run {$missing} with ipiasm::assemble_from_plan $plan <stage>"
    }
    ipiasm::_gate_set plan 1 "PLAN:[file tail $plan] sha256=[string range $current 0 11] staged"
    return "PLAN_EXECUTION:OK path=$plan sha256=$current staged=1 receipt=$receipt"
}

proc ipiasm::_discover_assembly_plans {} {
    set candidates {}
    foreach pattern [list \
        [file join [pwd] assembly_plan.json] \
        [file join [pwd] outputs assembly_plan.json] \
        [file join [pwd] outputs * assembly_plan.json]] {
        foreach path [glob -nocomplain $pattern] {
            if {[file isfile $path]} { lappend candidates [file normalize $path] }
        }
    }
    return [lsort -unique $candidates]
}

# ============================================================
# 0. Gates, policy, timing (enforced in code, not in prose)
# ============================================================
# Four gates guard the build order. They are STATE, not advice: the procs
# that would build past an unmet gate refuse with GATE_FAIL:<stage> instead of
# running. A skill instruction can be skipped; a proc that refuses cannot.
#   plan      -> set by assemble_from_plan once the compiled plan is verified, or by
#                plan_execution_status when that plan already ran (or gate_override)
#   board     -> set by require_board_preset on READY:  (or gate_override)
#   inventory -> set by gate_inventory when every spec'd IP count reads OK
#   qos       -> set when check_qos AND check_noc_solution are both OK/NA
# gate_override <g> <reason> records a deliberate, user-approved bypass.

proc ipiasm::_gate_set {g v {why ""}} {
    variable gates
    dict set gates $g $v
    if {$why ne ""} {
        dict set gates ${g}_why $why
    } elseif {!$v} {
        # A gate that just shut must not keep advertising why it was once open;
        # gate_status printed a stale `READY:board_part=..` next to board=0.
        dict unset gates ${g}_why
    }
    return ""
}
proc ipiasm::_gate_get {g} {
    variable gates
    if {![dict exists $gates $g]} { return 0 }
    return [dict get $gates $g]
}
# Returns "" when the gate is open, else a GATE_FAIL line the caller returns.
proc ipiasm::_need {g stage} {
    if {[ipiasm::_gate_get $g]} { return "" }
    switch -- $g {
        plan      { set fix "build the delivery design from a compiled plan: binding_intent.json -> binding probe in a scratch project -> proven configurations -> assembly_plan.json compiled by scripts/compile_assembly_plan.py to ASSEMBLY_PLAN:OK (ASSEMBLY_PLAN.md), then ipiasm::assemble_from_plan <abs-plan>, which opens this gate; after a Vivado restart ipiasm::plan_execution_status <abs-plan> reopens it. Scratch experiments use ipcfg::create_cell_cfg, which this gate does not block. Only the user can approve building without a plan: ipiasm::gate_override plan <their reason>" }
        board     { set fix "run ipiasm::require_board_preset (READY:) and ipiasm::check_board_hooks, or ipiasm::gate_override board <reason> after the user approved building without a board file" }
        inventory { set fix "run ipiasm::gate_inventory {<ip> <want> ...} with counts taken from the spec until every line reads OK (in a scratch experiment, list the cells that experiment created)" }
        qos       { set fix "run ipiasm::apply_qos_plan, then ipiasm::check_qos and ipiasm::check_noc_solution until both are OK (they are NA on a non-NoC part)" }
        default   { set fix "open the gate" }
    }
    return "GATE_FAIL:$stage requires the '$g' gate -> $fix"
}
# The plan gate binds only after ipiasm::init, like the inventory gate.
proc ipiasm::_need_plan {operation} {
    variable initialized
    if {!$initialized} { return "" }
    return [ipiasm::_need plan $operation]
}
proc ipiasm::_need_inventory {operation} {
    variable initialized
    if {!$initialized} { return "" }
    return [ipiasm::_need inventory $operation]
}
# --- native guards: once ipiasm.tcl is loaded, Vivado's own create_bd_cell and
# write_hw_platform are held to the same gates as the helpers unless the skill's code or
# Vivado's own code makes the call.
# Measured on the GitHub-issue designs (2026-11-07): with only the helpers gated, two
# of three agents created every cell with raw create_bd_cell, and all three wrote the
# XSA with raw write_hw_platform after the final gate failed or was never run.
# A run where the library was sourced but ipiasm::init never ran, and stage scripts were
# sourced through ipcfg::source_reported, got around a namespace-based check, so the
# caller is identified by its code: the nearest calling frame with a source file must be
# a proc body in the skill's library or in the Vivado installation (automation rules).
# Typed commands, agent scripts however they are sourced, and agent-defined procs are not.
proc ipiasm::_install_native_guards {} {
    foreach command {create_bd_cell write_hw_platform} {
        set native ::ipiasm::_native_$command
        if {[llength [info commands $native]]} {continue}
        # Vivado registers block-design commands on first use; -help loads one without acting.
        if {![llength [info commands ::$command]] && [string match -nocase *vivado* [file tail [info nameofexecutable]]]} {
            catch {uplevel #0 [list $command -help]}
        }
        if {![llength [info commands ::$command]]} {continue}
        rename ::$command $native
        proc ::$command {args} [string map [list @C@ $command] {
            if {$args ne "-help"} {
                set refusal [::ipiasm::_native_guard @C@ [::ipiasm::_trusted_caller]]
                if {$refusal ne ""} {return -code error $refusal}
            }
            return [uplevel 1 [list ::ipiasm::_native_@C@ {*}$args]]
        }]
    }
    return ""
}
# 1 when the native command's caller is skill or Vivado code: walking out from the
# wrapper's caller, the first frame with a source file is a proc body in a trusted file.
proc ipiasm::_trusted_caller {} {
    for {set level [expr {[info frame] - 1}]} {$level >= 1} {incr level -1} {
        set frame [info frame $level]
        if {[dict exists $frame proc] && [dict get $frame proc] in {::ipiasm::_trusted_caller ::create_bd_cell ::write_hw_platform}} {continue}
        if {![dict exists $frame file]} {continue}
        return [expr {[dict exists $frame proc] && [ipiasm::_trusted_file [dict get $frame file]]}]
    }
    return 0
}
# 1 when a source file belongs to the skill libraries or the Vivado installation.
proc ipiasm::_trusted_file {path} {
    set path [file normalize $path]
    set roots [list $::ipiasm::library_dir]
    if {[info exists ::ipcfg::dir]} {lappend roots $::ipcfg::dir}
    if {[info exists ::env(XILINX_VIVADO)] && $::env(XILINX_VIVADO) ne ""} {lappend roots $::env(XILINX_VIVADO)}
    # The running Vivado: <root>/bin/unwrapped/<platform>/vivado.
    lappend roots [file join [file dirname [info nameofexecutable]] .. .. ..]
    foreach root $roots {
        if {[string first "[file normalize $root]/" $path] == 0} {return 1}
    }
    # The installation's shared data tree sits beside Vivado (<install>/data): block-automation
    # rules (data/rsb/design_assist) and the CED store (data/xhub/ced). Its read-only scripts
    # count as the installation's.
    set installs [list [file join [file dirname [info nameofexecutable]] .. .. ..]]
    if {[info exists ::env(XILINX_VIVADO)] && $::env(XILINX_VIVADO) ne ""} {lappend installs $::env(XILINX_VIVADO)}
    set shared {}
    foreach root $installs { lappend shared [file join [file dirname [file normalize $root]] data] }
    foreach root $shared {
        if {[string first "$root/" $path] == 0 && [file exists $path] && ![file writable $path]} {return 1}
    }
    return 0
}
# Create one binding-probe cell; the generated binding probe calls this so that its
# creation counts as skill code.
proc ipiasm::_probe_create {vlnv name} {
    return [create_bd_cell -type [ipcfg::_cell_type $vlnv] -vlnv $vlnv $name]
}
# 1 when the open project is a scratch project: the skill keeps those under <out>/.ipiasm/.
proc ipiasm::_scratch_project {} {
    if {[catch {get_property DIRECTORY [current_project]} directory] || $directory eq ""} {return 0}
    return [expr {".ipiasm" in [file split [file normalize $directory]]}]
}
# "" when a native command may run now, else the GATE_FAIL line it fails with.
# Before the plan gate opens, cells exist only in scratch projects (<out>/.ipiasm/<token>_probe/),
# made by the skill's helpers, the binding probe or Vivado's automation: a sweep run built a
# whole delivery design with ipcfg::create_cell_cfg, a scratch helper, and skipped the plan.
proc ipiasm::_native_guard {command trusted} {
    switch -- $command {
        create_bd_cell {
            if {[ipiasm::_gate_get plan]} {return ""}
            if {$trusted && [ipiasm::_scratch_project]} {return ""}
            return "[ipiasm::_need plan create_bd_cell] (cells exist before the plan only in a scratch project under <out>/.ipiasm/<token>_probe/, made by the binding probe or ipcfg::create_cell_cfg; the delivery design gets its cells from the plan)"
        }
        write_hw_platform {
            if {$trusted} {return ""}
            set guard [ipiasm::_delivery_guard_result]
            if {[string match DELIVERY_GUARD:OK* $guard]} {return ""}
            return "GATE_FAIL:write_hw_platform requires delivery_guard to pass -> run ipiasm::final_contract_gate_from_plan (or final_contract_gate) until FINAL_CONTRACT:PASS, then ipiasm::delivery_guard; now: $guard"
        }
    }
    return ""
}
# Open a build-order gate with the user's stated reason; never for routine recovery.
proc ipiasm::gate_override {g reason} {
    variable gates
    if {$g ni {plan board inventory qos}} { return "GATE_FAIL:unknown gate '$g' (plan|board|inventory|qos)" }
    if {[string trim $reason] eq ""} { return "GATE_FAIL:gate_override needs the reason the user gave" }
    ipiasm::_gate_set $g 1 "OVERRIDE: $reason"
    return "GATE:$g overridden ($reason)"
}
# Current state of the plan, board, inventory and qos gates with the reason each opened.
proc ipiasm::gate_status {} {
    variable gates
    set out {}
    foreach g {plan board inventory qos} {
        set why ""
        if {[dict exists $gates ${g}_why]} { set why " ([dict get $gates ${g}_why])" }
        lappend out "$g=[ipiasm::_gate_get $g]$why"
    }
    return "GATES:[join $out { }]"
}
# Close all build-order gates (plan, board, inventory, qos).
proc ipiasm::gate_reset {} {
    variable gates
    variable qos_sub
    set gates [dict create plan 0 board 0 inventory 0 qos 0]
    set qos_sub [dict create qos 0 noc 0]
    return "GATES:reset"
}

# --- policy: attended (default) asks the user on every escalation;
#     unattended records the ASK lines in the report and continues where a
#     documented default exists (benchmark runs). Read it before AskQuestion.
proc ipiasm::policy {{p ""}} {
    variable policy
    if {$p eq ""} { return "POLICY:$policy" }
    if {$p ni {attended unattended}} { return "POLICY_FAIL:$p (attended|unattended)" }
    set policy $p
    return "POLICY:$policy"
}

# --- timing: call ipiasm::stage <name> at each stage boundary; ipiasm::timings
#     prints where the wall clock went. Optimise from these numbers, not guesses.
proc ipiasm::stage {name} {
    variable stage_t
    variable stage_log
    variable initialized
    # Build timing trace (lib/stage_trace.tcl): labels later calls with this stage.
    if {[llength [info commands ::ipiasm::trace_label]]} {ipiasm::trace_label $name}
    if {$initialized
        && [regexp -nocase {^(cell|inventory|clock|connect|qos|external|address|visibility|validate|deliver)} $name]
        && ![ipiasm::_gate_get plan]} {
        return [ipiasm::_need plan "stage $name"]
    }
    if {$initialized
        && [regexp -nocase {^(clock|connect|qos|external|address|visibility|validate|deliver)} $name]
        && ![ipiasm::_gate_get inventory]} {
        return [ipiasm::_need inventory "stage $name"]
    }
    if {[regexp -nocase {^(qos|address|visibility|validate|deliver)} $name]
        && [ipiasm::_propagation_current]} {
        set clocks [ipiasm::_verified_clock_contracts stage]
        if {[string match CLOCK_CONTRACT_FAIL:* $clocks]} {return "GATE_FAIL:$name $clocks"}
    }
    set now [clock milliseconds]
    if {[dict exists $stage_t name]} {
        set prev [dict get $stage_t name]
        set dt [expr {($now - [dict get $stage_t t]) / 1000.0}]
        dict set stage_log $prev [expr {[expr {[dict exists $stage_log $prev] ? [dict get $stage_log $prev] : 0}] + $dt}]
    }
    dict set stage_t name $name
    dict set stage_t t $now
    return "STAGE:$name t=[clock format [expr {$now/1000}] -format %H:%M:%S]"
}
# Seconds spent in each ipiasm::stage so far; TIMINGS:total=...
proc ipiasm::timings {} {
    variable stage_log
    variable stage_t
    if {[dict exists $stage_t name]} { ipiasm::stage [dict get $stage_t name] }
    set total 0
    set rows {}
    foreach {k v} $stage_log { set total [expr {$total + $v}]; lappend rows "$k=[format %.1f $v]s" }
    return "TIMINGS:total=[format %.1f $total]s [join $rows { }]"
}

# 1 when the BD's propagated values (a sink pin's FREQ_HZ) can be read as current: ipiasm::validate
# passed and Vivado still lists IS_VALIDATED on the BD, which any later edit removes. A design
# validated once and then rebuilt read unpropagated sinks as "stale sink frequency" failures.
# Where the BD cannot be asked, design_validated alone decides.
proc ipiasm::_propagation_current {} {
    if {!$::ipiasm::design_validated} {return 0}
    if {[catch {set design [current_bd_design -quiet]; set properties [list_property $design]}]
        || $design eq ""} {return 1}
    return [expr {"IS_VALIDATED" in $properties && [get_property -quiet IS_VALIDATED $design] eq "1"}]
}

# --- checkpoint: save the BD so a failed run resumes here instead of at zero ---
# From the clocks stage on, a checkpoint is refused while any clock input is undriven.
proc ipiasm::checkpoint {tag} {
    if {[regexp -nocase {^(plan_)?(clock|connect|qos|external|address|visibility|validate|deliver)} $tag]} {
        set clocks [ipiasm::check_undriven_clocks]
        if {![string match "UNDRIVEN_CLK:OK*" $clocks]} { return "CHECKPOINT_FAIL:$tag $clocks" }
        # Clock contracts can be judged only while propagation is current (_propagation_current).
        if {[ipiasm::_propagation_current]} {
            set contracts [ipiasm::_verified_clock_contracts checkpoint]
            if {[string match CLOCK_CONTRACT_FAIL:* $contracts]} {return "CHECKPOINT_FAIL:$tag $contracts"}
        }
    }
    if {[catch {save_bd_design} e]} { return "CHECKPOINT_FAIL:$tag $e" }
    return "CHECKPOINT:$tag [ipiasm::gate_status] [ipiasm::timings]"
}

# --- INVENTORY GATE -----------------------------------------------------------
# wants: flat list {<ip-name-or-vlnv> <count> ...} derived by the agent FROM THE
# SPEC (never from this file, never from what looks reasonable). Counts every
# instance across -hier by vendor:library:name so hierarchy placement and the
# IP version do not matter. Every line must read OK: SHORT means build the
# missing cells; OVER means a duplicate the spec did not ask for. Cells that
# automation creates on its own (smartconnect, proc_sys_reset, NoC) are only
# checked if the spec pins their count.
proc ipiasm::gate_inventory {wants} {
    ipiasm::_gate_set inventory 0
    if {[llength $wants] < 2 || [llength $wants] % 2} {
        return "INVENTORY_FAIL:wants must be a flat {ip count ...} list"
    }
    set short {}; set over {}; set ok 0; set lines {}
    foreach {ip want} $wants {
        if {![string is integer -strict $want] || $want < 0} {
            return "INVENTORY_FAIL:count for $ip must be an integer, got '$want'"
        }
        # accept a bare name (axi_gpio), an unversioned vlnv (xilinx.com:ip:axi_gpio)
        # or a full vlnv; match on vendor:library:name.
        set parts [split $ip :]
        if {[llength $parts] >= 3} { set pat "[join [lrange $parts 0 2] :]:*" } else { set pat "*:$ip:*" }
        set n [llength [get_bd_cells -hier -quiet -filter "VLNV =~ $pat"]]
        if {$n == $want} { set verdict OK; incr ok } elseif {$n < $want} { set verdict SHORT; lappend short "$ip:$n/$want" } else { set verdict OVER; lappend over "$ip:$n/$want" }
        lappend lines "INVENTORY $ip built=$n want=$want $verdict"
    }
    puts [join $lines "\n"]
    if {[llength $short] == 0 && [llength $over] == 0} {
        set memory [ipiasm::check_bram_backing]
        if {![string match "BRAM:OK*" $memory]} {
            return "INVENTORY_FAIL:$memory"
        }
        ipiasm::_gate_set inventory 1 "$ok ip(s) match the spec"
        return "INVENTORY:OK $ok ip(s)\n[join $lines "\n"]"
    }
    ipiasm::_gate_set inventory 0
    return "INVENTORY_FAIL:short={$short} over={$over} -- build/remove these before stage 2; nothing wired or addressed against the wrong cell set is reusable\n[join $lines "\n"]"
}

# Read back a dict of CONFIG.* values on one cell and fail on any mismatch; CELL_CONFIG:OK.
proc ipiasm::check_cell_config {cell desired} {
    if {[catch {dict size $desired} size] || !$size} {
        return "CELL_CONFIG_FAIL:nonempty property dictionary required"
    }
    set objects [get_bd_cells -quiet $cell]
    if {[llength $objects] != 1} {return "CELL_CONFIG_FAIL:$cell must resolve to one cell"}
    dict for {key value} $desired {
        if {![string match CONFIG.* $key]} {return "CELL_CONFIG_FAIL:$key is not a CONFIG property"}
        if {[lsearch -exact [list_property $objects] $key] < 0} {
            return "CELL_CONFIG_FAIL:$cell has no property $key"
        }
    }
    set bad [ipcfg::verify_stuck $cell $desired]
    if {[llength $bad]} {return "CELL_CONFIG_FAIL:$cell $bad"}
    return "CELL_CONFIG:OK $cell $size property(s)"
}

# Check RTL source files exist with the expected SHA-256 and the synthesis top is present.
proc ipiasm::check_source_files {files top} {
    if {[catch {dict size $files} size] || !$size} {
        return "SOURCE_FILES_FAIL:nonempty absolute-path/sha256 dictionary required"
    }
    if {[string trim $top] eq ""} {return "SOURCE_FILES_FAIL:expected synthesis top required"}
    if {[catch {
        dict for {path expected} $files {
            if {[file pathtype $path] ne "absolute" || ![regexp {^[[:xdigit:]]{64}$} $expected]} {
                error "invalid absolute path or SHA256: $path"
            }
            if {![file isfile $path]} {error "absent source file: $path"}
            set actual [ipiasm::_file_sha256 $path]
            if {![string equal -nocase $actual $expected]} {
                error "SHA256 mismatch: $path expected=$expected actual=$actual"
            }
            set path [file normalize $path]
            if {[lsearch -exact [get_files -quiet [list $path]] $path] < 0} {
                error "source file not included in project: $path"
            }
        }
        set actual_top [get_property top [current_fileset]]
        if {$actual_top ne $top} {error "synthesis top mismatch: expected=$top actual=$actual_top"}
    } failure]} {
        return "SOURCE_FILES_FAIL:$failure"
    }
    return "SOURCE_FILES:OK $size file(s) top=$top"
}

# Check every AXI BRAM controller has connected backing memory of the required width and capacity.
proc ipiasm::check_bram_backing {} {
    set failures {}
    set checked 0
    foreach controller [get_bd_cells -hier -quiet -filter {VLNV =~ *:axi_bram_ctrl:*}] {
        set ports [get_bd_intf_pins -quiet $controller/BRAM_PORT*]
        if {![llength $ports]} {
            lappend failures "$controller exposes no BRAM interface"
        }
        foreach port $ports {
            set backed 0
            foreach peer [find_bd_objs -relation connected_to -thru_hier $port] {
                foreach memory [get_bd_cells -quiet -of_objects $peer] {
                    set vlnv [get_property VLNV $memory]
                    if {[string match *:blk_mem_gen:* $vlnv] ||
                        [string match *:emb_mem_gen:* $vlnv] ||
                        [string match *:module_ref:* $vlnv]} {
                        set backed 1
                    }
                }
            }
            if {!$backed} {lappend failures "$port has no connected memory implementation"}
            incr checked
        }
    }
    if {[llength $failures]} {return "BRAM_FAIL:[join $failures {; }]"}
    return "BRAM:OK $checked interface(s) backed (capacity requires separate readback)"
}

# --- one call per scratch round: binding probe -> snapshot -> plan requirements -> draft plan ---
# Measured on the GitHub-issue designs (2026-11-07): the PLAN stage took more than half of the
# model's turns, most of them single probe, parse and inspection round trips, each of which
# re-reads the whole conversation. This runs the round in the open scratch project and
# returns one short summary; the probe records, snapshot, draft and TODO list go to files.
#   out_dir    absolute folder holding binding_intent.json (the plan folder, <out>)
#   spec       absolute design_spec.json; when given, the plan template is drafted too
#   fragments  absolute proven/*.json files from ipcfg::export_proven, if any
proc ipiasm::scratch_bind {out_dir {spec ""} {fragments {}}} {
    ipiasm::_install_native_guards
    if {[llength [info commands ::ipiasm::trace_label]]} {ipiasm::trace_label plan}
    set out_dir [file normalize $out_dir]
    set intent [file join $out_dir binding_intent.json]
    if {![file isfile $intent]} {return "SCRATCH_BIND_FAIL:no binding_intent.json in $out_dir"}
    if {[ipiasm::context_identity] eq {}} {return "SCRATCH_BIND_FAIL:open the scratch project and BD first"}
    set scripts [file normalize [file join $::ipiasm::library_dir .. scripts]]
    set binder [file join $scripts bind_design_spec.py]
    set probe [file join $out_dir binding_probe.tcl]
    set records [file join $out_dir binding_probe.out]
    set snapshot [file join $out_dir binding_snapshot.json]
    set lines {}
    set probe_args [list --intent $intent --out $probe --records-out $records]
    if {$spec ne ""} {lappend probe_args --spec [file normalize $spec]}
    if {[catch {ipcfg::python_script $binder probe-tcl {*}$probe_args} r]} {
        return "SCRATCH_BIND_FAIL:probe-tcl: [string range $r 0 300]"
    }
    # The probe writes every record to the records file; keep them out of the tool output.
    set ::__bind_quiet 1
    set code [catch {uplevel #0 [list source $probe]} r]
    unset -nocomplain ::__bind_quiet
    catch {if {[info exists ::__bind_fh]} {close $::__bind_fh; unset ::__bind_fh}}
    if {$code} {return "SCRATCH_BIND_FAIL:probe: [string range $r 0 300] (records: $records)"}
    if {[catch {ipcfg::python_script $binder parse-probe --input $records --out $snapshot} r]} {
        return "SCRATCH_BIND_FAIL:parse-probe: [string range $r 0 300] (records: $records)"
    }
    # One line per role from the snapshot: resolved VLNV, or the attempts that failed.
    set roles [ipcfg::python_exec -c {
import json, sys
snap = json.load(open(sys.argv[1]))
for role, entry in sorted((snap.get("catalog") or {}).items()):
    resolved = entry.get("resolved_vlnv") or ""
    if resolved:
        print(f"ROLE {role} -> {resolved} interfaces={len(entry.get('interfaces') or [])}")
    else:
        tries = [str(v) for k, v in entry.items() if "attempt" in k]
        print(f"ROLE_FAIL {role}: no candidate created ({'; '.join(tries)[:200]})")
} $snapshot]
    lappend lines {*}[split [string trim $roles] \n]
    if {$spec ne ""} {
        set args [list --spec [file normalize $spec] --intent $intent --snapshot $snapshot \
            --out [file join $out_dir assembly_plan.template.json]]
        if {[llength $fragments]} {lappend args --fragments {*}$fragments}
        # A STRUCTURAL CED fit seeds the plan with the CED: the draft then derives its
        # removals, checks and coverage from the recorded reference.
        set fit [file join $out_dir ced_fit.json]
        if {[file isfile $fit]} {lappend args --ced-fit $fit}
        # Clock contracts are bound to a scratch fingerprint, which only an open scratch
        # project can compute: hash the clock generators and reset synchronizers the CED
        # reference or the bound roles use (all bound IP when they name none).
        if {[catch {ipcfg::python_exec -c {
import json, os, sys
spec = json.load(open(sys.argv[1]))
if not (spec.get("functional_contracts") or {}).get("clock_reset"):
    sys.exit(0)
clocking = ("clk_wiz", "clkx5_wiz", "clk_wizard", "proc_sys_reset")
found, bound = set(), set()
reference = os.path.join(sys.argv[3], "ced_reference.json")
if os.path.isfile(reference):
    for line in open(reference):
        record = json.loads(line) if line.strip() else {}
        vlnv = str(record.get("vlnv") or "")
        if record.get("kind") == "cell" and vlnv.split(":")[2:3] and vlnv.split(":")[2] in clocking:
            found.add(vlnv)
for entry in (json.load(open(sys.argv[2])).get("catalog") or {}).values():
    vlnv = str(entry.get("resolved_vlnv") or "")
    if vlnv:
        bound.add(vlnv)
        if vlnv.split(":")[2:3] and vlnv.split(":")[2] in clocking:
            found.add(vlnv)
print(" ".join(sorted(found or bound)))
} [file normalize $spec] $snapshot $out_dir} hashed]} {
            lappend lines "CLOCK_FINGERPRINT_FAIL:[string range $hashed 0 200]"
            set hashed ""
        }
        if {[string trim $hashed] ne ""} {
            set fingerprint [ipiasm::clock_fingerprint $out_dir [file normalize $spec] [string trim $hashed]]
            lappend lines $fingerprint
            if {[string match CLOCK_FINGERPRINT:OK* $fingerprint]} {
                lappend args --clock-fingerprint [file join $out_dir clock_fingerprint.tcldict]
            }
        }
        if {[catch {ipcfg::python_script [file join $scripts draft_plan.py] {*}$args} r]} {
            lappend lines "DRAFT_PLAN_FAIL:[string range $r 0 300]"
        } else {
            lappend lines {*}[split [string trim $r] \n]
        }
    }
    set failed [llength [lsearch -all $lines ROLE_FAIL*]]
    lappend lines "SCRATCH_BIND:[expr {$failed ? {PARTIAL} : {OK}}] roles=[llength [lsearch -all $lines ROLE*]] failed=$failed snapshot=$snapshot"
    return [join $lines \n]
}

# Write the cell / intf_net / addr records of every block design in the current project to
# channel (one JSON object per line; example_reference and ced_reference). Returns the cell count.
proc ipiasm::_write_bd_records {channel} {
    set json {apply {{s} {return "\"[string map {\\ \\\\ \" \\\" \n \\n \t \\t} $s]\""}}}
    set cells 0
    foreach bd [get_files -quiet *.bd] {
        if {[string match *.gen* $bd] || [string match */ip/* $bd]} {continue}
        open_bd_design $bd
        set tag [{*}$json [file tail $bd]]
        # Leaf cells: IP and inline HDL, not hierarchies. "user" holds the values set on purpose.
        set leaves [get_bd_cells -hierarchical -quiet -filter {TYPE != hier}]
        foreach c $leaves {
            incr cells
            set pairs {}; set user {}
            foreach p [lsort [list_property $c CONFIG.*]] {
                if {[string match *.VALUE_SRC $p]} {continue}
                set v [{*}$json [get_property $p $c]]
                lappend pairs "[{*}$json $p]:$v"
                if {![catch {get_property $p.VALUE_SRC $c} src] && [string equal -nocase $src user]} {lappend user "[{*}$json $p]:$v"}
            }
            set vlnv [get_property VLNV $c]
            if {$vlnv eq ""} {set vlnv "inline:[get_property REF_NAME $c]"}
            puts $channel "{\"kind\":\"cell\",\"bd\":$tag,\"path\":[{*}$json $c],\"vlnv\":[{*}$json $vlnv],\"config\":{[join $pairs ,]},\"user\":{[join $user ,]}}"
        }
        foreach n [get_bd_intf_nets -hierarchical -quiet] {
            # Nets inside hierarchy cells reach pins Vivado will not resolve from the net
            # object alone; record what resolves and skip the rest.
            set ends {}
            catch {foreach p [get_bd_intf_pins -quiet -of_objects [get_bd_intf_nets $n]] {lappend ends [{*}$json $p]}}
            catch {foreach p [get_bd_intf_ports -quiet -of_objects [get_bd_intf_nets $n]] {lappend ends [{*}$json "port:$p"]}}
            if {[llength $ends]} {puts $channel "{\"kind\":\"intf_net\",\"bd\":$tag,\"ends\":\[[join $ends ,]\]}"}
        }
        foreach s [get_bd_addr_segs -quiet -filter {OFFSET != ""}] {
            catch {puts $channel "{\"kind\":\"addr\",\"seg\":[{*}$json $s],\"offset\":[{*}$json [get_property OFFSET $s]],\"range\":[{*}$json [get_property RANGE $s]]}"}
        }
        # Hierarchy flattened: leaf-to-leaf interface links, clock inputs, interrupt links and
        # master-to-slave address assignments (what draft_spec.py and the comparisons read).
        set seen [dict create]
        foreach c $leaves {
            foreach ip [get_bd_intf_pins -quiet -of_objects $c] {
                foreach e [ipiasm::_leaf_ends $ip] {
                    set key [lsort [list $ip $e]]
                    if {[dict exists $seen $key]} {continue}
                    dict set seen $key 1
                    puts $channel "{\"kind\":\"link\",\"a\":[{*}$json $ip],\"b\":[{*}$json $e],\"vlnv\":[{*}$json [get_property VLNV $ip]],\"mode\":[{*}$json [get_property MODE $ip]]}"
                }
            }
            foreach p [get_bd_pins -quiet -of_objects $c] {
                if {[get_bd_intf_pins -quiet -of_objects $p] ne ""} {continue}
                set type [get_property TYPE $p]; set dir [get_property DIR $p]
                if {$type eq "clk" && $dir eq "I"} {
                    set drv [lsearch -all -inline -regexp [ipiasm::_leaf_ends $p] {^port:|/}]
                    set drv [lmap d $drv {expr {[string match port:* $d] || [get_property DIR [get_bd_pins -quiet $d]] eq "O" ? $d : [continue]}}]
                    puts $channel "{\"kind\":\"clk\",\"pin\":[{*}$json $p],\"freq\":[{*}$json [get_property CONFIG.FREQ_HZ $p]],\"src\":[{*}$json [join $drv ,]]}"
                } elseif {$dir eq "O" && ($type eq "intr" || [regexp {concat} "[get_property VLNV $c] [get_property -quiet REF_NAME $c]"])} {
                    # A concat's output is not typed as an interrupt; follow it to the controller.
                    foreach d [ipiasm::_leaf_ends $p] {
                        if {![string match port:* $d] && [get_property DIR [get_bd_pins -quiet $d]] ne "I"} {continue}
                        puts $channel "{\"kind\":\"irq\",\"src\":[{*}$json $p],\"dst\":[{*}$json $d]}"
                    }
                }
            }
        }
        foreach sp [get_bd_addr_spaces -quiet] {
            set m [get_bd_cells -quiet -of_objects $sp]
            foreach s [get_bd_addr_segs -quiet -of_objects $sp] {
                if {![string match */SEG_* $s]} {continue}
                set slave [lindex [get_bd_addr_segs -quiet -of_objects $s] 0]
                if {$slave eq ""} {continue}
                set sintf [lindex [get_bd_intf_pins -quiet -of_objects $slave] 0]
                catch {puts $channel "{\"kind\":\"map\",\"master\":[{*}$json $m],\"space\":[{*}$json [get_property NAME $sp]],\"slave\":[{*}$json [file dirname [file dirname $slave]]],\"slave_intf\":[{*}$json [expr {$sintf eq {} ? {} : [get_property NAME $sintf]}]],\"offset\":[{*}$json [get_property OFFSET $s]],\"range\":[{*}$json [get_property RANGE $s]]}"}
            }
        }
    }
    return $cells
}

# Leaf pins and top-level ports (port:<name>) connected to a pin or interface pin through any
# hierarchy.
proc ipiasm::_leaf_ends {obj} {
    set out {}
    if {[catch {find_bd_objs -quiet -relation connected_to -thru_hier $obj} objects]} {return {}}
    foreach o $objects {
        if {$o eq $obj} {continue}
        set cls [get_property CLASS $o]
        if {$cls in {bd_port bd_intf_port}} {
            lappend out "port:[get_property NAME $o]"
        } else {
            set c [get_bd_cells -quiet -of_objects $o]
            if {$c ne "" && [get_property TYPE $c] ne "hier"} {lappend out $o}
        }
    }
    return [lsort -unique $out]
}

# Which board interfaces of the current project's board an IP takes, as a CONFIG dict of
# its *_BOARD_INTERFACE parameters, plus the parameters several interfaces fit. Board
# interfaces name the IPs they suit (PREFERENCES); each preferred interface is tried on each
# parameter of a throwaway instance of the IP, because only set_property knows the legal
# values (list_property_value returns none for IP parameters). A parameter is chosen when
# exactly one interface fits it and that interface fits no other parameter. The rejected
# tries' messages are suppressed: they are probes, not errors. Returns
# {config <dict> ambiguous <list of "PARAM={a b}">}; empty without a board part.
proc ipiasm::_board_interface_choices {def} {
    set result [dict create config {} ambiguous {}]
    if {[get_property -quiet BOARD_PART [current_project]] eq ""} {return $result}
    set candidates {}
    foreach interface [get_board_part_interfaces -quiet] {
        foreach pattern [get_property -quiet PREFERENCES $interface] {
            if {[string match $pattern $def]} {lappend candidates [get_property NAME $interface]; break}
        }
    }
    if {![llength $candidates]} {return $result}
    set probe exref_board_probe
    catch {remove_files -quiet [get_files -quiet $probe.xci]}
    if {[catch {create_ip -vlnv $def -module_name $probe}]} {return $result}
    set ip [get_ips $probe]
    set messages {{IP_Flow 19-3461} {IP_Flow 19-3460} {Common 17-39}}
    foreach id $messages {catch {set_msg_config -id $id -suppress}}
    set fits {}
    foreach parameter [lsort [list_property $ip CONFIG.*_BOARD_INTERFACE]] {
        if {[get_property $parameter $ip] ne "Custom"} {continue}
        foreach candidate $candidates {
            if {![catch {set_property $parameter $candidate $ip}]} {
                dict lappend fits $parameter $candidate
                catch {set_property $parameter Custom $ip}
            }
        }
    }
    foreach id $messages {catch {reset_msg_config -id $id -suppress}}
    set file [get_files -quiet $probe.xci]
    catch {remove_files -quiet $file}
    if {$file ne ""} {catch {file delete -force [file dirname $file]}}
    dict for {parameter options} $fits {
        set shared 0
        dict for {other others} $fits {
            if {$other ne $parameter && [lindex $options 0] in $others} {set shared 1}
        }
        if {[llength $options] == 1 && !$shared} {
            dict set result config $parameter [lindex $options 0]
        } else {
            dict lappend result ambiguous "$parameter=\{$options\}"
        }
    }
    return $result
}

# --- ground truth for "recreate the IP example design" requests ---
# Generate the Vivado example design of one IP (Open IP Example Design) beside the scratch
# project, record what it contains, and return to the scratch project. The record is the
# source for the DesignSpec and plan: the example's IPs and their CONFIG values, its block
# designs' cells, interface connections and address segments, its top module and constraints.
# Writes <out_dir>/example_reference.json (one JSON object per line); returns one line.
#   vlnv    catalog VLNV (version optional)
#   config  CONFIG.* dict applied before generation. Empty (or the word "board") applies the
#           board interfaces of the project's board that this IP takes
#           (_board_interface_choices), as the IP customization's Board tab would set them:
#           agents asked for the default first and then regenerated with them, a minute each.
#           The word "custom" leaves them at Custom; it and a dict name what they left
#           (board_interfaces_unused=).
proc ipiasm::example_reference {vlnv out_dir {config {}}} {
    set out_dir [file normalize $out_dir]
    file mkdir $out_dir
    if {[catch {current_project} origin] || $origin eq ""} {return "EXAMPLE_REF_FAIL:open the scratch project first"}
    set def [lindex [lsort -dictionary [get_ipdefs -all -quiet [expr {[llength [split $vlnv :]] == 4 ? $vlnv : "$vlnv:*"}]]] end]
    if {$def eq ""} {return "EXAMPLE_REF_FAIL:no IP definition for $vlnv"}
    set module exref_[lindex [split $def :] 2]
    set board_note ""
    if {[catch {ipiasm::_board_interface_choices $def} choices]} {set choices [dict create config {} ambiguous {}]}
    if {$config in {{} board}} {
        set config [dict get $choices config]
        if {[dict size $config]} {set board_note " board_interfaces={$config} (config custom leaves them at Custom)"}
        if {[llength [dict get $choices ambiguous]]} {append board_note " board_ambiguous={[dict get $choices ambiguous]}"}
    } else {
        if {$config eq "custom"} {set config {}}
        set unused [dict filter [dict get $choices config] script {parameter value} {expr {![dict exists $config $parameter]}}]
        if {[dict size $unused]} {
            set board_note " board_interfaces_unused={$unused} (an empty config applies them)"
        }
    }
    set code [catch {
        set ip [create_ip -vlnv $def -module_name $module]
        if {[dict size $config]} {set_property -dict $config [get_ips $module]}
        open_example_project -force -dir [file join $out_dir example] [get_ips $module]
        # The example is written to disk, not opened: open it beside the scratch project.
        set xpr [lindex [glob -nocomplain [file join $out_dir example * *.xpr] [file join $out_dir example *.xpr]] 0]
        if {$xpr eq ""} {error "open_example_project wrote no project under [file join $out_dir example]"}
        open_project $xpr
        set example [current_project]
        if {$example eq $origin} {error "the example project did not open"}
        set path [file join $out_dir example_reference.json]
        set channel [open $path w]
        set json {apply {{s} {return "\"[string map {\\ \\\\ \" \\\" \n \\n \t \\t} $s]\""}}}
        puts $channel "{\"kind\":\"example\",\"ip\":[{*}$json $def],\"top\":[{*}$json [get_property top [current_fileset]]]}"
        set ips 0
        foreach x [get_ips -quiet] {
            incr ips
            set pairs {}; set user {}
            foreach p [lsort [list_property $x CONFIG.*]] {
                set pair [format %s:%s [{*}$json $p] [{*}$json [get_property $p $x]]]
                lappend pairs $pair
                # list_property does not list CONFIG.<p>.VALUE_SRC on an IP, but it reads back.
                if {![catch {get_property $p.VALUE_SRC $x} src] && [string equal -nocase $src user]} {lappend user $pair}
            }
            puts $channel "{\"kind\":\"ip\",\"name\":[{*}$json [get_property NAME $x]],\"vlnv\":[{*}$json [get_property IPDEF $x]],\"config\":{[join $pairs ,]},\"user\":{[join $user ,]}}"
        }
        set cells [ipiasm::_write_bd_records $channel]
        foreach x [get_files -quiet -of_objects [get_filesets constrs_1]] {
            puts $channel "{\"kind\":\"xdc\",\"file\":[{*}$json [file tail $x]]}"
        }
        close $channel
        close_project
        current_project $origin
        catch {delete_ip_run [get_ips $module]}
        catch {export_ip_user_files -of_objects [get_files -quiet $module.xci] -no_script -reset -force -quiet}
        catch {remove_files [get_files -quiet $module.xci]}
    } failure]
    if {$code} {
        catch {if {[current_project] ne $origin} {close_project; current_project $origin}}
        return "EXAMPLE_REF_FAIL:[string range $failure 0 300]"
    }
    set draft [expr {$cells || $ips ? [ipiasm::_draft_from_reference [file join $out_dir example_reference.json] $out_dir] : ""}]
    return "EXAMPLE_REF:OK ip=$def ips=$ips bd_cells=$cells file=[file join $out_dir example_reference.json]$board_note$draft"
}

# --- reference-design search: which of the release's CEDs already builds this? ---
# Reads the running release's Configurable Example Designs (get_example_designs: names,
# SUPPORTED_BOARDS, SUPPORTED_PARTS, CONFIG options, repository) into an index cached per
# Vivado build (the first get_example_designs in a session takes about a minute), then ranks
# them against the request, the DesignSpec beside out_dir and, after scratch_bind, the bound
# roles (scripts/reference_search.py). Writes <out_dir>/reference_search.json; returns the
# ranking lines and REFERENCE_SEARCH:<STRONG|PARTIAL|WEAK|NONE>. Needs an open project (the
# scratch project) for the board and part. IPIASM_CACHE_DIR moves the index cache.
proc ipiasm::reference_search {out_dir {request ""}} {
    variable library_dir
    set out_dir [file normalize $out_dir]
    file mkdir $out_dir
    if {[catch {current_project} project] || $project eq ""} {
        return "REFERENCE_SEARCH_FAIL:open the scratch project (board and part set) first"
    }
    set board [get_property -quiet BOARD_PART $project]
    set part [get_property -quiet PART $project]
    set index [ipiasm::_ced_index]
    if {[string match REFERENCE_SEARCH_FAIL* $index]} {return $index}
    set script [file normalize [file join $library_dir .. scripts reference_search.py]]
    set args [list --index $index --out [file join $out_dir reference_search.json] --board $board --part $part --text $request]
    set spec [file join $out_dir design_spec.json]
    if {[file isfile $spec]} {lappend args --spec $spec}
    set snapshot [file join $out_dir binding_snapshot.json]
    if {[file isfile $snapshot]} {lappend args --roles $snapshot}
    if {[catch {ipcfg::python_script $script {*}$args} result]} {
        return "REFERENCE_SEARCH_FAIL:[string range $result 0 300]"
    }
    return [string trim $result]
}

# Draft the DesignSpec and the reference digest from a recorded reference
# (scripts/draft_spec.py); returns " draft=<path> digest=<path>" or "" when drafting failed.
proc ipiasm::_draft_from_reference {reference out_dir} {
    variable library_dir
    set script [file normalize [file join $library_dir .. scripts draft_spec.py]]
    set args [list --reference $reference --out $out_dir]
    catch {lappend args --part [get_property PART [current_project]]}
    catch {set board [get_property BOARD_PART [current_project]]; if {$board ne ""} {lappend args --board $board}}
    if {[catch {ipcfg::python_script $script {*}$args} result] || ![regexp {draft=(\S+) digest=(\S+)} $result -> draft digest]} {
        return ""
    }
    return " draft=$draft digest=$digest"
}

# --- ground truth from a matching CED ---
# Instantiate one of the release's CEDs (the best match from reference_search) in its own
# scratch project under <out_dir>/.ipiasm/, record its block design the way example_reference
# records an IP example (cells with CONFIG, interface nets, address segments), and return to
# the scratch project. Writes <out_dir>/ced_reference.json; returns one line.
#   ced      example-design name (xilinx.com:design:<name>:<version>, version optional)
#   options  option -> value, e.g. {Preset Microcontroller}
proc ipiasm::ced_reference {ced out_dir {options {}}} {
    set out_dir [file normalize $out_dir]
    if {[catch {current_project} origin] || $origin eq ""} {return "CED_REF_FAIL:open the scratch project first"}
    set board [get_property -quiet BOARD_PART $origin]
    set part [get_property -quiet PART $origin]
    set name [lindex [split $ced :] 2]
    if {$name eq ""} {set name $ced}
    if {[catch {get_example_designs} designs]} {return "CED_REF_FAIL:get_example_designs failed: [string range $designs 0 200]"}
    set match [lindex [lsort -dictionary [lsearch -all -inline -glob $designs [expr {[llength [split $ced :]] == 4 ? $ced : "*:$name:*"}]]] end]
    if {$match eq ""} {return "CED_REF_FAIL:no example design $ced in this release"}
    set opts {}
    dict for {key value} $options { lappend opts "$key.VALUE" $value }
    set dir [file join $out_dir .ipiasm ced_$name]
    set code [catch {
        create_project -force ced_$name $dir -part $part
        set project [current_project]
        if {$board ne ""} {set_property board_part $board $project}
        create_bd_design design_1
        instantiate_example_design -design design_1 -options $opts $match
        set validation [expr {[catch {validate_bd_design -force} v] ? "FAIL" : "OK"}]
        catch {save_bd_design}
        set path [file join $out_dir ced_reference.json]
        set channel [open $path w]
        puts $channel "{\"kind\":\"ced\",\"name\":[ipcfg::_json_str $match],\"options\":[ipcfg::_json_strmap $options],\"board\":[ipcfg::_json_str $board],\"validate\":[ipcfg::_json_str $validation]}"
        set cells [ipiasm::_write_bd_records $channel]
        close $channel
        close_project
        current_project $origin
    } failure]
    if {$code} {
        catch {if {[current_project] ne $origin} {close_project; current_project $origin}}
        return "CED_REF_FAIL:[string range $failure 0 300]"
    }
    set draft [ipiasm::_draft_from_reference [file join $out_dir ced_reference.json] $out_dir]
    set result "CED_REF:OK ced=$match bd_cells=$cells validate=$validation file=[file join $out_dir ced_reference.json]$draft"
    if {[file isfile [file join $out_dir ced_checklist.json]]} { append result \n[ipiasm::ced_fit $out_dir] }
    return $result
}

proc ipiasm::_ced_cache_dir {} {
    if {[info exists ::env(IPIASM_CACHE_DIR)] && $::env(IPIASM_CACHE_DIR) ne ""} {return $::env(IPIASM_CACHE_DIR)}
    if {[info exists ::env(HOME)]} {return [file join $::env(HOME) .cache vivado-ipi-assembler]}
    return [file join [pwd] .ipiasm]
}

# Path of the CED index for this Vivado build, written on first use.
proc ipiasm::_ced_index {} {
    set build [ipcfg::_build_key]
    set dir [ipiasm::_ced_cache_dir]
    set path [file join $dir "ced_index_[regsub -all {[^A-Za-z0-9._-]} $build _].json"]
    if {[file isfile $path] && [file size $path] > 0} {return $path}
    # Opened before the CEDs' own Tcl runs (get_example_designs sources every CED's init.tcl).
    set tmp $path.[pid].tmp
    if {[catch {file mkdir $dir; open $tmp w} channel]} {
        return "REFERENCE_SEARCH_FAIL:cannot write the CED index in $dir: $channel"
    }
    set here [pwd]
    set code [catch {get_example_designs} designs]
    catch {cd $here}
    if {$code} {
        catch {close $channel; file delete $tmp}
        return "REFERENCE_SEARCH_FAIL:get_example_designs failed: [string range $designs 0 200]"
    }
    set rows {}
    foreach d $designs {
        # A CED's option and support procs run against the open project's part; some raise
        # on a part they do not support. Read what resolves and keep the CED.
        set config {}
        if {![catch {list_property $d CONFIG.*} names]} {
            foreach p $names {
                if {![catch {get_property $p $d} v]} { lappend config [string range $p 7 end] $v }
            }
        }
        set fields {}
        foreach {key prop} {name NAME display DISPLAY_NAME description DESCRIPTION boards SUPPORTED_BOARDS
                            parts SUPPORTED_PARTS repo REPO_DIRECTORY} {
            if {[catch {string trim [get_property $prop $d]} v]} { set v "" }
            if {$key eq "name" && $v eq ""} { set v $d }
            lappend fields $key [ipcfg::_json_str $v]
        }
        lappend fields config [ipcfg::_json_strmap $config]
        lappend rows [ipcfg::_json_obj $fields]
    }
    puts $channel "{\"build\": [ipcfg::_json_str $build], \"ceds\": \[\n[join $rows ",\n"]\n\]}"
    close $channel
    file rename -force $tmp $path
    return $path
}

# --- does the design keep its reference design's functional IP? ---
# When ced_reference.json or example_reference.json sits beside the DesignSpec (or one level
# below), every functional IP of that reference (glue, interconnect, resets and simulation-only
# models excluded) must be in the design, or be listed with a reason in
# <spec_dir>/reference_deviations.json: [{"ip": "<catalog name>", "count": n, "reason": "..."}].
# Run by final_contract_gate. Returns REFERENCE_COVERAGE:OK|NA|FAIL with one line per shortfall.
proc ipiasm::check_reference_coverage {spec_dir} {
    variable library_dir
    set refs [concat [glob -nocomplain -directory $spec_dir ced_reference.json example_reference.json] \
                     [glob -nocomplain -directory $spec_dir -join * ced_reference.json] \
                     [glob -nocomplain -directory $spec_dir -join * example_reference.json]]
    if {![llength $refs]} { return "REFERENCE_COVERAGE:NA no ced_reference.json or example_reference.json beside the DesignSpec" }
    set channel [file tempfile cells_file]
    try {
        foreach c [get_bd_cells -quiet -hierarchical -filter {TYPE == ip}] { puts $channel [get_property VLNV $c] }
    } on error {message} {
        close $channel
        file delete -force $cells_file
        return "REFERENCE_COVERAGE:FAIL cannot list the design's cells: $message"
    }
    close $channel
    set script [file normalize [file join $library_dir .. scripts reference_coverage.py]]
    if {[catch {ipcfg::python_script $script --built $cells_file --spec-dir $spec_dir} result]} {
        set result "REFERENCE_COVERAGE:FAIL checker error: [string range $result 0 200]"
    }
    file delete -force $cells_file
    return [string trim $result]
}

# --- plan seed: start the delivery design from a matching CED ---
# Plan operation (cells stage, first): instantiate the release's CED into the open, empty
# delivery block design with the given options, so the plan only adjusts it (configuration,
# counts, removals, additions). Use the CED that ipiasm::reference_search ranked STRONG and
# ipiasm::ced_reference recorded. Returns CED:OK ced=<name> cells=<n>.
proc ipiasm::instantiate_ced {ced {options {}}} {
    if {[set g [ipiasm::_need_plan "instantiate_ced $ced"]] ne ""} { return $g }
    if {[set g [ipiasm::_need board "instantiate_ced $ced"]] ne ""} { return $g }
    set bd [current_bd_design -quiet]
    if {$bd eq ""} { return "CED_FAIL:no open block design; create the delivery design first" }
    # A plan replay reruns this operation on the design it already seeded: skip it there.
    set record [file join [get_property DIRECTORY [current_project]] .ipiasm_ced_seed]
    set seeded {}
    catch {set c [open $record r]; set seeded [read $c]; close $c}
    set name [lindex [split $ced :] 2]
    if {$name eq ""} { set name $ced }
    if {[llength [get_bd_cells -quiet]] && [dict exists $seeded $bd] && [string match "*:$name:*" [dict get $seeded $bd]]} {
        return "SKIP:$bd already seeded with [dict get $seeded $bd]"
    }
    if {[llength [get_bd_cells -quiet]]} {
        return "CED_FAIL:$bd already has [llength [get_bd_cells -quiet]] cell(s); a CED seeds an empty design, so put instantiate_ced first in the cells stage"
    }
    if {[catch {get_example_designs} designs]} { return "CED_FAIL:get_example_designs failed: [string range $designs 0 200]" }
    set match [lindex [lsort -dictionary [lsearch -all -inline -glob $designs [expr {[llength [split $ced :]] == 4 ? $ced : "*:$name:*"}]]] end]
    if {$match eq ""} { return "CED_FAIL:no example design $ced in this release" }
    set opts {}
    dict for {key value} $options { lappend opts "$key.VALUE" $value }
    if {[catch {instantiate_example_design -design $bd -options $opts $match} failure]} {
        return "CED_FAIL:[string range $failure 0 300]"
    }
    current_bd_design $bd
    dict set seeded $bd $match
    catch {set c [open $record w]; puts -nonewline $c $seeded; close $c}
    return "CED:OK ced=$match cells=[llength [get_bd_cells -quiet -hierarchical]]"
}

# Plan operation: remove a cell that a CED seed brought in and the DesignSpec does not ask
# for, with the nets that only it used and the external ports on those nets (a board
# interface left unconnected raises BD 41-3717 at validate). Returns
# SUCCESS:removed <cell> ports=<n>.
proc ipiasm::remove_cell {cell} {
    if {[set g [ipiasm::_need_plan "remove_cell $cell"]] ne ""} { return $g }
    set object [get_bd_cells -quiet $cell]
    if {$object eq ""} { return "SKIP:no cell $cell" }
    set nets {}
    set ports {}
    foreach pin [get_bd_pins -quiet -of_objects $object] {
        foreach net [get_bd_nets -quiet -of_objects $pin] {
            set net_ports [get_bd_ports -quiet -of_objects $net]
            if {[llength [get_bd_pins -quiet -of_objects $net]] + [llength $net_ports] <= 2} {
                lappend nets $net
                lappend ports {*}$net_ports
            }
        }
    }
    set intf {}
    set intf_ports {}
    foreach pin [get_bd_intf_pins -quiet -of_objects $object] {
        foreach net [get_bd_intf_nets -quiet -of_objects $pin] {
            lappend intf $net
            lappend intf_ports {*}[get_bd_intf_ports -quiet -of_objects $net]
        }
    }
    # Nets first, one at a time (a shared object list is rejected), then the cell. A port
    # collected above sat only on a deleted net (an interface port has one net; the scalar
    # nets deleted have at most two ends), so it is orphaned and goes too.
    foreach n [lsort -unique $intf] { catch {delete_bd_objs [get_bd_intf_nets -quiet $n]} }
    foreach n [lsort -unique $nets] { catch {delete_bd_objs [get_bd_nets -quiet $n]} }
    if {[catch {delete_bd_objs $object} failure]} {
        return "CONFIGURE_FAIL:REMOVE:[string range $failure 0 200]"
    }
    set removed 0
    foreach p [lsort -unique $intf_ports] {
        if {![catch {delete_bd_objs [get_bd_intf_ports -quiet $p]}]} { incr removed }
    }
    foreach p [lsort -unique $ports] {
        if {![catch {delete_bd_objs [get_bd_ports -quiet $p]}]} { incr removed }
    }
    return "SUCCESS:removed $cell ports=$removed"
}

# --- gated cell creation: the board must be settled before the first cell ---
proc ipiasm::create_cell {vlnv cell {d {}}} {
    if {[set g [ipiasm::_need_plan "create_cell $cell"]] ne ""} { return $g }
    if {[set g [ipiasm::_need board "create_cell $cell"]] ne ""} { return $g }
    if {![ipcfg::vlnv_ok $vlnv]} {
        if {[llength [split $cell :]] >= 3} {
            return "CONFIGURE_FAIL:ARGUMENT_ORDER:'$vlnv' is not an IP type but '$cell' looks like one -> RECOVER:ipiasm::create_cell <vlnv> <cell> ?config?"
        }
        return "CONFIGURE_FAIL:no ipdef for $vlnv (ask get_ipdefs; never downgrade a VLNV silently)"
    }
    return [ipcfg::create_cell_cfg $vlnv $cell $d]
}

# Create an RTL module-reference cell from source files, with optional CONFIG properties.
proc ipiasm::create_module_ref {module cell sources {config {}}} {
    if {[set gate [ipiasm::_need_plan "create_module_ref $cell"]] ne ""} { return $gate }
    if {[set gate [ipiasm::_need board "create_module_ref $cell"]] ne ""} { return $gate }
    if {$module eq "" || $cell eq "" || [llength $sources] == 0} {
        return "CONFIGURE_FAIL:MODULE_REF:module, cell and source files are required"
    }
    if {[catch {dict size $config}]} { return "CONFIGURE_FAIL:MODULE_REF:config must be a dictionary" }
    dict for {key value} $config {
        if {![string match CONFIG.* $key]} { return "CONFIGURE_FAIL:MODULE_REF:only CONFIG.* properties are allowed" }
    }
    set normalized {}
    foreach source $sources {
        if {![file isfile $source] || ![file readable $source]} {
            return "CONFIGURE_FAIL:MODULE_SOURCE:missing or unreadable file $source"
        }
        lappend normalized [file normalize $source]
    }
    set object [get_bd_cells -quiet $cell]
    if {[llength $object] > 1} { return "CONFIGURE_FAIL:MODULE_REF:ambiguous cell $cell" }
    if {$object ne ""} {
        set identity [split [get_property VLNV $object] :]
        if {[llength $identity] != 4 || [lrange $identity 0 2] ne [list xilinx.com module_ref $module]} {
            return "CONFIGURE_FAIL:MODULE_REF:$cell is not a reference to $module"
        }
    }
    set fileset [current_fileset]
    set registered {}
    foreach source [get_files -quiet -of_objects $fileset] {
        lappend registered [file normalize $source]
    }
    set pending {}
    foreach source [lsort -unique $normalized] {
        if {$source ni $registered} { lappend pending $source }
    }
    if {[llength $pending]} {
        if {[catch {ipcfg::attempt_call $cell module_sources {
            add_files -norecurse -fileset $fileset $pending
            update_compile_order -fileset $fileset
        }} diagnostic]} { return "CONFIGURE_FAIL:MODULE_SOURCE:$diagnostic" }
    }
    if {$object eq ""} {
        if {[catch {ipcfg::attempt_call $cell create_module_ref {
            update_compile_order -fileset $fileset
            create_bd_cell -type module -reference $module $cell
        }} diagnostic]} { return "CONFIGURE_FAIL:MODULE_REF:$diagnostic" }
    }
    set mismatch [ipcfg::verify_stuck $cell $config]
    if {[llength $mismatch]} {
        set result [ipcfg::apply_dict $cell $config]
        if {![string match SUCCESS:* $result]} { return $result }
        set mismatch [ipcfg::verify_stuck $cell $config]
        if {[llength $mismatch]} { return "CONFIGURE_FAIL:MODULE_CONFIG:$mismatch" }
    }
    return "SUCCESS:module reference $cell -> $module"
}

# Preserve a functional block's DesignSpec role in its live instance name.
# Independent readback cannot infer that an anonymous axi_bram_ctrl_0 is the
# "mailbox" function; role-bearing names make the implementation self-describing
# without constraining hierarchy or catalog IP choice.
proc ipiasm::ensure_semantic_cell_name {cell role} {
    set obj [get_bd_cells -quiet $cell]
    if {$obj eq ""} { return "ROLE_NAME_FAIL:no cell $cell" }
    set safe [string tolower $role]
    regsub -all {[^a-z0-9_]+} $safe {_} safe
    set safe [string trim $safe _]
    if {$safe eq ""} { return "ROLE_NAME_FAIL:empty semantic role '$role'" }
    set current [file tail $obj]
    set tokens [lsearch -all -inline -not -exact [split $safe _] ""]
    set missing 0
    foreach token $tokens {
        if {![string match -nocase "*${token}*" $current]} { set missing 1; break }
    }
    if {!$missing} { return "ROLE_NAME:OK role=$safe cell=$obj" }
    set parent [file dirname $obj]
    if {$parent eq "." || $parent eq "/"} { set parent "" }
    set leaf "${safe}_${current}"
    set candidate [expr {$parent eq "" ? "/$leaf" : "$parent/$leaf"}]
    if {[llength [get_bd_cells -quiet $candidate]]} {
        return "ROLE_NAME_FAIL:target cell already exists $candidate"
    }
    if {[catch {set_property NAME $leaf $obj} error]} {
        return "ROLE_NAME_FAIL:$error"
    }
    set renamed [get_bd_cells -quiet $candidate]
    if {$renamed eq ""} { return "ROLE_NAME_FAIL:rename did not produce $candidate" }
    return "ROLE_NAME:OK role=$safe cell=$renamed renamed_from=$obj"
}

# ============================================================
# 1. Connection layer
# ============================================================

# --- internal: is an interface pin already attached to a net? ---
proc ipiasm::_intf_wired {pin} {
    return [expr {[llength [get_bd_intf_nets -quiet -of_objects $pin]] > 0}]
}
proc ipiasm::_net_wired {pin} {
    return [expr {[llength [get_bd_nets -quiet -of_objects $pin]] > 0}]
}
# An interface used signal by signal (a GPIO's gpio_io_o, an interrupt's irq)
# has no interface net, but it is wired: one of its member pins carries a net.
proc ipiasm::_intf_members_wired {pin} {
    foreach member [get_bd_pins -quiet -of_objects [get_bd_intf_pins -quiet $pin]] {
        if {[ipiasm::_net_wired $member]} { return 1 }
    }
    return 0
}

# --- is a scalar clk/rst pin actually a member of a wired/external interface? ---
# Differential clocks (e.g. ddr_noc/sys_clk0_clk_p|_clk_n) and interface-borne
# clocks expose scalar leaf pins whose PARENT_INTF property is empty in this
# Vivado build, so a naive "scalar clk pin with no net" check flags them as
# unconnected even though their parent diff-clock INTERFACE pin is wired or
# externalized to a boundary port. Match the scalar leaf to an intf pin on the
# same cell (leaf starts with "<intf>_") and treat it as covered if that intf is
# wired, without hiding a genuinely open clock.
proc ipiasm::_scalar_in_wired_intf {pin} {
    set cell [get_bd_cells -quiet -of_objects [get_bd_pins -quiet $pin]]
    if {$cell eq ""} { return 0 }
    # Vivado knows the owning interface of most members (a memory's clka/rsta
    # inside BRAM_PORTA); the name match below covers the ones it does not.
    foreach ip [get_bd_intf_pins -quiet -of_objects [get_bd_pins -quiet $pin]] {
        if {[ipiasm::_intf_wired $ip]} { return 1 }
    }
    set leaf [file tail $pin]
    foreach ip [get_bd_intf_pins -quiet -of_objects $cell] {
        set ibase [file tail $ip]
        if {[string match "${ibase}_*" $leaf] && [ipiasm::_intf_wired $ip]} { return 1 }
    }
    return 0
}

# --- is a scalar leaf pin a MEMBER of any interface pin on its cell? ---
# (regardless of wired state.) Externalizing such a scalar individually collides
# with externalizing its parent INTERFACE (Vivado [BD 41-1306] "connection ...
    # overridden") -- observed when scalar AXI pins were passed
# alongside S00_AXI. externalize_oos uses this to skip members and let the
# interface carry them.
proc ipiasm::_scalar_is_intf_member {pin} {
    set cell [get_bd_cells -quiet -of_objects [get_bd_pins -quiet $pin]]
    if {$cell eq ""} { return 0 }
    set leaf [file tail $pin]
    foreach ip [get_bd_intf_pins -quiet -of_objects $cell] {
        if {[string match "[file tail $ip]_*" $leaf]} { return 1 }
    }
    return 0
}

# Is this edge a BRAM controller-to-memory connection? gate_inventory requires every BRAM
# controller backed, so that one connection is made before the gate, not behind it.
proc ipiasm::_bram_edge {src dst} {
    foreach end [list $src $dst] {
        set pin [get_bd_intf_pins -quiet $end]
        if {$pin eq "" || ![string match "*:bram_rtl:*" [get_property -quiet VLNV $pin]]} { return 0 }
    }
    return 1
}

# --- connect two interface pins (idempotent) ---
# src/dst are "cell/PIN" strings (e.g. gmsl_camera_rx_0/pixel_out).
proc ipiasm::connect_intf {src dst} {
    if {![ipiasm::_bram_edge $src $dst] &&
        [set g [ipiasm::_need_inventory "connect_intf"]] ne ""} { return $g }
    set sp [get_bd_intf_pins -quiet $src]
    set dp [get_bd_intf_pins -quiet $dst]
    if {$sp eq ""} { return "INTF_FAIL:no source intf pin $src" }
    if {$dp eq ""} { return "INTF_FAIL:no dest intf pin $dst" }
    # already wired together? -> idempotent skip
    set sn [get_bd_intf_nets -quiet -of_objects $sp]
    set dn [get_bd_intf_nets -quiet -of_objects $dp]
    if {$sn ne "" && $sn eq $dn} { return "SKIP:already connected $src<->$dst" }
    # 2026.1+ pre-flight: clean machine-readable reason, no thrown error to parse.
    set pf [ipiasm::check_connect $src $dst]
    if {[string match "ILLEGAL:*" $pf]} { return "INTF_FAIL:illegal:[string range $pf 8 end]" }
    if {[catch {connect_bd_intf_net $sp $dp} e]} {
        return "INTF_FAIL:$e"
    }
    return "SUCCESS:intf $src -> $dst"
}

# --- connect two scalar pins (idempotent), e.g. clk/reset/interrupt nets ---
proc ipiasm::connect_net {src dst} {
    if {[set g [ipiasm::_need_inventory "connect_net"]] ne ""} { return $g }
    set sp [get_bd_pins -quiet $src]
    set dp [get_bd_pins -quiet $dst]
    if {$sp eq ""} { return "NET_FAIL:no source pin $src" }
    if {$dp eq ""} { return "NET_FAIL:no dest pin $dst" }
    set sn [get_bd_nets -quiet -of_objects $sp]
    set dn [get_bd_nets -quiet -of_objects $dp]
    if {$sn ne "" && $sn eq $dn} { return "SKIP:already connected $src<->$dst" }
    # 2026.1+ pre-flight: clean machine-readable reason, no thrown error to parse.
    set pf [ipiasm::check_connect $src $dst]
    if {[string match "ILLEGAL:*" $pf]} { return "NET_FAIL:illegal:[string range $pf 8 end]" }
    if {[catch {connect_bd_net $sp $dp} e]} {
        return "NET_FAIL:$e"
    }
    return "SUCCESS:net $src -> $dst"
}

# ============================================================
# Native connection introspection (Vivado 2026.1+, VIVADO-23126)
# Version-gated via ipcfg::has_native can_connect. Lets the assembler ENUMERATE
# legal targets and PRE-FLIGHT legality with a machine-readable reason instead of
# try-connect-then-parse-error. All return NATIVE_NA on 2025.2 so callers (and the
# pre-flight above) transparently fall back to the reactive path.
# ============================================================

# Judge a list of interface edges in one pass and report every missing or wrong route, not just the first.
proc ipiasm::check_interface_routes {edges} {
    if {[catch {llength $edges} count] || $count == 0} {
        return "INTERFACE_ROUTES_FAIL:nonempty edge list required"
    }
    # Every edge is judged in one pass. A connection stage that reports its first
    # broken edge and stops makes the caller rediscover the rest one source at a
    # time; the whole list is what tells them whether it is one wiring slip or a
    # wrong topology.
    if {[catch {
        set defects {}
        foreach edge $edges {
            if {[catch {llength $edge} width] || $width != 2} {
                lappend defects "each edge requires two exact interface paths, got {$edge}"
                continue
            }
            set resolved 1
            foreach role {source target} path $edge {
                if {[string trim $path] eq "" || [string trim $path /] eq ""
                    || [regexp {[*?\[\]\\]} $path]} {
                    lappend defects "invalid exact interface path: $path"
                    set resolved 0
                    continue
                }
                set path /[string trim $path /]
                set $role ""
                foreach getter {get_bd_intf_ports get_bd_intf_pins} {
                    foreach object [$getter -quiet $path] {
                        if {[string trim $object /] eq [string trim $path /]} {
                            set $role $object
                            break
                        }
                    }
                    if {[set $role] ne ""} { break }
                }
                if {[set $role] eq ""} {
                    lappend defects "no exact interface: $path"
                    set resolved 0
                }
            }
            if {!$resolved} { continue }
            set connected 0
            foreach peer [find_bd_objs -relation connected_to -thru_hier $source] {
                if {[string trim $peer /] eq [string trim $target /]} {
                    set connected 1
                    break
                }
            }
            if {!$connected} {
                # "Disconnected" alone does not say whether the edge is missing
                # or impossible. Ask the native check why, so a protocol
                # mismatch reads as one that needs a bridge rather than one that
                # needs another attempt at the same connect_bd_intf_net.
                set why [ipiasm::check_connect $source $target]
                set reason ""
                if {[string match "ILLEGAL:*" $why]} {
                    set reason " -- illegal: [string range $why 8 end]"
                } elseif {[string match "LEGAL:*" $why]} {
                    set reason " -- legal but unwired"
                }
                lappend defects "disconnected interface edge: $source -> $target$reason"
            }
        }
        if {[llength $defects]} { error [ipiasm::_defects $defects] }
    } detail]} {
        return "INTERFACE_ROUTES_FAIL:$detail"
    }
    return "INTERFACE_ROUTES:OK n=$count"
}

# Enumerate compatible INTERFACE destinations for an interface pin. Returns
# "INTF_TARGETS:<list of {path .. mode .. vlnv .. connected .. block ..}>" or
# NATIVE_NA / CC_ERR. Empty list => no legal peer exists (e.g. need to add a NoC).
proc ipiasm::legal_intf_targets {pin} {
    if {![ipcfg::has_native can_connect]} { return "NATIVE_NA" }
    if {[catch {bd::util_cmd can_connect_intf $pin} out]} { return "CC_ERR:$out" }
    return "INTF_TARGETS:$out"
}
# Enumerate compatible standalone PIN destinations (clk/rst/intr/data/ce),
# interface-members already filtered out by Vivado. Returns "PIN_TARGETS:<list>".
proc ipiasm::legal_pin_targets {pin} {
    if {![ipcfg::has_native can_connect]} { return "NATIVE_NA" }
    if {[catch {bd::util_cmd can_connect_pin $pin} out]} { return "CC_ERR:$out" }
    return "PIN_TARGETS:$out"
}
# Pre-flight one connection. src/dst are "cell/PIN" (scalar or interface) paths.
# Returns LEGAL:1 | ILLEGAL:<reason> | NATIVE_NA | CC_ERR:<e>.
proc ipiasm::check_connect {src dst} {
    if {![ipcfg::has_native can_connect]} { return "NATIVE_NA" }
    if {[catch {bd::util_cmd can_connect $src $dst} out]} { return "CC_ERR:$out" }
    # $out is a Tcl dict: "legal 0|1 [reason {...}]"
    if {[catch {dict get $out legal} lg]} { return "CC_ERR:unparsable:$out" }
    if {$lg == 1} { return "LEGAL:1" }
    set r ""; catch { set r [dict get $out reason] }
    return "ILLEGAL:$r"
}

# ---------------------------------------------------------------- same domain
# THE single implementation of "are these two interfaces synchronous?".
# preflight_interface_contracts, preflight_connect and disconnect_intf_edge all
# route through here, so a contract check and a topology change can never reach
# opposite conclusions about the same edge.
#
# Two proofs are accepted, in order:
#   net    - both associated clocks sit on ONE live clock net. Strongest.
#   domain - no shared net, but both publish the same CLK_DOMAIN and nothing
#            contradicts on FREQ_HZ.
# A matching nominal rate alone is deliberately NOT proof (REFERENCE, "Clock and
# reset domains": matching frequencies do not prove the domains are synchronous),
# which is why the CLK_DOMAIN strings must agree before the rate is even read.
proc ipiasm::_same_domain_verdict {src_metadata src_clock dst_metadata dst_clock} {
    set src_clock_net [get_bd_nets -quiet -of_objects $src_clock]
    set dst_clock_net [get_bd_nets -quiet -of_objects $dst_clock]
    set src_domain [ipiasm::_contract_domain $src_metadata $src_clock]
    set dst_domain [ipiasm::_contract_domain $dst_metadata $dst_clock]
    set src_hz [ipiasm::_contract_frequency $src_metadata $src_clock]
    set dst_hz [ipiasm::_contract_frequency $dst_metadata $dst_clock]
    set same [expr {$src_clock_net ne "" && $src_clock_net eq $dst_clock_net}]
    set proof net
    if {!$same && $src_domain ne "" && $src_domain eq $dst_domain
        && ($src_hz eq "" || $dst_hz eq "" || $src_hz eq $dst_hz)} {
        set same 1
        set proof domain
    }
    if {!$same} { set proof none }
    return [dict create same_domain $same proof $proof \
        src_clock_net $src_clock_net dst_clock_net $dst_clock_net \
        src_domain $src_domain dst_domain $dst_domain \
        src_hz $src_hz dst_hz $dst_hz]
}

# Resolve both endpoints' clocks, then apply the verdict above. `opts` is an
# optional contract dict; src_clk/dst_clk name a clock that a hierarchy boundary
# does not publish through ASSOCIATED_BUSIF. Returns the verdict dict plus
# `resolved`, or resolved=0 with a reason when no clock could be resolved --
# unresolved is not the same as asynchronous, and callers must not treat it so.
proc ipiasm::_edge_domain_facts {src dst {opts {}}} {
    set src_clk_path [ipiasm::_contract_scalar_path $opts src clk $src]
    set dst_clk_path [ipiasm::_contract_scalar_path $opts dst clk $dst]
    if {$src_clk_path eq "" || $dst_clk_path eq ""} {
        return [dict create resolved 0 reason \
            "no associated clock metadata (src_clk={$src_clk_path} dst_clk={$dst_clk_path});\
 name them explicitly with src_clk/dst_clk"]
    }
    set src_clock [ipiasm::_contract_scalar $src_clk_path]
    set dst_clock [ipiasm::_contract_scalar $dst_clk_path]
    if {$src_clock eq "" || $dst_clock eq ""} {
        return [dict create resolved 0 reason \
            "clock pin does not exist ($src_clk_path / $dst_clk_path)"]
    }
    set verdict [ipiasm::_same_domain_verdict $src $src_clock $dst $dst_clock]
    dict set verdict resolved 1
    dict set verdict src_clk $src_clk_path
    dict set verdict dst_clk $dst_clk_path
    return $verdict
}

# --- pre-flight ONE edge, before anything mutates -------------------------
# Stage 3 pre-flights edges with check_connect/legal_*_targets. This is that
# check as a single whitelisted plan operation, so an assembly plan can prove an
# edge legal BEFORE connect_intf/connect_net commits it. Strictly read-only.
#
# It is deliberately stricter than check_connect alone: Vivado will happily call
# an AXI-Stream edge legal across two unrelated clocks, because bus compatibility
# is all can_connect answers. The clock verdict is the half that catches a design
# which validates and then fails in hardware.
#
#   src/dst : "cell/PIN" interface or scalar paths, or top-level ports
#   opts    : optional contract dict -- src_clk/dst_clk to name clocks a
#             hierarchy boundary does not publish, allow_cdc 1 to accept a
#             genuine asynchronous crossing that a real CDC bridge spans.
# PREFLIGHT:OK <detail> | PREFLIGHT_FAIL:<reason>
proc ipiasm::preflight_connect {src dst {opts {}}} {
    if {[catch {dict size $opts}]} { return "PREFLIGHT_FAIL:opts is not a Tcl dict" }
    set si [ipiasm::_contract_intf $src]
    set di [ipiasm::_contract_intf $dst]
    set ss [ipiasm::_contract_scalar $src]
    set ds [ipiasm::_contract_scalar $dst]
    if {$si eq "" && $ss eq ""} { return "PREFLIGHT_FAIL:no such endpoint $src" }
    if {$di eq "" && $ds eq ""} { return "PREFLIGHT_FAIL:no such endpoint $dst" }
    # An interface pin and a scalar pin are not connectable to each other, and
    # saying so here is clearer than the error connect_bd_* would throw later.
    set src_kind [expr {$si ne "" ? "intf" : "scalar"}]
    set dst_kind [expr {$di ne "" ? "intf" : "scalar"}]
    if {$src_kind ne $dst_kind} {
        return "PREFLIGHT_FAIL:$src is $src_kind but $dst is $dst_kind"
    }

    set notes {}
    if {$src_kind eq "intf"} {
        set sn [get_bd_intf_nets -quiet -of_objects $si]
        set dn [get_bd_intf_nets -quiet -of_objects $di]
    } else {
        set sn [get_bd_nets -quiet -of_objects $ss]
        set dn [get_bd_nets -quiet -of_objects $ds]
    }
    set already [expr {$sn ne "" && $sn eq $dn}]
    if {$already} {
        lappend notes "already wired on $sn"
    } else {
        set legality [ipiasm::check_connect $src $dst]
        if {[string match "ILLEGAL:*" $legality]} {
            return "PREFLIGHT_FAIL:$src->$dst illegal:[string range $legality 8 end]"
        }
        if {[string match "CC_ERR:*" $legality]} {
            return "PREFLIGHT_FAIL:$src->$dst $legality"
        }
        lappend notes $legality
    }

    # Clock verdict: interface edges only. A scalar clk/rst/interrupt pin has no
    # associated clock of its own, so there is nothing here to prove.
    if {$src_kind eq "intf"} {
        set facts [ipiasm::_edge_domain_facts $si $di $opts]
        if {![dict get $facts resolved]} {
            lappend notes "clocks unresolved: [dict get $facts reason]"
        } else {
            set same [dict get $facts same_domain]
            set allow_cdc [expr {[dict exists $opts allow_cdc]
                                 && [dict get $opts allow_cdc]}]
            if {!$same && !$allow_cdc} {
                set unclocked [expr {[dict get $facts src_clock_net] eq "" && [dict get $facts dst_clock_net] eq ""}]
                return "PREFLIGHT_FAIL:$src->$dst requires CDC:\
clocks={[dict get $facts src_clock_net],[dict get $facts dst_clock_net]}\
 domains={[dict get $facts src_domain],[dict get $facts dst_domain]}\
 freq={[dict get $facts src_hz],[dict get $facts dst_hz]}[expr {$unclocked ? { -- neither interface's associated clock pin is connected, so these are default frequencies: connect both associated clock pins to one existing clock pin first (in a scratch design: a raw connect_bd_net guarded by an empty get_bd_nets check; FREQ_HZ edits do not change the domain), then rerun} : {}}]"
            }
            lappend notes "same_domain=$same proof=[dict get $facts proof]"
            if {!$same} { lappend notes "allow_cdc=1 accepted by caller" }
        }
    }
    return "PREFLIGHT:OK $src->$dst {[join $notes {; }]}"
}

# --- remove ONE interface edge, when that is a real topology change -------
# The mutation counterpart to the verdict above. Two refusals, both deliberate:
#
#  * A NoC ingress edge is never removed here. Deleting only the interface net
#    leaves the logical CONFIG.CONNECTIONS route, its QoS declaration and the
#    Address Editor mapping behind, so the design keeps a route it no longer has
#    a wire for -- and still validates. plan_remove_noc_path / remove_noc_path
#    clear that state properly, so this points you there instead. No `force`
#    bypasses it: the hazard is orphaned state, not an unjustified intent.
#
#  * A PROVEN same-domain edge needs a stated reason. Same-domain is exactly the
#    case where tearing the edge out to insert a CDC bridge buys nothing and
#    costs latency and area, so `force` is the caller's justification string and
#    it is recorded, the way gate_override records the user's words. It is for a
#    separately justified non-CDC topology change; it is not an argument against
#    the verdict, which stays in the return either way.
#
# An unresolved or asynchronous edge disconnects without ceremony -- there is no
# proven synchronous relationship to protect.
# SUCCESS:disconnected .. | SKIP:.. | DISCONNECT_REFUSED:.. | DISCONNECT_FAIL:..
proc ipiasm::disconnect_intf_edge {src dst {force ""}} {
    if {[set g [ipiasm::_need_inventory "disconnect_intf_edge"]] ne ""} { return $g }
    set si [ipiasm::_contract_intf $src]
    set di [ipiasm::_contract_intf $dst]
    if {$si eq ""} { return "DISCONNECT_FAIL:no source intf $src" }
    if {$di eq ""} { return "DISCONNECT_FAIL:no destination intf $dst" }
    set sn [get_bd_intf_nets -quiet -of_objects $si]
    set dn [get_bd_intf_nets -quiet -of_objects $di]
    if {$sn eq "" || $sn ne $dn} {
        return "SKIP:$src and $dst are not on one interface net (src={$sn} dst={$dn})"
    }

    set noc_cells [ipiasm::_all_noc_cells]
    foreach {end path} [list $si $src $di $dst] {
        set owner [get_bd_cells -quiet -of_objects $end]
        if {$owner ne "" && [lsearch -exact $noc_cells "$owner"] >= 0} {
            return "DISCONNECT_REFUSED:$path is on NoC cell $owner -- removing the\
 interface net would orphan its CONFIG.CONNECTIONS route, QoS and address\
 mapping; use ipiasm::plan_remove_noc_path then ipiasm::remove_noc_path -apply"
        }
    }

    set facts [ipiasm::_edge_domain_facts $si $di]
    set verdict "clocks unresolved"
    set same 0
    if {[dict get $facts resolved]} {
        set same [dict get $facts same_domain]
        set verdict "same_domain=$same proof=[dict get $facts proof]\
 clocks={[dict get $facts src_clock_net],[dict get $facts dst_clock_net]}\
 domains={[dict get $facts src_domain],[dict get $facts dst_domain]}"
    }
    if {$same && [string trim $force] eq ""} {
        return "DISCONNECT_REFUSED:$src->$dst is a proven synchronous edge\
 ($verdict). Pass the justification for this topology change as the third\
 argument. Inserting a CDC bridge is not one -- these endpoints already share a\
 clock."
    }
    if {[catch {delete_bd_objs $sn} e]} {
        return "DISCONNECT_FAIL:[lindex [split $e \n] 0]"
    }
    set why ""
    if {[string trim $force] ne ""} { set why " reason={[string trim $force]}" }
    return "SUCCESS:disconnected $src->$dst net=$sn ($verdict)$why --\
 addresses and QoS downstream of this edge are now stale; rerun the address\
 stage and final_contract_gate before delivery"
}

# Keep propagated interface metadata coherent after a clock-domain choice.
# Either endpoint may be an internal interface pin or a top-level interface.
proc ipiasm::sync_intf_freq_hz {source target} {
    set src [get_bd_intf_ports -quiet $source]
    if {$src eq ""} { set src [get_bd_intf_pins -quiet $source] }
    set dst [get_bd_intf_ports -quiet $target]
    if {$dst eq ""} { set dst [get_bd_intf_pins -quiet $target] }
    if {$src eq ""} { return "FREQ_SYNC_FAIL:no interface $source" }
    if {$dst eq ""} { return "FREQ_SYNC_FAIL:no interface $target" }
    set freq ""
    catch {set freq [get_property -quiet CONFIG.FREQ_HZ $src]}
    if {$freq eq ""} { catch {set freq [get_property -quiet FREQ_HZ $src]} }
    # A frequency Vivado holds as a double reads back in %g form (2.66525e+08).
    if {![string is entier -strict $freq] && [string is double -strict $freq]} { set freq [expr {round($freq)}] }
    if {$freq eq "" || ![string is entier -strict $freq] || $freq <= 0} {
        return "FREQ_SYNC_FAIL:$source has no positive FREQ_HZ"
    }
    if {[catch {set_property CONFIG.FREQ_HZ $freq $dst} e]} {
        return "FREQ_SYNC_FAIL:$target :: [lindex [split $e \n] 0]"
    }
    set got [get_property -quiet CONFIG.FREQ_HZ $dst]
    if {$got ne $freq} {
        return "FREQ_SYNC_FAIL:$target reads $got after asking for $freq"
    }
    return "FREQ_SYNC:OK ${source}($freq) -> $target"
}

# --- AUTOMATION-FIRST: connect an AXI master to a slave ---
# Tries the axi4 connection-automation rule (auto-inserts smartconnect/NoC and
# assigns the address). Only if no rule applies (or it errors) does it fall back
# to a manual connect_intf. `rule` is the axi4 bd_rule from the dump; pass "" to
# skip straight to manual. master/slave are "cell/PIN" intf pins.
proc ipiasm::connect_axi_auto {master slave {rule ""} {config {}}} {
    if {[set g [ipiasm::_need_inventory "connect_axi_auto"]] ne ""} { return $g }
    if {$rule ne ""} {
        set sl [get_bd_intf_pins -quiet $slave]
        set cfg $config
        if {[llength $cfg] == 0} { set cfg [list Master $master] }
        set r [ipiasm::connect_automation $slave $rule $cfg]
        if {[string match "*ATTEMPT_*" $r]} { return $r }
        if {[string match "SUCCESS:*" $r]} { return "AUTO:$r" }
        # fall through to manual on automation failure
    }
    set m [ipiasm::connect_intf $master $slave]
    return "MANUAL:$m"
}

# --- connect a top-level AXI ingress to an internal slave ---
# Vivado's axi4 automation does not always admit a top-level port as Master.
# Try the supplied rule/config first; if it refuses, build one SmartConnect as
# the explicit fallback. No design-specific pin, name, clock or reset is baked
# into this operation.
proc ipiasm::connect_external_axi {port slave {rule ""} {config {}} \
                                   {sc_cell external_axi_sc} \
                                   {clk_pin ""} {rst_pin ""}} {
    if {[set g [ipiasm::_need_inventory "connect_external_axi"]] ne ""} { return $g }
    set ext [get_bd_intf_ports -quiet $port]
    if {$ext eq ""} { return "EXT_AXI_FAIL:no top-level interface port $port" }
    set dst [get_bd_intf_pins -quiet $slave]
    if {$dst eq ""} { return "EXT_AXI_FAIL:no internal slave pin $slave" }
    if {$rule ne ""} {
        set obj $dst
        if {![catch {ipcfg::attempt_call [file dirname $slave] connection_automation {
            if {[llength $config]} {
                apply_bd_automation -rule $rule -config $config $obj
            } else {
                apply_bd_automation -rule $rule $obj
            }
        }} auto_result]} {
            if {[llength [get_bd_intf_nets -quiet -of_objects $ext]]
                && [llength [get_bd_intf_nets -quiet -of_objects $dst]]} {
                return "EXT_AXI:OK automation=$rule port=$port slave=$slave"
            }
        }
        if {[string match "*ATTEMPT_*" $auto_result]} { return $auto_result }
    }

    set made [ipcfg::create_cell xilinx.com:ip:smartconnect $sc_cell]
    if {[string match "*FAIL:*" $made]} { return "EXT_AXI_FAIL:$made" }
    set sc [get_bd_cells -quiet $sc_cell]
    if {[catch {
        set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI 1] $sc
        connect_bd_intf_net $ext [get_bd_intf_pins $sc_cell/S00_AXI]
        connect_bd_intf_net [get_bd_intf_pins $sc_cell/M00_AXI] $dst
        if {$clk_pin ne ""} {
            set clk [get_bd_pins -quiet $clk_pin]
            if {$clk eq ""} { error "no clock pin $clk_pin" }
            set sc_clk [get_bd_pins $sc_cell/aclk]
            set src_net [get_bd_nets -quiet -of_objects $clk]
            set dst_net [get_bd_nets -quiet -of_objects $sc_clk]
            if {$dst_net eq ""} {
                connect_bd_net $clk $sc_clk
            } elseif {$src_net eq "" || $dst_net ne $src_net} {
                error "$sc_cell/aclk is already on $dst_net, not $clk"
            }
            set freq ""
            catch {set freq [get_property -quiet CONFIG.FREQ_HZ $clk]}
            if {$freq eq ""} { catch {set freq [get_property -quiet FREQ_HZ $clk]} }
            if {$freq ne ""} { catch {set_property CONFIG.FREQ_HZ $freq $ext} }
        }
        if {$rst_pin ne ""} {
            set rst [get_bd_pins -quiet $rst_pin]
            if {$rst eq ""} { error "no reset pin $rst_pin" }
            set sc_rst [get_bd_pins $sc_cell/aresetn]
            set src_net [get_bd_nets -quiet -of_objects $rst]
            set dst_net [get_bd_nets -quiet -of_objects $sc_rst]
            if {$dst_net eq ""} {
                connect_bd_net $rst $sc_rst
            } elseif {$src_net eq "" || $dst_net ne $src_net} {
                error "$sc_cell/aresetn is already on $dst_net, not $rst"
            }
        }
    } e]} {
        return "EXT_AXI_FAIL:fallback $sc_cell :: [lindex [split $e \n] 0]"
    }
    set why ""
    if {[info exists auto_result] && $auto_result ne ""} {
        set why " automation_refused={[lindex [split $auto_result \n] 0]}"
    }
    return "EXT_AXI:OK fallback=$sc_cell port=$port slave=$slave$why"
}

# --- AUTOMATION-FIRST: build/clock a clock+reset off a source clock pin ---
# Applies the clkrst rule to a clock source pin (e.g. a PS/clock-wizard clk out).
# The rule auto-creates a proc_sys_reset and wires slowest_sync_clk/ext_reset for
# you -- the correct Versal pattern and what bd.tcl was generated from. Manual
# build_clocks + fanout_domain remain available as the FALLBACK when no clkrst
# rule is present on this install.
#   clk_pin: a clock SOURCE pin (cell/PIN)   rule: clkrst bd_rule from the dump
proc ipiasm::wire_clk_rst_auto {clk_pin rule {config {}}} {
    if {[set g [ipiasm::_need_inventory "wire_clk_rst_auto"]] ne ""} { return $g }
    if {$rule eq ""} { return "CLK_FAIL:no clkrst rule supplied (use build_clocks fallback)" }
    return [ipiasm::connect_automation $clk_pin $rule $config]
}

# --- connection-automation: apply a connection rule discovered via the dump ---
# The rules dump (see ipcfg::rule_for_vlnv) also enumerates connection rules
# (axi4, clkrst, board, ...). apply_bd_automation drives them from a pin with a
# -config {Master <m> Slave <s> ...} payload. This wraps it and reports new cells
# (e.g. a smartconnect/interconnect the rule may auto-insert).
#   pin:    the bd intf pin the rule is applied to (cell/PIN)
#   rule:   xilinx.com:bd_rule:<short>
#   config: {Key Value ...} from the rule's options
proc ipiasm::connect_automation {pin rule {config {}}} {
    if {[set g [ipiasm::_need_inventory "connect_automation"]] ne ""} { return $g }
    set before [get_bd_cells -quiet]
    set obj [get_bd_intf_pins -quiet $pin]
    if {$obj eq ""} { set obj [get_bd_pins -quiet $pin] }
    if {$obj eq ""} { return "AUTO_FAIL:no pin $pin" }
    if {[catch {ipcfg::attempt_call [file dirname $pin] connection_automation {
        if {[llength $config] > 0} {
            apply_bd_automation -rule $rule -config $config $obj
        } else {
            apply_bd_automation -rule $rule $obj
        }
    }} e]} {
        return "AUTO_FAIL:$e"
    }
    set new {}
    foreach c [get_bd_cells -quiet] {
        if {[lsearch -exact $before $c] < 0} { lappend new $c }
    }
    return "SUCCESS:automation $rule on $pin new_cells={$new}"
}

# ============================================================
# 1b. Common control-plane pattern: one master -> N control slaves
# ============================================================
# Validated against PG247 (SmartConnect): a single SI can fan out to many MIs
# (up to 64 AXI4-Lite), with one aclk/aresetn per clock domain. This builds the
# control interconnect the spec implies (a CPU/PS M_AXI driving every IP's
# S_AXI_LITE) in ONE deterministic helper instead of N ad-hoc connects.
#   master:   the control master intf pin (cell/PIN, e.g. cips/M_AXI_FPD)
#   slaves:   list of control slave intf pins (cell/PIN ...)
#   sc_cell:  smartconnect instance name to create (default control_sc)
#   sc_vlnv:  version-free smartconnect VLNV
#   clk_pin:  optional clock SOURCE pin for the SmartConnect aclk ("" = leave to
#             the domain builder/autofix)
#   rst_pin:  optional reset SOURCE pin for aresetn ("" = leave)
# Wires master->S00_AXI and M0n_AXI->slave[n]; sizes NUM_MI to the slaves that
# actually have pins (no dangling MI ports). Idempotent: re-running skips nets
# already in place. NOTE: a single-clock SmartConnect does NOT clock-convert --
# for a master and slaves in DIFFERENT clock domains use the axi4 connection
# automation (it inserts a properly-clocked converter) rather than this same-
# domain fan-out. Returns SUCCESS with the created cell, wired count, clkrst
# wiring, and a WARN if aclk is left open.
proc ipiasm::build_control_fanout {master slaves {sc_cell control_sc} {sc_vlnv xilinx.com:ip:smartconnect} {clk_pin ""} {rst_pin ""}} {
    if {[set g [ipiasm::_need_inventory "build_control_fanout"]] ne ""} { return $g }
    if {![ipcfg::vlnv_ok $sc_vlnv]} { return "CTRL_FAIL:no smartconnect ipdef for $sc_vlnv" }
    set mp [get_bd_intf_pins -quiet $master]
    if {$mp eq ""} { return "CTRL_FAIL:no master intf pin $master" }
    if {[llength $slaves] == 0} { return "CTRL_FAIL:no control slaves given" }

    # Pre-resolve which slaves actually have a pin so NUM_MI matches the number
    # we will really wire -- sizing NUM_MI to the raw request left dangling MI
    # ports (a validate critical) whenever a slave was missing.
    set valid {}; set skipped {}
    foreach s $slaves {
        if {[get_bd_intf_pins -quiet $s] ne ""} { lappend valid $s } else { lappend skipped "${s}(no pin)" }
    }
    set nmi [llength $valid]
    if {$nmi == 0} { return "CTRL_FAIL:no valid control slave pins ({$skipped})" }
    if {$nmi > 64} { return "CTRL_FAIL:$nmi slaves exceeds SmartConnect MI max (64, PG247)" }

    set r [ipcfg::create_cell $sc_vlnv $sc_cell]
    if {[string match "CONFIGURE_FAIL:*" $r]} { return "CTRL_FAIL:$r" }
    catch { set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI $nmi] [get_bd_cells $sc_cell] }

    # master -> S00_AXI (idempotent: skip if already on the same net)
    set s00 [get_bd_intf_pins -quiet $sc_cell/S00_AXI]
    set mn [get_bd_intf_nets -quiet -of_objects $mp]
    set sn [get_bd_intf_nets -quiet -of_objects $s00]
    if {!($mn ne "" && $mn eq $sn)} {
        if {[catch {connect_bd_intf_net $mp $s00} e]} { return "CTRL_FAIL:master->S00_AXI: $e" }
    }

    set wired {}
    set i 0
    foreach s $valid {
        set sp [get_bd_intf_pins -quiet $s]
        set mi [get_bd_intf_pins -quiet [format "%s/M%02d_AXI" $sc_cell $i]]
        set en [get_bd_intf_nets -quiet -of_objects $sp]
        if {$mi eq ""} {
            lappend skipped "${s}(no M[format %02d $i]_AXI)"
        } elseif {$en ne "" && $en eq [get_bd_intf_nets -quiet -of_objects $mi]} {
            lappend wired "M[format %02d $i]_AXI->${s}(already)"
            incr i
        } elseif {[catch {connect_bd_intf_net $mi $sp} e]} {
            lappend skipped "${s}($e)"
        } else {
            lappend wired "M[format %02d $i]_AXI->$s"
            incr i
        }
    }

    # Optional clock/reset wiring. A single-clock SmartConnect does NOT clock-
    # convert; if the master and slaves are in DIFFERENT clock domains, use the
    # axi4 connection-automation rule (which inserts a properly-clocked
    # converter) instead of this same-domain fan-out. We wire only what the
    # caller passes and otherwise flag aclk as still-open.
    set clkrst {}
    if {$clk_pin ne ""} {
        set ap [get_bd_pins -quiet $sc_cell/aclk]
        set cp [get_bd_pins -quiet $clk_pin]
        if {$ap ne "" && $cp ne ""} {
            set an [get_bd_nets -quiet -of_objects $ap]
            set cn [get_bd_nets -quiet -of_objects $cp]
            if {$an eq "" || $cn eq "" || $an ne $cn} { catch {connect_bd_net $cp $ap} }
            lappend clkrst "aclk<-$clk_pin"
        }
    }
    if {$rst_pin ne ""} {
        set rp [get_bd_pins -quiet $sc_cell/aresetn]
        set xp [get_bd_pins -quiet $rst_pin]
        if {$rp ne "" && $xp ne ""} {
            set rn [get_bd_nets -quiet -of_objects $rp]
            set xn [get_bd_nets -quiet -of_objects $xp]
            if {$rn eq "" || $xn eq "" || $rn ne $xn} { catch {connect_bd_net $xp $rp} }
            lappend clkrst "aresetn<-$rst_pin"
        }
    }
    # A single-clock fan-out runs every slave on its own clock, so each slave interface's
    # clock (the pin whose ASSOCIATED_BUSIF names it) and that clock's ASSOCIATED_RESET are
    # driven from the same clock and reset, when nothing drives them yet.
    set endpoints {}
    set cp [expr {$clk_pin eq "" ? "" : [get_bd_pins -quiet $clk_pin]}]
    set xp [expr {$rst_pin eq "" ? "" : [get_bd_pins -quiet $rst_pin]}]
    if {$cp ne ""} {
        foreach s $valid {
            set cell [get_bd_cells -quiet -of_objects [get_bd_intf_pins -quiet $s]]
            if {$cell eq ""} { continue }
            # Vivado resolves interface names case-insensitively (S_AXI finds s_axi), so match
            # the association the same way.
            set busif [string tolower [file tail $s]]
            foreach pin [get_bd_pins -quiet -of_objects $cell] {
                if {[get_property -quiet TYPE $pin] ne "clk" || [get_property -quiet DIR $pin] ne "I"} { continue }
                set associated [split [string tolower [get_property -quiet CONFIG.ASSOCIATED_BUSIF $pin]] :]
                if {$busif ni $associated} { continue }
                if {![ipiasm::_net_wired $pin] && ![catch {connect_bd_net $cp $pin}]} {
                    lappend endpoints [string trimleft $pin /]
                }
                if {$xp eq ""} { continue }
                foreach name [split [get_property -quiet CONFIG.ASSOCIATED_RESET $pin] :] {
                    set rp [get_bd_pins -quiet $cell/$name]
                    if {$rp ne "" && ![ipiasm::_net_wired $rp] && ![catch {connect_bd_net $xp $rp}]} {
                        lappend endpoints [string trimleft $rp /]
                    }
                }
            }
        }
    }
    set aclk_open [expr {[get_bd_pins -quiet $sc_cell/aclk] ne "" && ![ipiasm::_net_wired [get_bd_pins -quiet $sc_cell/aclk]]}]

    set out "SUCCESS:control_fanout $sc_cell SI=1 MI=$nmi wired=[llength $wired]"
    if {[llength $clkrst] > 0} { append out " clkrst={$clkrst}" }
    if {[llength $endpoints] > 0} { append out " endpoint_clkrst=[llength $endpoints]" }
    if {$aclk_open}            { append out " WARN:aclk-open(wire via domain builder or use axi4 automation for cross-domain)" }
    if {[llength $skipped] > 0} { append out " skipped={$skipped}" }
    return $out
}

# ============================================================
# 1c. NoC soft-master attach (Versal axi_noc / axi_noc2)
# ============================================================
# Validated against PG313 (NoC General/Connectivity tabs) + UG1387 (QoS via
# set_property READ_BANDWIDTH/WRITE_BANDWIDTH on the connection). Growing a NoC to
# accept a new PL "soft" master means: bump NUM_SI by one, route the new S<k>_AXI
# to a destination column in the connectivity matrix, then connect + (optionally)
# tag QoS bandwidth. Returns the new S<k>_AXI pin name so the caller can wire it.
#   noc_cell:  the axi_noc cell
#   dest:      connectivity target for the new SI (e.g. MC_0 or M00_AXI)
#   master:    optional master intf pin (cell/PIN) to connect into the new SI ("" = leave for wiring)
#   rd_bw/wr_bw: optional QoS bandwidth in MB/s ("" = NoC defaults)

# Add a NoC slave interface for one PL master and route it to dest with read/write bandwidth; the port gets its own aclk on the master's clock.
proc ipiasm::add_noc_si {noc_cell dest {master ""} {rd_bw ""} {wr_bw ""}} {
    if {[set g [ipiasm::_need_inventory "add_noc_si"]] ne ""} { return $g }
    set nc [get_bd_cells -quiet $noc_cell]
    if {$nc eq ""} { return "NOC_FAIL:no noc cell $noc_cell" }
    if {[catch {set cur [get_property -quiet CONFIG.NUM_SI $nc]}]} { set cur "" }
    if {![string is integer -strict $cur]} { set cur 0 }
    set k $cur
    set si [format "S%02d_AXI" $k]
    set new [expr {$cur + 1}]
    if {[catch {set_property CONFIG.NUM_SI $new $nc} e]} { return "NOC_FAIL:NUM_SI=$new: $e" }

    # Route the new ingress to the requested destination. CONFIG.CONNECTIONS is a
    # property of the *interface pin*, not of the cell -- setting it on the cell
    # is accepted and silently does nothing, which is what made this look like an
    # undiscoverable schema. ipiasm::set_qos owns the dict merge and carries the
    # caller's QoS bandwidth (UG994) so the declared rate is actually applied.
    set rb [expr {$rd_bw ne "" ? $rd_bw : 100}]
    set wb [expr {$wr_bw ne "" ? $wr_bw : 100}]
    if {$dest ne ""} {
        set r [ipiasm::set_qos $noc_cell/$si $dest $rb $wb]
        if {![string match "SUCCESS:*" $r]} {
            return "NOC_FAIL:connectivity $si->$dest: $r"
        }
    }
    # optional master wire-in
    set wired ""
    set width ""
    if {$master ne ""} {
        set mp [get_bd_intf_pins -quiet $master]
        set sp [get_bd_intf_pins -quiet $noc_cell/$si]
        if {$mp eq ""} { return "NOC_FAIL:no master interface pin $master" }
        if {$sp eq ""} { return "NOC_FAIL:no new NoC interface pin $noc_cell/$si" }
        catch {set width [get_property -quiet CONFIG.DATA_WIDTH $mp]}
        if {$width eq ""} { catch {set width [get_property -quiet DATA_WIDTH $mp]} }
        if {$width ne "" && [string is integer -strict $width] && $width > 0} {
            if {[catch {set_property CONFIG.DATA_WIDTH $width $sp} e]} {
                return "NOC_FAIL:$si DATA_WIDTH=$width: [lindex [split $e \n] 0]"
            }
            set got [get_property -quiet CONFIG.DATA_WIDTH $sp]
            if {$got ne $width} {
                return "NOC_FAIL:$si DATA_WIDTH reads $got, master $master requires $width"
            }
        }
        if {[catch {connect_bd_intf_net $mp $sp} e]} {
            return "NOC_FAIL:wire $master->$si: [lindex [split $e \n] 0]"
        }
        set wired "wired:$master->$si width=[expr {$width eq "" ? "unknown" : $width}]"
        set clock [ipiasm::_attach_noc_port_clock $noc_cell $si $mp]
        if {[string match "NOC_FAIL:*" $clock]} { return $clock }
        append wired " $clock"
    }
    # A NoC SI must carry its master's category (BD 41-2331). A hardened master
    # states its own in MY_CATEGORY (its CATEGORY names the peer type); only a
    # master that declares none is a PL soft master.
    set qos ""
    set category ""
    if {$master ne ""} {
        catch {set category [get_property -quiet CONFIG.MY_CATEGORY [get_bd_intf_pins -quiet $master]]}
    }
    if {$category eq "" && ($rd_bw ne "" || $wr_bw ne "")} { set category pl }
    if {$category ne ""} {
        catch { set_property CONFIG.CATEGORY $category [get_bd_intf_pins -quiet $noc_cell/$si] }
    }
    if {$rd_bw ne "" || $wr_bw ne ""} {
        set qos "qos(rd=$rb wr=$wb)"
    }
    if {$category ne ""} { append qos " category=$category" }
    return "SUCCESS:noc $noc_cell +SI $si -> $dest (NUM_SI $cur->$new) $wired $qos"
}

# ============================================================
# 2. Clock / reset domain builder (MANUAL FALLBACK)
# ============================================================
# Prefer ipiasm::wire_clk_rst_auto (clkrst automation) first. Use these only
# when no clkrst rule applies, or to pre-stage a shared multi-output wizard that
# fans several PL clocks from one generated wizard.

# --- build one clock wizard + one proc_sys_reset per domain ---
# domains: a list of {name freq_mhz ...} pairs OR {name freq reset_cell} triples
# (the manifest's clock_domains). Creates:
#   * one clock wizard with a CLKOUT_PORT list = the distinct domain frequencies
#     (named clk_<MHz>).
#   * one proc_sys_reset_<MHz> per domain.
# Returns "SUCCESS:clocks={...} resets={...}" or *_FAIL. IP-agnostic VLNVs are
# resolved with ipcfg::vlnv_ok so no version is hardcoded.
proc ipiasm::build_clocks {domains {wiz_vlnv xilinx.com:ip:clk_wizard} {rst_vlnv xilinx.com:ip:proc_sys_reset} {wiz_cell ipiasm_clk_wiz}} {
    if {[set g [ipiasm::_need_inventory "build_clocks"]] ne ""} { return $g }
    variable clk_wizard
    if {![ipcfg::vlnv_ok $wiz_vlnv]} { return "CLK_FAIL:no clock wizard ipdef for $wiz_vlnv" }
    if {![ipcfg::vlnv_ok $rst_vlnv]} { return "CLK_FAIL:no reset ipdef for $rst_vlnv" }

    # distinct frequencies in declared order -> CLKOUT port names
    set freqs {}
    foreach {name freq} $domains {
        set mhz [expr {int(round($freq))}]
        if {[lsearch -exact $freqs $mhz] < 0} { lappend freqs $mhz }
    }
    set ports {}
    foreach m $freqs { lappend ports "clk_$m" }

    set r [ipcfg::create_cell $wiz_vlnv $wiz_cell]
    if {[string match "CONFIGURE_FAIL:*" $r]} { return "CLK_FAIL:$r" }
    set clk_wizard $wiz_cell

    # Enable + name the output clocks. Wizard param schemas DIFFER, so apply both
    # known shapes in tolerant catches (no IP-specific value is mandatory):
    #   * some wizard variants: comma-LISTS CLKOUT_USED / CLKOUT_REQUESTED_OUT_FREQUENCY
    #     gate which clk_<mhz> pins actually appear (verified: CLKOUT_PORT alone is
    #     NOT enough -- CLKOUT_USED must mark each output used).
    #   * clk_wizard (UltraScale): per-index CLKOUT<i>_REQUESTED_OUT_FREQ.
    # We then report the clk_<mhz> pins that ACTUALLY materialized, not the
    # requested list, and flag any requested port that did not appear.
    set used {}
    set reqf {}
    foreach m $freqs { lappend used true; lappend reqf "$m.000" }
    catch { set_property CONFIG.CLKOUT_PORT [join $ports ,] [get_bd_cells $wiz_cell] }
    catch { set_property CONFIG.CLKOUT_USED [join $used ,] [get_bd_cells $wiz_cell] }
    catch { set_property CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY [join $reqf ,] [get_bd_cells $wiz_cell] }
    set i 1
    foreach m $freqs {
        catch { set_property CONFIG.CLKOUT${i}_REQUESTED_OUT_FREQ $m.000 [get_bd_cells $wiz_cell] }
        incr i
    }

    # Report the clocks that actually exist on the wizard (truthful, not requested).
    set made_ports {}
    set missing {}
    foreach m $freqs {
        if {[get_bd_pins -quiet $wiz_cell/clk_$m] ne ""} {
            lappend made_ports clk_$m
        } else {
            lappend missing clk_$m
        }
    }

    set resets {}
    foreach {name freq} $domains {
        set mhz [expr {int(round($freq))}]
        set rc "proc_sys_reset_$mhz"
        if {[lsearch -exact $resets $rc] >= 0} continue
        set rr [ipcfg::create_cell $rst_vlnv $rc]
        if {[string match "CONFIGURE_FAIL:*" $rr]} { return "CLK_FAIL:$rr" }
        lappend resets $rc
    }
    set out "SUCCESS:clocks={$made_ports on $wiz_cell} resets={$resets}"
    if {[llength $missing] > 0} {
        append out " WARN:not_created={$missing} (doc-discover this wizard's output-enable param or use wire_clk_rst_auto)"
    }
    return $out
}

# --- fan a domain's clock+reset out to a list of cell clock/reset pins ---
# Connects clk_<mhz> from the wizard and the matching proc_sys_reset's
# peripheral_aresetn to every (cell pin) pair given. Each pair is {cell clk_pin
# rst_pin}; rst_pin may be "" to skip reset. Also drives the reset block's
# slowest_sync_clk + ext_reset_in if those are still floating.
#   mhz:   domain frequency (selects clk_<mhz> + proc_sys_reset_<mhz>)
#   triples: {cell clkpin rstpin  cell clkpin rstpin ...}
proc ipiasm::fanout_domain {mhz triples} {
    if {[set g [ipiasm::_need_inventory "fanout_domain"]] ne ""} { return $g }
    variable clk_wizard
    if {$clk_wizard eq ""} { return "RST_FAIL:build_clocks not run yet" }
    set clkpin "$clk_wizard/clk_$mhz"
    if {[get_bd_pins -quiet $clkpin] eq ""} { return "RST_FAIL:no $clkpin" }
    set rc "proc_sys_reset_$mhz"
    if {[get_bd_cells -quiet $rc] eq ""} { return "RST_FAIL:no reset cell $rc" }

    # wire the reset block's clock + ext reset once (idempotent)
    catch { connect_bd_net [get_bd_pins $clkpin] [get_bd_pins $rc/slowest_sync_clk] }
    set rstout [get_bd_pins -quiet $rc/peripheral_aresetn]

    set done {}
    foreach {cell cpin rpin} $triples {
        if {$cpin ne "" && [get_bd_pins -quiet $cell/$cpin] ne ""} {
            catch { connect_bd_net [get_bd_pins $clkpin] [get_bd_pins $cell/$cpin] }
        }
        if {$rpin ne "" && $rstout ne "" && [get_bd_pins -quiet $cell/$rpin] ne ""} {
            catch { connect_bd_net $rstout [get_bd_pins $cell/$rpin] }
        }
        lappend done $cell
    }
    return "SUCCESS:domain ${mhz}MHz fanned to {$done}"
}

# ============================================================
# 3. Address map
# ============================================================

# --- automatic assignment of all unmapped masters ---
# assign_bd_address returns rc=0 whether or not it mapped anything, and the
# unassigned-segment list it leaves behind reads EMPTY in two opposite states:
# everything got mapped, and there was nothing to map because the DRAM windows
# do not exist. A design can carry enabled memory controllers with all
# four LPDDR5 controllers bound to the board and zero published DDR segments
# answered "SUCCESS:auto_address done unassigned={}" on every call while every
# master's address space stayed empty. The agent believed it and moved on.
# So the claim of success is checked where it is made: if any NoC in the design
# asked for a memory controller, a DRAM window has to exist afterwards.
proc ipiasm::auto_address {} {
    if {[set g [ipiasm::_need inventory "auto_address"]] ne ""} { return $g }
    if {[llength [ipiasm::_all_noc_cells]] && [set g [ipiasm::_need qos "auto_address"]] ne ""} { return $g }
    set want_mc [ipiasm::_requested_mc]
    if {[catch {assign_bd_address} e]} {
        return "ADDR_FAIL:$e"
    }
    set unassigned [ipiasm::_unassigned_segs]
    set ddr [ipiasm::_noc_ddr_segs]
    if {$want_mc > 0 && ![llength $ddr]} {
        return "ADDR_FAIL:assign_bd_address returned rc=0 but there was no DRAM\
window to map. NUM_MC=$want_mc is requested across this design's NoC(s) and no\
DDR/HBM segment exists on any of them, so every master's address space is still\
without DRAM and calling this again changes nothing.\n  -> the window is missing\
UPSTREAM of addressing, so fix that first: ipiasm::check_noc_legality (an\
illegal NoC route stops the compiler from publishing the memory apertures) and\
ipiasm::check_memory_subsystem $want_mc. Re-address once they are clean."
    }
    # A published window is not a reached window. assign_bd_address maps what it
    # can reach, skips what it cannot, and returns rc=0 either way
    # one observed design mapped every peripheral register and no memory, and said
    # success. So the useful number here is how many masters came out of it
    # holding DRAM, not how many windows exist.
    set on_dram [ipiasm::_dram_masters]
    if {$want_mc > 0 && [llength $ddr] && ![llength $on_dram]} {
        set why ""
        set unbraced [ipiasm::_unbraced_noc_decls]
        if {[llength $unbraced]} {
            set why "\n  -> the traffic spec on [join $unbraced {, }] is written\
with unbraced values (read_bw 2000 rather than read_bw {2000}); axi_noc2 accepts\
that form and builds no route for it, so there is no path for the window to be\
mapped through. Rewrite with ipiasm::set_qos / apply_qos_plan and re-address."
        } else {
            set why "\n  -> the window exists but nothing routes to it. Check\
ipiasm::check_noc_solution and ipiasm::check_noc_legality; re-running addressing\
will not change this."
        }
        return "ADDR_FAIL:assign_bd_address returned rc=0 and\
[llength $ddr] DRAM window(s) are published, but NOT ONE master address space\
holds one, so no master can reach DDR.$why"
    }
    return "SUCCESS:auto_address done ddr_windows=[llength $ddr]\
masters_on_dram=[llength $on_dram] unassigned={$unassigned}"
}

# --- keep a data-mover out of the register maps it has no business reaching ---
# assign_bd_address maps every slave a master can see. On a shared NoC that is
# far more than the design means: a multi-camera build can leave video
# codec's DMA came out holding an aperture onto the ISP's control registers
# alongside its frame buffer, because both hang off the same fabric. It
# validates and it routes, and it is not what the design says the codec does.
# Excluding the segment is the supported way to say so, and it is a design
# statement rather than a repair, so nothing infers it: the caller names what
# the master is FOR.
#   master_cell : the cell whose address spaces get narrowed
#   keep        : glob patterns for the segment names to keep (e.g. {*DDR* *MC*})
proc ipiasm::restrict_master {master_cell keep} {
    set cell [get_bd_cells -quiet $master_cell]
    if {$cell eq ""} { return "RESTRICT_FAIL:no such cell $master_cell" }
    if {[llength $keep] == 0} {
        return "RESTRICT_FAIL:no keep pattern given for $master_cell -- refusing\
to exclude every segment this master has"
    }
    set dropped {}
    set kept {}
    foreach space [get_bd_addr_spaces -quiet -of_objects $cell] {
        foreach seg [get_bd_addr_segs -quiet -of_objects $space] {
            set name [get_property -quiet NAME $seg]
            set keepit 0
            foreach p $keep {
                if {[string match -nocase $p $name]} { set keepit 1 }
            }
            if {$keepit} {
                lappend kept $name
                continue
            }
            if {[catch {exclude_bd_addr_seg -target_address_space $space $seg} e]} {
                return "RESTRICT_FAIL:$name :: [lindex [split $e \n] 0]"
            }
            lappend dropped $name
        }
    }
    if {[llength $kept] == 0} {
        return "RESTRICT_FAIL:$master_cell keeps nothing -- none of\
{[join $keep { }]} matched any segment it holds; excluded [llength $dropped]\
and left it addressing nothing. Re-map with ipiasm::auto_address."
    }
    return "SUCCESS:$master_cell keeps [llength $kept] segment(s)\
{[join $kept {, }]} and no longer addresses [llength $dropped]\
{[join $dropped {, }]}"
}

# Exact target allow-list for a specific master address space, including a
# top-level AXI ingress that has no owning BD cell.
proc ipiasm::restrict_address_space {space_path keep_targets} {
    set space [get_bd_addr_spaces -quiet $space_path]
    if {$space eq ""} { return "RESTRICT_FAIL:no address space $space_path" }
    if {![llength $keep_targets]} {
        return "RESTRICT_FAIL:no keep target given for $space_path"
    }
    set kept {}
    set dropped {}
    set failed {}
    foreach mapped [get_bd_addr_segs -quiet -of_objects $space] {
        set targets [get_bd_addr_segs -quiet -of_objects $mapped]
        if {![llength $targets]} { set targets [list $mapped] }
        set keep 0
        foreach target $targets {
            set path [string trimleft $target /]
            foreach pattern $keep_targets {
                if {[string match -nocase [string trimleft $pattern /] $path]} {
                    set keep 1
                }
            }
        }
        if {$keep} {
            lappend kept $mapped
        } elseif {[catch {
            exclude_bd_addr_seg -target_address_space $space $mapped
        } e]} {
            lappend failed "$mapped:[lindex [split $e \n] 0]"
        } else {
            lappend dropped $mapped
        }
    }
    if {![llength $kept]} {
        return "RESTRICT_FAIL:$space_path kept no segment matching {$keep_targets}"
    }
    if {[llength $failed]} {
        return "RESTRICT_FAIL:$space_path kept=[llength $kept] dropped=[llength $dropped] errors={$failed}"
    }
    return "RESTRICT:OK space=$space_path kept=[llength $kept] dropped=[llength $dropped]"
}

# --- fixed-window scope is an exact allow-list, not "at least these masters" ---
proc ipiasm::_resolve_addr_spaces {patterns} {
    set spaces {}
    foreach pattern $patterns {
        foreach space [get_bd_addr_spaces -quiet $pattern] {
            if {[lsearch -exact $spaces $space] < 0} { lappend spaces $space }
        }
    }
    return $spaces
}

proc ipiasm::_is_master_addr_space {space} {
    # Slave memory maps are also bd_addr_space objects, but only initiating
    # master spaces expose the SECURE_READ/SECURE_WRITE policy properties.
    return [expr {
        [lsearch -exact [list_property $space] SECURE_READ] >= 0
        || [lsearch -exact [list_property $space] SECURE_WRITE] >= 0
    }]
}

proc ipiasm::_mapped_seg_targets {mapped slave} {
    if {$mapped eq $slave} { return 1 }
    set related [get_bd_addr_segs -quiet -of_objects $mapped]
    if {[lsearch -exact $related $slave] >= 0} { return 1 }
    set target ""
    catch {set target [get_property -quiet SLAVE_SEGMENT $mapped]}
    if {$target ne "" && ($target eq $slave
        || [string trimleft $target /] eq [string trimleft $slave /])} {
        return 1
    }
    return 0
}

# Remove one slave window from every address space outside allowed_spaces.
# Call only after topology and automatic addressing are final; no later
# connectivity/address pass may recreate mappings behind this policy.
proc ipiasm::restrict_window_to_spaces {slave_seg allowed_spaces} {
    set slave [get_bd_addr_segs -quiet $slave_seg]
    if {$slave eq ""} { return "WINDOW_SCOPE_FAIL:no slave segment $slave_seg" }
    set allowed [ipiasm::_resolve_addr_spaces $allowed_spaces]
    if {[llength $allowed] == 0} {
        return "WINDOW_SCOPE_FAIL:none of allowed_spaces={$allowed_spaces} exists"
    }
    set excluded {}
    set errors {}
    foreach space [get_bd_addr_spaces -quiet] {
        if {![ipiasm::_is_master_addr_space $space]} { continue }
        if {[lsearch -exact $allowed $space] >= 0} { continue }
        foreach mapped [get_bd_addr_segs -quiet -of_objects $space] {
            if {![ipiasm::_mapped_seg_targets $mapped $slave]} { continue }
            set range 0
            catch {set range [get_property -quiet RANGE $mapped]}
            if {$range eq "" || [catch {expr {$range > 1}} placed] || !$placed} {
                continue
            }
            if {[catch {
                exclude_bd_addr_seg -target_address_space $space $mapped
            } e]} {
                lappend errors "$space:[lindex [split $e \n] 0]"
            } else {
                lappend excluded $space
            }
        }
    }
    if {[llength $errors]} {
        return "WINDOW_SCOPE_FAIL:excluded=[llength $excluded] errors={$errors}"
    }
    return "WINDOW_SCOPE:RESTRICTED slave=$slave allowed=[llength $allowed] excluded=[llength $excluded]"
}

# Prove exact scope and placement after all address work. allowed_spaces may
# contain globs; each must resolve to concrete address spaces.
proc ipiasm::check_window_scope {slave_seg allowed_spaces offset range} {
    set slave [get_bd_addr_segs -quiet $slave_seg]
    if {$slave eq ""} { return "WINDOW_SCOPE_FAIL:no slave segment $slave_seg" }
    set allowed [ipiasm::_resolve_addr_spaces $allowed_spaces]
    if {[llength $allowed] == 0} {
        return "WINDOW_SCOPE_FAIL:none of allowed_spaces={$allowed_spaces} exists"
    }
    set actual {}
    set moved {}
    foreach space [get_bd_addr_spaces -quiet] {
        if {![ipiasm::_is_master_addr_space $space]} { continue }
        foreach mapped [get_bd_addr_segs -quiet -of_objects $space] {
            if {![ipiasm::_mapped_seg_targets $mapped $slave]} { continue }
            set got_offset [get_property -quiet OFFSET $mapped]
            set got_range  [get_property -quiet RANGE $mapped]
            if {$got_range eq "" || [catch {expr {$got_range > 1}} placed] || !$placed} {
                continue
            }
            if {[catch {
                expr {$got_offset == $offset && $got_range == $range}
            } correct] || !$correct} {
                lappend moved "$space@$got_offset/$got_range"
            } elseif {[lsearch -exact $actual $space] < 0} {
                lappend actual $space
            }
        }
    }
    set missing {}
    foreach space $allowed {
        if {[lsearch -exact $actual $space] < 0} { lappend missing $space }
    }
    set extra {}
    foreach space $actual {
        if {[lsearch -exact $allowed $space] < 0} { lappend extra $space }
    }
    if {[llength $missing] || [llength $extra] || [llength $moved]} {
        return "WINDOW_SCOPE_FAIL:required=[llength $allowed] present=[llength $actual]\
 missing={$missing} extra={$extra} moved={$moved}"
    }
    return "WINDOW_SCOPE:OK required=[llength $allowed] present=[llength $actual] extra=0 moved=0"
}

# Offset-free visibility assertion, read-only. check_window_scope proves a FIXED
# window sits in exactly the right spaces at exactly the right address; a slave
# whose offset is auto-assigned has no such address to compare against, so a
# DesignSpec rule of the form "these masters must not reach this slave" -- the
# common shape of a visibility rule -- had no check at all. This is that check.
#
# Two different designs satisfy the same rule two different ways: never creating
# the mapping, or creating it and then excluding it. Both are invisibility, and
# both look identical here, because a plain -of_objects query returns only the
# mappings that are live. A check written to count exclusions instead would call
# a design that simply never mapped the slave non-compliant.
#
# The allow-list is exact in both directions: a master that should reach the
# slave and does not is as much a contract failure as one that should not and
# does.
#   slave_seg      : slave segment whose reachability is being asserted
#   allowed_spaces : globs for the master address spaces permitted to reach it;
#                    {} asserts that no master may
proc ipiasm::check_visibility {slave_seg {allowed_spaces {}}} {
    set slave [get_bd_addr_segs -quiet $slave_seg]
    if {$slave eq ""} { return "VISIBILITY_FAIL:no slave segment $slave_seg" }
    set allowed {}
    if {[llength $allowed_spaces]} {
        set allowed [ipiasm::_resolve_addr_spaces $allowed_spaces]
        if {[llength $allowed] == 0} {
            return "VISIBILITY_FAIL:none of allowed_spaces={$allowed_spaces} exists"
        }
    }
    set seen {}
    foreach space [get_bd_addr_spaces -quiet] {
        if {![ipiasm::_is_master_addr_space $space]} { continue }
        foreach mapped [get_bd_addr_segs -quiet -of_objects $space] {
            if {![ipiasm::_mapped_seg_targets $mapped $slave]} { continue }
            if {[lsearch -exact $seen $space] < 0} { lappend seen $space }
        }
    }
    set extra {}
    foreach space $seen {
        if {[lsearch -exact $allowed $space] < 0} { lappend extra $space }
    }
    set missing {}
    foreach space $allowed {
        if {[lsearch -exact $seen $space] < 0} { lappend missing $space }
    }
    if {[llength $extra] || [llength $missing]} {
        return "VISIBILITY_FAIL:slave=$slave_seg allowed=[llength $allowed]\
 reached=[llength $seen] extra={$extra} missing={$missing}"
    }
    return "VISIBILITY:OK slave=$slave_seg allowed=[llength $allowed]\
 reached=[llength $seen] extra=0 missing=0"
}

# Idempotently make one fixed window's live Address Editor state equal the
# DesignSpec's exact allow-list. Unlike place_named_windows this closes both
# halves of the contract: missing allowed mappings are created and every
# mapping outside the allow-list is excluded. Cell/space names are bindings
# supplied by the caller; this proc contains no board- or design-specific data.
proc ipiasm::enforce_window_scope {name slave_seg allowed_spaces offset range} {
    set slave [get_bd_addr_segs -quiet $slave_seg]
    if {$slave eq ""} { return "WINDOW_SCOPE_FAIL:no slave segment $slave_seg" }
    set allowed [ipiasm::_resolve_addr_spaces $allowed_spaces]
    if {[llength $allowed] == 0} {
        return "WINDOW_SCOPE_FAIL:none of allowed_spaces={$allowed_spaces} exists"
    }

    set errors {}
    foreach space $allowed {
        set exact 0
        foreach mapped [get_bd_addr_segs -quiet -of_objects $space] {
            if {![ipiasm::_mapped_seg_targets $mapped $slave]} { continue }
            set got_offset [get_property -quiet OFFSET $mapped]
            set got_range [get_property -quiet RANGE $mapped]
            if {![catch {
                expr {$got_offset == $offset && $got_range == $range}
            } correct] && $correct} {
                set exact 1
                break
            }
        }
        if {$exact} { continue }
        set result [ipiasm::add_addr_seg $space $slave \
            "${name}_[string map {/ _} [string trimleft $space /]]" \
            $offset $range]
        if {![string match "SUCCESS:*" $result]} { lappend errors $result }
    }
    if {[llength $errors]} {
        return "WINDOW_SCOPE_FAIL:$name placement errors={$errors}"
    }
    set restricted [ipiasm::restrict_window_to_spaces $slave $allowed]
    if {![string match "WINDOW_SCOPE:RESTRICTED*" $restricted]} {
        return $restricted
    }
    return [ipiasm::check_window_scope $slave $allowed $offset $range]
}

# Fingerprint every delivery-relevant mutable object: cells and CONFIG state,
# scalar/interface connectivity, and address mappings. A count-only fingerprint
# misses replacing one mapping with another while keeping the same total.
proc ipiasm::_delivery_fingerprint {} {
    set rows {}
    foreach cell [lsort [get_bd_cells -quiet -hierarchical]] {
        set vlnv [get_property -quiet VLNV $cell]
        lappend rows "CELL\t$cell\t$vlnv"
        foreach prop [lsort [list_property $cell]] {
            if {![string match "CONFIG.*" $prop]} { continue }
            if {[catch {set value [get_property -quiet $prop $cell]}]} { continue }
            lappend rows "CFG\t$cell\t$prop\t$value"
        }
    }
    foreach net [lsort [get_bd_intf_nets -quiet -hierarchical]] {
        set ends [concat \
            [get_bd_intf_pins -quiet -of_objects $net] \
            [get_bd_intf_ports -quiet -of_objects $net]]
        lappend rows "INTF\t$net\t[join [lsort $ends] ,]"
    }
    foreach net [lsort [get_bd_nets -quiet -hierarchical]] {
        set ends [concat \
            [get_bd_pins -quiet -of_objects $net] \
            [get_bd_ports -quiet -of_objects $net]]
        lappend rows "NET\t$net\t[join [lsort $ends] ,]"
    }
    foreach space [lsort [get_bd_addr_spaces -quiet]] {
        foreach mapped [lsort [get_bd_addr_segs -quiet -of_objects $space]] {
            set target ""; set offset ""; set range ""; set excluded ""
            catch {set target [get_property -quiet SLAVE_SEGMENT $mapped]}
            catch {set offset [get_property -quiet OFFSET $mapped]}
            catch {set range [get_property -quiet RANGE $mapped]}
            catch {set excluded [get_property -quiet EXCLUDED $mapped]}
            lappend rows "ADDR\t$space\t$mapped\t$target\t$offset\t$range\t$excluded"
        }
    }
    set body [join [lsort $rows] \n]
    if {![catch {set crc [format %08x [zlib crc32 $body]]}]} {
        return "$crc:[llength $rows]"
    }
    return "[string length $body]:[llength $rows]"
}

# Recheck the exact state after all topology/address repairs. window_specs is a
# list of {name slave-segment {allowed-address-spaces} offset range} records
# derived from the DesignSpec plus live technology bindings. plan_path and
# spec_path default to discovery; a missing file fails unless given as "none".
proc ipiasm::final_contract_gate {window_specs {want_mc 0} {ps_cell ""} {required_irqs 0} {force_noc 1} {require_pinned 1} {plan_path auto} {overmap_limit ""} {required_dram_masters {}} {spec_path auto}} {
    if {[llength [info commands ::ipiasm::trace_label]]} {ipiasm::trace_label final}
    variable delivery_fp
    variable recorded_evidence
    variable library_dir
    ipcfg::ledger_load
    ipiasm::_ledger_restore
    set failures {}
    set evidence {}
    set clocks [ipiasm::check_clock_contracts]
    lappend evidence $clocks
    if {[string match CLOCK_CONTRACT_FAIL:* $clocks]} {lappend failures $clocks}

    if {$plan_path eq "auto"} {
        set plans [ipiasm::_discover_assembly_plans]
        if {[llength $plans] > 1} {
            lappend failures "PLAN_EXECUTION_FAIL:multiple assembly plans found {$plans}; pass the delivered plan path explicitly"
        } elseif {[llength $plans] == 1} {
            set plan_path [lindex $plans 0]
        } else {
            lappend failures "PLAN_EXECUTION_FAIL:no assembly_plan.json found under [pwd]; pass its path, or none only for a design assembled without a plan"
            lappend evidence [lindex $failures end]
            set plan_path ""
        }
    } elseif {$plan_path eq "none"} {
        set plan_path ""
        set why [expr {[dict exists $::ipiasm::gates plan_why] ? [dict get $::ipiasm::gates plan_why] : ""}]
        if {[string match "OVERRIDE:*" $why]} {
            lappend evidence "PLAN_EXECUTION:NONE approved by the user ($why)"
        } else {
            lappend failures "PLAN_EXECUTION_FAIL:plan 'none' is accepted only after ipiasm::gate_override plan <the user's reason>; build the design from assembly_plan.json with ipiasm::assemble_from_plan and pass that plan (or auto)"
            lappend evidence [lindex $failures end]
        }
    }
    if {$plan_path ne ""} {
        set plan_result [ipiasm::plan_execution_status $plan_path]
        lappend evidence $plan_result
        if {![string match "PLAN_EXECUTION:OK*" $plan_result]} {
            lappend failures $plan_result
        }
    }

    set bundle [ipiasm::validate_bundle $want_mc $ps_cell $required_irqs $force_noc $overmap_limit]
    lappend evidence $bundle
    if {![string match "BUNDLE:OK*" $bundle]} { lappend failures $bundle }
    set ps_noc [ipiasm::check_ps_noc_masters]
    lappend evidence $ps_noc
    if {[string match "PS_NOC_FAIL:*" $ps_noc]} { lappend failures $ps_noc }
    set dram [ipiasm::check_required_dram_masters $required_dram_masters]
    lappend evidence $dram
    if {![string match "DRAM_REACH:OK*" $dram]
        && ![string match "DRAM_REACH:NA*" $dram]} {
        lappend failures $dram
    }

    foreach spec $window_specs {
        if {[llength $spec] != 5} {
            lappend failures "WINDOW_SCOPE_FAIL:expected {name slave {spaces} offset range}, got {$spec}"
            continue
        }
        lassign $spec name slave spaces offset range
        set result [ipiasm::check_window_scope $slave $spaces $offset $range]
        lappend evidence "$name $result"
        if {![string match "WINDOW_SCOPE:OK*" $result]} { lappend failures "$name $result" }
    }

    if {[llength [info procs ::ipcfg::audit_requirements]]} {
        set req [ipcfg::audit_requirements]
        lappend evidence $req
        if {[string match "REQ_FAIL:*" $req]
            || ($require_pinned && ![string match "REQ:OK*" $req])} {
            lappend failures $req
        }
    }
    set report [ipiasm::assembly_report]
    lappend evidence $report
    if {[string match "ASSEMBLY:BLOCKED*" $report]} { lappend failures $report }

    set irq [ipiasm::check_irq_contracts]
    lappend evidence $irq
    if {[string match IRQ_PATHS_FAIL:* $irq]} { lappend failures $irq }
    if {[dict size $recorded_evidence]} {
        set now [ipiasm::_delivery_fingerprint]
        dict for {label record} $recorded_evidence {
            lassign $record categories result fingerprint
            if {$fingerprint eq $now} {
                lappend evidence "EVIDENCE:$label categories={$categories} $result"
            } else {
                lappend evidence "EVIDENCE_STALE:$label design changed after it was recorded; rerun that check and ipiasm::record_evidence again"
            }
        }
    }

    if {$spec_path eq "auto"} {
        set specs {}
        foreach path [list [file join [pwd] design_spec.json] [file join [pwd] outputs design_spec.json]] {
            if {[file isfile $path]} { lappend specs [file normalize $path] }
        }
        set spec_path [expr {[llength $specs] == 1 ? [lindex $specs 0] : ""}]
        if {$spec_path eq ""} {
            lappend failures "COVERAGE_FAIL:[expr {[llength $specs] ? {several DesignSpecs found} : {no design_spec.json found}}] under [pwd]; pass its path, or none only when the design has no DesignSpec"
            lappend evidence [lindex $failures end]
        }
    } elseif {$spec_path eq "none"} {
        set spec_path ""
        set found {}
        set places [list [pwd] [file join [pwd] outputs] [file dirname [pwd]]]
        if {[dict exists $::ipiasm::executed_plan path]} {lappend places [file dirname [dict get $::ipiasm::executed_plan path]]}
        foreach place $places {
            set path [file join $place design_spec.json]
            if {[file isfile $path]} {lappend found [file normalize $path]}
        }
        if {[llength $found]} {
            lappend failures "COVERAGE_FAIL:spec 'none' refused: a DesignSpec exists ([lindex [lsort -unique $found] 0]); pass its path so every contract is checked against the live design"
            lappend evidence [lindex $failures end]
        } else {
            set why [expr {[dict exists $::ipiasm::gates plan_why] ? [dict get $::ipiasm::gates plan_why] : ""}]
            if {[string match "OVERRIDE:*" $why]} {
                lappend evidence "COVERAGE:NONE declared by caller (user-approved hand build: $why)"
            } else {
                lappend failures "COVERAGE_FAIL:spec 'none' is accepted only after ipiasm::gate_override plan <the user's reason>; write design_spec.json with the vivado-spec-generator skill and pass it (or auto)"
                lappend evidence [lindex $failures end]
            }
        }
    }
    if {$spec_path ne ""} {
        set checker [file normalize [file join $library_dir .. scripts contract_coverage.py]]
        set channel [file tempfile evidence_file]
        try {
            fconfigure $channel -encoding utf-8 -translation lf
            puts $channel [join $evidence \n]
        } finally { close $channel }
        if {[catch {ipcfg::python_script $checker check --spec [file normalize $spec_path] --evidence $evidence_file} coverage]} {
            set coverage "COVERAGE_FAIL:checker error: $coverage"
        }
        file delete -force $evidence_file
        set coverage [string trim $coverage]
        lappend evidence $coverage
        if {![string match COVERAGE:OK* $coverage]} { lappend failures $coverage }
        set reference [ipiasm::check_reference_coverage [file dirname [file normalize $spec_path]]]
        lappend evidence $reference
        if {[string match REFERENCE_COVERAGE:FAIL* $reference]} { lappend failures $reference }
    }

    if {[llength $failures]} {
        set delivery_fp ""
        return [ipiasm::_logged "FINAL_CONTRACT_FAIL:[llength $failures] check(s)\n  [join $evidence \n\ \ ]"]
    }
    set delivery_fp [ipiasm::_delivery_fingerprint]
    return [ipiasm::_logged "FINAL_CONTRACT:PASS windows=[llength $window_specs] fingerprint=$delivery_fp\n  [join $evidence \n\ \ ]"]
}

# final_contract_gate for a plan-built design, with every argument taken from the
# executed plan and its DesignSpec (scripts/final_gate_inputs.py): the plan's
# window checks named after the spec's windows, its validate_bundle arguments,
# and the DRAM masters the spec's memory contracts require. The plan's required
# reachability and its check_visibility ops are rechecked and recorded as evidence
# first. Evidence for a
# category the plan does not check (a supplied address-map checker, for one)
# must be recorded with ipiasm::record_evidence before calling this.
proc ipiasm::final_contract_gate_from_plan {{plan_path auto} {spec_path auto}} {
    variable library_dir
    if {$plan_path eq "auto"} {
        set plans [ipiasm::_discover_assembly_plans]
        if {[llength $plans] != 1} {
            return [ipiasm::_logged "FINAL_CONTRACT_FAIL:expected one assembly_plan.json under [pwd], found [llength $plans]; pass its path"]
        }
        set plan_path [lindex $plans 0]
    }
    if {$spec_path eq "auto"} {
        set spec_path [file join [file dirname $plan_path] design_spec.json]
    }
    foreach path [list $plan_path $spec_path] {
        if {![file isfile $path]} { return [ipiasm::_logged "FINAL_CONTRACT_FAIL:no file $path"] }
    }
    set script [file normalize [file join $library_dir .. scripts final_gate_inputs.py]]
    if {[catch {ipcfg::python_script $script --plan [file normalize $plan_path] --spec [file normalize $spec_path]} inputs]} {
        return [ipiasm::_logged "FINAL_CONTRACT_FAIL:[string trim $inputs]"]
    }
    set inputs [string trim $inputs]
    if {[string match FINAL_INPUTS_FAIL:* $inputs]} { return [ipiasm::_logged "FINAL_CONTRACT_FAIL:$inputs"] }
    if {[llength [dict get $inputs reachability]]} {
        set reach [ipiasm::check_required_reachability [dict get $inputs reachability]]
        if {![string match REACH:OK* $reach]} { return [ipiasm::_logged "FINAL_CONTRACT_FAIL:$reach"] }
        ipiasm::record_evidence plan_reachability {control_reachability} $reach
    }
    if {[dict exists $inputs visibility]} {
        set index 0
        foreach check [dict get $inputs visibility] {
            set seen [ipiasm::check_visibility {*}$check]
            if {![string match VISIBILITY:OK* $seen]} { return [ipiasm::_logged "FINAL_CONTRACT_FAIL:$seen"] }
            ipiasm::record_evidence plan_visibility_[incr index] {visibility_policy} $seen
        }
    }
    lassign [dict get $inputs bundle] want_mc ps_cell required_irqs force_noc overmap_limit
    return [ipiasm::final_contract_gate [dict get $inputs windows] $want_mc $ps_cell $required_irqs \
        $force_noc 1 $plan_path $overmap_limit [dict get $inputs dram_masters] $spec_path]
}

# Print a delivery verdict's first line as well as returning it, so the session
# log holds the result even when the caller only keeps the return value.
proc ipiasm::_logged {result} {
    puts [lindex [split $result \n] 0]
    return $result
}

# Call immediately before save/export and again afterward. Any changed cell,
# property, net, or address mapping invalidates the final-contract evidence.
proc ipiasm::delivery_guard {} {
    return [ipiasm::_logged [ipiasm::_delivery_guard_result]]
}
proc ipiasm::_delivery_guard_result {} {
    variable delivery_fp
    set clocks [ipiasm::check_clock_contracts]
    if {[string match CLOCK_CONTRACT_FAIL:* $clocks]} {return "DELIVERY_GUARD_FAIL:$clocks"}
    if {$delivery_fp eq ""} {
        return "DELIVERY_GUARD_FAIL:final_contract_gate has not passed"
    }
    set current [ipiasm::_delivery_fingerprint]
    if {$current ne $delivery_fp} {
        return "DELIVERY_GUARD_FAIL:design changed after final contract gate expected=$delivery_fp current=$current"
    }
    return "DELIVERY_GUARD:OK fingerprint=$current"
}

# Deliver a validated design in one call: final gate (taken from the plan), delivery guard,
# save, output products, wrapper, BD Tcl, XSA, guard again, delivery context and the timing
# report. Returns one DELIVERED line with the paths, or DELIVER_FAIL naming the step.
proc ipiasm::deliver {out {plan_path auto} {spec_path auto}} {
    if {[llength [info commands ::ipiasm::trace_label]]} {ipiasm::trace_label deliver}
    set out [file normalize $out]
    file mkdir $out
    # Default to the plan this process executed (agents keep it beside the DesignSpec, which is
    # often not Vivado's working directory), and the DesignSpec beside that plan. A plan run
    # stage by stage is recorded only once every stage passes, so before that the plan in
    # <out> is the one to gate: its gate then names the stages still to run.
    if {$plan_path eq "auto" && [dict exists $::ipiasm::executed_plan path]} {
        set plan_path [dict get $::ipiasm::executed_plan path]
    } elseif {$plan_path eq "auto" && [file isfile [file join $out assembly_plan.json]]} {
        set plan_path [file join $out assembly_plan.json]
    }
    if {$spec_path eq "auto" && $plan_path ne "auto"} {
        set spec_path [file join [file dirname $plan_path] design_spec.json]
    }
    set gate [ipiasm::final_contract_gate_from_plan $plan_path $spec_path]
    if {![string match FINAL_CONTRACT:PASS* $gate]} {
        set lines [split $gate \n]
        set failing [ipiasm::_gate_failures [lrange $lines 1 end]]
        if {![llength $failing]} {set failing [lrange $lines 1 8]}
        return "DELIVER_FAIL:final gate: [join [list [lindex $lines 0] {*}[lrange $failing 0 11]] { | }]"
    }
    return [ipiasm::_export_delivery $out]
}

# The lines of a final-gate report that can be a failure: passing checks, EVIDENCE lines
# and NOTE and ADVISORY paragraphs are dropped. The gate lists twenty-odd passing checks
# and multi-line QoS NOTEs (one lists every connection at the bandwidth seed) before the
# failing check, so a fixed head of the report hid it and agents re-ran the gate by hand.
# A NOTE or ADVISORY runs until a line that opens with a check token (FAMILY:...,
# FAMILY_FAIL..., or "<window> FAMILY:..."); its own lines are prose or object paths, and
# the next check's indentation varies (an ADVISORY sits at column 0, checks at 2). A
# coverage line "<contract>: needs <proc> -> FAMILY:OK" names the missing proof, so it is
# kept although it quotes a passing token. Lines with a failure marker come first: the rest
# is prose and lists of other checks (a stale .ncr note, AIE paths, declared open pins), and
# a run read twelve of those while the COVERAGE_FAIL that failed the gate came after them.
proc ipiasm::_gate_failures {lines} {
    set failing {}
    set other {}
    set in_block 0
    foreach line $lines {
        if {$in_block} {
            if {![regexp {^\s*(\S+\s+)?[A-Z][A-Z0-9_]+(:|_FAIL)} $line]} {continue}
            set in_block 0
        }
        if {[regexp {^\s*(NOTE:|ADVISORY)} $line]} {set in_block 1; continue}
        if {![regexp {^\s*\S+: needs } $line]
            && [regexp {\m[A-Z_]+:(OK|PASS|NA|CLEAN)\M|^\s*EVIDENCE:|^\s*$} $line]} {continue}
        if {[regexp {[A-Z0-9]_FAIL\M|\mFAIL\M|^\s*\S+: needs |\m(MISSING|ERROR)\M} $line]} {
            lappend failing $line
        } else {
            lappend other $line
        }
    }
    return [concat $failing $other]
}

# --- does the recorded CED meet the request? ---
# Runs scripts/ced_fit.py on <out>/ced_reference.json and <out>/ced_checklist.json, writes
# <out>/ced_fit.json and returns CED_FIT:<EXACT|CONFIG_ONLY|STRUCTURAL> with its differences
# and a NEXT: line naming the command to run.
proc ipiasm::ced_fit {out} {
    variable library_dir
    set out [file normalize $out]
    foreach need {ced_reference.json ced_checklist.json} {
        if {![file isfile [file join $out $need]]} {
            return "CED_FIT_FAIL:no $need in $out (ipiasm::ced_reference records the CED; write the checklist from the request and the board's reference document)"
        }
    }
    set script [file normalize [file join $library_dir .. scripts ced_fit.py]]
    if {[catch {ipcfg::python_script $script --reference [file join $out ced_reference.json] \
            --checklist [file join $out ced_checklist.json] --out [file join $out ced_fit.json]} fit]} {
        return "CED_FIT_FAIL:[string range $fit 0 200]"
    }
    set lines [lsearch -all -inline -not [split [string trim $fit] \n] CHANGES_TCL*]
    set channel [open [file join $out ced_reference.json] r]; set head [gets $channel]; close $channel
    regexp {"name":"([^"]+)"} $head -> ced
    regexp {"options":(\{[^\}]*\})} $head -> options
    if {[string match CED_FIT:STRUCTURAL* $fit]} {
        lappend lines "NEXT: build through the plan (DesignSpec from design_spec.draft.json), seeding it with ipiasm::instantiate_ced as the first cells-stage operation"
    } elseif {[info exists ced]} {
        set opts {}
        if {[info exists options]} {
            foreach {- k v} [regexp -all -inline {"([^"]+)"\s*:\s*"([^"]*)"} $options] { lappend opts $k $v }
        }
        lappend lines "NEXT: in the delivery project (board set, empty block design): puts \[ipiasm::deliver_ced $out $ced [list $opts]\] -- no DesignSpec or plan"
    }
    return [join $lines \n]
}

# --- deliver a CED that already meets the request: no DesignSpec, no plan ---
# For a request the release's CED meets as it is (EXACT) or with setting changes only
# (CONFIG_ONLY). Before calling it: ipiasm::reference_search <out> (the CED search),
# ipiasm::ced_reference <ced> <out> <options> (the CED recorded in a scratch project), and
# <out>/ced_checklist.json written from the request and the board's reference document (format:
# scripts/ced_fit.py). This then:
#   1. checks the recorded CED against the checklist (scripts/ced_fit.py) and refuses a
#      STRUCTURAL fit, which goes through the plan seeded with ipiasm::instantiate_ced;
#   2. instantiates the CED into the open, empty block design of the delivery project and
#      applies the fit's setting changes;
#   3. validates, checks every clock input is driven, and checks the delivered design itself
#      against the checklist (it must now be EXACT);
#   4. exports through the same guard as ipiasm::deliver.
# Returns DELIVERED ... fit=<verdict> ced=<name>, or DELIVER_FAIL / DELIVER_CED_REFUSED.
proc ipiasm::deliver_ced {out ced {options {}}} {
    variable library_dir
    variable delivery_fp
    set out [file normalize $out]
    if {[llength [info commands ::ipiasm::trace_label]]} {ipiasm::trace_label deliver}
    foreach need {reference_search.json ced_reference.json ced_checklist.json} {
        if {![file isfile [file join $out $need]]} {
            return "DELIVER_CED_REFUSED:no $need in $out (run ipiasm::reference_search, ipiasm::ced_reference, and write the checklist from the request first)"
        }
    }
    set fit_script [file normalize [file join $library_dir .. scripts ced_fit.py]]
    set recorded [file join $out ced_reference.json]
    set channel [open $recorded r]; set head [gets $channel]; close $channel
    set name [lindex [split $ced :] 2]
    if {$name eq ""} { set name $ced }
    if {![string match "*:$name:*" $head]} {
        return "DELIVER_CED_REFUSED:ced_reference.json records another CED ([string range $head 0 120]); record $ced first"
    }
    if {[catch {ipcfg::python_script $fit_script --reference $recorded --checklist [file join $out ced_checklist.json] --out [file join $out ced_fit.json]} fit]} {
        return "DELIVER_FAIL:fit check: [string range $fit 0 200]"
    }
    regexp {CED_FIT:(\S+)} $fit -> verdict
    if {![info exists verdict] || $verdict ni {EXACT CONFIG_ONLY}} {
        return "DELIVER_CED_REFUSED:[string trim $fit] -> build through the plan, seeded with ipiasm::instantiate_ced"
    }
    set changes {}
    regexp -line {^CHANGES_TCL (.*)$} $fit -> changes
    if {[catch {current_project} project] || $project eq ""} { return "DELIVER_FAIL:open the delivery project first" }
    if {[ipiasm::_scratch_project]} { return "DELIVER_FAIL:the current project is a scratch project; open the delivery project" }
    set board [get_property -quiet BOARD_PART $project]
    if {$board eq ""} { return "DELIVER_FAIL:the delivery project has no board part; set the CED's board first" }
    set bd [current_bd_design -quiet]
    if {$bd eq ""} { set bd [create_bd_design design_1] }
    if {[llength [get_bd_cells -quiet]]} { return "DELIVER_FAIL:$bd is not empty; a CED is delivered into an empty block design" }
    ipiasm::_gate_set plan 1 "CED:$ced fit=$verdict"
    ipiasm::_gate_set board 1 "READY:board_part=$board (CED $ced)"
    set seeded [ipiasm::instantiate_ced $ced $options]
    if {![string match CED:OK* $seeded]} { return "DELIVER_FAIL:$seeded" }
    dict for {cell values} $changes {
        if {[catch {set_property -dict $values [get_bd_cells $cell]} failure]} {
            return "DELIVER_FAIL:setting $cell: [string range $failure 0 200]"
        }
    }
    set valid [ipiasm::validate]
    if {$valid ne "VALIDATE:PASS"} { return "DELIVER_FAIL:[string range $valid 0 300]" }
    set clocks [ipiasm::check_undriven_clocks]
    if {![string match UNDRIVEN_CLK:OK* $clocks]} { return "DELIVER_FAIL:$clocks" }
    # The delivered design itself, not the scratch record, must meet the checklist.
    set live [file join $out ced_delivered.json]
    set channel [open $live w]
    try { ipiasm::_write_bd_records $channel } finally { close $channel }
    current_bd_design $bd
    if {[catch {ipcfg::python_script $fit_script --reference $live --checklist [file join $out ced_checklist.json]} check]
        || ![string match CED_FIT:EXACT* [string trim $check]]} {
        return "DELIVER_FAIL:the delivered design does not meet the checklist: [string range [string trim $check] 0 300]"
    }
    set delivery_fp [ipiasm::_delivery_fingerprint]
    set result [ipiasm::_export_delivery $out]
    if {[string match DELIVERED* $result]} { append result " fit=$verdict ced=$ced changes=[dict size $changes]" }
    return $result
}

# The export every delivery ends with: guard, save, generate, wrapper, write_bd_tcl,
# write_hw_platform, guard again, context check, timing report. Returns DELIVERED or DELIVER_FAIL.
proc ipiasm::_export_delivery {out} {
    set guard [ipiasm::_delivery_guard_result]
    if {![string match DELIVERY_GUARD:OK* $guard]} {return "DELIVER_FAIL:$guard"}
    set bd [current_bd_design]
    set bd_file [get_files -quiet $bd.bd]
    if {[catch {
        save_bd_design
        generate_target all $bd_file
        set wrapper [make_wrapper -files $bd_file -top]
        if {![llength [get_files -quiet $wrapper]]} {add_files -norecurse $wrapper}
        set_property top [file rootname [file tail $wrapper]] [current_fileset]
        write_bd_tcl -force -no_ip_version [file join $out ${bd}_bd.tcl]
        write_hw_platform -fixed -force -file [file join $out $bd.xsa]
    } failure]} {return "DELIVER_FAIL:export: [string range $failure 0 300]"}
    set guard [ipiasm::_delivery_guard_result]
    if {![string match DELIVERY_GUARD:OK* $guard]} {return "DELIVER_FAIL:after export: $guard"}
    set context [ipiasm::_delivery_context_result $bd [get_property DIRECTORY [current_project]]]
    if {![string match DELIVERY_CONTEXT:OK* $context]} {return "DELIVER_FAIL:$context"}
    set timing "none"
    set trace [file join [pwd] .ipiasm trace]
    if {[file isdirectory $trace]} {
        set report [file normalize [file join $::ipiasm::library_dir .. scripts timing_report.py]]
        if {![catch {ipcfg::python_script $report $trace} text]} {
            set timing [file join $out timing_report.md]
            set channel [open $timing w]
            try {puts $channel $text} finally {close $channel}
        }
    }
    foreach path [list [file join $out $bd.xsa] [file join $out ${bd}_bd.tcl]] {
        if {![file isfile $path] || [file size $path] == 0} {return "DELIVER_FAIL:missing or empty $path"}
    }
    return "DELIVERED xsa=[file join $out $bd.xsa] bd_tcl=[file join $out ${bd}_bd.tcl] timing=$timing gate=PASS guard=OK context=OK"
}

# Prove that the session exposed to the caller still holds the intended final
# project and BD. A successful probe project is not a delivered system.
proc ipiasm::assert_delivery_context {expected_bd expected_project_dir} {
    return [ipiasm::_logged [ipiasm::_delivery_context_result $expected_bd $expected_project_dir]]
}
proc ipiasm::_delivery_context_result {expected_bd expected_project_dir} {
    set bd [current_bd_design]
    if {$bd ne $expected_bd} {
        return "DELIVERY_CONTEXT_FAIL:current_bd=$bd expected=$expected_bd"
    }
    set project [current_project -quiet]
    if {$project eq ""} {
        return "DELIVERY_CONTEXT_FAIL:no current project"
    }
    set actual [file normalize [get_property -quiet DIRECTORY $project]]
    set expected [file normalize $expected_project_dir]
    if {$actual ne $expected} {
        return "DELIVERY_CONTEXT_FAIL:project_dir=$actual expected=$expected"
    }
    set guard [ipiasm::_delivery_guard_result]
    if {![string match "DELIVERY_GUARD:OK*" $guard]} { return $guard }
    return "DELIVERY_CONTEXT:OK bd=$bd project_dir=$actual"
}

# --- targeted segment: place ONE slave seg into a master's space ---
# Used for the spec's named windows (PCIe BAR2/BAR3, BRAM mailbox, ctrl regs).
# Validated against UG835 assign_bd_address: -offset/-range place a seg at an
# EXPLICIT base; -force skips the validity checks when a legal-but-overlapping
# layout is intended; create_bd_addr_seg is the last-resort manual builder.
#   space:  master address space (get_bd_addr_spaces target, e.g. cell/Data)
#   seg:    target slave seg (get_bd_addr_segs target)
#   name:   segment name      offset/range: e.g. 0x20000000 / 512M
proc ipiasm::add_addr_seg {space seg name offset range} {
    if {[set g [ipiasm::_need inventory "add_addr_seg"]] ne ""} { return $g }
    if {[llength [ipiasm::_all_noc_cells]] && [set g [ipiasm::_need qos "add_addr_seg"]] ne ""} { return $g }
    set sp [get_bd_addr_spaces -quiet $space]
    set sg [get_bd_addr_segs -quiet $seg]
    if {$sp eq ""} { return "ADDR_FAIL:no addr space $space" }
    if {$sg eq ""} { return "ADDR_FAIL:no addr seg $seg" }
    # 1) normal explicit placement
    if {![catch {assign_bd_address -target_address_space $sp $sg -offset $offset -range $range} e]} {
        return "SUCCESS:seg $name @ $offset ($range) in $space"
    }
    # 2) retry forcing past validity checks (UG835 -force) -- needed when the
    #    auto-checker rejects a deliberate fixed/overlapping window from the spec
    if {![catch {assign_bd_address -force -target_address_space $sp $sg -offset $offset -range $range} e2]} {
        return "SUCCESS:seg $name @ $offset ($range) in $space (forced)"
    }
    # 3) last resort: build the master segment by hand
    if {![catch {create_bd_addr_seg -range $range -offset $offset $sp $sg $name} e3]} {
        return "SUCCESS:seg $name @ $offset ($range) in $space (manual seg)"
    }
    return "ADDR_FAIL:$e | $e2 | $e3"
}

# --- convert a human size ("512M", "128MB", "64K", "2G", "0x...") to a hex range ---
# Generic, IP-agnostic. Returns "" if the size cannot be parsed (caller escalates).
proc ipiasm::size_to_range {size} {
    set s [string trim $size]
    if {$s eq ""} { return "" }
    if {[string match -nocase "0x*" $s]} { return $s }
    if {[regexp -nocase {^([0-9]+)\s*([kmgt])b?$} $s -> n u]} {
        set p [string tolower $u]
        set mult [dict get {k 1024 m 1048576 g 1073741824 t 1099511627776} $p]
        return [format 0x%X [expr {wide($n) * $mult}]]
    }
    if {[string is integer -strict $s]} { return [format 0x%X [expr {wide($s)}]] }
    return ""
}

# --- place named address windows from the manifest (spec sizes -> ranges) ---
# Generic: each window's SIZE comes from the spec; the master address SPACE, the
# target SEG, and the OFFSET are design bindings the spec does not pin down, so a
# window missing any of them is ESCALATED (never invented). This is the stage-5
# companion to autofix_address: call it for the named host windows, then let
# auto-assign cover the remaining unambiguous slaves.
#   windows: flat list  {name space seg offset size}  repeated (offset "" = ask)
proc ipiasm::place_named_windows {windows} {
    if {[set g [ipiasm::_need inventory "place_named_windows"]] ne ""} { return $g }
    if {[llength [ipiasm::_all_noc_cells]] && [set g [ipiasm::_need qos "place_named_windows"]] ne ""} { return $g }
    set placed {}; set escalate {}
    foreach {name space seg offset size} $windows {
        set range [ipiasm::size_to_range $size]
        if {$range eq ""} {
            lappend escalate "${name}(unparseable/absent size '$size' -> ASK spec size)"
            continue
        }
        if {$space eq "" || $seg eq ""} {
            lappend escalate "${name}(size=$size ok; need master-space + target-seg binding -> ASK which host space maps to which slave)"
            continue
        }
        if {$offset eq ""} {
            lappend escalate "${name}(size=$size ok; offset not in spec -> ASK base offset, or auto)"
            continue
        }
        set r [ipiasm::add_addr_seg $space $seg $name $offset $range]
        if {[string match "SUCCESS:*" $r]} {
            lappend placed "${name}@${offset}($size)"
        } else {
            lappend escalate "${name}($r)"
        }
    }
    set out "NAMED_WINDOWS:placed=[llength $placed]"
    if {[llength $placed] > 0}   { append out " {[join $placed {, }]}" }
    if {[llength $escalate] > 0} { append out " ESCALATE={[join $escalate {; }]}" }
    return $out
}

# --- list segments already mapped at a FIXED offset (must not be relocated) ---
# Some slaves (CIPS regions, IPs that lock their base) carry a non-empty OFFSET
# the moment they connect. Auto-assign would otherwise try to move them. We
# enumerate mapped segs whose offset is set so autofix can HONOR (skip) them.
proc ipiasm::_fixed_segs {} {
    set fixed {}
    foreach sp [get_bd_addr_spaces -quiet] {
        foreach sg [get_bd_addr_segs -quiet -of_objects $sp] {
            if {[catch {set off [get_property -quiet OFFSET $sg]}]} { continue }
            if {$off ne "" && $off ne "0x0000000000000000"} {
                lappend fixed "$sg@$off"
            }
        }
    }
    return $fixed
}

# --- list DELIBERATELY-EXCLUDED slave segments (advisory, NOT a gap) ---
# get_bd_addr_segs -excluded returns segments that were intentionally excluded
# from a master space (the normal outcome of resolving an overlap / scoping a
# slave to only some masters). These are NOT "unmapped" -- treating them as such
# inflated the assembly gap count with intentional design decisions.
proc ipiasm::_excluded_segs {} {
    set ex {}
    if {[catch {set ex [get_bd_addr_segs -quiet -excluded]}]} { set ex {} }
    return $ex
}

# --- enumerate addressable SLAVE endpoint segments (mode=Slave intf pins) ---
proc ipiasm::_all_slave_segs {} {
    set segs {}
    foreach ip [get_bd_intf_pins -quiet -of_objects [get_bd_cells -quiet]] {
        if {[catch {set mode [get_property MODE $ip]}]} { continue }
        if {$mode ne "Slave"} { continue }
        foreach s [get_bd_addr_segs -quiet -of_objects $ip] { lappend segs $s }
    }
    return [lsort -unique $segs]
}

# --- list TRULY-unmapped slave segments (a real gap) ---
# This Vivado build has no `get_bd_addr_segs -unassigned`, and `-excluded` means
# intentionally excluded (see above), so neither is a correct "unmapped" source.
# A slave endpoint is genuinely unmapped iff no master space addresses it
# (get_bd_addr_segs -addressing <seg> is empty) AND it was not excluded on
# purpose. Excluded segments are reported separately as advisory.
proc ipiasm::_unassigned_segs {} {
    set excluded [ipiasm::_excluded_segs]
    set unmapped {}
    foreach seg [ipiasm::_all_slave_segs] {
        if {[lsearch -exact $excluded $seg] >= 0} { continue }
        set addr ""
        catch {set addr [get_bd_addr_segs -quiet -addressing $seg]}
        if {$addr eq ""} { lappend unmapped $seg }
    }
    return $unmapped
}

# ============================================================
# 3b. Externalize out-of-scope / boundary pins
# ============================================================
# Validated against UG835 make_bd_intf_pins_external / make_bd_pins_external:
# a pin OR a whole cell can be exposed as a top-level port (cell form externalizes
# every UNCONNECTED pin). The manifest marks certain pins as out-of-scope (MIPI
# D-PHY, board GT/refclk, sensor control) -- those belong on the BD boundary as an
# XDC handoff, NOT wired internally. This turns them into real external ports so
# validate_bd_design can fully close instead of flagging them as gaps.
#   pins: list of "cell/PIN" (intf or scalar) and/or bare "cell" to expose all.
# Returns SUCCESS:external={..} skipped={..}.
proc ipiasm::externalize_oos {pins} {
    set ext {}; set skip {}
    # Coerce to a plain STRING list first: a caller-built list of mixed bd_intf_pin
    # + bd_pin objects triggers [Common 17-224] "objects must be of the same type"
    # on lappend. Stringify defuses that.
    set plist {}
    foreach p $pins { lappend plist [string cat $p] }
    foreach p $plist {
        set ip [get_bd_intf_pins -quiet $p]
        set sp [get_bd_pins -quiet $p]
        set cl [expr {([string first / $p] < 0) ? [get_bd_cells -quiet $p] : ""}]
        if {$ip ne ""} {
            if {[ipiasm::_intf_wired $ip]} { lappend skip "${p}(already-wired)"; continue }
            if {[catch {make_bd_intf_pins_external $ip} e]} { lappend skip "${p}($e)" } else { lappend ext $p }
        } elseif {$sp ne ""} {
            if {[ipiasm::_net_wired $sp]} { lappend skip "${p}(already-wired)"; continue }
            # Skip interface-member scalars: their parent INTERFACE pin carries them.
            # Externalizing them individually causes [BD 41-1306] overrides.
            if {[ipiasm::_scalar_is_intf_member $sp]} { lappend skip "${p}(intf-member)"; continue }
            if {[catch {make_bd_pins_external $sp} e]} { lappend skip "${p}($e)" } else { lappend ext $p }
        } elseif {$cl ne ""} {
            if {[catch {make_bd_intf_pins_external $cl}]} {}
            if {[catch {make_bd_pins_external $cl}]} {}
            lappend ext "${p}(all-open-pins)"
        } else {
            lappend skip "${p}(no-such-pin/cell)"
        }
    }
    set out "SUCCESS:external={$ext}"
    if {[llength $skip] > 0} { append out " skipped={$skip}" }
    return $out
}

# --- create or expose one top-level interface declared by DesignSpec ---
# spec is a dict:
#   name <port_name> mode Master|Slave|Monitor vlnv <interface-vlnv>
#   properties {CONFIG.KEY value ...}
#   internal_pin <cell/PIN>             ;# optional: expose an existing pin
#
# This operation creates only the contract boundary. Connect the returned port
# to its functional destination with connect_automation/connect_axi_auto so the
# topology remains automation-owned.
proc ipiasm::externalize_interface {spec} {
    if {[set g [ipiasm::_need_inventory "externalize_interface"]] ne ""} { return $g }
    foreach key {name mode vlnv} {
        if {![dict exists $spec $key] || [string trim [dict get $spec $key]] eq ""} {
            return "EXTERNAL_FAIL:missing '$key' in interface spec"
        }
    }
    set name [dict get $spec name]
    set mode [dict get $spec mode]
    set vlnv [dict get $spec vlnv]
    if {$mode ni {Master Slave Monitor}} {
        return "EXTERNAL_FAIL:$name mode '$mode' must be Master, Slave or Monitor"
    }

    set port [get_bd_intf_ports -quiet $name]
    if {$port eq "" && [dict exists $spec internal_pin]
        && [string trim [dict get $spec internal_pin]] ne ""} {
        set pin_name [dict get $spec internal_pin]
        set pin [get_bd_intf_pins -quiet $pin_name]
        if {$pin eq ""} { return "EXTERNAL_FAIL:no interface pin $pin_name" }
        if {[ipiasm::_intf_wired $pin]} {
            return "EXTERNAL_FAIL:$pin_name is already connected"
        }
        if {[catch {set port [make_bd_intf_pins_external $pin]} e]} {
            return "EXTERNAL_FAIL:$pin_name :: [lindex [split $e \n] 0]"
        }
        catch {set_property NAME $name $port}
        set port [get_bd_intf_ports -quiet $name]
    }
    if {$port eq ""} {
        if {[catch {
            set port [create_bd_intf_port -mode $mode -vlnv $vlnv $name]
        } e]} {
            return "EXTERNAL_FAIL:$name :: [lindex [split $e \n] 0]"
        }
    }

    set properties {}
    if {[dict exists $spec properties]} { set properties [dict get $spec properties] }
    if {[llength $properties] % 2} {
        return "EXTERNAL_FAIL:$name properties must be a flat key/value list"
    }
    if {[llength $properties]} {
        if {[catch {set_property -dict $properties $port} e]} {
            return "EXTERNAL_FAIL:$name properties :: [lindex [split $e \n] 0]"
        }
    }
    return "EXTERNAL:OK name=$name mode=$mode vlnv=$vlnv path=$port"
}

# --- create one exactly named scalar top-level port, optionally wired ---
# For spec-named clock, reset, interrupt and GPIO boundary ports that the RTL top
# expects by name (externalize_oos auto-names them <pin>_0). dir is I or O; type
# is clk, rst, intr, data or "" for Vivado's default; pin, when given, is the
# cell pin this port drives (dir I) or is driven by (dir O), and a new port takes
# that pin's width. properties is a flat CONFIG list, e.g. {CONFIG.FREQ_HZ
# 250000000}. Re-running is safe: an existing port with the same direction is
# reused, never recreated.
proc ipiasm::create_port {name dir {type ""} {pin ""} {properties {}}} {
    if {[set g [ipiasm::_need_inventory "create_port"]] ne ""} { return $g }
    if {$dir ni {I O}} { return "PORT_FAIL:$name direction '$dir' must be I or O" }
    if {$type ni {"" clk rst intr data ce}} { return "PORT_FAIL:$name type '$type' is not clk, rst, intr, data or ce" }
    if {[llength $properties] % 2} { return "PORT_FAIL:$name properties must be a flat key/value list" }
    set p {}
    if {$pin ne ""} {
        set p [get_bd_pins -quiet $pin]
        if {$p eq ""} { return "PORT_FAIL:$name no pin $pin" }
    }
    set port [get_bd_ports -quiet $name]
    if {$port eq ""} {
        set options [list -dir $dir]
        if {$type ne ""} { lappend options -type $type }
        if {$p ne ""} {
            set left [get_property -quiet LEFT $p]
            set right [get_property -quiet RIGHT $p]
            if {[string is integer -strict $left] && [string is integer -strict $right]} {
                lappend options -from $left -to $right
            }
        }
        if {[catch {set port [create_bd_port {*}$options $name]} e]} {
            return "PORT_FAIL:$name :: [lindex [split $e \n] 0]"
        }
    } elseif {[get_property DIR $port] ne $dir} {
        return "PORT_FAIL:$name exists with direction [get_property DIR $port], not $dir"
    }
    if {[llength $properties] && [catch {set_property -dict $properties $port} e]} {
        return "PORT_FAIL:$name properties :: [lindex [split $e \n] 0]"
    }
    if {$pin eq ""} { return "PORT:OK name=$name dir=$dir" }
    set pn [get_bd_nets -quiet -of_objects $port]
    if {$pn ne "" && $pn eq [get_bd_nets -quiet -of_objects $p]} {
        return "PORT:OK name=$name dir=$dir pin=$pin (already connected)"
    }
    if {[catch {connect_bd_net $port $p} e]} {
        return "PORT_FAIL:$name -> $pin :: [lindex [split $e \n] 0]"
    }
    return "PORT:OK name=$name dir=$dir pin=$pin"
}

# Rename one exported top-level port, scalar or interface, to the name a boundary
# contract (RTL top, spec) expects. Automation names exported ports after their
# board interface or pin; the wrapper takes its port names from these, so a
# boundary-named port must carry the contract's name. Re-running is safe.
proc ipiasm::name_port {current wanted} {
    foreach kind {intf scalar} {
        set get [expr {$kind eq "intf" ? "get_bd_intf_ports" : "get_bd_ports"}]
        set have [$get -quiet $wanted]
        set old [$get -quiet $current]
        if {$have ne "" && $old ne "" && $current ne $wanted} {
            return "PORT_NAME_FAIL:both $current and $wanted exist; remove or rename one first"
        }
        if {$have ne ""} { return "PORT_NAME:OK $wanted (already named)" }
        if {$old ne ""} {
            if {[catch {set_property NAME $wanted $old} e]} {
                return "PORT_NAME_FAIL:$current -> $wanted :: [lindex [split $e \n] 0]"
            }
            return "PORT_NAME:OK $current -> $wanted"
        }
    }
    return "PORT_NAME_FAIL:no port $current or $wanted"
}

# The ports the generated <design>_wrapper can have, as an RTL instance names them:
# each scalar port and interface member, plus the single X_io inout the wrapper
# creates when it buffers a tristate X_i/X_o/X_t set (either form is accepted).
proc ipiasm::_wrapper_port_names {} {
    set interfaces [lmap port [get_bd_intf_ports -quiet] {string trimleft $port /}]
    set names {}
    foreach port [get_bd_ports -quiet] {
        set name [string trimleft $port /]
        if {$name ni $interfaces} { lappend names $name }
    }
    set all $names
    foreach name $names {
        if {[regexp {^(.+)_i$} $name -> base] && "${base}_o" in $names && "${base}_t" in $names} {
            lappend all "${base}_io"
        }
    }
    return [lsort -unique $all]
}

# The port-connection text of the first instance of module in Verilog text, or ""
# when there is none. Scans balanced parentheses, so a parameter block, nested
# expressions and the instances that follow cannot extend or cut it short.
proc ipiasm::_instance_ports {text module} {
    set pattern "\\m[string map {. \\.} $module]\\M"
    set start 0
    while {[regexp -start $start -indices $pattern $text at]} {
        set i [expr {[lindex $at 1] + 1}]
        set start $i
        regexp -start $i -indices {\A\s*} $text gap
        set i [expr {[lindex $gap 1] + 1}]
        if {[string index $text $i] eq "#"} {
            set i [ipiasm::_skip_parens $text [string first ( $text $i]]
            if {$i < 0} { return "" }
        }
        if {![regexp -start $i -indices {\A\s*\w+\s*\(} $text head]} { continue }
        set open [lindex $head 1]
        set close [ipiasm::_skip_parens $text $open]
        if {$close < 0} { return "" }
        return [string range $text [expr {$open + 1}] [expr {$close - 2}]]
    }
    return ""
}

# Index just past the parenthesis that closes the one at open, or -1.
proc ipiasm::_skip_parens {text open} {
    if {$open < 0 || [string index $text $open] ne "("} { return -1 }
    set depth 0
    for {set i $open} {$i < [string length $text]} {incr i} {
        switch -- [string index $text $i] {
            ( { incr depth }
            ) { if {[incr depth -1] == 0} { return [expr {$i + 1}] } }
        }
    }
    return -1
}

# Check the block design's boundary against an RTL file that instantiates its
# wrapper (default <current BD>_wrapper): every named port connection must exist
# on the wrapper. Missing names are listed, and an exported interface port whose
# members all appear under another prefix in the RTL is named as a rename for
# ipiasm::name_port.
proc ipiasm::check_wrapper_boundary {rtl_file {wrapper ""}} {
    if {![file readable $rtl_file]} { return "BOUNDARY_FAIL:cannot read $rtl_file" }
    if {$wrapper eq ""} { set wrapper "[current_bd_design]_wrapper" }
    set channel [open $rtl_file r]; set text [read $channel]; close $channel
    regsub -all {//[^\n]*} $text {} text
    regsub -all {/\*.*?\*/} $text {} text
    set body [ipiasm::_instance_ports $text $wrapper]
    if {$body eq ""} { return "BOUNDARY:NA no instance of $wrapper in [file tail $rtl_file]" }
    set wanted [lsort -unique [lmap item [regexp -all -inline {\.\w+\s*\(} $body] {string trim [string range $item 1 end] " ("}]]
    set have [ipiasm::_wrapper_port_names]
    set missing [lmap name $wanted {expr {$name in $have ? [continue] : $name}}]
    if {![llength $missing]} { return "BOUNDARY:OK ports=[llength $wanted]" }
    # For each exported interface port none of whose members the RTL uses, find
    # the RTL prefix that carries every one of its member suffixes.
    set hints {}
    foreach port [lsort [get_bd_intf_ports -quiet]] {
        set from [string trimleft $port /]
        set suffixes {}
        foreach name $have {
            if {[string match "${from}_*" $name]} { lappend suffixes [string range $name [string length $from] end] }
        }
        if {![llength $suffixes] || [llength [lmap x $suffixes {expr {"$from$x" in $wanted ? $x : [continue]}}]]} { continue }
        set counts {}
        foreach m $missing {
            foreach x $suffixes {
                if {[string match "*$x" $m]} { dict incr counts [string range $m 0 end-[string length $x]] }
            }
        }
        set best {}; set score -1
        dict for {to n} $counts {
            if {$n != [llength $suffixes]} { continue }
            # Prefer the target that carries every number in the source name (c0 -> C0_...).
            set digits 1
            foreach number [regexp -all -inline {\d+} $from] {
                if {$number ni [regexp -all -inline {\d+} $to]} { set digits 0 }
            }
            if {$digits > $score} { set best [list $to]; set score $digits } elseif {$digits == $score} { lappend best $to }
        }
        if {[llength $best] == 1} { lappend hints "$from -> [lindex $best 0]" }
    }
    set out "BOUNDARY_FAIL:[llength $missing] of [llength $wanted] port(s) the RTL instance names are not on the wrapper\n  missing: [join $missing { }]"
    if {[llength $hints]} { append out "\n  renames (ipiasm::name_port): [join $hints {; }]" }
    return $out
}

# ============================================================
# 3c. Hardened-fabric awareness
# ============================================================
# The Versal perception core (ISP visp_ss / AIE ai_engine / NoC-DDRMC / PS PCIe+
# CAN-FD) is NOT a set of independent cells: Vivado enforces hardened physical
# coupling. The caller supplies message codes and meanings obtained from current
# AMD documentation or live Vivado feedback; this library carries no
# design-derived signature list.
# Returns: FABRIC:coupled reasons={...}  |  FABRIC:none

# Report hardened-fabric coupling (ISP, AIE, NoC/DDRMC, PS PCIe) that makes cells depend on each other, from caller-supplied message codes.
proc ipiasm::detect_fabric_coupling {console {documented_codes {}}} {
    set hits {}
    if {[llength $documented_codes] % 2} {
        return "FABRIC_FAIL:documented_codes must be {message-code meaning ...}"
    }
    foreach {code why} $documented_codes {
        if {[string first $code $console] >= 0} { lappend hits $why }
    }
    if {[llength $hits] == 0} { return "FABRIC:none" }
    return "FABRIC:coupled reasons={[join $hits {; }]}\n  -> discover and apply the catalog's integrated subsystem automation; do not externalize or hand-wire hardened physical pins."
}

# ============================================================
# 5b. Board layer -- a registered board file IS the preset source
# ============================================================
#
# Whether a board file exists for the silicon being built is a QUERY, not an
# assumption. UG895: board files live in repositories registered through the
# `board.repoPaths` param (project property BOARD_PART_REPO_PATHS), and every
# board_component records the silicon it was authored for in PART_NAME. So the
# skill asks Vivado before it decides it is building blind -- the preset-pack
# fallback is for when this query comes back empty, not for whenever BOARD_PART
# happens to be unset.

# --- internal: die+package prefix of a part name (drops speed/temp grade) ---
# A board file's pinout and presets belong to the die in its package; a sibling
# speed or temperature grade shares both.
proc ipiasm::_part_family {part} {
    set f [split $part -]
    if {[llength $f] < 2} { return $part }
    return "[lindex $f 0]-[lindex $f 1]"
}

# --- internal: compare dotted board file_versions numerically (1.10 > 1.9) ---
proc ipiasm::_vercmp {a b} {
    set av [split [lindex [split $a :] end] .]
    set bv [split [lindex [split $b :] end] .]
    set n [expr {max([llength $av], [llength $bv])}]
    for {set i 0} {$i < $n} {incr i} {
        set x [lindex $av $i] ; set y [lindex $bv $i]
        if {![string is integer -strict $x]} { set x 0 }
        if {![string is integer -strict $y]} { set y 0 }
        if {$x != $y} { return [expr {$x < $y ? -1 : 1}] }
    }
    return [string compare $a $b]
}

# --- internal: registered board parts for the current project's silicon ---
# Exact PART_NAME (or ADDITIONALLY_SUPPORTED_PART_NAMES) matches first, then
# same-die/package grade siblings; newest file_version first within each group.
# A supplied hint is authoritative: if no board name or part matches it, return
# no candidate rather than silently selecting a different board for the silicon.
proc ipiasm::_board_candidates {{hint ""}} {
    set part ""
    catch {set part [get_property -quiet PART [current_project]]}
    set exact {} ; set kin {}
    foreach name [get_board_parts -quiet] {
        set obj [get_board_parts -quiet $name]
        if {$obj eq ""} { continue }
        set bpart "" ; set alts {}
        catch {set bpart [get_property -quiet PART_NAME $obj]}
        catch {set alts  [get_property -quiet ADDITIONALLY_SUPPORTED_PART_NAMES $obj]}
        if {$hint ne ""
            && ![string match -nocase "*$hint*" $name]
            && ![string match -nocase "*$hint*" $bpart]
            && ![string match -nocase "*$hint*" [join $alts " "]]} {
            continue
        }
        if {$part eq "" || $bpart eq $part || [lsearch -exact $alts $part] >= 0} {
            lappend exact $name
        } elseif {[ipiasm::_part_family $bpart] eq [ipiasm::_part_family $part]} {
            lappend kin $name
        }
    }
    return [concat [lsort -decreasing -command ipiasm::_vercmp $exact] \
                   [lsort -decreasing -command ipiasm::_vercmp $kin]]
}

# --- is there a board file for what we are building? ---
# `hint` is a board or part substring taken from the request. A non-empty hint
# must match; this prevents a missing named board from resolving to another
# board that happens to use the same silicon.
# Returns: BOARDS:<n> {<board_part> ...}   (newest file_version first)
proc ipiasm::discover_board_parts {{hint ""}} {
    set c [ipiasm::_board_candidates $hint]
    set part ""
    catch {set part [get_property -quiet PART [current_project]]}
    if {[llength $c] == 0} {
        return "BOARDS:0 {} (no registered board file matches part '$part'; repos: [get_param board.repoPaths])"
    }
    return "BOARDS:[llength $c] {[join $c { }]}"
}

# --- board-preset precheck: PS/CIPS/ISP automation needs a board part loaded ---
# A board-preset mismatch in SysMon/PCIe/pin properties is not a parameter the
# skill can invent. Call this BEFORE any PS/CIPS/subsystem
# automation. When exactly one registered board file matches the silicon, that IS
# the answer and it is selected here. Several file_versions of the SAME board are
# not ambiguity either -- the newest is taken, because a build must not stop to
# ask which revision of the one board it found. Only several DIFFERENT boards, or
# no board at all, escalate. Nothing is ever guessed: every auto-selection names
# the board file it took and why.
proc ipiasm::_require_board_preset_impl {{hint ""}} {
    set bp ""
    catch {set bp [get_property -quiet BOARD_PART [current_project]]}
    if {$bp ne ""} { return "READY:board_part=$bp" }
    set c [ipiasm::_board_candidates $hint]
    if {[llength $c] == 0} {
        return "ASK(board): no BOARD_PART set and no registered board file matches the requested hint '$hint' for part '[get_property -quiet PART [current_project]]' (repos: [get_param board.repoPaths]); board-owned memory, GT, and pin values cannot be guessed | options: register the requested board repository with ipiasm::register_board_repo <board_files dir> (set_param board.repoPaths + project BOARD_PART_REPO_PATHS; never set_property board.repoPaths) ; provide an explicit platform manifest ; Other"
    }
    # vendor:library:name, i.e. the board itself with its file_version dropped
    set stems {}
    foreach n $c { lappend stems [join [lrange [split $n :] 0 2] :] }
    set stems [lsort -unique $stems]
    if {[llength $stems] > 1} {
        return "ASK(board): [llength $c] registered board files from [llength $stems] different boards match this silicon | options: [join $c { ; }] ; Other"
    }
    set pick [lindex $c 0]
    if {[llength $c] == 1} {
        set why "the one registered board file for [get_property -quiet PART [current_project]]"
    } else {
        set why "newest of [llength $c] file_versions of [lindex $stems 0] ([join $c { }])"
    }
    if {[catch {set_property board_part $pick [current_project]} err]} {
        return "ASK(board): registered board file $pick would not attach to this project ($err) | options: recreate the project with -board_part $pick ; provide board files / preset .tcl ; Other"
    }
    set got [get_property -quiet BOARD_PART [current_project]]
    if {$got ne $pick} {
        return "ASK(board): board_part did not take (asked $pick, reads '$got') | options: recreate the project with -board_part $pick ; provide board files / preset .tcl ; Other"
    }
    return "READY:board_part=$pick (auto-selected: $why) -> pass board_preset Yes to the PS automation and use ipiasm::connect_board_intf for board-backed IO"
}

# --- make a shipped board-file directory visible to Vivado ---
# board.repoPaths is a Vivado PARAMETER (set_param), not a property:
# set_property board.repoPaths fails with Common 17-142. The parameter alone
# does not refresh the catalog of an already-open project (measured: 2026.1),
# so this also appends the project's BOARD_PART_REPO_PATHS. It never replaces
# other repos, and proves the catalog now lists a board from this directory.
proc ipiasm::register_board_repo {dir} {
    if {$dir eq "" || [file pathtype $dir] ne "absolute"} {
        return "BOARD_REPO_FAIL:give an absolute board-file directory (got '$dir')"
    }
    set dir [file normalize $dir]
    if {![file isdirectory $dir]} { return "BOARD_REPO_FAIL:$dir is not a directory" }
    set xmls [glob -nocomplain -directory $dir -types f board.xml */board.xml */*/board.xml */*/*/board.xml */*/*/*/board.xml]
    if {![llength $xmls]} {
        return "BOARD_REPO_FAIL:no board.xml under $dir (point at the board_files root that contains <vendor>/<board>/<version>/board.xml)"
    }
    set boards {}
    foreach xml $xmls {
        set channel [open $xml r]
        set head [read $channel 8192]
        close $channel
        if {[regexp {<board\s[^>]*\mname="([^"]+)"} $head -> board]} { lappend boards $board }
    }
    set boards [lsort -unique $boards]
    set before [get_board_parts -quiet]
    set paths [get_param board.repoPaths]
    if {[lsearch -exact [lmap p $paths {file normalize $p}] $dir] < 0} {
        lappend paths $dir
        if {[catch {set_param board.repoPaths $paths} err]} { return "BOARD_REPO_FAIL:set_param board.repoPaths :: $err" }
    }
    # An open project keeps the catalog it loaded, so the parameter alone only
    # reaches later projects; the project's own repo list refreshes it now.
    set project ""
    catch {set project [current_project -quiet]}
    if {$project ne ""} {
        set project_paths [get_property -quiet BOARD_PART_REPO_PATHS $project]
        if {[lsearch -exact [lmap p $project_paths {file normalize $p}] $dir] < 0} {
            lappend project_paths $dir
            if {[catch {set_property BOARD_PART_REPO_PATHS $project_paths $project} err]} {
                return "BOARD_REPO_FAIL:set_property BOARD_PART_REPO_PATHS :: $err"
            }
        }
    }
    set added {}
    set listed {}
    foreach p [get_board_parts -quiet] {
        if {$p ni $before} { lappend added $p }
        if {[lindex [split $p :] 1] in $boards} { lappend listed $p }
    }
    if {![llength $listed]} {
        return "BOARD_REPO_FAIL:$dir registered but get_board_parts lists none of its boards {[join $boards { }]}; repos={[get_param board.repoPaths]}"
    }
    set candidates [ipiasm::_board_candidates]
    return "BOARD_REPO:OK repo=$dir boards={[join $listed { }]} new_parts={[join $added { }]} match_current_part={[join $candidates { }]} -> ipiasm::require_board_preset <board hint>"
}

# --- board gate wrapper: READY: opens the board gate, anything else keeps it shut ---
proc ipiasm::require_board_preset {{hint ""}} {
    set r [ipiasm::_require_board_preset_impl $hint]
    if {[string match "READY:*" $r]} {
        ipiasm::_gate_set board 1 [lindex [split $r " "] 0]
    } else {
        ipiasm::_gate_set board 0 [string trim [lindex [split $r |] 0]]
    }
    return $r
}

# --- what the selected board file offers ---
# Each board interface names the IP it prefers (PREFERENCES) and the preset it
# carries for that IP (PRESETS) -- i.e. the board file states which cell to build
# and how to configure it, so read this before choosing IP or parameters.
# Returns: BOARD_INTFS:<n> {<name> vlnv=<..> ip=<..> preset=<..>; ...}
proc ipiasm::board_interfaces {{pattern *}} {
    set bp ""
    catch {set bp [get_property -quiet BOARD_PART [current_project]]}
    if {$bp eq ""} { return "BOARD_INTFS:0 {} (no BOARD_PART set - run ipiasm::require_board_preset first)" }
    set out {}
    foreach i [get_board_part_interfaces -quiet] {
        set nm [get_property -quiet NAME $i]
        if {![string match -nocase $pattern $nm]} { continue }
        lappend out "$nm vlnv=[get_property -quiet VLNV $i] ip=[get_property -quiet PREFERENCES $i] preset=[get_property -quiet PRESETS $i]"
    }
    return "BOARD_INTFS:[llength $out] {[join $out {; }]}"
}

# --- internal: the memory interfaces a board part offers, in board order ---
# Returns a list of {name family} pairs, where family is the busdef token the NoC
# names its per-controller board-interface parameters after (lpddr5_rtl ->
# LPDDR5, ddr5_rtl -> DDR5).
proc ipiasm::_board_memory_intfs {} {
    set out {}
    foreach i [get_board_part_interfaces -quiet] {
        set bd [get_property -quiet BUSDEF_NAME $i]
        if {![regexp {^((?:lp)?ddr[0-9]*)_rtl$} $bd -> fam]} { continue }
        lappend out [list [get_property -quiet NAME $i] [string toupper $fam]]
    }
    return [lsort -index 0 $out]
}

# --- board-backed memory controllers (the NUM_MC answer, from the board) ---
# The memory case of ipcfg::board_bind. A NoC's integrated controllers cannot be
# turned on with a bare `set_property CONFIG.NUM_MC` (see the NUM_MC contract
# above), and the block-automation rule the tutorials use for this,
# `xilinx.com:bd_rule:axi_noc`, DOES NOT EXIST for axi_noc2 in 2026.1 -- it
# returns "[BD 41-569] The rule ... cannot be found", so following the
# documentation cannot bring memory up on a Gen2 board. What works is the same
# generic hook every other IP uses: naming a board memory interface on
# `C<n>[_CH<m>]_<FAMILY>_BOARD_INTERFACE` pulls in that controller's board
# preset -- count, controller type, speed grade, data width, interleave size --
# and brings its package pins with it.
#
# It must be ONE set_property: the NoC cross-checks its NoC-General controller
# count against the count the DDRMC preset carries and rejects any intermediate
# state ("Number of Controllers selected value is 1 in NoC-General page whereas
# Number of Controllers value is 4 in DDRMC5 Configuration GUI").
#
# `want` is the spec's controller count; omit it to take the board's own count
# (how many memory interfaces it exposes). PG313 allows 0, 1, 2 or 4.
proc ipiasm::_ps_hsm_refclk {} {
    foreach ps [get_bd_cells -hierarchical -quiet -filter {VLNV=~*:ps_wizard:*}] {
        foreach pin [get_bd_pins -quiet "$ps/*hsm*ref_clk*"] {
            if {[get_property -quiet DIR $pin] eq "O"} { return $pin }
        }
    }
    return ""
}

# Without a PS hsm*_ref_clk, each memory NoC sys_clk must come from a top-level port.
proc ipiasm::_mem_sysclk_from_ports {noc_cell pins} {
    set bad {}
    set sources {}
    foreach intf [get_bd_intf_pins -quiet "$noc_cell/sys_clk*"] {
        set ports [get_bd_intf_ports -quiet -of_objects [get_bd_intf_nets -quiet -of_objects $intf]]
        if {[llength $ports]} { lappend sources {*}$ports } else { lappend bad $intf }
    }
    if {![llength [get_bd_intf_pins -quiet "$noc_cell/sys_clk*"]]} {
        foreach pin $pins {
            set ports [get_bd_ports -quiet -of_objects [get_bd_nets -quiet -of_objects $pin]]
            if {[llength $ports]} { lappend sources {*}$ports } else { lappend bad $pin }
        }
    }
    if {[llength $bad]} {
        return "MEM_SYSCLK_FAIL:no PS hsm*_ref_clk output, and {$bad} not driven by a top-level clock port"
    }
    return "MEM_SYSCLK:OK external [join [lsort -unique $sources] { }] -> $noc_cell sys_clk (no PS hsm*_ref_clk on this part)"
}

# Drive every memory NoC sys_clk pin from the PS hsm reference clock.
proc ipiasm::wire_mem_noc_sysclk {noc_cell} {
    set cell [get_bd_cells -quiet $noc_cell]
    if {$cell eq ""} { return "MEM_SYSCLK_FAIL:no such cell $noc_cell" }
    set pins [get_bd_pins -quiet "$noc_cell/sys_clk*"]
    if {![llength $pins]} { return "MEM_SYSCLK:NA $noc_cell has no sys_clk pins" }
    set hsm [ipiasm::_ps_hsm_refclk]
    if {$hsm eq ""} {
        return "MEM_SYSCLK:DEFER no PS hsm*_ref_clk output exists yet"
    }
    set rewired {}
    foreach pin $pins {
        set net [get_bd_nets -quiet -of_objects $pin]
        if {$net ne "" && [lsearch -exact [get_bd_pins -quiet -of_objects $net] $hsm] >= 0} {
            continue
        }
        if {$net ne ""} {
            set other_cells {}
            foreach peer [get_bd_pins -quiet -of_objects $net] {
                if {$peer eq $pin} { continue }
                set owner [get_bd_cells -quiet -of_objects $peer]
                if {$owner ne ""} { lappend other_cells $peer }
            }
            if {[llength $other_cells]} {
                return "MEM_SYSCLK_FAIL:$pin is driven by {$other_cells}, not $hsm"
            }
            set ports [get_bd_ports -quiet -of_objects $net]
            delete_bd_objs $net
            foreach port $ports { catch {delete_bd_objs $port} }
        }
        if {[catch {connect_bd_net $hsm $pin} e]} {
            return "MEM_SYSCLK_FAIL:$hsm -> $pin :: [lindex [split $e \n] 0]"
        }
        lappend rewired $pin
    }
    return "MEM_SYSCLK:OK $hsm -> [llength $pins] sys_clk pin(s) rewired=[llength $rewired]"
}

# Check every memory NoC sys_clk pin is driven by the PS hsm reference clock, or, on a
# part whose processing system has no hsm*_ref_clk output (CIPS), by a top-level clock
# port such as the board's memory reference clock.
proc ipiasm::check_mem_noc_sysclk {noc_cell} {
    set pins [get_bd_pins -quiet "$noc_cell/sys_clk*"]
    if {![llength $pins]} { return "MEM_SYSCLK:NA $noc_cell has no sys_clk pins" }
    set hsm [ipiasm::_ps_hsm_refclk]
    if {$hsm eq ""} { return [ipiasm::_mem_sysclk_from_ports $noc_cell $pins] }
    set bad {}
    foreach pin $pins {
        set net [get_bd_nets -quiet -of_objects $pin]
        if {$net eq "" || [lsearch -exact [get_bd_pins -quiet -of_objects $net] $hsm] < 0} {
            lappend bad $pin
        }
    }
    if {[llength $bad]} {
        return "MEM_SYSCLK_FAIL:[llength $bad]/[llength $pins] not driven by $hsm {$bad}"
    }
    return "MEM_SYSCLK:OK $hsm -> [llength $pins] sys_clk pin(s)"
}

# --- memory region selectors: which MC_CHAN_REGION value serves which address ---
# The IP's parameter-dependency dump lists every legal selector with its address
# range ("DDR_CH0_MED DDR CH0 MED (0x0008 0000 0000 to 0x000F FFFF FFFF; 32GB)").
# Parsed per selector key: {CONFIG.MC_CHAN_REGION<n> {<value> {lo hi} ...} ...}.
proc ipiasm::_region_ranges_from_dump {text} {
    set ranges {}
    set key ""
    set listing 0
    foreach line [split $text \n] {
        if {[regexp {^Parameter Name:\s+(MC_CHAN_REGION[0-9]+)\s*$} $line -> name]} {
            set key CONFIG.$name
            set listing 0
            dict set ranges $key {}
            continue
        }
        if {[regexp {^Parameter Name:} $line]} { set key ""; continue }
        if {$key eq ""} { continue }
        if {[regexp {^Range List:} $line]} { set listing 1 }
        if {$listing && [regexp {^(?:Range List:)?\s*([A-Z][A-Z0-9_]*)\s[^(]*\((0x[0-9A-Fa-f ]+)to\s+(0x[0-9A-Fa-f ]+);} $line -> value lo hi]} {
            dict set ranges $key $value [list [expr {[string map {" " ""} $lo]}] [expr {[string map {" " ""} $hi]}]]
        } elseif {$listing && [regexp {^[A-Z][a-z]+( [A-Za-z]+)*:} $line] && ![regexp {^Range List:} $line]} {
            set listing 0
        }
    }
    return $ranges
}

# DesignSpec regions as {name base size} dicts, or "" when given only {name size ...}.
proc ipiasm::_region_dicts {regions} {
    set out {}
    foreach region $regions {
        if {[catch {dict size $region}] || ![dict exists $region base] || ![dict exists $region size]} { return "" }
        set name [expr {[dict exists $region name] ? [dict get $region name] : "region[llength $out]"}]
        if {[catch {expr {[dict get $region base] + 0}} base] || [catch {expr {[dict get $region size] + 0}} size] || $size <= 0} {
            error "region $name needs numeric base and a positive size"
        }
        lappend out [list $name $base $size]
    }
    return $out
}

# Map DesignSpec regions onto selectors: each region takes the smallest legal
# selector range containing it, assigned to MC_CHAN_REGION0.. in base order.
# Returns {CONFIG.MC_CHAN_REGION<i> <value> ...}; errors name what cannot be served.
proc ipiasm::_pick_region_selectors {ranges regions} {
    set keys [lsort -dictionary [dict keys $ranges]]
    set sorted [lsort -integer -index 1 $regions]
    if {[llength $sorted] > [llength $keys]} {
        error "[llength $sorted] region(s) but the memory NoC has only [llength $keys] selector(s) ([join $keys { }]); use another memory NoC for the rest"
    }
    set picked {}
    foreach region $sorted key [lrange $keys 0 [expr {[llength $sorted] - 1}]] {
        lassign $region name base size
        set last [expr {$base + $size - 1}]
        set best ""; set width 0
        dict for {value span} [dict get $ranges $key] {
            lassign $span lo hi
            if {$base >= $lo && $last <= $hi && ($best eq "" || $hi - $lo < $width)} {
                set best $value; set width [expr {$hi - $lo}]
            }
        }
        if {$best eq ""} {
            error "region $name [format 0x%llX $base]+[format 0x%llX $size] fits no $key range"
        }
        lappend picked $key $best
    }
    return $picked
}

# Live, read-only: the selector ranges of <noc_cell>'s IP, from a scratch managed
# IP's parameter dump (the block design is not touched). Cached per VLNV.
proc ipiasm::memory_region_ranges {noc_cell} {
    variable region_range_cache
    if {![info exists region_range_cache]} { set region_range_cache {} }
    set cell [get_bd_cells -quiet $noc_cell]
    if {$cell eq ""} { error "no such cell $noc_cell" }
    set vlnv [get_property VLNV $cell]
    if {[dict exists $region_range_cache $vlnv]} { return [dict get $region_range_cache $vlnv] }
    set work [file join [get_property DIRECTORY [current_project]] .ipiasm_region_dump]
    set dump [file join $work regions.txt]
    set result [ipcfg::param_deps ipiasm_region_probe $dump $vlnv $work]
    if {![string match PARAM_DEPS:* $result]} { error "parameter dump unavailable: $result" }
    set channel [open $dump r]
    try { set ranges [ipiasm::_region_ranges_from_dump [read $channel]] } finally { close $channel }
    catch {file delete -force $work}
    if {![dict size $ranges]} { error "parameter dump lists no MC_CHAN_REGION selectors for $vlnv" }
    dict set region_range_cache $vlnv $ranges
    return $ranges
}

proc ipiasm::_format_selectors {picked ranges} {
    set out {}
    foreach {key value} $picked {
        lassign [dict get $ranges $key $value] lo hi
        lappend out "[regsub {^CONFIG\.} $key {}]=$value ([format 0x%llX $lo]-[format 0x%llX $hi])"
    }
    return [join $out {, }]
}

# Public: which selectors <noc_cell> needs for DesignSpec regions {{name base size} ...}.
proc ipiasm::region_selectors {noc_cell regions} {
    if {[catch {
        set dicts [ipiasm::_region_dicts $regions]
        if {$dicts eq ""} { error "regions must be {name <n> base <addr> size <bytes>} dicts" }
        set ranges [ipiasm::memory_region_ranges $noc_cell]
        set picked [ipiasm::_pick_region_selectors $ranges $dicts]
    } detail]} {
        return "REGION_SELECT_FAIL:$detail"
    }
    return "REGION_SELECT:OK selectors={$picked} ranges={[ipiasm::_format_selectors $picked $ranges]}"
}

# Bind a memory NoC to the board's LPDDR interfaces, optionally selecting regions from DesignSpec {name base size} dicts.
proc ipiasm::attach_board_memory {noc_cell {want ""} {regions {}}} {
    set cell [get_bd_cells -quiet $noc_cell]
    if {$cell eq ""} { return "MEM_ATTACH_FAIL:no such cell $noc_cell" }
    # DesignSpec regions ({name base size} dicts) are mapped to selectors here.
    if {[catch {ipiasm::_region_dicts $regions} dicts]} { return "MEM_ATTACH_FAIL:$dicts" }
    if {$dicts ne ""} {
        set chosen [ipiasm::region_selectors $noc_cell $regions]
        if {![string match REGION_SELECT:OK* $chosen]} { return "MEM_ATTACH_FAIL:$noc_cell $chosen" }
        regexp {selectors=\{([^\}]*)\}} $chosen -> regions
    }
    if {[llength $regions] % 2} {
        return "MEM_ATTACH_FAIL:regions must be a flat {MC_CHAN_REGION<n> <region> ...} list"
    }
    set region_dict {}
    foreach {key value} $regions {
        set key [expr {[string match CONFIG.* $key] ? $key : "CONFIG.$key"}]
        if {![regexp {^CONFIG\.MC_CHAN_REGION[0-9]+$} $key]} {
            return "MEM_ATTACH_FAIL:region key $key is not an MC_CHAN_REGION<n> selector"
        }
        if {$key ni [list_property $cell]} {
            return "MEM_ATTACH_FAIL:$noc_cell has no $key"
        }
        dict set region_dict $key $value
    }
    set bp ""
    catch {set bp [get_property -quiet BOARD_PART [current_project]]}
    if {$bp eq ""} { return "MEM_ATTACH_FAIL:no BOARD_PART set (run ipiasm::require_board_preset first)" }
    set mem [ipiasm::_board_memory_intfs]
    if {[llength $mem] == 0} { return "MEM_ATTACH_FAIL:board part $bp exposes no memory interface" }
    if {$want eq ""} { set want [llength $mem] }
    if {$want ni {0 1 2 4}} {
        return "MEM_ATTACH_FAIL:want=$want is not a legal NUM_MC (PG313: 0, 1, 2 or 4); board offers [llength $mem] interface(s)"
    }
    if {$want > [llength $mem]} {
        return "MEM_ATTACH_FAIL:want=$want but board part $bp offers only [llength $mem] memory interface(s)"
    }
    # Map controller index -> the cell's own parameter for it, per family.
    set params {}
    foreach p [list_property $cell] {
        if {[regexp {^CONFIG\.C([0-9]+)_(?:CH[0-9]+_)?([A-Z0-9]+)_BOARD_INTERFACE$} $p -> idx fam]} {
            if {![dict exists $params $fam $idx]} { dict set params $fam $idx $p }
        }
    }
    set binding {}
    set used {}
    for {set n 0} {$n < $want} {incr n} {
        set entry [lindex $mem $n]
        set iname [lindex $entry 0]
        set fam   [lindex $entry 1]
        if {![dict exists $params $fam $n]} {
            return "MEM_ATTACH_FAIL:$noc_cell has no $fam board-interface parameter for controller $n (has: [dict keys $params])"
        }
        lappend binding [dict get $params $fam $n] $iname
        lappend used "C$n=$iname"
    }
    # The generic board bind does the work: it applies the hooks atomically with
    # the controller count (the NoC rejects a state where its NoC-General count
    # and the board DDRMC preset disagree), finds the board-enable flag if the
    # first attempt is refused, and records everything the preset then wrote so
    # ipcfg::audit_board can catch a later hand-write over it.
    # Region selectors are disabled until controllers exist, and writing them
    # after the bind resets board-preset DDRMC5_CONFIG keys, so they ride along.
    set bind [ipcfg::board_bind $noc_cell $binding \
                  [concat [list CONFIG.NUM_MC $want] $region_dict] "board memory x$want"]
    if {[string match "BOARD_FAIL:*" $bind]} {
        return "MEM_ATTACH_FAIL:$noc_cell :: [string range $bind 11 end]\n  -> check `want` against the board's [llength $mem] memory interface(s)."
    }
    set mc [get_property -quiet CONFIG.NUM_MC $cell]
    if {$mc != $want} {
        return "MEM_ATTACH_FAIL:$noc_cell NUM_MC reads $mc after asking for $want (the set did not take)"
    }
    set region_read {}
    dict for {key value} $region_dict {
        set actual [get_property -quiet $key $cell]
        if {$actual ne $value} {
            return "MEM_ATTACH_FAIL:$noc_cell $key reads '$actual' after asking for '$value'"
        }
        lappend region_read "[regsub {^CONFIG\.} $key {}]=$actual"
    }
    set preset [ipcfg::preset_drift $noc_cell]
    if {[string match PRESET_DRIFT:* $preset]} {
        return "MEM_ATTACH_FAIL:$noc_cell board memory preset overridden: [string range $preset 0 400]"
    }
    set defaulted {}
    foreach key [lsort [list_property $cell CONFIG.MC_CHAN_REGION*]] {
        if {[dict exists $region_dict $key]} { continue }
        lappend defaulted "[regsub {^CONFIG\.} $key {}]=[get_property -quiet $key $cell]"
    }
    set dd ""
    catch {set dd [get_property -quiet CONFIG.DDRMC5_CONFIG $cell]}
    set from_board {}
    foreach k {DDRMC5_CONTROLLERTYPE DDRMC5_SPEED_GRADE DDRMC5_DATA_WIDTH DDRMC5_INTERLEAVE_SIZE} {
        if {[dict exists $dd $k]} { lappend from_board "$k=[dict get $dd $k]" }
    }
    set sysclk [ipiasm::wire_mem_noc_sysclk $noc_cell]
    if {[string match "MEM_SYSCLK_FAIL:*" $sysclk]} {
        return "MEM_ATTACH_FAIL:$sysclk"
    }
    # Binding the board interface configures the controller but does not export
    # its memory pins; without the top-level port the DRAM has no package pins.
    set ports {}
    foreach {param iname} $binding {
        set pin_name [regsub {_BOARD_INTERFACE$} [regsub {^CONFIG\.} $param {}] {}]
        set r [ipiasm::connect_board_intf $iname $noc_cell/$pin_name]
        if {![regexp {^(SUCCESS|SKIP):} $r]} { return "MEM_ATTACH_FAIL:$r" }
        lappend ports $iname
    }
    return "SUCCESS:board_memory $noc_cell NUM_MC=$mc NUM_MCP=[get_property -quiet CONFIG.NUM_MCP $cell] {[join $used {, }]} ports={$ports} regions={[join $region_read {, }]} ip_default_regions={[join $defaulted {; }]} from_board={[join $from_board {, }]} preset={$preset} sysclk={$sysclk} (pass the DesignSpec regions as {name base size} dicts and this call picks each MC_CHAN_REGION selector; DDR windows appear only after a NoC input is connected to an MC port via CONFIG.CONNECTIONS -- then run ipiasm::check_memory_regions)"
}

# --- automation-first board IO: apply_board_connection, not make_*_external ---
# A board-backed external interface should come from the board file so it arrives
# with its preset, its pin LOCs and its top-level ports already right. Only fall
# back to make_bd_intf_pins_external for boundaries the board file does not carry.
proc ipiasm::_missing_board_pin {board_intf ip_intf_pin} {
    set cell [get_bd_cells -quiet [file dirname $ip_intf_pin]]
    if {$cell eq ""} { return "BOARD_CONN_FAIL:no such interface pin $ip_intf_pin (no cell [file dirname $ip_intf_pin])" }
    set bif [get_board_part_interfaces -quiet -filter "NAME == $board_intf"]
    if {$bif eq ""} {
        return "BOARD_CONN_FAIL:no such interface pin $ip_intf_pin, and '$board_intf' is not a board interface of [get_property -quiet BOARD_PART [current_project]] (list them with ipiasm::board_interfaces)"
    }
    set want ""
    catch {set want [get_property -quiet VLNV $bif]}
    set pins {}
    set matching {}
    foreach pin [lsort [get_bd_intf_pins -quiet -of_objects $cell]] {
        lappend pins [file tail $pin]
        if {$want ne "" && [get_property -quiet VLNV $pin] eq $want} { lappend matching [file tail $pin] }
    }
    set hooks {}
    set bound ""
    foreach hook [ipcfg::board_hooks $cell] {
        set value [get_property -quiet $hook $cell]
        lappend hooks "[regsub {^CONFIG\.} $hook {}]=$value"
        if {$value eq $board_intf} { set bound $hook }
    }
    set name [string trimleft $cell /]
    if {$bound ne ""} {
        return "SKIP:board_conn $board_intf is already bound to $name through $bound (a board hook, not an interface pin)"
    }
    set advice "the pin name was guessed; choose one of the listed pins"
    if {[llength $matching]} {
        set advice "pins matching $board_intf's bus type ($want): {[join $matching { }]} -- use one of them"
    } elseif {[llength $hooks]} {
        set advice "no pin of $name carries $board_intf's bus type ($want); this IP binds board interfaces through its CONFIG.*_BOARD_INTERFACE hooks -- use ipcfg::board_bind $name {CONFIG.<hook> $board_intf} and read the result, instead of connect_board_intf"
    }
    return "BOARD_CONN_FAIL:no such interface pin $ip_intf_pin; $name interface pins: {[join $pins { }]}; board hooks: {[join $hooks { }]}; $advice (pins gated by an enablement parameter: ipcfg::param_deps + ipcfg::enablement_ports)"
}

# Connect a board interface to one IP interface pin through board automation.
proc ipiasm::connect_board_intf {board_intf ip_intf_pin} {
    set pin [get_bd_intf_pins -quiet $ip_intf_pin]
    if {$pin eq ""} { return [ipiasm::_missing_board_pin $board_intf $ip_intf_pin] }
    if {[ipiasm::_intf_wired $pin]} { return "SKIP:board_conn $ip_intf_pin already wired" }
    if {[llength [get_board_part_interfaces -quiet -filter "NAME == $board_intf"]] == 0} {
        return "BOARD_CONN_FAIL:board interface '$board_intf' not on [get_property -quiet BOARD_PART [current_project]] (see ipiasm::board_interfaces)"
    }
    set diagram [current_bd_design]
    if {[catch {apply_board_connection -board_interface $board_intf \
                    -ip_intf $pin -diagram $diagram} err]} {
        return "BOARD_CONN_FAIL:$board_intf -> $ip_intf_pin :: $err"
    }
    # The connection applies the interface's preset, which can overwrite values pinned on the
    # cell (a camera receiver's pixel format reset to the board's). Those were pinned on
    # purpose, so restore them, record the override, and fail only if they will not stick.
    set cell [get_bd_cells -quiet -of_objects $pin]
    if {$cell eq ""} { return "SUCCESS:board_conn $board_intf -> $ip_intf_pin" }
    set drift [ipcfg::pinned_drift $cell]
    if {![llength [dict get $drift items]]} { return "SUCCESS:board_conn $board_intf -> $ip_intf_pin" }
    set items [join [dict get $drift items] { }]
    set applied [ipcfg::apply_dict $cell [dict get $drift restore] 1]
    set left [ipcfg::pinned_reset $cell]
    if {![string match "SUCCESS:*" $applied] || $left ne ""} {
        return "BOARD_CONN_FAIL:$board_intf -> $ip_intf_pin reset pinned value(s) $items, and restoring\
them failed: $applied $left"
    }
    set name [string trimleft $cell /]
    set kept [expr {[dict exists $::ipcfg::board_override $name] ? [dict get $::ipcfg::board_override $name] : {}}]
    foreach {key value} [dict get $drift restore] {
        lappend kept [list $key $value "pinned before board interface $board_intf was bound"]
    }
    dict set ::ipcfg::board_override $name $kept
    ipcfg::ledger_record board_override $::ipcfg::board_override
    return "SUCCESS:board_conn $board_intf -> $ip_intf_pin RESTORED [llength [dict get $drift items]]\
pinned value(s) its preset reset: $items"
}

# --- PS fabric-interrupt capacity after PS automation, before IRQ wiring ---
proc ipiasm::check_ps_fabric_irqs {ps_cell required} {
    if {![string is integer -strict $required] || $required < 0} {
        return "IRQ_CAPACITY_FAIL:required must be a non-negative integer"
    }
    set ps [get_bd_cells -quiet $ps_cell]
    if {$ps eq ""} { return "IRQ_CAPACITY_FAIL:no PS cell $ps_cell" }
    set pins {}
    set capacity 0
    foreach pin [get_bd_pins -quiet "$ps_cell/*"] {
        set name [get_property -quiet NAME $pin]
        if {[get_property -quiet DIR $pin] ne "I"
            || ![regexp -nocase {^pl_.*irq} $name]} { continue }
        lappend pins $pin
        set width 1
        set left ""; set right ""
        catch {set left [get_property -quiet LEFT $pin]}
        catch {set right [get_property -quiet RIGHT $pin]}
        if {[string is integer -strict $left] && [string is integer -strict $right]} {
            set width [expr {abs($left - $right) + 1}]
        }
        incr capacity $width
    }
    if {$capacity < $required} {
        return "IRQ_CAPACITY_FAIL:$ps_cell exposes $capacity/$required PL IRQ input(s)\
 pins={$pins}; re-run PS automation with enough fabric interrupts before wiring"
    }
    return "IRQ_CAPACITY:OK available=$capacity required=$required pins=[llength $pins]"
}

# --- interrupt paths: trace every expected sink bit back to its real source ---
# Capacity says nothing about which source lands where, and an undriven
# interrupt input is tied low without a validation error, so a missing
# concat -> controller link or a swapped pair only shows up in a trace.
proc ipiasm::_irq_width {pin} {
    set left ""; set right ""
    catch {set left [get_property -quiet LEFT $pin]}
    catch {set right [get_property -quiet RIGHT $pin]}
    if {[string is integer -strict $left] && [string is integer -strict $right]} {
        return [expr {abs($left - $right) + 1}]
    }
    return 1
}

proc ipiasm::_irq_is_source {object} {
    set class [get_property -quiet CLASS $object]
    set dir [get_property -quiet DIR $object]
    if {$class eq "bd_port"} { return [expr {$dir eq "I"}] }
    if {$class ne "bd_pin" || $dir ne "O"} { return 0 }
    set cell [get_bd_cells -quiet -of_objects $object]
    return [expr {$cell ne "" && [get_property -quiet TYPE $cell] ne "hier"}]
}

# The leaf output or input port driving a pin, across hierarchy boundaries.
proc ipiasm::_irq_driver {pin} {
    if {[ipiasm::_irq_is_source $pin]} { return $pin }
    set peers {}
    catch {set peers [find_bd_objs -relation connected_to -thru_hier $pin]}
    foreach peer $peers {
        if {[ipiasm::_irq_is_source $peer]} { return $peer }
    }
    # A hierarchy pin reports only its outside peers; its driver is on the inner net.
    set cell [get_bd_cells -quiet -of_objects $pin]
    if {$cell eq "" || [get_property -quiet TYPE $cell] ne "hier"} { return "" }
    foreach net [get_bd_nets -quiet "[string trimleft $cell /]/*"] {
        set members [get_bd_pins -quiet -of_objects $net]
        if {[lsearch -exact $members $pin] < 0} { continue }
        foreach member $members {
            if {"$member" eq "$pin"} { continue }
            if {[ipiasm::_irq_is_source $member]} { return $member }
            set inner {}
            catch {set inner [find_bd_objs -relation connected_to -thru_hier $member]}
            foreach peer $inner {
                if {[ipiasm::_irq_is_source $peer]} { return $peer }
            }
        }
    }
    return ""
}

# UNCONNECTED | CONST:<v> | PORT:<name>[<bit>] | <cell>/<pin>[<bit>], seen through
# concat, slice and constant cells.
proc ipiasm::_irq_source {pin {bit 0} {depth 0}} {
    if {$depth > 16} { return "LOOP" }
    set driver [ipiasm::_irq_driver $pin]
    if {$driver eq ""} { return "UNCONNECTED" }
    set suffix [expr {[ipiasm::_irq_width $driver] > 1 ? "\[$bit\]" : ""}]
    if {[get_property -quiet CLASS $driver] eq "bd_port"} {
        return "PORT:[get_property -quiet NAME $driver]$suffix"
    }
    set cell [get_bd_cells -quiet -of_objects $driver]
    set vlnv [get_property -quiet VLNV $cell]
    if {[regexp {:(xl|il)constant:} $vlnv]} {
        set value [get_property -quiet CONFIG.CONST_VAL $cell]
        if {[catch {expr {($value >> $bit) & 1}} bit_value]} { return "CONST:$value" }
        return "CONST:$bit_value"
    }
    if {[regexp {:(xl|il)concat:} $vlnv]} {
        set offset 0
        set ports [get_property -quiet CONFIG.NUM_PORTS $cell]
        if {![string is integer -strict $ports]} { set ports 0 }
        for {set k 0} {$k < $ports} {incr k} {
            set width [get_property -quiet CONFIG.IN${k}_WIDTH $cell]
            if {![string is integer -strict $width] || $width < 1} { set width 1 }
            if {$bit < $offset + $width} {
                set input [get_bd_pins -quiet $cell/In$k]
                if {$input eq ""} { return "UNCONNECTED" }
                return [ipiasm::_irq_source $input [expr {$bit - $offset}] [expr {$depth + 1}]]
            }
            incr offset $width
        }
        return "UNCONNECTED"
    }
    if {[regexp {:(xl|il)slice:} $vlnv]} {
        set from [get_property -quiet CONFIG.DIN_FROM $cell]
        set to [get_property -quiet CONFIG.DIN_TO $cell]
        set input [get_bd_pins -quiet $cell/Din]
        if {$input ne "" && [string is integer -strict $from] && [string is integer -strict $to]} {
            return [ipiasm::_irq_source $input [expr {min($from, $to) + $bit}] [expr {$depth + 1}]]
        }
    }
    return "[string trimleft $driver /]$suffix"
}

proc ipiasm::_irq_pin_ref {text} {
    set bit 0
    regexp {^(.*)\[([0-9]+)\]$} $text -> text bit
    # get_bd_pins also returns a pin view of a top-level port, which has no peers.
    set pin ""
    if {[string first / [string trimleft $text /]] < 0} { set pin [get_bd_ports -quiet [string trimleft $text /]] }
    if {$pin eq ""} { set pin [get_bd_pins -quiet $text] }
    return [list $pin $bit]
}

# expected: {<sink> <want> ?<label>?} records. sink is <cell>/<pin> or
# <cell>/<pin>[<bit>]; want is <cell>/<pin>[<bit>], CONST:<0|1> or OPEN; label
# names the DesignSpec source (<block>.<output>) the slot delivers.
proc ipiasm::check_irq_paths {expected} {
    if {![llength $expected]} { return "IRQ_PATHS_FAIL:no interrupt slots given" }
    set bad {}; set want_at {}; set covered {}; set seen {}
    foreach record $expected {
        if {[llength $record] < 2 || [llength $record] > 3} {
            return "IRQ_PATHS_FAIL:expected {sink want ?label?}, got {$record}"
        }
        lassign $record sink want label
        if {$sink in $seen} { return "IRQ_PATHS_FAIL:sink $sink listed twice" }
        lappend seen $sink
        lassign [ipiasm::_irq_pin_ref $sink] pin bit
        if {$pin eq ""} { lappend bad "$sink: no such pin"; continue }
        set got [ipiasm::_irq_source $pin $bit]
        set target $want
        if {![regexp {^(CONST:|OPEN$)} $want]} {
            lassign [ipiasm::_irq_pin_ref $want] source source_bit
            if {$source eq ""} { lappend bad "$sink: expected source $want does not exist"; continue }
            set target [ipiasm::_irq_source $source $source_bit]
        } elseif {$want eq "OPEN"} {
            set target UNCONNECTED
        }
        dict set want_at $target $sink
        if {$got eq $target} {
            if {$label ne ""} { lappend covered $label }
            continue
        }
        set text "$sink: want $want got $got"
        if {$label ne ""} { append text " ($label)" }
        if {[string match CONST:* $got] && ![string match CONST:* $want]} {
            append text " -- tied to a constant instead of its source"
        }
        lappend bad $text
    }
    set swaps {}
    foreach line $bad {
        if {![regexp {^(\S+): want \S+ got (\S+)} $line -> sink got]} { continue }
        if {[regexp {^(CONST:|UNCONNECTED$)} $got]} { continue }
        if {[dict exists $want_at $got] && [dict get $want_at $got] ne $sink} {
            lappend swaps "$got belongs at [dict get $want_at $got], found at $sink"
        }
    }
    if {[llength $bad]} {
        set hint ""
        if {[llength $swaps]} { append hint "\n  -> sources are in the wrong positions: [join $swaps {; }]" }
        set open {}
        foreach cell [get_bd_cells -quiet -hier -filter {VLNV =~ *concat*}] {
            set dout [get_bd_pins -quiet $cell/dout]
            if {$dout ne "" && ![llength [get_bd_nets -quiet -of_objects $dout]]} {
                lappend open [string trimleft $cell /]
            }
        }
        if {[llength $open]} { append hint "\n  -> concat output(s) drive nothing: {[join $open { }]} -- connect each dout to its interrupt sink" }
        return "IRQ_PATHS_FAIL:[llength $bad]/[llength $expected] slot(s)\n  [join $bad "\n  "]$hint"
    }
    # Slots the caller did not list still reach the sink: a floating concat input
    # is an undeclared reserved slot. Tie it to a constant, or list it as OPEN.
    set declared_open 0
    foreach record $expected { if {[lindex $record 1] eq "OPEN"} { incr declared_open } }
    set floating {}
    foreach cell [get_bd_cells -quiet -hier -filter {VLNV =~ *concat*}] {
        set dout [get_bd_pins -quiet $cell/dout]
        if {$dout eq "" || ![llength [get_bd_nets -quiet -of_objects $dout]]} { continue }
        set ports [get_property -quiet CONFIG.NUM_PORTS $cell]
        if {![string is integer -strict $ports]} { continue }
        for {set k 0} {$k < $ports} {incr k} {
            set pin [get_bd_pins -quiet $cell/In$k]
            if {$pin ne "" && ![llength [get_bd_nets -quiet -of_objects $pin]]} {
                lappend floating [string trimleft $cell/In$k /]
            }
        }
    }
    if {[llength $floating] > $declared_open} {
        return "IRQ_PATHS_FAIL:[llength $floating] floating interrupt input(s) not in the contract\n  [join [lsort -dictionary $floating] "\n  "]\n  -> tie each reserved slot to a constant (list it as CONST:0), or list it as OPEN if the spec leaves it unconnected"
    }
    return "IRQ_PATHS:OK n=[llength $expected] covered={[join $covered { }]}"
}

# Register the interrupt table once, from the DesignSpec plus bindings, so the
# final gate re-traces it; a retry must repair wiring, not rewrite the table.
proc ipiasm::set_irq_contracts {expected} {
    variable irq_contracts
    if {[ipiasm::context_identity] eq {}} { return "IRQ_CONTRACT_FAIL:open project and BD required" }
    if {![llength $expected]} { return "IRQ_CONTRACT_FAIL:empty interrupt table is not acceptance" }
    foreach record $expected {
        if {[llength $record] < 2 || [llength $record] > 3} {
            return "IRQ_CONTRACT_FAIL:expected {sink want ?label?}, got {$record}"
        }
    }
    set candidate [dict create expected $expected context [ipiasm::context_identity]]
    if {$irq_contracts ne {} && $irq_contracts ne $candidate} {
        return "IRQ_CONTRACT_FAIL:contracts already registered; repair wiring, not requirements"
    }
    set irq_contracts $candidate
    ipcfg::ledger_record irq_contracts $irq_contracts
    return "IRQ_CONTRACTS:SET slots=[llength $expected]"
}

# Check the interrupt paths registered by set_irq_contracts on the current design.
proc ipiasm::check_irq_contracts {} {
    variable irq_contracts
    if {$irq_contracts eq {}} { return "IRQ_CONTRACTS:NA not registered" }
    if {[dict get $irq_contracts context] ne [ipiasm::context_identity]} {
        return "IRQ_PATHS_FAIL:design context changed since set_irq_contracts"
    }
    return [ipiasm::check_irq_paths [dict get $irq_contracts expected]]
}

# Keep a passing result from a check the final gate does not run itself (for
# example a supplied address-map checker). It counts only while the design is
# unchanged; categories name the DesignSpec contract lists it proves.
proc ipiasm::record_evidence {label categories result} {
    variable recorded_evidence
    set known {connections control_reachability memory_reachability interrupts
               fixed_address_windows visibility_policy clock_reset}
    foreach category $categories {
        if {$category ni $known} { return "EVIDENCE_FAIL:unknown category $category (known: $known)" }
    }
    if {![llength $categories]} { return "EVIDENCE_FAIL:name the DesignSpec categories this result proves" }
    if {![regexp {(^|[\s:_])OK([\s:_]|$)} $result] || [regexp -nocase {FAIL|ERROR} $result]} {
        return "EVIDENCE_FAIL:only a passing result is evidence: [string range $result 0 200]"
    }
    dict set recorded_evidence $label [list $categories [string range $result 0 400] [ipiasm::_delivery_fingerprint]]
    ipcfg::ledger_record recorded_evidence $recorded_evidence
    return "EVIDENCE:OK recorded $label categories={$categories}"
}

# --- memory-subsystem gate: the DDRMC count is a silent performance contract ---
# A controller-count mismatch can validate clean while violating the DesignSpec
# bandwidth contract. PG313 defines the legal interleave counts.
# Call after the NoC/MC automation, before declaring the build done.
# `want_mc` is the spec's controller count (PG313: must be 0, 1, 2, or 4).
# --- the DDR windows a memory NoC actually publishes ---
# CONFIG.NUM_MC is a REQUEST for controllers: it reads back the instant the
# property is written, whether or not a controller ever materialized. The
# evidence that DDR exists AND is addressable is the segment list the NoC
# publishes, which on axi_noc2 appears as <noc>/DDR_MC_PORTS/DDR_CH0_* once the
# board memory is attached. A design carrying enabled memory controllers with
# an empty segment list. assign_bd_address then had nothing to map, every
# master silently lost DDR, and validate_bd_design still returned PASS.
proc ipiasm::_noc_ddr_segs {} {
    set segs {}
    foreach c [get_bd_cells -quiet -filter {VLNV=~*axi_noc*}] {
        foreach s [get_bd_addr_segs -quiet $c/*] {
            # A NoC publishes its own register window too, and counting that as
            # DRAM is how a design with no memory at all reads as reachable.
            if {[ipiasm::_is_dram_seg $s]} { lappend segs $s }
        }
    }
    return $segs
}

# --- how many memory controllers this design has ASKED for, design-wide ---
# The request side of the same contract _noc_ddr_segs measures the delivery of.
# Kept separate from check_memory_subsystem's own tally because auto_address
# needs the number before it can decide whether a missing DRAM window is a
# defect or simply a design that deliberately has no DDR.
proc ipiasm::_requested_mc {} {
    set total 0
    foreach c [get_bd_cells -quiet -filter {VLNV=~*axi_noc*}] {
        set mc 0
        catch {set mc [get_property -quiet CONFIG.NUM_MC $c]}
        if {[string is integer -strict $mc]} { incr total $mc }
    }
    return $total
}

# --- is this segment a DRAM window? ---
# Matches the DDR/LPDDR families and HBM. Deliberately tested against the
# segment's LEAF name: a full path picks up unrelated cell names (`ps11_0_dma_pmc_0`
# contains `mc_0`), which is how a register window gets mistaken for DRAM.
proc ipiasm::_is_dram_seg {seg} {
    set leaf [file tail $seg]
    return [expr {[string match -nocase "*DDR*" $leaf] || [string match -nocase "*HBM*" $leaf]}]
}

# --- render a CONNECTIONS dict the way the NoC actually reads it ---
# CONFIG.CONNECTIONS is a string property, and axi_noc2 only honours a
# per-destination value when it arrives brace-wrapped:
#
#     M00_INI {read_bw {2000} write_bw {2000}}      route is built
#     M00_INI {read_bw 2000 write_bw 2000}          route is DISCARDED
#
# Nothing reports the second form. set_property accepts it, get_property reads
# it back verbatim, validate_bd_design passes, the NoC compiler raises nothing,
# and check_qos sees the declared bandwidth because Tcl's dict parser does not
# care about the braces. The only visible consequence is downstream and looks
# unrelated: no DDR segment is ever published into any master's address space,
# so assign_bd_address maps the peripherals, silently skips memory, and still
# returns success. An otherwise identical design differing
# only in these braces reaches DDR from every master or from none.
#
# Building the string with `dict set` produces the unbraced form, because Tcl
# only quotes values that need it. So the dict is the working representation and
# this is the only thing that may write it to a pin.
proc ipiasm::_conn_braced {conn} {
    set out {}
    if {[catch {dict size $conn}]} { return "" }
    dict for {dest sub} $conn {
        if {[catch {dict size $sub}]} { set sub {} }
        set kv {}
        dict for {k v} $sub { lappend kv "$k {$v}" }
        lappend out "$dest {[join $kv { }]}"
    }
    return [join $out " "]
}

# --- NoC declarations written in the form the NoC silently discards ---
# Structural, free, and independent of addressing, so it can run the moment the
# traffic spec is written -- long before anything would notice the missing DDR.
proc ipiasm::_unbraced_noc_decls {} {
    set bad {}
    foreach c [get_bd_cells -quiet -filter {VLNV=~*axi_noc*}] {
        foreach pin [get_bd_intf_pins -quiet $c/*] {
            set raw ""
            catch {set raw [get_property -quiet CONFIG.CONNECTIONS $pin]}
            if {$raw eq ""} { continue }
            if {[regexp {(?:read_bw|write_bw|read_avg_burst|write_avg_burst|initial_boot)\s+[^\s\{]} $raw]} {
                lappend bad [string trimleft $pin /]
            }
        }
    }
    return $bad
}

# --- who actually holds a DRAM window, read off the address map ---
# The ground truth for "can this master reach DDR", and the one measurement no
# topology can fool: it asks the address map rather than inferring reachability
# from CONFIG.CONNECTIONS. Inferring is what let a design with an INI aggregator
# report a clean memory subsystem while not one master could address DDR -- no
# AXI port declares MC_* once the ingress is INI, so the declaration-side view
# saw nothing to complain about.
#
# The published windows themselves live in the memory NoC's own DDR_MC_PORTS
# space. That is the supply side; skip those exact segment objects so only
# consumers are counted.
proc ipiasm::_dram_masters {} {
    set published [ipiasm::_noc_ddr_segs]
    set on {}
    foreach sp [get_bd_addr_spaces -quiet] {
        foreach seg [get_bd_addr_segs -quiet -of_objects $sp] {
            if {[lsearch -exact $published $seg] >= 0} { continue }
            if {[ipiasm::_is_dram_seg $seg]} {
                lappend on [string trimleft $sp /]
                break
            }
        }
    }
    return $on
}

# Prove every DesignSpec role that requires external memory owns a mapped DRAM
# segment. Patterns name address spaces derived from bound semantic roles; this
# helper carries no instance names.
proc ipiasm::check_required_dram_masters {patterns} {
    if {![llength $patterns]} {
        if {[ipiasm::_requested_mc] > 0} {
            return "DRAM_REACH_FAIL:the design requests memory controllers but\
the DesignSpec supplied no required DRAM-master role patterns"
        }
        return "DRAM_REACH:NA no external-memory masters required"
    }
    set bad {}
    set good {}
    foreach pattern $patterns {
        set spaces [get_bd_addr_spaces -quiet $pattern]
        if {![llength $spaces]} {
            lappend bad "${pattern}(no matching address space)"
            continue
        }
        set reached 0
        foreach space $spaces {
            foreach segment [get_bd_addr_segs -quiet -of_objects $space] {
                if {[ipiasm::_is_dram_seg $segment]} {
                    set reached 1
                    lappend good $space
                    break
                }
            }
        }
        if {!$reached} { lappend bad "${pattern}(no mapped DRAM segment)" }
    }
    if {[llength $bad]} {
        return "DRAM_REACH_FAIL:[llength $bad]/[llength $patterns] required\
role pattern(s) cannot reach DRAM\n  [join $bad "\n  "]"
    }
    return "DRAM_REACH:OK [llength $patterns]/[llength $patterns] role patterns\
spaces={[lsort -unique $good]}"
}

# --- pre-address reachability, read from topology rather than the address map ---
# pairs: {<address-space glob> <target-segment glob | DRAM> ...}, one per DesignSpec
# memory_reachability / control_reachability contract. Every matching master space
# must be able to address a matching segment; no address values are required.
proc ipiasm::check_required_reachability {pairs} {
    if {[llength $pairs] % 2} {
        return "REACH_FAIL:pairs must be a flat {space-glob target-glob ...} list"
    }
    if {![llength $pairs]} { return "REACH:NA no reachability contracts supplied" }
    set bad {}
    set ok 0
    foreach {pattern target} $pairs {
        set spaces [get_bd_addr_spaces -quiet $pattern]
        if {![llength $spaces]} {
            lappend bad "$pattern -> $target (no matching address space)"
            continue
        }
        foreach space $spaces {
            set reachable {}
            foreach intf [concat [get_bd_intf_pins -quiet -of_objects $space] \
                              [get_bd_intf_ports -quiet -of_objects $space]] {
                lappend reachable {*}[get_bd_addr_segs -quiet -addressable -of_objects $intf]
            }
            set hit 0
            foreach seg $reachable {
                if {$target eq "DRAM"} {
                    set hit [ipiasm::_is_dram_seg $seg]
                } else {
                    set hit [string match "/[string trimleft $target /]" "/[string trimleft $seg /]"]
                }
                if {$hit} { break }
            }
            if {$hit} { incr ok } else { lappend bad "[string trimleft $space /] -> $target" }
        }
    }
    if {[llength $bad]} {
        return "REACH_FAIL:[llength $bad] required path(s) cannot be addressed\n  [join $bad "\n  "]"
    }
    return "REACH:OK $ok space/target path(s) addressable"
}

# --- clock inputs with no driver, found before validate_bd_design (BD 41-758) ---
# A clock pin carried by a wired interface (a differential reference clock) is
# driven through that interface.
proc ipiasm::check_undriven_clocks {} {
    set open {}
    set defer {}
    set checked 0
    foreach cell [get_bd_cells -quiet -hier -filter {TYPE == ip}] {
        foreach pin [get_bd_pins -quiet -of_objects $cell -filter {TYPE == clk && DIR == I}] {
            incr checked
            if {[ipiasm::_scalar_in_wired_intf $pin]} { continue }
            if {[ipiasm::_clock_driver $pin] ne ""} { continue }
            set entry "[string trimleft $pin /] [ipiasm::_undriven_clock_hint $pin]"
            if {[ipiasm::_undriven_clock_deferred $pin]} {
                lappend defer $entry
            } else {
                lappend open $entry
            }
        }
    }
    if {[llength $open]} {
        return "UNDRIVEN_CLK_FAIL:[llength $open]/$checked clock input(s) have no driver\n  [join $open "\n  "]\n  -> drive each pin from the clock its interfaces' peers run on (ASSOCIATED_BUSIF):\
 wire_clk_rst_auto or fanout_domain for PL clocks, wire_mem_noc_sysclk for NoC sys_clk,\
 add_noc_si or attach_noc_port_clocks (after wiring) for a NoC port aclk -- one aclk per\
 port clock source; ports on different clocks never share one. An unused function's clock\
 still needs a driver; ask\
 before removing its cell."
    }
    if {[llength $defer]} {
        return "UNDRIVEN_CLK:OK [expr {$checked - [llength $defer]}]/$checked clock input(s) driven, [llength $defer] deferred until\
 their interfaces are wired\n  [join $defer "\n  "]\n  -> run attach_noc_port_clocks after the\
 wiring that feeds them, then re-run this check; a pin still listed then is undriven."
    }
    return "UNDRIVEN_CLK:OK $checked clock input(s) driven"
}

# --- top-level ports whose names the requirements fix ---
# ports: list of dicts {name <port> [dir I|O|IO] [type clk|rst|...] [freq_hz <n>]
#                       [width <bits>]}.
# A name the spec gives is a contract with the RTL top or software; a port that
# carries the right signal under another name still breaks it. Width is part of
# that contract: an RTL top declaring four one-bit interrupt inputs does not bind
# to one four-bit port, and the BD validates either way, so state width whenever
# the requirements fix the shape as well as the name.
proc ipiasm::check_required_ports {ports} {
    set bad {}
    foreach want $ports {
        if {![dict exists $want name]} { return "PORTS_FAIL:entry without name: $want" }
        set name [dict get $want name]
        set port [get_bd_ports -quiet -filter "NAME == \"$name\""]
        if {$port eq ""} { set port [get_bd_intf_ports -quiet -filter "NAME == \"$name\""] }
        if {$port eq ""} {
            set near {}
            foreach other [get_bd_ports -quiet] {
                set same 1
                foreach {field prop} {dir DIR type TYPE freq_hz CONFIG.FREQ_HZ} {
                    if {[dict exists $want $field] &&
                        [get_property -quiet $prop $other] ne [dict get $want $field]} { set same 0 }
                }
                if {$same && [dict size $want] > 1} { lappend near [get_property NAME $other] }
            }
            set hint [expr {[llength $near] ? " (same signal exists as {$near}: rename it)" : ""}]
            lappend bad "$name missing$hint"
            continue
        }
        foreach {field prop} {dir DIR type TYPE freq_hz CONFIG.FREQ_HZ} {
            if {![dict exists $want $field]} { continue }
            set have [get_property -quiet $prop $port]
            if {$have ne [dict get $want $field]} {
                lappend bad "$name $prop=$have, required [dict get $want $field]"
            }
        }
        if {[dict exists $want width]} {
            set have [ipiasm::_irq_width $port]
            set need [dict get $want width]
            if {$have ne $need} {
                lappend bad "$name is $have bit(s), required $need -> a vector does not\
 bind to separate one-bit RTL ports; create one port per bit (or one vector) to match"
            }
        }
        if {[llength [get_bd_nets -quiet -of_objects $port]] == 0 &&
            [llength [get_bd_intf_nets -quiet -of_objects $port]] == 0} {
            lappend bad "$name unconnected"
        }
    }
    if {[llength $bad]} {
        return "PORTS_FAIL:[llength $bad] problem(s) in [llength $ports] required port(s)\n  [join $bad "\n  "]"
    }
    return "PORTS:OK [llength $ports]"
}

# The object driving a clock: itself when it is a source, else its connected source.
proc ipiasm::_clock_driver {pin} {
    set class [get_property -quiet CLASS $pin]
    set dir [get_property -quiet DIR $pin]
    if {($class eq "bd_pin" && $dir eq "O") || ($class eq "bd_port" && $dir eq "I")} { return $pin }
    # Iterate the command result directly: copying it into a new list turns the
    # objects into strings that get_property -quiet silently cannot resolve.
    set peers {}
    catch {set peers [find_bd_objs -relation connected_to -thru_hier $pin]}
    foreach peer $peers {
        set class [get_property -quiet CLASS $peer]
        set dir [get_property -quiet DIR $peer]
        if {($class eq "bd_pin" && $dir eq "O") || ($class eq "bd_port" && $dir eq "I")} { return $peer }
    }
    return ""
}

# --- is this clock's driver simply not knowable yet? ---
# A clock follows its interfaces' peers, so a pin whose interfaces are all still
# unconnected has no driver to find: reporting it as undriven makes the clock
# stage fail for work the connect stage has not done yet. A NoC aclk is the same
# case one step earlier -- attach_noc_port_clocks assigns ports to free aclk
# indices as it wires them, so an unassociated aclk on a NoC with unwired AXI
# ports is still waiting for its port, not missing a driver.
# Deferral ends when the wiring happens: once an interface is connected the pin
# is judged normally. It also ends with the stages before wiring: from connect on,
# an interface still unwired never will be, so its clock is simply undriven and
# validate_bd_design would say so (BD 41-758).
proc ipiasm::_before_wiring_stage {} {
    variable stage_result
    variable stage_t
    set name ""
    if {[dict exists $stage_result stage]} { set name [dict get $stage_result stage] }
    if {$name eq "" && [dict exists $stage_t name]} { set name [dict get $stage_t name] }
    return [regexp -nocase {^(plan_)?(board|cell|inventory|clock)} $name]
}

proc ipiasm::_undriven_clock_deferred {pin} {
    if {![ipiasm::_before_wiring_stage]} { return 0 }
    set cell [get_bd_cells -quiet -of_objects $pin]
    set busifs [ipiasm::_associated_busifs $pin]
    if {[llength $busifs]} {
        foreach busif $busifs {
            set intf [get_bd_intf_pins -quiet $cell/$busif]
            if {$intf ne "" && [get_bd_intf_nets -quiet -of_objects $intf] ne ""} { return 0 }
        }
        return 1
    }
    if {![string match "aclk*" [file tail $pin]]} { return 0 }
    if {[lsearch -exact [ipiasm::_all_noc_cells] "$cell"] < 0} { return 0 }
    foreach p [get_bd_intf_pins -quiet -of_objects $cell] {
        set port [file tail $p]
        if {![string match "S*_AXI" $port] && ![string match "M*_AXI" $port]} { continue }
        if {[get_bd_intf_nets -quiet -of_objects $p] eq ""} { return 1 }
    }
    return 0
}

# Name the clock an undriven pin should carry: the source clock of the peers of
# the interfaces listed in its ASSOCIATED_BUSIF.
proc ipiasm::_undriven_clock_hint {pin} {
    set busifs [ipiasm::_associated_busifs $pin]
    if {![llength $busifs]} { return "carries no interface" }
    set cell [get_bd_cells -quiet -of_objects $pin]
    set sources {}
    foreach busif $busifs {
        set intf [get_bd_intf_pins -quiet $cell/$busif]
        if {$intf eq ""} { continue }
        set peers {}
        catch {set peers [find_bd_objs -relation connected_to -thru_hier $intf]}
        foreach peer $peers {
            if {"$peer" eq "$intf"} { continue }
            foreach clock [ipiasm::_associated_scalars $peer clk] {
                set source [string trimleft [ipiasm::_clock_driver $clock] /]
                if {$source ne "" && $source ni $sources} { lappend sources $source }
            }
        }
    }
    set text "carries {$busifs}"
    if {[llength $sources] == 1} {
        append text " -> drive from [lindex $sources 0]"
    } elseif {[llength $sources] > 1} {
        append text " -> peers run on {$sources}; share one clock or insert CDC"
    } else {
        append text " -> peer clock unresolved (interface unconnected or peer has no associated clock)"
    }
    return $text
}

# --- does the memory NoC publish a DRAM segment for every DesignSpec region? ---
# regions: {<name> <size> ...}, or DesignSpec {name base size} dicts (then a failure
# also names the selectors that would serve the missing regions). Before addressing
# a published segment carries only its RANGE, so each region needs its own segment
# at least that large; exact bases are proven by the address stage.
proc ipiasm::check_memory_regions {regions} {
    if {[catch {ipiasm::_region_dicts $regions} dicts]} { return "MEM_REGION_FAIL:$dicts" }
    if {$dicts ne ""} {
        set spec $regions
        set regions {}
        foreach region $dicts { lappend regions [lindex $region 0] [lindex $region 2] }
    }
    if {[llength $regions] % 2} {
        return "MEM_REGION_FAIL:regions must be a flat {name size ...} list or {name base size} dicts"
    }
    if {![llength $regions]} { return "MEM_REGION:NA DesignSpec states no memory regions" }
    set wanted {}
    foreach {name size} $regions {
        if {[catch {expr {$size + 0}} bytes] || $bytes <= 0} {
            return "MEM_REGION_FAIL:region $name has invalid size '$size'"
        }
        lappend wanted [list $name $bytes]
    }
    set segs {}
    foreach seg [ipiasm::_noc_ddr_segs] {
        if {![catch {expr {[get_property -quiet RANGE $seg] + 0}} range]} {
            lappend segs [list $seg $range]
        }
    }
    set segs [lsort -integer -index 1 $segs]
    set bad {}
    set held {}
    foreach region [lsort -integer -decreasing -index 1 $wanted] {
        lassign $region name bytes
        set pick -1
        for {set i 0} {$i < [llength $segs]} {incr i} {
            if {[lindex $segs $i 1] >= $bytes} { set pick $i; break }
        }
        if {$pick < 0} {
            lappend bad "$name size=[format 0x%llX $bytes] (no unused DRAM segment that large)"
            continue
        }
        lappend held "$name->[file tail [lindex $segs $pick 0]]"
        set segs [lreplace $segs $pick $pick]
    }
    if {[llength $bad]} {
        set hint "  -> pass the DesignSpec regions as {name base size} dicts to get the exact MC_CHAN_REGION selectors"
        if {$dicts ne ""} {
            set hint ""
            foreach noc [get_bd_cells -quiet -filter {VLNV =~ *:axi_noc*}] {
                if {[catch {expr {[get_property -quiet CONFIG.NUM_MC $noc] + 0}} mc] || $mc <= 0} { continue }
                set current {}
                foreach key [lsort [list_property $noc CONFIG.MC_CHAN_REGION*]] {
                    lappend current "[regsub {^CONFIG\.} $key {}]=[get_property -quiet $key $noc]"
                }
                set chosen [ipiasm::region_selectors $noc $spec]
                set picked {}
                regexp {selectors=\{([^\}]*)\}} $chosen -> picked
                set drift 0
                foreach {key value} $picked {
                    if {[get_property -quiet $key $noc] ne $value} { set drift 1 }
                }
                if {[llength $picked] && !$drift} {
                    # Selectors are right: the NoC publishes no segment until a
                    # NoC input routes to an MC port, so fix routing, not selectors.
                    set ingress [ipiasm::_mc_noc_axi_ingress]
                    set routes {}
                    foreach p [get_bd_intf_pins -quiet -of_objects $noc -filter {MODE == Slave}] {
                        set dests {}
                        foreach {dest qos} [get_property -quiet CONFIG.CONNECTIONS $p] { lappend dests $dest }
                        lappend routes "[file tail $p]->{[join $dests { }]}"
                    }
                    append hint "\n  -> $noc selectors are already correct {[join $current {, }]}; do NOT rebuild them. A DRAM segment is published only after a NoC input routes to an MC port (CONFIG.CONNECTIONS), so this is a routing gap: $noc inputs route {[join $routes {, }]} (MC-routed AXI slaves design-wide: {[join $ingress { }]}) -- route an existing input with ipiasm::set_qos [string trimleft $noc /]/<input> MC_<n> <rd_bw> <wr_bw>, or add one with ipiasm::add_noc_si [string trimleft $noc /] MC_<n> <master intf pin> (or check the automation that should have done it), then re-run ipiasm::check_memory_regions"
                } elseif {[regexp {ranges=\{([^\}]*)\}} $chosen -> needed]} {
                    append hint "\n  -> $noc has {[join $current {, }]}; the regions need {$needed}: rebuild its board bind with ipiasm::attach_board_memory [string trimleft $noc /] $mc <the same DesignSpec regions> (a later set_property on the selectors resets board-preset keys)"
                } else {
                    append hint "\n  -> $noc has {[join $current {, }]}; $chosen"
                }
            }
        }
        return "MEM_REGION_FAIL:[llength $bad]/[llength $wanted] region(s) not published\n  [join $bad "\n  "]$hint"
    }
    return "MEM_REGION:OK [llength $wanted] region(s) held={$held}"
}

# --- which NoC memory board interfaces are actually bound, design-wide ---
# Read off the same generic hook ipcfg::board_bind writes, so this reports the
# real cell state rather than whatever a ledger remembers -- it has to stay
# right for a design assembled across several sessions, or restored from disk.
proc ipiasm::_bound_mem_intfs {} {
    set out {}
    foreach c [get_bd_cells -quiet] {
        if {![string match "*:axi_noc*" [get_property -quiet VLNV $c]]} { continue }
        set props {}
        catch {set props [list_property $c]}
        foreach p $props {
            if {![string match "CONFIG.C*_BOARD_INTERFACE" $p]} { continue }
            set v ""
            catch {set v [get_property -quiet $p $c]}
            if {$v eq "" || $v eq "Custom"} { continue }
            lappend out "[string trimleft $c /]:$v"
        }
    }
    return [lsort -unique $out]
}

# --- AXI slave ports that declare a memory-controller destination directly ---
# Report the live structure; the caller must compare it with current product
# documentation and automation output rather than applying a stored topology.
proc ipiasm::_mc_noc_axi_ingress {} {
    set out {}
    foreach c [ipiasm::_all_noc_cells] {
        set mc 0
        catch {set mc [get_property -quiet CONFIG.NUM_MC $c]}
        if {![string is integer -strict $mc] || $mc <= 0} { continue }
        foreach p [get_bd_intf_pins -quiet $c/*] {
            if {[get_property -quiet MODE $p] ne "Slave"} { continue }
            if {[string match "*_INI" [file tail $p]]} { continue }
            set conn ""
            catch {set conn [get_property -quiet CONFIG.CONNECTIONS $p]}
            if {[string match "*MC_*" $conn]} {
                lappend out "[string trimleft $c /]/[file tail $p]"
            }
        }
    }
    return $out
}

# Check the number of memory controllers (want_mc 0, 1, 2 or 4) and that memory responds.
proc ipiasm::check_memory_subsystem {want_mc} {
    if {$want_mc ni {0 1 2 4}} {
        return "MEM_FAIL:want_mc=$want_mc is not a legal NUM_MC (PG313: must be 0, 1, 2, or 4)"
    }
    set total 0
    set detail {}
    set responders {}
    foreach c [get_bd_cells -quiet] {
        set vlnv [get_property -quiet VLNV $c]
        if {[string match "*:ddrmc*_responder:*" $vlnv]} {
            lappend responders [string trimleft $c /]
            continue
        }
        if {![string match "*:axi_noc*" $vlnv]} { continue }
        set mc 0
        catch {set mc [get_property -quiet CONFIG.NUM_MC $c]}
        if {![string is integer -strict $mc]} { set mc 0 }
        if {$mc > 0} {
            set mcp ""
            catch {set mcp [get_property -quiet CONFIG.NUM_MCP $c]}
            lappend detail "[string trimleft $c /]:NUM_MC=$mc,NUM_MCP=$mcp"
            incr total $mc
        }
    }
    # The controller count agreeing with the request is necessary and NOT
    # sufficient -- see _noc_ddr_segs. Check for the published windows too, or
    # this gate hands out MEM:OK on a design where nothing can reach DRAM.
    if {$total == $want_mc} {
        # A controller bound to a board memory interface still needs its memory
        # pins exported, or the DRAM has no package pins in the wrapper.
        set unexported {}
        foreach c [get_bd_cells -quiet] {
            if {![string match "*:axi_noc*" [get_property -quiet VLNV $c]]} { continue }
            set props {}
            catch {set props [list_property -quiet $c CONFIG.C*_BOARD_INTERFACE]}
            foreach p $props {
                set bi [get_property -quiet $p $c]
                if {$bi eq "" || $bi eq "Custom"} { continue }
                set pin [get_bd_intf_pins -quiet $c/[regsub {_BOARD_INTERFACE$} [regsub {^CONFIG\.} $p {}] {}]]
                if {$pin ne "" && ![llength [get_bd_intf_nets -quiet -of_objects $pin]]} {
                    lappend unexported "[string trimleft $pin /]<-$bi"
                }
            }
        }
        if {[llength $unexported]} {
            return "MEM_FAIL:[llength $unexported] board-bound memory interface(s) not exported: {[join $unexported {, }]}\n  -> connect each with ipiasm::connect_board_intf <board_intf> <noc>/<pin> (attach_board_memory does this); it creates the top-level memory port"
        }
        set ddr [ipiasm::_noc_ddr_segs]
        if {$total > 0 && ![llength $ddr]} {
            # Two different designs land here and they need OPPOSITE advice, so
            # tell them apart before saying anything. If the controllers are
            # already bound to board memory interfaces, re-attaching is a no-op. In that
            # state the window is missing because no master reaches the NoC yet
            # and nothing has been addressed -- not because memory is absent.
            if {[llength [ipiasm::_bound_mem_intfs]] > 0} {
                set direct [ipiasm::_mc_noc_axi_ingress]
                if {[llength $direct]} {
                    return "MEM_FAIL:num_mc=$total matches want=$want_mc and the controllers\
ARE bound {[join [ipiasm::_bound_mem_intfs] {, }]}, but the memory NoC publishes no address\
segment at all and these AXI slave ports are trying to reach a controller directly:\
[join $direct {, }].\n  -> do NOT re-run attach_board_memory and do NOT re-address; neither\
creates the aperture. Inspect the current automation schema and product documentation\
for the legal memory-controller ingress topology; do not copy one from another design."
                }
                return "MEM_FAIL:num_mc=$total matches want=$want_mc and the controllers ARE\
 bound to board memory {[join [ipiasm::_bound_mem_intfs] {, }]}, but no addressable DRAM\
 window exists yet {[join $detail {; }]}\n  -> do NOT re-run attach_board_memory; it already\
 succeeded and repeating it changes nothing. Two things keep the aperture from appearing\
 once the controllers are bound, in this order: (1) the NoC compiler REFUSED a route, so its\
 solution never resolves and it publishes nothing -- check with ipiasm::check_noc_legality;\
 (2) no master reaches this NoC yet -- connect one (ipiasm::connect_axi_auto) and then\
 ipiasm::auto_address. Re-run this gate after that; if auto_address returns ADDR_FAIL saying\
 there was no window to map, it is (1)."
            }
            return "MEM_FAIL:num_mc=$total matches want=$want_mc, but the NoC publishes\
NO addressable DRAM window {[join $detail {; }]}\n  -> NUM_MC is set and the\
controller is not there. Nothing was attached to the board's memory interface, so\
`assign_bd_address` has no DDR segment to map and every master loses DRAM while\
validate_bd_design still passes. Call ipiasm::attach_board_memory <noc_cell>\
(or re-run the MC automation) and re-address; then this returns the\
<noc>/DDR_MC_PORTS/DDR_CH0_* segments."
        }
        set mode [expr {$total >= 2 ? "interleaved" : "single"}]
        # The memory subsystem can be entirely correct while nothing can use it:
        # controllers bound, interleaved, windows published, and not one master
        # holding an aperture. That is a routing/addressing defect rather than a
        # memory one, so it stays MEM:OK -- but MEM:OK must not be readable as
        # "memory works".
        set reach ""
        if {[llength $ddr] && ![llength [ipiasm::_dram_masters]]} {
            set reach " | NOTE: the memory is attached correctly and NO master\
can reach it -- not one address space holds any of these window(s). That is\
downstream of this gate: run ipiasm::check_noc_solution, which names the cause."
        }
        # Which address regions the controllers actually serve. A channel serves
        # the regions named by its MC_CHAN_REGION* properties and no others, so
        # a controller count and a published window can both be right while part
        # of the attached DRAM has no address at all -- and that reads as MEM:OK
        # because a window does exist. Disclosed rather than judged: how much
        # capacity a region covers is a board fact, so the agent compares this
        # against the memory the board declares.
        set regions {}
        foreach c [get_bd_cells -quiet] {
            if {![string match "*:axi_noc*" [get_property -quiet VLNV $c]]} {
                continue
            }
            set mc 0
            catch {set mc [get_property -quiet CONFIG.NUM_MC $c]}
            if {![string is integer -strict $mc] || $mc <= 0} { continue }
            set props {}
            catch {set props [lsort [list_property -quiet $c]]}
            foreach p $props {
                if {![string match "CONFIG.MC_CHAN_REGION*" $p]} { continue }
                set v [get_property -quiet $p $c]
                lappend regions "[regsub {^CONFIG\.} $p ""]=$v"
            }
        }
        set regionnote ""
        if {[llength $regions] > 0} {
            set regionnote " | channel regions {[join $regions {, }]}"
        }
        return "MEM:OK num_mc=$total ($mode) ddr_windows=[llength $ddr] {[join $detail {; }]}$regionnote$reach"
    }
    # A responder is a memory model, not the integrated controller. PG313: "You
    # must use the NoC IP to communicate with the integrated DDR memory controller."
    set note ""
    if {[llength $responders] > 0} {
        set note " | responder cell(s) {[join $responders {, }]} are memory models, NOT integrated controllers"
    }
    return "MEM_FAIL:num_mc=$total want=$want_mc {[join $detail {; }]}$note\n  -> re-run the MC automation with the controller count in its -config (e.g. ps_wizard {mc_type LPDDR5 num_mc_lpddr $want_mc}); do not hand-set CONFIG.NUM_MC, the automation owns it."
}

# ============================================================
# 6b. NoC quality of service -- the traffic specification
# ============================================================
# UG994 "Creating a Design with NoC IP": the axi_noc/axis_noc cells are only a
# LOGICAL view of one physical NoC. IP integrator "automatically aggregates the
# connectivity and QoS information from all of the logical NoC instances to form
# a unified traffic specification", and the NoC compiler solves against that
# aggregate alone. How many NoC cells the design is split into is therefore a
# readability choice that does not reach the hardware; what reaches the hardware
# is the set of connections and the QoS on each one.
#
# UG994 / PG313 / PG406 "Quality of Service" define a QoS requirement as:
#   * read and write bandwidth, in MB/s, associated with the egress (slave) end
#     of the connection -- each connection may differ in each direction;
#   * a traffic class, set at the NMU (the master end) and applying to every
#     path out of that NMU: LOW_LATENCY, ISOCHRONOUS or BEST_EFFORT.
# The NoC_HBM QoS tutorial states the rule plainly: "the QoS bandwidth
# requirement should be set to reflect the average data bandwidth the connection
# is expected to consume".
#
# Vivado seeds every new connection at 500 MB/s read / 500 MB/s write with an
# average burst of 4. That is a placeholder, not a measurement. Leaving it in
# place under-declares a real video pipeline several times over, so the NoC
# compiler optimizes for traffic the design does not have and the NoC it builds
# cannot carry the traffic the design does have. Nothing warns about it: the
# design validates, addresses and routes perfectly clean. Treat a design still
# sitting on 500/500 the same way as NUM_MC=1 -- silently wrong.
#
# Where the data lives (discovered on axi_noc2 in 2026.1, and matching the
# current design's exported property syntax):
#   * CONFIG.CONNECTIONS is a property of the INTERFACE PIN, not of the cell.
#     Setting it on the cell is accepted and does nothing. It is a dict
#       {<dest> {read_bw {N} write_bw {N} read_avg_burst {N} write_avg_burst {N}} ...}
#     where <dest> is a memory controller (MC_0), a NoC master port (M00_AXI) or
#     an inter-NoC link (M00_INI).
#   * CONFIG.R_TRAFFIC_CLASS / CONFIG.W_TRAFFIC_CLASS sit on the same pin and
#     accept exactly BEST_EFFORT, ISOCHRONOUS, LOW_LATENCY.

# --- bandwidth of an uncompressed video flow, in MB/s ---
# The pixel-rate arithmetic behind every camera/ISP/display path:
#   MB/s = width * height * fps * bits_per_pixel / 8 / 1e6
# `overhead_pct` pads for blanking and burst inefficiency (10% by default).
# Rounds up, so rounding can only ever over-declare bandwidth, never starve it.
proc ipiasm::bw_video {w h fps bpp {overhead_pct 10}} {
    foreach {label v} [list width $w height $h fps $fps bpp $bpp] {
        if {![string is double -strict $v] || $v <= 0} {
            return -code error "bw_video: $label must be a positive number, got '$v'"
        }
    }
    set mbs [expr {double($w) * $h * $fps * $bpp / 8.0 / 1000000.0}]
    return [expr {int(ceil($mbs * (100.0 + $overhead_pct) / 100.0))}]
}

# --- bandwidth of a MIPI CSI-2 D-PHY link, in MB/s ---
# MB/s = lanes * line_rate_Mbps / 8. Use this when the spec names a camera but
# no frame geometry: the configured link (CMN_NUM_LANES x DPY_LINE_RATE on the
# CSI-2 RX subsystem) is a number the design already carries, and it bounds
# every downstream capture path. Prefer bw_video when the resolution and frame
# rate are actually known -- the link rate is a ceiling, not a measurement.
proc ipiasm::bw_mipi {lanes line_rate_mbps} {
    foreach {label v} [list lanes $lanes line_rate_mbps $line_rate_mbps] {
        if {![string is double -strict $v] || $v <= 0} {
            return -code error "bw_mipi: $label must be a positive number, got '$v'"
        }
    }
    return [expr {int(ceil(double($lanes) * $line_rate_mbps / 8.0))}]
}

# --- bandwidth ceiling of an AXI port, in MB/s ---
# MB/s = clock_MHz * (data_width_bits / 8) * utilisation. Use this for the
# streaming ports whose traffic is set by the link, not by a frame rate.
proc ipiasm::bw_axi {freq_mhz data_width_bits {utilization_pct 100}} {
    foreach {label v} [list freq_mhz $freq_mhz data_width_bits $data_width_bits] {
        if {![string is double -strict $v] || $v <= 0} {
            return -code error "bw_axi: $label must be a positive number, got '$v'"
        }
    }
    set mbs [expr {double($freq_mhz) * ($data_width_bits / 8.0) * $utilization_pct / 100.0}]
    return [expr {int(ceil($mbs))}]
}

# --- the legal traffic classes, per PG313/PG406 ---
proc ipiasm::qos_classes {} { return {BEST_EFFORT ISOCHRONOUS LOW_LATENCY} }

# --- set QoS on one NoC connection ---
#   si_pin   : the NoC slave interface pin carrying the master (noc/S03_AXI)
#   dest     : the connectivity target (MC_0, M00_AXI, M00_INI)
#   read_bw  / write_bw : MB/s the connection is expected to consume
#   tclass   : traffic class for this NMU, or "" to leave it as-is
#   burst    : average burst length in beats
# Merges into the pin's existing CONNECTIONS dict so sibling destinations and
# any keys we do not manage survive untouched.
proc ipiasm::set_qos {si_pin dest read_bw write_bw {tclass ""} {burst 16} {initial_boot ""}} {
    if {$dest eq ""} { return "QOS_FAIL:$si_pin needs a destination" }
    # Any Tcl boolean: a plan's JSON true arrives as 1. The NoC property takes true/false.
    if {$initial_boot ne ""} {
        if {![string is boolean -strict $initial_boot]} {
            return "QOS_FAIL:$si_pin initial_boot must be true or false"
        }
        set initial_boot [expr {$initial_boot ? "true" : "false"}]
    }
    foreach {label v} [list read $read_bw write $write_bw] {
        if {![string is integer -strict $v] || $v < 0} {
            return "QOS_FAIL:$si_pin ${label}_bw must be a non-negative integer MB/s, got '$v'"
        }
    }
    if {![string is integer -strict $burst] || $burst < 1} {
        return "QOS_FAIL:$si_pin burst must be a positive integer, got '$burst'"
    }
    if {$tclass ne "" && $tclass ni [ipiasm::qos_classes]} {
        return "QOS_FAIL:$si_pin traffic class '$tclass' is not one of [join [ipiasm::qos_classes] {, }]"
    }
    if {$tclass eq "LOW_LATENCY" && $write_bw > 0} {
        return "QOS_FAIL:$si_pin -> $dest LOW_LATENCY is read-only but write_bw=$write_bw\
 MB/s; use BEST_EFFORT/ISOCHRONOUS for a bidirectional or write path"
    }
    set p [get_bd_intf_pins -quiet $si_pin]
    if {$p eq ""} { return "QOS_FAIL:no interface pin $si_pin" }

    set conn ""
    catch {set conn [get_property -quiet CONFIG.CONNECTIONS $p]}
    if {[catch {dict size $conn}]} { set conn "" }
    set sub {}
    if {$conn ne "" && [dict exists $conn $dest]} { set sub [dict get $conn $dest] }
    if {[catch {dict size $sub}]} { set sub {} }
    dict set sub read_bw $read_bw
    dict set sub write_bw $write_bw
    dict set sub read_avg_burst $burst
    dict set sub write_avg_burst $burst
    if {$initial_boot ne ""} { dict set sub initial_boot $initial_boot }
    dict set conn $dest $sub

    # Brace-wrapped, always: see ipiasm::_conn_braced. Handing the raw dict to
    # set_property writes the form axi_noc2 discards without saying so.
    set props [list CONFIG.CONNECTIONS [ipiasm::_conn_braced $conn]]
    # PG313/PG406: the traffic class belongs to the NMU and covers every path
    # leaving it, so two destinations on one SI pin cannot disagree. Say so
    # rather than letting the last writer win silently.
    set clash ""
    if {$tclass ne ""} {
        set prior ""
        catch {set prior [get_property -quiet CONFIG.R_TRAFFIC_CLASS $p]}
        if {$prior ne "" && $prior ne $tclass && [llength [dict keys $conn]] > 1} {
            set clash " (NOTE: NMU class $prior->$tclass also moves the other destinations on $si_pin)"
        }
        if {$tclass eq "LOW_LATENCY"} {
            lappend props CONFIG.R_TRAFFIC_CLASS LOW_LATENCY \
                CONFIG.W_TRAFFIC_CLASS BEST_EFFORT
        } else {
            lappend props CONFIG.R_TRAFFIC_CLASS $tclass \
                CONFIG.W_TRAFFIC_CLASS $tclass
        }
    }
    if {[catch {set_property -dict $props $p} e]} {
        return "QOS_FAIL:$si_pin -> $dest: $e"
    }
    # PG313: zero bandwidth disables that direction, and any later access on it
    # hangs the NoC. Legal, but never something to arrive at by accident.
    set warn ""
    if {$read_bw == 0 && $write_bw == 0} {
        set warn " (WARNING: 0 both ways accepts no traffic at all; any access on this path hangs)"
    } elseif {$read_bw == 0 || $write_bw == 0} {
        set warn " (NOTE: a 0 direction accepts no traffic -- right for a one-way master, a hang for anything else)"
    }
    # Writing to a pin that does not own the path is accepted by Vivado and then
    # discarded by the compiler, so say it here rather than let the value look
    # applied. Declare on the owning ingress -- the NMU the traffic starts at.
    set own [ipiasm::qos_owner $p]
    if {$own eq "ignored"} {
        append warn " (NOTE: $si_pin does not own this path, so PG313 says the\
 bandwidth and traffic class are IGNORED here -- the declaration that binds has\
 to go on the owning NMU as well. Writing a real number here is still better\
 than leaving the 500/500 seed, which reads as unconfigured.)"
    } elseif {$own eq "unresolved"} {
        append warn " (NOTE: path ownership for $si_pin is still 'auto'; validate,\
 then re-check with ipiasm::check_qos)"
    }
    return "SUCCESS:qos $si_pin -> $dest rd=${read_bw}MB/s wr=${write_bw}MB/s burst=$burst class=[expr {$tclass eq "" ? "unchanged" : $tclass}]$clash$warn"
}

# --- how many slave segments are currently mapped into a master's space ---
proc ipiasm::_mapped_seg_count {} {
    set n 0
    foreach space [get_bd_addr_spaces -quiet] {
        incr n [llength [get_bd_addr_segs -quiet -of_objects $space]]
    }
    return $n
}

# --- which MC_CHAN_REGION selector each NoC channel currently carries ---
# A QoS row writes CONFIG.CONNECTIONS, and the same connectivity edit that can
# unmap address segments can also return a channel selector to its IP default.
# The design still validates and the window still exists, so the loss is silent
# unless the values are read before and after. Returns {cell prop value ...}.
proc ipiasm::_region_selectors {} {
    set out {}
    foreach cell [lsort [get_bd_cells -quiet]] {
        if {![string match "*:axi_noc*" [get_property -quiet VLNV $cell]]} { continue }
        foreach prop [lsort [list_property -quiet $cell CONFIG.MC_CHAN_REGION*]] {
            lappend out [list $cell $prop [get_property -quiet $prop $cell]]
        }
    }
    return $out
}

# --- apply a whole traffic specification ---
# plan rows: {si_pin dest read_bw write_bw ?class? ?burst?}
# Applies every row and reports per-row, so one bad row cannot quietly leave the
# rest of the spec at the 500/500 default.
#
# ORDERING: run this BEFORE assign_bd_address. Vivado treats a CONFIG.CONNECTIONS
# write as a connectivity change, and on an already-addressed design it answers
# with [BD 41-1931] "the CONNECTIVITY change has put the mapped segment ... out
# of scope. Please unmap and remap the segments" -- observed to drop the DDR
# segments out of every PS address space at once. Those warnings scroll past in
# a long build and the design still validates, so the count is checked here
# instead of trusted to be read.
proc ipiasm::apply_qos_plan {plan} {
    set mapped_before [ipiasm::_mapped_seg_count]
    set regions_before [ipiasm::_region_selectors]
    set ok 0
    set failed {}
    set lines {}
    foreach row $plan {
        if {[llength $row] < 4} {
            lappend failed "malformed row {$row} (need si_pin dest read_bw write_bw ?class? ?burst?)"
            continue
        }
        lassign $row si dest rb wb tc burst
        if {$tc eq ""} { set tc "" }
        if {$burst eq ""} { set burst 16 }
        set r [ipiasm::set_qos $si $dest $rb $wb $tc $burst]
        lappend lines "  $r"
        if {[string match "SUCCESS:*" $r]} { incr ok } else { lappend failed $r }
    }
    set head "QOS:applied $ok/[llength $plan]"
    set mapped_after [ipiasm::_mapped_seg_count]
    if {$mapped_after < $mapped_before} {
        set lost [expr {$mapped_before - $mapped_after}]
        return "QOS_FAIL:$head but the connectivity write unmapped $lost address\
segment(s) ($mapped_before -> $mapped_after) \[BD 41-1931\]\n[join $lines \n]\n  -> the\
design was already addressed. Re-run the address assignment (ipiasm::auto_address /\
place_named_windows) and confirm the memory segments came back, then apply QoS BEFORE\
addressing next time."
    }
    set regions_after [ipiasm::_region_selectors]
    if {$regions_after ne $regions_before} {
        set lost {}
        foreach was $regions_before {
            lassign $was cell prop value
            set now ""
            foreach is $regions_after {
                if {[lrange $is 0 1] eq [list $cell $prop]} { set now [lindex $is 2] }
            }
            if {$now ne $value} {
                lappend lost "$cell [regsub {^CONFIG\.} $prop ""] $value -> [expr {$now eq "" ? "(gone)" : $now}]"
            }
        }
        if {[llength $lost]} {
            return "QOS_FAIL:$head but the connectivity write changed [llength $lost] channel\
region selector(s) {[join $lost {; }]}\n[join $lines \n]\n  -> a channel serves only the\
regions its MC_CHAN_REGION* properties name, so part of the attached DRAM now has no\
address even though the design still validates. Re-apply the selectors (ipiasm::attach_board_memory\
with the DesignSpec regions) and re-check with ipiasm::check_memory_regions."
        }
    }
    if {[llength $failed]} {
        return "QOS_FAIL:$head\n[join $lines \n]"
    }
    return "$head\n[join $lines \n]"
}

# --- the master feeding a NoC slave interface pin ---
proc ipiasm::_intf_source {pin} {
    set net [get_bd_intf_nets -quiet -of $pin]
    if {$net eq ""} { return "" }
    foreach p [get_bd_intf_pins -quiet -of $net] {
        if {"$p" eq "$pin"} { continue }
        if {[get_property -quiet MODE $p] eq "Master"} { return $p }
    }
    return ""
}

# --- does this ingress OWN the QoS for its paths? ---
# PG313 (QoS Tab): "QoS is ignored when a NoC instance does not own the path...
# If Owns QoS and Path is `no` then all settings BW and traffic class is
# ignored." Ownership sits at the transition from an NMU (or an INI with
# strategy=Driver) to an NSU, MC, or an INI with strategy=Load.
#
# An AXI ingress can carry CONFIG.HAS_QOS=1, while an
# INI pin has no HAS_QOS property at all and instead carries INI_STRATEGY /
# COMPUTED_STRATEGY. A value on a non-owning INI may never reach the compiler.
# Returns: own | ignored | unresolved (strategy still "auto", so unprovable).
proc ipiasm::qos_owner {pin} {
    set p [get_bd_intf_pins -quiet $pin]
    if {$p eq ""} { return "unresolved" }
    set hq ""
    catch {set hq [get_property -quiet CONFIG.HAS_QOS $p]}
    if {$hq eq "1" || [string equal -nocase $hq true]} { return "own" }
    # An INI ingress owns the path only when it is the Driver end.
    set st ""
    catch {set st [get_property -quiet CONFIG.COMPUTED_STRATEGY $p]}
    if {$st eq "" || [string equal -nocase $st auto]} {
        set st ""
        catch {set st [get_property -quiet CONFIG.INI_STRATEGY $p]}
    }
    if {[string equal -nocase $st driver]} { return "own" }
    if {[string equal -nocase $st load]}   { return "ignored" }
    if {$st eq "" || [string equal -nocase $st auto]} {
        # No HAS_QOS and no resolved strategy. An INI pin here is unproven; any
        # other slave pin without HAS_QOS is not a QoS owner.
        if {[string match "*_INI" [get_property -quiet NAME $p]]} { return "unresolved" }
        return "ignored"
    }
    return "unresolved"
}

# --- dump the aggregate traffic specification ---
# Every NoC connection in the design, with the master that actually drives it
# and whether this ingress owns the QoS (see ipiasm::qos_owner -- a declaration
# on a non-owning pin is discarded by the compiler).
# This is the view the NoC compiler works from, flattened across NoC instances
# exactly the way IP integrator flattens it.
proc ipiasm::noc_traffic_spec {} {
    set rows {}
    foreach c [get_bd_cells -hierarchical -quiet] {
        if {![string match "*:axi_noc*" [get_property -quiet VLNV $c]]} { continue }
        foreach p [get_bd_intf_pins -quiet -of $c] {
            if {[get_property -quiet MODE $p] ne "Slave"} { continue }
            set conn ""
            catch {set conn [get_property -quiet CONFIG.CONNECTIONS $p]}
            if {$conn eq "" || [catch {dict size $conn}]} { continue }
            set rtc ""; set wtc ""
            catch {set rtc [get_property -quiet CONFIG.R_TRAFFIC_CLASS $p]}
            catch {set wtc [get_property -quiet CONFIG.W_TRAFFIC_CLASS $p]}
            set src [ipiasm::_intf_source $p]
            if {$src eq ""} { set src "-" }
            set own [ipiasm::qos_owner $p]
            foreach dest [dict keys $conn] {
                set sub [dict get $conn $dest]
                set rb "?"; set wb "?"
                catch {set rb [dict get $sub read_bw]}
                catch {set wb [dict get $sub write_bw]}
                lappend rows [list [string trimleft $p /] $dest $rb $wb $rtc $wtc \
                                   [string trimleft $src /] $own]
            }
        }
    }
    return $rows
}

# --- plan/remove one logical path through a NoC ingress ----------------------
# A NoC path is four coupled pieces of state: the interface net, one or more
# CONFIG.CONNECTIONS/QoS destinations, Address Editor mappings, and the ingress
# name on a NoC aclk's ASSOCIATED_BUSIF. Deleting only the net leaves stale
# state. Planning is read-only; removal is dry-run unless -apply is present.
proc ipiasm::_noc_remove_info {noc_port {destination ""}} {
    set pin [get_bd_intf_pins -quiet $noc_port]
    if {$pin eq ""} { return -code error "no NoC interface pin $noc_port" }
    set noc [lindex [get_bd_cells -quiet -of_objects $pin] 0]
    set family [get_property -quiet VLNV $noc]
    if {![string match "*:axi_noc*:*" $family]} {
        return -code error "$noc_port belongs to $family, not an AXI NoC"
    }
    set conn ""
    catch {set conn [get_property -quiet CONFIG.CONNECTIONS $pin]}
    if {$conn eq "" || [catch {dict size $conn}]} { set conn {} }
    set destinations [dict keys $conn]
    if {$destination ne "" && ![dict exists $conn $destination]} {
        return -code error "$noc_port has destinations={$destinations}, not '$destination'"
    }
    set remove_dests [expr {$destination eq "" ? $destinations : [list $destination]}]
    set remain $conn
    foreach dest $remove_dests { dict unset remain $dest }

    set net [get_bd_intf_nets -quiet -of_objects $pin]
    set source [ipiasm::_intf_source $pin]
    set source_cell ""
    set source_family ""
    if {$source ne ""} {
        set source_cell [lindex [get_bd_cells -quiet -of_objects $source] 0]
        if {$source_cell ne ""} {
            set source_family [get_property -quiet VLNV $source_cell]
        }
    }
    set hardened 0
    foreach kind {ps_wizard visp_ss ai_engine vcu2} {
        if {[string match "*:${kind}:*" $source_family]} { set hardened 1 }
    }
    set clocks {}
    set port_name [get_property -quiet NAME $pin]
    foreach clk [get_bd_pins -quiet "$noc/aclk*"] {
        set assoc [split [get_property -quiet CONFIG.ASSOCIATED_BUSIF $clk] :]
        if {[lsearch -exact $assoc $port_name] >= 0} { lappend clocks $clk }
    }
    set spaces {}
    set mapped 0
    if {$source ne ""} {
        set spaces [get_bd_addr_spaces -quiet -of_objects $source]
    }
    if {![llength $spaces] && $source_cell ne ""} {
        set spaces [get_bd_addr_spaces -quiet -of_objects $source_cell]
    }
    if {[llength $spaces]} {
        foreach space $spaces {
            incr mapped [llength [get_bd_addr_segs -quiet -of_objects $space]]
        }
    }
    return [dict create pin $pin noc $noc net $net source $source \
        source_cell $source_cell source_family $source_family hardened $hardened \
        destinations $destinations remove $remove_dests remain $remain \
        clocks $clocks spaces $spaces mapped $mapped]
}

# Read-only preview of what remove_noc_path would disconnect and what would remain.
proc ipiasm::plan_remove_noc_path {noc_port {destination ""}} {
    if {[catch {set info [ipiasm::_noc_remove_info $noc_port $destination]} e]} {
        return "REMOVE_PLAN_FAIL:$e"
    }
    return "REMOVE_PLAN:port=$noc_port source=[dict get $info source]\
 destinations={[dict get $info remove]} remaining={[dict keys [dict get $info remain]]}\
 net=[dict get $info net] clocks={[dict get $info clocks]}\
 address_spaces=[llength [dict get $info spaces]] mapped=[dict get $info mapped]\
 hardened=[dict get $info hardened]"
}

proc ipiasm::_prune_unaddressable {spaces} {
    set pruned {}
    foreach space $spaces {
        set addressable [get_bd_addr_segs -quiet -addressable -of_objects $space]
        foreach mapped [get_bd_addr_segs -quiet -of_objects $space] {
            set targets [get_bd_addr_segs -quiet -of_objects $mapped]
            if {![llength $targets]} { continue }
            set still 0
            foreach target $targets {
                if {[lsearch -exact $addressable $target] >= 0} { set still 1; break }
            }
            if {!$still && ![catch {delete_bd_objs $mapped}]} {
                lappend pruned $mapped
            }
        }
    }
    return $pruned
}

proc ipiasm::_unassign_spaces {spaces} {
    set pruned {}
    foreach space $spaces {
        foreach mapped [get_bd_addr_segs -quiet -of_objects $space] {
            # There is no unassign_bd_address command in Vivado 2026.1.
            # Deleting this mapped MASTER segment removes the assignment while
            # preserving the target slave segment.
            if {![catch {delete_bd_objs $mapped}]} { lappend pruned $mapped }
        }
    }
    return $pruned
}

# Usage:
#   remove_noc_path <noc_port> ?<destination>?          ;# dry-run
#   remove_noc_path <noc_port> ?<destination>? -apply
#   ... -force   ;# required for PS/VISP/AIE/VCU hardened endpoints
proc ipiasm::remove_noc_path {noc_port args} {
    set destination ""
    set apply 0
    set force 0
    foreach arg $args {
        switch -- $arg {
            -apply { set apply 1 }
            -force { set force 1 }
            default {
                if {[string match "-*" $arg]} {
                    return "REMOVE_FAIL:unknown option $arg"
                }
                if {$destination ne ""} {
                    return "REMOVE_FAIL:only one destination may be named"
                }
                set destination $arg
            }
        }
    }
    if {[catch {set info [ipiasm::_noc_remove_info $noc_port $destination]} e]} {
        return "REMOVE_FAIL:$e"
    }
    if {!$apply} { return [ipiasm::plan_remove_noc_path $noc_port $destination] }
    if {[dict get $info hardened] && !$force} {
        return "REMOVE_FAIL:$noc_port is driven by hardened endpoint\
 [dict get $info source] ([dict get $info source_family]); inspect the plan and\
 repeat with -force only when the functional contract authorizes its removal"
    }
    set checkpoint [ipiasm::checkpoint "pre_remove_[string map {/ _} $noc_port]"]
    if {![string match "CHECKPOINT:*" $checkpoint]} {
        return "REMOVE_FAIL:$checkpoint"
    }

    set pin [dict get $info pin]
    set remain [dict get $info remain]
    set pruned {}
    if {[dict size $remain] == 0} {
        # Unassign while the path is still complete. Once the net is gone,
        # Vivado marks the master segment invalid and refuses to unassign it.
        set pruned [ipiasm::_unassign_spaces [dict get $info spaces]]
    }
    if {[catch {
        set_property CONFIG.CONNECTIONS [ipiasm::_conn_braced $remain] $pin
    } e]} {
        return "REMOVE_FAIL:CONNECTIONS update :: [lindex [split $e \n] 0]"
    }

    set net_removed 0
    set clocks_cleared {}
    if {[dict size $remain] == 0} {
        set net [dict get $info net]
        if {$net ne ""} {
            if {[catch {delete_bd_objs $net} e]} {
                return "REMOVE_FAIL:net $net :: [lindex [split $e \n] 0]"
            }
            set net_removed 1
        }
        catch {
            set_property -dict [list CONFIG.R_TRAFFIC_CLASS BEST_EFFORT \
                CONFIG.W_TRAFFIC_CLASS BEST_EFFORT] $pin
        }
        set port_name [get_property -quiet NAME $pin]
        foreach clk [dict get $info clocks] {
            set assoc [split [get_property -quiet CONFIG.ASSOCIATED_BUSIF $clk] :]
            set kept {}
            foreach name $assoc {
                if {$name ne "" && $name ne $port_name} { lappend kept $name }
            }
            set_property CONFIG.ASSOCIATED_BUSIF [join $kept :] $clk
            lappend clocks_cleared $clk
        }
    }
    if {[dict size $remain] != 0} {
        set pruned [ipiasm::_prune_unaddressable [dict get $info spaces]]
    }
    variable qos_sub
    set qos_sub [dict create qos 0 noc 0]
    ipiasm::_gate_set qos 0
    return "REMOVE:OK port=$noc_port removed={[dict get $info remove]}\
 remaining={[dict keys $remain]} net_removed=$net_removed\
 clocks_cleared={[llength $clocks_cleared]} addresses_pruned=[llength $pruned]\
 checkpoint={$checkpoint}"
}

# --- gate: refuse to ship a design still on the default traffic spec ---
# The companion to check_memory_subsystem. NUM_MC=1 loses bandwidth at the
# controller; an undeclared 500/500 traffic spec loses it in the fabric. Both
# validate clean, so both need an explicit gate.
# `allow` holds "<si_pin>/<dest>" glob patterns for connections that genuinely
# want the default -- pass them deliberately, never to silence the gate.
proc ipiasm::set_unused_qos_routes {routes} {
    variable unused_qos_routes
    variable qos_sub
    set normalized {}
    dict for {route reason} $routes {
        set route [string trimleft $route /]
        if {![regexp {^[A-Za-z0-9_./-]+/(MC_[0-9]+|M[0-9]+_(AXI|INI))$} $route] ||
            [string trim $reason] eq "" || [dict exists $normalized $route]} {
            return "QOS_FAIL:unused routes require unique exact paths and nonempty reasons"
        }
        dict set normalized $route $reason
    }
    set context [list [file normalize [get_property DIRECTORY [current_project]]] [current_bd_design]]
    set unused_qos_routes [dict create context $context routes $normalized]
    dict set qos_sub qos 0
    ipiasm::_gate_set qos 0
    return "QOS_UNUSED:SET count=[dict size $normalized] routes=$normalized"
}

proc ipiasm::_check_qos_impl {{allow {}}} {
    variable unused_qos_routes
    set unused {}
    if {[info exists unused_qos_routes]} {
        set context [list [file normalize [get_property DIRECTORY [current_project]]] [current_bd_design]]
        if {[dict get $unused_qos_routes context] eq $context} {
            set unused [dict get $unused_qos_routes routes]
        }
    }
    set unused_seen {}
    set defaults {}
    set zeros {}
    set ignored {}
    set unresolved {}
    set declared 0
    set isoc 0
    set total_rd 0
    set total_wr 0
    foreach row [ipiasm::noc_traffic_spec] {
        lassign $row pin dest rb wb rtc wtc src own
        set key "$pin/$dest"
        set exact_key [string trimleft $key /]
        set both_zero [expr {[string is integer -strict $rb] && $rb == 0 &&
                             [string is integer -strict $wb] && $wb == 0}]
        if {[dict exists $unused $exact_key]} {
            if {$own ne "own" || $rb ne "1" || $wb ne "1" ||
                $rtc ne "BEST_EFFORT" || $wtc ne "BEST_EFFORT"} {
                return "QOS_FAIL:declared-unused route $key must own a 1/1 MB/s BEST_EFFORT reservation (owner=$own rd=$rb wr=$wb read_class=$rtc write_class=$wtc)"
            }
            dict set unused_seen $exact_key [dict get $unused $exact_key]
            incr declared
            incr total_rd $rb
            incr total_wr $wb
            continue
        }
        if {$own eq "own" && $both_zero} {
            lappend zeros "$key (rd=0 wr=0)"
            continue
        }
        set skip 0
        foreach pat $allow {
            if {[string match $pat $key]} { set skip 1; break }
        }
        if {$skip} { continue }
        # Vivado's 500/500 seed is checked BEFORE ownership, on every pin. On a
        # non-owning pin the compiler discards whatever the value is, so the
        # seed does no harm to this topology -- but it is indistinguishable from
        # a path nobody ever configured, no reviewer can tell the two apart, and
        # it goes live the moment a re-partition makes that pin the owner. So it
        # is still reported; it is the one thing worth writing on a pin whose
        # value is otherwise ignored.
        if {$rb eq 500 && $wb eq 500} {
            set why [expr {$own eq "ignored" ? " -- not the QoS owner, but 500/500\
 is indistinguishable from unconfigured" : ""}]
            lappend defaults "$key (driven by $src)$why"
            continue
        }
        # A declaration the compiler discards is neither a win nor a defect --
        # counting it as "declared" is how a green gate gets reported for a
        # traffic spec that never reaches hardware.
        if {$own eq "ignored"} {
            lappend ignored "$key (rd=$rb wr=$wb -- not the QoS owner)"
            continue
        }
        if {$own eq "unresolved"} { lappend unresolved $key }
        incr declared
        if {[string is integer -strict $rb]} { incr total_rd $rb }
        if {[string is integer -strict $wb]} { incr total_wr $wb }
        if {$rtc eq "ISOCHRONOUS" || $wtc eq "ISOCHRONOUS"} { incr isoc }
    }
    set tail ""
    dict for {route reason} $unused {
        if {![dict exists $unused_seen $route]} {
            return "QOS_FAIL:declared-unused route $route is missing from the traffic specification"
        }
    }
    if {[dict size $unused_seen]} {append tail " unused=[dict size $unused_seen] unused_routes=$unused_seen"}
    if {[llength $ignored] > 0}    { append tail " ignored=[llength $ignored]" }
    if {[llength $unresolved] > 0} { append tail " unresolved=[llength $unresolved]" }
    if {[llength $zeros] > 0} {
        return "QOS_FAIL:[llength $zeros] owning connection(s) declare 0 MB/s in BOTH directions\n  [join $zeros "\n  "]\n  -> Declare unused but connected routes with set_unused_qos_routes and apply 1/1 MB/s BEST_EFFORT. Active routes need workload bandwidth; interface capacity is not demand.${tail}"
    }
    # `isochronous=0` printed inside a green gate reads as a fact about a design
    # that is fine, and a run shipped 34 sized connections with no bounded-latency
    # path because of it. Not a failure -- a design with no live video legitimately
    # has none -- but it should have to be a decision rather than an oversight.
    if {$declared > 0 && $isoc == 0} {
        append tail "\n  NOTE: no connection asks for ISOCHRONOUS. Every path here is\n        best-effort, so a live capture or display path has no bounded DDR\n        latency and underruns under load. Set it on the paths that cannot\n        wait, or confirm this design has none."
    }
    # Leaving a connection at the 500/500 seed is a legitimate design choice, not a
    # contract violation: the reference design ships 61 of its 92 destination entries
    # at exactly 500/500 and sizes explicitly only where the contract names a rate.
    # Blocking on it refused to address the gold design, so it is reported and not
    # gated -- the concern above (indistinguishable from unconfigured, and live the
    # moment a re-partition makes that pin the owner) is worth a reviewer's eye, but
    # it is a review note rather than a reason to stop.
    if {[llength $defaults] > 0} {
        append tail "\n  NOTE: [llength $defaults] connection(s) sit at Vivado's 500/500 MB/s seed:\n  [join $defaults "\n  "]\n        That is a valid choice where no contract names a rate. Where one does,\n        size it with ipiasm::bw_video / ipiasm::bw_axi and apply ipiasm::set_qos\n        (UG994: the NoC compiler solves only against what you declare). Silence\n        this per route with ipiasm::check_qos {pattern ...} once it is deliberate."
    }
    return "QOS:OK declared=$declared total_rd=${total_rd}MB/s total_wr=${total_wr}MB/s isochronous=$isoc${tail}"
}

# --- effective read bandwidth of one NoC channel, in MB/s ---
# Supply the raw channel rate and packetization geometry from current product
# documentation or live IP metadata; no target-specific rate is assumed.
proc ipiasm::noc_channel_mbs {raw {chop_bytes 256} {flit_bytes 16}} {
    if {![string is double -strict $raw] || $raw <= 0
        || ![string is double -strict $chop_bytes] || $chop_bytes <= 0
        || ![string is double -strict $flit_bytes] || $flit_bytes <= 0} {
        return "QOS_RATE_FAIL:positive raw/chop/flit values are required"
    }
    set n [expr {double($chop_bytes) / double($flit_bytes)}]
    return [expr {int(double($raw) * $n / ($n + 1.0))}]
}

# --- locate the NoC compiler's solution for the current design ---
# validate_bd_design does not just check the design: it RUNS the NoC compiler and
# writes the resulting routes to <proj>.gen/sources_1/bd/<bd>/nsln/<bd>.ncr. That
# file therefore already exists after the validate this flow performs anyway, so
# reading it costs nothing and needs no extra Vivado run.
proc ipiasm::_ncr_path {} {
    set pdir ""
    catch {set pdir [get_property -quiet DIRECTORY [current_project]]}
    if {$pdir eq ""} { return "" }
    set bd ""
    catch {set bd [get_property -quiet NAME [current_bd_design]]}
    set pats {}
    if {$bd ne ""} { lappend pats [file join $pdir *.gen sources_1 bd $bd nsln *.ncr] }
    lappend pats [file join $pdir *.gen sources_1 bd * nsln *.ncr]
    foreach pat $pats {
        set hits [glob -nocomplain $pat]
        if {![llength $hits]} { continue }
        # Newest wins. A solution left over from an earlier block design would
        # describe traffic this design never declared.
        set best "" ; set bestt -1
        foreach f $hits {
            set t [file mtime $f]
            if {$t > $bestt} { set bestt $t ; set best $f }
        }
        return $best
    }
    return ""
}

# --- read the .ncr ---
# It is pretty-printed JSON, one field per line, so this reads it line-wise
# rather than depending on a JSON package Vivado's tclsh may not carry. "From"
# delimits each path record; the nested Nets objects key their numbers as
# PhyInstanceStart / RequiredBW / AchievedBW, which cannot collide with the
# Read*/Write* keys taken here.
# Returns {solution_type path_dicts}.
proc ipiasm::_ncr_parse {file} {
    set sol "UNKNOWN"
    set paths {}
    set cur {}
    if {[catch {set fh [open $file r]}]} { return [list $sol $paths] }
    while {[gets $fh line] >= 0} {
        if {[regexp {\"SolutionType\"\s*:\s*\"([^\"]+)\"} $line -> v]} { set sol $v ; continue }
        if {[regexp {\"From\"\s*:\s*\"([^\"]+)\"} $line -> v]} {
            if {[dict size $cur]} { lappend paths $cur }
            set cur [dict create from $v to "" port "" rtc "" wtc "" \
                                 rbw 0 wbw 0 rach 0 wach 0]
            continue
        }
        if {![dict size $cur]} { continue }
        if {[regexp {\"To\"\s*:\s*\"([^\"]+)\"} $line -> v]}      { dict set cur to  $v ; continue }
        if {[regexp {\"Port\"\s*:\s*\"([^\"]+)\"} $line -> v]}    { dict set cur port $v ; continue }
        if {[regexp {\"ReadTC\"\s*:\s*\"([^\"]+)\"} $line -> v]}  { dict set cur rtc $v ; continue }
        if {[regexp {\"WriteTC\"\s*:\s*\"([^\"]+)\"} $line -> v]} { dict set cur wtc $v ; continue }
        if {[regexp {\"ReadAchievedBW\"\s*:\s*(-?[0-9]+)} $line -> v]}  { dict set cur rach $v ; continue }
        if {[regexp {\"WriteAchievedBW\"\s*:\s*(-?[0-9]+)} $line -> v]} { dict set cur wach $v ; continue }
        if {[regexp {\"ReadBW\"\s*:\s*(-?[0-9]+)} $line -> v]}    { dict set cur rbw $v ; continue }
        if {[regexp {\"WriteBW\"\s*:\s*(-?[0-9]+)} $line -> v]}   { dict set cur wbw $v ; continue }
    }
    close $fh
    if {[dict size $cur]} { lappend paths $cur }
    return [list $sol $paths]
}

# --- is the on-disk solution current for the design in memory? ---
# It usually is NOT. A forced validation can leave
# the .ncr mtime untouched at 4 minutes old, because the NoC compiler writes
# that file during target generation / synthesis rather than during validate.
# A flow that stops at validate -- which this one does -- therefore reads a
# solution describing an EARLIER design. An early run was failed by this gate
# for "DDR unreachable" on the strength of a solution written before the memory
# NoC was wired at all.
#
# So the .ncr is advisory: trusted for SolutionType only while it is newer than
# the saved .bd, and never the source of a reachability verdict.
proc ipiasm::_ncr_is_fresh {ncr} {
    if {$ncr eq "" || ![file exists $ncr]} { return 0 }
    set bd ""
    catch {
        set name [get_property -quiet NAME [current_bd_design]]
        set bd [get_property -quiet FILE_NAME [get_files -quiet ${name}.bd]]
    }
    if {$bd eq "" || ![file exists $bd]} { return 0 }
    return [expr {[file mtime $ncr] >= [file mtime $bd]}]
}

# --- gate: can the traffic that was declared actually get to DRAM? ---
# Reads the ADDRESS MAP, which is generated from the design in memory and so is
# always current, instead of the NoC compiler's stale artifact. The question it
# answers: every NoC slave interface that declares traffic to a memory
# controller -- does the master driving it own a DRAM window?
#
# Declared bandwidth to a controller that owns no address window is not a data
# path, so compare declarations with live address-space reachability.
#
# Not every master that drives a NoC is an addressing master. A PS NoC port
# (FPD_AXI_NOC0) and an inter-NoC link (M00_INI) aggregate traffic on behalf of
# the spaces BEHIND them -- the A78 cores and the AIE own the windows, so the
# port itself has no address space and asking it for one flags a correct design.
# Those are counted as aggregated, not failed. A master that does own a space is
# held to it.
#
# Returns {declared N unaddressed {...} addressed {...} aggregated {...}}.
proc ipiasm::_dram_paths {} {
    set declared 0
    set unaddressed {}
    set ok {}
    set aggregated {}
    foreach c [get_bd_cells -quiet -filter {VLNV=~*axi_noc*}] {
        foreach pin [get_bd_intf_pins -quiet $c/*] {
            if {[get_property -quiet MODE $pin] ne "Slave"} { continue }
            set conn [get_property -quiet CONFIG.CONNECTIONS $pin]
            if {$conn eq ""} { continue }
            # Only MC_* destinations are DRAM. M00_AXI / M00_INI leave this NoC.
            set to_mc 0
            foreach {dest spec} $conn {
                if {[string match -nocase "MC_*" $dest]} { set to_mc 1 }
            }
            if {!$to_mc} { continue }
            incr declared
            set master [ipiasm::_intf_source $pin]
            if {$master eq ""} {
                lappend unaddressed "[bd::utils::get_short_name $pin] (nothing drives it)"
                continue
            }
            # The master's address space carries the master interface's own path.
            set sp [get_bd_addr_spaces -quiet $master]
            if {$sp eq ""} {
                lappend aggregated [string trimleft $master /]
                continue
            }
            set hit 0
            foreach seg [get_bd_addr_segs -quiet -of_objects $sp] {
                if {[ipiasm::_is_dram_seg $seg]} { set hit 1 ; break }
            }
            if {$hit} { lappend ok [string trimleft $master /] } else {
                lappend unaddressed [string trimleft $master /]
            }
        }
    }
    return [dict create declared $declared unaddressed $unaddressed \
                       addressed $ok aggregated $aggregated]
}

# ------------------------------------------------------ illegal NoC routing
# The failure that stops the other two from being fixable. The NoC compiler runs
# INSIDE validate_bd_design, decides a route is not buildable, says so -- and
# validate_bd_design can still return rc=0:
#
#   CRITICAL WARNING: [Ipconfig 75-153] AIE to AIE path through NoC is illegal
#     (aie_ml_inference_ss/axi_noc/inst/S00_AXI_nmu -> .../M00_AXI_nsu).
#
# Detection is by cumulative message counters:
#   * `get_msg_config -id <id> -count` works, on both the bracketed and the bare
#     id spelling.
#   * there is NO `reset_msg_count` in 2026.1 -- the counters cannot be zeroed,
#     they are cumulative for the whole session. So "did THIS operation say it?"
#     is answered by snapshotting the counts before and differencing after, not
#     by arming.
#   * a plain `validate_bd_design` does NOT re-run the NoC compiler: it says
#     "[BD 41-3275] NoC Compiler will not be re-run because NoC configuration
#     has not changed since the last successful run" and emits nothing. Only
#     `validate_bd_design -force` makes the compiler speak again. That is why
#     the gate below re-validates with -force by default: without it, a design
#     carrying a refused route reads clean.
#
# The counter is the general signal, but for the AIE there is also a structural
# one, because the loop has a single known author: the ai_engine block
# automation builds it (see ipiasm::check_aie_config). Vivado's native
# `can_connect` cannot stand in for either because an already-wired pair is no
# longer a free candidate connection.

# --- documented compiler message ids that mean a route is not buildable ---
proc ipiasm::illegal_msg_ids {} {
    return [list \
        {Ipconfig 75-153} "the NoC compiler refuses this route (an AIE-to-AIE\
path through the NoC); it stops the solution resolving, and an unresolved\
solution publishes no memory aperture"]
}

# --- read a message counter, tolerating both id spellings ---
# get_msg_config takes the id bracketed ({[Ipconfig 75-153]}) but accepts the
# bare form too; ask for both and keep the larger. A build without the counter
# reports 0 rather than failing the gate.
proc ipiasm::_msg_count {id} {
    set best 0
    foreach form [list "\[$id\]" $id] {
        set n ""
        if {[catch {set n [get_msg_config -id $form -count]}]} { continue }
        if {[string is integer -strict $n] && $n > $best} { set best $n }
    }
    return $best
}

# Clock/reset defects are critical even when validate_bd_design returns rc=0.
proc ipiasm::clock_reset_msg_ids {} {
    return [list \
        {BD 41-1348} "a reset pin is asynchronous to its associated clock" \
        {BD 41-1343} "a reset is synchronous to a different clock; add a proc_sys_reset on the associated clock" \
        {BD 41-1347} "a reset pin is driven by an asynchronous source; use peripheral_aresetn of a proc_sys_reset on its clock" \
        {BD 41-1660} "an external reset is not associated with its clock; set CONFIG.ASSOCIATED_RESET on the clock port"]
}

# Resolve interface metadata through its actual associated clock when the
# interface itself does not publish CLK_DOMAIN/FREQ_HZ.
proc ipiasm::_interface_clock_facts {intf} {
    set domain [get_property -quiet CONFIG.CLK_DOMAIN $intf]
    set hz [get_property -quiet CONFIG.FREQ_HZ $intf]
    set clocks [ipiasm::_associated_scalars $intf clk]
    if {[llength $clocks] == 1} {
        set clock [lindex $clocks 0]
        if {$domain eq ""} {
            set domain [get_property -quiet CONFIG.CLK_DOMAIN $clock]
        }
        if {$hz eq ""} { set hz [get_property -quiet CONFIG.FREQ_HZ $clock] }
    }
    return [dict create domain $domain hz $hz clocks $clocks]
}

# Every connected SmartConnect AXI boundary with resolvable metadata must match
# its peer. A hierarchy boundary may legitimately omit interface metadata; that
# is unresolved here rather than a false mismatch, and an explicit registered
# interface contract is the strict check for such a boundary.
proc ipiasm::check_interface_clock_domains {} {
    set checked 0
    set unresolved 0
    set bad {}
    foreach cell [get_bd_cells -quiet -hierarchical -filter {VLNV =~ *:smartconnect:*}] {
        foreach pin [get_bd_intf_pins -quiet -of_objects $cell] {
            set net [get_bd_intf_nets -quiet -of_objects $pin]
            if {$net eq ""} { continue }
            set peers [concat \
                [get_bd_intf_pins -quiet -of_objects $net] \
                [get_bd_intf_ports -quiet -of_objects $net]]
            foreach peer $peers {
                if {"$peer" eq "$pin"} { continue }
                lassign [ipiasm::_compare_intf_clocks $pin $peer] verdict detail
                if {$verdict eq "unresolved"} { incr unresolved; continue }
                incr checked
                if {$verdict eq "bad"} { lappend bad $detail }
            }
        }
    }
    # validate_bd_design applies the same rule to every connection (BD 41-237);
    # checking leaf-to-leaf peers here reports it before the NoC compiler runs.
    set seen {}
    foreach cell [get_bd_cells -quiet -hierarchical -filter {TYPE == ip && VLNV !~ *:smartconnect:*}] {
        foreach pin [get_bd_intf_pins -quiet -of_objects $cell] {
            if {[get_bd_intf_nets -quiet -of_objects $pin] eq ""} { continue }
            set peers {}
            catch {set peers [find_bd_objs -relation connected_to -thru_hier $pin]}
            foreach peer $peers {
                if {"$peer" eq "$pin"} { continue }
                set owner [get_bd_cells -quiet -of_objects $peer]
                if {$owner ne "" && [string match *:smartconnect:* [get_property -quiet VLNV $owner]]} { continue }
                set key [lsort [list $pin $peer]]
                if {[dict exists $seen $key]} { continue }
                dict set seen $key 1
                lassign [ipiasm::_compare_intf_clocks $pin $peer] verdict detail
                if {$verdict eq "unresolved"} { incr unresolved; continue }
                incr checked
                if {$verdict eq "bad"} { lappend bad $detail }
            }
        }
    }
    if {!$checked && !$unresolved} { return "CLOCK_DOMAIN:NA no connected interfaces" }
    if {[llength $bad]} {
        return "CLOCK_DOMAIN_FAIL:[llength $bad]/$checked interface peer\
relation(s) mismatched\n  [join $bad "\n  "]"
    }
    return "CLOCK_DOMAIN:OK checked=$checked unresolved=$unresolved interface peer relations"
}

# ok | unresolved | {bad <detail>} for one connected interface pair.
proc ipiasm::_compare_intf_clocks {pin peer} {
    set pin_facts [ipiasm::_interface_clock_facts $pin]
    set peer_facts [ipiasm::_interface_clock_facts $peer]
    set pin_clocks [dict get $pin_facts clocks]
    set peer_clocks [dict get $peer_facts clocks]
    if {[llength $pin_clocks] == 1 && [llength $peer_clocks] == 1} {
        set verdict [ipiasm::_same_domain_verdict $pin \
            [lindex $pin_clocks 0] $peer [lindex $peer_clocks 0]]
        if {[dict get $verdict same_domain]} { return ok }
    }
    set pin_domain [dict get $pin_facts domain]
    set peer_domain [dict get $peer_facts domain]
    if {$pin_domain eq "" || $peer_domain eq ""} { return unresolved }
    set pin_hz [dict get $pin_facts hz]
    set peer_hz [dict get $peer_facts hz]
    if {$pin_domain ne $peer_domain
        || ($pin_hz ne "" && $peer_hz ne "" && $pin_hz ne $peer_hz)} {
        set detail "${pin}(domain=$pin_domain freq=$pin_hz)<->\
${peer}(domain=$peer_domain freq=$peer_hz)"
        # A NoC edge has one specific cure, and naming it here is the difference
        # between a report that ends the hunt and one that starts it. This gate
        # named the right three edges of a failing design and said only that two
        # domains differed -- true, and no help at all.
        foreach side [list $pin $peer] {
            set c [get_bd_cells -quiet -of_objects $side]
            if {$c ne "" && [string match "*:axi_noc*" [get_property -quiet VLNV $c]]} {
                append detail " -- enable an aclk on [string trimleft $c /] whose\
CONFIG.ASSOCIATED_BUSIF names [file tail $side] and wire it to the peer's own\
source clock (ipiasm::attach_noc_port_clocks, or autofix)"
                break
            }
        }
        return [list bad $detail]
    }
    return ok
}

# ------------------------------------------------ metadata-aware interfaces
# A compatible bus type is not proof that a connection is synchronous. The
# configured IP instances, ASSOCIATED_BUSIF/ASSOCIATED_RESET metadata, and live
# clock/reset nets form the contract. The binder registers explicit contracts;
# validate checks all of them in one batch before expensive forced validation.
proc ipiasm::_contract_intf {path} {
    set object [get_bd_intf_pins -quiet $path]
    if {$object eq ""} { set object [get_bd_intf_ports -quiet $path] }
    return $object
}

proc ipiasm::_contract_scalar {path} {
    set object [get_bd_pins -quiet $path]
    if {$object eq ""} { set object [get_bd_ports -quiet $path] }
    return $object
}

proc ipiasm::_associated_busifs {scalar} {
    set value [get_property -quiet CONFIG.ASSOCIATED_BUSIF $scalar]
    regsub -all {[:,]} $value " " value
    set result {}
    foreach item $value {
        set item [string trim $item]
        if {$item ne ""} { lappend result $item }
    }
    return $result
}

proc ipiasm::_associated_scalars {intf type} {
    set owner [get_bd_cells -quiet -of_objects $intf]
    if {$owner ne ""} {
        set candidates [get_bd_pins -quiet -of_objects $owner]
    } else {
        set candidates [get_bd_ports -quiet]
    }
    set leaf [file tail $intf]
    set matches {}
    foreach scalar $candidates {
        if {[get_property -quiet TYPE $scalar] ne $type} { continue }
        if {[lsearch -exact [ipiasm::_associated_busifs $scalar] $leaf] >= 0} {
            lappend matches $scalar
        }
    }
    return $matches
}

proc ipiasm::_contract_vlnv {intf} {
    set owner [get_bd_cells -quiet -of_objects $intf]
    if {$owner eq ""} { return "external" }
    return [get_property -quiet VLNV $owner]
}

proc ipiasm::_contract_domain {intf clock} {
    set domain [get_property -quiet CONFIG.CLK_DOMAIN $clock]
    if {$domain eq ""} { set domain [get_property -quiet CONFIG.CLK_DOMAIN $intf] }
    return $domain
}

proc ipiasm::_contract_frequency {intf clock} {
    set hz [get_property -quiet CONFIG.FREQ_HZ $clock]
    if {$hz eq ""} { set hz [get_property -quiet CONFIG.FREQ_HZ $intf] }
    return $hz
}

proc ipiasm::_contract_scalar_path {contract side kind intf} {
    set key "${side}_${kind}"
    if {[dict exists $contract $key] && [dict get $contract $key] ne ""} {
        return [dict get $contract $key]
    }
    set matches [ipiasm::_associated_scalars $intf $kind]
    if {[llength $matches] == 1} { return [lindex $matches 0] }
    return ""
}

# Register metadata-aware interface contracts (clock, width, protocol) for later preflight and checks.
proc ipiasm::set_interface_contracts {contracts} {
    variable interface_contracts
    set normalized {}
    set index 0
    foreach contract $contracts {
        incr index
        if {[catch {dict size $contract}]} {
            return "INTF_CONTRACT_FAIL:contract $index is not a Tcl dict"
        }
        foreach key {src dst} {
            if {![dict exists $contract $key] || [dict get $contract $key] eq ""} {
                return "INTF_CONTRACT_FAIL:contract $index has no $key"
            }
        }
        lappend normalized $contract
    }
    set interface_contracts $normalized
    ipcfg::ledger_record interface_contracts $interface_contracts
    return "INTF_CONTRACTS:SET count=[llength $interface_contracts]"
}

# Check registered interface contracts against the live cells before connecting anything.
proc ipiasm::preflight_interface_contracts {{contracts __registered__}} {
    variable interface_contracts
    if {$contracts eq "__registered__"} { set contracts $interface_contracts }
    if {![llength $contracts]} {
        return "INTF_PREFLIGHT:NA no registered contracts"
    }

    set failures {}
    set notes {}
    set index 0
    foreach contract $contracts {
        incr index
        set src_path [dict get $contract src]
        set dst_path [dict get $contract dst]
        set src [ipiasm::_contract_intf $src_path]
        set dst [ipiasm::_contract_intf $dst_path]
        if {$src eq ""} {
            lappend failures "$index:$src_path missing source interface"
            continue
        }
        if {$dst eq ""} {
            lappend failures "$index:$dst_path missing destination interface"
            continue
        }

        foreach side {src dst} {
            set intf [set $side]
            set path [set ${side}_path]
            set metadata_intf $intf
            set metadata_key "${side}_metadata_intf"
            if {[dict exists $contract $metadata_key]
                && [dict get $contract $metadata_key] ne ""} {
                set metadata_path [dict get $contract $metadata_key]
                set metadata_intf [ipiasm::_contract_intf $metadata_path]
                if {$metadata_intf eq ""} {
                    lappend failures \
                        "$index:$path metadata interface $metadata_path does not exist"
                    set metadata_intf $intf
                }
            }
            set ${side}_metadata $metadata_intf
            set expected_key "${side}_vlnv"
            if {[dict exists $contract $expected_key]} {
                set actual [ipiasm::_contract_vlnv $metadata_intf]
                set expected [dict get $contract $expected_key]
                if {![string match $expected $actual]} {
                    lappend failures \
                        "$index:$path VLNV=$actual expected=$expected"
                }
            }

            set clock_path \
                [ipiasm::_contract_scalar_path $contract $side clk $intf]
            set ${side}_clock ""
            if {$clock_path eq ""} {
                lappend failures \
                    "$index:$path has no associated clock metadata or explicit ${side}_clk"
                continue
            }
            set clock [ipiasm::_contract_scalar $clock_path]
            set ${side}_clock $clock
            if {$clock eq ""} {
                lappend failures "$index:$path clock $clock_path does not exist"
                continue
            }
            set busifs [ipiasm::_associated_busifs $clock]
            if {[llength $busifs]
                && [lsearch -exact $busifs [file tail $metadata_intf]] < 0} {
                lappend failures \
                    "$index:$clock_path ASSOCIATED_BUSIF={$busifs} excludes [file tail $metadata_intf]"
            } elseif {![llength $busifs]} {
                lappend notes \
                    "$index:$clock_path association supplied by bound contract"
            }
            if {![llength [get_bd_nets -quiet -of_objects $clock]]} {
                lappend failures "$index:$clock_path has no live clock net"
            }
            set intf_domain \
                [get_property -quiet CONFIG.CLK_DOMAIN $metadata_intf]
            set clock_domain [get_property -quiet CONFIG.CLK_DOMAIN $clock]
            if {$intf_domain ne "" && $clock_domain ne ""
                && $intf_domain ne $clock_domain} {
                lappend failures \
                    "$index:$path CLK_DOMAIN=$intf_domain contradicts $clock_path CLK_DOMAIN=$clock_domain"
            }
            set intf_hz [get_property -quiet CONFIG.FREQ_HZ $metadata_intf]
            set clock_hz [get_property -quiet CONFIG.FREQ_HZ $clock]
            if {$intf_hz ne "" && $clock_hz ne "" && $intf_hz ne $clock_hz} {
                lappend failures \
                    "$index:$path FREQ_HZ=$intf_hz contradicts $clock_path FREQ_HZ=$clock_hz"
            }

            set reset_path \
                [ipiasm::_contract_scalar_path $contract $side rst $intf]
            set ${side}_reset ""
            if {$reset_path ne ""} {
                set reset [ipiasm::_contract_scalar $reset_path]
                set ${side}_reset $reset
                if {$reset eq ""} {
                    lappend failures "$index:$path reset $reset_path does not exist"
                } elseif {![llength [get_bd_nets -quiet -of_objects $reset]]} {
                    lappend failures "$index:$reset_path has no live reset net"
                }
                set associated_reset \
                    [get_property -quiet CONFIG.ASSOCIATED_RESET $clock]
                if {$associated_reset ne ""
                    && [file tail $reset] ne $associated_reset} {
                    lappend failures \
                        "$index:$clock_path ASSOCIATED_RESET=$associated_reset but contract uses [file tail $reset]"
                }
            } elseif {![dict exists $contract allow_missing_reset]
                      || ![dict get $contract allow_missing_reset]} {
                lappend failures \
                    "$index:$path has no associated reset metadata or explicit ${side}_rst"
            }
        }

        if {$src_clock eq "" || $dst_clock eq ""} { continue }
        # Shared with preflight_connect and disconnect_intf_edge so a contract
        # check and a topology change cannot disagree about the same edge.
        set verdict [ipiasm::_same_domain_verdict \
            $src_metadata $src_clock $dst_metadata $dst_clock]
        foreach key {same_domain src_clock_net dst_clock_net
                     src_domain dst_domain src_hz dst_hz} {
            set $key [dict get $verdict $key]
        }
        set allow_cdc [expr {[dict exists $contract allow_cdc]
                             && [dict get $contract allow_cdc]}]
        if {!$same_domain && !$allow_cdc} {
            set hint [expr {$src_clock_net eq "" && $dst_clock_net eq ""
                            ? " (neither clock pin is connected; connect the clocks first)" : ""}]
            lappend failures \
                "$index:$src_path->$dst_path requires CDC: clocks={$src_clock_net,$dst_clock_net} domains={$src_domain,$dst_domain} freq={$src_hz,$dst_hz}$hint"
        }

        if {$same_domain && $src_reset ne "" && $dst_reset ne ""} {
            set src_reset_net [get_bd_nets -quiet -of_objects $src_reset]
            set dst_reset_net [get_bd_nets -quiet -of_objects $dst_reset]
            set allow_split \
                [expr {[dict exists $contract allow_separate_resets]
                       && [dict get $contract allow_separate_resets]}]
            if {$src_reset_net ne $dst_reset_net && !$allow_split} {
                lappend failures \
                    "$index:$src_path->$dst_path shares a clock domain but reset nets differ={$src_reset_net,$dst_reset_net}"
            }
        }

        set src_net [get_bd_intf_nets -quiet -of_objects $src]
        set dst_net [get_bd_intf_nets -quiet -of_objects $dst]
        if {$src_net ne "" && $src_net eq $dst_net} {
            lappend notes \
                "$index:$src_path->$dst_path already wired same_domain=$same_domain"
        } else {
            set legality [ipiasm::check_connect $src_path $dst_path]
            if {[string match "ILLEGAL:*" $legality]
                || [string match "CC_ERR:*" $legality]} {
                lappend failures "$index:$src_path->$dst_path $legality"
            } else {
                lappend notes \
                    "$index:$src_path->$dst_path $legality same_domain=$same_domain"
            }
        }
    }
    if {[llength $failures]} {
        return "INTF_PREFLIGHT_FAIL:[llength $failures] defect(s) across\
[llength $contracts] contract(s)\n  [join $failures "\n  "]"
    }
    return "INTF_PREFLIGHT:OK contracts=[llength $contracts]\
 {[join $notes {; }]}"
}

# Create one reset synchronizer for an explicit clock domain. Reset assertion
# may be asynchronous; release is synchronized by proc_sys_reset.
proc ipiasm::ensure_reset_domain {cell clk_pin async_reset_pin reset_pins {locked_pin ""}} {
    set clk [get_bd_pins -quiet $clk_pin]
    set async_reset [get_bd_pins -quiet $async_reset_pin]
    if {$clk eq ""} { return "RESET_SYNC_FAIL:no clock pin $clk_pin" }
    if {$async_reset eq ""} {
        return "RESET_SYNC_FAIL:no asynchronous reset pin $async_reset_pin"
    }
    # C_EXT_RESET_HIGH is read-only: validation propagates it from the source's
    # POLARITY, so connect directly and refuse a source whose polarity is unknown.
    set reset_source $async_reset
    set type [get_property -quiet TYPE $async_reset]
    set polarity [string toupper [get_property -quiet CONFIG.POLARITY $async_reset]]
    if {$type ne "rst" || $polarity ni {ACTIVE_LOW ACTIVE_HIGH}} {
        return "RESET_SYNC_FAIL:$async_reset_pin is TYPE={$type} POLARITY={$polarity};\
proc_sys_reset takes its ext_reset_in polarity from the source, so drive it from a reset\
pin with CONFIG.POLARITY ACTIVE_LOW or ACTIVE_HIGH"
    }
    if {![ipcfg::vlnv_ok xilinx.com:ip:proc_sys_reset]} {
        return "RESET_SYNC_FAIL:proc_sys_reset is unavailable for this part"
    }
    if {[get_bd_cells -quiet $cell] eq ""} {
        set result [ipcfg::create_cell xilinx.com:ip:proc_sys_reset $cell]
        if {[string match "CONFIGURE_FAIL:*" $result]} {
            return "RESET_SYNC_FAIL:$result"
        }
    }

    # A generated clock is qualified by its own lock output. Discover it from
    # the clock pin's owning cell when the caller did not pass one.
    if {$locked_pin eq ""} {
        set owner [get_bd_cells -quiet -of_objects $clk]
        if {$owner ne ""
            && [string match "*:clk_wizard:*" [get_property -quiet VLNV $owner]]} {
            set candidate "$owner/locked"
            if {[get_bd_pins -quiet $candidate] eq ""} {
                return "RESET_SYNC_FAIL:$owner generates $clk_pin but has no\
locked output; enable the clock wizard lock port"
            }
            set locked_pin $candidate
        }
    }

    foreach {destination source} [list \
        slowest_sync_clk $clk ext_reset_in $reset_source] {
        set pin [get_bd_pins -quiet $cell/$destination]
        if {$pin eq ""} { return "RESET_SYNC_FAIL:$cell has no $destination pin" }
        foreach net [get_bd_nets -quiet -of_objects $pin] {
            catch {disconnect_bd_net $net $pin}
        }
        if {[catch {connect_bd_net $source $pin} error]} {
            return "RESET_SYNC_FAIL:$source->$cell/$destination: $error"
        }
    }
    if {$locked_pin ne ""} {
        set lock [get_bd_pins -quiet $locked_pin]
        set dcm [get_bd_pins -quiet $cell/dcm_locked]
        if {$lock eq "" || $dcm eq ""} {
            return "RESET_SYNC_FAIL:locked qualification pins are absent"
        }
        foreach net [get_bd_nets -quiet -of_objects $dcm] {
            catch {disconnect_bd_net $net $dcm}
        }
        if {[catch {connect_bd_net $lock $dcm} error]} {
            return "RESET_SYNC_FAIL:$locked_pin->$cell/dcm_locked: $error"
        }
    }

    set synchronized [get_bd_pins -quiet $cell/peripheral_aresetn]
    if {$synchronized eq ""} {
        return "RESET_SYNC_FAIL:$cell has no peripheral_aresetn"
    }
    set synchronized_net [get_bd_nets -quiet -of_objects $synchronized]
    set rewired {}
    foreach path $reset_pins {
        # A top-level port is resolved as a port: get_bd_pins also answers for one, but with
        # a pin view that disconnect_bd_net does not accept.
        set pin {}
        if {[string first / [string trimleft $path /]] < 0} {
            set pin [get_bd_ports -quiet [string trimleft $path /]]
        }
        if {$pin eq ""} { set pin [get_bd_pins -quiet $path] }
        if {$pin eq ""} { return "RESET_SYNC_FAIL:no reset pin $path" }
        # Already on this domain's synchronized reset, e.g. a port created with it as driver.
        if {$synchronized_net ne ""
            && [get_bd_nets -quiet -of_objects $pin] eq $synchronized_net} {
            lappend rewired $path
            continue
        }
        foreach net [get_bd_nets -quiet -of_objects $pin] {
            catch {disconnect_bd_net $net $pin}
        }
        if {[catch {connect_bd_net $synchronized $pin} error]} {
            return "RESET_SYNC_FAIL:$cell/peripheral_aresetn->$path: $error"
        }
        lappend rewired $path
    }
    return "RESET_SYNC:WIRED $cell clock=$clk_pin async_reset=$async_reset_pin\
locked=$locked_pin resets={$rewired}"
}

proc ipiasm::_mipi_clock_margin_mhz {} {
    set part ""
    catch {set part [get_property -quiet PART [current_project]]}
    set descriptor ""
    if {$part ne "" && [llength [info commands get_parts]]} {
        set object [get_parts -quiet $part]
        foreach property {ARCHITECTURE ARCHITECTURE_FULL_NAME FAMILY DEVICE_FAMILY} {
            catch {append descriptor " " [get_property -quiet $property $object]}
        }
    }
    if {[regexp -nocase {versal.*(gen(eration)?[ _-]*2|2nd)} $descriptor]} {
        return 20.0
    }
    return 0.0
}

proc ipiasm::_clock_pin_hz {pin} {
    # Input-pin metadata can retain an IP preset after a different clock is
    # physically connected. Prefer the live net's driver frequency.
    set net [get_bd_nets -quiet -of_objects $pin]
    set candidates {}
    if {$net ne ""} {
        foreach peer [concat \
            [get_bd_pins -quiet -of_objects $net] \
            [get_bd_ports -quiet -of_objects $net]] {
            if {"$peer" eq "$pin"} { continue }
            set dir [get_property -quiet DIR $peer]
            if {$dir eq "O" || [lsearch -exact [get_bd_ports -quiet -of_objects $net] $peer] >= 0} {
                lappend candidates $peer
            }
        }
    }
    lappend candidates $pin
    foreach candidate $candidates {
        foreach property {CONFIG.FREQ_HZ FREQ_HZ CONFIG.CLK_FREQ_HZ CLK_FREQ} {
            set hz [get_property -quiet $property $candidate]
            if {[string is double -strict $hz] && $hz > 0} { return $hz }
        }
    }
    return ""
}

# PG232 AXI-stream video_aclk throughput gate. The output packs PPC pixels of
# the configured format each cycle, so its minimum is aggregate lane payload
# divided by PPC*bits-per-pixel. Do not apply the receiver's internal 32-bit
# lane-management rate to this output: the shipped FPGA Horizons v1.0
# mipi_csi2_rx_subsystem (4 lanes, RAW12, 4 PPC, 1500 Mb/s) uses a validated
# 149.999 MHz video_aclk; the packed-stream requirement is 125 MHz plus the
# existing 20 MHz Versal Gen2 margin.
proc ipiasm::check_mipi_video_clocks {{margin_mhz auto}} {
    set receivers [get_bd_cells -quiet -hierarchical \
        -filter {VLNV =~ *:mipi_csi2_rx_subsystem:*}]
    if {![llength $receivers]} {
        return "VIDEOCLK:NA no MIPI CSI-2 RX subsystem"
    }
    if {$margin_mhz eq "auto"} {
        set margin_mhz [ipiasm::_mipi_clock_margin_mhz]
    }
    if {![string is double -strict $margin_mhz] || $margin_mhz < 0} {
        return "VIDEOCLK_FAIL:margin must be a non-negative MHz value"
    }
    set rows {}
    set bad {}
    foreach receiver $receivers {
        set rate [get_property -quiet CONFIG.DPY_LINE_RATE $receiver]
        set lanes [get_property -quiet CONFIG.CMN_NUM_LANES $receiver]
        set ppc [get_property -quiet CONFIG.CMN_NUM_PIXELS $receiver]
        set format [get_property -quiet CONFIG.CMN_PXL_FORMAT $receiver]
        set bits ""
        if {[regexp -nocase {RAW([0-9]+)} $format -> raw_bits]} {
            set bits $raw_bits
        }
        if {$bits eq "" || ![string is double -strict $rate]
            || ![string is double -strict $lanes]
            || ![string is double -strict $ppc] || $ppc <= 0} {
            lappend bad "$receiver(unresolved rate=$rate lanes=$lanes\
ppc=$ppc format=$format)"
            continue
        }
        set pixels [expr {double($rate) * $lanes / (double($ppc) * $bits)}]
        set required [expr {$pixels + double($margin_mhz)}]
        set clock [get_bd_pins -quiet $receiver/video_aclk]
        set hz [ipiasm::_clock_pin_hz $clock]
        if {![string is double -strict $hz]} {
            lappend bad "$receiver(video_aclk frequency is unresolved)"
            continue
        }
        set actual [expr {double($hz) / 1000000.0}]
        set row [format "%s have=%.3fMHz need>=%.3fMHz" \
            $receiver $actual $required]
        lappend rows $row
        if {$actual + 0.0005 < $required} { lappend bad $row }
    }
    # visp_ss rule (native validate, visp:2.0-99): a driven PL video-out clock
    # must run at >= that ISP instance's core clock / 4.
    foreach isp [get_bd_cells -quiet -hierarchical -filter {VLNV =~ *:visp_ss:*}] {
        foreach pin [get_bd_pins -quiet -of_objects $isp -filter {NAME =~ tile*_pl_isp_vidout*_clk}] {
            if {![regexp {^tile([0-9]+)_pl_isp_vidout([0-9]+)_clk$} [get_property NAME $pin] -> t i]} { continue }
            if {[ipiasm::_clock_driver $pin] eq ""} { continue }
            set core [get_property -quiet CONFIG.C_TILE${t}_ISP${i}_CORE_CLK $isp]
            set hz [ipiasm::_clock_pin_hz $pin]
            if {![string is double -strict $core] || ![string is double -strict $hz]} { continue }
            set need [expr {$core / 4.0}]
            set row [format "%s have=%.3fMHz need>=%.3fMHz (ISP%s core %s MHz / 4)" \
                [string trimleft $pin /] [expr {$hz / 1e6}] $need $i $core]
            lappend rows $row
            if {$hz / 1e6 + 0.0005 < $need} { lappend bad $row }
        }
    }
    if {[llength $bad]} {
        return "VIDEOCLK_FAIL:[llength $bad] video clock(s) under-clocked or unresolved\n  [join $bad "\n  "]"
    }
    return "VIDEOCLK:OK [join $rows {; }]"
}

# --- before/after counting, since the counters cannot be reset ---
proc ipiasm::_msg_snapshot {} {
    set d {}
    foreach {id why} [ipiasm::illegal_msg_ids] { dict set d $id [ipiasm::_msg_count $id] }
    return $d
}

# --- findings, as "N new since $base" (exact) or cumulative (session-wide) ---
# base {} means "everything this session", which on a design just opened from
# disk is precisely the load-time verdict on the saved connectivity.
proc ipiasm::_illegal_noc_findings {{base {}}} {
    set out {}
    foreach {id why} [ipiasm::illegal_msg_ids] {
        set n [ipiasm::_msg_count $id]
        if {$base ne "" && [dict exists $base $id]} { incr n -[dict get $base $id] }
        if {$n > 0} { lappend out "\[$id\] x$n -- $why" }
    }
    return $out
}

# --- every NoC in the design, including the ones automation hid in a hierarchy ---
# A refused route can be inside the AIE subsystem's own NoC, which a plain
# get_bd_cells never returns -- it lists the current
# level only. A gate that cannot see that cell cannot see the defect.
proc ipiasm::_all_noc_cells {} {
    set out {}
    catch {set out [get_bd_cells -quiet -hierarchical -filter {VLNV=~*axi_noc*}]}
    if {[llength $out]} { return $out }
    return [get_bd_cells -quiet -filter {VLNV=~*axi_noc*}]
}

# --- gate: did the NoC compiler refuse any route in this design? ---
# Run it whenever a memory aperture is missing, and before believing a
# VALIDATE:PASS on a design with an AIE. It is the only place the refusal is
# reported: validate returns rc=0, check_qos passes, and the address map simply
# comes up empty with no reason attached.
#
# `force` re-runs validate_bd_design -force so the NoC compiler actually speaks
# (about 10 s on a camera-class design). Leave it on: with force 0 the gate can
# only report what something else already elicited, and a plain validate elicits
# nothing. Use force 0 only to re-read a verdict you have just obtained.

# --- internal: a cheap stand-in for "has this design changed?" ---
# Counts only, because the question is whether the NoC compiler could possibly
# have a different answer, and it cannot if nothing was added, wired or mapped.
# This structural check is much cheaper than forcing validation to learn the
# same thing.
#
# Returns "" when any count cannot be taken, and callers must read that as
# "unknown" rather than "unchanged": this exists only to skip work, so it has to
# fail towards doing the work. Forcing validation needlessly is slow; skipping
# a legality check that had something to say is a broken one.
proc ipiasm::_design_fp {} {
    set parts {}
    foreach q {
        {llength [get_bd_cells -hierarchical -quiet]}
        {llength [get_bd_intf_nets -hierarchical -quiet]}
        {llength [get_bd_nets -hierarchical -quiet]}
        {ipiasm::_mapped_seg_count}
    } {
        if {[catch {uplevel 1 $q} n]} { return "" }
        lappend parts $n
    }
    return [join $parts :]
}

# Run the NoC compiler (validate -force, skipped on an unchanged design) and report illegal NoC routes.
proc ipiasm::check_noc_legality {{force 1}} {
    variable forced_fp
    if {![llength [ipiasm::_all_noc_cells]]} {
        return "NOCLEGAL:NA this design has no NoC cell (non-Versal parts have none)"
    }
    set before [ipiasm::_msg_snapshot]
    set fresh {}
    set note ""
    # Forcing re-runs the NoC compiler through validate_bd_design -force, which
    # is one of the most expensive operations in this library, long enough
    # that vivado_execute hands the command off and the caller has to wait it
    # out. Nothing about the routing can have changed if the design has not, so
    # a repeat pass on an unchanged design reports the first pass's verdict.
    set fp [ipiasm::_design_fp]
    if {$force && $forced_fp ne "" && $fp ne "" && $fp eq $forced_fp} {
        set force 0
        set note " (design unchanged since the last forced pass, so nothing\
re-ran the NoC compiler; edit the design or call with force 1 after a change to\
make it speak again)"
    }
    if {$force} {
        catch {validate_bd_design -force}
        set forced_fp [ipiasm::_design_fp]
        set fresh [ipiasm::_illegal_noc_findings $before]
    } elseif {$note eq ""} {
        set note " (force 0: nothing re-ran the NoC compiler, so this is only\
what was already emitted)"
    }
    # Everything seen this session, which includes what the design LOAD said
    # about the connectivity saved in the .bd -- the counters cannot be reset,
    # so this is cumulative and is labelled as such.
    set seen [ipiasm::_illegal_noc_findings]
    if {![llength $seen] && ![llength $fresh]} {
        return "NOCLEGAL:OK no refused route$note -- ids\
[join [dict keys [ipiasm::illegal_msg_ids]] {, }] all at zero for this session"
    }
    set body ""
    if {[llength $fresh]} {
        append body "\n  emitted by a forced re-validate of THIS design:\n    [join $fresh "\n    "]"
    }
    if {[llength $seen]} {
        append body "\n  seen so far this session (cumulative, includes the design load):\n    [join $seen "\n    "]"
    }
    return "NOCLEGAL_FAIL:[llength $seen] refused-route message id(s)$body\n \
 -> this is a CONNECTIVITY defect, not a configuration one: nothing you write to\
CONFIG.* fixes it and no amount of re-addressing routes around it. Find the net\
Vivado named in the message and give that traffic a legal destination (an AIE\
that needs DRAM goes AIE -> NoC -> memory controller, never AIE -> NoC -> AIE;\
a hardened ISP/AIE NMU-NSU pin only mates with the NoC port the silicon wires it\
to), then re-run ipiasm::validate and ipiasm::auto_address."
}

# ---------------------------------------------------------------- AI Engine
# Reject any live graph that loops an engine master through a NoC and back into
# that engine's configuration slave. Select endpoints from the DesignSpec,
# interface metadata, and current Vivado legality checks.

# --- every ai_engine in the design, including inside an automation hierarchy ---
proc ipiasm::_aie_cells {} {
    set out {}
    catch {set out [get_bd_cells -quiet -hierarchical -filter {VLNV=~*:ai_engine:*}]}
    if {[llength $out]} { return $out }
    return [get_bd_cells -quiet -filter {VLNV=~*:ai_engine:*}]
}

# --- the cells on the far end of whatever an interface pin is wired to ---
proc ipiasm::_intf_peer_cells {pin} {
    set p [get_bd_intf_pins -quiet $pin]
    if {$p eq ""} { return {} }
    set net [get_bd_intf_nets -quiet -of_objects $p]
    if {$net eq ""} { return {} }
    set out {}
    foreach q [get_bd_intf_pins -quiet -of_objects $net] {
        if {"$q" eq "$p"} { continue }
        set c [get_bd_cells -quiet -of_objects $q]
        if {$c ne ""} { lappend out $c }
    }
    return $out
}

# --- the aclk pin that belongs to one NoC AXI port ---
# Give each NoC AXI port its own aclk named in CONFIG.ASSOCIATED_BUSIF (PG406).
# A NoC whose PMC ingress and AIE egress both used aclk0 failed with
# "[axi_noc2] Error resolving clock-association for NoC port S00_AXI":
# no elaboration, no NoC solution, no DRAM aperture.
proc ipiasm::_noc_clk_for_port {noc port} {
    # An unreadable NUM_CLKS must refuse: treating it as 0 set NUM_CLKS=1 and
    # deleted every other aclk of a live NoC.
    set cell [get_bd_cells -quiet $noc]
    if {[llength $cell] != 1} { return "" }
    set n [get_property -quiet CONFIG.NUM_CLKS $cell]
    if {![string is integer -strict $n]} { return "" }
    set free ""
    for {set i 0} {$i < $n} {incr i} {
        set p [get_bd_pins -quiet $cell/aclk$i]
        if {$p eq ""} { continue }
        set a [get_property -quiet CONFIG.ASSOCIATED_BUSIF $p]
        if {$a eq $port} { return "aclk$i" }
        # Undriven and unclaimed, or holding only the seed list a fresh NoC gives aclk0 (every
        # port): reuse it, or the seed is left behind as a clock nothing drives. An undriven aclk
        # already given to one other port stays that port's.
        if {$free eq "" && ![ipiasm::_net_wired $p] && [llength [split $a :]] != 1} { set free "aclk$i" }
    }
    if {$free ne ""} { return $free }
    if {[catch {set_property CONFIG.NUM_CLKS [expr {$n + 1}] $cell}]} {
        return ""
    }
    return "aclk$n"
}

# --- the clock of a peer interface that publishes no association ---
# A hierarchy boundary interface often carries no ASSOCIATED_BUSIF: the metadata
# lives on the leaf inside. The ISP subsystem exposes TILE0_ISP_NSU next to a
# tile0_nsu_axi_clk pin and declares neither, so the association lookup found 0
# clocks and the NoC egress port was left on the wrong clock -- validate_bd_design
# then refused the whole NoC with "Error resolving clock-association".
# Naming is the remaining evidence, so use it, but only when it is unambiguous:
# the winner must share at least two distinguishing tokens and must beat every
# other clock on that cell outright. A tie is not evidence and stays unresolved.
proc ipiasm::_intf_name_tokens {name} {
    set generic {axi clk aclk clock s m si mi intf port bus}
    set out {}
    foreach t [split [string tolower [file tail $name]] "_"] {
        if {$t eq "" || [lsearch -exact $generic $t] >= 0} { continue }
        lappend out $t
    }
    return $out
}

proc ipiasm::_peer_clock_by_name {intf} {
    set cell [get_bd_cells -quiet -of_objects $intf]
    if {[llength $cell] != 1} { return "" }
    set want [ipiasm::_intf_name_tokens $intf]
    if {[llength $want] < 2} { return "" }
    set best ""; set best_score 0; set runner 0
    foreach pin [get_bd_pins -quiet -of_objects $cell -filter {TYPE == clk}] {
        set score 0
        foreach t [ipiasm::_intf_name_tokens $pin] {
            if {[lsearch -exact $want $t] >= 0} { incr score }
        }
        if {$score > $best_score} {
            set runner $best_score; set best_score $score; set best $pin
        } elseif {$score > $runner} {
            set runner $score
        }
    }
    if {$best_score >= 2 && $best_score > $runner} { return $best }
    return ""
}

# Associate a NoC port with its own aclk and drive it from the clock of the
# peer interface it carries. Direction-neutral: `peer` is the master for an
# ingress (S*_AXI) port and the slave for an egress (M*_AXI) port -- both sides
# must name a NoC aclk that is driven by that peer's own source clock.
# WARN leaves the clock for check_undriven_clocks.
proc ipiasm::_attach_noc_port_clock {noc port peer} {
    set master $peer
    set clocks [ipiasm::_associated_scalars $master clk]
    if {[llength $clocks] != 1} {
        set inferred [ipiasm::_peer_clock_by_name $master]
        if {$inferred ne ""} { set clocks [list $inferred] }
    }
    if {[llength $clocks] != 1} {
        return "WARN:clock-unassociated([llength $clocks] clocks list [file tail $master];\
associate a [string trimleft $noc /] aclk with $port and drive it from the peer's clock)"
    }
    set clock [lindex $clocks 0]
    set driver [ipiasm::_clock_driver $clock]
    if {$driver eq ""} {
        return "WARN:clock-unassociated([string trimleft $clock /] has no driver yet)"
    }
    set which [ipiasm::_noc_clk_for_port $noc $port]
    if {$which eq ""} { return "NOC_FAIL:no aclk available for $port on $noc" }
    set aclk [get_bd_pins -quiet $noc/$which]
    if {$aclk eq ""} { return "NOC_FAIL:no clock pin $noc/$which" }
    set have [get_bd_nets -quiet -of_objects $aclk]
    set want [get_bd_nets -quiet -of_objects $driver]
    if {$have ne "" && $have ne $want} {
        return "NOC_FAIL:$noc/$which is on $have, not the master clock [string trimleft $driver /]"
    }
    if {[catch {set_property CONFIG.ASSOCIATED_BUSIF $port $aclk} e]} {
        return "NOC_FAIL:associate $port with $which: [lindex [split $e \n] 0]"
    }
    if {$have eq ""} {
        if {[catch {connect_bd_net $driver $aclk} e]} {
            return "NOC_FAIL:clock [string trimleft $driver /] -> $noc/$which: [lindex [split $e \n] 0]"
        }
    }
    return "clk:$port<-$which<-[string trimleft $driver /]"
}

# --- is this NoC port already on a correctly-driven aclk? ---
# Vivado tolerates a port being listed by several aclks -- the seed aclk0 claims
# every port on a fresh axi_noc2 and that alone is not an error. What it demands
# is that AMONG the aclks listing the port, one is driven by the peer's own
# source clock. So test the net, not the listing, or the seed reads as success.
proc ipiasm::_noc_port_clock_ok {noc port want_net} {
    if {$want_net eq ""} { return 0 }
    set n [get_property -quiet CONFIG.NUM_CLKS [get_bd_cells -quiet $noc]]
    if {![string is integer -strict $n]} { return 0 }
    for {set i 0} {$i < $n} {incr i} {
        set aclk [get_bd_pins -quiet $noc/aclk$i]
        if {$aclk eq ""} { continue }
        set assoc [get_property -quiet CONFIG.ASSOCIATED_BUSIF $aclk]
        if {[lsearch -exact [split $assoc ":"] $port] < 0} { continue }
        if {[get_bd_nets -quiet -of_objects $aclk] eq $want_net} { return 1 }
    }
    return 0
}

# --- give every wired NoC AXI port an aclk driven by its peer's clock ---
# The ingress builder attaches a clock as it creates each S*_AXI, so slave-side
# ports come out right. Nothing ever did this for the egress side: M*_AXI ports
# were wired to their destination and left on the seed aclk0. When that seed is
# the wrong clock -- an ISP tile's NSU clock rather than pl0_ref_clk --
# validate_bd_design fails the whole NoC with "Error resolving clock-association
# for NoC port M02_AXI", and the IP then fails to elaborate, so the errors
# cascade well past their cause.
#
# Direction-neutral and idempotent by design: it re-checks both directions and
# only touches a port whose listing aclks all carry the wrong net, so it is safe
# to run after any wiring change, and safe to run twice.
proc ipiasm::attach_noc_port_clocks {{pattern ""}} {
    set fixed {}; set ask {}; set checked 0
    foreach noc [ipiasm::_all_noc_cells] {
        if {$pattern ne "" && ![string match $pattern $noc]} { continue }
        foreach p [get_bd_intf_pins -quiet -of_objects $noc] {
            set port [file tail $p]
            if {![string match "S*_AXI" $port] && ![string match "M*_AXI" $port]} { continue }
            set net [get_bd_intf_nets -quiet -of_objects $p]
            if {$net eq ""} { continue }
            set peer ""
            foreach q [get_bd_intf_pins -quiet -of_objects $net] {
                if {"$q" ne "$p"} { set peer $q; break }
            }
            if {$peer eq ""} { continue }
            incr checked
            set pclk [ipiasm::_associated_scalars $peer clk]
            if {[llength $pclk] != 1} {
                set inferred [ipiasm::_peer_clock_by_name $peer]
                if {$inferred ne ""} { set pclk [list $inferred] }
            }
            set want ""
            if {[llength $pclk] == 1} {
                set d [ipiasm::_clock_driver [lindex $pclk 0]]
                if {$d ne ""} { set want [get_bd_nets -quiet -of_objects $d] }
            }
            if {[ipiasm::_noc_port_clock_ok $noc $port $want]} { continue }
            set r [ipiasm::_attach_noc_port_clock $noc $port $peer]
            if {[string match "clk:*" $r]} {
                lappend fixed "[string trimleft $noc /]/$port: [string range $r 4 end]"
            } else {
                lappend ask "[string trimleft $noc /]/$port: $r"
            }
        }
        # A port moved to its own driven aclk stays listed on the clock it left (aclk0
        # lists every port by default), which then reads as a clock those ports need and
        # nothing drives. Drop such ports from every undriven aclk.
        set owned {}
        foreach clk [get_bd_pins -quiet $noc/aclk*] {
            if {[ipiasm::_net_wired $clk]} {
                lappend owned {*}[split [get_property -quiet CONFIG.ASSOCIATED_BUSIF $clk] :]
            }
        }
        foreach clk [get_bd_pins -quiet $noc/aclk*] {
            if {[ipiasm::_net_wired $clk]} { continue }
            set listed [split [get_property -quiet CONFIG.ASSOCIATED_BUSIF $clk] :]
            set left {}
            foreach busif $listed { if {$busif ni $owned} { lappend left $busif } }
            if {[llength $left] != [llength $listed]} {
                catch {set_property CONFIG.ASSOCIATED_BUSIF [join $left :] $clk}
            }
        }
    }
    set detail ""
    foreach f $fixed { append detail "\n  FIXED:$f" }
    foreach a $ask   { append detail "\n  ASK:$a" }
    # A port left on the wrong clock is not a warning: it fails validate_bd_design and
    # stops the NoC elaborating, so a plan that calls this must stop here, not at delivery.
    if {[llength $ask]} {
        return "NOC_CLK_FAIL:[llength $ask] of $checked wired NoC port(s) still have no aclk\
 driven by their peer's clock (fixed=[llength $fixed])$detail\n  -> declare the peer\
 interface's own clock (CONFIG.ASSOCIATED_BUSIF on the peer cell) and re-run, or wire the\
 port's aclk by hand from the clock its peer runs on."
    }
    return "NOC_CLK:OK checked=$checked fixed=[llength $fixed] escalate=0$detail"
}

# --- wire an AIE configuration slave from a caller-selected legal NoC master ---
# noc_master is a MASTER interface pin on an axi_noc. Do NOT point it at the
# same NoC the AIE's own
# master port feeds -- that is the refused route; check_aie_config catches it.
# noc_clk names the NoC clock pin the AIE's s00_axi_aclk OUTPUT drives (the AIE
# sources this clock; it is not an input you can feed from pl0_ref_clk). Leave
# it empty and the port's own aclk is found, or added.
proc ipiasm::attach_aie_config {aie noc_master {noc_clk ""}} {
    set ac [get_bd_cells -quiet $aie]
    if {$ac eq ""} { return "AIE_FAIL:no cell $aie" }
    set s [get_bd_intf_pins -quiet $aie/S00_AXI]
    if {$s eq ""} { return "AIE_FAIL:$aie has no S00_AXI config slave" }
    set m [get_bd_intf_pins -quiet $noc_master]
    if {$m eq ""} { return "AIE_FAIL:no interface pin $noc_master" }
    set mc [get_bd_cells -quiet -of_objects $m]
    if {![string match "*:axi_noc*" [get_property -quiet VLNV $mc]]} {
        return "AIE_FAIL:$noc_master is on [get_property -quiet VLNV $mc], not an\
axi_noc -- product architecture requires a compatible NoC master"
    }
    if {[get_property -quiet MODE $m] ne "Master"} {
        return "AIE_FAIL:$noc_master is a [get_property -quiet MODE $m] port; the\
AIE config slave is fed by a NoC MASTER port"
    }
    # The loop the automation builds, refused before it is drawn.
    foreach p [get_bd_intf_pins -quiet $aie/*] {
        if {[get_property -quiet MODE $p] ne "Master"} { continue }
        foreach peer [ipiasm::_intf_peer_cells $p] {
            if {"$peer" eq "$mc"} {
                return "AIE_FAIL:[string trimleft $aie /] already drives\
[string trimleft $mc /] from [file tail $p], so feeding its S00_AXI from\
[file tail $m] on that same NoC is an AIE-to-AIE path -- the NoC compiler\
refuses it (\[Ipconfig 75-153\]). Use a different NoC for the config path, or\
send [file tail $p] to a memory NoC slave port instead."
            }
        }
    }
    # Endpoint categories are required by the documented hardened interface.
    catch {set_property CONFIG.CATEGORY NOC $s}
    catch {set_property CONFIG.CATEGORY aie $m}
    set r [ipiasm::connect_intf $noc_master $aie/S00_AXI]
    if {![string match "SUCCESS:*" $r] && ![string match "SKIP:*" $r]} {
        return "AIE_FAIL:config path $noc_master -> $aie/S00_AXI: $r"
    }
    # The verdict is whether the net exists, not whether 41-3287 printed.
    if {[get_bd_intf_nets -quiet -of_objects $s] eq ""} {
        return "AIE_FAIL:no net on $aie/S00_AXI after connect"
    }
    set clk "not wired"
    set cp [get_bd_pins -quiet $aie/s00_axi_aclk]
    set which $noc_clk
    if {$which ne ""} { set which [file tail $which] }
    set existing ""
    if {$cp ne "" && [llength [info commands get_bd_nets]]} {
        set current_net [get_bd_nets -quiet -of_objects $cp]
        if {$current_net ne ""} {
            foreach candidate [get_bd_pins -quiet "$mc/aclk*"] {
                if {[get_bd_nets -quiet -of_objects $candidate] eq $current_net} {
                    set existing [file tail $candidate]
                    break
                }
            }
        }
    }
    if {$existing ne ""} {
        if {$which ne "" && $which ne $existing} {
            return "AIE_FAIL:$aie/s00_axi_aclk is already on $mc/$existing, not requested $mc/$which"
        }
        set which $existing
    }
    if {$which eq ""} { set which [ipiasm::_noc_clk_for_port $mc [file tail $m]] }
    if {$cp ne "" && $which eq ""} {
        return "AIE_FAIL:no NoC clock is available for [file tail $m]"
    }
    if {$cp ne ""} {
        # The port has to own its clock, or the NoC cannot resolve the
        # association and the whole design stops elaborating.
        set np [get_bd_pins -quiet $mc/$which]
        if {$np eq ""} { return "AIE_FAIL:no clock pin [string trimleft $mc /]/$which" }
        if {[catch {set_property CONFIG.ASSOCIATED_BUSIF [file tail $m] $np} e]} {
            return "AIE_FAIL:associate [file tail $m] with $which: [lindex [split $e \n] 0]"
        }
        if {$existing eq ""} {
            set cr [ipiasm::connect_net $aie/s00_axi_aclk [string trimleft $mc /]/$which]
            if {![string match "SUCCESS:*" $cr] && ![string match "SKIP:*" $cr]} {
                return "AIE_FAIL:clock $aie/s00_axi_aclk -> [string trimleft $mc /]/$which: $cr"
            }
        }
        if {[llength [info commands get_bd_nets]]} {
            set an [get_bd_nets -quiet -of_objects $cp]
            set nn [get_bd_nets -quiet -of_objects $np]
            if {$an eq "" || $an ne $nn} {
                return "AIE_FAIL:clock verification failed for $aie/s00_axi_aclk -> $mc/$which"
            }
        }
        set clk "s00_axi_aclk -> $which"
    }
    return "SUCCESS:AIE config [string trimleft $mc /]/[file tail $m] ->\
[string trimleft $aie /]/S00_AXI (clk: $clk)"
}

# --- gate: is every AIE reachable, and reachable by a legal route? ---
# Cheap and structural: no validate, no NoC compiler. It catches the automation's
# loop at the moment it is created, before the compiler is ever asked, which is
# the difference between fixing one connection and re-deriving a whole design
# from a missing DRAM aperture three gates later.
proc ipiasm::check_aie_config {} {
    set aies [ipiasm::_aie_cells]
    if {![llength $aies]} {
        return "AIE:NA this design has no ai_engine cell"
    }
    set fails {}
    set ok {}
    foreach aie $aies {
        set short [string trimleft $aie /]
        set s [get_bd_intf_pins -quiet $aie/S00_AXI]
        if {$s eq ""} {
            lappend fails "$short has no S00_AXI -- nothing can configure the array"
            continue
        }
        set drv ""
        set net [get_bd_intf_nets -quiet -of_objects $s]
        if {$net ne ""} {
            foreach q [get_bd_intf_pins -quiet -of_objects $net] {
                if {"$q" ne "$s"} { set drv $q }
            }
        }
        if {$drv eq ""} {
            lappend fails "$short/S00_AXI is undriven -- the PS cannot reach the\
array, so nothing loads a graph into it. Drive it from a NoC master port\
(ipiasm::attach_aie_config)."
            continue
        }
        set dc [get_bd_cells -quiet -of_objects $drv]
        set dshort "[string trimleft $dc /]/[file tail $drv]"
        # The refused shape: the same AIE on both ends of one NoC.
        set looped {}
        foreach p [get_bd_intf_pins -quiet $aie/*] {
            if {[get_property -quiet MODE $p] ne "Master"} { continue }
            foreach peer [ipiasm::_intf_peer_cells $p] {
                if {"$peer" eq "$dc"} { lappend looped [file tail $p] }
            }
        }
        if {[llength $looped]} {
            lappend fails "$short is on BOTH ends of [string trimleft $dc /]:\
[join $looped {, }] drives into it and [file tail $drv] comes back out to\
S00_AXI. That is the AIE-to-AIE path the NoC compiler refuses (\[Ipconfig\
75-153\]), and while it stands the NoC solution does not resolve and no memory\
aperture is published. Remove the loop and select legal configuration and data endpoints from\
the DesignSpec and current interface graph."
            continue
        }
        if {![string match "*:axi_noc*" [get_property -quiet VLNV $dc]]} {
            lappend fails "$short/S00_AXI is driven by $dshort on\
[get_property -quiet VLNV $dc], not a compatible NoC master."
            continue
        }
        set cat [get_property -quiet CONFIG.CATEGORY $s]
        set note ""
        if {$cat ne "NOC"} {
            set note " (CONFIG.CATEGORY on S00_AXI is '$cat'; documentation requires NOC)"
        }
        lappend ok "$short/S00_AXI <- $dshort$note"
    }
    if {[llength $fails]} {
        return "AIE_FAIL:[llength $fails] AI Engine connectivity\
problem(s):\n  [join $fails "\n  "]"
    }
    return "AIE:OK [llength $ok] AI Engine config path(s):\n  [join $ok "\n  "]"
}

# --- gate: the NoC compiler's own verdict on the traffic spec ---
# The third place bandwidth is lost silently, and the one `validate` cannot tell
# you about. validate_bd_design checks NMU site counts, per-port DDRMC bandwidth
# (\[Ipconfig 75-886\]) and ports-per-master (\[BD 41-3265\]) and then STOPS -- it
# never checks inter-switch channel capacity. An excessive declaration can still
# return rc=0 while oversubscribing emitted routes. The signal is the
# SolutionType field dropping from OPTIMAL to SUBOPTIMAL.
#
# This also answers a question no other gate does: does anything actually REACH
# the memory controller? A path terminating at an `*_nsu` instead of an `MCn_ddrc`
# means the traffic never gets to DDR.
#
# want_memory 0 turns off the DDRMC reachability check for a design that
# deliberately has no DDR (rare -- say so before you pass it).
proc ipiasm::_check_noc_solution_impl {{want_memory 1}} {
    if {![llength [get_bd_cells -quiet -filter {VLNV=~*axi_noc*}]]} {
        return "NOC:NA this design has no NoC cell (non-Versal parts have none)"
    }
    set fails {}
    set notes {}

    # --- 1. DRAM reachability, from the address map (always current) ---
    set ddr_windows [llength [ipiasm::_noc_ddr_segs]]
    set p [ipiasm::_dram_paths]
    set declared    [dict get $p declared]
    set unaddressed [dict get $p unaddressed]
    set addressed   [dict get $p addressed]
    set aggregated  [dict get $p aggregated]
    # The illegal-route findings come first: when the compiler has refused a
    # route, every other line below is a downstream symptom of it, and advice
    # aimed at the symptoms is what sends an agent in circles.
    set illegal [ipiasm::_illegal_noc_findings]
    foreach f $illegal { lappend fails "REFUSED ROUTE: $f" }
    if {$want_memory} {
        # Ground truth, from the address map. The declaration-side view below
        # cannot see through an INI hop -- once the ingress is INI no AXI port
        # declares MC_* -- and a design where nothing reaches DDR reads exactly
        # like a correct one. This is what decides the verdict.
        set on_dram  [ipiasm::_dram_masters]
        set unbraced [ipiasm::_unbraced_noc_decls]
        if {$ddr_windows == 0} {
            # Same trap as check_memory_subsystem: two designs land on "no DRAM
            # window" and they need OPPOSITE advice. Telling a NoC whose
            # controllers are already bound to go attach board memory is wrong.
            set bound [ipiasm::_bound_mem_intfs]
            if {[llength $bound]} {
                set why "run ipiasm::check_noc_legality"
                if {[llength $illegal]} {
                    set why "fix the refused route(s) listed above"
                }
                set direct [ipiasm::_mc_noc_axi_ingress]
                if {![llength $illegal] && [llength $direct]} {
                    set why "the memory NoC has AXI slave port(s) declaring a\
controller directly ([join $direct {, }]); compare this live structure with\
the current automation schema and product documentation"
                }
                lappend fails "the NoC publishes no addressable DRAM window -> DDR\
is unreachable for every master, even though its controllers ARE bound to board\
memory {[join $bound {, }]}. Do NOT re-run ipiasm::attach_board_memory and do NOT\
re-run addressing: both already succeeded and neither creates the aperture.\
Something upstream is stopping the NoC solution from resolving -- $why."
            } else {
                lappend fails "the NoC publishes no addressable DRAM window -> DDR is\
unreachable for every master, and no controller is bound to a board memory\
interface. This is the memory subsystem, not the traffic spec: run\
ipiasm::check_memory_subsystem and ipiasm::attach_board_memory, then re-address."
            }
        } elseif {![llength $on_dram]} {
            # The window exists and no master owns any of it. Every earlier
            # signal reads clean here: validate passes, the NoC compiler is
            # quiet, auto_address returns success, and the memory gate is happy
            # because the controllers are bound and the aperture is published.
            set why "no master address space holds one of them. assign_bd_address\
maps what it can reach and returns success for the rest, so the address step\
will not report this."
            if {[llength $unbraced]} {
                set why "the traffic spec on [join $unbraced {, }] is written with\
unbraced values (read_bw 2000 rather than read_bw {2000}), which axi_noc2 accepts\
and then discards -- the route is never built, so there is nothing for the memory\
window to be mapped through. Rewrite those declarations with ipiasm::set_qos /\
apply_qos_plan, which brace them, and re-run addressing."
            }
            lappend fails "$ddr_windows DRAM window(s) are published and NOT ONE\
master can address DDR: $why"
        }
        if {$declared == 0 && ![llength $on_dram]} {
            lappend fails "no NoC slave interface declares traffic to a memory\
controller (no MC_* destination in any CONFIG.CONNECTIONS) -> nothing in this\
design has asked for DRAM bandwidth. Declare it with ipiasm::set_qos /\
apply_qos_plan before addressing."
        } elseif {[llength $unaddressed]} {
            # Only worth re-addressing if there is something to address INTO.
            # With no window published this list is a restatement of the line
            # above, and pointing it at auto_address closes the loop that ran
            # for 95 minutes in a measured run.
            if {$ddr_windows == 0} {
                set fix "the same root cause as the missing window -- there is no\
DDR segment anywhere to map them to, so address these only after a window exists."
            } else {
                set fix "declared bandwidth to a controller the master cannot address\
is not a data path. Re-run the address assignment (ipiasm::auto_address /\
place_named_windows) and confirm a DDR segment appears in each of those spaces."
            }
            set line "[llength $unaddressed] master(s) declare bandwidth to a\
memory controller but own NO DRAM address window:\n    [join $unaddressed "\n    "]\n\
   -> $fix"
            # This list is inferred from CONFIG.CONNECTIONS, and on a design
            # whose masters demonstrably do hold DRAM it has been measured to
            # name masters that are in fact addressed -- an external AXI port
            # into a memory NoC reads as unaddressed while owning both windows.
            # Where the address map disagrees with the inference, the address
            # map wins and this drops to a note.
            if {[llength $on_dram]} {
                lappend notes "$line\n   (the address map shows\
[llength $on_dram] master(s) reaching DRAM, so treat this as a lead, not a\
verdict -- check the named spaces directly)"
            } else {
                lappend fails $line
            }
        }
    }

    # --- 2. the compiler's routing verdict, if its artifact is current ---
    set f [ipiasm::_ncr_path]
    set sol "" ; set paths {}
    if {$f eq ""} {
        lappend notes "no .ncr yet (the NoC compiler writes it at target\
generation, not at validate) -- SolutionType unchecked"
    } elseif {![ipiasm::_ncr_is_fresh $f]} {
        lappend notes "the .ncr on disk is OLDER than the block design, so it\
describes an earlier state -- SolutionType unchecked. It refreshes at\
generate_target/synthesis, not at validate_bd_design."
    } else {
        lassign [ipiasm::_ncr_parse $f] sol paths
        set short {} ; set rd 0 ; set ach 0
        foreach q $paths {
            incr rd  [dict get $q rbw]
            incr ach [dict get $q rach]
            if {[dict get $q rach] < [dict get $q rbw] ||
                [dict get $q wach] < [dict get $q wbw]} {
                lappend short "[file tail [dict get $q from]] -> [file tail [dict get $q to]]\
declared rd=[dict get $q rbw]/wr=[dict get $q wbw]\
achieved rd=[dict get $q rach]/wr=[dict get $q wach]"
            }
        }
        lappend notes "solution=$sol paths=[llength $paths] declared_rd=${rd}MB/s\
achieved_rd=${ach}MB/s"
        if {$sol ne "OPTIMAL" && $sol ne "UNKNOWN"} {
            lappend fails "SolutionType=$sol (not OPTIMAL) -> the compiler gave up\
on balanced routing. validate still returns rc=0, so this is the only signal.\
Back the declared bandwidth off until it reads OPTIMAL again; the current\
topology determines the cliff."
        }
        if {[llength $short]} {
            lappend fails "[llength $short] path(s) achieve less than they declared:\n   \
[join $short "\n    "]"
        }
    }

    # on_dram leads because it is the measured fact; the rest is the inference
    # that was reading clean while no master could reach memory.
    set reach "?"
    if {$want_memory} { set reach [llength $on_dram] }
    set head "ddr_windows=$ddr_windows masters_on_dram=$reach\
declared_to_mc=$declared addressed=[llength $addressed]\
unaddressed=[llength $unaddressed] aggregated=[llength $aggregated]"
    if {[llength $notes]} { append head "\n  " [join $notes "\n  "] }
    if {[llength $fails]} { return "NOC_FAIL:$head\n  [join $fails "\n  "]" }
    return "NOC:OK $head"
}

# --- find the highest traffic declaration the compiler still routes cleanly ---
# Over-declaring is not the safe direction. Past the point where the routes stop
# balancing, the compiler abandons the good arrangement and the design carries
# LESS traffic than a smaller declaration would have. Find the right declaration
# on the current topology rather than copying a measured threshold.
#
# `plan` is an ipiasm::apply_qos_plan plan. `scales` are multipliers applied to
# every read_bw/write_bw in it (e.g. {1.0 1.5 2.0 3.0}). Each point costs one
# validate -- about 10 s on a camera-class design, since the block design is only
# mutated and re-validated, never rebuilt.
#
# Run this in the traffic layer, BEFORE addressing: every point rewrites
# CONFIG.CONNECTIONS, which is a connectivity change (\[BD 41-1931\]).
#
# When channel_raw_mbs is supplied from documentation or live metadata, the
# sweep may skip declarations far below capacity. Without it, run the sweep and
# let the compiler characterize the current topology.
proc ipiasm::qos_sweep {plan scales {force 0} {channel_raw_mbs ""}} {
    if {![llength $plan] || ![llength $scales]} {
        return "QOS_SWEEP_FAIL:need a plan and at least one scale"
    }
    set base 0
    foreach row $plan {
        lassign $row si dest rb wb tc burst
        if {[string is integer -strict $rb]} { incr base $rb }
    }
    # Capacity: the DDRMC egress side, which is what caps a memory-bound design.
    set mc 0 ; set mcp 1
    foreach c [get_bd_cells -hierarchical -quiet] {
        if {![string match "*:axi_noc*" [get_property -quiet VLNV $c]]} { continue }
        set n 0 ; catch {set n [get_property -quiet CONFIG.NUM_MC $c]}
        if {[string is integer -strict $n] && $n > 0} {
            incr mc $n
            set p 1 ; catch {set p [get_property -quiet CONFIG.NUM_MCP $c]}
            if {[string is integer -strict $p] && $p > $mcp} { set mcp $p }
        }
    }
    set cap 0
    if {$channel_raw_mbs ne ""} {
        set effective [ipiasm::noc_channel_mbs $channel_raw_mbs]
        if {[string match "QOS_RATE_FAIL:*" $effective]} { return $effective }
        set cap [expr {$mc * $mcp * $effective}]
    }
    if {!$force && $cap > 0 && $base < $cap / 2} {
        return "QOS_SWEEP:SKIP declared=${base}MB/s is under half the ${cap}MB/s\
this fabric can carry ($mc controller(s) x $mcp port(s) x [ipiasm::noc_channel_mbs]MB/s),\
so every point would route OPTIMAL. Apply the plan with ipiasm::apply_qos_plan and\
gate it with check_qos + check_noc_solution. Pass force=1 to sweep anyway."
    }
    set rows {}
    set best "" ; set best_scale ""
    foreach s $scales {
        set scaled {}
        foreach row $plan {
            lassign $row si dest rb wb tc burst
            if {[string is integer -strict $rb]} { set rb [expr {int($rb * $s)}] }
            if {[string is integer -strict $wb]} { set wb [expr {int($wb * $s)}] }
            lappend scaled [list $si $dest $rb $wb $tc $burst]
        }
        set ap [ipiasm::apply_qos_plan $scaled]
        if {[string match "QOS_FAIL:*" $ap]} {
            lappend rows [format "  x%-5s apply FAILED" $s]
            continue
        }
        set vrc [catch {validate_bd_design -force} vmsg]
        if {$vrc} {
            lappend rows [format "  x%-5s validate FAILED" $s]
            continue
        }
        set ns [ipiasm::check_noc_solution]
        set verdict [expr {[string match "NOC:OK*" $ns] ? "OK" : "REJECTED"}]
        regexp {solution=(\S+)} $ns -> soltype
        lappend rows [format "  x%-5s %-9s %s" $s $verdict [string range $ns 0 110]]
        if {$verdict eq "OK"} { set best $scaled ; set best_scale $s }
    }
    if {$best eq ""} {
        return "QOS_SWEEP_FAIL:no scale routed cleanly\n[join $rows \n]\n  -> every\
point was rejected. The plan's shape is wrong, not just its magnitude: check that\
each master declares its own total (NOT a per-controller share) and that the\
memory paths exist at all."
    }
    # Leave the design on the best point rather than wherever the sweep ended.
    ipiasm::apply_qos_plan $best
    catch {validate_bd_design -force}
    return "QOS_SWEEP:best=x$best_scale\n[join $rows \n]\n  -> design left at\
x$best_scale. Anything above it was rejected by the compiler, so this is the\
ceiling for this topology, not a preference."
}

# --- normalize SELECTED_SIM_MODEL across NoC/AIE cells ---
# A NoC created standalone is `rtl` while AIE-automation's internal axi_noc is
# `tlm` -> validate fails [BD 41-2662]. Align all NoC-bearing cells to one mode
# (default tlm, which the hardened automation uses) before validate.
proc ipiasm::normalize_sim_model {{mode tlm}} {
    set touched {}
    foreach c [get_bd_cells -quiet] {
        set vlnv [get_property -quiet VLNV $c]
        if {[string match "*:axi_noc*" $vlnv] || [string match "*:ai_engine*" $vlnv]} {
            if {![catch {set_property SELECTED_SIM_MODEL $mode $c}]} { lappend touched $c }
        }
    }
    return "SIM_MODEL:$mode applied_to={$touched}"
}

# --- drive a clock pin buried inside a subsystem cell that exposes no boundary ---
# Block automation can leave an internal clk pin (e.g. aie_ml_inference_ss/
# aie_ml_inference/aclk0) with no subsystem-boundary pin to drive it. This creates
# the boundary pin and wires both sides (inner pin <- new boundary <- external src).
#   inner_pin : full hierarchical pin path (cell_ss/.../aclkN)
#   src_pin   : top-level clock source (e.g. versal_ps/pl2_ref_clk)
#   bname     : boundary pin name to create on the subsystem (default aclk_in)
proc ipiasm::wire_hier_clk {inner_pin src_pin {bname aclk_in}} {
    set inner [get_bd_pins -quiet $inner_pin]
    if {$inner eq ""} { return "CLK_FAIL:no inner pin $inner_pin" }
    # subsystem = the top-level cell that owns this hierarchical pin
    set ss [lindex [split $inner_pin /] 0]
    set bpin "$ss/$bname"
    if {[get_bd_pins -quiet $bpin] eq ""} {
        if {[catch {create_bd_pin -dir I -type clk $bpin} e]} { return "CLK_FAIL:mkpin:$e" }
    }
    if {[catch {connect_bd_net [get_bd_pins $bpin] $inner} e1]} { return "CLK_FAIL:inner:$e1" }
    if {[catch {connect_bd_net [get_bd_pins $src_pin] [get_bd_pins $bpin]} e2]} { return "CLK_FAIL:outer:$e2" }
    return "SUCCESS:hier_clk $src_pin -> $bpin -> $inner_pin"
}

# ============================================================
# 4. Multi-instance orchestration
# ============================================================

# --- instantiate + configure N copies with deterministic names ---
# Reuses ipcfg::create_cell_cfg for each copy. Names are <base>_0 .. <base>_{n-1}
# (matches Vivado's own auto-naming so it lines up with bd.tcl).
#   vlnv:  version-free IP VLNV
#   base:  cell base name (e.g. mipi_csi2_rx_subsystem)
#   n:     instance count
#   d:     optional shared CONFIG dict applied to every instance ("" = none)
# Returns "SUCCESS:instances={c0 c1 ...}" or "MULTI_FAIL:<cell>:<err>".
proc ipiasm::instantiate_n {vlnv base n {d {}}} {
    if {[set g [ipiasm::_need_plan "instantiate_n $base"]] ne ""} { return $g }
    if {[set g [ipiasm::_need board "instantiate_n $base"]] ne ""} { return $g }
    if {![ipcfg::vlnv_ok $vlnv]} { return "MULTI_FAIL:no ipdef for $vlnv" }
    set made {}
    for {set i 0} {$i < $n} {incr i} {
        set cell "${base}_$i"
        set r [ipcfg::create_cell_cfg $vlnv $cell $d]
        if {[string match "CONFIGURE_FAIL:*" $r]} { return "MULTI_FAIL:$cell:$r" }
        lappend made $cell
    }
    return "SUCCESS:instances={$made}"
}

# ============================================================
# 4b. AUTO-FIX with confidence (else escalate to the user)
# ============================================================
# Philosophy: fix only what is UNAMBIGUOUS; everything else is returned as a
# structured ESCALATE list the agent turns into AskQuestion prompts. Never guess
# a design decision (which clock for an ambiguous pin, a BAR offset, a datapath
# peer) -- ask.

# --- internal: unconnected INPUT pins of a cell, filtered by TYPE ---
proc ipiasm::_open_in_pins {cell type} {
    set out {}
    foreach pin [get_bd_pins -quiet -of_objects [get_bd_cells -quiet $cell]] {
        if {[catch {set ty [get_property TYPE $pin]}]} { continue }
        if {$ty ne $type} { continue }
        if {[catch {set dir [get_property DIR $pin]}]} { set dir "I" }
        if {$dir ne "I"} { continue }
        if {![ipiasm::_net_wired $pin]} { lappend out $pin }
    }
    return $out
}

# --- CONFIDENT clock/reset wiring from a known domain map ---
# domain_map: {cell mhz cell mhz ...} (each functional cell's intended domain,
# straight from the manifest clock_domains). For each cell:
#   * exactly ONE open input clk pin + clk_<mhz> exists  -> connect (CONFIDENT)
#   * >1 open clk pin, or no clk_<mhz> source            -> ESCALATE (ask)
#   * same logic for reset vs proc_sys_reset_<mhz>/peripheral_aresetn
# Returns "AUTOFIX_DOMAINS:fixed={..} ESCALATE={pin|reason ..}".
proc ipiasm::autofix_domains {domain_map} {
    variable clk_wizard
    set wiz $clk_wizard
    if {$wiz eq ""} {
        set candidates [get_bd_cells -quiet -hierarchical -filter {VLNV =~ *:clk_wizard:*}]
        if {[llength $candidates] == 1} { set wiz [lindex $candidates 0] }
    }
    set fixed {}; set escalate {}
    foreach {cell mhz} $domain_map {
        if {[get_bd_cells -quiet $cell] eq ""} { lappend escalate "${cell}(no-such-cell)"; continue }
        # ---- clocks ----
        set clks [ipiasm::_open_in_pins $cell clk]
        if {[llength $clks] == 1} {
            set src "$wiz/clk_$mhz"
            if {$wiz ne "" && [get_bd_pins -quiet $src] ne ""} {
                if {![catch {connect_bd_net [get_bd_pins $src] [lindex $clks 0]}]} {
                    lappend fixed "[lindex $clks 0]<-clk_$mhz"
                } else {
                    lappend escalate "[lindex $clks 0](connect-failed)"
                }
            } else {
                lappend escalate "[lindex $clks 0](no clk_$mhz source on wizard; ask for clock source)"
            }
        } elseif {[llength $clks] > 1} {
            lappend escalate "${cell}(>1-open-clk-inputs:$clks)"
        }
        # ---- resets ----
        set rsts [ipiasm::_open_in_pins $cell rst]
        set rc "proc_sys_reset_$mhz"
        set rstout [get_bd_pins -quiet $rc/peripheral_aresetn]
        if {[llength $rsts] == 1} {
            if {$rstout ne ""} {
                if {![catch {connect_bd_net $rstout [lindex $rsts 0]}]} {
                    lappend fixed "[lindex $rsts 0]<-$rc"
                } else {
                    lappend escalate "[lindex $rsts 0](connect-failed)"
                }
            } else {
                lappend escalate "[lindex $rsts 0](no $rc; ask for reset source)"
            }
        } elseif {[llength $rsts] > 1} {
            lappend escalate "${cell}(>1-open-rst-inputs:$rsts)"
        }
    }
    return "AUTOFIX_DOMAINS:fixed={$fixed} ESCALATE={$escalate}"
}

# --- CONFIDENT address assignment; leftover unmapped -> escalate ---
# windows: optional list {space seg offset range space seg offset range ...} of
# spec-declared placements (PCIe BAR windows, ctrl regs, mailbox). Each is placed
# EXPLICITLY first (ipiasm::add_addr_seg -> assign_bd_address -offset/-range,
# validated UG835). Already-fixed segs are HONORED (never relocated). Everything
# else is auto-assigned; whatever is STILL unmapped is escalated, never guessed.
proc ipiasm::autofix_address {{windows {}}} {
    if {[set g [ipiasm::_need inventory "autofix_address"]] ne ""} { return $g }
    if {[llength [ipiasm::_all_noc_cells]] && [set g [ipiasm::_need qos "autofix_address"]] ne ""} { return $g }
    set placed {}; set failed {}
    set honored [ipiasm::_fixed_segs]
    # 1) explicit spec windows
    foreach {space seg offset range} $windows {
        set nm "seg_[string map {/ _} [file tail $seg]]"
        set r [ipiasm::add_addr_seg $space $seg $nm $offset $range]
        if {[string match "SUCCESS:*" $r]} {
            lappend placed "$seg@$offset"
        } else {
            lappend failed "${seg}($r)"
        }
    }
    # 2) auto-map the remaining unmapped slaves (does not touch fixed segs)
    catch {assign_bd_address}
    set un [ipiasm::_unassigned_segs]
    set out "AUTOFIX_ADDR:placed={$placed} honored_fixed={$honored}"
    if {[llength $failed] > 0} { append out " window_fail={$failed}" }
    if {[llength $un] == 0 && [llength $failed] == 0} {
        append out " ESCALATE={}"
    } else {
        set esc $un
        foreach f $failed { lappend esc $f }
        append out " ESCALATE={$esc (need explicit offset/range -> ask, e.g. spec BAR windows)}"
    }
    return $out
}

# --- Orchestrator: confident fixes + the escalation question set ---
# Runs the confident fixers, then scans for the design-specific sources that must
# NEVER be auto-guessed (the clock-wizard input clk_in1, each proc_sys_reset
# ext_reset_in) and the still-unwired interface pins, packaging them as questions.
# domain_map drives clk/rst autofix. Returns a multi-line report:
#   AUTOFIX:fixed=N escalate=M
#   FIXED: ...
#   ASK:
#     [Q] <gap> | options: <opt1> ; <opt2> ; Other
# The agent should present each ASK line via AskQuestion before claiming done.
proc ipiasm::autofix {domain_map {windows {}} {oos_pins {}}} {
    if {[set gate [ipiasm::_need inventory "autofix"]] ne ""} { return $gate }
    if {[llength [ipiasm::_all_noc_cells]] && [set gate [ipiasm::_need qos "autofix"]] ne ""} { return $gate }
    set fixed {}; set ask {}

    set d [ipiasm::autofix_domains $domain_map]
    regexp {fixed=\{(.*)\} ESCALATE=\{(.*)\}} $d -> df de
    foreach f $df { lappend fixed $f }
    foreach e $de { if {$e ne ""} { lappend ask "Q(clk/reset): $e | options: tie to existing clk/reset domain ; new clock wizard output ; external board pin ; Other" } }

    # CONFIDENT: a NoC AXI port must sit on an aclk driven by its peer's own
    # clock (PG406). The peer is already wired, so the correct clock is a fact
    # of the design, not a guess -- and leaving it wrong is not a warning but a
    # hard validate_bd_design failure that also stops the NoC elaborating.
    if {[llength [ipiasm::_all_noc_cells]]} {
        set nc [ipiasm::attach_noc_port_clocks]
        foreach line [split $nc "\n"] {
            set line [string trim $line]
            if {[string match "FIXED:*" $line]} { lappend fixed "noc-clock:[string range $line 6 end]" }
            if {[string match "ASK:*" $line]} {
                lappend ask "Q(noc-clock): [string range $line 4 end] | options:\
associate the port with an aclk driven by the peer's source clock ;\
declare the peer interface's ASSOCIATED_BUSIF then retry ; Other"
            }
        }
    }

    set a [ipiasm::autofix_address $windows]
    regexp {placed=\{(.*?)\}} $a -> ap
    if {$ap ne ""} { lappend fixed "address:placed={$ap}" }
    set ae ""
    regexp {ESCALATE=\{(.*)\}} $a -> ae
    if {[string trim $ae] ne ""} {
        lappend ask "Q(address): $ae | options: auto offset ; explicit offset from spec ; exclude from map ; Other"
    } else {
        lappend fixed "address:all-mapped"
    }

    # CONFIDENT: caller-declared out-of-scope boundary pins -> externalize them
    # (UG835 make_bd_*_external). These are NOT guesses: the manifest marks them
    # OOS, so exposing as a top-level port is the correct, validatable closure.
    if {[llength $oos_pins] > 0} {
        set ex [ipiasm::externalize_oos $oos_pins]
        regexp {external=\{(.*?)\}} $ex -> exok
        if {$exok ne ""} { lappend fixed "externalized={$exok}" }
    }

    # design-specific external sources -> always ask (never guess)
    foreach pin [get_bd_pins -quiet -of_objects [get_bd_cells -quiet]] {
        set leaf [file tail $pin]
        if {$leaf eq "clk_in1" && ![ipiasm::_net_wired $pin]} {
            lappend ask "Q(ext-clock): $pin needs a board/PS reference clock | options: PS pl_clk ; board clock port ; Other"
        }
        if {$leaf eq "ext_reset_in" && ![ipiasm::_net_wired $pin]} {
            lappend ask "Q(ext-reset): $pin needs a reset source | options: PS pl_resetn ; board reset port ; constant 1 ; Other"
        }
    }

    # unwired interface pins -> peer is a design decision -> ask
    foreach ipin [get_bd_intf_pins -quiet -of_objects [get_bd_cells -quiet]] {
        if {![ipiasm::_intf_wired $ipin]} {
            lappend ask "Q(intf-peer): $ipin is unconnected | options: connect per manifest edge ; make external port ; leave open (out-of-scope) ; Other"
        }
    }

    set out "AUTOFIX:fixed=[llength $fixed] escalate=[llength $ask]"
    if {[llength $fixed] > 0} { append out "\nFIXED:\n  [join $fixed "\n  "]" }
    if {[llength $ask] > 0}   { append out "\nASK:\n  [join $ask "\n  "]" }
    return $out
}

# ============================================================
# 5. Validate + assembly coverage report
# ============================================================

# --- run validate_bd_design, classify the outcome ---
# validate_bd_design RAISES on a hard error; warnings/criticals are messages.
# Returns "VALIDATE:PASS" or "VALIDATE:FAIL:<err>". The agent should ALSO read
# the console for [BD nn-nnnn] CRITICAL WARNING lines and feed them to
# assembly_report (they are C-level messages, like the rules dump).
proc ipiasm::validate {} {
    variable last_validate_console
    variable last_reset_sync_findings
    variable validated_state
    # A pass is reused while nothing has changed since it: no BD-, property- or
    # project-mutating command has run (the execution trace behind
    # _mutation_epoch counts them, whoever issued them), and the design context
    # and shape are the same. On a large design each validate_bd_design -force
    # costs 20-45 s and the repair loop and final gate repeat it on an
    # unchanged design. Failures are never reused. IPIASM_NO_VERIFY_CACHE=1
    # turns reuse off.
    set state [ipiasm::_validate_state]
    if {$state ne {} && [dict exists $validated_state state]
        && [dict get $validated_state state] eq $state} {
        set last_validate_console [dict get $validated_state console]
        set last_reset_sync_findings {}
        set ::ipiasm::design_validated 1
        ipiasm::_validate_reused
        return "VALIDATE:PASS"
    }
    set validated_state {}
    set last_validate_console ""
    set last_reset_sync_findings {}
    set result [ipiasm::_validate_run]
    if {$result eq "VALIDATE:PASS"} {
        # Recorded with the state AFTER the run: validate_bd_design itself and
        # the parameter propagation it triggers count as mutations, so the
        # state before would never match again.
        set after [ipiasm::_validate_state]
        if {$after ne {}} {
            set validated_state [dict create state $after console $last_validate_console]
        }
    }
    return $result
}

# Everything a passing validation depends on, or {} when any part of it is
# unavailable (then validation always runs).
proc ipiasm::_validate_state {} {
    set epoch [ipiasm::_mutation_epoch]
    set shape [ipiasm::_design_fp]
    if {$epoch eq "unknown" || $shape eq ""} {return {}}
    if {[catch {current_bd_design -quiet} bd]} {set bd ""}
    # The registered clock contracts feed check_interface_clock_domains.
    set contracts [expr {[info exists ::ipiasm::clock_contracts] ? $::ipiasm::clock_contracts : ""}]
    return [list $epoch [ipiasm::context_identity] $bd $shape $contracts]
}

# No-op marker so the build trace (timing_report.py) counts reused validations.
proc ipiasm::_validate_reused {} {
    incr ::ipiasm::validate_reuse_count
}

proc ipiasm::_validate_run {} {
    variable last_validate_console
    variable last_reset_sync_findings
    # Reject all currently detectable metadata/clock/reset contract defects in
    # one cheap pass. This avoids spending a forced validation on each defect.
    set interface_preflight [ipiasm::preflight_interface_contracts]
    if {[string match "INTF_PREFLIGHT_FAIL:*" $interface_preflight]} {
        set last_validate_console "CRITICAL WARNING: $interface_preflight\n"
        return "VALIDATE:FAIL:$interface_preflight"
    }
    set reset_before {}
    foreach {id reason} [ipiasm::clock_reset_msg_ids] {
        dict set reset_before $id [ipiasm::_msg_count $id]
    }
    if {[catch {validate_bd_design -force} e]} {
        # stash the hard-error text so assembly_report can scan it for DRC lines
        # even when the caller did not capture/forward the console explicitly.
        set last_validate_console $e
        return "VALIDATE:FAIL:$e"
    }
    foreach {id reason} [ipiasm::clock_reset_msg_ids] {
        set count [expr {[ipiasm::_msg_count $id] - [dict get $reset_before $id]}]
        if {$count > 0} {
            lappend last_reset_sync_findings "\[$id\] x$count -- $reason"
        }
    }
    if {[llength $last_reset_sync_findings]} {
        set last_validate_console \
            "CRITICAL WARNING: [join $last_reset_sync_findings {; }]\n"
        return "VALIDATE:FAIL:CLOCK_RESET_SYNC\
[join $last_reset_sync_findings {; }]"
    }
    set clock_domains [ipiasm::check_interface_clock_domains]
    if {[string match "CLOCK_DOMAIN_FAIL:*" $clock_domains]} {
        set last_validate_console "CRITICAL WARNING: $clock_domains\n"
        return "VALIDATE:FAIL:$clock_domains"
    }
    set video_clock [ipiasm::check_mipi_video_clocks]
    if {[string match "VIDEOCLK_FAIL:*" $video_clock]} {
        set last_validate_console "CRITICAL WARNING: $video_clock\n"
        return "VALIDATE:FAIL:$video_clock"
    }
    # rc=0 is Vivado's verdict and it is reported as such. But a refused NoC
    # route is reported as a message, not a return code, so put what the
    # counters hold where the reporters already look -- a PASS must not be the
    # last word on a design something has already called illegal. (This plain
    # validate usually does not re-run the NoC compiler; ipiasm::check_noc_legality
    # is what forces it to speak.)
    foreach f [ipiasm::_illegal_noc_findings] {
        append last_validate_console "CRITICAL WARNING: refused NoC route -- $f\n"
    }
    set ::ipiasm::design_validated 1
    return "VALIDATE:PASS"
}

# One verdict bundle per repair iteration. This prevents a validate-only loop
# from fixing the next QoS/memory/AIE/clock defect one expensive pass at a time.
# required_irqs is the number of PL interrupts ps_cell must accept; it is NA in a
# design without a processing system.
proc ipiasm::validate_bundle {{want_mc 0} {ps_cell ""} {required_irqs 0} {force_noc 1} {overmap_limit ""}} {
    set lines {}
    set failed {}
    set inv [ipiasm::_need inventory "validate_bundle"]
    if {$inv ne ""} { return "BUNDLE_FAIL:$inv" }

    set checks [list \
        [ipiasm::validate] \
        [ipiasm::check_qos] \
        [ipiasm::check_memory_subsystem $want_mc] \
        [ipiasm::check_noc_post_address] \
        [ipiasm::check_aie_config] \
        [ipiasm::check_overmap $overmap_limit] \
        [ipiasm::check_noc_legality $force_noc]]
    foreach result $checks {
        lappend lines $result
        if {![regexp {^(VALIDATE:PASS|QOS:(OK|NA)|MEM:OK|NOC_POST:OK|AIE:(OK|NA)|OVERMAP:(OK|NA)|NOCLEGAL:(OK|NA))} $result]} {
            lappend failed [lindex [split $result \n] 0]
        }
    }

    set sysclk_seen 0
    foreach noc [ipiasm::_all_noc_cells] {
        if {![llength [get_bd_pins -quiet "$noc/sys_clk*"]]} { continue }
        set sysclk_seen 1
        set result [ipiasm::check_mem_noc_sysclk $noc]
        lappend lines $result
        if {![string match "MEM_SYSCLK:OK*" $result]
            && ![string match "MEM_SYSCLK:NA*" $result]} {
            lappend failed $result
        }
    }
    if {!$sysclk_seen} { lappend lines "MEM_SYSCLK:NA no memory NoC sys_clk pins" }

    if {$required_irqs > 0} {
        # PL-to-PS interrupt capacity is a processing-system limit. A soft-processor design
        # routes its interrupts to an interrupt controller (check_irq_paths covers those),
        # and agents passing the spec's interrupt count here failed on a capacity of 0.
        if {$ps_cell eq "" && ![llength [ipiasm::_ps_cells]]} {
            set result "IRQ_CAPACITY:NA no processing system in this design; $required_irqs interrupt(s)\
                go to a soft processor's interrupt controller (check_irq_paths)"
        } else {
            set result [ipiasm::check_ps_fabric_irqs $ps_cell $required_irqs]
        }
        lappend lines $result
        if {![string match "IRQ_CAPACITY:OK*" $result] && ![string match "IRQ_CAPACITY:NA*" $result]} {
            lappend failed $result
        }
    }
    if {[llength $failed]} {
        return "BUNDLE_FAIL:[llength $failed] check(s)\n  [join $lines "\n  "]"
    }
    return "BUNDLE:OK\n  [join $lines "\n  "]"
}

# --- gap-closure orchestration loop: validate -> autofix -> re-validate ---
# Drives the confident fixers toward a clean design WITHOUT guessing: each pass
# runs ipiasm::autofix (clk/rst domains, explicit/auto address, OOS externalize),
# then re-validates. It STOPS early when validate passes, when a pass makes no new
# confident fix (only escalations remain), or after `max` passes. The remaining
# ESCALATE questions are returned for the agent to put to the user via
# AskQuestion -- the loop never invents a design decision to force a pass.
#   domain_map / windows / oos_pins: same payloads as ipiasm::autofix.
# Returns a multi-line transcript ending in either CLOSED or NEEDS_USER.
proc ipiasm::close_gaps {domain_map {windows {}} {oos_pins {}} {max 3}} {
    set log {}
    set prev_fixed -1
    for {set pass 1} {$pass <= $max} {incr pass} {
        set v [ipiasm::validate]
        lappend log "pass $pass: $v"
        if {[string match "VALIDATE:PASS" $v]} {
            return "CLOSED after [expr {$pass-1}] fix-pass(es)\n  [join $log "\n  "]"
        }
        set fx [ipiasm::autofix $domain_map $windows $oos_pins]
        regexp {fixed=(\d+) escalate=(\d+)} $fx -> nf ne
        if {![info exists nf]} { set nf 0; set ne 0 }
        lappend log "pass $pass autofix: fixed=$nf escalate=$ne"
        # no NEW confident progress and questions remain -> stop, hand to user
        if {$nf <= $prev_fixed && $ne > 0} {
            return "NEEDS_USER (no further confident fix; $ne open question(s))\n  [join $log "\n  "]\n$fx"
        }
        set prev_fixed $nf
    }
    set v [ipiasm::validate]
    lappend log "final: $v"
    if {[string match "VALIDATE:PASS" $v]} {
        return "CLOSED at pass limit\n  [join $log "\n  "]"
    }
    return "NEEDS_USER (pass limit $max reached)\n  [join $log "\n  "]\n[ipiasm::autofix $domain_map $windows $oos_pins]"
}

# --- does each camera's pixel format match the ISP tile it feeds? ---
# Vivado connects a CSI-2 receiver to an ISP tile input whatever the two are
# configured for, and validates clean afterwards: the receiver's pixel format
# and the tile's input data format are separate parameters on separate IP, and
# nothing cross-checks them. Bit depth is the property the two have in common,
# so compare the configured receiver format with the tile's numeric input width.
proc ipiasm::_raw_bits {fmt} {
    if {[regexp -nocase {^raw([0-9]+)$} [string trim $fmt] -> bits]} {
        return $bits
    }
    return ""
}

# The tile's input-format property, found by pattern rather than by a fixed
# name, so a device with more than one ISP per tile still resolves.
proc ipiasm::_tile_format_prop {isp tile} {
    return [lindex [ipiasm::_tile_format_props $isp $tile] 0]
}
# Every ISP instance's input data-format property on a tile.
proc ipiasm::_tile_format_props {isp tile} {
    set props {}
    catch {set props [list_property -quiet $isp]}
    return [lsearch -all -inline $props "CONFIG.C_TILE${tile}_ISP*_BASIC_DATA_FORMAT"]
}

# Every camera-to-tile link in the design, as {receiver isp tile} triples. Both
# audits below compare a receiver parameter against the tile it actually feeds,
# so the trace that pairs them belongs in one place.
proc ipiasm::_camera_tile_links {} {
    set links {}
    foreach rx [get_bd_cells -hier -quiet \
                  -filter {VLNV =~ *:mipi_csi2_rx_subsystem:*}] {
        foreach pin [get_bd_intf_pins -quiet -of_objects $rx] {
            if {[get_property -quiet MODE $pin] ne "Master"} { continue }
            set net [get_bd_intf_nets -quiet -of_objects $pin]
            if {$net eq ""} { continue }
            foreach far [get_bd_intf_pins -quiet -of_objects $net] {
                if {"$far" eq "$pin"} { continue }
                set isp [get_bd_cells -quiet [file dirname "$far"]]
                if {$isp eq ""} { continue }
                if {![string match *:visp_ss:* \
                       [get_property -quiet VLNV $isp]]} { continue }
                if {![regexp -nocase {TILE([0-9]+)} [file tail "$far"] \
                        -> tile]} { continue }
                set stream 0
                regexp -nocase {VIDIN([0-9]+)} [file tail "$far"] -> stream
                lappend links [list $rx $isp $tile $stream]
            }
        }
    }
    return $links
}

# Check the camera receiver's stream format matches the ISP tile input it feeds (or will feed before wiring).
proc ipiasm::check_camera_isp_format {} {
    set bad {}
    set checked 0
    set links [ipiasm::_camera_tile_links]
    set planned ""
    if {![llength $links]} {
        # Before wiring: a receiver with the ISP bridge enabled feeds tile 0 (TILE0_ISP_MIPI_VIDIN0).
        foreach rx [get_bd_cells -hier -quiet -filter {VLNV =~ *:mipi_csi2_rx_subsystem:*}] {
            if {![string is true -strict [get_property -quiet CONFIG.C_SPRT_ISP_BRIDGE $rx]]} { continue }
            foreach isp [get_bd_cells -hier -quiet -filter {VLNV =~ *:visp_ss:*}] {
                lappend links [list $rx $isp 0 0]
            }
        }
        if {[llength $links]} { set planned " (planned ISP-bridge link, not yet wired)" }
    }
    foreach link $links {
        lassign $link rx isp tile stream
        set bits [ipiasm::_raw_bits \
                    [get_property -quiet CONFIG.CMN_PXL_FORMAT $rx]]
        if {$bits eq ""} { continue }
        # A tile's input streams go to ISP0 first, up to its live-input count, and
        # the rest to ISP1 (PG432, System Overview). Only the instance that owns
        # this camera's stream must take its format; the other may differ.
        set live0 [get_property -quiet CONFIG.C_TILE${tile}_ISP0_LIVE_INPUTS $isp]
        if {![string is integer -strict $live0] || $live0 < 1} { set live0 1 }
        set instance [expr {$stream < $live0 ? 0 : 1}]
        foreach prop [lsearch -all -inline [ipiasm::_tile_format_props $isp $tile] \
                          "CONFIG.C_TILE${tile}_ISP${instance}_*"] {
            set have [get_property -quiet $prop $isp]
            incr checked
            if {![string is integer -strict $have] || $have != $bits} {
                lappend bad "[get_property NAME $rx] captures\
RAW${bits} into [get_property NAME $isp] tile $tile, which is set to\
'$have' ($prop)"
            }
        }
    }
    if {$checked == 0} { return "FORMAT:none no camera feeds an ISP tile" }
    if {[llength $bad] > 0} {
        return "FORMAT_FAIL:[llength $bad]/$checked camera/ISP format\
mismatch$planned\n  [join $bad "\n  "]\n  -> set the tile's input data format to the\
bit depth the camera captures; Vivado will not object to either value on its\
own."
    }
    return "FORMAT:OK $checked/$checked$planned"
}

# The tile's input-width property, by pattern, for the same reason as the
# format one.
proc ipiasm::_tile_res_hor_prop {isp tile} {
    set props {}
    catch {set props [list_property -quiet $isp]}
    foreach p $props {
        if {[string match "CONFIG.C_TILE${tile}_ISP*_IBA*_RES_HOR" $p]} {
            return $p
        }
    }
    return ""
}

# --- is each camera's line buffer deep enough for the line it carries? ---
# CSI_BUF_DEPTH holds one line, so it has to reach the tile's horizontal
# resolution; short of that the receiver overflows mid-line and the design
# still validates. It is easy to miss because the depth usually arrives with a
# board preset rather than by decision. Compare the live buffer depth with the
# configured horizontal resolution for every connected receiver.
proc ipiasm::check_camera_line_buffer {} {
    set bad {}
    set checked 0
    foreach link [ipiasm::_camera_tile_links] {
        lassign $link rx isp tile
        set depth [get_property -quiet CONFIG.CSI_BUF_DEPTH $rx]
        if {![string is integer -strict $depth]} { continue }
        set prop [ipiasm::_tile_res_hor_prop $isp $tile]
        if {$prop eq ""} { continue }
        set hor [get_property -quiet $prop $isp]
        if {![string is integer -strict $hor]} { continue }
        incr checked
        if {$depth < $hor} {
            lappend bad "[get_property NAME $rx] buffers $depth pixels but\
feeds [get_property NAME $isp] tile $tile at $hor ($prop)"
        }
    }
    if {$checked == 0} { return "LINEBUF:none no camera feeds an ISP tile" }
    if {[llength $bad] > 0} {
        return "LINEBUF_FAIL:[llength $bad]/$checked line buffer(s) shorter\
than the line\n  [join $bad "\n  "]\n  -> raise CSI_BUF_DEPTH to the smallest\
legal value at or above the tile's RES_HOR. A receiver whose board interface\
carries no operating point keeps the catalog default, so this is the one a\
board preset will not fix for you."
    }
    return "LINEBUF:OK $checked/$checked"
}

proc ipiasm::_tile_res_ver_prop {isp tile} {
    set props {}
    catch {set props [list_property -quiet $isp]}
    foreach p $props {
        if {[string match "CONFIG.C_TILE${tile}_ISP*_IBA*_RES_VER" $p]} {
            return $p
        }
    }
    return ""
}

# --- the frame geometry the board says its cameras deliver ---
# A camera board interface that carries an operating point states it as
# PARAM.max_res_hor / PARAM.max_res_ver, beside the pixel format and line rate
# the preset applies. Returns {name hor ver} per interface that states one.
proc ipiasm::_board_camera_geometry {} {
    set out {}
    foreach i [get_board_part_interfaces -quiet] {
        set hor [get_property -quiet PARAM.max_res_hor $i]
        set ver [get_property -quiet PARAM.max_res_ver $i]
        if {![string is integer -strict $hor]} { continue }
        if {![string is integer -strict $ver]} { continue }
        lappend out [list [get_property -quiet NAME $i] $hor $ver]
    }
    return $out
}

# --- internal: which board interface a receiver was bound to, if any ---
proc ipiasm::_receiver_board_intf {rx} {
    set props {}
    catch {set props [list_property -quiet $rx]}
    foreach p $props {
        if {![string match "CONFIG.*_BOARD_INTERFACE" $p]} { continue }
        set v [get_property -quiet $p $rx]
        if {$v eq "" || [string equal -nocase $v Custom]} { continue }
        return $v
    }
    return ""
}

# --- is each ISP tile set up for the frame its camera delivers? ---
# requirements is {receiver width height ...} from the DesignSpec. A receiver
# explicitly bound to a board interface may use that interface's geometry when
# no spec entry overrides it. Unbound receivers never inherit another
# interface's preset.
proc ipiasm::check_camera_geometry {{requirements {}}} {
    if {[llength $requirements] % 3} {
        return "GEOMETRY_FAIL:requirements must be {receiver width height ...}"
    }
    set geo [ipiasm::_board_camera_geometry]
    set bad {}
    set unresolved {}
    set checked 0
    foreach link [ipiasm::_camera_tile_links] {
        lassign $link rx isp tile
        set bound [ipiasm::_receiver_board_intf $rx]
        set want ""
        set src ""
        foreach {receiver hor ver} $requirements {
            if {"$receiver" eq "$rx"
                || [file tail "$receiver"] eq [file tail "$rx"]} {
                set want [list $hor $ver]
                set src "DesignSpec"
                break
            }
        }
        foreach g $geo {
            lassign $g name hor ver
            if {$want eq "" && $bound ne "" && $name eq $bound} {
                set want [list $hor $ver]
                set src "board interface $name"
            }
        }
        if {$want eq ""} {
            lappend unresolved "$rx has no DesignSpec geometry and no bound board geometry"
            continue
        }
        set hprop [ipiasm::_tile_res_hor_prop $isp $tile]
        set vprop [ipiasm::_tile_res_ver_prop $isp $tile]
        if {$hprop eq "" || $vprop eq ""} { continue }
        set hor [get_property -quiet $hprop $isp]
        set ver [get_property -quiet $vprop $isp]
        if {![string is integer -strict $hor]} { continue }
        if {![string is integer -strict $ver]} { continue }
        incr checked
        lassign $want whor wver
        if {$hor < $whor || $ver < $wver} {
            lappend bad "[get_property NAME $isp] tile $tile takes\
${hor}x${ver} from [get_property NAME $rx], which delivers ${whor}x${wver}\
($src)"
        }
    }
    if {[llength $bad] > 0} {
        return "GEOMETRY_FAIL:[llength $bad]/$checked tile(s) smaller than the\
frame the camera sends\n  [join $bad "\n  "]\n  -> size the tile input from\
the DesignSpec or its bound board-interface geometry."
    }
    # A frame size the requirements leave to camera firmware is not invented here:
    # with no stated geometry there is nothing to compare, which is not a defect.
    set note [expr {[llength $unresolved] ? " unchecked={[join $unresolved {; }]}" : ""}]
    if {$checked == 0 && [llength $unresolved]} {
        return "GEOMETRY:NA no DesignSpec or bound board geometry to compare$note"
    }
    if {$checked == 0} { return "GEOMETRY:none no camera feeds an ISP tile" }
    return "GEOMETRY:OK $checked/$checked$note"
}

# --- can software still retune the cameras once the design is running? ---
# Three switches make lanes, virtual channels and datatypes settable at runtime:
# the active-lane control, the datatype filter, and the PHY register interface
# that reaches them. All three are off by default and none of them arrives with
# a board preset, so a design that captures perfectly can still have no way to
# retune -- and the register interface is the one that gets left behind, because
# its name reads D-PHY (DPY_) while a C-PHY receiver needs it just as much and
# takes it just as well. Advisory: a fixed-function camera legitimately needs
# none of this, so this reports what the design can do rather than failing it.
proc ipiasm::camera_retune_note {} {
    set switches {C_CSI_EN_ACTIVELANES C_CSI_FILTER_USERDATATYPE DPY_EN_REG_IF}
    set total 0
    set retunable 0
    set short {}
    foreach rx [get_bd_cells -hier -quiet \
                  -filter {VLNV =~ *:mipi_csi2_rx_subsystem:*}] {
        incr total
        set missing {}
        foreach s $switches {
            if {![string is true -strict [get_property -quiet CONFIG.$s $rx]]} {
                lappend missing $s
            }
        }
        if {[llength $missing] == 0} {
            incr retunable
        } else {
            lappend short "[get_property NAME $rx] is missing [join $missing {, }]"
        }
    }
    if {$total == 0} { return "RETUNE:none no camera receiver in the design" }
    if {[llength $short] == 0} {
        return "RETUNE:OK $retunable/$total receivers expose the software\
retune path"
    }
    return "RETUNE:$retunable/$total receivers expose the software retune\
path\n  [join $short "\n  "]\n  NOTE: without all three, lanes, virtual\
channels and datatypes are fixed at\n        build time. Set them on every\
receiver a spec expects software to retune;\n        DPY_EN_REG_IF applies in\
C-PHY mode too."
}

# --- internal: connected interfaces of one bus definition, anywhere ---
# An interface nothing drives controls nothing, so an unwired one does not count.
proc ipiasm::_connected_intf_count {busdef} {
    set n 0
    foreach pin [get_bd_intf_pins -hier -quiet \
                   -of_objects [get_bd_cells -hier -quiet]] {
        if {![string match -nocase *:$busdef* [get_property -quiet VLNV $pin]]} {
            continue
        }
        if {[get_bd_intf_nets -quiet -of_objects $pin] eq ""} { continue }
        incr n
    }
    return $n
}

# --- is there a sensor-control channel per camera? ---
# "Per-tile IIC and GPIO for sensor control" means one channel of each kind per
# camera, and a single shared channel is not per-tile however wide it is. Counted
# by bus definition, so a hardened ISP's native per-tile ports count for exactly
# as much as discrete axi_iic/axi_gpio cells. Advisory: a design whose sensors
# need no control from this device legitimately has none.
proc ipiasm::camera_control_note {} {
    set cams [llength [get_bd_cells -hier -quiet \
                         -filter {VLNV =~ *:mipi_csi2_rx_subsystem:*}]]
    if {$cams == 0} { return "CAMCTRL:none no camera receiver in the design" }
    set iic [ipiasm::_connected_intf_count iic]
    set gpio [ipiasm::_connected_intf_count gpio]
    set offered {}
    foreach i [get_board_part_interfaces -quiet] {
        set nm [get_property -quiet NAME $i]
        if {[string match -nocase *_PL_IIC $nm] \
                || [string match -nocase *_PL_GPIO $nm]} {
            lappend offered $nm
        }
    }
    set head "CAMCTRL:$iic IIC and $gpio GPIO control channel(s) for $cams camera(s)"
    if {$iic >= $cams && $gpio >= $cams} { return "$head -- one of each per camera" }
    set out $head
    append out "\n  NOTE: a shared channel is not per-camera control, however wide\
it is.\n        Count what the design connects, not the cells it built."
    if {[llength $offered] > 0} {
        append out "\n        This board exposes one per tile: [join $offered {, }]."
    }
    return $out
}

# Check no ISP builds a PL I2C or GPIO core that nothing uses.
# IIC/GPIO Select = PL instantiates that core inside the ISP and brings its pins out
# as the instance's TILE<t>_ISP<i>_PL_IIC / _PL_GPIO interface (PG432, Resources tab).
# PL is the IP default, so a design that drives its sensor-control pins from discrete
# axi_iic/axi_gpio cells still carries the ISP's own cores, wired to nothing. PG432's
# Disable choice is the configuration that says the instance has none. Run after the
# connect stage: before it, every interface is unwired.
proc ipiasm::check_isp_resources {} {
    # PG432 User Parameters: IIC_SELECT PS I3C I2C0-7 = 0-7, PL = 8, Disable = 9;
    # GPIO_SELECT PSX_GPIO0/1 = 0/1, PL = 2, Disable = 3.
    set codes {IIC {pl 8 off 9} GPIO {pl 2 off 3}}
    set isps [get_bd_cells -hier -quiet -filter {VLNV =~ *:visp_ss:*}]
    if {![llength $isps]} { return "ISP_RESOURCES:NA no ISP in the design" }
    set used 0
    set unused {}
    set fixes {}
    foreach isp $isps {
        set off {}
        foreach prop [lsort [list_property $isp]] {
            if {![regexp {^CONFIG\.C_(TILE[0-9]+_ISP[0-9]+)_(IIC|GPIO)_SELECT$} $prop -> inst kind]} {
                continue
            }
            if {[get_property -quiet $prop $isp] ne [dict get $codes $kind pl]} { continue }
            # Vivado creates the interface only when it builds the core; no pin, no core.
            set pin [get_bd_intf_pins -quiet $isp/${inst}_PL_$kind]
            if {$pin eq ""} { continue }
            if {[get_bd_intf_nets -quiet -of_objects $pin] ne ""} {
                incr used
                continue
            }
            lappend unused "$isp $prop=[dict get $codes $kind pl] (PL) but ${inst}_PL_$kind\
reaches nothing"
            lappend off $prop [dict get $codes $kind off]
        }
        if {[llength $off]} { lappend fixes "ipcfg::apply_dict $isp [list $off]" }
    }
    if {![llength $unused]} { return "ISP_RESOURCES:OK $used PL I2C/GPIO core(s) in use" }
    return "ISP_RESOURCES_FAIL:[llength $unused] ISP PL I2C/GPIO core(s) built but\
unused\n  [join $unused "\n  "]\n  -> set Disable (PG432, Resources tab):\n     [join $fixes "\n     "]\n\
     or connect the interface if the design means to use the ISP's own core."
}

# --- did addressing hand the PL an aperture onto the whole processing system? ---
# assign_bd_address picks offsets; it does not decide visibility. It maps every
# slave into every master that can reach it, and on a shared fabric that means
# each PL master comes out addressing the PS's ~180 internal blocks -- OCM, the
# SDMA channels, the IPI mailboxes, every UART and XMPU.
#
# A design with no visibility policy can map hundreds of processing-system
# windows into PL masters, and validate_bd_design may then fail with "[BD 5-702] Failed to
# route address 0x800_000F_0000 ... overlaps a previously routed address", which
# names a SmartConnect and an offset and says nothing about the cause. This is
# here to name the cause, so run it after auto_address and before validate.
#
# Segment names are matched rather than targets because IP Integrator does not
# expose a mapped segment's target address block as a property, and the name it
# generates for a window onto another cell is SEG_<that cell>_<block>. Deriving
# the pattern from the PS cell's own name beats guessing at family prefixes: the
# segment names include the live processing-system cell name, so derive the
# pattern from that cell rather than assuming a family prefix.
proc ipiasm::check_overmap {{max_windows ""}} {
    set ps [ipiasm::_ps_cells]
    if {![llength $ps]} { return "OVERMAP:NA no processing system in this design" }
    set ps_spaces {}
    set prefixes {}
    foreach c $ps {
        # Address spaces belonging to the PS itself. The PS placing its own
        # blocks in its own masters is architectural, not this design's doing.
        foreach s [get_bd_addr_spaces -quiet -of_objects $c] { lappend ps_spaces $s }
        lappend prefixes "SEG_[get_property -quiet NAME $c]_*"
    }
    set worst {}
    set total 0
    foreach space [get_bd_addr_spaces -quiet] {
        if {[lsearch -exact $ps_spaces $space] >= 0} { continue }
        set n 0
        foreach seg [get_bd_addr_segs -quiet $space/*] {
            set name [get_property -quiet NAME $seg]
            foreach p $prefixes {
                if {[string match -nocase $p $name]} { incr n ; break }
            }
        }
        if {$n > 0} {
            incr total $n
            lappend worst [list $n $space]
        }
    }
    set masters [llength $worst]
    if {![string is integer -strict $max_windows] || $max_windows < 0} {
        return "OVERMAP_FAIL:DesignSpec visibility limit is required; observed\
$total processing-system window(s) from $masters PL master(s). Pass the\
maximum authorized count after applying the spec's allow-lists."
    }
    if {$total <= $max_windows} {
        return "OVERMAP:OK $total window(s) from $masters PL master(s)\
into the processing system"
    }
    set worst [lrange [lsort -integer -index 0 -decreasing $worst] 0 4]
    set detail {}
    foreach w $worst { lappend detail "[lindex $w 1] holds [lindex $w 0]" }
    return "OVERMAP_FAIL:$total window(s) from $masters PL master(s)\
onto processing-system internals (DesignSpec limit=$max_windows). Worst:\
[join $detail {; }].\n  -> addressing chose\
offsets but nothing decided visibility. Say what each of those masters is FOR\
with ipiasm::restrict_master <cell> {<keep-globs>} and re-check. Leaving it is\
not cosmetic: it is what makes validate_bd_design fail with a SmartConnect\
route conflict on an offset that looks fine in every master's own map."
}

# --- internal: the processing-system cells in the design ---
proc ipiasm::_ps_cells {} {
    set pats {*:ps_wizard:* *:versal_cips:* *:zynq_ultra_ps_e:* *:processing_system7:*}
    set out {}
    foreach c [get_bd_cells -hier -quiet] {
        set vlnv [get_property -quiet VLNV $c]
        foreach p $pats {
            if {[string match $p $vlnv]} { lappend out $c ; break }
        }
    }
    return $out
}

# --- internal: vendor:library:name, without the version ---
proc ipiasm::_vlnv_ip {vlnv} { return [join [lrange [split $vlnv :] 0 2] :] }

# --- internal: board interfaces whose preferred IP is this cell ---
# PREFERENCES is the board file naming the cell it expects on an interface, so
# it answers "does this board have a preset for this cell?" without the gate
# knowing one IP-specific name.
proc ipiasm::_board_hooks_for {cell} {
    set vlnv [ipiasm::_vlnv_ip [get_property -quiet VLNV $cell]]
    set out {}
    foreach i [get_board_part_interfaces -quiet] {
        foreach pref [get_property -quiet PREFERENCES $i] {
            if {$pref eq ""} { continue }
            if {[ipiasm::_vlnv_ip $pref] ne $vlnv && ![string match $pref $vlnv]} {
                continue
            }
            lappend out [get_property -quiet NAME $i]
            break
        }
    }
    return $out
}

# --- internal: the board-interface hooks on a cell, bound and unbound ---
proc ipiasm::_cell_hook_props {cell} {
    set bound {}
    set open {}
    set props {}
    catch {set props [list_property -quiet $cell]}
    foreach p $props {
        if {![string match "CONFIG.*BOARD_INTERFACE" $p]} { continue }
        set v [get_property -quiet $p $cell]
        if {$v eq "" || [string equal -nocase $v Custom]} {
            lappend open $p
        } else {
            lappend bound [list $p $v]
        }
    }
    return [list $bound $open]
}

# --- was the design bound to the board it is being built for? ---
# `ipcfg::audit_board` holds a run to the preset it bound. This answers the
# question before that one: did it bind at all? The processing system is where
# the answer decides whether the design is buildable, because its preset is not
# a convenience -- it is the board's MIO pinout, its DDR, and which protocol each
# GT quad carries. Binding a board-owned fixed-IO interface can write many
# processing-system values including the protocol on a GT quad; a hand-configured PS
# has none of them and validates perfectly, so a run assembled a whole display
# path for a device with no DisplayPort to send it to. Every other cell is
# reported rather than failed -- a design may have a good reason to drive a
# board-backed IP its own way -- but the PS is a defect.
proc ipiasm::check_board_hooks {} {
    set bp ""
    catch {set bp [get_property -quiet BOARD_PART [current_project]]}
    if {$bp eq ""} {
        return "HOOKS:none no BOARD_PART is set (ipiasm::require_board_preset)"
    }
    set bound_n 0
    set offered_n 0
    set unbound {}
    set ps_bad {}
    set ps_ok {}
    set ps_cells [ipiasm::_ps_cells]
    foreach c [get_bd_cells -hier -quiet] {
        lassign [ipiasm::_cell_hook_props $c] bound open
        if {[llength $bound] == 0 && [llength $open] == 0} { continue }
        set offers [ipiasm::_board_hooks_for $c]
        set is_ps [expr {[lsearch -exact $ps_cells $c] >= 0}]
        set nm [get_property -quiet NAME $c]
        if {[llength $bound] > 0} {
            incr bound_n
            if {$is_ps} {
                foreach b $bound { lappend ps_ok "$nm <- [lindex $b 1]" }
            }
            continue
        }
        if {[llength $offers] == 0} { continue }
        incr offered_n
        # Some processing systems take the board preset without recording a hook (Zynq
        # UltraScale+ leaves every *_BOARD_INTERFACE "custom"); values that match the
        # board's preset are the binding.
        set drift ""
        if {$is_ps && [llength [info commands ::ipcfg::preset_drift]]} {set drift [ipcfg::preset_drift $c]}
        if {[string match PRESET:OK* $drift]} {
            incr bound_n
            lappend ps_ok "$nm <- board preset ([lindex [regexp -inline {presets=(\S+)} $drift] 1])"
            continue
        }
        # Vivado's own preset automation leaves a few values off the preset file (measured on
        # a clean KV260 SOM PS: 6 of its preset's values). A hand-configured PS differs in its
        # DDR and MIO setup, far more than 5% of the preset.
        if {[string match PRESET_DRIFT:* $drift] && [info exists ::ipcfg::last_preset_drift]} {
            set record $::ipcfg::last_preset_drift
            set changed [llength [dict get $record drift]]
            set checked [dict get $record checked]
            if {$checked > 0 && $changed * 20 <= $checked} {
                incr bound_n
                lappend ps_ok "$nm <- board preset ([join [dict get $record presets] ,]; $changed of $checked values\
differ, as the board's preset automation leaves them: [join [lmap e [dict get $record drift] {dict get $e key}] { }])"
                continue
            }
        }
        if {$is_ps} {
            lappend ps_bad "$nm ([join $open {, }]) while the board offers\
[join $offers {, }][expr {$drift eq "" ? "" : " ($drift)"}]"
        } else {
            lappend unbound "$nm offers [join $offers {, }]"
        }
    }
    set tail ""
    if {[llength $unbound] > 0} {
        set tail "\n  NOTE: configured by hand while this board carries a preset\
for them:\n        [join $unbound "\n        "]"
    }
    if {[llength $ps_bad] > 0} {
        return "HOOKS_FAIL:the processing system is configured by hand on a board\
design\n  [join $ps_bad "\n  "]\n  -> bind it: ipcfg::board_bind <ps>\
{CONFIG.PS_BOARD_INTERFACE <hook>} {} \"board IO\"\n     The preset is the\
board's MIO pinout, its DDR and which protocol each GT quad\n     carries. A\
hand-configured PS keeps none of that and still validates, so a\n     display\
path can end up aimed at a DisplayPort this design never enabled.${tail}"
    }
    if {$bound_n == 0} {
        return "HOOKS:none nothing in this design has a board hook${tail}"
    }
    set head "HOOKS:OK $bound_n cell(s) bound to $bp"
    if {[llength $ps_ok] > 0} { append head " ([join $ps_ok {; }])" }
    return "$head${tail}"
}

# --- what the display path is, and where it can go ---
# Disclosure, like the memory gate's window report: a compositor has ONE video
# output, so a spec asking for two independent screens needs two of them, and a
# run that built one mixer and fanned its single output out reads as complete
# until someone counts. The GT protocol is here too because it is the other half
# of the same question -- there is no point compositing for a DisplayPort the PS
# does not carry.
proc ipiasm::display_note {} {
    set mixers [get_bd_cells -hier -quiet -filter {VLNV =~ *:v_mix:*}]
    set gt [ipiasm::_ps_gt_quad]
    if {[llength $mixers] == 0 && [llength $gt] == 0} {
        return "DISPLAY:none no compositor and no processing system"
    }
    set rows {}
    foreach m $mixers {
        set bits "[get_property -quiet NAME $m]"
        set cols [get_property -quiet CONFIG.MAX_COLS $m]
        set rws [get_property -quiet CONFIG.MAX_ROWS $m]
        if {[string is integer -strict $cols] && [string is integer -strict $rws]} {
            append bits " ${cols}x${rws}"
        }
        set layers [get_property -quiet CONFIG.NR_LAYERS $m]
        if {$layers ne ""} { append bits " layers=$layers" }
        if {[string is true -strict [get_property -quiet CONFIG.LOGO_LAYER $m]]} {
            append bits " logo=on"
        }
        lappend rows $bits
    }
    set dp "off"
    set why "no processing system states a GT protocol"
    if {[llength $gt] > 0} {
        lassign $gt ps prop val
        set why "$ps $prop UDH_GT=$val"
        if {[string match -nocase *DP* $val]} { set dp "on" }
    } elseif {[llength [ipiasm::_ps_cells]] > 0} {
        set why "the PS states no GT protocol -- it was never bound to the board"
    }
    set out "DISPLAY:[llength $mixers] compositor(s), one video output each;\
DisplayPort $dp ($why)"
    if {[llength $rows] > 0} { append out "\n  [join $rows "\n  "]" }
    if {$dp eq "off" && [llength $mixers] > 0} {
        append out "\n  NOTE: this design composites for a device with no\
DisplayPort. UDH_GT carries\n        the GT quad's protocol; a board preset that\
sets DP_X2_+_USB loses the DP the\n        moment anything writes USB over it."
    }
    return $out
}

# --- internal: the GT quad protocol a processing system carries ---
# UDH_GT appears in more than one PS dict and the later write is what takes, so
# read them in that order and report which dict answered.
proc ipiasm::_ps_gt_quad {} {
    foreach ps [ipiasm::_ps_cells] {
        foreach prop {CONFIG.MMI_CONFIG CONFIG.MMI_CONFIG_INTERNAL CONFIG.PS11_CONFIG} {
            set d [get_property -quiet $prop $ps]
            if {$d eq ""} { continue }
            set has 0
            catch {set has [dict exists $d UDH_GT]}
            if {!$has} { continue }
            return [list [get_property -quiet NAME $ps] $prop [dict get $d UDH_GT]]
        }
    }
    return {}
}

# Record pins the design leaves open on purpose, so assembly_report lists them as
# declared instead of as wiring gaps. pins: cell interface pins or input clk/rst
# pins. reason: why the design does not use them, citing the DesignSpec (a tile
# with no source, an interface the spec says is unconnected). A declaration
# never hides a required connection: the final gate checks those separately.
# A declared pin that is wired now is refused, so a stale declaration cannot
# outlive the design it described.
proc ipiasm::declare_open {pins reason} {
    variable declared_open
    if {[string trim $reason] eq ""} { return "OPEN_FAIL:give the reason each pin is left open" }
    set bad {}
    set added {}
    foreach pin $pins {
        set path "/[string trimleft $pin /]"
        set intf [get_bd_intf_pins -quiet $path]
        set scalar [get_bd_pins -quiet $path]
        if {$intf ne ""} {
            if {[ipiasm::_intf_wired $intf] || [ipiasm::_intf_members_wired $intf]} {
                lappend bad "$path is wired"; continue
            }
        } elseif {$scalar ne ""} {
            if {[ipiasm::_net_wired $scalar] || [ipiasm::_scalar_in_wired_intf $scalar]} {
                lappend bad "$path is wired"; continue
            }
        } else {
            lappend bad "$path not found"; continue
        }
        dict set declared_open $path $reason
        lappend added $path
    }
    if {[llength $bad]} { return "OPEN_FAIL:[join $bad {; }]" }
    return "OPEN:OK declared=[llength $added] total=[dict size $declared_open]"
}

# --- assembly coverage report: what is NOT yet a working design ---
# Live-introspects the persistent BD for the gaps that matter for a buildable
# system, and folds in the validate result + any CRITICAL/DRC lines the agent
# scraped from the console (passed as text, like ipcfg::find_disabled).
# Discloses, never hides:
#   * unwired interface pins (intf pins with no net on the pin or any member;
#     pins ipiasm::declare_open recorded are listed as declared, not as gaps)
#   * unconnected clock pins / reset pins (TYPE clk|rst pins with no net)
#   * unmapped address segments
#   * DRC criticals scraped from validate output
#   validate_out: optional console text from validate_bd_design (for DRC lines)
#
# The findings are reported in TWO classes, because they are not the same kind of
# thing and a run that reads one number cannot tell them apart. A hardened block
# has ports a design legitimately never uses, so an unwired interface pin is a
# question; an undeclared traffic spec or a tile smaller than its sensor's frame
# is a defect. Keep those classes separate in the report.
# Returns a multi-line report headed "ASSEMBLY:CLEAN", "ASSEMBLY:GAPS" (wiring
# questions only) or "ASSEMBLY:BLOCKED" (at least one defect).
proc ipiasm::assembly_report {{validate_out ""}} {
    variable last_validate_console
    variable last_reset_sync_findings
    variable declared_open
    # fall back to the console captured by the most recent ipiasm::validate when
    # the caller did not forward it (so DRC scanning still works in loops).
    if {$validate_out eq ""} { set validate_out $last_validate_console }
    set unwired_intf {}
    set declared {}
    foreach pin [get_bd_intf_pins -quiet -of_objects [get_bd_cells -quiet]] {
        if {[ipiasm::_intf_wired $pin] || [ipiasm::_intf_members_wired $pin]} { continue }
        if {[dict exists $declared_open $pin]} { lappend declared $pin; continue }
        lappend unwired_intf $pin
    }
    # Only INPUT clk/rst pins matter: an unconnected clk/rst OUTPUT (e.g. a
    # proc_sys_reset's spare peripheral_reset/bus_struct_reset) is not a gap, it
    # is simply an unused driver. Flagging those would falsely fail the design.
    set unconn_clk {}
    set unconn_rst {}
    foreach pin [get_bd_pins -quiet -of_objects [get_bd_cells -quiet]] {
        if {[catch {set ty [get_property TYPE $pin]}]} { continue }
        if {[catch {set dir [get_property DIR $pin]}]} { set dir "I" }
        if {$dir ne "I"} { continue }
        if {$ty ne "clk" && $ty ne "rst"} { continue }
        if {[ipiasm::_net_wired $pin]} { continue }
        set owner [get_bd_cells -quiet [file dirname $pin]]
        set owner_vlnv [get_property -quiet VLNV $owner]
        set leaf [file tail $pin]
        if {[string match "*:proc_sys_reset:*" $owner_vlnv]
            && ($leaf eq "aux_reset_in" || $leaf eq "mb_debug_sys_rst")} {
            continue
        }
        # skip scalar halves of a differential/interface clock|reset whose parent
        # interface pin is already wired or externalized.
        if {[ipiasm::_scalar_in_wired_intf $pin]} { continue }
        if {[dict exists $declared_open $pin]} { lappend declared $pin; continue }
        if {$ty eq "clk"} { lappend unconn_clk $pin }
        if {$ty eq "rst"} { lappend unconn_rst $pin }
    }
    set unmapped [ipiasm::_unassigned_segs]
    set excluded [ipiasm::_excluded_segs]

    set drc {}
    foreach line [split $validate_out "\n"] {
        # The refused-route lines validate stashes are reported in their own
        # section below, with their advice attached; skip them here so a
        # finding is not counted as two gaps.
        if {[string match {*refused NoC route*} $line]} { continue }
        if {[string match {*CRITICAL WARNING*} $line] || [string match {*\[BD *\]*ERROR*} $line]} {
            lappend drc [string trim $line]
        }
    }
    # Asked for directly rather than scraped, so this still reports on a design
    # opened from disk and on a caller that forwarded its own console text.
    if {[catch {set illegal [ipiasm::_illegal_noc_findings]}]} { set illegal {} }
    # Structural, so it reports on a design nothing has validated yet -- which is
    # exactly when the ai_engine automation's loop is cheapest to undo.
    set aie ""
    if {[catch {set aie [ipiasm::check_aie_config]}]} { set aie "" }
    set aie_gap [expr {[string match "AIE_FAIL:*" $aie] ? 1 : 0}]

    # Configuration gaps, not wiring gaps -- and invisible to everything above,
    # because a cell left on its defaults wires up and validates exactly like a
    # configured one. Four ways a design ends up not being the design that was
    # asked for: a requirement that never reached the cell, a value hand-written
    # over the board file, a camera feeding an ISP tile set to a different pixel
    # depth, a camera whose line buffer is shorter than the line it carries, and
    # a tile smaller than the frame its sensor sends. Reported here so the same
    # close_gaps loop that fixes wiring fixes these, rather than relying on the
    # agent to remember to audit.
    # Design-level requirements audit: ipcfg::require pins what the spec asked
    # for, ipcfg::audit_requirements checks the design still carries it. Guarded
    # because a run that pinned nothing has no requirements to audit.
    set intent ""
    if {[catch {set intent [ipcfg::audit_requirements]}]} { set intent "" }
    set intent_gap [expr {[string match "REQ_FAIL:*" $intent] ? 1 : 0}]
    set board ""
    if {[catch {set board [ipcfg::audit_board]}]} { set board "" }
    set board_gap [expr {[string match "BOARD_FAIL:*" $board] ? 1 : 0}]
    set fmt ""
    if {[catch {set fmt [ipiasm::check_camera_isp_format]}]} { set fmt "" }
    set fmt_gap [expr {[string match "FORMAT_FAIL:*" $fmt] ? 1 : 0}]
    set linebuf ""
    if {[catch {set linebuf [ipiasm::check_camera_line_buffer]}]} {
        set linebuf ""
    }
    set linebuf_gap [expr {[string match "LINEBUF_FAIL:*" $linebuf] ? 1 : 0}]
    set geometry ""
    if {[catch {set geometry [ipiasm::check_camera_geometry]}]} {
        set geometry ""
    }
    set geometry_gap [expr {[string match "GEOMETRY_FAIL:*" $geometry] ? 1 : 0}]
    set isp_res ""
    if {[catch {set isp_res [ipiasm::check_isp_resources]}]} { set isp_res "" }
    set isp_res_gap [expr {[string match "ISP_RESOURCES_FAIL:*" $isp_res] ? 1 : 0}]
    # The traffic spec and the board hooks belong here for the same reason the
    # audits above do: a design ships on Vivado's 500/500 placeholder, or with a
    # hand-configured PS on a board that states its pinout, and both validate.
    # QoS is re-read rather than trusted from earlier in the build, because every
    # connection added after the plan was applied comes back at the placeholder --
    # which is how a design that reached `isochronous=3` at the halfway mark ended
    # with 14 undeclared connections.
    set qos ""
    if {[catch {set qos [ipiasm::check_qos]}]} { set qos "" }
    set qos_gap [expr {[string match "QOS_FAIL:*" $qos] ? 1 : 0}]
    set hooks ""
    if {[catch {set hooks [ipiasm::check_board_hooks]}]} { set hooks "" }
    set hooks_gap [expr {[string match "HOOKS_FAIL:*" $hooks] ? 1 : 0}]
    # Advisory, like the QoS isochronous note: a design may legitimately have no
    # camera it retunes, so this reports the capability rather than demanding it.
    set retune ""
    if {[catch {set retune [ipiasm::camera_retune_note]}]} { set retune "" }
    set camctrl ""
    if {[catch {set camctrl [ipiasm::camera_control_note]}]} { set camctrl "" }
    set display ""
    if {[catch {set display [ipiasm::display_note]}]} { set display "" }

    # Defects: each one is a design that does not do what it was built to do.
    set blocking [expr {[llength $unmapped] + [llength $drc] + \
                        [llength $illegal] + $aie_gap + $intent_gap + \
                        $board_gap + $fmt_gap + $linebuf_gap + $geometry_gap + \
                        $isp_res_gap + $qos_gap + $hooks_gap}]
    # Questions: a hardened block's spare ports are not a defect, but nothing
    # here can tell which of them the design meant to leave alone.
    set wiring [expr {[llength $unwired_intf] + [llength $unconn_clk] + \
                      [llength $unconn_rst]}]
    if {$blocking > 0} {
        set verdict "BLOCKED blocking=$blocking wiring=$wiring"
    } elseif {$wiring > 0} {
        set verdict "GAPS wiring=$wiring"
    } else {
        set verdict "CLEAN"
    }
    set out "ASSEMBLY:$verdict (cells=[llength [get_bd_cells -quiet]])"
    if {$blocking > 0} {
        append out "\n  -> the $blocking finding(s) marked BLOCKING below are\
defects, not spare ports: fix\n     them or say why each one is right. The\
wiring list can hold ports this design\n     legitimately never uses; the\
blocking list cannot."
    }
    if {[llength $unwired_intf] > 0} {
        append out "\nUnwired interface pins ([llength $unwired_intf]):\n  [join $unwired_intf "\n  "]"
    }
    if {[llength $unconn_clk] > 0} {
        append out "\nUnconnected clock pins ([llength $unconn_clk]):\n  [join $unconn_clk "\n  "]"
    }
    if {[llength $unconn_rst] > 0} {
        append out "\nUnconnected reset pins ([llength $unconn_rst]):\n  [join $unconn_rst "\n  "]"
    }
    if {[llength $unmapped] > 0} {
        append out "\nBLOCKING unmapped address segments ([llength $unmapped]):\n  [join $unmapped "\n  "]"
    }
    if {[llength $drc] > 0} {
        append out "\nBLOCKING DRC criticals/errors ([llength $drc]):\n  [join $drc "\n  "]"
    }
    if {[llength $illegal] > 0} {
        append out "\nBLOCKING refused NoC routes ([llength $illegal]) -- validate returns\
rc=0 on these, and they are why the memory apertures are missing:\n  [join $illegal "\n  "]"
    }
    if {$aie_gap} {
        append out "\nBLOCKING[string range $aie [expr {[string first : $aie] + 1}] end]"
    }
    if {$intent_gap}  { append out "\nBLOCKING unapplied requirements:\n  $intent" }
    if {$board_gap}   { append out "\nBLOCKING board-file drift:\n  $board" }
    if {$fmt_gap}      { append out "\nBLOCKING $fmt" }
    if {$linebuf_gap}  { append out "\nBLOCKING $linebuf" }
    if {$geometry_gap} { append out "\nBLOCKING $geometry" }
    if {$isp_res_gap}  { append out "\nBLOCKING $isp_res" }
    if {$qos_gap} {
        append out "\nBLOCKING $qos"
    } elseif {[string match "*NOTE:*" $qos]} {
        # A declared spec that is best-effort everywhere passes the gate, and the
        # note about it is the only thing that says a live path has no bounded
        # latency. It belongs where the design gets read at the end.
        append out "\n$qos"
    }
    if {$hooks_gap}    { append out "\nBLOCKING $hooks" }
    if {[string match {RETUNE:[0-9]*} $retune]} { append out "\n$retune" }
    if {[string match {*NOTE:*} $camctrl]} { append out "\n$camctrl" }
    if {$display ne "" && ![string match "DISPLAY:none*" $display]} {
        append out "\n$display"
    }
    if {[llength $declared] > 0} {
        set lines [lmap pin $declared {format "%s -- %s" $pin [dict get $declared_open $pin]}]
        append out "\nDECLARED open pins ([llength $declared]) (ipiasm::declare_open, not a gap):\n  [join $lines "\n  "]"
    }
    # advisory only -- deliberately-excluded segments are NOT gaps
    if {[llength $excluded] > 0} {
        append out "\nADVISORY excluded segments ([llength $excluded]) (intentional, not a gap):\n  [join $excluded "\n  "]"
    }
    return $out
}

# ============================================================
# 6. Spec-conformance reconciliation (final pass)
# ============================================================
# validate/assembly_report answer "is it LEGAL and BUILDABLE?". This pass answers
# the orthogonal question "does the built BD MATCH THE SPEC?" -- a design can be
# ASSEMBLY:CLEAN yet still wrong (2 of 4 MIPI instances, a required datapath edge
# never wired, an off-Versal block accidentally instantiated). It is read-only:
# every check is an in-memory get_bd_* query (no validate, no DRC, no synth), so
# it runs in well under a second even on a large BD -- one MCP round-trip.
#
# BLIND INTEGRITY: it compares against the deterministic MANIFEST (distilled by
# scripts/manifest_expect.py), never the hidden answer key. The agent still
# reasons about the DIVERGED items (re-instantiate a missing copy = confident;
# an absent edge whose peer is ambiguous = ASK) via the same autofix/close_gaps
# loop.

# --- internal: is there at least one intf net between cell-group a and b? ---
# Groups match by name prefix so multi-instance cells (base_0..N-1) are covered.
# --- is a cell a transparent AXI/AXIS transport (interconnect) cell? ---
# These carry a datapath through without being its endpoint, so a path
# A -> <fabric> -> B is still "A connected to B" (e.g. ISP -> NoC -> DDR -> DMA,
# or PS -> SmartConnect -> control slaves).
proc ipiasm::_is_fabric {cell} {
    if {[catch {set vlnv [get_property -quiet VLNV $cell]}]} { return 0 }
    # NoC IPs span generations (axi_noc, axi_noc2, ...) so match a trailing
    # version-digit suffix on the noc/interconnect families with a glob.
    foreach k {smartconnect axi_interconnect axi_register_slice \
               axi_dwidth_converter axi_clock_converter axi_protocol_converter \
               axis_register_slice axis_data_fifo axis_dwidth_converter \
               axis_subset_converter axis_switch axis_broadcaster \
               axis_combiner axis_clock_converter} {
        if {[string match "*:${k}:*" $vlnv]} { return 1 }
    }
    foreach k {axi_noc axis_interconnect} {
        if {[string match "*:${k}:*" $vlnv] || [string match "*:${k}\[0-9\]:*" $vlnv]} { return 1 }
    }
    return 0
}

# --- cells directly reachable from $cell via interface nets ---
# Resolves cell -> intf pins -> nets -> cells (the per-pin form is reliable;
# get_bd_intf_nets -of_objects <CELL> returns empty in some Vivado builds,
# verified 2025.2, which previously made every edge a false-negative).
proc ipiasm::_intf_neighbors {cell} {
    set out {}
    set pins [get_bd_intf_pins -quiet -of_objects $cell]
    if {$pins eq ""} { return {} }
    foreach net [get_bd_intf_nets -quiet -of_objects $pins] {
        foreach c [get_bd_cells -quiet -of_objects $net] {
            if {$c ne $cell} { lappend out $c }
        }
    }
    return [lsort -unique $out]
}

# --- are cell-groups a and b connected (directly OR via transparent fabric)? ---
# Groups match by name prefix so multi-instance cells (base_0..N-1) are covered.
# BFS from the A-group, hopping ONLY through fabric/interconnect cells, so a
# NoC- or SmartConnect-mediated datapath is recognized while a path through an
# unrelated FUNCTIONAL cell is not (avoids over-matching everything on a shared
# NoC's far side). Depth-limited for speed.
proc ipiasm::_cells_connected {a b} {
    set ac [get_bd_cells -quiet "${a}*"]
    set bc [get_bd_cells -quiet "${b}*"]
    if {$ac eq "" || $bc eq ""} { return 0 }
    set seen {}
    set queue $ac
    for {set depth 0} {$depth < 6 && [llength $queue] > 0} {incr depth} {
        set next {}
        foreach c $queue {
            foreach nb [ipiasm::_intf_neighbors $c] {
                if {[lsearch -exact $bc $nb] >= 0} { return 1 }
                if {[lsearch -exact $seen $nb] >= 0} { continue }
                lappend seen $nb
                if {[ipiasm::_is_fabric $nb]} { lappend next $nb }
            }
        }
        set queue $next
    }
    return 0
}

# --- reconcile the live BD against the manifest (read-only) ---
#   manifest_path: the build manifest JSON (spec_to_manifest.py output)
#   expect_helper: path to scripts/manifest_expect.py (the deterministic digest)
# Returns a multi-line "RECONCILE:MATCH|DIVERGED" report. Missing cells/instances,
# absent required edges, missing clock domains, and out-of-scope blocks that were
# wrongly built all count as DIVERGED; extras + unmapped + window offsets are
# advisory (window names cannot be matched to BD SEG_* names without a caller map).
proc ipiasm::reconcile_spec {manifest_path expect_helper} {
    if {![file exists $manifest_path]} { return "RECONCILE_FAIL:no manifest $manifest_path" }
    if {![file exists $expect_helper]} { return "RECONCILE_FAIL:no helper $expect_helper" }
    if {[catch {ipcfg::python_exec $expect_helper $manifest_path} dig]} {
        return "RECONCILE_FAIL:digest:$dig"
    }
    array set M {cells {} domains {} edges {} windows {} oos {}}
    foreach line [split $dig "\n"] {
        set line [string trim $line]
        if {$line eq ""} continue
        set M([lindex $line 0]) [lindex $line 1]
    }

    # ---- cells + instance counts ----
    set cells_ok {}; set cells_missing {}; set cells_extra {}
    foreach {cell want} $M(cells) {
        set present {}
        if {[get_bd_cells -quiet $cell] ne ""} { lappend present $cell }
        for {set i 0} {$i < $want} {incr i} {
            if {[get_bd_cells -quiet "${cell}_$i"] ne ""} { lappend present "${cell}_$i" }
        }
        # Fallback: block automation may wrap or rename a cell. If exact/indexed names came up short,
        # accept prefix matches (<cell>*) so a wrapped instance isn't reported missing.
        if {[llength [lsort -unique $present]] < $want} {
            foreach m [get_bd_cells -quiet "${cell}*"] { lappend present [string cat $m] }
        }
        set cnt [llength [lsort -unique $present]]
        if {$cnt == $want} {
            lappend cells_ok "${cell}($cnt)"
        } elseif {$cnt < $want} {
            lappend cells_missing "$cell:$cnt/$want"
        } else {
            lappend cells_extra "$cell:$cnt/$want"
        }
    }

    # ---- clock domains (reset block + shared wizard exist) ----
    set dom_ok {}; set dom_missing {}
    foreach {name mhz reset wiz} $M(domains) {
        set why {}
        if {$reset ne "" && [get_bd_cells -quiet $reset] eq ""} { lappend why "no $reset" }
        if {$wiz ne "" && [get_bd_cells -quiet $wiz] eq ""}     { lappend why "no $wiz" }
        if {[llength $why] == 0} {
            lappend dom_ok "${name}(${mhz}MHz)"
        } else {
            lappend dom_missing "$name:[join $why ,]"
        }
    }

    # ---- required cell-to-cell datapath edges present? ----
    set e_present {}; set e_absent {}
    foreach {a b} $M(edges) {
        if {[ipiasm::_cells_connected $a $b]} {
            lappend e_present "$a->$b"
        } else {
            lappend e_absent "$a->$b"
        }
    }

    # ---- out-of-scope blocks must NOT be instantiated ----
    set oos_ok {}; set oos_viol {}
    foreach blk $M(oos) {
        if {[get_bd_cells -quiet "${blk}*"] ne ""} {
            lappend oos_viol $blk
        } else {
            lappend oos_ok $blk
        }
    }

    # ---- advisory: spec windows + still-unmapped segments ----
    set win_names {}
    foreach {nm size cnt} $M(windows) { lappend win_names "${nm}($size)" }
    set unmapped [llength [ipiasm::_unassigned_segs]]

    set diverged [expr {[llength $cells_missing] + [llength $e_absent] + \
                        [llength $dom_missing]  + [llength $oos_viol] > 0}]
    set verdict [expr {$diverged ? "DIVERGED" : "MATCH"}]
    set out "RECONCILE:$verdict (cells_ok=[llength $cells_ok]/[expr {[llength $M(cells)]/2}] edges_present=[llength $e_present]/[expr {[llength $M(edges)]/2}])"
    if {[llength $cells_missing] > 0} { append out "\nMissing cells/instances ([llength $cells_missing]):\n  [join $cells_missing "\n  "]" }
    if {[llength $dom_missing] > 0}   { append out "\nIncomplete clock domains ([llength $dom_missing]):\n  [join $dom_missing "\n  "]" }
    if {[llength $e_absent] > 0}      { append out "\nUnwired required edges ([llength $e_absent]):\n  [join $e_absent "\n  "]" }
    if {[llength $oos_viol] > 0}      { append out "\nOut-of-scope blocks wrongly built ([llength $oos_viol]):\n  [join $oos_viol "\n  "]" }
    if {[llength $cells_extra] > 0}   { append out "\nADVISORY extra instances:\n  [join $cells_extra "\n  "]" }
    append out "\nADVISORY windows (verify offsets manually): [join $win_names {, }]"
    append out "\nADVISORY unmapped segments: $unmapped"
    return $out
}

source [file join $::ipiasm::library_dir feasibility.tcl]
source [file join $::ipiasm::library_dir clocks.tcl]
set ::ipiasm::loaded_signature $::ipiasm::source_candidate
unset ::ipiasm::source_candidate
ipiasm::_install_native_guards
puts "LIBRARY:OK ipiasm"

# Check every coherent PS NoC manager port reaches the NoC the same way.
# The PS drives its coherent (CCI) NoC ports in interleaving mode: if any is used,
# all must connect to one logical NoC instance (PG450 PS NoC Interfaces; PG352 NoC
# Interfaces). A partly connected set still validates, with only BD 41-2670
# "incomplete address path" warnings, and fails at run time. The PS marks these
# ports itself with CONFIG.MY_CATEGORY ps_cci, so no port names are assumed.
# Interleaving needs every port to reach the same targets, but not the same memory
# controller PORT: a NoC interface may use only one port of a DDR controller (BD 41-3265),
# and PG352's lowest-latency table puts each CCI port on a different DDRMC port. So the
# MC_<n> ports of one NoC count as that NoC's single memory target.
proc ipiasm::check_ps_noc_masters {} {
    if {![llength [info commands get_bd_cells]]} { return "PS_NOC:NA no block design commands in this session" }
    set ps_cells [ipiasm::_ps_cells]
    if {![llength $ps_cells]} { return "PS_NOC:NA no processing system cell" }
    set problems {}
    set checked 0
    foreach ps $ps_cells {
        set coherent {}
        foreach pin [get_bd_intf_pins -quiet -of_objects $ps -filter {MODE == Master}] {
            if {[get_property -quiet CONFIG.MY_CATEGORY $pin] eq "ps_cci"} { lappend coherent $pin }
        }
        if {![llength $coherent]} { continue }
        set used {}; set unused {}; set nocs {}; set routes {}
        foreach pin [lsort $coherent] {
            set name [get_property NAME $pin]
            set peer {}
            foreach candidate [find_bd_objs -quiet -relation connected_to $pin] {
                if {$candidate ne $pin} { set peer $candidate; break }
            }
            if {$peer eq ""} { lappend unused $name; continue }
            lappend used $name
            dict set nocs $name [get_bd_cells -quiet -of_objects $peer]
            dict set routes $name [lsort -unique [regsub -all {\mMC_\d+\M} [regexp -all -inline {\m(?:M\d+_(?:AXI|INI)|MC_\d+)\M} \
                [get_property -quiet CONFIG.CONNECTIONS $peer]] {MC}]]
        }
        incr checked [llength $coherent]
        if {![llength $used]} { continue }
        if {[llength $unused]} {
            lappend problems "$ps: coherent NoC ports {[join $unused { }]} unconnected while {[join $used { }]} are used; connect all of them"
        }
        if {[llength [lsort -unique [dict values $nocs]]] > 1} {
            lappend problems "$ps: coherent NoC ports reach different NoC instances {[join [lsort -unique [dict values $nocs]] { }]}; use one"
        }
        if {[llength [lsort -unique [dict values $routes]]] > 1} {
            set rows {}
            dict for {name targets} $routes { lappend rows "$name->{$targets}" }
            lappend problems "$ps: coherent NoC ports route to different targets ([join $rows {; }]; MC = any port of this NoC's memory controller); give each port a route to every target, using one memory-controller port each"
        }
    }
    if {!$checked} { return "PS_NOC:NA no coherent PS NoC manager ports" }
    if {[llength $problems]} { return "PS_NOC_FAIL:\n  [join $problems "\n  "]" }
    return "PS_NOC:OK coherent_ports=$checked"
}


# --- qos gate wrappers: both halves must be OK (or NA on a NoC-less part) ---
proc ipiasm::_qos_gate_update {} {
    variable qos_sub
    if {[dict get $qos_sub qos] && [dict get $qos_sub noc]} {
        ipiasm::_gate_set qos 1 "check_qos + check_noc_solution green"
    } else {
        ipiasm::_gate_set qos 0
    }
}
# Check every NoC route has declared, non-placeholder QoS; feeds half of the qos gate.
proc ipiasm::check_qos {{allow {}}} {
    variable qos_sub
    if {[llength [ipiasm::_all_noc_cells]] == 0} {
        dict set qos_sub qos 1; dict set qos_sub noc 1
        ipiasm::_qos_gate_update
        return "QOS:NA this design has no NoC cell (non-Versal parts have none)"
    }
    set r [ipiasm::_check_qos_impl $allow]
    dict set qos_sub qos [string match "QOS:OK*" $r]
    ipiasm::_qos_gate_update
    return $r
}
# Pre-address gate: prove the memory aperture exists and traffic declarations
# are legal, but do not require a master to hold the aperture before Address
# Editor has run. This is the gate auto_address/place_named_windows consume.
proc ipiasm::check_noc_pre_address {{want_mc 0}} {
    set qos [ipiasm::check_qos]
    set memory [ipiasm::check_memory_subsystem $want_mc]
    set noc [ipiasm::check_noc_solution 0]
    set qos_ok [expr {
        [string match "QOS:OK*" $qos] || [string match "QOS:NA*" $qos]
    }]
    set mem_ok [string match "MEM:OK*" $memory]
    set noc_ok [expr {
        [string match "NOC:OK*" $noc] || [string match "NOC:NA*" $noc]
    }]
    if {!$qos_ok || !$mem_ok || !$noc_ok} {
        return "NOC_PRE_FAIL:\n  $qos\n  $memory\n  $noc"
    }
    return "NOC_PRE:OK\n  $qos\n  $memory\n  $noc"
}

# Post-address gate: the published aperture must now be present in functional
# master address spaces. Never override this result; repair addressing instead.
proc ipiasm::check_noc_post_address {} {
    set noc [ipiasm::check_noc_solution 1]
    if {[string match "NOC:OK*" $noc] || [string match "NOC:NA*" $noc]} {
        return "NOC_POST:OK\n  $noc"
    }
    return "NOC_POST_FAIL:\n  $noc"
}

# Check the NoC has a legal routing solution; feeds half of the qos gate.
proc ipiasm::check_noc_solution {{want_memory 1}} {
    variable qos_sub
    set r [ipiasm::_check_noc_solution_impl $want_memory]
    dict set qos_sub noc [expr {[string match "NOC:OK*" $r] || [string match "NOC:NA*" $r]}]
    if {[string match "NOC:NA*" $r]} { dict set qos_sub qos 1 }
    ipiasm::_qos_gate_update
    return $r
}

# A client gives up on a tool call after a fixed time (60 s in Claude Code) while Vivado
# finishes the command, and the reply is lost. Measured on 2026.2 (2026-11-18): the first
# get_example_designs in a process takes about 60 s however it is filtered, so
# reference_search outlasts the call, as do example_reference, a whole-plan
# assemble_from_plan and deliver (4-5 min on a Versal design with NoC and AI Engine); agents
# then slept 75-340 s guessing when it would end. Each call below marks itself running in
# <out>/logs/<proc>.running and writes its return value to <out>/logs/<proc>.result.txt when it
# ends, so scripts/await_result.py can return the moment it does.
set ::ipiasm::kept_results {
    reference_search {0 dir} example_reference {1 dir} ced_reference {1 dir} scratch_bind {0 dir}
    assemble_from_plan {0 file} deliver {0 dir}
}

# The <out>/logs directory of a traced call: the argument kept_results names (its parent
# directory for a file argument), or "" when the call omitted it.
proc ipiasm::_kept_logs {name command} {
    lassign [dict get $::ipiasm::kept_results $name] index kind
    set path [lindex $command [expr {$index + 1}]]
    if {$path eq ""} {return ""}
    return [file join [file normalize [expr {$kind eq "file" ? [file dirname $path] : $path}]] logs]
}

# Execution-trace callback (enter): write <logs>/<proc>.running holding the start time in
# seconds. Never raises.
proc ipiasm::_mark_running {name command op} {
    catch {
        set logs [ipiasm::_kept_logs $name $command]
        if {$logs eq ""} {return}
        file mkdir $logs
        set channel [open [file join $logs $name.running] w]
        puts -nonewline $channel [clock seconds]
        close $channel
    }
}

# Execution-trace callback (leave): write the traced call's return value, or its error, to
# <logs>/<proc>.result.txt, then delete <logs>/<proc>.running. The result is written first so
# a waiter that sees the marker gone always finds this call's result. Never raises: a failed
# write only loses the copy.
proc ipiasm::_keep_result {name command code result op} {
    catch {
        set logs [ipiasm::_kept_logs $name $command]
        if {$logs eq ""} {return}
        file mkdir $logs
        set channel [open [file join $logs $name.result.txt] w]
        fconfigure $channel -encoding utf-8
        puts -nonewline $channel [expr {$code == 0 ? $result : "ERROR: $result"}]
        close $channel
        file delete [file join $logs $name.running]
    }
}
dict for {name spec} $::ipiasm::kept_results {
    trace add execution ::ipiasm::$name enter [list ipiasm::_mark_running $name]
    trace add execution ::ipiasm::$name leave [list ipiasm::_keep_result $name]
}

# Build timing: inside Vivado every build records where its time goes (lib/stage_trace.tcl,
# summarised by scripts/timing_report.py). IPIASM_TRACE=off disables it; an absolute path
# in IPIASM_TRACE picks the log; IPIASM_TRACE_DRIVER substitutes another tracer. Outside
# Vivado (offline tests in tclsh) it starts only when IPIASM_TRACE names a log.
set ::ipiasm::trace_driver [file join $::ipiasm::library_dir stage_trace.tcl]
if {[info exists ::env(IPIASM_TRACE_DRIVER)] && $::env(IPIASM_TRACE_DRIVER) ne ""} {
    set ::ipiasm::trace_driver $::env(IPIASM_TRACE_DRIVER)
}
set ::ipiasm::trace_wanted [expr {[info exists ::env(IPIASM_TRACE)] ? $::env(IPIASM_TRACE) : ""}]
if {[string tolower $::ipiasm::trace_wanted] ni {off 0 no false}
    && ($::ipiasm::trace_wanted ne "" || [string match -nocase *vivado* [file tail [info nameofexecutable]]])} {
    if {[file isfile $::ipiasm::trace_driver]} {
        uplevel #0 [list source $::ipiasm::trace_driver]
        if {![info exists ::ipiasm::trace::channel] || $::ipiasm::trace::channel eq {}} {puts [ipiasm::trace_start]}
    } else {
        puts "TRACE_FAIL:no trace driver at $::ipiasm::trace_driver"
    }
}
