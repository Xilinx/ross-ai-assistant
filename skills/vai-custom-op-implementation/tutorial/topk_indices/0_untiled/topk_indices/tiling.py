# Copyright (C) 2025 - 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


# This part defines the tiling, i.e., which chunks of data are transferred how
# from DDR to the individual cores.
#
# The tiling here is the simplest single-core "untiled" form: the whole IFM is
# transferred DDR->L2->L1, the core processes it, and the whole OFM is
# transferred back L1->L2->DDR. Only the first core does useful work, but every
# core must have its ports connected, so the input is broadcast to all cores.
#
# This is a TopK-indices op: the output holds the indices of the K largest input
# values (packed into the output dtype's bits by the kernel). The OFM therefore
# uses the OUTPUT tensor's declared dtype (ofm.getDType()), which differs from
# the IFM dtype in general.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase, single-stamp op: use the first (and only) phase.
    phase = tiling[0][0]

    # Unpack the op interface to get IFM and OFM tensors.
    ifm, ofm = opInterface

    def get_num_elems(fm):
        n = 1
        for dim in fm.getShape():
            n *= dim
        return n

    ifm_elems = get_num_elems(ifm)
    ofm_elems = get_num_elems(ofm)

    # Channels for each memory-hierarchy hop.
    l3_l2 = phase.get_l3_to_l2_channels()
    l2_l1 = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = phase.get_l1_to_l2_channels()
    l2_l3 = phase.get_l2_to_l3_channels()

    # L1 memory
    # The region 0xA000 - 0xDFFF is reserved (stack & heap).
    # 0x0000 - 0x9FFF (40 KB) and 0xE000 - 0xFFFF (8 KB) are available.
    # We use single buffering because only a single tile is transferred.
    # We place the IFM buffer at 0x0000 and the OFM buffer at 0x5000, both inside
    # the first 40 KB region.
    def dtype_bytes(dt):
        return {
            tensor_expr.OperandType.Int8: 1,
            tensor_expr.OperandType.UInt8: 1,
            tensor_expr.OperandType.Int16: 2,
            tensor_expr.OperandType.UInt16: 2,
            tensor_expr.OperandType.BFloat16: 2,
            tensor_expr.OperandType.Float16: 2,
            tensor_expr.OperandType.Int32: 4,
            tensor_expr.OperandType.UInt32: 4,
            tensor_expr.OperandType.Float32: 4,
            tensor_expr.OperandType.Int64: 8,
            tensor_expr.OperandType.UInt64: 8,
            tensor_expr.OperandType.Float64: 8,
        }[dt]

    ifm_bytes = ifm_elems * dtype_bytes(ifm.getDType())
    ofm_bytes = ofm_elems * dtype_bytes(ofm.getDType())
    assert ifm_bytes <= 0x5000, "Input is too large to fit in L1"
    assert ofm_bytes <= 0x5000, "Output is too large to fit in L1"

    ifm_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(address=0x0)], shape=[ifm_elems], type=ifm.getDType()
    )
    ofm_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(address=0x5000)], shape=[ofm_elems], type=ofm.getDType()
    )

    # L2 memory
    # Each L2 memtile can hold 512 KB. IFM and OFM live in separate memtiles.
    ifm_mem_var = tensor_expr.TensorVar.make(
        [  # column, row of memtile, offset in memtile (in bytes)
            tensor_expr.Location(0, 0, 0),
        ],
        shape=[ifm_elems],
        type=ifm.getDType(),
    )
    ofm_mem_var = tensor_expr.TensorVar.make(
        [
            tensor_expr.Location(1, 0, 0),
        ],
        shape=[ofm_elems],
        type=ofm.getDType(),
    )

    # Transfer the full IFM tensor from DDR to L2.
    phase.set_l3_to_l2_transfer(l3_l2[0], ifm.getTensorVar(), ifm_mem_var)
    # And from L2 to L1. Only the first core does useful work, but every core is
    # fed the same input so that none is left unconnected (the L2->L1 channels
    # broadcast over columns).
    for channel in l2_l1:
        phase.set_l2_to_l1_transfer(channel, ifm_mem_var, ifm_mk_var)

    # Transfer the whole OFM from L1 to L2. Every core must have its output port
    # connected, although every core computes the same result. We write all
    # results into the same location in L2s.
    for channel in l1_l2:
        n_cores = len(channel.core_tile_ports)
        phase.set_l1_to_l2_transfer(channel, ofm_mk_var, [ofm_mem_var] * n_cores)
    # And from L2 to DDR.
    phase.set_l2_to_l3_transfer(l2_l3[0], ofm_mem_var, ofm.getTensorVar())

    # Kernel arguments, name, implementation, and RTPs (lp_params).
    # lp_params = [ifm_num_elems, k] in the kernel's exact order.
    phase.set_kernel_arguments([ifm_mk_var, ofm_mk_var])
    phase.set_kernel_function_name("cse_topk_indices_kernel")
    phase.set_kernel_impl(Path("kernel.cpp"))
    phase.set_kernel_params([ifm_elems, opInterface.attributes["k"]])
    phase.set_kernel_nb_calls(1)
