# TCL Reference — RTL Elaboration Analysis

Reference TCL blocks for MCP mode workflow. Execute all commands through the
`vivadoExecute` tool — **never** create `.tcl` script files.

---

## Step 1: Detect Project Mode

```tcl
set project_path [pwd]

set xpr_files [glob -nocomplain *.xpr]

if {[llength $xpr_files] > 0} {
    # PROJECT MODE
    set project_file [lindex $xpr_files 0]
    open_project $project_file
    set part_number [get_property part [current_project]]
    set project_dir [get_property DIRECTORY [current_project]]
    set top_module [get_property top [current_fileset]]
    if {$top_module eq ""} {
        set all_tops [find_top]
        set top_module [lindex $all_tops 0]
        if {[llength $all_tops] > 1} {
            puts "WARNING: Multiple top-module candidates: $all_tops"
            puts "  Auto-selected: $top_module"
        } elseif {$top_module eq ""} {
            error "Could not auto-detect top module."
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

    # Read files with correct language flags
    foreach f [glob -nocomplain *.sv] { read_verilog -sv $f }
    foreach f [glob -nocomplain *.v]  { read_verilog $f }
    foreach f [glob -nocomplain *.vhd *.vhdl] { read_vhdl $f }

    set all_tops [find_top]
    set top_module [lindex $all_tops 0]
    puts "NON-PROJECT mode | Top: $top_module | Part: $part_number"
}
```

---

## Step 2: Run RTL Elaboration

```tcl
# Create report directory
file mkdir vivado_agentic_ai_reports/rtl-elaboration-analysis

# Run elaboration only (-rtl) — much faster than full synthesis
# This triggers all Verific front-end messages without running
# synthesis optimization/mapping phases
catch {synth_design -top $top_module -part $part_number -rtl -name rtl_1} elab_result
puts "synth_design -rtl result: $elab_result"

# Optional speed-up switches:
#   -rtl_skip_ip          Skip loading OOC IP DCPs (uses stubs)
#   -rtl_skip_constraints  Skip loading XDC constraints
#   -rtl_skip_mlo          Skip MLO processing

# The elaboration messages are in the Vivado log
# For non-project mode: vivado.log in cwd
# For project mode: vivado.log (since -rtl doesn't use launch_runs)
```

---

## Step 3: Locate Log File

```tcl
# synth_design -rtl writes to vivado.log in cwd (both project and non-project mode)
set log_file "vivado.log"

if {![file exists $log_file]} {
    error "Log file not found: $log_file"
}
puts "Log file: $log_file"
```

---

## Notes

- `synth_design -rtl` runs only the Verific front-end (parsing + elaboration),
  skipping synthesis optimization and technology mapping — typically 5-10x faster
- Even if elaboration fails (returns error), the log will contain all
  messages up to the failure point
- The `catch` wrapper ensures we can still access the log after errors
- Use `-rtl_skip_ip` for large designs with many OOC IP to speed up further
- If full synthesis log already exists (e.g., user provides a `runme.log`),
  the log-file-only workflow is preferred — no need to re-run elaboration
