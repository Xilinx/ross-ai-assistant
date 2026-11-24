<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# IPI Assembler Workflow

The IPI Assembler turns a natural-language system request, diagram, or validated
DesignSpec into a persistent Vivado IP Integrator block design. It separates
design reasoning from deterministic execution: the agent decides what the
system requires and records that intent, while validated Python and Tcl
procedures perform and check the corresponding Vivado operations.

## Phase summary

| Phase | Purpose | Completion evidence |
|---|---|---|
| 0. DesignSpec and plan | Convert user intent into explicit functional contracts without guessing implementation details. | `SPEC: OK`, `design_spec.json`, `plan.md` |
| 1. Technology binding | Resolve semantic roles to IP versions, interfaces, pins, and properties available for the selected Vivado release, part, and board. | `binding_intent.json`, probe output, `binding_snapshot.json` |
| 2. Compiled assembly plan | Convert the resolved intent into a normalized, whitelisted, reproducible operation list. | Valid `assembly_plan.json` and recorded plan hash |
| 3. Board gate | Select the requested board and preserve every configuration value owned by its board files and presets. | `READY:board_part=...`, `BOARD_INTFS`, open board gate |
| 4. Cells and configuration | Create hardened and catalog IP, apply per-cell configuration, and preserve semantic roles. | `CELLS_OK`, successful intent audits |
| 5. Inventory gate | Prove that all required functional cells—and no unintended duplicates—exist before wiring. | `INVENTORY:OK` |
| 6. Clocks and resets | Build valid clock domains, synchronized reset release, and required CDC boundaries. | `MEM_SYSCLK:OK`, `CLOCK_DOMAIN:OK`, `VIDEOCLK:OK` |
| 7. Connectivity | Connect data, control, interrupt, stream, NoC, memory, and external interfaces using automation first. | Connectivity checks and stage checkpoint |
| 8. NoC traffic gate | Declare bandwidth and traffic class before addressing so the NoC compiler solves the intended traffic. | `NOC_PRE:OK`, open QoS gate |
| 9. Addressing and visibility | Place only fixed windows, derive all other offsets, and restrict each master to its intended targets. | `NOC_POST:OK`, window-scope checks, overmap check |
| 10. Final contract | Reconcile intent with the live design, validate it, and recheck configuration, interrupts, clocks, windows, NoC, and DRAM reachability. | `FINAL_CONTRACT:PASS`, `REQ:OK`, `ASSEMBLY:CLEAN` |
| 11. Delivery | Generate and verify the saved BD, targets, HDL wrapper, rebuild Tcl, status report, and delivery context. | `DELIVERY_GUARD:OK`, `DELIVERY_CONTEXT:OK` |
| 12. Optional compile | Run synthesis or later implementation only when requested and supported by the available constraints. | Successful Vivado run status |

The state card maintained in `plan.md` summarizes the main progression:

```mermaid
flowchart LR
    SPEC_OK --> BOARD_OK --> CELLS_OK --> INVENTORY_OK --> CLOCKS_OK
    CLOCKS_OK --> QOS_OK --> ADDRESS_OK --> VALIDATE_OK --> DELIVERED
```

## Where reasoning and deterministic execution live

Markdown files describe policy and guide reasoning; they do not directly
modify a Vivado design.

### Reasoning layer

The agent performs reasoning while following:

- `SKILL.md`: workflow contract, stage order, gates, escalation policy, and
  rules for choosing automation versus documented fallback behavior.
- `ASSEMBLY_PLAN.md`: technology-binding model, placeholder semantics, and
  allowed operation types.
- `REFERENCE.md`: reusable engineering invariants for boards, NoC, addressing,
  hardened interfaces, clocks, resets, and validation.
- `design_spec.json`: the normalized functional requirements derived from the
  user's request.
- `plan.md`: the agent's auditable interpretation of required inventory,
  roles, connections, clock/reset behavior, memory access, interrupts,
  visibility, fixed windows, exclusions, and unresolved questions.
- `binding_intent.json` and `assembly_plan.template.json`: semantic requests
  that still need live Vivado names substituted.

Reasoning is appropriate for decisions such as:

- deciding which functional blocks and connections the request requires;
- distinguishing user policy from board-owned configuration;
- deriving required bandwidth from the specified workload;
- selecting among functionally different legal implementations;
- deciding whether a Vivado diagnostic justifies an automatic repair;
- asking the user when the specification leaves a material choice unresolved.

The reasoning layer must not invent part numbers, board settings, addresses,
clock rates, bandwidth, instance counts, or topology. Those values must come
from the DesignSpec, board metadata, AMD documentation, or live Vivado
discovery.

### Deterministic execution layer

The following scripts and Tcl libraries perform repeatable transformations and
checks:

- `vivado-spec-generator/scripts/validate_spec.py` validates and canonicalizes the
  DesignSpec.
