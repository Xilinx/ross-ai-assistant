# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import sys

import numpy as np
import onnxruntime as ort
from ml_dtypes import bfloat16
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)


def main() -> int:
    register_dynamic_custom_ops_to_onnxruntime(
        [
            vaiml_custom_op_schema(
                domain="mydomain", name="myreducemax", nb_inputs=1, nb_outputs=1
            )
        ]
    )

    # Axis 2 reduction: reduce the contiguous inner-most dimension.
    G = 31360
    M = 2
    K = 4

    # The run on CPU uses float32. For better comparability, truncate
    # the input to bfloat16.
    inputs = (
        np.arange(0, G * M * K, dtype=bfloat16).astype(np.float32).reshape([G, M, K])
    )

    print("Running ReduceMax (axis=2) to generate reference outputs")
    ref_outputs = ort.InferenceSession("ReduceMax_axis_2.onnx").run(
        None, {"x": inputs}
    )[0]

    print("Compiling custom ReduceMax (axis=2) for AIE")
    onnx_session = ort.InferenceSession(
        "CustomReduceMax_axis_2.onnx",
        providers=["VitisAIExecutionProvider"],
        provider_options=[
            {
                "config_file": "vitisai_config.json",
                "ai_analyzer_visualization": True,
                "ai_analyzer_profiling": True,
                "cacheDir": "./vaip_cache",
                "cacheKey": "custom_reducemax_axis_2",
            }
        ],
    )

    print("Running custom ReduceMax (axis=2) on AIE")
    outputs = onnx_session.run(None, {"x": inputs})[0]

    print(f"Model ref outputs (axis=2):\n{ref_outputs}")
    print(f"Model outputs (axis=2):\n{outputs}")
    np.testing.assert_allclose(
        outputs.reshape([G * M]), ref_outputs.reshape([G * M]), rtol=5e-2, atol=1e-5
    )


if __name__ == "__main__":
    sys.exit(main())
