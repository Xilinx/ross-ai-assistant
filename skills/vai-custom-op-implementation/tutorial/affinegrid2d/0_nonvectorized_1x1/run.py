#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnxruntime_custom_ops import (
    register_dynamic_custom_ops_to_onnxruntime,
    vaiml_custom_op_schema,
)

cwd = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, f"{cwd}/../")


ref_onnxtxt = "op_affinegrid"
cop_onnxtxt = "op_myaffinegrid"
op_name = "AffineGrid2D"
cop_name = ["myAffineGrid2D"]
cop_shape = [2, 1]  # in,out

register_dynamic_custom_ops_to_onnxruntime(
    [
        vaiml_custom_op_schema(
            domain="mydomain",
            name=f"{cop_name[i]}",
            nb_inputs=cop_shape[0],
            nb_outputs=cop_shape[1],
        )
        for i in range(len(cop_name))
    ]
)


def cossim(a, b):
    a = np.array(a).flatten()
    b = np.array(b).flatten()
    return (np.abs(np.dot(a, b)) + 1e-8) / (
        (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-8
    )


def medAE(a, b):
    a = np.array(a).flatten()
    b = np.array(b).flatten()
    return np.median(np.abs(a - b))


def onnxtxt_to_onnx(onnxtxt_file, onnx_file) -> tuple[onnx.ModelProto, Path]:
    model = onnx.parser.parse_model(Path(onnxtxt_file).read_text(encoding="utf-8"))
    model = onnx.shape_inference.infer_shapes(model)

    # Add name to GridSample node for identification
    i = 0
    for n in model.graph.node:
        if op_name in n.op_type:
            n.name = f"{op_name}_{i}"
            i += 1

    with open(onnx_file, "wb") as f:
        onnx.save(model, f)
    return model, onnx_file


def onnx_filter_inputs(onnx_session, ref_inputs):
    inputs = {}
    for model_input in onnx_session.get_inputs():
        name = model_input.name
        inputs[name] = ref_inputs[name].astype(np.float32)
    return inputs


def main():
    parser = argparse.ArgumentParser(description="GridSample 4x4 Custom Op Test")
    parser.add_argument(
        "--ref-only",
        action="store_true",
        help="Only run reference model, skip custom op compilation",
    )
    args = parser.parse_args()

    # Reference model (standard GridSample)
    ref_onnx = f"{cwd}/{ref_onnxtxt}.onnx"

    # Custom op model
    cop_onnx = f"{cwd}/{cop_onnxtxt}.onnx"

    # Convert text models to binary if needed
    if os.path.exists(f"{cwd}/{ref_onnxtxt}.onnxtxt"):
        print(f"Converting {ref_onnxtxt}.onnxtxt to ONNX...")
        onnxtxt_to_onnx(f"{cwd}/{ref_onnxtxt}.onnxtxt", ref_onnx)

    if os.path.exists(f"{cwd}/{cop_onnxtxt}.onnxtxt"):
        print(f"Converting {cop_onnxtxt}.onnxtxt to ONNX...")
        onnxtxt_to_onnx(f"{cwd}/{cop_onnxtxt}.onnxtxt", cop_onnx)

    # =========================================================================
    # Run reference model on CPU
    # =========================================================================
    print("\n" + "=" * 60)
    print("Running reference ONNX model (CPU) to generate expected outputs")
    print("=" * 60)

    # Load theta inputs from JSON file
    with open(f"{cwd}/theta_inputs.json") as f:
        theta_dict = json.load(f)
    theta_values = [np.array(v, dtype=np.float32) for v in theta_dict.values()]
    theta_input = np.stack(theta_values, axis=0)

    # Get output shape from ref_session. Store `size` as float32 (the IFM dtype
    # the custom op expects); the standard AffineGrid reference below needs int64,
    # so it is cast only for that single reference run.
    size_input = np.array([theta_input.shape[0], 1, 100, 100]).astype(np.float32)

    # Contruct input and run ref_session
    ref_inputs = {
        "theta": theta_input,
        "size": size_input,
    }
    ref_session = ort.InferenceSession(f"{cwd}/{ref_onnxtxt}.onnx")
    ref_run_inputs = {**ref_inputs, "size": size_input.astype(np.int64)}
    ref_outputs = ref_session.run(None, ref_run_inputs)[0]

    print(f"\nReference output shape: {ref_outputs.shape}")
    print(f"Reference output range: [{ref_outputs.min():.4f}, {ref_outputs.max():.4f}]")

    if args.ref_only:
        print("\n--ref-only specified, skipping custom op compilation")
        return

    # =========================================================================
    # Compile and run custom op
    # =========================================================================
    print("\n" + "=" * 60)
    print("Compiling custom op for AIE")
    print("=" * 60)

    output_folder = "outputs"

    # Custom op schema already registered at module level
    onnx_session = ort.InferenceSession(
        cop_onnx,
        providers=["VitisAIExecutionProvider"],
        provider_options=[
            {
                "config_file": f"{cwd}/vitisai_config.json",
                "ai_analyzer_visualization": True,
                "ai_analyzer_profiling": True,
                "cacheDir": f"{cwd}/",
                "cacheKey": f"{output_folder}/output",
            }
        ],
    )

    # Use same inputs as reference if available
    onnx_inputs = onnx_filter_inputs(onnx_session, ref_inputs)

    np.save(f"{output_folder}/inputs_cop.npy", onnx_inputs, allow_pickle=True)
    np.save(f"{output_folder}/inputs_ref.npy", ref_inputs, allow_pickle=True)

    print("\nRunning custom op inference...")
    outputs = onnx_session.run(None, onnx_inputs)[0]

    np.save(f"{output_folder}/outputs_cop.npy", outputs, allow_pickle=False)

    print(f"\nCustom op output shape: {outputs.shape}")
    print(f"Custom op output range: [{outputs.min():.4f}, {outputs.max():.4f}]")

    # =========================================================================
    # Compare results
    # =========================================================================
    np.save(f"{output_folder}/outputs_ref.npy", ref_outputs, allow_pickle=False)
    print("\n" + "=" * 60)
    print("Comparison Results")
    print("=" * 60)

    stat_out = {}
    stat_out["cossim"] = cossim(outputs, ref_outputs)
    stat_out["medAE"] = medAE(outputs, ref_outputs)

    print("\nStatistics:")
    for k, v in stat_out.items():
        print(f"  {k}: {v}")

    # Detailed comparison
    if len(outputs) > 0 and len(ref_outputs) > 0:
        diff = np.abs(outputs - ref_outputs)
        print("\nDetailed comparison:")
        print(f"  Max absolute error: {diff.max():.6f}")
        print(f"  Mean absolute error: {diff.mean():.6f}")
        print(f"  Median absolute error: {np.median(diff):.6f}")

        # Check if results are acceptable
        if stat_out.get("cossim", 0) > 0.98:
            print("\n✓ Results are within acceptable tolerance!")
        else:
            print("\n⚠ Results may have significant differences")

    print("\n Outputs under: ./outputs\n")

    # Tolerance. The kernel used to compute the y row of the affine map from
    # theta_copy[4,5,6] instead of [3,4,5]: theta is [2,3] = 6 elements, so row 1
    # is {3,4,5}, and index 6 read past the matrix into the oversized
    # theta_copy[8] padding. That put the y component out by maxabs ~5.16 while x
    # was fine (~0.027). Both the Tiling_1x1 and AieConfig variants shared the
    # kernel, so both were wrong identically -- an old-vs-new bit-exactness check
    # could not see it. Fixed; the gap dropped to 0.0278 and cossim to 0.999988.
    #
    # An equivalent allclose bound is rtol=1.6e-2, atol=5e-2. Note atol has to
    # carry it: the grid is t0*x + t1*y + t2, a SUM of bf16 terms whose rounding
    # scales with |theta| and |x,y| (both O(1)), NOT with |result| -- and the
    # result cancels to exactly 0 at grid centres, so a pure-rtol bound is
    # unsatisfiable there. Measured error is flat (~0.01-0.028) across every
    # |ref| decade including |ref|~0. Validated over 30 seeds: worst ratio 0.40.
    # looking at %2 error, because of complex calculations
    assert stat_out["cossim"] > 0.98
    assert stat_out["medAE"] < 0.02


if __name__ == "__main__":
    main()
