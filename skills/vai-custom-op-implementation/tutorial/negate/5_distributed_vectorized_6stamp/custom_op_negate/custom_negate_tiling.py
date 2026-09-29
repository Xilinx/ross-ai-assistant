# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import math
from pathlib import Path

import tensor_expr

NUM_STAMPS = 6


# The flat input [N] is split into NUM_STAMPS contiguous DDR slices, one per
# stamp. Each core processes a fixed [tileSize] segment, so one pass over a
# stamp's array consumes COLS*ROWS*tileSize elements; a stamp slice larger than
# that is streamed through in ITERS temporal iterations rather than being staged
# whole in L2.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    assert len(tiling) >= NUM_STAMPS, f"Expected {NUM_STAMPS} stamps"

    # Unpack the op interface: negate is a unary op (one input, one output).
    ifm, ofm = opInterface

    assert (
        ifm.getPaddedShape() == ofm.getPaddedShape()
    ), "ifm/ofm padded shape inconsistent"

    # IMPORTANT: the tile size must be a multiple of the vector size used in the
    # kernel implementation, so that each core's starting address is aligned.
    # Here the kernel uses a vector size of 16 (see negate_vec_size).
    tileSize = 0x0800

    num_elements = math.prod(ifm.getPaddedShape())
    assert (
        num_elements % NUM_STAMPS == 0
    ), f"Number of elements must be divisible by {NUM_STAMPS}"
    num_elements_per_stamp = num_elements // NUM_STAMPS

    dtype = ifm.getDType()

    # Each stamp reads/writes its own contiguous DDR slice.
    ifm_stamp_tiled = (
        ifm.getTensorVar()
        .Reshape([NUM_STAMPS, num_elements_per_stamp])
        .TileTo(index=0, numTiles=NUM_STAMPS)
    )
    ofm_stamp_tiled = (
        ofm.getTensorVar()
        .Reshape([NUM_STAMPS, num_elements_per_stamp])
        .TileTo(index=0, numTiles=NUM_STAMPS)
    )

    for stamp_idx in range(NUM_STAMPS):
        stamp = tiling[stamp_idx][0]
        ROWS = stamp.aie_core_rows
        COLS = stamp.aie_core_cols

        # One pass over this stamp's array consumes a full
        # [COLS, ROWS, tileSize] tile; its slice is streamed through in ITERS
        # such tiles.
        L2_full_tile_size = tileSize * ROWS * COLS
        assert num_elements_per_stamp % L2_full_tile_size == 0, (
            f"Per-stamp size {num_elements_per_stamp} must be a multiple of "
            f"{L2_full_tile_size}"
        )
        ITERS = num_elements_per_stamp // L2_full_tile_size

        # Byte size of one L2 tile, i.e. the gap between ping and pong slots.
        L2_tile_bytes = L2_full_tile_size * tensor_expr.getOperandTypeSize(dtype)

        # Channels for each memory-hierarchy hop.
        l3_l2 = stamp.get_l3_to_l2_channels()
        l2_l1 = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
        l1_l2 = stamp.get_l1_to_l2_channels()
        l2_l3 = stamp.get_l2_to_l3_channels()

        # L2 buffers: one [COLS, ROWS, tileSize] tile each, in its own
        # memtile. Two Locations per buffer means the memtile ping-pongs
        # between them across the ITERS rotations.
        ifm_mem_var = tensor_expr.TensorVar.make(
            locations=[
                tensor_expr.Location(0, 0, 0x0),
                tensor_expr.Location(0, 0, L2_tile_bytes),  # in bytes
            ],
            shape=[COLS, ROWS, tileSize],
            type=dtype,
        )
        ofm_mem_var = tensor_expr.TensorVar.make(
            locations=[
                tensor_expr.Location(1, 0, 0x0),
                tensor_expr.Location(1, 0, L2_tile_bytes),  # in bytes
            ],
            shape=[COLS, ROWS, tileSize],
            type=ofm.getDType(),
        )

        # Both L2 buffers are refilled/drained once per iteration. This
        # rotation count MUST match the kernel-call count below, otherwise the
        # DMA dependency graph deadlocks.
        ifm_mem_var.setTemporalIterations(ITERS)
        ofm_mem_var.setTemporalIterations(ITERS)

        # L3 -> L2: cut this stamp's DDR slice into full-array tiles; each
        # temporal iteration transfers one tile into the memtile
        stamp.set_l3_to_l2_transfer(
            l3_l2[0],
            ifm_stamp_tiled[stamp_idx].TileBy(index=1, tileLen=L2_full_tile_size),
            ifm_mem_var.Reshape([L2_full_tile_size]),
        )

        # L2 -> L1: each column reads its [ROWS, tileSize] chunk,
        # broadcast to its ROWS cores. The kernel offsets internally by
        # row*tileSize, so each core gets the full column chunk.
        ifm_mk_var = tensor_expr.TensorVar.make(
            [ROWS, tileSize], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
        )
        ifm_per_col = ifm_mem_var.TileTo(index=0, numTiles=COLS)
        for col, channel in enumerate(l2_l1):
            stamp.set_l2_to_l1_transfer(channel, ifm_per_col[col], [ifm_mk_var] * ROWS)

        # kernel: negate this core's [tileSize] segment into a [tileSize]
        # tile, once per temporal iteration.
        ofm_mk_var = tensor_expr.TensorVar.make(
            [tileSize], ofm.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
        )
        stamp.set_kernel_arguments([ifm_mk_var, ofm_mk_var])
        stamp.set_kernel_function_name("negate_kernel")
        stamp.set_kernel_impl(Path("custom_negate.cpp"))
        stamp.set_kernel_params([tileSize])
        stamp.set_kernel_nb_calls(ITERS)

        # L1 -> L2 gather: each core drains its [tileSize] result into its
        # own (col, row) slot of the L2 tile.
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

        # L2 -> L3: send the full L2 tile and store it into this stamp's
        # DDR slice, tiled the same way: one tile per temporal iteration.
        stamp.set_l2_to_l3_transfer(
            l2_l3[0],
            ofm_mem_var.Reshape([L2_full_tile_size]),
            ofm_stamp_tiled[stamp_idx].TileBy(index=1, tileLen=L2_full_tile_size),
        )
