#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""Pipeline to quantize a ViT model for NPU deployment.

Produces a model matching MX9MHA TGs with:
  - MX9 BFP on MHA projections/attention
  - INT8 QDQ on FFN/MLP layers (→ Conv2d kernels)
  - BF16 residual Adds
  - Single NPU partition

Stages:
  1. Split fused QKV → Gemm(transB=1), fold Div
  2. Quark INT8 quantization (excludes residual Adds)
  3. Insert BFP wrappers on MHA blocks
  4. Strip attention-path QDQ, convert carry-path Adds to bf16 Cast

Usage:
    python quantize_vit.py \
        --input model_fp32.onnx \
        --output model_quantized.onnx \
        [--quark-config int8_exclude_adds.json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--input", required=True, help="Input FP32 ONNX model")
    parser.add_argument("--output", required=True, help="Output quantized ONNX model")
    parser.add_argument(
        "--quark-config", help="Quark JSON config (default: auto-generated)"
    )
    parser.add_argument(
        "--exclude-adds",
        nargs="*",
        help="Residual Add node names to exclude from INT8 (auto-detected if omitted)",
    )
    parser.add_argument(
        "--ln-precision",
        choices=["i8", "bf16"],
        default="i8",
        help="LayerNorm precision: i8 (all vint8) or bf16 (all bf16) (default: i8)",
    )
    parser.add_argument(
        "--residual-precision",
        choices=["i8", "bf16"],
        default="bf16",
        help="Residual Add precision: bf16 (excluded from INT8, bf16 Cast carry "
        "path) or i8 (VINT8 residual adds) (default: bf16)",
    )
    args = parser.parse_args()

    scripts_dir = Path(__file__).parent
    scripts_parent_dir = scripts_dir.parent
    inp = Path(args.input).resolve()
    out = Path(args.output).resolve()

    with tempfile.TemporaryDirectory(prefix="vit_quant_") as tmpdir:
        tmpdir = Path(tmpdir)
        split_model = tmpdir / "split.onnx"
        int8_model = tmpdir / "int8.onnx"
        mx9_model = tmpdir / "mx9.onnx"

        # --- Stage 1: Split QKV + fold Div ---
        print("=" * 60)
        print("Stage 1: Split QKV + fold Div")
        print("=" * 60)
        _run(
            sys.executable,
            str(
                scripts_parent_dir
                / "skills"
                / "experimental-methods"
                / "vai-mha-mx9"
                / "mha_mx9_quantize.py"
            ),
            "--input",
            str(inp),
            "--output",
            str(split_model),
            "--div-fold",
            "--split-only",
        )

        # --- Generate Quark config if needed ---
        quark_cfg = args.quark_config
        if not quark_cfg:
            if args.residual_precision == "i8":
                # Keep residual Adds in INT8 (VINT8 residual adds): do not
                # exclude them from Quark quantization.
                add_names = []
                print("\nResidual adds will be quantized to INT8 (VINT8 adds)")
            else:
                add_names = args.exclude_adds
                if not add_names:
                    add_names = _detect_residual_adds(str(split_model))
            cfg = {
                "global_config": {
                    "input_tensors": {"spec": "XInt8Spec"},
                    "weight": {"spec": "XInt8Spec"},
                    "bias": None,
                },
                "exclude": {"node_names": add_names},
                "extra_options": {
                    "UseRandomData": True,
                    "MatMulToGemm": True,
                    "ConvertClipToRelu": False,
                    "DedicatedQDQPair": True,
                },
            }
            quark_cfg = str(tmpdir / "quark_config.json")
            with open(quark_cfg, "w") as f:
                json.dump(cfg, f, indent=2)
            print(
                f"\nAuto-generated Quark config (excludes {len(add_names)} Add nodes)"
            )

        # --- Stage 2: Quark INT8 ---
        print("\n" + "=" * 60)
        print("Stage 2: Quark INT8 quantization")
        print("=" * 60)
        _run(
            sys.executable,
            str(scripts_dir / "mixed_precision_quantize.py"),
            "--input",
            str(split_model),
            "--output",
            str(int8_model),
            "--config",
            str(quark_cfg),
        )

        # --- Stage 3: BFP insertion ---
        print("\n" + "=" * 60)
        print("Stage 3: BFP insertion on MHA blocks")
        print("=" * 60)
        _run(
            sys.executable,
            str(
                scripts_parent_dir
                / "skills"
                / "experimental-methods"
                / "vai-mha-mx9"
                / "mha_mx9_quantize.py"
            ),
            "--input",
            str(int8_model),
            "--output",
            str(mx9_model),
            "--bfp-only",
        )

        # --- Stage 4: Strip attention QDQ + bf16 Casts ---
        print("\n" + "=" * 60)
        print("Stage 4: Strip attention QDQ + bf16 Casts")
        print("=" * 60)
        strip_args = [
            sys.executable,
            str(
                scripts_parent_dir
                / "skills"
                / "experimental-methods"
                / "vai-mha-mx9"
                / "strip_attention_qdq.py"
            ),
            str(mx9_model),
            str(out),
            "--ln-precision",
            args.ln_precision,
            "--residual-precision",
            args.residual_precision,
        ]
        _run(*strip_args)

    print("\n" + "=" * 60)
    print(f"Final model: {out}")
    print("=" * 60)


def _detect_residual_adds(model_path: str) -> list[str]:
    """Auto-detect residual Add node names (Add nodes whose inputs are NOT
    both from initializers)."""
    import onnx

    m = onnx.load(model_path, load_external_data=False)
    init_names = {i.name for i in m.graph.initializer}
    adds = []
    for n in m.graph.node:
        if n.op_type == "Add" and n.name.startswith("node_add"):
            if not all(inp in init_names for inp in n.input):
                adds.append(n.name)
    return adds


def _run(*args):
    print(f"  $ {' '.join(args[1:])}")
    result = subprocess.run(args, check=True)
    return result


if __name__ == "__main__":
    main()
