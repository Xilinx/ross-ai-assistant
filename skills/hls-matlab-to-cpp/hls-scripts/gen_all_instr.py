#!/usr/bin/env python3
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
"""
gen_all_instr.py
Scan a MATLAB design directory, find all sub-function files, and generate
an instrumented version of each using gen_matlab_instr.gen_instr().

What it does:
  1. Lists all *.m files in the design directory.
  2. Skips files that are clearly not sub-functions:
       - Files that do NOT start with the 'function' keyword (they are scripts).
       - Already-instrumented files (*_instr.m).
       - Runner/testbench/utility files matching skip patterns.
  3. For each remaining function file:
       - Derives a prefix from the filename (e.g. hb2_fir_q.m -> hb2_fir_q).
       - Calls gen_instr(src, dst, prefix) to generate <name>_instr.m.
  4. Prints a summary of all generated files and tracked variable counts.

Usage:
  python gen_all_instr.py [--dir <design_dir>] [--skip <pattern,...>] [--dry-run]

Options:
  --dir    Design directory to scan (default: current working directory)
  --skip   Additional comma-separated filename substrings to skip
           (always skips: _instr, _runme, _tb, _range, _init)
  --dry-run  Print what would be generated without writing any files

Example:
  cd /path/to/design
  python gen_all_instr.py
  python gen_all_instr.py --dir ~/designs/my_filter --skip _util,_helper
  python gen_all_instr.py --dry-run
"""

import os
import re
import sys
import argparse

# Import the core instrumentation function from gen_matlab_instr.py
# (must be in the same directory or on PYTHONPATH)
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)
from gen_matlab_instr import gen_instr

# ── patterns that always cause a file to be skipped ──────────────────────────
DEFAULT_SKIP = [
    '_instr',    # already-instrumented files
    '_runme',    # runner/top-level scripts
    '_tb',       # testbenches
    '_range',    # range-collection scripts
    '_init',     # initialisation scripts
    'gen_',      # this script itself if accidentally named *.m
]

def is_function_file(path):
    """Return True if the .m file starts with a 'function' declaration."""
    try:
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith('%'):
                    continue          # skip blank lines and comments
                return stripped.startswith('function ')
    except (IOError, UnicodeDecodeError):
        pass
    return False

def stem(filename):
    """Return filename without .m extension."""
    return os.path.splitext(filename)[0]

def get_function_name(path):
    """Extract the MATLAB function name from the first 'function' line."""
    with open(path) as f:
        for line in f:
            m = re.match(r'\s*function\s+(?:\w+\s*=\s*)?(\w+)\s*\(', line)
            if m:
                return m.group(1)
    return stem(os.path.basename(path))

# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Generate instrumented MATLAB functions for all sub-functions in a design.')
    parser.add_argument('--dir',     default='.', help='Design directory to scan')
    parser.add_argument('--skip',    default='',  help='Additional skip patterns (comma-separated)')
    parser.add_argument('--dry-run', action='store_true', help='Print plan without writing files')
    args = parser.parse_args()

    design_dir = os.path.abspath(args.dir)
    skip_patterns = DEFAULT_SKIP + [p.strip() for p in args.skip.split(',') if p.strip()]

    print(f'\n=== gen_all_instr ===')
    print(f'Directory : {design_dir}')
    print(f'Skip pats : {skip_patterns}')
    print(f'Dry run   : {args.dry_run}')
    print()

    # Collect all .m files
    try:
        all_m = sorted(f for f in os.listdir(design_dir) if f.endswith('.m'))
    except FileNotFoundError:
        print(f'ERROR: directory not found: {design_dir}')
        sys.exit(1)

    # Filter
    to_process = []
    skipped    = []

    for fname in all_m:
        path = os.path.join(design_dir, fname)

        # Skip by name pattern
        if any(pat in fname for pat in skip_patterns):
            skipped.append((fname, 'name pattern'))
            continue

        # Skip if not a function (it's a script)
        if not is_function_file(path):
            skipped.append((fname, 'not a function (script)'))
            continue

        to_process.append(fname)

    # Report plan
    print(f'Found {len(all_m)} .m files — {len(to_process)} to instrument, '
          f'{len(skipped)} skipped.\n')

    if skipped:
        print('Skipped:')
        for fname, reason in skipped:
            print(f'  {fname:<40s}  ({reason})')
        print()

    print('Will instrument:')
    for fname in to_process:
        src  = os.path.join(design_dir, fname)
        name = stem(fname)
        dst  = os.path.join(design_dir, f'{name}_instr.m')
        print(f'  {fname:<40s}  ->  {name}_instr.m   [prefix={name}]')
    print()

    if args.dry_run:
        print('(dry-run — no files written)')
        return

    # Generate
    print('─' * 70)
    results = []
    for fname in to_process:
        src    = os.path.join(design_dir, fname)
        name   = stem(fname)
        dst    = os.path.join(design_dir, f'{name}_instr.m')
        prefix = name

        print(f'\n[{fname}]')
        try:
            # Capture the tracked variable count via gen_instr (it prints to stdout)
            gen_instr(src, dst, prefix)
            results.append((fname, dst, True, None))
        except Exception as e:
            print(f'  ERROR: {e}')
            results.append((fname, dst, False, str(e)))

    # Final summary
    print('\n' + '=' * 70)
    print('SUMMARY')
    print('=' * 70)
    ok  = [(s, d) for s, d, ok, _ in results if ok]
    err = [(s, e) for s, _, ok, e in results if not ok]
    print(f'Generated {len(ok)} instrumented files:')
    for src, dst in ok:
        print(f'  {os.path.basename(dst)}')
    if err:
        print(f'\nFailed ({len(err)}):')
        for src, e in err:
            print(f'  {src}: {e}')

    print(f'\nNext step:')
    print(f'  Update your runner script to call the *_instr versions.')
    print(f'  Prefix used in RANGE lines = function filename stem.')

if __name__ == '__main__':
    main()
