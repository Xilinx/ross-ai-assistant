# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_budget.tcl: block RAM and URAM model, budget, depths, plan summary. Sourced by ila_insert.tcl; every proc here was moved from it without edits.

namespace eval ila {
    # The depth values Vivado accepts (C_DATA_DEPTH of an ILA core). Vivado has no property that lists them. They come from a table, and the plan
    # makes no scratch core: on Versal, and in a DFX design where Vivado accepts it, a scratch core leaves the design changed (a Versal DFX project
    # went from 2159 to 1142 cells; a flat Versal checkpoint from 94 to 97 cells and 22633 to 25620 nets, with a debug hub left behind), and
    # planning must not edit the design. The table was measured with a scratch core on UltraScale, UltraScale+, Versal and 7 series parts, Vivado
    # 2026.1 and 2026.2: the same eight depths every time.
    variable table_depths {1024 2048 4096 8192 16384 32768 65536 131072}
    proc valid_depths {} {
        variable table_depths
        return $table_depths
    }

    # 36 Kb block RAM tiles (RAMB36 equivalents, RAMB18 counts half, each core rounded up) the capture memory of one ILA core needs at
    # <depth> samples of <width> bits. Measured with opt_design, Vivado 2026.1 (and 2026.2 where it was run, same numbers), on UltraScale
    # (xcvu095, xcvu190), UltraScale+ (xcvu37p) and Versal (xcvm1802), depths 1024 to 131072, widths 32 to 4096. Storage qualification and
    # advanced trigger add none. Details and every point: docs/lf2-limits.md and references/memory-budget.md.
    #   UltraScale, UltraScale+, and Versal up to depth 2048: the memory is RAMB36 plus a last RAMB18, so bits / 36864 plus half a tile
    #   and 0.25 percent. Never below the measured tiles, at most one above (102 points).
    #   UltraScale and UltraScale+ above depth 16384: depth / 16384 times the 16384 value. Every measured core (depths 32768 to 131072,
    #   widths 32 to 1024, odd widths 48, 96, 768) is exactly that multiple of the 16384 deep core of the same width, or one to a few tiles
    #   below it; the single formula, taken to a deeper depth, drops below the measured tiles (65536 x 32: 58 against 60).
    #   Versal from depth 4096: versal_tiles, RAMB36 only. The structure is that of the fixed primitive algorithm of the Block Memory
    #   Generator (PG058, chapter 4, "Block RAM Usage": width ratio rounded up times depth ratio rounded up), with a 4Kx9 primitive and a
    #   capture word of width + 3 bits. That PG058 text is about the BMG core, not the ILA: the match is measured, not documented.
    # Not measured below width 32. See references/memory-budget.md and docs/lf2-limits.md.
    proc bram_tiles {depth width {arch ""}} {
        if {$arch eq "versal" && $depth >= 4096} { return [versal_tiles $depth $width] }
        if {$depth > 16384} { return [expr {int(ceil($depth / 16384.0)) * [bram_tiles 16384 $width $arch]}] }
        set raw [expr {$width * $depth / 36864.0}]
        return [expr {int(ceil($raw * 1.0025 + 0.5))}]
    }

    # Versal block RAM tiles from depth 4096 (RAMB36 only): ceil((width + 3) / 9) lanes of 9 bits, each lane ceil(depth / 4096) tiles deep.
    # Exact on every measured point from depth 4096 to 32768 (24 points, widths 32 to 1024) and on 65536 and 131072 (19 points) once the lane count is
    # raised by a factor that grows with depth: 1.0 up to depth 32768, 1.02 at 65536, 1.057 at 131072 (the cascade of a deep core costs
    # extra lanes). Never below the measured tiles; one lane (depth / 4096 tiles) above at 65536 x 192. Constants fitted to the points of
    # docs/lf2-limits.md (xcvm1802, Vivado 2026.1); valid depths are powers of two from 1024 to 131072.
    proc versal_tiles {depth width} {
        set g 0.0
        if {$depth >= 131072} { set g 0.057 } elseif {$depth >= 65536} { set g 0.02 }
        set lanes [expr {int(ceil(($width + 3) / 9.0 * (1.0 + $g) - 1e-9))}]
        return [expr {$lanes * int(ceil($depth / 4096.0))}]
    }

