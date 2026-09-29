# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import sys

import numpy as np
import onnxruntime as ort


def main() -> int:
    # No custom-op schema registration is needed here: spacetodepth.onnx is an
    # unmodified model containing a stock ONNX SpaceToDepth, which onnxruntime
    # already knows. The custom op is created by the compiler from the PDLL
    # pattern in custom_op_spacetodepth/custom_spacetodepth_match.pdll.
    onnx_model_path = "spacetodepth.onnx"

    print("Creating onnx session using VitisAIExecutionProvider...")
    onnx_session = ort.InferenceSession(
        onnx_model_path,
        providers=["VitisAIExecutionProvider"],
        provider_options=[
            {
                "config_file": "vitisai_config.json",
                "ai_analyzer_visualization": True,
                "ai_analyzer_profiling": True,
                "cacheDir": "cache",
            }
        ],
    )

    # Generate int8 input: values from -128 to 127, tiled to fill the shape
    num_elements = 1 * 3 * 320 * 320
    input_data = np.arange(-128, 128, dtype=np.int8)
    input_data = np.tile(input_data, (num_elements // 256) + 1)[:num_elements]
    input_data = input_data.reshape(1, 3, 320, 320)

    outputs = onnx_session.run(None, {"input": input_data})

    # Compute expected SpaceToDepth output on CPU
    blocksize = 2
    n, c, h, w = input_data.shape
    expected = input_data.reshape(
        n, c, h // blocksize, blocksize, w // blocksize, blocksize
    )
    expected = expected.transpose(0, 3, 5, 1, 2, 4)
    expected = expected.reshape(
        n, c * blocksize * blocksize, h // blocksize, w // blocksize
    )

    print(f"Output shape: {outputs[0].shape}, expected: {expected.shape}")
    print(f"Output dtype: {outputs[0].dtype}, expected: {expected.dtype}")

    # With int8, SpaceToDepth is pure rearrangement - expect exact match
    mismatch = np.sum(outputs[0] != expected)
    total = expected.size
    print(f"Mismatches: {mismatch} / {total}")

    np.testing.assert_array_equal(
        outputs[0], expected, err_msg="Output mismatch between custom op and reference!"
    )
    print("Success!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
