# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Generic-overlay (AieConn) tiling for the split-channel negate op.

    channel 0  <-  ifm[0 : N/2]      -> ifm_l2[0 : N/2]
    channel 1  <-  ifm[N/2 : N]      -> ifm_l2[N/2 : N]
"""

from pathlib import Path

import tensor_expr


def _numel(shape: list[int]) -> int:
    n = 1
    for d in shape:
        n *= d
    return n


def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    stamp = tiling[0][0]
    ROWS = stamp.aie_core_rows
    COLS = stamp.aie_core_cols

    info_refs = list(opInterface)
    num_operands = opInterface.getNumOperands()  # 1 input
    ifm = info_refs[:num_operands][0]
    out = info_refs[num_operands]

    N = _numel(ifm.getShape())
    HALF = N // 2
    Q = N // COLS  # quarter per column
    SEG = Q // ROWS  # per-core segment
    assert Q * COLS == N and SEG * ROWS == Q, "N must be divisible by COLS*ROWS"
    assert HALF * 2 == N, "N must be even to split into two halves"

    dtype = ifm.getDType()

    l3_l2 = stamp.get_l3_to_l2_channels()
    l2_l1 = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    assert len(l3_l2) >= 2, "need two L3->L2 channels to split the input"

    # ---- input: DDR -> ONE shared L2 buffer [N], filled over TWO channels. ----
    # The same external buffer is read twice in this phase: first half on
    # channel 0, second half on channel 1.
    ifm_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(0, 0, 0x0)],
        shape=[N],
        type=dtype,
    )
    src = ifm.getTensorVar().Reshape([N])
    stamp.set_l3_to_l2_transfer(
        l3_l2[0], src.Slice(0, 0, HALF), ifm_l2.Slice(0, 0, HALF)
    )
    stamp.set_l3_to_l2_transfer(
        l3_l2[1], src.Slice(0, HALF, HALF), ifm_l2.Slice(0, HALF, HALF)
    )

    # ---- L2 -> L1: each column reads its quarter, broadcast to its ROWS cores.
    in_mk_var = tensor_expr.TensorVar.make(
        [Q], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    for col, channel in enumerate(l2_l1):
        read_view = ifm_l2.Slice(0, col * Q, Q)  # [Q]
        stamp.set_l2_to_l1_transfer(channel, read_view, [in_mk_var] * ROWS)

    # ---- kernel: negate this core's [SEG] slice into a [SEG] tile. ----
    out_mk_var = tensor_expr.TensorVar.make(
        [SEG], out.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
    )
    stamp.set_kernel_arguments([in_mk_var, out_mk_var])
    stamp.set_kernel_function_name("negate_split_kernel")
    stamp.set_kernel_impl(Path("custom_negate_split.cpp"))
    stamp.set_kernel_params([Q, SEG])
    stamp.set_kernel_nb_calls(1)

    # ---- L1 -> L2 gather: each core -> its own contiguous DDR slot. ----
    ofm_mem_var = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(1, 0, 0x0)],
        shape=[COLS, ROWS, SEG],
        type=out.getDType(),
    )
    ofm_per_core = ofm_mem_var.Reshape([COLS * ROWS, SEG]).TileTo(
        index=0, numTiles=COLS * ROWS
    )
    for col, col_channel in enumerate(l1_l2):
        l1_src: list[tensor_expr.TensorExpr] = []
        l2_dst: list[tensor_expr.TensorExpr] = []
        for row in range(ROWS):
            l1_src += [out_mk_var]
            l2_dst.append(ofm_per_core[col * ROWS + row])
        stamp.set_l1_to_l2_transfer(col_channel, l1_src, l2_dst)

    # ---- L2 -> DDR drain. ----
    total = COLS * ROWS * SEG
    out_dst = out.getTensorVar().Reshape([total])
    stamp.set_l2_to_l3_transfer(l2_l3[0], ofm_mem_var.Reshape([total]), out_dst)
