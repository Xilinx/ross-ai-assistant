<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# PCIe Link Debug — Reference

Backing detail for [SKILL.md](SKILL.md). The engine is
`sysdbg_analyze(subsystem='cpm')` (the sys-dbg-util CPM sub-block, bundled by
chipscope-mcp), layered with `chipscope_pcie` (LTSSM transition history) and
`chipscope_memory` (config space).

## LTSSM state encoding — owned by the tools, not this document

**This skill deliberately does not maintain an LTSSM state-value table or a fixed
state/graph enumeration.** The LTSSM state set and graph are produced at runtime by
the tools, and the skill consumes them as-is:

- **Current state + decode:** `sysdbg_analyze(subsystem='cpm')` decodes the CPM5
  controller CSR bitfield (`phy_rdy.ltssm_state`) into a `link_state` string and a
  pass/warn/fail status. That decode lives in sys-dbg-util's CPM JSON
  (`CPM5_PCIE_CSR.json`), versioned with the tool.
- **State history + graph:** `chipscope_pcie` reads the PCIe link-debug core and
  returns the LTSSM `current_state` (a name), `visited_states`, per-state
  visitation codes, per-edge traversal counts, and a DOT graph — all emitted by the
  ChipScoPy debug-core IP.

Relying on these runtime values (rather than a table here) keeps the skill correct
for future CPM blocks whose LTSSM encoding differs. Any interpretation the skill
adds must be **encoding-independent** (e.g. "current link verdict = Stage 0
`link_state.status`"; "bounce = re-entry of the current state"), never a hardcoded
state name or number.

**Real encoding is lowercase/dotted and case-varies across sources.** On CPM5
hardware the `chipscope_pcie` state names come back lowercase and dotted — e.g.
`l0`, `r.lock`, `r.eq`, `r.speed`, `polling.active`, `configuration.linkwidth.start`
— and the **DOT graph renders them Title-cased** (`L0`, `R.Eq`), so the `dot` node
names do **not** match the `states` keys. Never string-match a state name, assume a
casing, or join names between `states` and `dot`. Correlate `current_state` /
`visited_states` to `states` by the visitation codes only.

---

## CPM5 module catalog

*Source: sys-dbg-util `data/subsystems/cpm/cpm-sub.json` and the per-module JSON.*

The CPM subsystem is CPM5-only. The pipeline emits one module per xrdb aperture,
then the `groupings` block folds each controller's apertures into one
`CPM5_CONTROLLER{index}` module with a group per aperture.

| Module type | xrdb pattern | Role | Accessibility gate |
|-------------|--------------|------|--------------------|
| `cpm5_slcr` | `CPM5_SLCR` | CPM5 version / presence | `cpm.cpm5-version` |
| `cpm5_pcie_csr` | `CPM5_PCIE?_CSR` | LTSSM link state, PHY handshake, EQ, sticky ISR | `cpm.cpm5-controller-live` |
| `cpm5_dma_csr` | `CPM5_DMA?_CSR` | Negotiated link status (speed/width = info) | `cpm.cpm5-controller-live` |
| `cpm5_dpll_attr` | `CPM5_DPLL?_ATTR` | DPLL PCSRLOCK / CALERROR | `cpm.cpm5-controller-live` |
| `cpm5_pcie_attr` | `CPM5_PCIE?_ATTR` | Build-time attributes (report-only) | `cpm.cpm5-controller-live` |
| `cpm5_gtyp_cfg` | `CPM5_GTYP?_CFG` | Captive GTYP quad status | `cpm.cpm5-captive-gt` |

Controller grouping (`cpm-sub.json`):

```json
"groupings": {
  "controller": {
    "display_name": "Controller {index}",
    "module_type": "cpm5_controller",
    "module_name_template": "CPM5_CONTROLLER{index}",
    "members": ["cpm5_pcie_csr", "cpm5_dma_csr", "cpm5_dpll_attr", "cpm5_pcie_attr"],
    "key_pattern": "CPM5_(?:PCIE|DMA|DPLL)(?P<index>\\d+)_"
  }
}
```

### Accessibility gating (why a controller can be absent)

- **Stage 0 (blanket):** power-domain isolation — `PMC_GLOBAL.DOMAIN_ISO_CNTRL`
  must show CPM isolation removed before *any* CPM read. This fails closed: an
  absent/unreadable isolation read means the read is not attempted.
- **CPM5 presence:** `cpm.cpm5-version` separates "not a CPM5 part" from "CPM5
  present but unpowered" via `CPM5_SLCR.CPM5_VERSION` + `CPM_PCSR.PCR.PCOMPLETE`.
- **Per-controller liveness:** `cpm.cpm5-controller-live` — xrdb exposes every
  controller even when the design instantiates one, so an unused aperture (read
  failure / all-ones) is NOT-AVAILABLE, never FAIL.
- **Captive GT:** `cpm.cpm5-captive-gt` reuses the GT subsystem's cs_server + IBERT
  safe-list check. Without cs_server (`-c`), captive quads are skipped, not read.

---

## What Stage 0 checks (CPM5)

*Source: sys-dbg-util CPM sub-block.*

- **Controller (`cpm5_pcie_csr`):** `link_state` (LTSSM + PHY-ready handshake),
  equalization mismatch/redo, sticky interrupts (`ISR` expected 0).
- **DMA (`cpm5_dma_csr`):** negotiated link status; **speed and width are info
  only** (never validated — the tool cannot know design intent). **Advisory
  downgrade check:** when the advertised max (`cpm5_pcie_attr`) and negotiated
  (`cpm5_dma_csr`) values are both present, negotiated < advertised may be surfaced
  as an *info-level* downgrade (possible SI / partner-capability limit) — never
  FAIL/WARN. On a **PIO design the DMA status aperture is absent**, so this reports
  `link_status = "Status
  Aperture Absent (PIO design)"` (`expected_info`) and there are **no** negotiated
  speed/width properties — their absence is expected, never an error. The trained
  rate is still available from the Stage-1 PCIe debug core (`link_info` /
  `link_speed` / `link_width`).
- **DPLL (`cpm5_dpll_attr`):** `REG_PCSR_STATUS` PCSRLOCK=1, CALERROR=0.
- **GT quad (`cpm5_gtyp_cfg`):** per-lane PIPE ready, PLL status, reset/symbol/PRBS
  errors; quad-level GTREADY, GTPOWERGOOD, CALDONE.
- **Build-time attributes (`cpm5_pcie_attr`):** report-only; never validated. This
  includes the **equalization presets** (default/LP TX preset, per-lane EQ control,
  short-adapt) — surfaced for correlation only. **EQ status itself is a Stage 0
  validated error:** `cpm5_pcie_csr` flags `pl_gen34_eq_mismatch` /
  `pl_gen34_redo_equalization` as `fail`, so an EQ problem already shows up as a
  controller FAIL — this skill does not add a separate EQ-phase decode.

### `link_state` property (the current-state decode)

*Source: `CPM5_PCIE_CSR.json`.* Enumerated over `phy_rdy.ltssm_state` /
`phy_rdy.rcvd`. Illustrative options (consume the tool's output; do not hardcode):

| `link_state` string | Validation status |
|---------------------|-------------------|
| `L0 (Link Up)` | pass |
| `Not Configured (link training disabled)` | warn |
| `PHY Never Ready` | fail |
| `Not in L0` (default) | fail |

The skill reports the string and status verbatim. New/renamed states added to the
tool flow through unchanged.

---

## `sysdbg_analyze` CPM result shape

*Source: sys-dbg-util `common/results/results.py` — `SubsystemResult`
(`schema_version: "subsystem.1.0.0"`).*

```json
{
  "name": "cpm",
  "display_name": "CPM5 PCIe Link Debug",
  "schema_version": "subsystem.1.0.0",
  "summary": {"modules_found": 1, "modules_analyzed": 1, "modules_accessible": 1,
              "error_count": 0, "warn_count": 0, "info_count": 0, "na_count": 0},
  "modules": [
    {
      "name": "CPM5_CONTROLLER0",
      "display_name": "Controller 0",
      "module_type": "cpm5_controller",
      "accessible": true,
      "summary": {"groups_analyzed": 4, "properties_checked": 6, "error_count": 0,
                  "warn_count": 0, "info_count": 0, "na_count": 0},
      "properties": [],
      "groups": [
        {
          "name": "CPM5_PCIE0_CSR",
          "group_type": "aperture",
          "display_name": "PCIe Controller CSR",
          "accessible": true,
          "properties": [
            {"name": "link_state", "display_name": "Controller LTSSM state ...",
             "category": "validated", "value": "L0 (Link Up)",
             "value_formatted": "L0 (Link Up)", "status": "pass",
             "messages": [], "is_computed": true, "details": {}}
          ]
        }
      ]
    }
  ],
  "issues": []
}
```

Notes:
- Property `status` is one of `pass|fail|warn|unchecked|expected_info|not_available`.
  `expected_info`/`unchecked`/`info` are informational and never escalate a verdict;
  `not_available` is unused/unreadable and is never a FAIL.
- **Roll-up:** a controller's verdict is the worst of its **validated property
  statuses** — any `fail` → FAIL; else any `warn` → WARN; else if inaccessible →
  NOT-AVAILABLE; else PASS. A **Stage-1 LTSSM bounce does not contribute** — it is
  diagnostic context, and `link_state` owns the verdict (a bounced-but-in-L0 link
  stays PASS). `summary.overall` is the worst controller verdict, uppercased
  (`PASS|WARN|FAIL|OUT_OF_SCOPE`); per-property/per-controller `status` stays
  lowercase.
- **Two module kinds:** `module_type: cpm5_controller` modules are PCIe controllers
  (carry `link_state`); `module_type: cpm5_gtyp_cfg` modules are captive-GT quads
  (no `link_state`, delegated to `hw-ibert-gt-debug`). Only the former are link
  controllers — do not index controllers by position, and do not treat a GT-cfg
  module as a PCIe link. A real board commonly returns several: e.g. one controller
  in `L0 (Link Up)` (pass), another `Not Configured (link training disabled)`
  (warn), plus captive GTYP modules — this mix rolls up to WARN.
- An unused controller appears as a single module with `accessible=false` and an
  `inaccessible_reason` (only under `--show-unavailable`); otherwise it is absent.
- Enumerate `modules`; never assume a controller count or fixed names.

---

## `chipscope_pcie` response shape (Stage 1)

*Source: chipscope-mcp `impl/pcie/core.py`.* Requires cs_server and a PCIe
link-debug core in the design.

`action='status'` / `action='ltssm_graph'`:

```json
{
  "success": true,
  "pcie_name": "PCIe_0",
  "current_state": "l0",
  "visited_states": ["configuration", "detect", "l0", "polling", "r.cfg", "r.eq", "r.idle", "r.lock", "r.speed"],
  "states": {"l0": 2, "r.idle": 1, "r.lock": 1, "r.eq": 1, "polling": 1, "detect": 1, "disabled": 0, "l0s": 0},
  "transitions": {"detect->polling": 6, "configuration->l0": 3, "r.idle->l0": 231, "l0->r.lock": 231},
  "properties": {"link_info": "Gen4x8", "link_speed": "Gen4", "link_width": 8, "current_state": "l0"},
  "dot": "digraph { ... \"L0\" ... }"
}
```

- **Real names are lowercase/dotted** (`l0`, `r.eq`, `r.speed`, `polling.active`);
  the `dot` node names are Title-cased and do **not** match the `states` keys. Treat
  names as opaque, tool-defined tokens; never string-match or assume casing.
- **State visitation codes** (`states`): `0`=never visited, `1`=visited,
  `2`=current. `current_state`/`visited_states` are derived from these by the tool.
- **`transitions`** maps `"src->dst"` → **cumulative** traversal count over the trace
  window. A large count on an ordinary edge is normal on a healthy trained link — it
  is **not** by itself a bounce. The bounce signal is **re-entry of the current
  state**: `sum(count for "X->current_state") > 1` (the link left its settled state
  and came back). Bounce is **advisory only** — it never changes the `link_state`
  verdict.
- **`properties`** echoes the debug core's scalar fields, including `link_info`
  (e.g. `Gen4x8`), `link_speed`, `link_width` — the trained rate, info-only, useful
  when Stage-0 speed/width are absent (PIO). It also carries a large `trace_data`
  ring buffer.
- **`dot`** is the graph text (always returned). `save_plot` writes a PNG
  best-effort (render failure degrades to a warning; DOT is never lost).
- `action='trace'` returns `{success, pcie_name, trace}` — the raw hierarchical
  state trace.
- **No debug core** → `CORE_NOT_FOUND`; treat as Stage 1 unavailable (degrade), not
  a link failure.

State names are whatever the IP exposes — the skill and this reference never assume
a fixed set.

---

## CPM5 config space (advisory — Stage 2)

*Source: PCI Express Base Specification §7.5 (config-space header); base address per
design — `[TO CONFIRM]` from the CPM5 address map (AM012).*

Config-space reads add host-enumeration context but are **advisory**: confirm the
CPM5 config-space base for the specific design before trusting decoded fields. Do
not hardcode a base address — verify from the device / design address map.

### Type 0 header offsets (relative to the config-space base)

| Offset | Size | Field | Diagnostic use |
|--------|------|-------|----------------|
| 0x00 | 2B | Vendor ID | `0xFFFF` → device not present / link down |
| 0x02 | 2B | Device ID | identity |
| 0x04 | 2B | Command | bit 1 Memory Space Enable, bit 2 Bus Master Enable |
| 0x06 | 2B | Status | error/capability status |
| 0x10–0x24 | 4B ea | BAR0–BAR5 | all 0 → host has not enumerated |

- **Command (0x04) Memory Space Enable = 0 while link up** → host enumerated but did
  not enable the endpoint ("link up, no data transfer").
- **Command (0x04) Bus Master Enable (bit 2) = 0 while link up** → endpoint cannot
  initiate DMA ("link trained, but no traffic") — a classic "link up, nothing works"
  cause. Advisory.
- **All BARs = 0** → not enumerated (enumeration timing / BAR sizing). Warm-reboot
  the host; if it persists, review BAR sizes.

Mark every decoded config-space finding as advisory in the report.

---

## AER — out of scope

Advanced Error Reporting decode is **not** part of this skill. AER is a standard
PCIe capability that a CPM5 endpoint can carry, but the sys-dbg-util CPM sub-block
does not decode it — none of the CPM5 module JSONs expose AER status — so this skill
does not report it. Do not fabricate AER decode from raw CPM5 reads; report AER as
out of scope (walk the config-space AER capability via `hw_manager` if it is needed).

---

## IBERT delegation

GT eye scans are delegated to **`hw-ibert-gt-debug`**. Stage 0 reports captive-GTYP
quad status (`cpm5_gtyp_cfg`, gated by `cpm.cpm5-captive-gt`, which reuses the GT
subsystem's cs_server + IBERT safe-list check). When SI-like symptoms appear
(bounce, speed/width downgrade, PHY-not-ready), cite that captive-GT status and hand
off to `hw-ibert-gt-debug` for the transceiver-level eye scan. This skill runs no
eye scan itself.

---

## Report JSON schema

*The shape of the skill's `report_data.json` output.*

```json
{
  "schema_version": "hw-pcie-link-debug/2.0.0",
  "backend": "chipscope-mcp",
  "device": {"part": "xcvp1202", "tool_version": "2026.2.0"},
  "block": {"type": "CPM5", "in_scope": true, "reason": null},
  "controllers": [
    {
      "name": "CPM5_CONTROLLER1",
      "display_name": "Controller 1",
      "accessible": true,
      "module_type": "cpm5_controller",
      "link_state": {"value": "L0 (Link Up)", "status": "pass", "messages": []},
      "isr": {"value": "0x0", "status": "pass"},
      "dpll": {"value": "PCSRLOCK=1 CALERROR=0", "status": "pass", "details": {}},
      "speed_width_info": null,
      "ltssm": {
        "current_state": "l0",
        "visited_states": ["configuration", "detect", "l0", "polling", "r.cfg", "r.eq", "r.idle", "r.lock", "r.speed"],
        "transitions": {"r.idle->l0": 231, "l0->r.lock": 231},
        "reentered_current": true,
        "reentry_count": 234,
        "properties": {"link_info": "Gen4x8", "link_speed": "Gen4", "link_width": 8},
        "available": true
      },
      "config_space": {"vendor_id": "0x10EE", "command_mem_enable": 1,
                       "bars_all_zero": false, "advisory": true},
      "captive_gt": {"delegated_to": "hw-ibert-gt-debug", "status": "pass"}
    }
  ],
  "summary": {"controllers_total": 4, "controllers_accessible": 4,
              "pcie_controllers": 2, "primary_controller": "CPM5_CONTROLLER1",
              "overall": "WARN"}
}
```

- `block.in_scope=false` (with `reason`) when the part is not a live CPM5 → the
  skill STOPs.
- `overall` is `FAIL` if any accessible controller's `link_state.status` is `fail`,
  `WARN` if any is `warn`, else `PASS`. **A bounce never changes `overall`** — it is
  advisory context. The example above is `WARN` because *another* controller
  (`CPM5_CONTROLLER0`) is `Not Configured`, not because the L0 controller bounced.
- `summary.primary_controller` names the trained PCIe controller the single-value
  views describe (chosen by role/state, never by index); `pcie_controllers` counts
  the `cpm5_controller` modules.
- `speed_width_info` may be `null` (PIO design has no DMA status aperture) — not an
  error; the trained rate is in `ltssm.properties.link_info` when present.
- `ltssm.available=false` when no PCIe debug core is present (Stage 1 degrade).

---

## Report Template (REPORT.md)

```markdown
# PCIe Link Debug Report

**Backend:** chipscope-mcp | **Part:** <part> | **Tool:** <version>
**Block:** CPM5 (in scope)

## Controller Summary

| Controller | Accessible | Link State | Status | Current LTSSM | Bounce (advisory) |
|-----------|-----------|-----------|--------|---------------|-------------------|
| CPM5_CONTROLLER0 | yes | Not Configured (link training disabled) | warn | n/a | n/a |
| CPM5_CONTROLLER1 | yes | L0 (Link Up) | pass | l0 | re-entered 234× |

## LTSSM Transition Graph

<primary controller's current state, visited states, per-edge counts; see
ltssm_graph.png. Bounce/re-entry is advisory — the verdict is the link_state.>

## Configuration Space (advisory)

| Field | Value |
|-------|-------|
| Vendor ID | <vid> |
| Memory Space Enable | <0/1> |
| BARs all zero | <yes/no> |

## Diagnosis

<link verdict from link_state; bounce from transitions; IBERT handoff>
```

---

## Troubleshooting guide (encoding-agnostic)

Guidance for turning the Stage 0/1 facts into a root cause. It is deliberately
**encoding-independent** — it never names a specific LTSSM code, state number, or
per-generation eye threshold (those are tool- and generation-specific and come from
the runtime output). Use the *shape* of the evidence, not hardcoded values, and let
the runtime state names/counts decide which case applies — never string-match a
state name.

### Reason about the current state (Stage 0 `link_state` + Stage 1 `current_state`)

1. **Link reported up** (`link_state` = pass, settled at the "up" state). Healthy.
   If Stage 1 shows the settled state was re-entered (`reentered_current`), that is
   advisory SI/instability context only — do not downgrade. Consider an IBERT eye
   scan when re-entry is high.
2. **Never progressed** (current state stuck early, far from up). The link never
   trained. Work the early stages in order: is the PHY ready (Stage 0 PHY-ready
   handshake)? Is the reference clock / GT power present? Is the link partner present
   and out of reset?
3. **Progressed then fell back** (visited the up state but settled below it, or
   repeated fallback edges). The link trained but is unstable — a signal-integrity or
   equalization / speed-negotiation problem. Correlate DPLL lock and EQ mismatch/redo
   (Stage 0), then hand off to `hw-ibert-gt-debug`.

### Generic root-cause categories (map evidence → area)

| Evidence shape | Likely area | Next step |
|----------------|-------------|-----------|
| PHY never ready / stuck at the earliest state | GT PHY / refclk / power | `hw-sysmon` (supply), `hw-ibert-gt-debug` (PHY) |
| Stuck in link-partner detect | No partner / partner in reset / cabling | Check partner + physical link |
| Stuck in speed/EQ negotiation, or EQ mismatch/redo (Stage 0) | Signal integrity / equalization | `hw-ibert-gt-debug` eye scan; review EQ presets |
| Reached up but re-enters recovery (`reentered_current`) | Marginal SI | `hw-ibert-gt-debug` eye scan |
| Up but no traffic; BME/MSE clear (Stage 2) | Host enumeration / driver | Check host enumeration; warm reboot |
| Negotiated < advertised (advisory) | SI or partner-capability limit | `hw-ibert-gt-debug`; confirm partner capability |

---

## Sources & References

| ID | Title | Content used |
|----|-------|--------------|
| PG346 | Versal Adaptive SoC CPM Mode for PCI Express | CPM5 architecture, controller/DPLL/GT roles |
| AM012 | Versal Adaptive SoC Register Reference | CPM5 config-space base (advisory), register map |
| — | sys-dbg-util CPM sub-block (`data/subsystems/cpm/`) | Module catalog, `link_state` decode, gating |
| — | chipscope-mcp `impl/pcie/core.py` | `chipscope_pcie` response shape, LTSSM graph |
| PCI Express Base Specification (Rev 5.0) | — | Config-space header layout |
