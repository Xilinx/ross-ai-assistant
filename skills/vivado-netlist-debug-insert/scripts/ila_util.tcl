# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_util.tcl: say, flat, session state, option checks, quoting helpers. Sourced by ila_insert.tcl; every proc here was moved from it without edits.

namespace eval ila {
    # The session state is made once. A second source of this file (an agent that loads the library again after an error) keeps it.
    # A deliberate fresh beginning is ila::reset (removes the cores this session made, and forgets the facts and the log) or ila::check_input.
    variable plan; variable facts; variable outdir; variable replay; variable replaced; variable said; variable apply_failed
    if {![info exists plan]}   { set plan [dict create] }     ;# last plan: groups, rejected, warnings
    if {![info exists facts]}  { set facts [dict create] }    ;# facts gathered by the procs (ila::facts)
    if {![info exists outdir]} { set outdir "" }
    if {![info exists replay]} { set replay {} }              ;# the Tcl commands apply ran (ila::replay)
    if {![info exists replaced]} { set replaced {} }          ;# the delete lines of the user's own cores that -replace_existing removed (and did not put back): reset and a second check_input keep them (the cores stay deleted)
    if {![info exists said]}   { set said {} }                ;# every message say printed in this design's session; ila::numbers_check (ila_numbers.tcl) reads it
    if {![info exists apply_failed]} { set apply_failed 0 }   ;# 1 from the start of an apply until it ends well: a finish must not run on the half-built design of a failed apply (review g03 C-07)
    proc say {msg} { variable said; lappend said $msg; puts "ILA: $msg" }

    # Vivado runs a command typed at the interactive prompt (-mode tcl, which is how the MCP server drives it) with per-command
    # bookkeeping that grows with the number of Vivado commands that top-level command has run so far. A proc that issues many
    # thousands of get_* calls then slows down quadratically (3000 get_property calls: 0.2 s, then 1.3 s on the sixth batch,
    # against a flat 0.07 s each under `source`). So the public procs run their body under `source` of a one-line temp file.
    # Measured in docs/plan-performance.md. If no temp file can be made, the body runs directly (correct, only slower).
    variable flat_args {}
    variable flat_busy 0
    proc flat {cmd args} {
        variable flat_args
        variable flat_busy
        if {$flat_busy || [catch {set fh [file tempfile fname ila_flat_]}]} { return [$cmd {*}$args] }
        set flat_args $args
        puts $fh "set ::ila::flat_rc \[catch {ila::$cmd {*}\$::ila::flat_args} ::ila::flat_res ::ila::flat_opt\]"
        close $fh
        set flat_busy 1
        set src [catch {source $fname} serr]
        set flat_busy 0
        catch {file delete $fname}
        if {$src} { return -code error "ILA_FLAT_SOURCE_FAILED: $serr" }
        variable flat_res
        variable flat_opt
        return -options $flat_opt $flat_res
    }

    # Refuse an option name the proc does not know, or an option with no value. `array set o $args` alone takes any name, so a
    # typo such as -dpth was ignored and the default was used without a word. Valid options come from the defaults of the proc.
    proc check_options {proc_name code valid args_list} {
        set valid [lsort $valid]
        if {[llength $args_list] % 2} {
            return -code error "$code: an option of the call has no value (AI only: $proc_name: the option [lindex $args_list end] has no value; valid options: [join $valid {, }]). Nothing was done."
        }
        foreach {k v} $args_list {
            if {[lsearch -exact $valid $k] < 0} {
                return -code error "$code: the call used an option the skill does not have (AI only: $proc_name does not know the option $k; valid options: [join $valid {, }]). Nothing was done."
            }
        }
    }

