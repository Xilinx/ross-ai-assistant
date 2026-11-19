# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""compact_reply: repeated per-row provenance values are stated once, and nothing is lost.

A 52 KB describe reply for ip-configurator-test-kit_21 was cut off by OpenCode, so Gemma never saw
its candidates (Vivado 2026.2, 2026-11-15). On the 41 KB reply of a passing trial, compact_reply
removes 9 KB.
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from structured_config import compact_reply


def row(i, **extra):
    base = dict(id='r1.p%d' % i, key='CONFIG.K%d' % i, range_scope='unavailable',
                live_scope='current_cell', enabled=True, current=str(i))
    base.update(extra)
    return base


def expand(reply):
    """What the agent is told: a row without a defaulted field has the default value."""
    defaults = {k: v for k, v in reply.get('row_defaults', {}).items() if k != 'note'}

    def walk(node):
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k != 'row_defaults'}
            if 'id' in node and 'key' in node:
                out = dict(defaults, **out)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node
    return walk(reply)


class CompactReplyTests(unittest.TestCase):
    def test_repeated_values_move_to_row_defaults_and_expand_back_exactly(self):
        reply = dict(stage='discovering', candidates=[
            dict(requirement_id='r1', rows=[row(1), row(2), row(3, enabled=False)])])
        original = copy.deepcopy(reply)
        compact = compact_reply(reply)
        self.assertEqual(reply, original)                      # the session's copy is untouched
        self.assertEqual(compact['row_defaults']['range_scope'], 'unavailable')
        self.assertTrue(compact['row_defaults']['enabled'])
        rows = compact['candidates'][0]['rows']
        self.assertNotIn('range_scope', rows[0])
        self.assertFalse(rows[2]['enabled'])                   # the odd value stays on its row
        self.assertEqual(expand(compact), original)

    def test_a_field_some_rows_lack_is_not_defaulted(self):
        # Defaulting it would give the rows without it a value they never had.
        rows = [row(1), row(2), row(3)]
        del rows[2]['live_scope']
        compact = compact_reply(dict(stage='discovering', rows=rows))
        self.assertNotIn('live_scope', compact['row_defaults'])
        self.assertEqual(compact['rows'][0]['live_scope'], 'current_cell')

    def test_values_and_identity_fields_are_never_defaulted(self):
        rows = [row(1, current='4', type='long'), row(2, current='4', type='long')]
        compact = compact_reply(dict(rows=rows))
        for field in ('current', 'type', 'id', 'key'):
            self.assertNotIn(field, compact['row_defaults'])
            self.assertIn(field, compact['rows'][0])

    def test_a_reply_without_rows_is_returned_as_is(self):
        reply = dict(stage='ready', next_op='apply')
        self.assertIs(compact_reply(reply), reply)


if __name__ == '__main__':
    unittest.main()
