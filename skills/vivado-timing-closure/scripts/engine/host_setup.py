#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
host_setup.py -- write a workspace's `.tc/engine.yaml` from what the host knows.

When Vivado MCP runs this skill as a workflow (`vivado_workflow`, action
`setup`), it hands over a context file: the Vivado build, the LSF settings and
its own executable, as configured for the user's editor. Those are exactly the
site settings `.tc/engine.yaml` holds, and asking the user for them a second
time is how the first runs from an editor went wrong (a `vivado` not on PATH
failed seven jobs in a row). So setup copies them, and nothing is hard-coded:
the backend command is the very server that called us.

What it decides:

- **Backend.** `session` -- runs launched through a private copy of the
  calling server's `vivado_lsf` -- when that server has LSF and this host can
  reach it; else `local`.
- **Resources.** Queue, memory, slots and OS from the server's LSF defaults.
  Memory follows the server's rule: a bare number is GB.

An existing file is never replaced without `force`; the reply then carries
what would change, so the agent can show it and ask.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import json
import os
import re
import shutil

from . import config

CONTEXT_SCHEMA = 1

_MEM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*$", re.I)
_MEM_MB = {"": 1024, "k": 1.0 / 1024, "m": 1, "g": 1024, "t": 1024 * 1024}


class ContextError(Exception):
    pass


def memory_mb(text):
    """'16GB' -> 16384, '8192m' -> 8192, '8' -> 8192 (GB, as Vivado MCP reads it)."""
    if text is None or str(text).strip() == "":
        return None
    m = _MEM_RE.match(str(text))
    if not m:
        return None
    mb = float(m.group(1)) * _MEM_MB[m.group(2).lower()]
    return int(mb) if mb >= 1 else None


