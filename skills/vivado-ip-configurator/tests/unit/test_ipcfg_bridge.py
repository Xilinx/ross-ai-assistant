# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""scripts/ipcfg_bridge.py is what the model actually talks to, so its protocol is tested.

Everything here runs the bridge as lib/ipcfg_op.tcl runs it -- a real process, real
stdio -- because the parts worth protecting are the boundary ones: a stray print must
never reach the protocol stream, a malformed op must come back as a readable refusal
instead of a dead pipe, a model repeating the same broken request must be stopped
rather than looped, and every op must carry a new "seq" so the stock MCP never skips
one as a duplicate.

Only ops that need no Vivado are sent ({"op":"status"} and refusals). Where the bridge
asks Vivado something, the test answers on the Tcl side of the pipe. The workflow
itself is covered by tests/unit/test_structured_config.py and by the eval suites.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
BRIDGE = SKILL / 'scripts' / 'ipcfg_bridge.py'


def encode(text):
    return base64.b64encode(text.encode()).decode('ascii')


def op(text):
    return 'OP ' + encode(text)


class BridgeProtocolTests(unittest.TestCase):
    def speak(self, *lines):
        """Send raw protocol lines to a fresh bridge; returns [(kind, payload), ...].

        The log file is placed as the MCP places it, under <run>/proj/.vivado-ai,
        since the bridge derives the run root (and so the evidence folder) from it.
        """
        root = Path(tempfile.mkdtemp(prefix='ipcfg-bridge-'))
        self.addCleanup(shutil.rmtree, root, True)
        log = root / 'proj' / '.vivado-ai' / 'session.log'
        log.parent.mkdir(parents=True)
        log.write_text('')
        env = dict(os.environ, PYTHONPYCACHEPREFIX=str(root / 'pycache'))
        done = subprocess.run([sys.executable, '-u', str(BRIDGE), '--log-file', str(log)],
                              input=''.join(line + '\n' for line in lines),
                              capture_output=True, text=True, timeout=300, env=env, cwd=str(root))
        replies = []
        for line in done.stdout.splitlines():
            kind, _, body = line.partition(' ')
            replies.append((kind, base64.b64decode(body).decode() if body else ''))
        self.root, self.stderr = root, done.stderr
        return replies

    def ops(self, *commands):
        return self.speak(*[op(command) for command in commands])

    def workflow(self, message):
        """The state a refusal carries after the message, so the model can recover from it."""
        return json.loads(message[message.index('{"workflow"'):].split(' {"request_schema"')[0])['workflow']

    def test_the_protocol_stream_carries_only_protocol(self):
        """The workflow prints and logs freely; one stray line in stdout would desync Tcl."""
        replies = self.ops('{"seq":1,"op":"status"}')
        self.assertEqual([kind for kind, _ in replies], ['DONE'], self.stderr)
        self.assertEqual(json.loads(replies[0][1])['next_op'], 'begin')

    def test_a_line_that_is_not_an_op_is_refused_without_killing_the_bridge(self):
        replies = self.speak('HELLO ' + encode('x'), op('{"seq":1,"op":"status"}'))
        self.assertEqual([kind for kind, _ in replies], ['FAIL', 'DONE'])
        self.assertIn('Expected an OP line', replies[0][1])

    def test_an_unmatched_closing_brace_is_repaired_and_the_reply_says_so(self):
        replies = self.ops('{"seq":1,"op":"status"}}}')
        self.assertEqual(replies[0][0], 'DONE')
        self.assertIn('Removed 2 unmatched', json.loads(replies[0][1])['request_repaired'])

    def test_too_much_damage_is_refused_rather_than_guessed_at(self):
        replies = self.ops('{"seq":1,"op":"status"}}}}}')
        self.assertEqual(replies[0][0], 'FAIL')

    def test_something_that_is_not_an_object_is_refused(self):
        replies = self.ops('["op","status"]')
        self.assertEqual(replies[0][0], 'FAIL')
        self.assertIn('one JSON object', replies[0][1])

    def test_reply_fields_echoed_into_a_request_are_removed_and_reported(self):
        """Small models copy the previous reply's stage/next_op into the next request."""
        replies = self.ops('{"seq":1,"operation":"status","stage":"new","session_id":"vivado-1"}')
        self.assertEqual(replies[0][0], 'DONE')
        repaired = json.loads(replies[0][1])['request_repaired']
        for words in ('Renamed operation to op', 'Removed response-only stage', 'Removed session_id'):
            self.assertIn(words, repaired)

    def test_an_unchanged_failing_request_is_told_to_change_and_then_stopped(self):
        """The third identical failure finishes partial instead of looping; before begin there is
        no cell, so the bridge asks Vivado whether a project is open and keeps it either way."""
        replies = self.speak(op('{"seq":1,"op":"nonsense"}'), op('{"seq":1,"op":"nonsense"}'),
                             op('{"seq":1,"op":"nonsense"}'), 'OK ' + encode(''))
        kinds = [kind for kind, _ in replies]
        self.assertEqual(kinds, ['FAIL', 'FAIL', 'TCL', 'DONE'], self.stderr)
        self.assertNotIn('repeat_count', replies[0][1])
        self.assertIn('one more identical failure', self.workflow(replies[1][1])['guidance'])
        self.assertIn('current_project', replies[2][1])
        final = json.loads(replies[3][1])
        self.assertTrue(final['failure_stop'])
        self.assertTrue(final['session_preserved'])
        self.assertEqual(final['next_op'], 'begin')

    def test_every_op_leaves_an_evidence_record(self):
        """Evidence is the only account of what the model sent once Vivado is gone."""
        self.ops('{"seq":1,"op":"status"}', '{"seq":2,"op":"nonsense"}')
        records = sorted((self.root / 'evidence').glob('bridge-*.json'))
        self.assertEqual(len(records), 2)
        first, second = (json.loads(path.read_text()) for path in records)
        self.assertEqual([first['failed'], second['failed']], [False, True])
        self.assertEqual([first['seq'], second['seq']], [1, 2])
        self.assertEqual(second['request'], {'op': 'nonsense'})

    # The stock Vivado MCP skips a write identical to one that succeeded in the last two
    # minutes, and every ipcfg_op is a write to it. A design is many cells, each ending in
    # {"op":"apply"} and {"op":"finish"}: without "seq" the second cell's would never run.

    def test_an_op_without_seq_is_refused_and_told_the_number_to_send(self):
        replies = self.ops('{"op":"status"}')
        self.assertEqual(replies[0][0], 'FAIL')
        self.assertIn('"seq":1', replies[0][1])

    def test_the_same_op_runs_again_under_a_new_seq(self):
        replies = self.ops('{"seq":1,"op":"status"}', '{"seq":2,"op":"status"}')
        self.assertEqual([kind for kind, _ in replies], ['DONE', 'DONE'])

    def test_a_seq_that_does_not_increase_is_refused(self):
        replies = self.ops('{"seq":4,"op":"status"}', '{"seq":4,"op":"status"}', '{"seq":3,"op":"status"}')
        self.assertEqual([kind for kind, _ in replies], ['DONE', 'FAIL', 'FAIL'])
        self.assertIn('"seq":5', replies[1][1])

    def test_a_failed_op_can_be_retried_under_the_same_seq(self):
        """The MCP always lets a failed command run again, so only success advances seq."""
        replies = self.ops('{"seq":1,"op":"nonsense"}', '{"seq":1,"op":"status"}')
        self.assertEqual([kind for kind, _ in replies], ['FAIL', 'DONE'])

    def test_seq_must_be_a_whole_number(self):
        for bad in ('true', '"2"', '1.5', 'null'):
            with self.subTest(seq=bad):
                replies = self.ops('{"seq":%s,"op":"status"}' % bad)
                self.assertEqual(replies[0][0], 'FAIL')
                self.assertIn('"seq":1', replies[0][1])

    def test_the_bridge_imports_nothing_vivados_python_lacks(self):
        """It runs under Vivado's bundled Python: no mcp, no tclsh, standard library only."""
        replies = self.speak(op('{"seq":1,"op":"status"}'))
        self.assertEqual(replies[0][0], 'DONE')
        probe = subprocess.run(
            [sys.executable, '-c', 'import sys; sys.argv=["x","--log-file","/nonexistent/a/b/c.log"]; '
             'sys.path.insert(0, %r); import ipcfg_bridge; sys.__stdout__.write(repr(sorted(m for m in sys.modules if m.split(".")[0] in '
             '("mcp", "torch", "sqlite_vec", "sentence_transformers", "guard_mcp"))) + chr(10))' % str(SKILL / 'scripts')],
            capture_output=True, text=True, timeout=60, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(probe.stdout.strip().splitlines()[-1], '[]', probe.stderr)

    def test_status_hands_back_the_last_finished_steps_reply(self):
        """A step can finish after the client has abandoned its call (60 s in Claude Code);
        status is how the model gets that step's reply back."""
        replies = self.ops('{"seq":1,"op":"status"}', '{"seq":2,"op":"apply"}', '{"seq":3,"op":"status"}')
        self.assertEqual([kind for kind, _ in replies], ['DONE', 'DONE', 'DONE'], self.stderr)
        self.assertNotIn('last_step', json.loads(replies[0][1]))
        last = json.loads(replies[2][1])['last_step']
        self.assertEqual((last['seq'], last['op']), (2, 'apply'))
        self.assertEqual(last['reply'], json.loads(replies[1][1]))


class SessionSeqTests(unittest.TestCase):
    def test_a_step_number_is_accepted_and_ignored_by_the_session(self):
        """SKILL.md numbers every op for the stock MCP; behind the relay that number is not
        required, but a model that copies the skill's ops must not be refused for it."""
        import asyncio
        sys.path.insert(0, str(SKILL / 'scripts'))
        from structured_config import StructuredSession

        class Guard:
            def record(self, data):
                return None
        session = StructuredSession(Guard(), None, 'sid', '/nonexistent/a/b/c.log')
        reply = asyncio.run(session.request({'seq': 7, 'op': 'status'}))
        self.assertEqual(reply['next_op'], 'begin')


if __name__ == '__main__':
    unittest.main()


class LiveStandInTests(unittest.TestCase):
    def test_an_unsupported_catalog_match_gets_its_stand_in_without_a_database(self):
        """ip-configurator-test-kit_26 on 2026.1: axi_noc reached begin only as a catalog match
        the Versal Gen2 part cannot host, with no axi_noc2 beside it, and the agent stopped."""
        import asyncio
        sys.path.insert(0, str(SKILL / 'scripts'))
        from structured_config import StructuredSession
        from tcl_data import join_list

        class Guard:
            def record(self, data):
                return None

        class Session(StructuredSession):
            async def native(self, expression, prefix=''):
                self.asked = expression
                alt = join_list(['vlnv', 'v:ip:axi_noc2', 'display_name', 'AXI NoC2', 'match', 'name_family'])
                row = join_list(['vlnv', 'v:ip:axi_noc:1.1', 'status', 'WRONG_PART', 'alternatives', join_list([alt])])
                return join_list(['AVAILABILITY:OK', 'rows', join_list([row])])

        session = Session(Guard(), None, 'sid', '/nonexistent/a/b/c.log')
        session.part, session.build = 'xc2ve3558-sfva1440-2MP-e-S', '6511674'
        session.ip_candidates = {'i1': dict(vlnv='v:ip:pcie_versal:1.1')}
        session.unavailable_ips = {'v:ip:axi_noc:1.1': dict(vlnv='v:ip:axi_noc:1.1')}
        asyncio.run(session.add_database_alternatives())
        self.assertIn('lite::candidate_availability', session.asked)
        added = [row for row in session.ip_candidates.values() if row['vlnv'] == 'v:ip:axi_noc2']
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]['alternative_to'], 'v:ip:axi_noc:1.1')
        self.assertEqual(added[0]['alternative_match'], 'name_family')
