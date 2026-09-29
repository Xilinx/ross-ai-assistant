# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""AieConfig (new-API) tiling for the tiled/distributed GridSample2D op.

This is the migration of the old Tiling_4x4 script to the new AieConfig API.

GridSample2D has TWO inputs and one output:
  * IFM  X    -- the input image        [BATCH, CHANNELS, H_IN, W_IN]
  * WTS  grid -- the sampling grid       [BATCH, H_OUT, W_OUT, 2]  (shared over channels)
  * OFM  Y    -- the sampled output      [BATCH, CHANNELS, H_OUT, W_OUT]

Sampling is random-access into the image, so the FULL image X[b,c] must be
resident on the core that samples it (it cannot be split). The grid and the
output, on the other hand, are split spatially: each core samples its own band
of output rows.

Distribution over the 4x4 overlay (an outer product, as in mul/2_distributed):
  * one BATCH per rotation (n_rot = BATCH rotations, kernel called once per core
    per rotation);
  * COLUMN c  <-  channel c: the image X[b,c] is broadcast down the ROWS of
    column c (a core has one column-input port, fed the IFM). Column c always
    processes channel c; because the batch changes every rotation, the image the
    column holds changes every call (kernelratio_ifm = 1).
  * ROW r     <-  H-band r: grid[b] (shared across channels) is cut into ROWS
    equal row-bands of H_OUT/ROWS rows; row-band r is broadcast across the COLS
    of row r (a core has one row-input port, fed the WTS/grid).
  * core (c, r) samples image X[b,c] with grid band r and writes output band r of
    channel c: Y[b, c, r*H_band : (r+1)*H_band, :].

Every core therefore does distinct, useful work: CHANNELS x ROWS bands per batch.
The kernel is unchanged; it processes HW_grid_sub = H_band * W_OUT contiguous
grid points from its own grid buffer, sampling the full image buffer, with no
per-core pointer offset (the tiling delivers each core its own slice directly).

L2 residency -- BATCH is TEMPORAL, not resident:
  A memtile column holds 512 KB x DEVICE_MEMTILE_ROWS (1024 KB on aie2ps).
  Staging a whole [BATCH, ...] tensor in L2 does not fit: at BATCH=16 the OFM
  alone needs 1250 KB against that 1024 KB column, which the compiler accepted
  silently and which then hung the board (CTRL_HANG / ERT_CMD_STATE_TIMEOUT).

  So every L2 slab holds ONE BATCH SLICE -- the working set of a single batch --
  and is refilled per batch. The BATCH axis lives on the L3 side as a TileBy,
  and each slab declares setTemporalIterations(BATCH) so its DMA is re-armed
  once per batch. num_buffers stays at 2 (ping/pong), so the next batch's DMA
  overlaps the current batch's compute and the DMA locks sequence the turnover.
  This restores the old Tiling_4x4 L2 sizing, which fits and runs.
