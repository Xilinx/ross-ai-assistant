#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import argparse
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
from PIL import Image

cwd = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, f"{cwd}/../")


ref_onnxtxt = "op_gridsample"
cop_onnxtxt = "op_mygridsample"
op_name = "GridSample"
cop_name = ["myGridSample2D"]
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


def load_input_images(path, reps=1):
    files = sorted(f for f in os.listdir(path) if f.lower().endswith(".png"))
    arrs = [np.array(Image.open(os.path.join(path, f))) for f in files]
    img = 2 * ((np.stack(arrs, axis=0).astype(np.float32) / 256) - 0.5)
    return np.repeat(np.expand_dims(img, axis=0), reps, axis=0)


def save_images_nc(images, prefix="output"):
    N = images.shape[0]
    C = images.shape[1]

    for n in range(N):
        for c in range(C):
            img = images[n, c]
            img_uint8 = ((img + 1) * 127.5).clip(0, 255).astype(np.uint8)

            if img_uint8.ndim == 2:
                pil_img = Image.fromarray(img_uint8, mode="L")
            else:
                raise ValueError("Expected each image as (H,W). Got something else.")

            filename = f"{prefix}_{n}_{c}.png"
            pil_img.save(filename)


def main():
    parser = argparse.ArgumentParser(description="GridSample 4x4 Custom Op Test")
    parser.add_argument(
        "--ref-only",
        action="store_true",
        help="Only run reference model, skip custom op compilation",
    )
    args = parser.parse_args()

    input_image_path = f"{cwd}/images_input/"
    output_image_path = f"{cwd}/images_output_temp/"
    os.makedirs(output_image_path, exist_ok=True)

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

    align_corners = 1  # if you change, you need to change onnxtxt attributes too

    ref_session = ort.InferenceSession(ref_onnx)
    ref_session.get_inputs()[0].shape
    grid_shape = ref_session.get_inputs()[1].shape
    ref_inputs = {
        "X": load_input_images(input_image_path, reps=grid_shape[0]),
        "grid": np.load(
            f"{cwd}/images_input/grid_input_align_corners_{align_corners}.npy"
        ),
    }
    ref_outputs = ref_session.run(None, ref_inputs)[0]
    save_images_nc(ref_outputs, f"{output_image_path}/outputs_ref")
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
                "cacheKey": f"{output_folder}/",
            }
        ],
    )

    save_images_nc(ref_outputs, f"{output_image_path}/outputs_ref")

    print("\nRunning custom op inference...")
    outputs = onnx_session.run(None, ref_inputs)[0]

    save_images_nc(outputs, f"{output_image_path}/outputs_cop")

    print(f"\nCustom op output shape: {outputs.shape}")
    print(f"Custom op output range: [{outputs.min():.4f}, {outputs.max():.4f}]")

    # =========================================================================
    # Compare results
    # =========================================================================
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

    print("\n Input images under: ./images_input")
    print("\n Output images under: ./images_output_temp\n")

    # looking at %2 error, because of complex calculations
    assert stat_out["cossim"] > 0.98
    assert stat_out["medAE"] < 0.02


if __name__ == "__main__":
    main()
