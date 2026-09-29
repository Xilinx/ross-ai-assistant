#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""
Patch Model for VAIP Compatibility

Adds shape/data_type attributes to com.amd.quark custom ops and fixes
zero-point dtype issues for VAIP (Vitis AI Execution Provider).

VAIML (direct compiler) does NOT need these patches. This is required only
when using the model through ONNXRuntime with the VitisAI EP (VAIP path).

Usage:
    python patch_model_for_vaip.py --input model.onnx --output model_patched.onnx
    python patch_model_for_vaip.py --input model.onnxtxt --output model_patched.onnx --fix-zp-dtype
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path
from typing import Any

import numpy as np
import onnx
from onnx import helper

# Ops that are safe to retag from com.microsoft to the default ONNX domain.
# They share the standard (x, scale, zero_point) → y signature.
_MS_RETAG_OPS = frozenset({"QuantizeLinear", "DequantizeLinear"})

# Minimum ONNX opset that supports QuantizeLinear / DequantizeLinear
# with the 3-input (x, scale, zp) signature used by these nodes.
_MIN_OPSET_FOR_QDQ = 13

# ---------------------------------------------------------------------------
# ONNX dtype helpers
# ---------------------------------------------------------------------------

_ONNX_DTYPE_TO_STR = {
    1: "float32",
    2: "uint8",
    3: "int8",
    5: "float16",
    6: "int32",
    7: "int64",
    10: "float16",
    11: "double",
    12: "uint32",
    13: "uint64",
    16: "bfloat16",
}

_ONNX_DTYPE_MAP = {
    "float16": 10,
    "bfloat16": 16,
}


def onnx_elem_type_to_str(elem_type: int) -> str:
    return _ONNX_DTYPE_TO_STR.get(elem_type, "float32")


def float_to_int16_bits(value: float, dtype: str) -> int:
    """Convert a Python float to its 16-bit representation."""
    if dtype == "float16":
        return int(np.float16(value).view(np.uint16))
    # bfloat16: truncate the lower 16 bits of float32
    return int(struct.unpack("<I", struct.pack("<f", value))[0] >> 16)


# ---------------------------------------------------------------------------
# Zero-point dtype fixing
# ---------------------------------------------------------------------------


def cast_tensor_to_target(tensor: Any, target_dtype: int, to_type: str) -> None:
    """Cast a scalar float TensorProto in-place to the target 16-bit type."""
    if tensor.data_type != 1:  # Not FLOAT
        return
    float_vals = list(tensor.float_data)
    if not float_vals and tensor.raw_data:
        n = len(tensor.raw_data) // 4
        float_vals = list(struct.unpack(f"<{n}f", tensor.raw_data))
        tensor.ClearField("raw_data")
    else:
        tensor.ClearField("float_data")
    tensor.data_type = target_dtype
    for v in float_vals:
        tensor.int32_data.append(float_to_int16_bits(v, to_type))


def fix_quark_zp_dtype(model: onnx.ModelProto, target_type: str = "bfloat16") -> bool:
    """Cast float32 zero-points of quark ops to bfloat16/float16.

    Required because the ONNX text parser cannot represent bf16/fp16 literals,
    so models from .onnxtxt have fp32 zero-points that must be cast.

    Returns True if any changes were made.
    """
    quark_domain = "com.amd.quark"
    target_dtype = _ONNX_DTYPE_MAP[target_type]

    # Collect zero-point tensor names from quark ops (3rd input, index 2)
    zp_names: set[str] = set()
    for node in model.graph.node:
        if node.domain == quark_domain and len(node.input) > 2:
            zp_names.add(node.input[2])

    if not zp_names:
        return False

    modified = False

    # Cast initializers
    for init in model.graph.initializer:
        if init.name in zp_names and init.data_type == 1:  # FLOAT
            cast_tensor_to_target(init, target_dtype, target_type)
            modified = True

    # Cast Constant nodes producing zero-points
    for node in model.graph.node:
        if node.op_type == "Constant" and node.output[0] in zp_names:
            for attr in node.attribute:
                if attr.name == "value" and attr.t.data_type == 1:
                    cast_tensor_to_target(attr.t, target_dtype, target_type)
                    modified = True

    return modified


