#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
page.py -- the page around a workflow view: stylesheets, the renderer, wf.js.

A page is a shell. workflow_view.js (the Vivado AI extension's own
workflowViewModel.ts, bundled; see web_run_platform/tools/build_workflow_view.sh)
parses and draws the documents in the browser, and wf.js does what the
editor's panel script does around it. Nothing here is inline script, so a
Content-Security-Policy of script-src 'self' holds.

Every value the page hands wf.js goes in a data-wf-* attribute: wf.js finds
its click targets with closest('[data-refresh]') and the like, and a body
attribute of the same name made every click on the page a "refresh" (row
details never opened).

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import os

try:
    from html import escape as _escape
except ImportError:                                  # pragma: no cover
    from cgi import escape as _escape

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_FILES = ("theme.css", "workflow_view.css", "workflow_view.js", "wf.js")


def esc(text):
    return _escape(str(text), quote=True)


def shell(title, page, data, prefix, links=(), noscript=""):
    """
    One page: page is "list" or "run"; data the values wf.js reads (workflow,
    workspace, arg, value, refresh, ...); prefix the URL prefix of the routes
    (/wf/, /api/wf/...); links extra (label, href) pairs for the header.
    """
    s = "%s/wf/static" % prefix
    values = dict(data, page=page, prefix=prefix)
    attrs = " ".join('data-wf-%s="%s"' % (k, esc(v)) for k, v in sorted(values.items()))
    extra = "".join('<a class="small" href="%s">%s</a>' % (esc(h), esc(l)) for l, h in links)
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="referrer" content="no-referrer">'
            "<title>%s</title>"
            '<link rel="stylesheet" href="%s/theme.css"><link rel="stylesheet" href="%s/workflow_view.css">'
            '<script src="%s/workflow_view.js" defer></script><script src="%s/wf.js" defer></script>'
            "</head><body %s>"
            '<header class="wf-top"><a class="wf-home" href="%s/wf/">Workflows</a>'
            '<span class="wf-sep">/</span><span class="wf-title">%s</span>'
            '<span class="wf-sp"></span><span id="wf-status" class="small muted"></span>%s</header>'
            '<main id="app"><p class="muted">Loading&hellip;</p></main>'
            '<div id="wf-toast" hidden></div>'
            "<noscript><p>This page needs JavaScript.%s</p></noscript>"
            "</body></html>"
            % (esc(title), s, s, s, s, attrs, esc(prefix), esc(title), extra, noscript))


def static_file(name):
    """(bytes, content type) of one of the page's static files, or None for anything else."""
    if name not in STATIC_FILES:
        return None
    path = os.path.join(STATIC_DIR, name)
    try:
        with open(path, "rb") as fh:
            body = fh.read()
    except (IOError, OSError):
        return None
    ctype = "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8"
    return body, ctype
