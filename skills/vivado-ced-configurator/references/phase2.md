<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Phase 2: settings inside the generated design

Phase 2 runs the bundled IP-configuration engine (`engine/`) on cells that the example design created. Each assigned cell gets its own requirement ledger; IDs, candidates and replies follow that skill's `describe`/`select` contract. Read `engine/CONTRACT.md` for candidate fields such as `write_mode`, `enablement_controls`, vectors, nested dictionaries, `covers`, `unchanged_selections` and `value_mismatches`.

## What the server does

- **assign** adopts the existing cell (no cell is created or renamed) and returns either a complete small `catalog` or `next_op: describe`. A cell holds only its own IP's parameters, so when `describe` finds nothing there the requirement is on the wrong cell: assign it again with another `cell_id` (the reply lists the move under `reassigned`). That works until the requirement selects something in its current cell; after a selection, the assignment stands.
- **propose** saves the Phase 1 design as a snapshot, applies all selections through the lite engine (native range checks and readback per cell), runs `validate_bd_design`, records every `CONFIG.*` value of every cell, saves that result as the proposal snapshot and restores the Phase 1 snapshot by reopening the project. The reply lists:
  - `requested_changes`: parameters your selections changed, with their requirement IDs;
  - `side_effects`: parameters Vivado changed in any cell as a consequence (propagation, board automation);
  - `validation`: block-design validation after the dry run;
  - `failures`: cells whose writes Vivado rejected, or that had nothing selectable;
  - `unresolved`: requirements without a selection.
  Dictionary parameters are reported per changed subkey (`PS_PMC_CONFIG/PS_UART0_BAUD_RATE`).
- **ced_apply_phase2** (approval tool) restores the proposal snapshot, validates, compares every recorded value with the proposal (`matches_proposal`; component names are ignored), reads back each pinned requirement and saves. It applies exactly what the user approved; it does not replay selections.

## Limits

- Three proposals per session. A paused proposal does not count.
- After a proposal, the ledger is locked: only approval, finish or status are accepted. To change requests, finish (Phase 1 is kept) and start a new session.
- A board-bound IP setting can be fixed by its board interface (for example an `axi_gpio` wired to `led_8bits` keeps its width until its board interface is set to Custom). Such a write is rejected or reverted and shows up in `failures` or as no change; report it.
- Very large PS IPs (Zynq UltraScale+ PS, Versal CIPS) expose thousands of parameters. When discovery does not return the requested setting within the describe budget, the requirement stays unresolved and is reported.
