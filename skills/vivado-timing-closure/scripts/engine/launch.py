#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
launch.py -- choose how a design's runs are launched, once, and say why.

`backend.default: auto` means: never install or configure a Vivado MCP
server for the flow. Use one the user already has -- from an editor, a CLI's
MCP config, or the Vivado AI extension -- if, and only if, it can launch runs
on LSF; otherwise submit with `bsub` directly; with no LSF at all, run here.

    a capable MCP server   ->  session  (a private --stdio copy of it)
    LSF reachable          ->  lsf      (bsub)
    otherwise              ->  local

"Capable" is measured, not assumed (mcp_discover.resolve): the newest
customer build -- the one the Vivado AI extension ships -- has no vivado_lsf
tool at all, while an older customer build and the internal ones do.

The choice is written to `<output_dir>/backend.json` and reused by every later
supervisor start for that design, so a restart never silently moves a design
to another launch path. The one change allowed mid-run is the failover in
backends/failover.py, which is recorded in the same file.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import datetime
import glob
import os
import re
import shutil

from . import config, jsonio, mcp_discover

RECORD = "backend.json"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lsf_available(cfg):
    """bsub reachable: on PATH, or through the configured LSF env script."""
    script = config.get(cfg, "backend", "lsf", "env_script")
    if script and os.path.isfile(script):
        return True
    return shutil.which("bsub") is not None


# .../amd.vivado-ai-extension-2026.9.1/bin/vivado-mcp-server-linux-amd64-2026.9.1
_EXT_BINARY = re.compile(r"^(?P<ext>.*vivado-ai-extension)-(?P<ver>[0-9][^/]*)/bin/"
                         r"(?P<name>vivado-mcp-server-[a-z]+-[a-z0-9_]+)-(?P=ver)$")


def _version_key(ver):
    return [int(x) if x.isdigit() else x for x in re.split(r"[.\-]", ver)]


def current_extension_binary(path):
    """
    For a pinned server inside a versioned editor-extension folder that no
    longer exists: the same server in the newest installed version of that
    extension, or None. An editor removes the old folder when the extension is
    upgraded, so a pin there breaks on every upgrade (2026-10-15: every new run
    of a closure flow failed to start after 2026.9.1 was replaced by 2026.9.2).
    """
    if not path or os.path.isfile(path):
        return None
    m = _EXT_BINARY.match(path)
    if not m:
        return None
    found = []
    for d in glob.glob("%s-*" % m.group("ext")):
        ver = os.path.basename(d)[len(os.path.basename(m.group("ext"))) + 1:]
        cand = os.path.join(d, "bin", "%s-%s" % (m.group("name"), ver))
        if re.match(r"^[0-9]", ver) and os.path.isfile(cand) and os.access(cand, os.X_OK):
            found.append((_version_key(ver), cand))
    return max(found)[1] if found else None


def _configured_candidate(cfg):
    """backend.session.mcp_command, when the site pinned one, as a Candidate."""
    raw = config.get(cfg, "backend", "session", "mcp_command")
    if not raw:
        return None
    from .backends import session
    argv = session.normalize_command(raw)
    argv = [a for a in argv if a != session.STDIO_FLAG]
    moved = current_extension_binary(argv[0]) if argv else None
    if moved:
        argv = [moved] + argv[1:]
    env = dict(config.get(cfg, "backend", "session", "env", default={}) or {})
    return mcp_discover.Candidate("backend.session.mcp_command", "configured",
                                  argv[0], argv[1:], env)


def resolve(cfg, start_dir=None, probe=None, discover=None, lsf_ok=None):
    """
    The launch record for a design: {"backend", "reason", "mcp", "rejected",
    "resolved_utc", "failover"}. Probes servers privately; never raises.
    """
    probe = probe or mcp_discover.probe
    rejected = []
    # A server pinned in the site config is the only one considered: the site
    # chose it, and falling back to some other discovered build would hide
    # that the chosen one cannot launch.
    pinned = _configured_candidate(cfg)
    if pinned is not None:
        found, report = mcp_discover.resolve(None, probe_fn=probe, candidates=[pinned])
    else:
        found, report = (discover or mcp_discover.resolve)(start_dir, probe_fn=probe)
    for r in report:
        if not r.get("ok"):
            rejected.append({"source": r.get("source"), "binary": r.get("binary"),
                             "version": r.get("version"), "channel": r.get("channel"),
                             "why": r.get("why")})
    rec = {"resolved_utc": _now(), "rejected": rejected, "failover": None, "mcp": None}
    if found is not None:
        ok = next(r for r in report if r.get("ok"))
        rec.update(backend="session",
                   reason="MCP %s (%s build) from %s: %s" % (
                       ok.get("version") or os.path.basename(found.binary),
                       ok.get("channel") or "unknown", found.source, ok.get("why")),
                   mcp={"binary": found.binary, "args": list(found.args),
                        "env": dict(found.env), "source": found.source,
                        "version": ok.get("version"), "commit": ok.get("commit"),
                        "channel": ok.get("channel")})
        return rec
    why = ("no Vivado MCP server installed" if not report else
           "no installed Vivado MCP server can launch on LSF (%s)"
           % "; ".join("%s: %s" % (os.path.basename(r.get("binary") or "?"), r.get("why"))
                       for r in report))
    if (lsf_ok if lsf_ok is not None else lsf_available(cfg)):
        rec.update(backend="lsf", reason="bsub: %s" % why)
    else:
        rec.update(backend="local", reason="this host: %s, and LSF is not reachable" % why)
    return rec


def load(output_dir):
    if not output_dir:
        return None
    rec = jsonio.read(os.path.join(output_dir, RECORD))
    return rec if isinstance(rec, dict) and rec.get("backend") else None


def save(output_dir, rec):
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    jsonio.write(os.path.join(output_dir, RECORD), rec)


def for_design(cfg, output_dir, start_dir=None, **kw):
    """The record for this design: the saved one, or a fresh resolution saved."""
    rec = load(output_dir)
    gone = _binary_gone(rec)
    if rec is None or gone:
        old = rec
        rec = resolve(cfg, start_dir=start_dir or output_dir, **kw)
        if old is not None:
            # Same choice as before, only rebound to a server that exists;
            # a failover already recorded stays in force.
            rec["failover"] = old.get("failover")
            rec["rebound"] = {"from": gone, "at_utc": _now()}
        if output_dir:
            save(output_dir, rec)
    return rec


def _binary_gone(rec):
    """The recorded MCP server binary, when it no longer exists on disk."""
    m = (rec or {}).get("mcp") or {}
    b = m.get("binary") if rec and rec.get("backend") == "session" else None
    return b if b and os.path.isabs(b) and not os.path.isfile(b) else None


def backend_block(cfg, rec):
    """The `backend:` config block to build the chosen backend from."""
    block = dict(cfg.get("backend") or {})
    if rec and rec.get("backend") == "session" and rec.get("mcp"):
        m = rec["mcp"]
        sess = dict(block.get("session") or {})
        sess["mcp_command"] = [m["binary"]] + list(m.get("args") or [])
        env = dict(m.get("env") or {})
        env.update(sess.get("env") or {})
        sess["env"] = env
        block["session"] = sess
    return block


def record_failover(output_dir, reason):
    """Note, in the design's record, that new runs now go through bsub."""
    rec = load(output_dir) or {}
    rec["failover"] = {"to": "lsf", "at_utc": _now(), "reason": reason}
    save(output_dir, rec)
