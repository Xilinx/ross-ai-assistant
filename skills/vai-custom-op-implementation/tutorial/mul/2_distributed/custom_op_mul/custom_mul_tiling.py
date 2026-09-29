# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


# This part defines the tiling, i.e., which chunks of data are transferred from
# DDR to the individual cores and the other way around.
#
# For this, it defines what all the buffers in L3 (DDR), L2 (mem) and L1 (core)
# look like and sets up the transfers from one level to the next.
#
# This tiling variant uses the 16 cores provided by the overlay.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase op: use the first (and only) phase of the first stamp.
    phase = tiling[0][0]

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

    tile_size = 64
    n_rows = 4
    n_columns = 4
    n_cores = n_rows * n_columns

    # One L2 rotation feeds all 16 cores with one tile each, so the DDR arrays
    # are consumed in chunks of n_cores * tile_size elements.
    shared_tile_size = tile_size * n_cores
    assert (
        ddr_size % shared_tile_size == 0
    ), f"Operand size must be a multiple of {shared_tile_size}"
    num_tiles = ddr_size // shared_tile_size

    dtype = ifm.getDType()

    # Channels for each memory-hierarchy hop. A core has one column-input and
    # one row-input L2->L1 port, so the two operands take separate broadcast
    # axes: ifm over columns, wts over rows.
    l3_l2 = phase.get_l3_to_l2_channels()
    l2_l1_cols = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l2_l1_rows = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)
    l1_l2 = phase.get_l1_to_l2_channels()
    l2_l3 = phase.get_l2_to_l3_channels()

    # L2 vars (each holding 16 L1 tiles): ifm on col0 (broadcast over AIE
    # columns), wts on col3 (over AIE rows), ofm drains from col1. Two Locations
    # per buffer means the memtile ping-pongs, overlapping fill and drain.
    ifm_mem_var = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(0, 0, 0x0),
            tensor_expr.Location(0, 0, 0x1000),
        ],
        shape=[n_columns, n_rows, tile_size],
        type=dtype,
    )
    wts_mem_var = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(3, 0, 0x0),
            tensor_expr.Location(3, 0, 0x1000),
        ],
        shape=[n_columns, n_rows, tile_size],
        type=wts.getDType(),
    )
    ofm_mem_var = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(1, 0, 0x4000),
            tensor_expr.Location(1, 0, 0x5000),
        ],
        shape=[n_columns, n_rows, tile_size],
        type=ofm.getDType(),
    )

    # All three L2 buffers are refilled/drained once per chunk. This rotation
    # count MUST match the kernel-call count below, otherwise the DMA deadlocks.
    ifm_mem_var.setTemporalIterations(num_tiles)
    wts_mem_var.setTemporalIterations(num_tiles)
    ofm_mem_var.setTemporalIterations(num_tiles)

    # L1 vars - automatic placement. Sizing constraints:
    #   1) ifm is broadcast along columns and wts along rows, so each core holds
    #      a full column of ifm and a full row of wts -- both 4x its ofm tile.
    #   2) L1 is 64 KB total, with 0xB000-0xD000 reserved for stack and heap.
    #   3) Padding is unsupported, so ddr_size must be a multiple of
    #      n_cores * tile_size.
    ifm_mk_var = tensor_expr.TensorVar.make(
        [n_rows, tile_size], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    wts_mk_var = tensor_expr.TensorVar.make(
        [n_columns, tile_size],
        wts.getDType(),
        tensor_expr.BufferingStrategy.DoubleBuffered,
    )

    # There is only one tile of OFM needed per core, since the result tiles are
    # mapped one-to-one to the DDR.
    ofm_mk_var = tensor_expr.TensorVar.make(
        [tile_size], ofm.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
    )

    # L3 -> L2: cut the flat DDR arrays into shared_tile_size chunks on the
    # sender side; one chunk lands in the memtile per temporal iteration.
    phase.set_l3_to_l2_transfer(
        l3_l2[0],
        ifm.getTensorVar()
        .Reshape([ddr_size])
        .TileBy(index=0, tileLen=shared_tile_size),
        ifm_mem_var.Reshape([shared_tile_size]),
    )

    # IFM gets broadcast across columns. Therefore, each column gets 1/4 of the
    # L2 data... and _every_ core along that column gets the whole data.
    ifm_per_column = ifm_mem_var.TileTo(index=0, numTiles=n_columns)
    for col, col_channel in enumerate(l2_l1_cols):
        phase.set_l2_to_l1_transfer(col_channel, ifm_per_column[col], ifm_mk_var)

    # wts fills from the L3->L2 channel that can reach its memtile column (3).
    phase.set_l3_to_l2_transfer(
        l3_l2[2],
        wts.getTensorVar()
        .Reshape([ddr_size])
        .TileBy(index=0, tileLen=shared_tile_size),
        wts_mem_var.Reshape([shared_tile_size]),
    )

    # WTS get broadcast across rows. Like IFM, each row gets 1/4 of the L2's
    # contents... but we indeed need to transpose it! The transpose to
    # [n_rows, n_columns, tile_size] makes each row's block contiguous on the
    # outer dimension, so cutting it into n_rows pieces hands row r its share.
    wts_per_row = wts_mem_var.Transpose([1, 0, 2]).TileTo(index=0, numTiles=n_rows)
    for row, row_channel in enumerate(l2_l1_rows):
        phase.set_l2_to_l1_transfer(row_channel, wts_per_row[row], wts_mk_var)

    # Kernel: core (col, row) multiplies its own tile_size-element slice, and is
    # called once per L2 rotation.
    phase.set_kernel_arguments([ifm_mk_var, wts_mk_var, ofm_mk_var])
    phase.set_kernel_function_name("mul_kernel")
    phase.set_kernel_impl(Path("custom_mul.cpp"))
    phase.set_kernel_params([tile_size])
    phase.set_kernel_nb_calls(num_tiles)

    # Each AIE core sends data back to L2 (it's a unicast), so each needs a read
    # pattern and a matching write pattern into the shared buffer. Every core
    # reads its own ofm_mk_var; the write patterns come from viewing
    # ofm_mem_var as n_cores flat tiles, where core (col, row) owns tile
    # col * n_rows + row.
    ofm_per_core = ofm_mem_var.Reshape([n_cores, tile_size]).TileTo(
        index=0, numTiles=n_cores
    )
    for col, col_channel in enumerate(l1_l2):
        phase.set_l1_to_l2_transfer(
            col_channel,
            [ofm_mk_var] * n_rows,
            [ofm_per_core[col * n_rows + row] for row in range(n_rows)],
        )

    # L2 -> L3: store the full L2 buffer into DDR tiled by shared_tile_size,
    # i.e. one chunk per temporal iteration.
    phase.set_l2_to_l3_transfer(
        l2_l3[0],
        ofm_mem_var.Reshape([shared_tile_size]),
        ofm.getTensorVar()
        .Reshape([ddr_size])
        .TileBy(index=0, tileLen=shared_tile_size),
    )
