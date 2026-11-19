#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Release-adaptive technology binding for DesignSpec assembly templates.

Workflow:
  probe-tcl --intent binding_intent.json --out binding_probe.tcl
  parse-probe --input probe_output.txt --out binding_snapshot.json
  bind --spec design_spec.json --intent binding_intent.json
       --snapshot binding_snapshot.json --template assembly_plan.template.json
       --out assembly_plan.json

The binder never generates raw Tcl operations. It resolves symbolic placeholders
inside a pre-reviewed assembly-plan template against the live catalog snapshot.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


PREFIX = "IPIASM_BIND"
PLACEHOLDER = re.compile(r"^\$\{([A-Za-z0-9_.:-]+)\}$")
EMBEDDED = re.compile(r"\$\{([A-Za-z0-9_.:-]+)\}")
LEFTOVER = re.compile(r"\$\{(?:bind|env)\.[^}]*\}")


class BindError(ValueError):
    pass


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _tcl(value: str) -> str:
    return "{" + value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}") + "}"


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise BindError(f"{path}: root must be an object")
    return value


def validate_intent(intent: dict[str, Any]) -> None:
    blocks = intent.get("blocks")
    if intent.get("schema_version") != 1:
        raise BindError("binding intent schema_version must be 1")
    if not isinstance(blocks, dict) or not blocks:
        raise BindError("binding intent blocks must be a non-empty object")
    for role, block in blocks.items():
        if not isinstance(block, dict):
            raise BindError(f"blocks.{role} must be an object")
        candidates = block.get("vlnv_candidates")
        if not isinstance(candidates, list) or not candidates:
            raise BindError(f"blocks.{role}.vlnv_candidates must be non-empty")
        if any(str(candidate).count(":") != 2 for candidate in candidates):
            raise BindError(
                f"blocks.{role}.vlnv_candidates must be version-free vendor:library:name"
            )
        if "instance" in block and (not isinstance(block["instance"], str)
                                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", block["instance"])):
            raise BindError(f"blocks.{role}.instance must be a cell name (a letter, then letters, digits or _)")


def _reference_configs(spec: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """CONFIG.* reference_config of each DesignSpec block, by block name."""
    configs: dict[str, dict[str, str]] = {}
    for block in (spec or {}).get("blocks") or []:
        config = ((block.get("parameters") or {}).get("reference_config") or {}) if isinstance(block, dict) else {}
        if isinstance(config, dict):
            kept = {str(k): str(v) for k, v in config.items() if str(k).startswith("CONFIG.")}
            if kept:
                configs[str(block.get("name"))] = kept
    return configs


def generate_probe_tcl(
    intent: dict[str, Any], records_out: Path | None = None, spec: dict[str, Any] | None = None
) -> str:
    """Tcl that creates each role's IP in the scratch BD and emits its catalog record.

    With a DesignSpec, each probe cell first takes its block's reference_config: interfaces
    such as axi_gpio GPIO2 (C_IS_DUAL) or MicroBlaze V M_AXI_DP (C_D_AXI) exist only when
    configured, and a default cell made the intent's aliases for them fail to bind.
    """
    validate_intent(intent)
    configs = _reference_configs(spec)
    lines = [
        f"set __BIND_PREFIX {_tcl(PREFIX)}",
        "proc __bind_emit {key value} {",
        "  global __BIND_PREFIX",
        "  set encoded [binary encode base64 -maxlen 0 [encoding convertto utf-8 $value]]",
        '  set line "$__BIND_PREFIX\\t$key\\t$encoded"',
        "  if {![info exists ::__bind_quiet]} {puts $line}",
        "  if {[info exists ::__bind_fh]} {puts $::__bind_fh $line; flush $::__bind_fh}",
        "}",
        '__bind_emit environment.vivado_version [version -short]',
        '__bind_emit environment.part [get_property -quiet PART [current_project]]',
        '__bind_emit environment.board_part [get_property -quiet BOARD_PART [current_project]]',
        '__bind_emit environment.capability.can_connect [expr {[llength [info commands bd::util_cmd]] > 0}]',
        '__bind_emit environment.capability.dump_param_deps [expr {[llength [info commands ::debug::dump_param_deps]] > 0}]',
        'set __boards {}; foreach b [get_board_part_interfaces -quiet] {lappend __boards [get_property NAME $b]}',
        '__bind_emit environment.board_interfaces [join [lsort -unique $__boards] ,]',
    ]
    if records_out is not None:
        lines[1:1] = [
            f"set __bind_records {_tcl(str(records_out.resolve()))}",
            "file mkdir [file dirname $__bind_records]",
            "set __bind_fh [open $__bind_records w]",
        ]
    for index, (role, block) in enumerate(intent["blocks"].items()):
        candidates = " ".join(_tcl(str(candidate)) for candidate in block["vlnv_candidates"])
        probe = f"ipiasm_bind_probe_{index}"
        lines += [
            f"set __resolved_{index} {{}}",
            f"__bind_emit catalog.{role}.requested {{{','.join(map(str, block['vlnv_candidates']))}}}",
            "set __attempt 0",
            f"foreach __candidate {{{candidates}}} {{",
            '  set __defs [lsort -dictionary -decreasing [get_ipdefs -all -quiet "${__candidate}:*"]]',
            "  foreach __definition $__defs {",
            "    incr __attempt",
            # Through ipiasm's probe helper, so the guard on hand-made create_bd_cell lets it run.
            f"    if {{[catch {{if {{[llength [info commands ::ipiasm::_probe_create]]}} {{::ipiasm::_probe_create $__definition {probe}}} else {{create_bd_cell -type [expr {{[lindex [split $__definition :] 1] eq \"inline_hdl\" ? \"inline_hdl\" : \"ip\"}}] -vlnv $__definition {probe}}}}} __create]}} {{",
            f"      __bind_emit catalog.{role}.attempt_${{__attempt}} \"$__definition: $__create\"",
            f"      set __failed_cells [get_bd_cells -quiet {probe}]",
            "      if {[llength $__failed_cells]} {delete_bd_objs $__failed_cells}",
            "      continue",
            "    }",
            f"    set __resolved_{index} $__definition",
            "    break",
            "  }",
            f"  if {{$__resolved_{index} ne {{}}}} {{break}}",
            "}",
            f"__bind_emit catalog.{role}.resolved_vlnv $__resolved_{index}",
            f"if {{$__resolved_{index} ne {{}}}} {{",
        ]
        config = configs.get(str(block.get("spec_block", role)))
        if config:
            # -quiet: a value this release rejects leaves the probe's default, not a failed round.
            pairs = " ".join(f"{_tcl(key)} {_tcl(value)}" for key, value in sorted(config.items()))
            lines.append(f"    catch {{set_property -quiet -dict [list {pairs}] $__create}}")
        lines += [
            "    set __props {}; foreach p [list_property $__create] {if {[string match CONFIG.* $p]} {lappend __props $p}}",
            f"    __bind_emit catalog.{role}.config_properties [join [lsort $__props] ,]",
            "    set __intfs {}; foreach p [get_bd_intf_pins -quiet -of_objects $__create] {lappend __intfs \"[get_property NAME $p]|[get_property MODE $p]|[get_property VLNV $p]\"}",
            f"    __bind_emit catalog.{role}.interfaces [join [lsort $__intfs] ,]",
            "    set __pins {}; foreach p [get_bd_pins -quiet -of_objects $__create] {set left [get_property -quiet LEFT $p]; set right [get_property -quiet RIGHT $p]; set width 1; if {$left ne {} && $right ne {}} {set width [expr {abs($left - $right) + 1}]}; lappend __pins \"[get_property NAME $p]|[get_property DIR $p]|[get_property TYPE $p]|$width\"}",
            f"    __bind_emit catalog.{role}.pins [join [lsort $__pins] ,]",
            "    delete_bd_objs $__create",
            "}",
        ]
    lines += [
        '__bind_emit probe.complete 1',
        'if {[info exists __bind_fh]} {close $__bind_fh; unset __bind_fh}',
        f'puts "{PREFIX}:DONE"',
    ]
    return "\n".join(lines) + "\n"


def parse_probe(text: str) -> dict[str, Any]:
    payloads = [text]
    try:
        envelope = json.loads(text)
    except (ValueError, TypeError):
        envelope = None
    if isinstance(envelope, dict) and isinstance(envelope.get("output"), str):
        payloads.append(envelope["output"])
    flat: dict[str, str] = {}
    for payload in payloads:
        for line in payload.splitlines():
            parts = line.strip(" \r").split("\t", 2)
            if len(parts) != 3 or parts[0] != PREFIX:
                continue
            try:
                flat[parts[1]] = base64.b64decode(parts[2]).decode()
            except (ValueError, UnicodeDecodeError) as exc:
                raise BindError(f"invalid probe payload for {parts[1]}: {exc}") from exc
    if not flat:
        raise BindError("probe output contains no binding records")
    if flat.get("probe.complete") != "1":
        raise BindError("IP discovery did not finish; rerun the probe before selecting IP")
    snapshot: dict[str, Any] = {"environment": {}, "catalog": {}}
    for key, value in flat.items():
        parts = key.split(".")
        node: dict[str, Any] = snapshot
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    env = snapshot["environment"]
    env["capabilities"] = env.pop("capability", {})
    env["board_interfaces"] = [
        item for item in env.get("board_interfaces", "").split(",") if item
    ]
    for block in snapshot["catalog"].values():
        block["config_properties"] = [
            item for item in block.get("config_properties", "").split(",") if item
        ]
        block["interfaces"] = [
            dict(zip(("name", "mode", "vlnv"), item.split("|", 2)))
            for item in block.get("interfaces", "").split(",") if item
        ]
        block["pins"] = [
            dict(zip(("name", "direction", "type", "width"), item.split("|", 3)))
            for item in block.get("pins", "").split(",") if item
        ]
    return snapshot


def _choose_alias(available: set[str], aliases: list[str], label: str) -> str:
    for alias in aliases:
        if alias in available:
            return alias
    raise BindError(f"{label}: none of aliases {aliases} exists")


def _choose_endpoint(
    available: list[dict[str, Any]], requirement: Any, label: str,
) -> str:
    if isinstance(requirement, list):
        return _choose_alias({item["name"] for item in available}, requirement, label)
    if not isinstance(requirement, dict) or not isinstance(requirement.get("aliases"), list):
        raise BindError(f"{label}: expected aliases or an endpoint requirement with aliases")
    allowed = {"aliases", "vlnv", "mode", "direction", "type", "width"}
    if set(requirement) - allowed:
        raise BindError(f"{label}: unknown endpoint requirements {sorted(set(requirement) - allowed)}")
    for alias in requirement["aliases"]:
        for observed in available:
            if observed["name"] != alias:
                continue
            if all(
                str(observed.get(key, "")).casefold() == str(value).casefold()
                for key, value in requirement.items() if key != "aliases"
            ):
                return alias
    raise BindError(f"{label}: no endpoint satisfies {requirement}; observed {available}")


def _instance_name(block: dict[str, Any], role: str) -> str:
    """Derive a safe instance name from the DesignSpec, never a baked-in plan."""
    value = (
        block.get("instance_name")
        or block.get("instance")
        or block.get("name")
        or role
    )
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(value)).strip("_")
    if not name:
        raise BindError(f"{role}: DesignSpec does not provide a usable block name")
    if not name[0].isalpha():
        name = f"block_{name}"
    return name


def resolve_bindings(
    spec: dict[str, Any], intent: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    validate_intent(intent)
    spec_blocks = {block.get("name"): block for block in spec.get("blocks", [])}
    # Several roles may serve one spec block (a controller and the memory behind it). Only
    # one cell can carry the block's name: a role named like the block, or one that says so
    # with "instance". Every other role on a shared block is named for the role.
    sharing: dict[str, int] = {}
    for role, wanted in intent["blocks"].items():
        sharing[wanted.get("spec_block", role)] = sharing.get(wanted.get("spec_block", role), 0) + 1
    resolved: dict[str, Any] = {"blocks": {}}
    unresolved: list[str] = []
    for role, wanted in intent["blocks"].items():
        semantic_name = wanted.get("spec_block", role)
        if semantic_name not in spec_blocks and wanted.get("required", True):
            unresolved.append(f"{role}: DesignSpec has no block {semantic_name!r} (a CED-seeded draft keeps "
                              "the CED's own cells without a role: drop the role, or add the block to the spec)")
            continue
        observed = (snapshot.get("catalog") or {}).get(role, {})
        if observed.get("create_error"):
            unresolved.append(f"{role}: IP creation failed: {observed['create_error']}")
            continue
        vlnv = observed.get("resolved_vlnv", "")
        if not vlnv:
            unresolved.append(
                f"{role}: none of {wanted['vlnv_candidates']} is available"
            )
            continue
        interfaces = observed.get("interfaces", [])
        pins = observed.get("pins", [])
        properties = set(observed.get("config_properties", []))
        if wanted.get("instance"):
            instance = wanted["instance"]
        elif sharing[semantic_name] == 1 or role == semantic_name:
            instance = _instance_name(spec_blocks[semantic_name], role)
        else:
            instance = _instance_name({}, role)
        block_result: dict[str, Any] = {
            "instance": instance,
            "spec_block": semantic_name,
            "vlnv": vlnv,
            "vlnv_unversioned": ":".join(vlnv.split(":")[:3]),
            "interfaces": {},
            "pins": {},
            "properties": {},
        }
        try:
            for name, aliases in (wanted.get("interfaces") or {}).items():
                block_result["interfaces"][name] = _choose_endpoint(
                    interfaces, aliases, f"{role}.interfaces.{name}"
                )
            for name, aliases in (wanted.get("pins") or {}).items():
                block_result["pins"][name] = _choose_endpoint(
                    pins, aliases, f"{role}.pins.{name}"
                )
            for name, aliases in (wanted.get("properties") or {}).items():
                block_result["properties"][name] = _choose_alias(
                    properties, aliases, f"{role}.properties.{name}"
                )
        except BindError as exc:
            unresolved.append(str(exc))
            continue
        resolved["blocks"][role] = block_result
    owners: dict[str, list[str]] = {}
    for role, block in resolved["blocks"].items():
        owners.setdefault(block["instance"], []).append(role)
    unresolved += [f"roles {' '.join(roles)} would all create cell {name!r}; give each its own "
                   f"blocks.<role>.instance, or name at most one role like its spec block"
                   for name, roles in sorted(owners.items()) if len(roles) > 1]
    if unresolved:
        raise BindError("unresolved bindings:\n  " + "\n  ".join(unresolved))
    return resolved


def _lookup(bindings: dict[str, Any], environment: dict[str, Any], path: str) -> Any:
    if path.startswith("env."):
        node: Any = environment
        parts = path.split(".")[1:]
    elif path.startswith("bind."):
        node = bindings
        parts = path.split(".")[1:]
    else:
        raise BindError(f"unknown placeholder root {path!r}")
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise BindError(f"unresolved placeholder ${{{path}}}")
        node = node[part]
    return node


def _resolve_template(
    value: Any, bindings: dict[str, Any], environment: dict[str, Any]
) -> Any:
    if isinstance(value, dict):
        return {
            key: _resolve_template(item, bindings, environment)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_template(item, bindings, environment) for item in value]
    if isinstance(value, str):
        match = PLACEHOLDER.fullmatch(value)
        if match:
            return _lookup(bindings, environment, match.group(1))
        return EMBEDDED.sub(lambda m: _embedded(bindings, environment, m.group(1), value), value)
    return value


def _embedded(bindings: dict[str, Any], environment: dict[str, Any], path: str, text: str) -> str:
    """A placeholder inside a longer string, such as ``${bind.blocks.cam.instance}/video_out``."""
    if not path.startswith(("bind.", "env.")):
        return f"${{{path}}}"
    resolved = _lookup(bindings, environment, path)
    if isinstance(resolved, bool) or not isinstance(resolved, (str, int, float)):
        raise BindError(f"placeholder ${{{path}}} inside {text!r} is not a scalar; use it as the whole value")
    return str(resolved)


def _leftover_placeholders(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_leftover_placeholders(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_leftover_placeholders(item) for item in value), set())
    return set(LEFTOVER.findall(value)) if isinstance(value, str) else set()


def bind(
    spec: dict[str, Any],
    intent: dict[str, Any],
    snapshot: dict[str, Any],
    template: dict[str, Any],
    spec_sha256: str | None = None,
) -> dict[str, Any]:
    # spec_sha256 is the digest of the DesignSpec file bytes, which is what
    # compile_assembly_plan.py --spec checks. Without it the canonical JSON is
    # hashed, which only a caller holding no file can mean.
    bindings = resolve_bindings(spec, intent, snapshot)
    environment = snapshot.get("environment") or {}
    plan = _resolve_template(template, bindings, environment)
    if not isinstance(plan, dict):
        raise BindError("resolved plan is not an object")
    leftover = _leftover_placeholders(plan)
    if leftover:
        raise BindError(f"unresolved placeholders in bound plan: {sorted(leftover)}")
    plan["binding_context"] = {
        "vivado_version": environment.get("vivado_version", ""),
        "part": environment.get("part", ""),
        "board_part": environment.get("board_part", ""),
        "capabilities": environment.get("capabilities", {}),
        "design_spec_sha256": spec_sha256 or hashlib.sha256(
            json.dumps(spec, sort_keys=True).encode()
        ).hexdigest(),
        "binding_intent_sha256": hashlib.sha256(
            json.dumps(intent, sort_keys=True).encode()
        ).hexdigest(),
    }
    plan["resolved_bindings"] = bindings
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe-tcl")
    probe.add_argument("--intent", type=Path, required=True)
    probe.add_argument("--out", type=Path, required=True)
    probe.add_argument("--records-out", type=Path)
    probe.add_argument("--spec", type=Path, help="design_spec.json: probe cells take its reference_config")
    parse = sub.add_parser("parse-probe")
    parse.add_argument("--input", type=Path, required=True)
    parse.add_argument("--out", type=Path, required=True)
    resolve = sub.add_parser("bind")
    for name in ("spec", "intent", "snapshot", "template", "out"):
        resolve.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "probe-tcl":
            result = generate_probe_tcl(
                load_object(args.intent), records_out=args.records_out,
                spec=load_object(args.spec) if args.spec else None,
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(result)
            print(f"BIND_PROBE:OK {args.out}")
        elif args.command == "parse-probe":
            result = parse_probe(args.input.read_text(errors="replace"))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            print(f"BIND_SNAPSHOT:OK {args.out}")
        else:
            result = bind(
                load_object(args.spec),
                load_object(args.intent),
                load_object(args.snapshot),
                load_object(args.template),
                spec_sha256=hashlib.sha256(args.spec.read_bytes()).hexdigest(),
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2) + "\n")
            print(f"BIND:OK blocks={len(result['resolved_bindings']['blocks'])} {args.out}")
    except (OSError, ValueError, BindError) as exc:
        print(f"BIND_FAIL:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
