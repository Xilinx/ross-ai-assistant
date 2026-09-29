#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""
Compare Accuracy Utility

Compares ONNX model outputs between different quantization configurations
against a CPU FP32 reference. Reports absolute error, relative error, PSNR,
and element-wise pass rate.

Usage:
    python compare_accuracy.py --model model.onnx --reference model_fp32.onnx \
        --atol 0.015 --rtol 0.01 [--inputs inputs.npz] [--seed 42]

    python compare_accuracy.py --model model_vint8.onnx --model model_bf16.onnx \
        --reference model_fp32.onnx --compare-all
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import onnx
import onnxruntime as ort


def generate_inputs(model_path: Path, seed: int = 42) -> dict[str, np.ndarray]:
    """Generate deterministic random inputs matching model input specs."""
    model = onnx.load(str(model_path))
    rng = np.random.default_rng(seed=seed)
    feeds = {}
    initializer_names = {init.name for init in model.graph.initializer}

    for inp in model.graph.input:
        if inp.name in initializer_names:
            continue
        shape = []
        for dim in inp.type.tensor_type.shape.dim:
            if dim.dim_value > 0:
                shape.append(dim.dim_value)
            else:
                shape.append(1)
        elem_type = inp.type.tensor_type.elem_type
        if elem_type in (1, 16):  # FLOAT, BFLOAT16
            feeds[inp.name] = rng.standard_normal(shape).astype(np.float32)
        elif elem_type == 3:  # INT8
            feeds[inp.name] = rng.integers(-128, 127, size=shape, dtype=np.int8)
        elif elem_type in (5, 10):  # FLOAT16
            feeds[inp.name] = rng.standard_normal(shape).astype(np.float16)
        elif elem_type == 2:  # UINT8
            feeds[inp.name] = rng.integers(0, 255, size=shape, dtype=np.uint8)
        else:
            feeds[inp.name] = rng.standard_normal(shape).astype(np.float32)
    return feeds


