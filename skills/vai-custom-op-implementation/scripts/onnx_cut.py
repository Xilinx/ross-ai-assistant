#!/usr/bin/env python

# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import argparse
import sys
from pathlib import Path

import onnx

sys.path.insert(0, str(Path(__file__).parent))
from onnx_graph_utils import (  # noqa: E402
    find_nodes_by_names,
    infer_cut_points,
    load_model,
)

"""
A CLI tool for:
  1) Reporting tensors and nodes in an ONNX model
  2) Extracting a subgraph by specifying input/output tensor names
  3) Extracting a subgraph by specifying node names (auto-infers cut points)

Examples:
  # Report tensors and nodes
  python onnx_cut.py --model model.onnx --report

  # Extract by node names (auto-infers input/output tensors)
  python onnx_cut.py --model model.onnx --nodes Div --output subgraph.onnx

  # Extract by tensor names
  python onnx_cut.py --model model.onnx \\
      --input-names matmul1_out --output-names div_out --output subgraph.onnx
"""


def extract_subgraph(
    input_filename: str,
    output_filename: str,
    input_names: list[str],
    output_names: list[str],
) -> None:
    """
    Extract a subgraph from an ONNX model given input and output tensor names.

    Args:
        input_filename: Path to the original ONNX model.
        output_filename: Path to write the extracted subgraph (.onnx recommended).
        input_names: List of tensor names to cut the graph inputs at.
        output_names: List of tensor names to cut the graph outputs at.
    """
    model = load_model(input_filename)
    graph = model.graph
    graph_output_names = {out.name for out in graph.output}

    # Workaround for onnx.utils.extract_model limitation:
    # When a tensor is both a graph output AND you want to use it as a subgraph input,
    # extract_model fails. Solution: temporarily remove conflicting outputs.
    conflicting_inputs = [name for name in input_names if name in graph_output_names]

    if conflicting_inputs:
        print(
            f"Note: {conflicting_inputs} are graph outputs. "
            "Temporarily removing them from outputs for extraction."
        )
        for out in list(graph.output):
            if out.name in conflicting_inputs:
                graph.output.remove(out)

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".onnx", delete=False) as tmp:
            temp_path = tmp.name
            onnx.save(model, temp_path)

        try:
            onnx.utils.extract_model(
                temp_path, output_filename, input_names, output_names
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)
    else:
        onnx.utils.extract_model(
            input_filename, output_filename, input_names, output_names
        )

    print(f"Subgraph extracted: {output_filename}")


def report_tensors(onnx_model_path: str) -> None:
    """Report input, output, all tensor names, and node info in an ONNX model."""
    model = load_model(onnx_model_path)
    graph = model.graph

    tensor_names = set()

    input_names = [inp.name for inp in graph.input]
    tensor_names.update(input_names)
    print("Input Tensors:")
    for n in input_names:
        print(f"  - {n}")

    output_names = [output.name for output in graph.output]
    tensor_names.update(output_names)
    print("Output Tensors:")
    for n in output_names:
        print(f"  - {n}")

    for node in graph.node:
        tensor_names.update(node.input)
        tensor_names.update(node.output)

    print("All Tensors (sorted):")
    for n in sorted(tensor_names):
        if n:  # skip empty strings
            print(f"  - {n}")

    print("\nNodes:")
    for i, node in enumerate(graph.node):
        name = node.name or f"(unnamed #{i})"
        inputs = [inp for inp in node.input if inp]
        outputs = [out for out in node.output if out]
        print(f"  [{i}] {node.op_type} (name='{name}'): {inputs} -> {outputs}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report tensors or extract a subgraph from an ONNX model."
    )
    parser.add_argument(
        "--model", "-m", required=True, help="Path to the input ONNX model."
    )
    parser.add_argument(
        "--input-names",
        "-i",
        nargs="+",
        help="Space-separated list of input tensor names to cut the graph at.",
    )
    parser.add_argument(
        "--output-names",
        "-o",
        nargs="+",
        help="Space-separated list of output tensor names to cut the graph at.",
    )
    parser.add_argument(
        "--nodes",
        "-n",
        nargs="+",
        help="Space-separated list of node names or op types to extract. "
        "Automatically infers input/output tensor names from the selected nodes.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for the extracted subgraph. "
        "Default: model_a.onnx in the same directory as the input model.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="If set, prints input/output/all tensor names and nodes, then exits.",
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.report:
        report_tensors(args.model)
        return 0

    # Resolve input/output tensor names
    if args.nodes:
        model = load_model(args.model)
        graph = model.graph

        try:
            target_nodes = find_nodes_by_names(graph, args.nodes)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        input_names, output_names = infer_cut_points(graph, target_nodes)

        print(f"Selected nodes ({len(target_nodes)}):")
        for node in target_nodes:
            print(f"  {node.op_type} (name='{node.name}')")
        print(f"Inferred input tensors: {input_names}")
        print(f"Inferred output tensors: {output_names}")

        # Allow explicit overrides
        if args.input_names:
            input_names = args.input_names
            print(f"Using explicit input override: {input_names}")
        if args.output_names:
            output_names = args.output_names
            print(f"Using explicit output override: {output_names}")

    elif args.input_names and args.output_names:
        input_names = args.input_names
        output_names = args.output_names
    else:
        parser.error(
            "Provide either --nodes (to auto-infer cut points) "
            "or both --input-names and --output-names.\n"
            "Tip: use --report first to discover valid names."
        )

    # Default output path: model_a.onnx next to the input model
    output_path = args.output
    if output_path is None:
        output_path = str(Path(args.model).parent / "model_a.onnx")
        print(f"Output: {output_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        extract_subgraph(
            input_filename=args.model,
            output_filename=output_path,
            input_names=input_names,
            output_names=output_names,
        )
    except Exception as e:
        print(f"ERROR: Extraction failed: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
