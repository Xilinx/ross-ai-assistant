# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


# This part defines the tiling, i.e., which chunks of data are transferred from
# DDR to the individual cores and the other way around.
#
# The tiling here breaks the data in DDR into fixed-size tiles, transfers
# each tile to L2 and from there to L1, where the core processes it.
# The output is then transferred back in the same way.
# This is repeated until all data is processed.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase, single-stamp (single-core) op: use the first (and only) phase.
    phase = tiling[0][0]

    # Unpack the op interface to get IFM and OFM tensors.
    ifm, ofm = opInterface

    # This tiling doesn't support padding in L3, i.e. when the input shape's
    # innermost dimension is not a multiple of 2.
    assert ifm.getShape() == ifm.getPaddedShape()

    # Compute the total number of elements. The kernel treats the tensor as a
    # flat buffer.
    size = 1
    for dim in ifm.getShape():
        size *= dim

    # Fixed tile size (in elements). Each element takes 2 bytes (bfloat16).
    # 0x1000 = 4096 elements = 8 KB per tile, which fits comfortably in L1.
    tileSize = 0x1000
    assert size % tileSize == 0, "Total size must be a multiple of tileSize"
    num_tiles = size // tileSize

    dtype = ifm.getDType()
    tileBytes = tileSize * tensor_expr.getOperandTypeSize(dtype)

    # Channels for each memory-hierarchy hop.
    l3_l2 = phase.get_l3_to_l2_channels()
    l2_l1 = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = phase.get_l1_to_l2_channels()
    l2_l3 = phase.get_l2_to_l3_channels()

    # L2 memory - hold a single tile of input and a single tile of output, each
    # in its own memtile. Two Locations per buffer means the memtile
    # ping-pongs between them, so the DMA filling the next tile overlaps the one
    # draining the current tile.
    ifm_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(0, 0, 0x0),
            tensor_expr.Location(0, 0, tileBytes),  # in bytes
        ],
        shape=[tileSize],
        type=dtype,
    )
    ofm_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(1, 0, 0x0),
            tensor_expr.Location(1, 0, tileBytes),  # in bytes
        ],
        shape=[tileSize],
        type=ofm.getDType(),
    )

    # Both L2 buffers are refilled/drained once per tile. This rotation count
    # MUST match the kernel-call count below, otherwise the DMA deadlocks.
    ifm_l2.setTemporalIterations(num_tiles)
    ofm_l2.setTemporalIterations(num_tiles)

    # L3 -> L2: cut the flat IFM in DDR into tiles of tileSize on the sender
    # side; each temporal iteration transfers one tile into the memtile.
    ifm_ddr = ifm.getTensorVar().Reshape([size]).TileBy(index=0, tileLen=tileSize)
    phase.set_l3_to_l2_transfer(l3_l2[0], ifm_ddr, ifm_l2)

    # L1 memory - one tile, double-buffered so that the transfer of the next tile
    # overlaps the computation of the current one. Use automatic l1 memory placement
    # for simplicity.
    ifm_mk_var = tensor_expr.TensorVar.make(
        [tileSize], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    ofm_mk_var = tensor_expr.TensorVar.make(
        [tileSize], ofm.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
    )

    # L2 -> L1: transfer the full content of the L2 buffer, i.e. the whole tile.
    # Only the single core does useful work, but every core must have its input
    # port connected, so we feed all broadcast channels the same buffer.
    for channel in l2_l1:
        phase.set_l2_to_l1_transfer(channel, ifm_l2, ifm_mk_var)

    # Kernel: process tileSize elements per invocation, called once per tile.
    phase.set_kernel_arguments([ifm_mk_var, ofm_mk_var])
    phase.set_kernel_function_name("negate_kernel")
    phase.set_kernel_impl(Path("custom_negate.cpp"))
    phase.set_kernel_params([tileSize])
    phase.set_kernel_nb_calls(num_tiles)

    # L1 -> L2: transfer the full output tile into the L2 output buffer. Every
    # core's output port must be connected, so we drain all channels.
    for channel in l1_l2:
        n_cores = len(channel.core_tile_ports)
        phase.set_l1_to_l2_transfer(channel, [ofm_mk_var] * n_cores, [ofm_l2] * n_cores)

    # L2 -> L3: send the full L2 buffer and store it in DDR tiled by tileSize,
    # i.e. one tile per temporal iteration into the matching slot of the OFM.
    ofm_ddr = ofm.getTensorVar().Reshape([size]).TileBy(index=0, tileLen=tileSize)
    phase.set_l2_to_l3_transfer(l2_l3[0], ofm_l2, ofm_ddr)
