# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import sys

import numpy as np
import onnxruntime as ort
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)

# Keep in sync with interleave.onnxtxt and
# custom_op_interleave/custom_interleave_tiling.py.
N = 1024
COLS = 4
ROWS = 4
Q = N // COLS  # 256
SEG = Q // ROWS  # 64
NVARS = 4


def reference_output(*vars_: np.ndarray) -> np.ndarray:
    """Golden output: column c gets the c-th quarter of every input; the 4 rows
    of a column split that quarter into SEG-sized segments. Each input is
    flattened to [N] first, so its DDR rank does not matter. Layout is
    (col, row, var, seg)."""
    flat = [v.reshape(N) for v in vars_]
    out = np.zeros((COLS, ROWS, NVARS, SEG), dtype=np.float32)
    for col in range(COLS):
        for row in range(ROWS):
            base = col * Q + row * SEG
            for v, x in enumerate(flat):
                out[col, row, v, :] = x[base : base + SEG]
    return out


def main() -> int:
    register_dynamic_custom_ops_to_onnxruntime(
        [
            vaiml_custom_op_schema(
                domain="mydomain", name="interleave", nb_inputs=NVARS, nb_outputs=1
            )
        ]
    )

    onnx_session = ort.InferenceSession(
        "interleave.onnx",
        providers=["VitisAIExecutionProvider"],
        provider_options=[
            {
                "config_file": "vitisai_config.json",
                "ai_analyzer_visualization": True,
                "ai_analyzer_profiling": True,
                "cache_dir": "cache",
            }
        ],
    )

    a = np.random.rand(N).astype(np.float32)
    b = np.random.rand(2, N // 2).astype(np.float32)
    c = np.random.rand(4, N // 4).astype(np.float32)
    d = np.random.rand(8, N // 8).astype(np.float32)
    outputs = onnx_session.run(None, {"A": a, "B": b, "C": c, "D": d})

    print(f"Model outputs shape: {np.asarray(outputs[0]).shape}")

    expected = reference_output(a, b, c, d)
    got = np.asarray(outputs[0]).reshape(expected.shape)
    np.testing.assert_allclose(got, expected, rtol=1.6e-2, atol=1e-3)
    print("interleave: PASS")

    return 0


if __name__ == "__main__":
    sys.exit(main())
