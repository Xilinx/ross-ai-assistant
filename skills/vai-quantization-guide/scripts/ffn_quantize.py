#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""
FFN Layer Detection and Quantization for ONNX Models

Identifies Feed-Forward Network (FFN) layers — MatMul+Add (weight+bias) patterns
that appear in Transformer/MHA blocks — and applies INT8 quantization to their
weights and biases.

FFN layers are single-layer MLPs: a MatMul with a 2D weight initializer followed
by an Add with a 1D bias initializer. They may be optionally followed by an
activation function (GELU, ReLU, SiLU, etc.).

Usage:
    # List detected FFN layers
    python ffn_quantize.py --input model.onnx --list-only

    # Quantize FFN weights/biases to INT8
    python ffn_quantize.py --input model.onnx --output model_ffn_int8.onnx --precision int8

    # Filter by node name pattern
    python ffn_quantize.py --input model.onnx --output out.onnx --precision int8 \
        --layer-filter "mlp|ffn"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import onnx
from onnx import ModelProto, NodeProto, helper, numpy_helper

# =============================================================================
# Data structures
# =============================================================================


class FFNLayer:
    """A detected FFN layer (MatMul+Add or Gemm with bias)."""

    def __init__(
        self,
        matmul_node: NodeProto,
        bias_node: NodeProto | None,
        weight_name: str,
        bias_name: str,
        weight_shape: list,
        bias_shape: list,
        activation_after: str | None = None,
        intermediate_nodes: list | None = None,
        is_gemm: bool = False,
    ):
        self.matmul_node = matmul_node
        self.bias_node = bias_node
        self.weight_name = weight_name
        self.bias_name = bias_name
        self.weight_shape = weight_shape
        self.bias_shape = bias_shape
        self.activation_after = activation_after
        self.intermediate_nodes = (
            intermediate_nodes if intermediate_nodes is not None else []
        )
        self.is_gemm = is_gemm


# Known activation op types that may follow an FFN layer
ACTIVATION_OPS = frozenset(
    {
        "Gelu",
        "Relu",
        "Sigmoid",
        "Tanh",
        "LeakyRelu",
        "Silu",
        "Elu",
        "HardSigmoid",
        "HardSwish",
        "Mish",
        "Softplus",
        "Softsign",
        "ThresholdedRelu",
    }
)


# =============================================================================
# Graph utilities
# =============================================================================


def build_graph_maps(graph: onnx.GraphProto):
    """Build producer and consumer maps for the graph."""
    producers = {}
    consumers = {}
    for node in graph.node:
        for out in node.output:
            producers[out] = node
        for inp in node.input:
            if inp:
                consumers.setdefault(inp, []).append(node)
    return producers, consumers


def get_initializer_map(graph: onnx.GraphProto) -> dict[str, onnx.TensorProto]:
    """Map initializer name → TensorProto."""
    return {init.name: init for init in graph.initializer}


def get_value_info_map(graph: onnx.GraphProto) -> dict[str, onnx.ValueInfoProto]:
    """Map value_info name → ValueInfoProto."""
    m = {}
    for vi in graph.value_info:
        m[vi.name] = vi
    for vi in graph.input:
        m[vi.name] = vi
    for vi in graph.output:
        m[vi.name] = vi
    return m


def get_tensor_shape(name: str, init_map: dict, vi_map: dict) -> list[int]:
    """Get shape of a tensor by name from initializers or value_info."""
    if name in init_map:
        return list(init_map[name].dims)
    if name in vi_map:
        vi = vi_map[name]
        if vi.type.HasField("tensor_type") and vi.type.tensor_type.HasField("shape"):
            return [d.dim_value for d in vi.type.tensor_type.shape.dim]
    return []


# =============================================================================
# FFN pattern detection
# =============================================================================


