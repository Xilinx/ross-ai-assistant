# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr

# WEIGHT-STREAMING GEMM-with-bias on the 4x4 AIE overlay, built on the 8x8x8
# `aie::mmul` (bf16 x bf16 -> fp32).
#
#   C[N, M] = W[N, K] . A[K, M] + bias[N]      (bias broadcast over M)
#
#     N           output rows      (the W rows, split across overlay ROWS)
#     K           the reduction    (contracted 8 at a time by `aie::mmul`)
#     M           the output cols  (split across overlay COLS)
#     W[N, K]     the weights
#     A[K, M]     the activations
#     bias[N]     per-output-row bias, folded into W (see below)
#
# This is the matmul / linear / fully-connected archetype. A pointwise (1x1)
# convolution maps onto it exactly -- N = output channels, K = input channels,
# M = spatial positions.
#
# Overlay mapping:
#   * OUTPUT ROWS N  -> split across the 4 overlay ROWS
#   * OUTPUT_COLS M  -> split across the 4 overlay COLS
#   One core owns N_per_row = N/4 output rows and M_per_col = M/4 output cols.
#
# Two independent tile sizes:
#   * L1_M_TILE_SIZE      -- the L1 / KERNEL tile: output cols computed per kernel call.
#   * L2_M_TILE_SIZE   -- the L2 STAGING width: output cols pulled from DDR per L2 fill.
#               L2_M_TILE_SIZE = L1_M_TEMPORAL_DIM * L1_M_TILE_SIZE, giving a bigger (more efficient) L3->L2 burst.
#
# Weight streaming: the full weight tensor is read once to L2 once and kept there.
# Each kernel call pulls only L1_N_TILE_SIZE output rows' worth of weights (L1_N_TILES = L1_N_TILE_SIZE/8
# row-tiles) into a small double-buffered L1 buffer.
#
# Because the same activation columns feed every output row, the IFM L1 tile is
# help (async) across the NB_N_SLICES = N_per_row/L1_N_TILE_SIZE weight blocks that reduce over it,
# while the WTS port rotates a fresh block in each call. This is expressed by
# giving the IFM L2->L1 transfer `ratio=NB_N_SLICES`. The kernel (unchanged) acquires
# the async IFM once and releases it after NB_N_SLICES (= lp_params[3]) calls.
#
# Bias folding: W_aug[N, K+8], column K = bias, rest 0. The kernel reduces over
# K/8 real tiles then does one extra mmul of the bias tile against a synthesised
# constant-1.0 operand, depositing bias[row] in every free lane. We pass the REAL
# K (not K_pad); the kernel derives the bias tile as the (K/BLK_K)-th weight tile.
# aie::mmul<8,8,8> tile edges. Same value, but named per axis so each buffer
# shape says which dimension its 8 refers to.
BLK_M = 8  # free / output-col edge
BLK_N = 8  # output-row edge
BLK_K = 8  # reduction edge


