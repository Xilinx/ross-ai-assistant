#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Post-compile codegen analysis of AIE build artifacts.

A small, high-precision "codegen health" checker that runs after a
VitisAI/aiecompiler build and inspects the per-core artifacts for known
anti-patterns. Findings that are *certain* to be wrong and have a canonical fix
are reported as ERRORS (they fail the build) -- a non-fatal warning is
functionally a no-op in an automated loop, so we only error on things we are
sure about, each carrying a machine-readable code and the fix. Uncertain /
intent-dependent findings are ADVISORY (reported, do not fail the build).

Artifacts inspected (under the compile cache):
  * .lst disassembly  -- soft-float / emulation symbol calls, rounding mode.
  * .opt.yaml         -- optimization records: the compiler's own basic blocks
                         with an opcode histogram each. Only emitted with
                         '--Xpreproc=-fsave-optimization-record'; a rule needing
                         them is skipped when they are absent.
  * <core>.map        -- static (.bss) array sizes.
  * reports/report_heap.txt -- heap usage.
  * mladf_compiler_report.json -- L1/L2 buffer placement (tile overflow /
                         region overlap).
  * top.json          -- ADF graph description (custom-op output dtypes).

Rules:
  ERROR:
    NO_SOFT_FLOAT_DIV  -- `__divsf3` (scalar software float-divide) in .lst.
                          Fix: use the hardware reciprocal `aie::inv`.
    SCALAR_FLOAT_KERNEL-- kernel dominated by scalar float-emulation math (many
                          __*sf3 calls, low vector-op ratio) => not using the
                          mmul/vector unit. Fix: aie::mmul + vectorize.
    LARGE_STATIC_ARRAY -- a user `.bss` (static/global) symbol > 256 B.
                          A large working set does not belong in static/stack/
                          heap; hold it in the oversized async OFM buffer
                          (the "psum-in-OFM" idiom, see conv2d tutorial).
    HEAP_USED          -- heap allocation above 64 B.
                          Same fix: keep the working set in OFM scratch.
    L1_BUFFER_OVERSHOOT -- a custom-op L1 buffer copy ends past the 64 KB tile top
                          (0x10000); the store leaves the core -> silent board
                          hang. Fix: shrink the L1 scratch/LUT tail or move it.
    L1_BUFFER_OVERLAP  -- a custom-op L1 buffer copy overlaps another buffer or a
                          framework region (stack/heap/lcp/sync) in the same
                          phase. Fix: resize/relocate one region in the tiling.
    L2_BUFFER_OVERLAP  -- two L2 (memtile) buffers of a custom-op layer share
                          bytes in a global per-row address (column*512KB+offset),
                          covering same-memtile and cross-memtile spill. Fix:
                          resize/relocate one L2 buffer in the tiling.
    HW_EXP2_ON_INT8_OUTPUT
                       -- hardware exp2 (vexp2, ~bf16 accuracy) feeding a
                          quantized int8 output; the error flips int8 LSBs. Fix:
                          exact int8-keyed LUT, sum/reciprocal in fp32.
    ROUNDING_MODE_NOT_CONV_EVEN
                       -- int8/uint8-output custom op whose rounding mode is not
                          conv_even (no `movx crrnd, #0xc` in the core .lst).
                          Fix: set_rounding(conv_even) at entry.

Usage:
    python3 post_compile_checks.py <cache_dir> [--json]

Exit code: 1 if any ERROR-severity finding, else 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from post_compile_utils import (
    CUSTOM_OP_NS,
    custom_op_output_dtypes,
    find_core_lsts,
    find_core_maps,
    find_lst_files,
    find_report,
    max_report_value,
    remark_facts,
    walk_dicts,
)

# -- thresholds -------------------------------------------------------------
STATIC_ARRAY_MAX_BYTES = 256  # a static/global .bss symbol larger than this -> error
HEAP_MAX_BYTES = 64  # heap use above this -> error (small allocs tolerated)

