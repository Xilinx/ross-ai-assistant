# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr

# Tiling for inner-most-axis ReduceMax over axis 2 on the 4x4 overlay.
#
# The tensor to be reduced over has shape [N, K]; dimension N is parallel, gets
# tiled and distributed across the cores.
# We pick T = 392 * 4 (rows) * 4 (columns) elements per kernel invocation; if
# the tensor is larger, several temporal iterations of the kernel (see the
# computation of G, the number of temporal iterations).


def _prod(shape):
    p = 1
    for d in shape:
        p *= d
    return p


def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase, single-stamp op: use the first (and only) stamp/phase.
    stamp = tiling[0][0]
    ROWS = stamp.aie_core_rows
    COLS = stamp.aie_core_cols
    N_CORES = ROWS * COLS

    ifm, ofm = opInterface  # unary op: one IFM, one OFM

    assert ifm.getRank() >= 2, "IFM must be 2D"
    assert "axes" in opInterface.attributes, "axes attribute is required"
    selectedAxis = int(
        opInterface.attributes["axes"]
    )  # pyright: ignore[reportArgumentType]
    assert (
        selectedAxis == ifm.getRank() - 1
    ), "This custom op supports reduction on the innermost axis only"

    # Size of the reduction dimension.
    K = ifm.getShape()[selectedAxis]

    ifm_size = _prod(ifm.getShape())
    ofm_size = _prod(ofm.getShape())

    # Number of reductions per core per kernel invocation.
    #   * T must divide N // N_CORES so the work distributes evenly and
    #     the number of output tiles NT = N // T is a multiple of N_CORES.
    #   * VEC_GROUPS (=8) in the kernel divides T so there is no scalar tail.
    #   * L1 per core (column-broadcast IFM, double-buffered) ~= 4*T*K*ROWS bytes;
    #     For K=4, T=392 -> ~50 KB, comfortably within L1.
    T = 392

    assert (
        ifm_size == ofm_size * K
    ), f"IFM size ({ifm_size}) must equal OFM size ({ofm_size}) * K ({K})"

    num_reductions = ofm_size
    assert num_reductions % (N_CORES * T) == 0, (
        f"num_reductions ({num_reductions}) must be a multiple of "
        f"N_CORES*T ({N_CORES} * {T} = {N_CORES * T})"
    )

    per_core = num_reductions // N_CORES
    # Number of temporal iterations of the kernel.
    G = per_core // T

    ifm_l2_tile = N_CORES * T * K
    ofm_l2_tile = N_CORES * T

    ifm_dtype = ifm.getDType()
    ofm_dtype = ofm.getDType()

    print(
        f"[reducemax axis2 tiling] N={num_reductions} K={K} T={T} "
        f"per_core={per_core} G={G} ifm_l2_tile={ifm_l2_tile} "
        f"ofm_l2_tile={ofm_l2_tile}"
    )

    # Channels for each memory-hierarchy hop.
    l3_l2 = stamp.get_l3_to_l2_channels()
    l2_l1 = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    # ---- L2 (memtile) buffers, double-buffered ----
    # IFM L2 is [COLS, ROWS, T*K]: TileTo(index=0) hands each column its
    # [ROWS, T*K] chunk (broadcast to that column's ROWS cores).
    ifm_mem = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(0, 0, 0x00000),
            tensor_expr.Location(0, 0, 0x20000),
        ],
        shape=[COLS, ROWS, T * K],
        type=ifm_dtype,
    )
    ofm_mem = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(1, 0, 0x00000),
            tensor_expr.Location(1, 0, 0x10000),
        ],
        shape=[COLS, ROWS, T],
        type=ofm_dtype,
    )

    # Both L2 buffers rotate G times (one rotation processes 16 output tiles,
    # one per core). The rotation count MUST match the kernel-call count below
    # or the simulator's DMA dependency graph deadlocks.
    ifm_mem.setTemporalIterations(G)
    ofm_mem.setTemporalIterations(G)

    # ---- L1 (core) buffers, double-buffered, automatic placement ----
    # IFM holds the full column-broadcast slice (all ROWS rows); the
    # kernel selects its row via core_row.
    ifm_mk = tensor_expr.TensorVar.make(
        [ROWS, T * K],
        ifm_dtype,
        tensor_expr.BufferingStrategy.DoubleBuffered,
    )
    ofm_mk = tensor_expr.TensorVar.make(
        [T],
        ofm_dtype,
        tensor_expr.BufferingStrategy.DoubleBuffered,
    )

    # ---- DDR -> L2 IFM ----
    # x is flat [ifm_size]; view as [G, COLS, ROWS, T*K] (contiguous slabs of
    # 16 core-tiles each) so each rotation transfers a [COLS, ROWS, T*K] block
    # matching ifm_mem.
    ifm_ddr = ifm.getTensorVar().Reshape([G, COLS, ROWS, T * K])
    stamp.set_l3_to_l2_transfer(l3_l2[0], ifm_ddr, ifm_mem)

    # ---- L2 -> L1 IFM: column-broadcast (each column gets its [ROWS, T*K]) ----
    ifm_per_col = ifm_mem.TileTo(index=0, numTiles=COLS)  # [ROWS, T*K] per column
    for col, channel in enumerate(l2_l1):
        stamp.set_l2_to_l1_transfer(channel, ifm_per_col[col], [ifm_mk] * ROWS)

    # ---- L1 -> L2 OFM: write each core's [T] tile into its (col,row) slot ----
    ofm_per_core = ofm_mem.Reshape([COLS * ROWS, T]).TileTo(
        index=0, numTiles=COLS * ROWS
    )
    for col, channel in enumerate(l1_l2):
        l1_src: list[tensor_expr.TensorExpr] = []
        l2_dst: list[tensor_expr.TensorExpr] = []
        for row in range(ROWS):
            l1_src += [ofm_mk]
            l2_dst.append(ofm_per_core[col * ROWS + row])
        stamp.set_l1_to_l2_transfer(channel, l1_src, l2_dst)

    # ---- L2 -> DDR OFM ----
    ofm_ddr = (
        ofm.getTensorVar().Reshape([ofm_size]).TileBy(index=0, tileLen=ofm_l2_tile)
    )
    stamp.set_l2_to_l3_transfer(l2_l3[0], ofm_mem.Reshape([ofm_l2_tile]), ofm_ddr)

    # ---- kernel ----
    # Arg order matches reducemax_adf_wrapper(ifm, ofm, layer_params).
    stamp.set_kernel_arguments([ifm_mk, ofm_mk])
    stamp.set_kernel_function_name("reducemax_adf_wrapper")
    stamp.set_kernel_impl(Path("custom_reducemax_kernel.cpp"))
    stamp.set_kernel_params([T, K])
    # Sync kernel: one call per L2 rotation per core => G calls total.
    stamp.set_kernel_nb_calls(G)
