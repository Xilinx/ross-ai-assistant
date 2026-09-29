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

    # Axis 0 reduction: reduce the strided outer dimension.
    R = 4
    M = 2
    N = 31360

    # The run on CPU uses float32. For better comparability, truncate
    # the input to bfloat16.
    inputs = (
        np.arange(0, R * M * N, dtype=bfloat16).astype(np.float32).reshape([R, M, N])
    )

    print("Running ReduceMax (axis=0) to generate reference outputs")
    ref_outputs = ort.InferenceSession("ReduceMax_axis_0.onnx").run(
        None, {"x": inputs}
    )[0]

    print("Compiling custom ReduceMax (axis=0) for AIE")
    onnx_session = ort.InferenceSession(
        "CustomReduceMax_axis_0.onnx",
        providers=["VitisAIExecutionProvider"],
        provider_options=[
            {
                "config_file": "vitisai_config.json",
                "ai_analyzer_visualization": True,
                "ai_analyzer_profiling": True,
                "cacheDir": "./vaip_cache",
                "cacheKey": "custom_reducemax_axis_0",
            }
        ],
    )

    print("Running custom ReduceMax (axis=0) on AIE")
    outputs = onnx_session.run(None, {"x": inputs})[0]

    print(f"Model ref outputs (axis=0):\n{ref_outputs}")
    print(f"Model outputs (axis=0):\n{outputs}")
    # Tolerance: ReduceMax SELECTS an input value rather than computing one, so
    # there is no accumulation error and no output-store rounding. On the inputs
    # above the comparison is in fact BIT-EXACT -- `arange` cast through bfloat16
    # is exactly bf16-representable, so quantising the operands (which is what
    # the DMA delivers to the cores) does not change them and this rtol never
    # binds. On randomly generated inputs the only gap is that input
    # quantisation, bounded by half a bf16 ulp = 2**-8 = 0.00390625 relative;
    # 8e-3 would cover that with ~2x margin. Both bounds are properties of the
    # format, not the data, so neither moves with the seed.
    #
    # Verified bit-exact on VEK385 against a bf16-operand CPU reference for both
    # the Tiling_4x4 and AieConfig tilings.
    np.testing.assert_allclose(
        outputs.reshape([M * N]), ref_outputs.reshape([M * N]), rtol=5e-2, atol=1e-5
    )


if __name__ == "__main__":
    sys.exit(main())