# SCALAR_FLOAT_KERNEL: a kernel dominated by scalar float-emulation math (i.e.
# not using the vector/matrix unit) is catastrophically slow. Fire only when the
# scalar float-call count is both absolutely large AND large relative to the
# vector float work (calibrated with wide margin: good mmul kernel = 2 calls /
# ratio 0.009; scalar 45 ms kernel = 35 calls / ratio 0.875).
SCALAR_FLOAT_ARITH = ("__mulsf3", "__addsf3", "__subsf3", "__divsf3")
VECTOR_FLOAT_OPS = ("vmul.f", "vmac.f", "vadd.f", "vsub.f")
SCALAR_FLOAT_MIN_CALLS = 16  # absolute floor on scalar float-call sites
SCALAR_FLOAT_RATIO = 0.25  # scalar_calls / (vector_ops + 1) above this -> error

# Framework .bss symbols to ignore for the static-array check (not user arrays).
FRAMEWORK_BSS = ("lcpPing", "lcpPong")


YAML_EXP2_PREFIX = "INST_VEXP2"  # InstructionMix key for the hardware exp2 (vexp2)

# set_rounding(mode) lowers to `movx crrnd, #<imm>`; conv_even is 0xc on AIE2PS.
CRRND_CONV_EVEN = 0xC


# L1 is 64 KB split into 4 data-memory banks of 16 KB (a/b/c/d = address spaces
# 5/6/7/8). A buffer's bank is therefore just its L1 offset / 16 KB.
L1_BANK_SIZE_BYTES = 0x4000
L1_NUM_BANKS = 4
L1_SIZE_BYTES = L1_BANK_SIZE_BYTES * L1_NUM_BANKS  # 0x10000: the core's 64 KB tile
# One L2 memtile is 512 KB. L2 offsets are memtile-local, so a global per-row
# address is column * this + offset (a buffer may span several memtiles).
L2_MEMTILE_SIZE_BYTES = 512 * 1024  # 0x80000

# The canonical "move it off stack/static/heap" fix (conv2d psum-in-OFM idiom).
PSUM_FIX = (
    "Do not place large working sets in static/stack/heap. Hold them in the "
    "oversized async OFM buffer past the real output (the 'psum-in-OFM' idiom, "
    "e.g. conv2d tutorial: `int32 *psum = (int32*)(op + OFM_OUT_ELEMS);`) so "
    "stack/heap are not grown for scratch."
)


@dataclass(frozen=True)
class Rule:
    """A .lst symbol check (fires when `symbol` appears in a scanned .lst)."""

    code: str
    symbol: str
    severity: str
    title: str
    why: str
    fix: str


# Curated .lst symbol rule set. Keep small and high-precision.
LST_RULES: list[Rule] = [
    Rule(
        code="NO_SOFT_FLOAT_DIV",
        symbol="__divsf3",
        severity="error",
        title="scalar software float-divide (__divsf3) in kernel disassembly",
        why=(
            "The compiler pulled in the software float-divide routine (~1 KB of "
            "program memory) because a scalar '/' on float was used. On AIE this "
            "is almost never intended and is far slower than the hardware path."
        ),
        fix=(
            "Use the AIE hardware reciprocal 'aie::inv(x)' (vectorized) instead "
            "of scalar float division 'a / b' -> 'a * aie::inv(b)'."
        ),
    ),
]


@dataclass
class Finding:
    code: str
    severity: str  # "error" (blocks) | "advisory" (reports only)
    title: str
    why: str
    fix: str
    locations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# scanners
# ---------------------------------------------------------------------------
def scan_lst(cache_dir: Path) -> list[Finding]:
    """Soft-float / emulation symbol checks over the .lst disassembly."""
    findings: dict[str, Finding] = {}
    for lst in find_lst_files(cache_dir):
        try:
            lines = lst.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for rule in LST_RULES:
            for i, line in enumerate(lines, start=1):
                if rule.symbol in line:
                    f = findings.setdefault(
                        rule.code,
                        Finding(
                            rule.code, rule.severity, rule.title, rule.why, rule.fix
                        ),
                    )
                    f.locations.append(f"{lst}:{i}: {line.strip()}")
    return list(findings.values())


