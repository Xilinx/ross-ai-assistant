---
name: vivado-spec-generator
description: >
  Convert a natural-language AMD Adaptive SoC or FPGA system request into a
  structured DesignSpec JSON before implementation. Use when a user describes a
  board design, block design, PS/PL/AIE system, external device, Linux-visible
  peripheral, data path, memory path, interrupt, fixed address, clock, or reset
  requirement in prose and another skill needs an implementation-ready spec.
license: MIT
compatibility: Requires Python 3.10+ and the sibling vivado-ip-configurator launcher, which prefers Vivado's bundled Python before a host interpreter. Core schema/audit checks use the standard library and do not need a live Vivado session. Optional model advice requires its external review runner. Implementation handoff uses vivado-ipi-assembler.
metadata:
  version: "1.0.0"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Design Spec Generator

Turn prose and supplied diagrams into an implementation-independent `DesignSpec`.
This skill specifies **what must work**. The downstream assembler decides which
SmartConnect, NoC, reset, adapter, concat, hierarchy, and other infrastructure
implements it.

## Output

Write `<out>/design_spec.json`, then check its schema:

```bash
<skills-root>/vivado-ip-configurator/lib/ipcfg/vivado_python.sh \
  <this-skill>/scripts/validate_spec.py <out>/design_spec.json \
  --canonicalize-out <out>/design_spec.json
```

On Windows, use `vivado_python.cmd` from the same directory. The launcher tries
Vivado's bundled Python before an existing host interpreter, using the active
Vivado environment; the Linux launcher also discovers `vivado` on PATH. No separate
Python installation or notebook setup is required. If discovery fails, locate the
installed Vivado and set `IPCFG_VIVADO_ROOT` to that root before retrying.

Record the stage with `scripts/stage_report.py start --stage 1 --out <out>` before the
first substantive operation and `finish --stage 1 --out <out> --audit <final-audit-dir>`
after the gate. It writes the timing and gate evidence the plan asks for and marks the
stage done only when the audit passed, so do not hand-write timing or evidence files.

[FUNCTIONS.md](FUNCTIONS.md) lists each script's purpose and options; the audit reports
every finding it has, so write the spec and fix what the audit prints.

To draw a spec as a block diagram, for review or to compare it with a supplied diagram, run
`<this-skill>/scripts/spec_to_svg.py <spec>.json <spec>.svg` through the same launcher. It
draws logical dataflow grouped into zones, not the physical interconnect, and never changes
the spec.

Put supporting files, such as a generator script, directly in `<out>/`, or run
`mkdir -p` before writing into a subdirectory. File-creation tools do not create
parent directories. Keep each file you write under about 20 KB: split a spec
generator into modules, for example blocks, contracts and a main script, from the
start. A refused or truncated write, whether from a missing directory or the
per-response output limit, is lost, and the whole file has to be emitted again.

Schema validation prints `SPEC:SCHEMA_OK`, NOT acceptance. Do not continue to
implementation until the audit below prints `SPEC: OK`. For the
complete field definitions, read `references/spec_template.md`.
`--canonicalize-out` migrates only lossless schema aliases; it never invents a
block, connection, address, or other functional requirement.

## 1. Extract stated facts

Capture:

- exact target device and `board_part` when given;
- functional blocks named or unambiguously implied by the request;
- operating-point parameters: lanes, line rate, format, resolution, frame rate,
  throughput, widths, depths, modes;
- external interfaces and software/OS consumers;
- explicit fixed addresses, ranges, and the reason each is fixed.

Keep source wording in block descriptions or contract purposes when useful.
Never turn an example, implementation artifact, or likely default into a fact.
Preserve each named architecture function as its own semantic block even when Vivado
implements it inside another block. For example, an integrated PCIe controller is still a
`pcie_endpoint` function in the DesignSpec; physical integration belongs to technology
binding, not requirements extraction.

