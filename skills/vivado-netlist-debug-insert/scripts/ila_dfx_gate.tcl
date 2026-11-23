# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_dfx_gate.tcl: DFX helpers, dfx_gate, DFX reasons. Sourced by ila_insert.tcl; every proc here was moved from it without edits.

namespace eval ila {
    # ---- 0. DFX (Dynamic Function eXchange) awareness ---------------------
    # A reconfigurable partition (RP) is a cell with HD.RECONFIGURABLE=1, or, in a static-only checkpoint, a black box cell.
    # This only reads the design. It never edits it (criterion 7: missing debug plumbing is reported, never added).
    variable bscan_ports {S_BSCAN_bscanid_en S_BSCAN_capture S_BSCAN_drck S_BSCAN_reset S_BSCAN_runtest S_BSCAN_sel S_BSCAN_shift S_BSCAN_tck S_BSCAN_tdi S_BSCAN_tdo S_BSCAN_tms S_BSCAN_update}

    # Returns a list of dicts: cell ref reconf (HD.RECONFIGURABLE) blackbox
    proc dfx_cells {} {
        set out {}
        set seen {}
        foreach c [get_cells -quiet -hier -filter {HD.RECONFIGURABLE == 1}] {
            lappend seen $c
            lappend out [dict create cell [get_property NAME $c] ref [get_property REF_NAME $c] reconf 1 blackbox [expr {[get_property IS_BLACKBOX $c] ? 1 : 0}]]
        }
        # A static-only checkpoint has the RP as a black box without the flag. Ordinary black boxes (out-of-context IP, a debug hub
        # before opt_design) are not partitions, so an unflagged black box counts only when it has at least one S_BSCAN_* pin.
        foreach c [get_cells -quiet -hier -filter {IS_BLACKBOX == 1 && !IS_PRIMITIVE}] {
            if {[lsearch -exact $seen $c] >= 0 || [regexp {dbg_hub|xsdbm} [get_property REF_NAME $c]]} continue
            if {[llength [dfx_bscan_missing $c]] >= 12} continue
            lappend out [dict create cell [get_property NAME $c] ref [get_property REF_NAME $c] reconf 0 blackbox 1]
        }
        return $out
    }

    # The S_BSCAN_* boundary ports (UltraScale+) an RP cell has. Returns the list of missing port names.
    proc dfx_bscan_missing {cell} {
        variable bscan_ports
        set have {}
        foreach p [get_pins -quiet -of_objects [get_cells $cell]] { lappend have [get_property REF_PIN_NAME $p] }
        return [lmap n $bscan_ports {if {[lsearch -exact $have $n] >= 0} continue; set n}]
    }

    # Versal DFX insertion: a debug core lives in one region, the RP that holds its probe nets or the static part. connect_debug_port refuses
    # to join a net to a core of another region (Chipscope 16-461), and the clock of an RP core must also come from the RP side.
    # The RP cell path that owns a hierarchical name, or "" for the static region. Longest match wins.
    proc dfx_region {name rps} {
        set best ""
        foreach r $rps { if {[string first "$r/" $name] == 0 && [string length $r] > [string length $best]} { set best $r } }
        return $best
    }

    # The name of a clock net as the RP sees it: of all segments (names) of the net, the one under the RP with the fewest levels,
    # for example design_1_i/rp1/s_axi_aclk for the static net design_1_i/clk_wizard_0_clk_out1. "" when the net has no name under the RP.
    proc dfx_rp_clock {rp clknet} {
        set best ""; set bd 99999
        set segs [get_nets -quiet -segments [get_nets $clknet]]
        if {[llength $segs] == 0} { return "" }
        foreach nm [get_property NAME $segs] {
            if {[string first "$rp/" $nm] != 0} continue
            set d [llength [split $nm /]]
            if {$best eq "" || $d < $bd || ($d == $bd && [string compare $nm $best] < 0)} { set bd $d; set best $nm }
        }
        return $best
    }

    # Delete a core that apply left half built (no error if it is not there).
    proc dfx_drop {name} {
        catch {delete_debug_core [get_debug_cores -quiet $name]}
    }

