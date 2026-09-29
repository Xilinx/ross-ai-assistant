# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import sys

import numpy as np
import onnxruntime as ort
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)

# Keep in sync with concat4.onnxtxt and
# custom_op_concat4/custom_concat4_tiling.py.
ROWS_OUT = 8  # shared outer dimension of the four inputs
INNER = 128  # innermost dimension of each input
NVARS = 4  # number of inputs concatenated
N = ROWS_OUT * INNER  # 1024 elements per input


def reference_output(*vars_: np.ndarray) -> np.ndarray:
    """Golden output: a 4-way concatenation on the innermost dimension, i.e. the
    innermost elements of the four inputs are alternated:

        out[r, NVARS*c + v] = vars_[v][r, c]

    So the output row r is A[r,0], B[r,0], C[r,0], D[r,0], A[r,1], ... All four
    inputs share the same [ROWS_OUT, INNER] shape; the result is
    [ROWS_OUT, NVARS*INNER]. Computed in bfloat16 to match the kernel."""
    stacked = np.stack(
        [v.reshape(ROWS_OUT, INNER) for v in vars_], axis=-1
    )  # [ROWS_OUT, INNER, NVARS]
    return stacked.reshape(ROWS_OUT, NVARS * INNER).astype(np.float32)


def main() -> int:
    register_dynamic_custom_ops_to_onnxruntime(
        [
            vaiml_custom_op_schema(
                domain="mydomain", name="concat4", nb_inputs=NVARS, nb_outputs=1
            )
        ]
    )

    onnx_session = ort.InferenceSession(
        "concat4.onnx",
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

    a = np.random.rand(ROWS_OUT, INNER).astype(np.float32)
    b = np.random.rand(ROWS_OUT, INNER).astype(np.float32)
    c = np.random.rand(ROWS_OUT, INNER).astype(np.float32)
    d = np.random.rand(ROWS_OUT, INNER).astype(np.float32)
    outputs = onnx_session.run(None, {"A": a, "B": b, "C": c, "D": d})

    print(f"Model outputs shape: {np.asarray(outputs[0]).shape}")

    expected = reference_output(a, b, c, d)
    got = np.asarray(outputs[0]).reshape(expected.shape)
    np.testing.assert_allclose(got, expected, rtol=1.6e-2, atol=1e-3)
    print("concat4: PASS")

    return 0


if __name__ == "__main__":
    sys.exit(main())
