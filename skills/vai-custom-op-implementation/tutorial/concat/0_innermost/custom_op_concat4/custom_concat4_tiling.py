# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Generic-overlay (AieConfig) tiling for the innermost concat4 op (VE2/aie2p).

Four inputs A, B, C, D share the same [ROWS_OUT, INNER] shape (equal element
count N). They are staged into TWO L2 buffers, one per memory tile:

    ab_l2 [HALF, N] on the first  input memtile column   (row 0 = A, row 1 = B)
    cd_l2 [HALF, N] on the second input memtile column   (row 0 = C, row 1 = D)

The split into two tiles (rather than one merged [NVARS, N] buffer) is kept
deliberately: there are only 4 L3->L2 shim channels for the op's inputs, and
they arrive in two pairs, each pair landing on a DIFFERENT memtile column. So
each L2 buffer is fed by exactly two L3->L2 channels on its own memtile.

The two memtile columns are DISCOVERED dynamically from the L3->L2 channel set
(grouped by destination memtile column) rather than hardcoded: the STX
(rai_1x4x4) overlay lands these channels on memtile columns 0 and 3, whereas the
VE2 (aie2_6x4x4) overlay lands them on columns 0 and 2. Hardcoding 0/3 makes the
tiling fail to compile for VE2 with "need HALF L3->L2 channels into each of
memtile columns 0 and 3"; discovering them keeps the same two-buffer topology
working on both overlays.

Because each L2->L1 channel exposes a single memtile source port, one read per
channel comes from one buffer. So the op has TWO kernel inputs, each fed from its
own tile over a DIFFERENT broadcast channel family:

  * ab_l2 -> COLUMN-broadcast channels (memtile column 1, reaches tile 0). The
    L2 view is [HALF, COLS, ROWS, SEG]; column c gets [HALF, ROWS, SEG] = [HALF,
    Q], the SAME data broadcast to all ROWS cores of the column. Core (c, r)
    later selects its r-th SEG-segment.
  * cd_l2 -> ROW-broadcast channels (memtile column 3, reaches tile 2). The L2
    view is [HALF, COLS, ROWS, SEG]; row r gets [HALF, COLS, SEG] = [HALF, Q]
    (the data for every core of the row -- one SEG-segment per column), broadcast
    to all COLS cores of the row. Core (c, r) later selects its c-th SEG-segment.

Both give core (c, r) the input elements at global positions n = c*Q + r*SEG + j.
The kernel alternates them into a contiguous [NVARS*SEG] tile
(out[NVARS*j + v] = input_v[n]); the 16 tiles gather back to DDR as the
flattened [ROWS_OUT, NVARS*INNER] result. Because global output element
NVARS*n + v is input v's element n, and core k = col*ROWS+row owns a contiguous
run of input positions, its tile lands at the contiguous global output range
[NVARS*SEG*k, NVARS*SEG*(k+1)).

