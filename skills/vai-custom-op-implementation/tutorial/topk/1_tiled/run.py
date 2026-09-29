# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import os
import sys

import numpy as np
import onnx
import onnxruntime as ort
from ml_dtypes import bfloat16
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)


def main() -> int:
    # Generate ONNX file from .onnxtxt if needed
    onnx_model_path = "topk.onnx"
    onnxtxt_path = "topk.onnxtxt"

    print(f"Generating {onnx_model_path} from {onnxtxt_path}...")
    model = onnx.load(onnxtxt_path, load_external_data=False)
    onnx.save(model, onnx_model_path)

    register_dynamic_custom_ops_to_onnxruntime(
        [
            vaiml_custom_op_schema(
                domain="mydomain", name="mytopk", nb_inputs=1, nb_outputs=1
            )
        ]
    )

    print("Creating onnx session using VitisAIExecutionProvider...")
    onnx_session = ort.InferenceSession(
        onnx_model_path,
        providers=["VitisAIExecutionProvider"],
        provider_options=[
            {
                "config_file": "vitisai_config.json",
                "ai_analyzer_visualization": True,
                "ai_analyzer_profiling": True,
                "cacheDir": "./",
                "cacheKey": os.getcwd() + "/model.cache",
            }
        ],
    )

    rng = np.random.default_rng(seed=42)
    # The model will operate on bfloat16 data. Do the same here for the
    # reference output calculation.
    test_input = (
        rng.integers(-200, 200, size=(48, 512)).astype(bfloat16).astype(np.float32)
    )

    # Run inference
    outputs = onnx_session.run(None, {"in": test_input})
    print(f"Model outputs shape: {outputs[0].shape}")
    print(f"Model outputs:\n{outputs[0]}")

    # Verify the output by computing expected top-k values
    # For each batch, sort and take the top 16 elements (in descending order)
    expected = np.sort(test_input, axis=1)[:, -16:][:, ::-1]

    # Check if the outputs match (with some tolerance for bfloat16 conversion)
    np.testing.assert_allclose(outputs[0], expected, rtol=1.6e-2, atol=1e-5)
    print("\nTest passed! TopK tiled operation works correctly.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
