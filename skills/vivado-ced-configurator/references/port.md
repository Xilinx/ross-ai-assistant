# Port: a CED on a board it does not list

`"port":"allowed"` at `begin` (set only when the user asks for a port) lets the workflow
build a design on a board the design supports, the **reference board**, and carry it to the
user's board. The model sends one extra step, `{"op":"port"}`, when `next_op` says so.
Everything else is decided by code (`lib/ced.tcl`, `cedcfg::port_*`) from Vivado's data.

## Steps

1. **Reference board** (`cedcfg::port_plan`, at `choose_ced`): an installed board from the
   design's `SUPPORTED_BOARDS` whose part has the target part's `FAMILY`; same architecture,
   then same package, then name. None: `port_refused`. A part target: `port_refused`.
2. **Reference project** (`cedcfg::port_reference`): the empty target project is replaced by
   `<run>/port-ref` on the reference board; the target board's interfaces are recorded.
   Options are evaluated and the design is built there by the normal `instantiate`.
3. **Carry across** (`cedcfg::port_apply`, at `port`):
   - The design's **own PS settings** are the built PS configuration minus the board preset
     alone (a throw-away PS cell with only its board interface set). For
     `versal_mb_v_preset` on the VEK385 that is `PS_ASU_ENABLE 0`, `PS_USE_BSCAN_USER2 1`,
     `PS_USE_PL_AXI_LPD 1`, `PS_USE_PMCPL_CLK0 1`, `PS_NUM_FABRIC_RESETS 1`.
   - `save_project_as` to `<run>/proj`. Cells bound to a board interface the target lacks
     are **dropped** with the nets and ports that served only them; same-named interfaces
     are kept (`rebound`). A memory, transceiver or host interface is never dropped: the
     port refuses instead. The PS cell is removed before the board changes (otherwise
     Vivado wraps it in an upgrade hierarchy).
   - The board part changes to the target's; locked IP is upgraded.
   - A fresh PS cell gets the target's board preset, then the design's own settings, and
     every net it had is reconnected by pin name. A pin the design used that the target's
     preset does not provide: the port refuses.
   - PS clock inputs left undriven are tied to an `ilconstant`, never to a running clock
     (an MMI clock pin has its own minimum period).
   - `assign_bd_address`, `validate_bd_design`, a new wrapper.
4. `port` returns `port` = `from`, `to`, `kept_settings`, `dropped`, `rebound`, `tied_off`,
   `validation`; `finish` copies it into `report.port`. A refused or skipped port makes
   `full` false and reports no target project.

Everything captured from the reference is stored as plain strings (`cedcfg::plain`): a
Vivado object kept across `save_project_as` and the board change goes stale and prints as
`null`. Through the Vivado MCP that silently lost the PS's external-port connections
(`gt_refclk0/1`, `MMI_GT`) while the batch runs kept them. After reconnecting, the port
checks that every connection the PS had in the reference is back, and refuses otherwise.

Interrupt inputs of dropped peripherals are left open (Vivado ties them to 0 with
`BD 41-759`).

## Boards outside Vivado's repository

Set `CED_BOARD_REPO` (colon-separated board-file directories) in Vivado's environment;
`cedcfg::begin` adds them to `board.repoPaths`. The relay passes it in its `environment`.
With the stock Vivado MCP, Vivado is started by the MCP server, not by the caller's shell,
so pass it as `vivado_start`'s `env` (`{"CED_BOARD_REPO": "<dir>"}`); a variable exported
in the agent's shell does not reach Vivado.

## Not ported (v1)

A part as the target; a design with more than one block design or PS; DDR/LPDDR, NoC
memory, GT, PCIe/CPM, MIPI or other host interfaces bound to the reference board;
designs across device families.
