# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""Census a model's operator types and match them to catalog flags.

Used at startup so the first flag proposal is grounded in the operator types the
model actually contains (e.g. Conv-heavy -> conv<->gemm flags).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import prod
from pathlib import Path

import onnx
from flag_catalog import FlagSpec
from onnx import shape_inference

# Element-wise ops split by arity. These are guidance so the census can steer
# the agent toward the relevant compile flags; the list only needs to be broadly
# representative -- a missing op just means the agent may iterate more. Keep it in
# sync as new ops become supported.
_ELEMENTWISE_UNARY = [
    "LeakyRelu",
    "Tanh",
    "Clip",
    "Cast",
    "Floor",
    "Relu",
    "Sigmoid",
    "Exp",
    "Abs",
    "Acos",
    "Acosh",
    "Asin",
    "Asinh",
    "Atan",
    "Atanh",
    "Ceil",
    "Cos",
    "Cosh",
    "DequantizeLinear",
    "Erf",
    "Hardmax",
    "Log",
    "Neg",
    "NonZero",
    "Not",
    "Optional",
    "OptionalGetElement",
    "OptionalHasElement",
    "QuantizeLinear",
    "Reciprocal",
    "Sign",
    "Sin",
    "Sinh",
    "Size",
    "StringNormalizer",
    "Tan",
    "Celu",
    "Elu",
    "HardSigmoid",
    "HardSwish",
    "Selu",
    "Shrink",
    "Softplus",
    "Softsign",
    "ThresholdedRelu",
]
_ELEMENTWISE_BINARY = [
    "Add",
    "Mul",
    "Equal",
    "Where",
    "Div",
    "Min",
    "Max",
    "Sub",
    "Less",
    "Greater",
    "Pow",
    "And",
    "BitShift",
    "Mean",
    "Mod",
    "Sum",
    "Xor",
    "CastLike",
    "GreaterOrEqual",
    "LessOrEqual",
    "PRelu",
]

# op type -> keywords to look for in a flag's name/description (lower-cased)
OP_KEYWORDS: dict[str, list[str]] = {
    "Conv": ["conv"],
    "ConvTranspose": ["convtranspose", "conv"],
    "Gemm": ["gemm"],
    "MatMul": ["matmul", "gemm"],
    "LSTM": ["lstm"],
    "Softmax": ["softmax"],
    "MaxPool": ["maxpool", "pool"],
    "AveragePool": ["averagepool", "avgpool", "pool"],
    "GlobalAveragePool": ["averagepool", "avgpool", "pool"],
    "Gather": ["gather"],
    "Slice": ["slice"],
    "Concat": ["concat"],
    "Transpose": ["transpose"],
    "Reshape": ["reshape"],
    **{op: ["elementwise", "unary"] for op in _ELEMENTWISE_UNARY},
    **{op: ["elementwise", "binary"] for op in _ELEMENTWISE_BINARY},
    # Ops whose flags are named after a *pattern* rather than the op, so plain
    # substring matching never reaches them. These entries come last on purpose:
    # they override (and extend) the element-wise defaults above for the ops
    # that appear in both, e.g. Quantize/DequantizeLinear are element-wise unary
    # AND the subject of quant/dequant matcher flags. These entries come last on
    # purpose: they override (and extend) the element-wise defaults above.
    "QuantizeLinear": ["elementwise", "unary", "qdq", "quantiz"],
    "DequantizeLinear": ["elementwise", "unary", "qdq", "quantiz", "dequantiz"],
    "RotaryEmbedding": ["rope", "rotary"],
    "GroupQueryAttention": ["gqa", "groupquery", "attention"],
    "MultiHeadAttention": ["mha", "attention"],
    "Attention": ["mha", "attention"],
    "MatMulNBits": ["matmulnbits", "matmul", "gemm"],
    "GridSample": ["gridsample"],
    "ScatterElements": ["scatter"],
    "LpNormalization": ["lpnorm", "normalization"],
    "InstanceNormalization": ["instancenorm", "normalization"],
    "LayerNormalization": ["layernorm", "normalization"],
    "Tile": ["tile"],
    "GatherElements": ["gather"],
    "Resize": ["resize"],
    "Pad": ["pad"],
    "TopK": ["topk"],
    # Trigonometric ops share one flag (trig-decompositions); they are also
    # element-wise unary, hence both tags.
    **{
        op: ["elementwise", "unary", "trig"]
        for op in ("Cos", "Tan", "Sinh", "Cosh", "Asinh", "Acosh", "Atanh")
    },
}


