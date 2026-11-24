"""Recovery from a detached native command, and the narrowed bank-missing write refusal."""
import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from mcp import types
from structured_config import StructuredSession


def reply(data, error=False):
    return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(data))], isError=error)


class Guard:
    def __init__(self, root):
        self.root, self.count = Path(root), 0

    def record(self, data):
        self.count += 1
        path = self.root / ('e%03d.json' % self.count)
        path.write_text(json.dumps(data, default=str))
        return path


class DetachingUpstream:
    """vivado_execute detaches once; vivado_status reports running twice; the fetch then sees
    whatever __ipcfg_reply_seq the 'Vivado side' holds."""

    def __init__(self, stored_seq, stored_reply='late answer'):
        self.stored_seq, self.stored_reply = stored_seq, stored_reply
        self.calls, self.running = [], 2

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == 'vivado_status':
            self.running -= 1
            return reply({'is_command_running': self.running > 0})
        command = arguments['command']
        if command.startswith('if {[info exists __ipcfg_reply_seq]'):
            wanted = int(command.split('== ')[1].split('}')[0])
            if wanted == self.stored_seq:
                encoded = base64.b64encode(self.stored_reply.encode()).decode()
                return reply({'exit_code': 0, 'output': '__IPCFG__' + encoded})
            return reply({'exit_code': 0, 'output': '__IPCFG_FAILED__boom while running'})
        return reply({'error_type': 'STILL_RUNNING_DETACHED', 'exit_code': 1,
                      'error': 'command still running in Vivado'}, error=True)


def session(upstream, root):
    s = StructuredSession.__new__(StructuredSession)
    s.guard, s.upstream, s.sid, s.native_serial = Guard(root), upstream, 'sid', 0
    return s


class DetachRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get('TMPDIR'))
        StructuredSession.DETACHED_POLL_SECONDS = 0

    def test_detached_command_returns_the_reply_it_stored(self):
        up = DetachingUpstream(stored_seq=1)
        out = asyncio.run(session(up, self.tmp).native('[slow_proc]'))
        self.assertEqual(out, 'late answer')
        self.assertEqual([n for n, _ in up.calls].count('vivado_status'), 2)
        self.assertIn('set __ipcfg_reply_seq 1', up.calls[0][1]['command'])

    def test_a_stale_reply_from_an_earlier_request_is_never_returned(self):
        up = DetachingUpstream(stored_seq=0)  # the command errored before setting seq 1
        with self.assertRaisesRegex(ValueError, 'detached command did not complete: boom'):
            asyncio.run(session(up, self.tmp).native('[slow_proc]'))


class BankMissingRefusalTests(unittest.TestCase):
    def test_received_clock_quantities_are_still_refused(self):
        for row in [dict(key='CONFIG.REF_CLK_FREQ', value_src='DEFAULT'),
                    dict(key='CONFIG.USER_CLK_FREQ0', value_src='DEFAULT'),
                    dict(key='CONFIG.C_WIDTH', value_src='PROPAGATED')]:
            self.assertTrue(StructuredSession.received_quantity(row), row)

    def test_a_local_setting_on_a_cell_whose_customizer_failed_is_writable(self):
        """proc_sys_reset on Vivado 2026.2 publishes no bank (its create_gui errors in batch
        mode), but its reset polarity is an ordinary local setting."""
        row = dict(key='CONFIG.C_AUX_RESET_HIGH', label='Aux Reset Active High', value_src='DEFAULT')
        self.assertFalse(StructuredSession.received_quantity(row))


