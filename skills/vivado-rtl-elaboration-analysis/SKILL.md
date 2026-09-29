---
name: vivado-rtl-elaboration-analysis
description: >
  Analyze Vivado Oasys synthesis elaboration errors, critical warnings, and warnings
  from synthesis logs and provide actionable RTL code fixes. Log analysis is the
  primary mode; an active Vivado session is secondary. Inspect the user's actual RTL
  at every reported file and line before proposing a fix.
license: MIT
allowed-tools: vivadoExecute vivado_doc_search read_file file_search grep_search run_in_terminal create_file replace_string_in_file runSubagent
argument-hint: "[RTL/project path and optional runme.log or vivado.log]"
metadata:
  version: "1.0.0"
---

# RTL Elaboration Analysis — Oasys Synthesis

## Purpose

This skill analyzes **Oasys synthesis elaboration diagnostics** from Vivado and ties
every finding to the user's actual RTL. Messages appear as `[Synth 8-XXX]` in logs.

The runtime input may be:
- RTL source files (`.v`, `.sv`, `.vhd`, `.vhdl`) and their project/file-list context
- A Vivado `runme.log`, `vivado.log`, messages CSV, or pasted diagnostic
- An active Vivado session for secondary MCP-mode analysis

The skill covers parser, type-checker, and elaboration phases of `synth_design` and
reports:
- Summary by severity and message ID
- Root-cause explanation tied to actual RTL locations
- Hotspot analysis across affected RTL files
- RTL-level fix guidance with diff-formatted patches
- Tier classification and prioritized resolution order

## ID Coverage

This skill covers **Oasys message IDs** from testcases (`synth_code_8_xxx_testcase/`):
- **Critical Warnings (8 IDs)**: 4442, 5796, 5967, 6030, 6781, 6790, 6858, 13329
- **Errors (~400 IDs)**: 27-13331 (see `oasys_severity_lists.csv`)
- **Warnings (~86 IDs)**: 85, 90, 151, 264, 302, 324, 327, 506, 567, 614, 689, etc.

Reference: `oasys_severity_lists.csv` for full message descriptions.

## Scope

Use this skill when:
- A `vivado.log`, `runme.log`, or `synth_1/runme.log` contains Oasys elaboration messages.
- The user asks why synthesis failed or asks to fix synthesis warnings/errors.
- The user provides RTL/project sources for source-context inspection.

Do not use this skill when:
- No log and no active Vivado session are available.
- User needs implementation/timing/physical optimization analysis.
- The issue is unrelated to RTL elaboration or synthesis.

## Inputs

Minimum required input:
- Vivado synthesis log (`vivado.log`, `runme.log`, or `synth_1/runme.log`), or
- Active Vivado session through MCP

Useful optional input:
- User RTL/project path accessible in the workspace
- Top module/entity and target part
- Vivado project file, file list, or synthesis script
- Constraints and included packages/headers
- Existing user RTL fixes or intended behavior

## Mandatory Workflow

Execute these steps sequentially in this exact order.

### Step 1: Locate and Identify Log File

Determine operating mode and find the synthesis log.

**Log Analysis Mode** (default when user provides a log file path or log exists in workspace):
1. If `$ARGUMENTS` is a `.log` file path, use it directly
2. Otherwise search workspace:
   ```
   file_search("**/runme.log")
   file_search("**/vivado.log")
   file_search("**/*.log")
   ```
3. If multiple logs found, prefer `synth_1/runme.log` > `vivado.log` > others
4. If no log found, switch to MCP Mode

**MCP Mode** (when no log exists or user requests live analysis):
1. Load Vivado tools: `tool_search` with query `"vivado execute"`
2. Follow TCL in [tcl-reference.md § Step 1](tcl-reference.md) to detect project/non-project
3. Run `synth_design` — TCL in [tcl-reference.md § Step 2](tcl-reference.md)
4. Locate the resulting log file

**Verify:** Log file path is known and file exists.

### Step 2: Create Report Directory

Create the report directory **inside the test case directory** (same directory as the
log file). This ensures each test case has its own self-contained report.

```
<test_case_dir>/vivado_agentic_ai_reports/rtl-elaboration-analysis/
```

Where `<test_case_dir>` is the directory containing the vivado.log (e.g.,
`error/8_27/bcnmxd_vhdl_neg_DataTypeMap_DataTypeMap4/`).

Use `run_in_terminal` to create: `mkdir -p <test_case_dir>/vivado_agentic_ai_reports/rtl-elaboration-analysis`

Or if in MCP mode: `file mkdir <test_case_dir>/vivado_agentic_ai_reports/rtl-elaboration-analysis`

