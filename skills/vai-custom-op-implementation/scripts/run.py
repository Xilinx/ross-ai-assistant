#!/usr/bin/env python3
# Copyright (C) 2022 - 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Run compiled ONNX models on VitisAI hardware using a pre-built inputs.npz.

Examples:
    # Run with inputs from <model_stem>_inputs/inputs.npz
    python run.py model_a.onnx

    # Run with a specific inputs directory and output directory
    python run.py model_a.onnx --input-dir my_inputs --output-dir output_a

    # Run model compiled in a specific cache directory
    python run.py model_b.onnx --cache-dir custom_cache --output-dir output_b
"""

import argparse
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Dict

import numpy as np
import onnxruntime as ort

if importlib.util.find_spec("onnxruntime") is None:
    print(
        "Product virtual environment not found, please source the virtual environment before running this script"
    )
    sys.exit(1)


def load_inputs(input_dir: Path) -> Dict[str, np.ndarray]:
    """Load inputs from input_dir's inputs.npz.

    Loads the npz directly, without checking the ONNX model's input names.
    """
    input_npz = input_dir / "inputs.npz"
    if not input_npz.exists():
        raise FileNotFoundError(f"No inputs.npz found in {input_dir}")

    print(f"Loading inputs from {input_npz}")
    data = np.load(input_npz)
    feeds = dict(data)
    for name, arr in feeds.items():
        print(f"  Input '{name}': shape={arr.shape}, dtype={arr.dtype}")
    return feeds


def save_outputs(output_dict: Dict[str, np.ndarray], output_dir: Path) -> None:
    """Save outputs to output_dir as .npz and individual .npy files.

    Args:
        output_dict: Dict mapping output names to numpy arrays
        output_dir: Directory to save outputs
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save as .npz
    np.savez(output_dir / "outputs.npz", **output_dict)
    print(f"Saved outputs.npz to {output_dir}/")

    # Save individual .npy files
    for name, arr in output_dict.items():
        safe_name = name.replace("/", "_").replace("\\", "_")
        filepath = output_dir / f"{safe_name}.npy"
        np.save(filepath, arr)
        print(
            f"  Output '{name}': shape={arr.shape}, dtype={arr.dtype}, "
            f"min={arr.min():.6f}, max={arr.max():.6f} -> {filepath}"
        )


def split_domain_and_name(op_fully_qualified_name: str):
    try:
        domain_name_separator = op_fully_qualified_name.rindex(".")
        op_domain = op_fully_qualified_name[:domain_name_separator]
        op_name = op_fully_qualified_name[domain_name_separator + 1 :]
    except ValueError:
        op_name = op_fully_qualified_name
        op_domain = ""
    return op_domain, op_name


def get_num_inputs_from_op_config(op_config_path: str) -> int:
    """
    Determine the number of inputs from an op config YAML file.

    Args:
        op_config_path: Path to the op config YAML file

    Returns:
        Number of inputs defined in the signature, or 1 if not available
    """
    if not op_config_path:
        return 1

    import yaml

    op_config_file = Path(op_config_path)
    if not op_config_file.exists():
        return 1

    with open(op_config_file) as yf:
        op_yaml = yaml.safe_load(yf)

    sig = op_yaml.get("signature", {})
    inputs = sig.get("inputs", [])

    return len(inputs) if inputs else 1


def register_custom_ops_from_vitisai_config(vitisai_config: Path):
    from onnxruntime_custom_ops import (
        register_dynamic_custom_ops_to_onnxruntime,
        vaiml_custom_op_schema,
    )

    assert (
        vitisai_config.exists()
    ), f"File {vitisai_config} does not exist in the filesystem."

    with open(vitisai_config) as f:
        vitisai_config = json.load(f)

    ops = []

    # Account for the fact that sometimes the vitisai_config has a init pass
    # {
    #     "name": "init",
    #     "plugin": "vaip-pass_init"
    # },
    passes = vitisai_config["passes"]
    custom_ops = None
    for p in passes:
        if p["name"] == "vaiml_partition":
            custom_ops = p.get("vaiml_config", {}).get("custom_ops")
            break

    if not custom_ops:
        print("No custom ops found in the Vitis AI config, skipping registration.")
        return

    for op, op_info in custom_ops.items():
        op_domain, op_name = split_domain_and_name(op)
        nb_inputs = get_num_inputs_from_op_config(op_info.get("op_config"))
        ops.append(
            vaiml_custom_op_schema(
                domain=op_domain,
                name=op_name,
                nb_inputs=nb_inputs,
                nb_outputs=1,
            )
        )
    register_dynamic_custom_ops_to_onnxruntime(ops)


