# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import sys

import numpy as np
import onnxruntime as ort
import torch
import torch.nn.functional as F
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)


def main() -> int:
    register_dynamic_custom_ops_to_onnxruntime(
        [
            vaiml_custom_op_schema(
                domain="mydomain", name="myconv1d", nb_inputs=2, nb_outputs=1
            )
        ]
    )

    onnx_session = ort.InferenceSession(
        "conv1d.onnx",
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
    rng = np.random.default_rng(0)
    in_ = rng.standard_normal(4103, dtype=np.float32)
    kernel = rng.standard_normal(8, dtype=np.float32)
    outputs = onnx_session.run(None, {"in_": in_, "kernel": kernel})

    in_bf32 = torch.tensor(in_).to(torch.bfloat16).to(torch.float32).view(1, 1, -1)
    wts_bf32 = torch.tensor(kernel).to(torch.bfloat16).to(torch.float32).view(1, 1, -1)
    expected = (
        F.conv1d(in_bf32, wts_bf32)
        .view(-1)
        .to(torch.bfloat16)
        .to(torch.float32)
        .numpy()
    )

    print(f"Model outputs:\n{outputs}")
    np.testing.assert_allclose(outputs[0], expected, rtol=1.6e-2, atol=1e-3)

    return 0


if __name__ == "__main__":
    sys.exit(main())
