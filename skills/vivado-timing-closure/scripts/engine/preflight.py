#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
preflight.py -- is this workspace set up to start a run? Nothing is submitted
until it is.

A run that starts on a wrong setting fails slowly and expensively: with no
LSF queue Vivado MCP picked an interactive one, with no OS selection jobs
landed on hosts whose glibc cannot run the Vivado build, and the supervisor
kept resubmitting. So `tc close` checks the effective settings first and,
when something is missing or wrong, stops with exit code 3 and
{"needs": "setup", ...} on stdout: Vivado MCP shows the user a form (or the
agent asks in chat), the answers go to the `setup` action, and the start is
tried again. Nothing here ever fills a value in by itself.

What counts as set: the workspace config (.tc/engine.yaml), and for runs
through Vivado MCP also the VIVADO_LSF_* settings the server passes on, since
that is what vivado_lsf will use.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import os
import re
import shlex
import subprocess

from . import config
from .backends import resources_from_config

NEEDS_EXIT = 3
PROBE_TIMEOUT_S = 20
MAX_CHOICES = 40


def _lsf_run(cmd, env_script=None, timeout=PROBE_TIMEOUT_S):
    """(rc, output) of an LSF query, after sourcing the site's env script; (None, why) when LSF is not here."""
    line = " ".join(shlex.quote(c) for c in cmd)
    if env_script:
        line = ". %s >/dev/null 2>&1; %s" % (shlex.quote(env_script), line)
    try:
        p = subprocess.run(["bash", "-c", line], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if p.returncode == 127:
        return None, "%s is not available here" % cmd[0]
    return p.returncode, p.stdout


def open_queues(env_script=None, run=_lsf_run):
    """Names of the queues that accept jobs, or None when LSF cannot be asked."""
    rc, out = run(["bqueues", "-w"], env_script)
    if rc != 0 or not out:
        return None
    names = []
    for line in out.splitlines()[1:]:
        f = line.split()
        if len(f) >= 3 and f[2].lower().startswith("open") and "inact" not in f[2].lower():
            names.append(f[0])
    return names


_COMMON = ("long", "medium", "normal", "short", "batch")


def ranked(queues):
    """The usual batch queues first, interactive ones last: a run takes hours."""
    def key(q):
        return (0 if q in _COMMON else 2 if "interactive" in q else 1,
                _COMMON.index(q) if q in _COMMON else 0, q)
    return sorted(queues or [], key=key)


def hosts_with(select, env_script=None, run=_lsf_run):
    """How many hosts match a select term, or None when LSF cannot be asked."""
    rc, out = run(["lshosts", "-w", "-R", "select[%s]" % select], env_script)
    if rc is None:
        return None
    if rc != 0:
        return 0
    return max(0, len([l for l in out.splitlines() if l.strip()]) - 1)


def _os_terms(os_type):
    """
    The select terms an OS setting may mean. A bare value ('ws8') is what
    Vivado MCP tries as ostype then osver (both are site resources, not LSF
    built-ins); a term with its own operator is used as written.
    """
    return [os_type] if "=" in os_type else ["ostype==%s" % os_type, "osver==%s" % os_type]


def _os_term(os_type):
    """The term written for plain bsub, which takes one: the osver form for a bare value."""
    return _os_terms(os_type)[-1]


def hosts_for_os(os_type, env_script=None, run=None):
    """Hosts matching any form of the OS setting, or None when LSF cannot be asked."""
    counts = [hosts_with(t, env_script, run or _lsf_run) for t in _os_terms(os_type)]
    known = [c for c in counts if c is not None]
    return max(known) if known else None


def effective(cfg, backend_name, env=None):
    """The settings a run will really use: config first, then what the server passes on."""
    env = os.environ if env is None else env
    b = (cfg or {}).get("backend") or {}
    res = resources_from_config(b, backend_name)
    out = {"vivado": b.get("vivado_path") or b.get("vivado"), "queue": res.queue,
           "os_type": res.os_type, "memory_mb": res.memory_mb, "slots": res.slots,
           "env_script": (b.get("lsf") or {}).get("env_script"), "extra_submit_args": None}
    if backend_name == "session":
        senv = dict(env)
        senv.update(((b.get("session") or {}).get("env")) or {})
        out["queue"] = out["queue"] or senv.get("VIVADO_LSF_QUEUE") or None
        out["os_type"] = out["os_type"] or senv.get("VIVADO_LSF_OS_TYPE") or None
        out["env_script"] = out["env_script"] or senv.get("VIVADO_LSF_ENV_SCRIPT") or None
        out["extra_submit_args"] = senv.get("VIVADO_LSF_EXTRA_SUBMIT_ARGS") or None
    return out


def check(cfg, backend_name, env=None, probe=True, run=_lsf_run):
    """
    {"ok": bool, "fields": [...], "warnings": [...], "settings": {...}}. Each
    field is something the user must give: name, label, question, current
    value, problem, and choices where the farm can list them.
    """
    eff = effective(cfg, backend_name, env)
    fields, warnings = [], []

    viv = eff["vivado"]
    if not viv or viv == "vivado" or not os.path.isabs(viv):
        fields.append({"name": "vivado", "label": "Vivado", "type": "path",
                       "question": "Which Vivado should runs use? The vivado executable or its install folder.",
                       "problem": "no Vivado path is set" if not viv or viv == "vivado"
                       else "%s is not an absolute path" % viv})
    elif not (os.path.isfile(viv) and os.access(viv, os.X_OK)):
        fields.append({"name": "vivado", "label": "Vivado", "type": "path", "current": viv,
                       "question": "Which Vivado should runs use? The vivado executable or its install folder.",
                       "problem": "%s is not an executable file" % viv})

    if backend_name in ("lsf", "session"):
        script = eff["env_script"]
        if script and not os.path.isfile(script):
            fields.append({"name": "env_script", "label": "LSF environment script", "type": "path",
                           "current": script, "question": "Which script sets up LSF (bsub, bqueues) here?",
                           "problem": "%s does not exist" % script})
            script = None
        queues = open_queues(script, run) if probe else None
        q = eff["queue"]
        if not q:
            fields.append({"name": "queue", "label": "LSF queue", "type": "string",
                           "question": "Which LSF queue should runs go to? Implementation runs take hours.",
                           "problem": "no queue is set, so Vivado MCP would pick one itself "
                                      "(often an interactive queue)",
                           "choices": ranked(queues)[:MAX_CHOICES]})
        elif queues is not None and q not in queues:
            fields.append({"name": "queue", "label": "LSF queue", "type": "string", "current": q,
                           "question": "Which LSF queue should runs go to?",
                           "problem": "queue %s does not exist or is not open" % q,
                           "choices": ranked(queues)[:MAX_CHOICES]})
        os_type = eff["os_type"]
        if not os_type:
            fields.append({"name": "os_type", "label": "LSF OS type", "type": "string",
                           "question": "Which OS type must hosts have (for example ws8)? Hosts with "
                                       "another OS may not run this Vivado.",
                           "problem": "no OS type is set, so jobs may land on hosts that cannot "
                                      "run this Vivado (GLIBC errors)"})
        elif probe:
            n = hosts_for_os(os_type, script, run)
            if n == 0:
                fields.append({"name": "os_type", "label": "LSF OS type", "type": "string",
                               "current": os_type,
                               "question": "Which OS type must hosts have (for example ws8)?",
                               "problem": "no host matches %s" % " or ".join(_os_terms(os_type))})
        extra = eff["extra_submit_args"] or ""
        if re.search(r"(^|\s)-m(\s|$)", extra):
            warnings.append("the extra submit arguments pin hosts (%s); a queue that does not use "
                            "those hosts refuses every job" % extra.strip())
    return {"ok": not fields, "backend": backend_name, "fields": fields, "warnings": warnings,
            "settings": eff}


def needs(result):
    """The stdout payload for exit code 3: what to ask, and the action that takes the answers."""
    names = ", ".join(f["label"] for f in result["fields"])
    return {"needs": "setup", "action": "setup",
            "question": "Before anything starts, this workspace needs: %s." % names,
            "fields": result["fields"], "warnings": result["warnings"]}


# Where each answer is recorded in .tc/engine.yaml, per backend.
def updates_for(answers, backend_name):
    """Dotted config keys for the answers given to `setup`."""
    up = {}
    if answers.get("vivado"):
        up["backend.vivado"] = answers["vivado"]
        up["backend.vivado_path"] = answers["vivado"]
    block = "session" if backend_name == "session" else "lsf"
    for key in ("queue", "memory_mb", "slots"):
        if answers.get(key) not in (None, ""):
            up["backend.%s.%s" % (block, key)] = answers[key]
    if answers.get("os_type"):
        os_type = answers["os_type"]
        if block == "session":
            up["backend.session.os_type"] = os_type.split("==", 1)[1].strip() if "==" in os_type else os_type
        else:
            up["backend.lsf.os_type"] = _os_term(os_type)
    if answers.get("resource_select"):
        # Upstream's --resource-select (PR #742): a whole select[...] string, or
        # just its expression; written verbatim as the select term (rusage stays ours).
        rs = answers["resource_select"].strip()
        m = re.match(r"^select\[(.*)\]$", rs)
        up["backend.lsf.os_type"] = m.group(1).strip() if m else rs
    if answers.get("extra_lsf_options"):
        up["backend.lsf.extra_bsub_args"] = answers["extra_lsf_options"]
    if answers.get("env_script"):
        up["backend.lsf.env_script"] = answers["env_script"]
        if block == "session":
            up["backend.session.env.VIVADO_LSF_ENV_SCRIPT"] = answers["env_script"]
    return up