    # Vivado 2026.2 and newer accept create_debug_core in an UltraScale+ DFX design; 2026.1 and older refuse it (17-69).
    # Compared numerically on `version -short`. A version that cannot be read (no such command, odd text) counts as "not supported", so
    # the refusal stays.
    proc dfx_insert_supported {} {
        if {[catch {version -short} v]} { return 0 }
        if {![regexp {^\s*(\d+)\.(\d+)} $v -> maj min]} { return 0 }
        scan $maj %d maj; scan [string trimleft $min 0] %d min
        if {$min eq ""} { set min 0 }
        return [expr {$maj > 2026 || ($maj == 2026 && $min >= 2)}]
    }
    # Debug infrastructure the design already carries, as {cores ports}: cores found by existing_debug_cores (the cores this run created
    # are not counted) and the partitions that have at least one S_BSCAN port. e16 (2026-10-30): on 2026.2 and HEAD the insertion
    # worked end to end only where both lists are empty (FU1).
    proc dfx_debug_infra {} {
        set mine [created_list]
        set cores {}
        # Once a core of ours exists, Vivado also lists the debug hub it made (dbg_hub, findings row 9). A plan is refused early when a hub was
        # already there, so a hub next to a core of ours is ours.
        foreach e [existing_debug_cores] {
            if {[lsearch -exact $mine [lindex $e 0]] >= 0} continue
            if {[llength $mine] > 0 && [regexp {(^|/)dbg_hub$} [lindex $e 0]]} continue
            lappend cores [lindex $e 0]
        }
        set ports {}
        foreach r [dfx_cells] { set c [dict get $r cell]; if {[llength [dfx_bscan_missing $c]] < 12} { lappend ports $c } }
        return [list $cores $ports]
    }
    # The skill goes on in an UltraScale+ DFX design only when Vivado accepts the insertion (2026.2 and newer) AND the design carries no
    # debug infrastructure yet. Anything else keeps the 2026.1 refusals.
    proc dfx_insert_proceeds {} {
        if {![dfx_insert_supported]} { return 0 }
        return [expr {[join [dfx_debug_infra] ""] eq ""}]
    }
    proc dfx_insert_note {} {
        return "NOTE D01 Vivado [version -short] accepts debug insertion in an UltraScale+ DFX design (2026.1 refused it, Common 17-69), so the skill goes on. Verified for a KU5P design with no debug ports or cores before the insertion, in software through route, pr_verify and bitstream, and on KCU116, VCU108 and VCU128 boards with the full image and five partial swaps. Not verified: other boards (anything but KCU116, VCU108 and VCU128), other UltraScale+ parts, designs that already carry debug ports or cores (this skill refuses those), a second RM under a core that probes RM-internal nets (the link fails, Netlist 29-77), project mode."
    }

    # Print one fact line per reconfigurable cell. Silent on a design that has none.
    proc dfx_report {arch} {
        variable facts
        set rps [dfx_cells]
        dict set facts dfx_cells [lmap r $rps {dict get $r cell}]
        foreach r $rps {
            set c [dict get $r cell]
            set nd [llength [get_debug_cores -quiet -filter "NAME =~ \"${c}/*\""]]
            set nleaf [llength [get_cells -quiet -hier -filter "IS_PRIMITIVE && NAME =~ \"${c}/*\""]]
            set kind [expr {[dict get $r reconf] ? "reconfigurable partition (HD.RECONFIGURABLE=1)" : "black box cell (HD.RECONFIGURABLE not set)"}]
            set bb [expr {[dict get $r blackbox] ? "black box: yes (no module linked)" : "black box: no, $nleaf leaf cells inside"}]
            if {$arch eq "versal"} { set dbg "debug cores inside: $nd" } else {
                set miss [dfx_bscan_missing $c]
                set dbg "S_BSCAN ports: [expr {12 - [llength $miss]}] of 12[expr {[llength $miss] ? " (missing [join $miss {, }])" : ""}], debug cores inside: $nd"
            }
            say "DFX $kind $c: ref=[dict get $r ref], $bb, [expr {[dfx_is_empty_rp $r] ? "no logic of its own (greybox or black box)" : "has logic"}], $dbg"
        }
    }

    # A partition with no logic of its own: a black box, or a greybox (Vivado fills a greybox with HD_PR_Connection_* tie-off cells only).
    proc dfx_is_empty_rp {r} {
        if {[dict get $r blackbox]} { return 1 }
        set leaves [get_cells -quiet -hier -filter "IS_PRIMITIVE && NAME =~ \"[dict get $r cell]/*\""]
        foreach l $leaves { if {![string match "*/HD_PR_Connection_*" [get_property NAME $l]]} { return 0 } }
        return 1
    }

