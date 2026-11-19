#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""tc -- entrypoint for the timing-closure engine. See engine/cli.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
