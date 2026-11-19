# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
migrate.py -- find, and with consent remove, what the retired workspace install left.

Timing closure used to reach an editor by copying itself into each workspace
(tools/install.py): the skill into four skills directories, role agents, a VS
Code coordinator agent, a Cursor rule, `tc guard` hooks merged into the
editors' settings, and a `timing-closure` MCP server registered beside Vivado
MCP's. Older workspaces also have a `claude-tc` launcher. All of that is now
served by the AMD skill pack (or the timing-closure plugin) and Vivado MCP's
vivado_workflow, so a workspace should carry nothing but its `.tc/` runs.

`tc migrate` lists what is left. `--remove` deletes it after the user says
yes (or `--yes`). Files are deleted only when they are ours: a skill directory
whose SKILL.md is the timing-closure skill, agents named as ours, hooks entries
that run `tc guard`, MCP entries named `timing-closure` that run tc. The user's
own entries in the same files are kept, and every edited file gets a .bak.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import json
import os
import re
import shutil

SKILL_DIRS = (".claude/skills", ".cursor/skills", ".agents/skills", ".github/skills")
SKILL_NAME = "timing-closure"
ROLE_AGENTS = ("tc-judge.md", "tc-launcher.md", "tc-monitor.md", "tc-planner.md",
               "tc-reporter.md", "tc-triage.md")
FILES = (".github/agents/timing-closure.agent.md", ".github/hooks/tc-guard.json",
         ".cursor/rules/timing-closure.mdc", "claude-tc", "bin/claude-tc")
HOOK_FILES = (".claude/settings.json", ".claude/settings.local.json", ".cursor/hooks.json")
MCP_FILES = ((".vscode/mcp.json", "servers"), (".cursor/mcp.json", "mcpServers"),
             (".mcp.json", "mcpServers"))
GUARD_MARK = "tc guard"
SERVER_NAME = "timing-closure"
_COMMENT_RE = re.compile(r"(?m)^\s*//.*$")


def _load(path):
    try:
        with open(path) as fh:
            raw = fh.read()
    except (IOError, OSError):
        return None
    for text in (raw, _COMMENT_RE.sub("", raw)):
        try:
            return json.loads(text)
        except ValueError:
            continue
    return None


def _is_our_skill(d):
    try:
        with open(os.path.join(d, "SKILL.md")) as fh:
            head = fh.read(2000)
    except (IOError, OSError):
        return False
    return bool(re.search(r"^name:\s*timing-closure\s*$", head, re.M))


def _is_ours_server(entry):
    text = json.dumps(entry)
    return "tc" in text and ("mcp_server" in text or "tc-mcp" in text or "/bin/tc" in text
                             or "timing-closure" in text)


def find(workspace):
    """Everything the old install left in workspace, as [{kind, path, detail, ...}]."""
    ws = os.path.abspath(workspace)
    found = []
    for rel in SKILL_DIRS:
        d = os.path.join(ws, rel, SKILL_NAME)
        if os.path.isdir(d) and _is_our_skill(d):
            found.append({"kind": "skill copy", "path": d, "op": "rmtree"})
    for name in ROLE_AGENTS:
        p = os.path.join(ws, ".claude", "agents", name)
        if os.path.isfile(p):
            found.append({"kind": "role agent", "path": p, "op": "unlink"})
    for rel in FILES:
        p = os.path.join(ws, rel)
        if os.path.isfile(p):
            kind = "launcher" if rel.endswith("claude-tc") else "agent, rule or hook file"
            found.append({"kind": kind, "path": p, "op": "unlink"})
    for rel in HOOK_FILES:
        p = os.path.join(ws, rel)
        data = _load(p)
        hooks = (data or {}).get("hooks") or {}
        n = sum(1 for entries in hooks.values() if isinstance(entries, list)
                for e in entries if GUARD_MARK in json.dumps(e))
        if n:
            found.append({"kind": "guard hooks", "path": p, "op": "hooks",
                          "detail": "%d entr%s running `tc guard`" % (n, "y" if n == 1 else "ies")})
    for rel, key in MCP_FILES:
        p = os.path.join(ws, rel)
        entry = ((_load(p) or {}).get(key) or {}).get(SERVER_NAME)
        if entry is not None and _is_ours_server(entry):
            found.append({"kind": "MCP registration", "path": p, "op": "mcp", "key": key,
                          "detail": "the `%s` server" % SERVER_NAME})
    return found


def _save(path, data):
    shutil.copy2(path, path + ".bak")
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def remove(items):
    """Remove each item; returns [(path, what was done)]."""
    done = []
    for it in items:
        p = it["path"]
        if it["op"] == "rmtree":
            shutil.rmtree(p)
            done.append((p, "removed"))
        elif it["op"] == "unlink":
            os.unlink(p)
            done.append((p, "removed"))
        elif it["op"] == "hooks":
            data = _load(p) or {}
            hooks = data.get("hooks") or {}
            for event in list(hooks):
                kept = [e for e in hooks[event] if GUARD_MARK not in json.dumps(e)]
                if kept:
                    hooks[event] = kept
                else:
                    del hooks[event]
            if not hooks:
                data.pop("hooks", None)
            _save(p, data)
            done.append((p, "tc guard hooks removed (backup %s.bak)" % os.path.basename(p)))
        elif it["op"] == "mcp":
            data = _load(p) or {}
            servers = data.get(it["key"]) or {}
            servers.pop(SERVER_NAME, None)
            _save(p, data)
            done.append((p, "timing-closure server removed (backup %s.bak)" % os.path.basename(p)))
    return done
