# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""
AffineGrid2D, non-vectorized single-core variant.

Demonstrates the use of:
* Temporal tiling (setTemporalIterations() on L2 buffers)
"""

from pathlib import Path

import tensor_expr


def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    # Single-phase, single-stamp op: use the first (and only) phase.
    phase = tiling[0][0]

    # Unpack the op interface: two inputs (theta, size) and one output (grid).
    ifm, wts, ofm = opInterface

    ifm_shape = ifm.getShape()
    wts_shape = wts.getShape()
    ofm_shape = ofm.getShape()

    assert (
        len(ifm_shape) == 3
    ), f"IFM (theta) must be 3D [N,2,3], got {len(ifm_shape)}D: {ifm_shape}"
    assert ifm_shape[1] == 2, f"Theta height must be 2, got {ifm_shape[1]}"
    assert (
        len(wts_shape) == 1
    ), f"WTS (size) must be 1D [4], got {len(wts_shape)}D: {wts_shape}"
    assert (
        wts_shape[0] == 4
    ), f"WTS (size) must have 4 elements [N,C,H,W], got {wts_shape[0]}"
    assert (
        len(ofm_shape) == 4
    ), f"OFM (grid) must be 4D [N,H,W,2], got {len(ofm_shape)}D: {ofm_shape}"
    assert (
        ofm_shape[3] == 2
    ), f"Grid last dimension must be 2 (x,y coords), got {ofm_shape[3]}"

    BATCH = ifm_shape[0]
    THETA_H = ifm_shape[1]
    THETA_W = ifm_shape[2]
    WTS_LEN = wts_shape[0]
    H_OUT = ofm_shape[1]
    W_OUT = ofm_shape[2]
    DIM_OUT = ofm_shape[3]

    ifm_dtype = ifm.getDType()
    wts_dtype = wts.getDType()
    ofm_dtype = ofm.getDType()

    # =========================================================================
    # L1 (per-core) memory layout. The region 0xA000-0xDFFF is reserved.
    # Each element is 2 bytes (bfloat16); DMA transfers align to 64 bytes. Only
    # one batch slice is resident at a time (single-buffered in L1).
    # =========================================================================
    ELEM_SIZE = 2
    ALIGN = 64

    def align_up(x, a):
        return ((x + a - 1) // a) * a

    IFM_L1_ADDRESS = 0x0
    WTS_L1_ADDRESS = align_up(IFM_L1_ADDRESS + THETA_H * THETA_W * ELEM_SIZE, ALIGN)
    OFM_L1_ADDRESS = align_up(WTS_L1_ADDRESS + WTS_LEN * ELEM_SIZE, ALIGN)

    l1_end = OFM_L1_ADDRESS + H_OUT * W_OUT * DIM_OUT * ELEM_SIZE
    assert (
        l1_end <= 0xA000
    ), f"L1 overflow: end@{l1_end:#x} exceeds reserved region at 0xA000"

    # L1 buffers (per-core), one batch slice each.
    ifm_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(address=IFM_L1_ADDRESS)],
        shape=[THETA_H, THETA_W],
        type=ifm_dtype,
    )
    wts_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(address=WTS_L1_ADDRESS)],
        shape=[WTS_LEN],
        type=wts_dtype,
    )
    ofm_mk_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(address=OFM_L1_ADDRESS)],
        shape=[H_OUT, W_OUT, DIM_OUT],
        type=ofm_dtype,
    )

    # =========================================================================
    # Memtile columns hosting each L2 buffer.
    # Note that L3->L2 transfers can happen with MT+-1 as destination memory
    # tile on the L2 side (on this overlay, channels have access to the adjacent
    # memtiles).
    THETA_MT, SIZE_MT, GRID_MT = 0, 2, 2

    # L2 (shared memory) buffers. theta and grid are double-buffered and stream
    # one batch slice per temporal iteration; size is a single static broadcast.
    # =========================================================================
    ifm_mem_var = tensor_expr.TensorVar.make(
        [
            tensor_expr.Location(THETA_MT, 0, 0x0000),
            tensor_expr.Location(
                THETA_MT, 0, align_up(THETA_H * THETA_W * ELEM_SIZE, ALIGN)
            ),
        ],
        shape=[THETA_H, THETA_W],
        type=ifm_dtype,
    )
    ifm_mem_var.setTemporalIterations(BATCH)

    wts_mem_var = tensor_expr.TensorVar.make(
        [tensor_expr.Location(SIZE_MT, 0, 0x0000)],
        shape=[WTS_LEN],
        type=wts_dtype,
    )

    # Place the OFM on column 2, after the size buffer, which is also on column 2.
    GRID_L2_BASE = align_up(WTS_LEN * ELEM_SIZE, ALIGN)
    GRID_L2_SLAB = align_up(H_OUT * W_OUT * DIM_OUT * ELEM_SIZE, ALIGN)
    ofm_mem_var = tensor_expr.TensorVar.make(
        [
            tensor_expr.Location(GRID_MT, 0, GRID_L2_BASE),
            tensor_expr.Location(GRID_MT, 0, GRID_L2_BASE + GRID_L2_SLAB),
        ],
        shape=[H_OUT, W_OUT, DIM_OUT],
        type=ofm_dtype,
    )
    ofm_mem_var.setTemporalIterations(BATCH)

    l3_l2 = phase.get_l3_to_l2_channels()
    l1_l2 = phase.get_l1_to_l2_channels()
    l2_l3 = phase.get_l2_to_l3_channels()

    # Pick a channel on the column that hosts the buffer.
    def _pick_ch_by_memtile_col(channels, col, fallback_index):
        matches = [c for c in channels if c.mem_tile_port.tile_col == col]
        return matches[0] if matches else channels[fallback_index]

    # theta rides the COLUMNS broadcast family; size rides the ROWS family.
    l2_l1_cols = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l2_l1_rows = phase.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)

    # DDR -> L2. theta [BATCH,2,3] is flattened and tiled by batch into BATCH
    # slices of THETA_H*THETA_W elements, one streamed per iteration into the
    # double-buffered L2 (identity pattern on the L2 side).
    IFM_CHUNK = THETA_H * THETA_W
    phase.set_l3_to_l2_transfer(
        _pick_ch_by_memtile_col(l3_l2, THETA_MT, 0),
        ifm.getTensorVar().Reshape([BATCH * IFM_CHUNK]).TileBy([IFM_CHUNK]),
        ifm_mem_var.Reshape([IFM_CHUNK]),
    )
    # Transfer the size input only once.
    phase.set_l3_to_l2_transfer(
        _pick_ch_by_memtile_col(l3_l2, SIZE_MT, 2), wts.getTensorVar(), wts_mem_var
    )

    # L2 -> L1: broadcast theta to every core over the column family (one batch
    # slice per iteration) so no core is left with an unconnected input port.
    for channel in l2_l1_cols:
        phase.set_l2_to_l1_transfer(channel, ifm_mem_var, ifm_mk_var)

    # L2 -> L1: broadcast size over the row family. size is identical for every
    # iteration, so repeat it BATCH times to feed all iterations.
    for channel in l2_l1_rows:
        phase.set_l2_to_l1_transfer(channel, wts_mem_var.Repeat(BATCH), wts_mk_var)

    # L1 -> L2: every core must have its output port connected. Each core
    # computed the same result, which goes into the single OFM L2 buffer
    # (redundant results are unused). One grid slice is produced per iteration.
    for channel in l1_l2:
        n_cores = len(channel.core_tile_ports)
        phase.set_l1_to_l2_transfer(channel, ofm_mk_var, [ofm_mem_var] * n_cores)

    # L2 -> DDR: reassemble the batch. Each iteration's grid slice (flattened,
    # identity on the L2 side) lands in the corresponding batch slice of the
    # flattened [BATCH,H,W,2] output.
    OFM_CHUNK = H_OUT * W_OUT * DIM_OUT
    phase.set_l2_to_l3_transfer(
        _pick_ch_by_memtile_col(l2_l3, GRID_MT, 0),
        ofm_mem_var.Reshape([OFM_CHUNK]),
        ofm.getTensorVar().Reshape([BATCH * OFM_CHUNK]).TileBy([OFM_CHUNK]),
    )

    # =========================================================================
    # Kernel binding. Argument order matches the kernel signature
    # (theta, size, grid); lp_params = [align_corners, H_OUT, W_OUT]. The kernel
    # is called once per batch element.
    # =========================================================================
    lp_params = [
        opInterface.attributes.get("align_corners", 1),
        H_OUT,
        W_OUT,
    ]

    phase.set_kernel_arguments([ifm_mk_var, wts_mk_var, ofm_mk_var])
    phase.set_kernel_function_name("affinegrid2d_kernel")
    phase.set_kernel_impl(Path("customop_affinegrid2d.cpp"))
    phase.set_kernel_params(lp_params)
    phase.set_kernel_nb_calls(BATCH)
