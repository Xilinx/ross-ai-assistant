# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""scripts/ced_bridge.py is what the model actually talks to, so its protocol is tested.

Everything here runs the bridge as lib/ced_op.tcl runs it -- a real process, real
stdio -- because the parts worth protecting are the boundary ones: a stray print must
never reach the protocol stream, a malformed op must come back as a readable refusal
instead of a dead pipe, and a model repeating the same broken request must be stopped
rather than looped.

Only ops that need no Vivado are sent ({"op":"status"} and refusals); the workflow
itself is covered by tests/unit/test_ced_session.py and by the eval suite.
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
BRIDGE = SKILL / 'scripts' / 'ced_bridge.py'


def encode(text):
    return base64.b64encode(text.encode()).decode('ascii')


class BridgeProtocolTests(unittest.TestCase):
    def speak(self, *lines):
        """Send raw protocol lines to a fresh bridge; returns [(kind, payload), ...].

        The log file is placed as the MCP places it, under <run>/work/.vivado-ai,
        since the bridge derives the run root (and so the evidence folder) from it.
        """
        root = Path(tempfile.mkdtemp(prefix='ced-bridge-'))
        self.addCleanup(shutil.rmtree, root, True)
        log = root / 'work' / '.vivado-ai' / 'session.log'
        log.parent.mkdir(parents=True)
        log.write_text('')
        env = dict(os.environ, PYTHONPYCACHEPREFIX=str(root / 'pycache'))
        done = subprocess.run(
            [sys.executable, '-u', str(BRIDGE), '--log-file', str(log), '--ced-cache', str(root / 'mirror')],
            input=''.join(line + '\n' for line in lines),
            capture_output=True, text=True, timeout=300, env=env, cwd=str(root))
        replies = []
        for line in done.stdout.splitlines():
            kind, _, body = line.partition(' ')
            replies.append((kind, base64.b64decode(body).decode() if body else ''))
        self.root = root
        return replies

    def ops(self, *commands):
        return self.speak(*['OP ' + encode(command) for command in commands])

    def test_the_protocol_stream_carries_only_protocol(self):
        """The workflow prints and logs freely; one stray line in stdout would desync Tcl."""
        replies = self.ops('{"seq":1,"op":"status"}')
        self.assertEqual([kind for kind, _ in replies], ['DONE'])
        self.assertEqual(json.loads(replies[0][1])['next_op'], 'begin')

    def test_a_line_that_is_not_an_op_is_refused_without_killing_the_bridge(self):
        replies = self.speak('HELLO ' + encode('x'), 'OP ' + encode('{"seq":1,"op":"status"}'))
        self.assertEqual([kind for kind, _ in replies], ['FAIL', 'DONE'])
        self.assertIn('Expected an OP line', replies[0][1])

    def test_an_unmatched_closing_brace_is_repaired_and_the_reply_says_so(self):
        """Models emit `ced_op {{...}}}`; the repair is silent otherwise, which hides the habit."""
        replies = self.ops('{"seq":1,"op":"status"}}}')
        self.assertEqual(replies[0][0], 'DONE')
        reply = json.loads(replies[0][1])
        self.assertEqual(reply['stage'], 'new')
        self.assertIn('Removed 2', reply['request_repaired'])

    def test_too_much_damage_is_refused_rather_than_guessed_at(self):
        replies = self.ops('{"seq":1,"op":"status"}}}}}')
        self.assertEqual(replies[0][0], 'FAIL')
        self.assertIn('Not a JSON object', replies[0][1])

    def test_something_that_is_not_an_object_is_refused(self):
        replies = self.ops('["op","status"]')
        self.assertEqual(replies[0][0], 'FAIL')
        self.assertIn('one JSON object', replies[0][1])

    def test_a_session_id_copied_from_vivado_start_is_ignored(self):
        """The relay took one, so models send one here too; the ops themselves reject stray fields."""
        replies = self.ops('{"seq":1,"op":"status","session_id":"abc123"}')
        self.assertEqual(replies[0][0], 'DONE')
        self.assertEqual(json.loads(replies[0][1])['stage'], 'new')

    def test_the_approval_op_takes_a_proposal_id_and_nothing_else(self):
        replies = self.ops('{"seq":1,"op":"ced_apply_phase2","proposal_id":"p1","selections":[]}')
        self.assertEqual(replies[0][0], 'FAIL')
        self.assertIn('takes proposal_id only', replies[0][1])

    def workflow(self, message):
        """The state a refusal carries after the message, so the model can recover from it."""
        return json.loads(message[message.index('{"workflow"'):])['workflow']

    def test_an_unchanged_failing_request_is_told_to_change_and_then_stopped(self):
        """ced-gen_H5: a model that cannot read the refusal will send it forever."""
        # A model that does number its steps still repeats itself: seq is not part of the request.
        replies = self.ops(*['{"seq":%d,"op":"nonsense"}' % n for n in (1, 2, 3)])
        self.assertEqual([kind for kind, _ in replies], ['FAIL'] * 3)
        self.assertNotIn('repeat_count', replies[0][1])
        self.assertEqual(self.workflow(replies[0][1])['next_op'], 'begin')
        for _, message in replies[1:]:
            self.assertIn('one more identical failure', self.workflow(message)['guidance'])
        self.assertEqual(self.workflow(replies[2][1])['repeat_count'], 3)

    def test_a_different_failure_does_not_count_towards_the_stop(self):
        replies = self.ops('{"seq":1,"op":"nonsense"}', '["op","status"]', '{"seq":2,"op":"nonsense"}')
        self.assertEqual([kind for kind, _ in replies], ['FAIL'] * 3)
        self.assertIn('repeat_count', replies[2][1], 'the same request failing twice is still a repeat')

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
    # minutes, and every ced_op is a write to it. In ced-gen_H3 (Haiku) the propose that
    # followed a narrowing assign was the same text as an earlier propose, so it never
    # ran; four retries were skipped too and nothing was applied. "seq" makes every
    # command distinct; these pin the rule that guarantees it.

    def test_an_op_without_seq_is_refused_and_told_the_number_to_send(self):
        replies = self.ops('{"op":"status"}')
        self.assertEqual(replies[0][0], 'FAIL')
        self.assertIn('"seq":1', replies[0][1])

    def test_the_same_op_runs_again_under_a_new_seq(self):
        replies = self.ops('{"seq":1,"op":"status"}', '{"seq":2,"op":"status"}')
        self.assertEqual([kind for kind, _ in replies], ['DONE', 'DONE'])

    def test_a_seq_that_does_not_increase_is_refused(self):
        """Accepting it would let the model send a command text the MCP may already have skipped."""
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


if __name__ == '__main__':
    unittest.main()