class OutsideEnableControlTests(unittest.TestCase):
    """Case 16: 'locked output exposed' retrieved LOCKED_PORT (disabled); its switch USE_LOCKED
    was not retrieved, so it was never offered. The cell's own two-state parameters that share
    a word with the field are tried, and kept only when a live context check enables it."""

    def make(self, enabling):
        s = StructuredSession.__new__(StructuredSession)
        s.guard, s.cell, s.native_serial = Guard(tempfile.mkdtemp(dir=os.environ.get('TMPDIR'))), 'clk', 0
        pool = [dict(key='CONFIG.USE_LOCKED', label='Locked', type='bool', default='false'),
                dict(key='CONFIG.USE_DYN_RECONFIG', label='Dynamic Reconfig', type='bool', default='false'),
                dict(key='CONFIG.LOCKED_FB_PORT', label='Locked FB Port', type='string', default='locked_fb')]
        async def api_rows():
            return pool
        async def enrich_live(rows):
            for row in rows:
                row['current'] = row.get('default')
        self.tried = []
        async def native(expression, prefix=''):
            import re
            # Arguments arrive base64-encoded by atom(); the proposal is the first after 'dict create'.
            proposal = expression.split('dict create ', 1)[1]
            control = base64.b64decode(re.search(r'base64 \{([A-Za-z0-9+/=]*)\}', proposal)[1]).decode()
            self.tried.append(control)
            on = '1' if control == enabling else '0'
            return 'CONFIG.LOCKED_PORT {enabled ' + on + '}'
        s.api_rows, s.enrich_live, s.native = api_rows, enrich_live, native
        return s

    def test_the_proven_switch_is_offered_and_linked_to_the_disabled_field(self):
        s = self.make('CONFIG.USE_LOCKED')
        target = dict(key='CONFIG.LOCKED_PORT', label='Locked Port', enabled=False)
        extra = asyncio.run(s.outside_enable_controls([target], [target]))
        self.assertEqual([r['key'] for r in extra], ['CONFIG.USE_LOCKED'])
        self.assertEqual(extra[0]['relation_scope'], 'live_enablement_control')
        self.assertEqual(target['enablement_controls'], ['CONFIG.USE_LOCKED'])
        # Only parameters sharing a specific word were tried; 'port' alone never selects one.
        self.assertEqual(self.tried, ['CONFIG.USE_LOCKED'])

    def test_a_field_naming_a_missing_pin_is_linked_to_the_switch_that_creates_it(self):
        """clkx5_wiz on 2026.2: LOCKED_PORT (= 'locked') names a pin the cell lacks; switching
        USE_LOCKED on adds that pin on a throwaway cell, which proves the link. USE_RESET is
        already a candidate and is linked to RESET_PORT without being offered twice."""
        s = self.make('none')
        s.vlnv = 'xilinx.com:ip:clkx5_wiz:2.0'
        async def native(expression, prefix=''):
            if 'get_bd_pins' in expression:
                return 'clk_in1 clk_out1'
            if 'lite::port_effect' in expression:
                return 'PORT_EFFECT:OK effects {CONFIG.USE_LOCKED locked CONFIG.USE_RESET reset}'
            raise AssertionError(expression)
        s.native = native
        locked = dict(key='CONFIG.LOCKED_PORT', label='Locked Port', current='locked', type='string')
        reset = dict(key='CONFIG.RESET_PORT', label='Reset Port', current='reset', type='string')
        use_reset = dict(key='CONFIG.USE_RESET', label='Reset', type='bool', current='false')
        rows = asyncio.run(s.port_name_controls([locked, reset, use_reset]))
        self.assertEqual([r['key'] for r in rows], ['CONFIG.LOCKED_PORT', 'CONFIG.RESET_PORT', 'CONFIG.USE_RESET', 'CONFIG.USE_LOCKED'])
        self.assertEqual(rows[-1]['relation_scope'], 'live_port_control')
        self.assertEqual(locked['enablement_controls'], ['CONFIG.USE_LOCKED'])
        self.assertEqual(reset['enablement_controls'], ['CONFIG.USE_RESET'])
        self.assertEqual(locked['enablement_source_scope'], 'live_port_presence')

    def test_selecting_a_port_name_field_switches_on_its_pin_control(self):
        s = StructuredSession.__new__(StructuredSession)
        locked = dict(key='CONFIG.LOCKED_PORT', current='locked', enablement_controls=['CONFIG.USE_LOCKED'],
                      enablement_source_scope='live_port_presence', enablement_on_value='true', missing_pin='locked')
        s.candidates = {'r7.p1': locked, 'r7.p9': dict(key='CONFIG.USE_LOCKED', current='false')}
        s.selections = {('r7', 'r7.p1'): dict(value='locked', mode='assert')}
        config = {}
        repairs = s.port_presence_repairs(config)
        self.assertEqual(config, {'CONFIG.USE_LOCKED': 'true'})
        self.assertEqual(repairs[0]['enables'], 'CONFIG.LOCKED_PORT')
        self.assertEqual(s.port_presence_repairs({'CONFIG.USE_LOCKED': 'true'}), [])   # already on

    def test_a_shared_word_alone_never_adds_a_control(self):
        s = self.make('nothing enables it')
        target = dict(key='CONFIG.LOCKED_PORT', label='Locked Port', enabled=False)
        self.assertEqual(asyncio.run(s.outside_enable_controls([target], [target])), [])
        self.assertNotIn('enablement_controls', target)


