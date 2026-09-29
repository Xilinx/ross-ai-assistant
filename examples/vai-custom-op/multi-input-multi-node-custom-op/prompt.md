<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
-->
# Multi-Input, Multi-Node — Nine Ops and Three Inputs In, One Custom Op Out

Quick-start prompt for the **custom op** skill (`/vai-custom-op`): hand it a
nine-node graph with **three real inputs**, plus a
`vitisai_config.json`, and let it generate everything else — kernel, tiling, op
config, the rewritten model, and the `custom_ops` section of the compile config.

The only two files you are provided with are in [`input/`](input/).

## The prompt

```
/vai-custom-op --model input/model.onnx \
    --vitisai-config input/vitisai_config.json \
    --ops Gemm_0,gemm_output_reshape,Slice_4,Exp,Mul_2,Slice_1,Mul_1,Add_1,Concat_5:myop

Create a single custom op that fuses every op between the tensors gemm_input
and concat_output, i.e. the Gemm plus the whole
Reshape / Slice / Exp / Mul / Add / Concat chain that follows it.

```

## Notes

- **`--ops <nodes>:myop`** — the node list is a single cluster, so all nine
  nodes collapse into one custom op. The spec matches on **node name**.
- **Three inputs, two buffers** — this is the point of the example. A custom op
  sees at most two core-tile buffers (IFM and WTS), so the three inputs cannot
  each get their own. The prompt tells the skill to solve it in the tiling, not
  by adding `Concat`/`Reshape` nodes to the graph, which would cost a DDR round
  trip.
- **`scale` fans out to both `Mul_1` and `Mul_2`**, so whatever packing the
  skill picks has to serve both branches.
- **Device**: `ve2`, from the supplied `vitisai_config.json`. The skill does not
  change the model-wide knobs; it only appends the `custom_ops` section.
- **Board**: the prompt does not name a board, so it works unchanged whatever
  your setup is. Export `BOARDHOST` and `BOARD_USER`, plus either
  `BOARD_PASSWORD` or `BOARD_KEY`, and the skill picks them up when it calls
  `run_on_board.py`.
  Without a reachable board the skill will warn that correctness rests on
  x86sim alone and that the Phase 5 optimization loop degrades to static
  metrics.
