# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


# New-API (AieConfig) tiling for the SCALAR gridsample2d op.
#
# WHY THIS IS NOT "1x1" ANYMORE
# -----------------------------
# The old Tiling_1x1 wrapper described a single AIE core. AieConfig has no such
# concept: it always describes the full core array, and every core's input and
# output ports must be connected. So this port keeps the scalar kernel and its
# single-core PROGRAMMING MODEL -- one core's worth of work per kernel call, no
# spatial split -- but REPLICATES that work across all cores: each core receives
# the same image tile and the same grid tile, and computes the same result.
#
# Only one core's output is meaningful; the rest are redundant copies draining
# into the same L2 slab. That is deliberate. This example exists to show the
# simplest possible scalar kernel, not to distribute work -- the distributed
# variants are 2_tiled_1x4 and 3_tiled_4x4. Compare with
# affinegrid2d/1_vectorized_1x1, which uses the same broadcast idiom.
#
# The kernel is core-position agnostic (no core_row/core_col offsets), so
# feeding every core identical tiles is what makes the replication correct.
#
# --- Data flow ---
# GridSample takes TWO inputs (an image X and a sampling grid) and produces a
# sampled output Y. Sampling is random-access, so the whole image for one
# (batch, channel) must be resident in L1; only the GRID and the OUTPUT are
# tiled along H.
#
# Each L2 buffer holds ONE tile and declares setTemporalIterations(num_tiles),
# so its DMA is re-armed once per kernel call. The L3 source and the L2
# destination are tiled over the SAME iteration count (identity per tile), which
# sequences the ping/pong DMA locks. Iteration order is tile-major
# (t, batch, channel) so all three L3 views expose the tile axis outermost.
#
# H_tile is auto detected here, not read from the model attribute.
#
# This kernel takes an 11-entry lp_params (it is the 3D-capable scalar variant):
# [mode, align_corners, kr_ifm, kr_wts, kr_ofm, H, W, D, HW_grid, HW_grid_sub,
#  D_grid] with D = D_grid = 1 for the 2D case. HW_grid = lp[8] * lp[9].
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase, single-stamp op: use the first (and only) phase.
    phase = tiling[0][0]

    # Unpack the op interface: image (ifm), grid (wts), output (ofm).
    ifm, wts, ofm = opInterface

    ifm_shape = ifm.getPaddedShape()
    wts_shape = wts.getPaddedShape()
    ofm_shape = ofm.getPaddedShape()

    assert len(ifm_shape) == 4, f"IFM must be 4D [N,C,H,W], got {ifm_shape}"
    assert len(ofm_shape) == 4, f"OFM must be 4D [N,C,H,W], got {ofm_shape}"
    assert wts_shape[-1] == 2, f"Grid last dim must be 2, got {wts_shape[-1]}"
    assert ifm_shape[0] == ofm_shape[0], "batch mismatch"
    assert ifm_shape[1] == ofm_shape[1], "channel mismatch"

    BATCH = ifm_shape[0]
    CHANNELS = ifm_shape[1]
    H_IN = ifm_shape[2]
    W_IN = ifm_shape[3]
    H_OUT = ofm_shape[2]
    W_OUT = ofm_shape[3]

    mode_attr = opInterface.attributes.get("mode", 1)
    if mode_attr in ("linear", "bilinear"):
        mode = 1
    elif mode_attr == "nearest":
        mode = 0
    else:
        mode = 0
    align_corners = opInterface.attributes.get("align_corners", 1)

    # =========================================================================
    # AUTO-DETECT H_tile / W_tile
    # =========================================================================
    # The whole input image stays resident (random-access sampling); the grid
    # and output are tiled along H. Every L1 buffer is DoubleBuffered, so size
    # the tile so that all three buffers fit at 2x their tile size.
    L1_AVAILABLE = 0xA000  # 40960 bytes of data memory
    ELEM_SIZE = 2  # bfloat16
    ALIGN = 64
    NB = 2  # double buffering factor

    def align_up(x, a):
        return ((x + a - 1) // a) * a

    ifm_l1_bytes = NB * align_up(H_IN * W_IN * ELEM_SIZE, ALIGN)
    assert ifm_l1_bytes < L1_AVAILABLE, "Input image too large for L1"
    remaining = L1_AVAILABLE - ifm_l1_bytes

    def tile_fits(ht, wt):
        h_grid = H_OUT // ht
        w_grid = W_OUT // wt
        wts_bytes = NB * align_up(h_grid * w_grid * 2 * ELEM_SIZE, ALIGN)
        ofm_bytes = NB * align_up(h_grid * w_grid * ELEM_SIZE, ALIGN)
        return wts_bytes + ofm_bytes <= remaining

    H_tile = W_tile = None
    for wt in range(1, W_OUT + 1):
        if W_OUT % wt != 0:
            continue
        for ht in range(1, H_OUT + 1):
            if H_OUT % ht != 0:
                continue
            if tile_fits(ht, wt):
                H_tile, W_tile = ht, wt
                break
        if H_tile is not None:
            break

    assert H_tile is not None and W_tile is not None, "No valid H_tile/W_tile"
    assert H_OUT % H_tile == 0 and W_OUT % W_tile == 0
    assert W_tile == 1, "This tiling only splits along H (W_tile must be 1)"

    h_grid = H_OUT // H_tile  # grid rows per tile
    w_grid = W_OUT  # W is not tiled
    print(f"H_tile={H_tile}, W_tile={W_tile}, h_grid={h_grid}, w_grid={w_grid}")

    # =========================================================================
    # ITERATION COUNTS
    # =========================================================================
    # One kernel call processes one grid tile (h_grid * w_grid samples) of one
    # (batch, channel) image. Total calls = BATCH * CHANNELS * H_tile.
    NUM_BC = BATCH * CHANNELS
    num_tiles = NUM_BC * H_tile
    print(f"num_tiles (kernel calls): {num_tiles}")

    # The kernel declares all three IO buffers as adf::bpc_async_0d. An async
    # buffer is selected in the AieConfig API by giving its L2<->L1 transfer a
    # `ratio` (kernelratio): the buffer is re-armed once every `ratio` kernel
    # calls. Every operand changes every call here, so ratio == 1 for all three.
    KR_IFM = 1
    KR_WTS = 1
    KR_OFM = 1

    ifm_dtype = ifm.getDType()
    wts_dtype = wts.getDType()
    ofm_dtype = ofm.getDType()

    # =========================================================================
    # CHANNELS
    # =========================================================================
    l3_l2 = phase.get_l3_to_l2_channels()
    l2_l1_cols = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l2_l1_rows = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)
    l1_l2 = phase.get_l1_to_l2_channels()
    l2_l3 = phase.get_l2_to_l3_channels()

    # =========================================================================
    # L3 READ/WRITE VIEWS
    # =========================================================================
    # All three tensors are streamed over the SAME num_tiles iterations with the
    # SAME leading-dim order (h_tile, batch, channel). The leading dims beyond
    # the per-tile shape ARE the temporal iteration space, so no merging Reshape
    # is needed.
    #
    # Image: [B, C, H_IN, W_IN] -> [H_tile, B, C, H_IN, W_IN]; each of the
    # H_tile grid tiles reuses the full image, so Repeat over the h_tile axis.
    ifm_read = ifm.getTensorVar().Repeat(H_tile)

    # Grid: [B, H_OUT, W_OUT, 2], shared across CHANNELS. Repeat over channels,
    # move batch outermost, then split H_OUT into H_tile contiguous bands:
    # [H_tile, B, C, h_grid, W_OUT, 2].
    wts_read = (
        wts.getTensorVar()
        .Repeat(CHANNELS)  # [C, B, H_OUT, W_OUT, 2]
        .Transpose([1, 0, 2, 3, 4])  # [B, C, H_OUT, W_OUT, 2]
        .TileBy(index=2, tileLen=h_grid)  # [H_tile, B, C, h_grid, W_OUT, 2]
    )

    # Output: [B, C, H_OUT, W_OUT] -> split H_OUT into H_tile bands:
    # [H_tile, B, C, h_grid, W_OUT].
    ofm_write = ofm.getTensorVar().TileBy(index=2, tileLen=h_grid)

    # =========================================================================
    # L2 BUFFERS (one tile each, re-armed once per kernel call)
    # =========================================================================
    ifm_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(0, 0, 0x0)],
        shape=[H_IN, W_IN],
        type=ifm_dtype,
    )
    ifm_l2.setTemporalIterations(num_tiles)

    wts_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(3, 0, 0x0)],
        shape=[h_grid, W_OUT, 2],
        type=wts_dtype,
    )
    wts_l2.setTemporalIterations(num_tiles)

    ofm_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(1, 0, 0x0)],
        shape=[h_grid, W_OUT],
        type=ofm_dtype,
    )
    ofm_l2.setTemporalIterations(num_tiles)

    # =========================================================================
    # L1 BUFFERS (async -> DoubleBuffered; the framework auto-places the
    # ping/pong slots and overlaps each tile's transfer with the previous
    # compute)
    # =========================================================================
    ifm_l1 = tensor_expr.TensorVar.make(
        [H_IN, W_IN], ifm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    wts_l1 = tensor_expr.TensorVar.make(
        [h_grid, W_OUT, 2], wts_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    ofm_l1 = tensor_expr.TensorVar.make(
        [h_grid, W_OUT], ofm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )

    # =========================================================================
    # TRANSFERS
    # =========================================================================
    # L3 -> L2 (identity per tile: the L3 view is tiled to num_tiles, the L2
    # slab holds one tile).
    phase.set_l3_to_l2_transfer(l3_l2[0], ifm_read, ifm_l2)
    phase.set_l3_to_l2_transfer(l3_l2[2], wts_read, wts_l2)

    # L2 -> L1: the image broadcasts over the COLUMNS family and the grid over
    # the ROWS family (the two distinct broadcast port families a core has), so
    # every core gets the SAME image tile and the SAME grid tile and computes
    # the same result. The `ratio` (kernelratio) arg is what selects the
    # kernel's async (bpc_async_0d) buffers in the new API.
    for channel in l2_l1_cols:
        phase.set_l2_to_l1_transfer(channel, ifm_l2, ifm_l1, KR_IFM)
    for channel in l2_l1_rows:
        phase.set_l2_to_l1_transfer(channel, wts_l2, wts_l1, KR_WTS)

    # Kernel: (image, grid, output) + lp_params. lp_params order matches the
    # kernel's gridsample2d_lpstruct_t (11 entries; D = D_grid = 1 for 2D).
    # HW_grid = h_grid * w_grid samples per call.
    lp_params = [
        mode,
        align_corners,
        1,  # kernelratio_ifm
        1,  # kernelratio_wts
        1,  # kernelratio_ofm
        H_IN,
        W_IN,
        1,  # D (unused for 2D)
        h_grid,
        w_grid,
        1,  # D_grid (unused for 2D)
    ]
    print(f"LP PARAMS: {lp_params}")
    phase.set_kernel_arguments([ifm_l1, wts_l1, ofm_l1])
    phase.set_kernel_function_name("gridsample2d_kernel")
    phase.set_kernel_impl(Path("customop_gridsample2d.cpp"))
    phase.set_kernel_params(lp_params)
    phase.set_kernel_nb_calls(num_tiles)

    # L1 -> L2: every core's output port must be connected, so drain all
    # channels. Each core computed the SAME result, so they all drain into the
    # single OFM L2 slab; the redundant copies are unused.
    for channel in l1_l2:
        n_cores = len(channel.core_tile_ports)
        phase.set_l1_to_l2_transfer(
            channel, [ofm_l1] * n_cores, [ofm_l2] * n_cores, KR_OFM
        )

    # L2 -> L3: drain each output tile back to DDR (identity per tile).
    phase.set_l2_to_l3_transfer(l2_l3[0], ofm_l2, ofm_write)
