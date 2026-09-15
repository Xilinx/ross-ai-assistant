<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TCL Reference — RTL Lint Skill

Reference TCL blocks for each workflow step. Execute all commands through the
`vivadoExecute` tool — **never** create `.tcl` script files.

---

## Step 1: Detect Project Mode

```tcl
set project_path [pwd]
# Use $ARGUMENTS if agent was given a project path argument
# e.g.: set project_path "$ARGUMENTS"

set xpr_files [glob -nocomplain *.xpr]

if {[llength $xpr_files] > 0} {
    # PROJECT MODE
    set project_file [lindex $xpr_files 0]
    open_project $project_file
    set part_number [get_property part [current_project]]
    set project_dir [get_property DIRECTORY [current_project]]

    # Honor the project's configured top module first
    set top_module [get_property top [current_fileset]]
    if {$top_module eq ""} {
        # Top not set in project — fall back to find_top for candidates
        # Syntax per UG835: find_top [-fileset arg] [-files args] [-quiet] [-verbose]
        set all_tops [find_top]
        set top_module [lindex $all_tops 0]
        if {[llength $all_tops] > 1} {
            puts "WARNING: Multiple top-module candidates detected: $all_tops"
            puts "  Auto-selected: $top_module"
            puts "  If this is wrong, re-run with: synth_design -top <correct_module>"
            # Agent: STOP and ask the user which module is the intended top.
        } elseif {$top_module eq ""} {
            error "Could not auto-detect top module. Please specify the top module name."
        }
    }
    puts "PROJECT mode: $project_file | Top: $top_module | Part: $part_number"
} else {
    # NON-PROJECT MODE
    set rtl_files [glob -nocomplain *.v *.sv *.vhd *.vhdl]
    if {[llength $rtl_files] == 0} {
        error "No RTL files found in workspace"
    }
    set part_number "xc7k70tfbg676-2"
    set project_dir [pwd]
    puts "NON-PROJECT mode (default part: $part_number) | [llength $rtl_files] RTL files"

    # Collect include directories from header files (.vh, .svh, .h)
    # Search recursively — headers often live in include/ or rtl/ subdirs
    set hdr_files [glob -nocomplain -type f *.vh *.svh *.h \
                   {*}/*{.vh,.svh,.h} {*}/*/*{.vh,.svh,.h}]
    set inc_dirs [list [pwd]]  ;# Always include cwd
    foreach hdr $hdr_files {
        set d [file dirname [file normalize $hdr]]
        if {$d ni $inc_dirs} { lappend inc_dirs $d }
    }
    puts "  Include dirs: $inc_dirs"

    # Read source files — headers (.vh/.svh/.h) are NOT read directly;
    # they are resolved via `include directives + include_dirs property
    # Syntax per UG835: read_verilog [-sv] files, read_vhdl files
    foreach rtl_file $rtl_files {
        switch [file extension $rtl_file] {
            .v       { read_verilog $rtl_file }
            .sv      { read_verilog -sv $rtl_file }
            .vhd - .vhdl { read_vhdl $rtl_file }
        }
    }

    # Set include search path so `include "defs.svh" resolves correctly
    set_property include_dirs [list {*}$inc_dirs] [current_fileset]

    # Auto-detect compile order and top module
    update_compile_order -fileset sources_1

    # find_top returns a rank-ordered list of top-module candidates
    # (uninstantiated modules). Index 0 is the best candidate.
    set all_tops [find_top]
    set top_module [lindex $all_tops 0]
    if {[llength $all_tops] > 1} {
        puts "WARNING: Multiple top-module candidates detected: $all_tops"
        puts "  Auto-selected: $top_module"
        puts "  If this is wrong, re-run with: synth_design -top <correct_module>"
        # Agent: STOP and ask the user which module is the intended top.
    } elseif {$top_module eq ""} {
        error "Could not auto-detect top module. Please specify the top module name."
    }
    puts "  Top module: $top_module"
}
```

---

## Step 2: Create Report Directory

```tcl
set report_dir "${project_dir}/vivado_agentic_ai_reports/rtl-lint"
file mkdir $report_dir

# Check Vivado version to determine report format
# version -short returns 4-digit year format: e.g. "2026.1.0"
# Use split instead of regexp — backslash in \. gets consumed by MCP transport
set vivado_version [version -short]
set _ver_parts [split $vivado_version "."]
set major [lindex $_ver_parts 0]
set minor [lindex $_ver_parts 1]
if {$major > 2026 || ($major == 2026 && $minor >= 1)} {
    set lint_csv_format true
} else {
    set lint_csv_format false
}

if {$lint_csv_format} {
    set lint_report_file "${report_dir}/linter.csv"
} else {
    set lint_report_file "${report_dir}/linter.rpt"
}
set markdown_report_file "${report_dir}/rtl_lint_report.md"
set_param synth.elaboration.rodinMoreOptions "rt::set_parameter linterCsvFile true"
puts "Vivado version: $vivado_version | CSV format: $lint_csv_format"
puts "Report directory: $report_dir"
```

---

## Step 3: Run RTL Linter

```tcl
synth_design -top $top_module -part $part_number -lint -file $lint_report_file
puts "RTL lint analysis complete — Report: $lint_report_file"
```

### Verify report was created

```tcl
if {![file exists $lint_report_file]} {
    error "CRITICAL: $lint_report_file was NOT created! Ensure -file option was used."
}
set file_size [file size $lint_report_file]
if {$file_size == 0} {
    error "CRITICAL: $lint_report_file is empty (0 bytes)!"
}
puts "Verification passed: $lint_report_file created ([expr {$file_size / 1024.0}] KB)"
```

---

## Step 4: Parse Report to CSV

For Vivado ≥ 26.1, `$lint_report_file` is already a headerless CSV (`linter.csv`).
For older versions, run [parse_lint_report.py](parse_lint_report.py) to convert the
`.rpt` file into the same CSV format.

```tcl
if {$lint_csv_format} {
    # Vivado >= 26.1: lint report is already a CSV
    set lint_csv_file $lint_report_file
} else {
    # Older Vivado: convert .rpt to 7-column CSV via Python
    # Run parse_lint_report.py (co-located with SKILL.md) to produce the CSV
    set lint_csv_file "${report_dir}/linter.csv"
    exec python3 parse_lint_report.py $lint_report_file $lint_csv_file
}
puts "Lint CSV ready: $lint_csv_file"
```
