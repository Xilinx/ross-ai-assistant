#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
server.py -- the run pages, from the standard library alone.

    python3 -m engine.web.server --port 8112 [--bind 0.0.0.0] [--home ~/.tc/web] [--host <fqdn>]

Started detached by `tc web start` or a run start (control.py); it is the same
page the Vivado AI extension's Workflows view shows, drawn by that view's own
renderer (static/workflow_view.js) from each workflow's own view documents
(views.py), so it ships with the skill and needs no Django.

    /wf/                      every workflow x registered workspace, as cards
    /wf/run?workflow=&workspace=&<detail_arg>=   one run, refreshed as the workflow asks
    /wf/file?...&path=        a text file the run's page links to
    /wf/static/<file>         the renderer, its CSS, theme.css, wf.js
    /api/wf/list/  /api/wf/view/  /api/wf/tail/   the same as JSON
    /                         -> /wf/

Access: every request carries the secret (control.token()): ?k=<token>, or the
cookie the server sets when a request carries a valid k (HttpOnly,
SameSite=Strict, so another site cannot ride it). No sign-in, no password on
the wire. A missing or wrong secret gets a bare 403 that says nothing else.
GET only; nothing here writes anything but this server's own log and state.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import argparse
import datetime
import hmac
import json
import os
import signal
import sys

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, urlencode, urlsplit
except ImportError:                                  # pragma: no cover
    raise SystemExit("engine.web.server needs Python 3")

