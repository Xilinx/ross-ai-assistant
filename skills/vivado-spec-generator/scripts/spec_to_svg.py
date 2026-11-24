#!/usr/bin/env python3
"""spec_to_svg.py  –  Convert a JSON DesignSpec to a grouped SVG block diagram.

Usage:
    python spec_to_svg.py [--dark] <input.json> [output.svg]

Blocks are placed in logical zones arranged like a system block diagram:
off-chip sources, ingest, processing and accelerators across the top; the
shared control fabric, memory-mapped interconnect and system memory in the
middle; processing system, clocking, endpoints and shared memory below; and
board I/O at the bottom.  Edges come from ``functional_contracts`` when the
spec declares them (protocol inference is only a fallback) and are merged
per zone pair.  Memory-mapped and register-control paths terminate on the
shared fabric bars, so the drawing shows logical dataflow, not a physical
interconnect topology.

Options:
    --dark    Use a dark background theme.
"""

import argparse
import html
import json
import math
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

# ── Resource classification ─────────────────────────────────────────

WORKLOAD_TO_RESOURCE = {
    "os_application": "PS",
    "bare_metal_firmware": "PS",
    "custom_ps": "PS",
    "sensor_interface": "PL",
    "custom_pl": "PL",
    "video_pipeline": "PL",
    "memory_controller": "PL",
    "motor_control": "PL",
    "stream_dsp": "AIE",
    "ml_inference": "AIE",
    "custom_aie": "AIE",
}

DOMAIN_SHORT = {"PL": "PL", "AIE": "AIE", "PS": "PS"}

# ── Zone model ──────────────────────────────────────────────────────

ZONES = {
    "off_chip": ("Off-chip devices", "off"),
    "ingest": ("Ingest", "blk"),
    "pipeline": ("Processing pipeline", "sub"),
    "accelerator": ("Accelerators", "blk"),
    "control_bar": ("Control fabric", "ctl"),
    "noc_bar": ("Memory-mapped interconnect", "noc"),
    "memory": ("System memory", "mem"),
    "ps": ("Processing system", "sub"),
    "clocking": ("Clocking", "blk"),
    "endpoint": ("Endpoints", "blk"),
    "peer": ("Off-chip peers", "off"),
    "shared_memory": ("Shared on-chip memory", "mem"),
    "board_io": ("Board I/O", "sub"),
    "platform": ("Platform and control", "sub"),
}

ROWS = (
    ("off_chip", "ingest", "pipeline", "accelerator"),
    ("control_bar", "noc_bar", "memory"),
    ("ps", "clocking", "endpoint", "peer", "shared_memory"),
    ("board_io", "platform"),
)

BARS = ("control_bar", "noc_bar")
PS_TYPES = {"custom_ps", "os_application", "bare_metal_firmware"}
ACCEL_TYPES = {"custom_aie", "stream_dsp", "ml_inference"}
SHARED_MEMORY_WORDS = re.compile(r"\b(bram|on-chip|ocm|mailbox|scratchpad|uram)\b")
OFF_CHIP_WORDS = re.compile(r"\boff-chip\b")

EDGE_CLASSES = ("data", "mm", "control", "io", "clock")
EDGE_LEGEND = {
    "data": "stream / video / link data",
    "mm": "memory-mapped, via interconnect",
    "control": "register control, via control fabric",
    "io": "board I/O bus",
    "clock": "clock / reset",
}
ZONE_LEGEND = {
    "off": "off-chip",
    "ctl": "control fabric",
    "noc": "interconnect",
    "mem": "memory",
}

# ── Colour themes ───────────────────────────────────────────────────

THEMES = {
    "light": {
        "canvas": "#ffffff",
        "title": "#1b2631",
        "subtitle": "#5d6d7e",
        "separator": "#d5dbdf",
        "zone_title": "#1b2631",
        "card_fill": "#ffffff",
        "card_stroke": "#b8c4ce",
        "card_title": "#1b2631",
        "card_text": "#4d5f6d",
        "card_meta": "#6b7b88",
        "label": "#4d5f6d",
        "label_halo": "#ffffff",
        "ok": "#1e7a4f",
        "warn": "#b9770e",
        "stat": "#1f5f8b",
        "footer": "#4d5f6d",
        "footer_bold": "#1b2631",
        "zone": {
            "blk": ("#eef3f8", "#34495e", ""),
            "sub": ("#f7fafc", "#34495e", ""),
            "noc": ("#dde8f2", "#2c3e50", ""),
            "ctl": ("#f4ecf7", "#6c3483", ""),
            "mem": ("#fdf3e2", "#b9770e", ""),
            "off": ("#f5f5f5", "#7f8c8d", "6 4"),
        },
        "resource": {
            "PS": "#e8983c", "PL": "#4a86c8", "AIE": "#3ba86a", "EXT": "#95a5a6",
        },
        "edge": {
            "data": ("#c0392b", 2.6, ""),
            "mm": ("#1f5f8b", 2.2, ""),
            "control": ("#6c3483", 1.6, ""),
            "io": ("#2c3e50", 1.4, "5 3"),
            "clock": ("#7f8c8d", 1.3, "2 3"),
        },
    },
    "dark": {
        "canvas": "#16161f",
        "title": "#e8e8f0",
        "subtitle": "#9898b0",
        "separator": "#33334a",
        "zone_title": "#e8e8f0",
        "card_fill": "#1f1f2c",
        "card_stroke": "#44445e",
        "card_title": "#e8e8f0",
        "card_text": "#b0b0c8",
        "card_meta": "#8888a0",
        "label": "#b0b0c8",
        "label_halo": "#16161f",
        "ok": "#4cd97b",
        "warn": "#ffb347",
        "stat": "#5caae6",
        "footer": "#9898b0",
        "footer_bold": "#c8c8d8",
        "zone": {
            "blk": ("#232334", "#6a7a90", ""),
            "sub": ("#1c1c2a", "#6a7a90", ""),
            "noc": ("#1f2a3a", "#5c90c0", ""),
            "ctl": ("#2a1f36", "#a070c8", ""),
            "mem": ("#33291a", "#d8a040", ""),
            "off": ("#202028", "#80808c", "6 4"),
        },
        "resource": {
            "PS": "#e89840", "PL": "#5090d0", "AIE": "#45b878", "EXT": "#80808c",
        },
        "edge": {
            "data": ("#ff6b6b", 2.6, ""),
            "mm": ("#5caae6", 2.2, ""),
            "control": ("#c090e8", 1.6, ""),
            "io": ("#b0c0c8", 1.4, "5 3"),
            "clock": ("#8888a0", 1.3, "2 3"),
        },
    },
}

