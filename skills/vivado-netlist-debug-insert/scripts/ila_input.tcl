# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_input.tcl: check_input, architecture, existing debug cores and their probed nets, MARK_DEBUG mode. Sourced by ila_insert.tcl; every proc here was moved from it without edits.

namespace eval ila {
    # The architecture of the open design (kintexuplus, versal, ...). ila::check_input records it. Every proc that branches on it asks here,
    # so a session that skipped check_input, or opened another design since, gets the architecture of THIS design and never "not Versal" by
    # default. A recorded fact is used when it belongs to this part (or has no part recorded). With no design open and no fact it refuses.
    proc require_architecture {} {
        variable facts
        set have [dict exists $facts architecture]
        if {[catch {
            set part [get_property PART [current_design]]
            if {$part eq ""} { error "no part" }
            if {!($have && (![dict exists $facts part] || [dict get $facts part] eq $part))} { set arch [get_property ARCHITECTURE [get_parts $part]] }
        }]} {
            if {$have} { return [dict get $facts architecture] }
            return -code error "ILA_NO_INPUT_CHECK: the architecture of this design is not known: no design is open, or Vivado gave no part. Check the input of this design first (AI only: ila::check_input). Nothing was done."
        }
        if {[info exists arch]} {
            dict set facts part $part
            dict set facts architecture $arch
        }
        return [dict get $facts architecture]
    }

    # ---- 1. input check -------------------------------------------------
    # The Design State line of report_timing_summary (Synthesized, Optimized, Fully Placed, Routed): the whole rest of the line. "" when the
    # report cannot be read. It is the only signal for "optimized, no cores" and it names the state in a refusal (P1-G).
    proc design_state {} {
        if {[catch {report_timing_summary -return_string -max_paths 1} rep]} { return "" }
        if {[regexp {Design State\s*:\s*([^\n|]+)} $rep -> s]} { return [string trim $s] }
        return ""
    }

    # 1 when the open design is the design of a project implementation run (Vivado refuses a debug core change there, 12-4097). A checkpoint
    # opened in a project session is an implicit project with no runs, so project_open_p is already 0 there. The design properties
    # IS_IMPLEMENTATION and PARENT are empty (measured 2026.1 and 2026.2); the run property answers.
    proc impl_run_design_p {} {
        if {![project_open_p]} { return 0 }
        return [string is true -strict [get_property -quiet IS_IMPLEMENTATION [get_runs -quiet [current_design -quiet]]]]
    }

    # The identity of the open design: name, part, top and port count. Vivado gives a design no id of its own (measured 2026.1 and 2026.2), so this is the
    # cheapest key that tells another design from this one in nearly every case; the placement query in input_gate covers the case that matters.
    # Ports never change when cores are added, so the key survives apply.
    proc design_key {} {
        set d [current_design]
        set key {}
        foreach q {NAME PART TOP} {
            if {[catch {get_property -quiet $q $d} v]} { set v "" }
            lappend key $v
        }
        if {[catch {llength [get_ports -quiet *]} n]} { set n "" }
        return [concat $key $n]
    }

