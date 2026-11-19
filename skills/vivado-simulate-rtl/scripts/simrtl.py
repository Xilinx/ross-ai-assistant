#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One-shot Vivado/XSim simulation driver for small-context agents.

Each ``sim`` invocation builds one batch Tcl script, runs it in a single
``vivado -mode batch`` process, copies every run's transcript, waveform,
coverage and activity artifacts to ``--out`` under run-specific names, parses
the transcripts, and prints a compact JSON summary (also written to
``<out>/<name>.summary.json``). The agent reads only that summary.

Examples:
  simrtl.py sim --out outputs/c1 --fixture-tcl create_xsim_fixture_project.tcl --fixture full
  simrtl.py sim --out outputs/c1 --xpr vivado_project/p.xpr --simset sim_bug1 --wcfg sim_bug1_fail
  simrtl.py sim --out outputs/c1 --fixture-tcl ... --fixture random --seeds 123456,11 --replay-failures
  simrtl.py sim --out outputs/c1 --fixture-tcl ... --fixture partial --cov --tb new_tb.sv
  simrtl.py run --backend questa --xpr p.xpr --out outputs/c1 --mode post-implementation --type timing
  simrtl.py logs FILE_OR_DIR...           # summarize existing logs (no launch)
  simrtl.py findings --out outputs/c1                   # report sections quoting each FAIL log
  simrtl.py xsim-files reduced.sv --out outputs/c1      # project-less XSim control
  simrtl.py simlib-cells --family artixuplus --cells SIP_PCIE4CE4 --out outputs/c1

Run ``simrtl.py sim -h`` for every option.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ----------------------------------------------------------------- log parsing

ERR_LINE = re.compile(r"^(?:\*\*\s*)?(Fatal|Error|FATAL_ERROR|ERROR)\b:?\s*(.*)$")
TOOL_ERR = re.compile(r"^(ERROR|CRITICAL WARNING): \[([^\]]+)\]\s*(.*)$")
TIME_LINE = re.compile(r"^Time:\s*([\d.]+\s*\w+)")
FINISH = re.compile(r"\$finish called at time\s*:\s*([\d.]+\s*\w+)")
PASS_MARK = re.compile(r"\b(TEST_PASS\w*|\w*REGRESSION_PASS\b[^\n]*)|\*\*\* ?PASS ?\*\*\*")
CHECK_FAIL = re.compile(r"(CHECK_FAIL\b.*)")
UVM_SUM = re.compile(r"^(UVM_(?:INFO|WARNING|ERROR|FATAL))\s*:\s*(\d+)\s*$", re.M)
UVM_TEST = re.compile(r"Running test (\S+)")
UVM_VER = re.compile(r"\bUVM-(\d\.\d\w*)")
SIGNATURE = re.compile(r"\[(XSIM|VRFC|USF-XSim|Common|filemgmt|Vivado|Simtcl|SIM-utils)[ -][^\]]*\]")


def read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def parse_transcript(text: str) -> dict:
    """Facts from one simulate transcript: markers, first failure + time."""
    lines = text.splitlines()
    first = None
    passes, checks_failed = [], []
    for i, line in enumerate(lines):
        s = line.strip()
        m = PASS_MARK.search(s)
        if m and not s.startswith(("#", "//")):
            passes.append(s[:120])  # whole line: markers often name what passed
        cf = CHECK_FAIL.search(s)
        if cf:
            checks_failed.append(cf.group(1)[:240])
        em = ERR_LINE.match(s)
        if first is None and (em or cf or "UVM_ERROR @" in s or "UVM_FATAL @" in s):
            t = None
            for nxt in lines[i + 1:i + 3]:
                tm = TIME_LINE.match(nxt.strip())
                if tm:
                    t = tm.group(1)
                    break
            if t is None:
                tm = re.search(r"(?:@|at time|Time:?)\s*:?\s*([\d.]+\s*[munpf]?s)\b", s)
                t = tm.group(1) if tm else None
            first = {"message": s[:300], "time": t, "line": i + 1}
    fin = FINISH.search(text)
    uvm = {k: int(v) for k, v in UVM_SUM.findall(text)}
    out = {
        "pass_markers": passes[:8],
        "pass_count": len(passes),
        "check_fail_count": len(checks_failed),
        "first_failure": first,
        "finish_time": fin.group(1) if fin else None,
    }
    if uvm or UVM_TEST.search(text):
        out["uvm"] = {
            "version": (UVM_VER.search(text) or [None, None])[1],
            "test": (UVM_TEST.search(text) or [None, None])[1],
            "errors": uvm.get("UVM_ERROR"), "fatals": uvm.get("UVM_FATAL"),
            "warnings": uvm.get("UVM_WARNING"),
        }
    asum = assertion_summary(text)
    if asum:
        out["assertions"] = asum
    return out


SRC_LOC = re.compile(r"File:\s*(\S+)\s+Line:\s*(\d+)")


def assertion_summary(text: str) -> list[dict]:
    """Per-assertion pass/fail counts; label and kind read from the source line."""
    rows: dict[tuple, dict] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        status = None
        if s.startswith("INFO: Assertion passed"):
            status, loc_line = "pass", (lines[i + 1] if i + 1 < len(lines) else "")
        elif s.startswith("Time:") and "Started:" in s and i + 1 < len(lines) and ERR_LINE.match(lines[i + 1].strip()):
            status, loc_line = "fail", s
        if not status:
            continue
        loc = SRC_LOC.search(loc_line)
        tm = TIME_LINE.match(loc_line.strip())
        if not loc:
            continue
        key = (loc.group(1), int(loc.group(2)))
        r = rows.setdefault(key, {"source": f"{Path(key[0]).name}:{key[1]}", "pass": 0, "fail": 0})
        r[status] += 1
        if status == "fail":
            r.setdefault("first_fail_time", tm.group(1) if tm else None)
            r.setdefault("message", lines[i + 1].strip()[:120])
    for (f, n), r in rows.items():
        src = read(Path(f)).splitlines()
        if 0 < n <= len(src):
            m = re.match(r"\s*(\w+)\s*:\s*(assert|assume|cover)\s*(property)?", src[n - 1])
            if m:
                r["label"] = m.group(1)
                r["kind"] = "concurrent" if m.group(3) else "immediate"
            elif "property" in src[n - 1]:
                r["kind"] = "concurrent"
    return list(rows.values())


def parse_tool_log(text: str) -> dict | None:
    """First ERROR in a compile/elaborate/Vivado log, with its tag."""
    for i, line in enumerate(text.splitlines()):
        m = TOOL_ERR.match(line.strip())
        if m and m.group(1) == "ERROR":
            return {"tag": m.group(2), "message": line.strip()[:300], "line": i + 1}
    return None


def verdict_of(stages: dict, tr: dict | None, want_marker: str | None) -> tuple[str, str, str]:
    """(functional_verdict, stage, suggested failure_class)."""
    for st in ("compile", "elaborate"):
        if stages.get(st):
            sig = stages[st]["tag"]
            klass = "tool_defect?" if re.search(r"43-3316|43-3294|SIGSEGV|EXCEPTION", stages[st]["message"]) else "design_or_testbench_source?"
            return "FAIL", st, klass
    if tr is None:
        return "INCONCLUSIVE", "simulate", "environment?"
    uvm = tr.get("uvm") or {}
    if tr["first_failure"] or tr["check_fail_count"] or (uvm.get("errors") or 0) or (uvm.get("fatals") or 0):
        return "FAIL", "simulate", "design?"
    has_pass = tr["pass_count"] > 0 and (not want_marker or any(want_marker in p for p in tr["pass_markers"]))
    if has_pass and tr.get("finish_time"):
        return "PASS", "simulate", "none"
    if has_pass:
        return "PASS", "simulate", "none"
    return "INCONCLUSIVE", "simulate", "timeout_or_no_marker?"


# ----------------------------------------------------------------- vcd tables

def vcd_table(path: Path, end_time_ps: int | None, clock: str | None, rows: int = 8) -> str:
    """Values of every traced signal at the last `rows` rising clock edges up to end_time."""
    text = read(path)
    ids, names, scale = {}, [], 1
    ts = re.search(r"\$timescale\s+(\d+)\s*(\w+)", text)
    if ts:
        scale = int(ts.group(1)) * {"s": 10**12, "ms": 10**9, "us": 10**6, "ns": 1000, "ps": 1, "fs": 0}.get(ts.group(2), 1)
    for m in re.finditer(r"\$var\s+\S+\s+(\d+)\s+(\S+)\s+(\S+)", text):
        ids[m.group(2)] = m.group(3)
        if m.group(3) not in names:
            names.append(m.group(3))
    body = text.split("$enddefinitions", 1)[-1]
    cur, t = {}, 0
    clk = clock or next((n for n in names if re.search(r"clk", n, re.I)), None)
    samples = []
    prev_clk = None
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("$"):
            continue
        if line[0] == "#":
            nt = int(line[1:]) * scale
            if end_time_ps is not None and nt > end_time_ps:
                break
            t = nt
            continue
        if line[0] in "bBrR":
            val, ident = line[1:].split()
        else:
            val, ident = line[0], line[1:]
        name = ids.get(ident)
        if not name:
            continue
        if name == clk:
            if prev_clk == "0" and val == "1":
                samples.append((t, dict(cur)))
            prev_clk = val
        cur[name] = _vcd_val(val)
    samples = samples[-rows:]
    if not samples:
        return ""
    if end_time_ps is not None:
        samples.append((t, dict(cur)))  # values in effect when the failure was reported
    cols = [n for n in names if n != clk][:14]
    hdr = "time_ns | " + " | ".join(cols)
    out = [hdr]
    for i, (t, vals) in enumerate(samples):
        tag = " (at failure)" if end_time_ps is not None and i == len(samples) - 1 else ""
        out.append(f"{t / 1000:g}{tag} | " + " | ".join(str(vals.get(c, "?")) for c in cols))
    return "\n".join(out)


def _vcd_val(v: str):
    if re.fullmatch(r"[01]+", v):
        return int(v, 2) if len(v) > 1 else int(v)
    return v


def to_ps(t: str | None) -> int | None:
    if not t:
        return None
    m = re.match(r"([\d.]+)\s*([a-z]*)", t.strip())
    if not m:
        return None
    mul = {"s": 10**12, "ms": 10**9, "us": 10**6, "ns": 1000, "ps": 1, "fs": 0.001}.get(m.group(2) or "ns", 1)
    return int(float(m.group(1)) * mul)


def with_unit(t: str) -> str:
    """A bare number is ns, as in the Vivado runtime property."""
    t = t.strip()
    return t if re.search(r"[a-z]$", t) else f"{t}ns"


# ----------------------------------------------------------------- Tcl builder

TCL_LIB = r'''
namespace eval ::sr {}
proc ::sr::simdir {simset} {
  set p [current_project]
  set base [file join [get_property DIRECTORY $p] "[get_property NAME $p].sim" $simset]
  set best ""
  set bt 0
  foreach f [glob -nocomplain -directory $base -types f */*/simulate.log */*/xvlog.log */*/compile.log */*/elaborate.log] {
    if {[file mtime $f] >= $bt} { set bt [file mtime $f]; set best [file dirname $f] }
  }
  return $best
}
proc ::sr::opt_add {simset prop add} {
  set fs [get_filesets $simset]
  set old [get_property $prop $fs]
  foreach a $add {
    regsub -all -- "(^|\\s)[lindex $a 0](\\s+\\S+)?" $old {} old
  }
  set_property -name $prop -value [string trim "$old [join $add { }]"] -objects $fs
}
proc ::sr::copy_run {simset name out} {
  set d [::sr::simdir $simset]
  puts "SR_SIMDIR $simset $d"
  if {$d eq ""} { return }
  foreach f {compile.log elaborate.log simulate.log xvlog.log xvhdl.log xelab.log} {
    if {[file exists [file join $d $f]]} { file copy -force [file join $d $f] [file join $out "$name.$f"] }
  }
  foreach w [glob -nocomplain -directory $d *.wdb] { file copy -force $w [file join $out "$name.wdb"] }
  foreach s {compile.sh elaborate.sh simulate.sh} {
    if {[file exists [file join $d $s]]} { file copy -force [file join $d $s] [file join $out "$name.$s"] }
  }
}
proc ::sr::ps {t} {
  if {![regexp {([0-9.]+)\s*([a-z]*)} $t -> v u]} { return 0 }
  array set m {s 1e12 ms 1e9 us 1e6 ns 1e3 ps 1 fs 1e-3 "" 1}
  return [expr {round($v * $m($u))}]
}
# run DUR; 1 if simulation advanced the full DUR, 0 if $finish/stop ended it early
proc ::sr::finished {} {
  set d [::sr::simdir $::sr::ss]
  if {$d eq ""} { return 0 }
  set fh [open [file join $d simulate.log] r]; set txt [read $fh]; close $fh
  return [string match {*$finish called*} $txt]
}
proc ::sr::run_for {dur} {
  if {[::sr::finished]} { return 0 }
  set a [::sr::ps [current_time]]
  if {[catch {run $dur} e]} { puts "SR_RUN_STOPPED $e"; return 0 }
  set b [::sr::ps [current_time]]
  return [expr {$b - $a >= [::sr::ps $dur] && ![::sr::finished]}]
}
proc ::sr::restore {ss} {
  foreach k [array names ::sr::orig] { catch {set_property -name $k -value $::sr::orig($k) -objects $ss} }
  catch {current_fileset -simset $::sr::prev_active}
}
proc ::sr::scope {top p} {
  if {[string index $p 0] ne "/"} { set p "/$p" }
  if {![catch {get_scopes $p} s] && [llength $s]} { return $p }
  set alt /$top/[join [lrange [split [string trimleft $p /] /] 1 end] /]
  if {![catch {get_scopes $alt} s] && [llength $s]} { puts "SR_SCOPE_FIXED $p -> $alt"; return $alt }
  return $p
}
proc ::sr::objs {top pats} {
  set r {}
  foreach p $pats {
    if {[string index $p 0] ne "/"} { set p "/$top/$p" }
    if {[catch {set o [get_objects $p]}] == 0 && [llength $o]} { set r [concat $r $o]; continue }
    puts "SR_NOOBJ $p"
    if {![info exists ::sr::listed] && ![catch {set all [get_objects /$top/*]}]} {
      set ::sr::listed 1
      puts "SR_TOPOBJS [lsort $all]"
    }
  }
  return $r
}
'''


TOUCHED_PROPS = (
    "xsim.simulate.runtime", "xsim.simulate.log_all_signals", "xsim.elaborate.debug_level",
    "xsim.simulate.xsim.more_options", "xsim.elaborate.xelab.more_options",
    "xsim.compile.xvlog.more_options", "xsim.elaborate.coverage.name",
    "xsim.elaborate.coverage.dir", "xsim.elaborate.coverage.type",
)


def tq(s: str) -> str:
    """Brace-quote a path/value for Tcl."""
    return "{" + str(s) + "}"


