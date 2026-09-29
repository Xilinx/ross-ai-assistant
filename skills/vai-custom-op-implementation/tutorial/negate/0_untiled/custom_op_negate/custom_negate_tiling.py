# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


# This part defines the tiling, i.e., which chunks of data are transferred from
# DDR to the individual cores and the other way around.
#
# For this, it defines what all the buffers in L3 (DDR), L2 (mem) and L1 (core)
# look like and sets up the transfers from one level to the next.
#
# The specific tiling here is the most simple one that one can imagine:
# It uses all the cores in one stamp to do the same thing. Additionally, it assumes
# that all data fits in L1 (i.e., you don't really tile the input), and that no padding is required to
# be able to transfer the data (DMA requires 32-bit transfers, this tiling
# assumes that the input shape naturally fulfills that).
# Starting with this very simple form of tiling can allow you to validate that
# a single-core kernel implementation is valid. # This can also be a starting point to actually do tiling, by modifying the
# L2/L1 sizes of the buffers as well as the transfers.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase, single-stamp op: use the first (and only) phase.
    phase = tiling[0][0]

    # Unpack the op interface to get IFM and OFM tensors.
    ifm, ofm = opInterface

    # Our ONNX op is 2D, so we get two values here. The kernel treats the tensor
    # as a flat buffer of d0*d1 elements.
    d0, d1 = ifm.getShape()

    # Channels for each memory-hierarchy hop.
    l3_l2 = phase.get_l3_to_l2_channels()
    l2_l1 = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = phase.get_l1_to_l2_channels()
    l2_l3 = phase.get_l2_to_l3_channels()

    # L1 memory
    # The region 0xA000 - 0xDFFF is reserved (stack & heap).
    # 0x0000 - 0x9FFF (40 KB) and 0xE000 - 0xFFFF (8 KB) are available.
    # We use single buffering because only a single tile is transfered.
    # Each element takes 2 bytes (bfloat16).
    # We place both the IFM and OFM buffer in the first 40 KB region,
    # allowing 20 KB for each.
    assert d0 * d1 * 2 < 0x5000, "Input is too large to fit in L1"

    ifm_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(address=0x0)], shape=[d0, d1], type=ifm.getDType()
    )
    ofm_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(address=0x5000)], shape=[d0, d1], type=ofm.getDType()
    )

    # L2 memory
    # Each L2 memtile can hold 512 KB.
    # Each memtile can also be accessed from its neighboring tile east and west.
    # Later, we'll use a memtine in column 1 to write result using shimtile 0
    ifm_mem_var = tensor_expr.TensorVar.make(
        [  # column, row of memtile, offset in memtile (in bytes)
            tensor_expr.Location(0, 0, 0),
        ],
        shape=[d0, d1],
        type=ifm.getDType(),
    )
    ofm_mem_var = tensor_expr.TensorVar.make(
        [
            tensor_expr.Location(1, 0, 0),
        ],
        shape=[d0, d1],
        type=ofm.getDType(),
    )
    # We assume the tensor is small enough to fully fit L2/L1, so we don't
    # need to tile in this example. Just transfer the full IFM tensor from DDR to L2.
    # Use shimtile in col 0, row 0 which can write into memtile in col 0, row 0
    phase.set_l3_to_l2_transfer(l3_l2[0], ifm.getTensorVar(), ifm_mem_var)
    # And from L2 to L1. Only the first core does useful work, but every core is
    # fed the same input so that none is left unconnected (the L2->L1 channels
    # broadcast over columns).
    for channel in l2_l1:
        phase.set_l2_to_l1_transfer(channel, ifm_mem_var, ifm_mk_var)

    # Similarily, transfer the whole OFM from L1 to L2. Just like the L2->L1
    # broadcast above, every core must have its output port connected, so we
    # drain all channels. Every core computes the same result, so they all drain
    # into the single OFM L2 buffer (results are overwritten but it doesn't matter
    # because all core produce the same).
    for channel in l1_l2:
        n_cores = len(channel.core_tile_ports)
        phase.set_l1_to_l2_transfer(channel, ofm_mk_var, [ofm_mem_var] * n_cores)

    # And from L2 to DDR.
    #
    # Use shimtile in col 0, row 0 which can read from memtile in col 1, row 0 since
    # memtiles can read from their neighboring tiles.
    #
    # Note: Both l3 -> l2 and l3 -> l2 use shimtile col 0, row 0, port 0 but
    # because the direction is different, it gets mapped to a different physical
    # port in the AIE Array
    phase.set_l2_to_l3_transfer(l2_l3[0], ofm_mem_var, ofm.getTensorVar())

    # - Set kernel runtime parameters (RTPs)
    #   - The kernel's lp_params[0] is the flat element count (d0*d1).
    # - Specify the kernel function name and its location
    # - Lastly, specify the number of times the kernel function will execute
    phase.set_kernel_arguments([ifm_mk_var, ofm_mk_var])
    phase.set_kernel_function_name("negate_kernel")
    phase.set_kernel_impl(Path("custom_negate.cpp"))
    phase.set_kernel_params([d0 * d1])
    phase.set_kernel_nb_calls(1)