    # The rule behind ILA_REFUSED_PLACED (review A finding 3): check_input records a verdict, and plan and apply honour it.
    #  - After a refusal, plan and apply refuse for that design, until ila::check_input accepts it (or -allow_state_reset 1 overrides).
    #  - apply also refuses when the open design is not the one check_input accepted, or when cells were placed since (one query), unless the override was given.
    # With no verdict (check_input never ran in this state) nothing is checked: the session works as before. what is plan or apply.
    proc input_gate {what} {
        variable facts
        if {![dict exists $facts input_verdict]} return
        set v [dict get $facts input_verdict]
        set same [expr {[design_key] eq [dict get $v key]}]
        set refused [expr {[dict get $v state] eq "refused"}]
        if {!$same} {
            # a plan only computes: the verdict of another design says nothing about this one. apply changes the design, so it needs its own check.
            if {$what ne "apply"} return
            return -code error "ILA_NO_INPUT_CHECK: the open design is not the one whose input was checked ([lindex [dict get $v key] 0], now [lindex [design_key] 0]). Check the input of this design first (AI only: ila::apply refused; run ila::check_input). Nothing was changed."
        }
        if {$refused} {
            if {[dict get $v code] eq "ILA_REFUSED_IMPL_RUN"} {
                return -code error "ILA_REFUSED_IMPL_RUN: the input check refused this design: it is the implementation run of the open project, where Vivado does not allow a debug core change. Allowing a state reset does not help here. Open the synthesized design (open_run synth_1) and check it again (AI only: ila::$what refused; run ila::check_input; -allow_state_reset does not apply). Nothing was done."
            }
            return -code error "ILA_REFUSED_PLACED: the input check refused this design because it is already placed or routed. Open the synthesized design and check it again. Only if the user asked to change this design as it is, check it with state reset allowed and plan again (AI only: ila::$what refused; ila::check_input, or ila::check_input -allow_state_reset 1 and then ila::plan). Nothing was done."
        }
        if {$what eq "apply" && ![dict get $v override]} {
            set now [llength [get_cells -quiet -hier -filter {IS_PRIMITIVE && STATUS == "PLACED"}]]
            if {$now > [dict get $v placed]} {
                return -code error "ILA_REFUSED_PLACED: this design was placed or routed after its input check accepted it ($now cells placed, [dict get $v placed] then). Open the synthesized design and check it again. Only if the user asked to change this design as it is, check it with state reset allowed and plan again (AI only: ila::apply refused; ila::check_input, or ila::check_input -allow_state_reset 1 and then ila::plan). Nothing was changed."
            }
        }
    }

