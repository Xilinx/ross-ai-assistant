# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Shared utilities for ONNX graph manipulation (used by onnx_cut.py and onnx_stitch.py)."""

from pathlib import Path

import onnx
from onnx import TensorProto


def load_model(model_path: str) -> onnx.ModelProto:
    """Load an ONNX model from .onnx or .onnxtxt file."""
    if Path(model_path).suffix == ".onnxtxt":
        return onnx.parser.parse_model(Path(model_path).read_text())
    return onnx.load(model_path)


def find_nodes_by_names(
    graph: onnx.GraphProto, node_names: list[str]
) -> list[onnx.NodeProto]:
    """Find nodes matching the given names.

    Matches against node.name first, then falls back to node.op_type.
    For op_type matching, if multiple nodes share the same op_type,
    all matching nodes are included.

    Args:
        graph: The ONNX graph.
        node_names: List of node names or op types to find.

    Returns:
        List of matching NodeProto objects.

    Raises:
        ValueError: If any name cannot be matched.
    """
    matched = []
    matched_ids = set()
    remaining = set(node_names)

    # First pass: exact match on node.name
    for node in graph.node:
        if node.name in remaining:
            matched.append(node)
            matched_ids.add(id(node))
            remaining.discard(node.name)

    # Second pass: match on op_type for anything not yet matched
    if remaining:
        for node in graph.node:
            if id(node) not in matched_ids and node.op_type in remaining:
                matched.append(node)
                matched_ids.add(id(node))

    # Check for unmatched names
    found_names = set()
    for node in matched:
        found_names.add(node.name)
        found_names.add(node.op_type)
    still_missing = remaining - found_names
    if still_missing:
        raise ValueError(
            f"Could not find nodes matching: {still_missing}. "
            "Use onnx_cut.py --report to see available node names and op types."
        )

    return matched


def infer_cut_points(
    graph: onnx.GraphProto, target_nodes: list[onnx.NodeProto]
) -> tuple[list[str], list[str]]:
    """Given a set of target nodes, infer input/output tensor names for extraction.

    Input tensors: tensors consumed by target nodes but NOT produced by any target node
    (excluding initializers, which are included automatically by extract_model).

    Output tensors: tensors produced by target nodes but consumed outside the subgraph
    or are graph outputs.

    Args:
        graph: The ONNX graph.
        target_nodes: The nodes forming the subgraph.

    Returns:
        (input_names, output_names) suitable for extract_subgraph or stitch.
    """
    target_ids = set(id(n) for n in target_nodes)
    initializer_names = {init.name for init in graph.initializer}

    produced = set()
    consumed = set()
    for node in target_nodes:
        for out in node.output:
            if out:
                produced.add(out)
        for inp in node.input:
            if inp:
                consumed.add(inp)

    # Inputs: consumed by targets but not produced by them, excluding initializers
    input_names = sorted(consumed - produced - initializer_names)

    # Outputs: produced by targets and consumed outside or are graph outputs
    consumed_by_non_target = set()
    for node in graph.node:
        if id(node) not in target_ids:
            for inp in node.input:
                if inp:
                    consumed_by_non_target.add(inp)

    graph_output_names = {out.name for out in graph.output}

    output_names = []
    for tensor in sorted(produced):
        is_consumed_outside = tensor in consumed_by_non_target
        is_graph_output = tensor in graph_output_names
        is_consumed_inside = any(tensor in node.input for node in target_nodes)
        if is_consumed_outside or is_graph_output or not is_consumed_inside:
            output_names.append(tensor)

    if not output_names:
        output_names = sorted(produced)

    return input_names, output_names


def dtype_enum_to_string(elem_type: int) -> str:
    """Convert ONNX TensorProto.DataType enum to string."""
    mapping = {
        TensorProto.FLOAT: "float32",
        TensorProto.DOUBLE: "float64",
        TensorProto.FLOAT16: "float16",
        TensorProto.INT8: "int8",
        TensorProto.INT16: "int16",
        TensorProto.INT32: "int32",
        TensorProto.INT64: "int64",
        TensorProto.UINT8: "uint8",
        TensorProto.UINT16: "uint16",
        TensorProto.UINT32: "uint32",
        TensorProto.UINT64: "uint64",
        TensorProto.BOOL: "bool",
    }
    if hasattr(TensorProto, "BFLOAT16") and elem_type == TensorProto.BFLOAT16:
        return "bfloat16"
    return mapping.get(elem_type, "float32")


def get_tensor_info(
    model: onnx.ModelProto, tensor_name: str
) -> tuple[list[int] | None, str | None]:
    """Look up shape and dtype for a tensor by name.

    Searches graph inputs, outputs, value_info, and initializers.

    Returns:
        (shape_list, dtype_string) or (None, None) if not found.
    """
    graph = model.graph

    for vi in graph.input:
        if vi.name == tensor_name:
            tt = vi.type.tensor_type
            shape = [
                d.dim_value if d.HasField("dim_value") else 0 for d in tt.shape.dim
            ]
            return shape, dtype_enum_to_string(tt.elem_type)

    for vi in graph.output:
        if vi.name == tensor_name:
            tt = vi.type.tensor_type
            shape = [
                d.dim_value if d.HasField("dim_value") else 0 for d in tt.shape.dim
            ]
            return shape, dtype_enum_to_string(tt.elem_type)

    for vi in graph.value_info:
        if vi.name == tensor_name:
            tt = vi.type.tensor_type
            shape = [
                d.dim_value if d.HasField("dim_value") else 0 for d in tt.shape.dim
            ]
            return shape, dtype_enum_to_string(tt.elem_type)

    for init in graph.initializer:
        if init.name == tensor_name:
            return list(init.dims), dtype_enum_to_string(init.data_type)

    return None, None
