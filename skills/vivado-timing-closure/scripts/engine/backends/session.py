#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
session.py -- runs that stay loaded, so the best one can be queried.

The other backends start `vivado -mode batch -source run.tcl`: the script runs,
Vivado exits, the slot is freed. That is right for six runs out of seven. It is
wrong for the one the planner is about to interrogate, because re-opening its
routed checkpoint costs minutes on a large design while the session that
produced it already has everything in memory.

This backend submits through the Vivado MCP server's own LSF path, which is
what buys the queue and OS-host feasibility checks the engine would otherwise
have to duplicate: a request the cluster can satisfy but the chosen queue
cannot is refused here, rather than accepted by LSF and pended forever.

The mechanism is a single documented behaviour of `vivado_lsf action='start'`:

    "Vivado sources it when the job starts ... A script ending in `exit`
     closes the session."

So `TC_KEEP_SESSION=1` -- which makes `tcl_gen` omit the trailing `exit` -- is
the entire switch. A run that finishes returns to the Tcl prompt with its
design loaded, and `engine/champion.py` decides which single one stays.

Three rules it keeps from the batch backends, all of them paid for:

* **Files remain authoritative.** `run.done` / `run.failed` / `hs_err` decide
  what happened; the session's own view is supplementary. A crashed run that
  the server still reports as live must not read as running -- that is what
  ended the Ciena workflow early.
* **The handle is written before the submit returns**, so an engine restart
  re-adopts rather than relaunching.
* **A failed run is never held.** It exits 1 by design, so there is no session
  to keep, and an idle Vivado holding ~18 GB is the incident to avoid.

Requires a *private* backend (`--stdio`): a shared daemon switching underneath
a six-hour run is how three design A runs were lost.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import datetime
import os
import shlex
import shutil

from .. import mcp_client, mcp_discover
from . import base

SESSION_TYPE = "general"


