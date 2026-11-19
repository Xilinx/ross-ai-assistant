#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Bounded, offline validation of scratch capability evidence, never hardware PASS."""

import argparse
import json
from pathlib import Path
import re
import sys

MAX_BYTES = 1024 * 1024
MAX_CHECKS = 128
MAX_COVERAGE = 1024
STAGES = {"cells", "clocks", "connect", "qos", "address", "validate"}
FINGERPRINTS = {"spec", "tool", "part", "board", "ip"}


class FeasibilityError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise FeasibilityError(message)


def fields(value, required, optional, label):
    require(type(value) is dict, label + " must be an object")
    require(not (required - value.keys()), label + " missing " + str(sorted(required - value.keys())))
    require(not (value.keys() - required - optional), label + " unknown fields " + str(sorted(value.keys() - required - optional)))


def text(value, label):
    require(type(value) is str and bool(value.strip()) and len(value) <= 2048,
            label + " must be nonempty text (max 2048 characters)")


def identifiers(value, label, limit=MAX_COVERAGE, allow_empty=False):
    require(type(value) is list and len(value) <= limit and (allow_empty or bool(value)),
            label + " must be a bounded nonempty list")
    for item in value:
        text(item, label)
    require(len(set(value)) == len(value), label + " contains duplicate IDs")


def mapping(value, label, limit):
    require(type(value) is dict and 0 < len(value) <= limit, label + " must be a bounded nonempty map")
    for name in value:
        text(name, label + " ID")


def typed(value_type, value, label):
    text(value_type, label + " type")
    if value_type == "integer":
        require(type(value) is int and 0 <= value < 2**64, label + " must be an unsigned 64-bit integer")
    elif value_type == "string":
        text(value, label)
    elif value_type == "boolean":
        require(type(value) is bool, label + " must be boolean")
    elif value_type == "interface":
        fields(value, {"vlnv", "mode"}, set(), label + " interface")
        text(value["vlnv"], label + " interface VLNV")
        require(len(value["vlnv"].split(":")) == 4 and all(value["vlnv"].split(":")),
                label + " malformed interface VLNV")
        require(value["mode"] in ("Master", "Slave", "Monitor"), label + " interface mode")
    elif value_type == "range":
        fields(value, {"base", "bytes"}, set(), label + " range")
        require(type(value["base"]) is int and type(value["bytes"]) is int
                and value["base"] >= 0 and value["bytes"] > 0
                and value["base"] + value["bytes"] <= 2**64, label + " invalid range")
    else:
        raise FeasibilityError(label + " unsupported type " + value_type)


def selector(value, label):
    fields(value, {"property", "value", "source"}, set(), label + " selector")
    for name in value:
        text(value[name], label + " selector " + name)
    require(value["property"].startswith("CONFIG."), label + " selector needs CONFIG property")