**Rule:** Never create a single shared report directory at workspace root. Each test
case gets its own `vivado_agentic_ai_reports/` alongside its log and source files.

### Step 3: Parse Elaboration Messages

Extract all elaboration messages from the log. Execute the parser:

```bash
python3 <skill_dir>/parse_elab_messages.py <log_file> vivado_agentic_ai_reports/rtl-elaboration-analysis/messages.csv
```

Where `<skill_dir>` is the path to this skill's directory.

**If Python is unavailable**, parse manually using `grep_search` on the log file:
- Pattern: `^(ERROR|CRITICAL WARNING|WARNING):\s*\[Synth\s+8-\d+\]`
- Extract: severity, message ID, text, file path, line number

**CSV column schema** (with header row, 7 columns):

| Col | Field | Description |
|-----|-------|-------------|
| 0 | S.No | Sequential serial number (1-based) for cross-referencing with report |
| 1 | Severity | `ERROR`, `CRITICAL WARNING`, `WARNING`, or `INFO` |
| 2 | Msg_ID | Integer message ID from `[Synth 8-XXX]` |
| 3 | Message | Full message text |
| 4 | File | Source file path (may be empty) |
| 5 | Line | Source line number (0 if not available) |
| 6 | Language | `VLOG` or `VHDL` (detected from file extension) |

**Verify:** CSV exists. Row count (excluding header) matches message count from log.

### Step 3.5: Create Resolution Checklist

Extract unique `msg_id` values (excluding INFO) from the CSV and classify each by
resolution availability. Write to:
`vivado_agentic_ai_reports/rtl-elaboration-analysis/resolution_checklist.md`

**Procedure:**
1. Read `messages.csv` (skip header row) and collect unique `msg_id` values (skip INFO)
2. For each unique ID, look it up in [message-handlers.md](message-handlers.md)
3. Write `resolution_checklist.md` with two sections:

```markdown
# Resolution Checklist

## HAS RESOLUTION FILE
| ID | Severity | Short Name | Resolution File | Loaded? |
|----|----------|------------|-----------------|--------|
| <id> | <sev> | <name> | resolution/<file>.md | [ ] |

## NO RESOLUTION FILE (use vivado_doc_search or own analysis)
| ID | Severity | Description | Source |
|----|----------|-------------|--------|
| <id> | <sev> | <text> | vivado_doc_search / own analysis |
```

**Verify:** Every unique WARNING/ERROR/CRITICAL WARNING `msg_id` from the CSV
appears in exactly one section.

### Step 4: Classify and Prioritize

Read the CSV and organize messages:

**4a. Severity grouping:**
1. **Errors** — Must fix. Block synthesis completion.
2. **Critical warnings** — Should fix. May cause incorrect synthesis results.
3. **Warnings** — Review and fix or waive.

**4b. Actionability classification:**

For each message, look up `msg_id` in [message-handlers.md](message-handlers.md):

- **Tier 1 (Directly Fixable):** Deterministic fix — apply without user input
- **Tier 2 (Fixable with Context):** Fix requires reading surrounding RTL
- **Tier 3 (Advisory):** Report but flag as "requires design decision"
- **Not in table:** Treat as Tier 2, use `resolution/uncovered-synth-messages.md`

**4c. File hotspot analysis:**
- Group messages by source file
- Rank files by total message count (descending)
- Files with >10 messages are "hotspots" — recommend structural review

**4d. Dependency detection:**
- Identify root cause messages vs cascading effects
- Prioritize root cause fixes
- Dependency classification only sets fix **priority** and section wording. It NEVER
  removes a message from the report: cascade, duplicate, and read-abort messages
  (e.g. `Failed to read`, `second declaration ignored`, `syntax error near`) each
  still get their own `###` section in Step 6 (may be brief and marked
  `Cascade of 8-XXX`, but must be present).

### Step 5: Generate Fixes

Process **every non-INFO message** in messages.csv. The folder/log may be named after
one "target" ID, but that NEVER limits the report to that ID alone — all messages in
the CSV are in scope. For each actionable message (Tier 1 and Tier 2), in priority
order (errors first):

1. **Read RTL source context:**
   - Resolve file path from CSV
   - If relative or filename-only: `file_search("**/<filename>")`
   - Read source with `read_file(resolved_path, line-10, line+10)`

2. **Look up message handler (MANDATORY):**
   - For every ID in "HAS RESOLUTION FILE": **MUST** load `resolution/<file>.md`
     with `read_file` BEFORE writing any fix. Follow the guide's patterns exactly.
   - For every ID in "NO RESOLUTION FILE": Use `vivado_doc_search` for UG901 guidance.
   - After loading each resolution file, mark it `[x]` in the checklist.
   - **Do NOT proceed to Step 6 if any resolution file remains unloaded.**