Use the canonical parameter names in `references/spec_template.md`. Do not invent aliases.
For camera receivers these are `line_rate_mbps`, `active_lane_filter`,
`user_data_type_filter`, `dphy_register_interface`, and `isp_bridge`. Record every
receiver feature the request says to retain; an omitted feature is dropped downstream.

## 2. Build functional contracts

Every required behavior must become at least one contract:

| Requirement | Contract |
|---|---|
| data or control endpoint A must connect to B | `connections` |
| software/master must control a device | `control_reachability` |
| interrupt outputs must reach a processor | `interrupts` |
| master must read/write system memory | `memory_reachability` |
| software/architecture fixes an address | `fixed_address_windows` |
| masters must be allowed/denied visibility | `visibility_policy` |
| active interface needs clock/reset behavior | `clock_reset` |
| OS/device-tree must expose hardware | `software` |

Use semantic endpoint names such as `camera_receiver.video_out`,
`linux_apu.control`, and `ps.fabric_irq`. Do not encode SmartConnect port numbers,
hierarchy paths, or generated infrastructure unless the user explicitly makes
them part of the interface contract.

Set an interface's `scope` to `top_level` when the requirement needs a design
boundary with its own address space (for example, an external AXI ingress).
The default is `internal`; `external` means an off-chip interface. This distinction
prevents an external master space from being confused with its target slave segment.

## 3. Separate function from implementation

Do not add blocks merely because a reference design commonly contains them, unless that
reference design is one of the spec's sources (see *Reference designs are sources* below):
then its named units are requirements like any other source's.
In particular, omit implementation-only interconnect, reset, constants, slices,
concats, FIFOs, width adapters, and debug IP unless the request requires their
observable behavior.

An interrupt contract says that source outputs reach a processor; it does not
require `axi_intc` or `xlconcat`. A clock/reset contract states frequency,
polarity, and synchronization; it does not require a particular reset-cell count.

That omission covers only infrastructure *you* would infer. When the sources
name a unit and give it observable behavior of its own — a register aperture
in an address map, a software-visible input vector, an output another block
consumes — the request requires it. That holds even for a kind this section
otherwise lets you omit, such as "the fabric interrupt controller" with its own
control window and stated input assignments. Declare it as its own block and
bind its window to that block. Never fold a stated fabric unit into the
processor it serves: a window owned by `processing_system` claims the aperture
is inside the PS, and the unit's inputs, outputs and parameters are then
left with no block.

**Enumerate separate instances; do not dismantle one instance.** Omitting
invented infrastructure counts is not licence to collapse stated ones. The test
is whether the sources describe **separately instantiable units** — several
controllers of the same kind, each of which a design could add, remove or
configure on its own. Those get one block each, not one block per kind with a
`*_count` parameter, which loses the per-instance ownership and connectivity
later stages have to place. Eight GPIO controllers are eight blocks; four I2C
controllers are four blocks; two UARTs are two blocks.

The opposite error is worse. A single configurable subsystem is **one** block
however much internal structure the sources describe: a three-tile hardened
image-signal-processor is one block with tile parameters, not three blocks,
even though its tiles have their own registers, interrupts and address windows.
Channels, lanes, ports and tiles are internal structure. Splitting a subsystem
into its parts leaves nothing that answers to the subsystem's own name, so every
requirement written against it — its control interface, its clock, the routes
that reach it — resolves to nothing. Being separately addressed does not make
something separately instantiable; ask whether it is a unit the design could
instantiate by itself.

**Carry a connection's stated traffic with the connection.** When the sources
give a figure for a specific link — bandwidth, burst length, or a flag such as
"this is an initial-boot route" — attach it to that connection's contract as a
typed field, not as prose in a description and not only in a table you read once.
Use the template's names: `read_bandwidth_mb_s`, `write_bandwidth_mb_s`,
`read_burst_length`, `write_burst_length` and `initial_boot`.
A connection entry that names its endpoints but carries no traffic fields cannot
be checked against the requirement it came from, and a later stage has nothing to
apply. If a row of a table covers several links, repeat the figures on each link
it covers.

