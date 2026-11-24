<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Methodology Resolution Report — Contents

This file is the single source of what both reports contain. `SKILL.md` Step 7
(generate output report: A generate the content, B write the Markdown, C write the HTML)
all read this file. Do not put formatting rules here.

## Content file (Step 7A output)

Step 7A writes every fact in the report to
`./methodology_results/tmp/report_content.json`. Steps 7B and 7C only format that
file and the files in `./methodology_results/`. They do not add or change facts.
`<skill_dir>/scripts/check_report.py content` verifies it.

```json
{
  "header": {"design": "", "part": "", "project": "", "run": "", "date": "", "skill_version": ""},
  "summary": [
    {"id": "TIMING-6", "severity": "Critical Warning", "count_before": 2, "count_after": 0,
     "status": "Resolved"}
  ],
  "total": {"count_before": 0, "count_after": 0, "note": "4 Critical Warnings resolved"},
  "resolutions": [
    {"check_ids": ["TIMING-6", "TIMING-7"], "instances": ["TIMING-6#1", "TIMING-7#1"],
     "root_cause": "", "analysis": "", "case": "CASE 1", "mode": "AUTO", "decision": "",
     "action": "", "before": "n/a", "after": "set_clock_groups ...", "diff_summary": ""}
  ],
  "waivers": [
    {"check_id": "TIMING-18", "justification": "", "objects": "get_ports {...}",
     "before": "", "after": "create_waiver ..."}
  ],
  "remaining_actions": [{"id": "TIMING-18", "count": 69, "action": ""}],
  "constraint_diff": {"added": 5, "removed": 0, "summary": ""},
  "waiver_diff": {"created": true, "added": 4, "removed": 1, "summary": ""}
}
```

Rules for the file:

- `summary[].status` starts with one of `Resolved`, `Waived`, `User Decision Required`,
  `New`, `Unresolved`, `Not Supported`
- `count_before` and `count_after` are read from `methodology.json` and
  `methodology_after_fix.json`. A check that is absent from a report has count 0
- `summary` has a row for every check in either report
- Every `Resolved` check is named in a `resolutions[].check_ids`
- Every `Waived` check has a `waivers` entry
- Every check that is `User Decision Required`, `New`, `Unresolved` or
  `Not Supported` has a `remaining_actions` entry
- `case` is the `CASE n` number the reference page labels, or `null` when the
  page has a single resolution
- `constraint_diff` and `waiver_diff` `added` and `removed` come from the
  `.diff` files, not from counting by eye
- `waiver_diff.created` is `false` and its other fields may be empty only when no
  waiver was created

- Markdown formatting: [METHODOLOGY_RESOLUTION_REPORT_DETAILS.md](METHODOLOGY_RESOLUTION_REPORT_DETAILS.md)
- HTML formatting: [HTML_REPORT_TEMPLATE.md](HTML_REPORT_TEMPLATE.md)
- Filled examples: `<skill_dir>/examples/methodology_resolution_report.md` and
  `<skill_dir>/examples/methodology_resolution_report.html`

Both files carry the same facts. A section that is omitted from one is omitted
from the other.

## Header

- Design name
- Part
- Project
- Run
- Date
- Skill version

## 1. Summary

One row for every check that appears in the before report or the after report,
plus a Total row.

| Column | Content |
|--------|---------|
| Check ID | Rule id, including non-`TIMING` ids |
| Severity | Error, Critical Warning, Warning, or Info |
| Count Before | Instance count in `methodology.json` |
| Count After | Instance count in `methodology_after_fix.json` |
| Status | Exactly one status from the list below |

Status values:

| Status | Use when |
|--------|----------|
| Resolved | The check was fixed in this run |
| Waived | A methodology waiver was created for the check |
| User Decision Required | The check needs a design-intent answer that was not given |
| New — \<next step\> | The after report introduced the check. Name the next step |
| Unresolved | The check was in scope and was not fixed or waived |
| Not Supported | The id is not a `TIMING-*` id, or the reference says this skill does not resolve it |

The Total row sums Count Before and Count After and states how many violations
were resolved.

## 2. Resolved violations

For each resolved check, or one combined entry when the same action resolves
several checks:

- The instance ids (for example `TIMING-6#1`)
- The root cause
- The analysis. When the reference lists more than one resolution, name the
  `CASE` number that was applied and the decision that selected it
- Whether the action was AUTO or USER, and any user decision recorded
- The constraint or waiver action that was applied

### Constraint change

For each constraint edit, record:

- The before text. Use `n/a` when the constraint is new
- The after text, the full Tcl command
- An aspect table of what changed (counts, or the clock fields below)
- A one-sentence diff summary

When the edit changes `create_clock` or `create_generated_clock`, the aspect
table uses these fields. The Constraint cell is the full Tcl command, including
`-source`, `-name`, `-multiply_by`, `-divide_by`, and the target. Period is a
number plus `ns`. Waveform is `{rise fall}`.

