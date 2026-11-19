#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
control.py -- the run's web page: one server per user and port, and its link.

`tc web start|status|stop|url` and the run start (tc close, the supervisor)
come through here. The server itself is server.py, started detached.

Access is a secret link, not a sign-in: a random token, made once, that every
request must carry (?k=<token> on the first one, then a cookie the server sets).
It is what lets a page be opened from any machine on the network with nothing
to type, while a guessed URL shows nothing.

State lives in ~/.tc/web/ (TC_WEB_HOME overrides it), mode 0700:
  token                 the secret, 0600, created once
  registry.json         the workspaces (and extra workflow folders) the server
                        shows; the server re-reads it on every request, so a
                        run started after the server shows at once
  server-<port>.json    the running server: pid, port, bind, host, started
  server-<port>.log     its log

It starts with each run by default (engine.yaml `web.enabled`, default true),
so a run's link is there without anyone asking for it. Under CI it stays off
whatever the config says: a test runner must not leave a server listening on
every interface. TC_WEB=on|off overrides both.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import datetime
import errno
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time

try:
    from urllib.parse import urlencode
except ImportError:                                  # pragma: no cover
    from urllib import urlencode

try:
    import fcntl
except ImportError:                                  # pragma: no cover - Windows
    fcntl = None

from .. import paths
from . import views

DEFAULT_PORT = 8112
DEFAULT_BIND = "0.0.0.0"
SERVER_MODULE = "engine.web.server"
WEB_FILE = "web.json"           # <output_dir>/web.json: the run's link, 0600


class WebError(Exception):
    pass


def _now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def as_bool(v):
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def in_ci(env=None):
    """True under a CI runner (GitHub Actions, GitLab, Jenkins, Azure, or anything setting CI)."""
    env = os.environ if env is None else env
    return (any(as_bool(env.get(k)) for k in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "TF_BUILD"))
            or bool(env.get("JENKINS_URL")))


def settings(cfg, env=None):
    """
    The engine config's web block, with its defaults. enabled is TC_WEB=on|off
    when set, else off under CI, else the config's (default on).
    """
    env = os.environ if env is None else env
    w = dict((cfg or {}).get("web") or {})
    try:
        port = int(w.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    override = str(env.get("TC_WEB") or "").strip().lower()
    if override in ("on", "off", "1", "0", "true", "false", "yes", "no"):
        enabled, why = as_bool(override), "TC_WEB"
    elif in_ci(env):
        enabled, why = False, "ci"
    else:
        enabled, why = as_bool(w.get("enabled", True)), "config"
    return {"enabled": enabled, "enabled_by": why, "port": port,
            "bind": str(w.get("bind") or DEFAULT_BIND), "host": w.get("host") or None,
            "workflow_dirs": [str(d) for d in (w.get("workflow_dirs") or []) if d]}


# ------------------------------------------------------------------ the state folder
def home():
    """~/.tc/web (or $TC_WEB_HOME), created 0700; an existing one is tightened to 0700."""
    h = os.path.abspath(os.path.expanduser(os.environ.get("TC_WEB_HOME") or "~/.tc/web"))
    if not os.path.isdir(h):
        os.makedirs(h, 0o700)
    os.chmod(h, 0o700)
    return h


def token():
    """The secret every request needs: made once (urlsafe, 32 bytes), kept 0600."""
    path = os.path.join(home(), "token")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
    else:
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(32) + "\n")
    os.chmod(path, 0o600)
    with open(path) as fh:
        value = fh.read().strip()
    if len(value) < 32:
        raise WebError("%s holds no usable token; remove it to make a new one" % path)
    return value


def _locked(fn):
    """Run fn() holding an exclusive lock on the registry (best effort without fcntl)."""
    lock = os.path.join(home(), "registry.lock")
    with open(lock, "a") as fh:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_UN)


def registry_path():
    return os.path.join(home(), "registry.json")


def read_registry():
    try:
        with open(registry_path()) as fh:
            data = json.load(fh)
    except (IOError, OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"workspaces": [str(w) for w in data.get("workspaces") or [] if w],
            "workflow_dirs": [str(w) for w in data.get("workflow_dirs") or [] if w]}


