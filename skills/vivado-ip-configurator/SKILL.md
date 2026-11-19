---
name: vivado-ip-configurator
description: Use for any request to add, insert, instantiate, create, configure, customize, modify, change, enable, disable, update or set parameters of a Vivado IP core or block-design cell, even when this skill is not named and even when the request says to use the Vivado MCP directly - including AXI GPIO, UART Lite/16550, timer, IIC, SPI, interrupt controller, BRAM controller and embedded memory, FIFOs, MicroBlaze and MicroBlaze V, Zynq and Versal processing systems (CIPS, PS Wizard), clocking wizards, resets, NoC and DDR/LPDDR memory controllers, DMA/CDMA, SmartConnect and interconnects, MIPI, video, ISP, VCU and AI Engine IP. Load it before the first Vivado tool call; it drives Vivado through the MCP itself, so configuring the IP with hand-written Tcl skips its legal-value reads and verification. Works one cell per prompt in an open block design, so a design can be built up over many prompts.
license: MIT
compatibility: Requires Vivado 2026.1 or later and the stock Vivado MCP. The workflow runs inside Vivado on Vivado's bundled Python (lib/ipcfg_op.tcl, scripts/ipcfg_bridge.py); nothing else is installed. Vivado 2026.2 and later also supply their shipped IP database.
metadata:
  version: "1.0.3"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

Use the Vivado MCP `start`, `execute`, and `stop` tools. Keep one Vivado session for the whole design: users build a design over many prompts, one cell at a time. The workflow runs inside Vivado: it constructs Tcl, obtains current legal values from Vivado, writes settings, verifies saved native readback, and saves the project; `finish` keeps Vivado and the project open for the next prompt. Never write any other Tcl.

Send every step below with `execute` as `ipcfg_op ` followed by the step's JSON exactly as written, and number it with `"seq"`: 1 for the first step, then one more on every call, including `status` and retries, for the whole Vivado session:

```tcl
ipcfg_op {"seq":4,"op":"describe","queries":["r1","r2"]}
```

The steps below omit `seq`; add it to each. It keeps every command distinct, because the Vivado MCP skips a command identical to one that just succeeded. The step's reply is the JSON object on the **last line** of the output; Vivado's own messages may come before it. A refused step fails the call with `STRUCTURED_ERROR: <what to fix>`. Do not add a second pair of braces: the command is complete when the JSON's own last `}` closes it, and an incomplete command leaves Vivado waiting. A JSON string value must not contain an unbalanced `{` or `}`.

A step on a large IP can outlast the tool call. If a call returns `"stage":"running"` or fails with a timeout, Vivado is still working on that step: send `{"seq":<next>,"op":"status"}`, again while it says `running`, until it returns the step's reply (or the state with `last_step`, the reply of the last step that finished). Never send the step again and never restart Vivado for this.

