#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""
Mixed-Precision Quantization Driver Script

Unified interface for quantizing ONNX models with various mixed-precision
configurations using AMD Quark. Supports JSON-based configuration presets
and exploration mode for finding optimal layer assignments.

Usage:
    # Basic quantization from JSON config:
    python mixed_precision_quantize.py --input model.onnx --output model_quant.onnx \
        --config full_vint8.json

    # Move specific nodes to bf16:
    python mixed_precision_quantize.py --input model.onnx --output model_quant.onnx \
        --config full_vint8.json --fp-nodes "Conv_0,Relu_1" --fp-precision bf16

    # Move op types to bf16:
    python mixed_precision_quantize.py --input model.onnx --output model_quant.onnx \
        --config full_vint8.json --fp-op-types "Sigmoid,Add" --fp-precision bf16

    # Exploration mode:
    python mixed_precision_quantize.py --input model.onnx --output model_quant.onnx \
        --config full_vint8.json --explore --explore-num-samples 50

    # Use calibration data:
    python mixed_precision_quantize.py --input model.onnx --output model_quant.onnx \
        --config full_vint8.json --calibration-data inputs.npz

    # Works with .onnxtxt files:
    python mixed_precision_quantize.py --input model.onnxtxt --output model_quant.onnx \
        --config full_bf16.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import numpy as np
import onnx
from onnx import TensorProto

try:
    from onnx import parser as onnx_parser
    from onnx import printer as onnx_printer
except ImportError:
    onnx_parser = None
    onnx_printer = None

try:
    from quark.onnx import ModelQuantizer, QConfig, QLayerConfig
    from quark.onnx.quantization.config.custom_config import (
        DEFAULT_MICROEXPONENTS_PARAMS,
    )
    from quark.onnx.quantization.config.spec import BFloat16Spec, MX6Spec, XInt8Spec

    try:
        from quark.onnx.quantization.config.spec import Float16Spec
    except ImportError:
        Float16Spec = None
    QUARK_AVAILABLE = True
except ImportError:
    QUARK_AVAILABLE = False


# =============================================================================
# Calibration Data Reader
# =============================================================================

_ONNX_DTYPE_TO_NP = {
    TensorProto.FLOAT: np.float32,
    TensorProto.UINT8: np.uint8,
    TensorProto.INT8: np.int8,
    TensorProto.FLOAT16: np.float16,
    TensorProto.INT32: np.int32,
    TensorProto.INT64: np.int64,
    TensorProto.FLOAT16: np.float16,  # elem_type 10 is also FLOAT16
    TensorProto.DOUBLE: np.float64,
    TensorProto.UINT32: np.uint32,
    TensorProto.UINT64: np.uint64,
    TensorProto.BFLOAT16: np.float32,  # no native bf16 in numpy, use float32
}

# Input distribution presets for random input generation.
# Models often require inputs in specific ranges to produce meaningful outputs
# (e.g., image models expect [0,1], audio expects small amplitudes, etc.).
DISTRIBUTIONS = {
    "small": "randn * 0.01  - near-zero, sigmoid/tanh linear region",
    "normal": "randn * 0.1   - moderate scale (default)",
    "unit": "randn * 1.0   - standard normal, exercises saturation",
    "large": "randn * 5.0   - strong saturation in sigmoid/tanh",
    "positive": "uniform(0, 1) - all positive (image-like inputs)",
    "negative": "uniform(-1, 0) - all negative",
    "mixed": "uniform(-5, 5) - wide range",
    "ones": "all ones",
    "zeros": "all zeros",
}


def _sample_float(
    distribution: str, shape: tuple, dtype: np.dtype, rng: np.random.Generator
) -> np.ndarray:
    """Generate float input data using the given distribution preset."""
    if distribution == "small":
        return (rng.standard_normal(shape) * 0.01).astype(dtype)
    if distribution == "normal":
        return (rng.standard_normal(shape) * 0.1).astype(dtype)
    if distribution == "unit":
        return rng.standard_normal(shape).astype(dtype)
    if distribution == "large":
        return (rng.standard_normal(shape) * 5.0).astype(dtype)
    if distribution == "positive":
        return rng.uniform(0, 1, shape).astype(dtype)
    if distribution == "negative":
        return rng.uniform(-1, 0, shape).astype(dtype)
    if distribution == "mixed":
        return rng.uniform(-5, 5, shape).astype(dtype)
    if distribution == "ones":
        return np.ones(shape, dtype=dtype)
    if distribution == "zeros":
        return np.zeros(shape, dtype=dtype)
    raise ValueError(
        f"Unknown distribution: {distribution}. Choose from: {list(DISTRIBUTIONS.keys())}"
    )


