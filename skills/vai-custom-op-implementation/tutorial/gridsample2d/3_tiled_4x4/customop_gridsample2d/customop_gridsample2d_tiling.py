# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""AieConfig (new-API) tiling for the fully-distributed 4x4 GridSample2D op.

Migration of the old Tiling_4x4 script to the new AieConfig API.

GridSample2D has TWO inputs and one output:
  * IFM  X    -- the input image        [BATCH, CHANNELS, H_IN, W_IN]
  * WTS  grid -- the sampling grid       [BATCH, H_OUT, W_OUT, 2]  (shared over channels)
  * OFM  Y    -- the sampled output      [BATCH, CHANNELS, H_OUT, W_OUT]

Sampling is random-access into the image, so the FULL image X[b,c] must be
resident on the core that samples it. The grid and the output are split
spatially: this variant distributes work over the full 4x4 array.

Distribution over the 4x4 overlay (an outer product):
  * COLUMN c  <-  channel c: the image X[b,c] is broadcast down the ROWS of
    column c. Column c always processes channel c.
  * ROW r     <-  the 4x4 kernel splits its grid band INTERNALLY by core_row.
    Each core is fed the WHOLE column H-band (H_grid rows of grid, all ROWS
    sub-bands); the kernel computes row_offset = HW_grid_sub * core_row and
    samples only its own [H_grid/ROWS, W_OUT] sub-band. Therefore the grid band
    is BROADCAST down the ROWS (not split per-row in the tiling) and each core
    writes just its own [H_grid/ROWS, W_OUT] output sub-band.

Temporal (H) tiling:
  The output H_OUT is cut into H_tile temporal tiles (auto-sized to fit L1).
  Each kernel call processes one column H-band of H_grid = H_OUT/H_tile output
  rows for one batch. Per column the temporal axis is BATCH * H_tile calls, and
  because CHANNELS == COLS one batch maps one channel onto every column, so the
  kernel is called BATCH * H_tile times per core (= mk_rep).