def scan_static_arrays(cache_dir: Path) -> list[Finding]:
    """Flag user static/global (.bss) symbols larger than STATIC_ARRAY_MAX_BYTES.

    .map section-summary lines look like:  <vma> <lma> <size_hex> <align> <name>
    e.g.  '   7b480    7b480      480     4 .bss.lcpPing'
    Framework symbols (lcpPing/lcpPong) are excluded.
    """
    finding = Finding(
        code="LARGE_STATIC_ARRAY",
        severity="error",
        title=f"static/global (.bss) array larger than {STATIC_ARRAY_MAX_BYTES} B",
        why=(
            "A large static/global array consumes fixed on-core memory and is "
            "usually a working set that should not live in static storage."
        ),
        fix=PSUM_FIX,
    )
    for m in find_core_maps(cache_dir):
        try:
            lines = m.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            # section-summary line: name (5th field) begins with ".bss", no ':'
            if len(fields) < 5 or not fields[4].startswith(".bss"):
                continue
            try:
                size = int(fields[2], 16)
            except ValueError:
                continue
            name = fields[4]
            name.split(".")[-1]
            if any(fw in name for fw in FRAMEWORK_BSS):
                continue
            if size > STATIC_ARRAY_MAX_BYTES:
                finding.locations.append(f"{m}: {name} = {size} B")
    return [finding] if finding.locations else []


def scan_scalar_float_kernel(cache_dir: Path) -> list[Finding]:
    """Flag kernels dominated by scalar float-emulation math (not using mmul/vector).

    Per core .lst: resolve the addresses of the linked scalar float-arithmetic
    routines from their def labels ('<__mulsf3>:'), count the `jl #0x<addr>` call
    sites to them (scalar_float_calls), count vector float ops, and fire when
    scalar_float_calls >= SCALAR_FLOAT_MIN_CALLS AND the scalar/vector ratio is
    above SCALAR_FLOAT_RATIO. NOTE: this is a structural CLASSIFIER (screen), not
    a cost model -- the actual slowdown is a dynamic/throughput property.
    """
    def_re = re.compile(
        r"^0*([0-9a-fA-F]+)\s+<(__(?:mul|add|sub|div)sf3)>:", re.MULTILINE
    )
    finding = Finding(
        code="SCALAR_FLOAT_KERNEL",
        severity="error",
        title="scalar-float-dominated kernel (matrix/vector unit under-used)",
        why=(
            "Most float math is done via scalar software-emulation calls "
            "(__mulsf3/__addsf3/__subsf3/__divsf3) rather than the vector/matrix "
            "unit. Each scalar call does 1 MAC via a function call, vs 512 "
            "MACs/issue for aie::mmul -- typically an order-of-magnitude slower."
        ),
        fix=(
            "Use aie::mmul<8,8,8> for the matmuls and vectorize the elementwise "
            "math (aie::vector ops, aie::exp2, aie::inv) so the hot loops run on "
            "the MAC/vector array instead of per-element scalar calls."
        ),
    )
    for lst in find_core_lsts(cache_dir):
        try:
            text = lst.read_text(errors="ignore")
        except OSError:
            continue
        # resolve arithmetic soft-float routine addresses (name -> minimal hex)
        addrs: dict[str, str] = {}
        for m in def_re.finditer(text):
            addrs[m.group(2)] = format(int(m.group(1), 16), "x")
        scalar_calls = 0
        for _name, ahex in addrs.items():
            scalar_calls += len(re.findall(r"jl\s+#0x0*" + ahex + r"\b", text))
        vec = sum(
            len(re.findall(r"\b" + re.escape(op) + r"\b", text))
            for op in VECTOR_FLOAT_OPS
        )
        ratio = scalar_calls / (vec + 1)
        if scalar_calls >= SCALAR_FLOAT_MIN_CALLS and ratio > SCALAR_FLOAT_RATIO:
            finding.locations.append(
                f"{lst}: scalar_float_calls={scalar_calls}, "
                f"vector_float_ops={vec}, ratio={ratio:.2f} "
                f"(thresholds: calls>={SCALAR_FLOAT_MIN_CALLS}, "
                f"ratio>{SCALAR_FLOAT_RATIO})"
            )
    return [finding] if finding.locations else []


