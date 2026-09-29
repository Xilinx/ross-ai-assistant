# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


# This tiling variant uses 2 stamps, each with 16 cores (4x4 grid).
# The DDR data (4096 elements) is split across stamps using TileBy + index:
#   - Stamp 0 processes elements 0-2047
#   - Stamp 1 processes elements 2048-4095
# Within each stamp, the L2/L1 tiling is identical to the single-stamp
# 2_distributed example.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Unpack the op interface to get IFM, WTS and OFM tensors.
    ifm, wts, ofm = opInterface

    assert ifm.getShape() == wts.getShape(), "Operands shapes mismatch"
    assert ifm.getShape() == ofm.getShape(), "Operand and result shapes mismatch"

    assert ifm.getPaddedShape() == wts.getPaddedShape(), "Operands padding mismatch"
    assert (
        ifm.getPaddedShape() == ofm.getPaddedShape()
    ), "Operand and result paddings mismatch"

    # Flatten down the shape.
    ddr_size = 1
    for dim in ifm.getPaddedShape():
        ddr_size *= dim

    assert len(tiling) >= 2, "Expected 2 stamps"
    n_stamps = 2
    tile_size = 64
    n_rows = 4
    n_cols = 4
    n_cores = n_rows * n_cols

    # One L2 rotation feeds all 16 cores of a stamp one tile each, so a stamp's
    # share of the DDR arrays is consumed in n_cores * tile_size chunks.
    shared_tile_size = tile_size * n_cores  # 1024
    per_stamp = ddr_size // n_stamps  # 2048
    assert per_stamp * n_stamps == ddr_size, "size must divide over stamps"
    assert (
        per_stamp % shared_tile_size == 0
    ), f"Per-stamp size must be a multiple of {shared_tile_size}"
    num_tiles = per_stamp // shared_tile_size  # 2

    dtype = ifm.getDType()

    # Get the full DDR tensors (shape [4096]).
    # Use TileBy to split into n_stamps tiles of per_stamp each.
    # This keeps buffer_dimension = {4096} and uses offset to select stamps.
    ifm_ddr = ifm.getTensorVar().Reshape([ddr_size])
    wts_ddr = wts.getTensorVar().Reshape([ddr_size])
    ofm_ddr = ofm.getTensorVar().Reshape([ddr_size])

    ifm_stamp_tiles = ifm_ddr.TileBy(index=0, tileLen=per_stamp)
    wts_stamp_tiles = wts_ddr.TileBy(index=0, tileLen=per_stamp)
    ofm_stamp_tiles = ofm_ddr.TileBy(index=0, tileLen=per_stamp)

    for stamp_idx in range(n_stamps):
        # Single-phase op: use the first (and only) phase of this stamp.
        phase = tiling[stamp_idx][0]

        # Channels for each memory-hierarchy hop. A core has one column-input
        # and one row-input L2->L1 port, so the two operands take separate
        # broadcast axes: ifm over columns, wts over rows.
        l3_l2 = phase.get_l3_to_l2_channels()
        l2_l1_cols = phase.get_l2_to_l1_channels(
            broadcast=tensor_expr.broadcast_on.COLUMNS
        )
        l2_l1_rows = phase.get_l2_to_l1_channels(
            broadcast=tensor_expr.broadcast_on.ROWS
        )
        l1_l2 = phase.get_l1_to_l2_channels()
        l2_l3 = phase.get_l2_to_l3_channels()

        # L2 buffers (identical per stamp): ifm on col0 (broadcast over AIE
        # columns), wts on col3 (over AIE rows), ofm drains from col1. Two
        # Locations per buffer means the memtile ping-pongs, overlapping fill
        # and drain.
        ifm_mem_var = tensor_expr.TensorVar.make(
            locations=[
                tensor_expr.Location(0, 0, 0x0),
                tensor_expr.Location(0, 0, 0x1000),
            ],
            shape=[n_cols, n_rows, tile_size],
            type=dtype,
        )
        wts_mem_var = tensor_expr.TensorVar.make(
            locations=[
                tensor_expr.Location(3, 0, 0x0),
                tensor_expr.Location(3, 0, 0x1000),
            ],
            shape=[n_cols, n_rows, tile_size],
            type=wts.getDType(),
        )
        ofm_mem_var = tensor_expr.TensorVar.make(
            locations=[
                tensor_expr.Location(1, 0, 0x4000),
                tensor_expr.Location(1, 0, 0x5000),
            ],
            shape=[n_cols, n_rows, tile_size],
            type=ofm.getDType(),
        )

        # Refilled/drained once per chunk. This rotation count MUST match the
        # kernel-call count below, otherwise the DMA deadlocks.
        ifm_mem_var.setTemporalIterations(num_tiles)
        wts_mem_var.setTemporalIterations(num_tiles)
        ofm_mem_var.setTemporalIterations(num_tiles)

        # L1 buffers (identical per stamp) - automatic placement. ifm is
        # broadcast along a column and wts along a row, so each core holds a
        # full column of ifm and a full row of wts -- both 4x its ofm tile --
        # and the kernel picks its own slice.
        ifm_mk_var = tensor_expr.TensorVar.make(
            [n_rows, tile_size], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
        )
        wts_mk_var = tensor_expr.TensorVar.make(
            [n_cols, tile_size],
            wts.getDType(),
            tensor_expr.BufferingStrategy.DoubleBuffered,
        )

        # There is only one tile of OFM needed per core, since the result tiles
        # are mapped one-to-one to the DDR.
        ofm_mk_var = tensor_expr.TensorVar.make(
            [tile_size], ofm.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
        )

        # DDR -> L2: select this stamp's portion, then TileBy for L2 chunks;
        # one chunk per temporal iteration.
        phase.set_l3_to_l2_transfer(
            l3_l2[0],
            ifm_stamp_tiles[stamp_idx].TileBy(index=0, tileLen=shared_tile_size),
            ifm_mem_var.Reshape([shared_tile_size]),
        )

        # L2 -> L1: IFM broadcast across columns. Each column gets 1/4 of the
        # L2 data... and _every_ core along that column gets the whole data.
        ifm_per_column = ifm_mem_var.TileTo(index=0, numTiles=n_cols)
        for col, col_channel in enumerate(l2_l1_cols):
            phase.set_l2_to_l1_transfer(col_channel, ifm_per_column[col], ifm_mk_var)

        # DDR -> L2 (WTS). wts fills from the L3->L2 channel that can reach its
        # memtile column (3).
        phase.set_l3_to_l2_transfer(
            l3_l2[2],
            wts_stamp_tiles[stamp_idx].TileBy(index=0, tileLen=shared_tile_size),
            wts_mem_var.Reshape([shared_tile_size]),
        )

        # L2 -> L1: WTS broadcast across rows. Like IFM, each row gets 1/4 of
        # the L2's contents... but we indeed need to transpose it! The
        # transpose to [n_rows, n_cols, tile_size] makes each row's block
        # contiguous on the outer dimension, so cutting it into n_rows pieces
        # hands row r its share.
        wts_per_row = wts_mem_var.Transpose([1, 0, 2]).TileTo(index=0, numTiles=n_rows)
        for row, row_channel in enumerate(l2_l1_rows):
            phase.set_l2_to_l1_transfer(row_channel, wts_per_row[row], wts_mk_var)

        # Kernel: core (col, row) multiplies its own tile_size-element slice,
        # and is called once per L2 rotation.
        phase.set_kernel_arguments([ifm_mk_var, wts_mk_var, ofm_mk_var])
        phase.set_kernel_function_name("mul_kernel")
        phase.set_kernel_impl(Path("custom_mul.cpp"))
        phase.set_kernel_params([tile_size])
        phase.set_kernel_nb_calls(num_tiles)

        # L1 -> L2: OFM unicast, so each core needs a read pattern and a
        # matching write pattern into the shared buffer. The write patterns come
        # from viewing ofm_mem_var as n_cores flat tiles, where core (col, row)
        # owns tile col * n_rows + row.
        ofm_per_core = ofm_mem_var.Reshape([n_cores, tile_size]).TileTo(
            index=0, numTiles=n_cores
        )
        for col, col_channel in enumerate(l1_l2):
            phase.set_l1_to_l2_transfer(
                col_channel,
                [ofm_mk_var] * n_rows,
                [ofm_per_core[col * n_rows + row] for row in range(n_rows)],
            )

        # L2 -> DDR: select this stamp's portion, then TileBy for L2 chunks,
        # i.e. one chunk per temporal iteration.
        phase.set_l2_to_l3_transfer(
            l2_l3[0],
            ofm_mem_var.Reshape([shared_tile_size]),
            ofm_stamp_tiles[stamp_idx].TileBy(index=0, tileLen=shared_tile_size),
        )
