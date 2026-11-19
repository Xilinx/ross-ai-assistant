<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Memory budget

`ila::plan` checks that the capture memory fits the device before anything is inserted.
Source: `scripts/ila_plan.tcl` (`plan`) and `scripts/ila_budget.tcl` (`bram_tiles`, `uram_tiles`, `free_bram_tiles`, `free_uram_tiles`).

## Formula (block RAM)

- Per core, at depth D samples and W probe bits. Let raw = W x D / 36864 (36864 bits is one 36 Kb tile).
- UltraScale, UltraScale+, and Versal up to depth 2048, and UltraScale and UltraScale+ up to depth 16384: tiles = ceil(raw x 1.0025 + 0.5). The memory is RAMB36 plus a last RAMB18.
- UltraScale and UltraScale+ above depth 16384: depth / 16384 times the tiles of the 16384 deep core of the same width. The memory grows in blocks of 16384 samples. The single formula, taken deeper, is below the real tiles (65536 x 32: 58 against 60).
- Versal from depth 4096 (RAMB36 only): lanes = ceil((W + 3) / 9 x f), tiles = lanes x ceil(D / 4096). A lane is a 4Kx9 RAMB36 column and the capture word is W + 3 bits. f is 1 up to depth 32768, 1.02 at 65536 and 1.057 at 131072 (a deep core costs extra lanes). The shape is the fixed primitive rule of the Block Memory Generator guide (PG058, chapter 4, Block RAM Usage: width ratio rounded up times depth ratio rounded up). PG058 describes the BMG core, not the ILA, so the match is measured, not documented.
- Tiles are summed over the cores. Each core rounds up on its own.
- Measured, not guessed, with `opt_design`: 132 points to depth 16384, then depths 32768 to 131072: UltraScale+ (xcvu37p) 26 points on 2026.1 and 10 on 2026.2 with the same tiles, UltraScale (xcvu190) 131072 x 1024, Versal (xcvm1802) 43 points from depth 4096 (19 of them at 65536 and 131072).
- Never below the measured tiles. UltraScale and UltraScale+: at most one tile above per 16384 samples. Versal: exact on 42 of 43 points, one lane (D / 4096 tiles) above on 65536 x 192.
- Storage qualification and advanced trigger add no block RAM.
- Not measured: widths below 32, UltraScale+ 131072 x 1024 on a UltraScale+ part (over capacity there; measured on UltraScale xcvu190: 3652).
- The 6 percent rule it replaced was up to 26 tiles high on UltraScale+ (16384 x 1024: 483 against 457 used), 2 low on Versal (16384 x 64: 31 against 32) and far below the real tiles at depth 65536 and 131072.
- The formula is labeled "formula". Every other number the library prints comes from a Vivado query.

## What Vivado does with a request that does not fit

- UltraScale+: `implement_debug_core` stops with `Synth 8-5833: Design has more instantiated block-RAMs than device capacity`. Measured with 131072 x 1024 on xcvu37p (2016 RAMB36) on 2026.1 and 2026.2. The library refuses the same request before anything is inserted.
- Versal: `opt_design` builds a core that needs more block RAM than the part has (65536 x 1024 built from 1872 tiles on a part with 967), so Vivado gives no early stop there and the library refusal is the only one. Placement of such a core was not tried.

## Formula (UltraRAM, Versal `-memory_type uram`)

- Tiles = ceil(W x D / 294912) per core. One URAM288 is 294912 bits.
- Measured on xcvm1802, depths 4096, 16384 and 32768, widths 32 to 1024: exact in all 18 points.
- Needs depth 4096 or more (Vivado refuses `C_MEMORY_TYPE 1` below that). The core then uses no block RAM.

## Free tiles

- Block RAM: the part's `BLOCK_RAMS` minus the design's RAMB36 and FIFO36 cells minus half the RAMB18 and FIFO18 cells (rounded up), minus the model tiles of ILA cores that exist as debug cores but are not built yet (with `-replace_existing`, the `u_ila_*` cores that will be deleted are left out). A core that is built already has its RAMB cells counted, so it is not counted twice. If existing cores take more than is free, free is 0 (not unknown) and the plan is refused as over budget.
- UltraRAM: the part's `ULTRA_RAMS` minus the design's URAM288 cells.
- If the part reports no count, free is -1 (unknown) and the check passes, but the plan prints `WARNING W02: budget NOT checked: ...` and the session facts (kept in memory, no file is written) has `budget_checked` 0. It is not refused: there is no number to refuse on.
- No LUT or FF cost.

## Known limit: memory only

The skill checks memory (block RAM and UltraRAM) only. It does not model LUT or flip-flop cost. A request that fits in memory but needs more flip-flops than the device has fails at `place_design` with DRC UTLZ-1. Case T14 (`4096-probe-bits-on-a-design-with-98-percent-flip-flops-refused-before-insertion`) shows it and stays a plain FAIL: a known gap, not in the first release.

## DFX partitions (block RAM)

- A core in a reconfigurable partition can use only the RAMB36 sites of that partition's pblock. Its free count is those sites (`get_sites -of_objects <pblock> RAMB36_X*Y*`) minus the RAMB and FIFO cells the design already has inside the partition cell, never below 0.
- A core in the static part cannot use the room the pblocks hold. Its free count is the device free count minus the free tiles of every partition pblock.
- Each region is checked alone: the cores of one partition against that partition. The first partition that does not fit, else the static part, gives the numbers on the `BUDGET:` line, and a second line names it: `BUDGET: partition <rp> (pblock <pb>) holds N block RAM tiles, U are used by the design inside it, F are free for the debug core.` `ila::apply` then raises `ILA_OVER_BUDGET: N tiles needed, F free in partition <rp> (its pblock)`.
- Versal and UltraScale+ on Vivado 2026.1 put a core in the partition that holds its nets. UltraScale+ on 2026.2 puts every core in the static part, so a partition pblock does not limit it.
- A partition with no pblock, or one whose sites cannot be read, is checked against the whole device and the plan prints `WARNING W02`.
- Debug cores that are already inside a partition and not built yet (they have no RAM cell until `opt_design`) take room from that partition: the model tiles of each such core are subtracted from the pblock's free count. Built cores are already counted as RAM cells inside the partition.
- UltraRAM (`-memory_type uram`, Versal) is scoped the same way: the free count is the URAM288 sites of the pblock (`get_sites -of_objects <pblock> URAM288_X*Y*`) minus the URAM288 cells the design already has inside the partition. The `BUDGET:` line says `URAM tiles` and the second line says `UltraRAM tiles`.
- Measured on a KU5P pblock of 4 RAMB36 and a VC1902 pblock of 2 (Vivado 2026.2): the site query returns exactly those sites.

### Known limits of the partition budget

- LUT and flip-flop cost is not modelled (see the memory-only limit above).
- A core in the static part is not limited by the partition pblock: the plan does not refuse it for a partition's size, so a static-part core that needs more than the partition holds is accepted (case T16a).

## Over budget

- The plan prints one line: `BUDGET: depth D x W bits needs N block RAM tiles, F are free. Largest valid depth that fits at this width: X.` For URAM it says `URAM tiles`.
- It then returns 0. `ila::apply` raises `ILA_OVER_BUDGET`.
- Report the tiles needed, tiles free, depth, width and the largest depth that fits.

## Depth

- Valid depths are read from Vivado's own error text on a scratch core. Expect 1024 to 131072, powers of two.
- Default depth is 1024.
- A depth Vivado does not accept stops with `ILA_DEPTH_RANGE`. Ask the user, or use the largest valid depth and say so.
