<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Clock And Reset Planning

## Implementation Boundary

The reusable implementation is split across:

- `scripts/plan_clocks.py`: strict integer-Hz requirements, bounded generator
  partitions/output ordering, and reset-domain derivation. Standard library only.
- `lib/feasibility.tcl`: independent achieved-clock and connectivity checks.
- `lib/clocks.tcl`: immutable per-design requirements and reset validation.
- `lib/ipiasm.tcl`: context lifecycle, checkpoint and downstream gate integration.
- `scripts/compile_assembly_plan.py`: whitelisted registration/check operations.

This first implementation generates proposals and checks explicitly bound live
contracts. It does **not** automatically configure/probe each proposal, translate
candidate IDs into delivery pin paths, or prove all device families. Continue to
use the configurator's discovery, automation, `require`, capture and attempt ledger
for those steps. Do not present `CANDIDATES_ONLY` as an executable/proven topology.

## Assumptions And Limits

Requirements supply integer Hz and explicit nonnegative tolerance in Hz. Zero
tolerance means exact agreement with the model, not infinite hardware accuracy.
Do not round MHz, invent ppm tolerance, or substitute requested values for actual
output readback. A ppm miss caused by a default input reference stored rounded is
not a tolerance question: set that reference at full precision (`ipcfg::reference_precision`)
and record an assumption. Fractional-Hz, phase, duty, jitter, placement and dynamic clock
switching requirements are not supported by this proposal schema; unknown fields
fail closed. Resolve these requirements with additional documented contracts,
not by dropping them before calling the planner.