def detect_ffn_layers(
    model: ModelProto, layer_filter: str | None = None
) -> list[FFNLayer]:
    """Detect FFN layers (MatMul+Add patterns) in an ONNX model.

    Looks for:
    1. Gemm nodes with a bias input (fused MatMul+Add)
    2. MatMul nodes whose output feeds an Add with a 1D bias initializer,
       possibly through Q/DQ or BFP nodes

    Args:
        model: The ONNX model to analyze.
        layer_filter: Optional regex to filter by node name.

    Returns:
        List of detected FFNLayer instances.
    """
    graph = model.graph
    producers, consumers = build_graph_maps(graph)
    init_map = get_initializer_map(graph)
    get_value_info_map(graph)
    pattern = re.compile(layer_filter) if layer_filter else None

    ffn_layers = []

    # Pass 1: detect Gemm nodes with bias (fused FFN)
    for node in graph.node:
        if node.op_type == "Gemm" and len(node.input) >= 3:
            weight_inp = node.input[1]
            bias_inp = node.input[2]
            # Resolve through DQ nodes for quantized models
            weight_init_name, w_shape = _resolve_weight_initializer(
                weight_inp, init_map, producers
            )
            bias_init_name, b_shape = _resolve_weight_initializer(
                bias_inp, init_map, producers
            )
            if weight_init_name is None or bias_init_name is None:
                continue
            if len(w_shape) != 2 or len(b_shape) != 1:
                continue
            if pattern and not pattern.search(node.name):
                continue
            # Check for activation after the Gemm output
            act_after = _find_activation_after(node.output[0], consumers)
            ffn_layers.append(
                FFNLayer(
                    matmul_node=node,
                    bias_node=None,
                    weight_name=weight_init_name,
                    bias_name=bias_init_name,
                    weight_shape=w_shape,
                    bias_shape=b_shape,
                    activation_after=act_after,
                    is_gemm=True,
                )
            )

    # Pass 2: detect MatMul → [Q → DQ | BFP] → Add(bias) patterns
    for node in graph.node:
        if node.op_type != "MatMul":
            continue

        # One input must resolve to a 2D initializer (the weight),
        # either directly or through DQ/BFP nodes (quantized models)
        weight_inp = None
        weight_init_name = None
        w_shape = None
        for inp in node.input:
            resolved_name, resolved_shape = _resolve_weight_initializer(
                inp, init_map, producers
            )
            if resolved_name is not None and len(resolved_shape) == 2:
                weight_inp = inp
                weight_init_name = resolved_name
                w_shape = resolved_shape
            else:
                pass
        if weight_init_name is None:
            continue

        # Walk forward from the MatMul output to find the bias Add
        matmul_out = node.output[0]
        bias_add, intermediate = _find_bias_add(
            matmul_out, consumers, producers, init_map
        )
        if bias_add is None:
            continue

        # Identify the bias initializer (may be behind a DQ node)
        bias_name = _get_bias_input(
            bias_add, matmul_out, intermediate, init_map, producers
        )
        if bias_name is None:
            continue
        b_shape = list(init_map[bias_name].dims)
        if len(b_shape) != 1:
            continue

        if (
            pattern
            and not pattern.search(node.name)
            and not pattern.search(bias_add.name)
        ):
            continue

        # Check for activation after the bias Add
        act_after = _find_activation_after(bias_add.output[0], consumers)

        ffn_layers.append(
            FFNLayer(
                matmul_node=node,
                bias_node=bias_add,
                weight_name=weight_init_name,
                bias_name=bias_name,
                weight_shape=w_shape,
                bias_shape=b_shape,
                activation_after=act_after,
                intermediate_nodes=intermediate,
            )
        )

    return ffn_layers


# Ops that are transparent/pass-through when looking for activations after an FFN.
# In quantized models, these sit between the bias Add/Gemm and the activation.
_PASSTHROUGH_OPS = frozenset(
    {
        "QuantizeLinear",
        "DequantizeLinear",
        "BFPQuantizeDequantize",
        "Reshape",
        "Cast",
    }
)


def _find_activation_after(
    tensor_name: str, consumers: dict, max_hops: int = 8
) -> str | None:
    """Check if an activation op follows a tensor, possibly through Q/DQ/Reshape.

    In quantized models the path looks like:
        Gemm/Add → Q → DQ → Reshape → Q → DQ → Gelu
    This function follows single-consumer chains of pass-through ops to find
    the activation.
    """
    current = tensor_name
    for _ in range(max_hops):
        cons_list = consumers.get(current, [])
        if len(cons_list) != 1:
            return None
        node = cons_list[0]
        if node.op_type in ACTIVATION_OPS:
            return node.op_type
        if node.op_type in _PASSTHROUGH_OPS:
            current = node.output[0]
            continue
        return None
    return None


