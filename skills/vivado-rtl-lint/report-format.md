<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Report Formatting Reference

This file defines mandatory formatting rules for the RTL lint markdown report.
All generated reports **must** follow these conventions.

---

## File Link Format

**All file links MUST use workspace-relative paths (NOT absolute paths).**

### Correct (clickable in VS Code):
```markdown
[design.sv:42](../../../design.sv#L42)
[module.sv:20](../../src/module.sv#L20)
```

### Wrong (will NOT open in VS Code preview):
```markdown
[design.sv:42](/proj/user/project/design.sv#L42)
```

### Path Calculation

1. Determine where the markdown report will be saved
2. Calculate relative path from report location to RTL source file
3. Use `../../` to navigate up directory levels, then path to file
4. Append `#L<line_number>` for line references

**Example:**
```
Workspace:
  project_root/
    src/design.sv                        ← TARGET
    vivado_agentic_ai_reports/
      rtl-lint/
        rtl_lint_report.md               ← REPORT

From report → target:
  ../../src/design.sv  (2 levels up, then down to src/)
```

### TCL Helper — Calculate Relative Paths

```tcl
# Execute through vivadoExecute tool
proc relative_path {from to} {
    set from_parts [file split [file dirname $from]]
    set to_parts [file split $to]
    set common 0
    foreach f $from_parts t $to_parts {
        if {$f eq $t} {incr common} else {break}
    }
    set up [expr {[llength $from_parts] - $common}]
    set rel [string repeat "../" $up]
    append rel [join [lrange $to_parts $common end] "/"]
    return $rel
}
```

---

## Diff Syntax Highlighting

**Always** use ` ```diff ` for code blocks showing errors and fixes — never `verilog`, `systemverilog`, or `vhdl`.

| Prefix | Meaning | Rendered Color |
|--------|---------|---------------|
| `-`    | Problematic / removed line | Red |
| `+`    | Corrected / added line | Green |
| (none) | Context line (unchanged) | Default |

### Inline Comment Rules

1. **Every `-` line MUST have an inline comment** explaining what is being removed
2. **Every `+` line SHOULD have an inline comment** explaining the fix (when not obvious)
3. Use language-appropriate comment syntax:
   - Verilog/SystemVerilog: `// Comment`
   - VHDL: `-- Comment`

### Verilog Example
```diff
 module example(
-    input [7:0] unused_port,    // Remove unused port from module interface
     output wire data
 );
```

### VHDL Example
```diff
 entity top is port (
-a : in std_logic_vector(6 downto 0);  -- Remove unused port 'a' from entity
 b : in std_logic_vector(6 downto 0);
 y1 : out std_logic
 );
```

### Multi-line Removal Example
```diff
-signal unread_sig : std_logic_vector(0 downto 0);  -- Remove unused signal declaration
 begin
-process(a,b)  -- Remove entire process since signal is unused
-variable unread_var : std_logic_vector(0 downto 0);  -- Remove this line
-begin  -- Remove this line
-unread_var(0 downto 0) := b(0 downto 0);  -- Remove this line
-unread_sig(0 downto 0) <= unread_var(0 downto 0);  -- Remove this line
-end process;  -- Remove this line
```

---

## Per-Violation Section Template

Use this template for **each** violation in the report.

**Source code requirement:** The "Problematic Code" and "Recommended Fix" blocks
**must** contain real source code read from the original RTL file at the violation's
file:line (±5 lines context). Never fabricate, paraphrase, or use placeholder code.
If the source file is unreadable, note this explicitly and fall back to the Vivado
message text.

```markdown
### [Issue Type] — [Message ID]

**Location**: [file.sv:line](relative/path/to/file.sv#Lline)

**Issue Description**: [Clear explanation from linter message]

**Problematic Code** *(from original source)*:
` ` `diff
 // Context lines from source file (unchanged)
- [actual line with error from source]    // ERROR: description
 // More context from source file
` ` `

**Recommended Fix**:
` ` `diff
 // Context lines (unchanged)
+ [corrected line]     // FIXED: description
 // More context
` ` `

**Rationale**: [Why this fix works, cite UG901 sections]

**Vivado Documentation**: [UG901/UG906 reference via vivado_doc_search]
```

---

## Report Output Location

```
vivado_agentic_ai_reports/
└── rtl-lint/
    ├── lint_report.rpt          ← Raw Vivado linter output
    └── rtl_lint_report.md       ← AI-generated analysis with fixes
```

Report directory path (TCL):
```tcl
set report_dir "${project_dir}/vivado_agentic_ai_reports/rtl-lint"
file mkdir $report_dir
set lint_report_file "${report_dir}/lint_report.rpt"
set markdown_report_file "${report_dir}/rtl_lint_report.md"
```
