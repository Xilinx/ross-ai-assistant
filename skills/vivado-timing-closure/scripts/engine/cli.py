#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
cli.py -- `tc`, the one interface everything else wraps.

The CLI is the API. The MCP server, the HTTP endpoint and any IDE panel are
thin adapters over these verbs, never alternatives to them, because shell
execution is the only capability every agent and every CI runner already has.
That is what makes the judgement artifact portable: SKILL.md can speak `tc`
without naming an IDE, an MCP server or an MCP tool.

Two families of verb:

* **Pass-through** -- every existing `orchestrate.py` command, forwarded
  verbatim (argv, exit code, stdout). The deterministic layer keeps owning
  every engineering decision; `tc` must never reinterpret one. See risk R5 in
  ENGINE_PLAN.md.

* **Engine** -- `run`, `serve`, `query`, `decisions`, `judge`, which are the
  work the agent used to do by holding Vivado MCP sessions open.

The five verbs an agent is expected to use are `status`, `decisions`,
`evidence`, `query` and `docs`. Everything else is engine-internal.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

from . import preflight
from . import watch_triage
from . import (backends, config, decisions, docs, host_setup, jsonio, judge, launch,
               mcp_discover, paths, query, serve, staging, token_usage, view, watch)
from .web import control as web_control

# Forwarded to orchestrate.py untouched. Keep in step with its subparsers.
ORCHESTRATE_VERBS = (
    "init", "probe-collect", "stage1-collect", "stage2-plan",
    "stage2-generate", "stage2-collect", "stage3-plan", "stage3-collect",
    "finalize", "status", "run-status", "prepare-retry", "wait-run",
    "relocate", "batch-plan", "planner-start", "planner-context",
    "planner-validate", "planner-apply", "planner-collect",
)

ENGINE_VERBS = ("run", "serve", "query", "decisions", "judge", "docs", "wait",
                "snapshot", "attach", "detach", "guard", "doctor", "setup", "runs", "view",
                "migrate", "extend", "batch", "watch")


# ---------------------------------------------------------------------------
# pass-through
# ---------------------------------------------------------------------------
def passthrough(argv, stdout=None):
    """Run orchestrate.py with argv verbatim and return its exit code."""
    try:
        cmd = paths.orchestrate_cmd() + list(argv)
    except paths.SkillNotFound as exc:
        print("tc: %s" % exc, file=sys.stderr)
        return 2
    return subprocess.call(cmd, stdout=stdout)


def orchestrate_json(argv):
    """Run an orchestrate.py command that prints JSON and parse it."""
    cmd = paths.orchestrate_cmd() + list(argv)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    out, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("%s failed (rc=%d): %s"
                           % (" ".join(argv), proc.returncode,
                              (err or out or "").strip()[:500]))
    try:
        return json.loads(out)
    except ValueError:
        raise RuntimeError("%s did not print JSON: %s"
                           % (" ".join(argv), (out or "").strip()[:200]))


# ---------------------------------------------------------------------------
# tc run
# ---------------------------------------------------------------------------
def _backend_name(args, cfg, out=None):
    """The backend for this command; `auto` resolves to the design's record."""
    name = args.backend or config.get(cfg, "backend", "default", default="auto")
    rec = None
    if name == "auto":
        rec = launch.for_design(cfg, out) if out else launch.resolve(cfg)
        name = rec["backend"]
    return name, rec


def _backend_for(args, cfg):
    # output_dir lets a backend keep state of its own beside the flow; the
    # session backend isolates its MCP server's state directory there so it
    # cannot see (or adopt) an editor's sessions.
    out = getattr(args, "output_dir", None) or getattr(args, "run_dir", None)
    name, rec = _backend_name(args, cfg, out)
    block = launch.backend_block(cfg, rec) if rec else dict(cfg.get("backend") or {})
    mcp_bin = getattr(args, "mcp_bin", None)
    if mcp_bin:
        # Pinning the server build per batch matters for the same reason
        # pinning the Vivado build does: a daily that has since been pruned
        # cannot be reproduced. Overrides config so a run records what it used.
        block["session"] = dict(block.get("session") or {},
                                mcp_command=mcp_bin)
    try:
        backend = backends.make(name, block, output_dir=out)
    except ValueError as exc:
        raise SystemExit("tc: %s" % exc)
    if name == "session" and out and launch.lsf_available(cfg):
        # The MCP server only launches; if it cannot be started, this design's
        # new runs continue through bsub (backends/failover.py).
        from .backends.failover import FailoverBackend
        lsf_block = dict(block)
        backend = FailoverBackend(
            backend, backends.make("lsf", lsf_block),
            lambda job_name: backends.resources_from_config(lsf_block, "lsf",
                                                            job_name=job_name),
            on_failover=lambda reason: launch.record_failover(out, reason))
        saved = launch.load(out) or {}
        if saved.get("failover"):
            backend.degraded = saved["failover"].get("reason") or "failed over earlier"
    return name, backend


def cmd_run_submit(args, cfg):
    run_dir = os.path.abspath(args.run_dir)
    run_tcl = args.run_tcl or os.path.join(run_dir, "run.tcl")
    name, backend = _backend_for(args, cfg)

    existing = backends.load_handle(run_dir)
    if existing and not args.force:
        st = backend.status(existing)
        if st.state not in backends.TERMINAL:
            print(json.dumps({"adopted": True, "job_id": existing.job_id,
                              "backend": existing.backend,
                              "state": st.state}, indent=2))
            return 0

    res = backends.resources_from_config(
        cfg.get("backend") or {}, name,
        job_name=args.job_name or os.path.basename(run_dir))
    try:
        handle = backend.submit(run_dir, run_tcl, res)
    except backends.SubmitError as exc:
        print("tc run submit: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps({"adopted": False, "job_id": handle.job_id,
                      "backend": handle.backend, "run_dir": handle.run_dir,
                      "log": handle.log_path}, indent=2))
    return 0


def cmd_run_status(args, cfg):
    run_dir = os.path.abspath(args.run_dir)
    handle = backends.load_handle(run_dir)
    if handle is None:
        print(json.dumps({"state": "not_submitted", "run_dir": run_dir},
                         indent=2))
        return 0
    _, backend = _backend_for(args, cfg)
    st = backend.status(handle)
    payload = {"run_dir": run_dir, "job_id": handle.job_id,
               "backend": handle.backend}
    payload.update(st.to_dict())
    payload.pop("raw", None)

    if args.classify:
        # The files decide; the backend only supplies the term reason.
        argv = ["run-status", "--run-dir", run_dir, "--json"]
        if st.term_reason:
            argv += ["--lsf-status", st.term_reason]
        if st.exit_code is not None:
            argv += ["--session-exit", str(st.exit_code)]
        try:
            payload["classified"] = orchestrate_json(argv)
        except RuntimeError as exc:
            payload["classify_error"] = str(exc)
    print(json.dumps(payload, indent=2))
    return 0


def cmd_run_kill(args, cfg):
    run_dir = os.path.abspath(args.run_dir)
    handle = backends.load_handle(run_dir)
    if handle is None:
        print("tc run kill: nothing submitted for %s" % run_dir,
              file=sys.stderr)
        return 1
    _, backend = _backend_for(args, cfg)
    backend.kill(handle)
    print(json.dumps({"killed": handle.job_id, "run_dir": run_dir}, indent=2))
    return 0


def cmd_run_logs(args, cfg):
    run_dir = os.path.abspath(args.run_dir)
    handle = backends.load_handle(run_dir)
    if handle is None:
        print("tc run logs: nothing submitted for %s" % run_dir,
              file=sys.stderr)
        return 1
    _, backend = _backend_for(args, cfg)
    path = backend.logs(handle)
    if not path or not os.path.isfile(path):
        print("tc run logs: no log at %s" % path, file=sys.stderr)
        return 1
    if args.path_only:
        print(path)
        return 0
    with open(path) as fh:
        sys.stdout.write(fh.read())
    return 0


# ---------------------------------------------------------------------------
# tc decisions
# ---------------------------------------------------------------------------
def cmd_dec_list(args, cfg):
    q = decisions.Queue(args.output_dir)
    q.expire_due()
    recs = q.list(state=decisions.PENDING if args.pending else None,
                  point=args.point)
    if args.json:
        print(json.dumps(recs, indent=2))
        return 0
    if not recs:
        print("No decisions at %s." % q.dir)
        return 0
    print("%-24s %-10s %-10s %-22s %s"
          % ("ID", "POINT", "STATE", "DEADLINE (UTC)", "ANSWERED BY"))
    for r in recs:
        print("%-24s %-10s %-10s %-22s %s"
              % (r["id"], r["point"], r["state"],
                 (r.get("deadline_utc") or "-")[:22],
                 r.get("answered_by") or "-"))
    return 0


def cmd_dec_get(args, cfg):
    q = decisions.Queue(args.output_dir)
    try:
        print(json.dumps(q.get(args.id), indent=2))
    except decisions.DecisionError as exc:
        print("tc decisions get: %s" % exc, file=sys.stderr)
        return 1
    return 0


def cmd_dec_answer(args, cfg):
    q = decisions.Queue(args.output_dir)
    if args.answer_json:
        # For callers with no stdin, such as Vivado MCP's vivado_workflow.
        try:
            answer = json.loads(args.answer_json)
        except ValueError as exc:
            print("tc decisions answer: --answer-json is not JSON (%s)" % exc,
                  file=sys.stderr)
            return 1
    elif args.file:
        answer = jsonio.read(args.file)
        if answer is None:
            print("tc decisions answer: cannot read JSON from %s" % args.file,
                  file=sys.stderr)
            return 1
    else:
        try:
            answer = json.loads(sys.stdin.read())
        except ValueError as exc:
            print("tc decisions answer: stdin is not JSON (%s)" % exc,
                  file=sys.stderr)
            return 1
    try:
        rec = q.answer(args.id, answer, args.answered_by, model=args.model)
    except decisions.ValidationError as exc:
        print("tc decisions answer: rejected: %s" % exc, file=sys.stderr)
        return 3
    except decisions.DecisionError as exc:
        print("tc decisions answer: %s" % exc, file=sys.stderr)
        return 1
    if rec.get("answered_by") == "ide_agent":
        watch.note_activity([args.output_dir], "decision answered")
    print(json.dumps({"id": rec["id"], "state": rec["state"],
                      "answered_by": rec["answered_by"]}, indent=2))
    return 0


def cmd_dec_expire(args, cfg):
    q = decisions.Queue(args.output_dir)
    fired = q.expire_due()
    print(json.dumps({"expired": [r["id"] for r in fired]}, indent=2))
    return 0


def cmd_dec_provenance(args, cfg):
    q = decisions.Queue(args.output_dir)
    print(json.dumps(q.provenance_summary(), indent=2))
    return 0


# ---------------------------------------------------------------------------
# tc judge
# ---------------------------------------------------------------------------
def cmd_judge_answer(args, cfg):
    q = decisions.Queue(args.output_dir)
    try:
        rec = judge.answer(q, args.id, cfg)
    except judge.JudgeUnavailable as exc:
        print("tc judge: %s" % exc, file=sys.stderr)
        return 2
    except judge.JudgeFailed as exc:
        # Not fatal: the decision stays pending and its rule applies at the
        # deadline. The flow is never blocked by a judge that misbehaves.
        print("tc judge: %s" % exc, file=sys.stderr)
        return 1
    except decisions.ValidationError as exc:
        print("tc judge: answer rejected: %s" % exc, file=sys.stderr)
        return 3
    print(json.dumps({"id": rec["id"], "answered_by": rec["answered_by"],
                      "answer": rec["answer"]}, indent=2))
    return 0


def cmd_judge_replay(args, cfg):
    q = decisions.Queue(args.output_dir)
    try:
        print(json.dumps(judge.replay(q, args.id, cfg), indent=2))
    except (judge.JudgeUnavailable, judge.JudgeFailed) as exc:
        print("tc judge replay: %s" % exc, file=sys.stderr)
        return 1
    return 0


