# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""lib/ipcfg_op.tcl is the only code the model's Tcl ever runs, so its loop is tested.

On the stock MCP the model sends `ipcfg_op {<json>}` and nothing else; everything the
workflow needs comes back over the pipe as Tcl for Vivado to evaluate. That loop has
to carry a result verbatim in both directions, turn a failed evaluation into ERR
rather than killing the call, and give a readable error when the helper dies -- which
is what the model would otherwise report as an unexplained empty reply.

The helper is tests/fixtures/fake_ipcfg_helper.py; ipcfg_op::start is bypassed, since
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
DRIVER = SKILL / 'tests' / 'fixtures' / 'ipcfg_op_driver.tcl'
HELPER = SKILL / 'tests' / 'fixtures' / 'fake_ipcfg_helper.py'
TCLSH = shutil.which('tclsh')


@unittest.skipIf(TCLSH is None, 'tclsh is not installed')
class IpcfgOpProtocolTests(unittest.TestCase):
    def run_calls(self, scenario, calls, budget=40000):
        """Several ipcfg_op calls on one helper; returns ([(rc, result)], what the helper received)."""
        root = tempfile.mkdtemp(prefix="ipcfg-op-")
        self.addCleanup(shutil.rmtree, root, True)
        record = Path(root, 'transcript.json')
        tcl_list = ' '.join('{' + call + '}' for call in calls)
        done = subprocess.run(
            [TCLSH, str(DRIVER), str(SKILL), str(budget), tcl_list, sys.executable, str(HELPER), scenario, str(record)],
            capture_output=True, text=True, timeout=120, cwd=root)
        lines = [line.split(' ', 1) for line in done.stdout.splitlines() if line.startswith(('RC ', 'RESULT '))]
        self.assertEqual(len(lines), 2 * len(calls), 'the driver produced no verdict:\n' + done.stdout + done.stderr)
        results = [(int(lines[i][1]), base64.b64decode(lines[i + 1][1] if len(lines[i + 1]) > 1 else '').decode())
                   for i in range(0, len(lines), 2)]
        transcript = json.loads(record.read_text()) if record.exists() else []
        return results, transcript

    def run_op(self, scenario, json_text='{"op":"status"}'):
        """One ipcfg_op call; returns (rc, result, what the helper received)."""
        results, transcript = self.run_calls(scenario, [json_text])
        return results[0][0], results[0][1], transcript

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
        self.assertIn('ipcfg_bridge.stderr.log', result)

    # A client abandons an MCP call after about a minute (Claude Code: 60 s) while Vivado
    # keeps working; ip-configurator-test-kit_21 and _26 lost a select and a begin that way.
    # ipcfg_op hands back "running" within its budget and status resumes the same step.

    def test_a_step_past_its_budget_returns_running_and_status_finishes_it(self):
        results, transcript = self.run_calls('slow', [
            '{"seq":5,"op":"select","selections":[]}', '{"seq":6,"op":"status"}', '{"seq":7,"op":"status"}',
            '{"seq":8,"op":"status"}'], budget=250)
        running = [json.loads(result) for rc, result in results[:3]]
        self.assertEqual([rc for rc, _ in results], [0, 0, 0, 0])
        self.assertEqual([r['stage'] for r in running], ['running'] * 3)
        self.assertEqual(running[0]['running_step'], 'select')
        self.assertEqual(json.loads(results[3][1]), {'stage': 'ready', 'step': 'select'})
        # The helper saw the step once and every round trip answered; status never reached it.
        self.assertEqual([t['kind'] for t in transcript], ['OP', 'OK', 'OK', 'OK'])

    def test_a_new_step_is_refused_while_one_is_in_flight(self):
        results, transcript = self.run_calls('slow', [
            '{"seq":5,"op":"select","selections":[]}', '{"seq":6,"op":"apply"}', '{"seq":7,"op":"status"}'], budget=250)
        self.assertEqual(results[1][0], 1)
        self.assertIn('still working on the select step', results[1][1])
        self.assertEqual(json.loads(results[2][1])['stage'], 'running')
        self.assertEqual([t.get('kind') for t in transcript].count('OP'), 1)

    def test_within_its_budget_a_step_returns_its_reply_directly(self):
        results, _ = self.run_calls('slow', ['{"seq":1,"op":"select","selections":[]}'], budget=40000)
        self.assertEqual(json.loads(results[0][1]), {'stage': 'ready', 'step': 'select'})

    def test_the_json_written_once_reaches_the_helper_whole(self):
        """`ipcfg_op {<json>}`: Tcl strips the braces around the word and ipcfg_op restores
        them, so a model never has to count an extra pair. Twelve selects ending in "}]"
        lost one with the doubled form and left Vivado waiting for the rest of the command."""
        stripped = '"seq":4,"op":"select","selections":[{"requirement_id":"r1","value":0}]'
        results, transcript = self.run_calls('echo', [stripped])
        self.assertEqual(results[0], (0, '{' + stripped + '}'))
        self.assertEqual(json.loads(transcript[0]['body'])['selections'][0]['value'], 0)


if __name__ == '__main__':
    unittest.main()