    # Which configuration of the project is open, and is it the parent or a child? Returns {config role run}, or "" when it cannot be told
    # (a checkpoint with no project, no configuration matches the open design, or two do). "" never blocks anything.
    # The open design has no property that names its configuration (current_pr_config says the parent's, whatever is open), so the
    # configuration is found from what the design holds: the REF_NAME of each partition cell is the RM that the configuration lists in
    # PARTITION_CELL_RMS ({cell:rm ...}); a greybox configuration lists the cell in GREYBOX_CELLS and its cell has no logic and a REF_NAME that no
    # configuration lists. The run of the configuration (PR_CONFIGURATION) is a child when its PARENT is an implementation run. In a
    # generated project impl_1 has PARENT synth_1, so "no PARENT" does not tell the parent.
    proc dfx_open_config {} {
        if {[catch {set cfgs [get_pr_configurations -quiet]}] || [llength $cfgs] == 0} { return "" }
        set rps [dfx_cells]
        if {[llength $rps] == 0} { return "" }
        set listed {}
        foreach cfg $cfgs { foreach pair [get_property PARTITION_CELL_RMS $cfg] { lappend listed [lindex [split $pair :] 1] } }
        set hit {}
        foreach cfg $cfgs {
            set rm [dict create]
            foreach pair [get_property PARTITION_CELL_RMS $cfg] { lassign [split $pair :] c m; dict set rm $c $m }
            set grey [get_property GREYBOX_CELLS $cfg]
            set ok 1
            foreach r $rps {
                set c [dict get $r cell]; set ref [dict get $r ref]
                if {[dict exists $rm $c]} { if {$ref ne [dict get $rm $c]} { set ok 0 } } \
                elseif {[lsearch -exact $grey $c] >= 0} { if {[lsearch -exact $listed $ref] >= 0 || ![dfx_is_empty_rp $r]} { set ok 0 } } \
                else { set ok 0 }
            }
            if {$ok} { lappend hit $cfg }
        }
        if {[llength $hit] != 1} { return "" }
        set cfg [lindex $hit 0]
        set run [lindex [get_runs -quiet -filter "PR_CONFIGURATION == $cfg"] 0]
        if {$run eq ""} { return "" }
        set par [get_property PARENT $run]
        set role [expr {$par ne "" && [get_property IS_IMPLEMENTATION [get_runs $par]] ? "child" : "parent"}]
        return [list $cfg $role $run]
    }