def cmd_judge_pending(args, cfg):
    """Answer every pending decision with the headless judge."""
    q = decisions.Queue(args.output_dir)
    q.expire_due()
    done, failed = [], []
    for rec in q.list(state=decisions.PENDING):
        try:
            judge.answer(q, rec["id"], cfg)
            done.append(rec["id"])
        except (judge.JudgeFailed, decisions.ValidationError) as exc:
            failed.append({"id": rec["id"], "error": str(exc)})
        except judge.JudgeUnavailable as exc:
            print("tc judge: %s" % exc, file=sys.stderr)
            return 2
    print(json.dumps({"answered": done, "failed": failed}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# tc mcp
# ---------------------------------------------------------------------------
def cmd_mcp(args, cfg):
    """Show which Vivado MCP server the engine would use, and why."""
    found = mcp_discover.discover(args.dir)
    chosen = next((c for c in found if c.usable()), None)
    if args.json:
        print(json.dumps({"chosen": chosen.to_dict() if chosen else None,
                          "candidates": [c.to_dict() for c in found]},
                         indent=2))
        return 0 if chosen else 1
    if not found:
        print("No Vivado MCP server configured. Looked in .mcp.json, "
              ".vscode/mcp.json, .cursor/mcp.json (upwards from %s), "
              "~/.claude.json and the user-level editor configs."
              % os.path.abspath(args.dir or os.getcwd()))
        return 1
    for c in found:
        mark = "->" if c is chosen else "  "
        print("%s %-4s %s" % (mark, "ok" if c.usable() else "MISS", c.binary))
        print("        from %s" % c.source)
        if c.args:
            print("        inherits %s" % " ".join(c.args))
    print()
    if chosen:
        print("The engine would use the marked one, with --stdio added and "
              "its own state directory.")
        return 0
    print("None of these exist on this host. Pass --mcp-bin <path>.")
    return 1


# ---------------------------------------------------------------------------
# tc query
# ---------------------------------------------------------------------------
def cmd_docs(args, cfg):
    if getattr(args, "action", None) != "search":
        print("tc docs: use `tc docs search <query>`", file=sys.stderr)
        return 2
    url, source = docs.endpoint(cfg)
    q = " ".join(args.query)
    try:
        text = docs.search(q, limit=max(1, min(50, args.limit)), family=args.family,
                           version=args.version, url=url)
    except docs.DocsError as exc:
        print("tc docs: %s" % exc, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"url": url, "source": source, "query": q, "text": text}, indent=2))
    else:
        print(text or "(no results)")
    return 0


class QueryLocalRefused(Exception):
    pass


def query_backend(cfg, where=None, run_dir=None):
    """
    (name, submit) for `tc query`. On the farm whenever the flow runs there: a
    routed checkpoint can take 10-30 GB, and the agent's own host is usually a
    shared login machine. With backend session it runs as a Vivado MCP LSF
    session, listed with the flow's runs; with backend lsf, as a bsub job.
    `query.backend` (or --where) overrides: local | lsf | session. Resources
    default to the flow's, with `query.memory_mb` / `query.slots` /
    `query.queue` taking precedence.
    """
    flow = config.get(cfg, "backend", "default", default="local")
    site = config.get(cfg, "query", "backend", default="auto")
    name = where or site
    if name == "auto":
        name = {"session": "session", "lsf": "lsf"}.get(flow, "local")
    if name == "local" and where == "local" and site != "local" and \
            flow in ("lsf", "session"):
        # The site runs Vivado on the farm; an agent may not move a query onto
        # this (usually shared login) host by itself -- one did, after the
        # farm queries failed. Only `query.backend: local` in the site config
        # allows it.
        raise QueryLocalRefused(
            "this site runs Vivado on the farm (backend %s); --where local is refused. "
            "If a farm query fails, report the error instead." % flow)
    if name == "local":
        return "local", None
    lsf = config.get(cfg, "backend", "lsf", default={}) or {}
    q = config.get(cfg, "query", default={}) or {}
    direct = query.lsf_submitter(
        queue=q.get("queue") or lsf.get("queue"),
        memory_mb=q.get("memory_mb") or lsf.get("memory_mb"),
        slots=q.get("slots") or 2,
        os_type=q.get("os_type") or lsf.get("os_type"),
        env_script=lsf.get("env_script"))
    if name != "session":
        return "lsf", direct
    block = dict(cfg.get("backend") or {})
    res = backends.resources_from_config(block, "session")
    res.queue = q.get("queue") or res.queue
    res.memory_mb = q.get("memory_mb") or res.memory_mb
    res.slots = q.get("slots") or 2
    if q.get("os_type"):
        res.os_type = str(q["os_type"]).split("==", 1)[-1].strip()
    root = _flow_root(run_dir) if run_dir else None
    return "session", query.session_submitter(
        lambda: backends.make("session", block, output_dir=root or run_dir),
        res, fallback=direct)


