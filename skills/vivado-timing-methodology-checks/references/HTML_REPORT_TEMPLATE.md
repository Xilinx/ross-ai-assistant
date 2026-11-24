<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# HTML Resolution Report — Formatting

This file is how `methodology_resolution_report.html` is written. `SKILL.md`
Step 7C points here. What the report contains is
[METHODOLOGY_RESOLUTION_REPORT_CONTENTS.md](METHODOLOGY_RESOLUTION_REPORT_CONTENTS.md).
The filled example is `<skill_dir>/examples/methodology_resolution_report.html`.

**DO NOT invent your own CSS.** Emit the `<style>` block below **verbatim** into
the report, then fill the body with the run's data. This keeps every report
visually identical (block-highlighted summary cells, red/green before-after
diffs).

## Rules

1. Copy the entire `<style>...</style>` block below character-for-character. Do
   not rename classes, change colors, or drop the `!important` flags.
2. Every summary **Status** cell MUST carry one of the status classes so the
   whole cell is background-highlighted (not just the text). Pick the class from
   the Status → class table below.
3. The constraint change section MUST use the `.before` (red) block on the left
   and the `.after` (green) block on the right, inside a `.sidebyside` flex row.
4. Keep the header dark (`th { background:#4a4a4a }`) — do not switch to a blue
   or other theme.
5. When this run created methodology waivers, add the Waiver Change block: the
   same red/green pair, `waivers.rpt` on the left and `waivers_after_fix.rpt` on
   the right. Omit that block when no waivers were created.
6. Sections 5 and 6 of the contents file are full-file diffs. Each is a
   `.sidebyside` flex row with the **before file on the left** and the **after
   file on the right**. Put the whole file in each `<pre>`, not a snippet.
   Mark lines from the output of `diff -u` (see the contents file): wrap each
   line present only in the before file in `<span class='del'>…</span>` and each
   line present only in the after file in `<span class='add'>…</span>`. Leave
   unchanged lines unwrapped. Escape `<`, `>` and `&` in file text.
7. Every section in the contents file has a block in the HTML. Do not collapse a
   section into a sentence, and do not drop the Waiver File Diff block when a
   waiver was created. The HTML is incomplete if it has only a status table.

## Status → CSS class

| Status text                              | class        | fill    |
|------------------------------------------|--------------|---------|
| ✓ Resolved                               | `resolved`   | green   |
| Waived                                   | `waived`     | purple  |
| ⚡ New — <next step>                      | `new`        | yellow  |
| ⚠ User Decision Required                 | `user`       | blue    |
| Unresolved                               | `user`       | blue    |
| ✗ Not Supported                          | `notsup`     | red     |
| Not Applicable — <reason>                | `na`         | grey    |
| Out of Scope — not targeted this run     | `oos`        | grey    |

## Canonical `<style>` block (emit verbatim)

```html
<style>
body { font-family: Arial, sans-serif; margin: 20px; }
h1 { color: #333; }
h2 { color: #555; border-bottom: 1px solid #ccc; padding-bottom: 6px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }
th { background: #4a4a4a; color: white; padding: 8px 12px; text-align: left; }
td { padding: 8px 12px; border: 1px solid #ddd; vertical-align: top; }
tr:nth-child(even) { background: #f9f9f9; }
.resolved { background: #d4edda !important; color: #155724; font-weight: bold; }
.waived { background: #e2d5f1 !important; color: #4a148c; font-weight: bold; }
.new { background: #fff3cd !important; color: #856404; font-weight: bold; }
.user { background: #cce5ff !important; color: #004085; font-weight: bold; }
.notsup { background: #f8d7da !important; color: #721c24; font-weight: bold; }
.na { background: #e2e3e5 !important; color: #383d41; font-weight: bold; }
.oos { background: #e2e3e5 !important; color: #383d41; font-weight: bold; }
.before { background: #f8d7da; padding: 10px; border-radius: 4px; margin-bottom: 10px; }
.after { background: #d4edda; padding: 10px; border-radius: 4px; }
.del { background: #f1aeb5; display: block; }
.add { background: #a3cfbb; display: block; }
pre { background: #f4f4f4; padding: 12px; border-radius: 4px; font-size: 13px; white-space: pre-wrap; }
</style>
```