def run_cpu(model_path: Path, feeds: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Run model on CPU and return outputs."""
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    output_names = [o.name for o in session.get_outputs()]
    results = session.run(None, feeds)
    return dict(zip(output_names, results))


def compute_psnr(reference: np.ndarray, actual: np.ndarray) -> float:
    """Compute Peak Signal-to-Noise Ratio (dB)."""
    ref = reference.astype(np.float64)
    act = actual.astype(np.float64)
    mse = float(np.mean((ref - act) ** 2))
    if mse == 0:
        return float("inf")
    max_val = float(np.max(np.abs(ref)))
    if max_val == 0:
        return 0.0
    return 10.0 * np.log10(max_val**2 / mse)


def compare_single_output(
    name: str,
    reference: np.ndarray,
    actual: np.ndarray,
    rtol: float = 0.01,
    atol: float = 0.015,
) -> dict[str, Any]:
    """Compare a single output tensor. Returns metrics dict."""
    ref = reference.astype(np.float64)
    act = actual.astype(np.float64)
    abs_diff = np.abs(ref - act)

    # Element-wise tolerance (assert_allclose formula)
    elem_tol = atol + rtol * np.abs(ref)
    within_tol = abs_diff <= elem_tol
    n_pass = int(np.sum(within_tol))
    total = ref.size
    pass_rate = 100.0 * n_pass / total if total else 0.0

    # Absolute error
    max_abs = float(np.max(abs_diff))
    mean_abs = float(np.mean(abs_diff))

    # Relative error (avoid division by zero)
    nonzero = np.abs(ref) > 1e-30
    if np.any(nonzero):
        rel_err = abs_diff[nonzero] / np.abs(ref[nonzero])
        max_rel = float(np.max(rel_err))
        mean_rel = float(np.mean(rel_err))
    else:
        max_rel = 0.0
        mean_rel = 0.0

    # PSNR
    psnr = compute_psnr(reference, actual)

    # Worst point
    worst_idx = np.unravel_index(np.argmax(abs_diff), abs_diff.shape)
    worst_ref = float(ref[worst_idx])
    worst_act = float(act[worst_idx])

    return {
        "name": name,
        "shape": list(ref.shape),
        "elements": total,
        "max_abs_error": max_abs,
        "mean_abs_error": mean_abs,
        "max_rel_error": max_rel,
        "mean_rel_error": mean_rel,
        "pass_rate": pass_rate,
        "n_pass": n_pass,
        "n_fail": total - n_pass,
        "psnr_db": psnr,
        "worst_point": {
            "index": worst_idx,
            "ref_value": worst_ref,
            "actual_value": worst_act,
        },
        "passed": pass_rate >= 99.9,  # Allow 0.1% element failures
    }


def compare_outputs(
    reference: dict[str, np.ndarray],
    actual: dict[str, np.ndarray],
    rtol: float = 0.01,
    atol: float = 0.015,
) -> tuple[bool, list[dict[str, Any]]]:
    """Compare all outputs. Returns (all_pass, metrics_list)."""
    results = []
    all_pass = True

    for name in reference:
        if name not in actual:
            results.append({"name": name, "error": "Missing in actual outputs"})
            all_pass = False
            continue

        metrics = compare_single_output(name, reference[name], actual[name], rtol, atol)
        results.append(metrics)
        if not metrics["passed"]:
            all_pass = False

    return all_pass, results


def print_report(results: list[dict[str, Any]], rtol: float, atol: float) -> None:
    """Print formatted accuracy report."""
    print(f"\n{'='*70}")
    print(f"ACCURACY REPORT  (atol={atol}, rtol={rtol})")
    print(f"{'='*70}")

    for r in results:
        if "error" in r:
            print(f"\n  Output '{r['name']}': ERROR - {r['error']}")
            continue

        status = "PASS" if r["passed"] else "FAIL"
        print(f"\n  Output '{r['name']}': [{status}]")
        print(f"    Shape: {r['shape']}  Elements: {r['elements']}")
        print(
            f"    Abs error: max={r['max_abs_error']:.6e}  mean={r['mean_abs_error']:.6e}"
        )
        print(
            f"    Rel error: max={r['max_rel_error']:.6e}  mean={r['mean_rel_error']:.6e}"
        )
        print(f"    Pass rate: {r['n_pass']}/{r['elements']} ({r['pass_rate']:.2f}%)")
        print(f"    PSNR: {r['psnr_db']:.2f} dB")
        wp = r["worst_point"]
        print(
            f"    Worst: idx={wp['index']} ref={wp['ref_value']:.6e} actual={wp['actual_value']:.6e}"
        )

    print(f"\n{'='*70}")
    all_pass = all(r.get("passed", False) for r in results if "error" not in r)
    print(f"OVERALL: {'PASS' if all_pass else 'FAIL'}")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Compare model accuracy")
    parser.add_argument(
        "--model", "-m", required=True, nargs="+", help="Model(s) to evaluate"
    )
    parser.add_argument(
        "--reference", "-r", required=True, help="Reference FP32 model for ground truth"
    )
    parser.add_argument(
        "--atol", type=float, default=0.015, help="Absolute tolerance (default: 0.015)"
    )
    parser.add_argument(
        "--rtol", type=float, default=0.01, help="Relative tolerance (default: 0.01)"
    )
    parser.add_argument("--inputs", help="Pre-generated inputs (.npz)")
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed for input generation"
    )
    parser.add_argument(
        "--psnr-threshold",
        type=float,
        default=30.0,
        help="Minimum PSNR in dB (default: 30)",
    )
    args = parser.parse_args()

    ref_path = Path(args.reference)

    # Generate or load inputs
    if args.inputs:
        feeds = dict(np.load(args.inputs))
        print(f"Loaded inputs from {args.inputs}")
    else:
        feeds = generate_inputs(ref_path, seed=args.seed)
        print(f"Generated random inputs (seed={args.seed})")

    # Run reference
    print(f"Running reference model: {ref_path}")
    ref_outputs = run_cpu(ref_path, feeds)

    # Evaluate each model
    for model_path_str in args.model:
        model_path = Path(model_path_str)
        print(f"\nEvaluating: {model_path}")

        try:
            actual_outputs = run_cpu(model_path, feeds)
        except Exception as e:
            print(f"  ERROR: Could not run model: {e}")
            continue

        all_pass, results = compare_outputs(
            ref_outputs, actual_outputs, args.rtol, args.atol
        )
        print_report(results, args.rtol, args.atol)

        # Additional threshold checks
        for r in results:
            if "error" in r:
                continue
            if r["psnr_db"] < args.psnr_threshold:
                print(
                    f"  WARNING: PSNR ({r['psnr_db']:.2f} dB) below threshold ({args.psnr_threshold} dB)"
                )


if __name__ == "__main__":
    main()
