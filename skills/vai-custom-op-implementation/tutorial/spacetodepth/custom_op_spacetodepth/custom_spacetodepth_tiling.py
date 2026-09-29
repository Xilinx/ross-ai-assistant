# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Generic-overlay (AieConfig) tiling for the SpaceToDepth op.

4x4 distributed implementation, HC/8W8 vectorized output.

Demonstrates the use of:
* Automatic padding (auto_pad in the kernel YAML and `ifm.getPaddedShape()`),
* Vectorized data layout in the kernel YAML: the output tensor is 4D, but we get
  it 5D with the vectorized dimension innermost,
* Temporal iterations (`set_kernel_nb_calls()` and `setTemporalIterations()` on
  L2 buffers).
"""

import math
from pathlib import Path

import tensor_expr


def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    stamp = tiling[0][0]
    ROWS = stamp.aie_core_rows
    COLS = stamp.aie_core_cols

    ifm, ofm = opInterface

    assert (
        ifm.getRank() == 4
    ), "IFM must be 4D (N, C, H, W), IFM has shape {ifm.getShape()}"
    assert (
        ofm.getRank() == 5
    ), f"OFM must be 5D (H / 2, C / 2, W / 2, N, 8), OFM has shape {ofm.getShape()}"

    # =========================================================================
    # CONSTANTS
    # =========================================================================
    IFM_N = ifm.getPaddedShape()[0]
    assert IFM_N == 1, "IFM must have batch size 1"
    IFM_C = ifm.getPaddedShape()[1]
    IFM_H = ifm.getPaddedShape()[2]
    IFM_W = ifm.getPaddedShape()[3]
    VEC_SIZE = 8
    expected_ofm_shape = [
        IFM_H // 2,
        math.ceil((4 * IFM_C) / VEC_SIZE),
        IFM_W // 2,
        IFM_N,
        VEC_SIZE,
    ]
    assert (
        ofm.getPaddedShape() == expected_ofm_shape
    ), f"OFM has shape {ofm.getPaddedShape()}, expected {expected_ofm_shape}"

    # HC/8W8 output: [H, C_VEC, W, VEC]
    OFM_H = IFM_H // 2
    OFM_W = IFM_W // 2
    OFM_C_VEC = 2

    # L2 tiling by H dimension
    L2_IFM_H = 32  # 32 input rows per L2 tile
    assert (
        IFM_H % L2_IFM_H == 0
    ), f"IFM height {IFM_H} must be divisible by L2_IFM_H {L2_IFM_H}"
    L2_OFM_H = L2_IFM_H // 2  # 16 output rows per L2 tile
    assert (
        OFM_H % L2_OFM_H == 0
    ), f"OFM height {OFM_H} must be divisible by L2_OFM_H {L2_OFM_H}"

    # Per column: 8 rows shared among ROWS cores
    COL_IFM_H = L2_IFM_H // COLS  # 8

    # Temporal iterations: the IFM/OFM are streamed through L2 in NUM_ITERS
    # chunks of L2_IFM_H input rows each (320/32 = 10).
    NUM_ITERS = IFM_H // L2_IFM_H  # 10

    ifm_dtype = ifm.getDType()
    ofm_dtype = ofm.getDType()

    l3_l2 = stamp.get_l3_to_l2_channels()
    # IFM L2 is put in memtile column 0.
    l3_l2_to_memtile_0 = [ch for ch in l3_l2 if ch.mem_tile_port.tile_col == 0]
    l2_l1 = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()
    # OFM L2 is put in memtile column 2.
    l2_l3_on_memtile_2 = [ch for ch in l2_l3 if ch.mem_tile_port.tile_col == 2]

    # =========================================================================
    # L2 MEMORY BUFFERS
    # =========================================================================
    # IFM L2: [IFM_C, L2_IFM_H, IFM_W].
    # With shape [IFM_C, L2_IFM_H, IFM_W] = [3, 32, 320], L2 size = 30 KB.
    # Double-buffered.
    IFM_L2_BYTES = IFM_C * L2_IFM_H * IFM_W  # int8 bytes
    IFM_PONG_OFFSET = ((IFM_L2_BYTES + 0x7FFF) // 0x8000) * 0x8000
    ifm_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(0, 0, 0x0),
            tensor_expr.Location(0, 0, IFM_PONG_OFFSET),
        ],
        shape=[IFM_C, L2_IFM_H, IFM_W],
        type=ifm_dtype,
    )
    # Number of rotations = number of kernel calls.
    ifm_l2.setTemporalIterations(NUM_ITERS)

    # OFM L2 (int8): [L2_OFM_H, OFM_C_VEC, OFM_W, VEC_SIZE].
    # With shape [L2_OFM_H, OFM_C_VEC, OFM_W, VEC_SIZE] = [16, 2, 160, 8],
    # L2 size = 40 KB. Double-buffered (ping/pong); the pong buffer sits at an
    # 0x8000-aligned offset past ping.
    OFM_L2_BYTES = L2_OFM_H * OFM_C_VEC * OFM_W * VEC_SIZE  # int8 bytes
    OFM_PONG_OFFSET = ((OFM_L2_BYTES + 0x7FFF) // 0x8000) * 0x8000
    ofm_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(2, 0, 0x0),
            tensor_expr.Location(2, 0, OFM_PONG_OFFSET),
        ],
        shape=[L2_OFM_H, OFM_C_VEC, OFM_W, VEC_SIZE],
        type=ofm_dtype,
    )
    # Number of rotations = number of kernel calls.
    ofm_l2.setTemporalIterations(NUM_ITERS)

    # =========================================================================
    # L1 CORE BUFFERS
    # =========================================================================
    # IFM L1: [IFM_C, COL_IFM_H, IFM_W] = [3, 8, 320]
    ifm_mk_var = tensor_expr.TensorVar.make(
        [IFM_C, COL_IFM_H, IFM_W],
        ifm_dtype,
        tensor_expr.BufferingStrategy.DoubleBuffered,
    )

    # OFM L1: [OFM_C_VEC, OFM_W, VEC_SIZE] = [2, 160, 8]
    ofm_mk_var = tensor_expr.TensorVar.make(
        [OFM_C_VEC, OFM_W, VEC_SIZE],
        ofm_dtype,
        tensor_expr.BufferingStrategy.DoubleBuffered,
    )

    # =========================================================================
    # DDR -> L2 (IFM): reshape [3,320,320], tile by H into L2_IFM_H tiles.
    # =========================================================================
    ifm_src = (
        ifm.getTensorVar()
        .Reshape([IFM_C, IFM_H, IFM_W])
        .TileBy(index=1, tileLen=L2_IFM_H)
    )
    stamp.set_l3_to_l2_transfer(l3_l2_to_memtile_0[0], ifm_src, ifm_l2)

    # =========================================================================
    # L2 -> L1 (IFM): distribute the 32 L2 rows across the 4 columns (8 rows
    # each) and broadcast each column's [3, 8, 320] to its ROWS cores.
    # =========================================================================
    # Each column reads a contiguous COL_IFM_H-row slice along H from the L2
    # tile [3, 32, 320] -> [3, 8, 320], and broadcasts it to its ROWS cores.
    for col, channel in enumerate(l2_l1):
        read_view = ifm_l2.Slice(1, col * COL_IFM_H, COL_IFM_H)  # [3, 8, 320]
        stamp.set_l2_to_l1_transfer(channel, read_view, [ifm_mk_var] * ROWS)

    # =========================================================================
    # KERNEL
    # =========================================================================
    stamp.set_kernel_arguments([ifm_mk_var, ofm_mk_var])
    stamp.set_kernel_function_name("spacetodepth_kernel")
    stamp.set_kernel_impl(Path("custom_spacetodepth.cpp"))
    stamp.set_kernel_params([COL_IFM_H, IFM_W])
    # One kernel call per temporal L2 tile rotation (ratio 1 per buffer).
    stamp.set_kernel_nb_calls(NUM_ITERS)

    # =========================================================================
    # L1 -> L2 (OFM): each core contributes [2, 160, 8]; the 16 cores fill the
    # L2 OFM tile [16, 2, 160, 8]. Expose the grid dims as [COLS, ROWS, ...].
    # =========================================================================
    ofm_grid = ofm_l2.Reshape(
        [COLS, ROWS, OFM_C_VEC, OFM_W, VEC_SIZE]
    ).SpatialDistribute2D(dimA=0, dimB=1, tileACount=COLS, tileBCount=ROWS)
    for col, col_channel in enumerate(l1_l2):
        l1_src: list[tensor_expr.TensorExpr] = []
        l2_dst: list[tensor_expr.TensorExpr] = []
        for row in range(ROWS):
            l1_src += [ofm_mk_var]
            l2_dst.append(ofm_grid[col][row])
        stamp.set_l1_to_l2_transfer(col_channel, l1_src, l2_dst)

    # =========================================================================
    # L2 -> DDR (OFM): place each L2 OFM half-tile into the HC/8W8 DDR tensor.
    # The DDR tensor is [OFM_H, OFM_C_VEC, OFM_W, VEC_SIZE]. TileBy H into
    # L2_OFM_H-row temporal tiles.
    # =========================================================================
    ofm_dst = (
        ofm.getTensorVar()
        .Reshape([OFM_H, OFM_C_VEC, OFM_W, VEC_SIZE])
        .TileBy(index=0, tileLen=L2_OFM_H)
    )
    # The OFM L2 buffer lives on memtile column 2. Use a L2->L3 channel
    # from that memtile.
    stamp.set_l2_to_l3_transfer(l2_l3_on_memtile_2[0], ofm_l2, ofm_dst)
