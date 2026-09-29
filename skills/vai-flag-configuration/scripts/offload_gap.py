# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""What is still running on the CPU after a compile, and why.

For a max-offload goal the useful question is not "which flag generally helps
performance" -- it is "which operators did the compiler fail to place on the
NPU, and what did it say about each one". That is a property of the *compiled
graph*, and the compiler already records it: every operator it pushes back to
the CPU becomes an ``xten_nn.subgraph`` with ``Reason = "CpuBecause"``, plus an
``Op`` attribute naming the operation responsible and a ``Message`` explaining
it. Those subgraphs live in the ``*.chained_kernel.tosa.mlir`` the compiler
writes when ``keep_outputs`` is on.

This module reads that file and reports the facts: which ops are missing, how
many nodes each costs, and the compiler's own message for each. It deliberately
does **not** infer a root cause. Messages are free-form strings emitted from
many places across the frontend, backend and tiling engine, so anything beyond
the handful of markers in ``_ROUTES`` -- each cited to the code that emits it --
is reported verbatim as ``unclassified`` rather than guessed at.

No MLIR bindings required, so it also works from compile artifacts alone. When the
product environment is available, ``flexml.mlir.stats.generate_report_for_vaiml``
gives the same ``CpuBecause`` grouping plus computed offloading percentages and
is the richer source; cite whichever one was actually used.

Route ``source`` strings name the compiler stage a message comes from, never a
path into the compiler tree: this module ships to users who do not have that
tree, so a path there would not be a citation they could follow.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from flag_catalog import PROPOSABLE, FlagSpec
from model_ops import OP_KEYWORDS

_CPU_BECAUSE = 'Reason = "CpuBecause"'
_SUBGRAPH = "xten_nn.subgraph"
_ATTRIBUTES = "attributes"


@dataclass
class CpuFallback:
    """One operator the compiler placed on the CPU."""

    op: str  # the `Op` attribute: the operation responsible
    layer: str  # the `LayerName` attribute
    message: str  # the compiler's own explanation, verbatim


@dataclass
class MessageRoute:
    """How a ``CpuBecause`` message should be followed up, if we can tell."""

    route: str
    source: str = ""  # what says so -- empty for `unclassified`
    action: str = ""  # suggested next step -- empty for `unclassified`


@dataclass
class GapEntry:
    """All CPU fallbacks attributed to one operation type."""

    op: str
    count: int
    messages: dict[str, int]
    layers: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)


# Message markers we can attribute to a specific origin, each cited to the code
# that emits it. Ordered: the two exact markers first, then the origin tags that
# the pdll/message sources use as a suffix convention.
_ROUTES: list[tuple[str, MessageRoute]] = [
    (
        "User requested this layer to be partitioned out",
        MessageRoute(
            "user-config",
            "a user-configured partition-out request",
            "this run's configuration pushed the layer to the CPU — review "
            "active flags before proposing new ones",
        ),
    ),
    (
        "Unwrapping layer with",
        MessageRoute(
            "tiling-unwrap",
            "a tiling-engine placement diagnostic",
            "the tiling engine could not place the named kernel — consider "
            "spilling analysis or a kernel-specific chaining flag",
        ),
    ),
    (
        "[FE]",
        MessageRoute(
            "fe",
            "the compiler frontend ([FE] message tag)",
            "the frontend did not match the operator -- look for a flag that "
            "decomposes or rewrites it, else quantization / custom-op support",
        ),
    ),
    (
        "[BE]",
        MessageRoute(
            "be",
            "the compiler backend ([BE] message tag)",
            "no backend kernel for this shape/dtype -- a rewrite flag "
            "(e.g. conv<->gemm) may reach a supported kernel",
        ),
    ),
    (
        "[MLLIB]",
        MessageRoute(
            "mllib",
            "a kernel-library constraint message",
            "a kernel constraint rejected this layer — read the compiler "
            "message before proposing a flag",
        ),
    ),
]


def classify_message(message: str) -> MessageRoute:
    """Route a ``CpuBecause`` message, or admit that we cannot.

    Only the cited markers above are recognised. Everything else returns
    ``unclassified`` with no source and no action: the caller must show the
    message as-is instead of inventing a cause for it.
    """
    for marker, route in _ROUTES:
        if marker in message:
            return route
    return MessageRoute("unclassified")


# --------------------------------------------------------------------------
# Reading the CpuBecause subgraphs out of the chained-kernel MLIR
# --------------------------------------------------------------------------


def _attribute_block(text: str, start: int) -> tuple[str, int] | None:
    """Slice the ``attributes { ... }`` dict that begins at/after ``start``.

    Brace matching is depth-aware and skips string literals, because attribute
    dicts nest (``Operands = [ { Name = ... } ]``) and a message may itself
    contain a brace.
    """
    open_at = text.find("{", start)
    if open_at == -1:
        return None
    depth = 0
    i = open_at
    while i < len(text):
        char = text[i]
        if char == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return text[open_at + 1 : i], i
        i += 1
    return None