# ---------------------------------------------------------------------------
# com.microsoft domain retagging
# ---------------------------------------------------------------------------


def retag_ms_qdq_to_onnx(model: onnx.ModelProto) -> bool:
    """Retag com.microsoft QuantizeLinear/DequantizeLinear to the default ONNX domain.

    ONNX shape inference ignores custom-domain ops, so com.microsoft Q/DQ nodes
    create shape-inference gaps.  These ops use the standard (x, scale, zp) → y
    signature and are safe to move to the default domain when the model already
    imports an opset >= 13.

    Guardrails
    ----------
    * Only QuantizeLinear and DequantizeLinear are retagged (see _MS_RETAG_OPS).
    * Each candidate node is validated to have exactly 2 or 3 inputs (the
      standard Q/DQ signature); nodes with unexpected arity are skipped.
    * The default opset version must be >= 13; if not, the function is a no-op
      (a warning is printed).
    * After retagging, the com.microsoft opset import is removed if no nodes
      in that domain remain.

    Returns True if any nodes were retagged.
    """
    ms_domain = "com.microsoft"

    # Check that the default opset is high enough for Q/DQ
    default_version = 0
    for opset in model.opset_import:
        if opset.domain in ("", "ai.onnx"):
            default_version = max(default_version, opset.version)
    if default_version < _MIN_OPSET_FOR_QDQ:
        print(
            f"    WARNING: default opset {default_version} < {_MIN_OPSET_FOR_QDQ}; "
            f"skipping com.microsoft retagging"
        )
        return False

    retagged = 0
    skipped = 0
    for node in model.graph.node:
        if node.domain != ms_domain:
            continue
        if node.op_type not in _MS_RETAG_OPS:
            continue
        # Validate input arity (standard Q/DQ: 2 or 3 inputs)
        n_inputs = len(node.input)
        if n_inputs < 2 or n_inputs > 3:
            skipped += 1
            print(
                f"    WARNING: skipping {node.op_type} {node.name!r} "
                f"(unexpected {n_inputs} inputs)"
            )
            continue
        node.domain = ""
        retagged += 1

    if skipped:
        print(f"    Skipped {skipped} com.microsoft node(s) with non-standard arity")

    # Remove com.microsoft opset import if no nodes remain in that domain
    if retagged:
        remaining_ms = any(n.domain == ms_domain for n in model.graph.node)
        if not remaining_ms:
            for i, opset in enumerate(model.opset_import):
                if opset.domain == ms_domain:
                    del model.opset_import[i]
                    break

    return retagged > 0


# ---------------------------------------------------------------------------
# Shape collection and propagation
# ---------------------------------------------------------------------------


def collect_tensor_info(model: onnx.ModelProto) -> dict[str, tuple[list[int], int]]:
    """Collect tensor name → (shape, elem_type) from all model sources."""
    tensor_info: dict[str, tuple[list[int], int]] = {}

    for inp in model.graph.input:
        tt = inp.type.tensor_type
        if tt.HasField("shape"):
            shape = [d.dim_value for d in tt.shape.dim]
            if shape:
                tensor_info[inp.name] = (shape, tt.elem_type)

    for init in model.graph.initializer:
        tensor_info[init.name] = (list(init.dims), init.data_type)

    for vi in model.graph.value_info:
        tt = vi.type.tensor_type
        if tt.HasField("shape"):
            shape = [d.dim_value for d in tt.shape.dim]
            if shape:
                tensor_info[vi.name] = (shape, tt.elem_type)

    for out in model.graph.output:
        tt = out.type.tensor_type
        if tt.HasField("shape"):
            shape = [d.dim_value for d in tt.shape.dim]
            if shape:
                tensor_info[out.name] = (shape, tt.elem_type)

    return tensor_info