def _find_bias_add(
    tensor_name: str,
    consumers: dict,
    producers: dict,
    init_map: dict,
    max_hops: int = 3,
) -> tuple[NodeProto | None, list[NodeProto]]:
    """Walk forward from a tensor through Q/DQ/BFP nodes to find a bias Add.

    Returns (bias_add_node, list_of_intermediate_nodes) or (None, []).
    """
    visited = []
    current = tensor_name
    for _ in range(max_hops):
        cons_list = consumers.get(current, [])
        if not cons_list:
            return None, []
        # Follow single-consumer chains (Q→DQ, BFP)
        if len(cons_list) == 1:
            next_node = cons_list[0]
            if next_node.op_type == "Add":
                return next_node, visited
            if next_node.op_type in (
                "QuantizeLinear",
                "DequantizeLinear",
                "BFPQuantizeDequantize",
            ):
                visited.append(next_node)
                current = next_node.output[0]
                continue
            return None, []
        # Multiple consumers — check if any is an Add with a bias
        for c in cons_list:
            if c.op_type == "Add":
                return c, visited
        return None, []
    return None, []


def _resolve_weight_initializer(
    tensor_name: str,
    init_map: dict,
    producers: dict,
    max_hops: int = 3,
) -> tuple[str | None, list[int]]:
    """Resolve a tensor name to its underlying weight initializer.

    Traces backward through DequantizeLinear/BFPQuantizeDequantize nodes to
    find the root initializer. This handles quantized models where the weight
    feeds through DQ before reaching MatMul/Gemm.

    Returns (initializer_name, shape) or (None, []) if no initializer found.
    """
    # Direct initializer
    if tensor_name in init_map:
        return tensor_name, list(init_map[tensor_name].dims)

    # Trace backward through DQ/BFP nodes
    current = tensor_name
    for _ in range(max_hops):
        if current not in producers:
            return None, []
        prod = producers[current]
        if prod.op_type in ("DequantizeLinear", "BFPQuantizeDequantize"):
            # The first input is the quantized tensor / initializer
            upstream = prod.input[0]
            if upstream in init_map:
                return upstream, list(init_map[upstream].dims)
            current = upstream
            continue
        return None, []
    return None, []


def _get_bias_input(
    add_node: NodeProto,
    matmul_out: str,
    intermediate: list[NodeProto],
    init_map: dict,
    producers: dict,
) -> str | None:
    """Determine which input to the Add node is the bias initializer.

    Handles both direct initializer inputs and inputs through DQ nodes
    (quantized bias).
    """
    # The tensor coming from the matmul chain
    chain_outputs = {matmul_out}
    for n in intermediate:
        chain_outputs.update(n.output)

    for inp in add_node.input:
        if inp in chain_outputs:
            continue
        # Direct initializer
        if inp in init_map:
            return inp
        # Bias through DQ node (quantized bias)
        resolved_name, _ = _resolve_weight_initializer(inp, init_map, producers)
        if resolved_name is not None:
            return resolved_name
    return None


# =============================================================================
# Quantization
# =============================================================================


