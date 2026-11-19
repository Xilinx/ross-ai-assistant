---
name: hw-pcie-link-debug
description: >
  Debug CPM5 PCIe link bring-up and link training on live AMD Versal Premium
  devices via ChipScoPy MCP. Gates on a live CPM5 controller, reports LTSSM link
  state / PHY-ready handshake / DPLL / sticky interrupts via
  sysdbg_analyze(subsystem='cpm'), reads the PCIe link-debug core's LTSSM state
  transition graph via chipscope_pcie, inspects PCIe configuration space via
  chipscope_memory, and delegates GT eye scans to hw-ibert-gt-debug. Use when the
  user asks to "check PCIe link", "PCIe status", "LTSSM state", "PCIe link
  training",   "PCIe config space", "PCIe speed", "PCIe width", "PCIe link errors",
  "link degradation", "PCIe Gen3/Gen4/Gen5", "PCIe BAR", "CPM5 PCIe", or "debug
  PCIe link failure".
license: MIT
compatibility: Requires chipscope-mcp 1.5.2+, ChipScoPy 2026.1+, and access to a programmed AMD Versal Premium device containing a CPM5 PCIe controller.
metadata:
  version: "2.0.1"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# PCIe Link Debug (EA)

> **Early Access** — this skill may change before general availability.