    # UltraRAM tiles (URAM288, 294912 bits) the capture memory of one core needs. Measured with opt_design on xcvm1802, depths 4096 and
    # 16384, widths 32 to 1024 (12 points): exactly ceil(bits / 294912). The tiles are packed by bits, not by 72-bit columns.
    proc uram_tiles {depth width} {
        return [expr {($width * $depth + 294911) / 294912}]
    }

    # free URAM288 tiles on the device minus those the design uses; -1 when unknown
    proc free_uram_tiles {} {
        set total [get_property ULTRA_RAMS [get_parts [get_property PART [current_design]]]]
        if {$total eq "" || $total == 0} { return -1 }
        return [expr {$total - [llength [get_cells -quiet -hier -filter {REF_NAME =~ "URAM288*"}]]}]
    }

    # RAMB36 tiles the cells of a design take: a RAMB36 or FIFO36 is one tile, a RAMB18 or FIFO18 half a tile, rounded up. A FIFO36E2 or
    # FIFO18E2 sits on a block RAM site, so it takes the same room as a RAMB of its size (UG974 FIFO36E2: "built from block RAM"; not
    # measured on a design here, see findings B-04).
    proc ram_tiles_used {cells} {
        set n36 0; set n18 0
        foreach c $cells {
            set r [get_property REF_NAME $c]
            if {[string match RAMB36* $r] || [string match FIFO36* $r]} { incr n36 } elseif {[string match RAMB18* $r] || [string match FIFO18* $r]} { incr n18 }
        }
        return [expr {$n36 + int(ceil($n18 / 2.0))}]
    }

    # free RAMB36 tiles on the device minus those used by the design (RAMB and FIFO cells); -1 when unknown
    proc free_bram_tiles {} {
        set total [get_property BLOCK_RAMS [get_parts [get_property PART [current_design]]]]
        if {$total eq "" || $total == 0} { return -1 }
        set cells [get_cells -quiet -hier -filter {REF_NAME =~ "RAMB36*" || REF_NAME =~ "RAMB18*" || REF_NAME =~ "FIFO36*" || REF_NAME =~ "FIFO18*"}]
        return [expr {$total - [ram_tiles_used $cells]}]
    }

    # Block RAM of a reconfigurable partition (criterion 4, 4.7): the RAMB36 sites of the pblock that holds the partition cell, and the RAMB
    # tiles the design already uses inside that cell. Returns {pblock-name total used}, or {} when the partition has no pblock or the sites
    # cannot be read (the caller then budgets against the device and says so). Measured on KU5P and VC1902, Vivado 2026.2: get_sites
    # -of_objects <pblock> RAMB36_X*Y* returns exactly the pblock's RAMB36 sites (the UltraScale+ site type is RAMBFIFO36, so match the name).
    proc partition_bram {rp} { return [partition_ram $rp RAMB36_X*Y* bram {RAMB* || REF_NAME =~ FIFO*}] }

    # UltraRAM of a reconfigurable partition (Versal, -memory_type uram): the URAM288 sites of the pblock and the URAM288 cells the design
    # already has inside the partition cell. Same shape and same "unknown" answer as partition_bram.
    proc uram_cells_used {cells} { return [llength $cells] }
    proc partition_uram {rp} { return [partition_ram $rp URAM288_X*Y* uram {URAM288*}] }

    proc partition_ram {rp sitepat kind refglob} {
        if {[catch {
            set pbs [get_pblocks -quiet -of_objects [get_cells -quiet $rp]]
            if {[llength $pbs] == 0} { return {} }
            set sites [dict create]
            foreach pb $pbs { foreach s [get_sites -quiet -of_objects $pb $sitepat] { dict set sites $s 1 } }
            set cells [get_cells -quiet -hier -filter "NAME =~ $rp/* && (REF_NAME =~ $refglob)"]
            set used [expr {$kind eq "uram" ? [uram_cells_used $cells] : [ram_tiles_used $cells]}]   ;# kind is bram or uram: two literal calls, no dispatch through a variable
            set name [get_property NAME [lindex $pbs 0]]
        }]} { return {} }   ;# an unreadable pblock is "unknown", reported by the caller as W02
        return [list $name [dict size $sites] $used]
    }

