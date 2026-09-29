# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from onnx import GraphProto, TensorProto, ValueInfoProto


def _onnx_dtype_to_numpy(elem_type: int) -> np.dtype:
    """
    Map ONNX TensorProto.DataType enum to numpy dtype.
    """
    mapping = {
        TensorProto.FLOAT: np.float32,
        TensorProto.DOUBLE: np.float64,
        TensorProto.FLOAT16: np.float16,
        TensorProto.BOOL: np.bool_,
        TensorProto.UINT8: np.uint8,
        TensorProto.INT8: np.int8,
        TensorProto.UINT16: np.uint16,
        TensorProto.INT16: np.int16,
        TensorProto.INT32: np.int32,
        TensorProto.INT64: np.int64,
        TensorProto.UINT32: np.uint32,
        TensorProto.UINT64: np.uint64,
        TensorProto.COMPLEX64: np.complex64,
        TensorProto.COMPLEX128: np.complex128,
    }

    # Handle bfloat16 if available
    if hasattr(TensorProto, "BFLOAT16") and elem_type == TensorProto.BFLOAT16:
        if "bfloat16" in np.sctypeDict:
            return np.dtype("bfloat16")
        raise ValueError("Input dtype 'bfloat16' is not supported by this numpy build.")

    if elem_type in mapping:
        return np.dtype(mapping[elem_type])

    raise ValueError(
        f"Unsupported or unrecognized ONNX tensor element type: {elem_type}"
    )


def _shape_from_value_info(
    value_info: ValueInfoProto,
) -> Tuple[Optional[int], ...]:
    """
    Extract shape from ONNX ValueInfoProto.
    Returns tuple of ints for static dims, None for dynamic dims.
    """
    dims: List[Optional[int]] = []
    tensor_type = value_info.type.tensor_type

    if not tensor_type.HasField("shape"):
        return tuple()  # scalar tensor

    for dim in tensor_type.shape.dim:
        if dim.HasField("dim_value"):
            dims.append(dim.dim_value)
        else:
            # dim_param (symbolic) or empty -> dynamic
            dims.append(None)

    return tuple(dims)


def _generate_random_tensor(
    rng: np.random.Generator,
    dtype: np.dtype,
    shape: Tuple[Optional[int], ...],
) -> np.ndarray:
    """
    Generate a random numpy array matching dtype and shape.
    Special cases int8 and uint8 to give full-range values.
    """
    for d in shape:
        if isinstance(d, int) and d > 0:
            continue
        raise ValueError(f"Dynamic dimension {d} is not supported")

    concrete_shape = tuple(d for d in shape)

    # Special case for uint8: uniform over [0, 256)
    if dtype == np.uint8:
        arr = rng.integers(0, 256, size=concrete_shape, dtype=np.uint8)
        return arr
    # Special case for int8: uniform over [-128, 128)
    if dtype == np.int8:
        arr = rng.integers(-128, 128, size=concrete_shape, dtype=np.int8)
        return arr

    if np.issubdtype(dtype, np.floating):
        arr = rng.standard_normal(concrete_shape).astype(dtype)
        return arr
    if np.issubdtype(dtype, np.bool_):
        arr = rng.integers(0, 2, size=concrete_shape, dtype=np.int8).astype(bool)
        return arr
    if np.issubdtype(dtype, np.integer):
        # moderate range for stability
        low, high = (-10, 11) if np.issubdtype(dtype, np.signedinteger) else (0, 11)
        arr = rng.integers(low, high, size=concrete_shape, dtype=dtype)
        return arr
    if np.issubdtype(dtype, np.complexfloating):
        real = rng.standard_normal(concrete_shape).astype(dtype.type(0).real.dtype)
        imag = rng.standard_normal(concrete_shape).astype(dtype.type(0).real.dtype)
        return (real + 1j * imag).astype(dtype)

    raise ValueError(f"Unsupported numpy dtype for random generation: {dtype}")


def prepare_random_inputs_from_graph(
    graph: GraphProto,
    rng: np.random.Generator,
) -> Dict[str, np.ndarray]:
    """
    Build feeds dict of name -> random tensor based on ONNX graph input metadata.
    Skips inputs that are also initializers (constant weights).
    """
    # Get names of initializers to skip them (they are weights, not inputs)
    initializer_names = {init.name for init in graph.initializer}

    feeds: Dict[str, np.ndarray] = {}
    for inp in graph.input:
        if inp.name in initializer_names:
            continue  # Skip initializers

        dtype = _onnx_dtype_to_numpy(inp.type.tensor_type.elem_type)
        shape = _shape_from_value_info(inp)
        feeds[inp.name] = _generate_random_tensor(rng, dtype, shape)

    return feeds


def generate_and_save_inputs(
    model_graph: GraphProto,
    input_dir: Optional[Path] = None,
    seed: int = 42,
    model_stem: str = "model",
) -> Dict[str, np.ndarray]:
    """Generate random inputs and save to input_dir.

    Args:
        model_graph: ONNX graph
        input_dir: Directory to save inputs (default: <model_stem>_inputs/)
        seed: Random seed
        model_stem: Model stem for default input_dir name

    Returns:
        Dict mapping input names to numpy arrays
    """
    if input_dir is None:
        input_dir = Path(f"{model_stem}_inputs")
    input_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating random inputs (seed={seed})...")
    rng = np.random.default_rng(seed=seed)
    feeds = prepare_random_inputs_from_graph(model_graph, rng)

    # Save as .npz
    np.savez(input_dir / "inputs.npz", **feeds)
    print(f"Saved inputs.npz to {input_dir}/")

    # Save individual .npy files
    for name, arr in feeds.items():
        safe_name = name.replace("/", "_").replace("\\", "_")
        filepath = input_dir / f"{safe_name}.npy"
        np.save(filepath, arr)
        print(f"  Input '{name}': shape={arr.shape}, dtype={arr.dtype} -> {filepath}")

    return feeds


def load_inputs(input_dir: Path, model_graph: GraphProto) -> Dict[str, np.ndarray]:
    """Load inputs from input_dir.

    Args:
        input_dir: Directory containing inputs.npz or individual .npy files
        model_graph: ONNX graph to get input names

    Returns:
        Dict mapping input names to numpy arrays
    """
    # Try loading inputs.npz first
    input_npz = input_dir / "inputs.npz"
    if input_npz.exists():
        print(f"Loading inputs from {input_npz}")
        data = np.load(input_npz)
        feeds = dict(data)
        for name, arr in feeds.items():
            print(f"  Input '{name}': shape={arr.shape}, dtype={arr.dtype}")
        return feeds

    # Try loading individual .npy files
    initializer_names = {init.name for init in model_graph.initializer}
    graph_inputs = [
        inp for inp in model_graph.input if inp.name not in initializer_names
    ]

    feeds = {}
    for inp in graph_inputs:
        safe_name = inp.name.replace("/", "_").replace("\\", "_")
        npy_file = input_dir / f"{safe_name}.npy"
        if npy_file.exists():
            arr = np.load(npy_file)
            feeds[inp.name] = arr
            print(
                f"  Loaded '{inp.name}' from {npy_file}: shape={arr.shape}, dtype={arr.dtype}"
            )
        else:
            raise FileNotFoundError(
                f"  Input '{inp.name}' ({npy_file}) not found in {input_dir}"
            )

    if not feeds:
        raise FileNotFoundError(f"No inputs found in {input_dir}")

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

    if not vitisai_config.exists():
        print(f"File {vitisai_config} does not exist in the filesystem.")
        sys.exit(1)

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
