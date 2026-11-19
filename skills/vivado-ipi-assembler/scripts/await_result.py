#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Wait for a long ipiasm call whose MCP tool call timed out, and print its result.

usage: await_result.py OUT [PROC ...] [--timeout SECONDS]

The MCP client can give up on a call (60 s in Claude Code) while Vivado keeps running it.
reference_search, example_reference, ced_reference, scratch_bind, assemble_from_plan and
deliver mark themselves running in <dir>/logs/<proc>.running and write their return value to
<dir>/logs/<proc>.result.txt when they end (lib/ipiasm.tcl, kept_results), where <dir> is the
directory the call was given. This reads OUT/logs and OUT/*/logs, so `deliver OUT/delivery`
is found from OUT too. It polls every 2 s until no marker is left for the named procs (all of
them when none is named), then prints the newest of their results. It reads files only; it
never talks to Vivado.

Prints RESULT <proc> (<age> s old) and the result, or STILL_RUNNING <proc> for <n> s after
--timeout (default 100, under the 120 s a shell tool call usually allows); run it again then.
Exit status is 0 in both cases and 2 when no logs directory holds a marker or a result.
"""
import argparse
import glob
import os
import sys
import time

# Result text past this is cut: the agent needs the status lines, not every log path.
LIMIT = 6000


def log_dirs(out):
    """OUT/logs first, then OUT/<sub>/logs, for calls given a subdirectory of OUT."""
    return [d for d in [os.path.join(out, 'logs')] + sorted(glob.glob(os.path.join(out, '*', 'logs')))
            if os.path.isdir(d)]


def markers(dirs, procs):
    """Paths of the .running markers left for procs (any proc when procs is empty)."""
    found = []
    for logs in dirs:
        names = procs or [os.path.basename(p)[:-len('.running')]
                          for p in glob.glob(os.path.join(logs, '*.running'))]
        found += [os.path.join(logs, f'{n}.running') for n in names
                  if os.path.isfile(os.path.join(logs, f'{n}.running'))]
    return found


def newest_result(dirs, procs):
    """The most recently written .result.txt for procs across dirs, or None."""
    paths = []
    for logs in dirs:
        paths += ([os.path.join(logs, f'{n}.result.txt') for n in procs] if procs
                  else glob.glob(os.path.join(logs, '*.result.txt')))
    paths = [p for p in paths if os.path.isfile(p)]
    return max(paths, key=os.path.getmtime) if paths else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('procs', nargs='*')
    ap.add_argument('--timeout', type=float, default=100.0)
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    deadline = time.monotonic() + a.timeout
    while True:
        # Rescanned each poll: a subdirectory's logs/ may appear only once its call starts.
        dirs = log_dirs(out)
        running = markers(dirs, a.procs)
        if not running or time.monotonic() >= deadline:
            break
        time.sleep(2)
    now = time.time()
    if running:
        for marker in running:
            try:
                started = float(open(marker).read().strip())
            except (OSError, ValueError):
                started = os.path.getmtime(marker)
            name = os.path.basename(marker)[:-len('.running')]
            print(f'STILL_RUNNING {name} for {int(now - started)} s ({marker}); run await_result.py again')
        return 0
    path = newest_result(dirs, a.procs)
    if path is None:
        print(f'NO_RESULT no .running marker or .result.txt in {out}/logs or {out}/*/logs')
        return 2
    text = open(path, encoding='utf-8', errors='replace').read()
    name = os.path.basename(path)[:-len('.result.txt')]
    print(f'RESULT {name} ({int(now - os.path.getmtime(path))} s old, {path})')
    print(text if len(text) <= LIMIT else text[:LIMIT] + f'\n... [{len(text) - LIMIT} more chars in {path}]')
    return 0


if __name__ == '__main__':
    sys.exit(main())