**Put a distinguishing descriptor in the field a checker will actually read, not
only in prose.** When the sources call out one path, clock, or setting as
different from a similar-looking one nearby — a *dedicated* link that bypasses
a *generic* fabric route, a block's *core* clock as opposed to its other
clocks, an acceptance policy that covers *all* IDs rather than one specific
ID — carry that distinguishing word in the technical field the requirement is
actually about, not only in `purpose`/`description` prose beside it:

- A `connections` entry for a stated dedicated/bridge path names that in
  `protocol` itself (for example `"Dedicated ISP bridge"`), in addition to any
  prose in `purpose`. A `protocol` of just the generic family name, with the
  distinguishing word left only in `purpose`, describes the same wiring, but
  nothing marks it as the specific path a downstream reader is meant to match.
- A `clock_reset` entry for a block's core clock uses a `target` that is the
  bare block name, or a qualifier containing "core", when the sources single
  out that clock among others on the same block. Once a block's core clock is
  stated that way, give its other entries with no frequency, such as a reset or
  control-domain contract, the interface they govern (`<block>.<interface>`): a
  bare-name entry without a frequency would read as a core clock with none.
- A parameter that states an acceptance policy ("accept every ID", "no
  filtering") uses the sources' own policy word (for example `"All"`) as the
  value, not a paraphrase that describes the same behavior differently.

This is the same principle as "carry a connection's stated traffic with the
connection": a fact that lives only in a sentence next to the JSON is
invisible to anything that reads the JSON.

**Carry each instance's address with it.** An enumerated instance is only useful
if it keeps the window that identifies it. When an address map names a target
per instance — `gpio.automatic`, `i2c.camera0`, `uart.0` — give that instance's
block its own base and size from the row whose target matches, together with the
master group that row assigns it. Enumerating eight GPIO controllers and leaving
all eight without addresses is not better than one block with a count: the
addresses are what make them distinct.

Every such window names its owner in a `block` field: the declared block whose
aperture it is. The map's target labels settle the instance question for you.
Labels that share a prefix before the first `.` (`uart.0`, `uart.1`) belong to
**one block**, when they are regions of a single unit, or to **one block each**,
when they are separate units. Never bind a family partly to a shared block and
partly to per-member blocks. If you are unsure which reading applies, the test in
"Enumerate separate instances" decides it.

**Name the owner with the sources' term.** The block that owns a family's windows
takes its name from the sources: the map's own label (`uart` for `uart.0`, so
`uart_0`), or a phrase the requirement text itself uses for that unit. Do not
rename a unit for what it does. A block called `transfer_engine` owning a map's
`dma.*` windows, with a reference group aliasing `dma.*` back to it, is internally
consistent, but anything that looks the unit up by the sources' name finds nothing.
The audit reports this as `SOURCE_NAME`; rename the block everywhere it is
referenced and drop any group that only aliased the old name.

**One block per function, not two.** Do not emit an abstract role block beside
the concrete block that implements it — a `camera` block next to a
`camera_receiver`, or a `memory` block next to the controller that serves it.
Downstream checks resolve a role to its block; two blocks claiming one role make
every parameter of that role ambiguous, and the empty one contributes nulls.
Model the external device only when the request makes it a requirement in its own
right, and name it so it cannot be mistaken for the on-chip function.

## 4. Resolve omissions before implementation

Ask one consolidated question when a missing answer changes function, including:

- board choice or external pin/interface;
- protocol, direction, width, rate, or format;
- which software master controls a device;
- interrupt destination;
- memory access direction or minimum bandwidth;
- whether an address is fixed;
- whether a peripheral is required or optional.

If running unattended, put unresolved items in
`global_constraints.unresolved_requirements` and fail validation with
`SPEC: FAILED`; do not guess.

