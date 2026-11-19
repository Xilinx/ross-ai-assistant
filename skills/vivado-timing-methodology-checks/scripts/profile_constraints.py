#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Profile every constraint of a `write_xdc -write_id` file, keyed by constraint ID.

Usage:
  python3 profile_constraints.py <constraints.xdc> <output.json> [wrapper_token ...]

Text-only (no open design needed). The unified constraints file written by
`write_xdc -write_id` puts a `# id:N` comment before each timing constraint. N is the
"constraint position" that `report_methodology` messages and the Timing Constraints
window quote, so it is the main key of the profile.

Output (see SKILL.md, "constraints_profile.json"):
  {
    "source": "<xdc path>",
    "constraint_count": <int>,
    "ip_instances": ["<scoped hierarchy whose last segment is inst/U0>", ...],
    "scopes": [{"hierarchy": "...", "is_ip": true|false}, ...],
    "constraints": {
      "<id>": {
        "id": <int>, "type": "<command>", "category": "<family>",
        "file": "<original xdc>", "line": <line in constraints.xdc>,
        "scope": "<current_instance hierarchy or ''>",
        "is_ip": bool, "ip_instance": "<matching IP hierarchy or null>",
        "value": <number|null>, "options": {"-name": "clk", "-datapath_only": true, ...},
        "from": {"classes": ["cell"], "objects": ["r11a_reg"], ...}, "to": {...},
        "through": {...}, "target": {...}, "clock": {...}, "source": {...},
        "groups": [{...}, ...], "text": "<constraint as written>",
        "shared_with": [<other IDs with the same file and text, e.g. a scoped file applied to
                         several instances; [] when the constraint is unique>]
      }
    },
    "shared_constraints": [{"file": "...", "text": "...", "ids": [3, 9], "scopes": ["u0/x", "u1/x"]}],
    "unnumbered": [{"line": N, "type": "...", "scope": "...", "is_ip": bool, "text": "..."}]
  }
Object spec: {"classes": [...], "objects": [...], "wildcard": bool,
              "of_objects": [{"classes": [...], "objects": [...]}]  (only for -of_objects),
              "filter": "<expr>" (only when a -filter was used)}.
Classes: cell, pin, port, net, clock, pblock, site, unresolved (bare name or unknown variable).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- tables

CLASS_OF_QUERY = {
    "get_cells": "cell", "get_pins": "pin", "get_ports": "port", "get_nets": "net",
    "get_clocks": "clock", "get_generated_clocks": "clock", "get_pblocks": "pblock",
    "get_sites": "site", "get_bels": "bel", "get_ips": "ip", "get_iobanks": "iobank",
    "all_inputs": "port", "all_outputs": "port", "all_clocks": "clock",
    "all_registers": "cell", "all_fanin": "pin", "all_fanout": "pin",
}

# get_* options that take an argument (everything else starting with '-' is a flag).
GET_ARG_OPTS = {"-filter", "-of_objects", "-of", "-match_style", "-regexp_style",
                "-start_point", "-end_point", "-from", "-to", "-through"}

# Options of timing commands that take an OBJECT argument -> role name.
ROLE_OPTS = {
    "-from": "from", "-rise_from": "from", "-fall_from": "from",
    "-to": "to", "-rise_to": "to", "-fall_to": "to",
    "-through": "through", "-rise_through": "through", "-fall_through": "through",
    "-clock": "clock", "-source": "source", "-master_clock": "master_clock",
    "-reference_pin": "reference_pin", "-group": "group",
}
# Options that take a plain-value argument.
VALUE_OPTS = {"-period", "-name", "-waveform", "-divide_by", "-multiply_by", "-edges",
              "-edge_shift", "-duty_cycle", "-comment", "-group_name", "-weight",
              "-priority", "-type", "-name_prefix"}