def build_tcl(a, out: Path, runs: list[dict]) -> str:
    t = [TCL_LIB, f"set out {tq(out)}", "file mkdir $out"]
    if a.xpr:
        t.append(f"open_project {tq(Path(a.xpr).resolve())}")
    else:
        proj_dir = out / (a.project_dir or a.fixture)
        t += [f"set ::EVAL_FIXTURE {a.fixture}", f"set ::EVAL_OUTPUT_DIR {tq(proj_dir)}",
              f"source {tq(Path(a.fixture_tcl).resolve())}"]
    t.append("puts \"SR_VERSION [version -short]\"")
    t.append("puts \"SR_XPR [get_property DIRECTORY [current_project]]/[get_property NAME [current_project]].xpr\"")
    t.append('set ::sr::tsim [get_property target_simulator [current_project]]\n'
             'if {$::sr::tsim ne "XSim"} { puts "SR_NOT_XSIM $::sr::tsim"; catch {close_project}; exit 3 }')
    # extra testbenches, each in its own simulation set
    for tb in a.tb or []:
        tbp = Path(tb).resolve()
        top = a.tb_top or tbp.stem
        ss = f"sim_{top}"[:40]
        t += [f"if {{[llength [get_filesets -quiet {ss}]] == 0}} {{ create_fileset -simset {ss} }}",
              f"add_files -fileset {ss} -norecurse {tq(tbp)}",
              f"set_property top {top} [get_filesets {ss}]",
              f"set_property top_lib xil_defaultlib [get_filesets {ss}]",
              f"update_compile_order -fileset {ss}"]
    for r in runs:
        t += run_tcl(a, r)
    t.append("catch {close_project}")
    t.append("puts SR_DONE")
    return "\n".join(t) + "\n"


def run_tcl(a, r: dict) -> list[str]:
    ss, name = r["simset"], r["name"]
    t = [f"puts \"SR_RUN_BEGIN {name}\"", f"set ss [get_filesets {ss}]", f"set ::sr::ss {ss}",
         f"set top [get_property TOP $ss]", "catch {close_sim -force}",
         f"update_compile_order -fileset {ss}"]
    # Vivado reads some launch settings (e.g. the runtime) from the ACTIVE
    # simulation set, not from -simset, so make the target active. Snapshot
    # every property this run touches; ::sr::restore puts the project back.
    t.append("set ::sr::prev_active [current_fileset -simset]")
    t.append("current_fileset -simset $ss")
    t.append("array unset ::sr::orig")
    for prop in TOUCHED_PROPS:
        t.append(f"catch {{set ::sr::orig({prop}) [get_property {prop} $ss]}}")
    sim_opts, elab_opts, comp_opts = [], [], []
    if r.get("seed") is not None:
        sim_opts.append(f"-sv_seed {r['seed']}")
    for pa in a.plusarg or []:
        sim_opts.append(f"-testplusarg {pa}")
    if a.uvm_test:
        sim_opts.append(f"-testplusarg UVM_TESTNAME={a.uvm_test}")
        comp_opts.append("-L uvm")
        elab_opts.append("-L uvm")
    if a.assert_pass:
        elab_opts.append("-report_assertion_pass")
    stepped = bool(a.saif or a.vcd or a.ports or a.cov)
    if stepped or a.onfinish_stop:
        sim_opts.append("-onfinish stop")
    for prop, opts in (("xsim.simulate.xsim.more_options", sim_opts),
                       ("xsim.elaborate.xelab.more_options", elab_opts),
                       ("xsim.compile.xvlog.more_options", comp_opts)):
        if opts:
            t.append(f"::sr::opt_add {ss} {prop} {{{' '.join('{' + o + '}' for o in opts)}}}")
    # create_testbench may inject an option this xelab rejects (documented in
    # references/xsim-activity-capture.md); swap it only when xelab -help
    # offers the replacement, and report it as generated-flow remediation.
    t.append("""set eo [get_property xsim.elaborate.xelab.more_options $ss]
if {[regexp -- {-+ignore_localparam_override} $eo] && ![catch {exec xelab -help} h] && [string match *suppress_localparam_override_error* $h]} {
  regsub -all -- {-+ignore_localparam_override} $eo {-suppress_localparam_override_error} eo
  set_property -name xsim.elaborate.xelab.more_options -value $eo -objects $ss
  puts "SR_WORKAROUND replaced obsolete -ignore_localparam_override with -suppress_localparam_override_error (xelab -help confirms it)"
}""")
    if a.debug_typical:
        t.append("catch {set_property xsim.elaborate.debug_level typical $ss}")
    if r.get("cov"):
        cdir = f"$out/cov-{name}"
        t += [f"file mkdir {cdir}",
              f"set_property xsim.elaborate.coverage.name {name} $ss",
              f"set_property xsim.elaborate.coverage.dir {cdir} $ss",
              f"set_property xsim.elaborate.coverage.type {a.cov_type} $ss"]
    t.append("set_property xsim.simulate.runtime {} $ss")
    t.append("catch {set_property xsim.simulate.log_all_signals false $ss}")
    mode = ["-mode", a.mode] + (["-type", a.type] if a.type else [])
    t.append(f"if {{[catch {{launch_simulation -simset {ss} {' '.join(mode)}}} err]}} {{ puts \"SR_LAUNCH_ERROR $err\"; ::sr::copy_run {ss} {name} $out; ::sr::restore $ss; puts \"SR_RUN_END {name}\" }} else {{")
    # focused waveform: log before advancing
    wave = a.wave.split() if a.wave else ["*"]
    t.append(f"set wobjs [::sr::objs $top {{{' '.join(wave)}}}]")
    t.append("if {[llength $wobjs]} { catch {log_wave $wobjs} }")
    if a.trace:
        t.append(f"catch {{open_vcd $out/{name}.trace}}")
        t.append("catch {log_vcd $wobjs}")
    runtime = a.runtime
    if (a.saif or a.vcd) and not a.window:
        if a.saif:
            t += [f"open_saif $out/{name}.saif",
                  f"log_saif [get_objects [::sr::scope $top {{{a.saif.rstrip('/')}}}]/*]"]
        if a.vcd:
            t += [f"open_vcd $out/{name}.vcd", f"log_vcd [::sr::objs $top {{{a.vcd}}}]"]
        t.append(f"::sr::run_for {runtime}")
        t += (["close_saif"] if a.saif else []) + (["close_vcd"] if a.vcd else [])
    elif a.saif or a.vcd:
        start, end = (with_unit(x) for x in a.window.split(":"))
        t.append(f"set full [::sr::run_for {start}]")
        if a.saif:
            t += [f"open_saif $out/{name}.saif",
                  f"log_saif [get_objects [::sr::scope $top {{{a.saif.rstrip('/')}}}]/*]"]
        if a.vcd:
            t += [f"open_vcd $out/{name}.vcd",
                  f"log_vcd [::sr::objs $top {{{a.vcd}}}]"]
        t.append(f"if {{$full}} {{ set full [::sr::run_for {to_ps(end) - to_ps(start)}ps] }}")
        if a.saif:
            t.append("close_saif")
        if a.vcd:
            t.append("close_vcd")
        t.append(f"if {{$full}} {{ ::sr::run_for {runtime} }}")
    elif a.ports:
        t += [f"generate_vcd_ports [::sr::scope $top {{{a.ports}}}]", f"::sr::run_for {runtime}",
              "catch {close_vcd -ports}"]
    else:
        t.append(f"::sr::run_for {runtime}")
    t.append("puts \"SR_NOW [current_time]\"")
    if a.trace:
        t.append("catch {close_vcd}")
    if a.wcfg and r.get("primary"):
        wname = a.wcfg
        t += [f"catch {{create_wave_config {wname}}}",
              "catch {add_wave $wobjs}",
              f"catch {{save_wave_config $out/{wname}.wcfg}}"]
    if r.get("cov"):
        cdir = f"$out/cov-{name}"
        t += [f"catch {{write_xsim_coverage -cov_db_name {name} -cov_db_dir {cdir}}} e; puts \"SR_COV_WRITE $e\"",
              ]
    if a.ports:
        t += [f"set vcdp [glob -nocomplain -directory [::sr::simdir {ss}] *.vcd]",
              "foreach v $vcdp { file copy -force $v $out/[file tail $v] }"]
    t.append("catch {close_sim -force}")
    t.append(f"::sr::copy_run {ss} {name} $out")
    t.append("::sr::restore $ss")
    if a.ports:
        rs = "sim_replay"
        t += [f"if {{[catch {{create_testbench -name tb_replay -add_to_simset {rs} -set_as_top}} e]}} {{ puts \"SR_CREATE_TB_ERROR $e\" }}",
              f"catch {{update_compile_order -fileset [get_filesets {rs}]}}",
              f"set gen [get_files -quiet -of_objects [get_filesets {rs}] -filter {{FILE_TYPE =~ *Verilog*}}]",
              "foreach g $gen { puts \"SR_GENERATED $g\" }"]
    t.append(f"puts \"SR_RUN_END {name}\"")
    t.append("}")
    return t


# ----------------------------------------------------------------- runner

def find_vivado() -> str | None:
    v = shutil.which("vivado")
    if v:
        return v
    xv = os.environ.get("XILINX_VIVADO")
    if xv and Path(xv, "bin", "vivado").exists():
        return str(Path(xv, "bin", "vivado"))
    return None