Debugs PCIe link bring-up and link training on live AMD Versal hardware via
**ChipScoPy MCP**. **CPM5 is the only PCIe block currently supported** — the skill
detects the CPM version first and reports any other block as out of scope (see
[Scope](#scope) and [CPM Version Gate](#cpm-version-gate-run-first-before-any-analysis)).
The engine is **`sysdbg_analyze(subsystem='cpm')`** (the
sys-dbg-util CPM sub-block, bundled by chipscope-mcp) for the block-type gate and
the validated controller state, layered with **`chipscope_pcie`** for the LTSSM
state-transition history and **`chipscope_memory`** for PCIe configuration space.

See [REFERENCE.md](REFERENCE.md) for the CPM5 module catalog, the `sysdbg_analyze`
CPM SubsystemResult shape, the `chipscope_pcie` response shape, advisory
config-space offsets, and the report schema.

## Scope

**CPM5 only.** This is the block the sys-dbg-util CPM sub-block analyzes. Other
PCIe blocks are out of scope and must be reported as such, never failed:

| Block | Families | In scope? |
|-------|----------|-----------|
| **CPM5** | Premium (VP) | **Yes** — the subject of this skill |
| CPM4 | AI Core, Prime, AI Edge | No (out of scope) |
| CPM6 | Versal Gen 2 | No (out of scope) |
| PL-PCIE4 / PL-PCIE5 | soft block | No (out of scope) |
| UltraScale+ PCIE4C | US+ | No (not Versal; out of scope) |

The CPM5 gate is authoritative: `sysdbg_analyze(subsystem='cpm')` reports the
subsystem as unavailable on a non-CPM5 part rather than emitting failures. When
that happens, STOP and report the block as out of scope — do not attempt raw
register reads to force a result.

When out of scope, point the user at the right path rather than stopping blindly:
CPM4 / CPM6 PCIe and PL-PCIE4/5 (soft block) are not yet covered by a dedicated
skill — use register-based reads via the CPM/PCIE address map (AM012) or Vivado
`hw_manager`; UltraScale+ PCIE4C is non-Versal and outside this suite's scope.

## CPM Version Gate (run first, before any analysis)

**Detect the CPM version before proceeding.** The skill currently supports **CPM5
only**, so the first action after connecting is to establish which PCIe/CPM block
the part carries and stop early if it is not a live CPM5. Do this in two cheap,
authoritative steps — never assume the block from the user's wording:

1. **Identify the device family from the part number** —
   `chipscope_device(action='list')` returns a device block with `part` and
   `family`. `family` is generic (`"versal"` for *all* Versal parts), so it does
   **not** distinguish the CPM generation — map from the **part-number prefix**
   instead (see [Scope](#scope)): `xcvp*` → Premium → expect **CPM5**; `xcvc*` /
   `xcvm*` / `xcve*` → AI Core / Prime / AI Edge → CPM4; Versal Gen 2 → CPM6;
   others (PL-PCIE, US+ PCIE4C) → no CPM5. This is a hint only — Step 2 is
   authoritative.
2. **Confirm CPM5 is live** — `sysdbg_analyze(action='analyze', subsystem='cpm', output_format='json')`. This *is* the
   authoritative CPM-version check: sys-dbg-util's `cpm.cpm5-version` gate reads
   `CPM5_SLCR.CPM5_VERSION` + `CPM_PCSR.PCR.PCOMPLETE` to separate "not a CPM5 part"
   from "CPM5 present but unpowered". Consume its verdict; do not re-read the version
   register yourself.

**Decision (STOP unless CPM5 is confirmed live):**

| Detected | Meaning | Action |
|----------|---------|--------|
| CPM5 present + powered (modules returned) | In scope | **Proceed** to Stage 0/1/2 |
| CPM4 / CPM6 (family not Premium) | Different CPM generation | **STOP** — out of scope, name the detected generation, never FAIL |
| No CPM (PL-PCIE / US+ / soft block) | Not a CPM part | **STOP** — out of scope |
| Premium part but CPM5 unpowered (version reads, `PCOMPLETE=0`) | Present but not brought up | **STOP** — report "CPM5 present but not powered/complete", never FAIL |
| Zero modules on a known-CPM5 part | chipscope-mcp older than 1.5.2 | **STOP** — see [Tool Version Gate](#tool-version-gate-chipscope-mcp--152) |

Record the detected block in `report_data.json` under `block` — `block.type` (e.g.
`CPM5`, `CPM4`, `CPM6`, `none`), `block.in_scope`, and `block.reason` (the STOP
reason when out of scope).

## Tools Used

| Tool | Purpose |
|------|---------|
| `sysdbg_analyze` | **Stage 0 engine.** `subsystem='cpm'` — CPM5 block-type gate + validated per-controller link state, PHY-ready handshake, DPLL lock, sticky interrupts (ISR), and captive-GT status. |
| `chipscope_pcie` | **Stage 1.** LTSSM current state + state-transition graph (history the CSR cannot see): `status`, `ltssm_graph` (DOT + optional PNG), `trace`. Requires a PCIe link-debug core in the design. |
| `chipscope_memory` | **Stage 2 (advisory).** PCIe configuration-space reads (Vendor/Device ID, Command, BARs). |
| `chipscope_session` | Connect to hw_server + cs_server. |
| `chipscope_device` | List/select devices, check resources. |
| `chipscope_scan` | Debug-core discovery. Run once (`action='scan'`) when `debug_ready` is false/absent, before treating an empty `chipscope_pcie(action='list')` as "no core" (Step 4). |
| `chipscope_version` | Read tool version for the tool version gate + report. |
| Agent file tools | Write output files. |

## Tool Version Gate (chipscope-mcp >= 1.5.2)

- **Required: chipscope-mcp >= 1.5.2.** This is the release that supports CPM PCIe
  analysis (`sysdbg_analyze` accepts `subsystem='cpm'` and returns modules).
  Read the running version with `chipscope_version` at the start of the session and
  record it in `report_data.json` under `device.tool_version`.
- **If the connected chipscope-mcp is older than 1.5.2** — or
  `sysdbg_analyze(subsystem='cpm')` is rejected / returns zero modules on a
  known-CPM5 device — STOP and ask for a newer server:
  "This skill requires chipscope-mcp >= 1.5.2 for CPM PCIe analysis. Please connect
  a chipscope-mcp >= 1.5.2 and retry."

---

## Efficiency Guidelines

- **Gate on CPM5 first** — run Stage 0 before anything else. If CPM5 is not
  present/live, stop; there is nothing downstream to read.
- **cs_server is required** — CPM5 captive-GT status (Stage 0) and the LTSSM graph
  (Stage 1) both need a cs_server connection. Connect with both URLs up front.
- **The LTSSM graph needs a PCIe debug core** — `chipscope_pcie` only works when
  the design instantiates a PCIe link-debug core. If it is absent, Stage 0 still
  gives the *current* link state; Stage 1's *history* is simply unavailable.
- **Do NOT** write to PCIe registers without user confirmation — this can affect
  link state.
- **Do NOT** use terminal commands or Vivado Tcl. Use ChipScoPy MCP tools only.

---

## Mandatory Workflow

### Step 1 — Connect (hw_server + cs_server)

```
chipscope_session(action='status')
```

If not connected (cs_server is required for CPM captive GT and the LTSSM graph):

```
chipscope_session(action='connect',
    hw_server_url='TCP:<hw_host>:3121',
    cs_server_url='TCP:<cs_host>:3042')
```

If the cs_server is on a different host than the hw_server, pass it explicitly —
omitting it derives `<hw_host>:3042`, which is wrong when they differ.

---

### Step 2 — CPM version gate

Run the [CPM Version Gate](#cpm-version-gate-run-first-before-any-analysis) now,
before any deeper reads: identify the device family (`chipscope_device`) and confirm
CPM5 is live (`sysdbg_analyze(action='analyze', subsystem='cpm', output_format='json')`). **STOP unless a live CPM5 is
confirmed** — report the detected generation as out of scope, never FAIL. Only when
CPM5 is confirmed do you continue to Stage 0 below.

---

### Step 3 (Stage 0) — Validated controller state — `sysdbg_analyze`

```
sysdbg_analyze(action='analyze', subsystem='cpm', output_format='json')
```

The same call that confirmed CPM5 in Step 2 is the source of the validated state
(reuse its result — no need to call twice). The result is a `SubsystemResult`
(`schema_version: "subsystem.1.0.0"`); see
[REFERENCE.md](REFERENCE.md#sysdbg_analyze-cpm-result-shape).

- **Gate already passed:** the block-type/CPM-version gate ran in Step 2. If it did
  not confirm a live CPM5, you are not here. Do not fall back to raw reads.
- **Controllers:** the pipeline folds each controller's apertures into one
  `CPM5_CONTROLLER{N}` module (per the `groupings` block), with one group per
  aperture. Enumerate them; do **not** hardcode names or a controller count.
- **Per controller, read the validated properties** (verbatim from the tool):
  - `link_state` — LTSSM state + PHY-ready handshake (`pass` = up, `fail`/`warn`
    otherwise). **Consume the tool's decoded string and status; do not re-decode
    a raw LTSSM value against a hardcoded table.**
  - ISR sticky interrupts (expected 0), DPLL lock (PCSRLOCK/CALERROR), and
    captive-GT status.
- **Unused controllers:** an aperture that reads back as unused (read failure /
  all-ones) is reported NOT-AVAILABLE, never FAIL. Report it as unavailable.
- Negotiated **speed/width are info only** — never validated (the tool cannot know
  the target config). Surface them, do not fail on them. **Advisory downgrade
  check:** when both the advertised max (`cpm5_pcie_attr` max link speed/width) and
  the negotiated values (`cpm5_dma_csr`) are present, note negotiated < advertised
  as an **info-level downgrade** ("negotiated Gen3 x4 below advertised Gen4 x8").
  This is advisory context (possible SI / partner limit → consider
  `hw-ibert-gt-debug`), **never a FAIL/WARN**, and is simply absent on a PIO design
  (no DMA status aperture).

**Status taxonomy — consume the tool's status, do not invent one.** Every property
carries a `status`; treat it exactly as sys-dbg-util reports it (the same way DDRMC
treats a controller with no `CAL_STATUS` as *disabled*, never FAIL):

| Tool `status` | Meaning | Report as |
|---------------|---------|-----------|
| `pass` | Verified good | PASS |
| `fail` | Hard failure | FAIL (only these count as errors) |
| `warn` | Live but not proven good (e.g. link training disabled) | WARN — never FAIL |
| `expected_info` | Expected-by-design (e.g. PIO build has no DMA PHY aperture; shared-LCPLL lane) | INFO — **never FAIL, never WARN** |
| `unchecked` / `info` | Informational (e.g. negotiated speed/width) | INFO — never validated |
| `not_available` | Aperture/controller unused or unreadable | NOT-AVAILABLE — never FAIL |

**Per-controller roll-up (verbatim, deterministic):** a controller's verdict is the
worst of its **validated property statuses** — any `fail` → **FAIL**; else any `warn`
→ **WARN**; else if inaccessible → **NOT-AVAILABLE**; else **PASS**.
`expected_info`/`info`/`unchecked` never escalate the verdict. **A Stage-1 LTSSM
bounce is diagnostic context, NOT a verdict input** — sys-dbg-util's `link_state`
owns the verdict, so a controller in L0 stays **PASS** even when its transition
history shows recovery re-entry (real trained links accumulate large cumulative
transition counts; see [Step 4](#step-4-stage-1--ltssm-state-transition-graph--chipscope_pcie)).

**Negotiated speed/width are optional.** On a PIO design the DMA *status* aperture
is absent, so `sysdbg_analyze(cpm)` reports `link_status = "Status Aperture Absent
(PIO design)"` (`expected_info`) and there are **no** negotiated speed/width
properties. Their absence is expected — never treat a missing speed/width as an
error. When you need the trained rate, Stage 1's PCIe debug core carries it
(`link_info`/`link_speed`/`link_width`), still info-only.

---

### Step 4 (Stage 1) — LTSSM state-transition graph — `chipscope_pcie`

The CSR (Stage 0) gives the *current* LTSSM state; the PCIe link-debug core gives
the *history* — which distinguishes "trained cleanly on the first try" from
"bounced through recovery/detect before reaching L0".

```
chipscope_pcie(action='list')                        # confirm a PCIe debug core exists
chipscope_pcie(action='status')                      # current_state, visited_states, states, transitions
chipscope_pcie(action='ltssm_graph',
    save_plot='vivado_agentic_ai_reports/hw-pcie-link-debug/ltssm_graph.png')   # DOT (+ PNG)
chipscope_pcie(action='trace')                        # raw hierarchical state trace
```

- **Encoding-agnostic — consume what the tool returns.** `chipscope_pcie` surfaces
  the LTSSM `current_state` (a name), `visited_states`, per-state visitation codes
  in `states`, per-edge traversal counts in `transitions` (`"src->dst": count`),
  and the DOT graph — all derived from the debug-core IP. **Do not maintain or
  assume a fixed LTSSM state set, numeric encoding, or graph shape** in this skill:
  report the names and counts as given. This keeps the skill correct for future
  CPM blocks whose LTSSM encoding differs.
  - **Real names are tool-defined and case-specific.** On CPM5 hardware the state
    names come back lowercase and dotted (e.g. `l0`, `r.eq`, `r.speed`,
    `polling.active`), and the **DOT node casing may differ** from the `states`
    keys (the DOT renders `L0`/`R.Eq`). Never string-match a state name, assume a
    casing, or cross-reference names between `states` and `dot`. Correlate
    `current_state`/`visited_states` to the `states` map by the visitation codes,
    not by name equality across sources.
- **Interpretation (generic, encoding-independent):**
  - The *current* link verdict comes from Stage 0 `link_state.status`, not from
    string-matching a state name here.
  - **Bounce / instability is ADVISORY, not a verdict.** The meaningful signal is
    **re-entry of the settled state**: the total traversals *into* `current_state`
    is greater than one (the link left its current state and came back — recovery/
    detect re-entry). Do **not** use a naive "any edge count > 1": transition
    counts are *cumulative* over the trace window, so a healthy trained L0 link
    normally shows large counts on ordinary edges — treating that as a bounce
    false-positives (a real-hardware finding). Surface bounce as context ("reached
    L0 but re-entered recovery N×, consider IBERT"), but let Stage 0 `link_state`
    own PASS/WARN/FAIL — never downgrade a PASS link on bounce alone.
- **A zero/empty core reading is only authoritative after a scan.** Like the DDRMC
  and ILA skills, do not treat an empty `chipscope_pcie(action='list')` as
  "no core" until debug-core discovery has run: if the connect/session result
  exposes `debug_ready`, treat `debug_ready: true` as cores discovered; if it is
  `false`/absent, run `chipscope_scan(action='scan')` once and re-list. Only a zero
  result *after* a completed scan is authoritative.
- **No PCIe debug core?** After a completed scan, `chipscope_pcie` returns
  `CORE_NOT_FOUND`. That is a degrade, not a failure: record Stage 1 as unavailable
  and continue with the Stage 0 current state. (Its own error message points back to
  `sysdbg_analyze(subsystem='cpm')` for exactly this reason.)

---

### Step 5 (Stage 2, advisory) — PCIe configuration space — `chipscope_memory`

Config-space reads add host-enumeration context (did the RP enable the endpoint?).
These offsets are **advisory** — verify the CPM5 config-space base for the design
before trusting decoded fields. See
[REFERENCE.md](REFERENCE.md#cpm5-config-space-advisory--stage-2) (`[TO CONFIRM]`).

```
chipscope_memory(action='read', address=<cfg_base + 0x00>, count=1, word_size='w')  # Device/Vendor ID
chipscope_memory(action='read', address=<cfg_base + 0x04>, count=1, word_size='w')  # Command/Status
chipscope_memory(action='read', address=<cfg_base + 0x10>, count=6, word_size='w')  # BAR0-5
```

- **Vendor ID = 0xFFFF** → device not present on the bus / link went down after
  training.
- **Command reg (0x04) Memory Space Enable (bit 1) = 0** while link is up → host
  enumerated but did not enable the endpoint ("link up, no data transfer").
- **Command reg (0x04) Bus Master Enable (bit 2) = 0** while link is up → endpoint
  cannot initiate DMA ("link trained, but no traffic") — a classic "link up,
  nothing works" cause. Advisory.
- **All BARs = 0** → host has not enumerated the endpoint (enumeration timing / BAR
  sizing). Mark decoded config-space findings advisory in the report.

---

### Step 6 — GT eye scan → delegate to `hw-ibert-gt-debug`

This skill does **not** run GT eye scans. When Stage 0 reports captive-GT concerns,
or the link shows SI-like symptoms (bounce, speed/width downgrade), cite the
captive-GT status from Stage 0 and hand off to the **`hw-ibert-gt-debug`** skill for
the transceiver-level eye scan. Record the handoff in the report.

---

### Step 7 — Write Output Files — MANDATORY after every analysis

**This step is not optional and does not depend on the user asking for a report.**
Any prompt that results in a completed PCIe read — "check PCIe link", "PCIe
status", "LTSSM state", "PCIe link training", "why is the link down" — MUST write
all output files before any chat summary.

Write every file under `vivado_agentic_ai_reports/hw-pcie-link-debug/` using agent
file tools (they create missing parents — do **not** shell out to `mkdir`;
terminal commands are forbidden). ChipScoPy exports are confined to
`CHIPSCOPE_MCP_OUTPUT_DIR`.

| File | Format | Content |
|------|--------|---------|
| `report_data.json` | JSON | Block type, per-controller link state/ISR/DPLL, LTSSM current+graph, config space, versions (schema in [REFERENCE.md](REFERENCE.md#report-json-schema)) |
| `REPORT.md` | Markdown | Summary: controller table, link verdicts, LTSSM analysis, config-space (advisory), recommendations, IBERT handoff |
| `ltssm_graph.dot` | DOT | LTSSM graph text from `chipscope_pcie(action='ltssm_graph')` |
| `ltssm_graph.png` | PNG | LTSSM graph image (best-effort; `save_plot`) |
| `pcie_config.csv` | CSV | Decoded config-space reads (advisory) |

**After writing files, confirm in chat:** "Files written to
`vivado_agentic_ai_reports/hw-pcie-link-debug/`: `report_data.json`, `REPORT.md`, ..."

**Verdict casing (report_data.json and REPORT.md must agree):** per-property and
per-controller `status` values are lowercase (`pass|fail|warn|unchecked|expected_info|not_available`);
the rolled-up `summary.overall` is uppercase (`PASS|WARN|FAIL|OUT_OF_SCOPE`). Use the
same casing in REPORT.md.

**Where to find the report / recovery:** the report lives at
`vivado_agentic_ai_reports/hw-pcie-link-debug/REPORT.md` in the working directory
where the session started. If the analysis completed but the files were not written,
re-emit them from the Stage 0/Stage 1 results already read — do **not** reconnect or
re-run the analysis. Only re-read (from Step 2) if the analysis itself failed.

---

## Link Failure Diagnosis

When `link_state` is not `pass`:

1. Read the tool's `link_state` string + messages verbatim (e.g. "PHY Never
   Ready", "Not in L0") — never relabel it.
2. Correlate within the controller: PHY-ready handshake, DPLL lock, sticky ISR.
3. Use Stage 1 `transitions` to tell "never trained" (stuck early, current state
   far from up) from "trained then unstable" (settled state re-entered repeatedly —
   advisory, does not by itself change a PASS `link_state`).
4. Cross-reference `hw-ibert-gt-debug` (captive-GT eye scan), `hw-sysmon` (GT
   supply), and `hw-noc-debug` (NoC timeout at the endpoint).

Correlation is **within-module only** — controller↔quad lane mapping is
design-dependent, so no cross-module rule is hard-wired.

---

## Multiple Controllers

CPM5 can expose more than one PCIe controller. Like the DDRMC skill's multi-DDRMC
handling:

1. **Enumerate** every module from Stage 0 — never hardcode names or a count, and
   **never index by position** (controller 0 is frequently the idle one).
2. **Tell PCIe controllers from captive-GT modules.** Stage 0 returns two kinds of
   module: PCIe **controllers** (`module_type: cpm5_controller`, carry a
   `link_state`) and captive-**GT** blocks (`module_type: cpm5_gtyp_cfg`, no
   `link_state` — these are the captive quads delegated to `hw-ibert-gt-debug`). Only
   the former are link controllers; do not treat a GT-cfg module as a PCIe link.
3. **Read each accessible PCIe controller** (link_state, ISR, DPLL, and its Stage 1
   LTSSM). A controller present but with training disabled reports
   `link_state = "Not Configured (link training disabled)"` (`warn`); an aperture
   that reads unused is NOT-AVAILABLE. Neither is a FAIL, and both can coexist with
   an active L0 controller — that mix is normal and rolls up to **WARN** overall.
4. **Correlate the LTSSM trace to the right controller.** A design usually
   instantiates one PCIe debug core (`chipscope_pcie` → e.g. `PCIe_0`); attach its
   LTSSM history to the controller that actually brought a link up (the accessible
   PCIe controller whose `link_state` is up / most-progressed), **not** controller 0.
5. **Roll up `summary.overall`** as the worst controller verdict: any controller
   FAIL → **FAIL**; else any WARN → **WARN**; else if none accessible →
   **OUT_OF_SCOPE**/no-controllers; else **PASS**.
6. **Name the worst controller** with its exact `link_state` string + status and the
   corroborating ISR/DPLL/LTSSM evidence — never a bare "PCIe failed".

---

## Design-Specific Rules

| Rule | Wrong | Correct |
|------|-------|---------|
| Link status | "PCIe is up" | "CPM5_CONTROLLER0 link_state=L0 (Link Up), ISR=0, DPLL locked" |
| LTSSM | "link training" | "current_state=polling, never reached l0 — see transition graph" |
| Bounce | "link is bad" (downgrading a PASS) | "link_state=L0 (PASS, per Stage 0); LTSSM shows l0 re-entered 231× — advisory: run IBERT for SI" |
| Speed/width | "running at Gen4" / "speed missing = error" | "Negotiated Gen4 x8 (info only); or absent on a PIO design (DMA status aperture absent) — not an error" |
| Block type | "PCIe failed" | "Not a live CPM5 — out of scope for this skill" |

---

## Error Handling

| Symptom | Meaning | Action |
|---------|---------|--------|
| `sysdbg_analyze(cpm)` returns 0 modules on CPM5 | chipscope-mcp older than 1.5.2 | STOP (version gate) |
| Subsystem unavailable / not CPM5 | Wrong block | STOP, report out of scope (never FAIL) |
| Controller aperture unused (all-ones/read fail) | Present but not instantiated | Report NOT-AVAILABLE, never FAIL |
| `chipscope_pcie` → CORE_NOT_FOUND | No PCIe link-debug core in design | Degrade Stage 1; keep Stage 0 current state |
| cs_server not connected | Captive GT + LTSSM graph unavailable | Connect cs_server; without it, skip captive GT/graph |
| `link_state` = PHY Never Ready | GT PHY never initialized | Check refclk/GT power; hand off to hw-ibert-gt-debug / hw-sysmon |
| Vendor ID 0xFFFF | Device not present / link down | Correlate with LTSSM current state + transitions |

---

## Examples

**"Check the CPM5 PCIe link"** → connect → `sysdbg_analyze(cpm)` → CPM5 present,
`CPM5_CONTROLLER0` link_state=L0, ISR=0, DPLL locked → `chipscope_pcie` shows a
clean single traverse to the up state → write artifacts → "Link up (Gen5 x8, info
only), trained cleanly."

**"PCIe link won't train"** → `sysdbg_analyze(cpm)` link_state="Not in L0",
current_state (Stage 1) stuck in Polling → "Never reached L0; speed negotiation
stage. Check partner + refclk; hand off to hw-ibert-gt-debug."

**"Link is up but flaky"** → link_state=L0 (PASS — the verdict stays PASS) but
Stage 1 shows the settled state re-entered many times (recovery churn) → advisory:
"In L0, but re-entered recovery repeatedly — SI-marginal. Run hw-ibert-gt-debug eye
scan on the captive quad." (Bounce is context, not a downgrade.)

**"Debug PCIe on an AI Edge part"** → `sysdbg_analyze(cpm)` reports not CPM5 →
"Out of scope: this skill covers CPM5 (Premium) only."

---

## Integration

**Upstream:** `chipscope_session` (connection), `chipscope_device` (programming — **never auto-program; stop and ask**).
**Complementary:** `hw-ibert-gt-debug` (captive-GT eye scan on PCIe lanes), `hw-noc-debug` (NoC timeout → PCIe endpoint), `hw-sysmon` (supply affecting the GT).
**Downstream:** SI findings → board respin, equalization tuning, partner configuration.

## Metadata

**Keywords:** PCIe, CPM5, LTSSM, link training, link state, PHY ready, DPLL,
configuration space, Gen3, Gen4, Gen5, BAR, Bus Master Enable, link speed, link
width, captive GT, IBERT.
**Confidence Threshold:** 0.80