CATEGORY = {
    "create_clock": "clock_definition",
    "create_generated_clock": "generated_clock",
    "set_input_delay": "io_delay", "set_output_delay": "io_delay",
    "set_max_delay": "timing_exception", "set_min_delay": "timing_exception",
    "set_false_path": "timing_exception", "set_multicycle_path": "timing_exception",
    "set_bus_skew": "timing_exception", "set_data_check": "timing_exception",
    "set_max_time_borrow": "timing_exception",
    "set_clock_groups": "clock_relationship", "set_clock_sense": "clock_relationship",
    "set_clock_latency": "clock_property", "set_clock_uncertainty": "clock_property",
    "set_input_jitter": "clock_property", "set_system_jitter": "clock_property",
    "set_propagated_clock": "clock_property",
    "set_case_analysis": "case_analysis", "set_disable_timing": "disable_timing",
    "set_property": "property", "create_waiver": "waiver",
}


# --------------------------------------------------------------------------- tokenizer

def _split_words(s: str) -> list[tuple[str, str]]:
    """Split a Tcl command line into (kind, text) words.

    kind: 'bare' | 'brace' ({...}, text without braces) | 'bracket' ([...], inner text)
          | 'quote'. A trailing `;# comment` is dropped.
    """
    words: list[tuple[str, str]] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t":
            i += 1
        elif c == ";":
            break  # `;# comment` (or a second command: not expected in write_xdc output)
        elif c in "{[":
            close = "}" if c == "{" else "]"
            depth, j = 1, i + 1
            while j < n and depth:
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == c:
                    depth += 1
                elif s[j] == close:
                    depth -= 1
                j += 1
            words.append(("brace" if c == "{" else "bracket", s[i + 1:j - 1]))
            i = j
        elif c == '"':
            j = i + 1
            while j < n and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            words.append(("quote", s[i + 1:j]))
            i = j + 1
        else:
            j = i
            while j < n and s[j] not in " \t;":
                j += 1
            words.append(("bare", s[i:j]))
            i = j
    return words


def _list_items(text: str) -> list[str]:
    """Items of a Tcl list (brace/quote aware)."""
    return [w for _, w in _split_words(text.replace("\n", " "))]


def _num(text: str) -> int | float | None:
    if re.fullmatch(r"[+-]?\d+", text):
        return int(text)
    if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text):
        return float(text)
    return None


# --------------------------------------------------------------------------- object specs

def _merge(a: dict[str, Any] | None, b: dict[str, Any]) -> dict[str, Any]:
    """Merge object spec b into a (a role may hold several queries)."""
    if a is None:
        return b
    a["classes"] = sorted(set(a["classes"]) | set(b["classes"]))
    a["objects"] = a["objects"] + [o for o in b["objects"] if o not in a["objects"]]
    a["wildcard"] = a["wildcard"] or b["wildcard"]
    for k in ("of_objects",):
        if k in b:
            a.setdefault(k, []).extend(b[k])
    if "filter" in b:
        a["filter"] = (a["filter"] + " | " + b["filter"]) if "filter" in a else b["filter"]
    return a


