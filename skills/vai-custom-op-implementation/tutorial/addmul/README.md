<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# addmul

Custom implementation of `out = (A + B) * C`: a **two-phase** op whose phase
boundary lives in DDR.

| Example | Description |
|---|---|
| [0_l3_scratch](/docs/30_custom_ops/tutorial/addmul/0_l3_scratch) | Two sequential phases on the same 16 cores, tied together by an L3 scratch buffer. Shows `create_l3_scratch()`, `create_phase()`, two separate L1 input buffers reached over the two broadcast families, and `setTemporalIterations()` for streaming a tensor through chunk-sized memory tiles. |

## Why two phases

The intermediate `A + B` is 128 KB against a 64 KB L1 data memory, so it cannot
stay in the cores between the add and the multiply. Splitting the op into two
phases lets the add retire completely, spill its result to a DDR scratch
buffer, and have the multiply read it back. `AieConfig.create_l3_scratch()`
registers that buffer as an extra external buffer, whose memory the runtime
provides; it is neither an operand nor a result of the op. The lowering pairs
writer and reader by `TensorVar` variable id, so the *same* `TensorVar` must be
used at both ends.

## Two operands, two broadcast families

A core has exactly two DMA input ports, fed by two different broadcast
channels: one per column and one per row. The op gives each operand its own L1
buffer and its own family -- the first rides the column broadcast, the second
the row broadcast -- so the kernel takes two `input_buffer_conf` arguments
rather than one packed buffer with pointer arithmetic.

Core `(col, row)` therefore reads `col_l1[row]` and `row_l1[col]`, which name
the same global elements. The kernel picks its slice with
`aie::tile::current().id()`.

Note that a buffer must sit within one memory tile of every DMA that reads it:
memory tiles reach their immediate neighbours, not the whole array. The
column-broadcast channels source from memory tile 1 and the row-broadcast ones
from memory tile 3, which is what fixes where each operand's L2 slab may live.

## Streaming, not staging

Each phase moves its tensor through the array in `ITERS` chunks. The iteration
count lives entirely on the L3 side, where `TileBy()` splits the tensor into
tiles; every L2 slab holds a single tile and is an identity pattern -- one tile
in, one tile out.

What re-arms a slab's DMA once per tile is `setTemporalIterations()`, which
becomes its `adf::repetition_count`. This is separate from `num_buffers`, which
only says how many slots the slab ping-pongs between, not how often they turn
over. Without it the shim pushes the whole tensor at a memory tile that accepts
one chunk, and the design deadlocks with every worker parked in
`FW_STATE_WORKER_POST_WORK_BARRIER`.

The two sides of a transfer need agree on neither shape nor rank -- only on how
many elements make up one tile.
