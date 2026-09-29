# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Tiling for elementwise mul on the 6x4x4 performant overlay.

  Operand a => given to column broadcast (every pair of cores)
  Operand b => given to row broadcast (every pair of cores)
"""

from pathlib import Path

import tensor_expr


def _numel(shape: list[int]) -> int:
    n = 1
    for d in shape:
        n *= d
    return n


def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    ifm, wts, ofm = opInterface

    assert ifm.getShape() == wts.getShape(), "Operands shapes mismatch"
    assert ifm.getShape() == ofm.getShape(), "Operand and result shapes mismatch"

    stamp = tiling[0][0]
    ROWS = stamp.aie_core_rows
    COLS = stamp.aie_core_cols
    n_cores = ROWS * COLS

    N = _numel(ifm.getShape())
    SEG = N // n_cores
    assert SEG * n_cores == N, "N must be divisible by the number of cores"

    # The performant overlay splits the cores across two mem-tile columns.
    NUM_L2_TO_L1_MEM_COLS = 2
    assert 2 * NUM_L2_TO_L1_MEM_COLS == COLS, "COLS must split evenly across mem cols"

    dtype = ifm.getDType()
    out_dtype = ofm.getDType()

    # Flat DDR tensors viewed as [ROWS, COLS, SEG] so [row, col] selects tile g.
    a_ddr = ifm.getTensorVar().Reshape([ROWS, COLS, SEG])
    b_ddr = wts.getTensorVar().Reshape([ROWS, COLS, SEG])
    out_ddr = ofm.getTensorVar().Reshape([ROWS, COLS, SEG])

    # Contrary to the 6x4x4 overlay, in the performant overlay, the L2->L1
    # channels are spread over two columns (0 and 1 for column-wise cast, 2 and
    # 3 for row-wise cast). This geometry is fixed for the 6x4x4 performant
    # overlay, so hardcode it rather than re-deriving it from the channels.
    #
    # core-col group -> mem col carrying that group's broadcast DMA:
    a_bcast_col = {0: 0, 1: 1}  # column broadcast (operand a)
    b_bcast_col = {0: 2, 1: 3}  # row broadcast (operand b)

    a_l2, b_l2, out_l2 = {}, {}, {}
    for m in range(NUM_L2_TO_L1_MEM_COLS):
        # Create L2 buffers in the mem tiles that are used for L2->L1 transfers.
        a_l2[m] = tensor_expr.TensorVar.make(
            locations=[tensor_expr.Location(a_bcast_col[m], 0, 0x0)],
            shape=[2, ROWS, SEG],
            type=dtype,
        )
        b_l2[m] = tensor_expr.TensorVar.make(
            locations=[tensor_expr.Location(b_bcast_col[m], 0, 0x0)],
            shape=[ROWS, 2, SEG],
            type=dtype,
        )
        out_l2[m] = tensor_expr.TensorVar.make(
            locations=[tensor_expr.Location(a_bcast_col[m], 0, 0x4000)],
            shape=[2, ROWS, SEG],
            type=out_dtype,
        )

    # L3->L2 and L2->L3 channels are tied to a specific mem-tile column.
    # We hardcode here the L3 to L2 channels that target a given mem tile
    # column, although this can be derived fron the channels.
    l3_to_l2 = stamp.get_l3_to_l2_channels()
    l2_to_l3 = stamp.get_l2_to_l3_channels()
    l3_to_l2_by_l2_col = {
        0: [l3_to_l2[0], l3_to_l2[1]],
        1: [l3_to_l2[2], l3_to_l2[3]],
        2: [l3_to_l2[4], l3_to_l2[5]],
    }
    l2_to_l3_by_l2_col = {
        0: [l2_to_l3[0], l2_to_l3[1]],
        1: [l2_to_l3[2], l2_to_l3[3]],
        2: [l2_to_l3[4]],
    }

    # b's row-broadcast cols {2, 3} are not both wired to DDR: only col 2 exposes
    # L3->L2 fill channels (col 3 has none). Its two channels use ports 0-3, so
    # one fills the col-2 buffer locally and the other reaches one tile east to
    # fill the col-3 buffer.
    b_fill_col = 2
    b_fill_channels = l3_to_l2_by_l2_col[b_fill_col]

    # ---- L3 -> L2: fill each mem-tile column's shared a and b buffers. ----
    for m in range(NUM_L2_TO_L1_MEM_COLS):
        slice_cols = m * 2
        # a: [row, cc] in DDR -> [cc, row] in L2 (transpose to col-major).
        a_src = a_ddr[:, slice_cols : slice_cols + 2].Transpose([1, 0, 2])
        stamp.set_l3_to_l2_transfer(
            l3_to_l2_by_l2_col[a_bcast_col[m]][0], a_src, a_l2[m]
        )
        # b: [row, cc] in DDR -> [row, cc] in L2 (already row-major). Both fills
        # come from b_fill_col; the DMA writes into b's own (possibly neighbour)
        # mem tile.
        b_src = b_ddr[:, slice_cols : slice_cols + 2]
        stamp.set_l3_to_l2_transfer(b_fill_channels[m], b_src, b_l2[m])

    # ---- L2 -> L1: column broadcast delivers operand a to each vertical pair.
    a_l1 = tensor_expr.TensorVar.make(
        [2, SEG], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    for channel in stamp.get_l2_to_l1_channels(
        broadcast=tensor_expr.broadcast_on.COLUMNS
    ):
        cores = channel.core_tile_ports
        # The two cores are in the same column
        col = cores[0].tile_col
        # Two rows are served per channel.
        rows = sorted(c.tile_row for c in cores)
        # Which L2 variable to use for that transfer?
        src_l2 = a_l2[col // 2]
        # Serve two rows per channel (slice upper bound is exclusive).
        src = src_l2[col % 2, rows[0] : rows[1] + 1]
        stamp.set_l2_to_l1_transfer(channel, src, [a_l1 for _ in cores])

    # ---- L2 -> L1: row broadcast delivers operand b to each horizontal pair.
    b_l1 = tensor_expr.TensorVar.make(
        [2, SEG], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    for channel in stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS):
        cores = channel.core_tile_ports
        # The two cores are in the same row
        row = cores[0].tile_row
        # Two columns are served per channel.
        cols = sorted(c.tile_col for c in cores)
        # Which L2 variable to use for that transfer?
        src_l2 = b_l2[cols[0] // 2]
        # Serve two columns per channel. The inner dimension is implicit
        # (it is already 2)
        src = src_l2[row]
        stamp.set_l2_to_l1_transfer(channel, src, [b_l1 for _ in cores])

    # ---- Compute: out = a * b on this core's SEG slice. ----
    out_l1 = tensor_expr.TensorVar.make(
        [SEG], out_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    stamp.set_kernel_arguments([a_l1, b_l1, out_l1])
    stamp.set_kernel_function_name("mul_kernel")
    stamp.set_kernel_impl(Path("custom_mul.cpp"))
    stamp.set_kernel_params([SEG])
    stamp.set_kernel_nb_calls(1)

    # ---- L1 -> L2: packet-merged collect drains each vertical pair. ----
    for channel in stamp.get_l1_to_l2_channels():
        cores = channel.core_tile_ports
        col = cores[0].tile_col
        # Which L2 variable to use for that transfer?
        dst_l2 = out_l2[col // 2]
        # Write access patterns for each core.
        dst_slices = [dst_l2[col % 2, c.tile_row] for c in cores]
        stamp.set_l1_to_l2_transfer(channel, [out_l1 for _ in cores], dst_slices)

    # ---- L2 -> L3: drain each mem-tile column's output buffer. ----
    for m in range(NUM_L2_TO_L1_MEM_COLS):
        slice_cols = m * 2
        src = out_l2[m].Transpose([1, 0, 2])  # [cc, row] -> [row, cc]
        dst = out_ddr[:, slice_cols : slice_cols + 2]
        stamp.set_l2_to_l3_transfer(l2_to_l3_by_l2_col[m][0], src, dst)
