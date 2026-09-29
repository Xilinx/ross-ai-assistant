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
                domain="mydomain", name="mygemm_bias", nb_inputs=2, nb_outputs=1
            )
        ]
    )

    onnx_session = ort.InferenceSession(
        "gemm_bias.onnx",
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

    # C[N, M] = W[N, K] . A[K, M] + bias[N]
    N, K, M = 128, 64, 1024
    rng = np.random.default_rng(0)
    A = rng.standard_normal((K, M), dtype=np.float32)
    W = rng.standard_normal((N, K), dtype=np.float32)
    bias = rng.standard_normal(N, dtype=np.float32)

    # Fold bias into the weights: W_aug[:, 0:K] = W, W_aug[:, K] = bias,
    # W_aug[:, K+1:] = 0 (pad the reduction dim up to the next 8-tile edge).
    K_pad = K + 8
    W_aug = np.zeros((N, K_pad), dtype=np.float32)
    W_aug[:, :K] = W
    W_aug[:, K] = bias

    outputs = onnx_session.run(None, {"A": A, "W_aug": W_aug})

    # Reference: bf16-quantize operands, accumulate in fp32, quantize result to
    # bf16 -- matching the kernel's datapath. The folded-bias tile contributes
    # bias exactly the way the extra mmul step does on the device.
    def to_bf16(x: np.ndarray) -> np.ndarray:
        u = x.astype(np.float32).view(np.uint32)
        u = (u + 0x8000) & 0xFFFF0000
        return u.view(np.float32)

    A_bf = to_bf16(A)
    W_bf = to_bf16(W)
    bias_bf = to_bf16(bias)
    expected = to_bf16((W_bf @ A_bf + bias_bf[:, None]).astype(np.float32))

    print(f"Model output shape: {outputs[0].shape}")
    np.testing.assert_allclose(outputs[0], expected, rtol=3e-2, atol=3e-2)
    print("SUCCESS: GEMM+bias output matches the bf16 reference within tolerance")

    return 0


if __name__ == "__main__":
    sys.exit(main())
