# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import sys

import numpy as np
import onnxruntime as ort
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)


def main() -> int:
    register_dynamic_custom_ops_to_onnxruntime(
        [
            vaiml_custom_op_schema(
                domain="mydomain", name="mymul", nb_inputs=2, nb_outputs=1
            )
        ]
    )

    onnx_session = ort.InferenceSession(
        "mul.onnx",
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
    a = np.random.rand(1024).astype(np.float32)
    b = np.random.rand(1024).astype(np.float32)
    outputs = onnx_session.run(None, {"a": a, "b": b})

    print(f"Model outputs:\n{outputs}")
    np.testing.assert_allclose(outputs[0], a * b, rtol=1.6e-2, atol=1e-3)

    return 0


if __name__ == "__main__":
    sys.exit(main())