def _flow_root(path, depth=6):
    """The flow output directory (the one holding state.json) a path is in."""
    d = os.path.abspath(path)
    for _ in range(depth):
        if os.path.isfile(os.path.join(d, "state.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def cmd_query(args, cfg):
    commands = list(args.tcl or [])
    if args.file:
        with open(args.file) as fh:
            commands += [ln.strip() for ln in fh if ln.strip()
                         and not ln.lstrip().startswith("#")]
    if not commands:
        print("tc query: give at least one --tcl or a --file",
              file=sys.stderr)
        return 2

    if args.check_only:
        problems = []
        for c in commands:
            try:
                query.check(c)
            except query.QueryRefused as exc:
                problems.append({"command": c, "refused": str(exc)})
        print(json.dumps({"ok": not problems, "refused": problems}, indent=2))
        return 0 if not problems else 3

    try:
        where, submit = query_backend(cfg, args.where, args.run_dir)
    except QueryLocalRefused as exc:
        print("tc query: refused: %s" % exc, file=sys.stderr)
        return 3
    try:
        results = query.run(
            args.dcp, commands,
            vivado=config.get(cfg, "backend", "vivado", default="vivado"),
            run_dir=args.run_dir, cwd=args.run_dir,
            timeout=args.timeout, submit=submit)
    except query.QueryRefused as exc:
        print("tc query: refused: %s" % exc, file=sys.stderr)
        return 3
    except query.QueryError as exc:
        print("tc query: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2))
    return 0


# ---------------------------------------------------------------------------
# tc serve
# ---------------------------------------------------------------------------
_SERVE_EXIT = {
    serve.COMPLETE: 0,
    serve.STOPPED: 0,
    serve.NEEDS_ATTENTION: 4,
    serve.BUDGET_SPENT: 5,
}


# orchestrate.py init: the checkpoint is not an AMD Versal device
# (orchestrate.EXIT_UNSUPPORTED_DEVICE; the engine never imports orchestrate).
UNSUPPORTED_DEVICE_EXIT = 6

PID_FILE = "serve.pid"
SERVE_LOG = "serve.log"


# ---------------------------------------------------------------------------
# tc close -- the whole thing, in one command
# ---------------------------------------------------------------------------
def _warn(msg):
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# tc web -- the run's live page (engine/web)
# ---------------------------------------------------------------------------
def cmd_web(args, cfg):
    """
    The run pages: the Workflows view's renderer in a browser, behind a secret
    link. start registers a workspace and starts the server when it is not
    running; url prints the link to one run (--output-dir) or to the list.
    """
    w = web_control.settings(cfg)
    port = args.port or w["port"]
    bind = args.bind or w["bind"]
    host = args.host or w["host"]
    try:
        if args.action == "start":
            if args.register or w["workflow_dirs"]:
                web_control.register(args.register, w["workflow_dirs"])
            out = web_control.start(port, bind, host)
            lines = ["tc web: %s on %s:%d (pid %s)" % ("started" if out.get("started_now") else "running",
                                                     bind, port, out.get("pid")),
                     "open: %s" % out.get("url")]
        elif args.action == "status":
            out = web_control.status(port)
            lines = ["tc web: %s on port %d%s" % ("running" if out["running"] else "not running", port,
                                                  " (pid %s)" % out["pid"] if out["running"] else "")]
            lines += ["  workspace: %s" % ws for ws in out["workspaces"]]
            if out.get("url"):
                lines.append("open: %s" % out["url"])
        elif args.action == "stop":
            out = web_control.stop(port)
            lines = ["tc web: %s" % ("stopped pid %s" % out["stopped"] if out.get("stopped") else "not running")]
        else:
            if args.output_dir:
                url = web_control.run_url(args.output_dir, port, host)
            else:
                url = web_control.list_url(port, host)
            out = {"url": url, "running": web_control.status(port)["running"]}
            lines = [url] + ([] if out["running"] else ["(the server is not running: tc web start)"])
    except (web_control.WebError, OSError) as exc:
        print("tc web: %s" % exc, file=sys.stderr)
        return 1
    _emit(out, args.json, lines)
    return 0


def cmd_close(args, cfg):
    """
    Set up a flow for a checkpoint and start supervising it.

    This exists because of how the previous system failed. Its stage ordering
    and "don't stop early" rules lived in prose that an agent was asked to
    follow, and it did not: one recorded run launched two multi-hour Stage 2
    runs after Stage 1 had already closed timing. The fix then was to move
    enforcement into code, and the same reasoning applies to starting a run.

    Every step an agent has to sequence correctly is a step it can get wrong.
    So there is one command: it derives the output directory, initialises if
    needed, and detaches the supervisor. Re-running it attaches to what is
    already there rather than starting a second one.
    """
    dcp = os.path.abspath(args.dcp)
    if not os.path.isfile(dcp):
        print("tc close: no checkpoint at %s" % dcp, file=sys.stderr)
        return 1

    # Farm jobs read the checkpoint, write the run folders and source the
    # skill's Tcl by these paths: with stage_root set, all three move to a
    # shared folder (see staging.py).
    staged_from = assets = None
    try:
        root = staging.root_for(cfg, args.backend or config.get(cfg, "backend", "default",
                                                                default="auto"))
        if root:
            if args.output_dir and not staging.under(args.output_dir, root):
                print("tc close: --output-dir %s is outside stage_root %s; farm jobs need "
                      "it on the shared folder (leave --output-dir out to use one beside "
                      "the staged checkpoint)" % (args.output_dir, root), file=sys.stderr)
                return 1
            dcp, staged_from = staging.stage_dcp(dcp, root)
            if staged_from:
                print("tc close: checkpoint staged for the farm: %s" % dcp, file=sys.stderr)
            # Every orchestrate step and the detached supervisor inherit this,
            # so each run.tcl sources the staged copy, never the local skill.
            assets = staging.stage_assets(os.path.join(paths.skill_root(), "assets"), root)
            staging.use_assets(assets)
    except (staging.StagingError, paths.SkillNotFound) as exc:
        print("tc close: %s" % exc, file=sys.stderr)
        return 1
    staged = {}
    if staged_from:
        staged.update(dcp=dcp, staged_from=staged_from)
    if assets:
        staged["assets_root"] = assets
    args.staged = staged or None

    out = args.output_dir
    if not out:
        base = os.path.basename(dcp)
        for ext in (".dcp", ".DCP"):
            if base.endswith(ext):
                base = base[:-len(ext)]
                break
        out = os.path.join(os.path.dirname(dcp), "%s_tc" % base)
    out = os.path.abspath(out)

    running = _serve_pid(out)
    if running:
        res = {"already_running": True, "pid": running, "output_dir": out,
               "watch": "tc status --output-dir %s --json" % out}
        if args.staged:
            res.update(args.staged)
        web_url = web_control.ensure_for_run(cfg, out, log=_warn)
        if web_url:
            res["web_url"] = web_url
        if getattr(args, "attach", False):
            res["attached"] = _attach_here(out)
        print(json.dumps(res, indent=2))
        return 0
    # --json: stdout carries one JSON object and nothing else, so a caller
    # that parses it (Vivado MCP's vivado_workflow) gets the result rather
    # than text. init's own report goes to stderr.
    init_out = sys.stderr if getattr(args, "json", False) else None

    # init is idempotent and refuses a different DCP, so re-running close on
    # an existing flow resumes it instead of resetting it.
    # `auto` is decided here, once per design, and recorded in backend.json
    # for every later supervisor start.
    cfg, stop = _vivado_for_run(cfg)
    if stop is not None:
        return stop
    name, rec = _backend_name(args, cfg, out)
    if rec:
        print("tc close: launching runs via %s -- %s" % (name, rec["reason"]),
              file=sys.stderr)
    problem = check_vivado(cfg, name)
    if problem:
        print("tc close: %s" % problem, file=sys.stderr)
        return 1
    stop = _preflight("close", cfg, name, rec)
    if stop is not None:
        return stop

    state = os.path.join(out, "state.json")
    if not os.path.isfile(state):
        design = args.design or _design_name(dcp)
        init = ["init", "--output-dir", out, "--dcp", dcp, "--design", design]
        # One short job opens the checkpoint with the pinned build before the
        # multi-hour Stage 1 runs do: the 2026.2 daily crashed opening 13 of 14 TSR
        # checkpoints, and three SSI designs never opened on 2026.2 at all.
        # Not again when a flow beside this one already did, for this file and
        # this Vivado version.
        reused = None
        if not args.no_probe and config.get(cfg, "serve", "probe", default=True):
            reused = _probe_from_sibling(out, dcp, cfg)
            if not reused:
                init.append("--probe")
        for flag, _ in INIT_HOOKS:
            for cmd in getattr(args, flag[2:].replace("-", "_"), None) or []:
                init += [flag, cmd]
        sys.stdout.flush()
        rc = passthrough(init, stdout=init_out)
        if rc == UNSUPPORTED_DEVICE_EXIT:
            print("tc close: refused: not an AMD Versal device (see above); nothing was "
                  "launched", file=sys.stderr)
            return rc
        if rc != 0:
            print("tc close: init failed (rc=%d)" % rc, file=sys.stderr)
            return rc
        if reused:
            st = jsonio.read(state) or {}
            st["probe_result"] = dict(reused[1], reused_from=reused[0])
            jsonio.write(state, st)
            print("tc close: build probe skipped: %s already opened this checkpoint with "
                  "Vivado %s" % (os.path.basename(reused[0]), reused[1].get("vivado")),
                  file=sys.stderr)
        if staged_from:
            st = jsonio.read(state) or {}
            st["staged_from"] = staged_from
            jsonio.write(state, st)
    if assets:
        # Recorded so a restarted supervisor, tc watch --restart-dead and tc
        # extend generate their runs against the same staged copy.
        st = jsonio.read(state) or {}
        if st.get("assets_root") != assets:
            st["assets_root"] = assets
            jsonio.write(state, st)

    args.detach = True
    args.stop = False
    args.output_dir = out
    if getattr(args, "attach", False):
        # Before the supervisor detaches: once it forks, this process is gone.
        args.attach_lease = _attach_here(out)
    return cmd_serve(args, cfg)


def read_designs(path):
    """
    [(name, dcp)] from a design list: one `name,dcp` (or just `dcp`) per line;
    blank lines and `#` comments skipped. Raises ValueError on a repeated name.
    """
    out, seen = [], set()
    with open(path) as fh:
        for n, line in enumerate(fh, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if "," in line:
                name, dcp = (x.strip() for x in line.split(",", 1))
            else:
                dcp = line
                name = _design_name(os.path.abspath(dcp))
            if not name or not dcp:
                raise ValueError("%s:%d: expected name,dcp" % (path, n))
            if name in seen:
                raise ValueError("%s:%d: design %r appears twice" % (path, n, name))
            seen.add(name)
            out.append((name, dcp))
    return out


def cmd_batch(args, cfg):
    """
    `tc close` for every design in a list, in one call: one run per design under
    --output-root. An agent starting 101 designs one `start` at a time spent 102
    calls and over half of everything the tools returned on it (the Rubik batch).
    Starting is cheap (under a second each); the supervisors do the rest.
    """
    designs_path = os.path.abspath(args.designs)
    try:
        designs = read_designs(designs_path)
    except (IOError, OSError, ValueError) as exc:
        print("tc batch: %s" % exc, file=sys.stderr)
        return 1
    if args.only:
        wanted = set(args.only)
        designs = [d for d in designs if d[0] in wanted]
        missing = sorted(wanted - set(n for n, _ in designs))
        if missing:
            print("tc batch: not in %s: %s" % (designs_path, ", ".join(missing)), file=sys.stderr)
            return 1
    root = os.path.abspath(args.output_root or os.path.join(os.path.dirname(designs_path), "runs"))
    # With stage_root (farm backends), every design's flow must live on the
    # shared folder: default the batch there, and refuse an output root outside it.
    try:
        stage = staging.root_for(cfg, getattr(args, "backend", None) or
                                 config.get(cfg, "backend", "default", default="auto"))
    except staging.StagingError as exc:
        print("tc batch: %s" % exc, file=sys.stderr)
        return 1
    if stage:
        if args.output_root and not staging.under(args.output_root, stage):
            print("tc batch: --output-root %s is outside stage_root %s; farm jobs need it "
                  "on the shared folder" % (args.output_root, stage), file=sys.stderr)
            return 1
        if not args.output_root:
            stem = os.path.splitext(os.path.basename(designs_path))[0]
            root = os.path.join(stage, "%s-runs" % stem)
    entry = os.path.abspath(sys.argv[0]) if sys.argv and os.path.isfile(sys.argv[0]) else \
        os.path.join(paths.skill_root(), "tc")
    base_dir = os.path.dirname(designs_path)
    started, resumed, failed = [], [], []
    for name, dcp in designs:
        dcp = dcp if os.path.isabs(dcp) else os.path.join(base_dir, dcp)
        argv = [sys.executable, entry, "close", "--dcp", dcp, "--design", name,
                "--output-dir", os.path.join(root, name), "--json", "--attach"]
        if getattr(args, "backend", None):
            argv += ["--backend", args.backend]
        if getattr(args, "mcp_bin", None):
            argv += ["--mcp-bin", args.mcp_bin]
        if args.no_probe:
            argv.append("--no-probe")
        if args.concurrency is not None:
            argv += ["--concurrency", str(args.concurrency)]
        if args.judge:
            argv += ["--judge", args.judge]
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)
        if proc.returncode == preflight.NEEDS_EXIT:
            # The setup is incomplete: nothing of this design started, and none
            # of the rest will either. Pass the question on as `close` would.
            sys.stdout.write(proc.stdout)
            sys.stderr.write(proc.stderr)
            return preflight.NEEDS_EXIT
        try:
            res = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        except ValueError:
            res = {}
        if proc.returncode != 0:
            why = [l for l in (proc.stderr or proc.stdout).strip().splitlines() if l.strip()]
            failed.append({"design": name, "error": (why[-1] if why else "exit %d" % proc.returncode)[:300]})
        elif res.get("already_running"):
            resumed.append(name)
        else:
            started.append(name)
    out = {"designs": len(designs), "started": len(started), "already_running": len(resumed),
           "failed": failed, "output_root": root,
           "next": "wait_all (tc wait --all --compact): it returns when any run needs you"}
    w = web_control.settings(cfg)
    if w["enabled"] and (started or resumed):
        # Each design's own link is in its tc status; this one lists them all.
        try:
            if web_control.status(w["port"])["running"]:
                out["web_url"] = web_control.list_url(w["port"], w["host"])
        except (web_control.WebError, OSError):
            pass
    print(json.dumps(out, indent=None if args.json else 2))
    return 1 if failed and not (started or resumed) else 0


def _vivado_version(path):
    """`.../2026.1/Vivado/bin/vivado` -> '2026.1', or None."""
    found = re.findall(r"(?<![0-9])(20[0-9]{2}\.[0-9])(?![0-9])", path or "")
    return found[-1] if found else None


def _probe_from_sibling(out, dcp, cfg):
    """
    (run dir, probe result) of a flow beside `out` whose build probe opened
    this same checkpoint (unchanged since) with this same Vivado version, else
    None. The probe only proves the build can open the file, so one pass per
    checkpoint and version is enough.
    """
    version = _vivado_version(host_setup.configured_vivado(cfg) or "")
    if not version:
        return None
    try:
        dcp_mtime = os.path.getmtime(dcp)
    except OSError:
        return None
    parent = os.path.dirname(out)
    for name in sorted(os.listdir(parent)) if os.path.isdir(parent) else []:
        d = os.path.join(parent, name)
        if d == out or not os.path.isfile(os.path.join(d, "state.json")):
            continue
        st = jsonio.read(os.path.join(d, "state.json")) or {}
        probe = st.get("probe_result") or {}
        done = os.path.join(d, "probe", "run.done")
        if (os.path.realpath(st.get("dcp") or "") == os.path.realpath(dcp)
                and probe.get("vivado") == version and probe.get("part")
                and os.path.isfile(done) and os.path.getmtime(done) >= dcp_mtime):
            return d, {k: v for k, v in probe.items() if k != "reused_from"}
    return None


# ---------------------------------------------------------------------------
# tc extend -- add work to a run that exists
# ---------------------------------------------------------------------------
_IDEA_NAME = re.compile(r"[^a-z0-9_]+")
_FLOW_KEYS = ("opt_directive", "place_directive", "place_subdirective", "physopt_directive",
              "route_directive")


def _xdc_lines(path):
    with open(path) as fh:
        return [l.strip() for l in fh.read().splitlines()
                if l.strip() and not l.strip().startswith("#")]


def ideas_plan(ideas, placers=None, early_fixes=None):
    """
    A planner plan from simple ideas: [{name, xdc | xdc_lines, placers?,
    early_fixes?, <flow directives>?, justification?}]. Each becomes a flow
    fix; --placers and --early-fix apply to ideas that set none of their own.
    """
    fixes = []
    for i, idea in enumerate(ideas, 1):
        name = _IDEA_NAME.sub("_", str(idea.get("name") or "idea%d" % i).lower()).strip("_")[:40]
        params = {}
        lines = idea.get("xdc_lines") or (_xdc_lines(idea["xdc"]) if idea.get("xdc") else [])
        if lines:
            params["xdc_lines"] = lines
        for k in _FLOW_KEYS:
            if idea.get(k):
                params[k] = idea[k]
        fix = {"rank": i, "name": name or "idea%d" % i, "kind": "flow",
               "justification": idea.get("justification") or
               "Asked for: try %s on this run rather than a new flow." % name,
               "evidence_ids": ["U1"], "params": params}
        if idea.get("placers") or placers:
            fix["placers"] = list(idea.get("placers") or placers)
        if idea.get("early_fixes") or early_fixes:
            fix["early_fixes"] = dict(idea.get("early_fixes") or early_fixes)
        fixes.append(fix)
    return {"schema_version": 1,
            "summary": "Ideas to try on an existing run, as asked for (tc extend).",
            "evidence": [{"id": "U1", "finding": "asked for by the user or the agent",
                          "source": "tc extend"}],
            "fixes": fixes}


def _parse_early(items):
    out = {}
    for item in items or []:
        key, sep, val = item.partition("=")
        if not sep or val not in ("0", "1", "true", "false"):
            raise ValueError("--early-fix wants KEY=0|1, got %r" % item)
        out[key] = val in ("1", "true")
    return out


def cmd_extend(args, cfg):
    """
    Add work to a run that exists instead of starting a new flow: every result
    so far stays, one more round runs from the overall best (or --from a run),
    and the report is rebuilt. Five ideas once tried as five separate flows
    (about 130 jobs) are about 5-10 runs this way.
    """
    out = os.path.abspath(args.output_dir)
    if not os.path.isfile(os.path.join(out, "state.json")):
        print("tc extend: no timing-closure run at %s" % out, file=sys.stderr)
        return 1
    running = _serve_pid(out)
    if running:
        print("tc extend: the supervisor is still running this flow (pid %d); extend it "
              "once it has finished" % running, file=sys.stderr)
        return 1
    try:
        early = _parse_early(args.early_fix)
    except ValueError as exc:
        print("tc extend: %s" % exc, file=sys.stderr)
        return 2
    placers = [x.strip() for x in (args.placers or "").split(",") if x.strip()]
    plan_path = args.plan
    tmp_plan = None
    if args.ideas or args.idea:
        ideas = []
        if args.ideas:
            with open(args.ideas) as fh:
                data = json.load(fh)
            ideas += data if isinstance(data, list) else data.get("ideas") or []
        for item in args.idea or []:
            name, sep, xdc = item.partition("=")
            ideas.append({"name": name, "xdc": os.path.abspath(xdc)} if sep else {"name": name})
        if plan_path:
            print("tc extend: give --plan or ideas (--ideas/--idea), not both", file=sys.stderr)
            return 2
        tmp_plan = os.path.join(out, ".extend_plan.json")
        jsonio.write(tmp_plan, ideas_plan(ideas, placers, early))
        plan_path = tmp_plan
    elif placers or early:
        print("tc extend: --placers and --early-fix go with ideas (--ideas/--idea)",
              file=sys.stderr)
        return 2

    # Nothing changes until the runs could start.
    if not args.no_serve:
        cfg, stop = _vivado_for_run(cfg)
        if stop is not None:
            return stop
        name, rec = _backend_name(args, cfg, out)
        problem = check_vivado(cfg, name)
        if problem:
            print("tc extend: %s" % problem, file=sys.stderr)
            return 1
        stop = _preflight("extend", cfg, name, rec)
        if stop is not None:
            return stop

    argv = ["extend", "--output-dir", out]
    if plan_path:
        argv += ["--plan", os.path.abspath(plan_path)]
    if args.from_run:
        argv += ["--from", args.from_run]
    if args.max_runs:
        argv += ["--max-runs", str(args.max_runs)]
    if getattr(args, "restore", False):
        argv.append("--restore")
    sys.stdout.flush()
    try:
        rc = passthrough(argv, stdout=sys.stderr if args.json else None)
    finally:
        if tmp_plan and os.path.isfile(tmp_plan):
            os.unlink(tmp_plan)
    if rc != 0:
        print("tc extend: nothing was started (rc=%d)" % rc, file=sys.stderr)
        return rc
    if args.no_serve:
        dcp = (jsonio.read(os.path.join(out, "state.json")) or {}).get("dcp") or "<checkpoint>"
        print(json.dumps({"extended": True, "output_dir": out, "started": False,
                          "next": "tc close --dcp %s --output-dir %s starts its runs" % (dcp, out)},
                         indent=2))
        return 0
    args.detach = True
    args.stop = False
    args.output_dir = out
    if getattr(args, "attach", False):
        args.attach_lease = _attach_here(out)
    return cmd_serve(args, cfg)


def _attach_here(out):
    """Lease the run to the workspace we run in, so a stop guard holds the turn."""
    watch.attach(out, workspace=os.getcwd())
    return True


# orchestrate.py init's user TCL hooks (the upstream framework's six points).
INIT_HOOKS = (("--extra-cmd", "after open_checkpoint"),
              ("--post-opt-cmd", "after opt_design"),
              ("--post-place-cmd", "after place_design"),
              ("--post-popt-cmd", "after post-place phys_opt_design"),
              ("--post-route-cmd", "after route_design"),
              ("--post-route-popt-cmd", "after post-route phys_opt_design"))


def _design_name(dcp):
    """
    The design name for a checkpoint: its directory's name, unless that is a
    generic holding folder, in which case the file's own name. Left to init, a
    checkpoint in designs/ was recorded as design "designs".
    """
    parent = os.path.basename(os.path.dirname(dcp))
    stem = os.path.basename(dcp)
    for ext in (".dcp", ".DCP"):
        if stem.endswith(ext):
            stem = stem[:-len(ext)]
    generic = {"designs", "design", "dcp", "dcps", "inputs", "input", "checkpoints",
               "impl_1", "synth_1", "", "."}
    if parent.lower() in generic:
        return stem
    # A checkpoint at the workspace root: the folder is the workspace, not the
    # design (a run there was recorded as design "extension-workflow-testing-dcp").
    folder = os.path.dirname(os.path.abspath(dcp))
    if folder == os.getcwd() or any(os.path.isdir(os.path.join(folder, m))
                                    for m in (".tc", ".git", ".vscode", ".cursor")):
        return stem
    return parent


def check_vivado(cfg, backend_name):
    """
    None when the configured Vivado can be launched, else what to fix.

    Checked before anything is submitted: the first run started from VS Code
    used the local backend with `vivado` not on PATH, and every Stage 1 run died
    with exit 127 within seconds while the supervisor kept submitting more.
    """
    if backend_name not in ("local", "lsf"):
        # session: the MCP server launches it, but a path someone named must exist.
        named = host_setup.configured_vivado(cfg)
        if named and not (os.path.isfile(named) and os.access(named, os.X_OK)):
            return "backend.vivado=%s is not an executable file" % named
        return None
    vivado = config.get(cfg, "backend", "vivado", default="vivado")
    if os.path.isabs(vivado):
        if os.path.isfile(vivado) and os.access(vivado, os.X_OK):
            return None
        return "backend.vivado=%s is not an executable file" % vivado
    from shutil import which             # Python 3.3+
    if which(vivado):
        return None
    where = config.workspace_config() or config.config_path()
    return ("`%s` is not on PATH, so every run would fail at once. Set backend.vivado "
            "to the full path of the build to use, in %s (e.g. "
            "/opt/Xilinx/2026.1/Vivado/bin/vivado)" % (vivado, where))


def _serve_pid(output_dir):
    """The pid of a supervisor already running here, or None."""
    path = os.path.join(output_dir, PID_FILE)
    try:
        with open(path) as fh:
            pid = int((fh.read() or "").strip())
    except (IOError, OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def cmd_serve_stop(args):
    out = os.path.abspath(args.output_dir)
    pid = _serve_pid(out)
    if pid is None:
        print("tc serve: nothing running for %s" % out, file=sys.stderr)
        return 1
    os.kill(pid, signal.SIGTERM)
    # The loop finishes its current tick before it exits. Wait for that, so a
    # `tc close` straight after does not attach to a supervisor that is about
    # to vanish (it did, once, and left the run unsupervised).
    import time
    for _ in range(240):
        if _serve_pid(out) != pid:
            break
        time.sleep(0.5)
    # The supervisor leaves submitted jobs alone by design; only the loop
    # stops. Restarting adopts them from their job handles.
    print(json.dumps({"stopped": pid, "exited": _serve_pid(out) != pid,
                      "note": "submitted runs keep going"}, indent=2))
    return 0


def _detach(output_dir):
    """
    Become a daemon, so the supervisor outlives the shell that started it.

    An agent's terminal tool returns when the command does, and may kill what
    it backgrounded. A six-hour sweep cannot depend on that, and `tc serve`
    already keeps its jobs alive across its own restarts -- so the only piece
    missing was a first-class way to start it unattached.
    """
    log_path = os.path.join(output_dir, SERVE_LOG)
    if os.fork():                       # parent
        os._exit(0)
    os.setsid()
    if os.fork():                       # intermediate
        os._exit(0)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    with open(os.path.join(output_dir, PID_FILE), "w") as fh:
        fh.write("%d\n" % os.getpid())


def cmd_serve(args, cfg):
    out = os.path.abspath(args.output_dir)
    if getattr(args, "stop", False):
        return cmd_serve_stop(args)

    running = _serve_pid(out)
    if running:
        print("tc serve: already running for %s (pid %d); stop it with "
              "--stop" % (out, running), file=sys.stderr)
        return 1

    if getattr(args, "detach", False):
        problem = check_vivado(cfg, _backend_name(args, cfg, out)[0])
        if problem:
            print("tc serve: %s" % problem, file=sys.stderr)
            return 1
        if not os.path.isdir(out):
            os.makedirs(out)
        try:
            os.unlink(os.path.join(out, watch.OUTCOME_FILE))
        except OSError:
            pass
        log_path = os.path.join(out, SERVE_LOG)
        res = {"detached": True, "output_dir": out, "log": log_path,
               "watch": "tc status --output-dir %s --json" % out}
        if getattr(args, "staged", None):
            res.update(args.staged)
        web_url = web_control.ensure_for_run(cfg, out, log=_warn)
        if web_url:
            res["web_url"] = web_url
        if getattr(args, "attach_lease", None):
            res["attached"] = True
        print(json.dumps(res, indent=2))
        sys.stdout.flush()
        _detach(out)

    name, backend = _backend_for(args, cfg)
    problem = check_vivado(cfg, name)
    if problem:
        print("tc serve: %s" % problem, file=sys.stderr)
        return 1
    web_url = web_control.ensure_for_run(cfg, out, log=_warn)
    if web_url:
        print("tc serve: live page for this run: %s" % web_url, file=sys.stderr)
    if not backend.available():
        print("tc serve: backend %r is not usable on this host" % name,
              file=sys.stderr)
        return 1
    server = serve.Server(args.output_dir, backend, cfg=cfg,
                          concurrency=args.concurrency,
                          on_run_failure=args.on_run_failure,
                          judge=args.judge)
    server.install_signal_handlers()
    outcome = server.serve(max_ticks=args.max_ticks, sleep=args.poll)
    report = server.report()
    report["outcome"] = outcome
    # Read by `tc wait` / the stop guard: why the supervisor is gone.
    jsonio.write(os.path.join(out, watch.OUTCOME_FILE),
                 dict(report, finished_utc=watch._iso(watch._utc_now())))
    try:
        os.unlink(os.path.join(out, PID_FILE))
    except OSError:
        pass
    print(json.dumps(report, indent=2))
    return _SERVE_EXIT.get(outcome, 1)


# ---------------------------------------------------------------------------
# tc wait / attach / detach / guard -- staying in charge of a long run
# ---------------------------------------------------------------------------
def cmd_wait(args, cfg):
    if getattr(args, "all", False):
        # Every active run under the workspace, no lease needed: what a host
        # calling `tc` per action (vivado_workflow) waits on.
        ws = os.path.abspath(args.workspace or os.getcwd())
        if getattr(args, "fast", False):
            dirs = watch.fast_active_runs(ws, find_runs(ws))
        else:
            dirs = [d for d in find_runs(ws) if watch.is_active(watch.snapshot(d))]
        if not dirs:
            print(json.dumps({"event": "idle", "watching": [], "workspace": ws,
                              "next_step": "No run is active under this workspace. "
                                           "Report any finished run, or start one."},
                             indent=None if args.compact else 2))
            return 0
        snap = watch.wait_many(dirs, timeout_s=args.timeout, poll_s=args.poll,
                               fast=getattr(args, "fast", False))
        if getattr(args, "fast", False) and snap.get("event") in ("supervisor_stopped", "complete"):
            watch.note_reported_stop(ws, snap["output_dir"])
    elif args.attached:
        dirs = watch.attached_runs(args.workspace or os.getcwd())
        if not dirs:
            print("tc wait: no run is attached in this workspace (tc attach "
                  "--output-dir <dir>)", file=sys.stderr)
            return 2
        snap = watch.wait_many(dirs, timeout_s=args.timeout, poll_s=args.poll)
    elif args.output_dir:
        snap = watch.wait(args.output_dir, timeout_s=args.timeout, poll_s=args.poll)
    else:
        print("tc wait: pass --output-dir <dir> or --attached", file=sys.stderr)
        return 2
    print(json.dumps(watch.compact(snap) if args.compact else snap,
                     indent=None if args.compact else 2))
    return 0


def cmd_watch(args, cfg):
    """
    `tc wait --all --fast` that also does, between agent turns, what the agent
    would only repeat: answer triage calls whose answer the evidence fixes and,
    each only behind its own flag, restart a supervisor that died without an
    outcome, free superseded checkpoints when the disk refuses writes, and kill
    farm jobs left behind in retired attempt_* folders. Returns, like `tc wait`,
    only when something needs the agent; `actions` lists what it did meanwhile.
    """
    ws = os.path.abspath(args.workspace or os.getcwd())
    end = time.time() + args.timeout
    actions = []
    # As cmd_batch calls tc for each design: this interpreter and entry script.
    entry = os.path.abspath(sys.argv[0]) if sys.argv and os.path.isfile(sys.argv[0]) else None
    tc_argv = [sys.executable, entry] if entry else [os.path.join(paths.skill_root(), "tc")]
    deleted_log = os.path.join(ws, watch.LEASE_DIR, "watch_deleted_dcps.txt")

    def log(msg):
        print(msg, file=sys.stderr)

    def backend_for(out):
        return _backend_for(argparse.Namespace(output_dir=out, backend=None, mcp_bin=None), cfg)[1]

    def free_if_full(where):
        if not args.free_space or watch_triage.writable(where):
            return
        freed = watch_triage.free_superseded(find_runs(ws), log_path=deleted_log)
        actions.append({"action": "free_space", "freed_gb": round(freed / 1e9, 1),
                        "log": deleted_log})

    def emit(res):
        res = watch.compact(res) if args.compact else res
        res["actions"] = actions
        print(json.dumps(res, indent=None if args.compact else 2))
        return 0

    if watch_triage.session_server_up(cfg) is False:
        return emit({"event": "session_server_down", "workspace": ws,
                     "next_step": "The shared Vivado MCP server in .tc/engine.yaml "
                                  "(backend.session.url) is not listening; new runs cannot "
                                  "launch through it. Ask the user to restart it."})
    if args.kill_strays:
        for k in watch_triage.kill_strays(find_runs(ws), backend_for,
                                          cache_path=os.path.join(ws, watch.LEASE_DIR,
                                                                  "watch_strays.json"), log=log):
            actions.append(dict(k, action="kill_stray"))

    while True:
        remaining = int(end - time.time())
        dirs = watch.fast_active_runs(ws, find_runs(ws))
        if not dirs:
            return emit({"event": "idle", "workspace": ws,
                         "next_step": "No run is active under this workspace."})
        if args.auto_triage:
            for d in dirs:
                done, _ = watch_triage.auto_triage(d, log=log)
                actions += [dict(x, action="triage") for x in done]
                if any(x["rule"] == "infra-first-retry:disk_full" for x in done):
                    free_if_full(d)
        if remaining < 10:
            return emit(dict(watch.fast_snapshot(dirs[0]), event="timeout", waited_s=args.timeout))
        snap = watch.wait_many(dirs, timeout_s=remaining, poll_s=args.poll, fast=True,
                               quiet_phases=True)
        ev, out = snap.get("event"), snap.get("output_dir")
        if ev == "decision" and args.auto_triage:
            done, left = watch_triage.auto_triage(out, log=log)
            actions += [dict(x, action="triage") for x in done]
            if any(x["rule"] == "infra-first-retry:disk_full" for x in done):
                free_if_full(out)
            others = [p for p in watch.fast_snapshot(out)["pending_decisions"]
                      if p.get("point") != "triage"]
            if not left and not others:
                continue
        if ev == "supervisor_stopped" and args.restart_dead and snap.get("outcome") is None:
            # No outcome file: the supervisor died without finishing (a stop,
            # complete or needs_attention always writes one).
            free_if_full(out)
            ok, err = watch_triage.restart(out, tc_argv)
            actions.append({"action": "restart", "design": os.path.basename(out),
                            "ok": ok, "error": err or None})
            if ok and watch.serve_pid(out):
                continue
        if ev in ("supervisor_stopped", "complete"):
            watch.note_reported_stop(ws, out)
        return emit(snap)


def dashboard(output_dir):
    """watch.dashboard plus the model calls' tokens and cost (token_usage.py)."""
    dash = watch.dashboard(output_dir)
    dash["token_usage"] = token_usage.totals(output_dir)
    web_url = web_control.read_run_url(output_dir)
    if web_url:
        dash["web_url"] = web_url
    return dash


def cmd_snapshot(args, cfg):
    """Read-only view for dashboards: never consumes a decision notification."""
    print(json.dumps(dashboard(args.output_dir),
                     indent=None if args.compact else 2))
    return 0


def cmd_attach(args, cfg):
    lease = watch.attach(args.output_dir, workspace=args.workspace,
                         max_wall_hours=args.max_wall_hours)
    print(json.dumps(dict(lease, lease=watch.lease_path(args.workspace),
                          attached_runs=watch.attached_runs(args.workspace or os.getcwd())),
                     indent=2))
    return 0


def cmd_detach(args, cfg):
    res = watch.detach(workspace=args.workspace, output_dir=args.output_dir,
                       user_asked=args.user_asked)
    print(json.dumps({"detached": bool(res["released"]), "released": res["released"],
                      "kept_active": res["kept_active"],
                      "attached_runs": watch.attached_runs(args.workspace or os.getcwd())}))
    if res["kept_active"] and not res["released"]:
        print("tc detach: refused -- %s still active. Keep orchestrating: answer pending "
              "decisions and wait (tc wait --attached). Pass --user-asked only if the user "
              "asked you to stop." % ", ".join(res["kept_active"]), file=sys.stderr)
        return 3
    return 0


# ---------------------------------------------------------------------------
# tc doctor / setup / runs -- what a host (Vivado MCP's vivado_workflow) needs
# ---------------------------------------------------------------------------
def _emit(obj, as_json, lines):
    """One JSON object on stdout with --json, else the human lines."""
    if as_json:
        print(json.dumps(obj, indent=2))
    else:
        for line in lines:
            print(line)


def _preflight_probe():
    return os.environ.get("TC_PREFLIGHT_PROBE", "1") != "0"


def _preflight(cmd, cfg, name, rec=None):
    """
    None when the workspace is set up to start a run, else the exit code after
    saying what is missing: the payload Vivado MCP turns into a form, and a
    line for someone at a terminal. Nothing has been submitted.
    """
    block = launch.backend_block(cfg, rec) if rec else dict(cfg.get("backend") or {})
    res = preflight.check({"backend": block}, name, probe=_preflight_probe())
    for w in res["warnings"]:
        print("tc %s: warning: %s" % (cmd, w), file=sys.stderr)
    if res["ok"]:
        return None
    print(json.dumps(preflight.needs(res)))
    print("tc %s: not started, the setup is incomplete:\n%s\nGive the missing values to "
          "`tc setup` (for example --queue long --os-type ws8), or fill in the setup form."
          % (cmd, "\n".join("  - %s: %s" % (f["label"], f["problem"]) for f in res["fields"])),
          file=sys.stderr)
    return preflight.NEEDS_EXIT


def _setup_backend(cfg):
    """The backend whose block setup answers go to: the configured one, `auto` as it would resolve."""
    name = config.get(cfg, "backend", "default", default="auto")
    if name == "auto":
        name = "session" if config.get(cfg, "backend", "session", "mcp_command") else "lsf"
    return name


def _needs_vivado(cmd):
    """Stop for a Vivado path: Vivado MCP asks the user, a terminal user is told."""
    print(json.dumps(host_setup.NEEDS_VIVADO))
    print("tc %s: no Vivado path. Through Vivado MCP the user is asked; on the command "
          "line set backend.vivado in .tc/engine.yaml to the vivado executable." % cmd,
          file=sys.stderr)
    return host_setup.NEEDS_EXIT


def _vivado_for_run(cfg):
    """
    (cfg, None) with an explicit Vivado in cfg, else (None, exit code). A path
    from the host context is recorded in the workspace config first, so the
    supervisor this run starts reads the same one.
    """
    if host_setup.configured_vivado(cfg):
        return cfg, None
    viv = host_setup.context_vivado()
    if not viv:
        return None, _needs_vivado("close")
    ws_cfg = config.workspace_config()
    if ws_cfg:
        host_setup.record_vivado(ws_cfg, viv)
    else:
        host_setup.setup(os.environ["VIVADO_WORKFLOW_CONTEXT"], os.getcwd())
    print("tc close: using Vivado %s, recorded in .tc/engine.yaml" % viv, file=sys.stderr)
    return config.load(), None


def cmd_setup(args, cfg):
    ws = os.path.abspath(args.workspace or os.getcwd())
    path = os.path.join(ws, config.WORKSPACE_CONFIG)
    answers = {"vivado": args.vivado, "queue": args.queue, "os_type": args.os_type,
               "memory_mb": args.memory_mb, "slots": args.slots, "env_script": args.env_script,
               "resource_select": getattr(args, "resource_select", None),
               "extra_lsf_options": getattr(args, "extra_lsf_options", None)}
    answers = {k: v for k, v in answers.items() if v not in (None, "")}
    lsf_only = [f for f, k in (("--resource-select", "resource_select"),
                               ("--extra-lsf-options", "extra_lsf_options")) if k in answers]
    if lsf_only:
        current = config.load(path) if os.path.isfile(path) else config.load("/nonexistent")
        if _setup_backend(current) != "lsf":
            print("tc setup: %s: plain bsub only (backend lsf); this workspace uses the %s "
                  "backend, whose Vivado MCP takes --queue/--os-type/--memory-mb/--slots"
                  % (", ".join(lsf_only), _setup_backend(current)), file=sys.stderr)
            return 2
    if getattr(args, "web", None):
        # The run's live page: a server on every interface, so only on this explicit switch.
        host_setup.record(path, {"web.enabled": args.web == "on"}, why="set by `tc setup --web`")
        if not (answers or args.check or args.host_context):
            _emit({"config": path, "web": {"enabled": args.web == "on"}}, args.json,
                  ["%s: web %s" % (path, args.web)])
            return 0
    if answers.get("vivado"):
        try:
            answers["vivado"] = host_setup.resolve_vivado(answers["vivado"])
        except ValueError as exc:
            print("tc setup: %s" % exc, file=sys.stderr)
            return 1
    if answers and not os.path.isfile(path) and args.host_context and os.path.isfile(args.host_context):
        # First time here: the editor's settings, then the answers on top.
        try:
            host_setup.setup(args.host_context, ws)
        except host_setup.NeedsVivado:
            if not answers.get("vivado"):
                return _needs_vivado("setup")
        except host_setup.ContextError as exc:
            print("tc setup: %s" % exc, file=sys.stderr)
            return 1
    if answers:
        current = config.load(path) if os.path.isfile(path) else config.load("/nonexistent")
        updates = preflight.updates_for(answers, _setup_backend(current))
        host_setup.record(path, updates, why="given to `tc setup`")
    if answers or args.check:
        cfg = config.load(path) if os.path.isfile(path) else config.load("/nonexistent")
        res = preflight.check(cfg, _setup_backend(cfg), probe=_preflight_probe())
        out = {"config": path, "recorded": sorted(preflight.updates_for(answers, _setup_backend(cfg))),
               "ok": res["ok"], "backend": res["backend"], "settings": res["settings"],
               "missing": res["fields"], "warnings": res["warnings"]}
        lines = ["%s: %s" % (path, "ready to start runs" if res["ok"] else "not ready")]
        lines += ["  missing: %s -- %s" % (f["label"], f["problem"]) for f in res["fields"]]
        lines += ["  warning: %s" % w for w in res["warnings"]]
        _emit(out, args.json, lines)
        return 0
    if not args.host_context:
        print("tc setup: give --host-context, the values to record (--vivado, --queue, "
              "--os-type, ...), or --check", file=sys.stderr)
        return 2
    try:
        res = host_setup.setup(args.host_context, ws, force=args.force,
                               dry_run=args.dry_run)
    except host_setup.NeedsVivado:
        return _needs_vivado("setup")
    except host_setup.ContextError as exc:
        print("tc setup: %s" % exc, file=sys.stderr)
        return 1
    lines = ["%s %s" % ("wrote" if res.get("written") else "left", res["config"]),
             "backend: %s" % res["summary"]["backend"]]
    lines += ["note: %s" % n for n in res["notes"]]
    if res.get("next_step"):
        lines.append(res["next_step"])
    _emit(res, args.json, lines)
    return 0


def find_runs(workspace, depth=3):
    """
    Output directories of the runs under a workspace, newest first: every
    folder up to `depth` levels down whose state.json is a flow's, whatever
    its name (watch.run_dirs), plus any run attached here that lives elsewhere.
    """
    ws = os.path.abspath(workspace)
    found = set(watch.run_dirs(ws, depth))
    for d in watch.attached_runs(ws):
        if os.path.isfile(os.path.join(d, "state.json")):
            found.add(d)

    def mtime(d):
        try:
            return os.path.getmtime(os.path.join(d, "state.json"))
        except OSError:
            return 0
    return sorted(found, key=mtime, reverse=True)


def run_summary(output_dir):
    """The few fields a run list shows; watch.snapshot has the rest."""
    snap = watch.snapshot(output_dir)
    state = jsonio.read(os.path.join(output_dir, "state.json")) or {}
    return {
        "output_dir": snap["output_dir"],
        "design": state.get("design"),
        "dcp": state.get("dcp") or state.get("input_dcp"),
        "phase": snap.get("phase"),
        "active": watch.is_active(snap),
        "supervisor_alive": snap.get("supervisor_alive"),
        "runs": snap.get("runs"),
        "pending_decisions": [d.get("id") for d in snap.get("pending_decisions") or []],
        "best": snap.get("best"),
        "outcome": (snap.get("outcome") or {}).get("outcome"),
        "token_usage": token_usage.brief(output_dir),
    }


def cmd_runs(args, cfg):
    ws = os.path.abspath(args.workspace or os.getcwd())
    runs = [run_summary(d) for d in find_runs(ws, args.depth)]
    lines = ["No timing-closure runs under %s." % ws] if not runs else [
        "%-60s %-18s %s" % (r["output_dir"], r["phase"],
                            "active" if r["active"] else "-") for r in runs]
    _emit({"workspace": ws, "runs": runs}, args.json, lines)
    return 0


def cmd_view(args, cfg):
    if args.action == "runs":
        ws = os.path.abspath(args.workspace or os.getcwd())
        items = []
        attached = set(os.path.abspath(r) for r in watch.attached_runs(ws))
        for d in find_runs(ws):
            state = jsonio.read(os.path.join(d, "state.json")) or {}
            updated = watch._mtime_iso(os.path.join(d, "state.json"))
            items.append(view.list_item(watch.snapshot(d), design=state.get("design"),
                                        updated=updated,
                                        agent=watch.agent_state(d, os.path.abspath(d) in attached)))
        print(json.dumps(view.list_document(view.sort_items(items), ws), indent=2))
        return 0
    if args.action == "run":
        out = os.path.abspath(args.output_dir)
        if not os.path.isfile(os.path.join(out, "state.json")):
            print("tc view run: no timing-closure run at %s" % out, file=sys.stderr)
            return 1
        print(json.dumps(view.detail_document(dashboard(out)), indent=2))
        return 0
    print("tc view: runs | run", file=sys.stderr)
    return 2


def cmd_doctor(args, cfg):
    """
    Can this workspace run the flow? Read-only; one report, never a partial
    failure, so a host can show it whole. Exit 0 whenever it could check.
    """
    ws = os.path.abspath(args.workspace or os.getcwd())
    problems, warnings, checks = [], [], {}

    checks["python"] = sys.version.split()[0]
    try:
        checks["skill_root"] = paths.skill_root()
    except paths.SkillNotFound as exc:
        problems.append("the deterministic layer is missing: %s" % exc)

    ws_cfg = config.workspace_config(ws)
    checks["config"] = ws_cfg
    if ws_cfg:
        cfg = config.load(ws_cfg)
    else:
        problems.append("no .tc/engine.yaml in this workspace: run setup (the host "
                        "fills it from Vivado MCP), or write one by hand")

    name = config.get(cfg, "backend", "default", default="auto")
    checks["backend"] = name
    problem = check_vivado(cfg, name)
    if problem:
        problems.append(problem)
    viv = config.get(cfg, "backend", "vivado_path") or config.get(cfg, "backend", "vivado")
    checks["vivado"] = viv
    if name == "session":
        cmd = config.get(cfg, "backend", "session", "mcp_command")
        checks["mcp_command"] = cmd
        if not cmd:
            problems.append("backend.session.mcp_command is not set")
        else:
            binary = str(cmd).split()[0]
            moved = launch.current_extension_binary(binary)
            if moved:
                checks["mcp_command_moved"] = (
                    "%s is gone (its extension was upgraded); tc uses %s. Point "
                    "backend.session.mcp_command there." % (binary, moved))
            elif not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
                problems.append("backend.session.mcp_command %s is not an executable file"
                                % binary)
        if viv and os.path.isabs(viv) and not os.path.isfile(viv):
            problems.append("backend.vivado_path %s does not exist on this host" % viv)
    pin = config.get(cfg, "backend", "session", "mcp_command")
    if pin and launch._EXT_BINARY.match(str(pin).split()[0]):
        # An editor removes an extension's folder when it upgrades it.
        checks["mcp_command_pin"] = ("pinned inside a versioned extension folder: tc follows "
                                     "the newest installed version when that one is removed")
    checks["lsf_reachable"] = launch.lsf_available(cfg)
    if name == "lsf" and not checks["lsf_reachable"]:
        problems.append("backend is lsf but bsub is not reachable (PATH or "
                        "backend.lsf.env_script)")
    elif name == "auto" and not checks["lsf_reachable"]:
        # Not a problem (a run still starts), but auto then quietly runs Vivado
        # on this host unless a Vivado MCP server with vivado_lsf turns up, and
        # backend.json keeps that choice for the whole run. Claude Code's Bash
        # is a non-login bash, so LSF set up in a tcsh login file is not seen
        # (`bjobs` was "command not found" in exactly that shell).
        warnings.append("bsub is not reachable here, so with backend auto runs will execute "
                        "on this machine unless a Vivado MCP server with LSF is found. For the "
                        "farm, give the site's LSF profile (tc setup --env-script "
                        "<.../conf/profile.lsf>) or put bsub on PATH; to run here on purpose, "
                        "set backend default: local")

    from . import migrate
    leftovers = migrate.find(ws)
    checks["old_install_leftovers"] = len(leftovers)
    if leftovers:
        # Not a problem for running the flow, so not counted against ok: but a
        # stale copy can shadow the pack's skill, so say how to clear it.
        checks["old_install_hint"] = ("%d item(s) from the retired workspace install; see "
                                      "`tc migrate`" % len(leftovers))

    runs = find_runs(ws)
    checks["runs"] = len(runs)
    checks["active_runs"] = [d for d in runs if watch.is_active(watch.snapshot(d))]

    ok = not problems
    res = {"ok": ok, "workspace": ws, "checks": checks, "problems": problems,
           "warnings": warnings,
           "next_step": ("Ready: start a run on a checkpoint." if not warnings else
                         "Ready, but tell the user the warnings first: start a run once they "
                         "agree.") if ok else
           "Fix the problems listed, then run doctor again."}
    lines = ["%-14s %s" % (k, v) for k, v in checks.items()]
    lines += ["PROBLEM: %s" % p for p in problems] or ["ok"]
    lines += ["WARNING: %s" % w for w in warnings]
    _emit(res, args.json, lines)
    return 0


def cmd_migrate(args, cfg):
    """
    What the retired workspace install left here (skill copies, agents, hooks,
    the timing-closure MCP registration, a claude-tc launcher), and with
    --remove, take it out. Removing needs the user's yes: on a terminal it is
    asked here; elsewhere pass --yes, which an agent may do only after the user
    agreed (vivado_workflow's migrate_remove action asks them first).
    """
    from . import migrate
    ws = os.path.abspath(args.workspace or os.getcwd())
    items = migrate.find(ws)
    res = {"workspace": ws, "leftovers": [dict(i) for i in items], "removed": []}
    lines = ["%-24s %s%s" % (i["kind"], i["path"], " (%s)" % i["detail"] if i.get("detail") else "")
             for i in items] or ["nothing left from the old workspace install"]
    if args.remove and items:
        if not args.yes:
            if not sys.stdin.isatty():
                res["next_step"] = ("Nothing removed. Show the list to the user; with their yes, "
                                    "rerun with --remove --yes.")
                _emit(res, args.json, lines + [res["next_step"]])
                return 1
            reply = input("Remove these %d item(s)? [y/N] " % len(items)).strip().lower()
            if reply not in ("y", "yes"):
                res["next_step"] = "Nothing removed."
                _emit(res, args.json, lines + ["Nothing removed."])
                return 1
        res["removed"] = ["%s: %s" % d for d in migrate.remove(items)]
        lines = res["removed"]
        res["next_step"] = ("Removed. Timing closure now comes from the AMD skill pack (or its "
                            "plugin) and Vivado MCP's vivado_workflow; reload the editor.")
    else:
        res["next_step"] = ("Nothing to do." if not items else
                            "These are from the retired workspace install; with the user's yes, "
                            "`tc migrate --remove` takes them out.")
    _emit(res, args.json, lines + [res["next_step"]])
    return 0


# Cursor names its events in camelCase; the rest of the guard speaks Claude's.
CURSOR_EVENTS = {"stop": "Stop", "sessionStart": "SessionStart",
                 "subagentStop": "SubagentStop", "beforeSubmitPrompt": "UserPromptSubmit"}


def _cursor_hooks_installed(root):
    try:
        with open(os.path.join(root, ".cursor", "hooks.json")) as fh:
            return "tc guard" in fh.read()
    except (IOError, OSError):
        return False


def cmd_guard(args, cfg):
    """
    The hook command for every editor. Stop: prints the editor's block JSON
    while an attached run is active, nothing otherwise. SessionStart /
    UserPromptSubmit: tells the chat which runs it orchestrates. Never fails
    the hook: any error lets the turn end.
    """
    try:
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        try:
            event = json.loads(raw or "{}")
        except ValueError:
            event = {}
        name = event.get("hook_event_name") or event.get("hookEventName") or "Stop"
        ide = args.ide
        # A plugin's hook need not run in the workspace; Cursor names the open
        # folders in the payload.
        roots = [r for r in event.get("workspace_roots") or [] if isinstance(r, str) and r]
        root = (os.environ.get("CURSOR_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")
                or args.workspace or (roots[0] if roots else None) or os.getcwd())
        if name in CURSOR_EVENTS:
            ide = "cursor"
        name = CURSOR_EVENTS.get(name, name)
        if getattr(args, "plugin", False) and os.environ.get("CURSOR_VERSION"):
            # The skill pack ships Claude-format hooks and Cursor-format ones;
            # Cursor may load both. Its own file answers; this one stays quiet,
            # or every follow-up would arrive twice.
            return 0
        if ide == "auto" and os.environ.get("CURSOR_VERSION"):
            # Cursor can also load the Claude-format hooks. Its own
            # .cursor/hooks.json entry answers; this one stays quiet, or the
            # agent would get every follow-up twice.
            if _cursor_hooks_installed(root):
                return 0
            ide = "cursor"
        chat = watch.chat_of(event)
        transcript = event.get("transcript_path") if isinstance(event.get("transcript_path"), str) \
            else None
        if name in ("SessionStart", "UserPromptSubmit", "Stop"):
            # The orchestrating agent is there: whatever it does next, it did
            # this now. Only its own chat counts, or any chat kept it "active".
            _, mine = watch.leases_for_chat(root, chat, claim=(name == "Stop"),
                                            transcript=transcript)
            watch.note_activity([os.path.abspath(l["output_dir"]) for l in mine], {
                "SessionStart": "session start", "UserPromptSubmit": "prompt",
                "Stop": "stop hook"}[name])
        if name in ("SessionStart", "UserPromptSubmit"):
            if ide == "cursor" and name != "SessionStart":
                return 0          # beforeSubmitPrompt cannot add context in Cursor
            out = watch.context_hook(name, start=root, ide=ide if ide == "cursor" else None,
                                     chat=chat)
            if out is not None:
                print(json.dumps(out))
            return 0
        if ide == "cursor" and event.get("status") == "aborted":
            return 0              # the user pressed stop: never send them back in
        if name != "Stop":
            # Only the top-level orchestrator is held. A role (or any other
            # helper) ending is how its result gets back; blocking it made a
            # VS Code helper repeat `tc wait` six times in 70 s.
            return 0
        block, reason = watch.guard(start=root, chat=chat, transcript=transcript)
        if ide == "auto":
            # VS Code also runs hooks from .claude/settings.json, without
            # CLAUDE_PROJECT_DIR; answer each editor in its own format.
            ide = "claude" if os.environ.get("CLAUDE_PROJECT_DIR") else "vscode"
        out = watch.hook_output(block, reason, ide)
        if out is not None:
            print(json.dumps(out))
    except Exception as exc:                       # noqa: BLE001
        print("tc guard: %s" % exc, file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="tc",
        description="Timing-closure engine. Engine verbs below; every "
                    "orchestrate.py command (init, status, stage1-collect, "
                    "planner-apply, ...) is also accepted and forwarded "
                    "verbatim.")
    # Shared by every verb that talks to a backend, so they can be written
    # after the subcommand where people expect them rather than before it.
    be_opts = argparse.ArgumentParser(add_help=False)
    be_opts.add_argument("--backend",
                         help="override backend.default (auto|local|lsf|session)")
    be_opts.add_argument("--mcp-bin",
                         help="Vivado MCP server binary for --backend "
                              "session. --stdio is added automatically: the "
                              "engine starts a private backend rather than "
                              "sharing an editor's.")

    sub = p.add_subparsers(dest="verb")

    run = sub.add_parser("run", parents=[be_opts],
                         help="Submit and track one Vivado run.")
    run_sub = run.add_subparsers(dest="action")

    s = run_sub.add_parser("submit", parents=[be_opts], help="Submit run.tcl, detached.")
    s.add_argument("--run-dir", required=True)
    s.add_argument("--run-tcl", help="default: <run-dir>/run.tcl")
    s.add_argument("--job-name")
    s.add_argument("--force", action="store_true",
                   help="submit even when a live job is already recorded")

    st = run_sub.add_parser("status", parents=[be_opts], help="Scheduler view of a run.")
    st.add_argument("--run-dir", required=True)
    st.add_argument("--classify", action="store_true",
                    help="also run orchestrate.py run-status, feeding it the "
                         "backend's termination reason")

    k = run_sub.add_parser("kill", parents=[be_opts], help="Kill a run.")
    k.add_argument("--run-dir", required=True)

    lg = run_sub.add_parser("logs", parents=[be_opts], help="Backend log for a run.")
    lg.add_argument("--run-dir", required=True)
    lg.add_argument("--path-only", action="store_true")

    dec = sub.add_parser(
        "decisions",
        help="The judgement inbox: what needs deciding, and answering it.")
    dec_sub = dec.add_subparsers(dest="action")

    dl = dec_sub.add_parser("list", help="Pending and answered decisions.")
    dl.add_argument("--output-dir", required=True)
    dl.add_argument("--pending", action="store_true")
    dl.add_argument("--point", choices=decisions.POINTS)
    dl.add_argument("--json", action="store_true")

    dg = dec_sub.add_parser("get", help="One decision, with its evidence.")
    dg.add_argument("--output-dir", required=True)
    dg.add_argument("--id", required=True)

    da = dec_sub.add_parser("answer", help="Answer a decision (JSON).")
    da.add_argument("--output-dir", required=True)
    da.add_argument("--id", required=True)
    da.add_argument("--file", help="answer JSON; default: stdin")
    da.add_argument("--answer-json", help="the answer JSON itself, instead of --file or stdin")
    da.add_argument("--answered-by", default="ide_agent",
                    choices=decisions.ANSWERERS)
    da.add_argument("--model", help="recorded in provenance, for replay")

    de = dec_sub.add_parser(
        "expire", help="Answer past-deadline decisions from their rules.")
    de.add_argument("--output-dir", required=True)

    dp = dec_sub.add_parser(
        "provenance", help="Who answered what, for the recipe.")
    dp.add_argument("--output-dir", required=True)

    jd = sub.add_parser(
        "judge",
        help="Run the configured headless answerer against the queue.")
    jd_sub = jd.add_subparsers(dest="action")

    ja = jd_sub.add_parser("answer", help="Answer one decision.")
    ja.add_argument("--output-dir", required=True)
    ja.add_argument("--id", required=True)

    jp = jd_sub.add_parser("pending", help="Answer every pending decision.")
    jp.add_argument("--output-dir", required=True)

    jr = jd_sub.add_parser(
        "replay",
        help="Re-run a recorded decision and diff the answer.")
    jr.add_argument("--output-dir", required=True)
    jr.add_argument("--id", required=True)

    mp = sub.add_parser(
        "mcp",
        help="Show which Vivado MCP server the engine would use, and why.")
    mp.add_argument("--dir", help="resolve as if from this directory")
    mp.add_argument("--json", action="store_true")

    dp = sub.add_parser(
        "docs",
        help="Search AMD documentation (UG949, UG906, UG1387, ...) to ground a fix. "
             "Only the query text leaves this machine: name concepts, never design "
             "names, paths or netlist content.")
    dp_sub = dp.add_subparsers(dest="action")
    ds = dp_sub.add_parser("search", help="Search; prints the matching passages with source URLs.")
    ds.add_argument("query", nargs="+", help="what to look up, e.g. USER_CLOCK_ROOT Versal")
    ds.add_argument("--limit", type=int, default=5,
                    help="passages to return, 1-50 (the service's maximum; default 5)")
    ds.add_argument("--family", action="append",
                    help="e.g. 'Versal Premium', 'Virtex UltraScale+' (repeatable)")
    ds.add_argument("--version", action="append", help="e.g. 2026.1 (repeatable)")
    ds.add_argument("--json", action="store_true", help="{url, query, text}")

    qp = sub.add_parser(
        "query", parents=[be_opts],
        help="Read-only Vivado queries against a checkpoint. Writes, "
             "implementation commands and Tcl escapes are refused.")
    qp.add_argument("--dcp", help="checkpoint to open (usually the winner's)")
    qp.add_argument("--run-dir", help="where to run, and where to log queries")
    qp.add_argument("--tcl", action="append",
                    help="a read-only command; repeatable")
    qp.add_argument("--file", help="file of commands, one per line")
    qp.add_argument("--timeout", type=int, default=1800,
                    help="seconds, including LSF queue time")
    qp.add_argument("--where", choices=["auto", "local", "lsf", "session"],
                    help="where Vivado runs (default: query.backend, else the farm "
                         "whenever the flow runs on LSF)")
    qp.add_argument("--check-only", action="store_true",
                    help="validate against the allow-list without running")

    cl = sub.add_parser(
        "close", parents=[be_opts],
        help="Close timing on a checkpoint: set up the flow and start "
             "supervising it, in one command. The usual entry point.")
    cl.add_argument("--dcp", required=True, help="the input checkpoint")
    cl.add_argument("--no-probe", action="store_true",
                    help="skip the build probe (one short job that opens the "
                         "checkpoint before Stage 1 is released)")
    cl.add_argument("--design",
                    help="design name; default: the checkpoint's directory, or "
                         "its file name when that directory is generic")
    cl.add_argument("--output-dir",
                    help="default: <dcp dir>/<dcp name>_tc")
    for flag, where in INIT_HOOKS:
        cl.add_argument(flag, action="append", default=[],
                        help="raw TCL, or a .tcl file to source, %s, in every run (repeatable; set at the "
                             "first start only)" % where)
    cl.add_argument("--judge", choices=["rules", "headless", "inbox"],
                    default="inbox")
    cl.add_argument("--concurrency", type=int)
    cl.add_argument("--on-run-failure", choices=["stop", "continue"])
    cl.add_argument("--poll", type=int)
    cl.add_argument("--max-ticks", type=int, help=argparse.SUPPRESS)
    cl.add_argument("--json", action="store_true",
                    help="stdout is one JSON object (init's report goes to stderr)")
    cl.add_argument("--attach", action="store_true",
                    help="also lease the run to this workspace (tc attach), so a stop "
                         "guard keeps the agent's turn while it is active")

    ba = sub.add_parser(
        "batch", parents=[be_opts],
        help="tc close for every design in a list (name,dcp per line), one run each "
             "under --output-root, in one call")
    ba.add_argument("--designs", required=True, help="the design list, e.g. designs.cfg")
    ba.add_argument("--output-root", help="default: runs/ beside the design list")
    ba.add_argument("--only", action="append", default=[], help="start only this design (repeatable)")
    ba.add_argument("--no-probe", action="store_true")
    ba.add_argument("--concurrency", type=int)
    ba.add_argument("--judge", choices=["rules", "headless", "inbox"])
    ba.add_argument("--json", action="store_true", help="one line")

    ex = sub.add_parser(
        "extend", parents=[be_opts],
        help="Add work to a run that exists (finished, or between stages) instead of starting "
             "a new flow: ideas or a plan tried from the overall best (or --from a run), "
             "or one more planner round. Every result so far stays.")
    ex.add_argument("--output-dir", required=True, help="the run to extend")
    ex.add_argument("--idea", action="append", metavar="NAME[=FILE.xdc]",
                    help="an idea to try: physical XDC lines from FILE (pblocks, MAX_FANOUT, "
                         "...); NAME alone with --early-fix (repeatable)")
    ex.add_argument("--ideas", metavar="FILE",
                    help="JSON list of ideas: {name, xdc | xdc_lines, placers, early_fixes, "
                         "place_directive, ...}")
    ex.add_argument("--plan", help="a plan.json in the planner's format, instead of ideas")
    ex.add_argument("--placers", metavar="FLOW,FLOW",
                    help="Stage 1 flows to try each idea on (default: the starting run's)")
    ex.add_argument("--early-fix", action="append", metavar="KEY=0|1",
                    help="switch an early fix for the ideas, e.g. structural_analysis=1")
    ex.add_argument("--from", dest="from_run",
                    help="start from this run (id or folder) instead of the overall best")
    ex.add_argument("--max-runs", type=int,
                    help="allow this many more runs than the run's guardrail leaves")
    ex.add_argument("--restore", action="store_true",
                    help="the starting run's checkpoint was removed (output.keep_dcps: winner): "
                         "re-run it first to rebuild it, for incremental fixes or a planner round")
    ex.add_argument("--no-serve", action="store_true",
                    help="set the round up, but do not start the supervisor")
    ex.add_argument("--judge", choices=["rules", "headless", "inbox"], default="inbox")
    ex.add_argument("--concurrency", type=int)
    ex.add_argument("--on-run-failure", choices=["stop", "continue"])
    ex.add_argument("--poll", type=int)
    ex.add_argument("--max-ticks", type=int, help=argparse.SUPPRESS)
    ex.add_argument("--json", action="store_true")
    ex.add_argument("--attach", action="store_true",
                    help="also lease the run to this workspace (tc attach)")

    doc = sub.add_parser(
        "doctor",
        help="Check this workspace can run the flow: Python, skill, config, Vivado, "
             "backend, LSF. Read-only.")
    doc.add_argument("--workspace", help="default: the current directory")
    doc.add_argument("--json", action="store_true")

    mg = sub.add_parser(
        "migrate",
        help="List what the retired workspace install left here (skill copies, agents, "
             "hooks, the timing-closure MCP server, claude-tc); --remove takes it out.")
    mg.add_argument("--workspace", help="default: the current directory")
    mg.add_argument("--remove", action="store_true", help="remove what is listed (asks first)")
    mg.add_argument("--yes", action="store_true",
                    help="do not ask: only with the user's agreement")
    mg.add_argument("--json", action="store_true")

    su = sub.add_parser(
        "setup",
        help="Write .tc/engine.yaml from a host context file (Vivado MCP's "
             "vivado_workflow passes one). Leaves an existing file alone unless --force.")
    su.add_argument("--host-context",
                    help="JSON from the host: Vivado, LSF, its own executable, client")
    su.add_argument("--vivado", help="the vivado executable or its install folder")
    su.add_argument("--queue", help="the LSF queue runs go to")
    su.add_argument("--os-type", help="the OS type hosts must have (e.g. ws8), or a select term")
    su.add_argument("--memory-mb", type=int, help="memory per run, MB")
    su.add_argument("--slots", type=int, help="CPU slots per run")
    su.add_argument("--resource-select",
                    help="the whole LSF select[...] string (or its expression), used verbatim "
                         "instead of --os-type; backend lsf only")
    su.add_argument("--extra-lsf-options",
                    help="more bsub options for every run, e.g. \"-sla swdev -P proj\"; "
                         "backend lsf only")
    su.add_argument("--env-script", help="the script that sets up LSF here")
    su.add_argument("--check", action="store_true",
                    help="report whether runs can start; write nothing unless values are given")
    su.add_argument("--web", choices=("on", "off"),
                    help="the run's live page (web.enabled, default on; always off under CI): "
                         "started with each run, its secret link reported as web_url")
    su.add_argument("--workspace", help="default: the current directory")
    su.add_argument("--force", action="store_true", help="replace an existing config (kept as .bak)")
    su.add_argument("--dry-run", action="store_true", help="report, write nothing")
    su.add_argument("--json", action="store_true")

    rn = sub.add_parser(
        "runs",
        help="Every timing-closure run under the workspace, newest first, with "
             "phase, best result and pending decisions. Read-only.")
    rn.add_argument("--workspace", help="default: the current directory")
    rn.add_argument("--depth", type=int, default=3,
                    help="directory levels to search (default %(default)s)")
    rn.add_argument("--json", action="store_true")

    wb = sub.add_parser(
        "web",
        help="The runs' live pages: the Workflows view in a browser, behind a secret link "
             "(engine.yaml web: block; tc setup --web on starts it with every run).")
    wb.add_argument("action", choices=("start", "status", "stop", "url"))
    wb.add_argument("--port", type=int, help="default: web.port (8112)")
    wb.add_argument("--bind", help="default: web.bind (0.0.0.0)")
    wb.add_argument("--host", help="the host name links carry (default: web.host, else this host's FQDN)")
    wb.add_argument("--register", metavar="WORKSPACE",
                    help="start: show this workspace's runs too")
    wb.add_argument("--output-dir", help="url: the link to this run's page")
    wb.add_argument("--json", action="store_true")

    vw = sub.add_parser(
        "view",
        help="Runs as workflow-view/1 documents, for an IDE's generic Workflows view. "
             "Read-only.")
    vw_sub = vw.add_subparsers(dest="action")
    vl = vw_sub.add_parser("runs", help="Every run under the workspace, as list items.")
    vl.add_argument("--workspace", help="default: the current directory")
    vl.add_argument("--json", action="store_true", help="accepted; output is always JSON")
    vd = vw_sub.add_parser("run", help="One run in full: sections, paths, prompts.")
    vd.add_argument("--output-dir", required=True)
    vd.add_argument("--json", action="store_true", help="accepted; output is always JSON")

    sv = sub.add_parser(
        "serve", parents=[be_opts],
        help="Drive the flow to completion. Needs no IDE, no MCP and no LLM.")
    sv.add_argument("--output-dir", required=True)
    sv.add_argument("--concurrency", type=int,
                    help="runs in flight; 0 = unbounded (LSF default)")
    sv.add_argument("--on-run-failure", choices=["stop", "continue"])
    sv.add_argument("--judge", choices=["rules", "headless", "inbox"],
                    help="who may answer decisions; 'rules' applies the "
                         "deterministic default at once and never waits")
    sv.add_argument("--poll", type=int, help="seconds between ticks")
    sv.add_argument("--max-ticks", type=int,
                    help="stop after N ticks (testing)")
    sv.add_argument("--detach", action="store_true",
                    help="run in the background and return at once; the "
                         "supervisor outlives the shell that started it")
    sv.add_argument("--stop", action="store_true",
                    help="stop a detached supervisor. Submitted runs keep "
                         "going; restarting adopts them.")

    wt = sub.add_parser(
        "wait",
        help="Block until a run needs attention (decision, phase change, failure, "
             "supervisor stopped, done) or the timeout, then print one JSON "
             "snapshot. The orchestrating agent's heartbeat.")
    wt.add_argument("--output-dir")
    wt.add_argument("--attached", action="store_true",
                    help="wait on every run attached in this workspace; the "
                         "result names the run in `output_dir`")
    wt.add_argument("--all", action="store_true",
                    help="wait on every active run under --workspace (no attach "
                         "needed); event 'idle' when none is active")
    wt.add_argument("--workspace", help="with --attached or --all; default: the current directory")
    wt.add_argument("--timeout", type=int, default=watch.WAIT_DEFAULT_S,
                    help="seconds (default %(default)s)")
    wt.add_argument("--poll", type=int, default=watch.WAIT_POLL_S)
    wt.add_argument("--compact", action="store_true", help="one line")
    wt.add_argument("--fast", action="store_true",
                    help="read each run's files instead of starting `status` per run per "
                         "poll (use for large batches); with --all, a run that completed "
                         "or whose supervisor died is reported once, then left out")

    wa = sub.add_parser(
        "watch",
        help="`tc wait --all --fast` that answers deterministic triage calls and, "
             "each only when asked, restarts dead supervisors, frees superseded "
             "checkpoints on a full disk and kills stray attempt jobs. Returns when "
             "something needs the agent; `actions` lists what it did.")
    wa.add_argument("--workspace", help="default: the current directory")
    wa.add_argument("--timeout", type=int, default=watch.WAIT_DEFAULT_S,
                    help="seconds (default %(default)s)")
    wa.add_argument("--poll", type=int, default=30)
    wa.add_argument("--compact", action="store_true", help="one line")
    wa.add_argument("--auto-triage", action="store_true",
                    help="answer triage calls the evidence settles: known deterministic "
                         "errors, a first error the design already showed on 2+ other "
                         "runs (no retry), a first host/farm failure (retry)")
    wa.add_argument("--restart-dead", action="store_true",
                    help="restart a supervisor that died without an outcome (tc close "
                         "with its own checkpoint; it adopts running jobs)")
    wa.add_argument("--free-space", action="store_true",
                    help="when writes fail, delete checkpoints superseded within the same "
                         "run and those of retired attempts (logged to .tc/)")
    wa.add_argument("--kill-strays", action="store_true",
                    help="kill farm jobs still alive in retired attempt_* folders")

    sn = sub.add_parser(
        "snapshot",
        help="Read-only JSON view of a run (every run of the stage with its "
             "furthest step and timing, stage bests, decisions, guard, log "
             "tail). For dashboards; does not block.")
    sn.add_argument("--output-dir", required=True)
    sn.add_argument("--compact", action="store_true", help="one line")

    at = sub.add_parser(
        "attach",
        help="Mark this workspace as orchestrating a run, so the stop guard "
             "keeps the agent's turn alive until the run finishes. Several runs "
             "can be attached at once.")
    at.add_argument("--output-dir", required=True)
    at.add_argument("--workspace", help="default: the current directory")
    at.add_argument("--max-wall-hours", type=float,
                    default=watch.GUARD_MAX_WALL_HOURS)

    dt = sub.add_parser("detach", help="Release a run's lease (every lease in "
                                       "the workspace without --output-dir); "
                                       "the runs themselves keep going.")
    dt.add_argument("--workspace", help="default: the current directory")
    dt.add_argument("--output-dir", help="release only this run")
    dt.add_argument("--user-asked", action="store_true",
                    help="the user asked to stop: release a run that is still active "
                         "(otherwise an active run keeps its lease)")

    gd = sub.add_parser(
        "guard",
        help="Hook command. Stop: blocks ending the turn while an attached run "
             "is active (rate- and time-limited). SessionStart / UserPromptSubmit: "
             "tells the chat about live runs so it resumes the loop.")
    gd.add_argument("--ide", choices=["claude", "vscode", "cursor", "auto"], default="auto")
    gd.add_argument("--workspace", help="default: $CLAUDE_PROJECT_DIR or cwd")
    gd.add_argument("--plugin", action="store_true",
                    help="this is the skill pack's Claude-format hook: stay quiet "
                         "under Cursor, whose own hook file answers")

    return p


_RUN_ACTIONS = {
    "submit": cmd_run_submit,
    "status": cmd_run_status,
    "kill": cmd_run_kill,
    "logs": cmd_run_logs,
}

_DECISION_ACTIONS = {
    "list": cmd_dec_list,
    "get": cmd_dec_get,
    "answer": cmd_dec_answer,
    "expire": cmd_dec_expire,
    "provenance": cmd_dec_provenance,
}

_JUDGE_ACTIONS = {
    "answer": cmd_judge_answer,
    "pending": cmd_judge_pending,
    "replay": cmd_judge_replay,
}

_SUBVERBS = {"run": _RUN_ACTIONS, "decisions": _DECISION_ACTIONS,
             "judge": _JUDGE_ACTIONS}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # Pass-through is decided before argparse so that orchestrate.py's own
    # flags (--force, --json, --compact, repeated --run-dir) reach it
    # unmangled.
    for i, tok in enumerate(argv):
        if tok.startswith("-"):
            continue
        if tok in ORCHESTRATE_VERBS:
            return passthrough(argv[i:])
        break

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.verb:
        parser.print_help()
        return 2

    cfg = config.load()

    if args.verb == "close":
        return cmd_close(args, cfg)

    if args.verb == "extend":
        return cmd_extend(args, cfg)

    if args.verb == "batch":
        return cmd_batch(args, cfg)

    if args.verb == "serve":
        return cmd_serve(args, cfg)

    if args.verb == "query":
        return cmd_query(args, cfg)

    if args.verb == "mcp":
        return cmd_mcp(args, cfg)

    if args.verb == "docs":
        return cmd_docs(args, cfg)

    simple = {"wait": cmd_wait, "watch": cmd_watch, "snapshot": cmd_snapshot, "attach": cmd_attach,
              "detach": cmd_detach, "guard": cmd_guard, "doctor": cmd_doctor, "migrate": cmd_migrate,
              "setup": cmd_setup, "runs": cmd_runs, "view": cmd_view, "web": cmd_web}
    if args.verb in simple:
        return simple[args.verb](args, cfg)

    actions = _SUBVERBS.get(args.verb)
    if actions is not None:
        if not getattr(args, "action", None):
            parser.parse_args([args.verb, "--help"])
            return 2
        return actions[args.action](args, cfg)

    print("tc: %r is not implemented yet" % args.verb, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