def _spec_from_query(inner: str, variables: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Object spec of a `[get_cells ...]` / `[all_inputs]` bracket body."""
    words = _split_words(inner)
    if not words:
        return {"classes": ["unresolved"], "objects": [], "wildcard": False}
    cmd = words[0][1]
    cls = CLASS_OF_QUERY.get(cmd)
    if cls is None:
        return {"classes": ["unresolved"], "objects": [inner.strip()], "wildcard": False,
                "query": cmd}
    spec: dict[str, Any] = {"classes": [cls], "objects": [], "wildcard": False}
    k = 1
    while k < len(words):
        kind, w = words[k]
        if kind == "bare" and w.startswith("-") and _num(w) is None:
            if w in ("-of_objects", "-of") and k + 1 < len(words):
                sub = _spec_from_word(words[k + 1], variables)
                spec.setdefault("of_objects", []).append(
                    {"classes": sub["classes"], "objects": sub["objects"]})
                k += 2
            elif w == "-filter" and k + 1 < len(words):
                spec["filter"] = words[k + 1][1]
                k += 2
            elif w in GET_ARG_OPTS and k + 1 < len(words):
                k += 2
            else:
                k += 1
            continue
        for item in _list_items(w) if kind == "brace" else [w]:
            spec["objects"].append(item)
        k += 1
    spec["wildcard"] = any(re.search(r"[*?]", o) for o in spec["objects"])
    return spec


def _spec_from_word(word: tuple[str, str], variables: dict[str, dict[str, Any]]) -> dict[str, Any]:
    kind, w = word
    if kind == "bracket":
        return _spec_from_query(w, variables)
    if kind == "bare" and w.startswith("$"):
        v = variables.get(w[1:])
        if v is not None:
            return json.loads(json.dumps(v))  # copy
        return {"classes": ["unresolved"], "objects": [w], "wildcard": False}
    items = _list_items(w) if kind == "brace" else [w]
    return {"classes": ["unresolved"], "objects": items, "wildcard": False}


# --------------------------------------------------------------------------- command parsing

def _parse_command(text: str, variables: dict[str, dict[str, Any]]) -> dict[str, Any]:
    words = _split_words(text)
    cmd = words[0][1]
    out: dict[str, Any] = {"type": cmd, "category": CATEGORY.get(cmd, "other")}
    options: dict[str, Any] = {}
    roles: dict[str, dict[str, Any]] = {}
    groups: list[dict[str, Any]] = []
    positional: list[tuple[str, str]] = []

    k = 1
    while k < len(words):
        kind, w = words[k]
        if kind == "bare" and w.startswith("-") and _num(w) is None:
            if w in ROLE_OPTS and k + 1 < len(words):
                role = ROLE_OPTS[w]
                spec = _spec_from_word(words[k + 1], variables)
                if role == "group":
                    groups.append(spec)
                else:
                    roles[role] = _merge(roles.get(role), spec)
                    if w != "-" + role:  # keep -rise_from etc. visible
                        options.setdefault(w, True)
                k += 2
            elif w in VALUE_OPTS and k + 1 < len(words):
                raw = words[k + 1][1]
                options[w] = _num(raw) if _num(raw) is not None else raw
                k += 2
            else:
                options[w] = True
                k += 1
        else:
            positional.append(words[k])
            k += 1

    value: int | float | None = None
    if cmd == "create_clock":
        value = options.get("-period")
    elif cmd == "create_generated_clock":
        value = options.get("-divide_by", options.get("-multiply_by"))
    elif cmd == "set_property":
        # set_property NAME VALUE OBJECTS
        if len(positional) >= 3:
            options["property"] = positional[0][1]
            raw = positional[1][1]
            value = _num(raw) if _num(raw) is not None else raw
            positional = positional[2:]
    else:
        for idx, (kind, w) in enumerate(positional):
            if kind == "bare" and _num(w) is not None:
                value = _num(w)
                positional.pop(idx)
                break

    if positional:
        target: dict[str, Any] | None = None
        for word in positional:
            target = _merge(target, _spec_from_word(word, variables))
        roles["target"] = target  # type: ignore[assignment]

    if groups:
        out["groups"] = groups
    out["value"] = value
    out["options"] = options
    for role in ("from", "to", "through", "target", "clock", "source",
                 "master_clock", "reference_pin"):
        if role in roles:
            out[role] = roles[role]
    return out


# --------------------------------------------------------------------------- file walk

def _is_ip_scope(scope: str, ip_instances: list[str]) -> str | None:
    for ip in ip_instances:
        if scope == ip or scope.startswith(ip + "/"):
            return ip
    return None


def profile(xdc: Path, wrapper_tokens: list[str]) -> dict[str, Any]:
    wrapper = {t.lower() for t in wrapper_tokens}
    raw_lines = xdc.read_text().splitlines()

    # Pass 1: logical commands (continuations / open brackets joined), with context.
    scope = ""
    cur_file = ""
    pending_id: int | None = None
    scopes: list[str] = []
    variables: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []

    i = 0
    while i < len(raw_lines):
        line = raw_lines[i].strip()
        lineno = i + 1
        i += 1
        if not line:
            continue
        if line.startswith("#"):
            m = re.match(r"#\s*Constraints from file\s*:\s*'(.*)'", line)
            if m:
                cur_file = m.group(1)
            m = re.match(r"#\s*id:\s*(\d+)\s*$", line)
            if m:
                pending_id = int(m.group(1))
            continue
        # Join continuation lines and lines with unbalanced braces/brackets.
        while (line.endswith("\\")
               or line.count("{") > line.count("}")
               or line.count("[") > line.count("]")) and i < len(raw_lines):
            line = line.rstrip("\\").rstrip() + " " + raw_lines[i].strip()
            i += 1

        words = _split_words(line)
        if not words:
            continue
        cmd = words[0][1]

        if cmd == "current_instance":
            hier = ""
            for kind, w in words[1:]:
                if not (kind == "bare" and w.startswith("-")):
                    hier = w
                    break
            scope = hier
            if hier and hier not in scopes:
                scopes.append(hier)
            continue
        if cmd == "set" and len(words) == 3 and words[1][1].startswith("_xlnx_shared"):
            variables[words[1][1]] = _spec_from_word(words[2], variables)
            continue

        entry = _parse_command(line, variables)
        entry.update({"id": pending_id, "file": cur_file, "line": lineno,
                      "scope": scope, "text": line})
        pending_id = None
        entries.append(entry)

    ip_instances = [h for h in scopes if h.split("/")[-1].lower() in wrapper]

    constraints: dict[str, Any] = {}
    unnumbered: list[dict[str, Any]] = []
    for e in entries:
        ip = _is_ip_scope(e["scope"], ip_instances)
        e["is_ip"] = ip is not None
        e["ip_instance"] = ip
        if e["id"] is None:
            unnumbered.append({k: e[k] for k in
                               ("line", "type", "category", "file", "scope", "is_ip",
                                "ip_instance", "value", "options", "text")})
            continue
        ordered = {"id": e["id"], "type": e["type"], "category": e["category"],
                   "file": e["file"], "line": e["line"], "scope": e["scope"],
                   "is_ip": e["is_ip"], "ip_instance": e["ip_instance"],
                   "value": e["value"], "options": e["options"]}
        for role in ("from", "to", "through", "target", "clock", "source",
                     "master_clock", "reference_pin", "groups"):
            if role in e:
                ordered[role] = e[role]
        ordered["text"] = e["text"]
        constraints[str(e["id"])] = ordered

    # A scoped XDC file applied to several instances of a module is written once per
    # instance: the same text from the same original file under a different `current_instance`.
    # Link those copies so a resolution can see that a constraint is not unique.
    by_signature: dict[tuple[str, str], list[str]] = {}
    for cid, c in constraints.items():
        by_signature.setdefault((c["file"], " ".join(c["text"].split())), []).append(cid)
    shared: list[dict[str, Any]] = []
    for (file, text), ids in by_signature.items():
        if len(ids) < 2:
            continue
        shared.append({"file": file, "text": text, "ids": [int(i) for i in ids],
                       "scopes": [constraints[i]["scope"] for i in ids]})
        for i in ids:
            constraints[i]["shared_with"] = [int(o) for o in ids if o != i]
    for c in constraints.values():
        c.setdefault("shared_with", [])

    return {
        "source": str(xdc),
        "constraint_count": len(constraints),
        "ip_instances": ip_instances,
        "scopes": [{"hierarchy": h, "is_ip": h in ip_instances} for h in scopes],
        "shared_constraints": shared,
        "constraints": constraints,
        "unnumbered": unnumbered,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    xdc, out = Path(argv[1]), Path(argv[2])
    tokens = argv[3:] or ["inst", "U0"]
    if not xdc.is_file():
        print(f"profile_constraints: constraints file not found: {xdc}", file=sys.stderr)
        return 1
    result = profile(xdc, tokens)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1) + "\n")
    n_ip = sum(1 for c in result["constraints"].values() if c["is_ip"])
    print(f"profile_constraints: wrote {out} ({result['constraint_count']} constraints, "
          f"{n_ip} in IP scope, {len(result['ip_instances'])} IP instances)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