def scan_hw_exp2_on_int8_output(cache_dir: Path) -> list[Finding]:
    """
    Look for hardware exp2 (vexp2, ~bf16 accuracy) feeding a quantized int8 output.

    See "Complex Math functions implementation" in the AIE API Reference skill.
    """
    facts = remark_facts(cache_dir)
    if not facts:
        return []

    int8_kernels = [
        fn
        for fn, dt in custom_op_output_dtypes(cache_dir).items()
        if dt in ("int8", "uint8")
    ]
    if not int8_kernels:
        return []

    # (2) hardware exp2 anywhere in this op's custom_ops functions
    exp2_fns = [
        fn
        for fn, scope in facts.items()
        if any(
            key.startswith(YAML_EXP2_PREFIX)
            for mix in scope["mix"].values()
            for key in mix
        )
    ]
    if not exp2_fns:
        return []

    return [
        Finding(
            code="HW_EXP2_ON_INT8_OUTPUT",
            severity="error",
            title="hardware exp2 (vexp2, ~bf16 accuracy) feeds a quantized int8 output",
            why=(
                "Hardware aie::exp2 is only ~bf16-accurate, so for int8 output "
                "ops, using a LUT is more accurate. The int8 input has only 256 "
                "possible exp arguments, so an exact lookup table gives the same "
                "speed with a bit-exact result."
            ),
            fix=(
                "Replace the hardware aie::exp2 with an exact LUT keyed by the "
                "int8 input byte (256 possible fp32 values), and keep the sum "
                "and reciprocal in fp32. If the flat 256-entry table does not "
                "fit the core's static-data budget, factor it into smaller "
                "per-nibble tables and materialise the full table in L1 scratch. "
                "See aie_api_references 'Complex Math functions implementation'."
            ),
            locations=sorted(int8_kernels)[:2] + sorted(exp2_fns)[:2],
        )
    ]


def scan_rounding_mode_not_conv_even(cache_dir: Path) -> list[Finding]:
    """int8/uint8-output custom op whose core rounding mode is not conv_even.

    Scans for custom ops kernels whose output dtype is (u)int8 and
    do not set the rounding mode using the `movx crrnd, #0xc` instruction.
    """
    int8_kernels = sorted(
        fn
        for fn, dt in custom_op_output_dtypes(cache_dir).items()
        if dt in ("int8", "uint8")
    )
    if not int8_kernels:
        return []

    cores = find_core_lsts(cache_dir)
    if not cores:
        return []

    imms: set[int] = set()
    seen: list[str] = []
    for lst in cores:
        hits = re.findall(r"movx\s+crrnd,\s*#(\w+)", lst.read_text(errors="ignore"))
        if hits:
            seen.append(str(lst))
            imms.update(int(v, 0) for v in hits)

    if CRRND_CONV_EVEN in imms:
        return []  # set_rounding(conv_even) is present -> pass

    if imms:
        detail = (
            "the core sets rounding to "
            + ", ".join(sorted("#0x%x" % v for v in imms))
            + " (none is conv_even #0xc)"
        )
    else:
        detail = (
            "no `movx crrnd, #imm` write in any core listing, so rounding is left "
            "at the hardware default (floor / truncation)"
        )

    return [
        Finding(
            code="ROUNDING_MODE_NOT_CONV_EVEN",
            severity="error",
            title="int8-output custom op does not set rounding mode to conv_even",
            why=(
                "The op writes an int8 output, so the final narrowing cast "
                "(aie::to_fixed<int8>) rounds using the core's persistent "
                "rounding mode. The graded reference (ONNX QuantizeLinear / numpy "
                "rint) rounds half-to-even, so the core must be set to "
                "conv_even; " + detail + ". Any other mode -- and the reset "
                "default is floor/truncation -- rounds every half-way value the "
                "opposite way, giving a large, systematic off-by-one on the int8 "
                "output rather than a handful of tie-point mismatches."
            ),
            fix=(
                "Set the mode once at kernel entry, before any to_fixed cast, "
                "guarded by a static flag: "
                "aie::tile::current().set_rounding(aie::rounding_mode::conv_even); "
                "(pair it with set_saturation(saturation_mode::saturate)). This "
                "lowers to `movx crrnd, #0xc`. See the vai-custom-op skill's "
                "'saturation + rounding modes' checklist item."
            ),
            locations=int8_kernels[:2] + seen[:2],
        )
    ]


