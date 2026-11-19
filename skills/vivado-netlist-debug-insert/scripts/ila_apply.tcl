# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_apply.tcl: apply, core creation, -replace_existing with restore, reset. Sourced by ila_insert.tcl; every proc here was moved from it without edits.

namespace eval ila {
    # Vivado reports unrelated failures under one message id: 12-727 has at least five texts, and 17-69 is a wrapper for any failed command.
    # So the cause is read from the text after the id, never from the id. Returns the kind: no_cips, no_hub, dfx, name_clash or other.
    proc classify_create_error {err} {
        if {[regexp -nocase {no versal_cips IP|fabric reset enabled to support auto debug hub} $err]} { return no_cips }
        if {[regexp -nocase {debug hub can not be inserted in RP region} $err]} { return no_hub }
        if {[regexp -nocase {reconfigurable partition.{0,80}Debug insertion is not currently supported} $err]} { return dfx }
        if {[regexp -nocase {Can't name new debug core.{0,80}already used} $err]} { return name_clash }
        return other
    }

    # Turn a create_debug_core failure into the error message the library returns, and say the blocker. Vivado's own text always follows.
    proc create_failure {err} {
        switch [classify_create_error $err] {
            no_cips {
                say "BLOCKED FB-V01: Vivado found no versal_cips IP in the design, so it cannot insert a debug hub."
                return "ILA_BLOCKED_NO_CIPS: Vivado cannot insert a debug hub because the design has no versal_cips IP. Required change: the design source needs a CIPS or PS Wizard with at least one PL (fabric) reset enabled. I will not add one. Vivado said: $err"
            }
            no_hub {
                say "BLOCKED DFX: Vivado cannot create a debug core in a region that has no debug hub."
                return "ILA_BLOCKED_DFX_NO_HUB: Vivado cannot create a core here because the partition or module has no debug hub (Everest debug hub can not be inserted in RP region). Required change: a hub must exist in that partition or module. Put an AXI Debug Hub in the module source. A greybox has no hub and cannot take an ILA. I will not add it. Vivado said: $err"
            }
            dfx {
                set rpnames [lmap r [dfx_cells] {dict get $r cell}]
                say "BLOCKED DFX: Vivado does not insert debug cores into a design that contains a reconfigurable partition."
                return "ILA_BLOCKED_DFX_UNSUPPORTED: Vivado refuses debug insertion in a design with a reconfigurable partition ([join $rpnames {, }]). Required change: [dfx_unsupported_change $rpnames] Vivado said: $err"
            }
        }
        return "ILA_CREATE_FAILED: $err"
    }

    # The required change for ILA_BLOCKED_DFX_UNSUPPORTED, from the facts of this design: the family decides the advice, ports and cores
    # that are already there are said to be there. UltraScale+ hubs are inferred by Vivado; Versal hubs are AXI Debug Hubs the user places.
    proc dfx_unsupported_change {rpnames} {
        set arch [get_property ARCHITECTURE [get_parts [get_property PART [current_design]]]]
        set t "Vivado does not insert debug cores into reconfigurable partitions (UG909 Configuration Debugging)."
        if {$arch eq "versal"} {
            append t " Instantiate the ILA in the module source. Hubs on Versal are AXI Debug Hubs that you place: put one in each module that holds a core."
        } else {
            append t " Instantiate the ILA in the module source; the partition needs the 12 S_BSCAN ports and Vivado infers the hubs."
        }
        foreach c $rpnames {
            if {$arch ne "versal"} {
                set miss [dfx_bscan_missing $c]
                if {[llength $miss] == 0} {
                    append t " Partition $c already has the 12 S_BSCAN ports: do not add them."
                } else {
                    append t " Partition $c has [expr {12 - [llength $miss]}] of the 12 S_BSCAN ports (missing [join $miss {, }])."
                }
            }
            set have [lmap e [existing_debug_cores] {expr {[string first "$c/" [lindex $e 0]] == 0 ? [lindex $e 0] : [continue]}}]
            if {[llength $have] > 0} { append t " Partition $c already holds [llength $have] debug core(s): [join $have {, }]." }
        }
        if {$arch ne "versal"} { append t " If every net to probe is in the static part, the static-first route can still do it when the user gives the static checkpoint from before the partition was flagged (references/routes.md)." }
        append t " I will not add them."
        return $t
    }

    # create_debug_core with a free name. A name clash (Vivado 12-727 "Can't name new debug core 'x'. That name is already used.")
    # happens when the name belongs to something get_debug_cores does not list, such as a core a saved RM constraint file made at open.
    # Then take the next free name. Returns the name that was created. Any other failure is returned as an error with Vivado's text.
    proc create_core_free {name type taken} {
        set base [regsub {[0-9]+$} $name ""]
        set idx [expr {[regexp {([0-9]+)$} $name -> n] ? [scan $n %d] : 0}]
        if {$base eq $name} { set base "${name}_" }
        for {set tries 0} {$tries < 100} {incr tries} {
            if {[lsearch -exact $taken $name] < 0 && ![catch {create_debug_core $name $type} err]} { return $name }
            if {[lsearch -exact $taken $name] < 0 && [classify_create_error $err] ne "name_clash"} { return -code error $err }
            incr idx
            set name "$base$idx"
            while {[get_debug_cores -quiet $name] ne ""} { incr idx; set name "$base$idx" }
        }
        return -code error "no free core name found after 100 tries (last $name)"
    }

    # The cores -replace_existing deletes, as Vivado returns them: the user's u_ila_* cores, and in a DFX design the ones inside a
    # partition too (*/u_ila_*). plan and apply both ask this one proc (review g03 C-04).
    proc replace_targets {dfx} {
        return [concat [get_debug_cores -quiet u_ila_*] [expr {$dfx ? [get_debug_cores -quiet */u_ila_*] : {}}]]
    }
    proc replace_target_names {dfx} {
        set out {}
        foreach c [replace_targets $dfx] { lappend out [get_property NAME $c] }
        return $out
    }
    # The names a plan with this replace flag leaves out of the budget, the hub count and N20: the cores apply deletes, and none when replace is 0.
    # The three procs keep their one-argument signatures and ask the design whether it is a DFX one themselves (review g03 C-04).
    proc replace_deleted {replace} {
        return [expr {$replace ? [replace_target_names [expr {[llength [dfx_cells]] > 0}]] : {}}]
    }
    # Is a core with this name pattern in the design, other than the cores named in skip (the ones a replace deletes)?
    proc name_taken {pat skip} {
        foreach c [get_debug_cores -quiet $pat] { if {[lsearch -exact $skip [get_property NAME $c]] < 0} { return 1 } }
        return 0
    }

    # What it takes to build a core again, read before -replace_existing deletes it: its options, and for each port its options and the
    # nets on it, by leaf name (the read-back check) and by full name (the connect). whole is 0, and why says so, when a port's nets cannot
    # be read back exactly (a gap, fewer nets than the width, or full names that do not end in the leaf names, bit for bit).
    proc core_recipe {name dfx} {
        set c [get_debug_cores $name]
        # report_debug_core prints a net by its leaf name (count[0] for u_a/count[0], e90), and a leaf name can fit many nets (obud: 24 state[0]).
        # -full_path prints the full name of each bit's net, in the same bit order (measured 2026.2: 25 of 25 obud ports, 10 of 10 usp ports).
        set rep [report_debug_core -return_string]
        set repf [report_debug_core -full_path -return_string]
        set props {}
        foreach p [lsort [list_property $c]] { if {$p ni {CLASS NAME UUID}} { lappend props $p [get_property $p $c] } }
        set ports {}
        set whole 1
        set why ""
        # Ports in number order (clk first, then probe0, probe1, ...): restore_core makes a missing probe port with create_debug_port,
        # which takes the next number, so probe10 must not come before probe2.
        set byname [dict create]
        foreach pt [get_debug_ports -quiet -of_objects $c] { dict set byname [file tail [get_property NAME $pt]] $pt }
        set probes [lsearch -all -inline -regexp [dict keys $byname] {^probe[0-9]+$}]
        set order [concat [lsearch -all -inline -not -regexp [dict keys $byname] {^probe[0-9]+$}] [port_order $probes]]
        foreach pn $order {
            set pt [dict get $byname $pn]
            set pp {}
            foreach p [lsort [list_property $pt]] { if {$p ni {CLASS NAME}} { lappend pp $p [get_property $p $pt] } }
            set nets [connected_nets $name $pn $dfx $rep]
            set full [connected_nets $name $pn $dfx $repf]
            if {[dict exists $pp PORT_WIDTH]} { set w [dict get $pp PORT_WIDTH] } elseif {[dict exists $pp port_width]} { set w [dict get $pp port_width] } else { set w "" }
            if {[llength $nets] > 0 && [llength $nets] != $w} { set whole 0; set why "port $pn lists [llength $nets] nets for a width of $w"; set nets {}; set full {} }
            if {[lmap x $full {file tail $x}] ne $nets} { set whole 0; set why "port $pn: its full net names do not match its leaf names"; set full {} }
            lappend ports [list $pn $pp $nets $full]
        }
        return [dict create name $name props $props ports $ports whole $whole why $why]
    }
    # Build the core of a recipe again. Returns the problems found (an empty list: it is back, and every option and net reads back as it was).
    proc restore_core {r dfx} {
        set name [dict get $r name]
        # a core that was not read back exactly is not built from half of it
        if {![dict get $r whole]} { return [list "it was not read back exactly before the delete: [dict get $r why]"] }
        if {[catch {create_debug_core $name ila} err]} { return [list "create_debug_core: $err"] }
        set bad {}
        set c [get_debug_cores $name]
        foreach {p v} [dict get $r props] { if {[catch {set_property $p $v $c} err]} { lappend bad "$p: $err" } }
        foreach pr [dict get $r ports] {
            lassign $pr pn pp nets full
            if {[llength [get_debug_ports -quiet $name/$pn]] == 0} {
                if {![string match probe* $pn] || [catch {create_debug_port $name probe} err]} { lappend bad "port $pn is missing"; continue }
            }
            set port [get_debug_ports $name/$pn]
            foreach {p v} $pp { if {[catch {set_property $p $v $port} err]} { lappend bad "$pn $p: $err" } }
            if {[llength $nets] > 0} {
                # the full names, looked up exactly: each must be found
                set objs [get_nets -quiet $full]
                if {[llength $objs] != [llength $full]} { lappend bad "$pn: [llength $full] nets were read before the delete, [llength $objs] found by name"; continue }
                if {[catch {connect_debug_port $name/$pn $objs} err]} { lappend bad "$pn: $err" }
            }
        }
        foreach {p v} [dict get $r props] { if {[catch {get_property $p $c} got] || ![prop_same $v $got]} { lappend bad "$p does not read back as $v" } }
        foreach pr [dict get $r ports] {
            lassign $pr pn pp nets
            if {[llength $nets] > 0 && [connected_nets $name $pn $dfx] ne $nets} { lappend bad "$pn nets differ from the old core" }
        }
        return $bad
    }
    # apply failed after -replace_existing deleted cores: remove what this run made, build the deleted cores again from their recipes, and
    # put the replay, the delete record and the created list back as they were. Returns the sentence that goes on the error.
    proc rollback_replace {recipes created0 replay0 replaced0 dfx} {
        variable replay
        variable replaced
        variable facts
        set names [lmap r $recipes {dict get $r name}]
        set keep [lmap c $created0 {expr {[lsearch -exact $names $c] >= 0 ? [continue] : $c}}]
        foreach c [created_list] {
            if {[lsearch -exact $keep $c] < 0 && [llength [get_debug_cores -quiet $c]] > 0} { catch {delete_debug_core [get_debug_cores $c]} }
        }
        set replay $replay0
        set replaced $replaced0
        dict set facts created_cores [join $created0 " "]
        set back {}
        set lost {}
        foreach r $recipes {
            set name [dict get $r name]
            # a core that is still here was never deleted (the run stopped before its turn): leave it alone
            if {[llength [get_debug_cores -quiet $name]] > 0} continue
            # a raise while it is built again (Vivado refusing a command) is a failed restore too: the half-built core is deleted below
            if {[catch {restore_core $r $dfx} bad]} { set bad [list "Vivado stopped while it was built again: [lindex [split $bad \n] 0]"] }
            if {[llength $bad] == 0} { lappend back $name; continue }
            lappend lost "$name ([join $bad {; }])"
            catch {delete_debug_core [get_debug_cores -quiet $name]}
            set line "delete_debug_core \[get_debug_cores [list $name]\]"
            lappend replay $line
            if {[lsearch -exact $created0 $name] < 0} { lappend replaced $line } else { forget_created $name }
        }
        set txt ""
        if {[llength $back] == 0 && [llength $lost] == 0} { append txt " No old core had been deleted when it failed." }
        if {[llength $back] > 0} { append txt " The old core(s) [join $back {, }] that replacing the existing cores had deleted were put back as they were." }
        if {[llength $lost] > 0} { append txt " The old core(s) [join $lost {, }] could NOT be put back. Nothing was saved: reopen the checkpoint to get them back." }
        say [string trim $txt]
        return $txt
    }

    # ---- 5. apply --------------------------------------------------------
    proc apply {} {
        set created [flat _apply]
        request_lines probed   ;# every name asked for and what was built for it, after the nets Vivado dropped (N99)
        next_steps   ;# printed only after a successful apply: a failed one raised above
        return $created
    }
    proc _apply {} {
        variable plan
        variable facts
        variable replay
        variable apply_failed
        input_gate apply   ;# a design check_input refused, or not the one it accepted, is not touched (before apply_failed is set: nothing was changed)
        set apply_failed 1
        if {![dict exists $plan groups] || [llength [dict get $plan groups]] == 0} {
            if {[dict exists $plan rejected] && [llength [lsearch -inline -all -index 1 [dict get $plan rejected] N20]] > 0} {
                set n20 [llength [lsearch -inline -all -index 1 [dict get $plan rejected] N20]]
                set lead [expr {$n20 == [llength [dict get $plan rejected]] ? "every requested net is already probed by an existing debug core" : "the requested nets that could be probed are already probed by an existing debug core; the others were refused for other reasons"}]
                return -code error "ILA_NOTHING_INSERTED: $lead ($n20 nets, listed in the rejections). Nothing was inserted. Tell the user. Plan again allowing a second probe only if they want one on those nets."
            }
            return -code error "ILA_NO_PLAN: there is no plan to apply: plan first and fix the rejections (AI only: ila::plan)"
        }
        if {![dict get $plan budget_ok]} { return -code error "ILA_OVER_BUDGET: [expr {[dict get $plan opts memtype] eq "uram" ? [dict get $plan uram_tiles_needed] : [dict get $plan bram_tiles_needed]}] tiles needed, [expr {[dict get $plan opts memtype] eq "uram" ? [dict get $plan uram_tiles_free] : [dict get $plan bram_tiles_free]}] free[expr {[dict exists $plan budget_region] && [dict get $plan budget_region] ne "" ? " in [expr {[dict get $plan budget_region] eq "static" ? "the static part" : "partition [dict get $plan budget_region] (its pblock)"}]" : ""}]" }
        set dfx [expr {[dict exists $plan dfx] && [dict get $plan dfx]}]
        set recipes {}
        set do_delete 0
        if {[dict exists $plan replace_existing] && [dict get $plan replace_existing]} {
            # Every check that can refuse comes before the first delete (review g03 C-03): a planned name that a core which stays already holds.
            set targets [replace_target_names $dfx]
            foreach g [dict get $plan groups] {
                set name [dict get $g name]
                if {[lsearch -exact $targets $name] < 0 && [get_debug_cores -quiet $name] ne ""} { return -code error "ILA_NAME_IN_USE: core $name exists, and replacing the existing cores keeps it (it deletes only the u_ila_* cores). Nothing was deleted. Plan again: the new plan picks a free name (AI only: ila::plan)." }
            }
            # What it takes to build the old cores again, in case the new ones cannot be made after the delete.
            foreach tn $targets {
                if {[catch {core_recipe $tn $dfx} r]} { set r [dict create name $tn whole 0 why "Vivado would not tell its options and nets: $r"] }
                lappend recipes $r
            }
            variable replaced
            set replay0 $replay
            set replaced0 $replaced
            set created0 [created_list]
            set marker0 [marker_read]   ;# the ownership record before the delete, put back if the run fails
            set do_delete 1
        }
        # The deletes and the create are one run: a failure in either puts the deleted cores back (B2-05).
        set rc [catch {
            if {$do_delete} {
                foreach c [replace_targets $dfx] {
                    # read the name first: a deleted Vivado object prints as null (e29, B5)
                    set cname [get_property NAME $c]
                    say "replacing the existing core $cname: it is deleted before the new probe set is created"
                    delete_debug_core $c
                    # the delete is part of the run: replaying insertion.tcl on the pristine checkpoint must remove the old core too
                    lappend replay "delete_debug_core \[get_debug_cores [list $cname]\]"
                    # a core this session made is not in the pristine checkpoint; only the user's own core needs the delete after a reset
                    if {[lsearch -exact [created_list] $cname] < 0} { variable replaced; lappend replaced "delete_debug_core \[get_debug_cores [list $cname]\]" }
                    forget_created $cname
                }
            }
            _apply_create $dfx
        } res opts]
        if {$rc && [llength $recipes] > 0} { marker_restore $marker0 }   ;# the ownership record as it was, before the rollback: a core it cannot put back is dropped again there
        if {$rc && [llength $recipes] > 0} { append res [rollback_replace $recipes $created0 $replay0 $replaced0 $dfx] }
        if {!$rc} { set apply_failed 0 }
        return -options $opts $res
    }
    # The part of apply that builds the new cores (the cores -replace_existing deleted are already gone).
    proc _apply_create {dfx} {
        variable plan
        variable facts
        variable replay
        # A debug hub that is already here (before our first create_debug_core) is the user's: set_hub_clock leaves its clock alone.
        # Once this session has created a core, a hub is the one that core caused, so it is not pre-existing.
        variable hub_preexisting
        set hub_preexisting [expr {[llength [created_list]] == 0 && ([get_debug_cores -quiet dbg_hub] ne "" || [get_debug_ports -quiet dbg_hub/clk] ne "" || [llength [get_cells -quiet -hier -filter {REF_NAME =~ *dbg_hub*}]] > 0)}]
        set created {}
        set built [dict create]
        # Vivado puts the debug hub on the clock of the first ILA whose clk is connected (measured, UltraScale+). Create and connect
        # the cores whose clock source is free first, so the hub lands on a free clock. The order inside each part is the plan order. Names stay as planned.
        set order {}
        set rest {}
        set gi 0
        foreach g [dict get $plan groups] {
            if {[dict get $g clock_verdict] eq "free"} { lappend order [list $gi $g] } else { lappend rest [list $gi $g] }
            incr gi
        }
        foreach ig [concat $order $rest] {
            lassign $ig gi g
            set name [dict get $g name]
            if {[lsearch -exact $created $name] < 0 && [get_debug_cores -quiet $name] ne ""} { return -code error "ILA_NAME_IN_USE: core $name exists; a reused name would replace it. Remove the cores this session added and begin again first (AI only: ila::reset)." }
            set ports [dict get $g ports]   ;# {type nets} per probe port, one per signal and type (unit_ports); the nets below are all of them
            set nets [concat {*}[lmap p $ports {lindex $p 1}]]
            set combinational 0
            foreach n $nets { if {[lsearch -exact [dict get $plan combnets] $n] >= 0} { set combinational 1; break } }
            # Vivado can silently skip nets on connect. Read back; if some were skipped, drop them (reported as N99)
            # and build the core again with the nets that connected, once.
            for {set attempt 0} {$attempt < 2} {incr attempt} {
                set want $name
                set rc [catch {create_core_free $name ila $created} made]
                if {$rc} { return -code error [create_failure $made] }
                note_created $made   ;# recorded now, so a failed apply or a second apply leaves a true list
                if {$made ne $want} {
                    say "core name $want is already used by something in the design, so this core is $made"
                    set name $made
                    dict set g name $name
                }
                set c [get_debug_cores $name]
                set op [dict get $plan opts]
                set pipe [dict get $op pipe]
                if {$pipe < 0} { set pipe [expr {$combinational ? 1 : 0}] }   ;# the wizard's rule: one stage when a probed net is not a register output
                set tf {0 false 1 true}
                set mucnt [mu_count [dict get $op adv] [dict get $op strg]]
                if {[dict exists $op comparators]} { set mucnt [mu_count [dict get $op adv] [dict get $op strg] [dict get $op comparators]] }   ;# ila::plan -comparators
                set coreprops {}
                set memprops {}
                if {[dict exists $op memtype] && [dict get $op memtype] eq "uram"} { set memprops {C_MEMORY_TYPE 1} }   ;# after C_DATA_DEPTH: Vivado checks the depth
                foreach {p v} [list C_DATA_DEPTH [dict get $plan depth] {*}$memprops C_TRIGIN_EN [dict get $tf [dict get $op trigin]] C_TRIGOUT_EN [dict get $tf [dict get $op trigout]] \
                                     C_ADV_TRIGGER [dict get $tf [dict get $op adv]] C_INPUT_PIPE_STAGES $pipe C_EN_STRG_QUAL [dict get $tf [dict get $op strg]] \
                                     ALL_PROBE_SAME_MU true ALL_PROBE_SAME_MU_CNT $mucnt] {
                    if {[catch {set_checked $c $p $v} perr]} { catch {delete_debug_core $c}; forget_created $name; return -code error $perr }
                    lappend coreprops "set_property $p $v \[get_debug_cores [list $name]\]"
                }
                # In a DFX design a failed connect must not leave the half-built core behind (an empty core would still be built by opt_design).
                set crc [catch {
                    set_checked [get_debug_ports $name/clk] port_width 1
                    if {[get_nets -quiet [dict get $g clocknet]] eq ""} {
                        # replace_existing deleted the old core: its clk-port net name is gone. Re-resolve from a probed register's clock pin.
                        set drv [get_cells -of_objects [get_pins -of_objects [get_nets [lindex $nets 0]] -filter {DIRECTION == OUT}]]
                        dict set g clocknet [get_nets -of_objects [get_pins -of_objects $drv -filter {IS_CLOCK}]]
                        say "clock net re-resolved after the old core was deleted: [dict get $g clocknet]"
                    }
                    connect_debug_port $name/clk [get_nets [dict get $g clocknet]]
                    set pi 0
                    foreach p $ports {
                        lassign $p ptype pnets
                        if {$pi > 0} { create_debug_port $name probe }   ;# the core has probe0; every other port is made here
                        set_checked [get_debug_ports $name/probe$pi] port_width [llength $pnets]
                        connect_debug_port $name/probe$pi [get_nets $pnets]
                        if {$ptype ne "data_and_trigger"} { set_checked [get_debug_ports $name/probe$pi] PROBE_TYPE [string toupper $ptype] }   ;# data_and_trigger is Vivado's default
                        incr pi
                    }
                } cerr copts]
                if {$crc} {
                    if {$dfx} { dfx_drop $name; forget_created $name; say "apply failed on $name, the half-built core was deleted: $cerr" } elseif {[string match ILA_* $cerr]} { catch {delete_debug_core $c}; forget_created $name }   ;# a refused or ignored property (set_checked); a failed connect on a flat design leaves the core, as before
                    return -options $copts $cerr
                }
                set missing {}
                set mports {}
                # a net of a core in a partition is listed without the partition path
                set pfx [expr {$dfx && [dict get $g region] ne "" ? "[dict get $g region]/" : ""}]
                set pi 0
                set byport [report_port_nets $name $dfx]   ;# one report for every port: a core has one port per signal, and a report per port grew with both
                foreach p $ports {
                    set pnets [lindex $p 1]
                    set have [expr {[dict exists $byport probe$pi] ? [dict get $byport probe$pi] : {}}]
                    set pmiss {}
                    if {[llength $have] != [llength $pnets]} {
                        # The report names a net the way Vivado does, which may differ from the name we asked for, so compare
                        # names only when the counts differ.
                        foreach n $pnets {
                            set nn [expr {$pfx ne "" && [string first $pfx $n] == 0 ? [string range $n [string length $pfx] end] : $n}]
                            if {[lsearch -exact $have $nn] < 0} { lappend pmiss $n }
                        }
                        if {[llength $pmiss] == 0} { set pmiss [lrange $pnets [llength $have] end] }
                    }
                    if {[llength $pmiss] > 0} {
                        say "N99: Vivado did not connect [llength $pmiss] net(s) to $name/probe$pi: $pmiss"
                        lappend mports $name/probe$pi
                        lappend missing {*}$pmiss
                    }
                    incr pi
                }
                if {[llength $missing] == 0} break
                foreach n $missing { dict lappend plan rejected [list $n N99 "Vivado silently skipped this net when connecting it to a probe port (see the Vivado message above). It was dropped from the ILA."] }
                delete_debug_core $c
                forget_created $name
                # drop the skipped nets; a port left with no net is gone, so the ports after it move up
                set ports [lmap p $ports {
                    set keep [lmap n [lindex $p 1] {expr {[lsearch -exact $missing $n] >= 0 ? [continue] : $n}}]
                    if {[llength $keep] == 0} continue
                    list [lindex $p 0] $keep
                }]
                set nets [concat {*}[lmap p $ports {lindex $p 1}]]
                if {[llength $nets] == 0} break
                if {![ports_store_data $ports]} { return -code error "ILA_CONNECT_MISMATCH: Vivado did not connect the nets of every port that stores data on $name, so the core would have no probe that stores data and Vivado would fail at opt_design (IP_Flow 19-3478). The core $name was deleted again: it is not in the design." }
            }
            if {[llength $nets] == 0} { say "no connectable nets left for $name; core skipped"; continue }
            if {[llength $missing] > 0} { return -code error "ILA_CONNECT_MISMATCH: [join $mports {, }] still has unconnected nets after one retry: $missing. The core $name was deleted again: it is not in the design." }
            dict set g nets $nets; dict set g width [llength $nets]
            if {[dict exists $g ports]} { dict set g ports $ports; dict set g stored [ports_stored $ports] }   ;# what connected, in the ports it connected to
            dict set built $gi $g
            lappend replay "create_debug_core [list $name] ila"
            foreach cp $coreprops { lappend replay $cp }
            lappend replay "set_property port_width 1 \[get_debug_ports [list $name/clk]\]"
            lappend replay "connect_debug_port [list $name/clk] \[get_nets [replay_braced [dict get $g clocknet]]\]"
            set pi 0
            foreach p $ports {
                lassign $p ptype pnets
                if {$pi > 0} { lappend replay "create_debug_port [list $name] probe" }
                lappend replay "set_property port_width [llength $pnets] \[get_debug_ports [list $name/probe$pi]\]"
                lappend replay "connect_debug_port [list $name/probe$pi] \[get_nets [replay_braced {*}$pnets]\]"
                if {$ptype ne "data_and_trigger"} { lappend replay "set_property PROBE_TYPE [string toupper $ptype] \[get_debug_ports [list $name/probe$pi]\]" }
                incr pi
            }
            say "created $name: clk=[dict get $g clocknet] [llength $ports] probe port[expr {[llength $ports] == 1 ? "" : "s"}]: [port_words $ports]; depth=[dict get $plan depth]"
            if {!$dfx} { marker_write $name }   ;# the ownership record ila::change checks (flat designs only)
            lappend created $name
        }
        # the plan keeps its own order (the names), whatever order the cores were created in
        set newgroups {}
        foreach gi [lsort -integer [dict keys $built]] { lappend newgroups [dict get $built $gi] }
        dict set plan groups $newgroups
        if {[llength $created] == 0} { return -code error "ILA_NOTHING_INSERTED: every probe net was refused. See the rejections." }
        set_hub_clock $newgroups
        hub_clock_report
        return $created
    }

    # Delete the cores this session created, so a corrected plan can be applied. A core the design already had (the user's own u_ila_*, from
    # their XDC) is not touched, and neither is any core made by an earlier session. The replay is cleared: it described the cores just deleted.
    proc reset {} {
        variable facts
        variable replay
        set gone {}
        foreach c [created_list] {
            if {[llength [get_debug_cores -quiet $c]] == 0} continue
            delete_debug_core [get_debug_cores $c]
            lappend gone $c
        }
        marker_drop $gone
        # Begin again: the facts (part, file sizes, finish record) and the log describe a build that no longer exists. A verify result
        # or a file size from before the reset must not reach ila::report. The "removed" line below is the first line of the new log.
        variable said
        set verdict [expr {[dict exists $facts input_verdict] ? [list input_verdict [dict get $facts input_verdict]] : {}}]   ;# the verdict of check_input outlives a reset: plan and apply still honour it
        set facts [dict create created_cores "" {*}$verdict]
        set said {}
        cache_reset
        variable apply_failed
        set apply_failed 0   ;# no cores are left, so there is no half-built design (finish then says ILA_NOT_APPLIED)
        variable replaced
        set replay $replaced
        say "removed [llength $gone] core(s) this session created[expr {[llength $gone] ? " ([join $gone {, }])" : ""}]; [llength [get_debug_cores -quiet]] debug core(s) left (not created by this session, not touched)"
        # a core -replace_existing deleted is not brought back here: say so, so nobody reads "removed" as an undo
        set stay {}
        foreach l $replaced { if {[regexp {get_debug_cores ([^\]\s]+)\]} $l -> n]} { lappend stay $n } }
        if {[llength $stay] > 0} { say "the core(s) [join $stay {, }] that replacing the existing cores deleted stay deleted: beginning again does not bring them back. To get them back, reopen the checkpoint." }
    }
}
