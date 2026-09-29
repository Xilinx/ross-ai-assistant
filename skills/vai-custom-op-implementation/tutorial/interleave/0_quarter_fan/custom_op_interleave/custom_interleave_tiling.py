# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Generic-overlay (AieConn) tiling for the interleave op.

Four inputs A, B, C, D of VARIABLE dimensionality but equal element count N are
each staged as ONE row into a single shared L2 buffer `abcd_l2` of shape [4, N]
(row 0 = A, row 1 = B, row 2 = C, row 3 = D). Because every input is reshaped to
[N] before being sliced into its row, the differing operand ranks
([N], [2,N/2], [4,N/4], [8,N/8]) all connect into the same L2 destination.

The 4x4 overlay's four column-broadcast channels then each read a DIFFERENT
quarter of that shared buffer:

    column c  <-  abcd_l2[:, c*Q : (c+1)*Q]   (shape [4, Q])

so a single L2->L1 transfer per column hands each column one quarter of every
operand, interleaving the four inputs. The 4 rows of a column split that quarter
into ROWS segments of SEG elements (chosen inside the kernel via the core row
id).

Each core copies its [4, SEG] piece to a contiguous [4*SEG] output tile; the 16
tiles are gathered back to DDR as a [COLS, ROWS, 4, SEG] tensor.

This is the AieConfig path (tiling annotated tensor_expr.AieConfig), mandatory
because the legacy ml_adf path caps at 2 operands and this op has 4 inputs.
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
    num_operands = opInterface.getNumOperands()  # 4 inputs
    inputs = info_refs[:num_operands]
    out = info_refs[num_operands]

    NVARS = num_operands
    N = _numel(inputs[0].getShape())
    for info in inputs:
        assert _numel(info.getShape()) == N, "all inputs must have equal numel"
    Q = N // COLS  # quarter per column
    SEG = Q // ROWS  # per-core segment
    assert Q * COLS == N and SEG * ROWS == Q, "N must be divisible by COLS*ROWS"
    tile_size = NVARS * SEG  # per-core output tile

    dtype = inputs[0].getDType()

    l3_l2 = stamp.get_l3_to_l2_channels()
    l2_l1 = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    assert len(l3_l2) >= NVARS, "need one L3->L2 channel per input"

    # ---- inputs: DDR -> one shared L2 buffer [NVARS, N] (row i = operand i). --
    # Each operand is reshaped to [N] regardless of its DDR rank, so all four
    # variable-shape inputs connect into the single shared L2 destination.
    # Stage on mem tile 1. A mem tile is reachable from its own column and from
    # its east/west neighbours, so tile 1 can be filled by the L3->L2 channels on
    # columns 0 and 2 -- all four of l3_l2[0..3] (cols [0,0,2,2]). Tile 0 could
    # only be reached from columns 0 and 1, so the two column-2 channels were out
    # of range and the build failed with
    #   aiecompiler 77-5767 ... shared_buffer_0.in[2] cannot access full range
    # Tile 1 is also where the column-broadcast L2->L1 channels read from, so the
    # fan-out to the cores is a same-tile read.
    abcd_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(1, 0, 0x0)],
        shape=[NVARS, N],
        type=dtype,
    )
    for i, info in enumerate(inputs):
        src = info.getTensorVar().Reshape([N])
        # Slice(0, i, 1) elides the length-1 outer dim -> contiguous [N] row i.
        dst = abcd_l2.Slice(0, i, 1)
        stamp.set_l3_to_l2_transfer(l3_l2[i], src, dst)

    # ---- L2 -> L1: each column reads the c-th quarter of ALL four vars. ----
    in_mk_var = tensor_expr.TensorVar.make(
        [NVARS, Q], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    for col, channel in enumerate(l2_l1):
        read_view = abcd_l2.Slice(1, col * Q, Q)  # [NVARS, Q]
        stamp.set_l2_to_l1_transfer(channel, read_view, [in_mk_var] * ROWS)

    # ---- kernel: copy this core's [NVARS, SEG] slice into a [NVARS*SEG] tile. -
    out_mk_var = tensor_expr.TensorVar.make(
        [tile_size], out.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
    )
    stamp.set_kernel_arguments([in_mk_var, out_mk_var])
    stamp.set_kernel_function_name("interleave_kernel")
    stamp.set_kernel_impl(Path("custom_interleave.cpp"))
    stamp.set_kernel_params([Q, SEG])
    stamp.set_kernel_nb_calls(1)

    # ---- L1 -> L2 gather: each core -> its own contiguous DDR slot. ----
    ofm_mem_var = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(1, 0, 0x0)],
        shape=[COLS, ROWS, tile_size],
        type=out.getDType(),
    )
    ofm_per_core = ofm_mem_var.Reshape([COLS * ROWS, tile_size]).TileTo(
        index=0, numTiles=COLS * ROWS
    )
    for col, col_channel in enumerate(l1_l2):
        l1_src: list[tensor_expr.TensorExpr] = []
        l2_dst: list[tensor_expr.TensorExpr] = []
        for row in range(ROWS):
            l1_src += [out_mk_var]
            l2_dst.append(ofm_per_core[col * ROWS + row])
        stamp.set_l1_to_l2_transfer(col_channel, l1_src, l2_dst)

    # ---- L2 -> DDR drain (emitted LAST so the output external buffer keeps the
    # operand-then-result index). ----
    total = COLS * ROWS * tile_size
    out_dst = out.getTensorVar().Reshape([total])
    stamp.set_l2_to_l3_transfer(l2_l3[0], ofm_mem_var.Reshape([total]), out_dst)