def op_type_counts(model: onnx.ModelProto | str | Path) -> dict[str, int]:
    proto = model if isinstance(model, onnx.ModelProto) else onnx.load(str(Path(model)))
    return dict(Counter(node.op_type for node in proto.graph.node))


# --------------------------------------------------------------------------
# Custom-op detection
# --------------------------------------------------------------------------
# Standard ONNX operator domains -- anything else is a custom op that needs a
# `custom_ops` op_config entry in the vitisai_config.json.
_STANDARD_ONNX_DOMAINS = {
    "",
    "ai.onnx",
    "ai.onnx.ml",
    "ai.onnx.training",
    "ai.onnx.preview.training",
}
# Quark quantization custom ops that must be supported (MX6 BFP QDQ, EQ/EDQ).
_QUARK_DOMAIN_PREFIX = "com.amd.quark"
_QUANT_CUSTOM_OP_TYPES = {"ExtendedQuantizeLinear", "ExtendedDequantizeLinear"}


@dataclass
class CustomOp:
    name: str  # node name
    op_type: str
    domain: str
    is_quant: bool  # a Quark quantization custom op (MX6 BFP QDQ / EQ-EDQ)


def detect_custom_ops(model: onnx.ModelProto | str | Path) -> list[CustomOp]:
    """Nodes using a non-standard operator domain.

    ``is_quant`` marks the Quark quantization custom ops (MX6 BFP QDQ,
    Extended[De]QuantizeLinear) that must be integrated via a `custom_ops`
    op_config; other non-standard-domain nodes are general custom ops.
    """
    proto = _load(model)
    found: list[CustomOp] = []
    for node in proto.graph.node:
        domain = node.domain or ""
        is_quant = (
            domain.startswith(_QUARK_DOMAIN_PREFIX)
            or node.op_type in _QUANT_CUSTOM_OP_TYPES
        )
        if domain not in _STANDARD_ONNX_DOMAINS or is_quant:
            found.append(
                CustomOp(
                    name=node.name,
                    op_type=node.op_type,
                    domain=domain,
                    is_quant=is_quant,
                )
            )
    return found


def match_flags_to_ops(
    op_counts: dict[str, int], catalog: list[FlagSpec]
) -> list[tuple[FlagSpec, str]]:
    keyword_to_ops: dict[str, set[str]] = {}
    for op in op_counts:
        for kw in OP_KEYWORDS.get(op, [op.lower()]):
            keyword_to_ops.setdefault(kw, set()).add(op)

    matches: list[tuple[FlagSpec, str]] = []
    for spec in catalog:
        haystack = f"{spec.name} {spec.description}".lower()
        hit_ops: set[str] = set()
        for needle, ops in keyword_to_ops.items():
            if needle in haystack:
                hit_ops |= ops
        if hit_ops:
            reason = (
                f"model contains {sorted(hit_ops)}; flag {spec.name} "
                f"({spec.path}) targets it"
            )
            matches.append((spec, reason))
    return matches


# --------------------------------------------------------------------------
# Pre-compile shape / size inspection
# --------------------------------------------------------------------------


@dataclass
class LargeLayer:
    name: str  # node name
    op_type: str
    output: str  # produced tensor name
    elements: int  # product of the known output dims


def _load(model: onnx.ModelProto | str | Path) -> onnx.ModelProto:
    return model if isinstance(model, onnx.ModelProto) else onnx.load(str(Path(model)))


def _dims(value_info: onnx.ValueInfoProto) -> list[int | None]:
    dims: list[int | None] = []
    for d in value_info.type.tensor_type.shape.dim:
        # A concrete dimension has dim_value > 0; symbolic/unknown dims -> None.
        dims.append(
            d.dim_value if d.HasField("dim_value") and d.dim_value > 0 else None
        )
    return dims


def input_shapes(model: onnx.ModelProto | str | Path) -> dict[str, list[int | None]]:
    """Declared shapes of the graph inputs (excluding initializers)."""
    proto = _load(model)
    initializers = {init.name for init in proto.graph.initializer}
    return {
        vi.name: _dims(vi) for vi in proto.graph.input if vi.name not in initializers
    }


def output_shapes(model: onnx.ModelProto | str | Path) -> dict[str, list[int | None]]:
    """Per-tensor shapes after ONNX shape inference (value_info + graph outputs)."""
    inferred = shape_inference.infer_shapes(_load(model))
    shapes: dict[str, list[int | None]] = {}
    for vi in list(inferred.graph.value_info) + list(inferred.graph.output):
        shapes[vi.name] = _dims(vi)
    return shapes