- `scripts/bind_design_spec.py` creates the live probe, parses its output, and
  substitutes resolved Vivado objects into the plan.
- `scripts/compile_assembly_plan.py` validates the operation whitelist and
  emits the normalized `assembly_plan.json`.
- `scripts/manifest_expect.py` converts the delivered build manifest into the
  constrained Tcl-list digest consumed by `ipiasm::reconcile_spec`.
- `lib/ipiasm.tcl` implements assembly stages, gates, checks, checkpoints,
  reporting, and `ipiasm::assemble_from_plan`.
- `vivado-ip-configurator/lib/ipcfg/ipcfg.tcl` implements deterministic per-cell discovery,
  configuration, automation runs with change reports, and intent audits.

Python is resolved centrally by `ipcfg::python_runtime`. Resolution order is:
the `IPCFG_PYTHON` override, the active Vivado release's bundled interpreter,
then host `python3`/`python`. The result is cached for the Vivado session.
`ipcfg::python_exec` clears inherited Python variables for the child and adds
the bundle's private library directory only while that child runs. Use
`ipcfg::python_status` to report the selected source, version, and executable.

Before Vivado is open, invoke the same policy through
`vivado-ip-configurator/lib/ipcfg/vivado_python.sh` on Linux or
`vivado-ip-configurator\lib\ipcfg\vivado_python.cmd` on Windows. Set `XILINX_VIVADO`,
`IPCFG_VIVADO_ROOT`, or `VIVADO_PATH` to expose the Vivado installation.
This also works in a container when that installation is present or mounted;
if the MCP server is in a separate container, pre-Vivado scripts must run in
that container or receive the same mount.

The compiled operation list is executed directly in the live Vivado session:

```tcl
ipiasm::assemble_from_plan <out>/assembly_plan.json
```

This call is recorded in the Vivado journal together with the normalized plan
path and content hash. Manual repair is allowed only after this execution and
uses uniquely named stage scripts:

```text
<out>/.ipiasm/<run-token>_<stage>.tcl
```

Each stage script starts with `ipiasm::stage`, batches its mutations and
read-only checks, prints one structured result, and is sourced once through
`vivado_execute` with full output. This keeps execution deterministic and
auditable while allowing the reasoning layer to respond to actual Vivado
feedback.

## Detailed workflow

### 0. DesignSpec and implementation plan

The vivado-spec-generator converts prose or diagrams into `design_spec.json`.
The spec audit must return `SPEC: OK` before Vivado is opened; `SPEC: FAILED`
reports blocking findings. The lab gate needs no policy or approval artifact and
does not prove prose completeness or hardware feasibility. The agent then writes
`plan.md`, recording functional requirements rather than a remembered
reference topology.

This phase is important because later automation needs an explicit source for
every count, role, connection, clock, reset, memory obligation, interrupt,
visibility rule, and fixed address. Questions are resolved here so they do not
stall or distort a partially built design.

### 1. Technology binding

The agent writes `binding_intent.json` using semantic roles and version-free IP
candidates. A disposable Vivado project probes the selected release, part, and
board. `bind_design_spec.py` converts the result into
`binding_snapshot.json`, resolving:

- exact VLNV versions;
- supported interfaces and pins;
- property names;
- board and part identity;
- capabilities needed by the assembly plan.

Binding is important because IP names and exposed properties can change across
Vivado releases. It prevents release-specific spellings from contaminating the
reusable reasoning.

When the part, board, standard processing-system family, and every required
operation are already unambiguous through live discovery, the disposable
binder may be omitted. The normalized plan execution remains mandatory.

### 2. Compiled assembly plan

The agent expresses its intended stages in `assembly_plan.template.json`.
Binding substitutes live names, and `compile_assembly_plan.py` rejects raw Tcl,
unknown procedures, unresolved placeholders, and invalid operations before it
emits `assembly_plan.json`.

The plan is executed through `ipiasm::assemble_from_plan` before manual
topology repair. This is important because it proves that the declared plan
and the operations applied to Vivado are the same. Editing the plan after
execution changes its hash and invalidates that evidence.

### 3. Board gate

The project part selects eligible board files. The requested board hint may
break ties, but it cannot substitute another board or fabricate a missing
preset. Board interfaces and board-owned properties are discovered before cell
configuration.

This gate is important because processing-system fixed IO, external memory,
package pins, and board-connected protocols are physical facts. Guessing them
can produce a design that validates but cannot operate on the board.

### 4. Cell creation and configuration

Cells are created in dependency order: hardened processing system, AI Engine
when required, board-served IP, and then remaining catalog IP. Vivado
automation is attempted first. `ipcfg::*` applies and audits per-cell intent,
and semantic names preserve each DesignSpec role.

This phase is important because cell presence alone does not prove correct
configuration. Each DesignSpec parameter must resolve to a live property and
pass the deterministic intent audit.

### 5. Inventory gate

