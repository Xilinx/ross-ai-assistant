# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Generic-overlay (AieConfig) tiling for the 4x4-distributed AffineGrid2D op.

AffineGrid2D takes theta [N, 2, 3] and a size input [4] and produces a sampling
grid [N, H, W, 2]. This variant distributes the *output* grid across the 4x4
core array: core (col, row) with flat index ``core_idx = col*ROWS + row``
generates the contiguous flat slice ``[core_idx*ks .. (core_idx+1)*ks)`` of the
H*W grid points (each point is two coordinates), where ``ks = H*W / NUM_CORES``.
The kernel (customop_affinegrid2d.cpp) computes its own ``core_start`` from the
tile id, so every core is fed the *full* theta and the *full* size input; only
the output is split. Concatenating the per-core [ks, 2] blocks in ``core_idx``
order therefore reproduces the natural row-major [H, W, 2] grid.

The op runs batch-by-batch (N temporal iterations). The batch axis lives on the
L3 side: TileBy([1]) on the leading dim streams one batch per iteration, every
L2 buffer holds a single batch's worth of data and declares
``setTemporalIterations(BATCH)`` so its DMA re-arms once per batch, and the
kernel is invoked once per batch (``set_kernel_nb_calls(BATCH)``).

A core has one COLUMNS-broadcast input port and one ROWS-broadcast input port,
so the two broadcast inputs use the two distinct families: theta rides the
COLUMNS broadcast (every column receives the full theta, fanned out to its ROWS
cores) and size rides the ROWS broadcast (every row receives the full size,
fanned out to its COLS cores). Every core thus ends up with the complete theta
and size.
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
    NUM_CORES = ROWS * COLS

    # ---- Unpack the op interface: theta (ifm), size (wts), grid (ofm). ----
    ifm, wts, ofm = opInterface

    ifm_shape = ifm.getPaddedShape()  # [N, 2, 3]
    wts_shape = wts.getPaddedShape()  # [4]
    ofm_shape = ofm.getPaddedShape()  # [N, H, W, 2]

    assert (
        len(ifm_shape) == 3 and ifm_shape[1] == 2
    ), f"theta must be [N,2,3]: {ifm_shape}"
    assert len(wts_shape) == 1 and wts_shape[0] == 4, f"size must be [4]: {wts_shape}"
    assert (
        len(ofm_shape) == 4 and ofm_shape[3] == 2
    ), f"grid must be [N,H,W,2]: {ofm_shape}"
    assert ifm_shape[0] == ofm_shape[0], "batch mismatch theta vs grid"

    BATCH = ofm_shape[0]
    THETA_H = ifm_shape[1]
    THETA_W = ifm_shape[2]
    WTS_LEN = wts_shape[0]
    H_OUT = ofm_shape[1]
    W_OUT = ofm_shape[2]
    DIM_OUT = ofm_shape[3]

    THETA_LEN = THETA_H * THETA_W  # elements of theta per batch (6)
    total_grid_points = H_OUT * W_OUT
    assert total_grid_points % NUM_CORES == 0, "H*W must be divisible by NUM_CORES"
    ks = total_grid_points // NUM_CORES  # grid points per core (== kernel_size)
    SEG = ks * DIM_OUT  # output elements per core per batch

    ifm_dtype = ifm.getDType()
    wts_dtype = wts.getDType()
    ofm_dtype = ofm.getDType()

    # ---- Channels for each memory-hierarchy hop. ----
    l3_l2 = stamp.get_l3_to_l2_channels()
    l2_l1_cols = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l2_l1_rows = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    # ---- L2 buffers: theta, size and the gathered output each in a distinct
    # memtile column so their DMA channels reach the full buffer range. Each
    # buffer holds one batch and streams BATCH temporal iterations. ----
    theta_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(0, 0, 0x0),
            tensor_expr.Location(0, 0, 0x40),
        ],
        shape=[THETA_LEN],
        type=ifm_dtype,
    )
    theta_l2.setTemporalIterations(BATCH)

    size_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(3, 0, 0x0),
            tensor_expr.Location(3, 0, 0x40),
        ],
        shape=[WTS_LEN],
        type=wts_dtype,
    )
    # The size input has no batch dimension; it is broadcast every batch, so it
    # is re-read once per iteration together with theta.
    size_l2.setTemporalIterations(BATCH)

    # Double-buffered (two locations, one per PIPO slot) so its DMA re-arms per
    # batch; placed in memtile column 1, the column whose l2_l3[0] channel can
    # reach the full output-buffer range (mirrors the working mul 4x4 sibling).
    ofm_l2_bytes = COLS * ROWS * SEG * 2  # bf16 = 2 bytes/element
    OFM_L2_ALIGN = ((ofm_l2_bytes + 0x3F) // 0x40) * 0x40
    ofm_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(1, 0, 0x0),
            tensor_expr.Location(1, 0, OFM_L2_ALIGN),
        ],
        shape=[COLS, ROWS, SEG],
        type=ofm_dtype,
    )
    ofm_l2.setTemporalIterations(BATCH)

    # ---- L3 -> L2: theta streams one [THETA_LEN] batch per iteration; size is
    # re-sent whole each iteration. ----
    theta_src = ifm.getTensorVar().Reshape([BATCH, THETA_LEN]).TileBy([1, THETA_LEN])
    stamp.set_l3_to_l2_transfer(l3_l2[0], theta_src, theta_l2)

    # size fills from the L3->L2 channel co-located with its memtile column (3),
    # mirroring the sibling 4x4 ops; l3_l2[1] cannot reach it. It has no batch
    # axis, so Repeat(BATCH) supplies one copy per temporal iteration.
    size_src = wts.getTensorVar().Reshape([WTS_LEN]).Repeat(BATCH)
    stamp.set_l3_to_l2_transfer(l3_l2[2], size_src, size_l2)

    # ---- L2 -> L1: theta over COLUMNS, size over ROWS. Every core needs the
    # full theta and full size (the kernel self-offsets the output), so each
    # broadcast reads the whole buffer. ----
    theta_l1 = tensor_expr.TensorVar.make(
        [THETA_LEN], ifm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    for col, channel in enumerate(l2_l1_cols):
        stamp.set_l2_to_l1_transfer(channel, theta_l2, [theta_l1] * ROWS)

    size_l1 = tensor_expr.TensorVar.make(
        [WTS_LEN], wts_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    for row, channel in enumerate(l2_l1_rows):
        stamp.set_l2_to_l1_transfer(channel, size_l2, [size_l1] * COLS)

    # ---- kernel: core (col,row) generates its [SEG] flat slice of the grid. ----
    ofm_l1 = tensor_expr.TensorVar.make(
        [SEG], ofm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    stamp.set_kernel_arguments([theta_l1, size_l1, ofm_l1])
    stamp.set_kernel_function_name("affinegrid2d_kernel")
    stamp.set_kernel_impl(Path("customop_affinegrid2d.cpp"))
    # Preserve the kernel's exact lp_params: [align_corners, H_out, W_out].
    stamp.set_kernel_params(
        [opInterface.attributes.get("align_corners", 1), H_OUT, W_OUT]
    )
    stamp.set_kernel_nb_calls(BATCH)

    # ---- L1 -> L2 gather: each core -> its own contiguous slot [SEG]. ----
    ofm_per_core = ofm_l2.Reshape([NUM_CORES, SEG]).TileTo(index=0, numTiles=NUM_CORES)
    for col, col_channel in enumerate(l1_l2):
        l1_src: list[tensor_expr.TensorExpr] = []
        l2_dst: list[tensor_expr.TensorExpr] = []
        for row in range(ROWS):
            l1_src += [ofm_l1]
            l2_dst.append(ofm_per_core[col * ROWS + row])
        stamp.set_l1_to_l2_transfer(col_channel, l1_src, l2_dst)

    # ---- L2 -> DDR drain: the [COLS,ROWS,SEG] gather is exactly the natural
    # row-major [H*W*2] grid of this batch (core_idx = col*ROWS+row writes the
    # contiguous flat slice core_idx*SEG..). Stream one batch per iteration. ----
    batch_len = NUM_CORES * SEG
    ofm_dst = ofm.getTensorVar().Reshape([BATCH, batch_len]).TileBy([1, batch_len])
    stamp.set_l2_to_l3_transfer(l2_l3[0], ofm_l2.Reshape([batch_len]), ofm_dst)
