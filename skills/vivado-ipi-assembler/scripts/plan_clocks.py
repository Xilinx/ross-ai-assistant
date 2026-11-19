#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Generate bounded clock topology candidates; only live Vivado can prove them.

Assumptions: rates and tolerances are integer Hz from accepted requirements;
sources describe live-discovered capabilities for the selected part/IP version.
No PLL equations, family limits, phase guarantees or hardware qualification are
inferred here. Unknown contract fields fail rather than silently losing intent.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from validate_feasibility import FeasibilityError, fields, load_json, require, text


def integer(value, label, minimum=0, maximum=2**64 - 1):
    require(type(value) is int and minimum <= value <= maximum,
            label + " must be an integer in [%s, %s]" % (minimum, maximum))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_request(request):
    """Reject ambiguous units, duplicate consumers and unsupported intent."""
    fields(request, {"schema_version", "fingerprint", "sources", "clocks"}, set(), "request")
    require(type(request["schema_version"]) is int and request["schema_version"] == 1, "unsupported schema")
    fields(request["fingerprint"], {"tool", "part", "board", "ip", "sources"}, set(), "fingerprint")
    for name, value in request["fingerprint"].items():
        text(value, "fingerprint " + name)
    require(type(request["sources"]) is dict and 0 < len(request["sources"]) <= 32, "sources must be a bounded map")
    for name, source in request["sources"].items():
        text(name, "source ID")
        fields(source, {"hz", "max_outputs", "vlnv", "evidence"}, {"requested_hz"}, "source " + name)
        integer(source["hz"], "source hz", 1)
        if "requested_hz" in source:
            integer(source["requested_hz"], "nominal reference hz", 1)
        integer(source["max_outputs"], "live max_outputs", 1, 32)
        text(source["vlnv"], "live VLNV")
        require(len(source["vlnv"].split(":")) == 4, "exact versioned VLNV required")
        text(source["evidence"], "live capability evidence")
    clocks = request["clocks"]
    require(type(clocks) is list and 0 < len(clocks) <= 32, "clocks must be a bounded list")
    names, sinks, reset_sinks = set(), set(), set()
    for clock in clocks:
        fields(clock, {"id", "source", "hz", "tolerance_hz", "sinks", "resets", "requirement"},
               {"coherent_group", "requested_hz"}, "clock")
        for name in ("id", "source", "requirement"):
            text(clock[name], "clock " + name)
        require(clock["id"] not in names, "duplicate clock ID")
        names.add(clock["id"])
        require(clock["source"] in request["sources"], "unknown clock source")
        integer(clock["hz"], "clock hz", 1)
        # Nominal wizard settings and exact propagated requirements can differ.
        # The nominal value never replaces the independent acceptance target.
        if "requested_hz" in clock:
            integer(clock["requested_hz"], "requested_hz", 1)
        integer(clock["tolerance_hz"], "clock tolerance_hz")
        require(clock["tolerance_hz"] < clock["hz"], "tolerance must not admit zero frequency")
        if "coherent_group" in clock:
            text(clock["coherent_group"], "coherent_group")
        require(type(clock["sinks"]) is list and 0 < len(clock["sinks"]) <= 1024, "clock needs bounded sinks")
        for sink in clock["sinks"]:
            text(sink, "clock sink")
            require(sink not in sinks, "duplicate clock sink")
            sinks.add(sink)
        require(type(clock["resets"]) is list and len(clock["resets"]) <= 1024, "resets must be a bounded list")
        for reset in clock["resets"]:
            fields(reset, {"sink", "source", "source_polarity", "output", "release", "lock"}, set(), "reset")
            for name, value in reset.items():
                # An empty lock declares a clock with no lock output, such as a PS reference clock.
                if name == "lock" and value == "":
                    continue
                text(value, "reset " + name)
            require(reset["source_polarity"] in ("ACTIVE_LOW", "ACTIVE_HIGH"), "unknown reset source polarity")
            require(reset["output"] in ("peripheral_aresetn", "peripheral_reset", "interconnect_aresetn", "bus_struct_reset"), "unsupported reset output")
            require(reset["release"] == "proc_sys_reset", "unsupported reset release policy")
            require(reset["sink"] not in reset_sinks, "duplicate reset sink")
            reset_sinks.add(reset["sink"])
    return clocks


def derive_reset_domains(clocks):
    """Share a synchronizer only within one clock identity and reset policy.

    Clock-only consumers contribute no reset. Output sequencing classes may
    share a controller, but different reset sources or lock policies may not.
    Equal frequencies never establish a synchronous relationship.
    """
    groups = {}
    for clock in clocks:
        for reset in clock["resets"]:
            key = (clock["id"], reset["source"], reset["source_polarity"], reset["release"], reset["lock"])
            if key not in groups:
                groups[key] = {"id": "reset_" + digest(key)[:16], "clock": clock["id"],
                               "source": reset["source"], "source_polarity": reset["source_polarity"],
                               "release": reset["release"], "lock": reset["lock"], "sinks": []}
            groups[key]["sinks"].append({"pin": reset["sink"], "output": reset["output"]})
    return list(groups.values())