def scan_heap(cache_dir: Path) -> list[Finding]:
    """Flag any heap usage (> HEAP_MAX_BYTES)."""
    report = find_report(cache_dir, "report_heap.txt")
    max_v, notes = max_report_value(report, r"Heap Size Used.*=\s*(\d+)")
    if max_v > HEAP_MAX_BYTES:
        return [
            Finding(
                code="HEAP_USED",
                severity="error",
                title=f"heap allocation detected (max {max_v} B used)",
                why=(
                    "Heap use on-core is almost always an accidental dynamic "
                    "allocation of a working set; it grows the heap and is not "
                    "needed on AIE."
                ),
                fix=PSUM_FIX,
                locations=[f"{report}"] + notes[:8],
            )
        ]
    return []


def scan_l1_memory_overflow_and_overlap(cache_dir: Path) -> list[Finding]:
    """Flag a custom-op L1 buffer that runs past the L1 memory or into another region.

      * OVERSHOOT -- a ping/pong copy's end passes the L1 memory.
      * OVERLAP -- a copy overlaps another buffer of the same core, or a system
        memory region (stack / heap / lcp_ping / lcp_pong / sync)

    Data is taken from mladf_compiler_report.json: each l1_buffer's ping/pong
    offset+size and l1_data_memory_info.system_memory_info.
    The DRC is scoped to custom_ops:: cores only, de-duplicated over the 16
    identical cores.

    """
    overshoot = Finding(
        code="L1_BUFFER_OVERSHOOT",
        severity="error",
        title="custom-op L1 buffer extends past the 64 KB core data memory",
        why=(
            "A ping/pong buffer size extends past L1 memory size "
            "(64 KB for aie2p/aie2ps). This can hang the board at runtime."
        ),
        fix="Shrink the buffer in the tiling script so both ping and pong fit in L1.",
    )
    overlap = Finding(
        code="L1_BUFFER_OVERLAP",
        severity="error",
        title="custom-op L1 buffer overlaps another buffer or a framework region",
        why=(
            "Two buffers in the same phase overlap in L1 memory "
            "(buffers or stack / heap / lcp_ping / lcp_pong / sync). "
            "This can hang the board at runtime."
        ),
        fix="Resize or relocate one region in the tiling script so they no longer overlap.",
    )

    report = find_report(cache_dir, "mladf_compiler_report.json")
    if report is None:
        return []
    try:
        data = json.loads(report.read_text(errors="ignore"))
    except (OSError, ValueError):
        return []

    seen: set[str] = set()  # collapse the 16 identical cores; report once per kernel
    for layer in walk_dicts(data, "core_information"):
        cores = layer["core_information"]
        if not isinstance(cores, dict):
            continue
        for core in cores.values():
            if not isinstance(core, dict):
                continue
            kname = str(core.get("kernel_name", ""))
            if not kname.startswith(CUSTOM_OP_NS) or kname in seen:
                continue
            l1 = core.get("l1_data_memory_info")
            if not isinstance(l1, dict):
                continue
            buffers = l1.get("l1_buffers")
            if not isinstance(buffers, list):
                continue
            seen.add(kname)

            # Single-instance framework regions: live in both ping and pong phases.
            sysinfo = l1.get("system_memory_info")
            sys_regions: list[tuple[str, int, int]] = []
            if isinstance(sysinfo, dict):
                for rname, r in sysinfo.items():
                    if not isinstance(r, dict):
                        continue
                    off = r.get("offset")
                    size = r.get("size_in_bytes")
                    if isinstance(off, int) and isinstance(size, int) and size > 0:
                        sys_regions.append((rname, off, off + size))

            for buf in buffers:
                if not isinstance(buf, dict):
                    continue
                po = buf.get("ping_offset_in_bytes")
                ps = buf.get("ping_size_in_bytes")
                go = buf.get("pong_offset_in_bytes")
                gs = buf.get("pong_size_in_bytes")
                if not all(isinstance(x, int) for x in (po, ps, go, gs)):
                    continue
                if ps <= 0 or gs <= 0:  # single-buffered: no second copy to clash
                    continue
                if po < go + gs and go < po + ps:  # half-open interval intersection
                    port = str(buf.get("name", "")).rsplit(".", 1)[-1]
                    overlap.locations.append(
                        f"{kname}: {port} ping (0x{po:x}..0x{po + ps:x}) overlaps "
                        f"its own pong (0x{go:x}..0x{go + gs:x})"
                    )

            for phase in ("ping", "pong"):
                intervals = list(sys_regions)
                for buf in buffers:
                    if not isinstance(buf, dict):
                        continue
                    off = buf.get(f"{phase}_offset_in_bytes")
                    size = buf.get(f"{phase}_size_in_bytes")
                    # size <= 0 => the buffer is not allocated in this phase (held /
                    # async single-buffered); its offset is a sentinel, so skip it.
                    if not isinstance(off, int) or not isinstance(size, int):
                        continue
                    if size <= 0:
                        continue
                    port = str(buf.get("name", "")).rsplit(".", 1)[-1]
                    intervals.append((port, off, off + size))

                # (A) OVERSHOOT: a copy ends past the physical tile top.
                for name, start, end in intervals:
                    if end > L1_SIZE_BYTES:
                        overshoot.locations.append(
                            f"{kname}: {name} [{phase}] 0x{start:x}..0x{end:x} "
                            f"exceeds L1 top 0x{L1_SIZE_BYTES:x} by "
                            f"{end - L1_SIZE_BYTES} B"
                        )

                # (B) OVERLAP: a sweep over the sorted extents. Track the interval
                # reaching farthest so far, so a long region overlapping several
                # later ones is caught, not just adjacent pairs.
                ordered = sorted(intervals, key=lambda t: t[1])
                if ordered:
                    top_name, _, top_end = ordered[0]
                    for name, start, end in ordered[1:]:
                        if start < top_end:
                            overlap.locations.append(
                                f"{kname}: {top_name} (..0x{top_end:x}) overlaps "
                                f"{name} (0x{start:x}..0x{end:x}) [{phase}]"
                            )
                        if end > top_end:  # this interval now reaches farthest
                            top_name, top_end = name, end
    return [f for f in (overshoot, overlap) if f.locations]


