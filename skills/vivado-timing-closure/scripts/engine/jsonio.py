#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
jsonio.py -- atomic JSON on disk.

Every file the engine writes is read back by a later process (a restart, a
collect step, the recipe export), so a killed write must leave the previous
contents intact rather than half of the new ones. Same rule orchestrate.py uses
for state.json: tmp file in the same directory, then os.replace.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import json
import os
import tempfile


def write(path, payload):
    d = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(d):
        os.makedirs(d)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read(path, default=None):
    """Parse `path`, or return `default` when it is missing or malformed."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except (IOError, OSError, ValueError):
        return default
