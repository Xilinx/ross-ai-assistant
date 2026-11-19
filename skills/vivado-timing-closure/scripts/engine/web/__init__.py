# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
engine.web -- the run pages: the Workflows view's renderer in a browser, behind a secret link.

    views.py    any workflow's own view documents (workflow.json "view"), with the
                rules the Vivado MCP server uses (shared with web_run_platform/'s Django app)
    page.py     the page shell and its static files (static/)
    server.py   the stdlib HTTP server
    control.py  token, registry, start/stop/status, the run's link (tc web, run start)
"""