    # ila::check_input ?-allow_state_reset 0|1?. A placed or routed design is refused unless -allow_state_reset 1 (never in a project
    # implementation run). An optimized design that is not placed is accepted with a warning. check_input records a verdict that plan and apply honour (input_gate).
    proc check_input {args} {
        variable facts
        array set o {-allow_state_reset 0}
        if {[llength $args] % 2} { return -code error "ILA_BAD_OPTION: check_input: option [lindex $args end] needs a value (0 or 1)" }
        foreach {k v} $args {
            if {![info exists o($k)]} { return -code error "ILA_BAD_OPTION: check_input knows -allow_state_reset, not $k" }
            if {![string is boolean -strict $v]} { return -code error "ILA_BAD_OPTION: check_input -allow_state_reset takes 0 or 1, not '$v'" }
            set o($k) [string is true $v]
        }
        # Cores this session made that are still in the design stay on the list: a second check_input (to re-orient) must not leave cores
        # that ila::reset can no longer remove. A core that is not in the design (another design was opened) is forgotten, and so is every
        # core when the open design is not the one apply changed: a name is no proof, the user's own u_ila_0 has the same name (review A finding 1).
        set marked [design_marked_p]
        set tok [expr {[dict exists $facts design_token] ? [dict get $facts design_token] : ""}]
        set kept [lmap c [expr {$marked ? [created_list] : {}}] {expr {[llength [get_debug_cores -quiet $c]] > 0 ? $c : [continue]}}]
        variable replay
        variable replaced
        set replay0 $replay
        set replaced0 $replaced
        session_reset
        if {[llength $kept] > 0} {
            dict set facts created_cores [join $kept " "]
            if {$tok ne ""} { dict set facts design_token $tok }
            # the same design: its replay and the deletes of the user's cores still describe it, so keep them (dropping them would lose track of what this session did to a design that still holds it)
            set replay $replay0
            set replaced $replaced0
            say "this design already holds [join $kept {, }], made by this skill in this session. To change a setting of [expr {[llength $kept] == 1 ? "that core" : "one of them"}], ask to change it; to probe other signals instead, ask to replace it with a new probe set. Beginning again deletes [expr {[llength $kept] == 1 ? "it" : "them"}] from the open design, and that work is lost (AI only: a setting is ila::change <core>; other signals are ila::plan with -replace_existing 1; ila::reset deletes every core this session created, here [join $kept {, }])."
        }
        set part  [get_property PART [current_design]]
        set arch  [get_property ARCHITECTURE [get_parts $part]]
        # STATUS is FIXED for cells the user pinned with LOC in XDC (I/O buffers: group I/O, also FIXED in a synthesized checkpoint, measured 2026.1), and for EVERY
        # cell after lock_design (a static routed checkpoint of a DFX flow: 0 cells PLACED, 3139 FIXED, Design State Routed, measured 2026.1). So PLACED alone
        # is not the signal: a routed checkpoint after lock_design has no PLACED cell at all.
        set placed [llength [get_cells -quiet -hier -filter {IS_PRIMITIVE && STATUS == "PLACED"}]]
        set cores [real_debug_cores]
        dict set facts part $part
        dict set facts architecture $arch
        dict set facts existing_debug_cores [join $cores " "]
        # State rule (D-D). The Design State line of the timing summary is the main signal: anything but Synthesized or Optimized (Placed, Fully Placed, Physopt,
        # Routed) is a placed or routed design. Placed cells, or FIXED cells that are not I/O buffers, are the second signal, asked when the line does not say
        # synthesis. "Cores exist and a *_CV black box is left" means synthesized (not yet built) and needs no timing summary.
        set ds ""
        set dsread 0
        if {$placed > 0 || [llength $cores] == 0 || [llength [get_cells -quiet -hier -filter {REF_NAME =~ *_CV}]] == 0} {
            set ds [design_state]
            set dsread 1
        }
        if {$dsread && [string tolower $ds] ni {synthesized optimized}} {
            incr placed [llength [get_cells -quiet -hier -filter {IS_PRIMITIVE && STATUS == "FIXED" && PRIMITIVE_GROUP != "I/O"}]]
        }
        dict set facts placed_cells $placed
        set swarn {}
        if {$placed > 0 || [regexp -nocase {place|rout|physopt} $ds]} {
            if {$ds eq ""} { set ds unknown }
            if {[impl_run_design_p]} {
                say "REFUSED: this is the implementation run of the open project (Design State: $ds). Vivado does not allow a debug core change here (Vivado 12-4097)."
                say "Close this design and open the synthesized design (open_run synth_1), then check it again. Allowing a state reset does not help in a project implementation run (AI only: ila::check_input; -allow_state_reset does not apply)."
                dict set facts input_verdict [dict create state refused code ILA_REFUSED_IMPL_RUN override 0 placed $placed key [design_key]]
                return -code error "ILA_REFUSED_IMPL_RUN (AI only: this code is for you; tell the user the reason above in words and never say the code)"
            }
            if {!$o(-allow_state_reset)} {
                say "REFUSED: this design is already placed or routed (Design State: $ds, $placed cells placed)."
                say "Vivado accepts a debug core change here without any message, but it drops part of the placement and routing and still reports the same state."
                say "After the change opt_design, place_design and route_design must all run again. Until then write_debug_probes and write_bitstream fail. A design that already holds a built core may also crash report_debug_core, and unsaved work would be lost."
                say "Open the synthesized design instead. Only if the user asked to change this design as it is, check it again with state reset allowed (AI only: ila::check_input -allow_state_reset 1)."
                dict set facts input_verdict [dict create state refused code ILA_REFUSED_PLACED override 0 placed $placed key [design_key]]
                return -code error "ILA_REFUSED_PLACED (AI only: this code is for you; tell the user the reason above in words and never say the code)"
            }
            lappend swarn "WARNING: state reset allowed: this design is already placed or routed (Design State: $ds, $placed cells placed). Going on as asked; nothing else changes (AI only: check_input ran with -allow_state_reset 1)." \
                "Placement and routing will be partly dropped. After the change opt_design, place_design and route_design must all run again." \
                "A design that already holds a built core may crash report_debug_core, and unsaved work would be lost."
        } elseif {[llength $cores] > 0} {
            # Cores with no *_CV black box left are not always an optimized design: a synthesized Versal DFX or block design project holds
            # its AXI Debug Hubs as built IP (v-dfx, measured 2026.1 and 2026.2). The Design State line tells the two apart.
            if {$dsread && $ds ne "Synthesized"} {
                lappend swarn "WARNING: this design is already optimized and its debug cores are built (no black box core is left). A change turns them into black boxes until opt_design runs again. The debug core change is accepted. opt_design must run again after the change."
            }
        } else {
            if {$ds eq ""} {
                lappend swarn "WARNING: could not read the design state (report_timing_summary failed). If this design was already optimized, opt_design must run again after the change."
            } elseif {$ds eq "Optimized"} {
                lappend swarn "WARNING: this design is already optimized (Design State: Optimized). The debug core change is accepted. opt_design must run again after the change."
            }
        }
        if {$arch eq "versal" && [llength [get_cells -quiet -hier -filter {REF_NAME == PS9 || REF_NAME == PS11}]] == 0} {
            say "BLOCKED FB-V01: this Versal design has no CIPS or PS Wizard (no PS9 or PS11 cell). The debug hub reaches the host through the CIPS, so Vivado cannot insert one (error 12-727). The design source needs a CIPS or PS Wizard with at least one PL reset enabled. I will not add one."
            return -code error "ILA_BLOCKED_NO_CIPS (AI only: this code is for you; tell the user the reason above in words and never say the code)"
        }
        foreach w $swarn { say $w }   ;# after the blocker above, so FB-V01 stays the first line
        # the verdict plan and apply honour (input_gate): this design was accepted, with or without the override
        dict set facts input_verdict [dict create state accepted code "" override $o(-allow_state_reset) placed $placed key [design_key]]
        say "input ok: part=$part architecture=$arch placed=$placed existing_debug_cores=[llength $cores]"
        set mdm [mark_debug_mode]
        dict set facts mark_debug_mode [expr {$mdm eq "" ? "unknown" : $mdm}]
        say "[join [mark_debug_mode_text $mdm] { }]"
        dfx_report $arch
        set ex [existing_debug_cores]
        dict set facts existing_debug_cores [join [lsort -unique [concat $cores [lmap e $ex {lindex $e 0}]]] " "]
        if {[llength $ex] > 0} { say [existing_cores_text $arch $ex] }
        return $arch
    }

