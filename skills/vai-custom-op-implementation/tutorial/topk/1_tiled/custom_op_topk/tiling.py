# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


# Tiled, "single-core" implementation of TopK.
#
# This implementation is an evolution of the single-core one, where the IFM is
# tiled, allowing to describe a large input tile by tile. It shows how to use
# temporal tiling and how to hold the OFM buffer across IFM tiles.
#
# This implementation of TopK, like the single-core one, feeds the same data to
# all cores, that all compute the same result.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase, single-stamp (single-core) op: use the first (and only) phase.
    phase = tiling[0][0]

    # Unpack the op interface to get IFM and OFM tensors.
    ifm, ofm = opInterface

    # Shapes - expecting [batch, width] input and [batch, k] output.
    ifm_shape = ifm.getShape()
    ofm_shape = ofm.getShape()

    if len(ifm_shape) != 2:
        raise ValueError(f"Expected 2D input tensor, got shape: {ifm_shape}")
    batch_size, width = ifm_shape

    if len(ofm_shape) != 2:
        raise ValueError(f"Expected 2D output tensor, got shape: {ofm_shape}")
    out_batch, k = ofm_shape
    assert (
        out_batch == batch_size
    ), f"Output batch {out_batch} must match input {batch_size}"

    # L1 input tile size (elements). Each batch row is split into this many-element
    # tiles; the reduction accumulates across them.
    l1_tile_size = 256
    assert (
        width % l1_tile_size == 0
    ), f"Width {width} must be a multiple of tile size {l1_tile_size}"
    tiles_per_batch = width // l1_tile_size

    # Total IFM elements and total number of IFM tiles (= kernel calls).
    total_size = batch_size * width
    num_ifm_tiles = total_size // l1_tile_size  # batch_size * tiles_per_batch

    ifm_dtype = ifm.getDType()
    ofm_dtype = ofm.getDType()

    # Channels for each memory-hierarchy hop.
    l3_l2 = phase.get_l3_to_l2_channels()
    l2_l1 = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = phase.get_l1_to_l2_channels()
    l2_l3 = phase.get_l2_to_l3_channels()

    # L2 memory - stage the whole IFM and the whole OFM, each in its own memtile.
    ifm_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(0, 0, 0x0)],
        shape=[total_size],
        type=ifm_dtype,
    )
    ofm_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(1, 0, 0x0)],
        shape=[batch_size * k],
        type=ofm_dtype,
    )

    # L3 -> L2: transfer the full IFM tensor from DDR to L2 in one shot.
    phase.set_l3_to_l2_transfer(
        l3_l2[0], ifm.getTensorVar().Reshape([total_size]), ifm_l2
    )

    # L1 memory - one IFM tile (double-buffered) and one OFM row buffer of k
    # elements. The OFM buffer is asynchronous: it is held across tiles_per_batch
    # kernel calls, so it is written back to L2 once per batch.
    ifm_mk_var = tensor_expr.TensorVar.make(
        [l1_tile_size], ifm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    ofm_mk_var = tensor_expr.TensorVar.make(
        [k], ofm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )

    # L2 -> L1: cut the L2 IFM buffer into num_ifm_tiles tiles of l1_tile_size;
    # we send each core a tile per kernel call.
    # Every core must be connected, although they all perform the same operation.
    ifm_l2_tiled = ifm_l2.TileBy(index=0, tileLen=l1_tile_size)
    for channel in l2_l1:
        phase.set_l2_to_l1_transfer(channel, ifm_l2_tiled, ifm_mk_var)

    # Kernel: process one IFM tile per call, called once per IFM tile.
    #   lp_params[0] = tile size (elements per tile)
    #   lp_params[1] = number of tiles per batch (async release period)
    #   lp_params[2] = k
    phase.set_kernel_arguments([ifm_mk_var, ofm_mk_var])
    phase.set_kernel_function_name("topk_kernel")
    phase.set_kernel_impl(Path("kernel.cpp"))
    phase.set_kernel_params([l1_tile_size, tiles_per_batch, k])
    phase.set_kernel_nb_calls(num_ifm_tiles)

    # L1 -> L2: write the k-element OFM row into the matching batch slot of the L2
    # output buffer. The OFM buffer is asynchronous and stays live for
    # tiles_per_batch kernel calls, so the L1->L2 transfer carries
    # ratio=tiles_per_batch: one write per batch (num_ifm_tiles / tiles_per_batch
    # = batch_size writes total). Every core's output port must be connected, so
    # collect all channels.
    ofm_l2_tiled = ofm_l2.TileBy(index=0, tileLen=k)
    for channel in l1_l2:
        n_cores = len(channel.core_tile_ports)
        phase.set_l1_to_l2_transfer(
            channel,
            [ofm_mk_var] * n_cores,
            [ofm_l2_tiled] * n_cores,
            ratio=tiles_per_batch,
        )

    # L2 -> L3: send the whole OFM tensor from L2 back to DDR in one shot.
    phase.set_l2_to_l3_transfer(
        l2_l3[0], ofm_l2, ofm.getTensorVar().Reshape([batch_size * k])
    )
