#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
mcp_discover.py -- find the Vivado MCP server the user already has configured.

Asking someone to re-state a path they have already written in their editor
config is a good way to have them pin a stale build by accident. So the engine
reads the same files they do.

**It takes the binary, not the command.** A discovered entry says how *that*
client wants to talk to the server, and for an editor that is usually the
shared daemon -- a real config on this machine reads:

    "vivado-mcp-server": {"command": ".../vivado-mcp-server-linux-amd64-0.6.9",
                          "args": ["--stdio-bridge"]}

`--stdio-bridge` relays to the shared HTTP daemon, which is the one transport a
long run must not use. Inheriting the command verbatim would quietly undo the
isolation. So discovery keeps the executable and the options that describe
*which Vivado to drive*, and the backend supplies the transport itself.

Matching is on the binary name rather than the config key, because the same
files hold several other Vivado-ish servers -- `vivado-skill-server`,
`vivado-knowledge-server`, `vivado-doc-*` -- and a loose "vivado" match picks
the wrong one.

Sources, most specific first. The first readable hit wins, so a project config
beats a user-wide one, exactly as the editors themselves resolve it:

* project `.mcp.json`, `.vscode/mcp.json`, `.cursor/mcp.json` (walking up);
* `~/.claude.json` (the project block, then the global one);
* user files: Cursor, Copilot CLI, VS Code (local and remote server), Codex;
* a Vivado MCP daemon already running for this user -- the Vivado AI
  extension registers its server through the editor's API, not a file, and on
  a remote host its settings exist only on that process's command line;
* the binaries the extension ships in its `bin/` directory.

**Presence is not capability.** Customer builds have no `vivado_lsf` tool
("customer build: gated features are absent"), the extension's bundled server
included, so `resolve()` starts each candidate privately and keeps the first
whose tools include `vivado_lsf` and whose `vivado_lsf check` passes.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import json
import os
import re

# A server whose executable looks like this is the one we want.
BINARY_RE = re.compile(r"^vivado-mcp-server(\b|[-_.])", re.I)
# Fallback when the command is a wrapper script with an unhelpful name.
KEY_RE = re.compile(r"^vivado[-_]?mcp(\b|[-_.])", re.I)

# Transport and mode flags: how *that* client talks to the server. Never ours.
DROP_FLAGS = frozenset([
    "--stdio", "--stdio-bridge", "--proxy-mode", "--rfb-serve",
    "--builtin-wm", "--kill-all", "--daemon",
])
DROP_PREFIXES = ("--port", "--rfb-port", "--bind", "--logdir")

# Worth inheriting: they describe the *environment* -- which Vivado to drive
# and how to reach the farm. `--enable-lsf` matters most of all: the server
# only registers the vivado_lsf tool when it is on, so inheriting an editor's
# settings without it leaves the backend unable to submit anything.
KEEP_PREFIXES = ("--vivado-path", "--env-script", "--allow-path-override",
                 "--enable-lsf", "--lsf-env-script", "--lsf-bin-path",
                 "--max-sessions")

# The extension passes every setting as `--env=KEY=VALUE`. These keys describe
# the environment and are kept; the rest (GUI, display, session type, SSH
# machines, extension attach) are per-session choices and are dropped.
KEEP_ENV_KEYS = frozenset([
    "VIVADO_PATH", "VIVADO_ENV_SCRIPT", "VIVADO_ALLOW_PATH_OVERRIDE",
    "VIVADO_ENABLE_LSF", "VIVADO_MAX_SESSIONS", "VIVADO_LICENSE_MODE",
    "XILINXD_LICENSE_FILE", "LD_PRELOAD",
])
KEEP_ENV_PREFIXES = ("VIVADO_LSF_",)

# Deliberately *not* inherited, though the VS Code extension passes them:
# --default-workdir, --display, --display-mode, --gui-mode, --session-type.
# Those are per-session choices, and the engine makes them per run. Taking an
# editor's --gui-mode=true would start a GUI on a farm node for a batch job.

_COMMENT_RE = re.compile(r"(?m)^\s*//.*$")