def _top_level_attrs(block: str) -> dict[str, str]:
    """``Key = "value"`` pairs at depth 0 of an attribute block.

    Depth filtering is what keeps a nested operand's ``Name`` from being read as
    the subgraph's own attribute.
    """
    attrs: dict[str, str] = {}
    depth = 0
    i = 0
    key_start = 0
    while i < len(block):
        char = block[i]
        if char == '"':
            i += 1
            while i < len(block) and block[i] != '"':
                i += 2 if block[i] == "\\" else 1
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif char == "," and depth == 0:
            key_start = i + 1
        elif char == "=" and depth == 0:
            key = block[key_start:i].strip()
            rest = block[i + 1 :].lstrip()
            if key.isidentifier() and rest.startswith('"'):
                end = 1
                while end < len(rest) and rest[end] != '"':
                    end += 2 if rest[end] == "\\" else 1
                attrs[key] = rest[1:end]
        i += 1
    return attrs


def cpu_fallbacks(mlir_path: str | Path) -> list[CpuFallback]:
    """Every ``Reason = "CpuBecause"`` subgraph, in graph order."""
    text = Path(mlir_path).read_text(errors="replace")
    found: list[CpuFallback] = []
    search = 0
    while True:
        at = text.find(_SUBGRAPH, search)
        if at == -1:
            break
        search = at + len(_SUBGRAPH)
        attr_at = text.find(_ATTRIBUTES, at)
        if attr_at == -1:
            break
        sliced = _attribute_block(text, attr_at)
        if sliced is None:
            break
        block, end = sliced
        search = end
        if _CPU_BECAUSE not in block:
            continue
        attrs = _top_level_attrs(block)
        if attrs.get("Reason") != "CpuBecause":
            continue
        found.append(
            CpuFallback(
                op=attrs.get("Op", ""),
                layer=attrs.get("LayerName", ""),
                message=attrs.get("Message", ""),
            )
        )
    return found


def offload_gap(mlir_path: str | Path) -> list[GapEntry]:
    """The offload gap: ops still on the CPU, biggest first.

    "Biggest" is node count -- how many nodes of that op type were pushed back.
    Empty for a fully offloaded model.
    """
    by_op: dict[str, list[CpuFallback]] = {}
    for fallback in cpu_fallbacks(mlir_path):
        by_op.setdefault(fallback.op, []).append(fallback)

    entries = [
        GapEntry(
            op=op,
            count=len(items),
            messages=dict(Counter(f.message for f in items)),
            layers=[f.layer for f in items],
            routes=sorted({classify_message(f.message).route for f in items}),
        )
        for op, items in by_op.items()
    ]
    entries.sort(key=lambda e: (-e.count, e.op))
    return entries


def find_chained_kernel_mlir(work_dir: str | Path) -> Path | None:
    """Locate the compiler's chained-kernel MLIR under a compile output dir.

    Only written when the compile ran with ``keep_outputs: true`` -- the same
    precondition the L3-spilling check needs.
    """
    for path in sorted(Path(work_dir).rglob("*.chained_kernel.tosa.mlir")):
        return path
    return None


# --------------------------------------------------------------------------
# From the gap to candidate flags
# --------------------------------------------------------------------------


# Category tags shared by most ops. A flag matching only these is a weak signal
# -- "elementwise" appears in nearly every chaining flag's description -- so it
# is ranked below, and labelled apart from, a flag that names the missing op.
_GENERIC_KEYWORDS = frozenset({"elementwise", "unary", "binary"})


def match_gap_to_flags(
    gap: list[GapEntry], catalog: list[FlagSpec]
) -> list[tuple[FlagSpec, str]]:
    """Catalog flags that target an op in the gap -- and only those.

    The point of the gap is to stop proposing flags for work the NPU already
    does: a Conv flag is not a fix when Conv is offloaded and QuantizeLinear is
    not. Flags the relevance policy holds back are skipped here too, so a gap
    match can never smuggle an excluded flag back into a proposal.

    Ordered by how specifically a flag targets the gap, then by how many
    CPU-bound nodes it could reclaim, so the top of the list is the part worth
    spending a compile on.
    """
    keyword_to_ops: dict[str, set[str]] = {}
    for entry in gap:
        for keyword in OP_KEYWORDS.get(entry.op, [entry.op.lower()]):
            keyword_to_ops.setdefault(keyword, set()).add(entry.op)

    counts = {entry.op: entry.count for entry in gap}
    ranked: list[tuple[bool, int, str, FlagSpec, str]] = []
    for spec in catalog:
        if spec.relevance != PROPOSABLE:
            continue
        haystack = f"{spec.name} {spec.description}".lower()
        hits = {kw: ops for kw, ops in keyword_to_ops.items() if kw in haystack}
        if not hits:
            continue
        hit_ops = {op for ops in hits.values() for op in ops}
        specific = bool(set(hits) - _GENERIC_KEYWORDS)
        covered = sum(counts[op] for op in hit_ops)
        listed = ", ".join(
            f"{op} x{counts[op]}" for op in sorted(hit_ops, key=lambda o: -counts[o])
        )
        qualifier = "" if specific else " (generic element-wise match, weaker evidence)"
        reason = (
            f"still on CPU: {listed}; flag {spec.name} ({spec.path}) "
            f"targets it{qualifier}"
        )
        ranked.append((not specific, -covered, spec.name, spec, reason))

    ranked.sort(key=lambda item: item[:3])
    return [(spec, reason) for *_, spec, reason in ranked]
