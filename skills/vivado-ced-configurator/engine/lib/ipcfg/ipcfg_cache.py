#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Learned-config cache engine for the vivado-ip-configurator skill (idea #3).

Stores ONLY earned parameter-DISCOVERY facts so repeated runs stop re-deriving
the same mapping (kills the "found-then-lost" run-to-run variance) and cost
fewer MCP calls. It stores NO prompt/expected values, so it can never leak
benchmark answers:

  key   = "<ip>|<normalized feature phrase>"
  value = {
    "ip": "...", "feature": "...",
    "param": "CONFIG.<NAME>",          # which param implements the feature
    "shape": "scalar|comma-list|nested",
    "enabler": "<gating parent param>|null",
    "value_src": "user|default",       # whether the feature needs a non-default value
    "doc": "<citation/feedback that established it>",
    "ip_version": "<x.y>"
  }

Note there is deliberately no "value" field. A blind run may consult/populate
this cache freely.

CLI (the Tcl helpers shell out to this):
  ipcfg_cache.py get  <path> <ip> <feature>
  ipcfg_cache.py put  <path> <ip> <feature> <param> <shape> <enabler> <value_src> <doc> <ip_version>
  ipcfg_cache.py dump <path>
"""
import json
import os
import sys

FORBIDDEN_FIELDS = {"value", "expected", "expected_value", "want"}


def _norm_feature(feature: str) -> str:
    return " ".join(str(feature).strip().lower().split())


def _key(ip: str, feature: str) -> str:
    return f"{str(ip).strip().lower()}|{_norm_feature(feature)}"


def _load(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def cache_get(path: str, ip: str, feature: str):
    return _load(path).get(_key(ip, feature))


def cache_put(path: str, ip: str, feature: str, param: str, shape: str,
              enabler: str, value_src: str, doc: str, ip_version: str) -> dict:
    entry = {
        "ip": str(ip),
        "feature": _norm_feature(feature),
        "param": param,
        "shape": shape,
        "enabler": (None if enabler in ("", "null", "None") else enabler),
        "value_src": value_src,
        "doc": doc,
        "ip_version": ip_version,
    }
    # blind-integrity guard: never persist a concrete answer value
    for bad in FORBIDDEN_FIELDS:
        entry.pop(bad, None)
    data = _load(path)
    data[_key(ip, feature)] = entry
    _save(path, data)
    return entry


def main(argv) -> int:
    if len(argv) < 2:
        print("usage: ipcfg_cache.py {get|put|dump} ...", file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "get" and len(argv) == 5:
        entry = cache_get(argv[2], argv[3], argv[4])
        if entry is not None:
            print(json.dumps(entry))
        return 0
    if cmd == "put" and len(argv) == 11:
        print(json.dumps(cache_put(*argv[2:11])))
        return 0
    if cmd == "dump" and len(argv) == 3:
        print(json.dumps(_load(argv[2]), indent=2, sort_keys=True))
        return 0
    print(f"bad args for '{cmd}': {argv[2:]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