def propagate_quark_shapes(
    model: onnx.ModelProto, tensor_info: dict[str, tuple[list[int], int]]
) -> bool:
    """Propagate shapes through quark ops (pointwise: output = first input shape)."""
    quark_domain = "com.amd.quark"
    changed = False

    for node in model.graph.node:
        if node.domain != quark_domain:
            continue
        first_input = node.input[0] if node.input else ""
        if first_input not in tensor_info or not tensor_info[first_input][0]:
            continue

        out_shape = tensor_info[first_input][0]
        if "Dequantize" in node.op_type:
            scale_name = node.input[1] if len(node.input) > 1 else ""
            out_dtype = tensor_info.get(scale_name, ([], 1))[1]
        else:
            zp_name = node.input[2] if len(node.input) > 2 else ""
            out_dtype = tensor_info.get(zp_name, ([], 1))[1]

        for out_name in node.output:
            if out_name not in tensor_info:
                tensor_info[out_name] = (out_shape, out_dtype)
                changed = True

    return changed


def inject_quark_value_info(
    model: onnx.ModelProto, tensor_info: dict[str, tuple[list[int], int]]
) -> None:
    """Add value_info entries for quark op outputs that have known shapes."""
    quark_domain = "com.amd.quark"
    existing = {vi.name for vi in model.graph.value_info}

    for node in model.graph.node:
        if node.domain != quark_domain:
            continue
        for out_name in node.output:
            if out_name in tensor_info and out_name not in existing:
                out_shape, out_dtype = tensor_info[out_name]
                if out_shape:
                    model.graph.value_info.append(
                        helper.make_tensor_value_info(out_name, out_dtype, out_shape)
                    )
                    existing.add(out_name)


# ---------------------------------------------------------------------------
# Main patching logic
# ---------------------------------------------------------------------------