Physical IP port names and implementation topology are not omissions. Unless the sources
supply them as requirements, they belong to implementation planning: leave them unresolved,
neither inventing them nor listing them as unresolved requirements.

## 5. Validate completeness

Before handoff:

1. Every required endpoint's block exists.
2. Every external data source has a path to its stated sink.
3. Every software-controlled device has control reachability.
4. Required interrupt outputs have a destination.
5. Every memory master states read/write direction.
6. Only genuinely fixed offsets appear in `fixed_address_windows`.
7. Active clocked blocks have clock/reset requirements.
8. Linux-visible devices appear in a `software` contract.
9. No infrastructure instance count is invented.
10. Every name in a contract resolves to a semantic block, interface, or explicitly named
    external actor; do not hide a named function in prose.
11. Use canonical processing-system visibility actors:
    `processing_system.pmc`, `processing_system.ppu`, `processing_system.asu`, and
    `processing_system.mmi_peripheral_masters`.
12. Preserve every approved software-interface detail: field offsets and widths,
    status-bit meanings, value encodings, ownership, notification writes, ordering,
    buffer lifetime, discovery and acknowledgement behavior. A general statement
    such as "compatible with existing software" does not preserve a detailed ABI.
13. Define every referenced group and its members. Prefer explicit semantic targets;
    otherwise use `global_constraints.reference_groups` as a name-to-members object.
    Groups must be nonempty, acyclic and resolve to declared targets. Explicit
    external actors can be listed in `global_constraints.external_actors`.

## 6. Check And Hand Off

The lab has two gate results: `SPEC: OK` (exit 0) and `SPEC: FAILED` (exit 1).
No independently approved policy or fidelity-review artifact is required.

**Reference designs are sources.** Before generation, search doc search (the AMD
documentation MCP) for an AMD reference design or example that matches the requested board and
function: a TRD or XD user guide, or the example-design chapter of the IP's product guide, and
for an IP-example request also the IP's Vivado example design (`ipiasm::example_reference`
writes it to `example_reference.json`). A request's words often only parameterise such a design
("an I/O expander with N I2C, M GPIO, K UART"). When one matches, add it to the sources as an
inline entry `{"id":"reference:<document id>","url":"<doc url>","text":"<the excerpts that define
its subsystems, processor, interrupts, clocks and resets>","sha256":"<UTF-8-text-hash>"}`; its
units are then requirements, and the audit checks the spec against them. When none matches, add
`{"id":"reference-search","text":"<each query run>; no matching reference design","sha256":"..."}`
so the search is on record. The vivado-ipi-assembler refuses a plan whose sources have neither.
The release's example designs (CEDs) are part of the search: when the assembler's
`reference_search.json` or `ced_reference.json` is present, cite the matched CED the same way
(`"id":"reference:<ced name>"`, with its cells and options as the text). A CED is cited in
addition to the document search, never instead of it. Where the board's reference document and
the CED differ (clock frequency, extra units), the document's value is the requirement. A unit of
the reference that the request does not mention stays in the spec. Drop one only when the request
conflicts with it.

A reference's excerpts must carry what defines it, not only its overview:
- every clock with its frequency (for example, a clocks table that says the processor clock is
  200 MHz);
- the processor's configuration (preset or template, caches, interrupt use);
- the list of IP and how many of each.

Put each one in the DesignSpec as a requirement: a clock domain with its frequency, block
parameters, and blocks. When the request gives a count ("4 GPIO"), it counts the reference's own
unit (four AXI GPIO blocks), not pins or bits.

Before generation, create `<out>/spec_sources.json` automatically from exactly the
user-specified requirement sources, plus the reference entry above. Each entry is
`{"path":"relative/path","sha256":"raw-file-hash"}`, with each path written exactly as the
request names it (for example `inputs/DesignDescription.md`), relative to the workspace
root; pass that same root as `--source-root`. This manifest records source
identity, not approval; do not ask the lab user to create it. For an inline request,
use `{"id":"request","text":"original prompt","sha256":"UTF-8-text-hash"}`.
Do not add implementation files or omit supplied requirements. Preserve the
manifest during repairs; update it only when the user changes the source inputs.

