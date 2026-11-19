# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
###########################################################################
## detect_macro_clusters.tcl
## Identify CRITICAL memory-to-memory "macro clusters" on a POST-OPT (pre-place)
## Versal checkpoint and emit placer co-location tags.
##
## Structure targeted (start set): mem -> N*LUT -> mem where
##   patterns = BRAM->BRAM, BRAM->URAM, URAM->URAM  (URAM->BRAM also collected)
## Criticality is STRUCTURAL (checkpoint is pre-place: no LOC / no slack):
##   (1) SOURCE read port is UNREGISTERED (large clock-to-out):
##          BRAM DOA_REG/DOB_REG == 0 ; URAM OREG_A/OREG_B == FALSE
##   (2) a "good amount" of logic levels between the two macros:
##          #LUT levels on the src->dst combinational path >= MIN_LEVELS
## Then EXPAND each endpoint primitive to its full sibling ARRAY (XPM instance /
## shared parent), union the arrays into CLUSTERS (capacity-capped), and emit a
## parameterized tagging script so the placer co-locates each cluster.
##
## Public entry:  ::mc::run_macro_cluster_analysis <outdir>  (namespaced — must be
##                 called with the ::mc:: qualifier, it is not exported)
## READ-ONLY on the design (only reads properties/connectivity; emits files).
###########################################################################
namespace eval ::mc {
    # ---- tunables (env-overridable) ----
    variable MIN_LEVELS         2      ;# min #LUT levels between the 2 macros
    variable MAX_LEVELS         8      ;# BFS depth bound (combinational only)
    variable MAX_CLUSTER_MACROS 32     ;# capacity cap: macros per cluster
    variable PATTERNS  {BRAM2BRAM BRAM2URAM URAM2URAM}  ;# kept src->dst combos
    variable SAME_PARTITION     1      ;# only cluster within one DFX partition
    variable MIN_GROUPS_PER_CLUSTER 2  ;# a cluster must join >=2 arrays
    variable SRC_LIMIT          0      ;# 0 = all sources; >0 caps sources (quick test)
    variable MAX_CONE           0      ;# per-source unique-LUT-cone cap (0=unlimited) - guards huge fanout
    variable SCOPE              ""     ;# hier-prefix: only memories under it are SOURCES (fast portion run; "" = whole design)

    proc env_or {name def} {
        if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
        return $def
    }
}

## ---- helper: memory type from ref name ----
proc ::mc::mem_type {ref} {
    if {[string match RAMB* $ref]} { return BRAM }
    if {[string match URAM* $ref]} { return URAM }
    return ""
}
proc ::mc::is_mem {ref} { return [expr {[string match RAMB* $ref] || [string match URAM* $ref]}] }
proc ::mc::is_lut {ref} { return [string match LUT* $ref] }

## ---- helper: ancestor chain (innermost -> outermost) ----
proc ::mc::ancestors {cell} {
    set chain {} ; set c $cell
    while {1} {
        set p [get_property -quiet PARENT $c]
        if {$p eq "" || $p eq $c} break
        lappend chain $p ; set c $p
    }
    return $chain
}

## ---- helper: sibling-array group id of a memory primitive (NAME-based, fast) ----
## Primary = innermost XPM memory/fifo instance in the hierarchy name (one logical
## memory). Fallback = immediate parent (strip last '/'-delimited segment).
proc ::mc::array_group {name} {
    if {[regexp {^(.*/xpm_memory_base_inst)/} $name -> g]} { return $g }
    if {[regexp {^(.*/xpm_fifo_base_inst)/}   $name -> g]} { return $g }
    set i [string last "/" $name]
    if {$i >= 0} { return [string range $name 0 [expr {$i-1}]] }
    return $name
}

## ---- helper: DFX partition (RP) id of a memory (NAME-based prefix match) ----
## ::mc::RPS is a length-desc list of reconfigurable-cell names (built in run).
proc ::mc::partition_of {name} {
    variable RPS
    foreach rp $RPS { if {$name eq $rp || [string match "$rp/*" $name]} { return $rp } }
    return TOP
}

