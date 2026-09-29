# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Two-phase tiling for `out = (A + B) * C`, with the phase boundary in L3.

    phase 0 :  A, B (L3) -> L2 -> L1 -> add_kernel -> L1 -> L2 -> scratch (L3)
    phase 1 :  scratch, C (L3) -> L2 -> L1 -> mul_kernel -> L1 -> L2 -> out (L3)

The two phases run sequentially on the same 16 cores and are tied together by a
DDR scratch buffer: phase 0 writes it, phase 1 reads it back. `create_l3_scratch()`
registers it as an extra external buffer, so the runtime provides its memory.

Each phase takes its two operands in two separate L2 buffers and two separate L1
buffers, reached over the two distinct broadcast families a core has input ports
for. The first operand rides the column broadcast, so a column shares it and each
row picks its segment; the second rides the row broadcast, so a row shares it and
each column picks its segment. Core (col, row) reads `col_l1[row]` and
`row_l1[col]`, which are the same global elements.

The intermediate is deliberately too large for L1 (128 KB against a 64 KB data
memory), which is what forces it out to a scratch buffer in the first place.

The op is tiled temporally: each phase streams the tensor through the array in
ITERS chunks rather than staging it whole. The iteration axis lives entirely on
the L3 side, where TileBy splits the tensor into ITERS tiles. Every L2 buffer
holds one such tile and is an identity pattern -- one tile in, one tile out --
so the memory tiles carry CHUNK elements rather than N. Each slab declares
setTemporalIterations(ITERS), which becomes its adf::repetition_count and
re-arms its DMA once per chunk; the ping/pong pair and the DMA locks then
sequence the iterations, so a tile is drained before the next overwrites it.
Only the scratch buffer holds the complete intermediate.
"""

import tensor_expr

DIM = tensor_expr.DimensionSliceValue

ITERS = 16


def _numel(shape: list[int]) -> int:
    n = 1
    for d in shape:
        n *= d
    return n


def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    stamp = tiling[0]

    ROWS = stamp.get_overlay_info().aie_core_rows
    COLS = stamp.get_overlay_info().aie_core_cols

    stamp.create_phase()
    phase_0_add = stamp[0]
    phase_1_mul = stamp[1]

    a, b, c = opInterface.operandInfo
    out = opInterface.getResultInfo()

    dtype = a.getDType()
    out_dtype = out.getDType()

    N = _numel(a.getShape())
    CHUNK = N // ITERS
    Q = CHUNK // COLS
    SEG = Q // ROWS
    assert CHUNK * ITERS == N, "N must be divisible by ITERS"
    assert Q * COLS == CHUNK and SEG * ROWS == Q, "CHUNK must divide COLS*ROWS"

    scratch = tiling.create_l3_scratch(shape=[N], type=dtype)

    col_l1 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(0x0), tensor_expr.Location(0x2000)],
        shape=[ROWS, SEG],
        type=dtype,
    )
    row_l1 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(0x4000), tensor_expr.Location(0x6000)],
        shape=[COLS, SEG],
        type=dtype,
    )
    out_l1 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(0x8000), tensor_expr.Location(0xA000)],
        shape=[SEG],
        type=out_dtype,
    )

    L2_SLOT = 0x4000

    def make_l2(memtile: int, slot: int, dt) -> tensor_expr.TensorVar:
        base = slot * 2 * L2_SLOT
        var = tensor_expr.TensorVar.make(
            locations=[
                tensor_expr.Location(memtile, 0, base),
                tensor_expr.Location(memtile, 0, base + L2_SLOT),
            ],
            shape=[COLS, ROWS, SEG],
            type=dt,
        )
        # Each slab holds one chunk of a tensor streamed in ITERS pieces, so
        # its DMA is re-armed once per chunk. num_buffers only says how many
        # slots it ping-pongs between, not how often they turn over.
        var.setTemporalIterations(ITERS)
        return var

    a_l2 = make_l2(0, 0, dtype)
    mid_l2 = make_l2(1, 0, dtype)
    t_l2 = make_l2(2, 0, dtype)
    c_l2 = make_l2(2, 1, dtype)
    b_l2 = make_l2(2, 2, dtype)
    out_l2 = make_l2(2, 3, out_dtype)

    def stage(phase, src_col, src_row, l2_col, l2_row, col_channel, row_channel):
        """DDR -> two L2 buffers -> a column-broadcast and a row-broadcast L1."""
        channels = phase.get_l3_to_l2_channels()

        print("### L3 -> L2 Channels")
        # [L3->L2[shim=(col=0, row=0, port=0), mem=(col=0, row=0, port=0)], L3->L2[shim=(col=0, row=0, port=1), mem=(col=0, row=0, port=1)], L3->L2[shim=(col=2, row=0, port=0), mem=(col=2, row=0, port=0)], L3->L2[shim=(col=2, row=0, port=1), mem=(col=2, row=0, port=1)], L3->L2[shim=(col=1, row=0, port=0), mem=(col=2, row=0, port=2)], L3->L2[shim=(col=1, row=0, port=1), mem=(col=2, row=0, port=3)]]
        print(channels)

        for channel, src, dest in (
            (channels[col_channel], src_col, l2_col),
            (channels[row_channel], src_row, l2_row),
        ):
            phase.set_l3_to_l2_transfer(
                channel,
                src.Reshape([N]).TileBy([CHUNK]),
                dest.Reshape([CHUNK]),
            )

        col_broadcast = phase.get_l2_to_l1_channels(
            broadcast=tensor_expr.broadcast_on.COLUMNS
        )
        print("### L2 -> L1 Broadcast Columns Channels")
        print(col_broadcast)
        for col, channel in enumerate(col_broadcast):
            phase.set_l2_to_l1_transfer(
                channel,
                l2_col.Slice(index=0, offset=col, length=1),
                [col_l1] * ROWS,
            )

        row_broadcast = phase.get_l2_to_l1_channels(
            broadcast=tensor_expr.broadcast_on.ROWS
        )
        print("### L2 -> L1 Broadcast Rows Channels")
        print(row_broadcast)
        for row, channel in enumerate(row_broadcast):
            phase.set_l2_to_l1_transfer(
                channel,
                l2_row.Slice(1, row, 1),
                [row_l1] * COLS,
            )

    def gather(phase, dest_l2):
        """L1 -> this core's slot of `dest_l2`.

        Slicing out the core's column and row drops both dimensions, leaving
        [SEG] against the kernel's [SEG] output: an identity transfer, fired
        once per kernel call. The deterministic merge feeding the memory tile
        rejects a repetition count, which an identity pattern does not carry.
        """
        for col, channel in enumerate(phase.get_l1_to_l2_channels()):
            phase.set_l1_to_l2_transfer(
                channel,
                [out_l1] * ROWS,
                [
                    dest_l2.Slice({0: DIM(col, 1), 1: DIM(row, 1)})
                    for row in range(ROWS)
                ],
            )

    def drain(phase, src_l2, dest_l3, channel_index):
        print("### L2 -> L3 Channels")
        print(phase.get_l2_to_l3_channels())

        phase.set_l2_to_l3_transfer(
            phase.get_l2_to_l3_channels()[channel_index],
            src_l2.Reshape([CHUNK]),
            dest_l3.Reshape([N]).TileBy([CHUNK]),
        )

    def kernel(phase, name, impl):
        phase.set_kernel_arguments([col_l1, row_l1, out_l1])
        phase.set_kernel_function_name(name)
        phase.set_kernel_impl(impl)
        phase.set_kernel_params([SEG])
        phase.set_kernel_nb_calls(ITERS)

    stage(phase_0_add, a.getTensorVar(), b.getTensorVar(), a_l2, b_l2, 0, 4)
    kernel(phase_0_add, "add_kernel", "custom_addmul_add.cpp")
    gather(phase_0_add, mid_l2)
    drain(phase_0_add, mid_l2, scratch, 0)

    stage(phase_1_mul, scratch, c.getTensorVar(), t_l2, c_l2, 2, 3)
    kernel(phase_1_mul, "mul_kernel", "custom_addmul_mul.cpp")
    gather(phase_1_mul, out_l2)
    drain(phase_1_mul, out_l2, out.getTensorVar(), 2)