class Candidate(object):
    __slots__ = ("source", "name", "binary", "args", "env")

    def __init__(self, source, name, binary, args=None, env=None):
        self.source = source
        self.name = name
        self.binary = binary
        self.args = list(args or [])
        self.env = dict(env or {})

    def usable(self):
        return bool(self.binary) and os.path.isfile(self.binary) \
            and os.access(self.binary, os.X_OK)

    def to_dict(self):
        return {"source": self.source, "name": self.name,
                "binary": self.binary, "args": self.args,
                "usable": self.usable()}

    def __repr__(self):
        return "<Candidate %s from %s>" % (self.binary, self.source)


def _load(path):
    """Parse JSON, tolerating the // comments editors allow in these files."""
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


def _looks_right(name, command):
    if command and BINARY_RE.match(os.path.basename(command)):
        return True
    return bool(KEY_RE.match(name or ""))


def _filter_args(args):
    """
    Keep what describes the design environment; drop the transport.

    A separated value (`--port 18090`, `--vivado-path /v/bin/vivado`) has to
    follow its flag, so the next token is consumed either way -- but kept or
    dropped according to which flag it belonged to. One shared "skip" flag for
    both meanings silently kept the values of dropped options.
    """
    kept = []
    pending = None          # None | "keep" | "drop" | "env"
    for arg in args or []:
        arg = str(arg)
        if pending is not None:
            if pending == "keep":
                kept.append(arg)
            elif pending == "env" and _env_kept(arg):
                kept.append("--env=" + arg)     # one token: no dangling value
            pending = None
            continue
        if arg == "--env":
            pending = "env"
            continue
        if arg.startswith("--env="):
            if _env_kept(arg[len("--env="):]):
                kept.append(arg)
            continue
        if arg in DROP_FLAGS:
            continue
        if arg.startswith(DROP_PREFIXES):
            pending = "drop" if "=" not in arg else None
            continue
        if arg.startswith(KEEP_PREFIXES):
            kept.append(arg)
            pending = "keep" if "=" not in arg else None
    return kept


def _env_kept(pair):
    key = pair.split("=", 1)[0]
    return key in KEEP_ENV_KEYS or key.startswith(KEEP_ENV_PREFIXES)


def _from_servers(source, servers):
    out = []
    for name, entry in (servers or {}).items():
        if not isinstance(entry, dict):
            continue
        command = entry.get("command")
        if not _looks_right(name, command):
            continue
        out.append(Candidate(source, name, command,
                             _filter_args(entry.get("args")),
                             entry.get("env")))
    return out


def _from_file(path):
    data = _load(path)
    if not isinstance(data, dict):
        return []
    found = []
    # Claude Code and Cursor use `mcpServers`; VS Code uses `servers`.
    for key in ("mcpServers", "servers"):
        found += _from_servers(path, data.get(key))
    return found


def _from_claude_json(start_dir):
    """
    ~/.claude.json holds a global block and a per-project one. The project
    block wins, which is how the client resolves it too.
    """
    path = os.path.expanduser("~/.claude.json")
    data = _load(path)
    if not isinstance(data, dict):
        return []

    found = []
    projects = data.get("projects") or {}
    cur = os.path.abspath(start_dir or os.getcwd())
    while True:
        block = projects.get(cur)
        if isinstance(block, dict):
            found += _from_servers("%s [%s]" % (path, cur),
                                   block.get("mcpServers"))
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    found += _from_servers("%s [global]" % path, data.get("mcpServers"))
    return found


def _project_files(start_dir):
    """Walk up from start_dir, as the editors do, nearest first."""
    paths = []
    cur = os.path.abspath(start_dir or os.getcwd())
    while True:
        for rel in (".mcp.json",
                    os.path.join(".vscode", "mcp.json"),
                    os.path.join(".cursor", "mcp.json")):
            paths.append(os.path.join(cur, rel))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return paths


USER_FILES = (
    "~/.cursor/mcp.json",
    "~/.copilot/mcp-config.json",
    "~/.config/Code/User/mcp.json",
    "~/.vscode-server/data/User/mcp.json",
    "~/.vscode-server/data/Machine/mcp.json",
    "~/Library/Application Support/Code/User/mcp.json",
)
CODEX_CONFIG = "~/.codex/config.toml"

