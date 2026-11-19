#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
views.py -- any workflow's own view, the way the Workflows view gets it.

The Vivado AI extension's Workflows view knows nothing about timing closure: a
workflow's workflow.json names, in its "view" block, a read-only list action
and a detail action, and the extension draws the workflow-view/1 documents
they print. This module does the same for a browser, with the same rules as
the Vivado MCP server's vivado_workflow tool (workflow_manifest.go,
workflow_run.go) for the part a view needs:

* What runs: only the actions the view block names, only when the manifest
  marks them readOnly, as <workflow dir>/<entry> plus the action's argv
  template, with cwd = the workspace and the builtins {workspace},
  {workflow_dir} and {context_file}. The detail action gets one more value,
  view.detail_arg, and that value must be an item id the list action itself
  returned for that workspace, or (for a "path" argument) a path inside it.
* What comes back: stdout parsed as JSON; the {ok, result} wrapper
  vivado_workflow adds is accepted as well as the bare document; anything but
  a workflow-view/1 document is an error.
* What a page may show (a followed log, an "open" link): regular text files
  inside the workspace or inside one of the paths the run's own document lists
  -- the extension's readTail rule -- read with the server's own account.

Which workflows and workspaces is the caller's business: a Source carries
them. Both the engine's own server (server.py) and the Django app in
web_run_platform/ use this module, so the two cannot drift.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import json
import os
import re
import subprocess
import tempfile
import threading
import time

VIEW_SCHEMA = "workflow-view/1"
MANIFEST = "workflow.json"
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")      # workflow_manifest.go's rule
STDOUT_CAP = 8 * 1024 * 1024
TAIL_BYTES = 64 * 1024        # the extension's FOLLOW_BYTES / FOLLOW_LINES
TAIL_LINES = 300
OPEN_BYTES = 2 * 1024 * 1024
# What "open" and "tail" may show: text the browser can display as text/plain.
TEXT_EXT = (".log", ".jou", ".out", ".err", ".txt", ".rpt", ".json", ".jsonl", ".csv", ".tcl",
            ".xdc", ".yaml", ".yml", ".md", ".xml", ".html", ".htm", ".cfg", ".ini", ".sh", ".py")


class WorkflowError(Exception):
    pass


