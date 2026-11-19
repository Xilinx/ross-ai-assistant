# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# run_methodology_setup.tcl
# Purpose: the single Tcl file of the vivado-timing-methodology-checks skill. It defines
# every Tcl proc of the skill in the `methodology_check` namespace and is the
# Step 1 entry point: one source produces every file the rest of the skill depends
# on, so the skill text only needs to document the JSON schemas below instead of
# a sequence of commands.
#
# Usage (inside Vivado, with the design open, working directory at the project root):
#   source <skill_dir>/scripts/run_methodology_setup.tcl
# where <skill_dir> is the directory that contains the skill's SKILL.md. Source it by
# absolute path; do not cd into <skill_dir>, which would move ./methodology_results.
# Sourcing this file (1) loads all procs below and (2) runs the setup once,
# writing to ./methodology_results, resolved to an absolute path at source time. To use
# another output directory, set it BEFORE sourcing:
#   set ::methodology_out_dir /path/to/methodology_results
#   source <skill_dir>/scripts/run_methodology_setup.tcl
# To re-run the setup later without re-sourcing:
#   methodology_check::run_methodology_setup ./methodology_results
#
# All procs MUST be called by their namespace-qualified name. Public procs
# (procs starting with `_` are private helpers and must not be called):
#   methodology_check::check_dont_touch_on_net          TIMING-14: DONT_TOUCH check on net segments / hierarchical pins
#   methodology_check::profile_constraints              Step 1: profile every constraint by ID (type, IP or not, value, from/to object classes)
#   methodology_check::resolve_clock_groups_override    TIMING-24: replace a two-clock set_clock_groups, keeping the budgeted crossing timed
#   methodology_check::resolve_datapath_only_override   TIMING-24: carve a set_max_delay -datapath_only out of a broad exception
#   methodology_check::run_methodology_setup            Step 1: produce every file the rest of the flow needs
#   methodology_check::timing18_summary                 TIMING-18: per-clock missing input/output delay table
#   methodology_check::trace_clock_back_to_source       CLOCK_TRACING.md: trace a pin back to its clock source port/pin
#
# Produces, under <out_dir> (default ./methodology_results):
#   methodology.json       - raw `report_methodology -json` output (Vivado's
#                             native format: tables."Report Summary".headers/data)
#   constraints.xdc         - unified constraints backup (`write_xdc -write_id`, plain XDC text; `# id:N` comments)
#   waivers.rpt             - methodology waivers present at setup
#                             (`report_waivers -type METHODOLOGY -no_header
#                             -show_msgs_with_no_waivers`; -no_header drops the
#                             Date/Host/Command lines that `write_waivers` and
#                             report_waivers' own header both carry, so the file
#                             is the same every run when nothing waiver-related
#                             changed and `diff -u` against waivers_after_fix.rpt
#                             shows only real differences)
#   tmp/command_history.rpt - `report_design_analysis -qor_summary` output (plain text
#                             Tool Option Summary table). Used by TIMING-14.md to
#                             determine whether `opt_design` has already run.
#   tmp/constraints_profile.json - one entry per constraint ID (the `# id:N` of constraints.xdc,
#                             the "constraint position" in methodology messages): type, IP or
#                             not, value, options, and the object classes of from/to/etc.
#                             Its "ip_instances" list is the IP-vs-user-RTL classification:
#                             a violation belongs to IP iff its hierarchy equals,
#                             or is nested under ("<entry>/"), one of these entries.
#                             (see methodology_check::profile_constraints).
#   methodology_index.json  - severity-ordered, reference-annotated work list
#                             (see process_methodology_json.py). Shape:
#       {
#         "source_file": <path to methodology.json>,
#         "report_header": {...},                      # passthrough from methodology.json
#         "severity_priority": ["Error","Critical Warning","Warning","Info"],
#         "ordered_rules":     [<rule entry>, ...],     # ALL rules, severity-ordered
#         "timing_work_list":  [<rule entry>, ...],     # subset: is_timing_id == true
#         "unsupported_rules": [<rule entry>, ...],     # subset: is_timing_id == false
#         "lookup_by_rule":    {"<RULE>": <rule entry>} # O(1) lookup by rule ID
#       }
#     where <rule entry> is:
#       {
#         "rule": "TIMING-1",            # check ID
#         "severity": "Critical Warning",
#         "severity_rank": 1,             # 0=Error,1=Critical Warning,2=Warning,3=Info,99=other
#         "description": "...",           # one-line description from the report
#         "count": 5,                     # number of violation instances for this rule
#         "is_timing_id": true,           # true only for TIMING-* rules (in scope for this skill)
#         "supported": true,              # same as is_timing_id today
#         "has_reference_file": true,     # whether <skill_dir>/references/<rule>.md exists
#         "reference_file": "references/TIMING-1.md",  # path if it exists, else null
#         "details_count": 5,             # total raw detail-string count for this rule
#         "details_preview": [...]        # first 5 raw detail strings only (not all)
#       }
#
# Note: `details_preview` is a preview, not the full violation list. If a rule
# has more than 5 instances and you need every instance's object/pin, re-parse
# `methodology.json` directly for that rule's full `details` array.

namespace eval methodology_check {
    # Captured at source time: `info script` is empty once sourcing has finished,
    # so it cannot be evaluated inside a proc when the proc is called later.
    variable script_dir [file dirname [file normalize [info script]]]

    # Capture endpoints of a clock = data input pins of the cells it clocks.
    proc _dpo_clk_endpoints {clk} {
        set sp [_dpo_clk_startpoints $clk]
        if {![llength $sp]} { return {} }
        return [get_pins -quiet -filter {IS_CLOCK==0 && DIRECTION==IN} \
                    -of [get_cells -quiet -of $sp]]
    }

    # Launch startpoints of a clock = leaf clock pins driven by the clock net.
    proc _dpo_clk_startpoints {clk} {
        set nets [get_nets -quiet -of [get_clocks -quiet $clk]]
        if {![llength $nets]} { return {} }
        return [all_fanout -flat -endpoints_only $nets]
    }