def run_vivado(tcl: str, out: Path, tag: str, timeout: int) -> tuple[int, str, float]:
    viv = find_vivado()
    if not viv:
        return 127, "vivado not found on PATH and XILINX_VIVADO unset", 0.0
    script = out / f"{tag}.tcl"
    script.write_text(tcl)
    console = out / f"{tag}.vivado.txt"
    t0 = time.time()
    try:
        p = subprocess.run([viv, "-mode", "batch", "-nojournal", "-notrace", "-source", str(script),
                            "-log", str(console)], cwd=out, stdout=subprocess.DEVNULL,
                           stderr=subprocess.STDOUT, timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = 124
    return rc, read(console), time.time() - t0


def summarize_run(out: Path, name: str, console: str, want_marker: str | None) -> dict:
    tr_path = out / f"{name}.simulate.log"
    stages = {}
    for st, files in (("compile", ("compile.log", "xvlog.log", "xvhdl.log")), ("elaborate", ("elaborate.log", "xelab.log"))):
        for f in files:
            e = parse_tool_log(read(out / f"{name}.{f}"))
            if e:
                stages[st] = e
                break
    seg = console.split(f"SR_RUN_BEGIN {name}", 1)[-1].split(f"SR_RUN_END {name}", 1)[0]
    le = re.search(r"SR_LAUNCH_ERROR (.*)", seg)
    if le and not stages:
        e = parse_tool_log(seg)
        stages["elaborate" if "elab" in seg.lower() else "compile"] = e or {"tag": "launch", "message": le.group(1)[:300], "line": 0}
    tr = parse_transcript(read(tr_path)) if tr_path.exists() else None
    verdict, stage, klass = verdict_of(stages, tr, want_marker)
    arts = sorted(_rel(p) for p in out.glob(f"{name}*")
                  if p.is_file() and p.suffix in (".log", ".wdb", ".saif", ".vcd", ".wcfg", ".trace", ".json", ".md"))
    res = {"run": name, "verdict": verdict, "stage": stage, "suggested_class": klass}
    if stages:
        res["first_tool_error"] = next(iter(stages.values()))
    if tr:
        res.update({k: v for k, v in tr.items() if v not in (None, [], 0) or k == "pass_count"})
    now = re.search(r"SR_NOW (.*)", seg)
    if now:
        res["sim_time_end"] = now.group(1).strip()
    wk = re.search(r"SR_WORKAROUND (.+)", seg)
    if wk:
        res["workaround"] = wk.group(1).strip()
    for k in ("SR_COV_EXPORT", "SR_CREATE_TB_ERROR"):
        m = re.search(k + r" (.+)", seg)
        if m and m.group(1).strip() not in ("", "0"):
            res[k.lower()] = m.group(1).strip()[:200]
    res["artifacts"] = arts
    return res


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(p)


class _Cells(HTMLParser):
    """Table rows of an xcrg HTML page as lists of cell texts."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell, self.skip = [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        elif tag in ("td", "th") and self.row is not None and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None and not self.skip:
            self.cell.append(data)


def html_rows(p: Path) -> list[list[str]]:
    c = _Cells()
    c.feed(read(p))
    return c.rows


def functional_bins(rep: Path) -> list[tuple[str, int, str]]:
    """(bin, hits, coverpoint) for user-named bins in the functional HTML report."""
    out = []
    for grp in sorted((rep / "functionalCoverageReport").glob("grp*.html")):
        text = read(grp)
        # each bin table follows a "... Cover Point - <inst> :: <cp>" / "Cross Point" heading
        for sect in re.split(r"(?=Details of Instance Cro?s?s? ?Point|Details of Instance Cover Point)", text)[1:]:
            cp = re.search(r"::\s*([\w.]+)", sect)
            cpn = cp.group(1) if cp else "?"
            for row in _rows_of(sect):
                if len(row) == 3 and re.fullmatch(r"\d+", row[1]) and re.fullmatch(r"\d+", row[2]):
                    out.append((row[0], int(row[1]), cpn))
    return out


def _rows_of(fragment: str) -> list[list[str]]:
    c = _Cells()
    c.feed(fragment)
    return c.rows


def xcrg(out: Path, name: str, db_dir: Path, db_name: str | None, merge: bool = False) -> dict | None:
    """Code + functional coverage report for one database (or a merge of every
    database under db_dir) with xcrg; scores from its transcript, functional
    bins rendered to text because this release writes them only as HTML."""
    if not shutil.which("xcrg"):
        return {"error": "xcrg not on PATH"}
    rep = out / f"cov-{name}-report"
    rep.mkdir(parents=True, exist_ok=True)
    cmd = ["xcrg", "-cov_db_dir", str(db_dir), "-report_dir", str(rep), "-report_format", "all", "-nolog"]
    if db_name:
        cmd += ["-cov_db_name", db_name]
    if merge:
        cmd += ["-merge_dir", str(out / f"cov-{name}"), "-merge_db_name", name]
    p = subprocess.run(cmd, cwd=out, capture_output=True, text=True, timeout=900)
    log = p.stdout + p.stderr
    (rep / "xcrg_summary.txt").write_text(log)
    res: dict = {"report_dir": rep.name, "html": bool(list(rep.rglob("*.html")))}
    for kind in ("Line", "Branch", "Condition", "Toggle"):
        m = re.search(rf"{kind} Coverage Score\s+([\d.]+)", log)
        if m:
            res[{"Line": "statement"}.get(kind, kind.lower())] = float(m.group(1))
    mods = rep / "codeCoverageReport" / "modules.html"
    if mods.exists():
        rows = [r for r in html_rows(mods) if len(r) >= 8 and re.fullmatch(r"\d+", r[0] or "")]
        res["modules"] = [f"{r[1]} inst={r[3]} stmt={r[4]} branch={r[5]} cond={r[6]} toggle={r[7]}" for r in rows][:6]
        scopes = set()
        for f in (rep / "codeCoverageReport").glob("mod*.html"):
            txt = " ".join(c for r in html_rows(f) for c in r)
            scopes.update(re.findall(r"\b([A-Za-z_]\w*)\.[A-Za-z_]\w*\s*\[", txt))
        res["generate_scopes"] = sorted(scopes)
    bins = functional_bins(rep)
    if bins:
        lines = ["# functional coverage bins rendered from xcrg's HTML report (bin , hits , at_least , coverpoint)"]
        lines += [f"  {b} , {h} , 1 , {cp}" for b, h, cp in bins]
        (rep / "functional_bins.txt").write_text("\n".join(lines) + "\n")
        res["functional_bins"] = [f"{cp}.{b}={h}" for b, h, cp in bins][:24]
    return res


def write_manifest(out: Path, a, r: dict, res: dict, version: str | None):
    man = {
        "run_id": r["name"], "simulation_set": r["simset"], "test": a.uvm_test or res.get("uvm", {}).get("test"),
        "seed": r.get("seed"), "plusargs": a.plusarg or [], "mode": a.mode, "runtime": a.runtime,
        "vivado_xsim_version": version, "verdict": res["verdict"], "first_failure": res.get("first_failure"),
        "transcript": f"{r['name']}.simulate.log", "waveform": f"{r['name']}.wdb",
        "coverage_database": f"cov-{r['name']}" if r.get("cov") else None,
        "artifacts": {p.name: p.stat().st_size for p in out.glob(f"{r['name']}.*") if p.is_file()},
    }
    (out / f"{r['name']}.manifest.json").write_text(json.dumps(man, indent=2))


def resolve_xpr(p: str) -> str:
    """A .xpr path, or a directory holding exactly one project."""
    q = Path(p)
    if q.is_dir():
        found = sorted(q.glob("*.xpr")) or sorted(q.glob("*/*.xpr"))
        if found:
            return str(found[0])
    return p


def mcp_missing(a) -> dict:
    """The Vivado MCP server journals each session under <working_dir>/.vivado-ai/ and marks every
    vivado_execute call there with MCP_CMD_START."""
    if a.no_mcp or not (a.xpr or getattr(a, "fixture_tcl", None)):
        return {}
    jous = [j for d in ("", "*/", "*/*/") for j in Path.cwd().glob(f"{d}.vivado-ai/vivado_*.jou")]
    if any("MCP_CMD_START" in read(j) for j in jous):
        return {}
    target = f"open_project <abs>/{a.xpr}" if a.xpr else "source the fixture Tcl"
    done = "vivado_start ran but no vivado_execute yet" if jous else "no Vivado MCP session in this workspace"
    return {"error": f"MCP build step not done: {done}",
            "next": f"{'' if jous else 'call Vivado:vivado_start (working_dir = this workspace), then '}call "
                    f"Vivado:vivado_execute once with `{target}; close_project`, then rerun this exact command"}


def cmd_sim(a) -> int:
    if (gate := mcp_missing(a)):
        print(json.dumps(gate))
        return 2
    missing = [tb for tb in a.tb or [] if not Path(tb).is_file()]
    if missing:
        print(json.dumps({"error": f"--tb takes a testbench file path; not found: {', '.join(missing)}",
                          "next": "write the new test as its own .sv file and pass that path; S sim adds it to its "
                                  "own simset sim_<top>, so do not add it to --simset or edit the given testbench"}))
        return 2
    if a.xpr:
        xprs = [resolve_xpr(x) for x in a.xpr.split(",") if x]
        names = [a.name or (a.simset if len(xprs) == 1 else Path(x).parent.name) for x in xprs]
        builds = [argparse.Namespace(**{**vars(a), "xpr": x, "name": n}) for x, n in zip(xprs, names)]
    else:
        fx = [f for f in (a.fixture or "").split(",") if f]
        builds = [argparse.Namespace(**{**vars(a), "fixture": f, "name": a.name if len(fx) == 1 else f}) for f in fx] or [a]
    if len(builds) == 1:
        summary = sim_once(builds[0])
    else:
        summary = {"builds": [sim_once(b) for b in builds]}
        builds = [b.name for b in builds]
        scopes = {f: set(next((r["coverage"].get("generate_scopes", []) for r in b["runs"] if r.get("coverage")), []))
                  for f, b in zip(builds, summary["builds"])}
        allsc = set().union(*scopes.values())
        diff = {f: sorted(allsc - sc) for f, sc in scopes.items() if allsc - sc}
        if diff:
            summary["scopes_absent_from_build"] = diff
            lines = ["# Elaborated scopes compared across builds", ""]
            for f, sc in scopes.items():
                lines.append(f"- {f}: {', '.join(sorted(sc)) or '(no generate scopes)'}")
            lines.append("")
            for f, missing in diff.items():
                lines.append(f"- absent from {f} (elaborated out, not untested): {', '.join(missing)}")
            Path(a.out, "build-compare.md").write_text("\n".join(lines) + "\n")
            summary["build_compare"] = "build-compare.md"
    shown = _compact(summary)
    if a.contract and not summary.get("error"):
        shown["contract"], shown["contract_next"] = contract_of(summary), CONTRACT_NEXT
    out = Path(a.out).resolve()
    guide = {}
    for r in summary.get("runs") or []:
        if r.get("verdict") == "FAIL":
            guide = fail_guidance(next((out / Path(f).name for f in r.get("artifacts", []) if f.endswith(".simulate.log")), None))
            break
        if r.get("verdict") == "PASS" and r.get("first_fail"):
            first = out / r["first_fail"]["log"]
            passed, quote, _ = fail_facts(first)
            guide = {"requirement": f"initial FAIL ({first.name}) violated "
                                    + requirement_hint(passed, quote, passing_order(companion_pass_log(first))),
                     "next": REPAIRED_NEXT}
            break
        if r.get("verdict") == "INCONCLUSIVE" and not r.get("pass_count"):
            guide = {"next": NO_MARKER_NEXT}
    runs = summary.get("runs") or []
    if a.cov and not guide and runs and all(r.get("verdict") == "PASS" for r in runs if "verdict" in r):
        if any(r.get("run") == "merged" for r in runs):
            reps = [k for k in summary.get("kept_previous", []) if k.endswith("-report")]
            reps += [r["coverage"]["report_dir"] for r in runs if (r.get("coverage") or {}).get("report_dir")]
            guide = {"next": f"{COV_MERGED_NEXT}. Reports (text+HTML): {', '.join(reps)}"}
        elif not a.tb and not a.seeds and len(builds) == 1:
            guide = {"next": COV_BASELINE_NEXT}
    if a.ports:
        exp = (a.expect or [""])[0]
        mk = a.replay_marker or (f"REPLAY_{exp.split('=')[0].upper()}" if exp else None)
        guide["replay_next"] = (
            f"the checked replay prints `{mk}={exp.split('=', 1)[-1]}` then `TEST_PASS`. If the request names "
            "another line, rerun this exact command with --replay-marker set to the text before '='"
            if exp else
            "the checked replay has no output oracle yet: rerun this exact command with `--expect <port>=<final value "
            "from the source run>` and `--replay-marker <NAME>`; it then prints `<NAME>=<value>` and TEST_PASS (for a "
            "required `FOO_SUM=17` pass `--expect sum=17 --replay-marker FOO_SUM`). Do not write another replay "
            "testbench or simset yourself")
    if summary.get("unmatched_signals"):
        guide["signals_next"] = (f"not recorded, these names matched nothing: {' '.join(summary['unmatched_signals'])}. "
                                 "Rerun this command with the exact names from signals_available, one per requested "
                                 "signal; a dump missing a requested signal does not satisfy the request")
    nxt = {"contract_next": shown.pop("contract_next")} if "contract_next" in shown else {}
    shown = {**nxt, **guide, **note_findings(out), **shown}
    emit(out, shown, a.max_print)
    return 0


def report_path(out) -> dict:
    """Absolute report path for the case: the shell keeps a cd between commands,
    and a later relative outputs/<case>/report.md then lands in a nested copy."""
    parts = Path(out).resolve().parts if out else ()
    if "outputs" not in parts or parts.index("outputs") + 1 >= len(parts):
        return {}
    i = parts.index("outputs")
    case = Path(*parts[:i + 2])
    hint = (f"save the report to {case}/report.md and later evidence under {case}, giving the tool those absolute "
            "paths (paths quoted inside the report stay workspace-relative); a cd in an earlier command moves "
            "relative paths into a nested outputs/ where nobody looks")
    if "outputs" in parts[i + 1:]:
        hint += f". This --out already resolved inside a nested outputs/ ({Path(*parts)}): rerun with {case}"
    return {"report_next": hint}


def emit(out: Path, res: dict, limit: int) -> None:
    """Print the result and keep the same JSON in <out>/last_result.json, so a
    run the agent's shell moved to the background can still be read."""
    res = {**report_path(out), **res}
    text = json.dumps(res, separators=(",", ":"))
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / "last_result.json").write_text(text + "\n")
    print(text[:limit])


def contract_of(summary: dict) -> dict:
    """Draft of the report contract from a sim summary. failure_class is the
    parser's suggestion; confirm it against the evidence before reporting."""
    runs = [r for r in summary.get("runs") or [] if "verdict" in r]
    for b in summary.get("builds") or []:
        runs += [r for r in b.get("runs") or [] if "verdict" in r]
    verdicts = {r["verdict"] for r in runs}
    verdict = "FAIL" if "FAIL" in verdicts else "PASS" if verdicts == {"PASS"} else "INCONCLUSIVE"
    key = next((r for r in runs if r["verdict"] == verdict), runs[0] if runs else {})
    ff = key.get("first_failure") or key.get("first_tool_error") or {}
    files = [f for f in key.get("artifacts", []) if f.endswith((".simulate.log", ".wdb", ".vcd", ".saif", ".values.md"))]
    if verdict == "PASS":
        what = f"terminal marker {(key.get('pass_markers') or ['?'])[0]!r}"
        if key.get("finish_time"):
            what += f", $finish at {key['finish_time']}"
    elif ff:
        what = f"first causal message {ff.get('message')!r}" + (f" at {ff['time']}" if ff.get("time") else "")
    else:
        what = "no terminal marker and no failure line"
    notes = (f"{key.get('run', '?')}: {verdict} at stage {key.get('stage', '?')}; {what}. "
             f"Evidence: {', '.join(Path(f).name for f in files[:6]) or 'none'}. "
             f"{len(runs)} run(s): " + ", ".join(f"{r['run']}={r['verdict']}" for r in runs[:8]) + ".")
    replay = next((r for r in runs if r.get("checked_testbench")), None)
    if replay:
        notes += (f" Portable replay {replay['run']}={replay['verdict']}: create_testbench output captured into its own "
                  f"simulation set, run from a checked copy ({Path(replay['checked_testbench']).name}) that adds an "
                  "output oracle, a watchdog and one terminal marker; the DUT and source testbench were not changed.")
        if replay.get("workaround"):
            notes += (f" Generated-flow remediation, not a failure: {replay['workaround']}. "
                      "failure_class stays the run's own class.")
    out = Path(summary.get("out") or ".")
    c = {"backend": "XSim", "backend_version": summary.get("vivado_version"),
         "stage": key.get("stage", "n/a"), "functional_verdict": verdict,
         "failure_class": (key.get("suggested_class") or "none").rstrip("?").split("_or_")[0],
         "failure_class_basis": "parser suggestion; confirm design vs testbench from the expected/observed values",
         "first_causal_message": ff.get("message"), "sim_time": ff.get("time"),
         "dut_modified": False, "switched_simulator": False,
         "artifacts": [_rel(out / Path(f).name) for f in files[:8]], "notes": notes}
    if verdict == "FAIL":
        c["tests_passed_before_failure"] = len([p for p in key.get("pass_markers", []) if "REGRESSION" not in p])
    first = key.get("first_fail")
    if first:
        c.update({"initial_verdict": "FAIL", "tests_passed_before_failure": first["tests_passed_before_failure"],
                  "first_check_fail": first["message"]})
        c["artifacts"].insert(0, _rel(out / first["log"]))
        c["notes"] = (f"Initial FAIL ({first['log']}): {first['message']!r} at {first['time']}, after "
                      f"{first['tests_passed_before_failure']} passing test(s). Latest run: " + notes)
    return c


def _compact(x):
    """Printed view: no artifact lists (they are in <out>/*.summary.json)."""
    if isinstance(x, dict):
        d = {k: _compact(v) for k, v in x.items() if k not in ("artifacts", "out")}
        if x.get("artifacts"):
            d["files"] = [f for f in x["artifacts"]
                          if Path(f).suffix in (".wdb", ".wcfg", ".saif", ".vcd") or f.endswith(("simulate.log", "values.md"))]
        return d
    if isinstance(x, list):
        return [_compact(v) for v in x if not (isinstance(v, str) and v.startswith("glbl "))]
    return x


def keep_previous_cov(out: Path, name: str) -> list[str]:
    """A coverage run never overwrites an earlier run of the same name: its transcript and database
    are kept as <name>.prev<k>.* and cov-<name>.prev<k>, so baseline and final both survive."""
    log, db = out / f"{name}.simulate.log", out / f"cov-{name}"
    if not (log.exists() or db.exists()):
        return []
    k = 1
    while (out / f"{name}.prev{k}.simulate.log").exists() or (out / f"cov-{name}.prev{k}").exists():
        k += 1
    kept = []
    for src, dst in ((log, out / f"{name}.prev{k}.simulate.log"), (db, out / f"cov-{name}.prev{k}"),
                     (out / f"cov-{name}-report", out / f"cov-{name}.prev{k}-report")):
        if src.exists():
            shutil.move(str(src), dst)
            kept.append(_rel(dst))
    return kept


def keep_first_fail(out: Path, res: dict) -> None:
    """The first FAIL transcript of a run name survives later re-runs (repairs) under that name."""
    first = out / f"{res['run']}.first_fail.simulate.log"
    cur = out / f"{res['run']}.simulate.log"
    if res.get("verdict") == "FAIL" and not first.exists() and cur.exists():
        shutil.copy(cur, first)
        return
    if not first.exists():
        return
    tr = parse_transcript(re.sub(r"(?m)^# ?", "", read(first)))
    ff = tr.get("first_failure") or {}
    res["first_fail"] = {"log": first.name, "message": ff.get("message"), "time": ff.get("time"),
                         "tests_passed_before_failure": len([p for p in tr["pass_markers"] if "REGRESSION" not in p])}
    if res.get("verdict") == "FAIL" and (res.get("first_failure") or {}).get("message") != ff.get("message"):
        res["first_fail"]["note"] = ("this run fails differently from the first FAIL, so an edit changed the "
                                     "failure: restore that edit and report the first FAIL")


def _changed(a: list[str], b: list[str]) -> int:
    n = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            n += max(i2 - i1, j2 - j1)
    return n


def repair_check(out: Path, simset: str, sources: list[str], verdict: str) -> dict:
    """Snapshot the simset's sources at its first FAIL; on later runs, count lines changed since then."""
    snap = out / ".first_fail_sources" / simset
    manifest = snap / "manifest.json"
    files = [Path(s) for s in sources if Path(s).is_file()]
    if not manifest.exists():
        if verdict == "FAIL" and files:
            snap.mkdir(parents=True, exist_ok=True)
            m = {}
            for i, f in enumerate(files):
                shutil.copy(f, snap / f"{i}_{f.name}")
                m[str(f)] = f"{i}_{f.name}"
            manifest.write_text(json.dumps(m, indent=1))
        return {}
    m = json.loads(manifest.read_text())
    per = {}
    for src, copy in m.items():
        if Path(src).is_file():
            n = _changed(read(snap / copy).splitlines(), read(Path(src)).splitlines())
            if n:
                per[Path(src).name] = n
    total = sum(per.values())
    res = {"lines_changed_since_first_fail": total, "files": per}
    if total > 1:
        res["warning"] = (f"{total} lines differ from the sources at the first FAIL; a repair changes exactly one line. "
                          "If you added a signal or edited each use of one, revert those edits and change only the "
                          "line that defines the signal, then re-run.")
    return res


