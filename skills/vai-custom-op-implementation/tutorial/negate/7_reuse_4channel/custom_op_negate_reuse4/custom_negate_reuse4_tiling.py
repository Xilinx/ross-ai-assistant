# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Example of using the same input over 4 different DDR channels.

    mem-tile 0  <-  A[0   : 256]  +  A[256 : 512]   (its two L3->L2 channels)
    mem-tile 2  <-  A[512 : 768]  +  A[768 :1024]   (its two L3->L2 channels)

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
    HALF = N // 2  # elements staged per mem tile
    Q_COL = N // COLS  # quarter handed to each core-col
    SEG = Q_COL // ROWS  # per-core segment
    assert HALF * 2 == N, "N must be even to split across two mem tiles"
    assert Q_COL * COLS == N and SEG * ROWS == Q_COL, "N must divide COLS*ROWS"

    dtype = ifm.getDType()

    l3_l2 = stamp.get_l3_to_l2_channels()
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    # Discover which mem tiles to stage each buffer on, rather than hard-coding
    # tile indices. The origin (mem tile) of the first COLUMNS-broadcast L2->L1
    # channel gives buffer A's tile; the first ROWS-broadcast one gives B's.
    cols_l2_l1 = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    assert cols_l2_l1, "overlay must expose a COLUMNS-broadcast L2->L1 channel"
    MEM_TILE = cols_l2_l1[0].mem_tile_port.tile_col

    # Select the L3->L2 channels dynamically by the mem tile they target,
    # rather than hard-coding channel indices. Each buffer needs two channels.
    def _channels_on_tile(
        tile_col: int, exact: bool
    ) -> list[tensor_expr.L3_L2_Channel]:
        return [
            ch
            for ch in l3_l2
            if ch.mem_tile_port.tile_col == tile_col
            or (not exact and ch.mem_tile_port.tile_col in [tile_col + 1, tile_col - 1])
        ]

    l3_l2_channels = _channels_on_tile(MEM_TILE, exact=False)
    assert (
        len(l3_l2_channels) >= 4
    ), f"overlay must expose >=2 L3->L2 channels targeting mem tile {MEM_TILE}"
    assert (
        len(cols_l2_l1) >= COLS
    ), "need one column-broadcast L2->L1 channel per column"

    src = ifm.getTensorVar().Reshape([N])

    # ---- input: DDR -> TWO shared L2 buffers, each filled over two channels. ----
    ifm_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(MEM_TILE, 0, 0x0)], shape=[N], type=dtype
    )
    Q = HALF // 2  # 256; one channel's worth
    stamp.set_l3_to_l2_transfer(
        l3_l2_channels[0], src.Slice(0, 0, Q), ifm_l2.Slice(0, 0, Q)
    )
    stamp.set_l3_to_l2_transfer(
        l3_l2_channels[1], src.Slice(0, Q, Q), ifm_l2.Slice(0, Q, Q)
    )
    stamp.set_l3_to_l2_transfer(
        l3_l2_channels[2], src.Slice(0, HALF, Q), ifm_l2.Slice(0, HALF, Q)
    )
    stamp.set_l3_to_l2_transfer(
        l3_l2_channels[3], src.Slice(0, HALF + Q, Q), ifm_l2.Slice(0, HALF + Q, Q)
    )

    # ---- L2 -> L1: row-broadcast each row's quarter to its COLS cores. ----
    # rows 0,1 come from mem col0 (buffer A); rows 2,3 from mem col3 (buffer B).
    in_mk_var = tensor_expr.TensorVar.make(
        [Q_COL], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    for col in range(COLS):
        buf = ifm_l2
        offset = col * Q_COL
        read_view = buf.Slice(0, offset, Q_COL)
        stamp.set_l2_to_l1_transfer(cols_l2_l1[col], read_view, [in_mk_var] * COLS)

    # ---- kernel: negate this core's [SEG] slice (picked by column id). ----
    out_mk_var = tensor_expr.TensorVar.make(
        [SEG], out.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
    )
    stamp.set_kernel_arguments([in_mk_var, out_mk_var])
    stamp.set_kernel_function_name("negate_reuse4_kernel")
    stamp.set_kernel_impl(Path("custom_negate_reuse4.cpp"))
    stamp.set_kernel_params([Q_COL, SEG])
    stamp.set_kernel_nb_calls(1)

    # ---- L1 -> L2 gather (column-wise) then L2 -> DDR drain. ----
    # Column-major gather: core (row, col) computes A block (col*ROWS + row)
    # (its column reads A[col*Q_COL:...], the kernel picks segment row*SEG), so
    # its output must land at that same linear position for out == -A in order.
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
            l1_src.append(out_mk_var)
            l2_dst.append(ofm_per_core[col * ROWS + row])
        stamp.set_l1_to_l2_transfer(col_channel, l1_src, l2_dst)

    total = ROWS * COLS * SEG
    out_dst = out.getTensorVar().Reshape([total])
    stamp.set_l2_to_l3_transfer(l2_l3[0], ofm_mem_var.Reshape([total]), out_dst)