def quantize_ffn_weights(
    model: ModelProto,
    ffn_layers: list[FFNLayer],
    precision: str = "int8",
) -> ModelProto:
    """Quantize FFN weight and bias initializers to the given precision.

    For INT8: inserts symmetric per-tensor QuantizeLinear/DequantizeLinear
    around each weight and bias initializer.

    Args:
        model: The input model (will be modified in place).
        ffn_layers: Detected FFN layers to quantize.
        precision: Target precision ("int8", "bf16", "fp32").

    Returns:
        The modified model.
    """
    if precision == "fp32":
        return _dequantize_ffn_weights(model, ffn_layers)
    if precision == "bf16":
        return _cast_ffn_to_bf16(model, ffn_layers)
    if precision != "int8":
        raise ValueError(f"Unsupported precision: {precision}")

    graph = model.graph
    init_map = get_initializer_map(graph)
    producers, consumers = build_graph_maps(graph)

    new_nodes = []
    new_initializers = []
    quantized_tensors = set()

    for layer in ffn_layers:
        if layer.is_gemm:
            # For Gemm nodes, we quantize the weight initializer
            _quantize_initializer_int8(
                graph,
                layer.weight_name,
                init_map,
                new_nodes,
                new_initializers,
                quantized_tensors,
                layer.matmul_node,
                input_idx=1,
                producers=producers,
                consumers=consumers,
            )
            _quantize_initializer_int8(
                graph,
                layer.bias_name,
                init_map,
                new_nodes,
                new_initializers,
                quantized_tensors,
                layer.matmul_node,
                input_idx=2,
                producers=producers,
                consumers=consumers,
            )
        else:
            # For MatMul+Add, quantize the weight on the MatMul
            _quantize_initializer_int8(
                graph,
                layer.weight_name,
                init_map,
                new_nodes,
                new_initializers,
                quantized_tensors,
                layer.matmul_node,
                input_idx=_weight_input_idx(layer),
                producers=producers,
                consumers=consumers,
            )
            # Quantize the bias on the Add node
            if layer.bias_node is not None:
                bias_idx = 0 if layer.bias_node.input[0] == layer.bias_name else 1
                _quantize_initializer_int8(
                    graph,
                    layer.bias_name,
                    init_map,
                    new_nodes,
                    new_initializers,
                    quantized_tensors,
                    layer.bias_node,
                    input_idx=bias_idx,
                    producers=producers,
                    consumers=consumers,
                )

    # Replace original FP32 initializers with INT8 versions
    for init in new_initializers:
        graph.initializer.append(init)
    # Remove orphaned original FP32 initializers that were quantized
    remaining_inits = []
    for init in graph.initializer:
        if init.name in quantized_tensors:
            continue
        remaining_inits.append(init)
    del graph.initializer[:]
    graph.initializer.extend(remaining_inits)

    # Insert DQ nodes right before the consuming node
    existing_nodes = list(graph.node)
    rebuilt = []
    for node in existing_nodes:
        # Insert any new DQ nodes that feed this node
        for new_node in new_nodes:
            if new_node.output[0] in node.input:
                rebuilt.append(new_node)
        rebuilt.append(node)
    del graph.node[:]
    graph.node.extend(rebuilt)

    return model


def _weight_input_idx(layer: FFNLayer) -> int:
    """Determine which input index of the MatMul is the weight."""
    init_map_names = {layer.weight_name}
    for i, inp in enumerate(layer.matmul_node.input):
        if inp in init_map_names:
            return i
    return 1


def _quantize_initializer_int8(
    graph: onnx.GraphProto,
    init_name: str,
    init_map: dict,
    new_nodes: list,
    new_initializers: list,
    quantized_tensors: set,
    consuming_node: NodeProto,
    input_idx: int,
    producers: dict,
    consumers: dict,
) -> None:
    """Insert a DequantizeLinear node for an initializer (symmetric INT8).

    Quantizes the float initializer to int8 (symmetric per-tensor) and replaces
    the consuming node's input with the DQ output.
    """
    if init_name in quantized_tensors:
        return
    if init_name not in init_map:
        return

    tensor = init_map[init_name]
    data = numpy_helper.to_array(tensor).astype(np.float32)
    abs_max = float(np.abs(data).max())
    if abs_max == 0:
        abs_max = 1.0
    scale = abs_max / 127.0

    quantized_data = np.clip(np.round(data / scale), -128, 127).astype(np.int8)

    # Create quantized weight initializer
    q_name = f"{init_name}_quantized"
    q_init = numpy_helper.from_array(quantized_data, name=q_name)
    new_initializers.append(q_init)

    # Create scale initializer
    scale_name = f"{init_name}_scale"
    scale_init = numpy_helper.from_array(
        np.array([scale], dtype=np.float32), name=scale_name
    )
    new_initializers.append(scale_init)

    # Create zero point initializer
    zp_name = f"{init_name}_zero_point"
    zp_init = numpy_helper.from_array(np.array([0], dtype=np.int8), name=zp_name)
    new_initializers.append(zp_init)

    # Create DequantizeLinear node
    dq_out_name = f"{init_name}_dequantized"
    dq_node = helper.make_node(
        "DequantizeLinear",
        inputs=[q_name, scale_name, zp_name],
        outputs=[dq_out_name],
        name=f"DequantizeLinear_{init_name}",
    )
    new_nodes.append(dq_node)

    # Update the consuming node to use the DQ output
    inputs = list(consuming_node.input)
    inputs[input_idx] = dq_out_name
    del consuming_node.input[:]
    consuming_node.input.extend(inputs)

    quantized_tensors.add(init_name)


