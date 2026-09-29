# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import sys

import numpy as np
import onnxruntime as ort
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)

# Keep in sync with custom_op_addmul/custom_addmul_tiling.py.
SHAPE = (2, 1024, 32)


def main() -> int:
    # The board run passes vitisai_config_board.json (runner_type "hw"); the
    # default vitisai_config.json runs on x86sim.
    config_file = sys.argv[1] if len(sys.argv) > 1 else "vitisai_config.json"

    register_dynamic_custom_ops_to_onnxruntime(
        [
            vaiml_custom_op_schema(
                domain="mydomain", name="myaddmul", nb_inputs=3, nb_outputs=1
            )
        ]
    )

    onnx_session = ort.InferenceSession(
        "addmul.onnx",
        providers=["VitisAIExecutionProvider"],
        provider_options=[
            {
                "config_file": config_file,
                "ai_analyzer_visualization": True,
                "ai_analyzer_profiling": True,
                "cache_dir": "cache",
            }
        ],
    )

    rng = np.random.default_rng(42)
    a = (rng.random(SHAPE) * 100.0).astype(np.float32)
    b = (rng.random(SHAPE) * 100.0).astype(np.float32)
    c = (rng.random(SHAPE) * 100.0).astype(np.float32)
    outputs = onnx_session.run(None, {"A": a, "B": b, "C": c})

    # phase 0 computes A + B into the L3 scratch buffer, phase 1 multiplies it
    # by C.
    expected = (a + b) * c
    got = np.asarray(outputs[0]).reshape(expected.shape)
    np.testing.assert_allclose(got, expected, rtol=1.6e-2, atol=2e-1)
    print("addmul (L3 scratch): PASS")

    return 0


if __name__ == "__main__":
    sys.exit(main())
