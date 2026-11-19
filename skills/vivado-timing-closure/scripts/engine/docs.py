#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
docs.py -- `tc docs search`: AMD documentation search from any agent.

The planner's step "ground the fix" needs UG949 / UG906 / UG1387 text to check
that a property or directive applies to this device. An agent in an editor with
the Vivado AI extension has a doc-search tool; a CLI agent, or one without the
extension, has only a shell. This gives both the same service: AMD's
documentation MCP endpoint, spoken to over streamable HTTP.

Endpoint: $TC_DOC_SEARCH_URL, else `docs.url` in the engine config, else an
`amd-doc-search` (or any `*doc-search*` URL) entry in the user's MCP configs,
else the default the Vivado AI extension uses.

Only the query text leaves this machine. Queries must name concepts
("USER_CLOCK_ROOT Versal clock region"), never design names, paths or
netlist content -- the skill says so, and `search()` refuses absolute paths.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import json
import os
import re
import time
import urllib.error
import urllib.request

from . import config, mcp_discover

DEFAULT_URL = "https://vivado.amd.com/mcp/doc-search"
TOOL = "vivado_doc_search"
PROTOCOL = "2025-06-18"

_PATHLIKE = re.compile(r"(^|\s)/[\w.-]+/[\w./-]+")


class DocsError(Exception):
    pass


def endpoint(cfg=None, start_dir=None):
    """(url, where it came from)."""
    env = os.environ.get("TC_DOC_SEARCH_URL")
    if env:
        return env, "$TC_DOC_SEARCH_URL"
    url = config.get(cfg or {}, "docs", "url")
    if url:
        return url, "docs.url"
    paths = mcp_discover._project_files(start_dir) + [
        os.path.expanduser("~/.claude.json")] + [os.path.expanduser(p)
                                                 for p in mcp_discover.USER_FILES]
    for path in paths:
        data = mcp_discover._load(path) if os.path.isfile(path) else None
        if not isinstance(data, dict):
            continue
        blocks = [data.get("mcpServers"), data.get("servers")]
        blocks += [(p or {}).get("mcpServers") for p in (data.get("projects") or {}).values()
                   if isinstance(p, dict)]
        for servers in blocks:
            for name, entry in (servers or {}).items():
                u = isinstance(entry, dict) and entry.get("url")
                if u and ("doc-search" in u or name == "amd-doc-search"):
                    return u, "%s (%s)" % (path, name)
    return DEFAULT_URL, "default"


def _post(url, payload, session=None, timeout=60):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": PROTOCOL}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.headers.get("Mcp-Session-Id"), resp.headers.get("Content-Type") or "", body
    except (urllib.error.URLError, OSError) as exc:
        raise ServiceError("cannot reach %s: %s" % (url, exc))


def _message(content_type, body, request_id):
    """The JSON-RPC reply, from a JSON body or a server-sent-events stream."""
    chunks = [body]
    if "text/event-stream" in content_type:
        chunks = [ln[5:].strip() for ln in body.splitlines() if ln.startswith("data:")]
    for chunk in chunks:
        try:
            msg = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == request_id:
            return msg
    raise ServiceError("no reply to request %s" % request_id)


class ServiceError(DocsError):
    """The documentation service itself failed (its "internal error", for one)."""


def search(query, limit=5, family=None, version=None, url=None, timeout=60,
           retries=1, retry_wait=3):
    """
    The service's answer text for `query`. A failure on the service's side is
    retried `retries` times; if it persists, the message names the service, so
    nobody reads "internal error" as ours (on 2026-10-08 it answered every
    search that way, to Claude Code's own doc-search tool too).
    """
    url = url or DEFAULT_URL
    for attempt in range(retries + 1):
        try:
            return _search_once(query, limit, family, version, url, timeout)
        except ServiceError as exc:
            if attempt >= retries:
                raise ServiceError(
                    "the AMD documentation service (%s) failed: %s (%d attempt(s)). "
                    "This is on the service's side; try again later -- nothing in "
                    "the flow depends on it." % (url, exc, attempt + 1))
            time.sleep(retry_wait)


def _search_once(query, limit, family, version, url, timeout):
    query = (query or "").strip()
    if not query:
        raise DocsError("empty query")
    if _PATHLIKE.search(query):
        raise DocsError("the query looks like it contains a file path; search for the "
                        "concept, not design data (the query leaves this machine)")
    session, ctype, body = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                       "params": {"protocolVersion": PROTOCOL,
                                                  "capabilities": {},
                                                  "clientInfo": {"name": "tc", "version": "1"}}},
                                 timeout=timeout)
    init = _message(ctype, body, 1)
    if "error" in init:
        raise ServiceError("initialize failed: %s" % init["error"].get("message"))
    try:
        _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session,
              timeout=timeout)
    except DocsError:
        pass                      # optional for this server; the call decides
    args = {"query": query, "limit": int(limit)}
    if family:
        args["family"] = list(family)
    if version:
        args["version"] = list(version)
    _, ctype, body = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": TOOL, "arguments": args}},
                           session, timeout=timeout)
    msg = _message(ctype, body, 2)
    if "error" in msg:
        raise ServiceError("search failed: %s" % msg["error"].get("message"))
    result = msg.get("result") or {}
    text = "\n".join(c.get("text", "") for c in result.get("content") or []
                     if c.get("type") == "text").strip()
    if result.get("isError"):
        raise ServiceError(text or "search failed")
    return text
