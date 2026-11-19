---
name: vivado-ced-configurator
description: Find and configure a Vivado Configurable Example Design (CED) from the live XilinxCEDStore for a natural-language request, in two phases with user approval before IP-level changes. Use when the user asks for an example design, preset or reference design on a board or part (Zynq, MPSoC, MicroBlaze, Versal, Kria, PCIe/CPM, DMA, extensible platform), optionally with settings inside it.
license: MIT
compatibility: Requires Vivado 2026.1 or later (verified on 2026.1 and 2026.2) and the stock Vivado MCP. The workflow runs inside Vivado on Vivado's bundled Python (lib/ced_op.tcl, scripts/ced_bridge.py, engine/); nothing else is installed. The store branch follows the running Vivado. See references/setup.md.
metadata:
  version: "1.1.0"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

Use the Vivado MCP. The workflow runs inside Vivado: you send each step below as a JSON object, and it reads the store at its current commit, runs the design's own option logic and writes all Tcl. Never write any other Tcl.

Send every step with `vivado_execute`, the JSON wrapped in `ced_op {...}` (the outer braces are Tcl quoting; keep the JSON inside them unchanged), and number it with `"seq"`: 1 for the first step, then one more on every call, including `status` and retries:

```tcl
ced_op {{"seq":3,"op":"choose_ced","candidate_id":"c1"}}
```

The steps below omit `seq`; add it to each. It keeps every command distinct, because the Vivado MCP skips a command identical to one that just succeeded.

The step's reply is the JSON object on the **last line** of the output; Vivado's own messages may come before it. A refused step fails the call with `STRUCTURED_ERROR: <what to fix>`. A JSON string value must not contain an unbalanced `{` or `}`.

**Phase 1: choose, set options, instantiate**

1. Start headless Vivado (`session_type` `ipi`) in the supplied scratch directory: the directory's `work` subfolder when it has one, otherwise the directory itself. Keep `session_id`. Load the workflow once, with this skill's own directory: `source -notrace <this skill's directory>/lib/ced_op.tcl`.
2. Begin. `target` is the board short name (`zcu102`, `vck190`, `kv260`, `kcu105`) or an exact part. Split the request into requirements, one feature with its values each:

   ```json
   {"op":"begin","task":"<copy the original request word for word>","target":"<board or part>","requirements":["<feature with values>","<next feature>"],"phase2_approval":"ask"}
   ```

   A preset, mode or configuration the request names is one requirement: "the BRAM and GPIO preset" is `"BRAM and GPIO preset"`, not `"BRAM"` and `"GPIO"`.

   Set `"phase2_approval":"pre_approved"` only when the user's message explicitly says to apply the changes without asking (for example "apply the IP-level changes without asking me"); otherwise `"ask"`.

   Add `"port":"allowed"` only when the user explicitly asks to port, retarget or adapt a design to a board it does not support; otherwise leave it out.

   If it returns `target_candidates`, choose with `{"op":"choose_target","target_id":"t1"}`. If `target_not_found`, begin again with the board short name.