def _dequantize_ffn_weights(
    model: ModelProto, ffn_layers: list[FFNLayer]
) -> ModelProto:
    """Remove quantization from FFN layers (restore float weights)."""
    # Placeholder — full implementation would strip Q/DQ from FFN paths
    return model


def _cast_ffn_to_bf16(model: ModelProto, ffn_layers: list[FFNLayer]) -> ModelProto:
    """Cast FFN weights to BF16 (insert Cast nodes)."""
    # Placeholder — full implementation would insert Cast(to=BFLOAT16)
    return model


# =============================================================================
# Reporting
# =============================================================================


def summarize_ffn_layers(ffn_layers: list[FFNLayer]) -> str:
    """Produce a human-readable summary of detected FFN layers."""
    if not ffn_layers:
        return "No FFN layers detected."

    lines = [f"Detected {len(ffn_layers)} FFN layer(s):\n"]
    lines.append(
        f"{'#':<4} {'Node Name':<40} {'Weight Shape':<20} {'Bias Shape':<15} {'Activation':<12} {'Type':<8}"
    )
    lines.append("-" * 99)
    for i, layer in enumerate(ffn_layers):
        name = layer.matmul_node.name
        w_str = "x".join(str(d) for d in layer.weight_shape)
        b_str = "x".join(str(d) for d in layer.bias_shape)
        act = layer.activation_after or "-"
        typ = "Gemm" if layer.is_gemm else "MatMul+Add"
        lines.append(f"{i:<4} {name:<40} {w_str:<20} {b_str:<15} {act:<12} {typ:<8}")
    return "\n".join(lines)


# =============================================================================
# Model I/O
# =============================================================================


def load_model(path: str, load_external_data: bool = True) -> ModelProto:
    """Load an ONNX model from .onnx or .onnxtxt."""
    if path.endswith(".onnxtxt"):
        try:
            from onnx import parser as onnx_parser
        except ImportError:
            raise ImportError("onnx.parser required for .onnxtxt files")
        with open(path) as f:
            return onnx_parser.parse_model(f.read())
    return onnx.load(path, load_external_data=load_external_data)


def save_model(model: ModelProto, path: str) -> None:
    """Save ONNX model."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if path.endswith(".onnxtxt"):
        try:
            from onnx import printer as onnx_printer
        except ImportError:
            raise ImportError("onnx.printer required for .onnxtxt output")
        with open(path, "w") as f:
            f.write(onnx_printer.to_text(model))
    else:
        onnx.save(model, path)


# =============================================================================
# CLI
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Detect and quantize FFN layers in ONNX models"
    )
    parser.add_argument("--input", required=True, help="Input ONNX model path")
    parser.add_argument(
        "--output", help="Output ONNX model path (required unless --list-only)"
    )
    parser.add_argument(
        "--precision",
        default="int8",
        choices=["int8", "bf16", "fp32"],
        help="Target precision for FFN weights/biases",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only list detected FFN layers, don't quantize",
    )
    parser.add_argument(
        "--layer-filter", default=None, help="Regex filter on node names"
    )
    parser.add_argument(
        "--no-external-data",
        action="store_true",
        help="Skip loading external data (for structural inspection only)",
    )
    args = parser.parse_args()

    load_ext = not args.no_external_data
    model = load_model(args.input, load_external_data=load_ext)
    ffn_layers = detect_ffn_layers(model, layer_filter=args.layer_filter)

    print(summarize_ffn_layers(ffn_layers))

    if args.list_only:
        return

    if not args.output:
        parser.error("--output is required when not using --list-only")

    if not ffn_layers:
        print("Nothing to quantize.")
        return

    if not load_ext:
        print(
            "ERROR: cannot quantize without loading tensor data. "
            "Remove --no-external-data to quantize."
        )
        return

    model = quantize_ffn_weights(model, ffn_layers, precision=args.precision)
    save_model(model, args.output)
    print(f"\nSaved quantized model to {args.output}")


if __name__ == "__main__":
    main()
