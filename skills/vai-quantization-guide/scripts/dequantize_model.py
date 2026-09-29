#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""
Dequantize Model Utility

Strips quantization from an ONNX model to produce a non-quantized (float32)
version suitable for re-quantization.

Supports:
- Standard QDQ (QuantizeLinear/DequantizeLinear)
- Extended QDQ (com.amd.quark::ExtendedQuantizeLinear/ExtendedDequantizeLinear)
- BFP/MX6 quantization nodes

Usage:
    python dequantize_model.py --input model_quantized.onnx --output model_float.onnx
    python dequantize_model.py --input model_quantized.onnxtxt --output model_float.onnx --keep-weights
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, numpy_helper


def load_model(path: str) -> onnx.ModelProto:
    """Load ONNX model from .onnx or .onnxtxt file."""
    if path.endswith(".onnxtxt"):
        from onnx import parser as onnx_parser

        with open(path) as f:
            return onnx_parser.parse_model(f.read())
    return onnx.load(path)


def save_model(model: onnx.ModelProto, path: str) -> None:
    """Save ONNX model to .onnx or .onnxtxt file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if path.endswith(".onnxtxt"):
        from onnx import printer as onnx_printer

        with open(path, "w") as f:
            f.write(onnx_printer.to_text(model))
    else:
        onnx.save(model, path)


def get_initializer_map(model: onnx.ModelProto) -> dict[str, TensorProto]:
    """Build name → initializer mapping."""
    return {init.name: init for init in model.graph.initializer}


def is_qdq_node(node: onnx.NodeProto) -> bool:
    """Check if node is a standard Q or DQ."""
    return node.op_type in ("QuantizeLinear", "DequantizeLinear") and node.domain == ""


def is_eqdq_node(node: onnx.NodeProto) -> bool:
    """Check if node is an Extended Q or DQ (com.amd.quark domain)."""
    return node.domain == "com.amd.quark" and node.op_type in (
        "ExtendedQuantizeLinear",
        "ExtendedDequantizeLinear",
    )


def is_bfp_node(node: onnx.NodeProto) -> bool:
    """Check if node is a BFP/MX6 fused QDQ."""
    if node.domain == "com.amd.quark" and node.op_type == "BFPQuantizeDequantize":
        return True
    return False


def find_qdq_pairs(
    model: onnx.ModelProto,
) -> list[tuple[onnx.NodeProto, onnx.NodeProto]]:
    """Find Q→DQ pairs in the graph."""
    # Build output→node mapping
    output_to_node = {}
    for node in model.graph.node:
        for out in node.output:
            output_to_node[out] = node

    pairs = []
    for node in model.graph.node:
        if node.op_type == "DequantizeLinear" and node.domain == "":
            # Check if input comes from a QuantizeLinear
            q_input = node.input[0]
            if q_input in output_to_node:
                q_node = output_to_node[q_input]
                if q_node.op_type == "QuantizeLinear" and q_node.domain == "":
                    pairs.append((q_node, node))
    return pairs


def find_eqdq_pairs(
    model: onnx.ModelProto,
) -> list[tuple[onnx.NodeProto, onnx.NodeProto]]:
    """Find ExtendedQ→ExtendedDQ pairs."""
    output_to_node = {}
    for node in model.graph.node:
        for out in node.output:
            output_to_node[out] = node

    pairs = []
    for node in model.graph.node:
        if (
            node.op_type == "ExtendedDequantizeLinear"
            and node.domain == "com.amd.quark"
        ):
            eq_input = node.input[0]
            if eq_input in output_to_node:
                eq_node = output_to_node[eq_input]
                if (
                    eq_node.op_type == "ExtendedQuantizeLinear"
                    and eq_node.domain == "com.amd.quark"
                ):
                    pairs.append((eq_node, node))
    return pairs


def bypass_fused_qdq(model: onnx.ModelProto, node: onnx.NodeProto) -> None:
    """Bypass a single fused BFP QDQ node (BFPQuantizeDequantize).

    The fused node takes a float input and produces a float output (it
    quantizes and immediately dequantizes internally). To remove it, rewire
    consumers of its output to use its input directly.
    """
    original_input = node.input[0]
    fused_output = node.output[0]

    for n in model.graph.node:
        for i, inp in enumerate(n.input):
            if inp == fused_output:
                n.input[i] = original_input

    for out in model.graph.output:
        if out.name == fused_output:
            out.name = original_input


def dequantize_weight(
    weight_tensor: TensorProto, scale_tensor: TensorProto, zp_tensor: TensorProto | None
) -> TensorProto:
    """Dequantize an int8 weight back to float32."""
    weight = numpy_helper.to_array(weight_tensor).astype(np.float32)
    scale = numpy_helper.to_array(scale_tensor).astype(np.float32)
    zp = (
        numpy_helper.to_array(zp_tensor).astype(np.float32)
        if zp_tensor
        else np.zeros_like(scale)
    )

    # Per-tensor or per-channel dequantization
    if scale.ndim == 0 or scale.size == 1:
        float_weight = (weight - zp.item()) * scale.item()
    else:
        # Per-channel: scale and zp have shape matching one axis of weight
        # Reshape for broadcasting
        axis = 0  # typically quantized along output channels
        shape = [1] * weight.ndim
        shape[axis] = -1
        float_weight = (weight - zp.reshape(shape)) * scale.reshape(shape)

    return numpy_helper.from_array(float_weight, name=weight_tensor.name)


def bypass_node_pair(
    model: onnx.ModelProto,
    q_node: onnx.NodeProto,
    dq_node: onnx.NodeProto,
    init_map: dict[str, TensorProto],
    keep_weights: bool,
) -> set[str]:
    """Bypass a Q→DQ pair by rewiring the graph. Returns names of nodes to remove."""
    nodes_to_remove = set()
    # The original input to Q
    original_input = q_node.input[0]
    # The output of DQ that consumers use
    dq_output = dq_node.output[0]

    # Check if this is a weight (input is an initializer)
    if original_input in init_map and keep_weights:
        # Dequantize the weight
        weight_init = init_map[original_input]
        scale_name = q_node.input[1] if len(q_node.input) > 1 else ""
        zp_name = q_node.input[2] if len(q_node.input) > 2 else ""
        scale_init = init_map.get(scale_name)
        zp_init = init_map.get(zp_name)

        if scale_init and weight_init.data_type in (
            TensorProto.INT8,
            TensorProto.UINT8,
        ):
            new_weight = dequantize_weight(weight_init, scale_init, zp_init)
            # Replace the initializer
            for i, init in enumerate(model.graph.initializer):
                if init.name == original_input:
                    model.graph.initializer[i].CopyFrom(new_weight)
                    break

    # Rewire: all consumers of dq_output now use original_input
    for node in model.graph.node:
        for i, inp in enumerate(node.input):
            if inp == dq_output:
                node.input[i] = original_input

    # Also update graph outputs
    for out in model.graph.output:
        if out.name == dq_output:
            out.name = original_input

    nodes_to_remove.add(id(q_node))
    nodes_to_remove.add(id(dq_node))
    return nodes_to_remove


def remove_standalone_boundary_qdq(model: onnx.ModelProto) -> None:
    """Remove standalone Q or DQ at model boundaries."""
    input_names = {inp.name for inp in model.graph.input}
    output_names = {out.name for out in model.graph.output}

    nodes_to_remove = set()

    for node in model.graph.node:
        # Standalone DQ at input boundary
        if (
            node.op_type == "DequantizeLinear"
            and node.domain == ""
            and node.input[0] in input_names
        ):
            # Check if this DQ's input Q doesn't exist (standalone DQ)
            has_q_producer = any(
                n.op_type == "QuantizeLinear" and node.input[0] in n.output
                for n in model.graph.node
            )
            if not has_q_producer:
                # Rewire consumers
                dq_output = node.output[0]
                original = node.input[0]
                for n in model.graph.node:
                    for i, inp in enumerate(n.input):
                        if inp == dq_output:
                            n.input[i] = original
                nodes_to_remove.add(id(node))

        # Standalone Q at output boundary
        if (
            node.op_type == "QuantizeLinear"
            and node.domain == ""
            and node.output[0] in output_names
        ):
            has_dq_consumer = any(
                n.op_type == "DequantizeLinear" and node.output[0] in n.input
                for n in model.graph.node
            )
            if not has_dq_consumer:
                q_output = node.output[0]
                original = node.input[0]
                for out in model.graph.output:
                    if out.name == q_output:
                        out.name = original
                nodes_to_remove.add(id(node))

    # Remove marked nodes
    kept_nodes = [n for n in model.graph.node if id(n) not in nodes_to_remove]
    del model.graph.node[:]
    model.graph.node.extend(kept_nodes)


def update_io_types(model: onnx.ModelProto) -> None:
    """Update all model I/O to float32."""
    for inp in model.graph.input:
        tt = inp.type.tensor_type
        if tt.elem_type in (TensorProto.INT8, TensorProto.UINT8, TensorProto.BFLOAT16):
            tt.elem_type = TensorProto.FLOAT

    for out in model.graph.output:
        tt = out.type.tensor_type
        if tt.elem_type in (TensorProto.INT8, TensorProto.UINT8, TensorProto.BFLOAT16):
            tt.elem_type = TensorProto.FLOAT


def remove_unused_initializers(model: onnx.ModelProto) -> None:
    """Remove initializers not referenced by any node."""
    used_names = set()
    for node in model.graph.node:
        for inp in node.input:
            used_names.add(inp)

    kept_inits = [init for init in model.graph.initializer if init.name in used_names]
    del model.graph.initializer[:]
    model.graph.initializer.extend(kept_inits)


def clean_opset_imports(model: onnx.ModelProto) -> None:
    """Remove opset imports for domains with no remaining nodes."""
    used_domains = {""}  # default domain always kept
    for node in model.graph.node:
        used_domains.add(node.domain)

    kept_opsets = [
        opset for opset in model.opset_import if opset.domain in used_domains
    ]
    del model.opset_import[:]
    model.opset_import.extend(kept_opsets)


def dequantize_model(
    input_path: str, output_path: str, keep_weights: bool = True
) -> None:
    """Main dequantization routine."""
    print(f"Loading model: {input_path}")
    model = load_model(input_path)
    init_map = get_initializer_map(model)

    # Count quantization nodes
    n_qdq = sum(1 for n in model.graph.node if is_qdq_node(n))
    n_eqdq = sum(1 for n in model.graph.node if is_eqdq_node(n))
    n_bfp = sum(1 for n in model.graph.node if is_bfp_node(n))
    print(f"  Found: {n_qdq} QDQ nodes, {n_eqdq} EQDQ nodes, {n_bfp} BFP nodes")

    nodes_to_remove = set()

    # Process standard QDQ pairs
    qdq_pairs = find_qdq_pairs(model)
    print(f"  Processing {len(qdq_pairs)} QDQ pairs...")
    for q_node, dq_node in qdq_pairs:
        removed = bypass_node_pair(model, q_node, dq_node, init_map, keep_weights)
        nodes_to_remove.update(removed)

    # Process Extended QDQ pairs
    eqdq_pairs = find_eqdq_pairs(model)
    print(f"  Processing {len(eqdq_pairs)} EQDQ pairs...")
    for eq_node, edq_node in eqdq_pairs:
        removed = bypass_node_pair(model, eq_node, edq_node, init_map, keep_weights)
        nodes_to_remove.update(removed)

    # Process single fused BFP QDQ nodes (BFPQuantizeDequantize)
    fused_bfp = [n for n in model.graph.node if is_bfp_node(n)]
    print(f"  Processing {len(fused_bfp)} fused BFP QDQ nodes...")
    for node in fused_bfp:
        bypass_fused_qdq(model, node)
        nodes_to_remove.add(id(node))

    # Remove processed nodes
    kept_nodes = [n for n in model.graph.node if id(n) not in nodes_to_remove]
    del model.graph.node[:]
    model.graph.node.extend(kept_nodes)

    # Handle boundary nodes
    remove_standalone_boundary_qdq(model)

    # Update I/O types
    update_io_types(model)

    # Cleanup
    remove_unused_initializers(model)
    clean_opset_imports(model)

    # Validate
    try:
        onnx.checker.check_model(model)
        print("  Model validation: PASSED")
    except Exception as e:
        print(f"  Model validation WARNING: {e}")

    # Save
    save_model(model, output_path)
    print(f"  Saved dequantized model: {output_path}")

    # Summary
    remaining_q = sum(
        1
        for n in model.graph.node
        if is_qdq_node(n) or is_eqdq_node(n) or is_bfp_node(n)
    )
    print(f"  Remaining quantization nodes: {remaining_q}")
    print(f"  Total nodes: {len(model.graph.node)}")


def main():
    parser = argparse.ArgumentParser(description="Dequantize an ONNX model")
    parser.add_argument(
        "--input", "-i", required=True, help="Input quantized model path"
    )
    parser.add_argument("--output", "-o", required=True, help="Output float model path")
    parser.add_argument(
        "--keep-weights",
        action="store_true",
        default=True,
        help="Preserve dequantized weight values (default: True)",
    )
    parser.add_argument(
        "--no-keep-weights",
        dest="keep_weights",
        action="store_false",
        help="Do not dequantize weights (just remove Q/DQ nodes)",
    )
    args = parser.parse_args()

    dequantize_model(args.input, args.output, args.keep_weights)


if __name__ == "__main__":
    main()