| Aspect | Period | Waveform | Clock Definition Object | Constraint |
|--------|--------|----------|-------------------------|------------|
| Before | | | | |
| After | | | | |

When a resolution writes its constraints to a separate file (`./methodology_results/TIMING-11.xdc`
for TIMING-11 CASE 1), the after text is the file path, the number of pairs and constraints it
holds, and its first lines, not the whole file. The file is a deliverable of the run. Name it in
the diff summary and note that `constraints_after_fix.xdc` (section 5) does not contain its
constraints.

### Conflicting constraints

When two definitions of the same clock were compared, record both definitions
with the same fields as the clock table, and state which case was selected.

## 3. Waivers

When this run created methodology waivers, record the same before/after facts
used for a constraint change:

- Before: the full text of `methodology_results/waivers.rpt` (the setup snapshot)
- After: the full text of `methodology_results/waivers_after_fix.rpt`
- An aspect table of which checks were waived, with the `-user`/`-description` you collected
  when creating the waiver (`report_waivers`' table has no user/description column, so this
  comes from your own waiver-creation action, not from re-parsing the artifact)
- A one-sentence diff summary

`waivers.rpt` is not overwritten. When no waivers were created, state that none
were applied and do not describe an after file.

## 4. Remaining actions

One entry for every check whose status is not Resolved:

- Check id and count
- What is still required (user decision, CDC review, RTL change, or another named next step)

A waived check stays here when a real constraint should replace the waiver later.
Name that remaining action.

## File diffs (sections 5 and 6)

Sections 5 and 6 each diff one before/after pair of files from the output
directory. Both sections use the same rules:

- Read both files from `./methodology_results/` at report time. Never retype
  them from memory and never show only the changed lines. The reader needs the
  whole file on each side.
- **Before is the left side. After is the right side.**
- Mark removed lines (present only in the before file) and added lines (present
  only in the after file). Unchanged lines are shown as is.
- Produce the line marking from a real diff of the two files, not by eye:

```sh
diff -u ./methodology_results/<before> ./methodology_results/<after> > ./methodology_results/tmp/<name>.diff
```

- Every section ends with a diff summary: the file names, the number of lines
  added, the number removed, and one sentence on why.
- If the two files are identical, still write the section. State that the files
  are identical and show the file once.
- If the after file does not exist, write the section and state which step did
  not run. Do not drop the section silently.

| Section | Before (left) | After (right) | Diff file | Required when |
|---------|---------------|---------------|-----------|---------------|
| 5. Constraint file diff | `constraints.xdc` | `constraints_after_fix.xdc` | `tmp/constraints.diff` | Always. Step 6 reads `constraints_after_fix.xdc`, so it always exists |
| 6. Waiver file diff | `waivers.rpt` | `waivers_after_fix.rpt` | `tmp/waivers.diff` | A waiver was created. Otherwise state that `waivers.rpt` is the only waiver file and no waivers were added |

### 5. Constraint file diff

- Full `constraints.xdc` on the left, full `constraints_after_fix.xdc` on the right
- Keep the `# Constraints from file : '<name>'` comment headers, so each
  changed line can be traced to its original file
- Each added or removed constraint is attributable to a check id. Add the
  check id in a comment on the added line, for example `# TIMING-6/7 Fix`
- Diff summary names the check ids behind the change

### 6. Waiver file diff

- Full `waivers.rpt` on the left (the setup snapshot, including the placeholder
  comment when it was empty), full `waivers_after_fix.rpt` on the right
- Both files are `report_waivers -type METHODOLOGY -no_header -show_msgs_with_no_waivers`
  output: a `REPORT DETAILS (METHODOLOGY)` table of `Rule | Severity | Description | Total
  Vios | Remaining Vios | Waived Vios | Used Waivers | Set Waivers` rows, or `No waivers
  found.` when empty
- Every row added on the right names the check id (`Rule` column) that gained a waiver;
  cross-reference the aspect table in section 3 for the user/description behind it
- Diff summary lists each waived check id and its `Set Waivers` count

## Completeness

A report is complete only when all of the following are true. A short list of
statuses or a single sentence about waivers or diffs is not a report.

- The header has design, part, project, run, date and skill version
- The summary has one row per check, with severity, count before, count after,
  status and a Total row
- Every resolved check has its instance ids, root cause, analysis with the
  `CASE` number, action, and before/after constraint
- Every waived check appears in section 3 with its before/after waiver text
- Every check that is not Resolved appears in section 4
- Sections 5 and 6 contain full-file diffs from the files above
- The Markdown and HTML reports contain the same sections and the same counts
- Every number in the summary matches `methodology.json` (before) and
  `methodology_after_fix.json` (after)
- Any new violation in the after report is named in the summary as `New — <next step>`