    # Debug cores already in the design, found by what they are, not by what they are called. A debug core is either a core Vivado lists
    # (get_debug_cores: from XDC or an earlier insertion) or a cell of a debug core IP (an instantiated ila, vio, hub, bridge or JTAG to AXI;
    # a block design wraps the IP, so the reference name ends in the IP name, such as design_1_axis_ila_0_0). Returns {{name type} ...}.
    # A cell inside another core's cell is the same core, so only the outermost cell is listed.
    variable core_ref_rx {(^|_)(axis_ila|axis_vio|axi_dbg_hub|dbg_hub|debug_bridge|jtag_axi|ila|vio)(_[0-9]+)*(_CV)?$}
    # The debug core IP type a reference name stands for, or "" when the name is none of them. The name only gives the TYPE: whether the cell is
    # debug IP at all is debug_ip_type's question.
    proc core_ref_type {ref} {
        variable core_ref_rx
        if {![regexp $core_ref_rx $ref -> pre type]} { return "" }
        return $type
    }
    # The type of a debug core cell, or "" when the cell is not one. Vivado says what a cell is: the cell property IS_DEBUG_CORE is 1 on a Xilinx
    # ILA, VIO or hub IP cell (ila_0, design_1_ila_0_0) and on a core made by create_debug_core (u_ila_0_CV), also in a checkpoint, and empty on
    # a user module whatever its name (pixel_ila, rgb_vio_2, design_1_pixel_ila_0_0 for a module in a block design: review A-01, measured on
    # 2026.1 and 2026.2, e91). The cell inside an ILA IP has TRUE, so the test is string is true. The HBM IP has 1 too: its name is not a
    # debug core name, so the type is "".
    proc debug_ip_type {cell} {
        if {![string is true -strict [get_property IS_DEBUG_CORE $cell]]} { return "" }
        return [core_ref_type [get_property REF_NAME $cell]]
    }
    proc existing_debug_cores {} {
        set found [dict create]
        # A debug core has no TYPE property (get_property TYPE prints Vivado 12-4444 on every call): the type comes from the cell scan below.
        foreach c [real_debug_cores] { dict set found $c "" }
        foreach g {*ila* *vio* *dbg_hub* *debug_bridge* *jtag_axi*} {
            foreach c [get_cells -quiet -hier -filter "REF_NAME =~ $g"] {
                set type [debug_ip_type $c]
                if {$type eq ""} continue
                set n [get_property NAME $c]
                if {![dict exists $found $n] || [dict get $found $n] eq ""} { dict set found $n $type }
            }
        }
        set names [lsort [dict keys $found]]
        set out {}
        foreach n $names {
            set inner 0
            foreach o $names { if {$o ne $n && [string first "$o/" $n] == 0} { set inner 1; break } }
            if {$inner} continue
            set t [dict get $found $n]
            lappend out [list $n [expr {$t eq "" ? "debug core" : $t}]]
        }
        return $out
    }