STREAMING_COMPAT = {"AXI-Stream", "PLIO"}

# ── Layout constants ────────────────────────────────────────────────

CARD_W = 250
CARD_GAP = 10
CARD_TEXT_W = CARD_W - 24
ZONE_PAD = 12
ZONE_TITLE_H = 30
ZONE_GAP = 96
MAX_STACK = 3
BOTTOM_STACK = 2
BAR_H = 64
BAR_MIN_W = 300
MIN_GAP = 64
LANE_START = 30
LANE = 16
CHANNEL = 12
MARGIN = 24
FONT = "'Segoe UI', system-ui, -apple-system, 'DejaVu Sans', sans-serif"

LINE_H = {"title": 17, "meta": 16, "text": 15, "ext": 14, "stat": 14}
LINE_SIZE = {"title": 13, "meta": 10, "text": 10.5, "ext": 9.5, "stat": 9.5}
TILE_W = 136
TILE_H = 50
TILE_GAP = 8

# ── Helpers ─────────────────────────────────────────────────────────


def esc(text):
    return html.escape(str(text))


ACRONYMS = {
    "adc", "dac", "fft", "dma", "cfar", "gpio", "axi", "jesd", "plio",
    "dpu", "vcu", "iic", "spi", "uart", "ddr", "bram", "dsp", "lut",
    "cdc", "fifo", "pll", "usb", "hdmi", "pcie", "ps", "pl", "aie",
    "mipi", "csi", "i2c", "pwm", "tsn", "ros", "foc", "vdma", "tpg",
    "reid", "cpu", "mmu", "noc", "hbm", "pcb", "isp", "irq", "io",
    "lpddr4", "lpddr5", "ddr4",
}


def fmt(name):
    words = str(name).replace("_", " ").split()
    return " ".join(w.upper() if w.lower() in ACRONYMS else w.title() for w in words)


def classify(block):
    return WORKLOAD_TO_RESOURCE.get(block.get("workload_type", ""), "PL")


def proto_compat(p1, p2):
    return p1 == p2 or (p1 in STREAMING_COMPAT and p2 in STREAMING_COMPAT)


def protocol_class(protocol):
    """Map a free-form protocol string to a drawing class."""
    p = str(protocol).lower()
    if any(k in p for k in ("software", "api", "ssh", "v4l2")):
        return "other"
    if "clock" in p or "reset" in p:
        return "clock"
    if "interrupt" in p or "irq" in p or "msi" in p:
        return "irq"
    if any(k in p for k in ("i2c", "iic", "spi", "uart", "gpio")):
        return "io"
    if "lite" in p or "register" in p:
        return "control"
    if any(k in p for k in (
        "stream", "csi", "mipi", "bridge", "pci", "ethernet", "video",
        "plio", "jesd", "displayport", "hdmi", "lvds", "serdes",
    )):
        return "data"
    if any(k in p for k in (
        "axi", "noc", "memory", "bram", "ddr", "processor", "dma",
        "master", "manager", "subordinate",
    )):
        return "mm"
    return "other"


def wrap(text, width, size, bold=False, max_lines=None):
    """Greedy word wrap using a conservative average glyph width."""
    limit = max(4, int(width / (size * (0.66 if bold else 0.6))))
    lines, current = [], ""
    for word in str(text).split():
        while len(word) > limit:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:limit])
            word = word[limit:]
        candidate = f"{current} {word}".strip()
        if len(candidate) <= limit:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: limit - 1].rstrip() + "\u2026"
    return lines


def clock_mhz(domain):
    for key, scale in (("frequency_mhz", 1), ("frequency_hz", 1000000)):
        if key in domain:
            try:
                value = Decimal(str(domain[key])) / Decimal(scale)
            except InvalidOperation:
                return str(domain[key])
            return format(value.normalize(), "f")
    return None


# ── Block detail line ───────────────────────────────────────────────


def block_detail(block):
    p = block.get("parameters") or {}
    if not isinstance(p, dict):
        return ""
    if "fft_size" in p:
        return f'{p["fft_size"]}-pt FFT \u00b7 {p.get("channels", "?")} ch'
    if "taps" in p:
        return f'{p["taps"]} taps \u00b7 {p.get("channels", "?")} ch'
    if "num_channels" in p and "sample_width" in p:
        return f'{p["num_channels"]} ch \u00b7 {p["sample_width"]}-bit'
    if "num_channels" in p:
        return f'{p["num_channels"]} channels'
    if "arch" in p:
        return str(p["arch"])
    if "ip_name" in p:
        return str(p["ip_name"])
    if "os" in p:
        cpu = f' \u00b7 {p["cpu_pct"]}% CPU' if "cpu_pct" in p else ""
        return f'{p["os"]}{cpu}'
    if "gpio_width" in p:
        return f'{p["gpio_width"]}-bit GPIO'
    return ""


# ── Grouping ────────────────────────────────────────────────────────


def zone_for(block):
    if block.get("_peer"):
        return "peer"
    workload = block.get("workload_type", "")
    name = str(block.get("name", "")).lower()
    text = f'{name} {block.get("description", "")}'.lower()
    interfaces = block.get("interfaces") or []
    scopes = {i.get("scope", "internal") for i in interfaces}
    classes = [protocol_class(i.get("protocol", "")) for i in interfaces]
    if workload == "external_device" or (
        workload == "sensor_interface"
        and (OFF_CHIP_WORDS.search(text) or (interfaces and scopes <= {"external"}))
    ):
        return "off_chip"
    if workload in PS_TYPES:
        return "ps"
    if workload in ACCEL_TYPES:
        return "accelerator"
    if workload == "memory_controller":
        return "shared_memory" if SHARED_MEMORY_WORDS.search(text) else "memory"
    if SHARED_MEMORY_WORDS.search(name):
        return "shared_memory"
    if workload == "sensor_interface":
        return "ingest"
    if workload in ("video_pipeline", "stream_dsp"):
        return "pipeline"
    if workload == "protocol_bridge":
        return "endpoint"
    if "clock" in name or (classes and classes.count("clock") * 2 >= len(classes)):
        return "clocking"
    if any(
        i.get("scope") in ("external", "top_level")
        and protocol_class(i.get("protocol", "")) == "io"
        for i in interfaces
    ):
        return "board_io"
    return "platform"