## Document skeleton (fill with run data)

```html
<!DOCTYPE html>
<html>
<head>
<title>Methodology Check Diff Report — <DESIGN></title>
<!-- emit the canonical <style> block here verbatim -->
</head>
<body>
<h1>Methodology Resolution Report — <DESIGN> Design</h1>
<p><b>Part:</b> <PART> &nbsp; <b>Run:</b> <RUN></p>

<h2>Violation Summary: Before vs After</h2>
<table>
<tr><th>Check ID</th><th>Severity</th><th>Count Before</th><th>Count After</th><th>Status</th></tr>
<!-- one row per check; Status cell uses a status class, e.g.: -->
<tr><td>TIMING-6</td><td>Critical Warning</td><td>2</td><td>0</td><td class='resolved'>✓ Resolved</td></tr>
<tr><th>Total</th><th></th><th><SUM_BEFORE></th><th><SUM_AFTER></th><th><SUMMARY_NOTE></th></tr>
</table>

<!-- One constraint-change block per resolved fix (or group of fixes). -->
<h2>Constraint Change: <CHECK_IDS> Fix</h2>
<div style='display:flex; gap:20px;'>
<div style='flex:1'><h3>Before (<SOURCE_XDC>)</h3>
<div class='before'><pre><BEFORE_SNIPPET></pre></div></div>
<div style='flex:1'><h3>After (constraints_after_fix.xdc)</h3>
<div class='after'><pre><AFTER_SNIPPET></pre></div></div>
</div>

<!-- Clock comparison and conflicting-constraints tables from the contents file
     use a normal <table> with the canonical style. Do not invent another theme. -->

<!-- Only when the contents file says waivers were created. -->
<h2>Waiver Change</h2>
<div style='display:flex; gap:20px;'>
<div style='flex:1'><h3>Before (waivers.rpt)</h3>
<div class='before'><pre><WAIVERS_BEFORE></pre></div></div>
<div style='flex:1'><h3>After (waivers_after_fix.rpt)</h3>
<div class='after'><pre><WAIVERS_AFTER></pre></div></div>
</div>

<h2>Remaining Actions</h2>
<table>
<tr><th>Check ID</th><th>Count</th><th>Action</th></tr>
<!-- one row per unresolved / user-decision / not-supported check -->
</table>

<!-- Section 5: always. Whole files, before on the left, after on the right. -->
<h2>Constraint File Diff</h2>
<div style='display:flex; gap:20px;'>
<div style='flex:1'><h3>Before (constraints.xdc)</h3>
<div class='before'><pre><FULL_BEFORE_FILE_WITH_del_SPANS></pre></div></div>
<div style='flex:1'><h3>After (constraints_after_fix.xdc)</h3>
<div class='after'><pre><FULL_AFTER_FILE_WITH_add_SPANS></pre></div></div>
</div>
<p><b>Diff Summary:</b> <FILES>, <N_ADDED> added, <N_REMOVED> removed. <WHY></p>

<!-- Section 6: when a waiver was created. Otherwise one sentence saying none were added. -->
<h2>Waiver File Diff</h2>
<div style='display:flex; gap:20px;'>
<div style='flex:1'><h3>Before (waivers.rpt)</h3>
<div class='before'><pre><FULL_BEFORE_FILE_WITH_del_SPANS></pre></div></div>
<div style='flex:1'><h3>After (waivers_after_fix.rpt)</h3>
<div class='after'><pre><FULL_AFTER_FILE_WITH_add_SPANS></pre></div></div>
</div>
<p><b>Diff Summary:</b> <FILES>, <N_ADDED> added, <N_REMOVED> removed. <WHY></p>
</body></html>
```