3. **For Tier 2 — inspect surrounding code:**
   - Read module/entity header for port declarations
   - Read signal declarations for type/width information
   - Search workspace for matching names (fuzzy match for typos)

4. **Generate diff-formatted fix:**
   ```diff
   - old_code  // <- problem description
   + new_code  // <- fix description
   ```

5. **For Tier 3 — write advisory:**
   - Explain the issue
   - List design options the user should consider

**Scaling — Branch Decision:**

- **≤50 messages → Standard Flow:** Proceed directly to Step 6.
- **>50 messages → Extended Flow:** Execute Steps 5A and 5B first.

---

### Extended Flow (>50 messages only)

#### Step 5A: Generate Per-File Message Parts

Split the CSV into per-file message reports:

```bash
cd vivado_agentic_ai_reports/rtl-elaboration-analysis
python3 <skill_dir>/csv_to_per_file_parts.py messages.csv ./
```

**Generated files:**
- `messages_by_file.txt` — Summary with error counts + file rankings
- `messages_by_file_part1.txt` — Easiest files (start here)
- `messages_by_file_partN.txt` — Progressive difficulty

#### Step 5B: Per-File Subagent Analysis

Launch one subagent per part file. Each subagent processes up to 20 RTL files.

For each file in the part:
1. Parse messages
2. Locate source file with `file_search`
3. For each message: read source, look up handler, load resolution file, write diffs
4. Return per-file analysis markdown

Save outputs to `file_analysis/<filename>_analysis.md`

---

### Step 6: Write Report

**The workflow is incomplete until REPORT.md exists.**

Save to: `vivado_agentic_ai_reports/rtl-elaboration-analysis/REPORT.md`

**Report structure:**

```markdown
# RTL Elaboration Analysis Report

## Summary
| Severity | Count | Actionable | Advisory |
|----------|-------|------------|----------|
| Error | N | N | N |
| Critical Warning | N | N | N |
| Warning | N | N | N |

## File Hotspots
| File | Errors | Crit. Warnings | Warnings | Total |
|------|--------|----------------|----------|-------|

## Message Type Distribution
| ID | Severity | Count | Description | Actionable? |
|----|----------|-------|-------------|-------------|

## Fixes — Errors (Priority 1)
### [Synth 8-XXX] <description>
**CSV S.No:** <serial number(s) from messages.csv>
**File:** [file.v:42](../../path/to/file.v#L42)
**Message:** <full message text>
**Root Cause:** <explanation>

**Problematic Code:**
\```diff
  context line
- problematic line  // <- what's wrong
  context line
\```

**Recommended Fix:**
\```diff
  context line
+ fixed line  // <- what changed and why
  context line
\```

**Corrected Code (paste-ready):**
\```verilog
// Final corrected code only - no diff markers. Paste this over the flagged
// line(s) in <file>. Language tag = verilog / systemverilog / vhdl per the
// CSV Language column.
fixed line
\```

> You can copy the code block above and paste it directly into `<file>` at line(s)
> `<N>` to apply this fix in your RTL.

**Rationale:** <why this fix is correct>

## Fixes — Critical Warnings (Priority 2)
...

## Fixes — Warnings (Priority 3)
...

## Advisory — Design Decisions Required
...

## Recommendations
### Immediate (fix now)
### High-Impact Quick Wins
### Design Review Items
### Accept/Waive
```

**Formatting rules:**
- Each actionable fix has TWO code blocks: (a) a `diff` block showing the change,
  and (b) a **Corrected Code (paste-ready)** block that contains ONLY the final
  fixed code with NO diff markers (`+`/`-`/space) and NO `@@` hunk headers, so the
  user can copy it directly into the RTL.
- The paste-ready block uses a language fence chosen from the CSV `Language` column:
  `VLOG` -> ` ```verilog ` (or ` ```systemverilog ` for SV), `VHDL` -> ` ```vhdl `.
- The paste-ready block must contain real code taken from the actual RTL with the
  fix applied (verify by re-reading the source), plus just enough surrounding
  lines to unambiguously locate where it goes.
- **MANDATORY**: Immediately after every **Corrected Code (paste-ready)** block, add
  one explicit line telling the user the code can be copied and pasted into the
  target RTL, naming the file and the line(s), e.g.:
  `> You can copy the block above and paste it directly into \`<file>\` at line(s) <N> to apply this fix.`
  Every recommended fix in every generated report must carry this line.
- This report only PRESENTS the corrected code; it does NOT modify the user's RTL
  source files. Applying the paste-ready code is left to the user.