def infer_connections(blocks):
    """Match each output interface to the nearest compatible input downstream."""
    conns = []
    used_in, used_out = set(), set()
    for i, blk_a in enumerate(blocks):
        for ia in blk_a.get("interfaces", []):
            if ia["direction"] not in ("out", "bidirectional"):
                continue
            if (i, ia["name"]) in used_out:
                continue
            for j in range(i + 1, len(blocks)):
                found = False
                for ib in blocks[j].get("interfaces", []):
                    if ib["direction"] not in ("in", "bidirectional"):
                        continue
                    if (j, ib["name"]) in used_in:
                        continue
                    if proto_compat(ia["protocol"], ib["protocol"]):
                        conns.append({"src": i, "si": ia, "dst": j, "di": ib})
                        used_out.add((i, ia["name"]))
                        used_in.add((j, ib["name"]))
                        found = True
                        break
                if found:
                    break
    return conns


def collect(spec):
    """Return blocks (plus off-chip peers), zone per block, merged edges, skip counts."""
    blocks = [b for b in spec.get("blocks", []) if isinstance(b, dict)]
    contracts = spec.get("functional_contracts") or {}
    declared = contracts.get("connections")
    by_name = {b.get("name"): i for i, b in enumerate(blocks)}

    def head(ref):
        return str(ref).split(".", 1)[0]

    raw = []
    if isinstance(declared, list) and declared:
        source = "declared"
        for c in declared:
            if isinstance(c, dict):
                raw.append((head(c.get("source", "")), head(c.get("destination", "")),
                            str(c.get("protocol", ""))))
    else:
        source = "protocol-inferred"
        for c in infer_connections(blocks):
            raw.append((blocks[c["src"]]["name"], blocks[c["dst"]]["name"],
                        c["si"]["protocol"]))

    for src, dst, protocol in raw:
        if protocol_class(protocol) not in ("data", "io"):
            continue
        for name in (src, dst):
            if name and name not in by_name:
                by_name[name] = len(blocks)
                blocks.append({
                    "name": name, "_peer": True, "workload_type": "external_peer",
                    "description": "Off-chip peer referenced by the connection contracts.",
                    "interfaces": [],
                })

    zone_of = [zone_for(b) for b in blocks]
    edges = {}
    skipped = defaultdict(int)

    def add(a, b, cls, protocol, directed):
        if a == b:
            return
        key = (min(a, b), max(a, b), cls)
        edge = edges.setdefault(key, {"a": key[0], "b": key[1], "cls": cls,
                                      "protocols": [], "count": 0, "dirs": set()})
        edge["count"] += 1
        if protocol and protocol not in edge["protocols"]:
            edge["protocols"].append(protocol)
        if directed:
            edge["dirs"].add("ab" if a == key[0] else "ba")
        else:
            edge["dirs"].update(("ab", "ba"))

    for src, dst, protocol in raw:
        cls = protocol_class(protocol)
        if cls in ("irq", "other"):
            skipped["interrupt" if cls == "irq" else "software/other"] += 1
            continue
        si, di = by_name.get(src), by_name.get(dst)
        if si is None or di is None:
            skipped["unresolved"] += 1
            continue
        za, zb = zone_of[si], zone_of[di]
        if cls in ("mm", "control"):
            bar = "noc_bar" if cls == "mm" else "control_bar"
            add(za, bar, cls, protocol, False)
            add(zb, bar, cls, protocol, False)
        elif za == zb:
            skipped["within one group"] += 1
        else:
            add(za, zb, cls, protocol, True)

    real = [b["name"] for b in blocks if not b.get("_peer")]
    processors = [b["name"] for b in blocks if b.get("workload_type") == "custom_ps"]

    def resolve(ref, master):
        name = head(ref)
        if name in by_name:
            return by_name[name]
        tokens = name.lower().split("_")
        for matches in (
            [n for n in real if name.lower() in n.lower().split("_")],
            [n for n in real if n.lower() in tokens],
        ):
            if len(matches) == 1:
                return by_name[matches[0]]
        # Unnamed masters (CPU clusters, DMA engines) are processing-system roles.
        if master and len(processors) == 1:
            return by_name[processors[0]]
        return None

    for key, target_key, bar, cls in (
        ("memory_reachability", "memory", "noc_bar", "mm"),
        ("control_reachability", "target", "control_bar", "control"),
    ):
        label = key.replace("_", " ")
        for item in contracts.get(key) or []:
            if not isinstance(item, dict):
                continue
            for ref, master in ((item.get("master"), True), (item.get(target_key), False)):
                index = resolve(ref, master)
                if index is None:
                    skipped[f"unresolved {label}"] += 1
                else:
                    add(zone_of[index], bar, cls, label, False)

    for _ in contracts.get("interrupts") or []:
        skipped["interrupt"] += 1

    per_block = defaultdict(lambda: defaultdict(int))
    clocks = defaultdict(set)
    for key, field, short in (
        ("control_reachability", "target", "ctl"),
        ("memory_reachability", "memory", "mem"),
        ("fixed_address_windows", "target", "addr"),
        ("interrupts", "source", "irq"),
        ("clock_reset", "target", "clk"),
    ):
        for item in contracts.get(key) or []:
            if not isinstance(item, dict):
                continue
            index = resolve(item.get(field), False)
            if index is None:
                continue
            per_block[index][short] += 1
            if key == "clock_reset" and item.get("clock_mhz"):
                clocks[index].add(str(item["clock_mhz"]))
    for index, values in clocks.items():
        per_block[index]["mhz"] = "/".join(sorted(values))

    ordered = sorted(edges.values(),
                     key=lambda e: (EDGE_CLASSES.index(e["cls"]), e["a"], e["b"]))
    return blocks, zone_of, ordered, dict(skipped), source, per_block


# ── Card and zone sizing ────────────────────────────────────────────