def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    ifm, wts, ofm = opInterface  # A, W_aug, C

    phase = tiling[0][0]
    OVERLAY_ROWS = phase.aie_core_rows
    OVERLAY_COLS = phase.aie_core_cols

    # Shapes (use padded shapes)
    K, M = ifm.getPaddedShape()  # A     : [K, M]
    N, K_pad = wts.getPaddedShape()  # W_aug : [N, K_pad]  (K_pad = K + BLK_K)
    N_o, M_o = ofm.getPaddedShape()  # C     : [N, M]

    assert K_pad == K + BLK_K, (
        f"W_aug reduction dim {K_pad} != K + {BLK_K} ({K + BLK_K}); this variant "
        "expects bias folded in as one extra reduction tile"
    )
    assert N_o == N and M_o == M, f"C shape [{N_o},{M_o}] != [{N},{M}]"

    assert N % OVERLAY_ROWS == 0, f"N ({N}) must be a multiple of {OVERLAY_ROWS}"
    assert M % OVERLAY_COLS == 0, f"M ({M}) must be a multiple of {OVERLAY_COLS}"
    N_per_row = N // OVERLAY_ROWS  # output rows owned by one overlay row
    M_per_col = M // OVERLAY_COLS  # output cols owned by one overlay col

    # Output-col tile (L1/kernel)
    L1_M_TILE_SIZE = 32  # output cols per KERNEL CALL / L1 tile
    assert (
        M_per_col % L1_M_TILE_SIZE == 0
    ), f"M_per_col ({M_per_col}) not multiple of L1_M_TILE_SIZE ({L1_M_TILE_SIZE})"
    NB_M_SLICES = (
        M_per_col // L1_M_TILE_SIZE
    )  # L1_M_TILE_SIZE-wide slices of M per core

    # Output-col tile (L2 staging)
    L1_M_TEMPORAL_DIM = 4  # L1 tiles staged per L2 fill
    L2_M_TILE_SIZE = (
        L1_M_TILE_SIZE * L1_M_TEMPORAL_DIM
    )  # wider L2 staging width => bigger DDR burst
    assert (
        M_per_col % L2_M_TILE_SIZE == 0
    ), f"M_per_col ({M_per_col}) not multiple of L2_M_TILE_SIZE ({L2_M_TILE_SIZE})"
    L2_M_FILLS = M_per_col // L2_M_TILE_SIZE  # L3->L2 fills along M
    assert L2_M_FILLS * L1_M_TEMPORAL_DIM == NB_M_SLICES

    # Output-row block (weight streaming granularity)
    L1_N_TILE_SIZE = 16  # output rows produced per call (2 row-tiles)
    assert (
        N_per_row % L1_N_TILE_SIZE == 0
    ), f"N_per_row ({N_per_row}) not multiple of L1_N_TILE_SIZE ({L1_N_TILE_SIZE})"
    NB_N_SLICES = (
        N_per_row // L1_N_TILE_SIZE
    )  # L1_N_TILE_SIZE-tall slices of N per core
    # one kernel call per (M-slice, N-slice) pair

    assert (
        K % BLK_K == 0 and L1_M_TILE_SIZE % BLK_M == 0 and L1_N_TILE_SIZE % BLK_N == 0
    ), f"K ({K}), L1_M_TILE_SIZE ({L1_M_TILE_SIZE}), L1_N_TILE_SIZE ({L1_N_TILE_SIZE}) must be multiples of the mmul edge"
    K_TILES = K // BLK_K  # 8-tiles along K, the reduction (from A)
    K_TILES_W = K_pad // BLK_K  # ditto for the weights, incl. the bias tile
    L1_M_TILES = (
        L1_M_TILE_SIZE // BLK_M
    )  # 8-tiles along L1_M_TILE_SIZE, the output cols of one L1 tile
    L1_N_TILES = L1_N_TILE_SIZE // BLK_N  # output-row 8-tiles streamed per call
    N_TILES_ALL = N // BLK_N  # output-row 8-tiles over all of N

    # Channels for each memory-hierarchy hop
    l3_l2 = phase.get_l3_to_l2_channels()
    l2_l1_cols = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l2_l1_rows = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)
    l1_l2 = phase.get_l1_to_l2_channels()
    l2_l3 = phase.get_l2_to_l3_channels()

    # L2 (memtile) buffers
    # IFM double-buffered ACROSS MT0/MT1: one wide [COLS, K, L2_M_TILE_SIZE] buffer per
    # memtile (ping on MT0, pong on MT1). This is what lets each be wide enough
    # for the L2_M_TILE_SIZE burst while staying double-buffered.
    ifm_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(0, 0, 0x00000),  # ping -> Memtile 0
            tensor_expr.Location(1, 0, 0x00000),  # pong -> Memtile 1
        ],
        shape=[K, OVERLAY_COLS * L2_M_TILE_SIZE],
        type=ifm.getDType(),
    )
    ifm_l2.setTemporalIterations(L2_M_FILLS)  # L2_M_FILLS L3->L2 fills

    # Full W_aug resident on MT3, single-buffered: transferred once, then every
    # L2->L1 weight read is served from here.
    wts_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(3, 0, 0x00000)],
        shape=[N, K_pad],
        type=wts.getDType(),
    )

    # OFM L2 holds one [L1_N_TILE_SIZE, L1_M_TILE_SIZE] block per core call. Double-buffered on MT2.
    ofm_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(2, 0, 0x00000),
            tensor_expr.Location(2, 0, 0x10000),
        ],
        shape=[OVERLAY_ROWS, L1_N_TILE_SIZE, OVERLAY_COLS, L1_M_TILE_SIZE],
        type=ofm.getDType(),
    )
    ofm_l2.setTemporalIterations(
        NB_M_SLICES * NB_N_SLICES
    )  # one [L1_N_TILE_SIZE, L1_M_TILE_SIZE] block per call

    # L1 (per-core) buffers
    # IFM held (async) across the number of calls (NB_N_SLICES) -> single-buffered.
    # WTS rotates every call -> double-buffered. OFM drains one block/call -> double-buffered.
    ifm_l1 = tensor_expr.TensorVar.make(
        [K_TILES, L1_M_TILES, BLK_K, BLK_M],
        ifm.getDType(),
        tensor_expr.BufferingStrategy.SingleBuffered,
    )
    wts_l1 = tensor_expr.TensorVar.make(
        [L1_N_TILES, K_TILES_W, BLK_N, BLK_K],
        wts.getDType(),
        tensor_expr.BufferingStrategy.DoubleBuffered,
    )
    ofm_l1 = tensor_expr.TensorVar.make(
        [L1_N_TILE_SIZE, L1_M_TILE_SIZE],
        ofm.getDType(),
        tensor_expr.BufferingStrategy.DoubleBuffered,
    )

    # L3 -> L2 transfers: A[K, M] -> [M_TEMPORAL_TILE_DIM, K, L2_M_TILE_SIZE]
    ifm_ddr = ifm.getTensorVar().TileBy([K, OVERLAY_COLS * L2_M_TILE_SIZE])
    phase.set_l3_to_l2_transfer(l3_l2[0], ifm_ddr, ifm_l2)

    # Full weights transferred once and kept resident in L2.
    phase.set_l3_to_l2_transfer(l3_l2[2], wts.getTensorVar(), wts_l2)

    # L2 -> L1 transfers
    # L2 IFM Shape: [K, OVERLAY_COLS * L2_M_TILE_SIZE]
    # - L1_M_TEMPORAL_DIM dictates the number of transfers from L2 to L1 which differs from the ammount of
    #   transfers from L3 to L2. It is not a temporal dim from the L2 read point of view because the data is already there.
    # - Aply a Transpose(...) to move BLK_K right before BLK_M in preparation for aie::mmul.
    # - Spatially distribute the OVERLAY_COLS dimension as to distribute each of the 4 blocks between the 4 columns.
    # Final shape before SpatialDistribute: [L1_M_TEMPORAL_DIM, COLS, K_TILES, L1_M_TILES, 8, 8]
    ifm_per_col = (
        # ifm_l2 = [K, COLS*L2_M_TILE_SIZE]; factor M into the axes we need here
        ifm_l2.Reshape(
            [K_TILES, BLK_K, L1_M_TEMPORAL_DIM, OVERLAY_COLS, L1_M_TILES, BLK_M]
        )
        .Transpose(
            [2, 3, 0, 4, 1, 5]
        )  # [L1_M_TEMPORAL_DIM, OVERLAY_COLS, K_TILES, L1_M_TILES, 8, 8]
        .SpatialDistribute(dimension=1, tileCount=OVERLAY_COLS)
    )
    for col, channel in enumerate(l2_l1_cols):
        phase.set_l2_to_l1_transfer(
            channel, ifm_per_col[col], [ifm_l1] * OVERLAY_ROWS, NB_N_SLICES
        )

    # WTS: Convert N and K_pad into 8x8 block tiles -> [N // BLK_N, K_pad // BLK_K, 8, 8],
    # Split N // BLK_N per overlay row with SpatialDistribute.
    wts_rows = (
        wts_l2.Reshape([N_TILES_ALL, BLK_N, K_TILES_W, BLK_K])
        .Transpose([0, 2, 1, 3])
        .SpatialDistribute(dimension=0, tileCount=OVERLAY_ROWS)
    )
    # Because A is async and repeats NB_M_SLICES times we need to send the W sync input NB_M_SLICES times as well
    wts_read = [
        # .Repeat(NB_M_SLICES) the same weight blocks are re-sent 8 times, once per new M-slice.
        r.TileBy([L1_N_TILES, K_TILES_W, BLK_N, BLK_K]).Repeat(NB_M_SLICES)
        for r in wts_rows
    ]
    for row, channel in enumerate(l2_l1_rows):
        phase.set_l2_to_l1_transfer(channel, wts_read[row], [wts_l1] * OVERLAY_COLS)

    # Expected data layout by the kernel:
    # ifm_l1 = [K_TILES, M_tiles, BLK_K, BLK_M]
    # wts_l1 = [N_tiles, K_TILES, BLK_K, BLK_M]
    phase.set_kernel_arguments([ifm_l1, wts_l1, ofm_l1])
    phase.set_kernel_function_name("gemm_bias_kernel")
    phase.set_kernel_impl(Path("custom_gemm_bias.cpp"))
    phase.set_kernel_params([L1_M_TILE_SIZE, K, L1_N_TILE_SIZE, NB_N_SLICES])
    phase.set_kernel_nb_calls(NB_M_SLICES * NB_N_SLICES)

    # L1 -> L2 gather
    # Each core writes one [L1_N_TILE_SIZE, L1_M_TILE_SIZE] block per call into its (col, row) slot.
    ofm_spatial = ofm_l2.SpatialDistribute2D(
        dimA=2, dimB=0, tileACount=OVERLAY_COLS, tileBCount=OVERLAY_ROWS
    )
    for col, channel in enumerate(l1_l2):
        l1_src: list[tensor_expr.TensorExpr] = []
        l2_dst: list[tensor_expr.TensorExpr] = []
        for row in range(OVERLAY_ROWS):
            l1_src.append(ofm_l1)
            l2_dst.append(ofm_spatial[col][row])
        phase.set_l1_to_l2_transfer(channel, l1_src, l2_dst)

    # C[N, M] and ofm_l2 shape is [OVERLAY_ROWS, L1_N_TILE_SIZE, OVERLAY_COLS, L1_M_TILE_SIZE],
    # - Because COLS splits M and ROWS splits N data is continuous accross columns.
    # - The Transpose(...) moves both temporal dimensions ([NB_M_SLICES, NB_N_SLICES]) to the outer dims.
    ofm_ddr = (
        ofm.getTensorVar()
        .Reshape(
            [
                OVERLAY_ROWS,
                NB_N_SLICES,
                L1_N_TILE_SIZE,
                NB_M_SLICES,
                OVERLAY_COLS,
                L1_M_TILE_SIZE,
            ]
        )
        .Transpose(
            [3, 1, 0, 2, 4, 5]
        )  # [NB_M_SLICES, NB_N_SLICES, ROWS, L1_N_TILE_SIZE, COLS, L1_M_TILE_SIZE]
    )
    phase.set_l2_to_l3_transfer(l2_l3[2], ofm_l2, ofm_ddr)