def inside(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


# ------------------------------------------------------------------ manifests
class Workflow(object):
    def __init__(self, folder, data, entry=None):
        self.dir = os.path.realpath(folder)
        self.id = str(data.get("id") or "")
        self.title = str(data.get("title") or self.id)
        self.entry = str(data.get("entry") or "")
        # A development checkout keeps the manifest away from its launcher;
        # the caller then names the entry (a packaged skill never needs this).
        self.entry_override = entry
        self.actions = data.get("actions") or {}
        v = data.get("view") or {}
        self.list_action = v.get("list")
        self.detail_action = v.get("detail")
        self.detail_arg = v.get("detail_arg")
        try:
            r = float(v.get("refresh_seconds", 30))
        except (TypeError, ValueError):
            r = 30
        self.refresh_seconds = int(min(3600, max(5, r)))

    def to_json(self):
        return {"id": self.id, "title": self.title, "dir": self.dir, "refresh_seconds": self.refresh_seconds,
                "detail": bool(self.detail_action and self.detail_arg), "detail_arg": self.detail_arg}

    def entry_path(self):
        """The entry, resolved inside the workflow folder, or WorkflowError."""
        if self.entry_override:
            real = os.path.realpath(self.entry_override)
        else:
            if not self.entry or os.path.isabs(self.entry):
                raise WorkflowError("workflow %s: entry must be a path inside its folder" % self.id)
            real = os.path.realpath(os.path.join(self.dir, self.entry))
            if not inside(real, self.dir):
                raise WorkflowError("workflow %s: entry resolves outside its folder" % self.id)
        if not (os.path.isfile(real) and os.access(real, os.X_OK)):
            raise WorkflowError("workflow %s: entry %s is not an executable file" % (self.id, self.entry))
        return real

    def view_action(self, name):
        act = self.actions.get(name) if name else None
        if not isinstance(act, dict):
            raise WorkflowError("workflow %s: no action %r" % (self.id, name))
        if act.get("readOnly") is not True:
            raise WorkflowError("workflow %s: view action %r is not readOnly; not run" % (self.id, name))
        return act


def load(folder, entry=None):
    """The workflow in folder (its workflow.json), or WorkflowError."""
    path = os.path.join(folder, MANIFEST)
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (IOError, OSError, ValueError) as exc:
        raise WorkflowError("%s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise WorkflowError("%s: not a JSON object" % path)
    wf = Workflow(folder, data, entry=entry)
    if not wf.id:
        raise WorkflowError("%s: no id" % path)
    if not wf.list_action:
        raise WorkflowError("workflow %s declares no view" % wf.id)
    return wf


# ------------------------------------------------------------------ running
def render_argv(template, values):
    """workflow_run.go renderWorkflowArgv: strings filled, {if, argv} groups only when set."""
    missing = []

    def fill(s):
        def one(m):
            if m.group(1) not in values:
                missing.append(m.group(1))
                return ""
            return values[m.group(1)]
        return PLACEHOLDER.sub(one, str(s))

    argv = []
    for it in template or []:
        if isinstance(it, dict):
            if it.get("if") in values:
                argv.extend(fill(s) for s in it.get("argv") or [])
        else:
            argv.append(fill(it))
    if missing:
        raise WorkflowError("no value for {%s}" % missing[0])
    return argv


def document(stdout):
    """The workflow-view/1 document an action printed, or WorkflowError."""
    try:
        data = json.loads(stdout)
    except ValueError:
        raise WorkflowError("the action printed no JSON")
    if isinstance(data, dict) and "ok" in data and "result" in data:   # vivado_workflow's wrapper
        if data.get("ok") is not True:
            raise WorkflowError(str(data.get("stderr_tail") or data.get("error") or "the action failed")[-500:])
        data = data.get("result")
    if not isinstance(data, dict) or data.get("schema") != VIEW_SCHEMA:
        raise WorkflowError("the action returned no %s document" % VIEW_SCHEMA)
    return data


def run_view_action(wf, action, workspace, extra=None, timeout=60.0, client="web"):
    """The workflow-view/1 document a read-only view action prints."""
    act = wf.view_action(action)
    entry = wf.entry_path()
    fd, ctx = tempfile.mkstemp(prefix="wfview_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump({"schema_version": 1, "workflow": wf.id, "action": action, "workspace": workspace,
                       "client": {"type": "web", "name": client}}, fh)
        values = dict(extra or {}, workspace=workspace, workflow_dir=wf.dir, context_file=ctx)
        argv = render_argv(act.get("argv"), values)
        env = dict(os.environ, VIVADO_WORKFLOW_ID=wf.id, VIVADO_WORKFLOW_ACTION=action,
                   VIVADO_WORKFLOW_WORKSPACE=workspace, VIVADO_WORKFLOW_CONTEXT=ctx)
        try:
            p = subprocess.run([entry] + argv, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise WorkflowError("%s %s: no answer within %ds" % (wf.id, action, timeout))
        except OSError as exc:
            raise WorkflowError("%s %s: %s" % (wf.id, action, exc))
    finally:
        try:
            os.unlink(ctx)
        except OSError:
            pass
    if p.returncode != 0:
        tail_text = (p.stderr or p.stdout or b"").decode("utf-8", "replace").strip()[-400:]
        raise WorkflowError("%s %s exited %d: %s" % (wf.id, action, p.returncode, tail_text))
    return document(p.stdout[:STDOUT_CAP].decode("utf-8", "replace"))


# Each document a page last drew, so a followed log can be checked against the
# paths that run itself lists without running the action again on every tail.
_LAST = {}
_LOCK = threading.Lock()


class Source(object):
    """
    Which workflows and workspaces a server shows.

    workflow_dirs and workspaces are callables (re-read on every request, so a
    workspace registered while the server runs shows at once) returning lists
    of folders; entries maps a workflow folder to its launcher when the
    manifest's own entry is not beside it (a development checkout).
    """

    def __init__(self, workflow_dirs, workspaces, entries=None, timeout=60.0, client="web"):
        self._dirs = workflow_dirs
        self._spaces = workspaces
        self.entries = dict(entries or {})
        self.timeout = timeout
        self.client = client

    def workflow_dirs(self):
        return list(self._dirs())

    def workspaces(self):
        out = []
        for p in self._spaces():
            p = os.path.expanduser(str(p).strip())
            if p and os.path.isdir(p):
                real = os.path.realpath(p)
                if real not in out:
                    out.append(real)
        return out

    def workflows(self):
        """([Workflow], [errors]) for every configured folder, first id wins."""
        found, errors, seen = [], [], set()
        for d in self.workflow_dirs():
            try:
                wf = load(d, entry=self.entries.get(os.path.realpath(d)))
            except WorkflowError as exc:
                errors.append({"dir": d, "error": str(exc)})
                continue
            if wf.id in seen:
                errors.append({"dir": d, "error": "workflow %s: shadowed by an earlier folder" % wf.id})
                continue
            seen.add(wf.id)
            found.append(wf)
        return found, errors

    def find(self, workflow_id):
        for wf in self.workflows()[0]:
            if wf.id == workflow_id:
                return wf
        raise KeyError(workflow_id)

    def known_workspace(self, path):
        """The configured workspace path is, resolved, or None."""
        if not path:
            return None
        real = os.path.realpath(os.path.expanduser(path))
        return real if real in self.workspaces() else None

    def workspace_of(self, path):
        """The configured workspace a path lies in (the innermost), or None."""
        real = os.path.realpath(os.path.expanduser(path))
        best = None
        for ws in self.workspaces():
            if inside(real, ws) and (best is None or len(ws) > len(best)):
                best = ws
        return best

    # -------------------------------------------------------------- documents
    def list_doc(self, wf, workspace):
        doc = run_view_action(wf, wf.list_action, workspace, timeout=self.timeout, client=self.client)
        with _LOCK:
            _LAST[("list", wf.id, workspace)] = (time.time(), doc)
        return doc

    def _item_ids(self, wf, workspace):
        with _LOCK:
            hit = _LAST.get(("list", wf.id, workspace))
        doc = hit[1] if hit and time.time() - hit[0] < 2 * wf.refresh_seconds else self.list_doc(wf, workspace)
        return {str(i.get("id")) for i in doc.get("items") or [] if isinstance(i, dict)}

    def detail_value(self, wf, workspace, value):
        """The detail argument, if the list returned it or it is a path inside the workspace; else KeyError."""
        if not (wf.detail_action and wf.detail_arg) or not value or "\0" in value:
            raise KeyError(value)
        decl = ((wf.actions.get(wf.detail_action) or {}).get("args") or {}).get(wf.detail_arg) or {}
        # A path inside the workspace needs no list run: the action is read-only and
        # the path cannot leave the workspace. This also lets a link to a run that
        # has only just started (not yet listed) open at once.
        if decl.get("type") == "path":
            p = value if os.path.isabs(value) else os.path.join(workspace, value)
            real = os.path.realpath(p)
            if inside(real, workspace):
                return real
        if value in self._item_ids(wf, workspace):
            return value
        raise KeyError(value)

    def detail_doc(self, wf, workspace, value):
        value = self.detail_value(wf, workspace, value)
        doc = run_view_action(wf, wf.detail_action, workspace, {wf.detail_arg: value},
                              timeout=self.timeout, client=self.client)
        with _LOCK:
            _LAST[("detail", wf.id, workspace, value)] = (time.time(), doc)
        return doc

    def _doc_paths(self, wf, workspace, value):
        with _LOCK:
            hit = _LAST.get(("detail", wf.id, workspace, value))
        doc = hit[1] if hit else self.detail_doc(wf, workspace, value)
        out = []
        for p in doc.get("paths") or []:
            if isinstance(p, dict) and isinstance(p.get("path"), str) and os.path.isabs(p["path"]):
                out.append(os.path.realpath(p["path"]))
        return out

    def readable(self, wf, workspace, value, path):
        """The resolved file when a page may show it (the extension's readTail roots), else KeyError."""
        value = self.detail_value(wf, workspace, value)
        if not path or "\0" in path or not os.path.isabs(path):
            raise KeyError(path)
        real = os.path.realpath(path)
        roots = [workspace] + self._doc_paths(wf, workspace, value)
        if not any(inside(real, r) for r in roots):
            raise KeyError(path)
        if not os.path.isfile(real) or not real.lower().endswith(TEXT_EXT):
            raise KeyError(path)
        return real


def tail(real):
    """The last lines of a file, as the extension's readTail reads them."""
    size = os.path.getsize(real)
    n = min(size, TAIL_BYTES)
    with open(real, "rb") as fh:
        fh.seek(size - n)
        lines = fh.read(n).decode("utf-8", "replace").split("\n")
    if n < size:
        lines = lines[1:]
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines[-TAIL_LINES:])


def head_or_tail(real):
    """A file to show whole: its start, or for a log its end, at most OPEN_BYTES."""
    size = os.path.getsize(real)
    with open(real, "rb") as fh:
        if size > OPEN_BYTES and real.lower().endswith((".log", ".jou", ".out", ".err")):
            fh.seek(size - OPEN_BYTES)
        return fh.read(OPEN_BYTES).decode("utf-8", "replace")