    # Debug is "wanted" in a partition when a spec item targets it: a resolved net or register lies under the RP cell, or the item
    # text names the RP (a greybox has no nets to resolve). Items that resolve outside every RP target the static part.
    # An item is a plain name, or the internal item filt:<expression> that ila::_plan adds for -filter (rule V8; a request with a filter and
    # no names comes here too). The origin labels reg:<cell> and net:<name> of the resolved pairs are internal tags (N6) and stay as they are.
    # UltraScale+: a targeted RP without the 12 S_BSCAN ports is a blocker (ILA_BLOCKED_DFX_NO_BSCAN).
    # Versal: a targeted RM with no AXI Debug Hub inside, or a static target with no hub outside the RPs, is a blocker (ILA_BLOCKED_DFX_NO_HUB).
    # A request that targets only empty partitions has nothing to insert: returns "nothing". Otherwise returns "".
    # Stops before anything is created. Reads the design only. Says what the user must change in their source and adds nothing.
    proc dfx_gate {items} {
        variable bscan_ports
        set rps [dfx_cells]
        if {[llength $rps] == 0} { return "" }
        set arch [get_property ARCHITECTURE [get_parts [get_property PART [current_design]]]]
        set names [lmap r $rps {dict get $r cell}]
        set rxs [lmap c $names {format "(^|\[:/*\\s\])[rx_quote $c](\[:/\]|\$)" }]
        set hit_rp {}; set hit_static 0; set per_item {}
        foreach item $items {
            set irp {}; set ist 0
            for {set k 0} {$k < [llength $names]} {incr k} { if {[regexp [lindex $rxs $k] $item]} { lappend irp [lindex $names $k] } }
            foreach pair [resolve_item $item] {
                lassign $pair net origin
                set nm [get_property NAME $net]
                set owner ""
                foreach c $names { if {[string first "$c/" $nm] == 0 || [string first "reg:$c/" $origin] == 0} { set owner $c; break } }
                if {$owner eq ""} { set ist 1 } else { lappend irp $owner }
            }
            lappend per_item [list [lsort -unique $irp] $ist]
            foreach c $irp { if {[lsearch -exact $hit_rp $c] < 0} { lappend hit_rp $c } }
            if {$ist} { set hit_static 1 }
        }
        # Versal: a static core added while a child configuration is open is dropped by the project run (E1, 2026-10-29): the child run
        # does not read the static debug XDC. Refuse before anything is created.
        if {$arch eq "versal" && $hit_static} {
            set oc [dfx_open_config]
            if {$oc ne "" && [lindex $oc 1] eq "child"} {
                set fix "Static cores must be added while the parent configuration is open and the parent run re-implemented; the child run does not read the static debug XDC, so a static core added here would be silently dropped."
                set cfgname [lindex $oc 0]
                say "BLOCKED DFX: the request probes the static part, and the open configuration $cfgname is a child (run [lindex $oc 2])."
                say "REQUIRED CHANGE: $fix"
                return -code error "ILA_BLOCKED_DFX_STATIC_IN_CHILD: the request probes the static part while the child configuration $cfgname is open. $fix"
            }
        }
        set msgs {}; set fix ""
        if {$arch ne "versal"} {
            set usp_ok [dfx_insert_proceeds]
            if {$usp_ok && [llength $hit_rp] > 0} { say [dfx_insert_note] }
            foreach r $rps {
                if {$usp_ok} break   ;# 2026.2 and newer, no debug infrastructure yet: the S_BSCAN ports are not needed (E10)
                set c [dict get $r cell]
                if {[lsearch -exact $hit_rp $c] < 0 || [dfx_is_empty_rp $r]} continue
                set miss [dfx_bscan_missing $c]
                if {[llength $miss] == 0} continue
                lappend msgs "reconfigurable partition $c has [expr {12 - [llength $miss]}] of the 12 S_BSCAN ports."
                say "BLOCKED DFX: [lindex $msgs end] Missing: [join $miss {, }]."
            }
            set code ILA_BLOCKED_DFX_NO_BSCAN
            set fix "Vivado does not insert debug cores into a reconfigurable partition, so the ILA must be instantiated in the RM source, and the partition needs the 12 ports [join $bscan_ports {, }] with exactly these names (S_BSCAN_tdo is the only output). The source must change: add the ports to every RM of the partition, leave them open (or 0) on the partition instance in the static source, instantiate the ILA in the RM, then re-synthesize the static design and every RM. Without the ports an ILA in the RM has no path to a debug hub, and ports alone do not enable insertion: the ILA must be in the RM source too. The change touches the static design, so run the parent and every child configuration again. I will not add them."
        } else {
            set code ILA_BLOCKED_DFX_NO_HUB
            foreach r $rps {
                set c [dict get $r cell]
                if {[lsearch -exact $hit_rp $c] < 0 || [dfx_is_empty_rp $r]} continue
                if {[llength [get_cells -quiet -hier -filter "REF_NAME =~ *dbg_hub* && NAME =~ \"$c/*\""]] == 0} {
                    lappend msgs "reconfigurable module in $c has no AXI Debug Hub."
                    say "BLOCKED DFX: [lindex $msgs end]"
                }
            }
            # A module-only request needs the static hub too: the module hub is reached through it (Mike, 2026-11-01). A greybox needs none.
            set mod_real 0
            foreach r $rps { if {[lsearch -exact $hit_rp [dict get $r cell]] >= 0 && ![dfx_is_empty_rp $r]} { set mod_real 1 } }
            if {$hit_static || $mod_real} {
                set inside [lmap c $names {format {NAME !~ "%s/*"} $c}]
                if {[llength [get_cells -quiet -hier -filter "REF_NAME =~ *dbg_hub* && [join $inside { && }]"]] == 0} {
                    lappend msgs "the static part has no AXI Debug Hub[expr {$hit_static ? "" : ", so a hub inside a module cannot be reached"}]."
                    say "BLOCKED DFX: [lindex $msgs end]"
                }
            }
            set fix "The source must change: put an AXI Debug Hub in the static part and in each RM of the partition (and in each RM of the parent configuration), connected to the CIPS through the NoC. Without it the ILA cannot be reached from the debugger. I will not add it."
        }
        if {[llength $msgs] > 0} {
            say "REQUIRED CHANGE: $fix"
            return -code error "$code: [join $msgs { }] $fix"
        }
        # nothing blocks: if every item targets only empty partitions, there is nothing to insert
        set empty {}
        foreach r $rps { if {[dfx_is_empty_rp $r]} { lappend empty [dict get $r cell] } }
        set only_empty [expr {[llength $items] > 0}]
        foreach pi $per_item {
            lassign $pi irp ist
            if {$ist || [llength $irp] == 0} { set only_empty 0; break }
            foreach c $irp { if {[lsearch -exact $empty $c] < 0} { set only_empty 0 } }
        }
        if {$only_empty} { return "nothing" }
        # 2026.2 and newer with debug infrastructure already in the design: Vivado accepts the insertion, the skill does not (e16).
        if {$arch ne "versal" && !$usp_ok && [dfx_insert_supported]} {
            lassign [dfx_debug_infra] cores ports
            set have {}
            if {[llength $cores] > 0} { lappend have "debug cores [join $cores {, }]" }
            if {[llength $ports] > 0} { lappend have "S_BSCAN ports on [join $ports {, }]" }
            # e16 and e17 measured designs with a debug hub or core. A design with S_BSCAN ports and no core or hub was not run: say so.
            set unmeasured [expr {[llength $cores] == 0 ? " Not measured: a design with partition ports and no debug core or hub was never run, so the skill refuses it to stay on the measured side." : ""}]
            set why "the design already carries debug infrastructure ([join $have {; }]).$unmeasured Vivado [version -short] would accept the insertion, but in measured runs on Vivado 2026.2 (KU5P) designs with a hub or core failed after it while the same flow without the insertion passed. Cause, measured: adding a core makes opt_design regenerate the existing debug hub without its 12 BSCAN ports to the partition. Directly, opt_design stops with Netlist 29-77; otherwise the partition's S_BSCAN pins stay unconnected (Chipscope 16-361) and the flow stops later, at opt_design (Opt 31-65) or in the grey child at update_design -buffer_ports (Common 17-39)."
            set fix "Instantiate the ILA in the module source (the debug instantiation flow) and re-synthesize, or give me the design before any debug port or core is added. I will not add them."
            say "BLOCKED DFX: this is the skill's refusal, not Vivado's: $why"
            say "REQUIRED CHANGE: $fix"
            return -code error "ILA_BLOCKED_DFX_UNSUPPORTED: this is the skill's refusal, not Vivado's: $why Required change: $fix"
        }
        return ""
    }