    # Block RAM tiles that the ILA cores already in the design (debug core objects, from XDC or an earlier insertion) will take. A core
    # that has not been through opt_design has no RAMB cell yet, so free_bram_tiles does not see it. For each core: the tiles the cost
    # model gives for its C_DATA_DEPTH and probe widths, minus the RAM cells already under it (after opt_design they are counted by
    # free_bram_tiles, and must not be counted twice). The hub has no capture memory. With a scope (a partition cell) only the cores whose
    # name starts with "<scope>/" count: the plan takes them off that partition's pblock room. With replace=1 the cores that -replace_existing
    # deletes (u_ila_*) are left out. A core whose depth or widths cannot be read adds nothing. Cells of ILA IPs are not cores here.
    proc existing_ila_tiles {replace {scope ""}} { return [existing_ila_ram $replace $scope 0] }
    # The same for UltraRAM (Versal): the cores built for UltraRAM (C_MEMORY_TYPE 1) take URAM288 tiles by the URAM model and no block RAM (review A finding 8).
    proc existing_ila_uram_tiles {replace {scope ""}} { return [existing_ila_ram $replace $scope 1] }
    proc existing_ila_ram {replace scope uram} {
        variable facts
        set arch [require_architecture]
        set tot 0
        set targets [replace_deleted $replace]
        foreach c [get_debug_cores -quiet] {
            set nm [get_property NAME $c]
            if {[regexp {(^|/)(axi_)?dbg_hub} $nm]} continue
            if {$replace && $nm in $targets} continue
            if {$scope ne "" && [string first "$scope/" $nm] != 0} continue   ;# only the cores inside that partition (a literal prefix: string match would read the [0] of gen_rp[0] as a class)
            # Only an ILA has a capture memory: listed_core_is_real, narrowed to the ILA types. A listed name whose cell is no debug IP (the HBM
            # IP), and a VIO, a hub or a bridge IP, have no C_DATA_DEPTH, and reading it makes Vivado print an ERROR line (12-4444) even inside
            # catch, which an agent relayed to the user (exploratory trial, 2026-11-04, a design with a VIO). The same queries listed_core_is_real
            # makes. A name with no cell is a core create_debug_core made: an ILA.
            set ccell [get_cells -quiet $nm]
            if {$ccell ne "" && [debug_ip_type $ccell] ni {ila axis_ila}} continue
            if {[catch {get_property C_DATA_DEPTH $c} depth]} continue   ;# a listed object with no such property adds nothing
            if {![string is integer -strict $depth] || $depth <= 0} continue
            # C_MEMORY_TYPE exists on Versal cores only: 1 is UltraRAM, anything else (also no property) is block RAM
            set mt ""
            if {$arch eq "versal" && [catch {get_property C_MEMORY_TYPE $c} mt]} { set mt "" }
            if {[expr {[string is integer -strict $mt] && $mt == 1}] != $uram} continue
            set w 0
            foreach p [get_debug_ports -quiet "$nm/probe*"] {
                set pw [get_property PORT_WIDTH $p]
                if {[string is integer -strict $pw]} { incr w $pw }
            }
            if {$w <= 0} continue
            if {$uram} {
                set have [uram_cells_used [get_cells -quiet -hier -filter "NAME =~ $nm/* && (REF_NAME =~ URAM288*)"]]
                incr tot [expr {max(0, [uram_tiles $depth $w] - $have)}]
            } else {
                set have [ram_tiles_used [get_cells -quiet -hier -filter "NAME =~ $nm/* && (REF_NAME =~ RAMB* || REF_NAME =~ FIFO*)"]]
                incr tot [expr {max(0, [bram_tiles $depth $w $arch] - $have)}]
            }
        }
        return $tot
    }