def scan_l2_buffer_overlap(cache_dir: Path) -> list[Finding]:
    """Flag two L2 (memtile) buffers of a custom-op layer that share bytes.

    Extents are flattened to a global per-row address
    (`column * MEMTILE_SIZE + offset`) and checked per (phase, row).
    Data is taken from mladf_compiler_report.json l2_data_memory_info.
    The DRC is scoped to custom-op layers. No overshoot check -- spanning
    memtiles is legal.

    """
    overlap = Finding(
        code="L2_BUFFER_OVERLAP",
        severity="error",
        title="two L2 (memtile) buffers of a custom-op layer overlap",
        why=(
            "Two buffers in the same phase overlap in L2 memory. "
            "This can hang the board at runtime."
        ),
        fix="Resize or relocate one L2 buffer in the tiling script so they no longer overlap.",
    )

    report = find_report(cache_dir, "mladf_compiler_report.json")
    if report is None:
        return []
    try:
        data = json.loads(report.read_text(errors="ignore"))
    except (OSError, ValueError):
        return []

    def global_extent(buf: dict, phase: str) -> tuple | None:
        """(row, g_start, g_end) for one phase, or None if not allocated/unknown."""
        off = buf.get(f"{phase}_offset_in_bytes")
        size = buf.get(f"{phase}_size_in_bytes")
        loc = buf.get(f"{phase}_location")
        if not isinstance(off, int) or not isinstance(size, int) or size <= 0:
            return None
        if not isinstance(loc, dict):
            return None
        col, row = loc.get("column"), loc.get("row")
        if not isinstance(col, int) or not isinstance(row, int):
            return None
        g_start = col * L2_MEMTILE_SIZE_BYTES + off
        return (row, g_start, g_start + size)

    seen: set[str] = set()  # dedup identical messages (layers repeat per stamp/path)
    for layer in walk_dicts(data, "l2_data_memory_info"):
        cores = layer.get("core_information")
        is_custom = isinstance(cores, dict) and any(
            isinstance(c, dict)
            and str(c.get("kernel_name", "")).startswith(CUSTOM_OP_NS)
            for c in cores.values()
        )
        if not is_custom:
            continue
        l2 = layer.get("l2_data_memory_info")
        if not isinstance(l2, list):
            continue
        layer_key = str(layer.get("layer_id"))

        # Flatten every extent to a global (row) address; group by (phase, row).
        groups: dict[tuple, list[tuple[str, int, int]]] = {}
        for buf in l2:
            if not isinstance(buf, dict):
                continue
            name = str(buf.get("name", "")).rsplit(".", 1)[-1]
            ext = {}
            for phase in ("ping", "pong"):
                g = global_extent(buf, phase)
                if g is None:
                    continue
                row, gs, ge = g
                ext[phase] = g
                groups.setdefault((phase, row), []).append((name, gs, ge))
            # A buffer's own ping and pong copies, if flattened into the same row.
            if "ping" in ext and "pong" in ext:
                (pr, pgs, pge), (qr, qgs, qge) = ext["ping"], ext["pong"]
                if pr == qr and pgs < qge and qgs < pge:
                    msg = (
                        f"layer {layer_key}: {name} ping (0x{pgs:x}..0x{pge:x}) "
                        f"overlaps its own pong (0x{qgs:x}..0x{qge:x}) [row {pr}]"
                    )
                    if msg not in seen:
                        seen.add(msg)
                        overlap.locations.append(msg)

        # Buffer-vs-buffer overlap within each (phase, row), max-end sweep in
        # global address space.
        for (phase, row), ivs in groups.items():
            ivs.sort(key=lambda t: t[1])
            top_name, _, top_end = ivs[0]
            for name, start, end in ivs[1:]:
                if start < top_end:
                    msg = (
                        f"layer {layer_key}: {top_name} (..0x{top_end:x}) overlaps "
                        f"{name} (0x{start:x}..0x{end:x}) [{phase} row {row}]"
                    )
                    if msg not in seen:
                        seen.add(msg)
                        overlap.locations.append(msg)
                if end > top_end:
                    top_name, top_end = name, end
    return [overlap] if overlap.locations else []


