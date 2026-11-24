#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Match DesignSpec `top_level` interfaces to the port names the RTL top instance uses.

The plan compiler's BOUNDARY check accepts, for each `top_level` interface, a
`check_required_ports` entry named after the interface, or after the bare name at
the other end of a required connection that uses it. When the RTL top names the
port differently (a renamed signal, or one port per bit of a grouped interface),
neither name can be a wrapper port, so the DesignSpec must carry the RTL name as
that bare connection end. Do it before binding: the plan records the spec hash.

  check --spec design_spec.json --rtl top.sv [--wrapper design_1_wrapper]
      BOUNDARY_NAMES:OK | BOUNDARY_NAMES_FAIL (each interface, its accepted names,
      and RTL ports that look related) | BOUNDARY_NAMES:NA (no wrapper instance)
  add --spec design_spec.json <block>.<interface>=<port>[,<port>...] ...
      Appends one required connection per RTL port, idempotently, and prints
      BOUNDARY_NAMES:ADDED <n>. Rerun the spec validators and bind afterwards.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def rtl_instance_ports(text: str, wrapper: str) -> list[str] | None:
    """Named port connections (`.name(`) of the first instance of `wrapper`, or None."""
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    match = re.search(rf"\b{re.escape(wrapper)}\b\s*(?:#\s*\(.*?\)\s*)?\w+\s*\((.*?)\)\s*;", text, flags=re.S)
    if not match:
        return None
    return sorted(set(re.findall(r"\.(\w+)\s*\(", match.group(1))))


def top_level_interfaces(spec: dict) -> list[tuple[str, dict]]:
    out = []
    for block in spec.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for interface in block.get("interfaces") or []:
            if isinstance(interface, dict) and interface.get("scope") == "top_level":
                out.append((f"{block.get('name')}.{interface.get('name')}", interface))
    return out


def accepted_names(spec: dict, endpoint: str) -> set[str]:
    """The port names the plan compiler accepts for one top_level interface."""
    blocks = {block.get("name") for block in spec.get("blocks") or [] if isinstance(block, dict)}
    names = {endpoint.split(".", 1)[1]}
    for item in (spec.get("functional_contracts") or {}).get("connections") or []:
        if not isinstance(item, dict) or not item.get("required", True):
            continue
        ends = [str(item.get("source", "")), str(item.get("destination", ""))]
        if endpoint in ends:
            other = ends[1 - ends.index(endpoint)]
            if "." not in other and other not in blocks:
                names.add(other)
    return names


def on_rtl(name: str, ports: list[str]) -> bool:
    """A scalar port, or an interface port whose members the RTL uses as `<name>_<member>`."""
    return any(port == name or port.startswith(name + "_") for port in ports)


def related(endpoint: str, ports: list[str]) -> list[str]:
    tokens = {token for token in re.split(r"[^a-z]+", endpoint.split(".", 1)[1].lower()) if len(token) > 2}
    return [port for port in ports if any(token in port.lower() for token in tokens)]


def check(spec: dict, rtl_text: str, wrapper: str) -> str:
    ports = rtl_instance_ports(rtl_text, wrapper)
    if ports is None:
        return f"BOUNDARY_NAMES:NA no instance of {wrapper}"
    interfaces = top_level_interfaces(spec)
    bad = []
    for endpoint, _interface in interfaces:
        names = accepted_names(spec, endpoint)
        if not any(on_rtl(name, ports) for name in names):
            hint = related(endpoint, ports)
            bad.append(f"{endpoint} accepts {{{' '.join(sorted(names))}}}; RTL ports that look related: "
                       f"{{{' '.join(hint)}}}")
    if bad:
        return (f"BOUNDARY_NAMES_FAIL:{len(bad)} of {len(interfaces)} top_level interface(s) have no accepted "
                f"port name on the {wrapper} instance\n  " + "\n  ".join(bad) +
                "\n  -> boundary_names.py add --spec <spec> <block>.<interface>=<rtl port>[,<rtl port>...]"
                " (one RTL port per bit when the RTL splits it), then validate the spec and bind;"
                " an interface the RTL does not use keeps an accepted name for its port")
    return f"BOUNDARY_NAMES:OK {len(interfaces)} top_level interface(s) named on the {wrapper} instance"


def add(spec: dict, mappings: dict[str, list[str]]) -> int:
    """Append a required connection from each top_level interface to each RTL port name."""
    interfaces = dict(top_level_interfaces(spec))
    contracts = spec.setdefault("functional_contracts", {})
    connections = contracts.setdefault("connections", [])
    added = 0
    for endpoint, ports in mappings.items():
        if endpoint not in interfaces:
            raise ValueError(f"{endpoint} is not a top_level interface in the DesignSpec")
        interface = interfaces[endpoint]
        incoming = str(interface.get("direction", "")).lower() in ("in", "input", "i", "slave", "sink")
        for port in ports:
            if not re.fullmatch(r"\w+", port):
                raise ValueError(f"{port!r} is not a bare port name")
            source, destination = (port, endpoint) if incoming else (endpoint, port)
            if any(isinstance(item, dict) and item.get("required", True) and
                   {str(item.get("source")), str(item.get("destination"))} == {endpoint, port}
                   for item in connections):
                continue
            connections.append({
                "source": source, "destination": destination,
                "protocol": str(interface.get("protocol") or "top-level port"),
                "purpose": f"RTL top port {port} carries {endpoint}",
                "required": True,
            })
            added += 1
    return added


def parse_mappings(items: list[str]) -> dict[str, list[str]]:
    mappings: dict[str, list[str]] = {}
    for item in items:
        endpoint, sep, ports = item.partition("=")
        if not sep or "." not in endpoint or not ports:
            raise ValueError(f"{item!r}: expected <block>.<interface>=<port>[,<port>...]")
        mappings.setdefault(endpoint, []).extend(port for port in ports.split(",") if port)
    return mappings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    check_parser = sub.add_parser("check")
    check_parser.add_argument("--spec", type=Path, required=True)
    check_parser.add_argument("--rtl", type=Path, required=True)
    check_parser.add_argument("--wrapper", default="design_1_wrapper")
    add_parser = sub.add_parser("add")
    add_parser.add_argument("--spec", type=Path, required=True)
    add_parser.add_argument("mappings", nargs="+")
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text())
        if args.command == "check":
            result = check(spec, args.rtl.read_text(errors="replace"), args.wrapper)
            print(result)
            return 0 if not result.startswith("BOUNDARY_NAMES_FAIL") else 1
        added = add(spec, parse_mappings(args.mappings))
        if added:
            args.spec.write_text(json.dumps(spec, indent=2) + "\n")
        print(f"BOUNDARY_NAMES:ADDED {added} connection(s) to {args.spec}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"BOUNDARY_NAMES_FAIL:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