    # Nets connected to a debug core's probe port, read back from report_debug_core.
    # Vivado has no property or query for this; the report's channel table is the only source.
    # In a DFX design the report lists a core inside a partition without the partition path ("u_ila_0" for design_1_i/rp1/u_ila_0), and
    # names its nets the same way. Pass dfx=1 to match that short name. plan gives DFX cores numbers that are unique across regions,
    # so the short name is never a second core's name. rep is a report already read (core_recipe reads it once per core, and once with
    # -full_path for the full net names); empty reads it here.
    proc connected_nets {core port {dfx 0} {rep ""}} {
        if {$rep eq ""} { set rep [report_debug_core -return_string] }
        set inside 0
        set nets {}
        foreach line [split $rep \n] {
            if {[regexp {^Channel Data for Debug Core "([^"]+)"} $line -> nm]} { set inside [expr {$nm eq $core || ($dfx && [string match "*/$nm" $core])}]; continue }
            if {!$inside} continue
            set f [lmap x [split $line |] {string trim $x}]
            foreach i [lsearch -all -regexp $f "^${port}\\\[\\d+\\\]\$"] {
                set net [lindex $f end-1]
                if {$net ne ""} { lappend nets $net }
            }
        }
        return $nets
    }

    # E-25: free is the measured device count minus a MODELLED term for existing cores; say so when the model took anything.
    proc free_split_text {} {
        variable plan
        if {![dict exists $plan bram_tiles_modelled] || [dict get $plan bram_tiles_modelled] <= 0} { return "" }
        # In a DFX design with pblocks the free count is the one of the region that failed, or of the regions together, not the device count minus the model:
        # printing the split there made "free 12 (measured 600, minus 5 modelled)" read as 12 = 600 - 5 (review A finding 6).
        set meas [dict get $plan bram_tiles_free_measured]
        if {[dict exists $plan dfx] && [dict get $plan dfx] && [dict get $plan bram_tiles_free] != max(0, $meas - [dict get $plan bram_tiles_modelled])} {
            return " (the free count of the partitions and the static part that were checked; the device has $meas free, measured)"
        }
        return " (measured [dict get $plan bram_tiles_free_measured], minus [dict get $plan bram_tiles_modelled] modelled for existing debug cores)"
    }

    proc write_plan_summary {} {
        variable plan
        say "---- plan ----"
        # apply creates the cores whose clock source is free first (H3), so on UltraScale and UltraScale+ the hub lands on a free clock when the plan has one (review A finding 5).
        # Versal keeps the wording the cases pin: its hub clock is decided at apply (C04) and the plan cannot tell.
        set hub_on_free 0
        if {[catch {require_architecture} parch]} { set parch "" }
        if {$parch ne "" && $parch ne "versal"} {
            foreach g [dict get $plan groups] { if {[dict get $g clock_verdict] eq "free"} { set hub_on_free 1 } }
        }
        foreach g [dict get $plan groups] {
            say "core [dict get $g name]: [dict get $g width] probe bits, clock net [dict get $g clocknet]"
            say "   first nets: [lrange [dict get $g nets] 0 2] ... last: [lindex [dict get $g nets] end]"
            say "   clock source: [dict get $g clock_verdict] ([dict get $g clock_source]); path: [dict get $g clock_path]"
            if {[dict exists $g marked]} {
                set mk [dict get $g marked]
                say "   MARK_DEBUG: [expr {$mk eq "unknown" ? "could not be read" : "[lindex $mk 0] of [lindex $mk 1] probe nets carry it[expr {[llength [lindex $mk 2]] ? " (for example [join [lindex $mk 2] {, }])" : ""}]"}]"
            }
            if {[dict get $g clock_verdict] ne "free"} {
                if {$hub_on_free} {
                    say "   WARNING: this clock can stop, so this core can stop capturing. The debug hub is put on a free clock, not on this one, so it stays reachable. Tell the user."
                } else {
                    say "   WARNING: the debug hub shares this clock. A clock that can stop makes the hub unreachable. Tell the user."
                }
            }
        }
        request_lines planned
        # Every refused net is in the plan. The printed list is capped per code, so no code is hidden behind another:
        # the first 5 lines of each code, then one count line for that code. N01 and N17 refuse an item the user typed, so they are never capped. N20 is never capped either: each net already probed by an existing core gets its own line. So is N12: each net on an output port gets its own line.
        # A reason already printed for the same code is not repeated.
        set nprinted [dict create]; set nmore [dict create]; set firstev [dict create]; set codes {}
        foreach r [dict get $plan rejected] {
            lassign $r rn rc rev
            if {![dict exists $nprinted $rc]} { dict set nprinted $rc 0; dict set nmore $rc 0; lappend codes $rc }
            if {$rc ni {N01 N17 N20 N12} && [dict get $nprinted $rc] >= 5} { dict incr nmore $rc; continue }
            dict incr nprinted $rc
            if {[dict exists $firstev $rc $rev]} {
                say "REJECTED $rn \[$rc\]: same reason as [dict get $firstev $rc $rev]"
            } else {
                dict set firstev $rc $rev $rn
                say "REJECTED $rn \[$rc\]: $rev"
            }
        }
        foreach rc $codes {
            if {[dict get $nmore $rc] > 0} { say "REJECTED [dict get $nmore $rc] more \[$rc\] refused nets: not printed here. Every one is kept in the plan (AI only: dict get \$ila::plan rejected)." }
        }
        foreach w [dict get $plan warnings] {
            if {[string match "NOTE *" [lindex $w 1]]} { say "NOTE [lindex $w 0]: [string range [lindex $w 1] 5 end]" } else { say "WARNING [lindex $w 0]: [lindex $w 1]" }
        }
        if {[dict exists $plan opts] && [dict exists $plan opts memtype] && [dict get $plan opts memtype] eq "uram"} {
            say "memory (formula, calibrated on opt_design): need [dict get $plan uram_tiles_needed] UltraRAM tiles, free [dict get $plan uram_tiles_free], budget_ok=[dict get $plan budget_ok]"
        } else {
            say "memory (formula, calibrated on opt_design): need [dict get $plan bram_tiles_needed] block RAM tiles, free [dict get $plan bram_tiles_free][free_split_text], budget_ok=[dict get $plan budget_ok]"
        }
        if {[dict exists $plan budget_checked] && ![dict get $plan budget_checked]} { say "WARNING W02 budget NOT checked: the part reports no memory count, so budget_ok=[dict get $plan budget_ok] means nothing was compared. Tell the user." }
    }
    # The REQUEST lines: one per name asked for, with what became of every signal it matched, the bits of a bus together: <verb> (planned,
    # or probed after apply), the same net as a signal that is <verb>, or refused with the first sentence of its reason. Built from the
    # plan alone, so it asks Vivado nothing; apply prints them again after Vivado dropped nets (N99). A name with more than 12 signals
    # prints the counts and the refused signals (the first 8 of each reason and a count), then "all others <verb>". Exploratory trial, 2026-11-04: the summary
    # showed the first and last net and 5 of 100 refusals, and the agent told the user that signals the core probed might be missing.
    proc request_lines {verb} {
        variable plan
        if {![dict exists $plan requested]} return
        set rq [dict get $plan requested]
        set probed [dict create]
        foreach g [dict get $plan groups] { foreach n [dict get $g nets] { dict set probed $n 1 } }
        set refby [dict create]
        foreach r [dict get $plan rejected] { if {![dict exists $refby [lindex $r 0]]} { dict set refby [lindex $r 0] [lindex $r 2] } }
        set done [dict create]
        foreach item [dict get $rq items] {
            if {[dict exists $done $item]} continue
            dict set done $item 1
            set label [expr {$item eq "filt:" ? "the filter {[dict get $rq filter]}" : ([string match comb:* $item] ? "'[string range $item 5 end]' (combinational)" : "'$item'")}]
            if {[dict exists $rq refused $item]} {
                say "REQUEST $label: refused, [first_sentence [lindex [dict get $rq refused $item] 2]]."
                continue
            }
            # outcome per net: ok (probed), same <net> (an alias of a probed net) or a refusal reason; then the nets of one signal and one outcome together
            set byout [dict create]
            set bases [dict create]
            foreach n [lsort -unique [expr {[dict exists $rq nets $item] ? [dict get $rq nets $item] : {}}]] {
                set src $n
                if {[dict exists $probed $n]} {
                    set out ok
                } elseif {[dict exists $rq alias $n] && [dict exists $probed [dict get $rq alias $n]]} {
                    set out [list same [dict get $rq alias $n]]
                } else {
                    if {[dict exists $rq alias $n]} { set src [dict get $rq alias $n] }
                    if {[dict exists $refby $src]} {
                        set why [first_sentence [dict get $refby $src]]
                        if {[string first "$src " $why] == 0} { set why "it[string range $why [string length $src] end]" }   ;# most reasons begin with the net's name
                        set out [list no $why]
                    } elseif {[dict exists $rq clock $src]} {
                        set out [list no [first_sentence [lindex [dict get $rq clock $src] 2]]]
                    } else {
                        set out [list no "not in the plan"]
                    }
                }
                set base $n; set bit ""
                regexp {^(.*)\[([0-9]+)\]$} $n -> base bit
                dict set bases $base 1
                dict lappend byout $out [list $base $bit $n]
            }
            if {[dict size $bases] == 0} { say "REQUEST $label: no net matched it."; continue }
            set nbits 0; dict for {out ns} $byout { incr nbits [llength $ns] }
            set long [expr {[dict size $bases] > 12}]
            set okl {}; set refl {}; set capped 0
            foreach out [lsort [dict keys $byout]] {
                set sigs [request_signals [dict get $byout $out]]
                if {$out eq "ok"} { lappend okl {*}$sigs; continue }
                if {[lindex $out 0] eq "same"} { foreach s $sigs { lappend okl "$s (the same net as [lindex $out 1])" }; continue }
                set more ""
                if {$long && [llength $sigs] > 8} { set more " and [expr {[llength $sigs] - 8}] more"; set sigs [lrange $sigs 0 7]; set capped 1 }
                lappend refl "[join $sigs {, }]$more ([lindex $out 1])"
            }
            set ai [expr {$capped ? " (AI only: every refused net is in dict get \$ila::plan rejected)" : ""}]
            set head "[dict size $bases] signal[expr {[dict size $bases] == 1 ? "" : "s"}], $nbits bit[expr {$nbits == 1 ? "" : "s"}]"
            if {$long} {
                if {[llength $refl] == 0} { say "REQUEST $label: $head, all $verb."; continue }
                say "REQUEST $label: $head. Refused: [join $refl {; }]. All others $verb.$ai"
                continue
            }
            set parts {}
            if {[llength $okl] > 0} { lappend parts "$verb: [join [lsort -dictionary $okl] {, }]" }
            if {[llength $refl] > 0} { lappend parts "refused: [join $refl {; }]" }
            say "REQUEST $label: [join $parts {. }]."
        }
    }
    # The signals of one outcome in a REQUEST line: a single net by its name, the bits of a bus as name[hi:lo] when they run without a gap,
    # else as "name (N bits)". Entries are {base bit net}.
    proc request_signals {entries} {
        set bybase [dict create]
        foreach e $entries { dict lappend bybase [lindex $e 0] $e }
        set out {}
        foreach base [lsort -dictionary [dict keys $bybase]] {
            set es [dict get $bybase $base]
            if {[llength $es] == 1 || "" in [lmap e $es {lindex $e 1}]} { foreach e $es { lappend out [lindex $e 2] }; continue }   ;# one net, or a scalar beside bits of the same name: each by its name
            set bits [lsort -integer [lmap e $es {lindex $e 1}]]
            if {[lindex $bits end] - [lindex $bits 0] + 1 == [llength $bits]} {
                lappend out "$base\[[lindex $bits end]:[lindex $bits 0]\]"
            } else {
                lappend out "$base ([llength $bits] bits)"
            }
        }
        return $out
    }
    # The first sentence of a reason, without its full stop.
    proc first_sentence {text} {
        if {[regexp {^(.*?)\.(\s|$)} $text -> s]} { return $s }
        return [string trimright $text .]
    }
}
