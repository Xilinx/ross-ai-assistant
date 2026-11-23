# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The IP-configuration workflow as a helper process of Vivado, driven through the stock Vivado MCP.

lib/ipcfg_op.tcl starts this script with Vivado's own Python and talks to it over a
pipe. Each workflow op the model sends with vivado_execute arrives here as one line;
StructuredSession runs it exactly as it does behind the optional relay
(scripts/guard_mcp.py, see examples/README.md), and every Tcl command it needs goes
back up the pipe for Vivado to evaluate. So the workflow, its refusals and its
readback all run as code, and the model only ever sends `ipcfg_op {<json>}` through
the standard vivado_execute tool.

Protocol, one line each way, payloads base64 so any text survives:
  Tcl -> helper   OP <json>               a workflow op
  helper -> Tcl   TCL <script>            evaluate in Vivado, then answer:
  Tcl -> helper   OK <result> | ERR <message>
  helper -> Tcl   DONE <json> | FAIL <message>   the op's reply or its error
stdout carries only the protocol; anything else the code prints goes to stderr.

Every op carries an increasing "seq" (Bridge.take_seq), so no two vivado_execute
commands are ever identical: the stock MCP skips a write that repeats one which just
succeeded, and a design is built from many cells whose apply and finish ops would
otherwise read the same.

Needs only the standard library, so Vivado's bundled Python runs it as is.
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PROTOCOL = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
sys.stdout = sys.stderr  # a stray print must never land in the protocol stream

from request_repair import parse_request, repair_response_echoes  # noqa: E402
from structured_config import StructuredSession, compact_reply, run_root  # noqa: E402


def encode(text):
    return base64.b64encode(text.encode()).decode('ascii')


def decode(text):
    return base64.b64decode(text).decode()


def send(kind, payload):
    PROTOCOL.write(kind + ' ' + encode(payload) + '\n')
    PROTOCOL.flush()


class Recorder:
    """What the session expects of its host: evidence, a native serial and the release database.

    The vector metadata snapshot (ip_database.py) needs packages Vivado's Python does
    not ship, so only the relay serves it; here the session uses the database Vivado
    itself ships (data/ip/ipdb), which needs only sqlite3, or live discovery.
    """

    def __init__(self):
        self.evidence, self.count, self.closed = None, 0, set()
        self.serial = 0
        self.database_config = None
        self.release_database_path = self.release_database_release = None
        self.database = None
        self.database_lock = threading.Lock()

    def record(self, data):
        self.count += 1
        if self.evidence is None:
            return None
        path = self.evidence / ('bridge-%04d.json' % self.count)
        path.write_text(json.dumps(data, default=str, indent=1))
        return path

    def next_native_serial(self, session):
        """One counter for the whole Vivado session, across cells, as the relay keeps."""
        self.serial += 1
        return self.serial

    def adopt_release_database(self, install_root, version_text):
        from merged_database import release_of, shipped_database
        path = shipped_database(install_root)
        if path is None:
            return None
        self.release_database_path = str(path)
        self.release_database_release = release_of(version_text)
        return self.release_database_path

    def has_database(self):
        return bool(self.release_database_path)

    def database_request(self, command):
        if not self.has_database():
            raise ValueError('DATABASE_NOT_CONFIGURED: use live Vivado discovery')
        if not isinstance(command, str) or len(command) > 2400:
            raise ValueError('Database command must be a bounded JSON object')
        request = json.loads(command)
        started = time.monotonic()
        with self.database_lock:
            if self.database is None:
                from merged_database import MergedDatabase
                self.database = MergedDatabase(self.release_database_path, release=self.release_database_release)
            result = self.database.request(request)
        self.record(dict(database_query_timing=True, op=request.get('op'), elapsed_seconds=time.monotonic() - started))
        return result


class BridgeSession(StructuredSession):
    """The structured session with Vivado reached through the pipe instead of the MCP."""

    async def native(self, expression, prefix=''):
        self.native_serial = self.guard.next_native_serial(self.sid)
        serial = str(self.native_serial)
        # The same variables the relay sets, so lite's state reads the same either way.
        script = ('set __ipcfg_request_seq ' + serial + '\n' + prefix + '\nset __ipcfg_reply ' + expression
                  + '\nset __ipcfg_reply_seq ' + serial + '\nset __ipcfg_reply')
        started = time.monotonic()
        send('TCL', script)
        line = sys.stdin.readline()
        if not line:
            raise SystemExit('Vivado closed the pipe')
        kind, _, body = line.strip().partition(' ')
        result = decode(body)
        path = self.guard.record({'native': True, 'serial': self.native_serial, 'command': script[-4000:], 'status': kind,
                                  'elapsed_seconds': round(time.monotonic() - started, 3), 'result': result[-4000:]})
        if kind != 'OK':
            raise ValueError('NATIVE_ERROR: ' + result[-700:] + '; evidence=' + str(path))
        return result

    async def recover_detached(self, serial, evidence):
        raise ValueError('NATIVE_ERROR: no detached commands over the pipe; evidence=' + str(evidence))

    async def close(self):
        """Vivado is stopped by the model with vivado_stop; a cell's finish keeps it open."""
        return False