def plan_clock_architecture(request, limit=8, search_limit=4096):
    """Return deterministic proposals, never a feasibility verdict.

    A coherent group is indivisible during partitioning; this alone does not
    prove phase alignment. Numeric phase/duty constraints need an explicit
    extension and live checks and are therefore currently rejected.
    """
    clocks = validate_request(request)
    integer(limit, "candidate limit", 1, 8)
    integer(search_limit, "search limit", 1, 65536)
    units = {}
    for clock in clocks:
        key = ("coherent", clock["coherent_group"]) if "coherent_group" in clock else ("clock", clock["id"])
        units.setdefault(key, []).append(clock)
    for unit in units.values():
        require(len({clock["source"] for clock in unit}) == 1, "coherent group spans sources; explicit phase architecture required")
        require(len(unit) <= request["sources"][unit[0]["source"]]["max_outputs"], "coherent group exceeds discovered output capacity")
    ordered_units = list(units.values())
    partitions, visited = [], 0

    def visit(index, groups, wanted):
        nonlocal visited
        if visited >= search_limit or len(partitions) >= limit:
            return
        visited += 1
        if index == len(ordered_units):
            if len(groups) == wanted:
                partitions.append([list(group) for group in groups])
            return
        unit = ordered_units[index]
        source = unit[0]["source"]
        for position, group in enumerate(groups):
            if group[0]["source"] == source and len(group) + len(unit) <= request["sources"][source]["max_outputs"]:
                visit(index + 1, groups[:position] + [group + unit] + groups[position + 1:], wanted)
        if len(groups) < wanted:
            visit(index + 1, groups + [unit], wanted)

    # Bound exploration independently of the number of returned candidates.
    # Hitting either bound means search is incomplete, not that silicon failed.
    for count in range(1, len(ordered_units) + 1):
        visit(0, [], count)
        if len(partitions) >= limit or visited >= search_limit:
            break
    candidates, seen = [], set()
    # PG321 prioritizes earlier outputs. Try a small reproducible ordering set;
    # do not spend the entire search on factorial output permutations.
    for ordering in ("requirement", "tightest", "reverse"):
        for partition in partitions:
            generators = []
            for group in partition:
                if ordering == "tightest":
                    group = sorted(group, key=lambda clock: (clock["tolerance_hz"], -clock["hz"], clock["id"]))
                elif ordering == "reverse":
                    group = list(reversed(group))
                source = group[0]["source"]
                generators.append({"source": source, "vlnv": request["sources"][source]["vlnv"],
                                   "input_hz": request["sources"][source]["hz"],
                                   "requested_input_hz": request["sources"][source].get("requested_hz", request["sources"][source]["hz"]),
                                   "outputs": [{"clock": clock["id"], "hz": clock["hz"],
                                                "requested_hz": clock.get("requested_hz", clock["hz"]),
                                                "tolerance_hz": clock["tolerance_hz"]} for clock in group]})
            signature = digest(generators)
            if signature not in seen:
                seen.add(signature)
                candidates.append({"id": signature, "generators": generators, "status": "PENDING_LIVE"})
    # Fewer generators is only a preference. A failed candidate is not evidence
    # that another partition cannot meet the unchanged requirement.
    candidates.sort(key=lambda candidate: len(candidate["generators"]))
    return {"schema_version": 1, "status": "CANDIDATES_ONLY", "hardware_status": "UNVERIFIED",
            "fingerprint": request["fingerprint"], "request_sha256": digest(request),
            "budget_id": "clock_architecture", "candidate_limit": limit,
            "search_truncated": visited >= search_limit or len(partitions) >= limit or len(candidates) > limit,
            "candidates": candidates[:limit], "reset_domains": derive_reset_domains(clocks),
            "contracts": clocks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--tcl", action="store_true", help="Emit Tcl data, never executable commands")
    args = parser.parse_args()
    try:
        result = plan_clock_architecture(load_json(args.request), args.limit)
    except (FeasibilityError, ValueError, OSError, RecursionError) as error:
        print(json.dumps({"status": "CLOCK_PLAN_FAIL", "error": str(error)}))
        return 1
    print(tcl_data(result) if args.tcl else json.dumps(result, indent=2))
    return 0


def tcl_data(value):
    """Encode JSON-shaped data as a Tcl value without evaluation/substitution."""
    if isinstance(value, dict):
        value = [item for pair in value.items() for item in pair]
    if isinstance(value, list):
        return " ".join("{" + tcl_data(item) + "}" if isinstance(item, (dict, list))
                        else tcl_data(item) for item in value)
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    # Backslash quoting is interpreted by Tcl's list parser, not eval/source.
    return "".join("\\" + character if character in " \\{}[]$;\"\n\r\t" else character
                   for character in str(value)) or "{}"


if __name__ == "__main__":
    sys.exit(main())