Run the audit after schema validation:

```bash
<skills-root>/vivado-ip-configurator/lib/ipcfg/vivado_python.sh \
  <this-skill>/scripts/audit_spec.py \
  --spec <out>/design_spec.json --source-root <workspace-root> \
  --sources <out>/spec_sources.json --out <new-audit-directory>
```

The audit checks schema, contract consistency, references, source hashes and
unresolved functional intent. Missing, changed or malformed inputs also fail.
Generate the contracts for a supplied address-map CSV instead of writing them by hand:
`scripts/address_contracts.py --csv <map> --skeleton` prints a small binding to fill in (the
block owning each target, which targets are memory, each master's group members); `--bind
<binding> --out <file>` then emits `fixed_address_windows`, `control_reachability`,
`memory_reachability` and `reference_groups` exactly as the audit matches them, or
`ADDRESS_CONTRACTS_FAIL` naming each unbound target or wrong group size. Merge them into the
spec and add only obligations the prose states beyond the table, such as bandwidth.

Address tables are checked row by row. A table is a supplied CSV, or a pipe table in a
supplied Markdown file, with `master`, `base` and `size` columns; addresses written only in
prose are not checked. Every row needs a `fixed_address_windows` entry with that master (or a
name ending in `.<master>`), offset and range. Each unmatched row is its own `SOURCE_COVERAGE`
finding and names the nearest window and what differs; fix that window rather than adding one.
With a `target` column, each table target must map to one spec target and each spec target to
one table target (`SOURCE_TARGET`); names may differ. A window whose `target` quotes a table
target must carry one of that target's rows (`SOURCE_EXTRA`). Every window matched to a targeted row
must also name, in `block`, the one declared block that owns that target (`SOURCE_BLOCK`), and
targets sharing a prefix before the first `.` must bind to one block or to one block each
(`SOURCE_SPLIT`). A table with `Target` and `Instances` columns (`gpio.*` | 8) fixes how many
blocks that family binds to (`SOURCE_INSTANCES`). A `block` that names no declared block is a
schema error. With a `master_count` column, the master's reference group, expanded through
nested groups, must hold that many masters (`SOURCE_MASTER_COUNT`).
It writes `report.json` and readable `report.txt`, with findings and repair guidance.
Use a new audit directory each time to preserve previous evidence. Neither sources
nor the candidate are edited by the checker.

`SPEC: OK` means these implemented checks passed for the recorded candidate and
source hashes. It is not proof that every prose or diagram requirement was captured,
nor that the hardware is feasible. Compare the spec against all supplied requirements
using the completeness checklist above before handoff. Report concrete omissions or
ambiguities rather than treating a mechanical pass as an exhaustive review.

On `SPEC: FAILED`, repair confirmed candidate defects and rerun. For missing or
ambiguous functional intent, ask the user the actual design question; do not erase
the unresolved item or invent a default. Do not change inputs or weaken checks to
clear a finding. Do not request policy approval as part of the normal lab workflow.
Run the audit at most 10 times per request unless the user's request sets another
limit. If the last run still fails, stop: report `SPEC: FAILED` with its remaining
findings and do not hand off.

Optional advanced checks remain available through `--policy`; when explicitly
supplied, its existing source-backed rules and fidelity-review requirements still
apply. An invalid supplied policy fails rather than being ignored. Optional
`--advisory --model <model> --timeout 120` adds non-authoritative model advice; it
cannot change the gate verdict. Neither option is needed for the lab.

Hand the current JSON and its passing, hash-bound audit to `vivado-ipi-assembler`.
Do not proceed after schema-only success, an agent-written verdict or an audit of
a different candidate. Stage 2 proves implementation feasibility separately.
