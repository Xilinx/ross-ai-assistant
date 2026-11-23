# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run one skill script under cProfile for stage_trace.tcl.

Usage: trace_python.py OUT.jsonl SCRIPT.py [ARGS...]

SCRIPT runs as __main__ with the sys.argv, sys.path[0] and __file__ it would
have if launched directly, so its stdout, stderr and exit status are exactly
what ipcfg::python_exec would otherwise see. One JSON line naming the
functions that took the time is appended to OUT. Timings are inflated by
profiling overhead; compare them with each other, not with an untraced run.
"""

from cProfile import Profile
import json
import os
import pstats
import runpy
import sys
import time

TOP = 15


def _label(key):
    filename, line, function = key
    if filename == "~":
        return function
    return f"{os.path.basename(filename)}:{line}:{function}"


def _record(out, script, argv, elapsed, status, profile):
    stats = pstats.Stats(profile).stats
    rows = sorted(stats.items(), key=lambda item: item[1][2], reverse=True)[:TOP]
    record = {
        "v": 1,
        "ts": int(time.time() * 1000),
        "kind": "python_profile",
        "pid": os.getpid(),
        "script": os.path.basename(script),
        "argv": argv,
        "total_us": int(elapsed * 1e6),
        "status": status,
        "top": [
            {"fn": _label(key), "calls": calls, "excl_us": int(own * 1e6), "incl_us": int(cumulative * 1e6)}
            for key, (_, calls, own, cumulative, _) in rows
        ],
    }
    with open(out, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def main(argv):
    out, script = argv[1], argv[2]
    sys.argv = argv[2:]
    sys.path[0] = os.path.dirname(os.path.abspath(script))
    profiler = Profile()
    status = 0
    started = time.perf_counter()
    try:
        profiler.runcall(runpy.run_path, script, run_name="__main__")
    except SystemExit as stop:
        status = stop.code
        raise
    except BaseException:
        status = "exception"
        raise
    finally:
        try:
            _record(out, script, argv[3:], time.perf_counter() - started, status, profiler)
        except OSError:
            pass  # A failed trace write must never change the script's result.


if __name__ == "__main__":
    main(sys.argv)