def get_model_input_info(model: onnx.ModelProto) -> list[dict[str, Any]]:
    """Extract input name, shape, and dtype from model (excluding initializers)."""
    initializer_names = {init.name for init in model.graph.initializer}
    inputs = []
    for inp in model.graph.input:
        if inp.name in initializer_names:
            continue
        shape = [
            d.dim_value if d.dim_value > 0 else 1
            for d in inp.type.tensor_type.shape.dim
        ]
        elem_type = inp.type.tensor_type.elem_type
        np_dtype = _ONNX_DTYPE_TO_NP.get(elem_type, np.float32)
        inputs.append({"name": inp.name, "shape": tuple(shape), "dtype": np_dtype})
    return inputs


class CalibrationDataReader:
    """Calibration data reader for Quark quantization.

    Supports:
    - .npz files (keys match model input names)
    - .npy files (one per model input)
    - Deterministic random generation
    """

    def __init__(
        self,
        model: onnx.ModelProto,
        calibration_paths: list[str] | None = None,
        num_samples: int = 1,
        seed: int = 2024,
        distribution: str = "positive",
    ):
        self.input_info = get_model_input_info(model)
        self.samples: list[dict[str, np.ndarray]] = []
        self._index = 0

        if calibration_paths:
            self._load_files(calibration_paths)
        else:
            self._generate_random(num_samples, seed, distribution)

    def _load_files(self, paths: list[str]) -> None:
        npz = [p for p in paths if p.endswith(".npz")]
        npy = [p for p in paths if p.endswith(".npy")]

        if npz:
            for path in npz:
                data = dict(np.load(path))
                sample = {}
                for info in self.input_info:
                    if info["name"] in data:
                        sample[info["name"]] = data[info["name"]].astype(info["dtype"])
                    else:
                        keys = list(data.keys())
                        idx = next(
                            (
                                i
                                for i, inf in enumerate(self.input_info)
                                if inf["name"] == info["name"]
                            ),
                            None,
                        )
                        if idx is not None and idx < len(keys):
                            sample[info["name"]] = data[keys[idx]].astype(info["dtype"])
                self.samples.append(sample)
        elif npy:
            n_inputs = len(self.input_info)
            num_samples = len(npy) // n_inputs
            for s in range(num_samples):
                sample = {}
                for i, info in enumerate(self.input_info):
                    arr = np.load(npy[s * n_inputs + i])
                    sample[info["name"]] = arr.astype(info["dtype"])
                self.samples.append(sample)

    def _generate_random(self, num_samples: int, seed: int, distribution: str) -> None:
        rng = np.random.default_rng(seed)
        for _ in range(num_samples):
            sample = {}
            for info in self.input_info:
                if np.issubdtype(info["dtype"], np.floating):
                    sample[info["name"]] = _sample_float(
                        distribution, info["shape"], info["dtype"], rng
                    )
                elif np.issubdtype(info["dtype"], np.integer):
                    iinfo = np.iinfo(info["dtype"])
                    sample[info["name"]] = rng.integers(
                        0,
                        min(100, iinfo.max + 1),
                        size=info["shape"],
                        dtype=info["dtype"],
                    )
                else:
                    sample[info["name"]] = _sample_float(
                        distribution, info["shape"], np.float32, rng
                    )
            self.samples.append(sample)

    def get_next(self) -> dict[str, np.ndarray] | None:
        if self._index >= len(self.samples):
            return None
        sample = self.samples[self._index]
        self._index += 1
        return sample

    def rewind(self) -> None:
        self._index = 0


# =============================================================================
# Model I/O
# =============================================================================


def load_model(path: str) -> onnx.ModelProto:
    """Load model from .onnx or .onnxtxt."""
    if path.endswith(".onnxtxt") and onnx_parser:
        with open(path) as f:
            return onnx_parser.parse_model(f.read())
    return onnx.load(path)


