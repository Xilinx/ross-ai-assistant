# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The extension points this skill needs from its bundled lite engine (engine/).

ced_guard.serve(...) and ced_mcp_bridge.py drive the bundled engine's
guard_mcp with CED schemas, a CED session class and a guard-owned approval tool,
and Phase 2 borrows lite's StructuredSession through StructuredSession.adopted
on cells that live inside generated hierarchies. All of it defaults to, or sits
beside, lite's own behaviour, so lite's tests pass whether or not it exists -- a
promotion that rewrites those files drops it silently and Phase 2 then fails at
run time with an AttributeError or 'simple cell name required'. This asserts the
surface instead, so the break surfaces here rather than in a live eval run.
"""
import inspect
import os
from pathlib import Path
import re
import sys
import unittest

SKILL = Path(__file__).resolve().parents[2]
LITE = Path(os.environ.get('CED_LITE_SKILL', SKILL / 'engine')).resolve()
sys.path[:0] = [str(SKILL / 'scripts'), str(SKILL / 'examples'), str(LITE / 'scripts')]

import guard_mcp  # noqa: E402


class LiteGuardContractTests(unittest.TestCase):
    def test_serve_accepts_every_parameter_ced_guard_passes(self):
        expected = {'session_factory', 'tool_specs', 'extra_handlers',
                    'server_name', 'upstream_env', 'start_env'}
        parameters = inspect.signature(guard_mcp.serve).parameters
        self.assertTrue(expected <= set(parameters),
                        'guard_mcp.serve is missing ' + ', '.join(sorted(expected - set(parameters))))
        for name in expected:
            self.assertIsNot(parameters[name].default, inspect.Parameter.empty,
                             name + ' must default so lite keeps working without it')

    def test_execution_guard_accepts_a_session_factory(self):
        parameters = inspect.signature(guard_mcp.ExecutionGuard.__init__).parameters
        self.assertIn('session_factory', parameters)
        self.assertIsNone(parameters['session_factory'].default)

    def test_structured_definitions_accepts_replacement_specs(self):
        specs = {'ced_apply_phase2': ('Apply an approved proposal.', {
            'session_id': {'type': 'string'}, 'proposal_id': {'type': 'string'}},
            ['session_id', 'proposal_id'])}
        tools = guard_mcp.structured_definitions([], specs)
        self.assertEqual([t.name for t in tools], ['ced_apply_phase2'])

    def test_structured_definitions_without_specs_still_serves_lite(self):
        names = {t.name for t in guard_mcp.structured_definitions([])}
        self.assertEqual(names, {'vivado_start', 'vivado_execute', 'vivado_stop'})

    def test_ced_guard_imports_against_the_installed_lite(self):
        import ced_guard
        self.assertTrue(set(ced_guard.SPECS) >= {'vivado_start', 'vivado_execute',
                                                 'vivado_stop', 'ced_apply_phase2'})


class AdoptionContractTests(unittest.TestCase):
    """Phase 2 runs lite's engine on cells of a design the CED workflow built."""

    def test_structured_session_offers_the_adopted_constructor(self):
        import structured_config
        adopted = getattr(structured_config.StructuredSession, 'adopted', None)
        self.assertIsNotNone(adopted, 'StructuredSession.adopted is gone; ced_session op_assign needs it')
        expected = ['guard', 'upstream', 'sid', 'log_file', 'cell', 'part', 'task', 'build', 'requirements']
        self.assertEqual(list(inspect.signature(adopted).parameters), expected)

    def test_lite_accepts_a_hierarchical_cell_path(self):
        """Generated designs put IP inside hierarchies; lite_ced::identity must take the path."""
        source = (LITE / 'lib/lite.tcl').read_text()
        pattern = re.search(r'proc lite_ced::identity \{cell\} \{.*?\n\}', source, re.S)
        self.assertIsNotNone(pattern, 'lite_ced::identity is gone')
        self.assertIn('(/[A-Za-z_][A-Za-z0-9_]*)*', pattern[0],
                      'lite_ced::identity rejects hierarchical cell paths such as tmr_0/MB1/axi_uartlite_0')

    def test_evidence_filenames_flatten_a_hierarchical_cell(self):
        source = (LITE / 'lib/lite.tcl').read_text()
        pattern = re.search(r'proc lite_ced::evidence_path \{cell label\} \{.*?\n\}', source, re.S)
        self.assertIsNotNone(pattern, 'lite_ced::evidence_path is gone')
        self.assertIn('string map {/ __}', pattern[0],
                      'an evidence path built from a hierarchical cell would name a missing directory')


if __name__ == '__main__':
    unittest.main()