def validate_manifest(manifest, expected):
    fields(expected, {"schema_version", "fingerprints", "coverage_ids", "setup", "checks"}, set(), "expected")
    fields(manifest, {"schema_version", "origin", "fingerprints", "setup", "observations"}, set(), "manifest")
    for label, document in (("expected", expected), ("manifest", manifest)):
        require(type(document["schema_version"]) is int and document["schema_version"] == 1,
                label + " unsupported schema_version")
        fingerprints = document["fingerprints"]
        mapping(fingerprints, label + " fingerprints", 128)
        require(FINGERPRINTS <= fingerprints.keys(), label + " missing required fingerprints")
        for name, digest in fingerprints.items():
            require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                    label + " malformed fingerprint " + name)
    require(manifest["fingerprints"] == expected["fingerprints"], "stale or unexpected fingerprints")
    require(manifest["origin"] in ("synthetic", "recorded"), "manifest origin must be synthetic or recorded")
    identifiers(expected["coverage_ids"], "coverage_ids")
    mapping(expected["checks"], "expected checks", MAX_CHECKS)
    mapping(manifest["observations"], "observation coverage", MAX_CHECKS)
    require(expected["checks"].keys() == manifest["observations"].keys(), "observation coverage mismatch")
    mapping(expected["setup"], "expected setup", MAX_CHECKS)
    mapping(manifest["setup"], "manifest setup", MAX_CHECKS)
    require(expected["setup"].keys() == manifest["setup"].keys(), "setup coverage mismatch")
    for name, setup in expected["setup"].items():
        fields(setup, {"kind", "description"}, set(), "setup " + name)
        require(setup["kind"] in ("connection", "configuration", "board"), "unsupported setup kind")
        text(setup["description"], "setup description")
        observed_setup = manifest["setup"][name]
        fields(observed_setup, {"completed", "evidence"}, set(), "setup evidence " + name)
        require(observed_setup["completed"] is True, "setup incomplete: " + name)
        text(observed_setup["evidence"], "setup evidence " + name)

    covered = []
    pending = []
    used_setup = set()
    for check_id, check in expected["checks"].items():
        fields(check, {"kind", "subject", "expected", "source", "derivation", "setup_requirements", "delivery"},
               {"selector", "assigned_window"}, check_id)
        kind = check["kind"]
        require(kind in ("config", "interface", "pin_width", "aperture_capacity"), check_id + " unsupported kind")
        text(check["subject"], check_id + " subject")
        text(check["source"], check_id + " source")
        fields(check["expected"], {"type", "value"}, set(), check_id + " expected")
        value_type = check["expected"]["type"]
        wanted = check["expected"]["value"]
        typed(value_type, wanted, check_id + " expected")
        legal_types = {"config": ("integer", "string", "boolean"), "interface": ("interface",),
                       "pin_width": ("integer",), "aperture_capacity": ("range",)}
        require(value_type in legal_types[kind], check_id + " kind/type mismatch")
        if kind == "pin_width":
            require(wanted > 0, check_id + " pin width must be positive")
        require(check["derivation"] in ("configured", "connection"), check_id + " unsupported derivation")
        setup_ids = check["setup_requirements"]
        identifiers(setup_ids, check_id + " setup requirements", MAX_CHECKS, allow_empty=True)
        require(set(setup_ids) <= expected["setup"].keys(), check_id + " missing setup requirements")
        used_setup.update(setup_ids)
        delivery = check["delivery"]
        fields(delivery, {"stage", "owner", "coverage_ids"}, set(), check_id + " delivery")
        text(delivery["stage"], check_id + " delivery stage")
        require(delivery["stage"] in STAGES, check_id + " unsupported delivery stage")
        text(delivery["owner"], check_id + " delivery owner")
        identifiers(delivery["coverage_ids"], check_id + " delivery coverage")
        if check["derivation"] == "connection":
            require(any(expected["setup"][name]["kind"] == "connection" for name in setup_ids),
                    check_id + " connection-derived check requires connection setup")
            require(delivery["stage"] in ("connect", "qos", "address", "validate"),
                    check_id + " connection-derived readback cannot precede connection stage")

        observed = manifest["observations"][check_id]
        fields(observed, {"supported", "subject", "type", "value", "evidence"}, {"selector"}, check_id + " observation")
        require(observed["supported"] is True, check_id + " unsupported or unresolved capability")
        require(observed["subject"] == check["subject"], check_id + " subject mismatch")
        require(observed["type"] == value_type, check_id + " observed type mismatch")
        typed(observed["type"], observed["value"], check_id + " observed")
        text(observed["evidence"], check_id + " evidence")
        if kind == "aperture_capacity":
            require("selector" in check and "selector" in observed, check_id + " missing selector")
            selector(check["selector"], check_id + " expected")
            selector(observed["selector"], check_id + " observed")
            require(all(observed["selector"][name] == check["selector"][name] for name in ("property", "value")),
                    check_id + " selector mismatch")
            capacity = observed["value"]
            require(capacity["base"] == wanted["base"] and capacity["bytes"] >= wanted["bytes"],
                    check_id + " aperture capacity insufficient or base mismatch")
            require("assigned_window" in check, check_id + " missing assigned_window")
            window = check["assigned_window"]
            fields(window, {"type", "value", "source", "stage", "owner", "coverage_id"}, set(), check_id + " assigned_window")
            require(window["type"] == "range", check_id + " assigned_window type must be range")
            typed(window["type"], window["value"], check_id + " assigned_window")
            text(window["source"], check_id + " assigned_window source")
            text(window["owner"], check_id + " assigned_window owner")
            text(window["coverage_id"], check_id + " assigned_window coverage")
            require(window["stage"] == "address", check_id + " assigned_window belongs to address stage")
            require(wanted["base"] <= window["value"]["base"]
                    and window["value"]["base"] + window["value"]["bytes"] <= wanted["base"] + wanted["bytes"],
                    check_id + " assigned_window outside required aperture")
            covered.append(window["coverage_id"])
            pending.append({"coverage_id": window["coverage_id"], "stage": "address", "owner": window["owner"],
                            "subject": check["subject"], "expected": {"type": "range", "value": window["value"]},
                            "reason": "Assigned software window requires independent delivery address readback"})
        else:
            require(not ({"selector", "assigned_window"} & check.keys()) and "selector" not in observed,
                    check_id + " unsupported aperture fields")
            require(observed["value"] == wanted, check_id + " value mismatch")
        covered.append(check_id)
        covered.extend(delivery["coverage_ids"])
        pending.append({"coverage_id": check_id, "stage": "scratch", "owner": "scratch_probe",
                        "subject": check["subject"], "expected": check["expected"],
                        "reason": "Offline evidence cannot authenticate hardware observations"})
        for coverage_id in delivery["coverage_ids"]:
            pending.append({"coverage_id": coverage_id, "stage": delivery["stage"], "owner": delivery["owner"],
                            "subject": check["subject"], "expected": check["expected"],
                            "reason": "Independent delivery readback remains mandatory"})
    require(used_setup == expected["setup"].keys(), "unused setup requirements")
    require(len(covered) == len(set(covered)) and set(covered) == set(expected["coverage_ids"]),
            "full coverage mismatch or duplicate coverage IDs")
    return {"status": "OFFLINE_EVIDENCE_OK", "hardware_status": "UNVERIFIED", "origin": manifest["origin"],
            "checks": len(expected["checks"]), "coverage_ids": sorted(covered),
            "fingerprints": dict(expected["fingerprints"]), "pending_live": pending}


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result, "duplicate JSON key " + name)
        result[name] = value
    return result


def load_json(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "JSON input exceeds 1 MiB")
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda value: require(False, "invalid JSON constant " + value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True,
                        help="Independent expectations and current fingerprint values; never copied from manifest")
    args = parser.parse_args()
    try:
        result = validate_manifest(load_json(args.manifest), load_json(args.expected))
    except (FeasibilityError, OSError, ValueError, RecursionError) as error:
        print(json.dumps({"status": "OFFLINE_EVIDENCE_FAIL", "hardware_status": "UNVERIFIED", "error": str(error)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())