def _write_json(path, data, mode=0o600):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def register(workspace=None, workflow_dirs=()):
    """Add a workspace (and workflow folders) to what the server shows. Returns the registry."""
    def update():
        reg = read_registry()
        if workspace:
            ws = os.path.realpath(os.path.expanduser(workspace))
            if not os.path.isdir(ws):
                raise WebError("no folder %s" % ws)
            if ws not in reg["workspaces"]:
                reg["workspaces"].append(ws)
        for d in workflow_dirs or ():
            d = os.path.realpath(os.path.expanduser(d))
            if os.path.isfile(os.path.join(d, views.MANIFEST)) and d not in reg["workflow_dirs"]:
                reg["workflow_dirs"].append(d)
        _write_json(registry_path(), reg)
        return reg
    return _locked(update)


# ------------------------------------------------------------------ this skill's own workflow
def own_workflow():
    """
    (folder holding workflow.json, launcher or None) for the skill this engine
    belongs to. Packaged, the manifest and its `tc` sit at the skill's top,
    above scripts/engine; in a development checkout the manifest is
    skills/timing-closure/workflow.json and the launcher is the repo's tc.
    """
    packaged = os.path.dirname(paths.REPO_ROOT)
    if os.path.isfile(os.path.join(packaged, views.MANIFEST)):
        return packaged, None
    dev = os.path.join(paths.REPO_ROOT, "skills", "timing-closure")
    if os.path.isfile(os.path.join(dev, views.MANIFEST)):
        return dev, os.path.join(paths.REPO_ROOT, "tc")
    raise WebError("no workflow.json found for this skill (looked in %s and %s)" % (packaged, dev))


def source(timeout=60.0):
    """What the server shows: this skill's workflow plus the registry's, for its workspaces."""
    own_dir, own_entry = own_workflow()
    entries = {os.path.realpath(own_dir): own_entry} if own_entry else {}

    def dirs():
        out = [own_dir]
        for d in read_registry()["workflow_dirs"]:
            if os.path.realpath(d) != os.path.realpath(own_dir):
                out.append(d)
        return out
    return views.Source(dirs, lambda: read_registry()["workspaces"], entries=entries,
                        timeout=timeout, client="tc web")


# ------------------------------------------------------------------ the server process
def state_path(port):
    return os.path.join(home(), "server-%d.json" % int(port))


def log_path(port):
    return os.path.join(home(), "server-%d.log" % int(port))


def _ours(pid, port):
    """True when pid is alive and is our server on this port (not a reused pid)."""
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    try:
        with open("/proc/%d/cmdline" % int(pid), "rb") as fh:
            argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
    except (IOError, OSError):
        return True                    # no /proc: trust the live pid
    return SERVER_MODULE in argv and "--port" in argv and str(int(port)) in argv


def _answers(port, bind):
    host = "127.0.0.1" if bind in ("0.0.0.0", "", "::") else bind
    try:
        with socket.create_connection((host, int(port)), timeout=1.5):
            return True
    except OSError:
        return False


def link_host(host=None):
    return host or socket.getfqdn()


def base_url(port, host=None):
    return "http://%s:%d" % (link_host(host), int(port))


def status(port=DEFAULT_PORT):
    """{running, pid, port, bind, host, started, url, log, workspaces}."""
    try:
        with open(state_path(port)) as fh:
            st = json.load(fh)
    except (IOError, OSError, ValueError):
        st = {}
    running = bool(st.get("pid")) and _ours(st["pid"], port)
    out = {"running": running, "port": int(port), "pid": st.get("pid") if running else None,
           "bind": st.get("bind"), "host": st.get("host"), "started": st.get("started") if running else None,
           "log": log_path(port), "workspaces": read_registry()["workspaces"]}
    if running:
        out["url"] = list_url(port, st.get("host"))
    return out