    # The nets the existing ILA cores probe, as a dict: net name -> core name. Every name of an electrical net is a key (segments), so an
    # alias of a probed net is found the way segseen finds an alias. The probe pins of the core cell lead to the nets (get_nets -of_objects
    # on a debug_port fails, Common 17-679). With replace, the cores a -replace_existing plan deletes (replace_targets) do not count.
    proc probed_nets {replace} {
        set out [dict create]
        set deleted [replace_deleted $replace]
        foreach e [existing_debug_cores] {
            lassign $e core type
            if {$type in {vio axis_vio dbg_hub axi_dbg_hub debug_bridge jtag_axi} || $core in $deleted} continue
            set cell [get_cells -quiet $core]
            if {$cell eq ""} {
                # A listed core with no cell: the report_debug_core table is the only source. Vivado 2026.1 and 2026.2 give an XDC or create_debug_core core a cell at once (checked on LSF, e01), so this is a safety net.
                if {[catch {connected_nets $core {probe\d+}} cn]} { set cn {} }
                foreach nm $cn { if {![dict exists $out $nm]} { dict set out $nm $core } }
                continue
            }
            # One query per core, not four per probe pin: a 4096-bit core would cost about 16000 Vivado calls before the first item is read.
            set pins [get_pins -quiet -of_objects $cell -filter {NAME =~ *probe*}]
            if {[llength $pins] == 0} continue
            set nets [get_nets -quiet -of_objects $pins]
            if {[llength $nets] == 0} continue
            set names [get_property NAME $nets]
            set sgs [get_nets -quiet -segments $nets]
            if {[llength $sgs] > 0} { lappend names {*}[get_property NAME $sgs] }
            foreach nm $names { if {![dict exists $out $nm]} { dict set out $nm $core } }
        }
        return $out
    }
    # A name get_debug_cores lists is a real debug core unless it has a cell whose type is not a debug core (core_ref_rx, which also takes
    # the _CV wrapper Vivado gives an inserted core). A core object with no cell (from XDC) counts. Asking a non-core such as the HBM IP
    # for a core property (C_DATA_DEPTH) is a Vivado error.
    proc listed_core_is_real {name} {
        set cell [get_cells -quiet $name]
        return [expr {$cell eq "" || [debug_ip_type $cell] ne ""}]
    }
    # The debug cores get_debug_cores lists, minus the names listed_core_is_real refuses (the HBM IP). Every caller that treats a listed name as a core uses this.
    # Returns NAMES. The string value of a listed core is its name, and Vivado 2026.1 and 2026.2 refuse get_property NAME on that string (Common 17-161),
    # so no caller reads NAME from the result. get_debug_cores can return objects or strings: the string value is taken as the name either way.
    proc real_debug_cores {} {
        set out {}
        foreach c [get_debug_cores -quiet] {
            set name "$c"
            if {[listed_core_is_real $name]} { lappend out $name }
        }
        return $out
    }
    # The cells of the debug cores in the design (from existing_debug_cores), once per plan. A cell is inside a debug core when its
    # name is a core cell or lies under one. N06 uses this instead of a name prefix, so a user register called dbg_hub_status_reg is not refused.
    # get_debug_cores can also list an IP that is not a debug core (the HBM IP u_hbm, REF_NAME hbm_0, in C14): see listed_core_is_real.
    proc in_debug_core {cellname} {
        variable core_cells
        if {![info exists core_cells]} {
            set core_cells {}
            foreach e [existing_debug_cores] { if {[listed_core_is_real [lindex $e 0]]} { lappend core_cells [lindex $e 0] } }
        }
        foreach c $core_cells { if {$cellname eq $c || [string first "$c/" $cellname] == 0} { return 1 } }
        return 0
    }

