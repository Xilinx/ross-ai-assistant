# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# ============================================================
# ipcfg.tcl - Generic, IP-agnostic helpers for the vivado-ip-configurator skill
#
# Source ONCE per Vivado session, after a block design is open:
#     source <skill_dir>/lib/ipcfg.tcl
#
# Every proc is IP-agnostic: no IP names, VLNVs, or parameter names are
# baked in. All IP-specific values (VLNV, CONFIG dict, automation rule,
# stub pin) are passed in by the caller, who discovers them from
# vivado_doc_search and Vivado's own error/read-back feedback.
#
# Procs return a single structured line the agent parses:
#     SUCCESS:<detail>
#     CONFIGURE_FAIL:<TYPE>:<detail>
# where <TYPE> is one of:
#     CREATE_ERROR PARAM_NOT_FOUND VALUE_OUT_OF_RANGE READ_ONLY
#     NOT_SUPPORTED PARAM_DISABLED AUTOMATION_ERROR STUB_ERROR UNKNOWN
# ============================================================

namespace eval ipcfg {}
set ::ipcfg::source_candidate [apply {{path} {
    set channel [open $path r]
    try {set content [read $channel]} finally {close $channel}
    return [list [file normalize $path] [zlib crc32 $content]]
}} [info script]]
if {[info exists ::ipcfg::loaded_signature]} {
    if {$::ipcfg::loaded_signature ne $::ipcfg::source_candidate} {
        unset ::ipcfg::source_candidate
        error "LIBRARY_FAIL:ipcfg changed; do not hot-reload a live design"
    }
    unset ::ipcfg::source_candidate
    return "SKIP:ipcfg already loaded"
}

namespace eval ipcfg {
    variable bd_file ""
    # Operating mode: "benchmark" (default) = one throwaway cell per prompt,
    # ipcfg::cleanup deletes it between prompts. "assemble" = persistent build
    # (the vivado-ipi-assembler skill): cells must SURVIVE, so ipcfg::cleanup becomes a
    # no-op guard. Set with ipcfg::set_mode. Everything else (create_cell_cfg,
    # verify_intent, audit_intent, discover_params, the learned cache) is reused
    # unchanged across both modes.
    variable mode "benchmark"
    # Requirement ledger: cell -> list of {key want base reason}, pinned BEFORE
    # the first write and audited against the FINAL read-back. verify_intent
    # compares against the dict the caller is holding right now, so intent that
    # drifts during error recovery passes every other check; this survives the
    # retry and is the only thing that catches that.
    variable req {}
    # Board ledger: cell -> {{{CONFIG.KEY value} ...} reason}, recorded by
    # ipcfg::board_bind. These are the values the BOARD FILE wrote, discovered
    # by diffing the cell across the bind rather than from any hard-coded list,
    # so "do not hand-write what the board owns" needs no per-IP knowledge and
    # cannot go stale when a new board or IP arrives.
    variable board_owned {}
    # Board values a pinned requirement deliberately replaced: cell -> list of
    # {key want spec-statement} from require's board_override argument. A board
    variable board_override {}
    # lib dir (resolved at source time) -> locate the cache engine + store
    variable dir [file dirname [file normalize [info script]]]
    variable cache_engine [file join $dir ipcfg_cache.py]
    variable cache_file   [file normalize [file join $dir .. cache learned_params.json]]
    # Resolved lazily and cached per Vivado process. The cache key includes all
    # environment inputs so an explicit override takes effect immediately.
    variable python_runtime_cache [dict create]
    variable python_runtime_key {}
}

# --- Shared Python runtime ---------------------------------------------------
# Prefer the interpreter distributed with the active Vivado release so a host
# Python installation is not required. IPCFG_PYTHON is an explicit escape hatch;
# host python3 is only the final fallback.
proc ipcfg::_python_env_key {} {
    set key [list $::tcl_platform(platform)]
    foreach name {IPCFG_PYTHON IPCFG_VIVADO_ROOT XILINX_VIVADO VIVADO_PATH PATH IPCFG_REQUIRE_BUNDLED} {
        if {[info exists ::env($name)]} {
            lappend key $name $::env($name)
        } else {
            lappend key $name <unset>
        }
    }
    return $key
}

proc ipcfg::python_reset {} {
    variable python_runtime_cache
    variable python_runtime_key
    set python_runtime_cache [dict create]
    set python_runtime_key {}
    return "PYTHON:RESET"
}

proc ipcfg::_python_restore_env {saved} {
    dict for {name state} $saved {
        lassign $state existed value
        if {$existed} {
            set ::env($name) $value
        } else {
            unset -nocomplain ::env($name)
        }
    }
}

proc ipcfg::_python_invoke {runtime arguments} {
    set names {PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT}
    if {$::tcl_platform(platform) eq "windows"} {
        lappend names PATH
    } else {
        lappend names LD_LIBRARY_PATH
    }
    set saved [dict create]
    foreach name $names {
        if {[info exists ::env($name)]} {
            dict set saved $name [list 1 $::env($name)]
        } else {
            dict set saved $name [list 0 ""]
        }
    }
    try {
        foreach name {PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT} {
            unset -nocomplain ::env($name)
        }
        set root [dict get $runtime root]
        if {$root ne ""} {
            if {$::tcl_platform(platform) eq "windows"} {
                set prefix [join [list $root [file join $root DLLs]] ";"]
                set old [expr {[info exists ::env(PATH)] ? $::env(PATH) : ""}]
                set ::env(PATH) [expr {$old eq "" ? $prefix : "$prefix;$old"}]
            } else {
                set lib [file join $root lib]
                set old [expr {
                    [info exists ::env(LD_LIBRARY_PATH)]
                    ? $::env(LD_LIBRARY_PATH) : ""
                }]
                set ::env(LD_LIBRARY_PATH) [expr {$old eq "" ? $lib : "$lib:$old"}]
            }
        }
        return [exec {*}[linsert $arguments 0 [dict get $runtime executable]]]
    } finally {
        ipcfg::_python_restore_env $saved
    }
}

proc ipcfg::_python_probe {executable root source} {
    set runtime [dict create executable $executable root $root source $source]
    set probe {
import argparse, base64, hashlib, json, pathlib, re, sys, typing
rank = {"alpha": 0, "beta": 1, "candidate": 2, "final": 3}[sys.version_info.releaselevel]
score = (((sys.version_info.major * 1000 + sys.version_info.minor) * 1000
          + sys.version_info.micro) * 10 + rank) * 1000 + sys.version_info.serial
print("%s\t%d\t%d\t%d" % (
    ".".join(map(str, sys.version_info[:3]))
    + ("" if sys.version_info.releaselevel == "final" else
       sys.version_info.releaselevel + str(sys.version_info.serial)),
    sys.version_info.major, sys.version_info.minor, score))
}
    set out [string trim [ipcfg::_python_invoke $runtime [list -c $probe]]]
    lassign [split $out "\t"] version major minor score
    if {![string is integer -strict $major]
        || ![string is integer -strict $minor]
        || ![string is wideinteger -strict $score]} {
        error "unparseable version response '$out'"
    }
    if {$major < 3 || ($major == 3 && $minor < 10)} {
        error "Python $version is too old; 3.10+ is required"
    }
    dict set runtime version $version
    dict set runtime score $score
    return $runtime
}

proc ipcfg::_python_vivado_roots {} {
    set roots {}
    foreach name {IPCFG_VIVADO_ROOT XILINX_VIVADO} {
        if {[info exists ::env($name)] && $::env($name) ne ""} {
            lappend roots $::env($name)
        }
    }
    if {[info exists ::env(VIVADO_PATH)] && $::env(VIVADO_PATH) ne ""} {
        set path $::env(VIVADO_PATH)
        if {[file tail [file dirname $path]] eq "bin"} {
            set path [file dirname [file dirname $path]]
        }
        lappend roots $path
    }
    if {[llength $roots] == 0} {
        set executable [lindex [auto_execok vivado] 0]
        if {$executable ne "" && [file tail [file dirname $executable]] eq "bin"} {
            lappend roots [file dirname [file dirname $executable]]
        }
    }
    set out {}
    foreach root $roots {
        if {[catch {set normalized [file normalize $root]}]} {
            set normalized $root
        }
        if {[lsearch -exact $out $normalized] < 0} {
            lappend out $normalized
        }
    }
    return $out
}

proc ipcfg::_python_bundle_candidates {} {
    set candidates {}
    foreach vivado [ipcfg::_python_vivado_roots] {
        if {$::tcl_platform(platform) eq "windows"} {
            set bundle_dirs [glob -nocomplain \
                [file join $vivado tps win64 python-*]]
            set names {python.exe bin/python.exe}
        } else {
            set bundle_dirs [glob -nocomplain \
                [file join $vivado tps lnx64 python-*]]
            set names {bin/python3 bin/python}
        }
        foreach bundle_dir $bundle_dirs {
            foreach name $names {
                set executable [file join $bundle_dir {*}[split $name /]]
                if {![file executable $executable]} {
                    continue
                }
                set root [file dirname [file dirname $executable]]
                if {$::tcl_platform(platform) eq "windows"
                    && [file tail [file dirname $executable]] ne "bin"} {
                    set root [file dirname $executable]
                }
                lappend candidates [list $executable $root]
                break
            }
        }
    }
    return $candidates
}

proc ipcfg::python_runtime {} {
    variable python_runtime_cache
    variable python_runtime_key
    set key [ipcfg::_python_env_key]
    if {$key eq $python_runtime_key
        && [dict exists $python_runtime_cache executable]} {
        return $python_runtime_cache
    }

    set attempted {}
    if {[info exists ::env(IPCFG_PYTHON)] && $::env(IPCFG_PYTHON) ne ""} {
        set executable $::env(IPCFG_PYTHON)
        set resolved [auto_execok $executable]
        if {$resolved ne ""} { set executable [lindex $resolved 0] }
        set root [file dirname [file dirname $executable]]
        if {![file isdirectory [file join $root lib]]
            && ![file isdirectory [file join $root DLLs]]} {
            set root ""
        }
        if {[catch {
            set runtime [ipcfg::_python_probe $executable $root override]
        } detail]} {
            error "PYTHON_RUNTIME_FAIL:IPCFG_PYTHON=$::env(IPCFG_PYTHON): $detail"
        }
        set python_runtime_cache $runtime
        set python_runtime_key $key
        return $runtime
    }

    set best [dict create]
    foreach candidate [ipcfg::_python_bundle_candidates] {
        lassign $candidate executable root
        if {[catch {
            set runtime [ipcfg::_python_probe $executable $root vivado]
        } detail]} {
            lappend attempted "$executable ($detail)"
            continue
        }
        if {![dict exists $best score]
            || [dict get $runtime score] > [dict get $best score]} {
            set best $runtime
        }
    }
    if {[dict exists $best executable]} {
        set python_runtime_cache $best
        set python_runtime_key $key
        return $best
    }

    if {[info exists ::env(IPCFG_REQUIRE_BUNDLED)] && $::env(IPCFG_REQUIRE_BUNDLED) eq "1"} {
        error "PYTHON_RUNTIME_FAIL:no compatible Vivado Python bundle; set IPCFG_VIVADO_ROOT to the installed Vivado root"
    }
    foreach command {python3 python} {
        set executable [auto_execok $command]
        if {$executable eq ""} { continue }
        set executable [lindex $executable 0]
        if {[catch {
            set runtime [ipcfg::_python_probe $executable "" host]
        } detail]} {
            lappend attempted "$executable ($detail)"
            continue
        }
        set python_runtime_cache $runtime
        set python_runtime_key $key
        return $runtime
    }
    set roots [ipcfg::_python_vivado_roots]
    error "PYTHON_RUNTIME_FAIL:no Python 3.10+; vivado_roots={$roots} attempted={$attempted}"
}

proc ipcfg::python_exe {} {
    return [dict get [ipcfg::python_runtime] executable]
}

proc ipcfg::python_exec {args} {
    return [ipcfg::_python_invoke [ipcfg::python_runtime] $args]
}

proc ipcfg::python_script {script args} {
    if {[file pathtype $script] ne "absolute" || ![file isfile $script] ||
        [file extension $script] ne ".py"} {
        error "PYTHON_SCRIPT_FAIL:expected an existing absolute .py script path: $script"
    }
    return [ipcfg::python_exec $script {*}$args]
}

proc ipcfg::write_evidence {path data} {
    if {[file pathtype $path] ne "absolute"} {
        error "EVIDENCE_FAIL:expected an absolute output path"
    }
    set path [file normalize $path]
    set record [dict create schema_version 1 data $data]
    set channel [open $path {WRONLY CREAT EXCL}]
    try {
        fconfigure $channel -encoding utf-8 -translation lf
        puts -nonewline $channel $record
    } finally {
        close $channel
    }
    return [dict create status RECORDED bytes [file size $path] evidence_path $path]
}