- **Fences in the generated REPORT.md must be PLAIN** (` ```diff `, ` ```verilog `,
  ` ```vhdl `) with NO backslash escaping. The `\`\`\`` shown in this skill's template
  is only escaped so the example nests inside this doc; never write `\`` before a
  backtick in the actual report, or the code block will not render.
- All file references are clickable markdown links with line numbers.
- **MANDATORY — no message dropped**: Every non-INFO message in messages.csv MUST be
  represented by a `###` section with File link, Problematic Code (diff block),
  Recommended Fix (diff block), and a Corrected Code (paste-ready) block. Never
  demote a message to only a Message-Type-Distribution table row or an Advisory
  bullet in place of its `###` section.
- **One section per distinct diagnostic.** A distinct diagnostic = a unique
  (Msg_ID + root cause + location). Two messages with different IDs, or the same ID
  at different file:line with different root causes, are distinct and each get their
  own section.
- **Grouping identical repeats is allowed and expected for large logs.** When the
  SAME Msg_ID with the SAME root cause repeats across many instances/signals
  (common in real designs and whenever total messages > 50), combine them into ONE
  `###` section that includes an **Occurrences** table listing every occurrence
  (S.No + instance/signal/line). This keeps the section count sane WITHOUT dropping
  any message — every row must still appear in some section's Occurrences list.
- This applies to **cascade / duplicate / read-abort** messages too. If a message is
  a downstream effect of another, still give it a `###` section; it may be brief and
  state `Cascade of 8-XXX — resolved by that fix`, but it must exist.
- A folder or log named after a single "target" ID does NOT reduce the required
  sections: cover every non-INFO message regardless of the folder name.
- Message section count in REPORT.md must equal non-INFO rows in messages.csv.
  Verify this count explicitly before finishing (Step 7 check #1). If sections are
  grouped by unique ID, instead verify that the total occurrences listed across all
  sections equals the non-INFO row count — every non-INFO row must be accounted for.

### Step 7: Verify Report Accuracy

1. **Every non-INFO row is accounted for.** Either (a) `###` section count == non-INFO
   rows in messages.csv, or (b) when identical repeats are grouped by unique ID, the
   total occurrences listed across all sections == non-INFO rows. If any row is
   unaccounted for, a message was dropped — add it before finishing. This is a HARD
   STOP; do not report completion while rows are unaccounted for.
2. All errors have fix recommendations (Tier 1/2) or advisory notes (Tier 3)
3. All code in diff blocks is from actual RTL files (verify by re-reading)
4. Every actionable fix has a Corrected Code (paste-ready) block with no diff markers
5. Every Corrected Code (paste-ready) block is followed by the explicit copy-paste
   line naming the target file and line(s)
6. Cascade/duplicate/read-abort messages each have their own `###` section (not just
   a table row or Advisory bullet)
7. File links point to correct locations
8. No fabricated violations or fixes
9. `messages.csv` (Step 3) and `resolution_checklist.md` (Step 3.5) exist in the
   report directory
10. Every resolution file in the checklist is marked `[x]` (loaded)
11. All code fences in REPORT.md are plain (` ```diff `/` ```verilog `/` ```vhdl `)
    with NO backslash before any backtick — grep the report for `\`` and fix any hit.

### Step 8: Apply Fixes to RTL

**This step comes AFTER the report is written.** The REPORT.md documents the
original problematic code and the recommended fix. Only after the report is
complete should the fixes be applied to the actual RTL source files.

1. For each fix in REPORT.md, apply the recommended change to the source file
2. Use `replace_string_in_file` to make each edit
3. Do NOT modify RTL before Step 6 — the report's diff blocks must show the
   original (unfixed) code with `- ` prefix and the fix with `+ ` prefix

**Workflow order is critical:**
- Steps 1–5: Analyze log, read original RTL, generate diffs
- Step 6: Write REPORT.md with diffs (source is still original)
- Step 7: Verify report
- Step 8: Apply fixes to actual RTL files
- Step 9: Re-run synthesis to verify fixes

## Resolution Policy

- Fix errors before critical warnings, then warnings.
- Use `message-handlers.md` and the referenced `resolution/` guide for each ID.
- Read real source code before generating any diff; never fabricate RTL.
- Preserve design behavior and flag any change that requires a design decision.

## Directory Assumptions

This skill expects these helper files beside this file:
- `message-handlers.md`
- `resolution-guide.md`
- `report-format.md`
- `tcl-reference.md`
- `parse_elab_messages.py`
- `csv_to_per_file_parts.py`
- `resolution/`

If any file is missing, continue with best-effort analysis from `message.txt`
If any file is missing, continue with best-effort analysis from the log and actual
RTL source, and clearly identify the missing asset in the report.
