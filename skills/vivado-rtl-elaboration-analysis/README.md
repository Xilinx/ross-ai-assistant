# RTL Elaboration Analysis Skill

## What This Skill Does

Analyzes Vivado synthesis elaboration diagnostics (`[Synth 8-XXX]` errors, critical
warnings, and warnings) and ties every finding back to your actual RTL. It explains
root causes at the reported file and line, then proposes concrete RTL-level fixes.

The report style is: summary first, hotspots next, then a per-message fix section for
every actionable diagnostic.

## Operating Modes

1. **Log Analysis Mode (primary):** Parse `vivado.log`, `runme.log`, or
	`synth_1/runme.log`, then inspect the referenced RTL and propose fixes.
2. **Session Mode (secondary):** Use an active Vivado session to run elaboration and
	analyze results in real time.

## Quick Usage

Analyze RTL plus a log:
- `Analyze /path/to/my_rtl and /path/to/vivado.log; identify RTL root causes and fixes`

Analyze a pasted diagnostic:
- `Analyze this [Synth 8-xxx] message against the RTL at the reported file and line`

Analyze with an active Vivado session:
- `Run RTL elaboration in Vivado and analyze all Synth 8-XXX messages`

## Report Style

Expected output is organized like a synthesis analysis report:
1. Summary of severity counts and actionability
2. Hotspot tables by RTL file and source location
3. Per-message analysis with cause, context, and recommended fix
4. Final recommendations and next steps

Supporting references included in this package:
- `report-format.md` — report structure, diff rules, and JSON schema
- `tcl-reference.md` — Vivado TCL flow for elaboration
- `resolution-guide.md` — dispatcher and resolution policy
- `resolution/` — message-to-guide map

## Structured JSON Input

When structured JSON diagnostics are available, the skill consumes them directly.
Your RTL and diagnostics remain the source of truth.

Minimum message fields:
- `severity`
- `msg_id`
- `message_text`
- `rtl_source_file`
- `rtl_source_line`

## Directory Layout

```
vivado-rtl-elaboration-analysis/
├── SKILL.md
├── README.md
├── message-handlers.md
├── resolution-guide.md
├── report-format.md
├── tcl-reference.md
├── parse_elab_messages.py
├── csv_to_per_file_parts.py
└── resolution/
```

## Core Commands

Parse elaboration messages from a Vivado log:
- `python3 parse_elab_messages.py <log_file> <output_csv>`

Split a messages CSV into per-file parts:
- `python3 csv_to_per_file_parts.py <elab_messages.csv> <output_dir>`

## Runbook

### A) Log Analysis Flow (Primary)

Prerequisites:
- RTL/project sources
- A synthesis/elaboration log (`runme.log` or `vivado.log`), a diagnostic CSV, or a
  pasted message

Steps:
1. Parse messages from the log when available:
	- `python3 parse_elab_messages.py <log_file> reports/rtl-elaboration-analysis/messages.csv`
2. (If actionable messages > 50) split by file for the extended flow:
	- `python3 csv_to_per_file_parts.py reports/rtl-elaboration-analysis/messages.csv reports/rtl-elaboration-analysis/`
3. Inspect the actual RTL around every reported file and line before proposing changes.
4. Generate the report and include a focused validation command for the project.

Expected outputs:
- `reports/rtl-elaboration-analysis/messages.csv`
- `reports/rtl-elaboration-analysis/messages_by_file.txt` (extended flow)
- `reports/rtl-elaboration-analysis/messages_by_file_part*.txt` (extended flow)

Validation checklist:
1. `messages.csv` exists and has a header plus data rows
2. The non-INFO row count matches the expected actionable count
3. A resolution checklist is generated in the report workflow (`resolution_checklist.md`)
4. The final report follows the rule: one non-INFO message row → one `###` section

### B) Session Flow (Secondary)

Prerequisites:
- An active Vivado session with the project and RTL loaded

Steps:
1. Run RTL elaboration through the Vivado session.
2. Collect the complete elaboration diagnostics.
3. Inspect the actual RTL and produce the same report as Log Analysis Mode.

### C) Missing Input Handling

If no log and no active Vivado session are available:
1. Ask for a `runme.log`/`vivado.log`, a diagnostic CSV, or a pasted `[Synth 8-XXX]` message.
2. If RTL is available, inspect the referenced sources directly once a diagnostic is provided.

## Expected Output

- Per-message root cause tied to the actual RTL location
- Tier classification (T1/T2/T3)
- Recommended RTL-level fixes
- Priority ordering and hotspot summary