"""

from pathlib import Path

import tensor_expr


def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    stamp = tiling[0][0]
    ROWS = stamp.aie_core_rows
    COLS = stamp.aie_core_cols

    # ---- unpack + validate ----
    ifm, wts, ofm = opInterface
    ifm_shape = ifm.getPaddedShape()
    wts_shape = wts.getPaddedShape()
    ofm_shape = ofm.getPaddedShape()

    assert len(ifm_shape) == 4, f"IFM must be 4D [N,C,H,W], got {ifm_shape}"
    assert len(ofm_shape) == 4, f"OFM must be 4D [N,C,H,W], got {ofm_shape}"
    assert wts_shape[-1] == 2, f"Grid last dim must be 2, got {wts_shape[-1]}"

    BATCH = ifm_shape[0]
    CHANNELS = ifm_shape[1]
    H_IN = ifm_shape[2]
    W_IN = ifm_shape[3]
    H_OUT = ofm_shape[2]
    W_OUT = ofm_shape[3]

    # This distribution maps channels onto COLUMNS and H-bands onto ROWS.
    assert CHANNELS == COLS, f"CHANNELS ({CHANNELS}) must equal COLS ({COLS})"
    assert H_OUT % ROWS == 0, f"H_OUT ({H_OUT}) must be divisible by ROWS ({ROWS})"

    # Mode / align_corners come from the op attributes (kernel expects ints).
    mode_attr = opInterface.attributes.get("mode", 1)
    if mode_attr in ("linear", "bilinear"):
        mode = 1
    elif mode_attr == "nearest":
        mode = 0
    else:
        mode = 0
    align_corners = opInterface.attributes.get("align_corners", 1)

    H_band = H_OUT // ROWS  # output rows sampled by one core
    n_rot = BATCH  # one batch processed per rotation

    # The kernel declares all three IO buffers as adf::bpc_async_0d (manual
    # acquire/release gated by the kernelratio lp_params). An async buffer in the
    # AieConfig API is selected by giving its L2<->L1 transfer a `ratio`: the
    # buffer is re-armed once every `ratio` kernel calls. Here every operand
    # changes every call, so the ratio (== kernelratio) is 1 for all three.
    KR_IFM = 1
    KR_WTS = 1
    KR_OFM = 1

    ifm_dtype = ifm.getDType()
    wts_dtype = wts.getDType()
    ofm_dtype = ofm.getDType()

    # ---- channels for each memory-hierarchy hop ----
    # Memtile columns hosting each L2 buffer. A channel reaches its own memtile
    # and its east/west neighbours; hosting each buffer on the channel's own
    # column is the safe default (see _pick below).
    IFM_MT = 0
    OFM_MT = 2
    WTS_MT = 3

    l3_l2 = stamp.get_l3_to_l2_channels()
    l2_l1_cols = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l2_l1_rows = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    # Select the DMA channel that can reach a given memtile column. A channel
    # reaches its own memtile plus its immediate neighbours (see the channel
    # tables in docs/30_custom_ops/tiling.md), not just its own column -- the
    # WTS fallback below relies on that, using L3->L2 channel 2 to reach MT3.
    # Picking the buffer's own column is simply the safe default. Prefer a
    # tile_col match; fall back to a positional index if the run-path channel
    # list does not expose distinct tile_col values (the standalone compile.py
    # and the ORT-recompile paths differ here).
    def _pick(channels, col, fallback_index):
        matches = [c for c in channels if c.mem_tile_port.tile_col == col]
        if matches:
            return matches[0]
        return channels[fallback_index]

    def l3_l2_on(col: int):
        # conv1d (identical MT layout) uses index 0 for MT0 and index 2 for MT3.
        fallback = {IFM_MT: 0, WTS_MT: 2}[col]
        return _pick(l3_l2, col, fallback)

    def l2_l3_on(col: int):
        return _pick(l2_l3, col, 0)

    # =====================================================================
    # L2 (memtile) buffers -- each operand on its own memtile column, the safe
    # placement for the channels that serve it.
    #   IFM   on MT0  (column-broadcast channels start at tile 0/1/2)
    #   OFM   on MT2  (L1->L2 gather / L2->L3 drain)
    #   WTS   on MT3  (row-broadcast channels start at tile 3)
    # Each slab holds ONE BATCH SLICE, not the whole tensor, and is refilled
    # once per batch (setTemporalIterations(BATCH)). Two Locations per slab =
    # ping/pong, so batch b+1 streams in while batch b computes.
    #
    #   IFM  [CHANNELS, H_IN,  W_IN ]  ->  4*64*80  =  20480 el =  40960 B
    #   WTS  [H_OUT,    W_OUT, 2    ]  ->  100*100*2 = 20000 el =  40000 B
    #   OFM  [CHANNELS, H_OUT, W_OUT]  ->  4*100*100 = 40000 el =  80000 B
    # vs a 1024 KB memtile column -- comfortable even doubled for ping/pong.
    # =====================================================================
    ELEM_SIZE = 2  # bfloat16
    ALIGN = 64

    def align_up(x, a):
        return ((x + a - 1) // a) * a

    ifm_l2_shape = [CHANNELS, H_IN, W_IN]
    wts_l2_shape = [H_OUT, W_OUT, 2]
    ofm_l2_shape = [CHANNELS, H_OUT, W_OUT]

    ifm_l2_elems = CHANNELS * H_IN * W_IN
    wts_l2_elems = H_OUT * W_OUT * 2
    ofm_l2_elems = CHANNELS * H_OUT * W_OUT

    def _l2_slab(memtile, shape, elems, dtype):
        """A double-buffered L2 slab holding one batch slice."""
        slab_bytes = align_up(elems * ELEM_SIZE, ALIGN)
        var = tensor_expr.TensorVar.make(
            locations=[
                tensor_expr.Location(memtile, 0, 0x0),
                tensor_expr.Location(memtile, 0, slab_bytes),
            ],
            shape=shape,
            type=dtype,
        )
        # The BATCH axis is temporal: this slab's DMA is re-armed once per
        # batch. num_buffers only says how many slots it ping-pongs between,
        # not how often they turn over.
        var.setTemporalIterations(BATCH)
        return var

    ifm_l2 = _l2_slab(IFM_MT, ifm_l2_shape, ifm_l2_elems, ifm_dtype)
    wts_l2 = _l2_slab(WTS_MT, wts_l2_shape, wts_l2_elems, wts_dtype)
    ofm_l2 = _l2_slab(OFM_MT, ofm_l2_shape, ofm_l2_elems, ofm_dtype)

    # =====================================================================
    # L1 (per-core) buffers -- double-buffered so the next rotation's DMA can
    # overlap the current rotation's compute.
    # =====================================================================
    ifm_l1 = tensor_expr.TensorVar.make(
        [H_IN, W_IN], ifm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    wts_l1 = tensor_expr.TensorVar.make(
        [H_band, W_OUT, 2], wts_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    ofm_l1 = tensor_expr.TensorVar.make(
        [H_band, W_OUT], ofm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )

    # =====================================================================
    # L3 -> L2: stream one batch slice at a time. TileBy puts the BATCH axis on
    # the L3 side as the iteration axis, so the memtile only ever holds one
    # slice; the slab's setTemporalIterations(BATCH) re-arms the DMA per batch.
    # =====================================================================
    stamp.set_l3_to_l2_transfer(
        l3_l2_on(IFM_MT),
        ifm.getTensorVar()
        .Reshape([BATCH * CHANNELS * H_IN * W_IN])
        .TileBy([ifm_l2_elems]),
        ifm_l2.Reshape([ifm_l2_elems]),
    )
    # WTS fills from the L3->L2 channel co-located with its memtile column (3).
    stamp.set_l3_to_l2_transfer(
        l3_l2_on(WTS_MT),
        wts.getTensorVar().Reshape([BATCH * H_OUT * W_OUT * 2]).TileBy([wts_l2_elems]),
        wts_l2.Reshape([wts_l2_elems]),
    )

    # =====================================================================
    # L2 -> L1 IFM: the slab now holds one batch slice [CHANNELS, H_IN, W_IN],
    # so column c is just channel c -- a plain slice, no Transpose needed. The
    # image is broadcast down the ROWS of its column and, because a fresh slice
    # arrives every batch, a fresh image arrives every call (ratio = 1).
    # =====================================================================
    ifm_per_col = ifm_l2.SpatialDistribute(
        dimension=0, tileCount=COLS
    )  # COLS views, each [H_IN, W_IN]
    for col, channel in enumerate(l2_l1_cols):
        stamp.set_l2_to_l1_transfer(channel, ifm_per_col[col], [ifm_l1] * ROWS, KR_IFM)

    # =====================================================================
    # L2 -> L1 WTS: the slab holds one batch's grid [H_OUT, W_OUT, 2], cut into
    # ROWS H-bands of H_band rows. Row r reads band r and broadcasts it across
    # the COLS of its row (the grid is channel-shared). BATCH is carried by the
    # slab's own refill, so no batch axis appears here.
    #   wts_l2                    [H_OUT, W_OUT, 2]
    #   .Reshape band axis        [ROWS, H_band, W_OUT, 2]
    #   .SpatialDistribute rows   -> ROWS views, each [H_band, W_OUT, 2]
    # =====================================================================
    wts_per_row = wts_l2.Reshape([ROWS, H_band, W_OUT, 2]).SpatialDistribute(
        dimension=0, tileCount=ROWS
    )  # list of ROWS views, each [H_band, W_OUT, 2]
    for row, channel in enumerate(l2_l1_rows):
        stamp.set_l2_to_l1_transfer(channel, wts_per_row[row], [wts_l1] * COLS, KR_WTS)

    # =====================================================================
    # kernel: core (c, r) samples the full image with its grid band, producing
    # H_band*W_OUT output points. All three async buffers rotate every call, so
    # every kernelratio is 1.
    # =====================================================================
    lp_params = [
        mode,
        align_corners,
        1,  # kernelratio_ifm
        1,  # kernelratio_wts
        1,  # kernelratio_ofm
        H_IN,
        W_IN,
        H_band,  # HW_grid factor 1
        W_OUT,  # HW_grid factor 2  (HW_grid_sub = H_band * W_OUT)
    ]
    stamp.set_kernel_arguments([ifm_l1, wts_l1, ofm_l1])
    stamp.set_kernel_function_name("gridsample2d_kernel")
    stamp.set_kernel_impl(Path("customop_gridsample2d.cpp"))
    stamp.set_kernel_params(lp_params)
    stamp.set_kernel_nb_calls(n_rot)

    # =====================================================================
    # L1 -> L2 gather: core (c, r) writes output band r of channel c, within the
    # CURRENT batch slice: Y_slice[c, r*H_band : (r+1)*H_band, :].
    # View ofm_l2 as [CHANNELS, ROWS, H_band, W_OUT] and give core (col=c, row=r)
    # the [c, r, :, :] slice -- BATCH is carried by the slab refill.
    # =====================================================================
    DIM = tensor_expr.DimensionSliceValue
    ofm_slots = ofm_l2.Reshape([CHANNELS, ROWS, H_band, W_OUT])
    for col, col_channel in enumerate(l1_l2):
        l1_src: list[tensor_expr.TensorExpr] = []
        l2_dst: list[tensor_expr.TensorExpr] = []
        for row in range(ROWS):
            l1_src.append(ofm_l1)
            # slice channel==col and band==row (singletons dropped), leaving the
            # [H_band, W_OUT] tile this core produces.
            l2_dst.append(ofm_slots.Slice({0: DIM(col, 1), 1: DIM(row, 1)}))
        stamp.set_l1_to_l2_transfer(col_channel, l1_src, l2_dst, KR_OFM)

    # =====================================================================
    # L2 -> DDR drain: one batch slice per iteration, mirroring the L3 -> L2
    # staging. TileBy on the L3 side supplies the BATCH iteration axis.
    # =====================================================================
    stamp.set_l2_to_l3_transfer(
        l2_l3_on(OFM_MT),
        ofm_l2.Reshape([ofm_l2_elems]),
        ofm.getTensorVar()
        .Reshape([BATCH * CHANNELS * H_OUT * W_OUT])
        .TileBy([ofm_l2_elems]),
    )