class SessionBackend(base.ComputeBackend):

    name = "session"
    supports_keepalive = True

    def __init__(self, client, submit_mode=None, vivado_path=None,
                 start_timeout=600, log=None, lsf=None):
        self.client = client
        self.submit_mode = submit_mode          # None -> the server's 'auto'
        self.vivado_path = vivado_path
        self.start_timeout = start_timeout
        self.log = log or (lambda msg: None)
        # The MCP server is only the launcher. Every run is also an LSF job,
        # and when the server cannot speak for a run -- it died, or a new one
        # never knew it -- status and kill go straight to LSF with this.
        self.lsf = lsf

    def reset(self):
        """Drop the server process; the next call starts a fresh one."""
        try:
            self.client.close()
        except Exception:                              # noqa: BLE001
            pass

    # -- plumbing -------------------------------------------------------

    def _ready(self):
        if not self.client.alive:
            self.client.start()
            # A server without vivado_lsf (the build the Vivado AI extension
            # ships) cannot launch anything: that is an outage of the
            # launcher, not a refusal of one job, so the failover can act.
            tools = [t.get("name") for t in
                     (self.client._request("tools/list", {}, timeout=60).get("tools") or [])]
            if "vivado_lsf" not in tools:
                self.client.close()
                raise mcp_client.McpError("the MCP server has no vivado_lsf tool "
                                          "(LSF launch is not in this build)")
        return self.client

    def available(self):
        try:
            self._ready()
        except mcp_client.McpError as exc:
            self.log("session backend unavailable: %s" % exc)
            return False
        try:
            self.client.call_tool("vivado_lsf", {"action": "check"},
                                  timeout=120)
            return True
        except (mcp_client.McpError, mcp_client.ToolError) as exc:
            self.log("session backend: LSF not usable: %s" % exc)
            return False

    # -- interface ------------------------------------------------------

    def submit(self, run_dir, run_tcl, resources=None):
        if not os.path.isfile(run_tcl):
            raise base.SubmitError("no run.tcl at %s" % run_tcl)
        res = resources or base.Resources()

        args = {
            "action": "start",
            "working_dir": os.path.abspath(run_dir),
            "session_type": SESSION_TYPE,
            # Sourced by Vivado once the job starts. Without a trailing `exit`
            # (TC_KEEP_SESSION=1) the session survives the script.
            "source": os.path.abspath(run_tcl),
            "gui_mode": False,
            "display_mode": "none",
        }
        if res.queue:
            args["queue"] = res.queue
        if res.slots:
            args["slots"] = int(res.slots)
        if res.memory_mb:
            args["memory"] = "%dm" % int(res.memory_mb)
        if res.os_type:
            args["os_type"] = res.os_type
        if self.submit_mode:
            args["submit_mode"] = self.submit_mode
        if self.vivado_path:
            args["vivado_path"] = self.vivado_path

        binary = (getattr(self.client, "command", None) or [None])[0]
        if binary and os.path.isabs(binary) and not os.path.isfile(binary):
            # A server already running keeps answering from its deleted file,
            # but every LSF job it starts runs that file on the farm host and
            # exits at once ("No such file or directory"): an outage of the
            # launcher, which the failover can act on.
            raise base.LauncherUnavailable(
                "the MCP server binary %s no longer exists (its editor extension was "
                "upgraded or removed)" % binary)
        try:
            self._ready()
            payload = self.client.call_json("vivado_lsf", args,
                                            timeout=self.start_timeout)
        except mcp_client.McpError as exc:
            # The server would not start or answer: the launcher is down.
            raise base.LauncherUnavailable(str(exc))
        except mcp_client.ToolError as exc:
            # A refusal of this job -- including the queue/OS feasibility
            # check, which is the whole reason for submitting through here.
            raise base.SubmitError(str(exc))

        session_id = payload.get("session_id")
        if not session_id:
            # str() first: the payload may be a parsed object rather than the
            # raw text, and an error path that crashes is worse than the error.
            raise base.SubmitError(
                "LSF start returned no session_id: %s"
                % str(payload.get("_text") or payload)[:300])

        handle = base.JobHandle(
            backend=self.name,
            job_id=payload.get("lsf_job_id") or session_id,
            run_dir=os.path.abspath(run_dir),
            log_path=payload.get("session_log") or None,
            submitted_utc=datetime.datetime.now(datetime.timezone.utc)
                                  .isoformat(),
            resources=res.to_dict())
        handle.resources["session_id"] = session_id
        handle.resources["lsf_host"] = payload.get("lsf_host")
        handle.resources["lsf_queue"] = payload.get("lsf_queue")
        handle.resources["lsf_job_id"] = (payload.get("lsf_job_id")
                                          or self._lsf_job_id(session_id))
        base.save_handle(handle)
        return handle

    def _lsf_job_id(self, session_id):
        """The LSF job id for a session: `start` omits it, `status` has it."""
        try:
            p = self.client.call_json("vivado_lsf", {"action": "status",
                                                     "session_id": session_id},
                                      timeout=60)
        except (mcp_client.McpError, mcp_client.ToolError):
            return None
        return str(p.get("job_id")) if p.get("job_id") else None

    def _via_lsf(self, handle):
        """A stand-in handle for asking LSF about this run directly."""
        job = (handle.resources or {}).get("lsf_job_id")
        if not job or self.lsf is None:
            return None
        return base.JobHandle(backend="lsf", job_id=job, run_dir=handle.run_dir,
                              log_path=os.path.join(handle.run_dir, ".tc-no-lsf-log"))

    def status(self, handle):
        """
        The scheduler's view, for the classifier to weigh against the files.

        Never raises. A backend that cannot be reached is UNKNOWN, which leaves
        the verdict where it belongs -- in the run directory.
        """
        session_id = self._session_of(handle)
        try:
            self._ready()
            payload = self.client.call_json(
                "vivado_lsf", {"action": "status", "session_id": session_id},
                timeout=120)
        except (mcp_client.McpError, mcp_client.ToolError) as exc:
            payload = {"_error": str(exc)[:300]}

        # A queued session answers {"status": "pending", "state": "PEND"}; a
        # single job's status is LSF's own word (PEND, RUN, DONE, EXIT).
        raw_state = str(payload.get("state") or payload.get("status") or payload.get("job_status")
                        or "").upper()
        state = _STAT.get(raw_state, base.UNKNOWN)
        if state == base.UNKNOWN:
            # The server is down, never knew this session ({"jobs": [...]}), or
            # answered in words this table does not know. LSF still knows the
            # job -- and a queued job read as UNKNOWN was declared stale and
            # failed on a busy queue (the Rubik batch). The server can still
            # answer for the LSF job by its id, without needing bjobs on PATH.
            job = (handle.resources or {}).get("lsf_job_id")
            if job:
                try:
                    self._ready()
                    p = self.client.call_json(
                        "vivado_lsf", {"action": "status", "job_id": str(job)},
                        timeout=120)
                except (mcp_client.McpError, mcp_client.ToolError):
                    p = {}
                js = _STAT.get(str(p.get("status") or p.get("state") or "").upper(),
                               base.UNKNOWN)
                if js != base.UNKNOWN:
                    return base.JobStatus(
                        js, host=p.get("host") or None,
                        exit_code=_int_or_none(p.get("exit_code")),
                        term_reason=p.get("exit_reason") or p.get("term_reason"),
                        raw="mcp job %s: %s" % (job, p.get("status") or p.get("state")))
            lsf_handle = self._via_lsf(handle)
            if lsf_handle is not None:
                try:
                    st = self.lsf.status(lsf_handle)
                except OSError:                # no bjobs on PATH
                    st = base.JobStatus(base.UNKNOWN)
                if st.state != base.UNKNOWN:
                    return st
            if not raw_state:
                return base.JobStatus(base.UNKNOWN, term_reason=None,
                                      raw=(payload.get("_error") or "session unknown to MCP")[:300])
        return base.JobStatus(
            state,
            host=payload.get("host") or payload.get("lsf_host"),
            exit_code=_int_or_none(payload.get("exit_code")),
            term_reason=payload.get("exit_reason") or payload.get("term_reason"),
            raw=raw_state or None)

    def kill(self, handle):
        session_id = self._session_of(handle)
        args = {"action": "kill", "session_id": session_id}
        # A server started after the submit does not know the session, but
        # it can still kill the LSF job by its id.
        job = (handle.resources or {}).get("lsf_job_id")
        if job:
            args = {"action": "kill", "job_id": str(job)}
        try:
            self._ready()
            self.client.call_tool("vivado_lsf", args, timeout=120)
            return
        except (mcp_client.McpError, mcp_client.ToolError) as exc:
            self.log("session backend: kill %s through MCP failed: %s"
                     % (session_id, exc))
        # A run the server cannot reach must not keep its slot: kill the LSF
        # job itself.
        lsf_handle = self._via_lsf(handle)
        if lsf_handle is not None:
            self.lsf.kill(lsf_handle)
            self.log("session backend: killed LSF job %s directly" % lsf_handle.job_id)

    def release(self, handle):
        """
        Stop holding a finished run's session and free its slot.

        Called by the champion policy for every run it is not keeping. Best
        effort: a session that cannot be reached is logged, because failing to
        tidy up must never stall a flow.
        """
        session_id = self._session_of(handle)
        if not session_id:
            return
        try:
            self._ready()
            self.client.call_tool(
                "vivado_stop",
                {"session_id": session_id, "close_vivado": True},
                timeout=180)
            self.log("session backend: released %s" % session_id)
        except (mcp_client.McpError, mcp_client.ToolError) as exc:
            # Its job already finished: nothing holds a slot, so it is released.
            if "session has ended" in str(exc):
                self.log("session backend: %s had already ended" % session_id)
                return
            self.log("session backend: could not release %s: %s"
                     % (session_id, exc))

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _session_of(handle):
        return (handle.resources or {}).get("session_id") or handle.job_id