    # M04 (was the refusal N20): a net on the boundary of a reconfigurable partition (DFX). UG909: HD.RECONFIGURABLE sets DONT_TOUCH on the RP cell and its
    # interface nets. Reads the netlist only: a net counts when it has DONT_TOUCH and one of its pins belongs to a reconfigurable
    # cell. Returns the reason, or "" when the net is not one. One helper, called from the plan loop, which turns it into a note. Cheap on a flat design.
    proc dfx_boundary_reason {net} {
        variable dfx_rpset
        if {![info exists dfx_rpset]} {
            set dfx_rpset {}
            foreach c [get_cells -quiet -hier -filter {HD.RECONFIGURABLE == 1}] { lappend dfx_rpset [get_property NAME $c] }
        }
        if {[llength $dfx_rpset] == 0} { return "" }
        if {[get_property DONT_TOUCH $net] ni {1 true TRUE}} { return "" }
        set names {}
        foreach s [get_nets -quiet -segments $net] { lappend names $s }
        if {[llength $names] == 0} { set names [list $net] }
        foreach s $names {
            foreach p [get_pins -quiet -of_objects $s] {
                set c [get_cells -quiet -of_objects $p]
                if {$c ne "" && [get_property NAME $c] in $dfx_rpset} {
                    return "[get_property NAME $net] is on the boundary of the reconfigurable partition [get_property NAME $c]. UG909: HD.RECONFIGURABLE sets DONT_TOUCH on the partition cell and its interface nets, and this net has DONT_TOUCH. Vivado accepted such nets in a Versal DFX run, so the net is probed. If Vivado refuses it at apply, probe a register inside the module instead."
                }
            }
        }
        return ""
    }

    # M05: a net inside a reconfigurable partition that is not on its boundary. A static core that probes it makes Vivado add one port to the
    # partition per net, so the static design links only the RM that was probed (e16, e17: Netlist 29-77, 24 ports missing for 24 inside nets;
    # a request on boundary nets only linked the second RM). Returns the partition cell, or "" (flat design, boundary net, static net).
    proc dfx_inside_reason {net} {
        variable dfx_rpset
        if {[dfx_boundary_reason $net] ne ""} { return "" }   ;# also fills dfx_rpset
        set nm [get_property NAME $net]
        set under ""
        foreach rp $dfx_rpset { if {[string first "$rp/" $nm] == 0} { set under $rp; break } }
        if {$under eq ""} { return "" }
        # A partition output net is named under the partition too and has no DONT_TOUCH on that name (e17: count[24..31]): it is a
        # boundary net when any segment of it has a pin on the partition cell.
        set segs [get_nets -quiet -segments $net]
        if {[llength $segs] == 0} { set segs [list $net] }
        foreach s $segs {
            foreach p [get_pins -quiet -of_objects $s] {
                set c [get_cells -quiet -of_objects $p]
                if {$c ne "" && [get_property NAME $c] in $dfx_rpset} { return "" }
            }
        }
        return $under
    }
}
