# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""lib/ced_op.tcl is the only code the model's Tcl ever runs, so its loop is tested.

On the stock MCP the model sends `ced_op {<json>}` and nothing else; everything the
workflow needs comes back over the pipe as Tcl for Vivado to evaluate. That loop has
to carry a result verbatim in both directions, turn a failed evaluation into ERR
rather than killing the call, and give a readable error when the helper dies -- which
is what the model would otherwise report as an unexplained empty reply.

The helper is tests/fixtures/fake_ced_helper.py; ced_op::start is bypassed, since
finding Vivado's bundled Python and its session log needs Vivado.
"""
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
DRIVER = SKILL / 'tests' / 'fixtures' / 'ced_op_driver.tcl'
HELPER = SKILL / 'tests' / 'fixtures' / 'fake_ced_helper.py'
TCLSH = shutil.which('tclsh')


@unittest.skipIf(TCLSH is None, 'tclsh is not installed')
class CedOpProtocolTests(unittest.TestCase):
    def run_op(self, scenario, json_text='{"op":"status"}'):
        """One ced_op call; returns (rc, result, what the helper received)."""
        root = tempfile.mkdtemp(prefix='ced-op-')
        self.addCleanup(shutil.rmtree, root, True)
        record = Path(root, 'transcript.json')
        done = subprocess.run(
            [TCLSH, str(DRIVER), str(SKILL), json_text, sys.executable, str(HELPER), scenario, str(record)],
            capture_output=True, text=True, timeout=120, cwd=root)
        lines = dict(line.split(' ', 1) for line in done.stdout.splitlines() if line.startswith(('RC ', 'RESULT ')))
        self.assertIn('RC', lines, 'the driver produced no verdict:\n' + done.stdout + done.stderr)
        result = base64.b64decode(lines.get('RESULT', '')).decode()
        transcript = json.loads(record.read_text()) if record.exists() else []
        return int(lines['RC']), result, transcript

    def test_a_finished_op_returns_the_helpers_reply(self):
        rc, result, transcript = self.run_op('done')
        self.assertEqual(rc, 0, result)
        self.assertEqual(json.loads(result), {'stage': 'new'})
        self.assertEqual(transcript[0]['kind'], 'OP')

    def test_the_op_text_reaches_the_helper_unchanged(self):
        """Base64 both ways is what lets a JSON value hold a brace or a non-ASCII character."""
        op = json.dumps({'op': 'begin', 'task': 'build a design {with braces}\nand a µs timer'})
        rc, result, transcript = self.run_op('echo', op)
        self.assertEqual(rc, 0, result)
        self.assertEqual(transcript[0]['body'], op)
        self.assertEqual(result, op)

    def test_tcl_the_helper_sends_is_evaluated_and_its_result_sent_back(self):
        rc, result, transcript = self.run_op('tcl')
        self.assertEqual(rc, 0, result)
        self.assertEqual([transcript[1]['kind'], transcript[1]['body']], ['OK', '42'])

    def test_a_failed_evaluation_comes_back_as_ERR_and_the_call_continues(self):
        """The workflow reads its own failures; a broken command must not end the op."""
        rc, result, transcript = self.run_op('tcl_error')
        self.assertEqual(transcript[1]['kind'], 'ERR')
        self.assertIn('boom', transcript[1]['body'])
        self.assertEqual(rc, 0, 'the op still finished through its DONE: ' + result)

    def test_a_refused_op_raises_with_the_helpers_message(self):
        rc, result, _ = self.run_op('fail')
        self.assertEqual(rc, 1)
        self.assertEqual(result, 'STRUCTURED_ERROR: no good')

    def test_an_unknown_protocol_line_is_refused_by_kind(self):
        rc, result, _ = self.run_op('bogus')
        self.assertEqual(rc, 1)
        self.assertIn('unexpected helper line WAT', result)

    def test_a_helper_that_dies_points_at_its_own_stderr_log(self):
        """Otherwise the model sees an empty reply and no reason for it."""
        rc, result, _ = self.run_op('exit')
        self.assertEqual(rc, 1)
        self.assertIn('the workflow helper exited', result)
        self.assertIn('ced_bridge.stderr.log', result)


if __name__ == '__main__':
    unittest.main()
