---
name: vivado-timing-methodology-checks
description: >-
  Resolve Vivado timing methodology violations on synthesized or implemented designs and generate XDC fixes or waivers. Use when the user mentions report_methodology, timing methodology, methodology violations or checks, a TIMING-<n> ID (e.g. TIMING-1#1), or asks to report, fix, resolve, waive or understand timing methodology warnings. Non-TIMING IDs (SYNTH-*, DRC-*, etc.) are reported as Not Supported.
license: MIT
compatibility: Requires Vivado 2026.1+ with the Vivado MCP server connected
metadata:
  author: John Blaine
  version: "2.0.2"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Table of Contents
- [Prerequisites](#prerequisites)
- [Step-by-Step Instructions](#step-by-step-instructions)
  - [Step 1: Run the setup script](#step-1-run-the-setup-script)
  - [Step 2: Consider Check Rules](#step-2-consider-check-rules)
  - [Step 3: Prioritize by severity](#step-3-prioritize-by-severity)
  - [Step 4: Generate data](#step-4-generate-data)
  - [Step 5: Follow Resolution Flow](#step-5-follow-resolution-flow)
  - [Step 6: Re-run report_methodology (single sweep)](#step-6-re-run-report_methodology-single-sweep)
  - [Step 7: Generate output report (mandatory)](#step-7-generate-output-report)
    - [A. Generate the report content](#a-generate-the-report-content)
    - [B. Write the Markdown report](#b-write-the-markdown-report)
    - [C. Write the HTML report](#c-write-the-html-report)
- [Design Edits and Actions](#design-edits-and-actions)
  - [Automatic Fixes](#automatic-fixes)
  - [User Directed Fixes](#user-directed-fixes)
  - [Creating Waivers](#creating-waivers)
- [Critical Rules](#critical-rules)
- [File Locations](#file-locations)

# Prerequisites

1. Use the vivado Ai toolset to either open the design or use an already open design.


# Step-by-Step Instructions
This assumes starting from a point where there is an open design.

**Deliverable:** this skill always ends with the written resolution reports
(`methodology_resolution_report.md` and `.html`, Step 7). They are mandatory on every run.
Do all seven steps. Do not stop after Step 6.

## Paths used in this skill
Two locations matter. Keep them separate:

* **`<skill_dir>`** is the directory that contains this `SKILL.md`. Every script is at
  `<skill_dir>/scripts/...`. Always run a script by this absolute path. **Do not `cd` into
  `<skill_dir>`**, in Tcl or in a shell, to run a script.
* **`./methodology_results/`** is the output directory. It is relative to the **project root**, the
  directory Vivado was started in (the working directory of your session). Keep Vivado's working
  directory and your shell's working directory at the project root for the whole run, so every
  `./methodology_results/...` path in this skill resolves to the same place. Run shell commands such as
  `python3` and `diff` from the project root too.

## Step 1: Run the setup script
* One call produces every file the rest of the flow depends on. No manual
  `report_methodology`/`write_xdc`/index-building sequence is needed.

**Example TCL (run inside Vivado, with the design open, working directory at the project root):**
```tcl
source <skill_dir>/scripts/run_methodology_setup.tcl
;# Sourcing loads every helper proc (see "Calling the Tcl procs") AND runs the setup once,
;# writing to ./methodology_results (resolved to an absolute path when sourced).
;# For a custom output dir, set this global BEFORE sourcing:
;#   set ::methodology_out_dir /path/to/methodology_results
;# To re-run later without re-sourcing:
;#   methodology_check::run_methodology_setup /path/to/methodology_results
```

## Calling the Tcl procs
Every Tcl proc in this skill lives in the **`methodology_check` namespace** and is loaded by the single
`source <skill_dir>/scripts/run_methodology_setup.tcl` in Step 1. After Step 1 nothing else needs to be sourced.

**Always call a proc by its namespace-qualified name.** The unqualified name does not exist and fails with
`invalid command name`.

| Proc (call exactly like this) | Defined in | Used by |
|-------------------------------|------------|---------|
| `methodology_check::check_dont_touch_on_net -from <obj> [-json <file>]` | `<skill_dir>/scripts/run_methodology_setup.tcl` | TIMING-14 |
| `methodology_check::profile_constraints <xdc> <json>` | `<skill_dir>/scripts/run_methodology_setup.tcl` | Step 1 (called for you) |
| `methodology_check::resolve_datapath_only_override -big_from ... [-out <file>]` | `<skill_dir>/scripts/run_methodology_setup.tcl` | TIMING-24 |
| `methodology_check::resolve_clock_groups_override -groups {{A} {B}} -budgets {{A B}} [-out <file>]` | `<skill_dir>/scripts/run_methodology_setup.tcl` | TIMING-24 |
| `methodology_check::run_methodology_setup [out_dir]` | `<skill_dir>/scripts/run_methodology_setup.tcl` | Step 1 |
| `methodology_check::timing11_cell_constraints -delay <ns> -from_clocks <list> -to_clocks <list> [-keep_cdc 0\|1] [-json <file>] [-out <file>]` | `<skill_dir>/scripts/run_methodology_setup.tcl` | TIMING-11 (CASE 1) |
| `methodology_check::timing18_summary [-json <file>] [-out <file>]` | `<skill_dir>/scripts/run_methodology_setup.tcl` | TIMING-18 |
| `methodology_check::trace_clock_back_to_source <pin>` | `<skill_dir>/scripts/run_methodology_setup.tcl` | `CLOCK_TRACING.md`, TIMING-1/2/3/4/35/36/43/51 |

All procs are defined in the one file `<skill_dir>/scripts/run_methodology_setup.tcl`, in alphabetical order.
Procs whose name starts with `_` (e.g. `_dt_prop`) are private helpers; do not call them.
If a proc is reported missing (`invalid command name "methodology_check::..."`), the setup file was not
sourced in this Vivado session: source `<skill_dir>/scripts/run_methodology_setup.tcl` again.

This produces, under `./methodology_results/` by default:

| File | Format | What it is |
|------|--------|------------|
| `methodology.json` | Vivado native JSON | Raw `report_methodology -json` output |
| `constraints.xdc` | plain XDC text | Unified constraints backup (`write_xdc -write_id`; each timing constraint is preceded by a `# id:N` comment giving its position number) |
| `waivers.rpt` | plain text | Methodology waivers present at setup (`report_waivers -type METHODOLOGY -no_header -show_msgs_with_no_waivers -file waivers.rpt`) |

This produces, under `./methodology_results/tmp` by default:

| File | Format | What it is |
|------|--------|------------|
| `constraints_profile.json` | JSON | Profile of every constraint keyed by constraint ID: type, IP or not, value, object classes of from/to. Also the IP-vs-user-RTL classification. See schema below |
| `methodology_index.json` | JSON | Severity-ordered, reference-annotated work list, see schema below |

You only need to know these two JSON shapes — the script already did the extraction:

**`constraints_profile.json`:**
The main key of `constraints` is the constraint ID: the `# id:N` that `write_xdc -write_id`
puts before each timing constraint in `constraints.xdc`. It is the same number as the
"constraint position N" quoted in methodology messages (TIMING-11, TIMING-24, TIMING-54, ...),
so a violation maps straight to its constraint: `constraints["<N>"]`.
```json
{
  "constraint_count": 3,
  "ip_instances": ["top_i/Clock_Gen/clk_wizard_0/inst"],
  "scopes": [{"hierarchy": "top_i/Clock_Gen/clk_wizard_0/inst", "is_ip": true}],
  "constraints": {
    "4": {
      "id": 4, "type": "set_max_delay", "category": "timing_exception",
      "file": "tm_top.xdc", "line": 14,
      "scope": "", "is_ip": false, "ip_instance": null,
      "value": 4.0, "options": {"-datapath_only": true},
      "from": {"classes": ["cell"], "objects": ["r11a_reg"], "wildcard": false},
      "to":   {"classes": ["cell"], "objects": ["r11b_reg"], "wildcard": false},
      "text": "set_max_delay -datapath_only -from [get_cells r11a_reg] -to [get_cells r11b_reg] 4.000"
    },
    "5": {
      "id": 5, "type": "set_multicycle_path", "category": "timing_exception", "value": 2,
      "options": {"-setup": true},
      "from": {"classes": ["clock"], "objects": [], "of_objects": [{"classes": ["pin"], "objects": ["mmcm_i/CLKOUT0"]}]},
      "to":   {"classes": ["clock"], "objects": [], "of_objects": [{"classes": ["pin"], "objects": ["mmcm_i/CLKOUT1"]}]}
    }
  },
  "unnumbered": [{"line": 10, "type": "set_property", "value": "LATENCY", "options": {"property": "PHASESHIFT_MODE"}}]
}
```
| Field | Meaning |
|---|---|
| `type` / `category` | The command (`create_clock`, `set_max_delay`, ...) and its family: `clock_definition`, `generated_clock`, `io_delay`, `timing_exception`, `clock_relationship`, `clock_property`, `case_analysis`, `disable_timing`, `property`, `other` |
| `file` / `line` | Original XDC file (from `# Constraints from file`) and the line in `constraints.xdc`. The report must name the original file |
| `scope` | The `current_instance` hierarchy in effect; `""` = top level |
| `is_ip` / `ip_instance` | Whether it is an IP constraint, and which IP hierarchy. IP iff the scope equals, or is nested under (`"<entry>/"`), a `current_instance` whose last path segment is `inst` (Verilog IP) or `U0` (VHDL IP). See [<skill_dir>/references/IP_CONSTRAINTS.md](./references/IP_CONSTRAINTS.md) |
| `value` | The main number: the period (`create_clock`), delay, cycle count, skew, latency, uncertainty, divide-by, or the property value (`set_property`). `null` if none |
| `options` | Flags and keyword options: `-datapath_only`, `-setup`, `-name`, `-period`, ... |
| `from`, `to`, `through`, `clock`, `source`, `master_clock`, `target`, `groups` | The objects of that option. `target` is the positional object (the port/pin a `create_clock` is on, the ports of `set_input_delay`). `groups` is one entry per `-group` (`set_clock_groups`). Each has `classes` (`cell`, `pin`, `port`, `net`, `clock`, `pblock`, `unresolved` for a bare name), `objects` (names as written), `wildcard` (`*`/`?` used), `of_objects` (the objects of a `get_clocks -of_objects ...`) and `filter` (a `-filter` expression) |
| `shared_with` | IDs of the other constraints with the same `file` and the same text, i.e. a scoped XDC file written once per instance (each under its own `scope`). `[]` when the constraint is unique. The top-level `shared_constraints` lists each such group (`ids`, `scopes`) |
| `unnumbered` | Constraints without an ID (e.g. `set_property`), with the same type/scope/IP fields |

The profile is parsed from the text of `constraints.xdc`, so classes come from the `get_*` command
used. It does not say what the objects are in the netlist (e.g. a register or a clock-divider):
look that up in the design when the resolution depends on it. Names are as written, so wildcards are not expanded.

**`methodology_index.json`:**
```json
{
  "source_file": "<path to methodology.json>",
  "report_header": { "...": "passthrough from methodology.json" },
  "severity_priority": ["Error", "Critical Warning", "Warning", "Info"],
  "ordered_rules":     [ "<rule entry>", "..." ],
  "timing_work_list":  [ "<rule entry>", "..." ],
  "unsupported_rules": [ "<rule entry>", "..." ],
  "lookup_by_rule":    { "<RULE>": "<rule entry>" }
}
```
- `ordered_rules` — every rule, severity-ordered (walk this top to bottom per Step 3).
- `timing_work_list` — subset where `is_timing_id == true`; this is the actual
  resolution work list (Step 3/5).
- `unsupported_rules` — subset where `is_timing_id == false`; report-only (Step 2).
- `lookup_by_rule` — same entries keyed by rule ID for O(1) lookup.

Each `<rule entry>` has:

| Field | Meaning |
|---|---|
| `rule` | Check ID, e.g. `"TIMING-1"` |
| `severity` | `"Error"` \| `"Critical Warning"` \| `"Warning"` \| `"Info"` \| other |
| `severity_rank` | `0`=Error, `1`=Critical Warning, `2`=Warning, `3`=Info, `99`=other |
| `description` | One-line description from the report |
| `count` | Number of violation instances for this rule |
| `is_timing_id` | `true` only for `TIMING-*` rules — in scope for this skill |
| `has_reference_file` | Whether `<skill_dir>/references/<rule>.md` exists |
| `reference_file` | Path to that doc if it exists, else `null` |
| `details_count` | Total raw detail-string count for this rule (may exceed 5) |
| `details_preview` | **First 5 raw detail strings only** — not the full list |

**Important:** `details_preview` is a preview, not every instance. If `details_count` >
5 and you need every instance's object/pin (e.g. `NAME` like `TIMING-1#1`, or the
`NETLIST_ELEMENT` embedded in each detail string as `<cell: hier/leaf>` or
`<pin: hier/leaf/Q>`), read that rule's full `details` array directly from
`methodology.json` instead of the index.


##  Step 2: Consider Check Rules
**Scope: this skill only resolves `TIMING-*` IDs.** Any `TIMING-<n>` reported by
`report_methodology` is in scope for resolution (no group gating).

Non-`TIMING` IDs (e.g. `SYNTH-*`, `LUTAR-*`, `AVAL-*`, `CLKC-*`, `DRC-*`) are **not
supported** for resolution. Detect and report them for visibility, but do not attempt to
fix them — record them in the report with status **Not Supported**. The
`is_timing_id` field in `./methodology_results/methodology_index.json` marks which IDs are
in scope, and `timing_work_list` / `unsupported_rules` pre-split them for you.

For each `TIMING` rule ID found in the methodology report:
1. Check whether a reference doc exists by looking directly for `<skill_dir>/references/<ID>.md`
   (e.g., `TIMING-1` → `<skill_dir>/references/TIMING-1.md`). The `has_reference_file` field in
   `./methodology_results/methodology_index.json` already records this for every ID.
2. General guidelines always apply. If a reference doc exists, it supplements or overrides the general guidelines for that ID. If no reference doc exists, resolve using the general guidelines alone.

General guidelines: [<skill_dir>/references/GENERAL_GUIDELINES.md](./references/GENERAL_GUIDELINES.md)
Reference docs: [<skill_dir>/references/<ID>.md](./references/<ID>.md)

**Example: check whether a reference doc exists for an ID**
```sh
# Reference doc present
test -f <skill_dir>/references/TIMING-16.md && echo "has reference" || echo "no reference"

# No reference doc — resolve with general guidelines only
test -f <skill_dir>/references/TIMING-99.md && echo "has reference" || echo "no reference"
```

## Step 3: Prioritize by severity
Resolve violations in strict severity order:

**Error → Critical Warning → Warning → Info**

`./methodology_results/methodology_index.json` is already sorted in this order, so walk it
top to bottom. Within the same severity, keep the order provided by the index.

✅ **Do:**
* Use the severity ordering from `methodology_index.json` as the work list.
* Use `timing_work_list` (only `TIMING-*` IDs, `is_timing_id` = true) as the resolution
  work list.

❌ **Do not:**
* Introduce any group-based ordering — grouping has been removed.
* Attempt to resolve non-`TIMING` IDs — they are not supported by this skill.
* Solve them at this stage. Resolution happens in Step 5.

## Step 4: Generate data
Most references have a `Generate Data` section. Generate the data specified for each check ID. A reference without one (e.g. `TIMING-11.md`) gathers what it needs in its own Flow, from `constraints_profile.json` and `methodology.json`.

✅ **Do:**
* Collect the data needed for the ID currently being resolved.

❌ **Do not:**
* Collect the same data more than once. Leverage other checks when the data has already been generated.

## Step 5: Follow Resolution Flow
* Each check has a defined flow to follow based on the reference documentation. 
* Resolutions are either:
- **AUTO**: You can fix it automatically
- **USER**: You must get user input; never guess their design intent

See [Design Edits and Actions](#design-edits-and-actions) for the canonical AUTO / USER / waiver procedure (backup → apply → report → single re-run); do not restate it here.

**Critical**: Prioritize reference files over general guidelines when they exist.

* Confirm the message understanding by outputting in the agent.log file:
[x] Find the problematic constraint or issue
[x] Understand why it is problematic
[x] Understand the correct resolution and why it is correct
[x] Understand why the selected resolution is better than the other options if they exist
* Increases confidence that any constraint fixes do not have unintended consequences

## Step 6: Re-run report_methodology (single sweep)
After applying all fixes, re-run the report **once** to capture the after state.
```tcl
reset_timing
read_xdc ./methodology_results/constraints_after_fix.xdc
foreach f [lsort [glob -nocomplain ./methodology_results/TIMING-*.xdc]] { read_xdc $f }
report_methodology -json ./methodology_results/methodology_after_fix.json
```
The `foreach` line reads any per-check constraint file a resolution wrote (for example
`./methodology_results/TIMING-11.xdc`, see `<skill_dir>/references/TIMING-11.md` CASE 1). Do not skip it. Those
constraints are not in `constraints_after_fix.xdc`.

**Single-sweep policy:** The skill performs exactly one cycle — generate report → fix →
re-run report_methodology. It does **not** loop or auto-rerun. If the after-fix report
introduces new violations, note them in the report and tell the user they may
run the skill again to address them.

## Step 7: Generate output report
**This step is mandatory. Both reports must be written on every run, including a
non-interactive run, a run where nothing could be fixed, and a run that only waived
or reported checks.** The skill is not finished, and you must not give a final answer,
until `python3 <skill_dir>/scripts/check_report.py html --dir ./methodology_results` has printed `check_report html: complete`.
The task prompt does not have to ask for the reports. Fixing and waiving are not the
deliverable of this skill, the reports are. Never end the run after Step 6.

The step has three parts. Do them in order: A, then B, then C.

### A. Generate the report content
Collect every fact the reports need into one file before writing either report.
Read [<skill_dir>/references/METHODOLOGY_RESOLUTION_REPORT_CONTENTS.md](./references/METHODOLOGY_RESOLUTION_REPORT_CONTENTS.md).
It defines the fields of `./methodology_results/tmp/report_content.json` and the
rules for filling them.

1. Generate the diff files that sections 5 and 6 of the reports show:
```sh
diff -u ./methodology_results/constraints.xdc ./methodology_results/constraints_after_fix.xdc > ./methodology_results/tmp/constraints.diff
# only when waivers_after_fix.rpt exists
diff -u ./methodology_results/waivers.rpt ./methodology_results/waivers_after_fix.rpt > ./methodology_results/tmp/waivers.diff
```
   `diff` exits 1 when the files differ. That is expected, not an error.
2. Write `./methodology_results/tmp/report_content.json` with the header, the
   summary row for every check, the resolutions, the waivers, the remaining
   actions and both diffs. Take counts from `methodology.json` and
   `methodology_after_fix.json`. Take the added and removed line counts from
   the `.diff` files.
3. Check it:
```sh
python3 <skill_dir>/scripts/check_report.py content --dir ./methodology_results
```
   The script prints one line per problem (a missing resolution, a count that
   disagrees with a JSON report, a check with no remaining action). Fix the
   content file and run the command again. Do not go to part B until it prints
   `check_report content: complete`.

### B. Write the Markdown report
Read [<skill_dir>/references/METHODOLOGY_RESOLUTION_REPORT_DETAILS.md](./references/METHODOLOGY_RESOLUTION_REPORT_DETAILS.md)
for how to format the Markdown. The filled example is
[<skill_dir>/examples/methodology_resolution_report.md](./examples/methodology_resolution_report.md).

Format `tmp/report_content.json` and the files in `./methodology_results/` into
`./methodology_results/methodology_resolution_report.md`. Include all six
sections, with the whole of each file in sections 5 and 6. Do not shorten a
section to a sentence and do not add facts that are not in the content file.

Check it:
```sh
python3 <skill_dir>/scripts/check_report.py md --dir ./methodology_results
```
Fix every reported problem and run it again. Do not go to part C until it
prints `check_report md: complete`.

### C. Write the HTML report
Read [<skill_dir>/references/HTML_REPORT_TEMPLATE.md](./references/HTML_REPORT_TEMPLATE.md)
for how to format the HTML and emit its `<style>` block verbatim. Do not invent
CSS. The filled example is
[<skill_dir>/examples/methodology_resolution_report.html](./examples/methodology_resolution_report.html).

Format the same content file into
`./methodology_results/methodology_resolution_report.html`. Include every
section the Markdown has, with the side-by-side full-file diffs (before on the
left, after on the right) built from the same `.diff` files. A status table
alone is not a finished report.

Check it:
```sh
python3 <skill_dir>/scripts/check_report.py html --dir ./methodology_results
```
Fix every reported problem and run it again. The run is not finished until it
prints `check_report html: complete`.

When A, B and C are all `complete`, the files `./methodology_results/tmp/report_content.json`,
`./methodology_results/methodology_resolution_report.md` and
`./methodology_results/methodology_resolution_report.html` exist. Name them in the final answer.
If a gate cannot be satisfied, say which item and why in the final answer. Do not skip the report.

# Design Edits and Actions

## Automatic Fixes
When the reference documentation marks a resolution as **AUTO** with high confidence, apply the fix directly without prompting:

1. Create backup file: `cp constraints.xdc constraints_after_fix.xdc`
2. Apply the change using the **modify** approach (replace/add/remove constraint as specified)
3. Report exactly what was changed with before/after code blocks
4. Re-run `report_methodology` to verify the fix
5. Generate HTML diff file showing differences between `constraints.xdc` and `constraints_after_fix.xdc`

**Note**: For AUTO fixes, always use the "modify" approach (direct replacement). Only use "comment out" for USER-directed fixes where the user explicitly requests it.



## User Directed Fixes
1. Generate Vivado command based on user's choice
2. Show command for review
3. Ask: "Execute this command? [Y/n]"
4. If yes, 
a. Create a new file: `cp constraints.xdc constraints_after_fix.xdc`
b. Apply the change (remove/modify/add constraint)
c. Report exactly what was changed
d. Say: "Re-run report_methodology to verify"
e. Generate a HTML file detailing the differences between `constraints.xdc` and  `constraints_after_fix.xdc`
5. If no, save command to script for manual execution

## Creating Waivers
1. Ask: "Provide justification (will be recorded):"
2. Collect: reason for waiving
3. Format: `<username> <date> "<reason>"`
4. Generate: `create_waiver -of_objects [get_methodology_violations TIMING-3#1] -user poldark -description <description> -tags <related_waiver_tag>`
5. Warn: "This violation will be ignored in future runs"
6. If this run created any methodology waivers, re-run `report_methodology -quiet` first (report_waivers
   degrades to "re-run report" and loses real counts for any waiver created since the last
   report_methodology run), then write the after snapshot. Do not overwrite the setup file
   `./methodology_results/waivers.rpt`.
```tcl
report_methodology -quiet
report_waivers -type METHODOLOGY -no_header -show_msgs_with_no_waivers -file ./methodology_results/waivers_after_fix.rpt
```
7. Include the waiver before/after content from [<skill_dir>/references/METHODOLOGY_RESOLUTION_REPORT_CONTENTS.md](./references/METHODOLOGY_RESOLUTION_REPORT_CONTENTS.md) in both reports. Skip this step when no waivers were created. The report_waivers table does not carry the `-user`/`-description` you collected in steps 1-4 — record those in the aspect table from your own actions, not by re-parsing the artifact.

---

# Final Output File Structure

**This is the golden source for filenames and locations**
```
<test-dir>/
└── methodology_results/
    ├── methodology.json                   # Report methodology before fixes
    ├── methodology_after_fix.json         # Report methodology after fixes
    ├── constraints.xdc                    # Constraints before fixes
    ├── constraints_after_fix.xdc          # Constraints after fixes
    ├── waivers.rpt                        # Methodology waivers before this run (setup)
    ├── waivers_after_fix.rpt                  # Methodology waivers after this run (only if waivers were created)
    ├── TIMING-11.xdc                      # Cell-level constraints replacing a clock-level -datapath_only (TIMING-11 CASE 1 only)
    ├── methodology_resolution_report.html # HTML output report
    ├── methodology_resolution_report.md   # MD output report
    └── tmp/
        ├── methodology_index.json         # Severity-ordered lookup index (built from methodology.json)
        ├── constraints_profile.json       # Every constraint by ID: type, IP or not, value, from/to object classes (IP = last current_instance segment inst/U0)
        ├── report_content.json            # Every fact in the reports (Step 7)
        ├── constraints.diff               # diff -u constraints.xdc constraints_after_fix.xdc
        ├── waivers.diff                   # diff -u waivers.rpt waivers_after_fix.rpt (only if waivers were created)
        └── command_history.rpt            # command history 
```

---

# Critical Rules

## DO:
✅ Always read `<skill_dir>/references/<CHECK_ID>.md` when it exists before responding
✅ Verify AUTO conditions before claiming you can fix it
✅ Present ALL options for USER resolutions
✅ For AUTO fixes: Apply immediately without prompting (high/medium confidence cases)
✅ For USER fixes: Ask confirmation before modifying any files
✅ Document every change made
✅ Solve violations in severity order (Error → Critical Warning → Warning → Info)
✅ Re-run report_methodology once after all fixes are applied (single sweep)
✅ Generate HTML diff after constraint modifications
✅ Recommend verification after fixes
✅ **Always generate both reports (Step 7, mandatory), even in a non-interactive run, and even if the prompt does not ask for them.** The run is finished only when `check_report.py html` prints `check_report html: complete`
✅ Write both reports from METHODOLOGY_RESOLUTION_REPORT_CONTENTS.md, using METHODOLOGY_RESOLUTION_REPORT_DETAILS.md for Markdown and HTML_REPORT_TEMPLATE.md for HTML
✅ Call every Tcl proc by its `methodology_check::` qualified name (see "Calling the Tcl procs")

## DON'T:
❌ Never stop after fixing or waiving, and never give a final answer before Step 7 has produced `report_content.json`, the `.md` report and the `.html` report
❌ Never guess design intent for USER resolutions
❌ Never apply AUTO fixes without verifying the conditions in the reference docs
❌ Never apply USER fixes without explicit user confirmation
❌ Never skip reading the reference documentation
❌ Never approve waivers without proper justification
❌ Never proceed if violation properties are missing
❌ Never re-run report_methodology more than once — this is a single-sweep skill; if new violations appear, ask the user to re-run the skill
❌ Never call a skill proc without the `methodology_check::` prefix, never `source` the helper scripts individually, and never copy or redefine a proc body into the session
❌ Never directly edit user constraints

# File Locations

**Scripts** (paths are relative to `<skill_dir>`, the directory containing this `SKILL.md`; run each by its
absolute path, see "Paths used in this skill"):
- `<skill_dir>/scripts/run_methodology_setup.tcl`   - **The only Tcl file; Step 1 entry point.** Defines every proc
  of the skill in the `methodology_check` namespace (alphabetical order) and, when sourced, runs
  report_methodology, write_xdc, constraint profiling, and index-building in one call. It contains
  `profile_constraints` (profiles every constraint by ID; **golden** IP detection: last path segment of
  a scoped `current_instance` in constraints.xdc is `inst`/`U0`; pure text check, no open design needed), `trace_clock_back_to_source`
  (clock tracing), `timing11_cell_constraints` (TIMING-11 cell-level constraint file),
  `timing18_summary` (TIMING-18 table), `check_dont_touch_on_net` (TIMING-14) and
  `resolve_datapath_only_override` and `resolve_clock_groups_override` (TIMING-24). See "Calling the Tcl procs" for the qualified call names.
- `<skill_dir>/scripts/profile_constraints.py` - Parses constraints.xdc into `constraints_profile.json` (called by `profile_constraints`)
- `<skill_dir>/scripts/process_methodology_json.py` - Build the severity-ordered violation index from methodology.json
- `<skill_dir>/scripts/check_report.py` - Steps 8-10 gate. `content`, `md` and `html` print every missing or inconsistent item and exit 1 until the report is complete

**Reference Docs:**
- `<skill_dir>/references/<CHECK_ID>.md` - Resolution guidance per violation
- Example: `<skill_dir>/references/TIMING-1.md`

---