    # coll intersect the objects whose NAME is in `names`.
    proc _dpo_intersect {coll names} {
        if {![llength $coll] || ![llength $names]} { return {} }
        set expr {}
        foreach nm $names { lappend expr "NAME == \"$nm\"" }
        return [filter $coll [join $expr " || "]]
    }

    # coll minus the objects whose NAME is in `names` (uses filter; portable).
    proc _dpo_minus {coll names} {
        if {![llength $coll]} { return {} }
        if {![llength $names]} { return $coll }
        set expr {}
        foreach nm $names { lappend expr "NAME != \"$nm\"" }
        return [filter $coll [join $expr " && "]]
    }

    # Names of a collection as a plain Tcl list.
    proc _dpo_names {coll} {
        set out {}
        foreach o $coll { lappend out [get_property NAME $o] }
        return $out
    }

    # Resolve a -from/-to spec (clocks, cells, or pins) into a pin collection.
    # side is "from" (startpoints) or "to" (endpoints).
    proc _dpo_resolve {spec side} {
        if {[llength [set p [get_pins -quiet $spec]]]}  { return $p }
        if {[llength [set c [get_cells -quiet $spec]]]} {
            if {$side eq "from"} { return [get_pins -quiet -filter {IS_CLOCK} -of $c] }
            return [get_pins -quiet -filter {IS_CLOCK==0 && DIRECTION==IN} -of $c]
        }
        if {[llength [get_clocks -quiet $spec]]} {
            if {$side eq "from"} { return [_dpo_clk_startpoints $spec] }
            return [_dpo_clk_endpoints $spec]
        }
        return {}
    }

    # Object class of one -from/-to spec: "clock", "netlist" (cells, pins, ports, nets),
    # "mixed" (clocks together with netlist objects) or "none" (nothing resolves).
    proc _dpo_spec_class {spec} {
        set clk 0; set net 0
        foreach el $spec {
            if {[llength [get_clocks -quiet $el]]} {
                set clk 1
            } elseif {[llength [get_cells -quiet $el]] || [llength [get_pins -quiet $el]] \
                      || [llength [get_ports -quiet $el]] || [llength [get_nets -quiet $el]]} {
                set net 1
            }
        }
        if {$clk && $net} { return mixed }
        if {$clk} { return clock }
        if {$net} { return netlist }
        return none
    }

    # Directed crossings that a set_clock_groups -asynchronous cuts: every ordered pair of clocks
    # in two different groups, minus the budgeted pairs (the -from/-to of a
    # set_max_delay -datapath_only), which must stay timed.
    #   clock_group_crossings -groups {{A C} {B D}} [-budgets {{A B}}]  ->  {{A D} {C B} {B A} ...}
    proc clock_group_crossings {args} {
        array set opt {-groups {} -budgets {}}
        array set opt $args
        set out {}
        set n [llength $opt(-groups)]
        for {set i 0} {$i < $n} {incr i} {
            for {set j 0} {$j < $n} {incr j} {
                if {$i == $j} { continue }
                foreach x [lindex $opt(-groups) $i] {
                    foreach y [lindex $opt(-groups) $j] {
                        if {[lsearch -exact $opt(-budgets) [list $x $y]] >= 0} { continue }
                        lappend out [list $x $y]
                    }
                }
            }
        }
        return $out
    }

    # --- Collect the hierarchical (boundary) pins the segments pass through.
    # A hierarchical pin is a pin whose owning cell is NOT a primitive.
    proc _dt_collect_hier_pins {segments} {
        set all_pins [get_pins -quiet -of_objects $segments]
        set hier {}
        foreach p $all_pins {
            set cell [get_cells -quiet -of_objects $p]
            if {[llength $cell] == 0} { continue }
            # IS_PRIMITIVE==0 -> hierarchical cell -> this is a hierarchical pin.
            if {![_dt_is_set [_dt_prop $cell IS_PRIMITIVE]]} {
                lappend hier $p
            }
        }
        return [lsort -unique $hier]
    }

    # --- Resolve the starting object into the set of net segments to inspect.
    # Accepts a cell (uses its pins), a pin, or a net. Returns a list of net objects
    # (segments) with duplicates removed.
    proc _dt_collect_segments {from} {
        set pins {}

        # Try to interpret $from as cells, pins, or nets (quietly).
        set cells [get_cells -quiet $from]
        set inpins [get_pins -quiet $from]
        set innets [get_nets -quiet $from]

        if {[llength $cells] > 0} {
            # A cell (e.g. a LUT): start from all of its pins.
            set pins [get_pins -quiet -of_objects $cells]
        } elseif {[llength $inpins] > 0} {
            set pins $inpins
        } elseif {[llength $innets] > 0} {
            # Already a net: expand to all of its segments directly below.
            return [lsort -unique [get_nets -quiet -segments -of_objects $innets]]
        } else {
            error "check_dont_touch_on_net: '-from $from' did not resolve to a\
                   cell, pin, or net."
        }

        if {[llength $pins] == 0} {
            error "check_dont_touch_on_net: no pins found for '-from $from'."
        }

        # -segments walks up and down through hierarchy boundaries, returning every
        # net segment that is electrically the same signal.
        return [lsort -unique [get_nets -quiet -segments -of_objects $pins]]
    }

    # --- Is a design currently open?
    proc _dt_design_open {} {
        return [expr {![catch {get_property NAME [current_design]}]}]
    }

    # --- Emit a JSON array of records.
    proc _dt_emit_array {out records} {
        set last [expr {[llength $records] - 1}]
        for {set i 0} {$i < [llength $records]} {incr i} {
            set rec [lindex $records $i]
            set comma [expr {$i < $last ? "," : ""}]
            puts $out "      \{"
            puts $out "        \"name\": \"[_dt_json_escape [dict get $rec name]]\","
            puts $out "        \"dont_touch\": [expr {[dict get $rec dont_touch] ? "true" : "false"}],"
            puts $out "        \"mark_debug\": [expr {[dict get $rec mark_debug] ? "true" : "false"}],"
            puts $out "        \"blocking\": [expr {[dict get $rec blocking] ? "true" : "false"}]"
            puts $out "      \}$comma"
        }
    }