    # The cores this session created, as a list. The key is missing until the first apply.
    proc created_list {} {
        variable facts
        return [expr {[dict exists $facts created_cores] ? [dict get $facts created_cores] : {}}]
    }
    # Record a core at the moment it is created (so a failed or repeated apply leaves a true list), and forget it when it is deleted.
    proc note_created {name} {
        variable facts
        set l [created_list]
        if {[lsearch -exact $l $name] < 0} { lappend l $name }
        dict set facts created_cores [join $l " "]
        mark_design
        # a new core has not been through opt_design: an earlier finish no longer covers the design, so implement must run it again
        dict unset facts finished_outdir
    }
    # Tie the created cores to the design that holds them (review A finding 1). Vivado gives neither a design nor a core an id of its own (a core's
    # UUID reads all zeros, measured 2026.1), and a name is no proof: u_ila_0 is the default name of this skill and of the Vivado wizard. So apply
    # marks the design it changes with a design property holding a token of this session, and design_marked_p asks whether the open design carries it.
    # Measured on 2026.1 and 2026.2: a design opened later does not have the property, and a checkpoint saved after the mark keeps it.
    # When the property cannot be written no token is recorded and the old name test stays.
    proc design_token_read {} {
        if {[catch {get_property -quiet ILA_SKILL_TOKEN [current_design]} t]} { return "" }
        return $t
    }
    proc mark_design {} {
        variable facts
        if {![dict exists $facts design_token]} { dict set facts design_token "ila_[clock milliseconds]_[expr {int(rand() * 1000000)}]" }
        set tok [dict get $facts design_token]
        if {[design_token_read] eq $tok} return
        if {[catch {create_property -quiet -class design ILA_SKILL_TOKEN; set_property ILA_SKILL_TOKEN $tok [current_design]}]} { dict unset facts design_token }
    }
    # 1 when the open design is the one this session changed, or when no mark was possible (then the caller falls back on names).
    proc design_marked_p {} {
        variable facts
        if {![dict exists $facts design_token]} { return 1 }
        return [expr {[design_token_read] eq [dict get $facts design_token]}]
    }
    proc forget_created {name} {
        variable facts
        set l [lmap c [created_list] {expr {$c eq $name ? [continue] : $c}}]
        dict set facts created_cores [join $l " "]
        marker_drop [list $name]   ;# the ownership record (ila_change.tcl) forgets a deleted core at every delete site
    }

    # A new design starts from nothing: facts, plan and replay of an earlier design in the same session must not leak into it.
    proc session_reset {} {
        variable facts
        variable plan
        variable replay
        variable replaced
        variable said
        set facts [dict create]
        set plan [dict create]
        set replay {}
        set replaced {}
        set said {}
        cache_reset
    }

    # The plan caches (the debug core cells, the bits of a vector, the reconfigurable partitions, the names suggest compared) are built
    # lazily from the open design. They are dropped when a plan starts, with the session and with ila::reset (ila::eco_probes keeps its own copies and drops those),
    # so a new design, a new core or an implemented checkpoint never reads the lists of an earlier one.
    proc cache_reset {} {
        variable suggest_seen
        variable vec_cache
        variable dfx_rpset
        variable core_cells
        unset -nocomplain suggest_seen vec_cache dfx_rpset core_cells
    }

    # One escape for text that goes into a regexp or a glob: every character that is special in a Tcl ARE or in string match gets a backslash
    # (A-09). The six hand-written maps it replaces each covered a different subset. In a glob, a backslash before an ordinary character is that character.
    proc rx_quote {s} { return [regsub -all {[][{}()*+?.^$|\\]} $s {\\&}] }
    # Words of a replay line (D-02). The names come from the netlist, so each one is written with list quoting: a name with a brace, bracket,
    # dollar, semicolon, backslash, space or quote reads back as the same name and never ends the word early.
    # get_nets takes a LIST of patterns (measured, 2026.1 and 2026.2: a name is quoted twice, once as a list element, once as the word).
    # get_debug_cores and get_debug_ports take ONE pattern: a double-quoted name is not found, so these get a single [list $name].
    # replay_braced: the braced form of a net list ({clk}, {{a[0]} {b[0]}}), with the braces the replay always had when nothing needs quoting.
    proc replay_braced {args} {
        set inner [list {*}$args]
        set q [list $inner]
        return [expr {$q eq $inner ? "\{$q\}" : $q}]   ;# nothing needed quoting: keep the braces the replay always had
    }

    # ---- 3b. read back every property set on a debug core or port (LF2) ----------------------------------------------
    # Vivado accepts some sets and keeps the old value: on Versal `C_TRIGIN_EN true` returns OK and reads false, and
    # `C_NUM_OF_PROBES 5` reads 2 (live, 2026.1). A set that returned OK proves nothing, so every set is read back.
    # Two values are the same when they are the same integer, the same truth value (1/true, 0/false, any case) or the same text.
    proc prop_same {want got} {
        set w [string tolower [string trim $want]]
        set g [string tolower [string trim $got]]
        if {[string is integer -strict $w] && [string is integer -strict $g]} { return [expr {$w == $g}] }
        set b {1 true 0 false true true false false}
        if {[dict exists $b $w] && [dict exists $b $g]} { return [expr {[dict get $b $w] eq [dict get $b $g]}] }
        return [expr {$w eq $g}]
    }

    # Set <prop> on <obj>, read it back. Raises ILA_OPTION_REFUSED (Vivado refused the set) or ILA_PROPERTY_MISMATCH (it did not stick).
    proc set_checked {obj prop val} {
        if {[catch {set_property $prop $val $obj} err]} { return -code error "ILA_OPTION_REFUSED: $prop $val: $err" }
        set got [get_property $prop $obj]
        if {![prop_same $val $got]} {
            return -code error "ILA_PROPERTY_MISMATCH: $prop on [get_property NAME $obj]: set to $val, Vivado reads back '$got'. Vivado accepted the set and ignored it, so this option cannot be used on this device."
        }
        return $got
    }
}
