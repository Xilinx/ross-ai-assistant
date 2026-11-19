#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
mcp_client.py -- a minimal JSON-RPC client for a *private* Vivado MCP backend.

The engine starts its own server with `--stdio` ("native stdio MCP transport,
in-process; no HTTP port and no shared daemon"). That detail is the whole
reason long runs can rely on it: the shared `:18090` daemon is what
orphaned sessions and killed three design A runs when a backend switch happened
underneath them. A private backend has nobody else to switch it.

Deliberately small. This speaks exactly the five calls the engine needs and
knows nothing about Vivado, LSF or run state -- those decisions belong to the
backend and the deterministic layer. It is not a general MCP client and should
not grow into one.

Protocol: JSON-RPC 2.0, newline-delimited, over the child's stdin/stdout.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import collections
import json
import os
import selectors
import subprocess
import threading
import time

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "tc-engine", "version": "0.1.0"}


# Bound to the editor window that started the shared server: a private server
# that inherits it watches that window instead of its own parent, and shuts
# down as soon as that window has gone ("backend is not running"). Without
# it the server watches its parent -- the engine that owns it.
PRIVATE_SERVER_DROP_ENV = ("VIVADO_EXTENSION_HOST_PID",)


def child_env(base, extra=None):
    """The environment for a private server: the caller's, minus what binds it to a window."""
    env = {k: v for k, v in dict(base).items() if k not in PRIVATE_SERVER_DROP_ENV}
    env.update(extra or {})
    return env


class McpError(Exception):
    """The server could not be started, or stopped answering."""


class ToolError(Exception):
    """The server ran the tool and it reported a failure."""