def scan_all(cache_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings += scan_lst(cache_dir)
    findings += scan_scalar_float_kernel(cache_dir)
    findings += scan_static_arrays(cache_dir)
    findings += scan_heap(cache_dir)
    findings += scan_l1_memory_overflow_and_overlap(cache_dir)
    findings += scan_l2_buffer_overlap(cache_dir)
    findings += scan_hw_exp2_on_int8_output(cache_dir)
    findings += scan_rounding_mode_not_conv_even(cache_dir)
    return findings


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def format_report(findings: list[Finding], max_locs: int = 8) -> str:
    lines: list[str] = []
    for f in findings:
        tag = "ERROR" if f.severity == "error" else "ADVISORY"
        lines.append(f"[{tag}] {f.code}: {f.title}")
        lines.append(f"    why: {f.why}")
        lines.append(f"    fix: {f.fix}")
        if f.locations:
            lines.append(f"    found {len(f.locations)} location(s):")
            for loc in f.locations[:max_locs]:
                lines.append(f"      {loc}")
            if len(f.locations) > max_locs:
                lines.append(f"      ... and {len(f.locations) - max_locs} more")
    return "\n".join(lines)


def analyze(cache_dir: Path) -> list[Finding]:
    """Print all findings, return only ERROR-severity ones (for compile.py)."""
    findings = scan_all(cache_dir)
    if findings:
        print("Codegen analysis (post_compile_checks) findings:", file=sys.stderr)
        print(format_report(findings), file=sys.stderr)
    return [f for f in findings if f.severity == "error"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze AIE build artifacts for known codegen anti-patterns."
    )
    parser.add_argument("cache_dir", type=Path, help="Compile cache directory")
    parser.add_argument(
        "--json", action="store_true", help="Emit findings as JSON instead of text"
    )
    args = parser.parse_args()

    if not args.cache_dir.exists():
        print(f"Error: cache dir not found: {args.cache_dir}", file=sys.stderr)
        return 2

    findings = scan_all(args.cache_dir)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "title": f.title,
                        "why": f.why,
                        "fix": f.fix,
                        "count": len(f.locations),
                        "locations": f.locations,
                    }
                    for f in findings
                ],
                indent=2,
            )
        )
    elif not findings:
        print("post_compile_checks: OK -- no issues found.")
    else:
        print(format_report(findings))

    return 1 if any(f.severity == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
