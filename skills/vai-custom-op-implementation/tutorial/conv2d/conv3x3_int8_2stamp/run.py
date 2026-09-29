# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import sys

import numpy as np
import onnx
import onnxruntime as ort
from onnx import numpy_helper
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)

MODEL = "conv3x3_int8.onnx"

C = 160  # input channels
C_OUT = 192  # output channels (ROWS*CG_OFM*OFM_ITER*8 -- divides cleanly)
H = W = 160  # spatial
CG = 20  # C / 8 input channel groups
CG_OUT = 24  # C_OUT / 8 output channel groups
V = 8  # 8-channel inner block
KH = KW = 3
HALO = 1  # H halo per side, baked into the arranged IFM
W_PAD_L = 1  # W left halo (native streams sub-tiles at col*8 - 1)
W_PAD_R = 7  # W right halo (last 16-wide sub-tile overruns to col 167)
H_PAD = H + 2 * HALO  # 162
W_PAD = W + W_PAD_L + W_PAD_R  # 168

# Packed weight-blob geometry (see ci_doc_*/reference_implementation.py).
BIAS_FOLD = 64  # sb/so = 2^-2 => accumulator seeded with b_q * 2^6
SHIFT_OUT = 8  # si*sw/so = 2^-8 => single >>8 requant


def arrange_ifm(x: np.ndarray) -> np.ndarray:
    """NCHW [C,H,W] -> the HCWN_C8-with-halo bytes the tiling reads from DDR.

    The graph input is a [1, C, H_PAD, W_PAD] container whose RAW ravel is
    [H_PAD, CG, W_PAD, V], so the tiling's Reshape reinterprets it in place.
    """
    xr = x.reshape(CG, V, H, W).transpose(2, 0, 3, 1)  # [H, CG, W, V]
    t = np.zeros((H_PAD, CG, W_PAD, V), dtype=np.int8)
    t[HALO : HALO + H, :, W_PAD_L : W_PAD_L + W, :] = xr
    return t.reshape(1, C, H_PAD, W_PAD)


def reference(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """int8 3x3 stride-1 conv reproducing the kernel datapath, in HCWN_C8."""
    xpad = np.zeros((C, H + 2, W + 2), dtype=np.int32)
    xpad[:, 1 : 1 + H, 1 : 1 + W] = x
    acc = np.broadcast_to(b[:, None, None] * BIAS_FOLD, (C_OUT, H, W)).copy()
    for ky in range(KH):
        for kx in range(KW):
            patch = xpad[:, ky : ky + H, kx : kx + W]
            acc += np.einsum("chw,dc->dhw", patch, w[:, :, ky, kx])
    scaled = acc.astype(np.float64) / float(1 << SHIFT_OUT)
    out = np.clip(np.rint(scaled), -128, 127).astype(np.int8)
    return out.reshape(CG_OUT, V, H, W).transpose(2, 0, 3, 1).reshape(1, C_OUT, H, W)


def main() -> int:
    register_dynamic_custom_ops_to_onnxruntime(
        [
            vaiml_custom_op_schema(
                domain="mydomain", name="myconv", nb_inputs=3, nb_outputs=1
            )
        ]
    )

    onnx_session = ort.InferenceSession(
        MODEL,
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
    x = rng.integers(-8, 9, size=(C, H, W), dtype=np.int8)

    model = onnx.load(MODEL)
    inits = {i.name: numpy_helper.to_array(i) for i in model.graph.initializer}
    # Weights are stored in mmul-wave order for the DMA; the reference needs the
    # logical [Cout, Cin, KH, KW] view, which ships alongside as a .npy.
    w = np.load("ci_doc_conv2d_conv3x3_int8_2stamp/w_ref.npy").astype(np.int32)
    # bias ships as the raw int8 bytes of int32(b * BIAS_FOLD), rank 4 (i8
    # constants must be rank 3 or 4), so undo that view here.
    b = inits["myconv_bias"].reshape(-1).view(np.int32) // BIAS_FOLD

    ifm_name = model.graph.input[0].name
    outputs = onnx_session.run(None, {ifm_name: arrange_ifm(x)})

    expected = reference(x.astype(np.int32), w, b)

    print(f"Model output shape: {outputs[0].shape}")
    # The kernel and the NumPy reference round the same accumulator, so they
    # agree exactly except where a value lands on a .5 tie -- allow +/-1 LSB.
    diff = np.abs(outputs[0].astype(np.int32) - expected.astype(np.int32))
    assert diff.max() <= 1, f"max |diff| = {diff.max()} (expected <= 1)"
    print("SUCCESS: int8 conv output matches the reference within +/-1 LSB")

    return 0


if __name__ == "__main__":
    sys.exit(main())