def card_lines(block, zone, stats=None):
    lines = [(t, "title") for t in wrap(fmt(block.get("name", "block")), CARD_TEXT_W,
                                        LINE_SIZE["title"], bold=True, max_lines=2)]
    if zone in ("off_chip", "peer"):
        meta = "off-chip"
    else:
        workload = fmt(block.get("workload_type", "")) or "Block"
        meta = f"{workload} \u00b7 {DOMAIN_SHORT[classify(block)]}"
    detail = block_detail(block)
    if detail:
        meta += f" \u00b7 {detail}"
    lines += [(t, "meta") for t in wrap(meta, CARD_TEXT_W, LINE_SIZE["meta"], max_lines=2)]
    description = block.get("description", "")
    if description:
        lines += [(t, "text") for t in wrap(description, CARD_TEXT_W, LINE_SIZE["text"],
                                            max_lines=3)]
    external = [
        str(i.get("name")) for i in block.get("interfaces") or []
        if i.get("scope") in ("external", "top_level")
    ]
    if external:
        lines += [(t, "ext") for t in wrap("External: " + ", ".join(external), CARD_TEXT_W,
                                           LINE_SIZE["ext"], max_lines=2)]
    if stats:
        parts = [f"{stats[k]} {k}" for k in ("ctl", "mem", "addr", "irq", "clk") if stats.get(k)]
        if stats.get("mhz"):
            parts.append(f'{stats["mhz"]} MHz')
        if parts:
            lines += [(t, "stat") for t in wrap("Contracts: " + " \u00b7 ".join(parts),
                                                CARD_TEXT_W, LINE_SIZE["stat"], max_lines=2)]
    return lines


def count(value):
    if isinstance(value, (list, dict)):
        return len(value)
    return 0 if value in (None, "", False) else 1


def preview(value, limit=4):
    if isinstance(value, dict):
        items = list(value)
    elif isinstance(value, list):
        items = [v.get("name") or v.get("id") or json.dumps(v)[:40] if isinstance(v, dict) else v
                 for v in value]
    else:
        items = [value]
    text = "; ".join(str(i) for i in items[:limit])
    return text + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def verify_sources(manifest):
    """Return (path, sha prefix, status) for each entry of a spec source manifest."""
    import hashlib

    try:
        entries = json.loads(Path(manifest).read_text())
    except (OSError, ValueError):
        return []
    results = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        recorded = str(entry.get("sha256", ""))
        status = "recorded"
        for base in [Path(manifest).parent, *list(Path(manifest).resolve().parents)[:4]]:
            candidate = base / entry["path"]
            if candidate.is_file():
                actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
                status = "verified" if actual == recorded else "changed"
                break
        results.append((str(entry["path"]), recorded[:12], status))
    return results


def summarize(spec, blocks, edges, skipped, sources):
    """Build stat tiles plus input-consumption and next-stage handoff text."""
    contracts = spec.get("functional_contracts") or {}
    constraints = spec.get("global_constraints") or {}
    real = [b for b in blocks if not b.get("_peer")]
    interfaces = [i for b in real for i in b.get("interfaces") or []]
    external = sum(1 for i in interfaces if i.get("scope") in ("external", "top_level"))
    unresolved = constraints.get("unresolved_requirements") or []
    verified = sum(1 for _, _, status in sources if status == "verified")
    tiles = [
        ("blocks", len(real), ""),
        ("interfaces", f"{len(interfaces)} ({external} ext)", ""),
        ("connections", count(contracts.get("connections")), ""),
        ("control reach", count(contracts.get("control_reachability")), ""),
        ("memory reach", count(contracts.get("memory_reachability")), ""),
        ("address windows", count(contracts.get("fixed_address_windows")), ""),
        ("interrupts", count(contracts.get("interrupts")), ""),
        ("clock / reset", count(contracts.get("clock_reset")), ""),
        ("visibility rules", count(contracts.get("visibility_policy")), ""),
        ("software", count(contracts.get("software")), ""),
        ("unresolved", len(unresolved), "ok" if not unresolved else "warn"),
    ]
    if sources:
        tiles.append(("sources verified", f"{verified}/{len(sources)}",
                      "ok" if verified == len(sources) else "warn"))

    consumed = []
    for path, digest, status in sources:
        mark = {"verified": "\u2713", "changed": "\u2717"}.get(status, "\u2022")
        consumed.append((f"{mark} {path}  sha256 {digest}\u2026  {status}", "item"))
    if not sources:
        consumed.append(("No spec_sources.json manifest found beside the spec.", "muted"))
    target = " \u00b7 ".join(str(spec[k]) for k in ("target_device", "board_part") if spec.get(k))
    if target:
        consumed.append(("Target: " + target, "item"))
    context = [f"{k.replace('_', ' ')} {count(v)}" for k, v in constraints.items()
               if "deferred" not in k and k != "unresolved_requirements" and count(v)]
    if context:
        consumed.append(("Captured context: " + " \u00b7 ".join(context), "item"))
    zones = defaultdict(int)
    for b in real:
        zones[ZONES[zone_for(b)][0]] += 1
    consumed.append(("Blocks by group: " + " \u00b7 ".join(f"{k} {v}" for k, v in zones.items()),
                     "item"))

    handoff = []
    if unresolved:
        handoff += [("Unresolved: " + str(u), "warn") for u in unresolved[:4]]
    else:
        handoff.append(("Unresolved requirements: none", "ok"))
    for key, value in constraints.items():
        if "deferred" in key and count(value):
            handoff.append((f"{fmt(key)} ({count(value)}): {preview(value)}", "item"))
    exact = []
    for domain in spec.get("clock_domains") or []:
        if isinstance(domain, dict) and clock_mhz(domain):
            exact.append(f'{domain.get("name")} {clock_mhz(domain)}')
    if exact:
        handoff.append(("Exact clocks (MHz): " + ", ".join(exact), "item"))
    windows = [w for w in contracts.get("fixed_address_windows") or [] if isinstance(w, dict)]
    if windows:
        targets = {str(w.get("target", "")).split(".", 1)[0] for w in windows}
        masters = {str(m) for w in windows for m in w.get("masters") or []}
        handoff.append((f"Address map: {len(windows)} fixed windows, {len(targets)} targets, "
                        f"masters {', '.join(sorted(masters)[:6])}", "item"))
    policy = [p for p in contracts.get("visibility_policy") or [] if isinstance(p, dict)]
    if policy:
        actions = defaultdict(int)
        for p in policy:
            actions[str(p.get("action", "rule"))] += 1
        handoff.append(("Visibility: " + ", ".join(f"{v} {k}" for k, v in actions.items()),
                        "item"))
    irqs = [i for i in contracts.get("interrupts") or [] if isinstance(i, dict)]
    if irqs:
        handoff.append(("Interrupts: " + "; ".join(
            f'{i.get("source")} \u2192 {i.get("destination")}' for i in irqs[:4]), "item"))
    drawn = sum(e["count"] for e in edges)
    handoff.append((f"Diagram: {drawn} contract endpoints drawn; "
                    + (", ".join(f"{v} {k}" for k, v in sorted(skipped.items())) or "none")
                    + " listed only here", "muted"))
    return tiles, consumed, handoff