def patch_model_for_vaip(
    input_path: str,
    output_path: str,
    fix_zp: bool = True,
    add_shapes: bool = True,
    target_fp_type: str = "bfloat16",
    retag_ms: bool = True,
) -> str:
    """Patch model for VAIP compatibility.

    Args:
        input_path: Path to input ONNX model
        output_path: Path for patched output model
        fix_zp: Fix zero-point dtypes (fp32 → bf16/fp16)
        add_shapes: Add shape/data_type attributes to custom ops
        target_fp_type: Target floating-point type ("bfloat16" or "float16")
        retag_ms: Retag com.microsoft Q/DQ nodes to the default ONNX domain

    Returns:
        Path to the patched model (may be unchanged if no patching needed)
    """
    print(f"Loading model: {input_path}")
    if input_path.endswith(".onnxtxt"):
        from onnx import parser as onnx_parser

        with open(input_path) as f:
            model = onnx_parser.parse_model(f.read())
    else:
        model = onnx.load(input_path)

    quark_domain = "com.amd.quark"
    has_quark = any(opset.domain == quark_domain for opset in model.opset_import)

    has_ms = any(opset.domain == "com.microsoft" for opset in model.opset_import)

    if not has_quark and not has_ms:
        print(
            "  No com.amd.quark or com.microsoft custom ops found — no patching needed"
        )
        onnx.save(model, output_path)
        return output_path

    modified = False

    # Step 0: Retag com.microsoft Q/DQ to default domain (before shape inference)
    if retag_ms and has_ms:
        ms_qdq_count = sum(
            1
            for n in model.graph.node
            if n.domain == "com.microsoft" and n.op_type in _MS_RETAG_OPS
        )
        if ms_qdq_count:
            print(
                f"  Retagging {ms_qdq_count} com.microsoft Q/DQ node(s) to default ONNX domain..."
            )
            if retag_ms_qdq_to_onnx(model):
                modified = True
                print("    Retagging successful")
            else:
                print("    No nodes retagged (check warnings above)")

    # Step 1: Fix zero-point dtypes
    if fix_zp and has_quark:
        print(f"  Fixing zero-point dtypes → {target_fp_type}...")
        if fix_quark_zp_dtype(model, target_fp_type):
            modified = True
            print("    Zero-points cast successfully")
        else:
            print("    No zero-points to fix")

    # Step 2: Run ONNX shape inference
    print("  Running shape inference...")
    try:
        model = onnx.shape_inference.infer_shapes(model, data_prop=True)
    except Exception as e:
        print(f"    Shape inference partial: {e}")

    # Step 3: Iteratively propagate through quark ops
    print("  Propagating shapes through custom ops...")
    iterations = 0
    for _ in range(10):
        tensor_info = collect_tensor_info(model)
        if not propagate_quark_shapes(model, tensor_info):
            break
        inject_quark_value_info(model, tensor_info)
        iterations += 1
        try:
            model = onnx.shape_inference.infer_shapes(model, data_prop=True)
        except Exception:
            pass
    print(f"    Propagation converged in {iterations} iteration(s)")

    # Step 4: Add shape/data_type attributes
    if add_shapes and has_quark:
        print("  Adding shape/data_type attributes to custom ops...")
        tensor_info = collect_tensor_info(model)
        n_patched = 0
        for node in model.graph.node:
            if node.domain != quark_domain:
                continue

            # Skip if already has shape attribute
            existing_attrs = {a.name for a in node.attribute}
            if "shape" in existing_attrs:
                continue

            first_input = node.input[0] if node.input else ""
            out_shape = (
                tensor_info[first_input][0] if first_input in tensor_info else []
            )

            if "Dequantize" in node.op_type:
                scale_name = node.input[1] if len(node.input) > 1 else ""
                out_dtype = tensor_info.get(scale_name, ([], 1))[1]
            else:
                zp_name = node.input[2] if len(node.input) > 2 else ""
                out_dtype = tensor_info.get(zp_name, ([], 1))[1]

            node.attribute.append(helper.make_attribute("shape", out_shape))
            node.attribute.append(
                helper.make_attribute("data_type", onnx_elem_type_to_str(out_dtype))
            )
            n_patched += 1
            modified = True

        print(f"    Patched {n_patched} custom op node(s)")

    # Save
    if modified:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        onnx.save(model, output_path)
        print(f"  Saved patched model: {output_path}")
    else:
        onnx.save(model, output_path)
        print(f"  No modifications needed, saved copy: {output_path}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Patch ONNX model for VAIP compatibility"
    )
    parser.add_argument("--input", "-i", required=True, help="Input model path")
    parser.add_argument(
        "--output", "-o", required=True, help="Output patched model path"
    )
    parser.add_argument(
        "--no-fix-zp",
        dest="fix_zp",
        action="store_false",
        default=True,
        help="Skip zero-point dtype fixing",
    )
    parser.add_argument(
        "--no-shapes",
        dest="add_shapes",
        action="store_false",
        default=True,
        help="Skip adding shape/data_type attributes",
    )
    parser.add_argument(
        "--fp-type",
        choices=["bfloat16", "float16"],
        default="bfloat16",
        help="Target floating-point type for zero-points (default: bfloat16)",
    )
    parser.add_argument(
        "--no-retag-ms",
        dest="retag_ms",
        action="store_false",
        default=True,
        help="Skip retagging com.microsoft Q/DQ ops to the default ONNX domain",
    )
    args = parser.parse_args()

    patch_model_for_vaip(
        args.input,
        args.output,
        fix_zp=args.fix_zp,
        add_shapes=args.add_shapes,
        target_fp_type=args.fp_type,
        retag_ms=args.retag_ms,
    )


if __name__ == "__main__":
    main()