`ipiasm::gate_inventory` compares the live catalog inventory with the
DesignSpec-derived list. Missing cells are created and unintended duplicates
are removed before clocking, wiring, QoS, or addressing continues.

This gate prevents downstream work from being performed against an incomplete
or duplicated topology, where later connections and addresses would have to be
discarded.

### 6. Clocks, resets, and CDC

Automation creates clock/reset infrastructure when possible; documented
helpers provide the fallback. Each active domain receives a reset
synchronizer, generated clocks use their lock signal when available, and
unrelated AXI4-Stream domains receive an explicit clock converter.

Deterministic checks then verify:

- memory-NoC system clocks;
- producer/consumer clock association;
- one resolved domain at each interconnect boundary;
- synchronized reset release and correct polarity;
- MIPI/video clock capacity derived from configured throughput.

These checks are important because `validate_bd_design` can accept nominally
matching clocks or asynchronous reset release that remains unsafe in hardware.

### 7. Functional connectivity

The agent maps each DesignSpec connection to live interfaces. Automation is
attempted first, with `can_connect` and legal-target queries used before
fallback wiring. The stage includes:

- stream and memory datapaths;
- processing-system control fanout;
- interrupts to processor fabric inputs;
- NoC ingress and hardened endpoints;
- external board interfaces;
- AI Engine configuration connectivity when required.

This phase is important because topology is an implementation choice, but
functional reachability is a contract.

### 8. NoC traffic gate

For designs containing a NoC, bandwidth is calculated from the DesignSpec or
configured interfaces and assigned with a documented traffic class.
`check_noc_pre_address` verifies the traffic declaration and published memory
apertures before addressing starts.

This ordering matters because applying NoC traffic properties after addresses
have been assigned can invalidate mappings while leaving a superficially valid
design.

### 9. Address map and visibility

Only software- or hardware-fixed windows are placed explicitly. Vivado assigns
the remaining offsets. The agent then restricts master visibility and verifies
exact fixed-window allow-lists, overmapping limits, NoC post-address state, and
NoC legality.

This phase is important because successful address assignment does not mean
that each master sees only the devices it should. Unrestricted mappings can
expose unintended processing-system windows or create route conflicts.

### 10. Final contract and DRAM reachability

The final phase normalizes simulation models, rechecks inventory, reconciles
the live design with the functional contracts, and runs the bundled final
contract gate.

The DRAM list must contain one address-space pattern for every DesignSpec block
that reads or writes external memory. `check_required_dram_masters` verifies
that each pattern resolves to a live master address space with a mapped DRAM
segment. This catches a VISP, DMA, codec, compositor, PCIe endpoint, or other
master that is physically connected to a fabric but cannot address memory.

The final contract also rechecks:

- board, inventory, and QoS gate state;
- fixed-window visibility;
- configuration requirements;
- interrupts;
- clock/reset and CDC rules;
- NoC solution and memory subsystem;
- plan-execution hash;
- validation diagnostics and design fingerprint.

This bundled check is important because topology or QoS changes can invalidate
addresses and earlier assumptions. A standalone `validate_bd_design` pass is
not sufficient delivery evidence.

### 11. Delivery

After the first successful final validation, deterministic Vivado commands:

1. save the block design;
2. generate all targets;
3. generate and add the top-level HDL wrapper;
4. export a version-independent rebuild Tcl;
5. write `final_status.json`;
6. verify that every required artifact exists and is non-empty;
7. confirm that the final project and BD—not a probe project—remain current.

This phase is performed immediately so a later optional audit or compile
cannot consume the remaining run budget without leaving usable deliverables.
Vivado-generated wrappers are valid regardless of their byte size.

### 12. Optional synthesis and implementation

Synthesis is run only when requested. Full implementation additionally
requires the appropriate board constraints and implementation inputs.

Keeping compilation optional separates block-design correctness from
downstream implementation work and avoids presenting missing constraints as an
assembly failure.

## Failure and repair behavior

A closed gate blocks only operations after that gate. It does not prevent the
earlier work needed to open it. On failure, the agent:

1. reads the structured failure and Vivado diagnostic;
2. determines whether the repair is implied by the DesignSpec, board metadata,
   product documentation, or live graph;
3. asks the user if multiple functionally different choices remain;
4. edits only the failed stage script;
5. reruns the failed stage and its checks;
6. checkpoints the repaired state and updates `plan.md`.

This preserves the boundary between reasoning and deterministic execution:
the agent chooses a justified repair, while the script applies and verifies it
repeatably.

## Related files

- `SKILL.md` — authoritative runtime contract.
- `ASSEMBLY_PLAN.md` — binder and normalized-plan schema.
- `REFERENCE.md` — reusable technical mechanics.
- `lib/ipiasm.tcl` — deterministic assembly and verification procedures.
- `scripts/bind_design_spec.py` — live technology binding.
- `scripts/compile_assembly_plan.py` — plan validation and compilation.
