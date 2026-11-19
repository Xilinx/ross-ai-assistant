# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The CED workflow as a helper process of Vivado, driven through the stock Vivado MCP.

lib/ced_op.tcl starts this script with Vivado's own Python and talks to it over a pipe.
Each workflow op the model sends with vivado_execute arrives here as one line; the
session runs it exactly as it does behind the MCP relay (examples/ced_guard.py), and
every Tcl command it needs goes back up the pipe for Vivado to evaluate. So the
workflow, its refusals and its readback all run as code, and the model only ever
sends `ced_op {<json>}` through the standard vivado_execute tool.

Protocol, one line each way, payloads base64 so any text survives:
  Tcl -> helper   OP <json>               a workflow op
  helper -> Tcl   TCL <script>            evaluate in Vivado, then answer:
  Tcl -> helper   OK <result> | ERR <message>
  helper -> Tcl   DONE <json> | FAIL <message>   the op's reply or its error
stdout carries only the protocol; anything else the code prints goes to stderr.

Every op carries an increasing "seq" (Bridge.take_seq), so no two vivado_execute
commands are ever identical: the stock MCP skips a write that repeats one which just
succeeded.

Needs only the standard library, so Vivado's bundled Python runs it as is.
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = Path(os.environ.get('CED_LITE_SKILL', HERE.parent / 'engine')).resolve()
sys.path[:0] = [str(HERE), str(ENGINE / 'scripts')]

PROTOCOL = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
sys.stdout = sys.stderr  # a stray print must never land in the protocol stream

from ced_session import CEDSession, run_root  # noqa: E402

APPROVE = 'ced_apply_phase2'


def encode(text):
    return base64.b64encode(text.encode()).decode('ascii')


def decode(text):
    return base64.b64decode(text).decode()


def send(kind, payload):
    PROTOCOL.write(kind + ' ' + encode(payload) + '\n')
    PROTOCOL.flush()


def parse_request(command):
    """A JSON op, forgiving up to three stray closing brackets as the relay does."""
    text, removed = command, 0
    while True:
        try:
            return json.loads(text), removed
        except json.JSONDecodeError as exc:
            stray = exc.pos < len(text) and text[exc.pos] in '}]' and exc.msg in ("Expecting ',' delimiter", 'Extra data')
            if not stray or removed >= 3:
                raise ValueError('Not a JSON object: ' + str(exc)) from None
            text, removed = text[:exc.pos] + text[exc.pos + 1:], removed + 1


class Recorder:
    """What the session expects of its host: evidence records and an optional database."""

    def __init__(self):
        self.evidence, self.count, self.closed = None, 0, set()

    def record(self, data):
        self.count += 1
        if self.evidence is None:
            return None
        path = self.evidence / ('bridge-%04d.json' % self.count)
        path.write_text(json.dumps(data, default=str, indent=1))
        return path

    def has_database(self):
        return False

    def database_request(self, command):
        raise ValueError('No IP database is mounted in this session')


class BridgeSession(CEDSession):
    """The CED session with Vivado reached through the pipe instead of the MCP."""

    # Ops run to completion: the Tcl side holds the vivado_execute call open meanwhile,
    # and there is no MCP relay that could return early and keep working.
    REPLY_BUDGET = 10 ** 6

    async def native(self, expression, prefix=''):
        self.native_serial += 1
        script = prefix + '\nset __ipcfg_reply [cedcfg::plain ' + expression + ']'
        started = time.monotonic()
        send('TCL', script)
        line = sys.stdin.readline()
        if not line:
            raise SystemExit('Vivado closed the pipe')
        kind, _, body = line.strip().partition(' ')
        result = decode(body)
        self.guard.record({'native': True, 'serial': self.native_serial, 'command': script, 'status': kind,
                           'elapsed_seconds': round(time.monotonic() - started, 3), 'result': result[-4000:]})
        if kind != 'OK':
            raise ValueError('NATIVE_ERROR: ' + result[-700:])
        return result

    async def close(self):
        """Save state is already on disk; Vivado itself is stopped by the model with vivado_stop."""
        try:
            await self.native('[close_project -quiet]')
        except ValueError:
            return False
        self.guard.closed.add(self.sid)
        return True