class StandInPointerTests(unittest.TestCase):
    """Case 31: the supported stand-in was already offered from the task text but not linked to
    the unsupported IP, and the agent stopped to ask for a part change."""

    def test_an_offered_stand_in_is_marked_and_named_in_the_part_note(self):
        s = StructuredSession.__new__(StructuredSession)
        s.part = 'xc2ve3558-sfva1440-2MP-e-S'
        s.ip_candidates = {'i1': dict(vlnv='a:b:v_proc_ss:2.3'), 'i2': dict(vlnv='a:b:ps_wizard:1.0')}
        s.unavailable_ips = {'a:b:versal_cips:3.4': dict(vlnv='a:b:versal_cips:3.4', example_parts=['x'])}
        s.has_database = lambda: True

        class G:
            def database_request(self, command):
                return {'rows': [{'vlnv': 'a:b:versal_cips:3.4', 'status': 'WRONG_PART',
                                  'alternatives': [{'vlnv': 'a:b:ps_wizard:1.0', 'match': 'catalog_text'}]}]}
            def record(self, data):
                pass
        s.guard, s.build = G(), '1'
        asyncio.run(s.add_database_alternatives())
        self.assertEqual(s.ip_candidates['i2']['alternative_to'], 'a:b:versal_cips:3.4')
        self.assertEqual(len(s.ip_candidates), 2)            # marked, not duplicated
        note = s.add_unavailable_choices()['part_note']
        self.assertIn('i2 is the same-function IP this part supports in place of u1', note)
        self.assertIn('"candidate_id":"i2"', note)



class StickPrerequisiteTests(unittest.TestCase):
    """Issue #548: a value Vivado accepts but does not keep (axi_noc2 NUM_MC) gets the setting that
    makes it stick, proven on a throwaway cell, and the next apply writes that setting first."""

    def make(self, stuck_by):
        s = StructuredSession.__new__(StructuredSession)
        s.guard, s.vlnv, s.build, s.native_serial = Guard(tempfile.mkdtemp(dir=os.environ.get('TMPDIR'))), 'x:ip:noc:1.0', '1', 0
        s.candidates = {'r1.p1': dict(key='CONFIG.NUM_MC'), 'r2.p1': dict(key='CONFIG.DATA_WIDTH')}
        s.has_database = lambda: True
        s.guard.database_request = lambda command: {'influences': {'CONFIG.NUM_MC': [
            {'param': 'CONFIG.DEVICE_TYPE', 'values': ['Components', 'DIMMs']}]}}
        self.trials = []
        async def native(expression, prefix=''):
            self.trials.append(expression)
            return 'STICK:OK alone 0 trials {{CONFIG.DEVICE_TYPE Components %s}}' % stuck_by
        s.native = native
        return s

    def test_a_proven_prerequisite_is_reported_and_written_first(self):
        s = self.make('1')
        reply = 'CFG:FIX failures {{r1.r1.p1 actual 1 wanted 4} {apply reason {other}}} saved SAVE:OK'
        proven = asyncio.run(s.prove_stick_prerequisites(reply))
        self.assertEqual(proven, [dict(key='CONFIG.NUM_MC', wanted='4', requires={'CONFIG.DEVICE_TYPE': 'Components'},
                                       evidence_scope='live_throwaway_cell')])
        config = {'CONFIG.DATA_WIDTH': '32', 'CONFIG.NUM_MC': '4'}
        repairs = s.stick_repairs(config)
        self.assertEqual(list(config), ['CONFIG.DEVICE_TYPE', 'CONFIG.DATA_WIDTH', 'CONFIG.NUM_MC'])
        self.assertEqual(repairs[0]['enables'], 'CONFIG.NUM_MC')

    def test_nothing_is_added_without_a_live_proof(self):
        s = self.make('0')
        reply = 'CFG:FIX failures {{r1.r1.p1 actual 1 wanted 4}} saved SAVE:OK'
        self.assertEqual(asyncio.run(s.prove_stick_prerequisites(reply)), [])
        config = {'CONFIG.NUM_MC': '4'}
        self.assertEqual(s.stick_repairs(config), [])
        self.assertEqual(config, {'CONFIG.NUM_MC': '4'})


