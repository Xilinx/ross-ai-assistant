---
name: hls-line-buffer
description: HLS Circular Line Buffer — step-by-step instructions for generating a KSIZE×KSIZE sliding-window stencil compute stage that achieves II=1. Algorithm-agnostic; scales to any KSIZE and COLS.
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# HLS Circular Line Buffer — Step-by-Step Instructions

Use these instructions whenever a kernel applies a KSIZE×KSIZE neighborhood to every pixel of a frame in raster-scan order (image filters, demosaic, convolution, etc.).

---

## Step 1 — Derive the constants

From KSIZE, ROWS, COLS, compute four values before writing any code:

1. `KRAD = KSIZE / 2` — distance from centre to edge of the kernel
2. `NLB  = KSIZE - 1` — number of line buffer rows needed
3. `WARMUP = KRAD * COLS + KRAD` — iterations before first valid output pixel
4. `TOTAL_ITER = ROWS * COLS + WARMUP` — total loop iterations including warmup

> COLS **must** be a power of 2. If it is not, flag this to the user — the col-counter derivation in Step 4 requires it.

---

## Step 2 — Declare arrays as non-static, with no initializer

Declare the line buffer and window inside the compute function:

- `lb[NLB][COLS]` — line buffer, `NLB` rows × `COLS` columns
- `win[KSIZE][KSIZE]` — sliding window

**Non-static** — do not add `static`. Static adds FSM overhead that prevents reaching the II=1 floor.
**No `= {}`** — do not zero-initialize the arrays. On a non-static array, `= {}` generates a sequential init loop every frame invocation, inflating latency by the array size.

Add a csim-only zero-init guard immediately after the declarations:

```
#ifndef __SYNTHESIS__
  zero all elements of lb and win
#endif
```

This block runs in csim (where stack memory is undefined) and is skipped in synthesis (where FPGA flip-flops power up to 0).

---

## Step 3 — Write load_input as a single loop with a zero tail

The load function streams `TOTAL_ITER` values in **one loop**:
- For iterations `i < ROWS * COLS`: stream the actual input pixel
- For iterations `i >= ROWS * COLS`: stream zero

Do not use two separate loops (one for data, one for zeros). Two loops generate an inter-loop FSM transition that costs ~16 extra cycles.

The zero tail lets compute() read the stream unconditionally every iteration — a conditional read forces II=2.

---

## Step 4 — Derive col from iter inside the compute loop

Inside the main loop, derive the column index as:

```
col = iter & (COLS - 1)
```

Do **not** maintain an explicit `col` counter with `if (++col == COLS) { col = 0; row++; }`. An explicit counter creates a 2-cycle loop-carried recurrence (load → increment → branch → store spans 2 pipeline states). Deriving from `iter` with a bitwise AND is a single-cycle operation with no loop-carried dependency.

---

## Step 5 — Read line buffer BEFORE shifting, then shift rows explicitly

In each iteration:

1. Read the current column from every line buffer row into local variables (`c0..cN`)
2. Then shift rows: `lb[NLB-1][col] = lb[NLB-2][col]`, ..., `lb[0][col] = pixel_in`

**Read before shift** — if you shift first, you lose the oldest row before reading it.
**Explicit assignments, no inner loop** — write each assignment on a separate line. An inner shift loop creates a loop-carried dependency inside the outer pipeline loop, forcing II > 1.

---

## Step 6 — Shift window columns explicitly, no inner loop

For each row of the window, shift columns from right to left in a single line:

```
win[r][KSIZE-1] = win[r][KSIZE-2]; ... win[r][1] = win[r][0]; win[r][0] = cR;
```

Write this for every row r = 0..KSIZE-1. Do not use an inner column-shift loop — same reason as Step 5.

After the shift, `win[KRAD][KRAD]` is the centre pixel.

---

## Step 7 — Gate output by iteration counter, derive position from iter

Only write to output when `iter >= WARMUP`. Inside the gate:

1. `out_idx = iter - WARMUP`
2. Row LSB: `(out_idx >> LOG2_COLS) & 1` — free shift (COLS = 2^LOG2_COLS)
3. Col LSB: `out_idx & 1` — free AND

Do **not** maintain `cen_row` / `cen_col` as loop-carried scalars — same II=2 risk as Step 4.

---

## Step 8 — Pragma

Place `#pragma HLS performance target_ti=<N>` as the first line inside the compute function body. Do not add PIPELINE, UNROLL, or ARRAY_PARTITION — the II=1 floor is achieved by the structural choices above.

---

## Summary of what determines II=1

| Choice | Rule |
|---|---|
| Arrays | Non-static, no `= {}` |
| col counter | Derived from `iter` via `& (COLS-1)` |
| Row/col position | Derived from `out_idx` via shifts and ANDs |
| lb shift | Explicit assignments, no inner loop |
| Window shift | Explicit assignments per row, no inner loop |
| Stream read | Unconditional every iteration |
| COLS | Must be power of 2 |