class Bridge:
    def __init__(self, log_file):
        self.log_file = log_file
        self.recorder = Recorder()
        self.session = None
        self.failures = {}
        # The highest step number of an op that succeeded. The stock MCP silently skips
        # a write identical to one that succeeded in the last two minutes, and ced_op is
        # stateful: in ced-gen_H3 (Haiku) the propose that follows a narrowing assign
        # was the same text as an earlier one, never reached Vivado, and nothing was
        # applied. An increasing "seq" makes every call distinct. Only success advances
        # it, because the MCP lets a failed command be retried unchanged.
        self.last_seq = 0

    def take_seq(self, request):
        seq = request.pop('seq', None)
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= self.last_seq:
            raise ValueError('Every op needs "seq", a step number greater than the last that succeeded; send it again with '
                             '"seq":%d. (Vivado MCP skips a command identical to one that just succeeded, so a repeated '
                             'step would not run.)' % (self.last_seq + 1))
        return seq

    def fresh(self):
        self.session = BridgeSession(self.recorder, None, 'vivado', self.log_file)
        # The run root as cedcfg::begin derives it.
        evidence = run_root(self.log_file) / 'evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        self.recorder.evidence = evidence

    async def handle(self, command):
        request = seq = None
        try:
            request, repaired = parse_request(command)
            if not isinstance(request, dict) or len(command) > 16000:
                raise ValueError('Send one JSON object with an "op" field')
            seq = self.take_seq(request)
            request.pop('session_id', None)
            if self.session is None or (request.get('op') == 'begin' and self.session.stage == 'finished'):
                self.fresh()
            if request.get('op') == APPROVE:
                if set(request) - {'op', 'proposal_id'}:
                    raise ValueError(APPROVE + ' takes proposal_id only')
                reply = await self.session.approve(request.get('proposal_id'))
            else:
                reply = await self.session.request(request)
            if repaired and isinstance(reply, dict):
                reply = dict(reply, request_repaired='Removed %d unmatched closing bracket(s)' % repaired)
            self.failures.clear()
            self.last_seq = seq
            self.recorder.record({'seq': seq, 'request': request, 'reply': reply, 'failed': False})
            return 'DONE', json.dumps(reply, separators=(',', ':'), default=str)
        except Exception as exc:
            self.recorder.record({'seq': seq, 'request': request, 'error': str(exc), 'failed': True})
            message = 'STRUCTURED_ERROR: ' + str(exc)
            if self.session is not None:
                snapshot = self.session.state()
                workflow = {k: snapshot.get(k) for k in ('stage', 'next_op')}
                identity = (json.dumps(request, sort_keys=True, default=str), str(exc))
                self.failures[identity] = count = self.failures.get(identity, 0) + 1
                if count >= 3 and self.session.initialized:
                    reply = await self.session.finish_partial('Repeated unchanged workflow failure; ' + str(exc)[:160])
                    self.last_seq = max(self.last_seq, seq or 0)
                    return 'DONE', json.dumps(dict(reply, failure_stop=True), separators=(',', ':'), default=str)
                if count >= 2:
                    workflow.update(repeat_count=count,
                                    guidance='Change the request using next_op; one more identical failure finishes partial')
                message += ' ' + json.dumps({'workflow': workflow}, separators=(',', ':'))
            return 'FAIL', message


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--log-file', required=True, help="Vivado's session log under <working_dir>/.vivado-ai")
    parser.add_argument('--ced-cache', required=True, help='Absolute mirror cache directory for the CED store')
    args = parser.parse_args()
    CEDSession.cache = args.ced_cache
    bridge = Bridge(args.log_file)
    for line in sys.stdin:
        kind, _, body = line.strip().partition(' ')
        if kind != 'OP':
            send('FAIL', 'Expected an OP line, got ' + kind[:20])
            continue
        send(*await bridge.handle(decode(body)))


if __name__ == '__main__':
    asyncio.run(main())
