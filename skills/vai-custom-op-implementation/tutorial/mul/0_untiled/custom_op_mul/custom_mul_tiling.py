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
# It uses the "single core" view on the AIE, i.e., it sets up the transfers as
# if only one core is available. Additionally, it assumes that all data fits in
# L1 (i.e., you don't really tile the input), and that no padding is required to
# be able to transfer the data (DMA requires 32-bit transfers, this tiling
# assumes that the input shape naturally fulfills that).
# Starting with this very simple form of tiling can allow you to validate that
# a single-core kernel implementation is valid, without already introducing the
# additional complexities of a distributed execution.
# This can also be a starting point to actually do tiling, by modifying the
# L2/L1 sizes of the buffers as well as the transfers.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Unpack the op interface to get IFM, WTS and OFM tensors.
    ifm, wts, ofm = opInterface

    assert ifm.getShape() == wts.getShape(), "Operands shapes mismatch"
    assert ifm.getShape() == ofm.getShape(), "Operand and result shapes mismatch"

    # Flatten down the shape.
    size = 1
    for dim in ifm.getShape():
        size *= dim

    # Single-phase, single-stamp op: pick the sole phase.
    phase = tiling[0][0]

    # Channels for each memory-hierarchy hop. A core has one row-input and one
    # column-input L2->L1 port, so the two operands take separate broadcast
    # axes: ifm over columns, wts over rows.
    l3_l2 = phase.get_l3_to_l2_channels()
    l2_l1_cols = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l2_l1_rows = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)
    l1_l2 = phase.get_l1_to_l2_channels()
    l2_l3 = phase.get_l2_to_l3_channels()

    # L2 vars (we assume the tensor is small enough to fully fit L2/L1). Each
    # buffer lives in its own memtile column: ifm in col0 (broadcast over AIE
    # columns), wts in col3 (broadcast over AIE rows), ofm drains from col1.
    ifm_mem_var = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(0, 0, 0x0)],
        shape=[size],
        type=ifm.getDType(),
    )
    wts_mem_var = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(3, 0, 0x0)],
        shape=[size],
        type=wts.getDType(),
    )
    ofm_mem_var = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(1, 0, 0x0)],
        shape=[size],
        type=ofm.getDType(),
    )

    # L1 vars
    ifm_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(0x0), tensor_expr.Location(0x1000)],
        [size],
        type=ifm.getDType(),
    )
    wts_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(0x2000), tensor_expr.Location(0x3000)],
        [size],
        type=wts.getDType(),
    )
    ofm_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(0x4000), tensor_expr.Location(0x5000)],
        [size],
        type=ofm.getDType(),
    )

    # transfers: L3 -> L2
    phase.set_l3_to_l2_transfer(l3_l2[0], ifm.getTensorVar(), ifm_mem_var)
    phase.set_l3_to_l2_transfer(l3_l2[2], wts.getTensorVar(), wts_mem_var)

    # L2 -> L1: ifm broadcast over columns, wts broadcast over rows. Only the
    # first core does useful work, but every core is fed the same inputs so that
    # none is left unconnected.
    for channel in l2_l1_cols:
        phase.set_l2_to_l1_transfer(channel, ifm_mem_var, ifm_mk_var)
    for channel in l2_l1_rows:
        phase.set_l2_to_l1_transfer(channel, wts_mem_var, wts_mk_var)

    # L1 -> L2 drain. Every core must have its output port connected, so we drain
    # all channels; every core computes the same result and drains into the
    # single OFM L2 buffer (the redundant results aren't used).
    for channel in l1_l2:
        n_cores = len(channel.core_tile_ports)
        phase.set_l1_to_l2_transfer(channel, ofm_mk_var, [ofm_mem_var] * n_cores)

    # L2 -> L3 drain.
    phase.set_l2_to_l3_transfer(l2_l3[0], ofm_mem_var, ofm.getTensorVar())

    # kernel
    phase.set_kernel_arguments([ifm_mk_var, wts_mk_var, ofm_mk_var])
    phase.set_kernel_function_name("mul_kernel")
    phase.set_kernel_impl(Path("custom_mul.cpp"))
    phase.set_kernel_params([size])
    phase.set_kernel_nb_calls(1)