def sim_once(a) -> dict:
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not a.xpr and not (a.fixture_tcl and a.fixture):
        return {"error": "need --xpr or --fixture-tcl + --fixture"}
    seeds = [s for s in (a.seeds or "").split(",") if s.strip()]
    base = a.name or (a.simset if a.xpr else a.fixture)
    runs = []
    if seeds:
        for s in seeds:
            runs.append({"simset": a.simset, "name": f"{base}-seed{s}", "seed": s, "cov": a.cov, "primary": True})
    else:
        runs.append({"simset": a.simset, "name": base, "cov": a.cov, "primary": True})
    for tb in a.tb or []:
        top = a.tb_top or Path(tb).stem
        runs.append({"simset": f"sim_{top}"[:40], "name": f"{base}-{top}", "cov": a.cov})
    kept = [k for r in runs if r.get("cov") for k in keep_previous_cov(out, r["name"])]
    tag = f"{base}.sr"
    rc, console, dt = run_vivado(build_tcl(a, out, runs), out, tag, a.timeout)
    other = re.search(r"SR_NOT_XSIM (\S+)", console)
    if other:
        return {"error": f"the project targets {other[1]}; sim runs XSim only and does not switch simulators. "
                         f"Run {other[1]} through the Vivado MCP (launch_simulation) as its backend reference says."}
    version = (re.search(r"SR_VERSION (\S+)", console) or [None, None])[1]
    results = []
    for r in runs:
        res = summarize_run(out, r["name"], console, a.pass_marker)
        if r.get("cov"):
            res["coverage"] = xcrg(out, r["name"], out / f"cov-{r['name']}", r["name"])
        if seeds or a.manifest:
            write_manifest(out, a, r, res, version)
        keep_first_fail(out, res)
        results.append(res)
    # replay failing seeds once with the exact same options
    if a.replay_failures and seeds:
        rruns = [{"simset": a.simset, "name": f"{r['name']}-replay", "seed": r["seed"], "cov": False}
                 for r, res in zip(runs, results) if r.get("seed") and res["verdict"] != "PASS"]
        if rruns:
            a2 = argparse.Namespace(**{**vars(a), "tb": []})
            rc2, console2, dt2 = run_vivado(build_tcl(a2, out, rruns), out, f"{base}.replay.sr", a.timeout)
            dt += dt2
            for r in rruns:
                res = summarize_run(out, r["name"], console2, a.pass_marker)
                write_manifest(out, a, r, res, version)
                orig = next(x for x in results if x["run"] == r["name"][:-7])
                same = _random_choices(out / f"{orig['run']}.simulate.log") == _random_choices(out / f"{r['name']}.simulate.log")
                ff1, ff2 = (orig.get("first_failure") or {}), (res.get("first_failure") or {})
                res["replay_of"] = orig["run"]
                res["reproduced_identically"] = bool(same and ff1.get("message") == ff2.get("message") and ff1.get("time") == ff2.get("time"))
                results.append(res)
    # portable replay: checked copy of the generated testbench, run in its own simset
    gen = re.findall(r"SR_GENERATED (\S+)", console)
    if a.ports and gen:
        xpr = (re.search(r"SR_XPR (\S+)", console) or [None, None])[1]
        chk = make_checked_replay([Path(g) for g in gen], a.expect or [], a.replay_marker, out / "replay")
        if chk and xpr:
            a2 = argparse.Namespace(**{**vars(a), "xpr": xpr, "fixture_tcl": None, "tb": [str(chk)],
                                       "tb_top": "tb_replay_checked", "ports": None, "cov": False,
                                       "saif": None, "vcd": None, "wcfg": None})
            res, dt2 = run_scripts_flow(a2, out, "sim_tb_replay_checked", f"{base}-replay")
            dt += dt2
            res["checked_testbench"] = str(chk)
            results.append(res)
    # merged coverage over passing runs only
    if a.cov and len([r for r in results if r.get("coverage")]) > 1:
        ok = [r["run"] for r in results if r.get("coverage") and r["verdict"] == "PASS"]
        if len(ok) > 1:
            pool = out / "cov-merge-inputs"
            for n in ok:
                for kind in ("xsim.codeCov", "xsim.covdb"):
                    src = out / f"cov-{n}" / kind / n
                    if src.exists():
                        shutil.copytree(src, pool / kind / n, dirs_exist_ok=True)
            m = xcrg(out, "merged", pool, None, merge=True)
            if m:
                m["merged_runs"] = ok
                m["excluded_runs"] = [r["run"] for r in results if r.get("coverage") and r["verdict"] != "PASS"]
                results.append({"run": "merged", "coverage": m})
    if a.trace:
        for res in results:
            ff = res.get("first_failure") or {}
            tp = out / f"{res['run']}.trace"
            if tp.exists():
                end = to_ps(ff.get("time")) if ff else None
                tbl = vcd_table(tp, end, a.clock, a.trace_rows)
                if tbl:
                    (out / f"{res['run']}.values.md").write_text(
                        f"Signal values at the last {a.trace_rows} rising clock edges"
                        f"{' up to the first failure at ' + ff['time'] if ff.get('time') else ''}:\n\n{tbl}\n")
                    res["values_table"] = f"{res['run']}.values.md"
    summary = {"tool": "XSim", "vivado_version": version, "vivado_rc": rc, "elapsed_s": round(dt),
               "out": str(out), "runs": results,
               **({"kept_previous": kept, "kept_note": "earlier coverage run of the same name kept, not "
                   "overwritten: cite it as the baseline"} if kept else {})}
    noobj = sorted(set(re.findall(r"SR_NOOBJ (\S+)", console)))
    if noobj:
        summary["unmatched_signals"] = noobj
        avail = re.search(r"SR_TOPOBJS (.*)", console)
        if avail:
            summary["signals_available"] = avail[1].split()[:80]
    if gen:
        summary["generated_testbench"] = gen
    if rc == 124:
        summary["error"] = f"vivado batch timed out after {a.timeout}s"
    elif rc == 127:
        summary["error"] = console
    elif not results or all(r.get("verdict") == "INCONCLUSIVE" and not r.get("first_tool_error") for r in results if "verdict" in r):
        errs = [l for l in console.splitlines() if l.startswith("ERROR:")][:3]
        if errs:
            summary["vivado_errors"] = errs
    (out / f"{base}.summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def run_scripts_flow(a, out: Path, simset: str, name: str) -> tuple[dict, float]:
    """launch_simulation -scripts_only, then run compile/elaborate/simulate
    outside Vivado. Used for create_testbench replays, whose generated elaborate
    command can carry an option the installed xelab rejects."""
    tcl = "\n".join([TCL_LIB, f"set out {tq(out)}", f"open_project {tq(a.xpr)}"] + [
        f"if {{[llength [get_filesets -quiet {simset}]] == 0}} {{ create_fileset -simset {simset} }}",
        *[f"add_files -fileset {simset} -norecurse {tq(Path(tb).resolve())}" for tb in a.tb],
        f"set_property top {a.tb_top} [get_filesets {simset}]",
        f"update_compile_order -fileset {simset}",
        f"set_property xsim.simulate.runtime {a.runtime} [get_filesets {simset}]",
        f"launch_simulation -simset {simset} -mode behavioral -scripts_only",
        f"puts \"SR_SCRIPTS [::sr::simdir {simset}]\"",
        f"puts \"SR_SCRIPTDIR [get_property DIRECTORY [current_project]]/[get_property NAME [current_project]].sim/{simset}/behav/xsim\"",
        "close_project"]) + "\n"
    rc, console, dt = run_vivado(tcl, out, f"{name}.scripts.sr", a.timeout)
    d = Path((re.search(r"SR_SCRIPTDIR (\S+)", console) or [None, ""])[1])
    res: dict = {"run": name}
    el = d / "elaborate.sh"
    if el.exists():
        txt = read(el)
        if re.search(r"-+ignore_localparam_override", txt):
            h = subprocess.run(["xelab", "-help"], capture_output=True, text=True).stdout
            if "suppress_localparam_override_error" in h:
                shutil.copy(el, out / f"{name}.elaborate.sh.generated")
                el.write_text(re.sub(r"-+ignore_localparam_override", "-suppress_localparam_override_error", txt))
                res["workaround"] = ("generated elaborate.sh passed obsolete -ignore_localparam_override; "
                                     "replaced with -suppress_localparam_override_error (xelab -help confirms); "
                                     f"original kept as {name}.elaborate.sh.generated")
    t0 = time.time()
    # the scripts-only simulate step loads the snapshot without running it, so
    # run the elaborated snapshot to $finish (the checked replay has a watchdog)
    steps = [["bash", "compile.sh"], ["bash", "elaborate.sh"],
             ["xsim", f"{a.tb_top}_behav", "-R", "-log", "simulate.log"]]
    for step in steps:
        if step[0] == "bash" and not (d / step[1]).exists():
            break
        try:
            p = subprocess.run(step, cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=a.timeout)
        except subprocess.TimeoutExpired:
            res["error"] = f"{' '.join(step)} timed out"
            break
        if p.returncode != 0:
            break
    dt += time.time() - t0
    for f in ("compile.log", "elaborate.log", "simulate.log", "xvlog.log", "xelab.log", "compile.sh", "elaborate.sh", "simulate.sh"):
        if (d / f).exists():
            shutil.copy(d / f, out / f"{name}.{f}")
    for w in d.glob("*.wdb"):
        shutil.copy(w, out / f"{name}.wdb")
    res.update(summarize_run(out, name, "", a.pass_marker))
    return res, dt


def make_checked_replay(gen: list[Path], expects: list[str], marker: str | None, dest: Path) -> Path | None:
    """Self-checking copy of a create_testbench replay: inlined vectors, output
    oracle just before the stimulus ends, watchdog, one terminal marker. The
    generated files are left untouched."""
    tb = next((g for g in gen if g.name.endswith("_tb.sv")), None)
    vec = next((g for g in gen if "xil_tb_vectors" in g.name), None)
    if not tb or not vec:
        return None
    vtext = read(vec)
    unit = {"ps": 1, "ns": 1000, "us": 10**6, "": 1}
    t_end = sum(int(n) * unit[u] for n, u in re.findall(r"#\s*(\d+)\s*(ps|ns|us)?\b", vtext))
    text = read(tb)
    mm = re.search(r"\bmodule\s+(\w+)", text)
    if mm:
        text = re.sub(rf"\b{mm.group(1)}\b", "tb_replay_checked", text)
    text = re.sub(r'`include\s+"[^"]*xil_tb_vectors[^"]*"', lambda m: vtext, text)
    checks, shown = [], []
    for e in expects:
        port, val = e.split("=", 1)
        checks.append(f'  if ({port} !== {val}) $fatal(1, "REPLAY_CHECK_FAIL {port} expected {val} got %0d", {port});')
        shown.append((port, val))
    disp = ""
    if shown:
        mk = marker or f"REPLAY_{shown[0][0].upper()}"
        disp = f'  $display("{mk}=%0d", {shown[0][0]});'
    add = "\n".join([
        "// ---- verification additions (simrtl.py): output oracle, watchdog, terminal marker",
        "initial begin : replay_oracle",
        f"  #({max(t_end - 1, 1)});",
        *checks, disp,
        '  $display("TEST_PASS");',
        "end",
        "initial begin : replay_watchdog",
        f"  #({2 * t_end + 100000});",
        '  $fatal(1, "replay watchdog expired");',
        "end",
    ])
    text = re.sub(r"\bendmodule\b(?![\s\S]*\bendmodule\b)", add + "\n\nendmodule", text)
    dest.mkdir(parents=True, exist_ok=True)
    p = dest / "tb_replay_checked.sv"
    p.write_text(text)
    return p


def _random_choices(p: Path) -> list[str]:
    return [l for l in read(p).splitlines() if l.startswith("RANDOM_CHOICE")]


# ----------------------------------------------------------------- logs

TOOL_ERR_ANY = re.compile(r"\s*(\*\* (Error|Fatal)|%E-|Error-\[|(\w+: )?\*[EF],\w+)")
THIRD_PARTY_ERR = re.compile(r"\s*(\*\* (Error|Fatal)|Error-\[|(\w+: )?\*[EF],\w+)")
THIRD_PARTY_NEXT = ("failure seen only on a third-party simulator, and no XSim run yet: read only the "
                    "error_sites lines (and the declarations they use), copy that construct with the same types "
                    "into outputs/<case>/reduced/<name>.sv with a tb module that ends $display(\"TEST_PASS\"); $finish;, "
                    "then S xsim-files outputs/<case>/reduced/<name>.sv --top <tb> --out outputs/<case>/xsim_control; "
                    "only a 'control: COMPLETE' result counts; on HARNESS_ERROR fix the harness and rerun")

STAGE_OF = [("compile", r"xvlog|xvhdl|compile|vlog|vcom|xmvlog|vlogan"),
            ("elaborate", r"xelab|elab|vopt|xmelab"),
            ("simulate", r"simulate|xsim|vsim|transcript|xmsim|simv|run")]


def stage_of(name: str) -> str:
    for st, pat in STAGE_OF:
        if re.search(pat, name, re.I):
            return st
    return "other"


# third-party error sites: Xcelium "*E,CODE (file,line|col)", Questa "** Error: (vlog-N) file(line)"
ERR_SITE = [re.compile(r"\*[EF],(\w+)\s+\(([^,()\s]+),(\d+)\|\d+\)"),
            re.compile(r"\*\* (?:Error|Fatal)(?: \(suppressible\))?: \((\w+-\d+)\) ([^\s()]+)\((\d+)\)")]
SRC_SUFFIX = (".sv", ".v", ".svh", ".vh", ".vhd", ".vhdl", ".xci", ".tcl", ".f", ".do", ".prj", ".sh", ".xpr")


def third_party_backend(err: str) -> str:
    """BACKENDS id of the simulator that printed this error line."""
    if re.search(r"\bxm(elab|vlog|vhdl|sim)\b|\bxrun\b|\*[EF],\w+", err):
        return "xcelium"
    if err.lstrip().startswith("Error-["):
        return "vcs"
    return "questa"


def error_sites(texts: list[str], roots: list[Path], limit: int = 8) -> list[dict]:
    """Per (code, source file): count, first line and that line's text, with the
    log's (customer-host) path mapped to the copy under the given directories."""
    index: dict[str, list[Path]] = {}
    for r in roots:
        for p in (r.rglob("*") if r.is_dir() else []):
            if p.is_file() and p.suffix.lower() in SRC_SUFFIX:
                index.setdefault(p.name, []).append(p)
    sites: dict[tuple, dict] = {}
    for t in texts:
        for rx in ERR_SITE:
            for code, path, line in rx.findall(t):
                k = (code, Path(path).name)
                s = sites.setdefault(k, {"code": code, "file": Path(path).name, "count": 0, "first_line": int(line)})
                s["count"] += 1
                s["first_line"] = min(s["first_line"], int(line))
    for (code, name), s in sites.items():
        cands = index.get(name, [])
        if cands:
            s["file"] = _rel(cands[0])
            src = read(cands[0]).splitlines()
            if 0 < s["first_line"] <= len(src):
                s["source"] = src[s["first_line"] - 1].strip()[:240]
                # declaration of the indexed name the error points at
                for ident in re.findall(r"\b([A-Za-z_]\w*)\s*\[", s["source"])[-1:]:
                    decl = re.compile(rf"^\s*(localparam|parameter|wire|logic|reg|string|bit|int)\b[^;]*\b{ident}\b")
                    n = next((i for i, l in enumerate(src) if decl.match(l)), None)
                    if n is not None:
                        s["declaration"] = f"{n + 1}: {src[n].strip()[:240]}"
    return sorted(sites.values(), key=lambda s: -s["count"])[:limit]


def cmd_logs(a) -> int:
    """Variant x stage matrix over recorded logs: one variant per directory."""
    files = []
    for p in a.paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(x for x in p.rglob("*") if x.is_file() and x.suffix in (".log", ".txt", ".out", ".jou")
                            and x.stat().st_size < 20_000_000)
        elif p.is_file():
            files.append(p)
    variants: dict[str, dict] = {}
    texts: list[str] = []
    for f in files[: a.limit]:
        if f.suffix == ".jou":
            continue
        txt = read(f)
        texts.append(txt)
        v = variants.setdefault(_rel(f.parent), {"stages": {}, "files": []})
        v["files"].append(f.name)
        st = stage_of(f.name)
        ver = (re.search(r"(?:Vivado|xsim|xelab|xvlog|Questa|ModelSim|Xcelium|VCS)\S*\s+v?(20\d\d\.\d(?:\.\d)?|[A-Z]-20\d\d\.\d+\S*|\d\d\.\d\d\.\d+)", txt)
               or re.search(r"(?m)^TOOL:\s+\w+\(\d+\)\s+(\d\d\.\d\d-\w+)", txt))
        if ver:
            v["version"] = ver.group(1)
        err_lines = [l.strip()[:300] for l in txt.splitlines() if TOOL_ERR_ANY.match(l)]
        e = parse_tool_log(txt) or ({"message": err_lines[0]} if err_lines else None)
        tr = parse_transcript(txt) if st == "simulate" else None
        crash = sorted(set(re.findall(r"(SIGSEGV|SIGABRT|EXCEPTION_ACCESS_VIOLATION|Abnormal program termination|core dumped|Segmentation fault|stack trace)", txt, re.I)))
        if crash:
            status = "CRASH"
        elif e or (tr and (tr["first_failure"] or tr["check_fail_count"])):
            status = "FAIL"
        elif st == "simulate" and tr and tr["pass_count"]:
            status = "PASS(marker)"
        elif not txt.strip():
            status = "empty"
        else:
            status = "clean"
        info = {"file": f.name, "status": status}
        if e:
            info["first_error"] = e["message"]
        if err_lines:
            info["error_lines"] = len(err_lines)
        if tr and tr["first_failure"]:
            info["first_failure"] = tr["first_failure"]["message"] + (f" @ {tr['first_failure']['time']}" if tr["first_failure"].get("time") else "")
        if tr and tr["pass_count"]:
            info["markers"] = tr["pass_markers"][:4]
        if crash:
            info["crash"] = crash
        if st == "simulate":
            vals = [l.strip()[:160] for l in txt.splitlines()
                    if re.search(r"(?i)(expected|got|actual|mismatch|\bvalue|\[\d+\]\s*=|\w+\s*=\s*-?(0x)?[0-9a-f]+\b)", l)
                    and not re.match(r"\s*(INFO|WARNING|#|source|\$finish)", l)]
            if vals:
                info["value_lines"] = vals[: a.values]
        v["stages"].setdefault(st, info) if st not in v["stages"] or status in ("FAIL", "CRASH") else None
        if status in ("FAIL", "CRASH"):
            v["stages"][st] = info
    # what a passing control actually checked: the guard lines before the
    # $display that prints its marker, from sources/<variant> when present
    for name, v in variants.items():
        sim = v["stages"].get("simulate") or {}
        for mk in sim.get("markers", [])[:2]:
            guard = marker_guard(Path(name), mk)
            if guard:
                sim.setdefault("checked_before_marker", []).append(guard)
    rows = ["| variant | version | compile | elaborate | simulate |", "|---|---|---|---|---|"]
    for name, v in variants.items():
        cells = []
        for st in ("compile", "elaborate", "simulate"):
            i = v["stages"].get(st)
            cell = "absent" if not i else i["status"] + (f": {i.get('first_error') or i.get('first_failure') or ''}"[:110] if i["status"] in ("FAIL", "CRASH") else "")
            if i and i.get("markers"):
                cell += f" `{i['markers'][0][:70]}`"
            if i and i.get("checked_before_marker"):
                cell += f" after `{i['checked_before_marker'][0][:150]}`"
            cells.append(cell)
        rows.append(f"| {name} | {v.get('version', '?')} | " + " | ".join(cells) + " |")
    errs = [i.get("first_error") or "" for v in variants.values() for i in v["stages"].values()
            if i["status"] in ("FAIL", "CRASH")]
    xsim_ran = any(re.search(r"xsim|xelab|xvlog", " ".join(v["files"]) + n, re.I) for n, v in variants.items())
    third_party_only = errs and not xsim_ran and all(THIRD_PARTY_ERR.match(e) for e in errs)
    out = {"next": THIRD_PARTY_NEXT.replace("outputs/<case>", a.out.rstrip("/")) if a.out else THIRD_PARTY_NEXT} \
        if third_party_only else {}
    if a.contract and third_party_only:
        out["contract"] = "not yet: the xsim-files control in next prints contract_draft once it is COMPLETE"
    roots = [Path(p) for p in a.paths]
    sites = error_sites(texts, roots)
    if sites:
        out["error_sites"] = sites
    inputs = sorted(_rel(p) for r in roots for p in (r.rglob("*") if r.is_dir() else [r])
                    if p.is_file() and p.suffix.lower() in SRC_SUFFIX)
    if inputs:
        out["inputs"] = inputs[:30] + ([f"... {len(inputs) - 30} more"] if len(inputs) > 30 else [])
    out.update({"matrix_md": "\n".join(rows), "variants": variants})
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        Path(a.out, "log-matrix.md").write_text("# Recorded runs: variant x stage\n\n" + "\n".join(rows) + "\n")
        if third_party_only:
            # what a later xsim-files control is differential against
            name, v = next(((n, v) for n, v in variants.items()
                            if any(i["status"] in ("FAIL", "CRASH") for i in v["stages"].values())), (None, {}))
            st, i = next((s, i) for s, i in v["stages"].items() if i["status"] in ("FAIL", "CRASH"))
            Path(a.out, "logs.json").write_text(json.dumps({"third_party": {
                "backend": third_party_backend(i.get("first_error", "")), "version": v.get("version"),
                "stage": st, "first_error": i.get("first_error"), "log": f"{name}/{i['file']}",
                "error_lines": i.get("error_lines")}}, indent=1) + "\n")
    print(json.dumps({**report_path(a.out), **out}, indent=1)[: a.max_print])
    return 0


def marker_guard(run_dir: Path, marker_line: str) -> str | None:
    """Checks guarding the line that printed a pass marker, read from the
    variant's sources (``runs/<v>`` -> ``sources/<v>``, else the run dir)."""
    text = re.sub(r"^\W*", "", marker_line).split(":")[0].strip() or marker_line
    want = marker_line.strip()[:60]
    cands = [run_dir.parent.parent / "sources" / run_dir.name, run_dir]
    for d in cands:
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix.lower() not in (".sv", ".v", ".svh", ".vhd", ".vhdl"):
                continue
            lines = read(f).splitlines()
            for i, line in enumerate(lines):
                if "display" in line or "report" in line.lower():
                    if want[:40] in line or (text in line and "PASS" in text):
                        guard = [l.strip() for l in lines[max(0, i - 6):i]
                                 if re.search(r"\b(if|assert)\b|!==|!=|==", l)]
                        if guard:
                            return f"{f.name}:{i + 1}: " + " ".join(guard)[:300]
    return None


# ----------------------------------------------------------------- backends

BACKENDS = {
    # id: (contract name, executables, env vars, UG973 host support snapshot for 2026.1, notes)
    # contract names match the backend values report contracts use
    # (XSim|Questa|ModelSim|VCS|Xcelium|Riviera|ActiveHDL)
    "questa": ("Questa", ["vsim", "vlog", "vopt"], ["MGLS_LICENSE_FILE", "SALT_LICENSE_SERVER", "LM_LICENSE_FILE"],
               "Red Hat Linux / Windows (check UG973 for the installed release)",
               "needs compile_simlib output for the same Vivado release (compxlib.questa_compiled_library_dir)"),
    "modelsim": ("ModelSim", ["vsim", "vlog"], ["MGLS_LICENSE_FILE", "LM_LICENSE_FILE"],
                 "Red Hat Linux, Windows", "distinct from Questa; a Questa install is not ModelSim"),
    "vcs": ("VCS", ["vcs"], ["VCS_HOME", "SNPSLMD_LICENSE_FILE", "LM_LICENSE_FILE"],
            "Red Hat Linux only (Vivado integration is Linux-only)", "also needs the GCC package VCS requires"),
    "xcelium": ("Xcelium", ["xrun"], ["CDS_INST_DIR", "CDS_LIC_FILE", "LM_LICENSE_FILE"],
                "Red Hat Linux only (Vivado integration is Linux-only)", ""),
    "riviera": ("Riviera", ["vsimsa", "riviera"], ["ALDEC_PATH", "ALDEC_LICENSE_FILE"],
                "Red Hat Linux, Windows", "distinct from Active-HDL"),
    "activehdl": ("ActiveHDL", ["avhdl", "vsimsa"], ["ALDEC_LICENSE_FILE"],
                  "Windows only", "never selectable on Linux; Riviera-PRO is not a substitute"),
}


# ----------------------------------------------------------------- third-party run

SDF_OK = {"questa": r"vsim-3587|SDF Backannotation Successfully Completed",
          "modelsim": r"vsim-3587|SDF Backannotation Successfully Completed",
          "vcs": r"SDF annotation end", "xcelium": r"Annotation completed successfully",
          "riviera": r"SDF.*annotat\w* (?:was )?successful"}
SDF_BAD = re.compile(r"Failed to parse SDF|unexpected \$end|SDF annotation aborted|SDFPE|SDFCOM_NNTC|"
                     r"SDFCNC|SDFIRE|skipping annotation", re.I)
TP_ERR = re.compile(r"^\W*(?:\*\*\s*)?(?:Error|Fatal|ERROR|FATAL)\b[-:\[ ].*|^\S+: \*[EF],\S+.*")
TP_VER = re.compile(r"(Questa\S*\s+\S+\s+\S+|Chronologic VCS.*?Version \S+|xrun\S*: \S+|ModelSim\S* \S+ \S+|"
                    r"Riviera-PRO \S+)", re.I | re.S)


def _paren_counts(p: Path) -> tuple[int, int]:
    t = read(p)
    return t.count("("), t.count(")")


def run3p_tcl(a, out: Path, disp: str, lc: str, name: str, extra_elab: str | None) -> str:
    t = [TCL_LIB, f"set out {tq(out)}", f"open_project {tq(Path(a.xpr).resolve())}",
         "puts \"SR_VERSION [version -short]\"",
         f"set_property target_simulator {disp} [current_project]",
         f"if {{[info exists ::env({lc.upper()}_COMPILED_LIBRARY_DIR)]}} {{ "
         f"set_property compxlib.{lc}_compiled_library_dir $::env({lc.upper()}_COMPILED_LIBRARY_DIR) [current_project] }}"]
    if a.mode != "behavioral":
        run = "impl_1" if a.mode == "post-implementation" else "synth_1"
        t += [f"if {{[get_property PROGRESS [get_runs {run}]] ne \"100%\"}} {{ "
              f"puts SR_IMPLEMENTING; launch_runs {run} -jobs 4; wait_on_run {run} }}",
              f"puts \"SR_RUN_STATUS [get_property STATUS [get_runs {run}]]\""]
    ss = a.simset
    t += [f"set ss [get_filesets {ss}]", "current_fileset -simset $ss",
          "set srcs [concat [get_files -quiet -compile_order sources -used_in simulation -of_objects $ss] "
          "[get_files -quiet -of_objects $ss -filter {FILE_TYPE =~ *Verilog* || FILE_TYPE =~ VHDL*}]]",
          "foreach f [lsort -unique $srcs] { puts \"SR_SRC $f\" }"]
    if extra_elab:
        prop = f"{lc}.elaborate.{lc}.more_options" if lc == "vcs" else f"{lc}.elaborate.more_options"
        t.append(f"catch {{::sr::opt_add {ss} {prop} {{{extra_elab}}}}}")
    mode = f"-mode {a.mode}" + (f" -type {a.type}" if a.type else "")
    t += [f"if {{[catch {{launch_simulation -simset {ss} {mode}}} err]}} {{ puts \"SR_LAUNCH_ERROR $err\" }}",
          "catch {close_sim -force}",
          f"set base [file join [get_property DIRECTORY [current_project]] \"[get_property NAME [current_project]].sim\" {ss}]",
          "set best {}; set bt 0",
          "foreach f [glob -nocomplain -types f -directory $base */*/*/compile.log */*/*/elaborate.log */*/*/simulate.log */*/compile.log */*/simulate.log] {"
          " if {[file mtime $f] >= $bt} { set bt [file mtime $f]; set best [file dirname $f] } }",
          "puts \"SR_SIMDIR $best\"",
          "foreach f {compile.log elaborate.log simulate.log} { if {$best ne {} && [file exists [file join $best $f]]} "
          f"{{ file copy -force [file join $best $f] [file join $out \"{name}.$f\"] }} }}",
          "if {$best ne {}} { foreach w [glob -nocomplain -directory $best *.wlf *.vpd *.fsdb] "
          f"{{ file copy -force $w [file join $out \"{name}[file extension $w]\"] }} }}"]
    if a.type == "timing" and a.mode == "post-implementation":
        t += ["if {![catch {open_run impl_1}]} {",
              f" catch {{write_sdf -mode timesim -force [file join $out {name}.rewrite.sdf]}}",
              " catch {close_design} }"]
    t += ["catch {close_project}", "puts SR_DONE"]
    return "\n".join(t) + "\n"


def summarize_3p(out: Path, name: str, console: str, lc: str, timing: bool) -> dict:
    simdir = (re.search(r"SR_SIMDIR (\S+)", console) or [None, None])[1]
    logs = {st: read(out / f"{name}.{st}.log") for st in ("compile", "elaborate", "simulate")}
    allt = "\n".join(logs.values())
    first, stage = None, None
    for st, txt in logs.items():
        for i, line in enumerate(txt.splitlines()):
            if TP_ERR.match(line.strip()) or (timing and SDF_BAD.search(line)):
                first, stage = {"message": line.strip()[:300], "line": i + 1, "log": f"{name}.{st}.log"}, st
                break
        if first:
            break
    launch = (re.search(r"SR_LAUNCH_ERROR (.*)", console) or [None, None])[1]
    sim_txt = logs["simulate"]
    if lc in ("questa", "modelsim", "riviera"):
        sim_txt = re.sub(r"(?m)^# ?", "", sim_txt)
    tr = parse_transcript(sim_txt) if sim_txt else None
    res = {"run": name, "simdir": simdir, "backend_version": (TP_VER.search(allt) or [None, None])[1],
           "launch_error": launch[:300] if launch else None, "first_failure": first,
           "pass_markers": (tr or {}).get("pass_markers"), "finish_time": (tr or {}).get("finish_time"),
           "logs": [f"{name}.{st}.log" for st, t in logs.items() if t],
           "waveform": next((f"{name}{e}" for e in (".wlf", ".vpd", ".fsdb") if (out / f"{name}{e}").exists()), None)}
    if tr and tr.get("first_failure") and not first:
        res["first_failure"], stage = {**tr["first_failure"], "log": f"{name}.simulate.log"}, "simulate"
    if timing:
        sdfs = sorted(Path(simdir).glob("*.sdf")) if simdir else []
        res["sdf"] = [{"file": str(p), "open": o, "close": c} for p in sdfs for o, c in [_paren_counts(p)]]
        rw = out / f"{name}.rewrite.sdf"
        if rw.exists():
            o, c = _paren_counts(rw)
            res["sdf_rewrite"] = {"file": str(rw), "open": o, "close": c}
        res["sdf_annotated"] = bool(re.search(SDF_OK.get(lc, r"$^"), allt)) and not SDF_BAD.search(allt)
        res["sdf_messages"] = sorted({m.group(0) for m in SDF_BAD.finditer(allt)})[:6]
    unbalanced = any(s["open"] != s["close"] for s in res.get("sdf", []))
    if res["first_failure"] or launch:
        verdict = "FAIL"
        klass = "tool_defect" if timing and unbalanced else "design_or_environment?"
    elif timing and not res.get("sdf_annotated"):
        verdict, klass = "INCONCLUSIVE", "environment?"
    elif res["pass_markers"]:
        verdict, klass = "PASS", "none"
    else:
        verdict, klass = "INCONCLUSIVE", "timeout_or_no_marker?"
    res.update(verdict=verdict, stage=stage or ("simulate" if logs["simulate"] else "prerequisite"),
               suggested_class=klass)
    if timing and unbalanced:
        res["sdf_note"] = ("Vivado-written SDF has unbalanced parentheses"
                           + (" and write_sdf -mode timesim reproduces it" if res.get("sdf_rewrite") and
                              res["sdf_rewrite"]["open"] != res["sdf_rewrite"]["close"] else "")
                           + "; the generated SDF was left untouched. Report failure_class tool_defect (Vivado's "
                           "SDF writer): the tool that mishandled legal input is Vivado, which took a legal routed design "
                           "and wrote an illegal SDF. The simulator rejecting that file is correct and does not make it "
                           "environment")
    return res


def cmd_run(a) -> int:
    lc = a.backend.lower().replace("-", "").replace("_", "")
    lc = {"questasim": "questa", "rivierapro": "riviera"}.get(lc, lc)
    if lc not in BACKENDS or lc == "activehdl":
        print(json.dumps({"error": f"--backend must be one of questa, modelsim, vcs, xcelium, riviera; XSim uses sim"}))
        return 2
    disp = {"questa": "Questa", "modelsim": "ModelSim", "vcs": "VCS", "xcelium": "Xcelium", "riviera": "Riviera"}[lc]
    if (gate := mcp_missing(a)):
        print(json.dumps(gate))
        return 2
    a.xpr = resolve_xpr(a.xpr)
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    timing = a.type == "timing"
    base = a.name or f"{a.simset}-{lc}" + ("-timing" if timing else "")
    rc, console, dt = run_vivado(run3p_tcl(a, out, disp, lc, base, None), out, f"{base}.run", a.timeout)
    runs = [summarize_3p(out, base, console, lc, timing)]
    keep_first_fail(out, runs[0])
    rcheck = repair_check(out, a.simset, re.findall(r"^SR_SRC (.+?)\s*$", console, re.M), runs[0].get("verdict", ""))
    if timing and lc == "vcs" and any("NNTC" in m for m in runs[0].get("sdf_messages", [])):
        rc2, console2, dt2 = run_vivado(run3p_tcl(a, out, disp, lc, f"{base}-neg_tchk", "+neg_tchk"), out,
                                        f"{base}-neg_tchk.run", a.timeout)
        dt += dt2
        r2 = summarize_3p(out, f"{base}-neg_tchk", console2, lc, timing)
        r2["note"] = "rerun with +neg_tchk: SDFCOM_NNTC aborts annotation on negative setup/hold values by default"
        runs.append(r2)
    last = runs[-1]
    res = {"tool": disp, "vivado_version": (re.search(r"SR_VERSION (\S+)", console) or [None, None])[1],
           "elapsed_s": round(dt), "implemented_here": "SR_IMPLEMENTING" in console, "runs": runs}
    (out / f"{base}.summary.json").write_text(json.dumps(res, indent=1) + "\n")
    if a.contract:
        ff = last.get("first_failure") or {}
        res["contract_next"] = CONTRACT_NEXT
        res["contract"] = {
            "backend": disp, "backend_version": last.get("backend_version"),
            "stage": last["stage"], "functional_verdict": last["verdict"],
            "failure_class": last["suggested_class"].rstrip("?").split("_or_")[0],
            "failure_class_basis": "parser suggestion; confirm from the first causal message",
            "first_causal_message": ff.get("message") or last.get("launch_error"),
            "dut_modified": False, "switched_simulator": False,
            "artifacts": [str(Path(a.out) / l) for r in runs for l in r["logs"]],
            "notes": " ".join(filter(None, [
                f"{disp} {a.mode}{' timing' if timing else ''} through Vivado launch_simulation: {last['verdict']}.",
                f"First causal message ({ff.get('log')}): {ff.get('message')}" if ff else None,
                last.get("sdf_note"),
                ("SDF annotation did not complete, so any TEST_PASS is zero-delay, not a timing result."
                 if timing and not last.get("sdf_annotated") else None),
                runs[-1].get("note")])),
        }
        c = res["contract"]
        if last["verdict"] == "FAIL":
            c["tests_passed_before_failure"] = len([p for p in last.get("pass_markers") or [] if "REGRESSION" not in p])
        first = runs[0].get("first_fail")
        if first:
            c.update({"initial_verdict": "FAIL", "tests_passed_before_failure": first["tests_passed_before_failure"],
                      "first_check_fail": first["message"]})
            c["notes"] = (f"Initial FAIL ({first['log']}): {first['message']!r} at {first['time']}, after "
                          f"{first['tests_passed_before_failure']} passing test(s). Latest run: " + c["notes"])
    guide = {}
    if last.get("verdict") == "FAIL":
        guide = fail_guidance(next((out / l for l in last.get("logs", []) if l.endswith(".simulate.log")), None))
    elif last.get("verdict") == "PASS":
        cur = out / f"{base}.simulate.log"
        first = next((f for f in (out / f"{base}.first_fail.simulate.log",
                                  out / re.sub(r"_pass(\w*)$", r"_fail\1.simulate.log", base)) if f.is_file()), None)
        if first and first != cur:
            passed, quote, _ = fail_facts(first)
            guide = {"requirement": f"initial FAIL ({first.name}) violated "
                                    + requirement_hint(passed, quote, passing_order(cur)), "next": REPAIRED_NEXT}
    if rcheck:
        guide = {"repair_check": rcheck, **guide}
    nxt = {"contract_next": res.pop("contract_next")} if "contract_next" in res else {}
    res = {**nxt, **guide, **note_findings(out), **res}
    emit(out, res, a.max_print)
    return 0


def host_info() -> dict:
    osr = {}
    for line in read(Path("/etc/os-release")).splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            osr[k] = v.strip('"')
    xv = os.environ.get("XILINX_VIVADO", "")
    ver = re.search(r"(20\d\d\.\d)", xv)
    return {"os": osr.get("PRETTY_NAME", sys.platform), "platform": sys.platform,
            "vivado": ver.group(1) if ver else None, "vivado_on_path": bool(find_vivado())}


def cmd_backends(a) -> int:
    host = host_info()
    rows, res = [], {"host": host, "backends": {}}
    for b in [x.strip().lower().replace("-", "").replace("_", "") for x in a.check.split(",") if x.strip()]:
        b = {"rivierapro": "riviera", "activehdl": "activehdl", "questasim": "questa"}.get(b, b)
        if b == "xsim":
            res["backends"]["xsim"] = {"available": host["vivado_on_path"], "version": host["vivado"]}
            continue
        if b not in BACKENDS:
            res["backends"][b] = {"error": "unknown backend id"}
            continue
        disp, exes, envs, support, note = BACKENDS[b]
        found = {e: shutil.which(e) for e in exes}
        envset = {e: bool(os.environ.get(e)) for e in envs}
        missing = []
        if b == "activehdl" and not sys.platform.startswith("win"):
            missing.append(f"unsupported host: Active-HDL integration is Windows-only, this host is {host['os']}")
        if not any(found.values()):
            missing.append(f"{disp} executable not on PATH ({', '.join(exes)})")
        if not any(envset.values()):
            missing.append(f"license/install environment not set ({', '.join(envs)})")
        if b in ("vcs", "xcelium") and "Red Hat" not in host["os"] and not sys.platform.startswith("win"):
            missing.append(f"host OS {host['os']} is not in the UG973 list for {disp} ({support}); confirm with vivado_doc_search")
        ver = None
        exe = next((p for p in found.values() if p), None)
        if exe and not missing:
            try:
                ver = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=60).stdout.strip()[:120]
            except (OSError, subprocess.TimeoutExpired):
                pass
        status = "BLOCKED" if missing else "available (versions/libraries still to validate)"
        product = {"modelsim": "Siemens ModelSim DE", "riviera": "Aldec Riviera-PRO",
                   "activehdl": "Aldec Active-HDL", "questa": "Siemens Questa",
                   "vcs": "Synopsys VCS", "xcelium": "Cadence Xcelium"}[b]
        res["backends"][b] = {"name": disp, "product": product, "status": status, "missing": missing, "executables": found,
                              "env_set": envset, "ug973_host_support": support, "note": note, "version": ver}
        rows.append(f"| {disp} | {status} | {'; '.join(missing) or '-'} |")
    res["user_must_provide"] = ("site install path on PATH, license environment, version-matched compile_simlib "
                                "library dir; nothing was guessed, no simulator setting was changed, nothing was launched")
    res["sources"] = ["UG900 Supported Simulators: https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Supported-Simulators",
                      "UG973 Compatible Third-Party Tools: https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Compatible-Third-Party-Tools"]
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        md = [f"# Simulator prerequisite check", "", f"Host: {host['os']}; Vivado {host['vivado']}", "",
              "| backend | status | missing prerequisite |", "|---|---|---|", *rows, "",
              f"User must provide: {res['user_must_provide']}.", "", *[f"- {x}" for x in res["sources"]]]
        if a.matrix:
            xs = _latest_xsim(out)
            md += ["", "## Cross-simulator verdict matrix", "",
                   "Held constant: sources, defines, top, timescale, seed, runtime.", "",
                   "| simulator | verdict | evidence |", "|---|---|---|",
                   f"| XSim {host['vivado'] or ''} | {xs[0]} | {xs[1]} |"]
            for b, info in res["backends"].items():
                if b != "xsim":
                    md.append(f"| {info.get('name', b)} | UNVERIFIED | not run on this host: {'; '.join(info.get('missing') or ['not executed'])} |")
            md += ["", "Which simulator is wrong cannot be decided from one side. A result that differs between "
                   "simulators can also be a testbench race (IEEE 1800 scheduling, e.g. blocking assignments "
                   "sampled at the same clock edge); review that before calling either tool defective."]
            res["matrix_file"] = str(out / "verdict-matrix.md")
            (out / "verdict-matrix.md").write_text("\n".join(md) + "\n")
        else:
            (out / "prerequisite-report.md").write_text("\n".join(md) + "\n")
    if a.contract:
        req = next((v for v in res["backends"].values() if v.get("name")), None)
        if req:
            blocked = bool(req["missing"])
            res["contract_next"] = CONTRACT_NEXT
            res["contract"] = {
                "backend": req["name"], "backend_version": req["version"],
                "stage": "prerequisite" if blocked else "n/a",
                "functional_verdict": "BLOCKED" if blocked else "UNVERIFIED",
                "failure_class": "environment" if blocked else "none",
                "missing_prerequisite": "; ".join(req["missing"]) or None,
                "dut_modified": False, "switched_simulator": False,
                "notes": (f"{req['product']}: {'; '.join(req['missing']) or 'prerequisites present, not run'}. "
                          f"Checked PATH for {', '.join(req['executables'])} and env "
                          f"{', '.join(k for k in req['env_set'])}; UG973 host support: {req['ug973_host_support']}. "
                          f"Nothing launched, target_simulator unchanged, no other simulator substituted. "
                          f"User must provide: {res['user_must_provide'].split(';')[0]}. "
                          f"Sources: UG900 Supported Simulators, UG973 Compatible Third-Party Tools."),
            }
    host_gap = [b for b, v in res["backends"].items()
                if any("unsupported host" in m or "UG973 list" in m for m in v.get("missing") or [])]
    if host_gap:
        name = res["backends"][host_gap[0]]["name"]
        res = {"next": f'Before answering, call Vivado:vivado_doc_search with query "UG973 compatible third-party '
                       f'tools {name} supported operating systems" and cite what it returns in notes.', **res}
    print(json.dumps({**report_path(a.out), **res}, indent=1)[: a.max_print])
    return 0