Buffers are declared adf::bpc_async_0d in the kernel (manual acquire/release
gated by the kernelratio lp_params). In the AieConfig API an async buffer is
selected by giving its L2<->L1 transfer a `ratio`: the buffer is re-armed once
every `ratio` kernel calls. The IFM changes once per batch (every H_tile calls),
so kernelratio_ifm = H_tile; the grid and output change every call, ratio = 1.

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
    assert ifm_shape[0] == ofm_shape[0], "batch mismatch"
    assert ifm_shape[1] == ofm_shape[1], "channel mismatch"

    BATCH = ifm_shape[0]
    CHANNELS = ifm_shape[1]
    H_IN = ifm_shape[2]
    W_IN = ifm_shape[3]
    H_OUT = ofm_shape[2]
    W_OUT = ofm_shape[3]

    # This distribution maps channels onto COLUMNS. The kernel splits each
    # column band across the ROWS internally (core_row), so CHANNELS == COLS.
    assert CHANNELS == COLS, f"CHANNELS ({CHANNELS}) must equal COLS ({COLS})"

    # Mode / align_corners come from the op attributes (kernel expects ints).
    mode_attr = opInterface.attributes.get("mode", 1)
    if mode_attr in ("linear", "bilinear"):
        mode = 1
    elif mode_attr == "nearest":
        mode = 0
    else:
        mode = 0
    align_corners = opInterface.attributes.get("align_corners", 1)

    ifm_dtype = ifm.getDType()
    wts_dtype = wts.getDType()
    ofm_dtype = ofm.getDType()

    # =========================================================================
    # AUTO-DETECT H_tile / W_tile (same L1-fit search as the old Tiling_4x4).
    # Each core holds: full image + one column grid band (H_grid rows) + its own
    # output sub-band (H_grid/ROWS rows).
    # =========================================================================
    L1_AVAILABLE = 0xA000  # 40960 bytes
    ELEM_SIZE = 2  # bfloat16
    ALIGN = 64

    def align_up(x, a):
        return ((x + a - 1) // a) * a

    ifm_l1_bytes = align_up(H_IN * W_IN * ELEM_SIZE, ALIGN)
    assert ifm_l1_bytes < L1_AVAILABLE, "input image too large for L1"
    remaining = L1_AVAILABLE - ifm_l1_bytes

    def tile_fits(ht, wt):
        h_grid = H_OUT // ht
        w_grid = W_OUT // wt
        wts_bytes = align_up(h_grid * w_grid * 2 * ELEM_SIZE, ALIGN)
        ofm_bytes = align_up((h_grid // ROWS) * w_grid * ELEM_SIZE, ALIGN)
        return wts_bytes + ofm_bytes <= remaining

    H_tile = None
    W_tile = None
    for wt in range(1, W_OUT + 1):
        if W_OUT % wt != 0:
            continue
        for ht in range(1, H_OUT // ROWS + 1):
            if H_OUT % (ht * ROWS) != 0:
                continue
            if tile_fits(ht, wt):
                H_tile = ht
                W_tile = wt
                break
        if H_tile is not None:
            break
    assert H_tile is not None and W_tile is not None, "no valid H_tile/W_tile"
    assert W_tile == 1, f"only W_tile==1 supported by this tiling, got {W_tile}"

    H_grid = H_OUT // H_tile  # grid rows per column band (all ROWS sub-bands)
    H_sub = H_grid // ROWS  # output rows one core writes per call
    assert H_grid % ROWS == 0, f"H_grid ({H_grid}) must be divisible by ROWS ({ROWS})"

    # Kernel calls per core = BATCH batches x H_tile temporal H-tiles.
    n_rot = BATCH * H_tile
    KR_IFM = H_tile  # image re-armed once per batch (every H_tile calls)
    KR_WTS = 1
    KR_OFM = 1

    # ---- memtile columns hosting each L2 buffer ----
    IFM_MT = 0
    OFM_MT = 2
    WTS_MT = 3

    l3_l2 = stamp.get_l3_to_l2_channels()
    l2_l1_cols = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l2_l1_rows = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    def _pick(channels, col, fallback_index):
        matches = [c for c in channels if c.mem_tile_port.tile_col == col]
        if matches:
            return matches[0]
        return channels[fallback_index]

    def l3_l2_on(col: int):
        fallback = {IFM_MT: 0, WTS_MT: 2}[col]
        return _pick(l3_l2, col, fallback)

    def l2_l3_on(col: int):
        return _pick(l2_l3, col, 0)

    # =====================================================================
    # L2 (memtile) buffers -- each operand in its own memtile column.
    #   IFM   on MT0
    #   OFM   on MT2
    #   WTS   on MT3
    # Each slab holds ONE BATCH SLICE, not the whole tensor, and is refilled
    # once per batch (setTemporalIterations(BATCH)). Two Locations per slab =
    # ping/pong, so batch b+1 streams in while batch b computes.
    #
    #   IFM  [CHANNELS, H_IN,  W_IN ]  ->  4*64*80  =  20480 el =  40960 B
    #   WTS  [H_OUT,    W_OUT, 2    ]  ->  100*100*2 = 20000 el =  40000 B
    #   OFM  [CHANNELS, H_OUT, W_OUT]  ->  4*100*100 = 40000 el =  80000 B
    # vs a 1024 KB memtile column -- comfortable even doubled for ping/pong.
    # =====================================================================
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
    # L1 (per-core) buffers -- double-buffered so the next call's DMA can
    # overlap the current call's compute.
    #   IFM   : the full image                     [H_IN, W_IN]
    #   WTS   : the whole column H-band (all ROWS)  [H_grid, W_OUT, 2]
    #   OFM   : this core's output sub-band         [H_sub, W_OUT]
    # =====================================================================
    ifm_l1 = tensor_expr.TensorVar.make(
        [H_IN, W_IN], ifm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    wts_l1 = tensor_expr.TensorVar.make(
        [H_grid, W_OUT, 2], wts_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    ofm_l1 = tensor_expr.TensorVar.make(
        [H_sub, W_OUT], ofm_dtype, tensor_expr.BufferingStrategy.DoubleBuffered
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
    stamp.set_l3_to_l2_transfer(
        l3_l2_on(WTS_MT),
        wts.getTensorVar().Reshape([BATCH * H_OUT * W_OUT * 2]).TileBy([wts_l2_elems]),
        wts_l2.Reshape([wts_l2_elems]),
    )

    # =====================================================================
    # L2 -> L1 IFM: the slab now holds one batch slice [CHANNELS, H_IN, W_IN],
    # so column c is just channel c -- a plain slice, no Transpose needed. The
    # image is broadcast down the ROWS of its column and re-armed once per batch
    # (ratio = KR_IFM = H_tile calls).
    # =====================================================================
    ifm_per_col = ifm_l2.SpatialDistribute(
        dimension=0, tileCount=COLS
    )  # COLS views, each [H_IN, W_IN]
    for col, channel in enumerate(l2_l1_cols):
        stamp.set_l2_to_l1_transfer(channel, ifm_per_col[col], [ifm_l1] * ROWS, KR_IFM)

    # =====================================================================
    # L2 -> L1 WTS: the slab holds one batch's grid [H_OUT, W_OUT, 2], cut into
    # H_tile temporal H-bands of H_grid rows. Each column reads the SAME band
    # (the grid is channel-shared) and broadcasts it down its ROWS cores; the
    # kernel self-slices by core_row. H_tile is now the only temporal axis here
    # -- BATCH is carried by the slab's own refill.
    #   wts_l2               [H_OUT, W_OUT, 2]
    #   .Reshape H-tile axis [H_tile, H_grid, W_OUT, 2]
    # =====================================================================
    wts_bands = wts_l2.Reshape([H_tile, H_grid, W_OUT, 2])
    for col, channel in enumerate(l2_l1_rows):
        stamp.set_l2_to_l1_transfer(channel, wts_bands, [wts_l1] * ROWS, KR_WTS)

    # =====================================================================
    # kernel: core (col=c, row=r) samples the full image X[b,c] with its grid
    # sub-band, producing H_sub*W_OUT output points. lp_params[7]*lp_params[8]
    # is HW_grid = H_grid * W_OUT; the kernel divides by ROWS internally to get
    # HW_grid_sub, and offsets grid_data by row_offset = HW_grid_sub * core_row.
    # =====================================================================
    lp_params = [
        mode,
        align_corners,
        KR_IFM,  # kernelratio_ifm
        KR_WTS,  # kernelratio_wts
        KR_OFM,  # kernelratio_ofm
        H_IN,
        W_IN,
        H_grid,  # HW_grid factor 1  (H_OUT // H_tile)
        W_OUT,  # HW_grid factor 2  (HW_grid = H_grid * W_OUT)
    ]
    stamp.set_kernel_arguments([ifm_l1, wts_l1, ofm_l1])
    stamp.set_kernel_function_name("gridsample2d_kernel")
    stamp.set_kernel_impl(Path("customop_gridsample2d.cpp"))
    stamp.set_kernel_params(lp_params)
    stamp.set_kernel_nb_calls(n_rot)

    # =====================================================================
    # L1 -> L2 gather: core (col=c, row=r) writes output sub-band r of channel
    # c for temporal H-tile t, within the CURRENT batch slice:
    #   Y_slice[c, t*H_grid + r*H_sub : t*H_grid + (r+1)*H_sub, :]
    # View ofm_l2 as [CHANNELS, H_tile, ROWS, H_sub, W_OUT]; give core
    # (col=c, row=r) the [c, :, r, :, :] slice -- H_tile stays temporal, BATCH
    # is carried by the slab refill.
    # =====================================================================
    DIM = tensor_expr.DimensionSliceValue
    ofm_slots = ofm_l2.Reshape([CHANNELS, H_tile, ROWS, H_sub, W_OUT])
    for col, col_channel in enumerate(l1_l2):
        l1_src: list[tensor_expr.TensorExpr] = []
        l2_dst: list[tensor_expr.TensorExpr] = []
        for row in range(ROWS):
            l1_src.append(ofm_l1)
            # slice channel==col and array-row==row (singletons dropped); keeps
            # the H_tile temporal axis and the [H_sub, W_OUT] tile.
            l2_dst.append(ofm_slots.Slice({0: DIM(col, 1), 2: DIM(row, 1)}))
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
