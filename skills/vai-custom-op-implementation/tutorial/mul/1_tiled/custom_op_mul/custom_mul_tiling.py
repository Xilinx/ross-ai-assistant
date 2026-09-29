# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


# This part defines the tiling, i.e., which chunks of data are transferred from
# DDR to the individual cores and the other way around.
#
# For this, it defines what all the buffers in L3 (DDR), L2 (mem) and L1 (core)
# look like and sets up the transfers from one level to the next.
#
# This tiling splits the global (DDR) arrays into chunks of size 64, and
# transfers one such chunk per operand into shared (L2) memory. That chunk is
# then processed and the result (of the same size) is sent back to the DDR.
#
# This example uses a single AIE core: there is tiling, but no parallelism
# across tiles (which will require distributing the data across the AIE cores).
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase, single-stamp (single-core) op: use the first (and only) phase.
    phase = tiling[0][0]

    # Unpack the op interface to get IFM, WTS and OFM tensors.
    ifm, wts, ofm = opInterface

    assert ifm.getShape() == wts.getShape(), "Operands shapes mismatch"
    assert ifm.getShape() == ofm.getShape(), "Operand and result shapes mismatch"

    # Flatten down the shape.
    size = 1
    for dim in ifm.getShape():
        size *= dim

    tile_size = 64
    # Padding requests are not yet supported, so the buffer size must be a
    # multiple of 64 elements.
    assert size % tile_size == 0, f"Operand size must be a multiple of {tile_size}"
    num_tiles = size // tile_size

    dtype = ifm.getDType()
    tile_bytes = tile_size * tensor_expr.getOperandTypeSize(dtype)

    # Channels for each memory-hierarchy hop. A core has one row-input and one
    # column-input L2->L1 port, so the two operands take separate broadcast
    # axes: ifm over columns, wts over rows.
    l3_l2 = phase.get_l3_to_l2_channels()
    l2_l1_cols = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l2_l1_rows = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)
    l1_l2 = phase.get_l1_to_l2_channels()
    l2_l3 = phase.get_l2_to_l3_channels()

    # L2 vars (each containing one tile of the corresponding DDR var), each in
    # its own memtile column: ifm in col0 (broadcast over AIE columns), wts in
    # col3 (broadcast over AIE rows), ofm drains from col1. Two Locations per
    # buffer means the memtile ping-pongs between them, so the DMA filling the
    # next tile overlaps the one draining the current tile.
    ifm_mem_var = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(0, 0, 0x0),
            tensor_expr.Location(0, 0, tile_bytes),  # in bytes
        ],
        shape=[tile_size],
        type=dtype,
    )
    wts_mem_var = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(3, 0, 0x0),
            tensor_expr.Location(3, 0, tile_bytes),  # in bytes
        ],
        shape=[tile_size],
        type=wts.getDType(),
    )
    ofm_mem_var = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(1, 0, 0x0),
            tensor_expr.Location(1, 0, tile_bytes),  # in bytes
        ],
        shape=[tile_size],
        type=ofm.getDType(),
    )

    # All three L2 buffers are refilled/drained once per tile. This rotation
    # count MUST match the kernel-call count below, otherwise the DMA deadlocks.
    ifm_mem_var.setTemporalIterations(num_tiles)
    wts_mem_var.setTemporalIterations(num_tiles)
    ofm_mem_var.setTemporalIterations(num_tiles)

    # L1 vars (double-buffered) - Using automatic L1 placement.
    # Double buffering allows the kernel to process tile N while DMA prefetches
    # tile N+1, enabling computation and data transfer to overlap.
    ifm_mk_var = tensor_expr.TensorVar.make(
        [tile_size], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    wts_mk_var = tensor_expr.TensorVar.make(
        [tile_size], wts.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
    )
    ofm_mk_var = tensor_expr.TensorVar.make(
        [tile_size], ofm.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
    )

    # L3 -> L2: cut the flat IFM and WTS in DDR into tiles of tile_size on the
    # sender side; each temporal iteration transfers one tile into the memtile.
    phase.set_l3_to_l2_transfer(
        l3_l2[0],
        ifm.getTensorVar().Reshape([size]).TileBy(index=0, tileLen=tile_size),
        ifm_mem_var,
    )
    phase.set_l3_to_l2_transfer(
        l3_l2[2],
        wts.getTensorVar().Reshape([size]).TileBy(index=0, tileLen=tile_size),
        wts_mem_var,
    )

    # L2 -> L1: transfer the full content of each L2 buffer, i.e. the whole tile.
    # ifm broadcasts over columns, wts over rows. Only the single core does
    # useful work, but every core must have its input port connected, so we feed
    # all broadcast channels the same buffer.
    for channel in l2_l1_cols:
        phase.set_l2_to_l1_transfer(channel, ifm_mem_var, ifm_mk_var)
    for channel in l2_l1_rows:
        phase.set_l2_to_l1_transfer(channel, wts_mem_var, wts_mk_var)

    # Kernel: process tile_size elements per invocation, called once per tile.
    phase.set_kernel_arguments([ifm_mk_var, wts_mk_var, ofm_mk_var])
    phase.set_kernel_function_name("mul_kernel")
    phase.set_kernel_impl(Path("custom_mul.cpp"))
    phase.set_kernel_params([tile_size])
    phase.set_kernel_nb_calls(num_tiles)

    # L1 -> L2: transfer the full output tile into the L2 output buffer. Every
    # core's output port must be connected, so we drain all channels.
    for channel in l1_l2:
        n_cores = len(channel.core_tile_ports)
        phase.set_l1_to_l2_transfer(
            channel, [ofm_mk_var] * n_cores, [ofm_mem_var] * n_cores
        )

    # L2 -> L3: send the full L2 buffer and store it in DDR tiled by tile_size,
    # i.e. one tile per temporal iteration into the matching slot of the OFM.
    phase.set_l2_to_l3_transfer(
        l2_l3[0],
        ofm_mem_var,
        ofm.getTensorVar().Reshape([size]).TileBy(index=0, tileLen=tile_size),
    )
