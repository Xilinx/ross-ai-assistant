# Report Format — RTL Elaboration Analysis

## Output Files

The skill produces these files in the report directory:

| File | Purpose |
|------|---------|
| `messages.csv` | Parsed messages: S.No, severity, msg_id, text, file, line, language (with header row) |
| `report_data.json` | Structured analysis results for machine consumption |
| `REPORT.md` | Human-readable report with fixes |

---

## REPORT.md Structure

```markdown
# RTL Elaboration Analysis Report

**Design**: <top_module>
**Log file**: <path_to_log>
**Messages found**: <total> (E:<errors> CW:<crit_warnings> W:<warnings>)
**Actionable**: <count> | **Advisory**: <count>

## Summary

| Severity | Count | Actionable |
|----------|-------|------------|
| ERROR | N | N |
| CRITICAL WARNING | N | N |
| WARNING | N | N |

## Errors (Tier 1 — Immediate Fixes)

### [Synth 8-128] 'my_signal' is not declared
**CSV S.No**: 1
**File**: [path/to/file.v](path/to/file.v#L42)
**Cause**: Signal used but never declared.
**Fix**:
\```diff
--- a/path/to/file.v
+++ b/path/to/file.v
@@ -41,2 +41,3 @@
 // existing code context
+wire [7:0] my_signal;
 assign out = my_signal;
\```
**Corrected code (paste-ready)** — copy into `path/to/file.v` around line 41:
\```verilog
// existing code context
wire [7:0] my_signal;
assign out = my_signal;
\```

### [Synth 8-XXX] ...
...

## Critical Warnings (Tier 1)

...

## Warnings (Tier 2 — Design Improvements)

### [Synth 8-566] inferring latch for variable 'state_reg'
**CSV S.No**: 5
**File**: [path/to/fsm.sv](path/to/fsm.sv#L100)
**Cause**: Incomplete case/if coverage in combinational always block.
**Fix**:
\```diff
--- a/path/to/fsm.sv
+++ b/path/to/fsm.sv
@@ -98,4 +98,5 @@
 always_comb begin
     case (state)
         IDLE: next_state = RUN;
+        default: next_state = IDLE;
     endcase
\```
**Corrected code (paste-ready)** — copy into `path/to/fsm.sv` around line 98:
\```systemverilog
always_comb begin
    case (state)
        IDLE: next_state = RUN;
        default: next_state = IDLE;
    endcase
\```

## Advisory (Tier 3 — No Code Fix Needed)

| ID | Message | File | Line |
|----|---------|------|------|
| 564 | referenced signal 'clk' should be ... | ctrl.v | 200 |

## Cascading Errors

The following errors may be caused by earlier failures:

- [Synth 8-402] `failed synthesizing module 'sub_mod'` — likely caused by errors above in sub_mod
```

---

## Diff Format Rules

1. Use unified diff format with `--- a/` and `+++ b/` headers
2. Use **workspace-relative paths** in diff headers (not absolute)
3. Include 2-3 lines of surrounding context
4. Use `@@` hunk headers with approximate line numbers
5. Mark additions with `+`, deletions with `-`, context with space
6. Wrap diffs in ` ```diff ` fenced code blocks

---

## Paste-Ready Fix Rules

Every actionable fix includes, **in addition to** the diff, a **Corrected code
(paste-ready)** block so the user can copy the code directly into their RTL.

1. The block contains ONLY the final corrected code — no `+`/`-`/space prefixes,
   no `--- a/` `+++ b/` headers, and no `@@` hunk lines.
2. Choose the fence language from the CSV `Language` column:
   `VLOG` -> ` ```verilog ` (or ` ```systemverilog ` for SystemVerilog),
   `VHDL` -> ` ```vhdl `.
3. Include just enough surrounding lines to locate where the code goes, and add a
   short lead-in noting the file and approximate line (e.g. "copy into `file.v`
   around line 41").
4. The code must be the actual corrected RTL (verify by re-reading the source).
5. This block only PRESENTS the code; the skill does NOT modify the RTL source
   files. Applying the change is the user's action.

---

## File Link Format

The CSV `File` column contains paths **relative to the report directory** (the
parser computes this automatically). Use them directly as the link target:

`[filename.v](CSV_File_value#L42)`

Example: CSV has `../../../memstream.sv` → link: `[memstream.sv](../../../memstream.sv#L73)`

- Use the CSV path as-is — no extra computation needed
- Never use absolute paths in links
- Display text = just the filename for readability

---

## report_data.json Schema

```json
{
  "design": "<top_module>",
  "log_file": "<path>",
  "timestamp": "<ISO-8601>",
  "summary": {
    "total": 15,
    "errors": 3,
    "critical_warnings": 2,
    "warnings": 10,
    "actionable": 12,
    "advisory": 3
  },
  "messages": [
    {
      "severity": "ERROR",
      "msg_id": 128,
      "text": "'my_signal' is not declared",
      "file": "src/top.v",
      "line": 42,
      "language": "VLOG",
      "tier": 1,
      "category": "undeclared_identifier",
      "fix_applied": true,
      "fix_description": "Added wire declaration",
      "cascading": false
    }
  ]
}
```

---

## Grouping and Ordering

1. Group by severity: ERROR → CRITICAL WARNING → WARNING
2. Within each group, sort by tier (1 first, then 2, then 3)
3. Within each tier, sort by file path then line number
4. Tier 3 messages go in a summary table, not individual sections
5. Cascading errors go in a separate section at the end