class McpClient(object):
    """One private MCP backend, owned by the engine process that started it."""

    def __init__(self, command, cwd=None, env=None, timeout=300, log=None,
                 stderr_path=None):
        self.command = list(command)
        self.cwd = cwd
        self.env = env
        self.timeout = timeout
        self.log = log or (lambda msg: None)
        # Where the server's stderr goes. It must always be consumed: a pipe
        # nobody reads holds 64 KB, and then the server blocks on its next log
        # line and stops answering (2026-10-08: two hours into a run, 64,018
        # bytes waiting, tools/call timed out after 600 s).
        self.stderr_path = stderr_path
        self._stderr_file = None
        self._stderr_lines = collections.deque(maxlen=200)
        self._proc = None
        self._next_id = 0
        self._lock = threading.Lock()
        self._sel = None

    # -- lifecycle ------------------------------------------------------

    @property
    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    def start(self):
        if self.alive:
            return self
        env = child_env(os.environ, self.env)
        stderr = subprocess.PIPE
        if self.stderr_path:
            try:
                d = os.path.dirname(self.stderr_path)
                if d:
                    os.makedirs(d, exist_ok=True)
                self._stderr_file = open(self.stderr_path, "a")
                stderr = self._stderr_file
            except (IOError, OSError):
                self._stderr_file = None
        try:
            self._proc = subprocess.Popen(
                self.command, cwd=self.cwd, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=stderr, universal_newlines=True, bufsize=1)
        except OSError as exc:
            raise McpError("cannot start %r: %s" % (self.command[0], exc))
        if stderr is subprocess.PIPE:
            t = threading.Thread(target=self._drain_stderr, args=(self._proc.stderr,),
                                 name="mcp-stderr", daemon=True)
            t.start()

        self._sel = selectors.DefaultSelector()
        self._sel.register(self._proc.stdout, selectors.EVENT_READ)

        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        self._notify("notifications/initialized", {})
        self.log("mcp: private backend up (%s)" % self.command[0])
        return self

    def close(self):
        """Shut the backend down. Never raises: this runs on the way out."""
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except (IOError, OSError, ValueError):
            pass
        try:
            self._proc.wait(timeout=10)
        except Exception:                             # noqa: BLE001
            try:
                self._proc.kill()
                self._proc.communicate()
            except Exception:                         # noqa: BLE001
                pass
        if self._sel is not None:
            try:
                self._sel.close()
            except Exception:                         # noqa: BLE001
                pass
            self._sel = None
        if self._stderr_file is not None:
            try:
                self._stderr_file.close()
            except Exception:                         # noqa: BLE001
                pass
            self._stderr_file = None
        self._proc = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # -- transport ------------------------------------------------------

    def _send(self, payload):
        if not self.alive:
            raise McpError("backend is not running")
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except (IOError, OSError, ValueError) as exc:
            raise McpError("backend stopped accepting input: %s" % exc)

    def _read_line(self, deadline):
        """
        One line from the server, or None at the deadline.

        Polled rather than blocking so a server that wedges cannot take the
        engine with it -- the loop must always be able to give up and let the
        files decide.
        """
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            if not self._sel.select(timeout=min(remaining, 1.0)):
                if not self.alive:
                    raise McpError("backend exited: %s" % self._stderr_tail())
                continue
            line = self._proc.stdout.readline()
            if line == "":
                raise McpError("backend closed its output: %s"
                               % self._stderr_tail())
            line = line.strip()
            if line:
                return line

    def _drain_stderr(self, stream):
        """Keep reading the server's stderr so its writes never block."""
        try:
            for line in iter(stream.readline, ""):
                self._stderr_lines.append(line.rstrip("\n"))
        except (IOError, OSError, ValueError):
            pass

    def _stderr_tail(self):
        try:
            if self.stderr_path and os.path.isfile(self.stderr_path):
                with open(self.stderr_path, "r", errors="replace") as fh:
                    fh.seek(max(0, os.path.getsize(self.stderr_path) - 4000))
                    return fh.read().strip()[-400:] or "(no stderr)"
            return "\n".join(self._stderr_lines).strip()[-400:] or "(no stderr)"
        except Exception:                             # noqa: BLE001
            return "(no stderr)"

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method, params, timeout=None):
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            self._send({"jsonrpc": "2.0", "id": request_id,
                        "method": method, "params": params})
            deadline = time.time() + (timeout or self.timeout)
            while True:
                line = self._read_line(deadline)
                if line is None:
                    raise McpError("%s timed out after %ss"
                                   % (method, timeout or self.timeout))
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue      # log noise on stdout; keep looking
                if msg.get("id") != request_id:
                    continue      # a notification, or a late reply
                if "error" in msg:
                    raise McpError("%s failed: %s"
                                   % (method, msg["error"].get("message")))
                return msg.get("result") or {}

    # -- tools ----------------------------------------------------------

    def call_tool(self, name, arguments=None, timeout=None):
        """Run a tool. Returns its text content; raises ToolError on failure."""
        result = self._request("tools/call",
                               {"name": name, "arguments": arguments or {}},
                               timeout=timeout)
        text = "\n".join(c.get("text", "")
                         for c in (result.get("content") or [])
                         if c.get("type") == "text").strip()
        if result.get("isError"):
            raise ToolError("%s: %s" % (name, text or "(no detail)"))
        return text

    def call_json(self, name, arguments=None, timeout=None):
        """
        Run a tool whose result is JSON.

        Falls back to the outermost object in the text: some tools prepend a
        human-readable line before the payload.
        """
        text = self.call_tool(name, arguments, timeout=timeout)
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            a, b = text.find("{"), text.rfind("}")
            if a >= 0 and b > a:
                try:
                    return json.loads(text[a:b + 1])
                except ValueError:
                    pass
        return {"_text": text}


