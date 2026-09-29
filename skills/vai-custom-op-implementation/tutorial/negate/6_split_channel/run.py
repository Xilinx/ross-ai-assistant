# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import sys

import numpy as np
import onnxruntime as ort
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)

# Keep in sync with make_onnx.py and
# custom_op_negate_split/custom_negate_split_tiling.py.
N = 1024


def main() -> int:
    # The board run passes vitisai_config_board.json (runner_type "hw"); the
    # default vitisai_config.json runs on x86sim.
    config_file = sys.argv[1] if len(sys.argv) > 1 else "vitisai_config.json"

    register_dynamic_custom_ops_to_onnxruntime(
        [
            vaiml_custom_op_schema(
                domain="mydomain", name="negate_split", nb_inputs=1, nb_outputs=1
            )
        ]
    )

    onnx_session = ort.InferenceSession(
        "negate_split.onnx",
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

    a = np.random.rand(N).astype(np.float32)
    outputs = onnx_session.run(None, {"A": a})

    print(f"Model outputs shape: {np.asarray(outputs[0]).shape}")

    expected = -a
    got = np.asarray(outputs[0]).reshape(expected.shape)
    np.testing.assert_allclose(got, expected, rtol=1.6e-2, atol=1e-3)
    print("negate_split: PASS")

    return 0


if __name__ == "__main__":
    sys.exit(main())
