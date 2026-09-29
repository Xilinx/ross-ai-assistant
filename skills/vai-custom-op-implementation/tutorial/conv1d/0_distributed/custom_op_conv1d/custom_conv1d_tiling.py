# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

from pathlib import Path

import tensor_expr


# 1-D single-channel convolution tiling for the 1x4x4 overlay (new AieConfig API).
#
# AieConfig port of the old Tiling_4x4 `custom_conv1d_tiling.py`. Same math and
# distribution, expressed with explicit channels:
#
#   * IFM   -- shape [L_in]
#   * WTS   -- shape [K]     (the convolution filter; a second input broadcast to
#                            every core and held resident across all rotations)
#   * OFM   -- shape [L_out] with L_out = L_in - K + 1
#
# Each AIE core produces one T-element OFM tile. A 4x4 grid produces N_CORES=16
# consecutive OFM tiles per "rotation"; the tensor is swept in
# `n_rotations = NT / N_CORES` rotations.
#
# IFM DDR is split into OVERLAPPING windows so that consecutive windows overlap by halo elements.
# The overlap exists only on the L3 READ side -- every rotation writes into a fresh (ping/pong) L2
# buffer, so nothing aliases on the write side.
#
# IFM MEMTILE is broadcast down each COLUMN: a column's ROWS cores share the
# (ROWS*T + halo)-element window feeding their 4 contiguous output tiles;
#
# The WTS L2 buffer is resident (moved once, kept live, single-buffered). Its
# L2->L1 transfer uses ratio, so it is re-armed once every n_rotations kernel calls;
# the kernel honours the async WTS buffer with explicit acquire/release driven by lp_params[2] = calls_per_wts.
# The IFM/OFM L2 buffers rotate n_rotations times and the kernel is called n_rotations times per
# core; these counts MUST agree or the DMA deadlocks.
def getTiling(
    opInterface: tensor_expr.OperatorInfo, tiling: tensor_expr.AieConfig
) -> None:
    stamp = tiling[0][0]
    ROWS = stamp.aie_core_rows
    COLS = stamp.aie_core_cols
    N_CORES = ROWS * COLS

    ifm, wts, ofm = opInterface

    # Tile size (one core's OFM contribution).
    T = 64

    (L_in,) = ifm.getPaddedShape()
    (K,) = wts.getPaddedShape()
    (L_out,) = ofm.getPaddedShape()

    # The conv needs K-1 halo elements past its output span. We use one
    # more (halo = K) so the L2->L1 window stays even and DMA aligned.
    # The extra trailing element is transferred but never read by the kernel. This needs L_in >= L_out + K,
    # i.e. 4104, which the YAML's `auto_pad: true` provides (4103 -> 4104).
    halo = K
    # bf16 per-core IO buffers must have an even element count (4-byte total).
    IFM_L1_READ = ROWS * T + halo  # elements the DMA fills / kernel reads
    IFM_L1_LEN = IFM_L1_READ + (IFM_L1_READ & 1)  # rounded up to even

    NT = L_out // T
    assert NT * T == L_out, f"L_out ({L_out}) must be a multiple of T ({T})"
    assert NT % N_CORES == 0, (
        f"NT ({NT}) must be a multiple of {N_CORES} so the workload distributes"
        " evenly across the 4x4 grid"
    )
    n_rotations = NT // N_CORES

    # The last global IFM window must fit inside L_in exactly. Its last element
    # is (n_rotations - 1) * (N_CORES*T) + (N_CORES*T + halo) == L_out + halo.
    assert (
        L_in >= L_out + halo
    ), f"L_in ({L_in}) must be >= L_out + halo ({L_out} + {halo} = {L_out + halo})"

    # WTS stays live for the entire compilation.
    calls_per_wts = n_rotations

    dtype = ifm.getDType()

    # `auto_pad: true` makes the padded L_in even, so the L3 windows below start
    # and end on even element offsets.
    assert L_in % 2 == 0, f"auto_pad should make L_in even (got {L_in})"

    # One rotation's IFM window and OFM block.
    win = N_CORES * T + halo
    block = N_CORES * T

    # ---- Channels for each memory-hierarchy hop. ----
    # Memtile columns hosting each L2 buffer (must match the Location()s below).
    IFM_MT = 0
    OFM_MT = 2
    WTS_MT = 2

    l3_l2 = stamp.get_l3_to_l2_channels()
    l2_l1_cols = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l2_l1_rows = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    # A memtile DMA channel reaches its own memtile plus its immediate
    # neighbours (one to the left, one to the right) -- not the whole row. So
    # the L1->L2 channels, which sit on MT(1), can write MT(0), MT(1) or MT(2)
    # but not MT(3); the L2->L3 channels sit on MT(2) and read MT(1)..MT(3).
    # Indexing these lists positionally is not enough: the channel at index i is
    # not guaranteed to sit on column i, and picking one that cannot reach the
    # chosen buffer fails the build with "aiecompiler 77-5767 ... cannot access
    # full range of the shared buffer memory space".
    def _pick(channels, col, fallback_index):
        matches = [c for c in channels if c.mem_tile_port.tile_col == col]
        return matches[0] if matches else channels[fallback_index]

    # L2 (memtile) buffers: one rotation resident, ping/pong.
    # IFM: one window on MT0 (broadcast over AIE columns). Two Locations means
    # the memtile ping-pongs between them, so the DMA filling rotation t+1
    # overlaps the cores consuming rotation t.
    ifm_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(IFM_MT, 0, 0x00000),
            tensor_expr.Location(IFM_MT, 0, 0x10000),
        ],
        shape=[win],
        type=dtype,
    )
    # WTS: full filter, resident on MT2 (broadcast over AIE rows). Moved once
    # and kept live, so it is single-buffered and has NO temporal iterations.
    # It shares MT2 with the OFM at a disjoint address: the OFM ping/pong takes
    # [0x00000,0x00800) and [0x10000,0x10800), so 0x20000 is clear. The row
    # channels sit on MT3 and reach MT2 as their neighbour.
    wts_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(WTS_MT, 0, 0x20000)],
        shape=[K],
        type=wts.getDType(),
    )
    # OFM: one rotation's N_CORES*T block on MT2, viewed as [COLS, ROWS, T] so
    # each core writes its own [T] slot. Ping/pong like the IFM.
    ofm_l2 = tensor_expr.TensorVar.make(
        locations=[
            tensor_expr.Location(OFM_MT, 0, 0x00000),
            tensor_expr.Location(OFM_MT, 0, 0x10000),
        ],
        shape=[COLS, ROWS, T],
        type=ofm.getDType(),
    )

    # IFM and OFM are refilled/drained once per rotation. This rotation count
    # MUST match the kernel-call count below, otherwise the DMA deadlocks. WTS
    # is resident and deliberately excluded.
    ifm_l2.setTemporalIterations(n_rotations)
    ofm_l2.setTemporalIterations(n_rotations)

    # L1 (per-core) buffers
    ifm_l1 = tensor_expr.TensorVar.make(
        [IFM_L1_LEN], dtype, tensor_expr.BufferingStrategy.DoubleBuffered
    )
    wts_l1 = tensor_expr.TensorVar.make(
        [K], wts.getDType(), tensor_expr.BufferingStrategy.SingleBuffered
    )
    ofm_l1 = tensor_expr.TensorVar.make(
        [T], ofm.getDType(), tensor_expr.BufferingStrategy.DoubleBuffered
    )

    # L3 -> L2 IFM: overlapping windows, one per rotation.
    # offset 0, stride N_CORES*T, wrap n_rotations. With `auto_pad: true` in the
    # op YAML the compiler pads the IFM's L3 allocation to an even 4104 elements
    # (l3_extend_end), so the last window ends exactly at L_in.
    ifm_src = (
        ifm.getTensorVar()
        .Reshape([L_in])
        .Tile(index=0, stride=block, tileLen=win, numTiles=n_rotations)
    )
    stamp.set_l3_to_l2_transfer(_pick(l3_l2, IFM_MT, 0), ifm_src, ifm_l2)

    # The whole (padded) WTS is transferred once and reused.
    stamp.set_l3_to_l2_transfer(
        _pick(l3_l2, WTS_MT, 2), wts.getTensorVar().Reshape([K]), wts_l2
    )

    # ---- L2 -> L1 IFM: one overlapping window per column. ----
    # The L2 buffer now holds exactly ONE rotation's window, so the rotation axis
    # is supplied by the L2 buffer rotation itself and no longer appears here.
    # Column c reads the ROWS*T + halo elements at offset c*(ROWS*T) within that
    # window, broadcast to its ROWS cores; each core keeps the whole window and
    # the kernel selects its row slice.
    ifm_l1_write = ifm_l1.Slice(0, 0, IFM_L1_READ)
    for col, channel in enumerate(l2_l1_cols):
        ifm_per_column = ifm_l2.Slice(0, col * (ROWS * T), IFM_L1_READ)
        stamp.set_l2_to_l1_transfer(channel, ifm_per_column, [ifm_l1_write] * ROWS)

    # L2 -> L1 WTS: full filter broadcast to every core, resident.
    # Re-armed once every n_rotations kernel calls (ratio = calls_per_wts).
    for channel in l2_l1_rows:
        stamp.set_l2_to_l1_transfer(channel, wts_l2, [wts_l1] * COLS, calls_per_wts)

    # kernel: core (c,r) computes its T-element OFM tile.
    stamp.set_kernel_arguments([ifm_l1, wts_l1, ofm_l1])
    stamp.set_kernel_function_name("conv1d_kernel")
    stamp.set_kernel_impl(Path("custom_conv1d.cpp"))
    stamp.set_kernel_params([T, K, calls_per_wts])
    stamp.set_kernel_nb_calls(n_rotations)

    # L1 -> L2 gather: each core -> its own [T] slot
    # ofm_l2 is shaped [COLS, ROWS, T] and holds one rotation, so core (c,r)
    # simply owns slot (c,r); the temporal axis is the buffer rotation.
    ofm_per_core = ofm_l2.Reshape([N_CORES, T]).TileTo(index=0, numTiles=N_CORES)
    for col, col_channel in enumerate(l1_l2):
        stamp.set_l1_to_l2_transfer(
            col_channel,
            [ofm_l1] * ROWS,
            [ofm_per_core[col * ROWS + row] for row in range(ROWS)],
        )

    # L2 -> DDR drain: one N_CORES*T block per rotation.
    stamp.set_l2_to_l3_transfer(
        _pick(l2_l3, OFM_MT, 0),
        ofm_l2.Reshape([block]),
        ofm.getTensorVar().Reshape([L_out]).TileBy(index=0, tileLen=block),
    )