def start(port=DEFAULT_PORT, bind=DEFAULT_BIND, host=None, wait_s=15.0):
    """The server's status, starting it detached first when it is not running."""
    st = status(port)
    if st["running"]:
        return dict(st, started_now=False)
    token()                            # made now, never inside the server's first request
    log = open(log_path(port), "ab")
    argv = [sys.executable, "-m", SERVER_MODULE, "--port", str(int(port)), "--bind", bind,
            "--home", home()]
    if host:
        argv += ["--host", host]
    try:
        proc = subprocess.Popen(argv, cwd=paths.REPO_ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, close_fds=True, start_new_session=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    finally:
        log.close()
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if proc.poll() is not None:
            raise WebError("the web server exited (rc=%s): %s" % (proc.returncode, _log_tail(port)))
        if _answers(port, bind) and status(port)["running"]:
            return dict(status(port), started_now=True)
        time.sleep(0.2)
    raise WebError("the web server did not answer on port %d within %ds: %s"
                   % (port, wait_s, _log_tail(port)))


def _log_tail(port, n=600):
    try:
        with open(log_path(port), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - n))
            return fh.read().decode("utf-8", "replace").strip()
    except (IOError, OSError):
        return ""


def stop(port=DEFAULT_PORT, wait_s=10.0):
    """Stop our own server on this port: only the recorded pid, after checking it is ours."""
    st = status(port)
    if not st["running"]:
        return {"stopped": None, "port": int(port), "note": "not running"}
    pid = int(st["pid"])
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + wait_s
    while time.time() < deadline and _ours(pid, port):
        time.sleep(0.1)
    exited = not _ours(pid, port)
    if exited:
        try:
            os.unlink(state_path(port))
        except OSError:
            pass
    return {"stopped": pid, "port": int(port), "exited": exited}


# ------------------------------------------------------------------ links
def list_url(port=DEFAULT_PORT, host=None):
    return "%s/wf/?%s" % (base_url(port, host), urlencode({"k": token()}))


def run_url(output_dir, port=DEFAULT_PORT, host=None):
    """The secret link to one run's page; the run must be in a registered workspace."""
    out = os.path.realpath(output_dir)
    src = source()
    ws = src.workspace_of(out)
    if ws is None:
        raise WebError("%s is in no registered workspace (tc web start --register <workspace>)" % out)
    wf = views.load(*own_workflow())
    if not (wf.detail_action and wf.detail_arg):
        raise WebError("workflow %s declares no detail view" % wf.id)
    q = [("workflow", wf.id), ("workspace", ws), (wf.detail_arg, out), ("k", token())]
    return "%s/wf/run?%s" % (base_url(port, host), urlencode(q))


def run_workspace(output_dir):
    """The workspace a run belongs to: where its .tc/engine.yaml is, else its parent folder."""
    from .. import config
    cfgfile = config.workspace_config(start=output_dir)
    if cfgfile:
        return os.path.dirname(os.path.dirname(cfgfile))
    return os.path.dirname(os.path.realpath(output_dir))


def read_run_url(output_dir):
    """The link recorded for a run (web.json), or None."""
    try:
        with open(os.path.join(output_dir, WEB_FILE)) as fh:
            return (json.load(fh) or {}).get("web_url")
    except (IOError, OSError, ValueError, AttributeError):
        return None


def ensure_for_run(cfg, output_dir, log=None, env=None):
    """
    When web is enabled: register the run's workspace, make sure the server is
    up, record the link in <output_dir>/web.json (0600) and return it. Never
    raises: a page that cannot start must not stop a run (the reason is logged).
    """
    w = settings(cfg, env)
    if not w["enabled"]:
        return None
    try:
        register(run_workspace(output_dir), w["workflow_dirs"])
        start(w["port"], w["bind"], w["host"])
        url = run_url(output_dir, w["port"], w["host"])
        if os.path.isdir(output_dir):
            _write_json(os.path.join(output_dir, WEB_FILE), {"web_url": url, "updated": _now()})
        return url
    except Exception as exc:                          # noqa: BLE001 - never fail the run
        if log:
            log("tc web: the run's web page is not available: %s" % exc)
        return None