proc ipcfg::_message_snapshot {path} {
    write_messages -severity OUTPUT $path
    set channel [open $path r]
    try {
        fconfigure $channel -encoding utf-8
        set text [read $channel]
    } finally {close $channel}
    if {![regsub {^\n#+\n# Generated by Vivado[^\n]*\n# Command Used: [^\n]*\n#+\n\n} $text {} text]} {
        error "CAPTURE_FAIL:unrecognized write_messages header: $path"
    }
    return $text
}

proc ipcfg::result_status {raw prefixes} {
    if {[regexp -nocase {(^|[\s:;])([A-Z0-9_]*FAIL(ED|URE)?|ERROR|PENDING|PARTIAL)([\s:;]|$)|CRITICAL WARNING} $raw]} {return FAIL}
    if {![regexp {^([A-Z][A-Z0-9_]*):([^\s:]+)} $raw -> family token]} {return FAIL}
    set positive {OK PASS SET WIRED RESTRICTED pinned+applied bound}
    set freeform {SUCCESS SKIP STAGE READY CHECKPOINT}
    if {$family eq "POLICY"} {set positive {attended unattended}}
    if {$family ni $freeform && $token ni $positive && $token ne "NA"} {return FAIL}
    if {$family ni $freeform && ![regexp {^[A-Z][A-Z0-9_]*:[^\s:]+(\s|$)} $raw]} {return FAIL}
    foreach prefix $prefixes {
        if {[string first $prefix $raw] != 0} {continue}
        if {[string index $prefix end] ne ":"} {
            set next [string index $raw [string length $prefix]]
            if {$next ne "" && ![string is space $next] && $next ne ":"} {continue}
        }
        if {$token eq "NA"} {
            if {$prefix eq "${family}:NA"} {return NA}
            continue
        }
        return PASS
    }
    return FAIL
}

proc ipcfg::_message_counts {} {
    set counts {}
    foreach severity {WARNING {CRITICAL WARNING} ERROR} {
        set count [get_msg_config -severity $severity -count]
        if {![string is integer -strict $count] || $count < 0} {error "CAPTURE_FAIL:invalid native message count"}
        dict set counts $severity $count
    }
    return $counts
}

proc ipcfg::capture {path script {expect {}}} {
    if {[file pathtype $path] ne "absolute"} {error "CAPTURE_FAIL:absolute evidence path required"}
    if {![llength [info commands write_messages]]} {error "CAPTURE_FAIL:Vivado write_messages unavailable"}
    set path [file normalize $path]
    set channel [open $path {WRONLY CREAT EXCL}]
    set started [clock milliseconds]
    set record [dict create status FAIL code 1 raw {} options {} warning_count 0 \
        critical_count 0 error_count 0 messages {} evidence_path $path \
        before_log $path.before.log log_path $path.after.log]
    try {
        fconfigure $channel -encoding utf-8 -translation lf
        set capture_code [catch {
            set before [ipcfg::_message_snapshot $path.before.log]
            set native_log {}
            if {[info exists ::ipcfg::native_log_path] && $::ipcfg::native_log_path ne ""} {
                set native_log $::ipcfg::native_log_path
                if {[file pathtype $native_log] ne "absolute"} {error "CAPTURE_FAIL:absolute native log path required"}
                file stat $native_log log_before
            }
            set counts_before [ipcfg::_message_counts]
            set code [catch {uplevel 1 $script} raw options]
            dict set record code $code
            dict set record raw $raw
            dict set record options $options
            set counts_after [ipcfg::_message_counts]
            dict set record native_counts_before $counts_before
            dict set record native_counts_after $counts_after
            set after [ipcfg::_message_snapshot $path.after.log]
            # Vivado can insert late messages into earlier history; the native log slice below is then the source.
            if {$before ne "" && [string first $before $after] != 0} {
                if {$native_log eq {}} {error "CAPTURE_FAIL:message history changed"}
                dict set record history_reordered 1
            }
            set delta [string range $after [string length $before] end]
            dict set record output $delta
            if {$native_log ne {}} {
                file stat $native_log log_after
                if {$log_before(ino) != $log_after(ino) || $log_before(dev) != $log_after(dev) || $log_after(size) < $log_before(size)} {
                    error "CAPTURE_FAIL:native log replaced or truncated"
                }
                set log_channel [open $native_log rb]
                try {
                    seek $log_channel $log_before(size)
                    set log_bytes [read $log_channel [expr {$log_after(size) - $log_before(size)}]]
                } finally {close $log_channel}
                set log_channel [open $path.native.log {WRONLY CREAT EXCL}]
                try {
                    fconfigure $log_channel -translation binary
                    puts -nonewline $log_channel $log_bytes
                } finally {close $log_channel}
                set delta [encoding convertfrom utf-8 $log_bytes]
                if {[dict exists $record history_reordered]} {dict set record output $delta}
                dict set record native_log $path.native.log
                dict set record native_log_source $native_log
                dict set record native_log_offsets [list $log_before(size) $log_after(size)]
            }
            set unsafe 0
            foreach line [split $delta \n] {
                if {![regexp -nocase {^\s*(ERROR|CRITICAL[ _]WARNING|WARNING)\s*:} $line -> severity]} {continue}
                dict lappend record messages $line
                switch -- [string toupper $severity] {
                    ERROR {dict incr record error_count; set unsafe 1}
                    WARNING {
                        dict incr record warning_count
                        # A subsystem re-deriving a core inside its own generated design
                        # ('<ip>/bd_xxxx/...') is not a caller's write being dropped.
                        if {[regexp {IP_Flow 19-(7090|3374)\].*for IP '[^']*/[^']*'} $line]} {
                            dict lappend record subcore_ignored $line
                        } elseif {[regexp -nocase {ignoring|ignored|does not exist|BD 41-1276|IP_Flow 19-(7090|3374)} $line]} {set unsafe 1}
                    }
                    default {dict incr record critical_count; set unsafe 1}
                }
            }
            set missing {}
            foreach {severity field} {WARNING warning_count {CRITICAL WARNING} critical_count ERROR error_count} {
                set emitted [expr {[dict get $counts_after $severity] - [dict get $counts_before $severity]}]
                if {$severity eq "WARNING" && $native_log ne {} && $emitted >= [dict get $record $field]} {
                    dict set record unprinted_warning_count [expr {$emitted - [dict get $record $field]}]
                    continue
                }
                if {$emitted != [dict get $record $field]} {
                    lappend missing "$severity emitted=$emitted exported=[dict get $record $field]"
                    if {$emitted > [dict get $record $field]} {dict set record $field $emitted}
                }
            }
            if {[llength $missing]} {error "CAPTURE_FAIL:native message export incomplete or counters changed: [join $missing {; }]"}
            dict set record expected $expect
            if {$expect ne {} && [ipcfg::result_status $raw $expect] eq "FAIL"} {
                set unsafe 1
                set miss "result does not match expected {$expect}: got '[string range $raw 0 80]'"
                if {$code == 0 && [regexp {^[A-Z][A-Z0-9_]*:OK\M} $raw]} {
                    # A different success token is a caller mistake, not a design failure.
                    append miss " -> the command succeeded with another success token; correct the expected prefix and rerun the capture, do not repair the design or count this as a design attempt"
                }
                dict set record expect_miss $miss
            }
            if {$code == 0 && !$unsafe} {dict set record status OK}
        } capture_error capture_options]
        if {$capture_code} {
            dict set record status FAIL
            dict set record capture_error $capture_error
            dict set record capture_options $capture_options
        }
        dict set record elapsed_ms [expr {[clock milliseconds] - $started}]
        puts -nonewline $channel [dict create schema_version 1 data $record]
    } finally {close $channel}
    set receipt [dict remove $record raw options output messages capture_options]
    set excerpt [join [dict get $record messages] {; }]
    if {[dict exists $record expect_miss]} {set excerpt "[dict get $record expect_miss] $excerpt"}
    if {[dict get $record status] eq "FAIL" && $excerpt eq ""} {set excerpt [dict get $record raw]}
    dict set receipt excerpt [string range [string map [list \n { } \r { }] $excerpt] 0 299]
    return $receipt
}

proc ipcfg::python_status {} {
    if {[catch {set runtime [ipcfg::python_runtime]} detail]} {
        return $detail
    }
    return "PYTHON:OK source=[dict get $runtime source]\
version=[dict get $runtime version] executable=[dict get $runtime executable]"
}

# --- Operating-mode control (benchmark vs assemble) ---
# In "assemble" mode the persistent design is being built, so destructive
# cleanup is refused. Returns the active mode.
proc ipcfg::set_mode {m} {
    variable mode
    if {$m ni {benchmark assemble}} {
        return "ERR:bad mode '$m' (expected benchmark|assemble)"
    }
    set mode $m
    return "SUCCESS:mode=$mode"
}
proc ipcfg::get_mode {} {
    variable mode
    return $mode
}

# --- internal: resolve the .bd file (for close/reopen during a part swap) ---
proc ipcfg::_bd_file {} {
    variable bd_file
    if {$bd_file ne ""} { return $bd_file }
    set bf [get_files -quiet *.bd]
    if {[llength $bf] > 0} { return [lindex $bf 0] }
    return "benchmark_bd.bd"
}

# Optionally pin the bd file name explicitly (else it is auto-detected).
proc ipcfg::set_bd_file {f} {
    variable bd_file
    set bd_file $f
    return "SUCCESS:bd_file=$f"
}

# --- internal: light value normalization for stuck-value comparison ---
proc ipcfg::_norm {v} {
    set v [string trim $v]
    set v [string tolower $v]
    if {$v eq "true"}  {set v 1}
    if {$v eq "false"} {set v 0}
    # collapse trailing zeros on decimals: 100.000 -> 100
    if {[regexp {^-?[0-9]+\.[0-9]+$} $v]} {
        set v [string trimright $v 0]
        set v [string trimright $v .]
    }
    return $v
}

proc ipcfg::_is_num {s} { return [string is double -strict $s] }

# --- internal: is `cur` a legal NEIGHBOR of the requested `want`? ---
# Diagnostic only (NEAREST): it explains a miss, it never accepts one.
#   - validset given: cur is a member but want is not (IP picked nearest legal)
#   - else both numeric: within 0.5 absolute or 1% relative of want
proc ipcfg::_neighbor {cur want {validset {}}} {
    if {[llength $validset] > 0} {
        set inset 0; set wantinset 0
        foreach m $validset {
            set nm [ipcfg::_norm $m]
            if {$nm eq $cur}  {set inset 1}
            if {$nm eq $want} {set wantinset 1}
        }
        if {$inset && !$wantinset} { return 1 }
    }
    if {[ipcfg::_is_num $cur] && [ipcfg::_is_num $want]} {
        set d [expr {abs(double($cur) - double($want))}]
        set denom [expr {abs($want) > 0 ? abs(double($want)) : 1.0}]
        if {$d <= 0.5 || ($d / $denom) <= 0.01} { return 1 }
    }
    return 0
}

# --- internal: a requirement's explicit tolerance: "" (exact), <abs>, or <n>% ---
proc ipcfg::_tolerance_ok {tolerance} {
    if {$tolerance eq ""} { return 1 }
    set number [expr {[string index $tolerance end] eq "%" ? [string range $tolerance 0 end-1] : $tolerance}]
    return [expr {[string is double -strict $number] && $number >= 0}]
}

proc ipcfg::_within_tolerance {cur want tolerance} {
    if {$tolerance eq "" || ![ipcfg::_is_num $cur] || ![ipcfg::_is_num $want]} { return 0 }
    set d [expr {abs(double($cur) - double($want))}]
    if {[string index $tolerance end] eq "%"} {
        return [expr {$d <= abs(double($want)) * double([string range $tolerance 0 end-1]) / 100.0}]
    }
    return [expr {$d <= double($tolerance)}]
}

# --- Exact-vs-miss classification ---
# Classify what happened to ONE key after an apply, using its pre-apply default.
#   want: requested value ("" => the key was NOT requested - a sibling scan)
#   base: pre-apply (default) value     cur: post-apply value
#   tolerance: "" (exact, the default) or the requirement's own <abs> / <n>%
# Returns for requested keys:
#   EXACT    - cur == want
#   RESOLVED - numeric, within the tolerance the requirement supplied
#   NEAREST  - a MISS: the IP snapped to a nearby legal value (diagnostic)
#   REVERTED - cur snapped back to default, or landed somewhere unrelated
# and for unrequested keys (want==""):  UNCHANGED | CHANGED (side effect).
proc ipcfg::classify_change {want base cur {validset {}} {tolerance ""}} {
    set nb [ipcfg::_norm $base]
    set nc [ipcfg::_norm $cur]
    if {$want eq ""} { return [expr {$nc eq $nb ? "UNCHANGED" : "CHANGED"}] }
    set nw [ipcfg::_norm $want]
    if {$nc eq $nw}                                  { return "EXACT" }
    if {[ipcfg::_within_tolerance $nc $nw $tolerance]} { return "RESOLVED" }
    if {[ipcfg::_neighbor $nc $nw $validset]}        { return "NEAREST" }
    return "REVERTED"
}

proc ipcfg::_miss_text {class cur want} {
    return [expr {$class eq "NEAREST" ? "nearest-legal:=$cur,want=$want" : "reverted:=$cur,want=$want"}]
}

# --- Phase 0: part swap / restore (close + reopen the bd around the change) ---
# Guarded: a part swap is destructive to a design-in-progress (existing IP may be
# incompatible with / need upgrading for the new part). If other bd_cells already
# exist and force==0, this REFUSES to swap and returns a WARN so the caller can
# confirm with the user (design mode) before forcing. On an empty/isolated BD it
# swaps freely (benchmark mode uses throwaway cells).
proc ipcfg::ensure_part {target {force 0}} {
    set orig [get_property PART [current_project]]
    if {$orig eq $target} { return "NOSWAP:$orig" }
    set others [llength [get_bd_cells -quiet]]
    if {$others > 0 && !$force} {
        return "WARN:SWAP_BLOCKED:$others existing cell(s) may be part-incompatible; confirm before swapping $orig->$target (call with force=1 to override)"
    }
    set bd [ipcfg::_bd_file]
    close_bd_design [current_bd_design]
    set_property PART $target [current_project]
    open_bd_design $bd
    return "SWAP:$orig->$target"
}

# Guarded in the mirror image of ensure_part: restoring a part that cannot
# support a cell built on the swapped part silently DROPS that cell. The IP you
# were asked to build is then simply gone -- and since the design is graded from
# a read-back taken AFTER you finish, a run that swapped, built and configured
# correctly scores zero with nothing in any error message to explain it. So
# refuse, and say which cells would be lost.
proc ipcfg::restore_part {orig {force 0}} {
    if {[get_property PART [current_project]] eq $orig} { return "NOSWAP:$orig" }
    if {!$force} {
        set doomed {}
        foreach c [get_bd_cells -quiet] {
            set v [get_property -quiet VLNV $c]
            if {$v eq ""} { continue }
            set sp [get_property -quiet SUPPORTED_PARTS [get_ipdefs -quiet $v]]
            if {[llength $sp] && [lsearch -exact $sp $orig] < 0} {
                lappend doomed [get_property NAME $c]
            }
        }
        if {[llength $doomed]} {
            return "WARN:RESTORE_BLOCKED:restoring $orig would DROP $doomed\
(unsupported on that part). Delete them first (ipcfg::cleanup <cell> $orig, which\
orders it correctly) or leave the part swapped -- the design is read back after\
you finish, so a cell dropped here is a cell you never built. force=1 to override"
        }
    }
    set bd [ipcfg::_bd_file]
    close_bd_design [current_bd_design]
    set_property PART $orig [current_project]
    open_bd_design $bd
    return "RESTORED:$orig"
}

# --- Phase 1: VLNV pre-validation (version-free prefix, e.g. xilinx.com:ip:axi_gpio) ---
# CATALOG MEMBERSHIP ONLY -- this is NOT a part-availability test. `get_ipdefs`
# lists IPs the current part cannot instantiate: on 2026.1/Versal Gen2 this returns
# 1 for clk_wizard, which then fails create_bd_cell with [BD 5-683]. Use
# ipcfg::ip_availability for the part question.
proc ipcfg::vlnv_ok {vlnv} {
    if {[llength [split $vlnv :]] == 4} {
        return [expr {[llength [get_ipdefs -quiet $vlnv]] > 0}]
    }
    return [expr {[llength [get_ipdefs -quiet -filter "VLNV =~ \"$vlnv:*\""]] > 0}]
}

# --- Phase 1 (gate): part-aware availability over a CANDIDATE SHORTLIST ---
# vlnv_ok is a validator (is the name I already picked present?); this is a
# SELECTOR aid, and it separates two failures a single boolean conflates:
#
#   ABSENT     -- not in this release's catalog at all: renamed or dropped
#                 (2026.1 ps11 -> ps_wizard). Reports catalog neighbours.
#   WRONG_PART -- in the catalog, but the part cannot instantiate it. This is
#                 the create-time [BD 5-683], predicted BEFORE the wasted create.
#                 Reports concrete parts that DO support it.
#   AVAILABLE  -- instantiable here and now.
#   UNKNOWN    -- the ipdef publishes no SUPPORTED_PARTS. Deliberately NOT
#                 reported as WRONG_PART: a false WRONG_PART would trigger a
#                 destructive part swap that was never needed.
#
# The verdict is the ipdef's own SUPPORTED_PARTS list, which on 2026.1 predicts
# [BD 5-683] exactly (verified: clk_wizard excludes a Gen2 xc2ve part and fails to
# create; clkx5_wiz includes it and creates). No family-regex parsing, no
# component.xml scraping, no static part map.
#
# `candidates` is the shortlist you are choosing BETWEEN -- pass all of it. A
# single pre-chosen name reduces this to a validator and defeats the point, so
# entries may instead be catalog GLOBS (`*clk*wiz*`), which are expanded against
# the release's catalog: that way the shortlist is DISCOVERED from the prompt's
# function words rather than pre-committed to the first name that came to mind.
# A one-name shortlist is reported as such (`SHORTLIST_OF_1`) unless an explicit
# `part` was passed, which marks the call as a swap-target confirmation.
#
# `part` defaults to the project's, and can be set to a prospective swap target
# to confirm it BEFORE the destructive swap.
#
# Returns ONE line (a Tcl dict after the "IPAVAIL:" tag):
#   IPAVAIL: part=<p> clk_wizard=WRONG_PART:xilinx.com:ip:clk_wizard:1.0:parts={xcvc1902-... ...}:devices=63 \
#            clkx5_wiz=AVAILABLE:xilinx.com:ip:clkx5_wiz:1.0 ps11=ABSENT:near={xilinx.com:ip:ps_wizard:1.0 ...}
proc ipcfg::ip_availability {candidates {part ""} {maxparts 8}} {
    set confirming [expr {$part ne ""}]
    if {$part eq ""} {
        if {[catch {set part [get_property PART [current_project]]}]} {
            return "IPAVAIL_ERR:no current project and no part given"
        }
    }
    # Trailing grade/speed tokens of the current part (e.g. 2MP-e-S). A swap
    # target that keeps them changes only the device, which is the smallest
    # move that can make the IP instantiable.
    set grade [join [lrange [split $part -] 2 end] -]
    set maxcand 16
    set names {}
    set nomatch {}
    foreach cand $candidates {
        set n [ipcfg::_ip_name $cand]
        if {![string match {*[*?]*} $n]} {
            if {$n ni $names} { lappend names $n }
            continue
        }
        set hits {}
        foreach d [lsort [get_ipdefs -quiet *:${n}:*]] {
            set nm [lindex [split $d :] 2]
            if {$nm ni $hits} { lappend hits $nm }
        }
        if {![llength $hits]} { lappend nomatch $n; continue }
        foreach nm $hits { if {$nm ni $names} { lappend names $nm } }
    }
    set extra 0
    if {[llength $names] > $maxcand} {
        set extra [expr {[llength $names] - $maxcand}]
        set names [lrange $names 0 [expr {$maxcand - 1}]]
    }
    set out {}
    set wrongpart {}
    set available {}
    foreach name $names {
        set r [ipcfg::resolve_vlnv $name]
        if {[string match "VLNV_CANDIDATES:*" $r]} {
            lappend out "$name=ABSENT:near=[string range $r 16 end]"
            continue
        }
        if {[string match "VLNV_NONE:*" $r]} {
            lappend out "$name=ABSENT:near={}"
            continue
        }
        set vlnv [string range $r 5 end]
        set sp [get_property -quiet SUPPORTED_PARTS [get_ipdefs -quiet $vlnv]]
        if {[llength $sp] == 0} {
            lappend out "$name=UNKNOWN:$vlnv"
        } elseif {[lsearch -exact $sp $part] >= 0} {
            lappend out "$name=AVAILABLE:$vlnv"
            lappend available $name
            set disp($name) [get_property -quiet DISPLAY_NAME [get_ipdefs -quiet $vlnv]]
        } else {
            lappend out "$name=WRONG_PART:$vlnv:parts={[ipcfg::_suggest_parts $sp $grade $maxparts]}:devices=[llength [ipcfg::_devices $sp]]"
            lappend wrongpart $name
            set disp($name) [get_property -quiet DISPLAY_NAME [get_ipdefs -quiet $vlnv]]
        }
    }
    foreach n $nomatch { lappend out "$n=NO_CATALOG_MATCH" }
    # Same DISPLAY_NAME + disjoint part support = ONE IP split across device
    # generations, not two competing IPs. 2026.1 ships "Clocking Wizard" twice:
    # clk_wizard covers Versal Gen1 (0 xc2ve parts) and clkx5_wiz covers Gen2
    # (375 xc2ve parts, none of Gen1's). On a Gen2 part the AVAILABLE one IS the
    # documented IP for that device, so swapping away to reach the other name
    # would be downgrading the device to use an older edition of the same core.
    # This must be settled here, because from the outside the pair looks exactly
    # like the wrong-IP substitution the next clause exists to prevent.
    set variants {}
    set genuine {}
    foreach w $wrongpart {
        set dw [string trim [expr {[info exists disp($w)] ? $disp($w) : ""}]]
        set matched ""
        foreach a $available {
            set da [string trim [expr {[info exists disp($a)] ? $disp($a) : ""}]]
            if {$dw ne "" && [string equal -nocase $dw $da]} { set matched $a; break }
        }
        if {$matched ne ""} { lappend variants "$w~$matched" } else { lappend genuine $w }
    }
    if {[llength $variants]} {
        lappend out "VARIANT_OF:{$variants}:same DISPLAY_NAME -- these are the SAME\
IP partitioned by device generation, so the AVAILABLE one is the correct IP for\
this part: USE IT and do NOT swap. (Swapping would move to an older device\
generation to reach an older edition of the same core.)"
    }
    # Whatever is left is the real hazard: a candidate that needs a part swap
    # sitting next to a DIFFERENT IP that happens to work here. A bare verdict
    # table reads as an invitation to take the one that works, so the rule has to
    # travel WITH the facts rather than live only in the skill prose.
    if {[llength $genuine] && [llength $available]} {
        lappend out "SELECT_RULE:{$genuine} need a part swap while {$available}\
work here, and they are DIFFERENT IPs (different DISPLAY_NAME) -- NOT\
interchangeable. Decide from DOCUMENTATION which one implements the requested\
function; if that one is WRONG_PART, SWAP THE PART (ipcfg::ensure_part -- it\
proceeds on its own when the BD holds no other cells). WRONG_PART is a DEVICE\
mismatch, not a capability limit: it is NOT grounds for a negative result and the\
WRONG_PART IP is NOT 'unavailable to you'. Taking the AVAILABLE one instead is a\
wrong-IP bug that NEVER errors -- it builds, configures and verifies clean"
    }
    if {$extra > 0} { lappend out "TRUNCATED=$extra" }
    # A one-name shortlist is the misuse this proc exists to prevent: it can only
    # confirm a name already chosen, so an IP that is WRONG_PART here is never
    # even compared against. Say so, and name the cheap correct call. Suppressed
    # when a part was passed, since that call IS a single-name confirmation.
    if {!$confirming && [llength $names] < 2} {
        lappend out "SHORTLIST_OF_1:validator-only-call:re-run with every\
candidate you are choosing between, or a catalog glob (e.g. {*clk*wiz*}), so the\
IP is chosen by documented function against what this release ships"
    }
    return "IPAVAIL: part=$part [join $out " "]"
}

# --- internal: bare IP name from a name or a (possibly partial) VLNV ---
proc ipcfg::_ip_name {hint} {
    set h [string trim $hint]
    if {[string first : $h] >= 0} {
        set p [split $h :]
        if {[llength $p] >= 3} { return [lindex $p 2] }
    }
    return $h
}

# --- internal: distinct device stems in a parts list (xcvc1902-vsva2197-... -> xcvc1902) ---
proc ipcfg::_devices {parts} {
    set d {}
    foreach p $parts {
        set s [lindex [split $p -] 0]
        if {$s ni $d} { lappend d $s }
    }
    return $d
}

# --- internal: compact swap-target suggestions from a SUPPORTED_PARTS list ---
# A supported list runs to ~1600 entries across ~60 devices, which is useless as
# a report. Narrow to parts carrying the current part's grade (so only the device
# changes), then one per device so the caller sees real alternatives rather than
# ten packages of the same chip. Falls back to the raw head when no grade matches.
proc ipcfg::_suggest_parts {parts grade max} {
    set pool $parts
    if {$grade ne ""} {
        set matched [lsearch -all -inline $parts *-$grade]
        if {[llength $matched]} { set pool $matched }
    }
    set seen {}
    set out {}
    foreach p [lsort $pool] {
        set s [lindex [split $p -] 0]
        if {$s in $seen} { continue }
        lappend seen $s
        lappend out $p
        if {[llength $out] >= $max} { break }
    }
    return $out
}

proc ipcfg::attempt_call {cell operation script} {
    set project [current_project]
    set design [current_bd_design]
    if {$project eq "" || $design eq ""} {
        error "CONFIGURE_FAIL:ATTEMPT_STATE:no active project/BD; user intervention required"
    }
    set directory [file join [get_property DIRECTORY $project] .ipcfg]
    set ledger [file join $directory attempts.tcldict]
    set resolved [get_bd_cells -quiet $cell]
    if {[llength $resolved] > 1} {
        error "CONFIGURE_FAIL:ATTEMPT_STATE:ambiguous cell $cell"
    }
    if {[llength $resolved] == 1} {
        set identity [lindex $resolved 0]
    } elseif {[string match {/*} $cell]} {
        set identity $cell
    } else {
        set identity "[string trimright [current_bd_instance .] /]/$cell"
    }
    set identity /[string trimleft $identity /]
    if {[info exists ::ipcfg::repair_guard]} {
        uplevel #0 [list {*}$::ipcfg::repair_guard $identity $operation]
        return [uplevel 1 $script]
    }
    set key [list $design $identity]
    if {[catch {
        file mkdir $directory
        set lock [open ${ledger}.lock {WRONLY CREAT EXCL}]
    } diagnostic]} {
        error "CONFIGURE_FAIL:ATTEMPT_STATE:$diagnostic; user intervention required"
    }
    set code [catch {
        set state {}
        if {[file exists $ledger]} {
            set channel [open $ledger r]
            try { set state [read $channel] } finally { close $channel }
            dict size $state
            dict for {stored_key record} $state {
                set count [dict get $record count]
                if {![string is integer -strict $count] || $count < 1 || $count > 10} {
                    error "invalid attempt count for $stored_key"
                }
                dict get $record diagnostic
            }
        }
        set count 0
        set history {}
        if {[dict exists $state $key]} { set count [dict get $state $key count] }
        if {[dict exists $state $key history]} { set history [dict get $state $key history] }
        if {$count >= 10} {
            error "CONFIGURE_FAIL:ATTEMPT_LIMIT:$identity count=10/10; user intervention required"
        }
        incr count
        lappend history [dict create count $count operation $operation diagnostic pending]
        dict set state $key [dict create count $count operation $operation diagnostic pending history $history]
        ipcfg::_attempt_save $ledger $state
        puts "ATTEMPT:$identity count=$count/10 operation=$operation"
        set result_code [catch {uplevel 1 $script} result result_options]
        dict set state $key diagnostic $result
        dict set state $key code $result_code
        lset history end [dict create count $count operation $operation diagnostic $result code $result_code]
        dict set state $key history $history
        ipcfg::_attempt_save $ledger $state
        if {$result_code != 0 && $count == 10} {
            set result "CONFIGURE_FAIL:ATTEMPT_LIMIT:$identity count=10/10; user intervention required; diagnostic=$result"
            puts $result
        }
    } state_error options]
    close $lock
    file delete ${ledger}.lock
    if {$code != 0} {
        if {![string match "CONFIGURE_FAIL:*" $state_error]} {
            set state_error "CONFIGURE_FAIL:ATTEMPT_STATE:$state_error; user intervention required"
        }
        return -options $options $state_error
    }
    return -options $result_options $result
}

proc ipcfg::_attempt_save {ledger state} {
    set temporary ${ledger}.[pid].tmp
    set channel [open $temporary w]
    try { puts $channel $state } finally { close $channel }
    file rename -force $temporary $ledger
}

# --- Phase 2: create the cell (idempotent) ---
# The ipdef's SUPPORTED_PARTS predicts create-time [BD 5-683]; refuse before the
# attempt and name catalog IP with a similar display name that this part supports.
proc ipcfg::_part_mismatch {vlnv} {
    set part ""
    catch {set part [get_property -quiet PART [current_project]]}
    if {$part eq ""} { return "" }
    set defs [get_ipdefs -quiet -all $vlnv]
    if {![llength $defs]} { set defs [get_ipdefs -quiet -all "$vlnv:*"] }
    if {![llength $defs]} { return "" }
    set def [lindex [lsort -dictionary $defs] end]
    set supported [get_property -quiet SUPPORTED_PARTS $def]
    if {![llength $supported] || [lsearch -exact $supported $part] >= 0} { return "" }
    set words [lsearch -all -inline -not -regexp [split [string tolower [get_property -quiet DISPLAY_NAME $def]]] {^.{0,2}$}]
    set alternatives {}
    foreach other [get_ipdefs -quiet -all *] {
        if {[llength $alternatives] >= 5 || $other eq $def} { continue }
        set shared 0
        foreach word [split [string tolower [get_property -quiet DISPLAY_NAME $other]]] { if {$word in $words} { incr shared } }
        if {$shared < 2} { continue }
        set parts [get_property -quiet SUPPORTED_PARTS $other]
        if {[llength $parts] && [lsearch -exact $parts $part] < 0} { continue }
        set name [join [lrange [split $other :] 0 2] :]
        if {$name ni $alternatives && $name ne [join [lrange [split $def :] 0 2] :]} { lappend alternatives $name }
    }
    return "$def is not supported on $part (SUPPORTED_PARTS excludes it; create_bd_cell would fail with BD 5-683); same-function IP this part supports: {[join $alternatives { }]} -- choose from these or run ipcfg::ip_availability"
}

proc ipcfg::create_cell {vlnv cell} {
    if {[llength [get_bd_cells -quiet $cell]] == 0} {
        set mismatch [ipcfg::_part_mismatch $vlnv]
        if {$mismatch ne ""} { return "CONFIGURE_FAIL:WRONG_PART:$mismatch" }
        if {[catch {ipcfg::attempt_call $cell create {create_bd_cell -type ip -vlnv $vlnv $cell}} e]} {
            return "CONFIGURE_FAIL:CREATE_ERROR:$e"
        }
    }
    return "SUCCESS:created $cell"
}

# --- Phase 3: apply a -dict with structured error classification ---
proc ipcfg::_native_log_size {} {
    if {![info exists ::ipcfg::native_log_path] || ![file isfile $::ipcfg::native_log_path]} {return -1}
    return [file size $::ipcfg::native_log_path]
}

proc ipcfg::_native_log_since {start} {
    if {$start < 0 || [catch {
        set channel [open $::ipcfg::native_log_path rb]
        try {seek $channel $start; set text [read $channel]} finally {close $channel}
    }]} {return ""}
    return [encoding convertfrom utf-8 $text]
}

# Parameters Vivado says must change with the requested ones (IP_Flow 19-3478), not in the dict.
proc ipcfg::_dependent_params {text d} {
    set out {}
    foreach {-> label name value} [regexp -all -inline {IP_Flow 19-3478\] Validation failed for parameter '([^'(]*)\((\w+)\)' with current value '([^']*)'} $text] {
        if {[dict exists $d CONFIG.$name]} {continue}
        regexp {for BD Cell '[^']*'\. ([^\n]*)} $text -> why
        lappend out "CONFIG.$name (now '$value': [string trim [expr {[info exists why] ? $why : $label}]])"
    }
    return [join [lsort -unique $out] {; }]
}

# `allow_board` opts out of the board-ownership guard for a deliberate,
# spec-driven deviation from the board file -- never to quiet the message.
proc ipcfg::_config_satisfied {cell desired} {
    if {[llength $desired] % 2} {return 0}
    if {[catch {
        set object [get_bd_cells -quiet $cell]
        if {[llength $object] != 1} {return 0}
        set properties [list_property $object]
        foreach {key expected} $desired {
            if {$key ni $properties} {return 0}
            set actual [get_property $key $object]
            if {[string match *_CONFIG $key]} {
                if {[dict size $actual] != [dict size $expected]} {return 0}
                dict for {subkey value} $expected {
                    if {![dict exists $actual $subkey] ||
                        [ipcfg::_norm [dict get $actual $subkey]] ne [ipcfg::_norm $value]} {return 0}
                }
            } elseif {[ipcfg::_norm $actual] ne [ipcfg::_norm $expected]} {return 0}
        }
    } detail options]} {
        if {[dict get $options -code] == 0} {return $detail}
        return 0
    }
    return 1
}

proc ipcfg::apply_dict {cell d {allow_board 0}} {
    if {[llength $d] == 0} { return "SUCCESS:no-params" }
    if {!$allow_board} {
        set clash [ipcfg::_board_conflicts $cell $d]
        if {[llength $clash] > 0} {
            return "CONFIGURE_FAIL:BOARD_OWNED:[join $clash { }] -- the board file already set these on $cell; hand-writing them fights the preset (this is how a NoC ends up with no addressable DRAM window) -> RECOVER:if the SPEC names these values, pin them with ipcfg::require <cell> <dict> <reason> {} {spec statement} (5th argument board_override), which overrides the preset and records the deviation; if it does not, drop them from the dict or re-bind with ipcfg::board_bind. allow_board=1 is the same override without the record."
        }
    }
    lassign [ipcfg::_board_merge $cell $d] d merged
    if {[ipcfg::_config_satisfied $cell $d]} {return "SUCCESS:already-satisfied"}
    set log_start [ipcfg::_native_log_size]
    if {[catch {ipcfg::attempt_call $cell configure {set_property -dict $d [get_bd_cells $cell]}} e]} {
        if {[string match "CONFIGURE_FAIL:ATTEMPT_*" $e]} { return $e }
        # Vivado's message carries a trailing newline; left in, it splits this
        # return across two lines and the caller parses only the first.
        set e [string map {"\n" " "} [string trim $e]]
        # The real cause is usually a console line, not the Tcl error: read it back.
        set dependent [ipcfg::_dependent_params [ipcfg::_native_log_since $log_start] $d]
        if {$dependent ne ""} {
            return "CONFIGURE_FAIL:DEPENDENT_PARAM:$dependent -> RECOVER:add these keys to the SAME dict with a value Vivado accepts and apply once; nothing from this dict was applied"
        }
        set t "UNKNOWN"
        if {[string match {*does not exist*} $e]}      {set t "PARAM_NOT_FOUND"}
        if {[string match {*is out of the range*} $e]} {set t "VALUE_OUT_OF_RANGE"}
        if {[string match {*read-only*} $e]}           {set t "READ_ONLY"}
        if {[string match {*It is read-only*} $e]}     {set t "READ_ONLY"}
        if {[string match {*not supported*} $e]}       {set t "NOT_SUPPORTED"}
        if {[string match {*disabled*} $e]}            {set t "PARAM_DISABLED"}
        # set_property -dict is ATOMIC: on failure NOT ONE key was applied, and
        # the message routinely names no key at all -- "failed due to earlier
        # errors", with the real cause (e.g. [BD 5-313] unsupported IP) emitted
        # as a console line this proc never sees. Rebuilding the dict by hand
        # from that is what silently regresses keys that were already correct,
        # so name the recovery here rather than leaving the caller to invent one.
        return "CONFIGURE_FAIL:$t:$e -> RECOVER:autofix_apply <cell> <dict> <console> (isolates the offending key; do NOT hand-rewrite the dict)"
    }
    if {[llength $merged] > 0} {
        return "SUCCESS:applied (merged into board-owned [join $merged {, }]; a bare write REPLACES these dicts)"
    }
    return "SUCCESS:applied"
}

# --- Phase 3 verification: detect keys that did NOT stick (silent revert) ---
# Returns "" if all stuck, else a list of offending keys with detail.
# Handles scalar values (normalized compare) and nested *_CONFIG dicts
# (checks each requested sub-key appears in the read-back string).
proc ipcfg::verify_stuck {cell d} {
    set bad {}
    foreach {k v} $d {
        if {[catch {set cur [get_property $k [get_bd_cells $cell]]}]} {
            lappend bad "${k}(missing)"
            continue
        }
        # Some *_CONFIG params are plain scalars (e.g. a tile mode); only a
        # key/value request against a dict readback is a nested compare.
        set nested [expr {[string match {*_CONFIG} $k] && [llength $v] >= 2 && [llength $v] % 2 == 0
                          && ![catch {dict size $cur}]}]
        if {$nested} {
            foreach {sk sv} $v {
                if {![dict exists $cur $sk]} {
                    lappend bad "${k}/${sk}(missing)"
                } elseif {[ipcfg::_norm [dict get $cur $sk]] ne [ipcfg::_norm $sv]} {
                    lappend bad "${k}/${sk}(reverted:=[dict get $cur $sk],want=$sv)"
                }
            }
        } else {
            if {[ipcfg::_norm $cur] ne [ipcfg::_norm $v]} {
                lappend bad "${k}(=$cur,want=$v)"
            }
        }
    }
    return $bad
}

# --- internal: does a value read as a boolean, and which way? ---
# Returns 1 (true), 0 (false), or "" when the value is not boolean-shaped, so a
# comma-list (true,true,false) or an enum can never be mistaken for a flag.
proc ipcfg::_bool {v} {
    switch -- [string tolower [string trim $v]] {
        true  - 1 - yes - on  { return 1 }
        false - 0 - no  - off { return 0 }
    }
    return ""
}

# --- Phase 3 verification: detect INERT writes (gated attribute, flag still off) ---
# The failure verify_stuck cannot see. An attribute parameter whose feature is
# switched off elsewhere is NOT rejected: the write succeeds, reads back exactly,
# and every existing check is clean -- but the IP never grows the port, so the
# requested behaviour is silently absent. Measured on clkx5_wiz:
#   set CONFIG.RESET_TYPE ACTIVE_LOW, USE_RESET left false
#     -> RESET_TYPE reads ACTIVE_LOW, pins are {clk_in1 clk_out1}: no reset at all
#   then set USE_RESET true -> pins gain {resetn}
# Detection is mechanical and per-cell: for each REQUESTED key, look for a
# sibling parameter on the SAME cell whose name is a known enabler form of the
# key's feature token, and whose current value reads false. Nothing about any
# specific IP is assumed -- an IP without such a sibling reports nothing.
# Returns "" when clean, else "INERT:<key>:enabler=<CONFIG.flag>=<value> ...".
# An enabler already requested truthily in the same dict is not reported.
proc ipcfg::check_enablers {cell d} {
    if {[catch {set obj [get_bd_cells $cell]}]} { return "" }
    # Case-insensitive index of the cell's own parameter names: IPs are not
    # consistent about case (axi_gpio has C_IS_DUAL, axi_dma has c_include_sg),
    # so candidates are matched on the upper-cased leaf and resolved back to the
    # real property name.
    set have {}
    foreach p [list_property $obj] {
        if {[string match -nocase "CONFIG.*" $p]} {
            dict set have [string toupper [ipcfg::_key_leaf $p]] $p
        }
    }
    set asked {}
    foreach {k v} $d { dict set asked [string toupper [ipcfg::_key_leaf $k]] $v }
    set out {}
    set seen {}
    foreach {k v} $d {
        set leaf [string toupper [ipcfg::_key_leaf $k]]
        # Feature tokens, widest first: the whole leaf; the leaf minus a trailing
        # attribute word (RESET_TYPE -> RESET); and the leading segment after an
        # optional C_ vendor prefix (C_SG_LENGTH_WIDTH -> SG, which is what
        # c_include_sg is named after).
        set toks [list $leaf]
        if {[regexp {^(.+)_(TYPE|MODE|WIDTH|FREQ|FREQUENCY|RATE|POLARITY|VALUE|SEL|SOURCE|SIZE|DEPTH|NUM|COUNT)$} \
                 $leaf -> stem]} {
            lappend toks $stem
        }
        set head [lindex [split [regsub {^C_} $leaf ""] _] 0]
        if {$head ne "" && $head ni $toks} { lappend toks $head }
        foreach t $toks {
            foreach cand [list USE_$t ENABLE_$t EN_$t HAS_$t INCLUDE_$t IS_$t \
                               ${t}_ENABLE ${t}_EN ${t}_USED \
                               C_USE_$t C_ENABLE_$t C_HAS_$t C_IS_$t C_INCLUDE_$t] {
                if {$cand eq $leaf || ![dict exists $have $cand]} { continue }
                set ck [dict get $have $cand]
                if {[dict exists $seen "$leaf>$cand"]} { continue }
                if {[dict exists $asked $cand] &&
                    [ipcfg::_bool [dict get $asked $cand]] eq 1} { continue }
                if {[catch {set cur [get_property $ck $obj]}]} { continue }
                if {[ipcfg::_bool $cur] eq 0} {
                    dict set seen "$leaf>$cand" 1
                    lappend out "INERT:$k:enabler=$ck=$cur"
                }
            }
        }
    }
    return [join $out " "]
}

# --- Intent-side companion: which feature flags does this request need? ---
# check_enablers only fires when an ATTRIBUTE was written, so it cannot catch a
# request whose whole content IS the flag: "expose the locked signal" needs
# CONFIG.USE_LOCKED and names no attribute at all. Pass the behavioural nouns
# from the prompt (reset, locked, interrupt, debug ...) and get back the boolean
# parameters on THIS cell that enable them, with their current values, so a
# still-false flag is visible before you finish rather than after grading.
# Returns "FLAGS:" plus "<word>:<CONFIG.flag>=<value>" per hit, or "FLAGS:none".
proc ipcfg::feature_flags {cell words} {
    if {[catch {set obj [get_bd_cells $cell]}]} { return "FLAGS:none" }
    set params {}
    foreach p [list_property $obj] {
        if {[string match -nocase "CONFIG.*" $p]} { lappend params $p }
    }
    set out {}
    foreach w $words {
        set t [string toupper [string trim $w]]
        if {$t eq ""} { continue }
        foreach p $params {
            set leaf [string toupper [ipcfg::_key_leaf $p]]
            if {![regexp "^(USE|ENABLE|EN|HAS|INCLUDE|IS|C_USE|C_ENABLE|C_HAS|C_IS|C_INCLUDE)_${t}\$" $leaf] &&
                ![regexp "^${t}_(ENABLE|EN|USED|USE)\$" $leaf]} { continue }
            if {[catch {set cur [get_property $p $obj]}]} { continue }
            if {[ipcfg::_bool $cur] eq ""} { continue }
            lappend out "[string tolower $t]:$p=$cur"
        }
    }
    if {![llength $out]} { return "FLAGS:none" }
    return "FLAGS: [join $out " "]"
}

# --- Parse a numeric legal range out of a Vivado error message ---
# Handles the common shapes:
#   "Value '600' is out of the range '50' to '500'"
#   "... out of the range (50 to 500)"
# Returns "{lo hi}" (numbers) or "" when no orderable numeric range is present
# (e.g. an enum list -> caller must ESCALATE, not guess).
proc ipcfg::parse_range {err} {
    set num {-?[0-9.]+(?:[eE][-+]?[0-9]+)?}
    # "range '50' to '500'"  /  "range (50 to 500)"
    if {[regexp "range\[^0-9eE.+-\]*($num)\[^0-9eE.+-\]+to\[^0-9eE.+-\]*($num)" $err -> lo hi]} {
        return [list $lo $hi]
    }
    # "range (1,32)" (comma-separated min,max in parens)
    if {[regexp "range\[^0-9eE.+-\]*\\(($num)\\s*,\\s*($num)\\)" $err -> lo hi]} {
        return [list $lo $hi]
    }
    return ""
}

proc ipcfg::_clamp {want lo hi} {
    if {![ipcfg::_is_num $want] || ![ipcfg::_is_num $lo] || ![ipcfg::_is_num $hi]} { return $want }
    if {double($want) < double($lo)} { return $lo }
    if {double($want) > double($hi)} { return $hi }
    return $want
}

# --- internal: the bare property name (CONFIG.C_GPIO_WIDTH -> C_GPIO_WIDTH) ---
proc ipcfg::_key_leaf {k} { return [regsub {^CONFIG\.} $k ""] }

# --- internal: console lines that mention a key (rich Vivado errors live here) ---
# set_property's catch result is often the generic "failed due to earlier errors";
# the useful "out of the range (1,32)" / "read-only" / "disabled" text is a
# C-level console message. So we accept the captured console and search the lines
# that reference THIS key (by its bare name) for the real reason.
proc ipcfg::_key_lines {k console} {
    set leaf [ipcfg::_key_leaf $k]
    set out {}
    foreach line [split $console "\n"] {
        if {[string first $leaf $line] >= 0} { lappend out $line }
    }
    return [join $out "\n"]
}

# --- Error-driven AUTO-FIX apply (confidence-bounded, console-aware) ---
# Tries the whole -dict fast path first. On failure, isolates per key and applies
# ONLY high-confidence remediations derived from Vivado's OWN feedback. Because
# the rich error is a console message (not the Tcl catch result), pass the
# captured vivado_execute output as `console` so per-key reasons can be read:
#   VALUE_OUT_OF_RANGE -> clamp to the nearest legal bound parsed from the range
#                         (records request->achieved like classify_change RESOLVED)
#   READ_ONLY          -> drop the key (connection/integration-derived; a
#                         set_property can never satisfy it -> confident skip)
#   PARAM_DISABLED     -> if the current (gated) value already equals the request,
#                         OMIT it (already satisfied); otherwise ESCALATE
# Everything we cannot fix with confidence is returned for the agent to ASK the
# user about (PARAM_NOT_FOUND, NOT_SUPPORTED, non-numeric range, disabled-differ,
# unknown errors). Bounded by maxpass per key (default 2) -> no infinite loops.
# Returns one structured line:
#   AUTOFIX:applied={..} clamped={k=>v ..} omitted={..} dropped_readonly={..} ESCALATE={k(reason) ..}
proc ipcfg::autofix_apply {cell d {console ""} {maxpass 2}} {
    set first [ipcfg::apply_dict $cell $d]
    if {[string match "CONFIGURE_FAIL:ATTEMPT_*" $first]} { return $first }
    if {[string match "SUCCESS:*" $first]} {
        return "AUTOFIX:applied={all} clamped={} omitted={} dropped_readonly={} ESCALATE={}"
    }
    set applied {}; set clamped {}; set omitted {}; set dropped {}; set escalate {}
    foreach {k v} $d {
        set want $v
        set ok 0
        for {set pass 0} {$pass < $maxpass && !$ok} {incr pass} {
            if {[catch {ipcfg::attempt_call $cell repair {set_property $k $want [get_bd_cells $cell]}} e]} {
                if {[string match "CONFIGURE_FAIL:ATTEMPT_*" $e]} { return $e }
                # combine the catch result with the console lines for THIS key
                set ctx "$e\n[ipcfg::_key_lines $k $console]"
                set rng [ipcfg::parse_range $ctx]
                if {$rng ne ""} {
                    lassign $rng lo hi
                    set nv [ipcfg::_clamp $want $lo $hi]
                    if {[ipcfg::_norm $nv] ne [ipcfg::_norm $want]} {
                        lappend clamped "${k}=>${nv}(want=$v)"
                        set want $nv
                        continue
                    }
                    lappend escalate "${k}(range-unfixable)"; break
                } elseif {[string match {*read-only*} $ctx] || [string match {*It is read-only*} $ctx]} {
                    lappend dropped "$k"; set ok 1; break
                } elseif {[string match {*disabled*} $ctx]} {
                    set cur ""
                    catch {set cur [get_property $k [get_bd_cells $cell]]}
                    if {[ipcfg::_norm $cur] eq [ipcfg::_norm $want]} {
                        lappend omitted $k; set ok 1; break
                    }
                    lappend escalate "${k}(disabled-differ:cur=$cur,want=$v)"; break
                } elseif {[string match {*does not exist*} $ctx] || [string match {*not a valid*} $ctx]} {
                    lappend escalate "${k}(not-found)"; break
                } elseif {[string match {*not supported*} $ctx]} {
                    lappend escalate "${k}(not-supported)"; break
                } else {
                    lappend escalate "${k}(error)"; break
                }
            } else {
                lappend applied $k; set ok 1
            }
        }
    }
    return "AUTOFIX:applied={$applied} clamped={$clamped} omitted={$omitted} dropped_readonly={$dropped} ESCALATE={$escalate}"
}

# --- Value-format introspection: read a param back to learn its shape ---
# Use when a key exists but your value is rejected/ignored (e.g. a param that
# expects one comma-separated list string rather than a scalar). Returns the
# current value so the caller can mirror its structure.
proc ipcfg::param_format {cell key} {
    if {[catch {get_property $key [get_bd_cells $cell]} v]} { return "" }
    return $v
}

# --- Baseline snapshot: capture default values BEFORE configuring ---
# Call right after create_cell, passing the keys you are about to set.
# Returns a {key value ...} map you later feed to verify_intent so defaults
# are learned from the IP itself (no per-IP defaults database). For nested
# *_CONFIG keys the whole read-back string is captured.
#   keys: a list of CONFIG.* property names (top-level keys of your dict)
proc ipcfg::snapshot {cell keys} {
    set out {}
    foreach k $keys {
        if {[catch {set v [get_property $k [get_bd_cells $cell]]}]} {
            set v ""
        }
        lappend out $k $v
    }
    return $out
}

# --- Intent audit: catch a wrong-but-valid param that produced NO change ---
# verify_stuck proves a value persisted; verify_intent proves the value is
# observably DIFFERENT from the captured default for requirements that mean
# "enable/select/turn-on". A param whose post-apply value equals its baseline
# default is SUSPECT: you likely set the wrong param for the named feature
# (the right one is still at its default). This fires with NO Vivado error.
#   reqmap: a list of triples {key wantval baselineval ...}
#           - key/wantval come from your dict
#           - baselineval comes from ipcfg::snapshot (the pre-config value)
#   flag_no_change: when 1 (default) a value that equals its default is flagged
#           (suspect-no-change). When 0, only HARD reverts are returned -- use 0
#           once doc grounding confirms the param is correct and the default
#           legitimately satisfies the intent (value_src=default is acceptable;
#           a user not changing a param does not mean the default is wrong).
# Uses classify_change: EXACT passes, RESOLVED passes only within a tolerance
# the caller supplied for that key, and a nearest-legal value is a miss.
# Acceptance is exact unless `tolerances` ({key <abs>|<n>% ...}) supplies one for
# a key; a nearby legal value outside it is a miss reported as nearest-legal.
# Returns "" if every requirement is observably realized, else a list of
# offending keys tagged (suspect-no-change), (nearest-legal) or (reverted).
proc ipcfg::verify_intent {cell reqmap {flag_no_change 1} {tolerances {}}} {
    set bad {}
    foreach {k want base} $reqmap {
        if {[catch {set cur [get_property $k [get_bd_cells $cell]]}]} {
            lappend bad "${k}(missing)"
            continue
        }
        set tolerance [expr {[dict exists $tolerances $k] ? [dict get $tolerances $k] : ""}]
        set class [ipcfg::classify_change $want $base $cur {} $tolerance]
        switch -- $class {
            EXACT {
                if {[ipcfg::_norm $want] eq [ipcfg::_norm $base] && $flag_no_change} {
                    # reached the value, but it was already the default: no observable
                    # effect -> SOFT flag (re-check doc grounding; value_src=default ok).
                    lappend bad "${k}(suspect-no-change:default=$base)"
                }
            }
            RESOLVED { }
            default  { lappend bad "${k}([ipcfg::_miss_text $class $cur $want])" }
        }
    }
    return $bad
}

# --- Tolerance-aware audit that also REPORTS within-tolerance keys ---
# Like verify_intent but returns a structured triple.
#   reqmap: {key want base ...}   tolerances: optional {key <abs>|<n>% ...}
# Returns "EXACT:{k ...} RESOLVED:{k(=cur,want=w) ...} BAD:{k(...) ...}".
proc ipcfg::audit_intent {cell reqmap {tolerances {}}} {
    set exact {}; set resolved {}; set bad {}
    foreach {k want base} $reqmap {
        if {[catch {set cur [get_property $k [get_bd_cells $cell]]}]} {
            lappend bad "${k}(missing)"; continue
        }
        set tolerance [expr {[dict exists $tolerances $k] ? [dict get $tolerances $k] : ""}]
        set class [ipcfg::classify_change $want $base $cur {} $tolerance]
        switch -- $class {
            EXACT    { lappend exact $k }
            RESOLVED { lappend resolved "${k}(=$cur,want=$want,tolerance=$tolerance)" }
            default  { lappend bad "${k}([ipcfg::_miss_text $class $cur $want])" }
        }
    }
    return "EXACT:{$exact} RESOLVED:{$resolved} BAD:{$bad}"
}

# --- Requirement ledger: pin a spec value, write it, audit it at the end. ---
# A board-file value is refused unless board_override quotes the spec statement.
#
# Why pin: the failure every other check in this file is blind to. verify_stuck and
# verify_intent both compare the read-back against the dict the caller holds
# NOW, so when a -dict fails atomically on one bad key and the caller rebuilds
# it, any key that changed in the rebuild is validated against its NEW value and
# reports clean. Measured on visp_ss: C_TILE0_ISP0_LIVE_INPUTS was set correctly
# to 1, the apply failed on an unrelated key (C_IMAGE_SPLITTER_STITCHER_ENABLE,
# which pulls in the unsupported img_split IP), the rebuilt dict carried 4, and
# the design validated and shipped with four live inputs for one camera.
#
# So requirements are recorded ONCE, from the spec, before the writes start, and
# audited against the FINAL read-back at the end.
#   cell   : BD cell the requirement applies to
#   d      : {CONFIG.KEY want ...} -- the same shape as the apply dict
#   reason : short provenance ("one camera"), reported on failure
#   tolerances : optional {CONFIG.KEY <abs>|<n>% ...} the SPEC states; every
#            other key must read back exactly
#   board_override : the spec statement that names a value the board file set
#            ("spec camera0: RAW12 at 1500 Mbps"); empty = board values are refused
# Re-pinning a key is allowed but never silent: it returns REQ:REPINNED so a
# recovery that genuinely changes the spec is visible in the transcript.
#
# THIS ALSO WRITES. Recording without writing was a trap with no legitimate
# use: a run pinned four parameters on each of two camera receivers, wrote
# neither, and shipped a design that validated clean on the board defaults --
# RAW8 at 800 Mbps for a brief that asked for RAW12 at 1500. Nothing reported
# it, because the ledger it had filled in is only read by audit_requirements,
# and a run that believes it has configured a cell has no reason to audit. An
# intent you pin but never apply is always a defect, so pinning applies it: the
# base value is captured first, so a later drift is still caught by the audit.
proc ipcfg::require {cell d {reason ""} {tolerances {}} {board_override ""}} {
    variable req
    if {[llength $d] % 2 != 0} { return "REQ_ERR:odd-length dict for $cell" }
    if {[llength $tolerances] % 2 != 0} { return "REQ_ERR:odd-length tolerances for $cell" }
    foreach {k t} $tolerances {
        if {![dict exists $d $k]} { return "REQ_ERR:tolerance for $k, which this call does not pin" }
        if {![ipcfg::_tolerance_ok $t] || ![ipcfg::_is_num [dict get $d $k]]} {
            return "REQ_ERR:tolerance '$t' for $k must be <abs> or <n>% on a numeric value"
        }
    }
    set existing [expr {[dict exists $req $cell] ? [dict get $req $cell] : {}}]
    set repinned {}
    foreach {k v} $d {
        set base ""
        catch {set base [get_property $k [get_bd_cells -quiet $cell]]}
        set tolerance [expr {[dict exists $tolerances $k] ? [dict get $tolerances $k] : ""}]
        set kept {}
        foreach e $existing {
            if {[lindex $e 0] eq $k} {
                # A restore of the same value keeps the tolerance the spec gave it.
                if {![dict exists $tolerances $k] && [ipcfg::_norm [lindex $e 1]] eq [ipcfg::_norm $v]} {
                    set tolerance [lindex $e 4]
                }
                if {[ipcfg::_norm [lindex $e 1]] ne [ipcfg::_norm $v] || [lindex $e 4] ne $tolerance} {
                    lappend repinned "${k}([lindex $e 1]->$v)"
                } else {
                    set base [lindex $e 2]
                }
            } else {
                lappend kept $e
            }
        }
        set existing $kept
        lappend existing [list $k $v $base $reason $tolerance]
    }
    dict set req $cell $existing
    set n [llength $existing]
    set note ""
    if {[llength $repinned] > 0} { set note " REPINNED [join $repinned { }]" }
    # Write it. A refusal is returned verbatim, recovery advice and all, because
    # the caller has to act on it -- and the requirement stays in the ledger, so
    # audit_requirements reports it again at the end if the recovery is skipped.
    set applied [ipcfg::apply_dict $cell $d]
    # A spec can differ from the board (RAW12/1500 on a RAW8/800 preset), but a
    # hand-derived value must not override the board, so the caller names the spec.
    if {[string match "*BOARD_OWNED*" $applied] && [string trim $board_override] eq ""} {
        return "REQ_FAIL:$cell pinned but NOT written ($n pinned)$note\n  ->\
$applied\n  -> if the SPEC names these values, repeat the call with the 5th argument\
board_override set to that spec statement: ipcfg::require <cell> <dict> <reason> {}\
{spec ...}. If it does not, drop them from the dict."
    }
    if {[string match "*BOARD_OWNED*" $applied]} {
        set applied [ipcfg::apply_dict $cell $d 1]
        if {[string match "SUCCESS:*" $applied]} {
            set ledger $::ipcfg::board_override
            set kept [expr {[dict exists $ledger $cell] ? [dict get $ledger $cell] : {}}]
            set names {}
            foreach {k v} $d {
                lappend kept [list $k $v $board_override]
                lappend names [ipcfg::_key_leaf $k]
            }
            dict set ::ipcfg::board_override $cell $kept
            return "REQ:pinned+applied [expr {[llength $d] / 2}] (total $n for\
$cell)$note -- OVERRODE the board preset on [join $names { }] because\
'$board_override'; audit_board will report this as an override"
        }
    }
    if {![string match "SUCCESS:*" $applied]} {
        return "REQ_FAIL:$cell pinned but NOT written ($n pinned)$note\n  ->\
$applied"
    }
    return "REQ:pinned+applied [expr {[llength $d] / 2}] (total $n for\
$cell)$note"
}

# Dump the ledger (one line per requirement) for the named cell, or all cells.
proc ipcfg::requirements_data {{cell ""}} {
    variable req
    set cells [expr {$cell eq "" ? [dict keys $req] : [list $cell]}]
    set records {}
    foreach name $cells {
        if {![dict exists $req $name]} {continue}
        foreach entry [dict get $req $name] {
            lassign $entry key expected base reason tolerance
            lappend records [dict create cell $name key $key expected $expected base $base reason $reason tolerance $tolerance]
        }
    }
    return $records
}

proc ipcfg::requirements {{cell ""}} {
    variable req
    set cells [expr {$cell eq "" ? [dict keys $req] : [list $cell]}]
    set out {}
    foreach c $cells {
        if {![dict exists $req $c]} { continue }
        foreach e [dict get $req $c] {
            lassign $e k want base reason tolerance
            set tol [expr {$tolerance eq "" ? "" : " tolerance=$tolerance"}]
            lappend out "$c $k want=$want$tol base=$base reason=$reason"
        }
    }
    if {[llength $out] == 0} { return "REQ:empty" }
    return [join $out "\n"]
}

# Audit the FINAL design against everything pinned: exact, unless the
# requirement itself carries a tolerance. Run this last, after the design validates.
proc ipcfg::audit_requirements {{cell ""}} {
    variable req
    set cells [expr {$cell eq "" ? [dict keys $req] : [list $cell]}]
    set bad {}; set n 0
    foreach c $cells {
        if {![dict exists $req $c]} { continue }
        foreach e [dict get $req $c] {
            lassign $e k want base reason tolerance
            incr n
            set why [expr {$reason eq "" ? "" : ",why=$reason"}]
            if {[catch {set cur [get_property $k [get_bd_cells $c]]}]} {
                lappend bad "${c}/${k}(missing${why})"
                continue
            }
            set class [ipcfg::classify_change $want $base $cur {} $tolerance]
            switch -- $class {
                EXACT    { }
                RESOLVED { }
                NEAREST  { lappend bad "${c}/${k}(=$cur,want=${want},nearest-legal${why})" }
                default  { lappend bad "${c}/${k}(=$cur,want=${want}${why})" }
            }
        }
    }
    if {$n == 0} { return "REQ:empty nothing was pinned -- call ipcfg::require before the first write" }
    if {[llength $bad] > 0} {
        return "REQ_FAIL:[llength $bad]/$n [join $bad { }]"
    }
    return "REQ:OK $n/$n"
}

# Every pinned requirement on <cell> that no longer holds (typically after a board
# preset landed), with one restore dict so a single ipcfg::require repairs all.
# Returns "" when nothing drifted, else "PINNED_RESET:<n> <k>=<cur>(want <v>) ... restore={..}".
proc ipcfg::pinned_reset {cell} {
    variable req
    set name ""
    foreach candidate [list $cell [string trimleft $cell /] "/[string trimleft $cell /]"] {
        if {[dict exists $req $candidate]} { set name $candidate; break }
    }
    if {$name eq ""} { return "" }
    set object [get_bd_cells -quiet $cell]
    if {$object eq ""} { return "" }
    set items {}; set restore {}
    foreach entry [dict get $req $name] {
        lassign $entry key want base reason tolerance
        if {[catch {get_property $key $object} current]} { set current "(missing)" }
        if {[ipcfg::classify_change $want $base $current {} $tolerance] in {EXACT RESOLVED}} { continue }
        lappend items "[ipcfg::_key_leaf $key]=${current}(want $want)"
        lappend restore $key $want
    }
    if {![llength $items]} { return "" }
    return "PINNED_RESET:[llength $items] [join $items { }] restore={$restore}"
}

# Commands a script calls that do not exist in this session, found without running it:
# every ipcfg::/ipiasm:: name, and every lowercase word opening a [command substitution].
proc ipcfg::script_check {path} {
    if {[catch {set channel [open $path r]} err]} { return "SCRIPT_CHECK_FAIL:cannot read $path: $err" }
    set text [read $channel]
    close $channel
    set code {}
    foreach line [split $text \n] {
        if {![regexp {^\s*#} $line]} { lappend code $line }
    }
    set code [join $code \n]
    set defined {}
    foreach {- name} [regexp -all -inline {(?:^|[\s;\[\{])proc\s+([^\s\{]+)} $code] { lappend defined [string trimleft $name :] }
    set names {}
    foreach {- name} [regexp -all -line -inline {(?:^|[\[;\{])\s*(?:::)?((?:ipcfg|ipiasm)::[A-Za-z0-9_]+)} $code] { lappend names $name }
    foreach {- name} [regexp -all -inline {\[\s*([a-z][a-z0-9_]{3,}(?:::[a-z0-9_]+)*)[\s\]]} $code] { lappend names $name }
    set unknown {}
    foreach name [lsort -unique $names] {
        if {$name in $defined || [llength [info commands ::$name]] || [info exists ::auto_index($name)]} { continue }
        lappend unknown $name
    }
    if {[llength $unknown]} {
        return "SCRIPT_CHECK_FAIL:[file tail $path] calls command(s) that do not exist here: {[join $unknown { }]} -- nothing was run. List the real helpers with ipcfg::api <word> / ipiasm::api_inventory <pattern>"
    }
    return "SCRIPT_CHECK:OK [file tail $path]"
}

# Source a script; on failure print the message and its file/line trace before
# re-raising, so the caller sees where it failed, not only what was raised.
proc ipcfg::source_reported {path} {
    set check [ipcfg::script_check $path]
    if {![string match SCRIPT_CHECK:OK* $check]} {
        puts $check
        return -code error $check
    }
    catch {source} usage
    set command [expr {[string match *-notrace* $usage] ? [list source -notrace $path] : [list source $path]}]
    if {![catch {uplevel #0 $command} result options]} { return $result }
    set trace [lrange [split [dict get $options -errorinfo] \n] 0 11]
    puts "SCRIPT_FAIL:[file tail $path]: $result"
    puts "SCRIPT_TRACE:[join $trace { | }]"
    return -code error "SCRIPT_FAIL:[file tail $path]: $result"
}

# Drop the ledger for one cell (or all). Call between prompts in benchmark mode.
proc ipcfg::forget_requirements {{cell ""}} {
    variable req
    variable board_override
    if {$cell eq ""} {
        set req {}
        set board_override {}
        return "REQ:cleared all"
    }
    if {[dict exists $req $cell]} { set req [dict remove $req $cell] }
    if {[dict exists $board_override $cell]} {
        set board_override [dict remove $board_override $cell]
    }
    return "REQ:cleared $cell"
}

# ============================================================================
# BOARD BINDING -- the board file answers these, so nobody hand-writes them
# ============================================================================
# Vivado gives every IP the same hook for this: a `CONFIG.<X>_BOARD_INTERFACE`
# property. Naming a board interface on it applies that interface's preset from
# the board file. Measured on a Versal Gen2 board/2026.1, one hook each for ps_wizard
# (PS_BOARD_INTERFACE), axi_iic (IIC_BOARD_INTERFACE) and mipi_csi2_rx_subsystem
# (DPHYRX_BOARD_INTERFACE), two for axi_gpio, twenty-eight for axi_noc2. So the
# rule below needs no IP-specific parameter names and does not rot when a new
# board or a new IP shows up.
#
# What the preset writes is BOARD-OWNED: the board already answered it, and a
# later hand-write is fighting the board file rather than configuring the
# design. The set is discovered by diffing CONFIG.* across the bind, so it is
# per-design fact rather than a hard-coded list. Two observed consequences of
# ignoring it: on axi_noc2 the board owns NUM_MC / NUM_MCP / DDRMC5_CONFIG, and
# a run that hand-edited DDRMC5_CONFIG (to chase DDRMC5_BOARD_INTRF_EN, a
# decoy the preset itself sets false) desynced the controller counts and never
# got an addressable DRAM window; on mipi_csi2_rx_subsystem the board owns
# CMN_NUM_LANES, CMN_PXL_FORMAT, CSI_BUF_DEPTH and DPY_LINE_RATE, which runs
# had been deriving by hand from the prompt.

# --- the hooks this cell exposes (IP-agnostic) ---
proc ipcfg::board_hooks {cell} {
    set c [get_bd_cells -quiet $cell]
    if {$c eq ""} { return {} }
    set out {}
    foreach p [list_property $c] {
        if {[string match "CONFIG.*_BOARD_INTERFACE" $p]} { lappend out $p }
    }
    return [lsort $out]
}

# --- candidate enablers: board-ish CONFIG properties that are not hooks ---
# Some IP gate the hook behind a flag, and until it is on the preset's values
# are outside the parameter's legal range, so the bind is REJECTED rather than
# silently dropped. axi_noc2 is the case in hand: without MC_BOARD_INTRF_EN the
# preset's DDRMC5_INTERLEAVE_SIZE=2048 fails with "Valid values are - 0". This
# is the same enabler pattern as ipcfg::check_enablers, applied to board binds.
proc ipcfg::board_enablers {cell} {
    set c [get_bd_cells -quiet $cell]
    if {$c eq ""} { return {} }
    set out {}
    foreach p [list_property $c] {
        if {![string match CONFIG.* $p]} { continue }
        if {[string match "CONFIG.*_BOARD_INTERFACE" $p]} { continue }
        if {![regexp -nocase {BOARD} $p]} { continue }
        set v ""
        catch {set v [get_property -quiet $p $c]}
        if {[string is boolean -strict $v]} { lappend out $p }
    }
    return [lsort $out]
}

proc ipcfg::_cfg_snapshot {c} {
    set d [dict create]
    foreach p [list_property $c] {
        if {![string match CONFIG.* $p]} { continue }
        set v ""
        catch {set v [get_property -quiet $p $c]}
        dict set d $p $v
    }
    return $d
}

# --- bind board interfaces to a cell and record what the board then owns ---
#   binding : {CONFIG.<X>_BOARD_INTERFACE <board-interface-name> ...}
#   extra   : additional CONFIG pairs to apply ATOMICALLY with the binding, for
#             IP that cross-check a count against the preset and reject any
#             intermediate state (axi_noc2 does this with NUM_MC).
# Applies as one set_property; on rejection, flips the candidate enablers and
# retries once, reporting which enabler it needed.
proc ipcfg::board_bind {cell binding {extra {}} {reason "board preset"}} {
    variable board_owned
    set c [get_bd_cells -quiet $cell]
    if {$c eq ""} { return "BOARD_FAIL:no such cell $cell" }
    if {[llength $binding] % 2 != 0} { return "BOARD_FAIL:odd-length binding for $cell" }
    if {[llength $binding] == 0}     { return "BOARD_FAIL:no binding given for $cell" }
    set hooks [ipcfg::board_hooks $cell]
    foreach {k v} $binding {
        if {$k ni $hooks} {
            return "BOARD_FAIL:$cell has no board hook $k (has: [join $hooks { }])"
        }
    }
    set before [ipcfg::_cfg_snapshot $c]
    set d $binding
    foreach {k v} $extra { lappend d $k $v }
    if {[ipcfg::_config_satisfied $cell $d]} {
        if {![dict exists $board_owned $cell] ||
            [llength [lindex [dict get $board_owned $cell] 0]] == 0} {
            return "BOARD_FAIL:OWNERSHIP_UNKNOWN:$cell already bound without recorded preset evidence"
        }
        set audit [ipcfg::audit_board $cell]
        if {[string match BOARD_FAIL:* $audit]} {return $audit}
        return "BOARD:bound $cell already-satisfied; ownership preserved"
    }
    set flipped {}
    if {[catch {ipcfg::attempt_call $cell board_bind {set_property -dict $d $c}} e]} {
        if {[string match "CONFIGURE_FAIL:ATTEMPT_*" $e]} { return $e }
        # Rejected -- try the enablers before giving up, then re-apply.
        foreach en [ipcfg::board_enablers $cell] {
            if {[catch {ipcfg::attempt_call $cell board_enable {set_property $en true $c}} enabled]} {
                if {[string match "CONFIGURE_FAIL:ATTEMPT_*" $enabled]} { return $enabled }
                continue
            }
            lappend flipped $en
        }
        if {[llength $flipped] == 0} {
            return "BOARD_FAIL:$cell :: [lindex [split $e \n] 0]\n  -> no board-enable flag found to retry with (hooks: [join $hooks { }])"
        }
        if {[catch {ipcfg::attempt_call $cell board_bind {set_property -dict $d $c}} e2]} {
            if {[string match "CONFIGURE_FAIL:ATTEMPT_*" $e2]} { return $e2 }
            return "BOARD_FAIL:$cell :: [lindex [split $e2 \n] 0]\n  -> retried with [join $flipped { }] still rejected; check the interface names against get_board_part_interfaces"
        }
    }
    set after [ipcfg::_cfg_snapshot $c]
    set owned {}
    foreach k [lsort [dict keys $after]] {
        set b ""
        catch {set b [dict get $before $k]}
        set a [dict get $after $k]
        if {[ipcfg::_norm $a] eq [ipcfg::_norm $b]} { continue }
        lappend owned [list $k $a]
    }
    if {[llength $owned] == 0 && [dict exists $board_owned $cell]} {
        set owned [lindex [dict get $board_owned $cell] 0]
    }
    dict set board_owned $cell [list $owned $reason]
    set note ""
    if {[llength $flipped] > 0} { set note " (needed enabler [join $flipped { }])" }
    set reset [ipcfg::pinned_reset $cell]
    if {$reset ne ""} {
        append note " $reset -> RECOVER:the preset overwrote pinned spec values; restore ALL of them in one ipcfg::require $cell <restore dict> <reason> {} {<spec statement naming them>}"
    }
    return "BOARD:bound $cell hooks=[expr {[llength $binding] / 2}] owned=[llength $owned]$note"
}

# --- which entries of an apply dict actually contradict the board? ---
# Granularity is the whole point here. On `ps_wizard` the board owns
# `CONFIG.PS11_CONFIG`, and that one property carries essentially every PS
# setting -- so refusing the property outright would refuse the PS. What the
# board owns is the SUB-KEYS it wrote, so a write is a conflict only where it
# changes one of those to a different value. Adding a sub-key the board never
# set, or rewriting one with the value it already has, is not a conflict.
# Returns a list of "KEY" or "KEY<subkey subkey>" for the offending entries.
proc ipcfg::_board_conflicts {cell d} {
    variable board_owned
    if {![dict exists $board_owned $cell]} { return {} }
    set out {}
    foreach e [lindex [dict get $board_owned $cell] 0] {
        lassign $e k bv
        if {![dict exists $d $k]} { continue }
        set nv [dict get $d $k]
        set bsz -1; set nsz -1
        catch {set bsz [dict size $bv]}
        catch {set nsz [dict size $nv]}
        if {$bsz > 0 && $nsz >= 0} {
            set subs {}
            foreach sk [dict keys $nv] {
                if {![dict exists $bv $sk]} { continue }
                if {[ipcfg::_norm [dict get $bv $sk]] ne [ipcfg::_norm [dict get $nv $sk]]} {
                    lappend subs $sk
                }
            }
            if {[llength $subs] > 0} {
                lappend out "[ipcfg::_key_leaf $k]<[join $subs { }]>"
            }
            continue
        }
        if {[ipcfg::_norm $bv] ne [ipcfg::_norm $nv]} {
            lappend out [ipcfg::_key_leaf $k]
        }
    }
    return $out
}

# --- keep a board-owned dict whole when adding to it ---
# These properties are REPLACE, not merge, and nothing says so. Measured on
# ps_wizard: after binding the board, CONFIG.PS11_CONFIG holds dozens of sub-keys;
# one `set_property CONFIG.PS11_CONFIG {PS_NUM_FABRIC_RESETS 1}` leaves a handful.
# The ones that vanish include PMC_CRP_PL0/PL1/PL2_REF_CTRL_FREQMHZ -- the PL clock
# frequencies the whole fabric is clocked from -- and the write returns success.
# So a write to a board-owned dict is folded into what is already there. The
# conflict check above has already refused (or been told to allow) any sub-key
# that disagrees with the board, so this cannot quietly re-assert a preset value
# over an intended change.
# Returns {newdict mergedkeys}.
proc ipcfg::_board_merge {cell d} {
    variable board_owned
    if {![dict exists $board_owned $cell]} { return [list $d {}] }
    set merged {}
    foreach e [lindex [dict get $board_owned $cell] 0] {
        lassign $e k bv
        if {![dict exists $d $k]} { continue }
        set bsz -1
        catch {set bsz [dict size $bv]}
        if {$bsz <= 0} { continue }
        set nv [dict get $d $k]
        set nsz -1
        catch {set nsz [dict size $nv]}
        if {$nsz < 0} { continue }
        # merge onto the LIVE value, so earlier additions survive too
        set cur $bv
        catch {set cur [get_property $k [get_bd_cells $cell]]}
        if {[catch {dict size $cur}]} { set cur $bv }
        set out $cur
        foreach {sk sv} $nv { dict set out $sk $sv }
        dict set d $k $out
        lappend merged [ipcfg::_key_leaf $k]
    }
    return [list $d $merged]
}

# The keys the board wrote for this cell (names only), for callers and gates.
proc ipcfg::board_owned {{cell ""}} {
    variable board_owned
    set cells [expr {$cell eq "" ? [dict keys $board_owned] : [list $cell]}]
    set out {}
    foreach c $cells {
        if {![dict exists $board_owned $c]} { continue }
        foreach e [lindex [dict get $board_owned $c] 0] {
            lappend out "$c [lindex $e 0]"
        }
    }
    return $out
}

# --- did anything move a value the board owns? ---
# Run beside audit_requirements. A board-owned value that changed after the
# bind means something hand-wrote over the board file, which is the defect this
# whole section exists to catch.
proc ipcfg::audit_board {{cell ""}} {
    variable board_owned
    variable board_override
    set cells [expr {$cell eq "" ? [dict keys $board_owned] : [list $cell]}]
    set bad {}; set n 0; set overrode {}
    foreach c $cells {
        if {![dict exists $board_owned $c]} { continue }
        lassign [dict get $board_owned $c] owned reason
        foreach e $owned {
            lassign $e k want
            # A value the spec named and ipcfg::require deliberately replaced is
            # a declared deviation, not drift. Still counted and still named, so
            # the transcript shows what the design does differently from the
            # board -- an audit that hid it would be as bad as one that failed
            # the run for obeying its brief.
            if {[dict exists $board_override $c]} {
                set claimed 0
                foreach ov [dict get $board_override $c] {
                    if {[lindex $ov 0] eq $k} { set claimed 1; break }
                }
                if {$claimed} {
                    lappend overrode "${c}/[ipcfg::_key_leaf $k]"
                    continue
                }
            }
            set cur ""
            if {[catch {set cur [get_property $k [get_bd_cells $c]]}]} {
                incr n
                lappend bad "${c}/[ipcfg::_key_leaf $k](missing)"
                continue
            }
            # Same sub-key granularity as the write guard: a PS11_CONFIG that
            # gained settings the board never mentioned has not been tampered
            # with, and reporting it as drift would make the audit unreadable
            # on exactly the IP where it matters most.
            set wsz -1; set csz -1
            catch {set wsz [dict size $want]}
            catch {set csz [dict size $cur]}
            if {$wsz > 0 && $csz >= 0} {
                incr n $wsz
                foreach sk [dict keys $want] {
                    if {![dict exists $cur $sk]} {
                        lappend bad "${c}/[ipcfg::_key_leaf $k]<$sk>(dropped)"
                        continue
                    }
                    if {[ipcfg::_norm [dict get $want $sk]] ne [ipcfg::_norm [dict get $cur $sk]]} {
                        lappend bad "${c}/[ipcfg::_key_leaf $k]<$sk>(board set '[dict get $want $sk]', now '[dict get $cur $sk]')"
                    }
                }
                continue
            }
            incr n
            if {[ipcfg::_norm $cur] ne [ipcfg::_norm $want]} {
                lappend bad "${c}/[ipcfg::_key_leaf $k](board set '[string range $want 0 24]', now '[string range $cur 0 24]')"
            }
        }
    }
    set note ""
    if {[llength $overrode] > 0} {
        set note " (spec override: [join $overrode { }])"
    }
    if {$n == 0} { return "BOARD:none nothing was bound from the board" }
    if {[llength $bad] > 0} {
        return "BOARD_FAIL:[llength $bad]/$n hand-written over the board file\n  [join $bad "\n  "]\n  -> re-bind with ipcfg::board_bind; the board file owns these values. If the SPEC names one of these, pin it with ipcfg::require <cell> <dict> <reason> {} {spec statement} (5th argument board_override) instead -- that records the deviation and reports it here as an override.$note"
    }
    return "BOARD:OK $n/$n board-owned values intact$note"
}

proc ipcfg::forget_board {{cell ""}} {
    variable board_owned
    if {$cell eq ""} { set board_owned {}; return "BOARD:cleared all" }
    if {[dict exists $board_owned $cell]} { set board_owned [dict remove $board_owned $cell] }
    return "BOARD:cleared $cell"
}

# --- Disabled/ignored scan: catch gated params that catch{} returns 0 for ---
# In BD mode a gated/disabled parameter is often NOT a hard error: Vivado emits
# a non-fatal warning (e.g. [BD 41-721] / "disabled parameter ... ignored") and
# catch returns 0. Scan the raw vivado_execute output for these patterns so the
# agent can trigger PARAM_DISABLED recovery (find + set the enabling parent).
# Returns "" if none found, else "DISABLED:<matched lines>".
proc ipcfg::find_disabled {output} {
    set hits {}
    foreach line [split $output "\n"] {
        if {[string match {*disabled parameter*} $line] ||
            [string match {*BD 41-721*} $line] ||
            ([string match {*disabled*} $line] && [string match {*ignor*} $line])} {
            lappend hits [string trim $line]
        }
    }
    if {[llength $hits] == 0} { return "" }
    return "DISABLED:[join $hits { | }]"
}

# --- Generic block automation (Designer Assistance) ---
# rule:   xilinx.com:bd_rule:<name> (from doc search; e.g. cips, microblaze,
#         zynq_ultra_ps_e, axi_ethernet, board, mig_7series ...)
# config: optional {param "value" ...} pairs (from doc search)
proc ipcfg::try_automation {cell rule {config {}}} {
    if {[catch {ipcfg::attempt_call $cell automation {
        if {[llength $config] > 0} {
            apply_bd_automation -rule $rule -config $config [get_bd_cells $cell]
        } else {
            apply_bd_automation -rule $rule [get_bd_cells $cell]
        }
    }} e]} {
        return "CONFIGURE_FAIL:AUTOMATION_ERROR:$e"
    }
    return "SUCCESS:automation $rule on $cell"
}

# --- Generic connection-derived driver (replaces per-IP Phase 4 stubs) ---
# Attaches a boundary interface port to a cell's interface pin so that
# elaboration/validation can proceed (and so connection-driven params that
# accept a settable port width can be influenced).
#   pin:  interface pin name on the cell (e.g. S_AXI, S_AXIS_S2MM)
#   vlnv: interface VLNV (e.g. xilinx.com:interface:aximm_rtl:1.0)
#   prop/val: optional property to set on the stub port; tolerated if read-only
# The boundary port mode MIRRORS the pin mode (IPI "make external" rule);
# the caller does NOT pass a mode. If CONFIG.<prop> is read-only on the port
# the width is connection-inherited at integration time -> grade partial.
proc ipcfg::add_stub {cell pin vlnv {prop ""} {val ""}} {
    set sp "STUB_[string map {/ _} $pin]"
    set pinobj [get_bd_intf_pins -quiet $cell/$pin]
    if {$pinobj eq ""} { return "CONFIGURE_FAIL:STUB_ERROR:no intf pin $cell/$pin" }
    set mode [get_property MODE $pinobj]
    if {[catch {ipcfg::attempt_call $cell stub {
        create_bd_intf_port -mode $mode -vlnv $vlnv $sp
        if {$prop ne ""} {
            catch {set_property CONFIG.$prop $val [get_bd_intf_ports $sp]}
        }
        connect_bd_intf_net [get_bd_intf_ports $sp] $pinobj
    }} e]} {
        return "CONFIGURE_FAIL:STUB_ERROR:$e"
    }
    return "SUCCESS:stub ${sp}($mode)->$pin"
}

# --- Cleanup: remove STUB_* ports, delete the cell, restore part if needed ---
proc ipcfg::cleanup {cell {orig_part ""}} {
    variable mode
    if {$mode eq "assemble"} {
        # Persistent build: cells must survive across the assembly. Refuse to
        # delete. (Use ipcfg::set_mode benchmark to re-enable throwaway cleanup.)
        return "SKIP:cleanup disabled in assemble mode (cell $cell kept)"
    }
    foreach p [get_bd_intf_ports -quiet STUB_*] {
        catch {delete_bd_objs [get_bd_intf_nets -quiet -of_objects $p]}
        catch {delete_bd_objs $p}
    }
    foreach p [get_bd_ports -quiet STUB_*] {
        catch {delete_bd_objs [get_bd_nets -quiet -of_objects $p]}
        catch {delete_bd_objs $p}
    }
    if {[llength [get_bd_cells -quiet $cell]] > 0} {
        delete_bd_objs [get_bd_cells $cell]
    }
    if {$orig_part ne "" && [get_property PART [current_project]] ne $orig_part} {
        set bd [ipcfg::_bd_file]
        close_bd_design [current_bd_design]
        set_property PART $orig_part [current_project]
        open_bd_design $bd
    }
    return "SUCCESS:cleanup $cell"
}

# --- Single-call create + configure (perf) ---
# Creates and parameterizes in ONE call via the create_bd_cell -set_param fast
# path, skipping the init-to-default + separate set_property round-trip. Falls
# back to the classic create_cell + apply_dict when -set_param is unavailable on
# this Vivado version or the cell was created but a param failed (so apply_dict
# can classify the real error). IP-agnostic: vlnv/dict are passed in.
proc ipcfg::create_cell_cfg {vlnv cell d} {
    if {[llength [get_bd_cells -quiet $cell]] > 0} {
        return [ipcfg::apply_dict $cell $d]
    }
    if {[llength $d] == 0} { return [ipcfg::create_cell $vlnv $cell] }
    set mismatch [ipcfg::_part_mismatch $vlnv]
    if {$mismatch ne ""} { return "CONFIGURE_FAIL:WRONG_PART:$mismatch" }
    if {![catch {ipcfg::attempt_call $cell create_configure {create_bd_cell -type ip -vlnv $vlnv -set_param $d $cell}} e]} {
        return "SUCCESS:created+configured $cell (set_param)"
    }
    if {[string match "CONFIGURE_FAIL:ATTEMPT_*" $e]} { return $e }
    # Cell may have been created before the param phase failed: classify via apply_dict.
    if {[llength [get_bd_cells -quiet $cell]] > 0} {
        return [ipcfg::apply_dict $cell $d]
    }
    # -set_param unsupported here -> classic two-step path.
    set c [ipcfg::create_cell $vlnv $cell]
    if {[string match "CONFIGURE_FAIL:*" $c]} { return $c }
    return [ipcfg::apply_dict $cell $d]
}

# --- Disabled/gated param reconciliation (simpler than enabler-hunting) ---
# For params reported disabled/gated, decide which are already satisfied instead
# of always pulling them out and re-parameterizing. Compares each key's current
# (gated/read-only) value to the intended value:
#   - equal  -> already satisfied; safe to OMIT (no enabler search needed)
#   - differ -> genuine problem: a real enabler is missing OR the value is wrong
# Only the DIFFER set warrants an enabler doc-search + re-apply.
#   d: the {key want ...} dict you tried to set.
# Returns "OMIT:{k ...} DIFFER:{k(=cur,want=w) ...}"
proc ipcfg::reconcile_disabled {cell d} {
    set omit {}
    set differ {}
    foreach {k v} $d {
        if {[catch {set cur [get_property $k [get_bd_cells $cell]]}]} {
            lappend differ "${k}(missing)"
            continue
        }
        if {[ipcfg::_norm $cur] eq [ipcfg::_norm $v]} {
            lappend omit $k
        } else {
            lappend differ "${k}(=$cur,want=$v)"
        }
    }
    return "OMIT:{$omit} DIFFER:{$differ}"
}

# --- Full-config snapshot (idea #1) ---
# Capture the ENTIRE CONFIG.* dict of a cell in one pass -> {key val ...}.
# Use as a baseline before an apply/automation so config_diff can show every
# key that moved (including IP-driven side effects you did not request).
proc ipcfg::snapshot_all {cell} {
    set obj [get_bd_cells $cell]
    set out {}
    foreach p [list_property $obj] {
        if {[string match "CONFIG.*" $p]} {
            if {![catch {get_property $p $obj} v]} { lappend out $p $v }
        }
    }
    return $out
}

# --- Full-config diff with change classification (idea #1 + #2) ---
# Compare a cell's current full config to a baseline snapshot. Optionally pass a
# {key want ...} request map; requested keys are classified EXACT/RESOLVED/
# REVERTED, every other moved key is reported CHANGED (an IP side effect).
# Returns {key old new class ...}; unrequested+unchanged keys are dropped.
proc ipcfg::config_diff {cell baseline {reqmap {}}} {
    set now [ipcfg::snapshot_all $cell]
    set out {}
    foreach {k new} $now {
        set old  [expr {[dict exists $baseline $k] ? [dict get $baseline $k] : ""}]
        set want [expr {[dict exists $reqmap $k]   ? [dict get $reqmap $k]   : ""}]
        set cls  [ipcfg::classify_change $want $old $new]
        if {$want ne "" || $cls eq "CHANGED"} { lappend out $k $old $new $cls }
    }
    return $out
}

# --- System-intent heuristic (idea #5) ---
# Does the prompt describe a SUBSYSTEM realized through block automation / a
# wizard rather than plain standalone CONFIG.* props (integrated memory
# controllers, a PCIe controller, PS PL-fabric clocks, hardened peripherals)?
# Returns "SYSTEM:{reason ...}" or "STANDALONE". A SYSTEM verdict tells the agent
# to try automation-first and, if no rule exists and the params are gated, to
# report integration-derived honestly rather than fighting disabled CONFIG.
proc ipcfg::is_system_intent {text} {
    set t [string tolower $text]
    set reasons {}
    foreach {label pats} {
        integrated-memory-controller {{memory controller} {integrated ddr} ddr4 ddr5 lpddr interleav}
        pcie-controller              {pcie endpoint {root port} {root complex}}
        ps-fabric-clocks             {{pl clock} {fabric clock} {pl fabric} {processing system}}
        ps-hardened-peripheral       {can-fd canfd {on mio} pmc_mio ps_mio peripheral}
    } {
        foreach p $pats {
            if {[string first $p $t] >= 0} { lappend reasons $label; break }
        }
    }
    if {[llength $reasons] == 0} { return "STANDALONE" }
    return "SYSTEM:$reasons"
}

# --- Automation rule enumeration (idea #5) ---
# The loaded Designer-Assistance rules ARE enumerable via the internal command
#     ::bd::util_cmd rules dump
# which prints a "[DBG] RulesMap:" block mapping
#     "<vlnv-or-intf>":["<description>", "<rule-short-name>", "<rules.tcl path>"]
# The dump is a C-level message (NOT Tcl `puts`/return), so it cannot be captured
# in-proc; the AGENT runs the dump in one execute, reads the RulesMap from the
# output, and feeds that text here (same pattern as find_disabled).
# rule_for_vlnv returns "RULE:xilinx.com:bd_rule:<short> desc={...}" or "RULE:none".
# vlnv may be version-free (xilinx.com:ip:axi_noc2) or full (…:1.1).
proc ipcfg::rule_for_vlnv {dump vlnv} {
    foreach line [split $dump "\n"] {
        set line [string trim $line]
        if {![regexp {^"([^"]+)":\[(.*)\],?$} $line -> key rest]} continue
        if {$key ne $vlnv && ![string match "${vlnv}:*" $key]} continue
        set q [regexp -all -inline {"([^"]*)"} $rest]
        set desc  [lindex $q 1]   ;# 1st quoted = human description
        set short [lindex $q 3]   ;# 2nd quoted = rule short-name
        if {$short ne ""} { return "RULE:xilinx.com:bd_rule:$short desc={$desc}" }
    }
    return "RULE:none"
}

# --- Run a block automation rule and report what it changed ---
# Discover the rule with `::bd::util_cmd rules dump` + ipcfg::rule_for_vlnv (rule
# IDs are xilinx.com:bd_rule:<short>). apply_bd_automation is the apply entry point.
# This checks the options against the rule, applies it, and reports the CONFIG
# keys that changed on the cell (vs a baseline) and any cells it added, so the
# agent can confirm the result matches intent instead of guessing gated CONFIG. NOTE a bare
# apply with no -config is often a no-op for subsystem features (e.g. NoC MC needs
# its MC options in <config>); pass the rule's options (from its rules.tcl/docs).
proc ipcfg::_addr_space_count {cell} {
    set n 0
    catch {set n [llength [get_bd_addr_spaces -quiet -of_objects [get_bd_cells -quiet $cell]]]}
    return $n
}

proc ipcfg::run_automation {cell rule {config {}} {baseline {}}} {
    # apply_rule reports only the first missing/illegal option per attempt and
    # silently ignores undeclared keys; check the whole -config against the
    # rule's live widget schema first (no design mutation) and fill omitted keys
    # with the rule's GUI defaults.
    set defaulted {}
    set schema_note {}
    if {[catch {ipcfg::ruleopts::parse_widgets [ipcfg::ruleopts::live_widgets $cell $rule]} schema]} {
        set schema_note " rule_options=unavailable"
    } else {
        set checked [ipcfg::ruleopts::check $config $schema]
        set shown [ipcfg::ruleopts::format_schema $schema]
        if {[llength [dict get $checked unknown]]} {
            return "CONFIGURE_FAIL:UNKNOWN_AUTOMATION_KEY:[dict get $checked unknown] (the rule would ignore these) -> RECOVER:use only the rule's keys: $shown"
        }
        if {[llength [dict get $checked illegal]]} {
            set bad {}
            foreach {key detail} [dict get $checked illegal] { lappend bad "$key=[list [lindex $detail 0]] legal=[list [lindex $detail 1]]" }
            return "CONFIGURE_FAIL:ILLEGAL_AUTOMATION_VALUE:[join $bad {; }] (legal values reflect the current design/board context; if the needed value is absent this rule cannot provide it here -- use another route rather than retrying) schema: $shown"
        }
        set config [dict get $checked config]
        set defaulted [dict get $checked defaulted]
    }
    set cells_before [get_bd_cells -quiet]
    set spaces_before [ipcfg::_addr_space_count $cell]
    set wants_preset [expr {[dict exists $config board_preset] &&
        [string tolower [dict get $config board_preset]] in {yes true 1}}]
    # Re-running a board-preset rule on a cell that already carries its preset
    # adds nothing and, for the Versal PS wizard (verified 2026.1), deletes every
    # PS address space; the loss survives save/reopen and blocks addressing.
    if {$wants_preset && $spaces_before > 0 &&
        [string match PRESET:OK* [ipcfg::preset_drift $cell]]} {
        return "SKIP:board automation already applied to $cell (preset OK, $spaces_before address spaces); re-running it would delete them -> connect further interfaces with the connect helpers instead"
    }
    if {$baseline eq ""} { set baseline [ipcfg::snapshot_all $cell] }
    if {[catch {ipcfg::attempt_call $cell automation {
        if {[llength $config] > 0} {
            apply_bd_automation -rule $rule -config $config [get_bd_cells $cell]
        } else {
            apply_bd_automation -rule $rule [get_bd_cells $cell]
        }
    }} e]} {
        if {[string match "CONFIGURE_FAIL:ATTEMPT_*" $e]} { return $e }
        # Classify the common automation failures so the agent knows the NEXT move
        # instead of treating every failure as opaque (learned from a board-preset run):
        #   - missing required option key  -> call automation_config_schema, fill it
        #   - board-preset / part SysMon    -> set BOARD_PART / load preset first
        if {[regexp {key \"([^\"]+)\" not known in dictionary} $e -> miss] || [regexp {Could not find expected configuration value \"([^\"]+)\"} $e -> miss]} {
            return "CONFIGURE_FAIL:MISSING_AUTOMATION_KEY:$miss (run ipcfg::automation_config_schema <rule_file> to get required keys + defaults, then re-apply with a full -config)"
        }
        if {[regexp -nocase {SMON_MEAS|board.?preset|out of range.*PKG|usr_constraints} $e]} {
            return "CONFIGURE_FAIL:NEEDS_BOARD_PRESET:$e (set_property BOARD_PART / load the board preset before this automation -- see ipiasm::require_board_preset)"
        }
        return "CONFIGURE_FAIL:AUTOMATION_ERROR:$e (full rule messages are in the Vivado log)$schema_note"
    }
    set changed [ipcfg::config_diff $cell $baseline]
    set spaces_after [ipcfg::_addr_space_count $cell]
    if {$spaces_after < $spaces_before} {
        return "CONFIGURE_FAIL:ADDRESS_SPACES_LOST:$cell had $spaces_before address spaces, now $spaces_after after $rule -> RECOVER:this is not repairable in place; restore the block design saved before this automation"
    }
    set new_cells {}
    foreach c [get_bd_cells -quiet] {
        if {[lsearch -exact $cells_before $c] < 0} { lappend new_cells $c }
    }
    # Other automation options (PL clock/reset counts, ...) can overwrite values
    # the board preset owns; that is a hand-set on top of the board file.
    if {$wants_preset} {
        set preset [ipcfg::preset_drift $cell]
        if {[string match PRESET_DRIFT:* $preset]} {
            return "CONFIGURE_FAIL:PRESET_OVERRIDDEN:$preset -> RECOVER:drop the automation options that set these values (or pass the preset's own values) and re-apply; do not hand-write board-owned parameters"
        }
        return "AUTOMATION:OK changed={$changed} added_cells={$new_cells} default_options={$defaulted} board_preset={$preset}"
    }
    return "AUTOMATION:OK changed={$changed} added_cells={$new_cells} default_options={$defaulted}$schema_note"
}

# --- Automation rule CONFIG schema discovery ---
# Block-automation rules (ps_wizard, axi_noc2, visp_ss, cips, ...) REQUIRE an
# options dict; a bare apply or `{}` fails with `key "<k>" not known in dictionary`
# (ps_wizard -> mc_type, visp_ss -> mem_map, ...). Rather than reverse-engineer the
# rule by hand, this reads the rule's own .tcl (path is the 3rd element of the
# `::bd::util_cmd rules dump` RulesMap entry) and returns the option keys apply_rule
# READS plus their default values, so the agent can build a valid -config.
#
# It extracts two things from the rule file:
#   1) keys  = every `dict get <...param_dict|RULE.OPTIONS|opts...> "<key>"`
#   2) defaults = the `config [dict create <k>_none "X" ... noc_def "Y" ...]` literals,
#      resolved through the `set gui_values(<key>) "[dict get $config <cfgkey>]"` map.
# Returns: SCHEMA:keys={k1 k2 ...} defaults={k1 v1 k2 v2 ...} file=<path>
# (defaults are best-effort GUI defaults; combo-only keys with no literal default
#  are reported with value "" so the agent supplies one from intent/docs.)
proc ipcfg::automation_config_schema {rule_file} {
    if {![file exists $rule_file]} { return "SCHEMA_ERR:no rule file $rule_file" }
    set fh [open $rule_file r]; set txt [read $fh]; close $fh
    # Block automations split apply_rule (rule .tcl) and the worker (sibling
    # utils.tcl) -- e.g. ps_wizard reads mc_type in bd.tcl but boot_config/pl_clocks
    # in utils.tcl. Merge both so the key set is complete.
    set utils [file join [file dirname $rule_file] utils.tcl]
    if {[file exists $utils]} { set u [open $utils r]; append txt "\n[read $u]"; close $u }
    # 1) option keys read from the USER options dict. Capture the source var and
    #    whitelist it so we get RULE.OPTIONS aliases (param_dict, config_dict, opts)
    #    but NOT the rule's internal `$config` defaults dict.
    set keys {}
    foreach {full var key} [regexp -all -inline {dict get +\$?\{?([A-Za-z0-9_.]+)\}? +\"?([a-z0-9_]+)\"?} $txt] {
        if {![regexp {^(param_dict|RULE\.OPTIONS|opts|config_dict)$} $var]} continue
        if {[regexp {(_none|_def|_jtag|_yes|_nocpm|_option|_classic|_board)$} $key]} continue
        if {[lsearch -exact {name value value_list parent widgets config} $key] >= 0} continue
        lappend keys $key
    }
    set keys [lsort -unique $keys]
    # 2) literal defaults from the rule's `config` dict (lines like:  <key> "value" \)
    array set cfg {}
    foreach line [split $txt "\n"] {
        if {[regexp {^\s*([a-z0-9_]+)\s+\"([^\"]*)\"\s*\\?\s*$} $line -> k v]} { set cfg($k) $v }
    }
    # gui_values(<optkey>) "[dict get $config <cfgkey>]"  -> map optkey to its default
    array set defmap {}
    foreach m [regexp -all -inline {gui_values\(([a-z_]+)\)\s+\"\[dict get \$config ([a-z_]+)\]\"} $txt] {
        # regexp -all -inline returns full,sub1,sub2 triples
    }
    foreach {full ok ck} [regexp -all -inline {gui_values\(([a-z_]+)\)\s+\"\[dict get \$config ([a-z_]+)\]\"} $txt] {
        if {[info exists cfg($ck)]} { set defmap($ok) $cfg($ck) }
    }
    set defaults {}
    foreach k $keys {
        if {[info exists defmap($k)]} {
            lappend defaults $k $defmap($k)
        } elseif {[info exists cfg(${k}_none)]} {
            lappend defaults $k $cfg(${k}_none)
        } else {
            lappend defaults $k ""
        }
    }
    return "SCHEMA:keys={$keys} defaults={$defaults} file=$rule_file"
}

# --- Coverage report (partial-configuration disclosure, REQUIRED) ---
# Turns the Step 1.5 requirement ledger into a verdict + a list of prompt parts
# that could NOT be parameterized, so the skill always discloses what it failed
# to apply and why.
#   ledger: list of triples {requirement_text key outcome ...} where outcome is
#           applied | default | unapplied:<reason>
# Returns a multi-line report beginning COVERAGE:FULL or COVERAGE:PARTIAL.
#   Outcome vocabulary (per requirement):
#     applied | applied-resolved:<achieved> | default | unapplied:<reason>
#   - applied-resolved means the IP snapped the request to its nearest legal
#     value (config_diff/classify_change -> RESOLVED); it IS applied, just
#     disclosed separately so the request-vs-achieved difference is transparent.
proc ipcfg::coverage_report {ledger} {
    set applied {}
    set resolved {}
    set unapplied {}
    foreach {req key outcome} $ledger {
        set line "  - \"$req\" (param: $key): $outcome"
        if {[string match "unapplied*" $outcome]} {
            lappend unapplied $line
        } elseif {[string match "applied-resolved*" $outcome] || [string match "resolved*" $outcome]} {
            lappend resolved $line
        } else {
            lappend applied $line
        }
    }
    set verdict [expr {[llength $unapplied] == 0 ? "FULL" : "PARTIAL"}]
    set out "COVERAGE:$verdict"
    if {[llength $applied] > 0} {
        append out "\nApplied (exact):\n[join $applied "\n"]"
    }
    if {[llength $resolved] > 0} {
        append out "\nApplied (resolved to nearest legal value):\n[join $resolved "\n"]"
    }
    if {[llength $unapplied] > 0} {
        append out "\nNOT applied (could not parameterize from the prompt):\n[join $unapplied "\n"]"
    }
    return $out
}

# --- Scoped standalone introspection (idea #4): COMPLETE param neighborhood ---
# On a throwaway standalone `create_ip` cell, return the CONFIG.* params whose
# NAME matches a feature keyword (filtered — not a full dump). Use when a feature
# could be split across sibling params so a requirement maps to the COMPLETE set
# rather than the first name-match. Validated: mipi_csi2_rx + "lane" returns BOTH
# CONFIG.CMN_NUM_LANES and CONFIG.C_DPHY_LANES (+ active-lane siblings).
# This is the ONE sanctioned use of list_property (scoped, on a scratch cell).
# Returns "DISCOVER:<param ...>" or "DISCOVER_ERR:<msg>". The scratch IP is deleted.
proc ipcfg::discover_params {vlnv keyword {tmpdir /tmp/ipcfg_disc}} {
    set name "ipcfg_disc_[clock clicks]"
    file mkdir $tmpdir
    # Judge by whether the IP OBJECT exists, not by create_ip's return code. On
    # 2026.1 an "IPI-only" IP such as ps_wizard emits [Ipptcl 7-1663] as a mere
    # WARNING and is created successfully, so a rc/message test both under- and
    # over-reports. Only when no object appeared is the managed-IP path genuinely
    # unavailable and the caller must switch to the BD-cell path.
    catch {create_ip -vlnv $vlnv -module_name $name -dir $tmpdir} e
    if {[llength [get_ips -quiet $name]] == 0} {
        if {[string match {*IPI only*} $e] || [string match {*7-1663*} $e]} {
            return "DISCOVER_IPI_ONLY:$vlnv (use ipcfg::find_params on a BD cell)"
        }
        return "DISCOVER_ERR:$e"
    }
    # Synonym-broadened: a prompt word ("CAN-FD") rarely equals the parameter
    # spelling ("PS_CAN1_PERIPHERAL"). Stop at the first spelling that hits, so
    # precision is kept while a single-spelling miss no longer returns nothing.
    set hits {}
    foreach pat [ipcfg::_synonyms $keyword] {
        foreach p [list_property [get_ips $name]] {
            if {[string match -nocase "CONFIG.*$pat*" $p]} { lappend hits $p }
        }
        if {[llength $hits]} { break }
    }
    catch {
        remove_files [get_files -quiet $tmpdir/$name/$name.xci]
        file delete -force $tmpdir/$name
    }
    return "DISCOVER:$hits"
}

# ============================================================
# Native IP-Integrator introspection (Vivado 2026.1+, VIVADO-23126)
# VERSION-GATED: every proc degrades cleanly to the 2025.2 reactive path
# (discover_params + doc search + parse_range). Detection is by COMMAND
# PRESENCE, not the version string, because several 2026.1_* build trees report
# 2025.2.0 and lack these commands. Use SURGICALLY: dump_param_deps files are
# large (36 KB axi_gpio .. 172 KB ps_wizard .. 665 KB axi_noc) -- ALWAYS write to
# a file and read only the targeted param block, never inline the whole dump.
# ============================================================

# Detect+cache native capabilities once per session. Returns a dict:
#   version <s> dump_param_deps <0|1> can_connect <0|1>
proc ipcfg::native_caps {} {
    variable _native_caps
    if {[info exists _native_caps]} { return $_native_caps }
    set dpd [expr {[llength [info commands ::debug::dump_param_deps]] > 0}]
    set cc 0
    catch { set h ""; catch {bd::util_cmd -help} h; set cc [string match *can_connect* $h] }
    set ver "unknown"; catch { set ver [version -short] }
    set _native_caps [dict create version $ver dump_param_deps $dpd can_connect $cc]
    return $_native_caps
}
# Convenience gate: ipcfg::has_native dump_param_deps | can_connect -> 0/1
proc ipcfg::has_native {feature} {
    if {[catch {dict get [ipcfg::native_caps] $feature} v]} { return 0 }
    return $v
}

# --- dump_param_deps wrapper: resolved ranges + Enabled/Disabled + dep graph ---
# Writes the (large) dump to a FILE; returns a one-line handle. The agent then
# reads only the param block(s) it needs via ipcfg::param_block. On older Vivado
# returns PARAM_DEPS_NA so the caller falls back to discover_params + doc search.
#   ipname : a managed-IP instance name (make one with create_ip first). BD cell
#            names are NOT accepted by dump_param_deps.
# Pass `vlnv` to have this proc build the scratch managed IP itself (and remove it
# again afterwards -- the dump file is the artifact, so nothing is left behind).
#
# Do NOT pre-exclude "IPI-only" IPs. That guard used to assume `create_ip` cannot
# build them at all, which is FALSE on 2026.1: for `ps_wizard`, [Ipptcl 7-1663]
# "intended for use in IPI only" arrives as a **WARNING**, `create_ip` SUCCEEDS,
# and the dump is produced (172,636 B measured). A `catch` around `create_ip`
# therefore returns 0 and the return code says nothing useful either way. Success
# is decided by the OBSERVABLE ARTIFACT -- did the managed IP appear, and did the
# dump file materialize non-empty -- never by a return code or a message match.
proc ipcfg::param_deps {ipname {file ""} {vlnv ""} {tmpdir /tmp/ipcfg_pd}} {
    if {![ipcfg::has_native dump_param_deps]} { return "PARAM_DEPS_NA" }
    if {$file eq ""} { set file /tmp/ipcfg_pd_[string map {/ _ : _} $ipname].txt }
    # Clear any stale dump so "did the file materialize" is a real test.
    catch {file delete -force -- $file}
    set scratch 0
    if {[llength [get_ips -quiet $ipname]] == 0} {
        if {$vlnv eq ""} {
            return "PARAM_DEPS_NO_MANAGED_IP:$ipname (no such managed IP --\
create_ip first, or pass the vlnv to have param_deps build a scratch one)"
        }
        catch {file mkdir $tmpdir}
        catch {create_ip -vlnv $vlnv -module_name $ipname -dir $tmpdir} ce
        if {[llength [get_ips -quiet $ipname]] == 0} {
            return "PARAM_DEPS_NO_MANAGED_IP:$ipname (create_ip $vlnv produced no\
managed IP: $ce)"
        }
        set scratch 1
    }
    catch {::debug::dump_param_deps -filename $file $ipname} e
    set sz [expr {[file exists $file] ? [file size $file] : 0}]
    if {$scratch} {
        catch {
            remove_files [get_files -quiet $tmpdir/$ipname/$ipname.xci]
            file delete -force $tmpdir/$ipname
        }
    }
    if {$sz == 0} { return "PARAM_DEPS_ERR:$e" }
    if {$scratch} { return "PARAM_DEPS:$file:$sz scratch=1" }
    return "PARAM_DEPS:$file:$sz"
}

# --- targeted read: pull ONE param's block (Range/Enabled/Disabled/Default) ---
# Returns just that param's block (token-cheap), never the whole dump. Blocks in
# the dump are separated by lines of asterisks.
proc ipcfg::param_block {file param} {
    if {![file exists $file]} { return "PB_ERR:no file $file" }
    set fh [open $file r]
    set cur {}; set found ""
    while {[gets $fh ln] >= 0} {
        if {[regexp {^\*{5,}} $ln]} {
            set txt [join $cur "\n"]
            if {[regexp -line "^Parameter Name:\\s+$param\\s*$" $txt]} { set found $txt; break }
            set cur {}
        } else {
            lappend cur $ln
        }
    }
    if {$found eq ""} {
        set txt [join $cur "\n"]
        if {[regexp -line "^Parameter Name:\\s+$param\\s*$" $txt]} { set found $txt }
    }
    close $fh
    if {$found eq ""} { return "PB_MISS:$param" }
    return [string trim $found]
}

# --- keyword neighborhood FROM THE DUMP (the dump-side discover_params) ---
# This is what lets the 2026.1 dump path REPLACE `discover_params` instead of
# merely preceding it. Without it the dump could only answer about a param you
# could already name, so "map the requirement to the COMPLETE sibling set" still
# forced a second scratch-cell `create_ip` + `list_property` round trip -- measured
# on case 016, where the agent called param_deps and then discover_params three
# more times, making the native path cost MORE than the legacy one.
#
# Reads only user-facing `PARAM_VALUE.*` ids, so the MODELPARAM twins never double
# the list. Keyword is synonym-broadened and stops at the first spelling that hits,
# exactly like discover_params, so the two are interchangeable in behaviour.
#   keyword : feature word from the prompt; "" or "*" lists every param.
# Returns "DUMP_PARAMS:CONFIG.<a> CONFIG.<b> ..." | "DUMP_PARAMS:none" | "GR_ERR:...".
proc ipcfg::dump_params {file {keyword ""}} {
    if {![file exists $file]} { return "GR_ERR:no file $file" }
    set fh [open $file r]
    set all {}
    while {[gets $fh ln] >= 0} {
        if {[regexp {^Parameter ID:\s+PARAM_VALUE\.(\S+)\s*$} $ln -> nm]} {
            lappend all $nm
        }
    }
    close $fh
    set all [lsort -unique $all]
    if {[llength $all] == 0} { return "DUMP_PARAMS:none" }
    if {$keyword eq "" || $keyword eq "*"} {
        return "DUMP_PARAMS:CONFIG.[join $all { CONFIG.}]"
    }
    foreach pat [ipcfg::_synonyms $keyword] {
        set hits {}
        foreach p $all {
            if {[string match -nocase "*$pat*" $p]} { lappend hits $p }
        }
        if {[llength $hits]} {
            return "DUMP_PARAMS:CONFIG.[join $hits { CONFIG.}]"
        }
    }
    return "DUMP_PARAMS:none"
}

# --- the legal values for ONE param, straight out of the dump ---
# Blocks carry the range in one of two shapes:
#   numeric : "Range Minimum:  1 Maximum: 32"
#   enum    : "Range List:       0 false" + indented continuation lines "1 true"
# (the value is the FIRST token on each line; the rest is its display label).
# Returns "RANGE_NUM:<lo>:<hi>" | "RANGE_LIST:<v> ..." | "RANGE_NONE" | "PB_*".
proc ipcfg::param_range {file param} {
    set blk [ipcfg::param_block $file $param]
    if {[string match "PB_*" $blk]} { return $blk }
    if {[regexp -line {^Range Minimum:\s*(\S+)\s+Maximum:\s*(\S+)\s*$} $blk -> lo hi]} {
        return "RANGE_NUM:$lo:$hi"
    }
    set vals {}; set in 0
    foreach ln [split $blk \n] {
        if {[regexp {^Range List:\s*(.*)$} $ln -> rest]} {
            set in 1
            set rest [string trim $rest]
            if {$rest ne ""} { lappend vals [lindex $rest 0] }
            continue
        }
        if {$in} {
            # continuation lines are indented; the next real field starts at col 0
            if {[regexp {^\s+(\S+)} $ln -> v]} { lappend vals $v } else { break }
        }
    }
    if {[llength $vals]} { return "RANGE_LIST:[join $vals { }]" }
    return "RANGE_NONE"
}

# --- internal: fold away case and separators, to spot a near-miss spelling ---
proc ipcfg::_val_norm {v} {
    return [string tolower [string map {_ "" - "" " " ""} $v]]
}

# --- PRE-flight value check: validate BEFORE writing, not after ---
# Saves the whole failed-set round trip, and catches the class of bug where Vivado
# accepts a wrong value without complaint. A misspelled enum is the dangerous one:
# a Versal CIPS request once wrote `PCIe0x2_10GbE` for the legal `PCIe0_x2_10GbE`
# (one missing underscore), Vivado did NOT validate it, and the miss only surfaced
# at grading -- so when a value is not in the list we also report the near-miss.
# Returns "VAL_OK:<param>=<want>"
#       | "VAL_OUT_OF_RANGE:<param>=<want> legal=<lo>..<hi>"
#       | "VAL_NOT_IN_LIST:<param>=<want> legal={...}" (+ " did_you_mean=<v>")
#       | "VAL_UNCHECKED:<param> (<why>)"   <- no range published; not an error
proc ipcfg::check_value {file param want} {
    set r [ipcfg::param_range $file $param]
    if {[string match "PB_*" $r]} { return "VAL_UNCHECKED:$param ($r)" }
    if {$r eq "RANGE_NONE"} { return "VAL_UNCHECKED:$param (no range published)" }
    if {[string match "RANGE_NUM:*" $r]} {
        lassign [split $r :] _ lo hi
        if {![ipcfg::_is_num $want]} {
            return "VAL_UNCHECKED:$param (non-numeric value vs numeric range $lo..$hi)"
        }
        if {double($want) < double($lo) || double($want) > double($hi)} {
            return "VAL_OUT_OF_RANGE:$param=$want legal=$lo..$hi"
        }
        return "VAL_OK:$param=$want"
    }
    set legal [lrange [split $r :] 1 end]
    set legal [split [join $legal :] " "]
    if {$want in $legal} { return "VAL_OK:$param=$want" }
    set hint ""
    foreach v $legal {
        if {[ipcfg::_val_norm $v] eq [ipcfg::_val_norm $want]} { set hint $v; break }
    }
    set out "VAL_NOT_IN_LIST:$param=$want legal={[join $legal { }]}"
    if {$hint ne ""} { append out " did_you_mean=$hint" }
    return $out
}

# --- internal: parse the dependency graph out of a param_deps dump ---
# The dump ends with a self-describing `digraph G { ... }` whose nodes are
# fully-qualified parameter ids (PARAM_VALUE.X / MODELPARAM_VALUE.C_X /
# PORT_ENABLEMENT.<port> / BUSIF_ENABLEMENT.<intf> / DICT_PARAM_KEY.<D><K>).
# Fills caller arrays: nodes(id)=label, preds(dstid)=list of src ids.
# Returns "" on success or a GR_* error token.
proc ipcfg::_graph_load {file nodesVar predsVar} {
    upvar 1 $nodesVar nodes $predsVar preds
    if {![file exists $file]} { return "GR_ERR:no file $file" }
    set fh [open $file r]
    set in 0; set n 0
    while {[gets $fh ln] >= 0} {
        if {!$in} {
            if {[string match "digraph G *" $ln]} { set in 1 }
            continue
        }
        if {[regexp {^([0-9]+)\[label="([^"]*)"\]} $ln -> id lab]} {
            set nodes($id) $lab; incr n
        } elseif {[regexp {^([0-9]+)->([0-9]+)} $ln -> a b]} {
            lappend preds($b) $a
        }
    }
    close $fh
    if {!$in} { return "GR_ERR:no dependency graph in $file" }
    if {$n == 0} { return "GR_ERR:graph has no nodes" }
    return ""
}

# --- internal: strip a vendor C_ prefix so mirrored names can be compared ---
proc ipcfg::_graph_bare {name} {
    if {[string match "C_*" $name]} { return [string range $name 2 end] }
    return $name
}

# --- internal: map a graph node back to a SETTABLE CONFIG.* parameter name ---
# PARAM_VALUE.X is already user-facing. MODELPARAM_VALUE.C_X is the HDL mirror and
# is NOT settable, so hop one edge back to the PARAM_VALUE that drives it.
#
# The hop is NOT a simple "take the first predecessor": a modelparam can be driven
# by many user params. Measured on 2026.1 clkx5_wiz, MODELPARAM_VALUE.C_USE_RESET
# has SEVEN PARAM_VALUE predecessors (USE_RESET AUTO_PRIMITIVE DESKEW1_LOCK_CIRCUIT_EN
# DESKEW2_LOCK_CIRCUIT_EN PRIMITIVE_TYPE CLKOUT_USED CLKOUT_DYN_PS), so picking the
# first would make the answer depend on hash order. Resolution, in order:
#   1. the predecessor whose name IS the mirror (C_USE_RESET <- USE_RESET) -> settable
#   2. a single predecessor, even if named differently (C_CLKIN1_BUFG <- PRIM_SOURCE)
#   3. otherwise the modelparam is DERIVED (C_CLK_TREE1 is computed from 9 params and
#      has no user-facing twin) -> there is no one param to set, so return the whole
#      contributor set tagged "derived" and let the caller present it as indirect.
# Returns a 2-element list: {settable <param>} | {derived <param ...>} | {none {}}
proc ipcfg::_graph_mirror {id nodesVar predsVar} {
    upvar 1 $nodesVar nodes $predsVar preds
    if {![info exists nodes($id)]} { return [list none {}] }
    set lab $nodes($id)
    if {[string match "PARAM_VALUE.*" $lab]} {
        return [list settable [string range $lab 12 end]]
    }
    if {![string match "MODELPARAM_VALUE.*" $lab]} { return [list none {}] }
    set base [ipcfg::_graph_bare [string range $lab 17 end]]
    set pvs {}
    if {[info exists preds($id)]} {
        foreach p $preds($id) {
            if {[info exists nodes($p)] && [string match "PARAM_VALUE.*" $nodes($p)]} {
                lappend pvs [string range $nodes($p) 12 end]
            }
        }
    }
    set pvs [lsort -unique $pvs]
    foreach c $pvs {
        if {[ipcfg::_graph_bare $c] eq $base} { return [list settable $c] }
    }
    if {[llength $pvs] == 1} { return [list settable [lindex $pvs 0]] }
    if {[llength $pvs] == 0} { return [list none {}] }
    return [list derived $pvs]
}

# --- AUTHORITATIVE gating: which params decide whether a port/interface EXISTS ---
# This is the ground truth that check_enablers can only GUESS at by name, and that
# the flat `Enabled:`/`Disabled:` fields mostly do not carry (measured on 2026.1
# clkx5_wiz: only 62 of 457 param blocks have a non-empty Enabled/Disabled, and
# RESET_TYPE's is EMPTY even though the reset pin genuinely depends on USE_RESET).
# The dependency graph does carry it, as PORT_ENABLEMENT.* / BUSIF_ENABLEMENT.*
# nodes, so ask the graph BEFORE the first write and fold the answers into the
# same -dict instead of discovering the miss during verification.
#
# CRITICAL -- DIRECT PREDECESSORS ONLY, never transitive closure. The graph is
# densely connected through `xgui` value-recalculation edges, so a transitive
# reverse walk from PORT_ENABLEMENT.RESETN on clkx5_wiz returns 65 params (i.e.
# essentially every param on the IP) and is useless. The depth-1 predecessor set
# is 4 params and is exactly right. Measured on 2026.1:
#   PORT_ENABLEMENT.RESETN -> USE_RESET RESET_TYPE INTERFACE_SELECTION USE_DYN_RECONFIG
#   PORT_ENABLEMENT.LOCKED -> USE_LOCKED
#   BUSIF_ENABLEMENT.GPIO2 -> C_IS_DUAL          (axi_gpio)
#   BUSIF_ENABLEMENT.IP2INTC_IRQ -> C_INTERRUPT_PRESENT
#
# SCOPE: only IPs whose dump actually has enablement nodes. CIPS-class subsystem
# IPs have NONE (2026.1 ps_wizard: 34 graph nodes, 0 PORT/BUSIF_ENABLEMENT), so a
# PE_MISS there means "this IP does not publish port gating" -- NOT "ungated".
#   file : a dump produced by ipcfg::param_deps
#   port : exact port or interface name (case-sensitive, as the IP spells it)
# Returns, in order of usefulness:
#   "PE:<port>:<param> ..."             params to set (may add DERIVED{...})
#   "PE_DERIVED:<port>:<param> ..."     no directly settable param -- the port is
#                                       gated by a COMPUTED value; these params
#                                       feed it, so change them and re-read
#   "PE_NONE:<port>"                    node exists with no dependencies
#   "PE_MISS:<port>"                    no such enablement node (see SCOPE above)
#   "GR_ERR:..."                        bad/missing dump
proc ipcfg::port_enablers {file port} {
    array set nodes {}; array set preds {}
    set err [ipcfg::_graph_load $file nodes preds]
    if {$err ne ""} { return $err }
    set want [list "PORT_ENABLEMENT.$port" "BUSIF_ENABLEMENT.$port"]
    set hits {}
    foreach id [array names nodes] {
        if {$nodes($id) in $want} { lappend hits $id }
    }
    if {[llength $hits] == 0} { return "PE_MISS:$port" }
    set params {}; set derived {}; set unmapped {}
    foreach id $hits {
        if {![info exists preds($id)]} { continue }
        foreach p $preds($id) {
            lassign [ipcfg::_graph_mirror $p nodes preds] kind val
            switch -- $kind {
                settable { lappend params $val }
                derived  { foreach v $val { lappend derived $v } }
                default  { if {[info exists nodes($p)]} { lappend unmapped $nodes($p) } }
            }
        }
    }
    set params  [lsort -unique $params]
    set derived [lsort -unique $derived]
    # A param that is directly settable for this port is not also "indirect".
    foreach p $params {
        set derived [lsearch -all -inline -not -exact $derived $p]
    }
    if {[llength $params] == 0 && [llength $derived] == 0 && [llength $unmapped] == 0} {
        return "PE_NONE:$port"
    }
    if {[llength $params] == 0} {
        set out "PE_DERIVED:$port:[join $derived { }]"
    } else {
        set out "PE:$port:[join $params { }]"
        if {[llength $derived] > 0} {
            append out " DERIVED{[join $derived { }]}"
        }
    }
    if {[llength $unmapped] > 0} {
        append out " UNMAPPED{[join [lsort -unique $unmapped] { }]}"
    }
    return $out
}

# --- companion: list every port/interface whose gating the graph publishes ---
# Use when you know the behaviour but not how the IP spells the port, and to tell
# "this IP publishes no gating at all" (CIPS-class) apart from "this port is
# ungated". `pat` is an optional glob over the port name.
# Returns "PE_PORTS:<name> ..." or "PE_PORTS:none".
proc ipcfg::enablement_ports {file {pat *}} {
    array set nodes {}; array set preds {}
    set err [ipcfg::_graph_load $file nodes preds]
    if {$err ne ""} { return $err }
    set out {}
    foreach id [array names nodes] {
        if {[regexp {^(?:PORT|BUSIF)_ENABLEMENT\.(.+)$} $nodes($id) -> nm]} {
            if {[string match $pat $nm]} { lappend out $nm }
        }
    }
    set out [lsort -unique $out]
    if {[llength $out] == 0} { return "PE_PORTS:none" }
    return "PE_PORTS:[join $out { }]"
}

# --- nested-dict introspection (PS/CIPS-class subsystem IPs) ---
# dump_param_deps emits per-param Range/Enabled/Disabled metadata only for
# TOP-LEVEL params. Subsystem IPs carry the real sub-keys inside ONE resolved
# dict value (ps_wizard PS11_CONFIG_INTERNAL = a ~1315-key flat Tcl dict;
# versal_cips PS_PMC_CONFIG/CPM_CONFIG similarly). This recovers sub-key NAMES +
# CURRENT/DEFAULT VALUES (no doc search needed); per-sub-key valid-range and
# gating are still NOT provided here -> use doc / set-and-observe for those.
#   dictparam : the param whose Current Value is a dict (e.g. PS11_CONFIG_INTERNAL).
#   subkey    : "" -> list all top-level sub-keys; else -> that sub-key's value.
proc ipcfg::param_dict {file dictparam {subkey ""}} {
    set blk [ipcfg::param_block $file $dictparam]
    if {[string match "PB_*" $blk]} { return $blk }
    if {![regexp -line {^Current Value:(.*)$} $blk -> val]} { return "PD_ERR:no current value" }
    set val [string trim $val]
    if {$val eq "" || ![string is list $val] || [llength $val] % 2 != 0} {
        return "PD_ERR:value not a dict (len=[string length $val])"
    }
    if {$subkey eq ""} { return "PD_KEYS:[dict keys $val]" }
    if {[dict exists $val $subkey]} { return "PD:$subkey=[dict get $val $subkey]" }
    return "PD_MISS:$subkey"
}

# --- internal: every DICT_PARAM_KEY node in the graph, grouped by owning dict ---
# Labels look like: 11[label="DICT_PARAM_KEY.MMI_CONFIG<MDB5_GT>"]
# Fills map(<dictparam>) = list of sub-key names. Returns "" or a GR_* token.
proc ipcfg::_dict_nodes {file mapVar} {
    upvar 1 $mapVar map
    array set nodes {}; array set preds {}
    set e [ipcfg::_graph_load $file nodes preds]
    if {$e ne ""} { return $e }
    foreach id [array names nodes] {
        if {[regexp {^DICT_PARAM_KEY\.([^<]+)<(.+)>$} $nodes($id) -> d k]} {
            lappend map($d) $k
        }
    }
    return ""
}

# --- dict SUB-KEY NAMES from the graph -- the names param_dict cannot see ---
# param_dict parses the RESOLVED value of a dict param, so it only ever reports
# sub-keys that are CURRENTLY SET. Measured on 2026.1 ps_wizard: MMI_CONFIG
# resolves to exactly ONE key (MMI_GPU_ENABLE), while the dependency graph names
# 13 of them -- so param_dict returns PD_MISS for MDB5_GT / MMI_PCIE0_PORT_TYPE /
# MMI_PCIE0_PERST even though the dump on disk knows all three.
# The graph is therefore the authority for NAMES, param_dict for VALUES, and this
# proc returns the UNION. This closes the silent-typo class on subsystem IPs: an
# agent that cannot list MMI_PCIE0_PORT_TYPE guesses its spelling (PCIe0x2_10GbE
# for the legal PCIe0_x2_10GbE) and Vivado accepts the bad write without error.
# Per-sub-key RANGES are still not published here -> ipcfg::probe_subkey_range.
#   dictparam : "" -> summarise every dict found; else -> that dict's sub-keys.
# Returns "DK_DICTS:<D>(<n>) ..." | "DK:<D>:<k> ..." | "DK_MISS:<D>" | "DK_NONE"
proc ipcfg::dict_keys {file {dictparam ""}} {
    array set map {}
    set e [ipcfg::_dict_nodes $file map]
    if {$e ne ""} { return [string map {GR_ERR DK_ERR} $e] }
    if {[array size map] == 0} { return "DK_NONE" }
    if {$dictparam eq ""} {
        set out {}
        foreach d [lsort [array names map]] {
            lappend out "${d}([llength [lsort -unique $map($d)]])"
        }
        return "DK_DICTS:[join $out { }]"
    }
    if {![info exists map($dictparam)]} { return "DK_MISS:$dictparam" }
    set keys $map($dictparam)
    set pd [ipcfg::param_dict $file $dictparam]
    if {[regexp {^PD_KEYS:(.*)$} $pd -> extra]} {
        foreach k $extra { lappend keys $k }
    }
    return "DK:$dictparam:[join [lsort -unique $keys] { }]"
}

# --- keyword search ACROSS dict sub-keys (the discover_params of nested dicts) ---
# This is the call that answers "what is the PCIe port-type key actually called".
# Returns "DF:<D><<k>> ..." | "DF:none" | "DF_ERR:..."
proc ipcfg::dict_find {file keyword} {
    array set map {}
    set e [ipcfg::_dict_nodes $file map]
    if {$e ne ""} { return [string map {GR_ERR DF_ERR} $e] }
    set hits {}
    foreach pat [ipcfg::_synonyms $keyword] {
        foreach d [lsort [array names map]] {
            foreach k [lsort -unique $map($d)] {
                if {[string match -nocase "*$pat*" $k]} { lappend hits "$d<$k>" }
            }
        }
        if {[llength $hits]} { break }
    }
    if {[llength $hits] == 0} { return "DF:none" }
    return "DF:[join [lsort -unique $hits] { }]"
}

# --- internal: render a param_range result compactly for a plan line ---
proc ipcfg::_plan_rng {r} {
    if {[string match "RANGE_NUM:*" $r]} {
        lassign [split $r :] _ lo hi
        return "\[$lo..$hi\]"
    }
    if {[string match "RANGE_LIST:*" $r]} {
        set v [lrange [split $r :] 1 end]
        set v [split [join $v :] " "]
        if {[llength $v] > 8} {
            return "{[join [lrange $v 0 7] { }] ...+[expr {[llength $v]-8}]}"
        }
        return "{[join $v { }]}"
    }
    return "(no range)"
}

# --- ONE-CALL discovery: the whole pre-write protocol against an existing dump ---
# Replaces the sequence the agent otherwise has to orchestrate by hand
# (dump_params -> param_block -> param_range, then separately realise the IP is a
# subsystem and go hunting for dict sub-keys). Adoption of the multi-step protocol
# was measured at 0-8%; a single documented call is what actually gets used.
# For every keyword it reports the matching TOP-LEVEL params with their legal
# values AND the matching DICT sub-keys, so a subsystem IP no longer looks like an
# IP with no matching parameters.
# Returns a multi-line "PLAN:" block, or "PLAN_ERR:<why>".
proc ipcfg::plan {file args} {
    if {![file exists $file]} { return "PLAN_ERR:no file $file" }
    if {[llength $args] == 0} { return "PLAN_ERR:no keywords given" }
    set out {}
    foreach kw $args {
        set lines {}
        set top [ipcfg::dump_params $file $kw]
        if {[regexp {^DUMP_PARAMS:(.*)$} $top -> tl] && $tl ne "none"} {
            foreach p $tl {
                set leaf [ipcfg::_key_leaf $p]
                lappend lines "  $p [ipcfg::_plan_rng [ipcfg::param_range $file $leaf]]"
            }
        }
        set sub [ipcfg::dict_find $file $kw]
        if {[regexp {^DF:(.*)$} $sub -> sl] && $sl ne "none"} {
            foreach s $sl { lappend lines "  DICT $s (range: probe_subkey_range)" }
        }
        if {[llength $lines] == 0} { set lines [list "  none"] }
        lappend out "KW $kw\n[join $lines \n]"
    }
    return "PLAN:\n[join $out \n]"
}

# --- the protocol and the proc index, IN the library instead of in SKILL.md ---
# SKILL.md used to be 1282 lines / 93 KB, and its Overview named doc-search as
# "Tier 1" while the faster native path sat at line 394 behind a routing table.
# Measured adoption of the native path was 0-8%: agents follow the overview they
# read first. Keeping the protocol here means the entry-point doc stays small and
# the authoritative version ships with the code that implements it.
#   ipcfg::api          -> the mandatory step order
#   ipcfg::api <pat>    -> matching procs, one line each
# --- help: call shape, purpose and result tokens of the loaded helpers ---
# Built from the live procedures, so it cannot drift from the code:
#   ipcfg::help board_bind   ipcfg::help *irq*   ipcfg::help ipiasm::*memory*
proc ipcfg::_help_purposes {} {
    variable help_purposes
    if {[info exists help_purposes]} { return $help_purposes }
    set help_purposes {}
    set dirs [list [file dirname [info script]] $::ipcfg::dir]
    if {[info exists ::ipiasm::library_dir]} { lappend dirs $::ipiasm::library_dir }
    foreach path [lsort -unique [concat {*}[lmap d [lsort -unique $dirs] {glob -nocomplain -directory $d *.tcl}]]] {
        set channel [open $path r]
        set lines [split [read $channel] \n]
        close $channel
        set comment {}
        foreach line $lines {
            if {[regexp {^\s*#\s?(.*)$} $line -> text]} {
                lappend comment [string trim [regsub -all {^-+\s*|\s*-+$} [string trim $text] {}]]
            } elseif {[regexp {^proc\s+(?:::)?((?:ipcfg|ipiasm)::[A-Za-z0-9_]+)\s} $line -> name]} {
                set purpose [lsearch -all -inline -not -exact $comment {}]
                dict set help_purposes $name [string range [join [lrange $purpose 0 1] { }] 0 159]
                set comment {}
            } else {
                set comment {}
            }
        }
    }
    return $help_purposes
}

proc ipcfg::help {{pattern *}} {
    if {![string match *::* $pattern]} { set pattern "*$pattern*" }
    set purposes [ipcfg::_help_purposes]
    set names {}
    foreach namespace {::ipcfg ::ipiasm} {
        if {![namespace exists $namespace]} { continue }
        foreach proc [info procs ${namespace}::*] {
            set name [string trimleft $proc :]
            if {[string match _* [namespace tail $name]] || ![string match $pattern $name]} { continue }
            lappend names $name
        }
    }
    set names [lsort $names]
    if {![llength $names]} { return "HELP:none match '$pattern' -- try a shorter word" }
    set out {}
    foreach name [lrange $names 0 24] {
        set call $name
        foreach argument [info args ::$name] {
            if {[info default ::$name $argument value]} {
                append call " ?$argument=[expr {$value eq {} ? {{}} : $value}]?"
            } elseif {$argument eq "args"} {
                append call " ?args...?"
            } else {
                append call " $argument"
            }
        }
        set ok {}; set fail {}
        foreach {- family token} [regexp -all -inline {return "([A-Z][A-Z0-9_]*):([A-Za-z+]*)} [info body ::$name]] {
            if {[regexp {FAIL|ERR} $family]} { lappend fail $family } else { lappend ok [expr {$token eq "" ? "$family:" : "$family:$token"}] }
        }
        lappend out $call
        if {[dict exists $purposes $name] && [dict get $purposes $name] ne ""} { lappend out "    [dict get $purposes $name]" }
        set results {}
        if {[llength $ok]} { lappend results "ok: [join [lsort -unique $ok] { }]" }
        if {[llength $fail]} { lappend results "fail: [join [lsort -unique $fail] { }]" }
        if {[llength $results]} { lappend out "    [join $results {   }]" }
    }
    if {[llength $names] > 25} { lappend out "... [expr {[llength $names] - 25}] more; narrow the pattern" }
    return [join $out \n]
}

proc ipcfg::api {{pat ""}} {
    set steps {STEPS (2026.1: run has_native first; 0 -> use the 2025.2 column)
 0 part      ensure_part / restore_part when the IP needs another device family
 1 identity  vlnv_ok, then ip_availability if the VLNV is not on this part
 2 discover  2026.1: param_deps <vlnv> to a FILE, then plan <file> <keywords>
              2025.2: discover_params / vivado_doc_search
              NEVER read a whole dump inline (172 KB ps_wizard, 665 KB axi_noc)
 3 gating    port_enablers <file> <port> BEFORE the first write, and fold the
              answer into the SAME -dict (a value set while its enabler is off
              reads back fine and produces no port)
 4 subkeys   subsystem IPs (ps_wizard, versal_cips): dict_keys / dict_find for
              the real sub-key names, probe_subkey_range for their legal values
 5 validate  check_value for every value you are about to write
 6 board     on a board part, board_bind <cell> {CONFIG.<X>_BOARD_INTERFACE <intf>}
              BEFORE writing anything else -- the board file already answers
              those parameters, and what it writes is board-owned from then on
 7 pin+write require <cell> <dict> <reason> -- pins the intent AND writes it,
              so a spec value cannot be pinned and then forgotten. Returns
              REQ_FAIL when the write was refused: recover, do not move on
 8 apply     create_cell_cfg, or apply_dict for a write you are NOT pinning --
              ONE -dict. On failure call autofix_apply.
              NEVER hand-rewrite the dict: that regresses keys the failure had
              nothing to do with
 9 verify    verify_stuck + check_enablers + verify_intent, then
              audit_requirements and audit_board LAST; scan the output for
              BD 41-1276 and IP_Flow 19-7090, which do NOT throw
10 cleanup   cleanup <cell> <orig_part>}
    set idx {
 has_native        version gate by COMMAND PRESENCE, not version string -> 0/1
 native_caps       which native VIVADO-23126 commands this build has
 ensure_part       swap PART when the IP needs another family; restore_part undoes
 vlnv_ok           does this VLNV exist in the catalogue
 ip_availability   which parts support a candidate IP list
 param_deps        2026.1 dump (ranges + dependency graph) to a FILE; pass a vlnv
                   and it builds and removes the scratch managed IP itself
 plan              ONE call: keyword -> top-level params WITH ranges + dict sub-keys
 dump_params       the dump-side discover_params (PARAM_VALUE.* only)
 param_block       ONE param block out of a dump; never inline the whole dump
 param_range       legal values of one param -> RANGE_NUM / RANGE_LIST / RANGE_NONE
 check_value       pre-flight a value, with a did_you_mean on a near-miss enum
 port_enablers     AUTHORITATIVE port gating from the graph (depth-1 by design;
                   a transitive walk returns nearly every param and is worthless)
 enablement_ports  which ports this IP publishes gating for
 dict_keys         dict sub-key NAMES from the graph UNION the resolved value
                   (param_dict alone sees only sub-keys currently set)
 dict_find         keyword search across dict sub-keys; recovers exact spelling
 param_dict        sub-key VALUES out of a resolved dict value
 probe_subkey_range  per-sub-key legal set via non-destructive set-and-observe;
                   needs capture_log=true, works on 2025.2 AND 2026.1
 discover_params   2025.2 route: synonym-broadened param search on a live cell
 create_cell_cfg   create a BD cell and apply its -dict in one go
 apply_dict        apply a -dict to an existing cell (ATOMIC: a failure applies
                   nothing and usually names no key -> recover with autofix_apply)
 autofix_apply     retry a failed -dict per key using Vivado's OWN feedback, so
                   one bad key cannot cost you the keys that were already right
 board_hooks       the CONFIG.<X>_BOARD_INTERFACE hooks this cell exposes
 board_enablers    boolean board flags that may be gating those hooks
 board_bind        bind board interfaces: applies the preset, retries behind the
                   enable flag if refused, records what the board now OWNS
 board_owned       the keys the board wrote (apply_dict refuses to overwrite them)
 audit_board       did anything hand-write over the board file
 forget_board      clear the board ledger between prompts
 require           pin a requirement AND write it; survives a rebuilt dict
 requirements      dump what is pinned
 audit_requirements  final read-back vs what was PINNED (not vs the last dict)
 forget_requirements  clear the ledger between prompts
 verify_stuck      did the values actually take
 check_enablers    find INERT writes (value set, enabler still off) -- name-based
 verify_intent     did the result match the request you are holding NOW
 config_diff       what changed against a snapshot
 add_stub          drive a required pin so DRC can run
 cleanup           delete the cell and restore the part}
    if {$pat eq ""} { return $steps }
    set out {}
    foreach ln [split $idx \n] {
        if {[string match -nocase "*$pat*" $ln] && [string trim $ln] ne ""} {
            lappend out [string trimright $ln]
        }
    }
    if {[llength $out] == 0} { return "API:no proc matching $pat" }
    return [join $out \n]
}

# --- per-sub-key RANGE via set-and-observe (the gap dump_param_deps can't fill) ---
# PROVEN on 2026.1_released ps_wizard: dump_param_deps gives sub-key NAMES+VALUES but
# NO sub-key range. The IP customizer still KNOWS the range and reports it on a failed
# set (IPLEVEL_DRC_PROC), e.g.:
#   PARAM PS_USE_PMCPL_CLK0 with value <7> is out of range  { 0,1 }
# So we push an out-of-range sentinel for ONE sub-key and parse the message. The
# customizer auto-restores to the previous valid config on error, so this is
# NON-DESTRUCTIVE. Version-INDEPENDENT (works on 2025.2 too -- it is the customizer
# feedback path, not a VIVADO-23126 command), so it is the range fallback on BOTH
# versions. Operates on a BD CELL (the dict user-param, e.g. PS11_CONFIG -- NOT the
# *_INTERNAL, which is derived/read-only).
# CAVEAT (also proven): only USER-FACING sub-keys are range-checked. Derived keys
# (PMC_CRP_*_DIVISOR0/SRCSEL/ACT_FREQMHZ ...) accept anything and get recomputed by
# the resolver -> they have no user range (returns PSR_NOTVALIDATED).
#   cell      : BD cell name (e.g. ps_wiz)
#   dictparam : the dict USER-param (e.g. PS11_CONFIG)
#   subkey    : sub-key to probe (e.g. PS_USE_PMCPL_CLK0)
#   sentinel  : an out-of-range value (default 999999; use a clearly-illegal token)
#   IMPORTANT (proven on 2026.1): the legal-set text ("...is out of range { 0,1 }")
#   is emitted to the Vivado MESSAGE LOG, NOT into the Tcl catch result or $::errorInfo,
#   and the log is not flushed synchronously enough to read in-proc. So run this with
#   vivado_execute capture_log=true: the SAME response carries both this proc's status
#   line AND the range line, which you parse with ipcfg::range_from_log (or read the
#   "is out of range { ... }" line directly). One MCP round-trip, non-destructive.
proc ipcfg::probe_subkey_range {cell dictparam subkey {sentinel 999999}} {
    if {[catch {ipcfg::attempt_call $cell range_probe {
        ipcfg::_probe_subkey_range $cell $dictparam $subkey $sentinel
    }} result]} { return $result }
    return $result
}

proc ipcfg::_probe_subkey_range {cell dictparam subkey sentinel} {
    set c [get_bd_cells -quiet $cell]
    if {$c eq ""} { return "PSR_ERR:no cell $cell" }
    set prop CONFIG.$dictparam
    set ov ""; catch { set ov [get_property -quiet $prop $c] }
    if {[string is list $ov] && [llength $ov] % 2 == 0} {
        set try $ov; dict set try $subkey $sentinel
    } else {
        set try [list $subkey $sentinel]
    }
    set rc [catch { set_property $prop $try $c } e]
    if {$rc != 0} {
        # validated key: customizer rejected the sentinel and AUTO-RESTORED the cell
        # (non-destructive). The legal set is in the captured log -> range_from_log.
        return "PSR_OUT_OF_RANGE:$subkey (range is in the captured log: 'PARAM $subkey ... is out of range { ... }')"
    }
    # rc==0: sentinel was accepted. Distinguish a live-but-unchecked user input from a
    # key the customizer ignored/derived (not retained in the override dict).
    set kept 0
    catch {
        set now [get_property -quiet $prop $c]
        if {[string is list $now] && [llength $now] % 2 == 0 && [dict exists $now $subkey] && [dict get $now $subkey] eq $sentinel} { set kept 1 }
    }
    catch { set_property $prop $ov $c }   ;# restore prior override
    if {$kept} { return "PSR_NOTVALIDATED:$subkey (input accepted; no user range enforced)" }
    return "PSR_NOKEY:$subkey (ignored or derived -- not a user-settable input)"
}

# --- stateless parser: pull a sub-key's legal range out of a captured console ---
# Pair with probe_subkey_range run under capture_log=true. Returns
# RANGE:<subkey>={ ... } | RANGE_NONE:<subkey>.
proc ipcfg::range_from_log {console subkey} {
    foreach line [split $console "\n"] {
        if {[string first "is out of range" $line] < 0} continue
        if {[string first $subkey $line] < 0} continue
        if {[regexp {is out of range\s*\{([^\}]*)\}} $line -> rng]} {
            return "RANGE:$subkey={[string trim $rng]}"
        }
    }
    return "RANGE_NONE:$subkey"
}

# --- GATING/derivation via set-and-observe: apply legal inputs, read resolved dict ---
# PROVEN on 2026.1_released: requesting {PS_USE_PMCPL_CLK0 1 PMC_CRP_PL0_REF_CTRL_FREQMHZ 250}
# made the customizer derive ACT_FREQMHZ=249.997 DIVISOR0=4 SRCSEL=NPLL PS_PMCPL_CLK0_BUF=1.
# So the resolved *_INTERNAL dict IS the gating/derivation result. Use this to learn
# which sub-keys a given input gates/derives. Version-INDEPENDENT.
# NOTE: this APPLIES the override (it is a real config change, by design -- it is how
# you actually configure the IP). Pass only legal inputs. Snapshot CONFIG.<userparam>
# yourself first if you need to roll back.
#   cell          : BD cell name
#   userparam     : the dict USER-param you set (e.g. PS11_CONFIG)
#   override      : a Tcl dict of legal {subkey value ...} inputs to apply
#   internalparam : resolved/derived dict param (default <userparam>_INTERNAL)
#   wantkeys      : "" -> just report resolved key count; else list -> return those keys' values
proc ipcfg::resolve_subkeys {cell userparam override {internalparam ""} {wantkeys ""}} {
    set c [get_bd_cells -quiet $cell]
    if {$c eq ""} { return "RSK_ERR:no cell $cell" }
    if {$internalparam eq ""} { set internalparam ${userparam}_INTERNAL }
    if {[catch {ipcfg::attempt_call $cell resolve_subkeys {set_property CONFIG.$userparam $override $c}} e]} {
        return "RSK_FAIL:[string range $e 0 200]"
    }
    set iv ""; catch { set iv [get_property -quiet CONFIG.$internalparam $c] }
    if {![string is list $iv] || [llength $iv] % 2 != 0} { return "RSK_ERR:$internalparam not a dict" }
    if {$wantkeys eq ""} { return "RSK_KEYS:[llength [dict keys $iv]] resolved sub-keys" }
    set out {}
    foreach k $wantkeys {
        if {[dict exists $iv $k]} { lappend out $k [dict get $iv $k] } else { lappend out $k <absent> }
    }
    return "RSK:$out"
}

# ============================================================
# Deterministic front door: discovery -> apply -> verify in ONE call
#
# Everything above is a primitive the agent composes by hand, and that is where
# run-to-run variance came from. On an observed 2026.1 run the agent used NONE
# of the discovery helpers: it hand-rolled get_property + lsearch, searched for
# "CANFD" (zero hits, because the params are PS_CAN*), broadened by hand, and
# had already written a guessed nested shape before it knew the real key names.
# Same prompt, different path each time.
#
# The procs below move that sequence into code: the agent supplies INTENT, the
# library supplies the PROCEDURE. A step that isn't a model decision can't vary.
# ============================================================

# --- keyword synonym expansion (pure string work, no MCP round-trips) ---
# A prompt's feature word rarely equals the parameter spelling: "CAN-FD" has to
# reach PS_CAN1_PERIPHERAL. Matching runs against an already-fetched key list,
# so trying many spellings costs nothing. Ordered most- to least-specific, and
# callers stop at the first spelling that hits, which keeps precision.
# Progressive truncation of the compact form is what bridges the gap generically
# (CANFD -> CANF -> CAN); *minlen* floors it (2 for catalog family search).
proc ipcfg::_synonyms {keyword {minlen 3}} {
    set k [string toupper [string trim $keyword]]
    set alnum [regsub -all {[^A-Za-z0-9]} $k ""]
    set under [regsub -all {[^A-Za-z0-9]+} $k "_"]
    set out {}
    foreach c [list $k $under $alnum] {
        if {$c ne "" && $c ni $out} { lappend out $c }
    }
    for {set n [expr {[string length $alnum] - 1}]} {$n >= $minlen} {incr n -1} {
        set c [string range $alnum 0 [expr {$n - 1}]]
        if {$c ni $out} { lappend out $c }
    }
    return $out
}

# --- internal: read a property whose value is a Tcl dict ("" when it isn't) ---
proc ipcfg::_dict_val {obj prop} {
    set v ""
    if {[catch {set v [get_property -quiet $prop $obj]}]} { return "" }
    if {$v eq ""} { return "" }
    if {![string is list $v] || [llength $v] % 2 != 0} { return "" }
    return $v
}

# --- nested-dict introspection straight off a BD CELL (no create_ip, no dump) ---
# The documented native path (param_deps -> param_dict) needs a MANAGED IP
# instance, but subsystem IPs are often IPI-only: on 2026.1 `ps_wizard` reports
# "[Ipptcl 7-1663] ... intended for use in IPI only", so create_ip cannot be
# used and that path is simply unavailable. The live BD cell carries the whole
# dict anyway, so read it there -- one round-trip, no 172 KB file.
#   dictparam : dict-valued param, with or without the CONFIG. prefix. Falls
#               back to <param>_INTERNAL, where the RESOLVED sub-keys live when
#               the user-facing param is still empty.
#   pattern   : "" -> every sub-key; else a keyword, synonym-broadened.
# Returns:
#   CDK:<prop> pattern=<matched> n=<count> {k v k v ...}
#   CDK_NONE:<prop> (nothing matched any spelling)
#   CDK_ERR:<detail>
proc ipcfg::cell_dict_keys {cell dictparam {pattern ""}} {
    set c [get_bd_cells -quiet $cell]
    if {$c eq ""} { return "CDK_ERR:no cell $cell" }
    set base [regsub {^CONFIG\.} $dictparam ""]
    set d ""; set prop ""
    foreach cand [list CONFIG.$base CONFIG.${base}_INTERNAL] {
        set d [ipcfg::_dict_val $c $cand]
        if {$d ne ""} { set prop $cand; break }
    }
    if {$d eq ""} { return "CDK_ERR:$dictparam is not a populated dict on $cell" }
    if {$pattern eq ""} {
        return "CDK:$prop pattern=* n=[expr {[llength $d]/2}] $d"
    }
    foreach pat [ipcfg::_synonyms $pattern] {
        set hits {}
        foreach {k v} $d {
            if {[string match -nocase "*$pat*" $k]} { lappend hits $k $v }
        }
        if {[llength $hits]} {
            return "CDK:$prop pattern=$pat n=[expr {[llength $hits]/2}] $hits"
        }
    }
    return "CDK_NONE:$prop (no sub-key matches [ipcfg::_synonyms $pattern])"
}

# --- unified feature discovery on a BD cell: flat params AND nested sub-keys ---
# Answers the two questions that drive every configure in one call: what are the
# real parameter names for this feature, and what SHAPE do they take (flat
# CONFIG props vs sub-keys of a dict container). Both are read off the live cell,
# so it is version-agnostic by construction: the same call finds flat
# CONFIG.PS_CAN1_PERIPHERAL on a 2025.2 `ps11` and the nested
# CONFIG.PS11_CONFIG/PS_CAN1_PERIPHERAL on a 2026.1 `ps_wizard`.
# Flat params are preferred when both exist -- they are directly settable.
# Returns:
#   FP:FLAT pattern=<p> n=<k> {CONFIG.k ...}
#   FP:NESTED container=CONFIG.<X> pattern=<p> n=<k> {sub ...}
#   FP_NONE:<detail> | FP_ERR:<detail>
proc ipcfg::find_params {cell keyword} {
    set c [get_bd_cells -quiet $cell]
    if {$c eq ""} { return "FP_ERR:no cell $cell" }
    set props {}
    foreach p [list_property $c] {
        if {[string match "CONFIG.*" $p]} { lappend props $p }
    }
    foreach pat [ipcfg::_synonyms $keyword] {
        set hits {}
        foreach p $props {
            if {[string match -nocase "*$pat*" $p]} { lappend hits $p }
        }
        if {[llength $hits]} {
            return "FP:FLAT pattern=$pat n=[llength $hits] {$hits}"
        }
    }
    foreach p $props {
        if {[ipcfg::_dict_val $c $p] eq ""} { continue }
        set r [ipcfg::cell_dict_keys $cell $p $keyword]
        if {![string match "CDK:*" $r]} { continue }
        set pat ""
        regexp {pattern=(\S+)} $r -> pat
        set subs {}
        foreach {k v} [lrange $r 3 end] { lappend subs $k }
        # Report the USER-settable container, never the derived *_INTERNAL.
        set container [regsub {_INTERNAL$} $p ""]
        return "FP:NESTED container=$container pattern=$pat n=[llength $subs] {$subs}"
    }
    return "FP_NONE:nothing matches '[join [ipcfg::_synonyms $keyword] ,]' on $cell"
}

# --- catalog-grounded IP identity (IP names change between releases) ---
# `vlnv_ok` only validates a VLNV you already picked, so it cannot rescue a name
# that no longer exists. On 2026.1 `xilinx.com:ip:ps11:1.0` is absent from the
# catalog entirely (the PS family is ps11_vip / ps_wizard / psx_vip /
# psx_wizard), so a 2025.2-era name fails with nothing to correct it. Resolve
# against the live catalog instead of trusting documentation.
# Returns:
#   VLNV:<vendor:lib:name:ver>     exact hit (highest version)
#   VLNV_CANDIDATES:{<vlnv> ...}   no exact hit; what this release ships instead
#   VLNV_NONE:<hint>
proc ipcfg::resolve_vlnv {hint} {
    set h [string trim $hint]
    set name $h
    if {[string first : $h] >= 0} {
        set parts [split $h :]
        if {[llength $parts] >= 3} { set name [lindex $parts 2] }
    }
    set exact [get_ipdefs -quiet *:${name}:*]
    if {[llength $exact]} { return "VLNV:[lindex [lsort $exact] end]" }
    # Offer catalog neighbours so the choice is grounded in what this release
    # actually ships. minlen 2 so a stale "ps11" still surfaces the PS family.
    # Accumulate across ALL spellings rather than stopping at the first hit:
    # "ps11" matches ps11_vip on its most specific spelling, but the IP the
    # caller actually wants (ps_wizard) only appears under the broader "PS", and
    # an identity search must show the alternatives, not the first near-miss.
    set all [get_ipdefs -quiet]
    set cands {}
    foreach pat [ipcfg::_synonyms $name 2] {
        foreach d [lsort $all] {
            set n [lindex [split $d :] 2]
            if {[string match -nocase "*$pat*" $n] && $d ni $cands} { lappend cands $d }
        }
    }
    if {[llength $cands]} { return "VLNV_CANDIDATES:{$cands}" }
    return "VLNV_NONE:$hint"
}

# --- internal: map a caller's (possibly partial) key onto a discovered name ---
# Exact match first, then a UNIQUE containment match. Ambiguity is never
# guessed: "CAN1" matches both PS_CAN1_CLK and PS_CAN1_PERIPHERAL, and picking
# one would be exactly the coin-flip this whole path exists to remove. Returns
# the name, "" when nothing matched, or AMBIGUOUS:{candidates} so the caller can
# report which precise key to use.
proc ipcfg::_best_name {want names} {
    set w [string toupper [regsub {^CONFIG\.} [string trim $want] ""]]
    foreach n $names {
        if {[string toupper [regsub {^CONFIG\.} $n ""]] eq $w} { return $n }
    }
    set hits {}
    foreach n $names {
        set bare [string toupper [regsub {^CONFIG\.} $n ""]]
        if {[string first $w $bare] >= 0 || [string first $bare $w] >= 0} {
            lappend hits $n
        }
    }
    if {[llength $hits] == 1} { return [lindex $hits 0] }
    if {[llength $hits] > 1}  { return "AMBIGUOUS:{$hits}" }
    return ""
}

# --- THE front door: identity -> discovery -> apply -> verify, in one call ---
# The agent supplies INTENT; this supplies the PROCEDURE. Every step that used
# to be a per-run model decision is fixed here: which discovery command, in what
# order, whether to discover BEFORE writing (always), how to shape a nested
# dict, and what to verify afterwards.
#
#   cell    : BD cell name (created when absent)
#   vlnv    : IP VLNV or bare name; resolved against the catalog first
#   feature : the prompt's feature word (e.g. "CAN-FD"); synonym-broadened
#   intent  : {param-or-subkey value ...}. Keys may be partial or use the
#             prompt's spelling -- each is matched against the DISCOVERED names,
#             so the caller never needs this release's exact spelling.
#
# Returns ONE line:
#   CF:OK shape=<FLAT|NESTED> vlnv=<v> src=<discovery|cache> applied={k v ...}
#   CF:PARTIAL shape=... applied={...} unresolved={...} bad={...} inert={...}
#   CF:FAIL:<TYPE>:<detail>
# `inert` is non-empty when a value persisted but its enabling flag is still off
# (see check_enablers): set the flag and re-apply, or the feature is not there.
proc ipcfg::configure_feature {cell vlnv feature intent} {
    # -- Phase 1: identity from the catalog, not from documentation ----------
    set r [ipcfg::resolve_vlnv $vlnv]
    if {[string match "VLNV_CANDIDATES:*" $r]} {
        return "CF:FAIL:WRONG_IP_NAME:'$vlnv' is not in this release's catalog;\
this release ships [string range $r 16 end]"
    }
    if {[string match "VLNV_NONE:*" $r]} {
        return "CF:FAIL:WRONG_IP_NAME:'$vlnv' not in catalog, no near match"
    }
    set real [string range $r 5 end]
    set ipname [lindex [split $real :] 2]

    # -- Phase 1b: part gate, BEFORE the create ------------------------------
    # An unsupported part is not recoverable from the create error: catch sees
    # only "[Common 17-39] 'create_bd_cell' failed due to earlier errors" while
    # the [BD 5-683] naming the real cause goes to the log. Predict it from the
    # ipdef instead and report the parts that WOULD work, so the caller can make
    # the swap decision with the facts.
    # Explicit part => a confirmation of the identity the CALLER chose, so the
    # shortlist advisory is suppressed: selecting between candidates is the
    # caller's Step 0a' gate, which runs before this and cannot be done here.
    set av [ipcfg::ip_availability [list $ipname] [get_property PART [current_project]]]
    if {[string match "*=WRONG_PART:*" $av]} {
        return "CF:FAIL:WRONG_PART:[string range $av 9 end] -- consider a guarded\
ipcfg::ensure_part; do NOT substitute a different IP that happens to be available"
    }

    # -- Phase 2: the cell must exist before it can be introspected ----------
    set c [ipcfg::create_cell $real $cell]
    if {[string match "CONFIGURE_FAIL:*" $c]} {
        return "CF:FAIL:[string range $c 15 end]"
    }

    # -- Phase 3: DISCOVER before writing anything --------------------------
    # Consult the learned cache first (0 extra work on a hit), but only trust it
    # after a cheap existence re-verify against this cell.
    set shape ""; set container ""; set names {}; set src "discovery"
    set hit [ipcfg::cache_get $ipname $feature]
    if {$hit ne ""} {
        set cs ""; set cp ""
        regexp {"shape"\s*:\s*"([^"]*)"} $hit -> cs
        regexp {"param"\s*:\s*"([^"]*)"} $hit -> cp
        if {$cs ne "" && $cp ne ""} {
            if {$cs eq "FLAT"} {
                set probe $cp
                if {![string match "CONFIG.*" $probe]} { set probe CONFIG.$probe }
                if {![catch {get_property $probe [get_bd_cells $cell]}]} {
                    set shape FLAT; set names [list $probe]; set src "cache"
                }
            } else {
                set cdk [ipcfg::cell_dict_keys $cell $cp $feature]
                if {[string match "CDK:*" $cdk]} {
                    set shape NESTED
                    set container [regsub {_INTERNAL$} [lindex $cdk 0] ""]
                    set container [regsub {^CDK:} $container ""]
                    foreach {k v} [lrange $cdk 3 end] { lappend names $k }
                    set src "cache"
                }
            }
        }
    }
    if {$shape eq ""} {
        set fp [ipcfg::find_params $cell $feature]
        if {[string match "FP_*" $fp]} { return "CF:FAIL:PARAM_NOT_FOUND:$fp" }
        set shape [lindex [split [lindex $fp 0] :] 1]
        if {$shape eq "FLAT"} {
            set names [lindex $fp 3]
        } else {
            set container [string range [lindex $fp 1] 10 end]
            set names [lindex $fp 4]
        }
    }

    # -- Phase 4: map intent onto the DISCOVERED names ----------------------
    set resolved {}
    set unresolved {}
    foreach {want val} $intent {
        set n [ipcfg::_best_name $want $names]
        if {[string match "AMBIGUOUS:*" $n]} {
            lappend unresolved "${want}(ambiguous:[string range $n 10 end])"
        } elseif {$n eq ""} {
            lappend unresolved "${want}(no-match)"
        } else {
            lappend resolved $n $val
        }
    }
    if {[llength $resolved] == 0} {
        return "CF:FAIL:PARAM_NOT_FOUND:unresolved={$unresolved}\
discovered={$names}"
    }

    # -- Phase 5: build the correctly-shaped dict and apply -----------------
    if {$shape eq "FLAT"} {
        set d {}
        foreach {n v} $resolved {
            if {![string match "CONFIG.*" $n]} { set n CONFIG.$n }
            lappend d $n $v
        }
    } else {
        set sub {}
        foreach {n v} $resolved { lappend sub [regsub {^CONFIG\.} $n ""] $v }
        set d [list $container $sub]
    }
    set ap [ipcfg::apply_dict $cell $d]
    if {[string match "CONFIGURE_FAIL:*" $ap]} {
        return "CF:FAIL:[string range $ap 15 end]"
    }

    # -- Phase 6: verify it actually stuck, and that it is not INERT ---------
    # A gated attribute persists and reads back while doing nothing at all, so
    # verify_stuck alone cannot certify the feature was delivered.
    set bad [ipcfg::verify_stuck $cell $d]
    set inert [ipcfg::check_enablers $cell $d]

    # -- Phase 7: write back what was learned (blind-safe: facts, not values) -
    set ipver [lindex [split $real :] 3]
    set learned [expr {$shape eq "FLAT" ? [lindex $resolved 0] : $container}]
    catch {
        ipcfg::cache_put $ipname $feature $learned $shape "" "USER" \
            "cell-introspection" $ipver
    }

    if {[llength $unresolved] || [llength $bad] || $inert ne ""} {
        return "CF:PARTIAL shape=$shape vlnv=$real src=$src applied={$d}\
unresolved={$unresolved} bad={$bad} inert={$inert}"
    }
    return "CF:OK shape=$shape vlnv=$real src=$src applied={$d} verified=all"
}

# --- Learned-config cache (idea #3): consult-first / write-back ---
# Stores earned DISCOVERY facts only (param/shape/enabler/value_src/doc/version),
# never expected values, so it is blind-safe. Shells out to ipcfg_cache.py.
#   cache_get <ip> <feature>            -> JSON entry string, or "" on miss
#   cache_put <ip> <feature> <param> <shape> <enabler> <value_src> <doc> <ipver>
# Flow: consult cache first (0 MCP calls) -> if hit, a cheap get_property
# existence re-verify -> apply; on miss, fall through to doc search; on success,
# write back so the next run is cheaper and deterministic.
proc ipcfg::cache_get {ip feature} {
    variable cache_engine
    variable cache_file
    if {[catch {
        ipcfg::python_exec $cache_engine get $cache_file $ip $feature
    } out]} { return "" }
    return [string trim $out]
}
proc ipcfg::cache_put {ip feature param shape enabler value_src doc ipver} {
    variable cache_engine
    variable cache_file
    if {[catch {
        ipcfg::python_exec $cache_engine put $cache_file $ip $feature $param \
            $shape $enabler $value_src $doc $ipver
    } out]} {
        return "CACHE_ERR:$out"
    }
    return [string trim $out]
}
proc ipcfg::cache_dump {} {
    variable cache_engine
    variable cache_file
    if {[catch {
        ipcfg::python_exec $cache_engine dump $cache_file
    } out]} { return "{}" }
    return $out
}

source [file join [file dirname [file normalize [info script]]] clock_search.tcl]
source [file join [file dirname [file normalize [info script]]] preset_drift.tcl]
source [file join [file dirname [file normalize [info script]]] rule_options.tcl]

set ::ipcfg::loaded_signature $::ipcfg::source_candidate
unset ::ipcfg::source_candidate
puts "LIBRARY:OK ipcfg"