def _element_count(dims: list[int | None]) -> int:
    known = [d for d in dims if d is not None]
    return int(prod(known)) if known else 0


def large_layers(
    model: onnx.ModelProto | str | Path, min_elements: int = 1_000_000
) -> list[LargeLayer]:
    """Nodes whose (inferred) output tensor has >= min_elements, largest first.

    Uses ONNX shape inference so the result is available BEFORE any compile. A
    layer whose output shape cannot be inferred (element count 0) is skipped.
    """
    proto = _load(model)
    shapes = output_shapes(proto)
    layers: list[LargeLayer] = []
    for node in proto.graph.node:
        for out in node.output:
            elements = _element_count(shapes.get(out, []))
            if elements >= min_elements:
                layers.append(
                    LargeLayer(
                        name=node.name,
                        op_type=node.op_type,
                        output=out,
                        elements=elements,
                    )
                )
    layers.sort(key=lambda layer: layer.elements, reverse=True)
    return layers


# --------------------------------------------------------------------------
# Pre-compile FM-spill proxy (heuristic fallback)
# --------------------------------------------------------------------------
# When no backend `DetailedSpillingAnalysis.csv` is available, this gives a
# rough, on-host estimate of which layers are likely to spill to L3.
# It is NOT a substitute for the report: it
# ignores tiling/layout and simply assumes a whole output feature map must fit
# in one column's L2. Any recommendation derived from it must be labelled as a
# heuristic, not as a fact from the spilling report.

L2_BYTES_PER_COLUMN = 512 * 1024  # STX / T50 / T20

# ONNX elem_type -> bytes per element.
_ELEM_BYTES: dict[int, int] = {
    onnx.TensorProto.FLOAT: 4,
    onnx.TensorProto.FLOAT16: 2,
    onnx.TensorProto.BFLOAT16: 2,
    onnx.TensorProto.DOUBLE: 8,
    onnx.TensorProto.INT8: 1,
    onnx.TensorProto.UINT8: 1,
    onnx.TensorProto.INT16: 2,
    onnx.TensorProto.UINT16: 2,
    onnx.TensorProto.INT32: 4,
    onnx.TensorProto.UINT32: 4,
    onnx.TensorProto.INT64: 8,
    onnx.TensorProto.UINT64: 8,
    onnx.TensorProto.BOOL: 1,
}
_DEFAULT_ELEM_BYTES = 4  # conservative when the dtype is unknown


@dataclass
class FmSpillEstimate:
    name: str  # node name
    op_type: str
    output: str  # produced tensor name
    elements: int
    bytes: int  # estimated footprint = elements * dtype size


def estimate_fm_spill_candidates(
    model: onnx.ModelProto | str | Path,
    l2_bytes: int = L2_BYTES_PER_COLUMN,
) -> list[FmSpillEstimate]:
    """Heuristic: output FMs whose estimated footprint exceeds L2, largest first.

    Available BEFORE any compile (uses ONNX shape inference), so it is the
    fallback signal when a `DetailedSpillingAnalysis.csv` does not exist. Callers
    must present its output as a heuristic estimate, not a report fact.
    """
    inferred = shape_inference.infer_shapes(_load(model))
    elem_bytes: dict[str, int] = {}
    for vi in (
        list(inferred.graph.value_info)
        + list(inferred.graph.output)
        + list(inferred.graph.input)
    ):
        elem_bytes[vi.name] = _ELEM_BYTES.get(
            vi.type.tensor_type.elem_type, _DEFAULT_ELEM_BYTES
        )
    shapes = {
        vi.name: _dims(vi)
        for vi in list(inferred.graph.value_info) + list(inferred.graph.output)
    }
    out: list[FmSpillEstimate] = []
    for node in inferred.graph.node:
        for tensor in node.output:
            elements = _element_count(shapes.get(tensor, []))
            if elements <= 0:
                continue
            nbytes = elements * elem_bytes.get(tensor, _DEFAULT_ELEM_BYTES)
            if nbytes > l2_bytes:
                out.append(
                    FmSpillEstimate(
                        name=node.name,
                        op_type=node.op_type,
                        output=tensor,
                        elements=elements,
                        bytes=nbytes,
                    )
                )
    out.sort(key=lambda e: e.bytes, reverse=True)
    return out
