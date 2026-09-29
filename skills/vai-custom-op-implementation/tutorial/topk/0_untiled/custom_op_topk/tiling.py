# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


# This part defines the tiling for the TopK operation.
# TopK selects the K largest elements from the input tensor.
#
# For this untiled version, we assume:
# - Single core execution
# - All data fits in L1
# - Input: [d0, d1], Output: [d0, k]
#
# This tiling allows "single-core" execution of the kernel: all cores execute
# the same kernel on the same data.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase, single-stamp op: use the first (and only) phase.
    phase = tiling[0][0]

    # Unpack the op interface to get IFM and OFM tensors.
    ifm, ofm = opInterface

    # Input is 2D: [d0, d1] -> total_elements = d0 * d1.
    # Output is 2D: [d0, k] -> k top elements per batch row.
    ifm_shape = ifm.getShape()
    ofm_shape = ofm.getShape()
    if len(ifm_shape) != 2:
        raise ValueError(f"Expected 2D input tensor, got shape: {ifm_shape}")
    if len(ofm_shape) != 2:
        raise ValueError(f"Expected 2D output tensor, got shape: {ofm_shape}")

    d0, d1 = ifm_shape
    out_d0, k = ofm_shape
    assert out_d0 == d0, f"Output batch dimension {out_d0} must match input {d0}"
    total_elements = d0 * d1

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
    # We place both the IFM and OFM buffer in the first 40 KB region.
    assert total_elements * 2 < 0x5000, "Input is too large to fit in L1"
    assert k * d0 * 2 < 0x3000, "Output is too large to fit in L1"

    ifm_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(address=0x0)], shape=[d0, d1], type=ifm.getDType()
    )
    ofm_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(address=0x5000)], shape=[d0, k], type=ofm.getDType()
    )

    # L2 memory
    # Each L2 memtile can hold 512 KB.
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
        shape=[d0, k],
        type=ofm.getDType(),
    )

    # We assume the tensor is small enough to fully fit L2/L1, so we don't need
    # to tile. Transfer the full IFM tensor from DDR to L2.
    phase.set_l3_to_l2_transfer(l3_l2[0], ifm.getTensorVar(), ifm_mem_var)
    # And from L2 to L1. Only the first core does useful work, but every core is
    # fed the same input so that none is left unconnected (the L2->L1 channels
    # broadcast over columns).
    for channel in l2_l1:
        phase.set_l2_to_l1_transfer(channel, ifm_mem_var, ifm_mk_var)

    # Similarly, transfer the whole OFM from L1 to L2. Just like the L2->L1
    # broadcast above, every core must have its output port connected.
    # Every core computes the same result, which are written into the single OFM
    # L2 buffer (the redundant results aren't used).
    for channel in l1_l2:
        n_cores = len(channel.core_tile_ports)
        phase.set_l1_to_l2_transfer(channel, ofm_mk_var, [ofm_mem_var] * n_cores)
    # And from L2 to DDR.
    phase.set_l2_to_l3_transfer(l2_l3[0], ofm_mem_var, ofm.getTensorVar())

    # Set kernel parameters (RTPs) and bind the kernel implementation.
    # lp_params[0] = total number of input elements
    # lp_params[1] = k (number of top elements to select per batch)
    # lp_params[2] = batch_size (d0)
    phase.set_kernel_arguments([ifm_mk_var, ofm_mk_var])
    phase.set_kernel_function_name("topk_kernel")
    phase.set_kernel_impl(Path("kernel.cpp"))
    phase.set_kernel_params([total_elements, k, d0])
    phase.set_kernel_nb_calls(1)