1. Use a prepared `session_id` when the prompt supplies one; otherwise reuse the Vivado session this conversation already started, and start headless Vivado in the supplied scratch project directory only if there is none. If that directory is named `proj` and does not exist yet, start Vivado in its parent directory instead (`vivado_start` refuses a missing directory); `begin` then creates the directory, project and block design. Do not create it any other way. Keep that one session for every prompt of the design. A prepared session is borrowed and remains open for its owner. Load the workflow in that session once, with this skill's own directory, before the first step: `source -notrace <this skill's directory>/lib/ipcfg_op.tcl` (sourcing it again later is harmless). Run nothing else in Vivado yourself, not even a query: `begin` creates the project and block design in that directory, or reopens the ones already there, so a request to create them if they do not exist is done by `begin`. A project command run earlier makes Vivado open an unsaved in-memory project, or puts the project somewhere other than where the user asked.

2. Begin once per cell. When one request names several cells, use only that cell's task clause and requirements; finish it, then begin the next cell on the same session. Never place another cell's settings in the current ledger. For a single cell, copy the complete task and split every requested feature, value, enable, interface, channel, tile, width, speed, and mode into the requirement ledger:

```json
{"op":"begin","ip":"<catalog name or VLNV>","cell":"<cell>","part":"<part if starting Vivado>","task":"<original task>","requirements":["<feature and value>","<feature and value>"]}
```

Use any `added_task_requirements` returned by begin. Write in `ip` the same IP your requirements describe: when the two name different catalog IPs, begin creates no cell and returns `ip_candidates` to choose from. Never delete or simplify a requirement. If `ip_candidates` are returned, choose a returned ID:

For a named cell already present in the open design, the helper reads its VLNV from Vivado and ignores an incorrect IP guess.

```json
{"op":"choose_ip","candidate_id":"i1"}
```

Choose by the task words, display name, and ranking evidence. Ambiguous choices are validated against writable live parameters. Keep a passed or inconclusive choice; choose a replacement only after definitive rejection. A candidate unsupported on the current part may include `example_parts`; change part only when the task explicitly asks for that device or for a part change. When the IP the task names is not available on the target part, the requested function is the requirement: choose the supported IP that performs it (a returned stand-in, an `alternative_to` candidate, or the `c*` same-category candidate whose description fits, as a device's own clocking wizard, processing system or interconnect replaces an earlier-generation or unavailable IP), configure it, and state the substitution in the report. Ask instead only when the user forbade substitutes or required that exact IP.

3. Discover all unresolved settings in batches of at most 12. Prefer the requirement text by sending IDs only:

```json
{"op":"describe","queries":["r1","r2","r3"]}
```

For a more precise lookup use `{"requirement_id":"r2","query":"channel 1 output clock frequency"}` inside `queries`. Batch every unresolved requirement before selecting. Each description contains candidates scoped to its requirement. Use only returned IDs. If begin returned a complete `catalog`, use its IDs directly and skip describe where possible.

`begin` reports `discovery`. With `"database"` (Vivado 2026.2 and later), describe also searches labels and help text, so the task's own words work. With `"live"` (Vivado 2026.1), describe matches only Vivado's parameter names, such as `CLKOUT1_REQUESTED_OUT_FREQ` or `C_GPIO_WIDTH`. When a live description returns no candidate that fits, describe that requirement once more with a query in Vivado's naming style: the interface or channel name as Vivado spells it, plus the quantity (`CLKOUT2 FREQ`, `GPIO2 WIDTH`, `DATA_WIDTH`). Describe is limited per requirement, so do not repeat a paraphrase of a query that already failed.

Candidate fields are source evidence: `key`, label/help, GUI context, current/default value, type, choices, shape, units, write mode, and live range. SQLite supplies searchable names, help, and enum explanations; it never supplies legal ranges. Ranges come from the current live configuration and apply rechecks them after related settings change. `port_dependencies` identifies source controls for requested ports. Select the required enable/use controls as well as data values. `enabled:false` means a controller must be selected first. `write_mode:assert` means verify the propagated/constant value rather than write it.

A `selection_bundle` represents every returned member of an explicitly counted family such as three tiles. Select the bundle once so no instance is omitted. Dictionary `subkey` candidates preserve unrelated fields. A candidate with `shape:"dictionary"` accepts only the returned field names. `semantic_labels` and `text_choice` explain source enum encodings; choose the complete encoding containing the task's distinguishing words. Never guess a range, enum, subkey, or candidate ID.

4. Send all grounded selections in one call when possible:

```json
{"op":"select","selections":[
  {"requirement_id":"r1","candidate_id":"r1.p1","value":4},
  {"requirement_id":"r2","candidate_id":"r2.p2","value":"PCIe0_x2_10GbE"}
]}
```

Several candidates may implement one requirement. For a vector, patch only requested zero-based indices: `"value":{"indices":{"0":250,"1":200}}`. Keep numeric values and Boolean enable vectors separate. For a propagated result select its candidate with `"mode":"assert"` and configure its owning source setting. For a source encoding whose documented meaning supplies an exact requested literal, `covers` may name that literal and `grounding` must state the returned source mapping. Do not use `covers` to excuse a missing mapping.

If a requirement is only external context and no IP setting implements it, describe it once and then waive it with a reason. Never waive requested behavior or a value:

```json
{"op":"waive","requirement_id":"r3","reason":"board-level clock domain name; no IP parameter"}
```

Follow returned repair data exactly. `unchanged_selections` means the selection changed nothing; inspect its alternatives. `value_mismatches` means the selected number differs from the task. `same_quantity_unselected` lists another setting, offered for the same requirement, that names the quantity you just set and still holds a different value (two lane-count parameters, for example): select it too when the task's value applies to it, in the same select. `replaced_selections` lists writes removed during failed-apply repair; resend any still needed in the same batch. `unretained_field_repairs` supplies the complete replacement selection batch for provisional nested fields. A repeated query or selection should not be repeated again.

5. Apply after every requirement is selected, covered, or properly waived:

```json
{"op":"apply"}
```

If apply pauses, nothing was written. Correct its listed coverage, value, or unchanged-selection issue, then apply. If apply fails with `next_op:"select_or_finish"`, use returned candidates or repair selections once and apply once more. At most two apply attempts are supported. Do not change requirement text. Native application writes controllers before dependent settings and verifies the fixed ledger.

6. Always finish each cell, including partial or failed work:

```json
{"op":"finish"}
```

`finish` saves and replies `session_preserved:true`, `next_op:"begin"`: begin the next named cell in the same request, or wait for the user's next prompt, on the same session. A later prompt about a cell configured earlier is a new `begin` for that cell. Call stop with Vivado close enabled only when the user says the design is done or asks to close Vivado; never stop it just because one cell is finished. Report configuration only from `saved_result.as_configured`, which is saved native readback. Claim full fidelity only when `full:true`. Report unresolved requirements, waivers, coverage gaps, rejected fields, or unapplied settings when present. Never report intended selections as saved results.

Every execute uses the same `session_id`. Supply `requirements` only in `begin`. A schema error lists unexpected and allowed fields; if `request_schema.corrected_request` is returned, resubmit it once. That correction only removes an exact copy of the immutable ledger and never changes selections or values. Otherwise resolve the listed fields without discarding requirements or guessing settings. Follow `next_op`. On an out-of-stage rejection, use the listed allowed operation; a second identical invalid request closes with a bounded partial result. Use `status` only to recover state or to collect a step that was still running. Never write other Tcl, edit the skill, inspect earlier runs, or read unrelated files.

Local models served through the relay in `scripts/guard_mcp.py` (see [examples/README.md](examples/README.md)) send the same JSON objects as the whole `execute` command, without `ipcfg_op`; `seq` is optional there.

When the `vivado-ipi-assembler` skill is building a whole block design, it drives this skill's bundled `ipcfg` Tcl library directly instead of this JSON workflow; follow [references/ipcfg-library.md](references/ipcfg-library.md) and [FUNCTIONS.md](FUNCTIONS.md) in that case.
