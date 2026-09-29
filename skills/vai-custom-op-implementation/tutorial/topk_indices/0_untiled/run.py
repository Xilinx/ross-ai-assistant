#!/usr/bin/env python

# Copyright (C) 2025 - 2026 Advanced Micro Devices, Inc. All rights reserved.

import os
import sys

import numpy as np
import onnx
import onnxruntime as ort
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)


def bitcast_to_msb(arr, dtype):
    return (arr.astype(np.int32) << np.int32(16)).view(dtype)


def get_shape_from_onnx_input(onnx_input):
    shape = []
    for dim in onnx_input.type.tensor_type.shape.dim:
        if dim is not None:
            shape.append(dim.dim_value)
        else:
            shape.append(dim.dim_parameter)
    return shape


def main() -> int:
    # Generate ONNX file from .onnxtxt if needed
    onnx_model_path = "topk_indices.onnx"
    onnxtxt_path = "topk_indices.onnxtxt"

    print(f"Generating {onnx_model_path} from {onnxtxt_path}...")
    model = onnx.load(onnxtxt_path, load_external_data=False)
    onnx.save(model, onnx_model_path)

    register_dynamic_custom_ops_to_onnxruntime(
        [
            vaiml_custom_op_schema(
                domain="cse", name="topk_indices", nb_inputs=1, nb_outputs=1
            )
        ]
    )

    graph = model.graph
    K = 1024

    inputs = {}
    for inp in graph.input:
        shape = get_shape_from_onnx_input(inp)
        input_tensor = np.arange(0.0, np.prod(shape), 1.0, dtype=np.float32).reshape(
            *shape
        )
        input_tensor = bitcast_to_msb(input_tensor, np.float32)
        inputs[inp.name] = input_tensor

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
                "cacheKey": os.getcwd() + "/cache",
            }
        ],
    )

    # Run inference
    outputs = onnx_session.run(None, inputs)
    print(f"Model outputs shape: {outputs[0].shape}")
    print(f"Model outputs:\n{outputs[0]}")

    # Verify results
    last_index = inputs["values"].shape[0] - 1
    expected_indices = np.arange(last_index, last_index - K, -1)

    assert len(outputs) == 1  # one output tensor produced
    assert len(outputs[0]) == K, f"Expected {K} elements, got {len(outputs[0])}"

    actual_indices = outputs[0].view(np.int32) >> np.int32(16)
    np.testing.assert_array_equal(actual_indices, expected_indices)

    print("\nTest passed! TopK indices untiled operation works correctly.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