def _latest_xsim(out: Path) -> tuple[str, str]:
    sums = sorted(out.glob("*.summary.json"), key=lambda p: p.stat().st_mtime)
    for p in reversed(sums):
        try:
            d = json.loads(read(p))
        except ValueError:
            continue
        for r in d.get("runs", []):
            if r.get("verdict"):
                return r["verdict"], f"{r['run']}.simulate.log ({', '.join(r.get('pass_markers', [])) or (r.get('first_failure') or {}).get('message', '')})"
    return "UNVERIFIED", "no XSim run found in this directory"


# ----------------------------------------------------------------- advise

ADVICE = {
    "bisect": ("version-bisect.md", [
        "Hold sources, defines, generics, top, seed, runtime and mode constant; only the Vivado release changes.",
        "Separate working directory per release; recompile third-party libraries (compile_simlib) per release.",
        "Compare the failure signature (message id, stage, time), not only pass/fail.",
        "Project upgrade / regenerated IP between releases is a confounder: prefer non-project sources, record IP versions.",
        "Report last-good and first-bad; last-good is the immediate fallback.",
        "Needs at least two installed releases; with one release the bisect cannot conclude (see host facts).",
        "Feed the first-bad release into documentation/Answer Record research.",
    ]),
    "reproducer": ("minimal-reproducer.md", [
        "Record the failure signature first; it is the reduction invariant.",
        "Work on a copy, never the golden project; back out any step that changes the signature.",
        "Keep an ablation record (what was removed, signature kept?) - no record, not minimal.",
        "Do not stub IP if that removes the signature; elaboration crashes need no testbench oracle.",
        "Wrong-results defects need a self-checking oracle with a terminal marker.",
        "Package: one clean-directory reproduce script, exact commands, tool version banner, host OS, complete stage logs, cross-simulator matrix.",
        "Verify by running the package in a fresh directory; do not upload or file customer sources.",
    ]),
    "performance": ("simulation-performance.md", [
        "Measure analysis (xvlog/xvhdl), elaboration (xelab) and runtime (xsim) separately; report the dominant stage and its share.",
        "Record host, CPU/threads, filesystem, peak memory, debug level, logging scope, coverage and runtime bound.",
        "Compare simulators only with identical debug, logging, coverage, optimisation, sources and host; repeat and report spread.",
        "Each speedup lever (less debug, focused logging, coverage off) stops proving something - state it.",
        "No speedup claim from one unrepeated run; never compare debug-enabled vs debug-disabled runs.",
    ]),
    "headless": ("headless-waveform.md", [
        "Log a focused object set (log_wave/log_vcd) before advancing simulation; no recursive whole-design dump.",
        "Produce a portable trace (VCD); verify each artifact exists and is non-empty before citing it.",
        "Compare baseline vs current with the same signal set, hierarchy, timescale, seed, stimulus and window.",
        "Report the first divergence (time + signal); later differences are consequences.",
        "Functional verdict stays on the transcript and terminal marker; a waveform is not proof of correctness; no GUI needed.",
    ]),
    "known-issue": ("known-issue-research.md", [
        "Classify a crash on legal source as a tool/environment problem, not a design FAIL; do not edit DUT RTL to dodge it.",
        "Search vivado_doc_search with the exact signature AND with the suspected construct (2-3 queries) before concluding.",
        "One signature (e.g. XSIM 43-3316) has several documented mechanisms; the failing construct selects which applies - isolate it with the complete elaborate log and version banner.",
        "Cite only Answer Records the search returned; check affected/fixed-in versions against the installed release.",
        "A documented workaround is UNVERIFIED until rerun on the same bounded test; state what it changes and the remaining risk.",
    ]),
    "new-backend": ("adding-a-simulator.md", [
        "Verify current support in UG900 (Supported Simulators, export_simulation) and UG973 (Compatible Third-Party Tools) for the installed release.",
        "If the docs list no newly supported simulator, say so instead of inventing one.",
        "Follow the backend contract: triggers, prerequisites, Vivado integration ids, execution, artifacts, verdict mapping, diagnostics.",
        "Add a direct backend reference plus evaluation scenarios (missing install/license, stale libs, PASS, assertion FAIL, unsupported platform).",
        "Mark combinations without an installed/licensed simulator UNVERIFIED; never claim untested platform support.",
    ]),
    "arbitration": ("cross-simulator-arbitration.md", [
        "Hold sources, defines, top, timescale, seed and runtime constant across simulators.",
        "Unavailable/unlicensed backends are UNVERIFIED cells, never passes.",
        "Do not name the wrong simulator without evidence from both; cite the LRM, a user guide or an Answer Record.",
        "Verdicts agreeing but values differing suggests a race/scheduling testbench defect.",
        "Never switch the user's backend because another one passes.",
    ]),
}