    # --- True if a property is effectively set/true (handles 1/TRUE/empty/0).
    proc _dt_is_set {val} {
        if {$val eq ""} { return 0 }
        return [expr {[string is boolean -strict $val] ? [expr {$val ? 1 : 0}] \
                                                       : ([string tolower $val] ne "0")}]
    }

    # --- Small JSON string escaper (handles characters that can appear in paths).
    proc _dt_json_escape {s} {
        set map [list \\ \\\\ \" \\\" \n \\n \r \\r \t \\t]
        return [string map $map $s]
    }

    # --- Best-effort property read (missing property -> empty string).
    proc _dt_prop {obj prop} {
        if {[catch {get_property $prop $obj} v]} { return "" }
        return $v
    }

    # --- Build a per-object record { name dont_touch mark_debug blocking }.
    proc _dt_record {obj} {
        set dt [_dt_prop $obj DONT_TOUCH]
        set md [_dt_prop $obj MARK_DEBUG]
        set dt_set [_dt_is_set $dt]
        set md_set [_dt_is_set $md]
        return [dict create \
            name       "$obj" \
            dont_touch $dt_set \
            mark_debug $md_set \
            blocking   $dt_set]
    }

    # --- (TIMING-11) Shrink a list of pin names to the fewest get_pins patterns.
    # Names that differ only in their last bus index (a_reg[0]/C ... a_reg[31]/C) become
    # one pattern (a_reg[*]/C), but only when get_pins expands that pattern to exactly
    # those pins. Otherwise the names stay listed one by one.
    proc _t11_compress {names} {
        set groups [dict create]
        foreach n [lsort -unique $names] {
            if {![regsub {\[[0-9]+\]((?:(?!\[[0-9]+\]).)*)$} $n {[*]\1} pat]} { set pat $n }
            dict lappend groups $pat $n
        }
        set out {}
        dict for {pat members} $groups {
            if {[llength $members] > 1 && [llength [get_pins -quiet $pat]] == [llength $members]} {
                lappend out $pat
            } else {
                foreach m $members { lappend out $m }
            }
        }
        return $out
    }

    # --- (TIMING-11) Group a pair map {key -> list of partners} by identical partner set.
    # Returns a list of {keys partners} records, one per distinct partner set.
    proc _t11_group {map} {
        set by_set [dict create]
        dict for {key partners} $map {
            dict lappend by_set [lsort -unique $partners] $key
        }
        set records {}
        dict for {partners keys} $by_set { lappend records [list [lsort $keys] $partners] }
        return [lsort -index 0 $records]
    }

    # --- (TIMING-11) 1 when the register that owns the pin is clocked by a clock in the list.
    # A data pin (D) carries no clock itself, so the clock comes from the cell's clock pin.
    proc _t11_on_clock {pin clks} {
        set cell [get_cells -quiet -of_objects [get_pins -quiet $pin]]
        if {![llength $cell]} { return 0 }
        foreach c [get_clocks -quiet -of_objects [get_pins -quiet -of_objects $cell -filter {IS_CLOCK}]] {
            if {[lsearch -exact $clks $c] >= 0} { return 1 }
        }
        return 0
    }

    # check_dont_touch_on_net -from <cell|pin|net> [-json <file>]
    # (TIMING-14) For a given starting object (a LUT/cell, a pin, or a net), collect the
    # full set of net *segments* (across hierarchy boundaries) and every
    # *hierarchical pin* those segments pass through, and report whether any
    # of them carry a DONT_TOUCH property.
    #
    # Why: A DONT_TOUCH on a net segment or a hierarchical pin prevents opt_design
    #      from absorbing/optimizing the logic on that net. For TIMING-14 (LUT on the
    #      clock tree), an inversion LUT1 is normally absorbed by opt_design and can be
    #      ignored -- UNLESS a DONT_TOUCH on the surrounding net segments or
    #      hierarchical pins blocks that optimization. This proc answers that
    #      question deterministically.
    #
    # What it checks, per object:
    #   * DONT_TOUCH  - the property that actually blocks optimization
    #   * MARK_DEBUG  - reported as informational (also keeps nets, for ILA/debug)
    #
    # Options:
    #   -from <obj>   Required. A cell (e.g. a LUT), a pin, or a net.
    #   -json <path>  Optional. Write a JSON report to this path.
    #
    # Requires an open design.
    # Return value: 1 if any blocking DONT_TOUCH was found (optimization blocked),
    #               0 if none were found (safe to ignore -- opt_design can absorb).
    proc check_dont_touch_on_net {args} {
        set from ""
        set json ""
        for {set i 0} {$i < [llength $args]} {incr i} {
            switch -- [lindex $args $i] {
                -from { set from [lindex $args [incr i]] }
                -json { set json [lindex $args [incr i]] }
                default {
                    error "check_dont_touch_on_net: unknown option [lindex $args $i]"
                }
            }
        }
        if {$from eq ""} {
            error "check_dont_touch_on_net: -from <cell|pin|net> is required."
        }
        if {![_dt_design_open]} {
            error "check_dont_touch_on_net: no open design."
        }

        set segments [_dt_collect_segments $from]
        set hier_pins [_dt_collect_hier_pins $segments]

        set net_records {}
        set n_net_blocking 0
        foreach seg $segments {
            set rec [_dt_record $seg]
            lappend net_records $rec
            if {[dict get $rec blocking]} { incr n_net_blocking }
        }

        set pin_records {}
        set n_pin_blocking 0
        foreach p $hier_pins {
            set rec [_dt_record $p]
            lappend pin_records $rec
            if {[dict get $rec blocking]} { incr n_pin_blocking }
        }

        set blocked [expr {($n_net_blocking + $n_pin_blocking) > 0}]

        # --- Console summary.
        puts "check_dont_touch_on_net: from=$from"
        puts "  net segments: [llength $segments] ([expr {$n_net_blocking}] with DONT_TOUCH)"
        puts "  hierarchical pins: [llength $hier_pins] ([expr {$n_pin_blocking}] with DONT_TOUCH)"
        puts "  => [expr {$blocked ? "BLOCKED (DONT_TOUCH present)" : "clear (no DONT_TOUCH)"}]"

        # --- Optional JSON report.
        if {$json ne ""} {
            set fh [open $json w]
            puts $fh "\{"
            puts $fh "  \"from\": \"[_dt_json_escape $from]\","
            puts $fh "  \"summary\": \{"
            puts $fh "    \"net_segments\": [llength $segments],"
            puts $fh "    \"net_segments_with_dont_touch\": $n_net_blocking,"
            puts $fh "    \"hierarchical_pins\": [llength $hier_pins],"
            puts $fh "    \"hierarchical_pins_with_dont_touch\": $n_pin_blocking,"
            puts $fh "    \"blocking\": [expr {$blocked ? "true" : "false"}]"
            puts $fh "  \},"
            puts $fh "  \"net_segments\": \["
            _dt_emit_array $fh $net_records
            puts $fh "  \],"
            puts $fh "  \"hierarchical_pins\": \["
            _dt_emit_array $fh $pin_records
            puts $fh "  \]"
            puts $fh "\}"
            close $fh
            puts "check_dont_touch_on_net: wrote $json"
        }

        return [expr {$blocked ? 1 : 0}]
    }

    # profile_constraints <xdc_file> <out_json> [wrapper_tokens]
    # Step 1: profile every constraint of constraints.xdc (replaces the old
    # parse_ip_scoping / ip_scoping_map.json). Text-only, no open design required.
    #
    # constraints.xdc is written by `write_xdc -write_id`, which puts `# id:N` before each
    # timing constraint. N is the "constraint position" that report_methodology messages
    # quote ("see constraint position 12"), so it is the main key of the profile. For each
    # ID the profile records (scripts/profile_constraints.py does the parsing):
    #   type / category   the command (create_clock, set_max_delay, ...) and its family
    #   file / line       original XDC file, and the line in constraints.xdc
    #   scope             the `current_instance` hierarchy in effect ('' = top level)
    #   is_ip/ip_instance IP constraint? IP iff the scope is, or is nested under, a
    #                     `current_instance` whose LAST path segment is an IP wrapper token
    #                     (`inst` = Verilog IP, `U0` = VHDL IP; case-insensitive)
    #   value             main number (period, delay, cycles, skew, latency, ...)
    #   options           flags and keyword options (-datapath_only, -setup, -name clk, ...)
    #   from/to/through/target/clock/source/groups
    #                     the class of the objects there (cell, pin, port, net, clock,
    #                     pblock, unresolved), the names, and whether they use wildcards
    # Constraints without an ID (e.g. set_property) are listed under "unnumbered".
    # The wrapper tokens are configurable via the third argument, e.g. {inst U0 u1}.
    # Called for you by run_methodology_setup (Step 1).
    #
    # Output: JSON, see the docstring of scripts/profile_constraints.py and SKILL.md.
    # Returns <out_json>.
    proc profile_constraints {xdc_file out_json {wrapper_tokens {inst U0}}} {
        variable script_dir
        if {![file exists $xdc_file]} {
            error "profile_constraints: constraints file not found: $xdc_file"
        }
        set script [file join $script_dir profile_constraints.py]
        puts [exec python3 $script $xdc_file $out_json {*}$wrapper_tokens]
        return $out_json
    }

    # resolve_datapath_only_override -big_from .. -big_to .. -big_type .. -dpo_from .. -dpo_to .. [-out <file>]
    # TIMING-24 CASE 2 automation (pin-level carve-out).
    #
    # A broad exception (a clock-to-clock set_false_path, or a set_clock_groups
    # -asynchronous) between BIG_FROM and BIG_TO overrides a more granular
    # set_max_delay -datapath_only whose endpoints (DPO_FROM / DPO_TO) are pins
    # or cells rather than whole clocks. Because set_false_path / set_clock_groups
    # outrank set_max_delay, the datapath_only budget is ignored.
    #
    # This proc rewrites the broad exception as point-to-point set_false_path
    # constraints that cover every crossing EXCEPT the datapath_only pins, so the
    # -datapath_only budget applies again and the two constraints no longer overlap.
    #
    # Set algebra (S = startpoints, E = endpoints; big = broad exception,
    # small = datapath_only):
    #
    #   keep = (S_big x E_big) \ (S_small x E_small)
    #        = ( S_big          x (E_big \ E_small) )
    #        U ( (S_big \ S_small) x  E_small        )
    #
    #   => two set_false_path commands, with no overlap on (S_small x E_small).
    #
    # Options: -big_from/-big_to/-dpo_from/-dpo_to <clocks|cells|pins>,
    #          -big_type false_path|clock_groups, [-out <file>]
    #
    # Default is a dry run: it prints the markup and the generated constraints.
    # With -out <file> (e.g. ./methodology_results/TIMING-24_<id>.xdc, one file per override: it is overwritten) the carve-out
    # constraints are written to that file; Step 6 reads TIMING-*.xdc files after
    # reset_timing. The proc never changes the open design: Vivado has no command
    # that deletes a single exception, so the broad false path / clock group must
    # be removed from constraints_after_fix.xdc by editing that file (TIMING-24).
    proc resolve_datapath_only_override {args} {
        array set opt {
            -big_from {} -big_to {} -big_type false_path
            -dpo_from {} -dpo_to {} -out {}
        }
        array set opt $args

        # TIMING-24 CASE 3: do not guess when clocks are mixed with cells/pins in one exception.
        foreach {nm a b} [list big $opt(-big_from) $opt(-big_to) dpo $opt(-dpo_from) $opt(-dpo_to)] {
            set ca [_dpo_spec_class $a]; set cb [_dpo_spec_class $b]
            if {$ca eq "mixed" || $cb eq "mixed" || ($ca ne "none" && $cb ne "none" && $ca ne $cb)} {
                error "TIMING-24 CASE 3 (user decision): the $nm exception mixes clocks with cells/pins\
(-from is $ca, -to is $cb). Show the user both exceptions and ask how to split them; do not generate constraints."
            }
        }
        if {$opt(-big_type) eq "clock_groups" && ([llength $opt(-big_from)] != 1 || [llength $opt(-big_to)] != 1)} {
            error "TIMING-24 CASE 3 (user decision): -big_type clock_groups needs exactly one clock in each of\
two groups. A larger set_clock_groups needs the user to decide (see clock_group_crossings)."
        }

        # 1. Enumerate coverage of the broad exception and the datapath_only.
        set S_big   [_dpo_resolve $opt(-big_from) from]
        set E_big   [_dpo_resolve $opt(-big_to)   to]
        set S_small [_dpo_resolve $opt(-dpo_from) from]
        set E_small [_dpo_resolve $opt(-dpo_to)   to]

        if {![llength $S_big] || ![llength $E_big]} {
            error "Could not resolve broad-exception endpoints from -big_from/-big_to"
        }
        if {![llength $S_small] || ![llength $E_small]} {
            error "Could not resolve datapath_only endpoints from -dpo_from/-dpo_to"
        }

        # 2. Mark up the overlap (which broad endpoints the datapath_only covers).
        set S_small_names [_dpo_names $S_small]
        set E_small_names [_dpo_names $E_small]
        set S_over [_dpo_intersect $S_big $S_small_names]
        set E_over [_dpo_intersect $E_big $E_small_names]
        puts "== Coverage markup =="
        puts [format "  broad startpoints : %d" [llength $S_big]]
        puts [format "  broad endpoints   : %d" [llength $E_big]]
        puts [format "  datapath_only startpoints covered : %d / %d" \
                  [llength $S_over] [llength $S_small]]
        puts [format "  datapath_only endpoints   covered : %d / %d" \
                  [llength $E_over] [llength $E_small]]

        # 3. Build the carve-out sets (no overlap on S_small x E_small).
        set E_keep [_dpo_minus $E_big $E_small_names]
        set S_keep [_dpo_minus $S_big $S_small_names]

        set cmds {}
        if {[llength $E_keep]} {
            lappend cmds [format "set_false_path -from \[get_pins \{%s\}\] -to \[get_pins \{%s\}\]" \
                              [join [_dpo_names $S_big] " "] [join [_dpo_names $E_keep] " "]]
        }
        if {[llength $S_keep]} {
            lappend cmds [format "set_false_path -from \[get_pins \{%s\}\] -to \[get_pins \{%s\}\]" \
                              [join [_dpo_names $S_keep] " "] [join [_dpo_names $E_small] " "]]
        }

        # A set_clock_groups also cut the reverse direction. Removing it would time B -> A, so keep it cut.
        if {$opt(-big_type) eq "clock_groups"} {
            lappend cmds [format "set_false_path -from \[get_clocks %s\] -to \[get_clocks %s\]" \
                              $opt(-big_to) $opt(-big_from)]
        }

        puts "== Generated carve-out constraints =="
        if {![llength $cmds]} {
            puts "  (none) datapath_only fully covers the broad exception; just remove the broad exception."
        }
        foreach c $cmds {
            puts [format "  %s%s" [string range $c 0 119] [expr {[string length $c] > 120 ? " ..." : ""}]]
        }

        if {$opt(-out) eq ""} {
            puts "== DRY RUN (nothing written). Re-run with -out <file> to write the constraints. =="
            return
        }
        set fh [open $opt(-out) w]
        puts $fh "# TIMING-24: point-to-point false paths that leave the set_max_delay -datapath_only budget in effect"
        foreach c $cmds { puts $fh $c }
        close $fh
        puts "== Wrote [llength $cmds] constraint(s) to $opt(-out) =="
        if {$opt(-big_type) eq "clock_groups"} {
            puts "NOTE: -big_type clock_groups. Now DELETE the set_clock_groups from constraints_after_fix.xdc."
            puts "      The reverse direction is kept false by the last constraint written above."
        } else {
            puts "NOTE: now remove the broad set_false_path (BIG_FROM -> BIG_TO) from constraints_after_fix.xdc."
        }
        puts "Then run Step 6; it reads constraints_after_fix.xdc and TIMING-*.xdc after reset_timing."
        return
    }

    # resolve_clock_groups_override -groups {{A} {B}} [-budgets {{A B}}] [-out <file>]
    # TIMING-24 CASE 1 when the overriding exception is a set_clock_groups -asynchronous and the
    # set_max_delay -datapath_only is clock to clock. It supports exactly two groups with one clock each.
    # The set_clock_groups is deleted, so every crossing it cut except the budgeted one is replaced by a
    # set_false_path. With groups {A} {B} and a budget A -> B that is only "set_false_path -from B -to A".
    # Anything larger (3+ groups, 2+ clocks in a group) is a user decision (TIMING-24 CASE 3): the proc
    # prints the constraints it WOULD generate as a proposal and stops with an error.
    # The proc never changes the open design.
    proc resolve_clock_groups_override {args} {
        array set opt {-groups {} -budgets {} -out {}}
        array set opt $args
        set cmds {}
        foreach pair [clock_group_crossings -groups $opt(-groups) -budgets $opt(-budgets)] {
            lappend cmds [format "set_false_path -from \[get_clocks %s\] -to \[get_clocks %s\]" \
                              [lindex $pair 0] [lindex $pair 1]]
        }
        set simple [expr {[llength $opt(-groups)] == 2}]
        foreach g $opt(-groups) { if {[llength $g] != 1} { set simple 0 } }
        if {!$simple} {
            puts "== Proposal only (needs the user to decide) =="
            foreach c $cmds { puts "  $c" }
            set sizes {}
            foreach g $opt(-groups) { lappend sizes [llength $g] }
            error "TIMING-24 CASE 3 (user decision): [llength $opt(-groups)] clock groups with\
[join $sizes +] clocks. Present the proposal above and ask the user; do not apply it."
        }
        puts "== Generated constraints (replace the set_clock_groups with these) =="
        foreach c $cmds { puts "  $c" }
        if {$opt(-out) eq ""} {
            puts "== DRY RUN (nothing written). Re-run with -out <file> to write the constraints. =="
            return
        }
        set fh [open $opt(-out) w]
        puts $fh "# TIMING-24: point-to-point false paths that replace a set_clock_groups; the budgeted crossing stays timed"
        foreach c $cmds { puts $fh $c }
        close $fh
        puts "== Wrote [llength $cmds] constraint(s) to $opt(-out) =="
        puts "NOTE: now DELETE the set_clock_groups from constraints_after_fix.xdc, then run Step 6."
        return
    }

    # run_methodology_setup [out_dir]
    # Step 1 entry point. Runs report_methodology, write_xdc,
    # report_design_analysis -qor_summary, the constraint profile (profile_constraints) and
    # process_methodology_json.py, and returns a dict of the files written.
    # Sourcing this file calls it once with ./methodology_results (or
    # $::methodology_out_dir if that global is set before sourcing).
    proc run_methodology_setup {{out_dir ./methodology_results}} {
        variable script_dir
        set skill_dir  [file dirname $script_dir]
        set refs_dir   [file join $skill_dir references]

        file mkdir $out_dir ${out_dir}/tmp

        set methodology_json  [file join $out_dir methodology.json]
        set constraints_xdc   [file join $out_dir constraints.xdc]
        set waivers_rpt       [file join $out_dir waivers.rpt]
        set command_history_rpt [file join $out_dir/tmp command_history.rpt]
        set profile_json      [file join $out_dir/tmp constraints_profile.json]
        set index_json        [file join $out_dir/tmp methodology_index.json]

        puts "run_methodology_setup: running report_methodology..."
        report_methodology -json $methodology_json

        puts "run_methodology_setup: writing unified constraints backup..."
        write_xdc -force -exclude_physical -write_id $constraints_xdc

        puts "run_methodology_setup: reporting methodology waivers..."
        # report_methodology already ran above, so waiver counts are fresh (report_waivers
        # warns and degrades its METHODOLOGY section to "re-run report" if waivers changed
        # since the last report_methodology/report_drc/report_cdc run). -no_header drops the
        # Date/Host/Command lines so this file is diff-clean against waivers_after_fix.rpt.
        catch {report_waivers -type METHODOLOGY -no_header -show_msgs_with_no_waivers -file $waivers_rpt}
        if {![file exists $waivers_rpt]} {
            set fh [open $waivers_rpt w]
            puts $fh "# No methodology waivers at setup"
            close $fh
        }

        puts "run_methodology_setup: capturing implementation command history..."
        report_design_analysis -qor_summary -file $command_history_rpt

        puts "run_methodology_setup: profiling constraints..."
        profile_constraints $constraints_xdc $profile_json

        puts "run_methodology_setup: building severity-ordered index..."
        set process_script [file join $script_dir process_methodology_json.py]
        set exec_result [exec python3 $process_script $methodology_json $refs_dir $index_json]
        puts $exec_result

        puts "run_methodology_setup: done. Files in $out_dir:"
        puts "  - $methodology_json"
        puts "  - $constraints_xdc"
        puts "  - $waivers_rpt"
        puts "  - $command_history_rpt"
        puts "  - $profile_json"
        puts "  - $index_json"

        return [dict create \
            methodology_json    $methodology_json \
            constraints_xdc     $constraints_xdc \
            waivers_rpt         $waivers_rpt \
            command_history_rpt $command_history_rpt \
            profile_json        $profile_json \
            index_json          $index_json]
    }

    # timing11_cell_constraints -delay <ns> [-from_clocks <list>] [-to_clocks <list>]
    #                           [-keep_cdc 0|1] [-json <file>] [-out <file>]
    # (TIMING-11, CASE 2A) Replace a clock-level `set_max_delay -datapath_only` on a
    # single clock with cell-level constraints, and write them to
    # ./methodology_results/TIMING-11.xdc so that every TIMING-11 warning that
    # constraint caused is cleared.
    #   methodology_check::timing11_cell_constraints -delay 4.000 -from_clocks clk -to_clocks clk
    # Reads every TIMING-11 start/endpoint pair from the methodology JSON (default
    # ./methodology_results/methodology.json, the full list, not the 5-item preview) and
    # keeps the pairs whose startpoint is on a -from_clocks clock and whose endpoint is
    # on a -to_clocks clock. For each pair it emits a plain `set_max_delay <ns>`
    # (no -datapath_only, so clock skew is analysed again). The pairs are grouped so a
    # constraint covers every pair of one startpoint set x endpoint set exactly: by
    # endpoint or by startpoint, whichever needs fewer constraints. Bus pins collapse to
    # `name[*]/pin` when that matches exactly those pins.
    # -keep_cdc 1 (default) also re-issues `-datapath_only` for endpoints that are
    # ASYNC_REG registers on a -to_clocks clock (a recognised synchronizer, a real CDC the
    # clock-level constraint was probably meant for). TIMING-11 does not report
    # those, and `-from [get_clocks] -to [get_cells]` does not trigger it.
    # The caller removes the clock-level constraint from constraints_after_fix.xdc.
    # Requires an open design. Returns a dict: pairs, constraints, keep_cdc, file.
    proc timing11_cell_constraints {args} {
        set dir [expr {[info exists ::methodology_out_dir] ? $::methodology_out_dir : "./methodology_results"}]
        set json $dir/methodology.json
        set out  $dir/TIMING-11.xdc
        set delay ""; set from_clks {}; set to_clks {}; set keep_cdc 1
        if {[llength $args] % 2} { error "timing11_cell_constraints: options come in pairs, e.g. -delay 4.000" }
        foreach {k v} $args {
            switch -- $k {
                -delay       { set delay $v }
                -from_clocks { set from_clks $v }
                -to_clocks   { set to_clks $v }
                -keep_cdc    { set keep_cdc $v }
                -json        { set json $v }
                -out         { set out $v }
                default { error "timing11_cell_constraints: unknown option $k" }
            }
        }
        if {$delay eq ""} { error "timing11_cell_constraints: -delay <ns> is required (the value in the clock-level set_max_delay)" }
        if {![file exists $json]} { error "timing11_cell_constraints: $json not found. Source run_methodology_setup.tcl (Step 1) first." }
        file mkdir [file dirname $out]
        set fh [open $json r]; set txt [read $fh]; close $fh

        # JSON text: "applied between <sp pin> and <ep pin>." with "/" escaped as "\/"
        set msg {A max delay constraint with -datapath_only has been applied between (\S+) and (\S+?)\. }
        set seen [dict create]; set by_ep [dict create]; set by_sp [dict create]
        set total 0
        foreach {all sp ep} [regexp -all -inline $msg $txt] {
            set sp [string map {\\/ /} $sp]; set ep [string map {\\/ /} $ep]
            incr total
            if {[dict exists $seen "$sp $ep"]} { continue }
            if {[llength $from_clks] && ![_t11_on_clock $sp $from_clks]} { continue }
            if {[llength $to_clks] && ![_t11_on_clock $ep $to_clks]} { continue }
            dict set seen "$sp $ep" 1
            dict lappend by_ep $ep $sp
            dict lappend by_sp $sp $ep
        }
        set npairs [dict size $seen]
        puts "timing11_cell_constraints: $total TIMING-11 instance(s) in $json, $npairs on the selected clock(s)"

        # Two groupings of the same pairs; keep the one with fewer constraints.
        set by_end   {}
        foreach r [_t11_group $by_ep] { lappend by_end [list [lindex $r 1] [lindex $r 0]] }   ;# {from to}
        set by_start [_t11_group $by_sp]                                                      ;# {from to}
        if {[llength $by_start] < [llength $by_end]} { set records $by_start } else { set records $by_end }

        set lines {}
        lappend lines "# TIMING-11: cell-level replacement for the clock-level"
        lappend lines "#   set_max_delay -datapath_only $delay -from \[get_clocks {$from_clks}\] -to \[get_clocks {$to_clks}\]"
        lappend lines "# Remove that constraint from constraints_after_fix.xdc. This file times the same $npairs"
        lappend lines "# start/endpoint pair(s) as plain set_max_delay (clock skew included, no -datapath_only)."
        lappend lines "# Generated by methodology_check::timing11_cell_constraints: [llength $records] constraint(s)."
        foreach r $records {
            lassign $r from_pins to_pins
            lappend lines "set_max_delay $delay -from \[get_pins {[_t11_compress $from_pins]}\] -to \[get_pins {[_t11_compress $to_pins]}\]"
        }

        set ncdc 0
        if {$keep_cdc && [llength $from_clks] && [llength $to_clks]} {
            # An endpoint TIMING-11 reported is not a recognised synchronizer: it already has
            # a plain constraint above, so it is left out of the -datapath_only list.
            set flagged [dict create]
            dict for {ep sps} $by_ep { dict set flagged [get_property NAME [get_cells -of_objects [get_pins $ep]]] 1 }
            set cdc {}
            foreach c [get_cells -quiet -hierarchical -filter {IS_SEQUENTIAL && ASYNC_REG == TRUE}] {
                set name [get_property NAME $c]
                if {[dict exists $flagged $name]} { continue }
                set ck [get_clocks -quiet -of_objects [get_pins -quiet -of_objects $c -filter {IS_CLOCK}]]
                foreach x $ck { if {[lsearch -exact $to_clks $x] >= 0} { lappend cdc $name; break } }
            }
            set ncdc [llength $cdc]
            if {$ncdc} {
                lappend lines "# Synchronizer endpoints (ASYNC_REG): a real CDC, keep -datapath_only on them"
                lappend lines "set_max_delay -datapath_only $delay -from \[get_clocks {$from_clks}\] -to \[get_cells {[lsort $cdc]}\]"
            }
        }

        set fh [open $out w]; puts $fh [join $lines "\n"]; close $fh
        puts "timing11_cell_constraints: $npairs pair(s) -> [llength $records] constraint(s), $ncdc synchronizer endpoint(s); wrote $out"
        return [dict create pairs $npairs constraints [llength $records] keep_cdc $ncdc file $out]
    }

    # timing18_summary [-json <methodology json>] [-out <report file>]
    # (TIMING-18) Summary table: one row per clock with period, waveform,
    # clock-tree info, and the number of ports missing set_input_delay /
    # set_output_delay relative to that clock. Requires an open design (for the
    # clock period / waveform / clock-tree columns).
    #   methodology_check::timing18_summary
    #   methodology_check::timing18_summary -json ./methodology_results/methodology.json
    # Prints the table and writes it to -out (default
    # ./methodology_results/timing18_summary.rpt). Returns the table text.
    #
    # The counts come from the TIMING-18 messages in the methodology JSON report,
    # so they match the methodology report exactly and waived violations (which
    # report_methodology leaves out of the JSON) are not counted. Default JSON:
    #   ./methodology_results/methodology_after_fix.json when it exists (after the
    #   Step 6 re-run), otherwise ./methodology_results/methodology.json (Step 1).
    # Ports are not queried for a delay: INPUT_DELAY/OUTPUT_DELAY are not valid
    # port properties, and check_timing skips ports that TIMING-18 reports
    # (for example unloaded inputs), so its counts would not match.
    proc timing18_summary {args} {
        set dir  [expr {[info exists ::methodology_out_dir] ? $::methodology_out_dir : "./methodology_results"}]
        set json ""
        set out  $dir/timing18_summary.rpt
        if {[llength $args] % 2} { error "timing18_summary: options come in pairs: -json <file> -out <file>" }
        foreach {k v} $args {
            switch -- $k {
                -json   { set json $v }
                -out    { set out $v }
                default { error "timing18_summary: unknown option $k (use -json <file> -out <file>)" }
            }
        }
        if {$json eq ""} {
            set json $dir/methodology_after_fix.json
            if {![file exists $json]} { set json $dir/methodology.json }
        }
        if {![file exists $json]} {
            error "timing18_summary: $json not found. Source run_methodology_setup.tcl (Step 1) first."
        }
        set fh [open $json r]; set txt [read $fh]; close $fh

        array set nin {}; array set nout {}
        set extra {}
        foreach c [get_clocks -quiet] { set nin($c) 0; set nout($c) 0 }
        # JSON text: "... delay is missing on <port> relative to the rising and\/or falling
        # clock edge(s) of <clock: <name>>." (the JSON escapes "/" as "\/")
        set msg {An (input|output) delay is missing on (\S+) relative to the rising and\\?/or falling clock edge\(s\) of <clock: ([^>]+)>}
        set parsed 0
        foreach {all kind port ck} [regexp -all -inline $msg $txt] {
            incr parsed
            # A clock name that is not in the design (e.g. fixed away) still gets its own row
            if {![info exists nin($ck)]} { set nin($ck) 0; set nout($ck) 0; lappend extra $ck }
            if {$kind eq "input"} { incr nin($ck) } else { incr nout($ck) }
        }
        set unparsed [expr {[regexp -all {delay is missing on} $txt] - $parsed}]
        puts "timing18_summary: $parsed TIMING-18 violation(s) read from $json"

        set fmt "| %-20s | %10s | %-20s | %-26s | %11s | %12s |"
        set lines {}
        lappend lines [format $fmt "Clock" "Period(ns)" "Waveform(r f)" "Clock Tree (driver/root)" "#No InDelay" "#No OutDelay"]
        lappend lines "|----------------------|------------|----------------------|----------------------------|-------------|--------------|"
        foreach c [lsort [get_clocks -quiet]] {
            set per  [get_property -quiet PERIOD $c]
            set wave [get_property -quiet WAVEFORM $c]
            # Clock tree: driving buffer REF_NAME + physical CLOCK_ROOT (falls back to source pin)
            set root [get_property -quiet CLOCK_ROOT $c]
            set drv  ""
            set net  [get_nets -quiet -of_objects $c]
            if {[llength $net]} {
                set dp [get_pins -quiet -leaf -of_objects $net -filter {DIRECTION == OUT}]
                if {[llength $dp]} { set drv [get_property -quiet REF_NAME [get_cells -quiet -of_objects [lindex $dp 0]]] }
            }
            set tree [string trim "$drv $root"]
            if {$tree eq ""} { set tree [get_property -quiet SOURCE_PINS $c] }
            lappend lines [format $fmt $c $per $wave $tree $nin($c) $nout($c)]
        }
        foreach ck $extra {
            lappend lines [format $fmt "$ck (not in design)" "-" "-" "-" $nin($ck) $nout($ck)]
        }
        if {$unparsed > 0} {
            lappend lines [format $fmt "<unparsed message>" "-" "-" "-" "?" "?"]
            puts "timing18_summary: WARNING $unparsed TIMING-18 message(s) in $json did not match the expected format"
        }
        set txt [join $lines "\n"]
        puts $txt
        set fh [open $out w]; puts $fh $txt; close $fh
        return $txt
    }

    # trace_clock_back_to_source <pin>
    # Trace up a clock tree from a netlist element to find where the clock
    # constraint should be defined. Uses get_timing_arcs to relate output pins to
    # input pins and continues until it reaches a port or an output pin with no
    # timing arc. Requires an open design.
    # Returns a 2-element list: {<port or pin object(s)> port|pin}
    proc trace_clock_back_to_source {pin} {
        set debug 1
        set pin [get_pins $pin]
        if {[get_property DIRECTION $pin] eq "OUT"} {
            set output_pins $pin
            if {$debug==1} {puts "-D: Starting trace from output pin $output_pins"}
        } else {
            set output_pins [get_pins -leaf -of [get_nets -of $pin] -filter {DIRECTION==OUT}]
            if {[llength $output_pins] == 0} {return [list $pin pin]}
        }

        set visited_pins {}
        set max_visits 5
        set pin_visit_count [dict create]

        while {1} {
            if {[llength [set arcs [get_timing_arcs -filter {TYPE=="Reg Clk to Q"||TYPE=="combinational"} -to $output_pins]]] == 0} {return [list $output_pins pin]}

            # Check if pin has been visited too many times
            if {[dict exists $pin_visit_count $output_pins]} {
                set count [dict get $pin_visit_count $output_pins]
                if {$count >= $max_visits} {return [list $output_pins pin]}
                dict set pin_visit_count $output_pins [expr {$count + 1}]
            } else {
                dict set pin_visit_count $output_pins 1
            }

            set input_pins [get_pins [get_property FROM_PIN $arcs]]
            set net [get_nets -parent -of $input_pins]
            set output_pins [get_pins -leaf -filter {DIRECTION==OUT} -of $net]
            if {[llength $output_pins] == 0} {
                if {[llength [set ports [get_ports -of $net]]] > 0} {return [list $ports port]}
            }
        }
    }

}

# Run the setup when this file is sourced. Override the output directory by
# setting the global ::methodology_out_dir before sourcing.
if {![info exists ::methodology_out_dir]} {
    # Resolve to an absolute path now, so a later cd cannot move the results.
    set ::methodology_out_dir [file normalize ./methodology_results]
}
methodology_check::run_methodology_setup $::methodology_out_dir
