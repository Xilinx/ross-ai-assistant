<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Setup

The skill runs on the **stock Vivado MCP** — the server the host already configures for `vivado_start`, `vivado_execute` and `vivado_stop`. Nothing is installed for this skill and no wrapper sits in front of the server.

The workflow itself runs inside Vivado. `lib/ced_op.tcl`, sourced once per session, defines the `ced_op` command; it starts `scripts/ced_bridge.py` under Vivado's bundled Python and passes each JSON op to it. So the host needs only:

- Vivado 2026.1 or later (verified on 2026.1 and 2026.2), started headless in `ipi` mode.
- `git`, for the CED store mirror (`scripts/ced_store.py`); the store branch follows the running Vivado.
- The IP-configuration engine bundled in `engine/`. Set `CED_LITE_SKILL` only to point at a different engine root.

A scratch directory outside `/home` for the project and the store mirror.

## Phase 2 approval

Phase 2 writes IP-level changes, so the skill presents the proposal and stops unless the request already gave approval (see SKILL.md, `ask_user`). On the stock MCP that gate is the model following the skill, since `vivado_execute` is one tool and the host cannot tell a Phase 1 step from a Phase 2 apply. A host that wants the gate enforced outside the model — typically one serving a local model — can run the relay in `examples/ced_guard.py`, which exposes the apply step as its own tool so a permission rule can ask the user. See `examples/README.md`.