DOC_QUERY = {
    "known-issue": "XSim crash known issue answer record <exact error signature>",
    "new-backend": "UG900 supported simulators third-party simulator list",
    "arbitration": "UG900 supported simulators simulation differences between simulators",
}


def request_stage(request: str | None) -> str:
    """The stage a request names (xelab -> elaborate), else n/a."""
    for stage, pat in (("elaborate", r"\bxelab\b|elaborat"), ("compile", r"\bxvlog\b|\bxvhdl\b|\bcompil"),
                       ("simulate", r"\bxsim\b.*\b(run|runtime|hang)|simulation (run|hang)")):
        if re.search(pat, request or "", re.IGNORECASE):
            return stage
    return "n/a"


def cmd_advise(a) -> int:
    if a.topic not in ADVICE:
        print(json.dumps({"error": f"topic must be one of {sorted(ADVICE)}"}))
        return 2
    ref, points = ADVICE[a.topic]
    body = read(HERE.parent / "references" / ref)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
    host = host_info()
    lines = [f"# {a.title or a.topic}", ""]
    if a.request:
        lines += ["## Request", "", a.request, ""]
    lines += ["## Key points", "", *[f"- {p}" for p in points], "",
              "## Host facts observed", "",
              f"- OS: {host['os']}", f"- Vivado on PATH: {host['vivado'] or 'none'} (releases visible to this session: {host['vivado'] or 'none'})", ""]
    for extra in a.append or []:
        lines += [extra, ""]
    lines += ["## Method (from the skill reference)", "", re.sub(r"^#\s.*\n", "", body, count=1), ""]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    res = {"report": str(out), "bytes": out.stat().st_size, "key_points": points, "host": host}
    if a.contract:
        res["contract_next"] = CONTRACT_NEXT
        res["contract"] = {
            "backend": a.backend, "backend_version": host["vivado"] if a.backend == "XSim" else None,
            "stage": a.stage or request_stage(a.request), "functional_verdict": "UNVERIFIED", "failure_class": a.failure_class,
            "dut_modified": False, "switched_simulator": False,
            "notes": (f"Method written to {out}; not executed on a design here. "
                      + " ".join(points[:4]) + f" Host: {host['os']}, Vivado {host['vivado'] or 'not on PATH'}."),
        }
    query = DOC_QUERY.get(a.topic)
    if query and a.topic == "known-issue" and a.request:
        query = f"{a.request[:150]} known issue answer record"
    done = (f"{out} is the complete deliverable: do not rewrite, replace or expand it with Write/Edit (one large "
            f"write overruns the output limit and kills the session); to add one short finding rerun this command "
            f"with `--append \"<finding>\"`. "
            f"Answer in a few lines and end with the contract.")
    res = {"next": (f'Call Vivado:vivado_doc_search once, with query "{query}" and limit 3, and cite what it '
                    f'returns in notes; do not repeat the search or reread the report. '
                    if query else "") + done, **res}
    print(json.dumps(res, indent=1))
    return 0