def card_height(lines):
    return 12 + sum(LINE_H[kind] for _, kind in lines) + 6


def layout_zone(cards, max_stack=MAX_STACK):
    """Split cards into columns at most max_stack high; return (w, h, columns)."""
    cols = max(1, math.ceil(len(cards) / max_stack))
    per = math.ceil(len(cards) / cols)
    columns = [c for c in (cards[i * per:(i + 1) * per] for i in range(cols)) if c]
    heights = [sum(h for _, _, h in col) + CARD_GAP * (len(col) - 1) for col in columns]
    width = len(columns) * CARD_W + (len(columns) - 1) * CARD_GAP + 2 * ZONE_PAD
    return width, ZONE_TITLE_H + max(heights) + ZONE_PAD, columns


def edge_label(edge):
    protocols = edge["protocols"] or [edge["cls"]]
    text = protocols[0] if len(protocols) == 1 else f"{protocols[0]} +{len(protocols) - 1}"
    if len(text) > 26:
        text = text[:25] + "\u2026"
    return f"{text} \u00d7{edge['count']}" if edge["count"] > 1 else text


# ── SVG generation ──────────────────────────────────────────────────


def generate_svg(spec, dark=False, sources_manifest=None):
    theme = THEMES["dark" if dark else "light"]
    blocks, zone_of, edges, skipped, edge_source, per_block = collect(spec)
    sources = verify_sources(sources_manifest) if sources_manifest else []
    if not blocks:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40"/>'

    members = defaultdict(list)
    for index, zone in enumerate(zone_of):
        members[zone].append(index)
    used_bars = {z for e in edges for z in (e["a"], e["b"]) if z in BARS}

    # --- node sizes ---------------------------------------------------
    nodes = {}
    for zone, indices in members.items():
        cards = []
        for index in indices:
            lines = card_lines(blocks[index], zone, per_block.get(index))
            cards.append((index, lines, card_height(lines)))
        width, height, columns = layout_zone(
            cards, BOTTOM_STACK if zone in ROWS[-1] else MAX_STACK)
        nodes[zone] = {"w": width, "h": height, "columns": columns, "bar": False}
    for bar in used_bars:
        nodes[bar] = {"w": BAR_MIN_W, "h": BAR_H, "bar": True}

    rows = [[z for z in row if z in nodes] for row in ROWS]
    rows = [row for row in rows if row]
    row_of = {z: r for r, row in enumerate(rows) for z in row}

    # --- x placement (relative to content origin) ---------------------
    def row_width(row):
        return sum(nodes[z]["w"] for z in row) + ZONE_GAP * (len(row) - 1)

    content_w = max(row_width(row) for row in rows)
    for row in rows:
        bars = [z for z in row if z in BARS]
        others = [z for z in row if z not in BARS]
        if bars:
            right = content_w
            for zone in reversed(others):
                right -= nodes[zone]["w"]
                nodes[zone]["x"] = right
                right -= ZONE_GAP
            span = right + ZONE_GAP - ZONE_GAP * len(bars)
            if len(bars) == 2:
                first = max(BAR_MIN_W, span * 0.4)
                widths = [first, span - first]
            else:
                widths = [span]
            cursor = 0
            for zone, width in zip(bars, widths):
                nodes[zone]["x"], nodes[zone]["w"] = cursor, width
                cursor += width + ZONE_GAP
        else:
            cursor = 0
            for zone in row:
                nodes[zone]["x"] = cursor
                cursor += nodes[zone]["w"] + ZONE_GAP

    def center(z):
        return nodes[z]["x"] + nodes[z]["w"] / 2

    # --- route planning -----------------------------------------------
    gap_lanes = defaultdict(int)
    channels = {"L": 0, "R": 0}
    side_slots = defaultdict(list)
    plans = []
    for edge in edges:
        a, b = edge["a"], edge["b"]
        flip = (row_of[a], nodes[a]["x"]) > (row_of[b], nodes[b]["x"])
        if flip:
            a, b = b, a
        plan = {"edge": edge, "a": a, "b": b, "flip": flip}
        ra, rb = row_of[a], row_of[b]
        if ra == rb:
            between = [z for z in rows[ra] if nodes[a]["x"] < nodes[z]["x"] < nodes[b]["x"]]
            if not between:
                plan["type"] = "h"
            else:
                plan["type"] = "u"
                plan["side"] = "bottom" if ra < len(rows) - 1 else "top"
                plan["gap"] = ra if plan["side"] == "bottom" else ra - 1
                plan["lane"] = gap_lanes[plan["gap"]]
                gap_lanes[plan["gap"]] += 1
                side_slots[(a, plan["side"])].append(plan)
                side_slots[(b, plan["side"])].append(plan)
        elif rb - ra == 1:
            plan["type"] = "v"
            plan["gap"] = ra
            side_slots[(a, "bottom")].append(plan)
            side_slots[(b, "top")].append(plan)
        else:
            plan["type"] = "c"
            plan["channel"] = "L" if center(b) < content_w / 2 else "R"
            plan["track"] = channels[plan["channel"]]
            channels[plan["channel"]] += 1
            plan["gaps"] = (ra, rb - 1)
            plan["lanes"] = []
            for gap in plan["gaps"]:
                plan["lanes"].append(gap_lanes[gap])
                gap_lanes[gap] += 1
            side_slots[(a, "bottom")].append(plan)
            side_slots[(b, "top")].append(plan)
        plans.append(plan)

    def other(plan, zone):
        return plan["b"] if zone == plan["a"] else plan["a"]

    for (zone, side), items in side_slots.items():
        node = nodes[zone]
        if node["bar"]:
            continue
        items.sort(key=lambda p: center(other(p, zone)))
        for k, plan in enumerate(items):
            plan.setdefault("slots", {})[(zone, side)] = (
                node["x"] + node["w"] * (k + 1) / (len(items) + 1))
            plan.setdefault("slot_index", {})[(zone, side)] = k

    def attach(plan, zone, side):
        node = nodes[zone]
        slots = plan.get("slots", {})
        if (zone, side) in slots:
            return slots[(zone, side)]
        peer = other(plan, zone)
        if plan["type"] == "c":
            offset = 16 + plan["track"] * CHANNEL
            if plan["channel"] == "L":
                return node["x"] + offset
            return node["x"] + node["w"] - offset
        peer_side = ("top" if side == "bottom" else "bottom") if plan["type"] == "v" else side
        target = slots.get((peer, peer_side), center(peer))
        return min(max(target, node["x"] + 16), node["x"] + node["w"] - 16)

    for plan in plans:
        if plan["type"] == "v":
            plan["x1"] = attach(plan, plan["a"], "bottom")
            plan["x2"] = attach(plan, plan["b"], "top")
            if abs(plan["x1"] - plan["x2"]) > 0.5:
                plan["lane"] = gap_lanes[plan["gap"]]
                gap_lanes[plan["gap"]] += 1

    # --- vertical placement -------------------------------------------
    left_w = channels["L"] * CHANNEL + (16 if channels["L"] else 0)
    right_w = channels["R"] * CHANNEL + (16 if channels["R"] else 0)
    origin = MARGIN + left_w
    canvas_w = int(origin + content_w + right_w + MARGIN)
    for node in nodes.values():
        node["x"] += origin
    for plan in plans:
        for key in plan.get("slots", {}):
            plan["slots"][key] += origin
        if "x1" in plan:
            plan["x1"] += origin
            plan["x2"] += origin

    title = fmt(spec.get("name", "Design"))
    subtitle = " \u00b7 ".join(
        str(p) for p in (spec.get("target_device", ""), spec.get("board_part", "")) if p)
    description_lines = wrap(spec.get("description", ""), canvas_w - 2 * MARGIN, 11.5,
                             max_lines=2)
    tiles, consumed, handoff = summarize(spec, blocks, edges, skipped, sources)
    panel_w = canvas_w - 2 * MARGIN
    panel_y = MARGIN + 58 + 15 * len(description_lines) + 6
    per_row = max(1, int((panel_w + TILE_GAP) // (TILE_W + TILE_GAP)))
    tile_w = (panel_w - TILE_GAP * (per_row - 1)) / per_row
    tiles_h = math.ceil(len(tiles) / per_row) * (TILE_H + TILE_GAP)
    column_w = (panel_w - 32) / 2
    columns_text = [
        [(line, kind) for text, kind in entries
         for line in wrap(text, column_w - 8, 10.5, max_lines=3)]
        for entries in (consumed, handoff)
    ]
    lists_y = panel_y + tiles_h + 6
    lists_h = 22 + 15 * max(len(c) for c in columns_text) + 6
    header_h = lists_y + lists_h + 26

    row_top, row_h = [], []
    y = header_h
    for r, row in enumerate(rows):
        row_top.append(y)
        row_h.append(max(nodes[z]["h"] for z in row))
        for zone in row:
            nodes[zone]["y"] = y
        if r < len(rows) - 1:
            y += row_h[r] + max(MIN_GAP, LANE_START + gap_lanes[r] * LANE + 20)
    content_bottom = row_top[-1] + row_h[-1]

    def gap_top(gap):
        return row_top[gap] + row_h[gap]

    def lane_y(gap, lane):
        return gap_top(gap) + LANE_START + lane * LANE

    def channel_x(plan):
        if plan["channel"] == "L":
            return origin - 16 - plan["track"] * CHANNEL
        return origin + content_w + 16 + plan["track"] * CHANNEL

    def bottom(z):
        return nodes[z]["y"] + nodes[z]["h"]

    legend_classes = [c for c in EDGE_CLASSES if any(e["cls"] == c for e in edges)]
    legend_styles = [st for st in ZONE_LEGEND if any(ZONES[z][1] == st for z in nodes)]
    clocks = [f'{c.get("name", "clock")}: {clock_mhz(c)} MHz'
              for c in spec.get("clock_domains") or []
              if isinstance(c, dict) and clock_mhz(c)]
    clock_lines = wrap("Clocks: " + " \u00b7 ".join(clocks), canvas_w - 2 * MARGIN, 10) \
        if clocks else []
    notes = [f"{count} {kind}" for kind, count in sorted(skipped.items())]
    note_text = (
        f"Edges: {edge_source} connections, merged per group pair"
        + (f". Not drawn: {', '.join(notes)}" if notes else "")
        + ". Logical grouping only; no interconnect topology is implied."
    )
    note_lines = wrap(note_text, canvas_w - 2 * MARGIN, 10)
    footer_y = content_bottom + 44
    power = spec.get("power_budget_watts")
    canvas_h = int(footer_y + 22 + 15 * (len(clock_lines) + len(note_lines))
                   + (18 if power else 0) + MARGIN)

    # --- SVG ----------------------------------------------------------
    s = ['<?xml version="1.0" encoding="UTF-8"?>']
    s.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas_w} {canvas_h}" '
        f'width="{canvas_w}" height="{canvas_h}" role="img">'
    )
    s.append(f"<title>{esc(title)}</title>")
    s.append("<defs>")
    for cls in EDGE_CLASSES:
        s.append(
            f'<marker id="a_{cls}" viewBox="0 0 10 8" refX="9" refY="4" markerWidth="9" '
            f'markerHeight="7" orient="auto-start-reverse" markerUnits="userSpaceOnUse">'
            f'<path d="M0,0 L10,4 L0,8 z" fill="{theme["edge"][cls][0]}"/></marker>'
        )
    s.append("</defs>")
    s.append(f"<style>text{{font-family:{FONT}}}</style>")
    s.append(f'<rect width="{canvas_w}" height="{canvas_h}" fill="{theme["canvas"]}"/>')

    s.append(f'<text x="{MARGIN}" y="{MARGIN + 20}" font-size="22" font-weight="700" '
             f'fill="{theme["title"]}">{esc(title)}</text>')
    if subtitle:
        s.append(f'<text x="{MARGIN}" y="{MARGIN + 40}" font-size="11.5" '
                 f'fill="{theme["subtitle"]}">{esc(subtitle)}</text>')
    for k, line in enumerate(description_lines):
        s.append(f'<text x="{MARGIN}" y="{MARGIN + 57 + 15 * k}" font-size="11.5" '
                 f'fill="{theme["subtitle"]}">{esc(line)}</text>')
    tile_fill, tile_stroke, _ = theme["zone"]["blk"]
    for k, (label, value, status) in enumerate(tiles):
        tx = MARGIN + (k % per_row) * (tile_w + TILE_GAP)
        ty = panel_y + (k // per_row) * (TILE_H + TILE_GAP)
        color = theme.get(status, theme["card_title"])
        s.append(f'<g data-stat="{esc(label)}"><rect x="{tx:.1f}" y="{ty}" width="{tile_w:.1f}" '
                 f'height="{TILE_H}" rx="5" fill="{tile_fill}" stroke="{tile_stroke}" '
                 f'stroke-opacity="0.35"/>')
        s.append(f'<text x="{tx + 10:.1f}" y="{ty + 24}" font-size="17" font-weight="700" '
                 f'fill="{color}">{esc(value)}</text>')
        s.append(f'<text x="{tx + 10:.1f}" y="{ty + 40}" font-size="10" '
                 f'fill="{theme["card_meta"]}">{esc(label)}</text></g>')
    for k, (heading, lines) in enumerate(
        (("Input consumed", columns_text[0]), ("Handoff to next stage", columns_text[1]))
    ):
        lx = MARGIN + k * (column_w + 32)
        s.append(f'<text x="{lx:.1f}" y="{lists_y + 14}" font-size="12.5" font-weight="700" '
                 f'fill="{theme["zone_title"]}">{heading}</text>')
        for j, (line, kind) in enumerate(lines):
            color = {"muted": theme["card_meta"], "ok": theme["ok"],
                     "warn": theme["warn"]}.get(kind, theme["card_text"])
            s.append(f'<text x="{lx:.1f}" y="{lists_y + 34 + 15 * j}" font-size="10.5" '
                     f'fill="{color}">{esc(line)}</text>')
    s.append(f'<line x1="{MARGIN}" y1="{header_h - 14}" x2="{canvas_w - MARGIN}" '
             f'y2="{header_h - 14}" stroke="{theme["separator"]}"/>')

    # --- zones and cards ----------------------------------------------
    for zone, node in nodes.items():
        zone_title, style = ZONES[zone]
        fill, stroke, dash = theme["zone"][style]
        da = f' stroke-dasharray="{dash}"' if dash else ""
        x, y, w, h = node["x"], node["y"], node["w"], node["h"]
        s.append(f'<g data-zone="{zone}">')
        s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="1.8"{da}/>')
        if node["bar"]:
            count = sum(e["count"] for e in edges if zone in (e["a"], e["b"]))
            s.append(f'<text x="{x + w / 2}" y="{y + 27}" font-size="14" font-weight="700" '
                     f'fill="{theme["zone_title"]}" text-anchor="middle">{esc(zone_title)}</text>')
            s.append(f'<text x="{x + w / 2}" y="{y + 46}" font-size="10.5" '
                     f'fill="{theme["card_text"]}" text-anchor="middle">'
                     f'{count} contract endpoints \u00b7 topology not implied</text>')
            s.append("</g>")
            continue
        s.append(f'<text x="{x + ZONE_PAD}" y="{y + 20}" font-size="13" font-weight="700" '
                 f'fill="{theme["zone_title"]}">{esc(zone_title)}</text>')
        for column_index, column in enumerate(node["columns"]):
            cx = x + ZONE_PAD + column_index * (CARD_W + CARD_GAP)
            cy = y + ZONE_TITLE_H
            for index, lines, height in column:
                block = blocks[index]
                external = zone in ("off_chip", "peer")
                resource = "EXT" if external else classify(block)
                card_dash = ' stroke-dasharray="5 3"' if external else ""
                tooltip = [block.get("name", ""), block.get("description", "")]
                tooltip += [
                    f'{i.get("name")} ({i.get("direction")}, {i.get("protocol")}, '
                    f'{i.get("scope", "internal")})'
                    for i in block.get("interfaces") or []
                ]
                s.append(f'<g data-block="{esc(block.get("name", ""))}">')
                s.append(f"<title>{esc(chr(10).join(str(t) for t in tooltip if t))}</title>")
                s.append(f'<rect x="{cx}" y="{cy}" width="{CARD_W}" height="{height}" rx="5" '
                         f'fill="{theme["card_fill"]}" stroke="{theme["card_stroke"]}"'
                         f'{card_dash}/>')
                s.append(f'<rect x="{cx}" y="{cy}" width="4" height="{height}" rx="2" '
                         f'fill="{theme["resource"][resource]}"/>')
                ty = cy + 12
                for text, kind in lines:
                    ty += LINE_H[kind]
                    color = {"title": theme["card_title"], "meta": theme["card_meta"],
                             "stat": theme["stat"]}.get(kind, theme["card_text"])
                    weight = ' font-weight="700"' if kind == "title" else ""
                    s.append(f'<text x="{cx + 12}" y="{ty - 3}" font-size="{LINE_SIZE[kind]}"'
                             f'{weight} fill="{color}">{esc(text)}</text>')
                s.append("</g>")
                cy += height + CARD_GAP
        s.append("</g>")

    # --- edges --------------------------------------------------------
    for plan in plans:
        edge = plan["edge"]
        a, b = plan["a"], plan["b"]
        color, width, dash = theme["edge"][edge["cls"]]
        dirs = edge["dirs"]
        if plan["flip"]:
            dirs = {{"ab": "ba", "ba": "ab"}[d] for d in dirs}
        markers = ""
        if "ab" in dirs:
            markers += f' marker-end="url(#a_{edge["cls"]})"'
        if "ba" in dirs:
            markers += f' marker-start="url(#a_{edge["cls"]})"'
        label = edge_label(edge)
        label_items = []
        slot_index = plan.get("slot_index", {})
        if plan["type"] == "h":
            siblings = [p for p in plans if p["type"] == "h" and {p["a"], p["b"]} == {a, b}]
            k = siblings.index(plan)
            common = min(nodes[a]["h"], nodes[b]["h"])
            ly = nodes[a]["y"] + common / 2 + (k - (len(siblings) - 1) / 2) * 44
            x1, x2 = nodes[a]["x"] + nodes[a]["w"], nodes[b]["x"]
            points = [(x1, ly), (x2, ly)]
            parts = wrap(label, ZONE_GAP - 8, 9, max_lines=3)
            for j, part in enumerate(parts):
                label_items.append(((x1 + x2) / 2, ly - 6 - (len(parts) - 1 - j) * 13,
                                    part, "middle"))
        elif plan["type"] == "v":
            x1, x2 = plan["x1"], plan["x2"]
            y1, y2 = bottom(a), nodes[b]["y"]
            if "lane" in plan:
                ly = lane_y(plan["gap"], plan["lane"])
                points = [(x1, y1), (x1, ly), (x2, ly), (x2, y2)]
            else:
                points = [(x1, y1), (x2, y2)]
            if (a, "bottom") in plan.get("slots", {}) and not nodes[a]["bar"]:
                k = slot_index[(a, "bottom")]
                label_items.append((x1 + 5, y1 + 13 + k * 12, label, "start"))
            else:
                k = slot_index.get((b, "top"), 0)
                label_items.append((x2 + 5, y2 - 6 - k * 12, label, "start"))
        elif plan["type"] == "u":
            side = plan["side"]
            x1, x2 = attach(plan, a, side), attach(plan, b, side)
            y1 = bottom(a) if side == "bottom" else nodes[a]["y"]
            y2 = bottom(b) if side == "bottom" else nodes[b]["y"]
            if side == "bottom":
                ly = lane_y(plan["gap"], plan["lane"])
            else:
                ly = gap_top(plan["gap"]) + 18 + plan["lane"] * LANE
            points = [(x1, y1), (x1, ly), (x2, ly), (x2, y2)]
            label_items.append((x1 + 5, ly - 3, label, "start"))
        else:
            x1, x2 = attach(plan, a, "bottom"), attach(plan, b, "top")
            y1, y2 = bottom(a), nodes[b]["y"]
            l1, l2 = (lane_y(g, lane) for g, lane in zip(plan["gaps"], plan["lanes"]))
            cx = channel_x(plan)
            points = [(x1, y1), (x1, l1), (cx, l1), (cx, l2), (x2, l2), (x2, y2)]
            if nodes[a]["bar"]:
                k = slot_index.get((b, "top"), 0)
                label_items.append((x2 + 5, y2 - 6 - k * 12, label, "start"))
            else:
                k = slot_index.get((a, "bottom"), 0)
                label_items.append((x1 + 5, y1 + 13 + k * 12, label, "start"))
        path = " ".join(("M" if i == 0 else "L") + f"{px:.1f},{py:.1f}"
                        for i, (px, py) in enumerate(points))
        da = f' stroke-dasharray="{dash}"' if dash else ""
        s.append(f'<g data-edge="{a}|{b}|{edge["cls"]}">')
        s.append(f"<title>{esc(edge['cls'] + ': ' + ', '.join(edge['protocols']))}"
                 f" ({edge['count']})</title>")
        s.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="{width}"'
                 f'{da}{markers}/>')
        for lx, ly, text, anchor in label_items:
            s.append(f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="9" fill="{theme["label"]}" '
                     f'stroke="{theme["label_halo"]}" stroke-width="3" paint-order="stroke" '
                     f'text-anchor="{anchor}">{esc(text)}</text>')
        s.append("</g>")

    # --- legend and footer --------------------------------------------
    s.append(f'<line x1="{MARGIN}" y1="{footer_y - 20}" x2="{canvas_w - MARGIN}" '
             f'y2="{footer_y - 20}" stroke="{theme["separator"]}"/>')
    lx = MARGIN
    for cls in legend_classes:
        color, width, dash = theme["edge"][cls]
        da = f' stroke-dasharray="{dash}"' if dash else ""
        s.append(f'<line x1="{lx}" y1="{footer_y - 4}" x2="{lx + 34}" y2="{footer_y - 4}" '
                 f'stroke="{color}" stroke-width="{width}"{da}/>')
        s.append(f'<text x="{lx + 40}" y="{footer_y}" font-size="10" '
                 f'fill="{theme["footer"]}">{esc(EDGE_LEGEND[cls])}</text>')
        lx += 40 + len(EDGE_LEGEND[cls]) * 6 + 22
    for style in legend_styles:
        fill, stroke, dash = theme["zone"][style]
        da = f' stroke-dasharray="{dash}"' if dash else ""
        s.append(f'<rect x="{lx}" y="{footer_y - 11}" width="26" height="13" rx="3" '
                 f'fill="{fill}" stroke="{stroke}"{da}/>')
        s.append(f'<text x="{lx + 32}" y="{footer_y}" font-size="10" '
                 f'fill="{theme["footer"]}">{esc(ZONE_LEGEND[style])}</text>')
        lx += 32 + len(ZONE_LEGEND[style]) * 6 + 22
    fy = footer_y + 22
    for line in clock_lines + note_lines:
        s.append(f'<text x="{MARGIN}" y="{fy}" font-size="10" '
                 f'fill="{theme["footer"]}">{esc(line)}</text>')
        fy += 15
    if power:
        s.append(f'<text x="{MARGIN}" y="{fy + 3}" font-size="10" '
                 f'fill="{theme["footer_bold"]}">Power budget: {esc(power)} W</text>')

    s.append("</svg>")
    return "\n".join(s)


# ── CLI ─────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Convert a JSON hardware spec to an SVG block diagram."
    )
    parser.add_argument("input", type=Path, help="Input JSON spec file")
    parser.add_argument("output", type=Path, nargs="?", help="Output SVG file")
    parser.add_argument(
        "--dark", action="store_true", help="Use dark background theme"
    )
    parser.add_argument(
        "--sources", type=Path,
        help="Source manifest (default: spec_sources.json beside the input, if present)",
    )
    args = parser.parse_args()

    out = args.output if args.output else args.input.with_suffix(".svg")

    with open(args.input) as f:
        spec = json.load(f)

    sources = args.sources or args.input.with_name("spec_sources.json")
    svg = generate_svg(spec, dark=args.dark,
                       sources_manifest=sources if sources.is_file() else None)
    out.write_text(svg, encoding="utf-8")
    print(f"Generated: {out}")


if __name__ == "__main__":
    main()