## ---- helper: designer instance where the XPM/memory is instantiated ----
## The USER_CLUSTER tag goes here (a few levels up from the leaf BRAM), NOT on the
## primitive. Walk up '/'-segments from the array group, dropping XPM / FIFO /
## altsyncram boilerplate wrapper segments; stop at the first designer instance.
## All banks of one logical memory (e.g. mem_loop[0..7]) collapse to the same node.
proc ::mc::user_instance {group} {
    set segs [split $group "/"]
    while {[llength $segs] > 1} {
        set last [lindex $segs end]
        if {[regexp {xpm_memory_|xpm_fifo_|u_xpm_|altsyncram|_xpm(_|$)} $last]} {
            set segs [lrange $segs 0 end-1]
        } else { break }
    }
    return [join $segs "/"]
}

## ---- forward combinational BFS from a source memory's unregistered DOUT ----
## Builds the DOUT pin collection INTERNALLY (in-scope so Vivado -of_objects works).
## Per-net frontier carries exact LUT levels; per net we resolve sinks in BULK
## (one get_cells + filter, NOT per-sink-pin) and dedup LUTs so each is expanded once.
## No name reconstruction (single-object -of is glob-safe). maxcone caps huge cones.
## Returns dict: dstMemCell -> minLevels (#LUTs on shortest src->dst comb path).
proc ::mc::forward_reach {m type doa dob orega oregb maxlev {maxcone 0}} {
    array set dstLev {}
    set f {}
    if {$type eq "BRAM"} {
        if {$doa == 0} { lappend f "REF_PIN_NAME=~DOUTADOUT* || REF_PIN_NAME=~DOUTPADOUTP*" }
        if {$dob == 0} { lappend f "REF_PIN_NAME=~DOUTBDOUT* || REF_PIN_NAME=~DOUTPBDOUTP*" }
    } else {
        if {$orega eq "FALSE"} { lappend f "REF_PIN_NAME=~DOUT_A*" }
        if {$oregb eq "FALSE"} { lappend f "REF_PIN_NAME=~DOUT_B*" }
    }
    if {![llength $f]} { return [array get dstLev] }
    set outpins [get_pins -quiet -of $m -filter "DIRECTION==OUT && ([join $f { || }])"]
    if {![llength $outpins]} { return [array get dstLev] }
    array set seenlut {}
    set nlut 0
    set frontier {}
    foreach n [get_nets -quiet -of $outpins] { lappend frontier [list $n 0] }
    while {[llength $frontier]} {
        set nf {}
        foreach item $frontier {
            lassign $item net lev
            if {$lev > $maxlev} continue
            set sinks [get_pins -quiet -leaf -of $net -filter {DIRECTION==IN}]
            if {![llength $sinks]} continue
            set cells [get_cells -quiet -of $sinks]
            if {![llength $cells]} continue
            # memory sinks at this net's level -> record dst (require >=1 LUT traversed)
            if {$lev >= 1} {
                foreach mc [filter $cells {REF_NAME=~RAMB* || REF_NAME=~URAM*}] {
                    if {$mc eq $m} continue
                    if {![info exists dstLev($mc)] || $lev < $dstLev($mc)} { set dstLev($mc) $lev }
                }
            }
            # LUT sinks -> expand each unseen LUT once (single-object -of, glob-safe)
            foreach lc [filter $cells {REF_NAME=~LUT*}] {
                if {[info exists seenlut($lc)]} continue
                set seenlut($lc) 1 ; incr nlut
                if {$maxcone > 0 && $nlut > $maxcone} continue
                foreach on [get_nets -quiet -of [get_pins -quiet -of $lc -filter {DIRECTION==OUT}]] {
                    lappend nf [list $on [expr {$lev + 1}]]
                }
            }
        }
        set frontier $nf
    }
    return [array get dstLev]
}