FAIL_LOG = re.compile(r"^(.+?)(?:\.first_fail|_fail\w*)\.simulate\.log$")


CONTRACT_NEXT = ("end the final message with the contract as a JSON block, notes filled in, "
                 "also when the message stops at a question or approval gate")
NO_MARKER_NEXT = ("no pass marker and no $finish: this run proves nothing yet; end the top with "
                  '$display("TEST_PASS"); $finish; and rerun before using it as a PASS or a control')
FINDINGS_NEXT = "final report: paste findings.md into the message, one section per target, filling each Requirement line"
FAIL_GATE_NEXT = ("when the message stops here to ask for repair approval, it must still end with the contract JSON "
                  "block (dut_modified false) carrying the repair / more evidence / stop question in notes; a question "
                  "with no JSON block is an incomplete answer. Before approval do not simulate a repaired copy of "
                  "the DUT either: the proposed repair stays unverified in notes, and a pass marker from a fixed copy "
                  "under outputs/ reads as a false pass")
COV_BASELINE_NEXT = ("baseline captured. For closure write only the new self-checking testbench file, then rerun this "
                     "exact command (same --out and --xpr) with `--tb <new_tb>.sv` added: it keeps this baseline report "
                     "as cov-<name>.prev1-report, runs the new test in its own simset with text+HTML reports, and merges "
                     "only passing runs. Do not create another project or out folder, and do not run xcrg, "
                     "export_xsim_coverage or merge scripts yourself")
COV_MERGED_NEXT = ("closure done: the baseline and final text+HTML reports below already satisfy 'keep reports'; "
                   "answer now with the targeted bin hit counts (baseline vs final) from this result and the contract. "
                   "Write no summary, manifest or report files and do not rerun")


REPAIR_RULE = ("change exactly one line; if the same term is missing at every use of a signal, edit only the "
               "line that defines the signal (its `wire ... =` or assign), not each use; never add a new wire, "
               "reg or signal")
REPAIRED_NEXT = ("repair verified: summarise it and stop; in a guided or step-by-step session ask before "
                 "launching the next simset, and do not launch it in this turn")


def fail_facts(log: Path) -> tuple[list[str], str | None, dict]:
    """Tests passed before the failure, the verbatim CHECK_FAIL line, and the parsed first failure."""
    text = "\n".join(re.sub(r"^#\s?", "", l) for l in read(log).splitlines())
    tr = parse_transcript(text)
    ff = tr.get("first_failure") or {}
    check = next((c for c in (re.search(r"CHECK_FAIL\b.*", l) for l in text.splitlines()) if c), None)
    passed = [s.split()[-1] for s in tr["pass_markers"] if "REGRESSION" not in s]
    return passed, (check[0].strip() if check else ff.get("message")), ff


def passing_order(log: Path) -> list[str]:
    """TEST_PASS names, in order, from a passing rerun of the same target."""
    if not log.is_file():
        return []
    tr = parse_transcript(re.sub(r"(?m)^# ?", "", read(log)))
    return [s.split()[-1] for s in tr["pass_markers"] if "REGRESSION" not in s]


def companion_pass_log(fail_log: Path) -> Path:
    name = fail_log.name
    if ".first_fail." in name:
        return fail_log.with_name(name.replace(".first_fail.", "."))
    return fail_log.with_name(re.sub(r"_fail(\w*)\.simulate\.log$", r"_pass\1.simulate.log", name))


def requirement_hint(passed: list[str], quote: str | None, order: list[str] | None = None) -> str:
    if not passed:
        return ("the reset requirement: the check failed before any test passed, so it is reset whatever "
                "property the message names")
    if quote and re.search(r"reference model|\bmodel\b", quote, re.IGNORECASE):
        running = order[len(passed)] if order and len(order) > len(passed) and order[:len(passed)] == passed else None
        test = f"the `{running}` test" if running else f"test {len(passed) + 1} (the one after `{passed[-1]}`)"
        return (f"the requirement {test} checks ({len(passed)} test(s) passed, so requirement {len(passed) + 1} when "
                f"the tests follow the numbered requirements); a reference-model mismatch belongs to the running test, "
                f"whatever the model is named, so do not pick the requirement whose words appear in the "
                f"model's name ({quote!r})")
    return f"the requirement whose property the check names: {quote!r}"


def fail_guidance(log: Path | None) -> dict:
    if not log or not log.is_file():
        return {}
    passed, quote, _ = fail_facts(log)
    return {"requirement": requirement_hint(passed, quote), "repair": REPAIR_RULE, "next": FAIL_GATE_NEXT}


def write_findings(out: Path, min_rows: int = 1) -> list[dict]:
    """One report section per target, quoting each saved FAIL log's first check verbatim."""
    logs: dict[str, Path] = {}
    for p in sorted(out.rglob("*.simulate.log")):
        m = FAIL_LOG.match(p.name)
        target = m and re.sub(r"_fail\w*$", "", m[1])
        if target and (target not in logs or ".first_fail." in p.name):
            logs[target] = p
    md, rows = [], []
    for target, p in sorted(logs.items()):
        passed, quote, ff = fail_facts(p)
        if not quote:
            continue
        hint = requirement_hint(passed, quote, passing_order(companion_pass_log(p)))
        md += [f"### {target}", "", f"Requirement N: <title from requirements.md> (exactly one: {hint})",
               f"First failing check (verbatim, {_rel(p)}): `{quote}`" + (f" at {ff['time']}" if ff.get("time") else ""),
               f"Tests passed before the failure: {len(passed)} ({', '.join(passed) or 'none'})", ""]
        rows.append({"target": target, "log": _rel(p), "first_check_fail": quote, "time": ff.get("time"),
                     "tests_passed_before_failure": len(passed)})
    if len(rows) >= min_rows:
        (out / "findings.md").write_text("\n".join(md))
    return rows


def note_findings(out: Path) -> dict:
    """After a run: once the case has several targets, refresh findings.md and keys pointing at it."""
    rows = write_findings(out, min_rows=2)
    return {"next": FINDINGS_NEXT, "findings": _rel(out / "findings.md")} if len(rows) > 1 else {}


def cmd_findings(a) -> int:
    out = Path(a.out)
    rows = write_findings(out)
    if not rows:
        print(json.dumps({"error": f"no <target>_fail*.simulate.log or <target>.first_fail.simulate.log under {out}"}))
        return 2
    print(json.dumps({"findings": _rel(out / "findings.md"), "targets": rows, "next": FINDINGS_NEXT},
                     indent=1)[: a.max_print])
    return 0


MODULE_DEF = re.compile(r"(?m)^\s*(?:module|program)\s+(?:automatic\s+|static\s+)?(\w+)")
# top not found, instantiated module not found: the harness is incomplete
HARNESS_ERR = re.compile(r"XSIM 43-3225|VRFC 10-2063")


def harness_facts(srcs: list[Path]) -> dict:
    """Module definitions per file: duplicates, and which ones call $finish."""
    defs: dict[str, list[str]] = {}
    finish: list[str] = []
    for s in srcs:
        txt = re.sub(r"//[^\n]*|/\*.*?\*/", "", read(s), flags=re.S)
        parts = MODULE_DEF.split(txt)
        for name, body in zip(parts[1::2], parts[2::2]):
            defs.setdefault(name, []).append(s.name)
            if "$finish" in body.split("endmodule")[0]:
                finish.append(name)
    return {"defs": defs, "finish": finish,
            "dups": {m: f for m, f in defs.items() if len(f) > 1}}


def control_outcome(top: str, hf: dict, verdict: str, stages: dict, tr: dict | None, files: list[str]) -> tuple[str, str | None]:
    """(control, next): is this run evidence about XSim, or a broken harness?"""
    harness = "HARNESS_ERROR (not evidence about XSim; fix the harness and rerun): "
    rerun = f"S xsim-files {' '.join(files)} --top <tb> --out <same>"
    if hf["dups"]:
        m, f = next(iter(hf["dups"].items()))
        return harness + f"module {m} is defined in {', '.join(f)}", f"pass each module once: {rerun}"
    err = next((v for v in stages.values() if v), None)
    if err and HARNESS_ERR.search(err["tag"] + " " + err["message"]):
        missing = re.search(r"Module <(\w+)> not found", err["message"])
        if missing:
            return harness + err["message"][:200], (f"define module {missing.group(1)} in the reduced file "
                                                    f"(or pass the file that defines it), then rerun")
        cand = [m for m in hf["finish"]] or list(hf["defs"])
        return harness + err["message"][:200], f"top must be a module that is in these files and ends $finish; candidates: {', '.join(cand[:6])}; {rerun}"
    if err:
        return f"XSIM_REJECTS: {err['message'][:200]} (check it is the construct, not the harness, before reporting)", None
    if verdict == "FAIL":
        return f"FAIL at simulate: {((tr or {}).get('first_failure') or {}).get('message', '')[:200]}", None
    if not (tr or {}).get("finish_time"):
        why = f"{top} has no $finish" if top not in hf["finish"] else "simulation stopped before $finish"
        return harness + why, f"end {top}'s initial block with $display(\"TEST_PASS\"); $finish; then rerun the same command"
    return "COMPLETE: XSim compiled, elaborated and reached $finish with no errors", None


CONTROL_READING = (
    "differential evidence only: XSim accepting a construct does not show it is legal or that another "
    "simulator is wrong. A legality claim needs a source opened in this run (doc-search hit, user guide, "
    "Answer Record), quoted; LRM section numbers from memory are not evidence. Without one report "
    "evidence_class narrowed_only and not failure_class tool_defect, and do "
    "not write that the construct is valid, the code is correct, or which release fixes it. Use "
    "contract_draft as the final JSON: fill the <...> field in its notes, add what you checked, and keep "
    "its 'not re-run here' sentence.")


def control_notes(out: Path, res: dict) -> str | None:
    """Draft notes for a COMPLETE control against the failure `S logs` recorded."""
    tp = recorded_failure(out)
    if not tp:
        return None
    name, exes = BACKENDS.get(tp["backend"], (tp["backend"], []))[:2]
    found = [e for e in exes if shutil.which(e)]
    rerun = (f"{name} was not re-run here: {', '.join(exes)} not on PATH" if not found
             else f"{name} ({found[0]}) is on PATH but this control did not run it")
    # quote the error with file names only: no customer-host absolute paths
    err = re.sub(r"(?:/[^\s,()/]+)+/([^\s,()/]+)", r"\1", tp.get("first_error") or "")[:200].rstrip(". ")
    return (f"{name} {tp.get('version') or ''} {tp['stage']} fails ({tp.get('error_lines') or '?'} error lines, "
            f"{tp['log']}): {err}. XSim {res.get('backend_version') or ''} control "
            f"{res['logs'].get('simulate')} reached $finish with no errors, so XSim accepts the reduced construct; "
            f"that is differential evidence, not proof that the code is legal or that {name} is wrong. {rerun}. "
            f"Legality: <quote from a source opened in this run, or 'not established here'>. "
            f"No workaround is verified.")


def recorded_failure(out: Path) -> dict | None:
    return next((json.loads(read(d / "logs.json")).get("third_party") for d in (out, out.parent, out.parent.parent)
                 if (d / "logs.json").is_file()), None)


def control_contract(out: Path, res: dict, notes: str, files: list[str]) -> dict:
    """Final-JSON draft for a recorded third-party failure plus a COMPLETE XSim
    control: the failing backend's facts, the control, and the evidence class
    the control alone supports (narrowed_only until a named source shows the
    construct is legal)."""
    tp = recorded_failure(out) or {}
    name, exes = BACKENDS.get(tp.get("backend"), (tp.get("backend"), []))[:2]
    ran_here = any(shutil.which(e) for e in exes)
    case = next((d for d in (out, out.parent, out.parent.parent) if (d / "logs.json").is_file()), out)
    return {
        "backend": name, "backend_version": tp.get("version"), "stage": tp.get("stage"),
        "functional_verdict": "UNVERIFIED" if ran_here else "BLOCKED",
        "failure_class": None,
        "evidence_class": "narrowed_only",
        "classification_basis": "tool_defect / simulator_defect only with a source opened in this run (doc-search "
                                "hit, reference file) that makes the construct legal, quoted in notes; section "
                                "numbers recalled from memory are not evidence",
        "first_causal_message": tp.get("first_error"),
        "controls": [f"XSim {res.get('backend_version') or ''} xsim-files on {', '.join(_rel(Path(f)) for f in files)}: "
                     f"COMPLETE, $finish at {res.get('finish_time')} with no errors ({res['logs'].get('simulate')})"],
        "dut_modified": False, "switched_simulator": False,
        "artifacts": [tp.get("log"), _rel(case / "log-matrix.md"), *(_rel(Path(f)) for f in files),
                      *res["logs"].values()],
        "notes": notes}