if __package__ in (None, ""):                         # run as a file: make `engine` importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.web import control, page, views          # noqa: E402

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
FORBIDDEN = b"403 Forbidden\n"


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "tc-web"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), fmt % args))

    def log_request(self, code="-", size="-"):
        # The path only: the query may carry the secret.
        self.log_message("%s %s %s", self.command, urlsplit(self.path).path, code)

    def _send(self, code, body, ctype, extra=()):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, data):
        self._send(code, json.dumps(data), "application/json; charset=utf-8", self._cookie)

    def _refuse(self, code=405):
        self._send(code, b"%d\n" % code, "text/plain; charset=utf-8", [("Allow", "GET")])

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = lambda self: self._refuse(405)

    def do_HEAD(self):
        self._refuse(405)

    # ------------------------------------------------------------ access
    def _authorised(self, q):
        """True when the request carries the secret; sets self._cookie when it came as ?k=."""
        secret = self.server.secret
        self._cookie = []
        given = (q.get("k") or [""])[0]
        if given and hmac.compare_digest(given.encode(), secret.encode()):
            self._cookie = [("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Strict"
                             % (self.server.cookie_name, secret))]
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.server.cookie_name and hmac.compare_digest(value.encode(), secret.encode()):
                return True
        return False

    # ------------------------------------------------------------ routes
    def do_GET(self):
        url = urlsplit(self.path)
        q = parse_qs(url.query, keep_blank_values=True)
        if not self._authorised(q):
            return self._send(403, FORBIDDEN, "text/plain; charset=utf-8")
        p = url.path
        try:
            if p in ("/", ""):
                # Keep the query: a link opened from another site carries its secret
                # only in the URL (a SameSite=Strict cookie is not sent across that hop).
                return self._send(302, b"", "text/plain", self._cookie + [("Location", "/wf/" + (
                    "?" + url.query if url.query else ""))])
            if p.startswith("/wf/static/"):
                hit = page.static_file(p[len("/wf/static/"):])
                if hit is None:
                    return self._send(404, b"404\n", "text/plain; charset=utf-8")
                return self._send(200, hit[0], hit[1], self._cookie)
            route = {"/wf/": self.page_list, "/wf/run": self.page_run, "/wf/file": self.page_file,
                     "/api/wf/list/": self.api_list, "/api/wf/view/": self.api_view,
                     "/api/wf/tail/": self.api_tail}.get(p)
            if route is None:
                return self._send(404, b"404\n", "text/plain; charset=utf-8")
            return route(q)
        except Exception as exc:                      # noqa: BLE001 - one bad request, not the server
            self.log_message("error %s: %s", p, exc)
            return self._send(500, b"500\n", "text/plain; charset=utf-8")

    def _target(self, q):
        """(workflow, workspace, detail value), or KeyError naming what is not allowed."""
        src = self.server.source
        try:
            wf = src.find((q.get("workflow") or [""])[0])
        except KeyError:
            raise KeyError("no such workflow")
        ws = src.known_workspace((q.get("workspace") or [""])[0])
        if ws is None:
            raise KeyError("not a registered workspace")
        if not (wf.detail_action and wf.detail_arg):
            raise KeyError("this workflow has no detail view")
        try:
            value = src.detail_value(wf, ws, (q.get(wf.detail_arg) or [""])[0])
        except (KeyError, views.WorkflowError):
            raise KeyError("not a run of this workflow in this workspace")
        return wf, ws, value

    def page_list(self, q):
        html = page.shell("Workflow runs", "list", {"workflow": (q.get("workflow") or [""])[0]}, "")
        return self._send(200, html, "text/html; charset=utf-8", self._cookie)

    def page_run(self, q):
        try:
            wf, ws, value = self._target(q)
        except KeyError as exc:
            return self._send(404, "404: %s\n" % exc.args[0], "text/plain; charset=utf-8")
        html = page.shell("%s run" % wf.title, "run",
                          {"workflow": wf.id, "workflow-title": wf.title, "workspace": ws,
                           "arg": wf.detail_arg, "value": value, "refresh": wf.refresh_seconds}, "")
        return self._send(200, html, "text/html; charset=utf-8", self._cookie)

    def page_file(self, q):
        try:
            wf, ws, value = self._target(q)
            real = self.server.source.readable(wf, ws, value, (q.get("path") or [""])[0])
            text = views.head_or_tail(real)
        except (KeyError, OSError, views.WorkflowError):
            return self._send(404, b"404: not a text file inside this run's workspace or paths\n",
                              "text/plain; charset=utf-8")
        return self._send(200, text, "text/plain; charset=utf-8", self._cookie)

    def api_list(self, q):
        src = self.server.source
        only = (q.get("workflow") or [""])[0]
        found, errors = src.workflows()
        out = []
        for wf in found:
            if only and wf.id != only:
                continue
            spaces = []
            for ws in src.workspaces():
                try:
                    spaces.append({"workspace": ws, "ok": True, "doc": src.list_doc(wf, ws)})
                except views.WorkflowError as exc:
                    spaces.append({"workspace": ws, "ok": False, "error": str(exc)})
            out.append(dict(wf.to_json(), workspaces=spaces))
        return self._json(200, {"ok": True, "workflows": out, "errors": errors, "workspaces": src.workspaces()})

    def api_view(self, q):
        try:
            wf, ws, value = self._target(q)
        except KeyError as exc:
            return self._json(404, {"ok": False, "error": exc.args[0]})
        try:
            doc = self.server.source.detail_doc(wf, ws, value)
        except views.WorkflowError as exc:
            return self._json(502, {"ok": False, "error": str(exc)})
        return self._json(200, {"ok": True, "doc": doc, "workflow": wf.to_json(), "workspace": ws, "value": value})

    def api_tail(self, q):
        try:
            wf, ws, value = self._target(q)
            real = self.server.source.readable(wf, ws, value, (q.get("path") or [""])[0])
            text = views.tail(real)
        except (KeyError, OSError, views.WorkflowError):
            return self._json(404, {"ok": False, "error": "not a text file inside this run's workspace or paths"})
        return self._json(200, {"ok": True, "path": real, "text": text})


def make_server(port, bind, source, secret):
    srv = _Server((bind, int(port)), Handler)
    srv.source = source
    srv.secret = secret
    srv.cookie_name = "tcweb_%d" % int(port)
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description="the run pages (see engine/web/control.py)")
    ap.add_argument("--port", type=int, default=control.DEFAULT_PORT)
    ap.add_argument("--bind", default=control.DEFAULT_BIND)
    ap.add_argument("--host", help="the host name the links use (default: this host's FQDN)")
    ap.add_argument("--home", help="state folder (default ~/.tc/web or $TC_WEB_HOME)")
    a = ap.parse_args(argv)
    if a.home:
        os.environ["TC_WEB_HOME"] = a.home
    srv = make_server(a.port, a.bind, control.source(), control.token())
    control._write_json(control.state_path(a.port), {
        "pid": os.getpid(), "port": a.port, "bind": a.bind, "host": a.host,
        "started": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"})
    def _term(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, _term)
    sys.stderr.write("tc web: serving %s on %s:%d\n" % (control.base_url(a.port, a.host), a.bind, a.port))
    sys.stderr.flush()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        try:
            with open(control.state_path(a.port)) as fh:
                if (json.load(fh) or {}).get("pid") == os.getpid():
                    os.unlink(control.state_path(a.port))
        except (IOError, OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