    # The EXISTING DEBUG CORES line. What the user can do depends on the design: replacing an existing core deletes only cores Vivado lists
    # (get_debug_cores) named u_ila_*, and it cannot work in a DFX design, where a core belongs to the module source.
    proc existing_cores_text {arch ex} {
        set list [join [lmap e $ex {format "%s (%s)" [lindex $e 0] [lindex $e 1]}] ", "]
        set txt "EXISTING DEBUG CORES: $list."
        if {[llength [dfx_cells]] > 0} {
            append txt " Tell the user. Replacing an existing core does not work in a DFX design: these cores belong to the module source, so changing what one probes means changing that source."
            if {$arch eq "versal"} {
                append txt " To add probes, plan normally: the new core gets the next free name and the old one stays."
            } else {
                if {[dfx_insert_supported]} {
                    append txt " This skill refuses to insert into a design that already carries debug cores or ports, also on Vivado 2026.2 and newer (measured: the insertion then failed downstream), so a plan for this design is refused as DFX not supported."
                } else {
                    append txt " Vivado 2026.1 and older do not insert new cores into a design with a reconfigurable partition (17-69), so the plan can succeed and the apply is then refused as DFX not supported (the 17-69 text of Vivado's own refusal)."
                }
            }
        } elseif {[llength [lsearch -all -inline -regexp [lmap e $ex {lindex $e 0}] {(^|/)u_ila_[0-9]+$}]] > 0 && [llength [get_debug_cores -quiet u_ila_*]] > 0} {
            append txt " Tell the user. To change what is probed, plan again and replace the old core (its probe set is deleted). To add probes, plan normally: the new core gets the next free name and the old one stays."
        } else {
            append txt " Tell the user. The skill cannot delete or change these (they are part of the design source). To add probes, plan normally: the new core gets the next free name and the old one stays."
        }
        return $txt
    }

    # ---- 2b. net and clock rules from the AMD docs (LF3) ------------------------------------------------
    # A warning whose text starts with "NOTE " is a note: it is printed as NOTE, not WARNING, and refuses nothing.
    # Codes: M01 mark_debug flow state, M02 MARK_DEBUG covers a whole bus, M03 partially driven vector, A02 canonical name and
    # aliases, C01 MMCM or PLL LOCKED, C02 hub clock report, M04 DFX partition boundary net (a note). See references/rejection-codes.md.

    # config_flows -mark_debug of THIS session: enable (default, MARK_DEBUG nets are kept), disable or synthesis_only (they can be
    # optimized away). Vivado 2026.1 has no query form of config_flows -mark_debug; report_config_flows prints the value, so the table
    # row is parsed. The setting belongs to the session, not to the checkpoint: UG835 says it must be applied before the checkpoint
    # is opened, and a checkpoint synthesized under another setting does not record it. Returns "" when the report cannot be read.
    proc mark_debug_mode {} {
        set v ""
        catch {
            set rep [report_config_flows -return_string -no_header]
            regexp -line {^mark_debug\s+\S+(?: \| \S+)*\s{2,}(\S+)\s} $rep -> v
        }
        set v [string tolower $v]
        return [expr {$v in {enable disable synthesis_only} ? $v : ""}]
    }

    # The text to print for the mark_debug state, and whether it is a warning. {level text}
    proc mark_debug_mode_text {mode} {
        switch -- $mode {
            enable { return [list NOTE "config_flows -mark_debug is enable in this session (the default): nets with MARK_DEBUG are not optimized away. The setting is not stored in a checkpoint, so it says nothing about how the checkpoint was synthesized."] }
            "" { return [list NOTE "config_flows -mark_debug could not be read in this session (report_config_flows gave no value), so it is not known whether MARK_DEBUG nets were kept."] }
            default {
                set what [expr {$mode eq "disable" ? "synthesis and implementation may optimize MARK_DEBUG nets away" : "implementation may optimize MARK_DEBUG nets away"}]
                return [list WARNING "config_flows -mark_debug is $mode in this session: $what (UG908, Managing mark_debug Behavior Post-Synthesis). A net marked in the RTL may be missing from this netlist. The skill does not depend on MARK_DEBUG to find a net: it resolves the name you gave in the netlist as it is now, and a request for the marked nets lists only the nets that still carry MARK_DEBUG."]
            }
        }
    }
}