3. `ced_candidates` are ranked by text match only. Choose the design the request describes and whose `supported` is true: `{"op":"choose_ced","candidate_id":"c1"}`. A design listed in `others_supported_on_target` can be chosen with `{"op":"choose_ced","name":"<display name>"}`. `supported:false` or `available:false` means it cannot be built on this target. Never change the user's board or part yourself, and never substitute a different design for one the request names. If the requested design is unsupported or unavailable, or a choice is `refused`, execute finish and report that, naming its `example_boards` and copying `report`, unless `next_op` is `choose_ced` or the reply has a `port` block: then continue. When a reply carries `requested_design` and `next_op: finish`, the design the request names cannot be built here: finish without choosing.
4. Map every requirement, using the returned option and requirement IDs:
   - A CED option (preset, mode, include/exclude, interrupt count, clocks string): `{"op":"set_options","selections":[{"requirement_id":"r1","option_id":"o1","value":"<choice value>"}]}`. Use a listed choice value. For an option without choices, follow `format_example` exactly (same fields, order and spacing). `option_hints` are word-overlap hints, not decisions. If the reply has `conflicts` or `design_messages`, the design's own logic rejected the combination and nothing was kept; choose values it allows. A requirement the design will not allow stays unmapped and is reported unresolved: never defer or waive it, because Phase 2 cannot change design options.
   - A setting inside the generated design (an IP's baud rate, width, cache size, clock, enable): `{"op":"defer","requirement_ids":["r2"]}`. It is handled in Phase 2.
   - Context that the chosen design or target already satisfies (the board name, the design's name): `{"op":"waive","requirement_id":"r3","reason":"<why no setting is needed>"}`. Never waive a requested value because finding it was hard. A software or system outcome ("boots PetaLinux", "runs Linux", "meets timing") is not an IP setting: waive it with that reason rather than deferring it.
5. `{"op":"instantiate"}` creates and validates the design. When `next_op` is `port`, send `{"op":"port"}`: it carries the design to the user's board; report its `port` block (dropped, kept settings, tie-offs). `instantiate_paused` lists unmapped requirements; map them, or instantiate again to report them unresolved. A large design takes minutes to build. Without deferred requirements there is no Phase 2 and nothing to approve: finish straight away.

**Phase 2: propose, then wait for the user**

6. The instantiate reply lists generated `cells` (`k*`: path, IP, external ports). If `more` is true, search with `{"op":"cells","query":"<ip or port words>"}`. Assign each deferred requirement to the cell(s) that implement it:

   ```json
   {"op":"assign","assignments":[{"requirement_id":"r2","cell_id":"k6"}]}
   ```

   Use ports to tell similar cells apart (for example the GPIO wired to `led_8bits`). A requirement for several cells ("all three UARTs") is assigned to each; the reply gives one ID per cell (`r2_k9`, `r2_k14`, ...), and each needs its own selection.
7. Configure with the cell's candidates exactly as in the IP configurator: when an assignment returns a `catalog`, select from it; otherwise `{"op":"describe","requirement_id":"r2","query":"<parameter purpose with units>"}`, then
   `{"op":"select","selections":[{"requirement_id":"r2","candidate_id":"<returned id>","value":"<encoded value>"}]}`. Candidate fields (`range`, `choices`, `enabled`, `current`, `help`, `units`) are source metadata; use them for the encoding. Each requirement gets three describes; a requirement still unresolved after that is reported, not guessed. See [references/phase2.md](references/phase2.md).
8. `{"op":"propose"}` dry-runs every selection on a copy, validates the design, records the exact result and restores the Phase 1 design. If it returns `proposal_paused`, follow its note (select a differing alternative or propose again). If nothing would change (`changes: []`), there is nothing to approve: report any `failures` (for example a value outside the IP's range) and finish.
9. On `stage: awaiting_approval`, follow `next_op`:
   - `ced_apply_phase2` (begin recorded `pre_approved`): send `ced_op {{"seq":<next>,"op":"ced_apply_phase2","proposal_id":"<proposal_id>"}}`, then finish.
   - `ask_user`: **stop**. Your answer gives, in this order: (1) the `requested_changes`, `side_effects`, `validation`, `failures` and `unresolved`; (2) the question whether to apply proposal `<proposal_id>`; (3) last, the final report copied from `report_while_waiting` exactly as you would copy `report` after finish (nothing is applied yet). The question is not the end of the answer; the report is. Call no tool until the user answers. Approved: send the `ced_apply_phase2` step above, which applies exactly the proposed result and verifies it. Declined: finish; the Phase 1 design is kept. Never send `ced_apply_phase2` without the user's approval or a `pre_approved` begin.

**Finish**

10. `{"op":"finish"}` saves, closes the project and returns the report. Require `session_closed=true`, then stop Vivado with `vivado_stop`. Copy `report` (design, Phase 1 options, Phase 2 status, `full`) and each requirement's `status` exactly as returned. `full:false` means something is unresolved, declined or failed; say which. Never report intended values as applied.

Rules: use only returned IDs and choice values. Follow `next_op`. `{"op":"status"}` shows the state. Each step returns only when Vivado has finished it; building a design can take minutes. Never repeat a step that is still running. Errors with `STRUCTURED_ERROR` describe what to fix; an unchanged request that fails three times ends the session as partial.

Local models served through `examples/ced_guard.py` (see examples/README.md) send the same JSON objects as the whole `vivado_execute` command, without `ced_op` (`seq` is optional there), and call `ced_apply_phase2` as a tool.