# bjobs states, as the LSF status action reports them. UNKWN/ZOMBI stay
# UNKNOWN on purpose so the run directory decides.
_STAT = {
    "PEND": base.PENDING, "PROV": base.PENDING, "WAIT": base.PENDING,
    "PSUSP": base.PENDING, "PENDING": base.PENDING, "QUEUED": base.PENDING,
    "RUN": base.RUNNING, "USUSP": base.RUNNING, "SSUSP": base.RUNNING,
    "RUNNING": base.RUNNING, "READY": base.RUNNING,
    "DONE": base.DONE,
    "EXIT": base.FAILED,
}


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


STATE_DIR_ENV = "VIVADO_STATE_DIR"
STATE_DIR_NAME = ".tc-mcp-state"
ADOPT_SCOPE_ENV = "VIVADO_ADOPT_SCOPE"

STDIO_FLAG = "--stdio"
BRIDGE_FLAG = "--stdio-bridge"


def normalize_command(command, log=None):
    """
    Turn what the user supplied into an argv that is guaranteed private.

    Accepts a bare path ("/path/to/vivado-mcp-server"), a command string, or an
    argv list. The transport is then enforced rather than trusted:

    * `--stdio-bridge` is **refused**. It relays to the shared HTTP daemon,
      which is the one thing this backend must not use: a client on a different
      channel replaces that daemon with no regard for how many sessions it is
      holding (bridgeDaemonDecision), and recovery depends on re-adoption --
      the path that "adopted and declared dead sessions it did not own".
    * `--stdio` is appended when absent, because the default transport is the
      shared one. Documenting this was not enough: the requirement appeared in
      three comments and nothing checked it, so a bare path silently bought
      the shared daemon for a six-hour run.
    """
    if command is None:
        raise ValueError("no MCP command given")
    if isinstance(command, str):
        command = shlex.split(command) if " " in command.strip() else [command]
    command = [str(c) for c in command if str(c).strip()]
    if not command:
        raise ValueError("empty MCP command")

    if BRIDGE_FLAG in command:
        raise ValueError(
            "%s relays to the shared daemon; the engine needs a private "
            "backend. Use %s instead." % (BRIDGE_FLAG, STDIO_FLAG))

    if STDIO_FLAG not in command:
        command = command + [STDIO_FLAG]
        if log:
            log("session backend: added %s -- the engine needs a private "
                "backend, not the shared daemon" % STDIO_FLAG)
    return command