def _int(text):
    try:
        n = int(str(text).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def read_context(path):
    try:
        with open(path) as fh:
            ctx = json.load(fh)
    except (IOError, OSError, ValueError) as exc:
        raise ContextError("cannot read the host context %s: %s" % (path, exc))
    if not isinstance(ctx, dict) or ctx.get("schema_version") != CONTEXT_SCHEMA:
        raise ContextError("host context %s is not schema_version %d"
                           % (path, CONTEXT_SCHEMA))
    return ctx


def _lsf_reachable(lsf):
    """bsub on PATH, or an LSF env script / bin dir the server was given."""
    if shutil.which("bsub"):
        return True
    script = lsf.get("env_script")
    if script and os.path.isfile(script):
        return True
    bindir = lsf.get("bin_path")
    return bool(bindir and os.path.isfile(os.path.join(bindir, "bsub")))


def plan(ctx, lsf_reachable=None):
    """
    The settings to write, as a nested dict, plus notes for the user.

    Pure apart from the LSF probe, which tests pass in.
    """
    viv = ctx.get("vivado") or {}
    lsf = ctx.get("lsf") or {}
    server = (ctx.get("mcp_server") or {}).get("command")
    reachable = _lsf_reachable(lsf) if lsf_reachable is None else lsf_reachable
    notes = []

    backend = {}
    vivado = viv.get("path")
    if vivado:
        backend["vivado"] = vivado
        backend["vivado_path"] = vivado
    else:
        notes.append("Vivado MCP has no Vivado path for this editor. Ask the user which "
                     "Vivado to use; never take `vivado` from PATH.")

    use_session = bool(lsf.get("enabled") and server and reachable)
    if use_session:
        backend["default"] = "session"
        session = {"mcp_command": server}
        if lsf.get("queue"):
            session["queue"] = lsf["queue"]
        mem = memory_mb(lsf.get("default_memory"))
        if mem:
            session["memory_mb"] = mem
        slots = _int(lsf.get("default_slots"))
        if slots:
            session["slots"] = slots
        if lsf.get("os_type"):
            session["os_type"] = lsf["os_type"]
        env = {}
        for key, name in (("env_script", "VIVADO_LSF_ENV_SCRIPT"),
                          ("bin_path", "VIVADO_LSF_BIN_PATH"),
                          ("submit_mode", "VIVADO_LSF_SUBMIT_MODE"),
                          ("resource_requirement", "VIVADO_LSF_RESOURCE_REQUIREMENT"),
                          ("extra_submit_args", "VIVADO_LSF_EXTRA_SUBMIT_ARGS")):
            if lsf.get(key):
                env[name] = lsf[key]
        if env:
            session["env"] = env
        backend["session"] = session
        # bsub settings, for `tc query` and for failover when the private
        # server cannot start. The OS term is a site resource (ostype/osver),
        # which Vivado MCP resolves per cluster and bsub cannot: left out.
        direct = {}
        if lsf.get("env_script"):
            direct["env_script"] = lsf["env_script"]
        if lsf.get("queue"):
            direct["queue"] = lsf["queue"]
        if mem:
            direct["memory_mb"] = mem
        if direct:
            backend["lsf"] = direct
        # What the editor's settings leave to chance. Said, never filled in:
        # the queue and OS are the user's farm, not ours to guess.
        if not lsf.get("queue"):
            notes.append("Vivado MCP has no LSF queue for this editor; it will pick one itself, "
                         "often an interactive queue. Ask the user which queue to use (setting "
                         "vivadoAI.lsfQueue, or backend.session.queue).")
        if not lsf.get("os_type"):
            notes.append("Vivado MCP has no OS type for this editor, so jobs may land on hosts "
                         "that cannot run this Vivado (GLIBC errors). Ask the user for the "
                         "farm's OS type (vivadoAI.lsfOsType, or backend.session.os_type).")
        if re.search(r"(^|\s)-m(\s|$)", lsf.get("extra_submit_args") or ""):
            notes.append("The extra submit arguments pin hosts (-m ...); a queue that does not "
                         "use those hosts refuses every job. Confirm them with the user "
                         "(vivadoAI.lsfExtraSubmitArgs).")
        if lsf.get("os_type"):
            notes.append("Runs go through Vivado MCP, which selects OS %r itself. `tc query` "
                         "and the bsub fallback do not: if this farm needs an OS select, set "
                         "backend.lsf.os_type to its term (for example osver==%s)."
                         % (lsf["os_type"], lsf["os_type"]))
    else:
        backend["default"] = "local"
        if not lsf.get("enabled"):
            why = "LSF is turned off in Vivado MCP"
        elif not server:
            why = "Vivado MCP did not report its own executable"
        else:
            why = "bsub is not reachable from this host"
        notes.append("Runs will execute on this host, one at a time (%s). A timing-closure "
                     "sweep is several multi-hour runs; with a farm it runs them in "
                     "parallel." % why)

    settings = {"backend": backend}
    summary = {
        "backend": backend["default"],
        "vivado": vivado,
        "vivado_version": viv.get("version"),
        "mcp_command": server if use_session else None,
        "queue": (backend.get("session") or {}).get("queue"),
        "memory_mb": (backend.get("session") or {}).get("memory_mb"),
        "client": (ctx.get("client") or {}).get("type"),
    }
    return settings, summary, notes


# ---------------------------------------------------------------------------
# YAML out (the subset yaml_lite reads back)
# ---------------------------------------------------------------------------
_PLAIN = re.compile(r"^[A-Za-z0-9_./=+:@-]+$")


def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if _PLAIN.match(s) and s.lower() not in ("true", "false", "null", "yes", "no"):
        return s
    return json.dumps(s)


def _emit(d, indent, out):
    for k, v in d.items():
        if isinstance(v, dict):
            out.append("%s%s:" % (" " * indent, k))
            _emit(v, indent + 2, out)
        else:
            out.append("%s%s: %s" % (" " * indent, k, _scalar(v)))


def render(settings, ctx):
    head = [
        "# Site settings for this workspace, read by `tc` from any subdirectory.",
        "# Written by `tc setup` from Vivado MCP's host context (client %s, server %s)."
        % ((ctx.get("client") or {}).get("type") or "unknown",
           (ctx.get("mcp_server") or {}).get("version") or "unknown"),
        "# Edit freely; `tc setup --force` rewrites it.",
    ]
    body = []
    _emit(settings, 0, body)
    return "\n".join(head + body) + "\n"


def _flatten(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        key = "%s.%s" % (prefix, k) if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def diff(old, new):
    """{key: [old, new]} for every setting that differs, dotted keys."""
    a, b = _flatten(old), _flatten(new)
    return {k: [a.get(k), b.get(k)] for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}


# ---------------------------------------------------------------------------
# Which Vivado: never a guess
# ---------------------------------------------------------------------------
# The order is: the path the user gave (or the editor's / server's configured
# one), which Vivado MCP puts in the host context; else the absolute path this
# workspace's config already names. With neither, the step stops and says so
# (NEEDS_EXIT with NEEDS_VIVADO on stdout) and Vivado MCP asks the user. The
# default `vivado` is a PATH lookup, which is a guess, so it does not count.
NEEDS_EXIT = 3
NEEDS_VIVADO = {"needs": "vivado_path", "question": "Which Vivado should timing closure use?"}


class NeedsVivado(Exception):
    pass


def resolve_vivado(raw):
    """
    The vivado executable for what a user typed: the executable itself, or an
    install folder holding bin/vivado (Vivado MCP resolves it the same way).
    Raises ValueError saying what is wrong.
    """
    p = os.path.expanduser((raw or "").strip())
    if not p:
        raise ValueError("empty Vivado path")
    if not os.path.isabs(p):
        raise ValueError("%r is not an absolute path" % raw)
    candidates = [p]
    if os.path.isdir(p):
        candidates = [os.path.join(p, "bin", "vivado"), os.path.join(p, "Vivado", "bin", "vivado"),
                      os.path.join(p, "vivado")]
    for c in candidates:
        if os.path.isfile(c):
            if not os.access(c, os.X_OK):
                raise ValueError("%s is not executable" % c)
            return os.path.normpath(c)
    if os.path.isdir(p):
        raise ValueError("no bin/vivado under %s" % p)
    raise ValueError("%s does not exist" % p)


def configured_vivado(cfg):
    """The Vivado the config names explicitly (an absolute path), else None."""
    for key in ("vivado_path", "vivado"):
        v = config.get(cfg, "backend", key)
        if v and os.path.isabs(str(v)):
            return str(v)
    return None


def context_vivado(context_file=None):
    """The Vivado in the host context vivado_workflow passed, else None."""
    path = context_file or os.environ.get("VIVADO_WORKFLOW_CONTEXT")
    if not path:
        return None
    try:
        with open(path) as fh:
            viv = (json.load(fh).get("vivado") or {}).get("path")
    except (IOError, OSError, ValueError):
        return None
    return viv if viv and os.path.isabs(viv) else None


def record(config_file, updates, why="recorded by `tc`"):
    """
    Merge dotted-key updates ({"backend.session.queue": "long"}) into a
    workspace config, creating it if needed; a .bak of the old one is kept.
    A value of None removes the key.
    """
    yaml_lite = config.paths.import_yaml_lite()
    raw = (yaml_lite.load_yaml(config_file) or {}) if os.path.isfile(config_file) else {}
    for dotted, value in updates.items():
        node = raw
        keys = dotted.split(".")
        for k in keys[:-1]:
            if not isinstance(node.get(k), dict):
                node[k] = {}
            node = node[k]
        if value is None:
            node.pop(keys[-1], None)
        else:
            node[keys[-1]] = value
    if os.path.isfile(config_file):
        shutil.copy2(config_file, config_file + ".bak")
    else:
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
    body = []
    _emit(raw, 0, body)
    head = ["# Site settings for this workspace, read by `tc` from any subdirectory.",
            "# Values %s; edit freely (the previous version is engine.yaml.bak)." % why]
    tmp = config_file + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(head + body) + "\n")
    os.rename(tmp, config_file)


def record_vivado(config_file, vivado):
    """Write backend.vivado / vivado_path into a workspace config."""
    record(config_file, {"backend.vivado": vivado, "backend.vivado_path": vivado},
           why="from the Vivado path Vivado MCP resolved or asked for")


def setup(context_file, workspace, force=False, dry_run=False, lsf_reachable=None):
    """
    Write <workspace>/.tc/engine.yaml. Returns the JSON-able result; raises
    ContextError for an unusable context.
    """
    ctx = read_context(context_file)
    settings, summary, notes = plan(ctx, lsf_reachable=lsf_reachable)
    path = os.path.join(os.path.abspath(workspace), config.WORKSPACE_CONFIG)
    if not (ctx.get("vivado") or {}).get("path"):
        kept = configured_vivado(config.load(path)) if os.path.isfile(path) else None
        if not kept:
            raise NeedsVivado("no Vivado path: the user did not give one and none is configured")
        # A forced rewrite keeps the Vivado this workspace already chose.
        settings.setdefault("backend", {}).update(vivado=kept, vivado_path=kept)
        notes = [n for n in notes if "Vivado path" not in n]
    text = render(settings, ctx)

    existing = None
    if os.path.isfile(path):
        existing = config.load(path)
        changes = diff({"backend": existing.get("backend")},
                       {"backend": config._deep_merge(config.DEFAULTS, settings)["backend"]})
    else:
        changes = None

    result = {"config": path, "summary": summary, "notes": notes}
    if existing is not None and not force:
        result.update(written=False, exists=True, changes=changes,
                      next_step="A config is already there and was left alone. "
                                "Show the user `changes`; if they want it replaced, "
                                "run setup again with force.")
        return result
    if dry_run:
        result.update(written=False, would_write=text, changes=changes)
        return result

    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    if existing is not None:
        shutil.copy2(path, path + ".bak")
        result["backup"] = path + ".bak"
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.rename(tmp, path)
    result.update(written=True, changes=changes,
                  next_step="Setup done. Check it with doctor, then start a run on a "
                            "checkpoint.")
    return result