class Bridge:
    def __init__(self, log_file):
        self.log_file = log_file
        self.recorder = Recorder()
        self.session = None
        self.failures = {}
        # The highest step number of an op that succeeded. Only success advances it,
        # because the MCP lets a failed command be retried unchanged.
        self.last_seq = 0
        # The last finished step's reply. A client abandons an MCP call after about a minute
        # while Vivado keeps working, so a step can finish with nobody reading its reply;
        # status hands it back (lib/ipcfg_op.tcl covers steps that are still running).
        self.last_step = None
        # The run root as lite::begin derives it.
        evidence = run_root(log_file) / 'evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        self.recorder.evidence = evidence

    def take_seq(self, request):
        seq = request.pop('seq', None)
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= self.last_seq:
            raise ValueError('Every op needs "seq", a step number greater than the last that succeeded; send it again with '
                             '"seq":%d. (Vivado MCP skips a command identical to one that just succeeded, so a repeated '
                             'step would not run.)' % (self.last_seq + 1))
        return seq

    def session_for(self, request):
        """finish saves a cell and keeps Vivado open, so the next begin starts a fresh cell ledger.

        A cell stopped by repeated failures blocks only a begin of that same cell.
        """
        previous = self.session
        if previous is None or (request.get('op') == 'begin' and previous.stage == 'finished'
                                and not (getattr(previous, 'terminal_stop', False)
                                         and request.get('cell') == getattr(previous, 'cell', None))):
            self.session = BridgeSession(self.recorder, None, 'vivado', self.log_file)
            self.failures.clear()
        return self.session

    async def handle(self, command):
        request = seq = None
        try:
            request, repaired = parse_request(command)
            if not isinstance(request, dict) or len(command) > 16000:
                raise ValueError('Send one JSON object with an "op" field')
            seq = self.take_seq(request)
            had_session = request.pop('session_id', None) is not None
            repairs = repair_response_echoes(request)
            reply = await self.session_for(request).request(request)
            if request.get('op') == 'status':
                if self.last_step and isinstance(reply, dict):
                    reply = dict(reply, last_step=self.last_step)
            else:
                self.last_step = dict(seq=seq, op=request.get('op'), reply=reply)
            if repaired:
                repairs.insert(0, 'Removed %d unmatched closing bracket(s)' % repaired)
            if had_session:
                repairs.append('Removed session_id; the op runs in the Vivado it is sent to')
            if repairs and isinstance(reply, dict):
                reply = dict(reply, request_repaired='; '.join(repairs) + '; check the parsed request fields in this reply')
            self.failures.clear()
            self.last_seq = seq
            self.recorder.record({'seq': seq, 'request': request, 'reply': reply, 'failed': False})
            return 'DONE', json.dumps(compact_reply(reply), separators=(',', ':'), default=str)
        except Exception as exc:
            self.recorder.record({'seq': seq, 'request': request, 'error': str(exc), 'failed': True})
            message = 'STRUCTURED_ERROR: ' + str(exc)
            state = self.session
            if state is not None:
                snapshot = state.state()
                workflow = {k: snapshot.get(k) for k in ('stage', 'next_op')}
                if hasattr(state, 'vlnv'):
                    workflow['vlnv'] = state.vlnv
                identity = (json.dumps(request, sort_keys=True, default=str), str(exc))
                self.failures[identity] = count = self.failures.get(identity, 0) + 1
                if count >= 3:
                    # Before begin succeeds there is no workflow to lock: keep the project
                    # and allow one valid begin. Once a cell exists, the stop is terminal
                    # for that cell only; Vivado and the project stay open for others.
                    state.terminal_stop = bool(state.initialized)
                    reply = await state.finish_partial('Repeated unchanged workflow failure; ' + str(exc)[:160])
                    if state.terminal_stop:
                        reply.update(next_op='begin', cell_stopped=True,
                                     cell_stopped_note='This cell stopped after repeated identical failures; '
                                                       'begin a different cell, or stop Vivado when the design is done.')
                        state.final_reply = reply
                    self.last_seq = max(self.last_seq, seq or 0)
                    self.recorder.record({'seq': seq, 'request': request, 'reply': reply, 'failed': False, 'failure_stop': True})
                    return 'DONE', json.dumps(dict(reply, failure_stop=True), separators=(',', ':'), default=str)
                if count >= 2:
                    workflow.update(repeat_count=count,
                                    guidance='Change the request using next_op; one more identical failure finishes partial')
                message += ' ' + json.dumps({'workflow': workflow}, separators=(',', ':'))
                details = getattr(exc, 'details', None)
                if details:
                    message += ' ' + json.dumps({'request_schema': details}, separators=(',', ':'), default=str)
            return 'FAIL', message


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--log-file', required=True, help="Vivado's session log under <working_dir>/.vivado-ai")
    args = parser.parse_args()
    bridge = Bridge(args.log_file)
    for line in sys.stdin:
        kind, _, body = line.strip().partition(' ')
        if kind != 'OP':
            send('FAIL', 'Expected an OP line, got ' + kind[:20])
            continue
        send(*await bridge.handle(decode(body)))


if __name__ == '__main__':
    asyncio.run(main())