def save_model(model: onnx.ModelProto, path: str) -> None:
    """Save model to .onnx or .onnxtxt."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if path.endswith(".onnxtxt") and onnx_printer:
        with open(path, "w") as f:
            f.write(onnx_printer.to_text(model))
    else:
        onnx.save(model, path)


# =============================================================================
# Quark config building from JSON
# =============================================================================


def resolve_spec(spec_name: str, params: dict | None = None):
    """Resolve spec name to Quark spec object."""
    if not QUARK_AVAILABLE:
        raise RuntimeError("Quark is not installed")
    params = params or {}
    if spec_name == "XInt8Spec":
        return XInt8Spec(**params)
    elif spec_name == "BFloat16Spec":
        return BFloat16Spec(**params)
    elif spec_name == "Float16Spec":
        if Float16Spec:
            return Float16Spec(**params)
        return BFloat16Spec(**params)  # fallback
    elif spec_name == "MX6Spec":
        return MX6Spec(**params)
    raise ValueError(f"Unknown spec: {spec_name}")


def build_layer_config(cfg_dict: dict) -> QLayerConfig:
    """Build QLayerConfig from dict."""
    kwargs = {}
    for field in ("input_tensors", "weight", "bias"):
        if field in cfg_dict:
            spec_info = cfg_dict[field]
            if spec_info is None:
                kwargs[field] = None
            else:
                kwargs[field] = resolve_spec(spec_info["spec"], spec_info.get("params"))
    return QLayerConfig(**kwargs)


def build_qconfig_from_json(
    config: dict[str, Any],
    fp_nodes: list[str] | None = None,
    fp_op_types: list[str] | None = None,
    fp_precision: str = "bf16",
) -> QConfig:
    """Build Quark QConfig from JSON config dict."""
    if not QUARK_AVAILABLE:
        raise RuntimeError("Quark is not installed")

    global_cfg = config.get("global_config", {})
    global_config = build_layer_config(global_cfg)

    layer_type_config = {}

    # Build specific_layer_config as dict[QLayerConfig, list[str]]
    # QConfig expects: {QLayerConfig_instance: [pattern1, pattern2, ...]}
    # where patterns starting with ^ are treated as regex, others as exact names.
    # IMPORTANT: Regex patterns MUST start with ^ AND contain .* to be valid.
    specific_layer_config: dict[Any, list[str]] = {}

    # Process specific_layer_config from JSON
    for entry in config.get("specific_layer_config", []):
        target_names = entry.get("target_node_names", [])
        if target_names:
            lc = build_layer_config(entry["layer_config"])
            specific_layer_config[lc] = target_names

    # FP overrides for specific nodes (passed via --fp-nodes CLI arg)
    fp_spec_name = "BFloat16Spec" if fp_precision == "bf16" else "Float16Spec"
    if fp_nodes:
        fp_lc = QLayerConfig(
            input_tensors=resolve_spec(fp_spec_name),
            weight=resolve_spec(fp_spec_name),
        )
        # Use exact node names (not regex) for --fp-nodes arguments.
        # Quark's get_all_target_nodes treats non-^ strings as exact matches.
        fp_node_patterns = list(fp_nodes)
        if fp_lc in specific_layer_config:
            specific_layer_config[fp_lc].extend(fp_node_patterns)
        else:
            specific_layer_config[fp_lc] = fp_node_patterns

    # FP overrides for op types
    if fp_op_types:
        fp_type_lc = QLayerConfig(
            input_tensors=resolve_spec(fp_spec_name),
            weight=resolve_spec(fp_spec_name),
        )
        layer_type_config[fp_type_lc] = fp_op_types

    # Extra options
    extra_options = config.get("extra_options", {})

    # Handle BFP attributes
    if "BFPAttributes" not in extra_options:
        # Check if any spec uses MX6
        has_mx6 = False
        if "MX6" in str(config.get("global_config", {})):
            has_mx6 = True
        for entry in config.get("specific_layer_config", []):
            if "MX6" in str(entry.get("layer_config", {})):
                has_mx6 = True
        if has_mx6:
            extra_options["BFPAttributes"] = DEFAULT_MICROEXPONENTS_PARAMS

    # Handle exclude configuration — nodes kept in their original (float)
    # precision. This must go through QConfig.exclude; extra_options
    # ["NodesToExclude"] alone is not honored by QConfig, so exclusion silently
    # did nothing. Keep NodesToExclude too for backward compatibility.
    exclude_cfg = config.get("exclude", {})
    exclude_nodes = exclude_cfg.get("node_names") if exclude_cfg else None
    if exclude_nodes:
        extra_options["NodesToExclude"] = exclude_nodes

    # Build QConfig — pass specific_layer_config so per-layer overrides take effect
    qconfig = QConfig(
        global_config=global_config,
        specific_layer_config=specific_layer_config if specific_layer_config else None,
        layer_type_config=layer_type_config if layer_type_config else None,
        exclude=exclude_nodes if exclude_nodes else None,
        extra_options=extra_options,
    )
    return qconfig


# =============================================================================
# Main quantization logic
# =============================================================================


def quantize_model(
    input_path: str,
    output_path: str,
    config_path: str,
    fp_nodes: list[str] | None = None,
    fp_op_types: list[str] | None = None,
    fp_precision: str = "bf16",
    calibration_paths: list[str] | None = None,
    num_calibration_samples: int = 1,
    random_seed: int = 2024,
    distribution: str = "positive",
) -> None:
    """Quantize model using Quark with JSON config."""
    if not QUARK_AVAILABLE:
        print("ERROR: Quark is not installed. Install it with:")
        print("  pip install quark-onnx")
        sys.exit(1)

    # Load config
    print(f"Loading config: {config_path}")
    with open(config_path) as f:
        config = json.load(f)

    # Build QConfig
    print("Building quantization config...")
    qconfig = build_qconfig_from_json(config, fp_nodes, fp_op_types, fp_precision)

    # Load model to check if we need calibration data
    model = load_model(input_path)

    # Prepare calibration reader
    use_random = config.get("extra_options", {}).get("UseRandomData", False)
    if use_random:
        print("  Using random calibration data (from config)")
        qconfig.extra_options["UseRandomData"] = True
        reader = None
    elif calibration_paths:
        print(f"  Loading calibration data from {len(calibration_paths)} file(s)")
        reader = CalibrationDataReader(
            model, calibration_paths, num_calibration_samples, random_seed
        )
    else:
        print(
            f"  Generating random calibration data (seed={random_seed}, distribution={distribution})"
        )
        reader = CalibrationDataReader(
            model, None, num_calibration_samples, random_seed, distribution
        )

    # Run quantization
    print(f"Quantizing: {input_path} → {output_path}")
    quantizer = ModelQuantizer(qconfig)
    quantizer.quantize_model(input_path, output_path, calibration_data_reader=reader)
    print("  Quantization complete!")

    # Report
    quantized = (
        onnx.load(output_path)
        if not output_path.endswith(".onnxtxt")
        else load_model(output_path)
    )
    n_q = sum(1 for n in quantized.graph.node if n.op_type == "QuantizeLinear")
    n_dq = sum(1 for n in quantized.graph.node if n.op_type == "DequantizeLinear")
    n_eq = sum(1 for n in quantized.graph.node if n.op_type == "ExtendedQuantizeLinear")
    n_edq = sum(
        1 for n in quantized.graph.node if n.op_type == "ExtendedDequantizeLinear"
    )
    print(f"  QDQ nodes: {n_q} Q + {n_dq} DQ")
    if n_eq or n_edq:
        print(f"  Extended QDQ: {n_eq} EQ + {n_edq} EDQ")
    print(f"  Total nodes: {len(quantized.graph.node)}")


# =============================================================================
# Exploration mode
# =============================================================================


def explore_sensitivity(
    input_path: str,
    config_path: str,
    num_samples: int = 50,
    seed: int = 2024,
) -> dict[str, float]:
    """Run sensitivity analysis to identify accuracy-critical layers.

    For each layer, computes the output range and estimates the quantization
    error when using INT8 vs BF16.

    Returns dict of {node_name: sensitivity_score} sorted by sensitivity.
    """
    import onnxruntime as ort

    model = load_model(input_path)
    input_info = get_model_input_info(model)

    # Get all compute nodes
    compute_ops = {
        "Conv",
        "Gemm",
        "MatMul",
        "Add",
        "Mul",
        "Sigmoid",
        "Relu",
        "Softmax",
        "LayerNormalization",
        "BatchNormalization",
    }
    target_nodes = [n for n in model.graph.node if n.op_type in compute_ops and n.name]

    if not target_nodes:
        print("WARNING: No named compute nodes found for sensitivity analysis")
        return {}

    # Add intermediate outputs
    model_copy = onnx.ModelProto()
    model_copy.CopyFrom(model)

    existing_outputs = {o.name for o in model_copy.graph.output}
    node_output_map = {}
    for node in model_copy.graph.node:
        if node.name and node.output and node.output[0] not in existing_outputs:
            node_output_map[node.name] = node.output[0]
            vi = onnx.helper.make_tensor_value_info(
                node.output[0], TensorProto.FLOAT, None
            )
            model_copy.graph.output.append(vi)

    # Run inference
    try:
        sess = ort.InferenceSession(
            model_copy.SerializeToString(), providers=["CPUExecutionProvider"]
        )
    except Exception as e:
        print(f"WARNING: Cannot create session for exploration: {e}")
        return {}

    rng = np.random.default_rng(seed)
    sensitivities: dict[str, list[float]] = {n.name: [] for n in target_nodes}

    for _ in range(min(num_samples, 10)):  # Use fewer samples for speed
        feeds = {}
        for info in input_info:
            if np.issubdtype(info["dtype"], np.floating):
                feeds[info["name"]] = _sample_float(
                    "positive", info["shape"], info["dtype"], rng
                )
            else:
                feeds[info["name"]] = rng.integers(
                    0, 100, size=info["shape"], dtype=info["dtype"]
                )

        try:
            output_names = [o.name for o in sess.get_outputs()]
            results = sess.run(output_names, feeds)
            result_map = dict(zip(output_names, results))
        except Exception:
            continue

        for node in target_nodes:
            if node.name in node_output_map:
                tensor_name = node_output_map[node.name]
                if tensor_name in result_map:
                    arr = result_map[tensor_name].astype(np.float64)
                    # Sensitivity = dynamic range (larger range → more quantization error)
                    dyn_range = float(np.max(np.abs(arr)))
                    sensitivities[node.name].append(dyn_range)

    # Average sensitivity scores
    avg_sensitivity = {}
    for name, scores in sensitivities.items():
        if scores:
            avg_sensitivity[name] = float(np.mean(scores))

    # Sort by sensitivity (highest first = most likely to lose accuracy in int8)
    sorted_sens = dict(
        sorted(avg_sensitivity.items(), key=lambda x: x[1], reverse=True)
    )
    return sorted_sens


def main():
    parser = argparse.ArgumentParser(description="Mixed-precision quantization")
    parser.add_argument("--input", "-i", required=True, help="Input float model")
    parser.add_argument("--output", "-o", required=True, help="Output quantized model")
    parser.add_argument("--config", "-c", required=True, help="Quark JSON config")
    parser.add_argument(
        "--fp-nodes", help="Comma-separated node names for FP precision"
    )
    parser.add_argument(
        "--fp-op-types", help="Comma-separated op types for FP precision"
    )
    parser.add_argument(
        "--fp-precision",
        choices=["bf16", "fp16"],
        default="bf16",
        help="Floating-point precision for specified layers",
    )
    parser.add_argument(
        "--calibration-data", nargs="+", help="Calibration data files (.npy or .npz)"
    )
    parser.add_argument(
        "--num-calibration-samples",
        type=int,
        default=1,
        help="Number of random calibration samples",
    )
    parser.add_argument(
        "--random-seed", type=int, default=2024, help="Random seed for calibration data"
    )
    parser.add_argument(
        "--explore",
        action="store_true",
        help="Enable exploration mode (sensitivity analysis)",
    )
    parser.add_argument(
        "--explore-num-samples",
        type=int,
        default=50,
        help="Number of samples for exploration",
    )
    parser.add_argument(
        "--distribution",
        choices=list(DISTRIBUTIONS.keys()),
        default="positive",
        help="Input distribution for random calibration data generation. "
        "Use 'positive' for image models (0-1 range), 'normal' for general "
        "models, 'small' for sigmoid/tanh-heavy networks. (default: positive)",
    )
    args = parser.parse_args()

    fp_nodes = args.fp_nodes.split(",") if args.fp_nodes else None
    fp_op_types = args.fp_op_types.split(",") if args.fp_op_types else None

    if args.explore:
        print("Running sensitivity analysis...")
        sens = explore_sensitivity(
            args.input, args.config, args.explore_num_samples, args.random_seed
        )
        print("\nLayer sensitivity (highest = most accuracy-critical):")
        for name, score in list(sens.items())[:20]:
            print(f"  {name}: {score:.4f}")
        print("\nRecommendation: Move the top layers to BF16 for better accuracy.")
        if sens:
            top_layers = list(sens.keys())[:5]
            print(f"Suggested --fp-nodes: {','.join(top_layers)}")

    quantize_model(
        args.input,
        args.output,
        args.config,
        fp_nodes=fp_nodes,
        fp_op_types=fp_op_types,
        fp_precision=args.fp_precision,
        calibration_paths=args.calibration_data,
        num_calibration_samples=args.num_calibration_samples,
        random_seed=args.random_seed,
        distribution=args.distribution,
    )


if __name__ == "__main__":
    main()