def run_vitisai(
    model_path: Path,
    input_dir: Path | None = None,
    output_dir: Path | None = None,
    cache_dir: Path | None = None,
    vitisai_config: Path = Path("vitisai_config.json"),
    num_runs: int = 1,
) -> dict[str, np.ndarray]:
    """Run model on VitisAI hardware.

    Args:
        model_path: Path to ONNX model
        input_dir: Directory containing inputs.npz (default: <model_stem>_inputs/)
        output_dir: Directory to save outputs
        cache_dir: Cache directory (default: model stem)
        vitisai_config: Path to vitisai_config.json
        num_runs: Number of inference runs (for timing)

    Returns:
        Dict mapping output names to numpy arrays (from the last run)
    """
    if num_runs < 1:
        raise ValueError(f"num_runs must be >= 1, got {num_runs}")

    if cache_dir is None:
        cache_dir = Path(model_path.stem)

    if output_dir is None:
        output_dir = Path(f"{model_path.stem}_outputs")

    if input_dir is None:
        input_dir = Path(f"{model_path.stem}_inputs")

    print(f"Running {model_path.name} on VitisAI...")
    print(f"  Cache directory: {cache_dir}/")
    print(f"  Number of runs: {num_runs}")

    # Register custom ops if config exists
    if vitisai_config.exists():
        register_custom_ops_from_vitisai_config(vitisai_config)

    # Load inputs from the npz file (no ONNX graph parsing)
    feeds = load_inputs(input_dir)

    # Create session
    options = ort.SessionOptions()
    session = ort.InferenceSession(
        str(model_path),
        options,
        providers=["VitisAIExecutionProvider"],
        provider_options=[
            {
                "target": "VAIML",
                "config_file": str(vitisai_config),
                "ai_analyzer_visualization": 1,
                "ai_analyzer_profiling": 1,
                "cache_dir": str(cache_dir),
                "cache_key": "cache",
            }
        ],
    )

    # Run inference
    output_names = [o.name for o in session.get_outputs()]
    run_times = []
    for run_idx in range(num_runs):
        start = time.perf_counter()
        results = session.run(None, feeds)
        elapsed = time.perf_counter() - start
        run_times.append(elapsed)
        if num_runs > 1:
            print(f"  Run {run_idx + 1}/{num_runs}: {elapsed:.4f}s")

    outputs = dict(zip(output_names, results))

    print(
        "Warning: Timing statistics include CPython/Runtime/OS overhead. They are only approximations."
    )

    # Print timing statistics
    if num_runs > 1:
        print(f"\n  Timing statistics ({num_runs} runs):")
        print(f"    Total:  {sum(run_times):.4f}s")
        print(f"    Min:    {min(run_times):.4f}s")
        print(f"    Max:    {max(run_times):.4f}s")
        print(f"    Mean:   {statistics.mean(run_times):.4f}s")
        print(f"    Median: {statistics.median(run_times):.4f}s")
        if num_runs > 2:
            print(f"    Stdev:  {statistics.stdev(run_times):.4f}s")
    else:
        print(f"  Inference time: {run_times[0]:.4f}s")

    # Save outputs (from the last run)
    save_outputs(outputs, output_dir)

    print(f"Execution successful: {model_path.name}")
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run compiled ONNX models on VitisAI hardware."
    )
    parser.add_argument("model", type=Path, help="Path to ONNX model")
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Input directory containing inputs.npz (default: <model_stem>_inputs/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: <model_stem>_outputs/)",
    )
    parser.add_argument(
        "--cache-dir", type=Path, help="Cache directory (default: <model_stem>/)"
    )
    parser.add_argument(
        "--vitisai-config",
        type=Path,
        default="vitisai_config.json",
        help="Path to vitisai_config.json (default: vitisai_config.json)",
    )
    parser.add_argument(
        "--num-runs",
        type=int,
        default=10,
        help="Number of inference runs for timing statistics (default: 10)",
    )

    args = parser.parse_args()

    if not args.model.exists():
        print(f"Error: Model not found: {args.model}", file=sys.stderr)
        return 1

    try:
        run_vitisai(
            model_path=args.model,
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
            vitisai_config=args.vitisai_config,
            num_runs=args.num_runs,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