class SameCategoryNoteTests(unittest.TestCase):
    """#674: with no stand-in, supported same-category IPs become choosable c* candidates ranked by
    the task's wording, and the note says to pick one by function instead of stopping; a partial
    word match on the unsupported IP's name no longer outranks them."""

    def make(self):
        s = StructuredSession.__new__(StructuredSession)
        s.part, s.ip_hint = 'xcvc1902-vsva2197-2MP-e-S', 'axi_protocol_convert'
        s.task = 'AXI protocol converter between the NoC M00_AXI master and an AXI4-Lite slave'
        s.ip_candidates = {'i1': dict(vlnv='a:b:axi_protocol_checker:2.0', source='begin_ip_hint', matched_phrase='axi protocol'),
                           'i2': dict(vlnv='a:b:axis_clock_converter:1.1', source='task_text_catalog', matched_phrase='converter')}
        s.unavailable_ips = {'a:b:axi_protocol_converter:2.1': dict(vlnv='a:b:axi_protocol_converter:2.1', example_parts=['x'])}
        s.has_database = lambda: True

        class G:
            def database_request(self, command):
                return {'rows': [{'vlnv': 'a:b:axi_protocol_converter:2.1', 'status': 'WRONG_PART', 'alternatives': [],
                                  'same_category': [
                                      {'vlnv': 'a:b:axi_vip:1.1', 'name': 'axi_vip', 'display_name': 'AXI Verification IP',
                                       'description': 'Verification IP for simulation'},
                                      {'vlnv': 'a:b:smartconnect:1.0', 'name': 'smartconnect', 'display_name': 'AXI SmartConnect',
                                       'description': 'connects one or more AXI memory-mapped master devices to slave devices'}]}]}
            def record(self, data):
                pass
        s.guard, s.build = G(), '1'
        return s

    def test_same_category_ips_become_choosable_candidates(self):
        s = self.make()
        asyncio.run(s.add_database_alternatives())
        note = s.add_unavailable_choices()['part_note']
        self.assertEqual(s.ip_candidates['c1']['vlnv'], 'a:b:smartconnect:1.0')    # ranked by the task's words
        self.assertEqual(s.ip_candidates['c1']['alternative_to'], 'a:b:axi_protocol_converter:2.1')
        self.assertIn('Do not stop', note)
        self.assertIn('"candidate_id":"c1"', note)

    def test_a_partial_name_match_does_not_outrank_them(self):
        s = self.make()
        asyncio.run(s.add_database_alternatives())
        s.add_unavailable_choices()
        s.significant_terms = StructuredSession.significant_terms.__get__(s)
        self.assertIsNone(s.explicit_identity_candidate())

class GiveUpGuardTests(unittest.TestCase):
    """#674: with supported c* candidates offered, choosing the unsupported u* without a part, or
    finishing before choosing, points the agent at the c* candidates (finish only once)."""

    def make(self):
        from test_structured_config import Session
        s = Session.__new__(Session)
        StructuredSession.__init__(s, Guard(tempfile.mkdtemp(dir=os.environ.get('TMPDIR'))), None, 's', 'log')
        s.stage, s.initialized, s.part, s.task = 'choosing_ip', True, 'xcvc1902-vsva2197-2MP-e-S', 'AXI protocol converter'
        s.ip_candidates = {'u1': dict(vlnv='a:b:axi_protocol_converter:2.1', supported_on_current_part=False,
                                      example_parts=['xa7a100tcsg324-1I']),
                           'c1': dict(vlnv='a:b:smartconnect:1.0', alternative_to='a:b:axi_protocol_converter:2.1',
                                      supported_on_current_part=True)}
        return s

    def test_choosing_the_unsupported_ip_points_at_supported_candidates(self):
        s = self.make()
        with self.assertRaisesRegex(ValueError, 'choose the supported candidate among c1'):
            asyncio.run(s.request({'op': 'choose_ip', 'candidate_id': 'u1'}))

    def test_first_finish_while_choosing_is_refused_with_guidance(self):
        s = self.make()
        reply = asyncio.run(s.request({'op': 'finish'}))
        self.assertTrue(reply['premature_finish'])
        self.assertEqual(reply['next_op'], 'choose_ip')
        self.assertEqual(s.stage, 'choosing_ip')


if __name__ == '__main__':
    unittest.main()
