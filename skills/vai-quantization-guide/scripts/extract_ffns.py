#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""
FFN Block Extraction and Reporting for ONNX Models

Extracts all Feed-Forward Network (FFN) layers from an ONNX model, groups them
into structural blocks (MLP, Attention, Standalone), and produces a report.

Reuses the FFN detection from ffn_quantize.py (detect_ffn_layers / FFNLayer).

Usage:
    # Report all FFN blocks in a model
    python extract_ffns.py --input model.onnx

    # JSON output
    python extract_ffns.py --input model.onnx --format json

    # Filter by node name pattern
    python extract_ffns.py --input model.onnx --layer-filter "mlp"
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ffn_quantize import FFNLayer, detect_ffn_layers, load_model


@dataclass
class FFNBlock:
    """A group of FFN layers forming a structural block."""

    block_type: str  # "MLP", "Attention", or "Standalone"
    ffns: list[FFNLayer] = field(default_factory=list)
    activation: str | None = None


def group_ffn_blocks(ffn_layers: list[FFNLayer]) -> list[FFNBlock]:
    """Group FFN layers into structural blocks.

    Grouping rules (applied in order of encounter):
    - FFN with activation followed by FFN without activation → MLP block
    - Two consecutive FFNs without activation → Attention block
    - Remaining FFNs → Standalone blocks
    """
    blocks: list[FFNBlock] = []
    i = 0
    while i < len(ffn_layers):
        cur = ffn_layers[i]
        if cur.activation_after:
            if i + 1 < len(ffn_layers) and not ffn_layers[i + 1].activation_after:
                blocks.append(
                    FFNBlock(
                        block_type="MLP",
                        ffns=[cur, ffn_layers[i + 1]],
                        activation=cur.activation_after,
                    )
                )
                i += 2
            else:
                blocks.append(
                    FFNBlock(
                        block_type="Standalone",
                        ffns=[cur],
                        activation=cur.activation_after,
                    )
                )
                i += 1
        elif i + 1 < len(ffn_layers) and not ffn_layers[i + 1].activation_after:
            blocks.append(
                FFNBlock(
                    block_type="Attention",
                    ffns=[cur, ffn_layers[i + 1]],
                )
            )
            i += 2
        else:
            blocks.append(
                FFNBlock(
                    block_type="Standalone",
                    ffns=[cur],
                )
            )
            i += 1
    return blocks


def _shape_str(shape: list[int]) -> str:
    return "x".join(str(d) for d in shape)


