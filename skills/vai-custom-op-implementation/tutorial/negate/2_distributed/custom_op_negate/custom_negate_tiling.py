# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


def _numel(shape: list[int]) -> int:
    n = 1
    for d in shape:
        n *= d
    return n


# Distributed 4x4 implementation. Each core processes a fixed [tileSize] segment,
# so one pass over the array consumes COLS*ROWS*tileSize elements. Tensors larger
# than that are streamed through the array in ITERS temporal iterations.
#
# Within one iteration the L2 tile [COLS, ROWS, tileSize] is split across the COLS
# columns (each column gets its [ROWS, tileSize] chunk) and broadcast to the ROWS
# cores of that column. Each core receives the full column chunk and negates its
# own [tileSize] segment at offset row*tileSize -- the kernel performs this row
# offset itself (see custom_negate.cpp), so every core is fed the same buffer.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    stamp = tiling[0][0]
    ROWS = stamp.aie_core_rows
    COLS = stamp.aie_core_cols

    # Unpack the op interface: negate is a unary op (one input, one output).
    ifm, ofm = opInterface

    assert (
        ifm.getPaddedShape() == ofm.getPaddedShape()
    ), "ifm/ofm padded shape inconsistent"

    # - The IFM is broadcast along each column, so every core holds a full
    #   [ROWS, tileSize] column chunk in L1 -- the L1 IFM buffer is ROWS
    #   times larger than the OFM one.
    #
    # - Total L1 memory is 64 KB (0x0000 - 0xFFFF). The stack and heap sit
    #   symmetrically around 0xC000, so 0xB000 - 0xD000 is reserved.
    tileSize = 0x0800

    # One pass over the array consumes a full [COLS, ROWS, tileSize] tile; the
    # tensor is streamed through in ITERS such tiles.
    L2_full_tile_size = tileSize * ROWS * COLS
    size = _numel(ifm.getPaddedShape())
    assert (
        size % L2_full_tile_size == 0
    ), f"Total size {size} must be a multiple of {L2_full_tile_size}"
    ITERS = size // L2_full_tile_size

    dtype = ifm.getDType()
    # Byte size of one L2 tile, i.e. the gap between the ping and pong slots.
    L2_tile_bytes = L2_full_tile_size * tensor_expr.getOperandTypeSize(dtype)

    l3_l2 = stamp.get_l3_to_l2_channels()
    l2_l1 = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    # L2 buffers: one [COLS, ROWS, tileSize] tile each, in its own memtile.
    # Two Locations per buffer means the memtile ping-pongs between them across
    # ITERS
    ifm_mem_var = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(0, 0, 0x0),
            tensor_expr.Location(0, 0, L2_tile_bytes),
        ],
        shape=[COLS, ROWS, tileSize],
        type=dtype,
    )
    ofm_mem_var = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(1, 0, 0x0),
            tensor_expr.Location(1, 0, L2_tile_bytes),
        ],
        shape=[COLS, ROWS, tileSize],
        type=ofm.getDType(),
    )

    # Both L2 buffers are refilled/drained once per iteration. This rotation
    # count MUST match the kernel-call count below, otherwise the DMA
    # deadlocks.
    ifm_mem_var.setTemporalIterations(ITERS)
    ofm_mem_var.setTemporalIterations(ITERS)

    # -> L2: cut the flat IFM in DDR into full-array tiles; each
    # temporal iteration transfers one tile into the memtile.
    ifm_ddr = (
        ifm.getTensorVar().Reshape([size]).TileBy(index=0, tileLen=L2_full_tile_size)
    )
    stamp.set_l3_to_l2_transfer(
        l3_l2[0], ifm_ddr, ifm_mem_var.Reshape([L2_full_tile_size])
    )

    # L2 -> L1: each column reads its [ROWS, tileSize] chunk, broadcast to
    # its ROWS cores. The kernel offsets internally by row*tileSize, so each
    # core gets the full column chunk.
    ifm_mk_var = tensor_expr.TensorVar.make(
        [ROWS, tileSize], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    ifm_per_col = ifm_mem_var.TileTo(index=0, numTiles=COLS)
    for col, channel in enumerate(l2_l1):
        stamp.set_l2_to_l1_transfer(channel, ifm_per_col[col], [ifm_mk_var] * ROWS)

    # kernel: negate this core's [tileSize] segment into a [tileSize] tile,
    # once per temporal iteration.
    ofm_mk_var = tensor_expr.TensorVar.make(
        [tileSize], ofm.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
    )
    stamp.set_kernel_arguments([ifm_mk_var, ofm_mk_var])
    stamp.set_kernel_function_name("negate_kernel")
    stamp.set_kernel_impl(Path("custom_negate.cpp"))
    stamp.set_kernel_params([tileSize])
    stamp.set_kernel_nb_calls(ITERS)

    # L1 -> L2 gather: each core drains its [tileSize] result into its own
    # (col, row) slot of the L2 tile.
    ofm_per_core = ofm_mem_var.Reshape([COLS * ROWS, tileSize]).TileTo(
        index=0, numTiles=COLS * ROWS
    )
    for col, col_channel in enumerate(l1_l2):
        l1_src: list[tensor_expr.TensorExpr] = []
        l2_dst: list[tensor_expr.TensorExpr] = []
        for row in range(ROWS):
            l1_src += [ofm_mk_var]
            l2_dst.append(ofm_per_core[col * ROWS + row])
        stamp.set_l1_to_l2_transfer(col_channel, l1_src, l2_dst)

    # L2 -> L3: send the full L2 tile and store it in DDR tiled the same
    # way, i.e. one tile per temporal iteration.
    ofm_ddr = (
        ofm.getTensorVar().Reshape([size]).TileBy(index=0, tileLen=L2_full_tile_size)
    )
    stamp.set_l2_to_l3_transfer(
        l2_l3[0], ofm_mem_var.Reshape([L2_full_tile_size]), ofm_ddr
    )