A kernel (not a pure DMA copy) is required because the element word size is
bf16 (16 bits, below 32): the innermost alternation cannot be expressed as a DMA
access pattern and must be done in the core. This is the AieConfig path (tiling
annotated tensor_expr.AieConfig), mandatory because the legacy ml_adf path caps
at 2 operands and this op has 4 inputs.
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

    info_refs = list(opInterface)
    num_operands = opInterface.getNumOperands()  # 4 inputs
    inputs = info_refs[:num_operands]
    out = info_refs[num_operands]

    NVARS = num_operands
    N = _numel(inputs[0].getShape())
    for info in inputs:
        assert _numel(info.getShape()) == N, "all inputs must have equal numel"
    Q = N // COLS  # quarter per column
    SEG = Q // ROWS  # per-core segment
    assert Q * COLS == N and SEG * ROWS == Q, "N must be divisible by COLS*ROWS"
    tile_size = NVARS * SEG  # per-core output tile

    dtype = inputs[0].getDType()

    l3_l2 = stamp.get_l3_to_l2_channels()
    l2_l1 = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.COLUMNS)
    l1_l2 = stamp.get_l1_to_l2_channels()
    l2_l3 = stamp.get_l2_to_l3_channels()

    HALF = NVARS // 2  # inputs per memory tile

    # Group the L3->L2 channels by their destination memtile column: an input can
    # only be staged into a tile that one of its shim channels physically reaches.
    # Each L2 buffer must sit on the SAME memtile its feeding L3->L2 channels land
    # on. We DISCOVER the two memtile columns from the channel set instead of
    # hardcoding them, so the same two-buffer topology compiles on both STX
    # (cols 0 and 3) and VE2 (cols 0 and 2).
    l3_by_col: dict[int, list] = {}
    for ch in l3_l2:
        l3_by_col.setdefault(ch.mem_tile_port.tile_col, []).append(ch)

    # Keep only memtile columns that can host a full HALF-input buffer, in
    # ascending column order. The first hosts ab_l2 (A,B), the second cd_l2 (C,D).
    usable_cols = sorted(col for col, chs in l3_by_col.items() if len(chs) >= HALF)
    assert len(usable_cols) >= 2, (
        "need HALF L3->L2 channels into each of two distinct memtile columns; "
        f"got channels on columns {dict((c, len(v)) for c, v in l3_by_col.items())}"
    )
    ab_col, cd_col = usable_cols[0], usable_cols[1]
    ab_chs = l3_by_col[ab_col]
    cd_chs = l3_by_col[cd_col]

    # ---- inputs: DDR -> TWO L2 buffers, one per memory tile. ab_l2 on the first
    # discovered memtile column holds A, B; cd_l2 on the second holds C, D. Each
    # memtile receives at most two inputs (one HALF-buffer), fed by that column's
    # two L3->L2 s2mm channels. -------------------------------------------------
    ab_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(ab_col, 0, 0x0)],
        shape=[HALF, N],
        type=dtype,
    )
    cd_l2 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(cd_col, 0, 0x0)],
        shape=[HALF, N],
        type=dtype,
    )
    for i in range(HALF):
        stamp.set_l3_to_l2_transfer(
            ab_chs[i], inputs[i].getTensorVar().Reshape([N]), ab_l2.Slice(0, i, 1)
        )
        stamp.set_l3_to_l2_transfer(
            cd_chs[i],
            inputs[HALF + i].getTensorVar().Reshape([N]),
            cd_l2.Slice(0, i, 1),
        )

    # ---- L2 -> L1: TWO kernel inputs, each read from its own tile over its own
    # broadcast channel family (distinct source ports, so nothing is reused).
    #
    # ab (column-broadcast): view ab_l2 as [HALF, COLS, ROWS, SEG]; column c gets
    # ab_view[:, c] = [HALF, ROWS, SEG] broadcast to the column's ROWS cores. The
    # L1 buffer holds all ROWS segments ([HALF, Q]); the kernel picks row r.
    #
    # cd (row-broadcast): view cd_l2 as [HALF, COLS, ROWS, SEG]; row r gets
    # cd_view[:, :, r] = [HALF, COLS, SEG] -- the data for EVERY core of the row,
    # one SEG-segment per column -- broadcast to the row's COLS cores. The L1
    # buffer holds all COLS segments ([HALF, Q]); the kernel picks column c.
    l2_l1_rows = stamp.get_l2_to_l1_channels(broadcast=tensor_expr.broadcast_on.ROWS)
    ab_view = ab_l2.Reshape([HALF, COLS, ROWS, SEG])
    cd_view = cd_l2.Reshape([HALF, COLS, ROWS, SEG])
    # The L1 buffers keep the read's natural 3D shape (a flat [HALF, Q] would need
    # a reshape across the sliced dimension that the DMA cannot express). They are
    # contiguous in L1, so the kernel still reads HALF*Q flat elements.
    #
    # The two input buffers get EXPLICIT, non-overlapping L1 ping/pong addresses.
    # Auto-placement assigns both the same address, which makes their L2->L1 reads
    # collapse onto a single source merge (the second buffer is then left with no
    # producer -- "shared buffer ...merge.out[0] has no outgoing connections").
    # Each buffer is HALF*Q bf16 = 1024 B; 0x2000 spacing leaves ample room.
    ab_l1 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(0x0), tensor_expr.Location(0x2000)],
        shape=[HALF, ROWS, SEG],
        type=dtype,
    )
    cd_l1 = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(0x4000), tensor_expr.Location(0x6000)],
        shape=[HALF, COLS, SEG],
        type=dtype,
    )
    for col, channel in enumerate(l2_l1):
        # ab_view[:, col] = [HALF, ROWS, SEG], broadcast to the column's ROWS cores.
        read_ab = ab_view.Slice(1, col, 1)
        stamp.set_l2_to_l1_transfer(channel, read_ab, [ab_l1] * ROWS)
    for row, channel in enumerate(l2_l1_rows):
        # cd_view[:, :, row] = [HALF, COLS, SEG], broadcast to the row's COLS cores.
        read_cd = cd_view.Slice(2, row, 1)
        stamp.set_l2_to_l1_transfer(channel, read_cd, [cd_l1] * COLS)

    # ---- kernel: alternate this core's A,B (ab_l1) and C,D (cd_l1) segments into
    # a contiguous [NVARS*SEG] output tile (out[NVARS*j + v] = input_v[n]). ----
    out_mk_var = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(0x8000), tensor_expr.Location(0xA000)],
        shape=[tile_size],
        type=out.getDType(),
    )
    stamp.set_kernel_arguments([ab_l1, cd_l1, out_mk_var])
    stamp.set_kernel_function_name("concat4_kernel")
    stamp.set_kernel_impl(Path("custom_concat4.cpp"))
    stamp.set_kernel_params([Q, SEG])
    stamp.set_kernel_nb_calls(1)

    # ---- L1 -> L2 gather: each core -> its own contiguous DDR slot. ----
    ofm_mem_var = tensor_expr.TensorVar.make(
        locations=[tensor_expr.Location(1, 0, 0x0)],
        shape=[COLS, ROWS, tile_size],
        type=out.getDType(),
    )
    ofm_per_core = ofm_mem_var.Reshape([COLS * ROWS, tile_size]).TileTo(
        index=0, numTiles=COLS * ROWS
    )
    for col, col_channel in enumerate(l1_l2):
        l1_src: list[tensor_expr.TensorExpr] = []
        l2_dst: list[tensor_expr.TensorExpr] = []
        for row in range(ROWS):
            l1_src += [out_mk_var]
            l2_dst.append(ofm_per_core[col * ROWS + row])
        stamp.set_l1_to_l2_transfer(col_channel, l1_src, l2_dst)

    # ---- L2 -> DDR drain (emitted LAST so the output external buffer keeps the
    # operand-then-result index). ----
    total = COLS * ROWS * tile_size
    out_dst = out.getTensorVar().Reshape([total])
    stamp.set_l2_to_l3_transfer(l2_l3[0], ofm_mem_var.Reshape([total]), out_dst)
