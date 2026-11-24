<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Markdown Resolution Report — Formatting

This file is how `methodology_resolution_report.md` is written. `SKILL.md`
Step 7B points here. What the report contains is
[METHODOLOGY_RESOLUTION_REPORT_CONTENTS.md](METHODOLOGY_RESOLUTION_REPORT_CONTENTS.md).
The filled example is `<skill_dir>/examples/methodology_resolution_report.md`.

## Writing the file

Build the markdown as one string with real newlines, then write it in one pass
to `./methodology_results/methodology_resolution_report.md`. Never write the
literal characters `/n`.

```tcl
set content "# Methodology Resolution Report\n\n"
append content "**Design:** $design_name\n"
append content "**Date:** $timestamp\n\n"
append content "## 1. Summary\n\n"
# ... continue building content
set report_file [open "./methodology_results/methodology_resolution_report.md" w]
puts -nonewline $report_file $content
close $report_file
```

Use the example's heading order: `## 1. Summary` through `## 6. Waiver File Diff`.
Skip a heading the contents file says to omit.

## Markdown table rules

- Use exactly one pipe `|` between columns and at the start and end of each row.
- Separator rows use single pipes only: `|----------|----------|`.
- Never use double pipes `||`. Never end a row with `||` or an extra trailing pipe.
- Use single backticks for inline code inside cells. Do not escape backticks.
- Use triple-backtick fences for Tcl. Write the markdown without escaping
  special characters.

**Correct**

```markdown
| Check ID | Severity | Count    |
|----------|----------|----------|
| Value A  | Value B  | Value C  |
```

**Incorrect** (double pipe breaks rendering)

```markdown
| Check ID | Severity | Count |
|----------|----------|-------||
```

## Summary status cells

Bold the status text: `**Resolved**`, `**Waived**`, `**User Decision Required**`,
`**New — CDC Review Required**`, `**Unresolved**`, `**Not Supported**`.

## File diffs (sections 5 and 6)

Markdown cannot place two code blocks side by side, so before comes first and
after comes second. This keeps the HTML ordering (before left, after right).
Each of sections 5 and 6 has, in this order:

1. `**Before:** <file>` and a fenced block with the whole file
2. `**After:** <file>` and a fenced block with the whole file
3. `**Diff:**` and a fenced `diff` block holding the output of
   `diff -u <before> <after>` from `./methodology_results/tmp/<name>.diff`
4. `**Diff Summary:**` with the file names, lines added, lines removed and why

Show the whole file, not the changed lines. Pair 5 is `constraints.xdc` and
`constraints_after_fix.xdc`. Pair 6 is `waivers.rpt` and `waivers_after_fix.rpt`.

## Constraint and waiver changes (sections 2 and 3)

In the body, put the before block, then the after block, then the aspect table,
then a `**Diff Summary:**` sentence. Use that shape for a single change
(sections 2 and 3 of the example).

~~~~markdown
**Before:** `methodology_results/waivers.rpt`

```tcl
# No methodology waivers at setup
```

**After:** `methodology_results/waivers_after_fix.rpt`

```tcl
create_waiver -type METHODOLOGY -id {TIMING-18} ...
```

| Aspect | Before | After |
|--------|--------|-------|
| TIMING-18 | 69 violations, not waived | 69 violations waived |

**Diff Summary:** Added TIMING-18 methodology waivers.
~~~~

## Clock comparison table

When the contents file requires a `create_clock` or `create_generated_clock`
comparison, render it with these column headers and cell shapes. Do not
truncate the Constraint cell.

```markdown
| Aspect | Period | Waveform | Clock Definition Object | Constraint |
|--------|--------|----------|-------------------------|------------|
| **Before** | 5.000 ns | {0 2.500} | GTX_CLK (primary) | `create_clock -period 5.000 -name GTX_CLK [get_pins ios_0/mmcm_0/CLKOUT2]` |
| **After** | 12.800 ns | {0 6.400} | GTX_CLK (generated) | `create_generated_clock -name GTX_CLK -source [get_pins ios_0/mmcm_0/CLKIN1] -multiply_by 30 -divide_by 48 [get_pins ios_0/mmcm_0/CLKOUT2]` |
```

## Conflicting constraints table

Use the same columns. Label the rows by role (`**Primary**`, `**Redefinition#1**`)
instead of Before and After.