The caller owns complete coverage of required clocks/reset sinks and chooses
valid reset qualifiers. `coherent_group` prevents splitting a group but does not
prove phase relationships. Different clock IDs remain different reset domains
even at identical frequencies. No reset sink means no derived reset controller.
Reset outputs share a controller only for the same clock ID, reset source,
source polarity, release policy and lock policy. The initial reset adapter is
`proc_sys_reset`; other release policies are rejected. A reset's `lock` names the
generator output that qualifies release (a clock wizard's `locked`); an empty `lock`
declares a clock with no lock output, such as a processing-system reference clock,
and the checker then requires `dcm_locked` left unconnected.

All frequency results are Vivado BD metadata, not measured clocks or post-route
timing. Native `validate_bd_design`, CDC checks and implementation timing remain
separate requirements. The reset checker verifies the declared lock connection,
not the analog lock behavior, correctness of a user-defined lock-combination
circuit, or auxiliary/debug reset behavior. Native validation is still required.

## Family Discovery

Use documentation as guidance and the selected live catalog/schema as authority.
Do not encode device electrical limits as one global family table.

| Family | Guidance | Required live discovery |
|---|---|---|
| 7 Series / Zynq-7000 | [UG472](https://docs.amd.com/v/u/en-US/ug472_7Series_Clocking), PG065 | Available wizard, primitive, per-output properties, input/VCO ranges for the actual part and speed grade |
| UltraScale / UltraScale+ / Zynq UltraScale+ | [UG572 VCO limits](https://docs.amd.com/r/en-US/ug572-ultrascale-clocking/VCO-Operating-Range), PG065 | Exact part/speed-grade limits; do not reuse 7 Series fractional-divider assumptions |
| Versal | [PG321 output configuration](https://docs.amd.com/r/en-US/pg321-clocking-wizard/Configuring-Output-Clocks), [primitive selection](https://docs.amd.com/r/en-US/pg321-clocking-wizard/Auto-Primitive) | Current wizard VLNV/version, vector/indexed parameter shape, available primitives/output count, actual values and enabled pins |
| Versal Gen 2 | Live catalog plus applicable device documentation | Independently discover `clkx5_wiz` or other supported IP; classic PG321 search results alone do not qualify Gen 2 |

[PG321 grouping](https://docs.amd.com/r/en-US/pg321-clocking-wizard/Auto-Buffer-Selection-and-Clock-Grouping)
describes clock-buffer grouping, not an independent frequency synthesizer.
Output order influences wizard solution priority, so the planner proposes several
orders. It deliberately does not implement a competing PLL solver.

[PG164](https://docs.amd.com/v/u/en-US/pg164-proc-sys-reset) documents reset output
sequencing, polarity and `dcm_locked`. Its older supported-family table is not a
substitute for the current catalog. [UG1387 startup guidance](https://docs.amd.com/r/en-US/ug1387-acap-hardware-ip-platform-dev-methodology/Controlling-and-Synchronizing-Device-Startup)
requires domain-appropriate synchronized startup. Clocks with no fabric reset
consumer do not automatically need a `proc_sys_reset` instance.

Documentation was queried through `vivado_doc_search` on 2026-09-26. A native
Vivado 2026.1 Gen 2 fixture passed on that date using `clkx5_wiz:1.0` and
`proc_sys_reset:5.0`: 100 MHz top-level reference, 50 MHz generated clock,
active-low external reset, and lock-qualified reset output. The regression is
`tests/integration/test_clock_contracts_live.tcl`; it requires an explicitly
authorized disposable project, not a live delivery design.

Thirteen recorded assertions covered setup, acceptance, a 1 Hz mismatch, wrong
driver, stale build, immutable requirements, missing/restored lock and same-design
resume. A subsequent clock checkpoint passed. Native warning, critical-warning
and error counters were zero. Other families, hierarchy crossings, phase/duty
requirements and multi-generator realization have not been natively qualified.
Mocked Tcl tests do not extend this qualification scope.

Native testing found that `get_bd_pins` exposes a `bd_pin` view even for a
top-level port. Endpoint resolution now prefers `get_bd_ports` and normalizes
connectivity peers before deciding whether they drive the net. The test also
keeps validation outside append-only message capture: validation can remove
earlier message records. Its return code, severity counters and native log are
retained separately; capture failures are not suppressed or accepted.

## Proposal Input

Run through the existing bundled-Python-first launcher:

```sh
/bin/sh <vivado-ip-configurator>/lib/ipcfg/vivado_python.sh <vivado-ipi-assembler>/scripts/plan_clocks.py <absolute-request.json> --limit 10
```

Inside Vivado, use `ipcfg::python_script`, not a separate interpreter resolver.
The CLI writes JSON to stdout and returns nonzero for malformed requirements.
The following values are a synthetic format example, not defaults:

```json
{
  "schema_version": 1,
  "fingerprint": {
    "tool": "full Vivado build identity",
    "part": "selected full part including speed grade",
    "board": "board identity and preset content hash, or explicit no-board",
    "ip": "catalog XML content hash and exact IP version",
    "sources": "accepted requirement and source-clock evidence hashes"
  },
  "sources": {
    "fabric_reference": {
      "hz": 100000000,
      "max_outputs": 2,
      "vlnv": "example:ip:generator:1.0",
      "evidence": "live discovery record path"
    }
  },
  "clocks": [{
    "id": "control",
    "source": "fabric_reference",
    "hz": 50000000,
    "tolerance_hz": 0,
    "sinks": ["control/clock"],
    "resets": [{
      "sink": "control/resetn",
      "source": "system/resetn",
      "source_polarity": "ACTIVE_LOW",
      "output": "peripheral_aresetn",
      "release": "proc_sys_reset",
      "lock": "own_generator"
    }],
    "requirement": "accepted-spec#/clocks/control"
  }]
}
```

`sources` entries are reference-plus-generator-capability choices; `max_outputs`
must be observed for the selected configuration, not guessed from this example.
The fingerprints/evidence strings are provenance, not authenticated observations.
Every proposal is `PENDING_LIVE`. The planner explores at most 4096 states by
default and returns at most ten proposals. `search_truncated` means incomplete
search, never hardware infeasibility. The three ordering heuristics are not an
exhaustive permutation search or a minimum-resource proof.

## Live Requirements

Register one `clock_frequency` check per required sink after binding exact pins.
`subject`, `driver`, `input`, and `reference` must resolve exactly. `subject` is a
cell clock pin; `reference` can also be a top-level input port. `input` is the
generator reference input. For a direct clock, use the reference itself as
`input` and `reference`; there is then no generator-input edge to check.

```tcl
set check {id control.clock kind clock_frequency subject /control/clock
    expected 50000000 source accepted-spec#/clocks/control stage clocks
    driver /generator/output input /generator/input reference /reference
    reference_hz 100000000 tolerance_hz 0}
set reset {cell /reset_control clock /generator/output source /system/resetn
    source_polarity ACTIVE_LOW lock /generator/locked
    sinks {{/control/resetn peripheral_aresetn}}}
set sources [dict create spec {<absolute-spec-file>} board {<absolute-preset-files>} ip {<absolute-IP-XML-files>}]
set fingerprint [ipiasm::feasibility_fingerprint $sources]
ipiasm::set_clock_contracts [list $check] [list $reset] $sources $fingerprint   ;# clocks stage
# ... connect, qos and address; then, once the design validates:
ipiasm::validate_bundle
ipiasm::check_clock_contracts
```

Register the contracts in the clocks stage, and check them only after the design
validates. The check reads values Vivado sets only on validation: a clock wizard's
input frequency and a reset synchronizer's `C_EXT_RESET_HIGH`. A design cannot validate
before its interface clocks are wired, so a check in the clocks stage always fails. The
plan compiler refuses it in any build stage, and before validation in a later one.

These are illustrative Tcl dicts; replace all paths with bound design paths.
Source fingerprints must come from the approved scratch
baseline; do not regenerate the expected fingerprint from changed delivery files.
The feasibility fingerprint binds full tool build, release, part and board part.
Include discovery evidence files in `sources` as well. No-board flows must supply an explicit hashed policy
artifact in the `board` role, not fabricate a board file.

| API | Result | Meaning |
|---|---|---|
| `set_clock_contracts checks resets sources fingerprint` | `CLOCK_CONTRACTS:SET` / `CLOCK_CONTRACT_FAIL` | Registers nonempty requirements for this design, no hardware mutation |
| `check_clock_contracts` | `CLOCK_CONTRACTS:OK` / `CLOCK_CONTRACT_FAIL` / `CLOCK_CONTRACTS:NA` | Fresh read-only frequency, reference, reset wiring and polarity check |

Registration survives same-design initialization and cannot be replaced by a
weaker contract. `begin_design` clears it on an explicit new-design transition.
`NA` preserves old plan compatibility and is **not** acceptance for new flows.
Compiled operations must expect `CLOCK_CONTRACTS:SET` or `CLOCK_CONTRACTS:OK`
explicitly. Reset sinks are `{pin output_name}` pairs; support both peripheral
and interconnect outputs with their appropriate active-high/active-low polarity.

## Stage Ownership

1. Requirements: record rates/tolerances/sinks/reset intent, not generator counts.
2. Scratch planning: discover source clocks and generator schema; run proposals
   through configurator automation/configuration and native propagation. Reject
   mismatched achieved values, try bounded alternatives, preserve evidence.
3. Board setup: compare source clocks and fingerprints with the proven baseline.
4. Cells: instantiate the proven generators/reset controllers and register bound
   contracts. Keep functional inventory separate from derived infrastructure.
5. Clocks: wire the proven architecture, propagate, require the live contract check
   and native validation. The clock checkpoint rechecks registered contracts.
6. Connectivity: cover any newly added infrastructure; the checkpoint rechecks.
7. QoS/address: stage entry and checkpoints recheck clock/reset requirements.
8. Final delivery: `final_contract_gate` and `delivery_guard` recheck before export.

Retain the same logical generator slots while trying candidates. The proposal's
`budget_id` labels the architecture, but does **not** reserve mutation attempts.
Each actual configuration still uses the existing persistent configurator budget.
Renaming cells/recreating projects must not be used to bypass exhaustion. Stop
before an exhausted attempt, even if proposals remain.

The legacy `build_clocks`/`fanout_domain` path cannot represent these contracts:
it rounds rates, conflates domains by MHz and assumes one generator. Do not use
it for new exact-frequency planning. It remains only for API compatibility.