class HttpMcpClient(McpClient):
    """
    The user's *shared* Vivado MCP server -- the one on 127.0.0.1:18090 that
    the Vivado AI extension's session tree shows -- spoken to over streamable
    HTTP. Opt-in only (backend.session.shared): sessions launched here are
    visible in the editor, at the price of depending on whoever holds the
    port. That owner can change under a run (an rc4 daemon from another
    client replaced the extension's own on 2026-10-08), so:

    * the URL is re-read from the port file at every (re)connect, never cached;
    * a request on a session the server no longer knows reconnects once and
      retries, so a new owner is picked up without failing the call;
    * the engine never starts a daemon itself: none listening is an outage,
      which the failover turns into bsub.
    """

    def __init__(self, url=None, url_file=None, timeout=300, log=None):
        self.url = url
        self.url_file = url_file or os.path.expanduser("~/.vivado-ai/mcp_url")
        self.timeout = timeout
        self.log = log or (lambda msg: None)
        self._session = None
        self._endpoint = None
        self._next_id = 0
        self._lock = threading.Lock()
        self.command = ["(shared server)"]

    @property
    def alive(self):
        return self._session is not None

    def endpoint(self):
        if self.url:
            return self.url
        try:
            with open(self.url_file) as fh:
                url = fh.read().strip()
        except (IOError, OSError):
            url = ""
        if not url:
            raise McpError("no shared Vivado MCP server: %s is missing (nothing to "
                           "launch through; start the editor's server, or drop "
                           "backend.session.shared)" % self.url_file)
        return url

    def _post(self, payload, timeout, session=True):
        import urllib.error
        import urllib.request
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL_VERSION}
        if session and self._session:
            headers["Mcp-Session-Id"] = self._session
        req = urllib.request.Request(self._endpoint, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return (resp.headers.get("Mcp-Session-Id"),
                        resp.headers.get("Content-Type") or "",
                        resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404) and session and self._session:
                raise _SessionLost("HTTP %d" % exc.code)
            raise McpError("shared server at %s: HTTP %d" % (self._endpoint, exc.code))
        except (urllib.error.URLError, OSError) as exc:
            raise McpError("shared server at %s unreachable: %s" % (self._endpoint, exc))

    @staticmethod
    def _reply(ctype, body, request_id):
        chunks = [body]
        if "text/event-stream" in ctype:
            chunks = [ln[5:].strip() for ln in body.splitlines() if ln.startswith("data:")]
        for chunk in chunks:
            try:
                msg = json.loads(chunk)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == request_id:
                return msg
        raise McpError("no reply to request %s" % request_id)

    def start(self):
        self._endpoint = self.endpoint()
        self._session = None
        sid, ctype, body = self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                       "params": {"protocolVersion": PROTOCOL_VERSION,
                                                  "capabilities": {},
                                                  "clientInfo": CLIENT_INFO}},
                                      timeout=60, session=False)
        msg = self._reply(ctype, body, 0)
        if "error" in msg:
            raise McpError("initialize failed: %s" % msg["error"].get("message"))
        self._session = sid or "-"
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                       timeout=30)
        except McpError:
            pass
        self.log("mcp: connected to the shared server at %s" % self._endpoint)
        return self

    def close(self):
        self._session = None

    def _notify(self, method, params):
        self._post({"jsonrpc": "2.0", "method": method, "params": params}, timeout=30)

    def _request(self, method, params, timeout=None):
        with self._lock:
            for attempt in (1, 2):
                if not self.alive:
                    self.start()
                self._next_id += 1
                rid = self._next_id
                try:
                    _, ctype, body = self._post({"jsonrpc": "2.0", "id": rid, "method": method,
                                                 "params": params},
                                                timeout=timeout or self.timeout)
                except _SessionLost:
                    # A new owner on the port, or a restart: reconnect once.
                    self.log("mcp: shared server lost our session; reconnecting")
                    self._session = None
                    if attempt == 2:
                        raise McpError("shared server keeps rejecting the session")
                    continue
                msg = self._reply(ctype, body, rid)
                if "error" in msg:
                    raise McpError("%s failed: %s" % (method, msg["error"].get("message")))
                return msg.get("result") or {}


class _SessionLost(McpError):
    pass