_TOML_TABLE = re.compile(r"^\s*\[mcp_servers\.\"?([^\]\"]+)\"?\]\s*$")
_TOML_KV = re.compile(r"^\s*(command|args)\s*=\s*(.+?)\s*$")


def _from_codex(path):
    """
    Codex CLI keeps servers in TOML: [mcp_servers.<name>] command = "...",
    args = [...]. Only those two keys matter here, and both are TOML values
    that are also valid JSON, so no TOML parser is needed (none in 3.6).
    """
    try:
        with open(os.path.expanduser(path)) as fh:
            lines = fh.read().splitlines()
    except (IOError, OSError):
        return []
    servers, cur = {}, None
    for line in lines:
        m = _TOML_TABLE.match(line)
        if m:
            cur = servers.setdefault(m.group(1), {})
            continue
        if line.strip().startswith("["):
            cur = None
            continue
        m = _TOML_KV.match(line)
        if cur is not None and m:
            try:
                cur[m.group(1)] = json.loads(m.group(2))
            except ValueError:
                pass
    return _from_servers(path, servers)


def _from_running(proc_root=None):
    """
    Vivado MCP servers this user has running as a daemon (no --stdio: that one
    belongs to some client's process tree). The Vivado AI extension starts one
    with its settings as --env=VIVADO_* flags, which is the only place a
    remote host can see them.
    """
    out = []
    # TC_MCP_PROC_ROOT lets tests (and a sandboxed run) hide this host's daemons.
    proc_root = proc_root or os.environ.get("TC_MCP_PROC_ROOT", "/proc")
    try:
        pids = [d for d in os.listdir(proc_root) if d.isdigit()]
    except OSError:
        return out
    uid = os.getuid()
    for pid in sorted(pids, key=int):
        base = os.path.join(proc_root, pid)
        try:
            if os.stat(base).st_uid != uid:
                continue
            with open(os.path.join(base, "cmdline"), "rb") as fh:
                argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
        except (IOError, OSError):
            continue
        if not argv or not BINARY_RE.match(os.path.basename(argv[0])):
            continue
        flags = set(argv[1:])
        if flags & {"--stdio", "--stdio-bridge", "--proxy-mode", "--kill-all",
                    "--version", "--help"}:
            continue
        out.append(Candidate("running daemon pid %s" % pid,
                             os.path.basename(argv[0]), argv[0],
                             _filter_args(argv[1:])))
    return out


EXTENSION_DIRS = (
    "~/.vscode-server/extensions",
    "~/.vscode/extensions",
    "~/.cursor/extensions",
    "~/.vscode-server-insiders/extensions",
    "~/.cursor-server/extensions",
    "~/.vscode-remote/extensions",
)


def _from_extensions():
    """
    The server binary the VS Code extension ships, at <extension>/bin/.

    A last resort, because it is only a binary: the extension's *settings*
    (which Vivado, whether LSF is enabled) live in the mcp.json it writes, and
    on hosts where it registers through an editor API instead there is no file
    at all. Finding the binary here still beats failing, but a run that needs
    LSF will want those settings supplied another way.

    Newest-looking install first. That is a string sort on the version in the
    directory name, which is right for 2026.9.1 over 0.6.7 and good enough for
    choosing a default.
    """
    out = []
    for rel in EXTENSION_DIRS:
        root = os.path.expanduser(rel)
        if not os.path.isdir(root):
            continue
        try:
            names = sorted(os.listdir(root), reverse=True)
        except OSError:
            continue
        for name in names:
            bindir = os.path.join(root, name, "bin")
            if not os.path.isdir(bindir):
                continue
            try:
                files = sorted(os.listdir(bindir))
            except OSError:
                continue
            for f in files:
                if not BINARY_RE.match(f) or f.endswith(".exe"):
                    continue
                out.append(Candidate("%s (extension %s)" % (bindir, name),
                                     name, os.path.join(bindir, f)))
    return out


