# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""All-CG-resident, H-halo-tiled, double-buffered tiling for the int8 3x3 conv op.

This example provides a template on how to use overlapping tiles when tiling
a convolution operation. It also demonstrates which functions from the AIE API
are suited for implementing the compute logic.

DO NOT consider this examples as optimal tiling in any sense of the word. With diferent
conv configurations h-halo-tiling strategies are suboptimal, especially in the presence
of big weights that don't fit in L3. In those cases, and to achieve optimal performance,
the ideal solution minimizes L3 -> L2 transfers and pushes reutilization of data in L2/L1
to the physical limit.

This examples works using the following dimensions: Cin=160, Cout=160, H=W=160, 3x3 s1, int8)

The constants below need to be changed in lock-step with the kernel's code (myconv.cpp).
What each knob is derived from:

  H, W, CG, V           <- the op's logical shape. CG = Cin/8, V=8 is fixed by
                           the int8 8-channel inner block. OFM CG = Cout/8.
  N_STAMPS              <- how many H-slabs to split the image into for extra
                           parallelism (2 here -> 32 cores). Needs
                           H % (N_STAMPS*HT_OUT) == 0.
  HALO = (KH-1)/2       <- halo rows per side for a KHxKW conv (1 for 3x3). Drives
                           H_L2 = HT_OUT + 2*HALO and the padding baked into the
                           model's IFM. For 1x1 there is NO halo -> use the
                           gemm (pure GEMM) tutorial instead.
  HT_OUT               <- output rows per H-tile. Bigger amortizes weight loads
                           but grows IFM/OFM L1; must divide H_PER_STAMP.
  COLS, ROWS           <- 4x4 overlay. COLS split the output W band, ROWS split
                           the output channel groups. W % COLS and CG % ROWS
                           should divide cleanly (else pad, as Cout 160->192 here).
  W_BLK, W_SUB         <- output W block (8) and input W window (W_BLK+KW-1,
                           padded to 16). Block-cyclic across COLS.
  CG_CHUNK / CG_PER    <- split the Cin reduction into CG_CHUNK depth chunks of
                           CG_PER groups (must be the kernel's NUM_DEPTH/CG_PER).
                           This is the OFM async ratio (partial-sum accumulation
                           across chunks). More chunks = smaller L1 IFM/WTS tile.
  OFM_ITER             <- output-channel tiles beyond the ROWS split
                           (Cout_pad/(ROWS*CG_OFM_TILE*8)); re-streams IFM/WTS.
  WTS_TOTAL / L1_WTS_TILE_SIZE   <- weight blob size and per-wave size. Here the blob is
                           embedded as the myconv_wts initializer of
                           conv3x3_int8.onnx (weights reordered into mmul waves,
                           Cout padded 160->192, per-channel requant tail folded
                           in). For a NEW layer you generate your own blob;
                           L1_WTS_TILE_SIZE = CG_OFM_TILE*CG_PER*KH*KW*64 + bias/requant tail.

Padding, memtile placement (IFM double buffer split over MT0/MT1 because one
buffer > half a memtile), and the psum-in-OFM scratch trick are general performance tricks.
"""

from pathlib import Path

import tensor_expr

# Logical / DDR (HCWN_C8) constants.
H = 160
CG = 20  # C / 8 input channel groups
W = 160
V = 8  # 8-channel inner block

COLS = 4
ROWS = 4
N_STAMPS = 2

H_PER_STAMP = H // N_STAMPS  # 80

# H-tile geometry: HT_OUT output rows + HALO halo rows on each side.
HALO = 1
HT_OUT = 10
H_L2 = HT_OUT + 2 * HALO  # 12 IFM rows per tile (10 output + 2 halo)
N_HT = H_PER_STAMP // HT_OUT  # 8 rotations per stamp (matches native)

H_PAD = H + 2 * HALO  # 162 padded IFM rows (Pad node materialises)
# Per-stamp padded slab: the tiles for stamp s span padded rows
# [s*H_PER_STAMP : s*H_PER_STAMP + (N_HT-1)*HT_OUT + H_L2] = 82 rows.
SLAB_PAD = (N_HT - 1) * HT_OUT + H_L2  # 82

W_BAND = W // COLS  # 40 output W columns per grid-column
CG_PER_CORE = CG // ROWS  # 5 output CGs per grid-row
ROW_LEN = W_BAND * V  # elements per (cg,row) in L1 = 320

# Padded IFM W geometry (Pad node materialises the W halo)
# Native streams 16-wide input W sub-tiles at padded offset col*W_BLK + blk*32,
# so W must be padded to 168 (1 left halo + 160 + 7 right overrun).
W_PAD_L = 1
W_PAD_R = 7
W_PAD = W + W_PAD_L + W_PAD_R  # 168 padded IFM W columns

# Native L2->L1 IFM streaming geometry (from tiling.h ifmsv_dim)
# ifmsv_dim {8,1,16,5,12} = [V, N, W_SUB, CG_PER, H] -> 7680 B L1 sub-tile.
W_BLK = 8
W_SUB = 16
N_BLK = W // (COLS * W_BLK)  # 160/(4*8) = 5 blocks per column
W_COL_SPAN = (N_BLK - 1) * (COLS * W_BLK) + W_SUB  # 4*32 + 16 = 144 (per-col W tile)
CG_CHUNK = CG // CG_PER_CORE  # 4 ifm-depth reduction chunks (5 CG each)
CG_PER = CG_PER_CORE  # 5 CGs per chunk (alias for L1 clarity)
OFM_ITER_IFM = 3  # IFM re-streamed per ofm-depth iteration
N_IFM_LOADS = OFM_ITER_IFM * N_BLK * CG_CHUNK * N_HT  # 480 loads/core
assert N_IFM_LOADS == 480

# Weights: conv3x3_int8.onnx carries the packed blob as the myconv_wts constant.
CIN = 160
KH = KW = 3
WTS_TOTAL = 276480  # pure weights: 192*160*3*3

# Native L2->L1 WTS streaming geometry (from native tiling.h)
# One kernel call's weights: CG_OFM_TILE(2) output CGs x CG_PER(5) input CGs
# x KH*KW taps x an 8x8 mmul tile. (CG_OFM_TILE is defined with the OFM
# geometry below; spelled out here to keep this block order-independent.)
L1_WTS_WEIGHTS = 2 * CG_PER * KH * KW * V * V  # 5760 B of weights
L1_BIAS_BYTES = 2 * V * 4  # 64 B: 16 int32, one per output channel of the wave
# The kernel reads bias at a fixed offset past the weights, so the two travel in
# ONE L1 buffer (there are only 4 row-broadcast L2->L1 ports and WTS holds them
# all -- bias cannot have its own stream). They are still SEPARATE ONNX operands:
# two L3->L2 transfers write disjoint slices of the same L2 buffer.
L1_WTS_TILE_SIZE = L1_WTS_WEIGHTS + L1_BIAS_BYTES  # 5824 B
IFM_CHUNK = 4
OFM_ITER = 3
assert OFM_ITER * ROWS * IFM_CHUNK * L1_WTS_WEIGHTS == WTS_TOTAL
WTS_WAVES = OFM_ITER * IFM_CHUNK  # 12 waves per core

IFM_L2_BUF = CG * H_L2 * W_PAD * V  # 20*12*168*8 = 322560 B per (single) buffer

# ---- Native OFM geometry (from tiling.h ofm_dim / ofmsv_dim / ofm_repetition)
CG_OUT = 24  # output CG (Cout 192 = ROWS*CG_OFM_TILE*OFM_ITER*8, divides cleanly)
CG_OFM_TILE = 2  # output CGs per mmul output tile (16 ch)
CG_ITER_STRIDE = ROWS * CG_OFM_TILE  # 8: CG step between ofm iterations
CG_ROW_SPAN = (OFM_ITER - 1) * CG_ITER_STRIDE + CG_OFM_TILE  # 18: per-row CG span
W_COL_SPAN_O = (N_BLK - 1) * (COLS * W_BLK) + W_BLK  # 136: per-col output W span
OFM_ACCUM = CG_CHUNK  # 4: OFM async ratio (CG-chunk accumulation)
OFM_L2_BUF = CG_OUT * HT_OUT * W * V  # 24*10*160*8 = 307200 B

# ---- OFM L1 psum-scratch sizing (self-contained mmul kernel) ----
OFM_SV_ELEMS = CG_OFM_TILE * HT_OUT * W_BLK * V  # 1280 int8 output elements
OFM_PSUM_BYTES = OFM_SV_ELEMS + OFM_SV_ELEMS * 4  # 1280 out + 5120 int32 psum
OFM_ROW_BYTES = CG_OFM_TILE * W_BLK * V  # 128 B per H row of the sv
OFM_L1_ROWS = -(-OFM_PSUM_BYTES // OFM_ROW_BYTES)  # ceil -> 50

# Total kernel calls per core: IFM feeds 480 loads (each a kernel call); OFM
# drains one tile per OFM_ACCUM=4 calls (120 tiles/core).
NB_CALLS = N_IFM_LOADS  # 480


def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    ifm, wts, bias, ofm = opInterface
    ifm_dtype = ifm.getDType()
    wts_dtype = wts.getDType()
    ofm_dtype = ofm.getDType()

    # DDR tensors in HCWN_C8 order. IFM is the PADDED activation (162 rows x 168 W).
    ifm_ddr = ifm.getTensorVar().Reshape([H_PAD, CG, W_PAD, V])
    ofm_ddr = ofm.getTensorVar().Reshape([H, CG_OUT, W, V])  # CG_OUT=24 (Cout 192)
    # [48 wave slots, 5760 B of weights each] -- matches wts_l2's slot layout
    wts_ddr = wts.getTensorVar().Reshape([OFM_ITER * ROWS * IFM_CHUNK, L1_WTS_WEIGHTS])
    # bias: one int32 per output channel, [OFM_ITER, ROWS, 16] == wave order
    # Bias is a separate ONNX operand carrying the RAW BYTES of the folded int32
    # values (int8 dtype, like the weights, so the DMA element counts line up
    # with the int8 L2 buffer). 16 channels x 4 B = 64 B per wave.
    bias_ddr = bias.getTensorVar().Reshape([OFM_ITER * ROWS, L1_BIAS_BYTES])

    # Per-stamp slab selection. IFM slabs are SLAB_PAD (82) rows and OVERLAP by
    # 2*HALO (stamp 0 = padded [0:82], stamp 1 = padded [80:162]); the padded
    # boundary rows supply the global top/bottom halo. OFM slabs are 80 rows.
    ifm_stamp = ifm_ddr.Tile(
        index=0, stride=H_PER_STAMP, tileLen=SLAB_PAD, numTiles=N_STAMPS
    )
    ofm_stamp = ofm_ddr.Tile(
        index=0, stride=H_PER_STAMP, tileLen=H_PER_STAMP, numTiles=N_STAMPS
    )

    for s in range(N_STAMPS):
        stamp = tiling[s][0]
        assert stamp.aie_core_cols == COLS and stamp.aie_core_rows == ROWS, (
            f"stamp {s} grid {stamp.aie_core_cols}x{stamp.aie_core_rows} "
            f"!= {COLS}x{ROWS}"
        )

        # ---- per-hop channels ----
        l3_l2 = stamp.get_l3_to_l2_channels()
        l2_l1_cols = stamp.get_l2_to_l1_channels(
            broadcast=tensor_expr.broadcast_on.COLUMNS
        )
        l2_l1_rows = stamp.get_l2_to_l1_channels(
            broadcast=tensor_expr.broadcast_on.ROWS
        )
        l1_l2 = stamp.get_l1_to_l2_channels()
        l2_l3 = stamp.get_l2_to_l3_channels()

        # ---- L2 buffers (double-buffered IFM/OFM; resident WTS) ----
        # IFM L2 buffer (322 KB) exceeds half a 512 KB memtile, so the two ping/
        # pong buffers live on separate memtiles (MT0, MT1) for double buffering.
        ifm_l2 = tensor_expr.TensorVar.make(
            locations=[
                tensor_expr.Location(0, 0, 0x00000),
                tensor_expr.Location(1, 0, 0x00000),
            ],
            shape=[CG, H_L2, W_PAD, V],
            type=ifm_dtype,
        )
        # The IFM L2 buffer is (double-)rotated once per H-tile: the L3->L2 read
        # lands N_HT overlapping H-tiles, so N_HT L2 rotations. This count is the
        # double-buffer synchronization barrier and MUST be declared (the new API
        # does not auto-infer it) -- omitting it deadlocks the multi-uC sim.
        ifm_l2.setTemporalIterations(N_HT)
        # OFM L2 buffer (300 KB, padded Cout 24 CG): single-buffered on MT2,
        # drained once per H-tile (N_HT rotations).
        ofm_l2 = tensor_expr.TensorVar.make(
            locations=[tensor_expr.Location(2, 0, 0x00000)],
            shape=[CG_OUT, HT_OUT, W, V],
            type=ofm_dtype,
        )
        ofm_l2.setTemporalIterations(N_HT)
        # Resident WTS holds native's full 282624 B blob flat, in the
        # [OFM_ITER, ROWS, IFM_CHUNK, L1_WTS_TILE_SIZE] wave layout. Moved once (one DMA).
        wts_l2 = tensor_expr.TensorVar.make(
            locations=[tensor_expr.Location(3, 0, 0x00000)],
            shape=[OFM_ITER * ROWS * IFM_CHUNK, L1_WTS_TILE_SIZE],
            type=wts_dtype,
        )

        # ---- L1 buffers (compiler-assigned addresses) ----
        # IFM L1 = native ifmsv (7680 B), byte-exact to native's ADF sv order
        # {8,16,5,12} = [V, W_SUB, CG_PER, H] -> numpy C-order [H, CG_PER, W_SUB, V].
        # DOUBLE-buffered so the next wave's DMA overlaps compute (480 waves/core).
        ifm_l1 = tensor_expr.TensorVar.make(
            [H_L2, CG_PER, W_SUB, V],
            ifm_dtype,
            tensor_expr.BufferingStrategy.DoubleBuffered,
        )
        # OFM L1: native ofmsv order [H, CG_OFM_TILE, W_BLK, V]. The int8 output
        # is only OFM_SV_ELEMS (1280 B), BUT the self-contained mmul kernel
        # accumulates the CG_CHUNK=4 depth chunks as int32 partial sums that must
        # persist across the 4 async-ratio-4 calls of a tile; that psum lives in
        # THIS buffer right after the int8 output. So the physical buffer is
        # enlarged to OFM_L1_ROWS rows even though only the first HT_OUT rows drain.
        ofm_l1 = tensor_expr.TensorVar.make(
            [OFM_L1_ROWS, CG_OFM_TILE, W_BLK, V],
            ofm_dtype,
            tensor_expr.BufferingStrategy.DoubleBuffered,
        )
        # WTS L1 = one native wave (5888 B), DOUBLE-buffered.
        wts_l1 = tensor_expr.TensorVar.make(
            [L1_WTS_TILE_SIZE],
            wts_dtype,
            tensor_expr.BufferingStrategy.DoubleBuffered,
        )

        # ---- L3 -> L2 IFM: stream N_HT overlapping H-halo tiles (all CGs) ----
        # slab [SLAB_PAD, CG, W_PAD, V] -> OVERLAPPING Tile of H (stride HT_OUT=10,
        # len H_L2=12) -> [N_HT, H_L2, CG, W_PAD, V] -> [N_HT, CG, H_L2, W_PAD, V].
        ifm_read = (
            ifm_stamp[s]
            .Tile(index=0, stride=HT_OUT, tileLen=H_L2, numTiles=N_HT)
            .Transpose([0, 2, 1, 3, 4])
        )
        stamp.set_l3_to_l2_transfer(l3_l2[0], ifm_read, ifm_l2)

        # ---- L2 -> L1 IFM: native block-cyclic-W / CG-chunk / ofm-repeat ----
        # Every core gets ALL 20 CG (streamed as CG_CHUNK=4 depth chunks of
        # CG_PER=5), and IFM is BROADCAST across rows (rows differ only by
        # weights). Columns get DISTINCT W: output blocks of W_BLK=8 are
        # block-cyclic across the 4 columns, so column c reads its 5 (N_BLK)
        # 16-wide input sub-tiles at padded W = c*8 + blk*32.
        #   ifm_l2 [CG, H_L2, W_PAD, V]
        #   -> Reshape CG into [CG_CHUNK, CG_PER]            (depth chunks)
        #   -> Tile W by column (stride 8, len 144, 4 tiles) (block-cyclic base)
        #   -> Tile W by block  (stride 32, len 16, 5 tiles) (the 16-wide windows)
        #   -> bring COLS outermost, Repeat OFM_ITER times, COLS back outermost
        # Result [COLS, OFM_ITER, N_BLK, CG_CHUNK, H_L2, CG_PER, W_SUB, V]; the
        # trailing [H, CG_PER, W_SUB, V] = ifm_l1 (7680 B). In the new API the
        # leading COLS axis is SpatialDistribute'd across the overlay columns and
        # each column's stream is broadcast to its ROWS cores.
        ifm_dist = (
            ifm_l2.Reshape([CG_CHUNK, CG_PER, H_L2, W_PAD, V])
            .Tile(index=3, stride=W_BLK, tileLen=W_COL_SPAN, numTiles=COLS)
            .Tile(index=4, stride=COLS * W_BLK, tileLen=W_SUB, numTiles=N_BLK)
            .Transpose(
                [1, 0, 2, 3, 4, 5, 6]
            )  # [COLS, N_BLK, CG_CHUNK, CG_PER, H, W_SUB, V]
            .Repeat(
                OFM_ITER_IFM
            )  # [OFM_ITER, COLS, N_BLK, CG_CHUNK, CG_PER, H, W_SUB, V]
            # bring COLS outermost AND swap CG_PER<->H so the trailing dims are
            # [H, CG_PER, W_SUB, V] = native ifmsv order (matches ifm_l1).
            .Transpose(
                [1, 0, 2, 3, 5, 4, 6, 7]
            )  # [COLS, OFM_ITER, N_BLK, CG_CHUNK, H, CG_PER, W_SUB, V]
        )
        ifm_per_col = ifm_dist.SpatialDistribute(dimension=0, tileCount=COLS)
        for col, channel in enumerate(l2_l1_cols):
            # L1 destination traversal: fill the buffer one H row at a
            # time, H_L2 rows per kernel call (old flow's write_mk_in0,
            # buf {8,16,5,12} tile {8,16,5,1} wrap=12).
            ifm_l1_rows = ifm_l1.Tile(index=0, stride=1, tileLen=1, numTiles=H_L2)
            stamp.set_l2_to_l1_transfer(channel, ifm_per_col[col], [ifm_l1_rows] * ROWS)

        # ---- L3 -> L2 WTS: move the WHOLE padded blob ONCE (resident) ----
        # Weights fill [.., 0:L1_WTS_WEIGHTS] of every wave slot...
        stamp.set_l3_to_l2_transfer(
            l3_l2[3],
            wts_ddr,
            wts_l2.Slice(1, 0, L1_WTS_WEIGHTS),
        )
        # ...and bias fills the 64 B tail. One bias slice serves all IFM_CHUNK
        # depth chunks of its (ofm-iter, row) pair, so it is replicated 4x.
        stamp.set_l3_to_l2_transfer(
            l3_l2[4],
            bias_ddr.Repeat(IFM_CHUNK).Transpose([1, 0, 2]),
            wts_l2.Slice(1, L1_WTS_WEIGHTS, L1_BIAS_BYTES),
        )

        # ---- L2 -> L1 WTS: native DISTINCT-per-row, 12-wave streaming ----
        # View the flat 282624 B L2 blob as native's
        #   [OFM_ITER, ROWS, IFM_CHUNK, L1_WTS_TILE_SIZE], bring ROWS to the front and
        # SpatialDistribute across the 4 overlay rows so each row gets a DISTINCT
        # [OFM_ITER, IFM_CHUNK, L1_WTS_TILE_SIZE] = 12 waves of 5888 B (row r owns output
        # tiles {r, r+4, r+8}). WTS is broadcast across the 4 columns per row.
        wts_native = wts_l2.Reshape(
            [OFM_ITER, ROWS, IFM_CHUNK, L1_WTS_TILE_SIZE]
        ).Transpose(
            [1, 0, 2, 3]
        )  # [ROWS, OFM_ITER, IFM_CHUNK, L1_WTS_TILE_SIZE]
        wts_rows = wts_native.SpatialDistribute(
            dimension=0, tileCount=ROWS
        )  # ROWS x [OFM_ITER, IFM_CHUNK, L1_WTS_TILE_SIZE]
        # Re-stream the 12 distinct waves across the spatial loop (N_BLK W-blocks
        # x N_HT H-tiles = 40x) so WTS delivers 480 tiles/row -- native's SYNC
        # re-stream. The order matches IFM's [H, ofm, W_blk, CG] so per-call
        # (IFM, WTS) pairing is native-faithful.
        wts_stream = [
            r.Repeat(N_BLK)  # [N_BLK, OFM_ITER, IFM_CHUNK, L1_WTS_TILE_SIZE]
            .Transpose([1, 0, 2, 3])  # [OFM_ITER, N_BLK, IFM_CHUNK, L1_WTS_TILE_SIZE]
            .Repeat(N_HT)  # [N_HT, OFM_ITER, N_BLK, IFM_CHUNK, L1_WTS_TILE_SIZE]
            for r in wts_rows
        ]  # 8*3*5*4 = 480 tiles/row (ratio 1, sync)
        for row, channel in enumerate(l2_l1_rows):
            stamp.set_l2_to_l1_transfer(channel, wts_stream[row], [wts_l1] * COLS)

        # ---- kernel: bind impl, args, params, calls ----
        stamp.set_kernel_arguments([ifm_l1, wts_l1, ofm_l1])
        stamp.set_kernel_function_name("myconv_kernel")
        stamp.set_kernel_impl(Path("myconv.cpp"))
        stamp.set_kernel_params([0])  # Unused
        stamp.set_kernel_nb_calls(NB_CALLS)

        # ---- L1 -> L2 OFM: native block-cyclic-W / row-CG / ofm-iter gather ----
        # Mirror of native ofm_pattern: cols get DISTINCT output W (8-wide blocks,
        # block-cyclic), rows get DISTINCT output CGs (row r owns 3 output tiles
        # {r*2, r*2+8, r*2+16}, one per ofm-iter, each CG_OFM_TILE=2 CG = 16 ch).
        #   ofm_l2 [CG_OUT, HT_OUT, W, V]
        #   -> Tile W by column (stride 8, len 136, 4)
        #   -> Tile W by block  (stride 32, len 8, 5)
        #   -> Tile CG by row   (stride 2, len 18, 4)
        #   -> Tile CG by iter  (stride 8, len 2, 3)
        #   -> Transpose to [COLS, ROWS, OFM_ITER, N_BLK, HT_OUT, CG_OFM_TILE,
        #                    W_BLK, V]
        # Leading [COLS, ROWS] SpatialDistribute2D across the 4x4 grid; the
        # trailing [HT_OUT, CG_OFM_TILE, W_BLK, V] = ofm_l1_out (1280 B). The OFM
        # async ratio (4 depth chunks accumulate into one tile before draining)
        # is the `ratio=OFM_ACCUM` on the L1->L2 transfer.
        ofm_write_core = (
            ofm_l2.Tile(index=2, stride=W_BLK, tileLen=W_COL_SPAN_O, numTiles=COLS)
            .Tile(index=3, stride=COLS * W_BLK, tileLen=W_BLK, numTiles=N_BLK)
            .Tile(index=2, stride=CG_OFM_TILE, tileLen=CG_ROW_SPAN, numTiles=ROWS)
            .Tile(
                index=3, stride=CG_ITER_STRIDE, tileLen=CG_OFM_TILE, numTiles=OFM_ITER
            )
            .Transpose([3, 1, 0, 2, 5, 4, 6, 7])
        )  # [COLS, ROWS, OFM_ITER, N_BLK, HT_OUT, CG_OFM_TILE, W_BLK, V]
        ofm_per_core = ofm_write_core.SpatialDistribute2D(
            dimA=0, dimB=1, tileACount=COLS, tileBCount=ROWS
        )
        for col, channel in enumerate(l1_l2):
            l1_src: list[tensor_expr.TensorExpr] = []
            l2_dst: list[tensor_expr.TensorExpr] = []
            for row in range(ROWS):
                l1_src.append(
                    ofm_l1.Tile(index=0, stride=1, tileLen=1, numTiles=HT_OUT)
                )
                l2_dst.append(ofm_per_core[col][row])
            stamp.set_l1_to_l2_transfer(channel, l1_src, l2_dst, OFM_ACCUM)

        # ---- L2 -> L3 OFM: write this H-tile band back (mirror of IFM read) ----
        # All CG_OUT output CGs are valid now (Cout=192 divides cleanly), so the
        # whole L2 buffer drains -- no padded-CG Slice needed.
        ofm_write = (
            ofm_stamp[s]
            .Tile(index=0, stride=HT_OUT, tileLen=HT_OUT, numTiles=N_HT)
            .Transpose([0, 2, 1, 3, 4])
        )
        # OFM L2 lives on memtile column 2, so drain through the co-located
        # L2->L3 channel (l2_l3[2]); a non-co-located channel cannot reach the
        # full OFM buffer range.
        stamp.set_l2_to_l3_transfer(l2_l3[2], ofm_l2, ofm_write)
