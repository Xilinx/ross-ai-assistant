#!/usr/bin/env python3
# Copyright (C) 2022 - 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Replace a subgraph in an ONNX model with a custom op node.

This is the inverse of onnx_cut.py: given an original model and the same
input/output tensor names used to extract a subgraph, this script removes
the subgraph nodes and inserts a single custom op node in their place.

Examples:
    # Replace by node names (auto-infers cut points)
    python onnx_stitch.py --model model.onnx \
        --nodes Div --custom-op-name div_custom --output model_custom.onnx

    # Report what would be replaced (dry run)
    python onnx_stitch.py --model model.onnx \
        --nodes Div --custom-op-name div_custom --report

    # Replace by explicit tensor names
    python onnx_stitch.py --model model.onnx \
        --input-names matmul1_out --output-names div_out \
        --custom-op-name div_custom --domain mydomain \
        --output model_custom.onnx
"""

import argparse
import sys
from pathlib import Path

import onnx
from onnx import helper, shape_inference

sys.path.insert(0, str(Path(__file__).parent))
from onnx_graph_utils import (  # noqa: E402
    find_nodes_by_names,
    get_tensor_info,
    infer_cut_points,
)


def find_subgraph_nodes(
    graph: onnx.GraphProto,
    input_names: list[str],
    output_names: list[str],
) -> list[onnx.NodeProto]:
    """Find all nodes in the subgraph between input_names and output_names.

    Uses forward + backward traversal to identify the exact set of nodes
    that should be replaced by the custom op.

    Args:
        graph: The ONNX graph.
        input_names: Tensor names at the subgraph entry (cut-point inputs).
        output_names: Tensor names at the subgraph exit (cut-point outputs).

    Returns:
        List of NodeProto in topological order that form the subgraph.
    """
    input_set = set(input_names)
    set(output_names)

    # Collect initializer names (these are available as inputs without a producer node)
    initializer_names = {init.name for init in graph.initializer}

    # Forward pass: find nodes reachable from input_names
    # A tensor is "available" if it's in input_names, is an initializer, or is a graph input
    graph_input_names = {inp.name for inp in graph.input}
    available = set(input_names) | initializer_names | graph_input_names

    forward_reachable = []
    for node in graph.node:
        # A node is reachable if at least one of its non-initializer, non-graph-input
        # inputs comes from a tensor in input_names or from a previously reachable node
        node_inputs_from_subgraph = any(
            inp in available and inp in input_set for inp in node.input
        )
        any(
            inp in available
            and inp not in initializer_names
            and inp not in graph_input_names
            and inp not in input_set
            for inp in node.input
            if inp  # skip empty inputs
        )
        # Check if any predecessor of this node is already reachable
        has_reachable_predecessor = False
        for inp in node.input:
            if inp and inp not in initializer_names and inp not in graph_input_names:
                for prev_node in forward_reachable:
                    if inp in prev_node.output:
                        has_reachable_predecessor = True
                        break
            if has_reachable_predecessor:
                break

        if node_inputs_from_subgraph or has_reachable_predecessor:
            forward_reachable.append(node)
            for out in node.output:
                available.add(out)

    # Backward pass: from output_names, keep only nodes whose outputs
    # are needed to produce the output tensors
    needed_tensors = set(output_names)
    backward_reachable = []

    for node in reversed(forward_reachable):
        if any(out in needed_tensors for out in node.output):
            backward_reachable.append(node)
            for inp in node.input:
                if inp:  # skip empty strings
                    needed_tensors.add(inp)

    backward_reachable.reverse()
    return backward_reachable


def find_subgraph_initializers(
    graph: onnx.GraphProto,
    subgraph_nodes: list[onnx.NodeProto],
    keep_as_inputs: bool = True,
) -> tuple[list[str], list[str]]:
    """Find initializers consumed by the subgraph.

    Args:
        graph: The ONNX graph.
        subgraph_nodes: Nodes that form the subgraph.
        keep_as_inputs: If True, return initializers to keep as custom op inputs.

    Returns:
        (exclusive_initializers, shared_initializers):
        - exclusive: only used by subgraph nodes (can be removed)
        - shared: used by both subgraph and non-subgraph nodes (must keep)
    """
    initializer_names = {init.name for init in graph.initializer}
    subgraph_node_set = set(id(n) for n in subgraph_nodes)

    # Collect all inputs consumed by subgraph nodes that are initializers
    subgraph_init_inputs = set()
    for node in subgraph_nodes:
        for inp in node.input:
            if inp in initializer_names:
                subgraph_init_inputs.add(inp)

    # Check which are also used by non-subgraph nodes
    non_subgraph_inputs = set()
    for node in graph.node:
        if id(node) not in subgraph_node_set:
            for inp in node.input:
                if inp in subgraph_init_inputs:
                    non_subgraph_inputs.add(inp)

    exclusive = sorted(subgraph_init_inputs - non_subgraph_inputs)
    shared = sorted(subgraph_init_inputs & non_subgraph_inputs)
    return exclusive, shared


def stitch(
    model: onnx.ModelProto,
    input_names: list[str],
    output_names: list[str],
    custom_op_name: str,
    domain: str = "mydomain",
    keep_weights: bool = True,
) -> onnx.ModelProto:
    """Replace a subgraph with a custom op node.

    Args:
        model: The original ONNX model.
        input_names: Subgraph entry tensor names (activation inputs).
        output_names: Subgraph exit tensor names.
        custom_op_name: Name for the custom op type (e.g., "div_custom").
        domain: Custom op domain (default: "mydomain").
        keep_weights: If True, keep initializers as additional custom op inputs.

    Returns:
        Modified ONNX model with the subgraph replaced.
    """
    import copy

    model = copy.deepcopy(model)
    graph = model.graph

    # Run shape inference so we have shape info for all tensors
    try:
        model = shape_inference.infer_shapes(model)
        graph = model.graph
    except Exception:
        pass  # proceed without full shape info

    # Find subgraph nodes
    subgraph_nodes = find_subgraph_nodes(graph, input_names, output_names)
    if not subgraph_nodes:
        raise ValueError(
            f"No nodes found between inputs {input_names} and outputs {output_names}"
        )

    subgraph_node_names = [n.op_type for n in subgraph_nodes]
    print(
        f"Subgraph nodes to replace ({len(subgraph_nodes)}): "
        f"{', '.join(subgraph_node_names)}"
    )

    # Find initializers
    exclusive_inits, shared_inits = find_subgraph_initializers(graph, subgraph_nodes)

    # Build custom op inputs: activation inputs + (optionally) initializer inputs
    custom_op_inputs = list(input_names)
    if keep_weights:
        # Add exclusive initializers as additional inputs to the custom op
        for node in subgraph_nodes:
            for inp in node.input:
                if inp in exclusive_inits and inp not in custom_op_inputs:
                    custom_op_inputs.append(inp)
        # Also add shared initializers
        for node in subgraph_nodes:
            for inp in node.input:
                if inp in shared_inits and inp not in custom_op_inputs:
                    custom_op_inputs.append(inp)

    print(f"Custom op inputs: {custom_op_inputs}")
    print(f"Custom op outputs: {output_names}")

    # Get output shape and dtype for the custom op node attributes
    # (required by VAIML shape inference)
    output_shapes = []
    output_dtypes = []
    for out_name in output_names:
        shape, dtype = get_tensor_info(model, out_name)
        if shape is None:
            raise ValueError(
                f"Cannot determine shape for output tensor '{out_name}'. "
                "Run onnx.shape_inference.infer_shapes() on the model first."
            )
        output_shapes.append(shape)
        output_dtypes.append(dtype or "float32")

    # Create the custom op node
    # For single output, use flat shape/data_type attributes
    # For multi-output, encode as lists
    attrs = {}
    if len(output_names) == 1:
        attrs["shape"] = output_shapes[0]
        attrs["data_type"] = output_dtypes[0]
    else:
        # Multi-output: encode shapes as flattened with a rank prefix per output
        for i, (shape, dtype) in enumerate(zip(output_shapes, output_dtypes)):
            attrs[f"shape_{i}"] = shape
            attrs[f"data_type_{i}"] = dtype

    custom_node = helper.make_node(
        custom_op_name,
        inputs=custom_op_inputs,
        outputs=list(output_names),
        domain=domain,
        **attrs,
    )

    # Find insertion position (where the first subgraph node was)
    subgraph_node_ids = set(id(n) for n in subgraph_nodes)
    insert_idx = None
    for i, node in enumerate(graph.node):
        if id(node) in subgraph_node_ids:
            insert_idx = i
            break

    # Remove subgraph nodes
    nodes_to_keep = [n for n in graph.node if id(n) not in subgraph_node_ids]

    # Remove all existing nodes and re-add with custom op inserted
    while len(graph.node) > 0:
        graph.node.pop()

    # If all nodes were subgraph nodes, just add the custom op
    if not nodes_to_keep:
        graph.node.append(custom_node)
    else:
        # Insert custom node at the position where first subgraph node was
        for i, node in enumerate(nodes_to_keep):
            if i == insert_idx:
                graph.node.append(custom_node)
            graph.node.append(node)

        # If insert_idx was at or beyond the end, append
        if insert_idx is not None and insert_idx >= len(nodes_to_keep):
            graph.node.append(custom_node)

    # Remove exclusive initializers if not keeping weights
    if not keep_weights:
        inits_to_remove = set(exclusive_inits)
        new_inits = [
            init for init in graph.initializer if init.name not in inits_to_remove
        ]
        while len(graph.initializer) > 0:
            graph.initializer.pop()
        for init in new_inits:
            graph.initializer.append(init)

        # Also remove from graph.input (initializers appear there too)
        new_inputs = [inp for inp in graph.input if inp.name not in inits_to_remove]
        while len(graph.input) > 0:
            graph.input.pop()
        for inp in new_inputs:
            graph.input.append(inp)

    # Clean up value_info: remove entries for tensors internal to the removed subgraph
    subgraph_internal_tensors = set()
    for node in subgraph_nodes:
        for out in node.output:
            if out not in set(output_names):
                subgraph_internal_tensors.add(out)

    new_value_info = [
        vi for vi in graph.value_info if vi.name not in subgraph_internal_tensors
    ]
    while len(graph.value_info) > 0:
        graph.value_info.pop()
    for vi in new_value_info:
        graph.value_info.append(vi)

    # Add custom domain opset import if not already present
    has_domain = any(op.domain == domain for op in model.opset_import)
    if not has_domain:
        new_opset = helper.make_opsetid(domain, 1)
        model.opset_import.append(new_opset)

    return model


def report_subgraph(
    model: onnx.ModelProto,
    input_names: list[str],
    output_names: list[str],
) -> None:
    """Report what would be replaced without modifying the model."""
    graph = model.graph

    # Run shape inference
    try:
        model = shape_inference.infer_shapes(model)
        graph = model.graph
    except Exception:
        pass

    subgraph_nodes = find_subgraph_nodes(graph, input_names, output_names)

    print("\n=== Subgraph Report ===")
    print(f"Input tensors (cut-point entries): {input_names}")
    print(f"Output tensors (cut-point exits): {output_names}")
    print(f"\nNodes to replace ({len(subgraph_nodes)}):")

    for node in subgraph_nodes:
        print(f"  {node.op_type}: {list(node.input)} -> {list(node.output)}")

    exclusive_inits, shared_inits = find_subgraph_initializers(graph, subgraph_nodes)

    if exclusive_inits:
        print("\nInitializers consumed exclusively by subgraph:")
        for name in exclusive_inits:
            shape, dtype = get_tensor_info(model, name)
            print(f"  {name}: shape={shape}, dtype={dtype}")

    if shared_inits:
        print("\nInitializers shared with other nodes (will be kept):")
        for name in shared_inits:
            print(f"  {name}")

    print("\nOutput tensor info:")
    for out_name in output_names:
        shape, dtype = get_tensor_info(model, out_name)
        print(f"  {out_name}: shape={shape}, dtype={dtype}")

    # Check graph-edge cases
    graph_input_names = {inp.name for inp in graph.input}
    graph_output_names = {out.name for out in graph.output}

    edge_inputs = [n for n in input_names if n in graph_input_names]
    edge_outputs = [n for n in output_names if n in graph_output_names]

    if edge_inputs:
        print(f"\nNote: {edge_inputs} are graph inputs (head modification)")
    if edge_outputs:
        print(f"Note: {edge_outputs} are graph outputs (tail modification)")

    print(f"\nTotal model nodes: {len(graph.node)}")
    print(f"Nodes after stitching: {len(graph.node) - len(subgraph_nodes) + 1}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replace a subgraph in an ONNX model with a custom op node."
    )
    parser.add_argument(
        "--model",
        "-m",
        required=True,
        help="Path to the original ONNX model.",
    )
    parser.add_argument(
        "--nodes",
        nargs="+",
        help="Node names or op types to replace. Auto-infers input/output tensors.",
    )
    parser.add_argument(
        "--input-names",
        "-i",
        nargs="+",
        help="Subgraph input tensor names (optional if --nodes is provided).",
    )
    parser.add_argument(
        "--output-names",
        "-o",
        nargs="+",
        help="Subgraph output tensor names (optional if --nodes is provided).",
    )
    parser.add_argument(
        "--custom-op-name",
        "-n",
        required=True,
        help="Name for the custom op type (e.g., 'div_custom').",
    )
    parser.add_argument(
        "--domain",
        "-d",
        default="mydomain",
        help="Custom op domain (default: mydomain).",
    )
    parser.add_argument(
        "--output",
        default="stitched_model.onnx",
        help="Output filename (default: stitched_model.onnx).",
    )
    parser.add_argument(
        "--keep-weights",
        action="store_true",
        default=True,
        help="Keep initializers as custom op inputs (default: yes).",
    )
    parser.add_argument(
        "--no-keep-weights",
        dest="keep_weights",
        action="store_false",
        help="Remove initializers consumed exclusively by the subgraph.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Dry run: report what would be replaced without modifying the model.",
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"Error: Model not found: {model_path}", file=sys.stderr)
        return 1

    # Load model
    if model_path.suffix == ".onnxtxt":
        model = onnx.parser.parse_model(model_path.read_text())
    else:
        model = onnx.load(str(model_path))

    # Resolve input/output names from --nodes if provided
    if args.nodes:
        try:
            target_nodes = find_nodes_by_names(model.graph, args.nodes)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        inferred_inputs, inferred_outputs = infer_cut_points(model.graph, target_nodes)

        print(f"Selected nodes ({len(target_nodes)}):")
        for node in target_nodes:
            print(f"  {node.op_type} (name='{node.name}')")
        print(f"Inferred input tensors: {inferred_inputs}")
        print(f"Inferred output tensors: {inferred_outputs}")

        input_names = args.input_names if args.input_names else inferred_inputs
        output_names = args.output_names if args.output_names else inferred_outputs
    elif args.input_names and args.output_names:
        input_names = args.input_names
        output_names = args.output_names
    else:
        parser.error(
            "Provide either --nodes (to auto-infer cut points) "
            "or both --input-names and --output-names."
        )
        return 1

    if args.report:
        report_subgraph(model, input_names, output_names)
        return 0

    try:
        modified = stitch(
            model,
            input_names=input_names,
            output_names=output_names,
            custom_op_name=args.custom_op_name,
            domain=args.domain,
            keep_weights=args.keep_weights,
        )
    except Exception as e:
        print(f"Error: Stitching failed: {e}", file=sys.stderr)
        return 1

    # Validate
    try:
        onnx.checker.check_model(modified)
        print("Model validation passed.")
    except onnx.checker.ValidationError as e:
        print(f"Warning: Model validation issue (may be OK for custom ops): {e}")

    # Save
    onnx.save(modified, args.output)
    print(f"Stitched model saved: {args.output}")
    print(f"  Nodes: {len(modified.graph.node)}")
    opset_strs = []
    for op in modified.opset_import:
        domain_str = op.domain or "default"
        opset_strs.append(f"{domain_str}:{op.version}")
    print(f"  Opsets: {', '.join(opset_strs)}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
