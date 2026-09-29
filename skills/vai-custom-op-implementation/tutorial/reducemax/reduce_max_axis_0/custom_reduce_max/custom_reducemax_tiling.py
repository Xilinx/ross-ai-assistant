# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr

# Tiling for ReduceMax over axis 0 (outermost).
# The tensor to be reduced over has shape [R, N]; dimension N is parallel, so it
# gets tiled and distributed across the cores. A tile size T is picked such that
# a [R, T] tensor fits in the AIE's L1 memories; if it is too large, we perform
# several temporal iterations of the kernel.
VEC = 16  # bf16 vector granularity the kernel targets; keep T a multiple of it


def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase, single-stamp op: use the first (and only) stamp/phase.
    stamp = tiling[0][0]
    ROWS = stamp.aie_core_rows
    COLS = stamp.aie_core_cols
    n_cores = ROWS * COLS

    ifm, ofm = opInterface  # unary op: one IFM, one OFM

    ifm_shape = ifm.getShape()
    ofm_shape = ofm.getShape()

    # Reduction is over axis 0.
    R = ifm_shape[0]

    # N = number of independent output positions (product of all OFM dims;
    # the reduced axis collapses to 1 in the OFM).
    N = 1
    for d in ofm_shape:
        N *= d

    # Per the definition of this reduction, the IFM must hold exactly R times as
    # many elements as the OFM.
    ifm_total = 1
    for d in ifm_shape:
        ifm_total *= d
    assert (
        ifm_total == R * N
    ), f"ifm_total({ifm_total}) != R({R}) * N({N}); padding likely mismatched"

    assert N % n_cores == 0, f"N({N}) must be divisible by {n_cores}"
    per_core = N // n_cores  # total output positions each core produces

    # Pick a tile size T: a multiple of VEC dividing per_core, as large as fits L1.
    # We need to fitROWS (=4) tiles per L1 buffer, hence the budget_T.

    L1_BANK_ELEMS = 4096
    IFM_BUF_BANKS = 2
    budget_T = (IFM_BUF_BANKS * L1_BANK_ELEMS) // (R * ROWS)
    max_T = min(640, budget_T)  # Takes into account the stack/heap
    T = 0
    cand = (min(per_core, max_T) // VEC) * VEC
    while cand >= VEC:
        if per_core % cand == 0:
            T = cand
            break
        cand -= VEC
    assert T > 0, f"no valid tile size (multiple of {VEC}) divides per_core={per_core}"

    G = per_core // T  # number of L2 rotations
    COL_W = ROWS * T  # positions per column chunk (and L1 stride)
    L2_window = n_cores * T  # positions processed per rotation (16T)
    plane_stride = COL_W  # L1 per-plane stride (ROWS*T)

    print(
        f"[reducemax tiling] R={R} N={N} per_core={per_core} "
        f"T={T} G={G} L2_window={L2_window} plane_stride={plane_stride}"
    )

    ifm_dtype = ifm.getDType()
    ofm_dtype = ofm.getDType()

    # Memtile columns hosting each L2 buffer (must match the Location()s below).
    #
    # Note that a memtile DMA channel reaches its own memtile plus its immediate
    # neighbours.
    IFM_MT = 0
    OFM_MT = 2

    # Channels for each memory-hierarchy hop.
    l3_l2 = stamp.get_l3_to_l2_channels()
    l2_l1 = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    # Helper to pick a channel whose memtile is on the given column.
    def _pick_ch_by_memtile_col(channels, col, fallback_index):
        matches = [c for c in channels if c.mem_tile_port.tile_col == col]
        return matches[0] if matches else channels[fallback_index]

    # ---- L2 buffers (double buffered: two locations each so DMA producer and
    # consumer ping-pong across the G rotations; single-buffered L2 deadlocks the
    # simulator's DMA dependency graph). ----
    # IFM L2 is laid out [COLS, R, ROWS*T] so that TileTo(index=0) hands each
    # column its [R, ROWS*T] chunk (broadcast to that column's ROWS cores).
    ifm_mem = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(IFM_MT, 0, 0x00000),
            tensor_expr.Location(IFM_MT, 0, 0x20000),
        ],
        shape=[COLS, R, COL_W],
        type=ifm_dtype,
    )
    ofm_mem = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(OFM_MT, 0, 0x00000),
            tensor_expr.Location(OFM_MT, 0, 0x10000),
        ],
        shape=[COLS, ROWS, T],
        type=ofm_dtype,
    )

    # Both L2 buffers rotate G times (one rotation processes 16 output tiles,
    # one per core). The rotation count MUST match the kernel-call count below
    # or the simulator's DMA dependency graph deadlocks.
    ifm_mem.setTemporalIterations(G)
    ofm_mem.setTemporalIterations(G)

    # ---- L1 buffers (automatic placement, double buffered) ----
    ifm_mk = tensor_expr.TensorVar.make(
        [R, COL_W], ifm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    ofm_mk = tensor_expr.TensorVar.make(
        [T], ofm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )

    # ---- DDR -> L2 (IFM) ----
    # x is plane-major [R, N] where N = G*COLS*COL_W.
    # Reshape as [R, G, COLS, COL_W], then bring rotation G outermost and the
    # column dim ahead of R so each rotation transfers a [COLS, R, COL_W] block
    # matching ifm_mem.
    ifm_ddr = (
        ifm.getTensorVar().Reshape([R, G, COLS, COL_W]).Transpose([1, 2, 0, 3])
    )  # [G, COLS, R, COL_W]
    stamp.set_l3_to_l2_transfer(
        _pick_ch_by_memtile_col(l3_l2, IFM_MT, 0), ifm_ddr, ifm_mem
    )

    # ---- L2 -> L1 (IFM) ----
    # Each column gets its [R, COL_W] chunk, broadcast to its ROWS cores; each
    # core picks its own [R, T] via the row*T offset inside the kernel.
    ifm_per_col = ifm_mem.TileTo(index=0, numTiles=COLS)  # [R, COL_W] per column
    for col, channel in enumerate(l2_l1):
        stamp.set_l2_to_l1_transfer(channel, ifm_per_col[col], [ifm_mk] * ROWS)

    # ---- L1 -> L2 (OFM) ----
    # Store each core's result [T] tile into its own [T] slot of the [COLS, ROWS, T]
    # L2 buffer.
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

    # ---- L2 -> DDR (OFM) ----
    ofm_ddr = ofm.getTensorVar().Reshape([N]).TileBy(index=0, tileLen=L2_window)
    stamp.set_l2_to_l3_transfer(
        _pick_ch_by_memtile_col(l2_l3, OFM_MT, 0), ofm_mem.Reshape([L2_window]), ofm_ddr
    )

    # ---- kernel ----
    stamp.set_kernel_arguments([ifm_mk, ofm_mk])
    stamp.set_kernel_function_name("myreducemax_kernel")
    stamp.set_kernel_impl(Path("custom_reducemax.cpp"))
    stamp.set_kernel_params([T, R, plane_stride])
    # Sync kernel, ratio 1: one call per L2 rotation per core => G calls total.
    stamp.set_kernel_nb_calls(G)