def _no_server_message(output_dir):
    """Say what was looked at, not just that nothing was found."""
    lines = ["no Vivado MCP server found.",
             "Pass --mcp-bin <path>, or set backend.session.mcp_command."]
    seen = mcp_discover.discover(output_dir)
    if seen:
        lines.append("Configured but not usable on this host:")
        for c in seen:
            lines.append("  %s  (%s)" % (c.binary, c.source))
    return "\n".join(lines)


def check_binary(path):
    """Fail at configuration time, not four hours into a sweep."""
    if os.path.sep in path or path.startswith("."):
        if not os.path.isfile(path):
            raise ValueError("no MCP server at %s" % path)
        if not os.access(path, os.X_OK):
            raise ValueError("%s is not executable" % path)
        return path
    found = shutil.which(path)
    if not found:
        raise ValueError("%r is not on PATH" % path)
    return found


def make(cfg, output_dir=None):
    """
    Build a SessionBackend from the `backend:` config block.

    `--stdio` means no HTTP port and no shared daemon, so the engine's backend
    cannot be *discovered* by an editor's. That is not the whole story: servers
    also find each other through a shared state directory, and one that adopted
    sessions it did not own has already cost us runs --

        "Old rc4 daemon shares ~/.vivado-ai with the planner's server: it
         adopted and declared dead sessions it did not own."

    So the engine pins VIVADO_STATE_DIR under its own output directory unless
    the caller set one. An editor's server, a colleague's, and this one then
    have no way to see each other's sessions at all.
    """
    sub = (cfg.get("session") or {})
    if sub.get("shared"):
        return _make_shared(cfg, sub)
    raw = sub.get("mcp_command")
    discovered = None
    if not raw:
        # Fall back to whatever the user already has configured, rather than
        # asking them to restate a path they have written once already -- the
        # surest way to pin a stale build by accident.
        discovered = mcp_discover.best(output_dir)
        if discovered:
            raw = [discovered.binary] + discovered.args
    if not raw:
        raise ValueError(_no_server_message(output_dir))

    command = normalize_command(raw)
    command[0] = check_binary(command[0])

    env = dict(sub.get("env") or {})
    if discovered:
        # The discovered entry's env says which Vivado to drive; our own keys
        # (the state dir, below) always win over it.
        for k, v in discovered.env.items():
            env.setdefault(k, v)
    if STATE_DIR_ENV not in env and os.environ.get(STATE_DIR_ENV) is None:
        if output_dir:
            env[STATE_DIR_ENV] = os.path.join(os.path.abspath(output_dir),
                                              STATE_DIR_NAME)
    # The host's session store is shared whatever the state directory: without
    # this the server adopts every session on the host, and a 101-design batch
    # ran 101 servers that each adopted and probed all 414 of them every 10 s.
    # Servers that do not know the setting ignore it.
    env.setdefault(ADOPT_SCOPE_ENV, "own")

    state_dir = env.get(STATE_DIR_ENV) or os.environ.get(STATE_DIR_ENV)
    client = mcp_client.McpClient(
        command, cwd=sub.get("cwd"), env=env, timeout=sub.get("timeout_s") or 300,
        # The server's log, kept for diagnosis instead of a pipe nobody read.
        stderr_path=os.path.join(state_dir, "mcp_server_stderr.log") if state_dir else None)
    from .lsf import LSFBackend
    lsf = LSFBackend(vivado=cfg.get("vivado", "vivado"),
                     env_script=(cfg.get("lsf") or {}).get("env_script"))
    # The site's pinned build wins over whatever the discovered server would
    # pick: backend.vivado_path, else an absolute backend.vivado.
    vivado_path = cfg.get("vivado_path")
    if not vivado_path and os.path.isabs(str(cfg.get("vivado") or "")):
        vivado_path = cfg.get("vivado")
    return SessionBackend(client,
                          submit_mode=sub.get("submit_mode"),
                          vivado_path=vivado_path,
                          start_timeout=sub.get("start_timeout_s") or 600,
                          lsf=lsf)


def _make_shared(cfg, sub):
    """
    backend.session.shared: launch through the user's shared server (the one
    the editor's session tree shows), reached over HTTP -- the URL in
    backend.session.url, else ~/.vivado-ai/mcp_url. Opt-in: see
    mcp_client.HttpMcpClient for what it costs.
    """
    client = mcp_client.HttpMcpClient(url=sub.get("url"), url_file=sub.get("url_file"),
                                      timeout=sub.get("timeout_s") or 300)
    from .lsf import LSFBackend
    lsf = LSFBackend(vivado=cfg.get("vivado", "vivado"),
                     env_script=(cfg.get("lsf") or {}).get("env_script"))
    vivado_path = cfg.get("vivado_path")
    if not vivado_path and os.path.isabs(str(cfg.get("vivado") or "")):
        vivado_path = cfg.get("vivado")
    return SessionBackend(client, submit_mode=sub.get("submit_mode"),
                          vivado_path=vivado_path,
                          start_timeout=sub.get("start_timeout_s") or 600, lsf=lsf)