def cmd_xsim_files(a) -> int:
    if not shutil.which("xvlog"):
        print(json.dumps({"error": "xvlog not on PATH; source the Vivado settings"}))
        return 2
    out = Path(a.out)
    work = out / f"{a.name}.xsim"
    work.mkdir(parents=True, exist_ok=True)
    srcs = [Path(f).resolve() for f in a.files]
    sv = [str(s) for s in srcs if s.suffix.lower() in (".sv", ".v", ".svh")]
    vhd = [str(s) for s in srcs if s.suffix.lower() in (".vhd", ".vhdl")]
    hf = harness_facts([s for s in srcs if str(s) in sv])
    # default top: the one module that calls $finish, else the last file's stem
    top = a.top or (hf["finish"][0] if len(hf["finish"]) == 1 else srcs[-1].stem)
    plan = ([("compile", ["xvlog", "--sv", *sv])] if sv else []) + \
           ([("compile_vhdl", ["xvhdl", *([] if a.vhdl93 else ["--2008"]), *vhd])] if vhd else []) + \
           [("elaborate", ["xelab", top, "-s", f"{a.name}_snap", "-debug", "typical", *(a.elab_opt or [])]),
            ("simulate", ["xsim", f"{a.name}_snap", "-R"])]
    stages, logs, tr = {}, {}, None
    for st, cmd in plan:
        try:
            p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=a.timeout)
            text, rc = p.stdout + p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            text, rc = (e.stdout or "") if isinstance(e.stdout, str) else "", "timeout"
        log = out / f"{a.name}.{st}.log"
        log.write_text(f"# {' '.join(cmd)}\n{text}")
        logs[st] = _rel(log)
        if st == "simulate":
            tr = parse_transcript(text)
        else:
            stages[st.split("_")[0]] = stages.get(st.split("_")[0]) or parse_tool_log(text)
        if rc != 0 and st != "simulate":
            break
    verdict, stage, klass = verdict_of(stages, tr, a.pass_marker)
    control, nxt = control_outcome(top, hf, verdict, stages, tr, a.files)
    ver = re.search(r"xsim v(20\d\d\.\d(?:\.\d)?)", read(Path(logs.get("simulate", ""))) if "simulate" in logs else "")
    res = {"control": control, **({"next": nxt} if nxt else {}),
           **({"reading": CONTROL_READING} if control.startswith("COMPLETE") else {}),
           "backend": "XSim", "backend_version": ver.group(1) if ver else None, "top": top,
           "verdict": verdict, "stage": stage, "suggested_class": klass,
           "first_error": next((v for v in stages.values() if v), None) or (tr or {}).get("first_failure"),
           "finish_time": (tr or {}).get("finish_time"), "pass_markers": (tr or {}).get("pass_markers"),
           "logs": logs}
    if control.startswith("COMPLETE"):
        draft = control_notes(out, res)
        if draft:
            res = {"control": control, "reading": CONTROL_READING,
                   "contract_draft": control_contract(out, res, draft, a.files),
                   **{k: v for k, v in res.items() if k not in ("control", "reading")}}
    (out / f"{a.name}.summary.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({**report_path(out), **res}, separators=(",", ":"))[: a.max_print])
    return 3 if control.startswith("HARNESS_ERROR") else 0


SIP_ENTRY = re.compile(r"^-f\s+(\S+)\s*//\s*(.*)$")


def secureip_filters(vivado: Path) -> list[dict]:
    rows = []
    for lst in ("secureip_cell.list.f", "secureip_sv_cell.list.f"):
        p = vivado / "data" / "secureip" / lst
        for n, line in enumerate(read(p).splitlines(), 1) if p.is_file() else ():
            m = SIP_ENTRY.match(line.strip())
            if not m:
                continue
            kv = dict(f.split("=", 1) for f in m.group(2).split(":") if "=" in f)
            cell = Path(m.group(1)).parent.name
            lib = next((k for k in ("unisims", "unifast") if k in kv), None)
            rows.append({"cell": cell, "model": kv.get(lib, ""), "arch": kv.get("arch", "").split(","),
                         "list": f"data/secureip/{lst}:{n}", "line": line.strip()})
    return rows


def cmd_simlib(a) -> int:
    viv = Path(os.environ.get("XILINX_VIVADO") or "")
    rows = secureip_filters(viv) if viv.name else []
    if not rows:
        print(json.dumps({"error": "no data/secureip/*cell.list.f under $XILINX_VIVADO; source the Vivado settings"}))
        return 2
    norm = lambda s: re.sub(r"^sip_", "", s.strip().lower())
    want = [norm(c) for c in (a.cells or "").split(",") if c.strip()]
    hits = [r for r in rows if r["cell"] in want or r["model"].lower() in want]
    compare = a.compare or next((f for r in hits for f in r["arch"] if f != a.family), None)
    cells = [{"cell": r["cell"], "model_field": r["model"], "arch": r["arch"],
              f"in_{a.family}": a.family in r["arch"],
              **({f"in_{compare}": compare in r["arch"]} if compare else {}),
              "list": r["list"],
              **({"metadata_mismatch": f"filter line names model {r['model']} for cell {r['cell']}"}
                 if r["model"] and norm(r["model"]) != r["cell"] else {})} for r in hits]
    skipped = sorted({r["cell"] for r in rows if compare in r["arch"] and a.family not in r["arch"]})
    gts = [c.upper() for c in skipped if re.match(r"gt\w*_(channel|common)$", c)]
    also = {"also_skipped_used_by_ip": gts, "answer_must_name": [f"SIP_{h['cell'].upper()}" for h in hits] + gts,
            "why": "PCIe/serial IP also instantiates its GT channel/common transceivers; a family that skips "
                   "these breaks the same elaboration, so name each one with the control"} if gts and hits else {}
    res = {**also,"vivado": str(viv), "family": a.family, "compare": compare,
           "filter_files": ["data/secureip/secureip_cell.list.f", "data/secureip/secureip_sv_cell.list.f"],
           "cells": cells, "not_found": [c for c in want if not any(c in (h["cell"], h["model"].lower()) for h in hits)],
           f"built_for_{compare}_not_{a.family}": skipped,
           "meaning": (f"compile_simlib -family {a.family} builds only SecureIP models whose arch= filter lists "
                       f"{a.family}; each name in built_for_{compare}_not_{a.family} is skipped, so a design that "
                       f"instantiates one fails elaboration with SIP_<CELL> undefined.") if compare else None,
           "control": (f"compile_simlib -family {compare} -library secureip into a separate directory and map that "
                       f"secureip library; do not add plaintext SIP sources. Not verified until the design elaborates.")
           if compare else None}
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        md = [f"# SecureIP family filters: {a.family} vs {compare}", "", f"Vivado: `{viv}`", "",
              "## Requested cells", "", *[f"- `{r['list']}`: `{r['line']}`" for r in hits], "",
              f"## Built for {compare} but not {a.family}", "", *[f"- {c}" for c in skipped], ""]
        (out / "secureip-filters.md").write_text("\n".join(md))
        (out / "secureip-filters.json").write_text(json.dumps(res, indent=1) + "\n")
        res["written"] = [str(out / "secureip-filters.md"), str(out / "secureip-filters.json")]
    print(json.dumps(res, indent=1)[: a.max_print])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sim", help="build/launch/parse one or more XSim runs in one Vivado batch")
    s.add_argument("--no-mcp", action="store_true", help="host has no Vivado MCP tools: skip the MCP build-step check")
    s.add_argument("--out", required=True, help="evidence directory, e.g. outputs/<case_id>")
    s.add_argument("--xpr", help="existing Vivado project")
    s.add_argument("--fixture-tcl", help="project generator Tcl (uses ::EVAL_FIXTURE/::EVAL_OUTPUT_DIR)")
    s.add_argument("--fixture", help="fixture name passed to the generator")
    s.add_argument("--project-dir", help="project dir name under --out (default: fixture name)")
    s.add_argument("--simset", default="sim_1")
    s.add_argument("--name", help="run name prefix (default: simset or fixture)")
    s.add_argument("--mode", default="behavioral", choices=["behavioral", "post-synthesis", "post-implementation"])
    s.add_argument("--type", choices=["functional", "timing"])
    s.add_argument("--runtime", default="100us", help="bound for the run (testbench $finish ends earlier)")
    s.add_argument("--seeds", help="comma list; one run per seed, each with its own manifest")
    s.add_argument("--replay-failures", action="store_true", help="re-run each failing seed once and compare")
    s.add_argument("--plusarg", action="append", help="KEY=VALUE passed as -testplusarg")
    s.add_argument("--uvm-test", help="UVM test name (+UVM_TESTNAME, integrated UVM 1.2 via -L uvm)")
    s.add_argument("--assert-pass", action="store_true", help="xelab -report_assertion_pass")
    s.add_argument("--cov", action="store_true", help="code+functional coverage db and text/HTML report per run; merged report of passing runs")
    s.add_argument("--cov-type", default="sbct")
    s.add_argument("--tb", action="append", help="extra testbench file: added in its own simset sim_<top> and run")
    s.add_argument("--tb-top", help="top module of --tb (default: file stem)")
    s.add_argument("--wave", help="space-separated objects (relative to tb top) to log; default: all top-level tb signals")
    s.add_argument("--wcfg", help="save a wave config with the logged objects as <out>/<NAME>.wcfg")
    s.add_argument("--trace", action="store_true", help="also trace logged objects to VCD and write <run>.values.md (values at clock edges before first failure)")
    s.add_argument("--trace-rows", type=int, default=8)
    s.add_argument("--clock", help="clock signal name for --trace (default: first *clk*)")
    s.add_argument("--saif", help="scope for SAIF, e.g. /tb/dut (needs --window)")
    s.add_argument("--vcd", help="space-separated objects for a scoped VCD (needs --window)")
    s.add_argument("--window", help="START:END, e.g. 100ns:240ns")
    s.add_argument("--ports", help="instance for generate_vcd_ports, then create_testbench into sim_replay")
    s.add_argument("--expect", action="append", help="with --ports: PORT=VALUE checked at the end of the replay")
    s.add_argument("--replay-marker", help="with --ports: print <MARKER>=<first expected port value> before TEST_PASS "
                                             "(for FOO_SUM=17 pass FOO_SUM with --expect sum=17)")
    s.add_argument("--onfinish-stop", action="store_true")
    s.add_argument("--debug-typical", action="store_true")
    s.add_argument("--manifest", action="store_true", help="write <run>.manifest.json for every run")
    s.add_argument("--pass-marker", help="terminal marker required for PASS (default: any TEST_PASS*/REGRESSION_PASS)")
    s.add_argument("--timeout", type=int, default=1500)
    s.add_argument("--max-print", type=int, default=4000)
    s.add_argument("--contract", action="store_true", help="also print a draft report-contract object")
    s.set_defaults(func=cmd_sim)
    r = sub.add_parser("run", help="Questa/ModelSim/VCS/Xcelium/Riviera run through Vivado launch_simulation")
    r.add_argument("--no-mcp", action="store_true", help="host has no Vivado MCP tools: skip the MCP build-step check")
    r.add_argument("--backend", required=True, help="questa|modelsim|vcs|xcelium|riviera")
    r.add_argument("--xpr", required=True, help="existing Vivado project (.xpr or its directory)")
    r.add_argument("--out", required=True, help="evidence directory, e.g. outputs/<case_id>")
    r.add_argument("--simset", default="sim_1")
    r.add_argument("--name", help="run name (default: <simset>-<backend>)")
    r.add_argument("--mode", default="behavioral", choices=["behavioral", "post-synthesis", "post-implementation"])
    r.add_argument("--type", choices=["functional", "timing"])
    r.add_argument("--timeout", type=int, default=2400)
    r.add_argument("--max-print", type=int, default=4000)
    r.add_argument("--contract", action="store_true", help="also print a draft report-contract object")
    r.set_defaults(func=cmd_run)
    l = sub.add_parser("logs", help="summarize existing logs (first error, crash, markers, value lines)")
    l.add_argument("paths", nargs="+")
    l.add_argument("--limit", type=int, default=200)
    l.add_argument("--out", help="also write <out>/log-matrix.md")
    l.add_argument("--values", type=int, default=6)
    l.add_argument("--max-print", type=int, default=5000)
    l.add_argument("--contract", action="store_true", help="note where the final-JSON draft comes from")
    l.set_defaults(func=cmd_logs)
    b = sub.add_parser("backends", help="check third-party simulator prerequisites without launching anything")
    b.add_argument("--check", required=True, help="comma list: questa,modelsim,vcs,xcelium,riviera,activehdl,xsim")
    b.add_argument("--out", help="write prerequisite-report.md (or verdict-matrix.md with --matrix) here")
    b.add_argument("--matrix", action="store_true", help="cross-simulator verdict matrix using the XSim summary in --out")
    b.add_argument("--max-print", type=int, default=4000)
    b.add_argument("--contract", action="store_true", help="also print the prerequisite-stop contract for the first backend")
    b.set_defaults(func=cmd_backends)
    ad = sub.add_parser("advise", help="write a methodology report.md from the skill reference; prints key points")
    ad.add_argument("topic", help="bisect|reproducer|performance|headless|known-issue|new-backend|arbitration")
    ad.add_argument("--out", required=True, help="report path, e.g. outputs/<case>/report.md")
    ad.add_argument("--title")
    ad.add_argument("--request", help="the user's request, quoted into the report")
    ad.add_argument("--append", action="append", help="extra markdown section (e.g. doc-search findings)")
    ad.add_argument("--contract", action="store_true", help="also print a draft report-contract object")
    ad.add_argument("--backend", help="contract backend, e.g. XSim")
    ad.add_argument("--stage", help="contract stage, e.g. elaborate")
    ad.add_argument("--failure-class", help="contract failure_class, e.g. tool_defect")
    ad.set_defaults(func=cmd_advise)
    fd = sub.add_parser("findings", help="report sections quoting each saved FAIL log's first check (multi-target reports)")
    fd.add_argument("--out", required=True, help="evidence directory holding the FAIL logs, e.g. outputs/<case>")
    fd.add_argument("--max-print", type=int, default=4000)
    fd.set_defaults(func=cmd_findings)
    xf = sub.add_parser("xsim-files", help="xvlog/xelab/xsim -R on a few sources without a project (differential control)")
    xf.add_argument("files", nargs="+", help="sources; the top (last file or --top) should end with $finish")
    xf.add_argument("--out", required=True, help="evidence directory, e.g. outputs/<case_id>")
    xf.add_argument("--name", default="xsim_control", help="log prefix: <name>.compile/elaborate/simulate.log")
    xf.add_argument("--top", help="top module (default: stem of the last file)")
    xf.add_argument("--elab-opt", action="append", help="extra xelab option, e.g. --elab-opt=--relax")
    xf.add_argument("--pass-marker")
    xf.add_argument("--vhdl93", action="store_true", help="compile VHDL without --2008 (default is VHDL-2008)")
    xf.add_argument("--timeout", type=int, default=300)
    xf.add_argument("--max-print", type=int, default=5000)
    xf.add_argument("--contract", action="store_true", help="accepted; a COMPLETE control after S logs always prints contract_draft")
    xf.set_defaults(func=cmd_xsim_files)
    sl = sub.add_parser("simlib-cells", help="which SecureIP models compile_simlib builds per family (installed filters)")
    sl.add_argument("--family", required=True, help="compile_simlib -family value, e.g. artixuplus")
    sl.add_argument("--cells", help="comma list of missing cells, e.g. SIP_PCIE4CE4")
    sl.add_argument("--compare", help="family to compare against (default: first other family that builds the cell)")
    sl.add_argument("--out", help="write secureip-filters.md/.json here")
    sl.add_argument("--max-print", type=int, default=4000)
    sl.set_defaults(func=cmd_simlib)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