## ================= main =================
proc ::mc::run_macro_cluster_analysis {outdir} {
    variable MIN_LEVELS
    variable MAX_LEVELS
    variable MAX_CLUSTER_MACROS
    variable PATTERNS
    variable SAME_PARTITION
    variable MIN_GROUPS_PER_CLUSTER
    variable SRC_LIMIT
    variable MAX_CONE
    variable SCOPE

    set MIN_LEVELS         [::mc::env_or MIN_LEVELS         $MIN_LEVELS]
    set MAX_LEVELS         [::mc::env_or MAX_LEVELS         $MAX_LEVELS]
    set MAX_CLUSTER_MACROS [::mc::env_or MAX_CLUSTER_MACROS $MAX_CLUSTER_MACROS]
    set SAME_PARTITION     [::mc::env_or SAME_PARTITION     $SAME_PARTITION]
    set PATTERNS           [::mc::env_or PATTERNS           $PATTERNS]
    set SRC_LIMIT          [::mc::env_or SRC_LIMIT          $SRC_LIMIT]
    set MAX_CONE           [::mc::env_or MAX_CONE           $MAX_CONE]
    set SCOPE              [::mc::env_or SCOPE              $SCOPE]
    array unset patSet ; foreach p $PATTERNS { set patSet([string toupper $p]) 1 }

    file mkdir $outdir
    set t0 [clock seconds]
    puts "\[mc\] start MIN_LEVELS=$MIN_LEVELS MAX_LEVELS=$MAX_LEVELS CAP=$MAX_CLUSTER_MACROS SAME_PARTITION=$SAME_PARTITION PATTERNS=$PATTERNS SCOPE=[expr {$SCOPE eq {} ? {<whole-design>} : $SCOPE}]"

    # reconfigurable-partition names (length desc -> innermost prefix wins)
    variable RPS
    set RPS [lsort -decreasing -command {apply {{a b} {expr {[string length $a]-[string length $b]}}}} \
                 [get_cells -hier -quiet -filter {HD.RECONFIGURABLE}]]
    puts "\[mc\] reconfigurable partitions: [llength $RPS]"

    # ---- gather memories + per-cell attributes (BULK) ----
    set brams [get_cells -hier -quiet -filter {REF_NAME =~ RAMB*}]
    set urams [get_cells -hier -quiet -filter {REF_NAME =~ URAM*}]
    set mems  [concat $brams $urams]
    puts "\[mc\] mems=[llength $mems] (bram=[llength $brams] uram=[llength $urams])"

    array set gidOf {}      ;# cell -> array group id
    array set gMems {}      ;# gid -> list of member mem cells
    array set gType {}      ;# gid -> BRAM/URAM/MIXED
    array set gPart {}      ;# gid -> partition id
    foreach m $mems r [get_property -quiet REF_NAME $mems] {
        set g [::mc::array_group $m]
        set gidOf($m) $g
        lappend gMems($g) $m
        set t [::mc::mem_type $r]
        if {![info exists gType($g)]} { set gType($g) $t } elseif {$gType($g) ne $t} { set gType($g) MIXED }
    }
    foreach g [array names gMems] { set gPart($g) [::mc::partition_of [lindex $gMems($g) 0]] }
    set noversize 0 ; set maxarr 0
    foreach g [array names gMems] { set n [llength $gMems($g)] ; if {$n > $maxarr} {set maxarr $n} ; if {$n > $MAX_CLUSTER_MACROS} { incr noversize } }
    puts "\[mc\] array-groups=[array size gMems] (oversize>$MAX_CLUSTER_MACROS: $noversize, largest=$maxarr)"

    # ---- source memories: unregistered read ports ----
    array set doaOf {} ; array set dobOf {}
    foreach b $brams a [get_property -quiet DOA_REG $brams] bb [get_property -quiet DOB_REG $brams] {
        set doaOf($b) $a ; set dobOf($b) $bb
    }
    array set oaOf {} ; array set obOf {}
    foreach u $urams a [get_property -quiet OREG_A $urams] bb [get_property -quiet OREG_B $urams] {
        set oaOf($u) $a ; set obOf($u) $bb
    }
    array set typeOf {}
    foreach m $mems r [get_property -quiet REF_NAME $mems] { set typeOf($m) [::mc::mem_type $r] }

    # ---- forward reach from every source primitive; build GROUP-level edges ----
    array set edgeLev {}   ;# "sg|dg" -> max levels (inter-array)
    array set edgeCnt {}   ;# "sg|dg" -> #primitive connections
    array set edgePat {}   ;# "sg|dg" -> pattern (src->dst prim types)
    array set intraLev {}  ;# array -> max levels of an internal mem->LUT->mem path
    array set intraCnt {}  ;# array -> #internal critical connections
    array set intraPat {}  ;# array -> pattern
    # source set: whole design, or (for a fast portion run) only memories under SCOPE
    if {$SCOPE ne ""} {
        set srcMems {}
        foreach m $mems { if {[string first $SCOPE $m] == 0} { lappend srcMems $m } }
        puts "\[mc\] SCOPE=$SCOPE -> [llength $srcMems] source memories (of [llength $mems])"
    } else {
        set srcMems $mems
    }
    set nsrc 0 ; set nedge 0
    foreach m $srcMems {
        set type $typeOf($m)
        if {$type eq "BRAM"} {
            set doa $doaOf($m) ; set dob $dobOf($m)
            if {$doa != 0 && $dob != 0} continue
            set oa {} ; set ob {}
        } else {
            set oa $oaOf($m) ; set ob $obOf($m)
            if {$oa ne "FALSE" && $ob ne "FALSE"} continue
            set doa 1 ; set dob 1
        }
        incr nsrc
        if {$SRC_LIMIT > 0 && $nsrc > $SRC_LIMIT} { incr nsrc -1 ; break }
        if {$nsrc % 500 == 0} { puts "\[mc\]   ..traversed $nsrc sources, edges so far=[array size edgeLev]" ; flush stdout }
        array unset reach ; array set reach [::mc::forward_reach $m $type $doa $dob $oa $ob $MAX_LEVELS $MAX_CONE]
        set sg $gidOf($m)
        foreach d [array names reach] {
            set lev $reach($d)
            if {$lev < $MIN_LEVELS} continue
            set dg $gidOf($d)
            set pat "${type}2$typeOf($d)"
            if {![info exists patSet([string toupper $pat])]} continue
            if {$dg eq $sg} {
                # intra-array critical mem->LUT->mem: this logical memory must stay compact
                if {![info exists intraLev($sg)] || $lev > $intraLev($sg)} { set intraLev($sg) $lev }
                incr intraCnt($sg)
                set intraPat($sg) $pat
                continue
            }
            if {$SAME_PARTITION && $gPart($sg) ne $gPart($dg)} continue
            set key "$sg|$dg"
            if {![info exists edgeLev($key)] || $lev > $edgeLev($key)} { set edgeLev($key) $lev }
            incr edgeCnt($key)
            set edgePat($key) $pat
        }
    }
    puts "\[mc\] sources-traversed=$nsrc inter-array-edges=[array size edgeLev] intra-array-critical=[array size intraLev]"

    # ---- greedy capacity-capped union-find over array groups ----
    array set uf_par {} ; array set uf_sz {}
    proc ::mc::_find {v} { upvar 1 uf_par par ; while {$par($v) ne $v} { set par($v) $par($par($v)) ; set v $par($v) } ; return $v }
    # seed union-find with all groups that appear in any inter-array edge
    foreach key [array names edgeLev] {
        lassign [split $key "|"] sg dg
        foreach g [list $sg $dg] {
            if {![info exists uf_par($g)]} { set uf_par($g) $g ; set uf_sz($g) [llength $gMems($g)] }
        }
    }
    # also seed self-critical (intra-array) arrays as singleton clusters
    foreach g [array names intraLev] {
        if {![info exists uf_par($g)]} { set uf_par($g) $g ; set uf_sz($g) [llength $gMems($g)] }
    }
    # sort edges by score desc: levels primary, count secondary
    set edges {}
    foreach key [array names edgeLev] { lappend edges [list $key $edgeLev($key) $edgeCnt($key)] }
    # stable double-sort: secondary (count,index 2) first, then primary (levels,index 1)
    set edges [lsort -integer -index 1 -decreasing [lsort -integer -index 2 -decreasing $edges]]
    set nmerged 0 ; set ncapskip 0
    foreach e $edges {
        lassign $e key lev cnt
        lassign [split $key "|"] sg dg
        set ra [::mc::_find $sg] ; set rb [::mc::_find $dg]
        if {$ra eq $rb} continue
        if {[expr {$uf_sz($ra)+$uf_sz($rb)}] > $MAX_CLUSTER_MACROS} { incr ncapskip ; continue }
        # union smaller into larger
        if {$uf_sz($ra) < $uf_sz($rb)} { set t $ra ; set ra $rb ; set rb $t }
        set uf_par($rb) $ra
        set uf_sz($ra) [expr {$uf_sz($ra)+$uf_sz($rb)}]
        incr nmerged
    }

    # ---- collect clusters (root -> groups) ----
    array set clGroups {}
    foreach g [array names uf_par] { lappend clGroups([::mc::_find $g]) $g }

    # ---- emit reports ----
    set sumF [open [file join $outdir macro_clusters_summary.rpt] w]
    set pairF [open [file join $outdir macro_cluster_pairs.csv] w]
    set clF  [open [file join $outdir macro_clusters.csv] w]
    set applyF [open [file join $outdir apply_macro_cluster_tags.tcl] w]
    puts $pairF "kind,src_group,dst_group,pattern,levels,connections,partition"
    foreach key [lsort [array names edgeLev]] {
        lassign [split $key "|"] sg dg
        puts $pairF "inter,$sg,$dg,$edgePat($key),$edgeLev($key),$edgeCnt($key),$gPart($sg)"
    }
    foreach g [lsort [array names intraLev]] {
        puts $pairF "intra,$g,$g,$intraPat($g),$intraLev($g),$intraCnt($g),$gPart($g)"
    }
    close $pairF

    puts $clF "cluster_id,uc_group,type,num_groups,num_macros,num_instances,capped,partition,max_levels,score,pattern_mix"
    puts $applyF "## Auto-generated USER_CLUSTER macro-cluster placer tags (read-only generator output)."
    puts $applyF "## Tags the DESIGNER instance where the XPM/memory is instantiated (a few levels up"
    puts $applyF "## from the leaf BRAM), NOT the primitive. All source + destination memory instances"
    puts $applyF "## of one cluster share the same uc_grp_<N>. Review, then source before place_design."
    puts $applyF ""
    set cid 0 ; set uci 0 ; set nclusters 0 ; set nself 0 ; set ninter 0 ; set taggedMacros 0 ; set ninst 0
    foreach root [lsort [array names clGroups]] {
        set groups $clGroups($root)
        set nG [llength $groups]
        set selfcrit 0
        foreach g $groups { if {[info exists intraLev($g)]} { set selfcrit 1 ; break } }
        # emit if it joins >=2 arrays (inter cluster) or a single self-critical array
        if {$nG < 2 && !$selfcrit} continue
        incr cid
        set cells {}
        foreach g $groups { foreach c $gMems($g) { lappend cells $c } }
        set nmac [llength $cells]
        # per-cluster max level + score + pattern mix from internal inter AND intra edges
        set maxlev 0 ; set score 0 ; array unset pmix
        foreach key [array names edgeLev] {
            lassign [split $key "|"] sg dg
            if {[::mc::_find $sg] eq $root && [::mc::_find $dg] eq $root} {
                if {$edgeLev($key) > $maxlev} { set maxlev $edgeLev($key) }
                set score [expr {$score + $edgeLev($key)*$edgeCnt($key)}]
                set pmix($edgePat($key)) 1
            }
        }
        foreach g $groups {
            if {[info exists intraLev($g)]} {
                if {$intraLev($g) > $maxlev} { set maxlev $intraLev($g) }
                set score [expr {$score + $intraLev($g)*$intraCnt($g)}]
                set pmix($intraPat($g)) 1
            }
        }
        set ctype [expr {$nG >= 2 ? "inter" : "self"}]
        if {$ctype eq "inter"} { incr ninter } else { incr nself }
        set capped [expr {$nmac > $MAX_CLUSTER_MACROS ? 1 : 0}]
        set part $gPart([lindex $groups 0])
        # distinct designer instances (the tag targets) for this cluster's arrays
        set uinsts {}
        foreach g $groups {
            set ui [::mc::user_instance $g]
            if {[lsearch -exact $uinsts $ui] < 0} { lappend uinsts $ui }
        }
        set grp "uc_grp_$uci"
        puts $clF "$cid,$grp,$ctype,$nG,$nmac,[llength $uinsts],$capped,$part,$maxlev,$score,[join [lsort [array names pmix]] {|}]"
        puts $applyF "# cluster $cid  $grp  ($ctype, $nmac macros, [llength $uinsts] instances, max_levels=$maxlev, score=$score)"
        foreach ui [lsort $uinsts] {
            puts $applyF "set_property USER_CLUSTER $grp \[get_cells {$ui}\]"
        }
        incr uci ; incr ninst [llength $uinsts]
        incr nclusters ; incr taggedMacros $nmac
    }
    close $applyF
    close $clF

    puts $sumF "Macro-cluster (mem -> N*LUT -> mem) analysis"
    puts $sumF "============================================"
    puts $sumF "patterns kept      : $PATTERNS"
    puts $sumF "MIN_LEVELS         : $MIN_LEVELS"
    puts $sumF "MAX_LEVELS         : $MAX_LEVELS"
    puts $sumF "MAX_CLUSTER_MACROS : $MAX_CLUSTER_MACROS"
    puts $sumF "SAME_PARTITION     : $SAME_PARTITION"
    puts $sumF "scope              : [expr {$SCOPE eq {} ? {<whole-design>} : $SCOPE}]"
    puts $sumF "memories           : [llength $mems] (bram=[llength $brams] uram=[llength $urams])"
    puts $sumF "array groups       : [array size gMems]"
    puts $sumF "oversize arrays    : $noversize  (single logical memory > $MAX_CLUSTER_MACROS macros; largest=$maxarr)"
    puts $sumF "sources traversed  : $nsrc"
    puts $sumF "inter-array edges  : [array size edgeLev]"
    puts $sumF "intra-array crit.  : [array size intraLev]  (arrays with an internal mem->LUT->mem path)"
    puts $sumF "edges merged       : $nmerged   (cap-skipped: $ncapskip)"
    puts $sumF "clusters           : $nclusters  (inter=$ninter self=$nself)"
    puts $sumF "macros tagged      : $taggedMacros"
    puts $sumF "elapsed            : [expr {[clock seconds]-$t0}]s"
    close $sumF

    puts "\[mc\] DONE clusters=$nclusters (inter=$ninter self=$nself) taggedMacros=$taggedMacros interEdges=[array size edgeLev] intraArrays=[array size intraLev] merged=$nmerged capskip=$ncapskip  ([expr {[clock seconds]-$t0}]s)"
    puts "\[mc\] outputs in $outdir: macro_clusters_summary.rpt macro_clusters.csv macro_cluster_pairs.csv apply_macro_cluster_tags.tcl"
    return $nclusters
}
