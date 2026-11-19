<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Reference designs: drafts and coverage

Detail for SKILL.md's *Reference designs are requirements* section.

**Start the DesignSpec from the reference's draft.** `ced_reference` and `example_reference`
also write two files (`draft=` and `digest=` on their result line):
- `design_spec.draft.json`: a schema-valid DesignSpec with one block per functional cell of the
  reference. It carries the reference's user-set CONFIG values (`parameters.reference_config`),
  address map (`fixed_address_windows`), interrupts, clocks and connections.
- `reference_digest.txt`: the excerpt to cite for the reference in `spec_sources.json`.

An RTL IP example has no block design. Its draft has one block per IP instance of the example,
simulation models included, with each instance's user-set CONFIG values and no contracts: read
the connections, clocks, resets and top-level ports from the example's top module (named in the
draft's description) and add them.

On a board project `example_reference` also tries the IP's `*_BOARD_INTERFACE` parameters
against the board interfaces whose PREFERENCES name the IP. A parameter that exactly one interface
fits (and that interface fits no other parameter) is a choice. A call without config applies
the choices (`board_interfaces={...}`), so the example uses the board's memory and clock parts.
Config `custom`, or an explicit CONFIG dict, leaves them at Custom and reports them as
`board_interfaces_unused={...}`. Set any `board_ambiguous=` parameters yourself.

Edit the draft to the request and to the board's reference document (counts, clock frequencies),
then validate, audit and save it as `design_spec.json`. Do not rewrite it from scratch or with
generator scripts. Carry `reference_config` values into the plan's cell configuration.

The final gate holds the build to its recorded reference. When `ced_reference.json` or
`example_reference.json` sits beside the DesignSpec (or one folder below), every functional IP of
that reference must be built. The only exception is one listed in
`<spec dir>/reference_deviations.json` as `[{"ip":"<catalog name>","count":<n>,"reason":"..."}]`.
Glue, interconnect, resets and simulation-only models are exempt. Otherwise the gate fails with
`REFERENCE_COVERAGE:FAIL`. Recreating an IP's example design means recreating all of it,
including its display and control paths; do not scope it down to the IP alone.