def format_report(
    ffn_layers: list[FFNLayer], blocks: list[FFNBlock], model_name: str = ""
) -> str:
    """Produce a human-readable extraction report."""
    lines: list[str] = []
    sep = "=" * 80

    lines.append(sep)
    lines.append("FFN EXTRACTION REPORT")
    lines.append(sep)
    if model_name:
        lines.append(f"Model: {model_name}")
    lines.append(f"Total FFN layers found: {len(ffn_layers)}")
    with_act = [f for f in ffn_layers if f.activation_after]
    without_act = [f for f in ffn_layers if not f.activation_after]
    lines.append(f"  FFNs with activation:    {len(with_act)}")
    lines.append(f"  FFNs without activation: {len(without_act)}")

    # Detailed list
    lines.append("")
    lines.append(sep)
    lines.append("DETAILED FFN LIST")
    lines.append(sep)
    lines.append(f"{'#':<4} {'Node Name':<55} {'Weight Shape':<20} {'Activation':<12}")
    lines.append(f"{'-'*4} {'-'*55} {'-'*20} {'-'*12}")
    for i, layer in enumerate(ffn_layers):
        act = layer.activation_after or "-"
        lines.append(
            f"{i+1:<4} {layer.matmul_node.name:<55} "
            f"{_shape_str(layer.weight_shape):<20} {act:<12}"
        )

    # Block summary
    lines.append("")
    lines.append(sep)
    lines.append("BLOCK GROUPING")
    lines.append(sep)

    type_counts: dict[str, list[FFNBlock]] = defaultdict(list)
    for b in blocks:
        type_counts[b.block_type].append(b)

    lines.append("")
    lines.append(
        f"{'Block Type':<25} {'Count':<8} {'FFNs/Block':<12} {'Total FFNs':<12}"
    )
    lines.append(f"{'-'*25} {'-'*8} {'-'*12} {'-'*12}")

    for btype in ("MLP", "Attention", "Standalone"):
        blist = type_counts.get(btype, [])
        if not blist:
            continue
        ffns_per = len(blist[0].ffns)
        total = sum(len(b.ffns) for b in blist)
        lines.append(
            f"{btype + ' block':<25} {len(blist):<8} {ffns_per:<12} {total:<12}"
        )
        if btype == "MLP" and blist:
            b = blist[0]
            lines.append(
                f"  FFN1: up-proj [{_shape_str(b.ffns[0].weight_shape)}] + {b.activation}"
            )
            lines.append(f"  FFN2: down-proj [{_shape_str(b.ffns[1].weight_shape)}]")
        elif btype == "Attention" and blist:
            b = blist[0]
            lines.append(f"  FFN1: in-proj [{_shape_str(b.ffns[0].weight_shape)}]")
            lines.append(f"  FFN2: out-proj [{_shape_str(b.ffns[1].weight_shape)}]")
        elif btype == "Standalone":
            for b in blist:
                ws = _shape_str(b.ffns[0].weight_shape)
                act = b.activation or "none"
                lines.append(f"  [{ws}] activation={act}")

    total_in_blocks = sum(len(b.ffns) for b in blocks)
    lines.append(f"\n{'TOTAL':<25} {len(blocks):<8} {'-':<12} {total_in_blocks:<12}")

    # Weight shape distribution
    lines.append("")
    lines.append(sep)
    lines.append("WEIGHT SHAPE DISTRIBUTION")
    lines.append(sep)
    shape_counts: dict[tuple[int, ...], int] = defaultdict(int)
    for layer in ffn_layers:
        shape_counts[tuple(layer.weight_shape)] += 1
    for shape, count in sorted(shape_counts.items()):
        lines.append(f"  {_shape_str(list(shape))}: {count} FFNs")

    return "\n".join(lines)


def to_json(ffn_layers: list[FFNLayer], blocks: list[FFNBlock]) -> list[dict]:
    """Serialize extraction results to JSON-compatible dicts."""
    result = []
    for block in blocks:
        block_dict = {
            "block_type": block.block_type,
            "activation": block.activation,
            "ffns": [],
        }
        for layer in block.ffns:
            block_dict["ffns"].append(
                {
                    "matmul_node": layer.matmul_node.name,
                    "op_type": "Gemm" if layer.is_gemm else "MatMul+Add",
                    "weight_name": layer.weight_name,
                    "weight_shape": layer.weight_shape,
                    "bias_name": layer.bias_name,
                    "bias_shape": layer.bias_shape,
                    "activation_after": layer.activation_after,
                }
            )
        result.append(block_dict)
    return result


def extract_ffns(
    model_path: str,
    layer_filter: str | None = None,
    load_external_data: bool = True,
) -> tuple[list[FFNLayer], list[FFNBlock]]:
    """Extract and group all FFN blocks from an ONNX model.

    Args:
        model_path: Path to the ONNX model.
        layer_filter: Optional regex to filter by node name.
        load_external_data: Whether to load external tensor data.

    Returns:
        Tuple of (ffn_layers, ffn_blocks).
    """
    model = load_model(model_path, load_external_data=load_external_data)
    ffn_layers = detect_ffn_layers(model, layer_filter=layer_filter)
    blocks = group_ffn_blocks(ffn_layers)
    return ffn_layers, blocks


def main():
    parser = argparse.ArgumentParser(
        description="Extract and report FFN blocks from ONNX models"
    )
    parser.add_argument("--input", required=True, help="Input ONNX model path")
    parser.add_argument(
        "--format",
        default="text",
        choices=["text", "json"],
        help="Output format",
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
    ffn_layers, blocks = extract_ffns(
        args.input, layer_filter=args.layer_filter, load_external_data=load_ext
    )

    model_name = Path(args.input).name
    if args.format == "json":
        print(json.dumps(to_json(ffn_layers, blocks), indent=2))
    else:
        print(format_report(ffn_layers, blocks, model_name=model_name))


if __name__ == "__main__":
    main()