def discover(start_dir=None):
    """Every Vivado MCP server configured for this machine, most specific first."""
    found = []
    for path in _project_files(start_dir):
        if os.path.isfile(path):
            found += _from_file(path)
    found += _from_claude_json(start_dir)
    for rel in USER_FILES:
        path = os.path.expanduser(rel)
        if os.path.isfile(path):
            found += _from_file(path)
    found += _from_codex(CODEX_CONFIG)
    found += _from_running()
    found += _from_extensions()

    seen, unique = set(), []
    for c in found:
        if c.binary in seen:
            continue
        seen.add(c.binary)
        unique.append(c)
    return unique


def best(start_dir=None):
    """The first configured server that actually exists, or None."""
    for candidate in discover(start_dir):
        if candidate.usable():
            return candidate
    return None


# ---------------------------------------------------------------------------
# capability
# ---------------------------------------------------------------------------
_VERSION_RE = re.compile(r"vivado-mcp-server\s+(\S+)(?:\s+\(commit:\s*([0-9a-f]+).*?channel:\s*(\w+))?")


def version_of(binary, timeout=15):
    """{'version', 'commit', 'channel'} from `<binary> --version`, best effort."""
    import subprocess
    try:
        out = subprocess.run([binary, "--version"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True,
                             timeout=timeout).stdout
    except Exception:                                  # noqa: BLE001
        return {}
    for line in reversed((out or "").splitlines()):
        m = _VERSION_RE.search(line)
        if m:
            return {"version": m.group(1), "commit": m.group(2),
                    "channel": m.group(3)}
    return {}


def probe(candidate, timeout=60, check_lsf=True, client_factory=None):
    """
    Can this server launch runs on LSF? Starts a private copy (`--stdio`, a
    throw-away state directory), lists its tools and, when `vivado_lsf` is
    there, asks it to check the farm. Never raises.

    Returns {"ok", "why", "tools", "lsf_check", "version"...}.
    """
    import shutil
    import tempfile
    from . import mcp_client

    info = {"ok": False, "binary": candidate.binary, "source": candidate.source}
    info.update(version_of(candidate.binary))
    if not candidate.usable():
        info["why"] = "not an executable file"
        return info
    state = tempfile.mkdtemp(prefix="tc-mcp-probe-")
    env = dict(candidate.env)
    env["VIVADO_STATE_DIR"] = state
    command = [candidate.binary] + list(candidate.args) + ["--stdio"]
    client = (client_factory or mcp_client.McpClient)(command, env=env, timeout=timeout)
    try:
        client.start()
        tools = sorted(t.get("name") for t in
                       (client._request("tools/list", {}, timeout=timeout).get("tools") or []))
        info["tools"] = tools
        if "vivado_lsf" not in tools:
            info["why"] = ("no vivado_lsf tool%s" % (
                " (a %s build: LSF is gated)" % info["channel"]
                if info.get("channel") == "customer" else ""))
            return info
        if check_lsf:
            try:
                report = client.call_json("vivado_lsf", {"action": "check"}, timeout=180)
            except mcp_client.ToolError as exc:
                # 0.6.x has vivado_lsf but not `check` (nor today's `start`
                # arguments): too old to drive, not merely unready.
                info["why"] = "vivado_lsf predates the check action: %s" % str(exc)[:120]
                return info
            info["lsf_check"] = {"ready": report.get("ready"),
                                 "summary": report.get("summary")}
            if not report.get("ready"):
                info["why"] = "vivado_lsf check failed: %s" % (report.get("summary")
                                                               or report.get("failures"))
                return info
        info["ok"] = True
        info["why"] = "has vivado_lsf%s" % (", LSF ready" if check_lsf else "")
        return info
    except Exception as exc:                           # noqa: BLE001
        info["why"] = "could not start it privately: %s" % str(exc)[:200]
        return info
    finally:
        try:
            client.close()
        except Exception:                              # noqa: BLE001
            pass
        shutil.rmtree(state, ignore_errors=True)


def resolve(start_dir=None, probe_fn=None, candidates=None):
    """
    (candidate | None, report): the first discovered server that can launch
    runs on LSF, and why every one before it could not.
    """
    probe_fn = probe_fn or probe
    report = []
    for c in (candidates if candidates is not None else discover(start_dir)):
        info = probe_fn(c)
        report.append(info)
        if info.get("ok"):
            return c, report
    return None, report
