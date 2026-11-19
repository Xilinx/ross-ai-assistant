# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""CED workflow state machine with a simulated Vivado. Run with TMPDIR and Python caches in scratch."""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

SKILL = Path(__file__).resolve().parents[2]
# examples/ holds the local-model relay (examples/README.md), which one test covers.
sys.path[:0] = [str(SKILL / 'scripts'), str(SKILL / 'examples')]
import ced_session  # noqa: E402
from ced_session import CEDSession, parse_debug_info  # noqa: E402
from structured_config import atom, tdict, tlist  # noqa: E402

STORE = SKILL / 'tests/fixtures/store'
DEBUG = '''Name: view
* * * * * Param * * * * *
Name        : Preset
Format Type : 4
Range Type  : discrete
Range       : {Microcontroller Microcontroller Suitable for baremetal} {Application_Processor Application Embedded linux capable}
Value       : Microcontroller
Enabled     : 1
Name: Preset
display Name: Preset Configurations
Visible: 1
* * * * * Param * * * * *
Name        : Include_DDR
Format Type : 1
Range Type  : none
Range       :
Value       : false
Enabled     : 0
Name: Include_DDR
display Name: Include_DDR
Visible: 1
* * * * * Image * * * * *
Name: Image
'''


def tcl(expression):
    return subprocess.check_output(['tclsh'], input='puts ' + expression + '\n', text=True).rstrip('\n')


class Guard:
    def __init__(self, root):
        self.root, self.records = root, []

    def record(self, data):
        self.records.append(data)
        return self.root / 'record.json'


class FakeMirror:
    def __init__(self, cache, branch, bundled=None):
        self.path, self.sha, self.source, self.branch = STORE, 'f' * 40, 'live_git', branch

    def resolve(self):
        return self.info()

    def info(self):
        return dict(path=str(self.path), sha=self.sha, branch=self.branch, source=self.source)

    def add_design(self, rel):
        return self.path / rel


class Session(CEDSession):
    """Answers helper calls the way lib/ced.tcl formats them."""
    targets = [dict(kind='board', name='xilinx.com:zcu102:part0:3.4', board='zcu102', display='Zynq board', part='xczu9eg')]
    live_support = {'xilinx.com:design:alpha_preset:1.0': dict(present='1', supported='1', boards='zcu102 zcu104'),
               'xilinx.com:design:beta_pcie:2.0': dict(present='1', supported='0', boards='vck190 vpk120'),
               'xilinx.com:design:gamma_rtl:1.0': dict(present='0')}
    writes = []
    messages = {}
    snapshots = []
    drivers = {}
    clock_loads = {}
    frequency_sources = {}

    async def native(self, expression, prefix=''):
        self.calls.append(expression)
        if expression.startswith('[dict create version'):
            return tcl(tdict(dict(version='Vivado v2026.1 (64-bit)\nSW Build 6511674 on Tue', bundled='/none')))
        if expression.startswith('[cedcfg::targets'):
            return tcl('[list ' + ' '.join(tdict(t) for t in self.targets) + ']')
        if expression.startswith('[cedcfg::project'):
            return 'PROJECT:OK'
        if expression.startswith('[cedcfg::load_repo'):
            return 'REPO:OK designs 3'
        if expression.startswith('[cedcfg::support'):
            vlnvs = [v for v in self.live_support if atom(v) in expression]
            return tcl('[list ' + ' '.join(tdict(dict(vlnv=v, repo=str(STORE), **self.live_support[v])) for v in vlnvs) + ']')
        if expression.startswith('[cedcfg::options'):
            path = self.root / 'evidence' / 'options.txt'
            path.write_text(DEBUG)
            return tcl(tdict(dict(file=str(path), add_options='', board_part='xilinx.com:zcu102:part0:3.4', part='xczu9eg')))
        if expression.startswith('[cedcfg::evaluate'):
            state = ('PROJECT_PARAM.BOARD_PART x Include_DDR.ENABLEMENT true Preset.VALUE '
                     + ('Application_Processor' if atom('Application_Processor') in expression else 'Microcontroller'))
            return tcl(tdict(dict(state=state, writes=tcl('[list ' + ' '.join(tlist(w) for w in self.writes) + ']'),
                                  errors='', messages=tcl(tdict(self.messages)), passes='2', converged='1')))
        if expression.startswith('[cedcfg::instantiate'):
            self.instantiated = expression
            return tcl(tdict(dict(status='OK', result='design_1', bd='design_1', validation='OK',
                                  messages=tcl(tdict(dict(counts='errors 0 critical_warnings 0', first='', available='1'))))))
        if expression.startswith('[cedcfg::drivers'):
            rows = [r for path, r in self.drivers.items() if atom(path) in expression]
            return tcl('[list ' + ' '.join(tdict(r) for r in rows) + ']')
        if expression.startswith('[cedcfg::clock_loads'):
            rows = [r for path, r in self.clock_loads.items() if atom(path) in expression]
            return tcl('[list ' + ' '.join(tdict(r) for r in rows) + ']')
        if expression.startswith('[cedcfg::frequency_source'):
            source = next((s for path, s in self.frequency_sources.items() if atom(path) in expression), None)
            if source is None:
                return tcl('[list]')
            return tcl(tdict(dict(pin_mhz=source['pin_mhz'],
                                  matches=tcl('[list ' + ' '.join(tdict(m) for m in source['matches']) + ']'))))
        if expression.startswith('[cedcfg::cells'):
            return tcl('[list ' + tdict(dict(path='axi_uartlite_0', vlnv='xilinx.com:ip:axi_uartlite:2.0', display='AXI Uartlite', ports='rs232_uart')) + ']')
        if expression.startswith('[cedcfg::validate'):
            return tcl(tdict(dict(status='OK', result='', messages=tcl(tdict(dict(counts='errors 0 critical_warnings 0', first='', available='1'))))))
        if expression.startswith('[cedcfg::snapshot'):
            path = Path(expression.split('base64 {')[1].split('}')[0])
            path = Path(base64.b64decode(str(path)).decode())
            path.write_text(self.snapshots.pop(0) if self.snapshots else '')
            return 'SNAPSHOT:OK'
        if expression.startswith('[cedcfg::restore'):
            return 'RESTORE:OK tag x bd design_1 locked_ips {}'
        if expression.startswith(('[cedcfg::checkpoint', '[cedcfg::save')):
            return 'SAVE:OK'
        raise AssertionError('unexpected helper call ' + expression[:80])

    async def close(self):
        self.closed = True
        return True


def row(cell, key, value, source='USER'):
    return '\t'.join([cell, key, source, base64.b64encode(value.encode()).decode()])


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'run'
        log = self.root / 'work/.vivado-ai/native.log'
        log.parent.mkdir(parents=True)
        (self.root / 'evidence').mkdir()
        self.saved_mirror = ced_session.Mirror
        ced_session.Mirror = FakeMirror
        self.log = log
        self.session = self.new_session()

    def new_session(self, fresh=False):
        """Another session over the same workspace, as a restarted Vivado gives.

        fresh=True gives it an empty one instead, for a run that is unrelated to
        whatever already finished here.
        """
        log = self.log
        if fresh:
            log = Path(self.tmp.name) / ('run-' + str(id(self))) / 'work/.vivado-ai/native.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            (log.parents[2] / 'evidence').mkdir(exist_ok=True)
        session = Session(Guard(self.root), None, 'sid', str(log))
        session.calls = []
        return session

    async def asyncTearDown(self):
        ced_session.Mirror = self.saved_mirror
        self.tmp.cleanup()

    async def begin(self, requirements=None, **extra):
        requirements = requirements or ['processor preset that can run embedded linux']
        return await self.session.request(dict(op='begin', task='Alpha ' + ' and '.join(requirements) + ' on the ZCU102.',
                                               target='zcu102', requirements=requirements, **extra))

    async def test_begin_ranks_supported_designs_and_marks_unsupported_or_unavailable(self):
        reply = await self.begin()
        cards = {c['name']: c for c in reply['ced_candidates']}
        self.assertEqual(reply['ced_candidates'][0]['name'], 'alpha_preset')
        self.assertTrue(cards['alpha_preset']['supported'])
        self.assertEqual(reply['store']['commit'], 'f' * 12)
        self.assertEqual(reply['next_op'], 'choose_ced')

    async def test_approval_sentences_never_become_requirements(self):
        await self.session.request(dict(op='begin', target='zcu102', requirements=['processor preset', 'UART at 9600 baud'],
                                        task='Alpha processor preset on the ZCU102 with the UART at 9600 baud. '
                                             'Apply the IP-level changes without asking me.'))
        self.assertEqual(list(self.session.requirements.values()), ['processor preset', 'UART at 9600 baud'])

    async def test_approval_flag_only_changes_the_next_step_after_a_proposal(self):
        with self.assertRaises(ValueError):
            await self.session.request(dict(op='begin', task='x on zcu102', target='zcu102', requirements=['x'], phase2_approval='yes'))
        await self.session.request(dict(op='begin', task='Alpha processor preset on the ZCU102.', target='zcu102',
                                        requirements=['processor preset'], phase2_approval='pre_approved'))
        self.session.stage = 'awaiting_approval'
        self.assertEqual(self.session.state()['next_op'], 'ced_apply_phase2')
        self.session.pre_approved = False
        self.assertEqual(self.session.state()['next_op'], 'ask_user')

    async def test_ambiguous_target_requires_a_choice(self):
        self.session.targets = Session.targets * 2
        reply = await self.begin()
        self.assertEqual((reply['stage'], len(reply['target_candidates'])), ('choosing_target', 2))
        reply = await self.session.request(dict(op='choose_target', target_id='t1'))
        self.assertEqual(reply['stage'], 'choosing_ced')

    async def test_an_empty_reply_from_vivado_is_an_answer_and_not_a_dead_command(self):
        """Every other test replaces native(), so the marker parsing it wraps went untested.

        cedcfg::targets returns an empty Tcl list for a part this install lacks,
        which encodes to a bare __IPCFG__ with nothing after it. Reading that as
        a dead command raised NATIVE_ERROR, begin retried to its limit, and the
        target_not_found path that names the design was never reached: H1 and H2
        reported ced null for cpm6_dma and cpm6_bmd on every run.
        """
        reply = dict(exit_code=0, output='__IPCFG__')

        class Upstream:
            async def call_tool(self, name, args):
                return unittest.mock.Mock(isError=False, model_dump=lambda mode=None: {})
        # guard_mcp imports the mcp package, which the unit suite does without.
        self.session.upstream = Upstream()
        with unittest.mock.patch.dict(sys.modules, {'guard_mcp': unittest.mock.Mock(payload=lambda r: reply)}):
            self.assertEqual(await CEDSession.native(self.session, '[cedcfg::targets x]'), '')
            reply['output'] = 'ERROR: Vivado died'
            with self.assertRaises(ValueError):
                await CEDSession.native(self.session, '[cedcfg::targets x]')

    async def test_a_part_this_vivado_lacks_still_names_the_requested_design(self):
        """get_parts returns nothing for an uninstalled part; the store still identifies it."""
        self.session.targets = []
        reply = await self.session.request(dict(op='begin', task='Alpha processor preset on part xcnope-1.',
                                                target='xcnope-1', requirements=['Alpha processor preset']))
        self.assertEqual(reply['target_not_found'], 'xcnope-1')
        self.assertEqual(reply['next_op'], 'finish')
        self.assertEqual(reply['report']['ced'], 'alpha_preset')
        self.assertIs(reply['report']['full'], False)
        self.assertFalse(any(c.startswith('[cedcfg::project') for c in self.session.calls),
                         'nothing may be created for a target that does not exist')
        reply = await self.session.request(dict(op='finish'))
        self.assertEqual(reply['report']['ced'], 'alpha_preset')
        self.assertIsNone(reply['ced'])

    async def test_a_part_based_twin_is_refused_on_a_board_and_says_which_variant_it_is(self):
        """Versal Extensible Embedded Platform ships twice: the board variant, and a
        "(Part based)" one whose getSupportedBoards returns "" and whose addOptions
        takes PROJECT_PARAM.PART. The board's own part is in the part variant's list,
        so cedcfg::support credited the part match and the card read supported=true;
        the model picked it (ced-gen_M3, 26B) and instantiate died with "does not
        support any boards. Please specify a part.". ced.tcl now counts a part match
        only in a part-targeted project and reports part_only; the reply names the
        variant, because the two designs share every option and nearly their display.
        """
        self.session.live_support = dict(
            Session.live_support,
            **{'xilinx.com:design:beta_pcie:2.0': dict(present='1', supported='0', part_only='1', boards='')})
        reply = await self.begin()
        card = {c['name']: c for c in reply['ced_candidates']}['beta_pcie']
        self.assertIs(card['supported'], False)
        self.assertIn('Part-based variant', card['reason'])
        reply = await self.session.request(dict(op='choose_ced', name='Beta PCIe Endpoint'))
        self.assertIn('part-based variant', reply['reason'])
        self.assertFalse(any(c.startswith('[cedcfg::options') for c in self.session.calls),
                         'nothing may be opened for a design that cannot be built here')

    async def test_a_part_based_twin_names_the_board_twin_and_the_call_that_takes_it(self):
        """ced-gen_M3's 26B run was told a board twin existed and never looked for it.

        The refusal said the twin "usually carries the same display name
        without (Part based)" and left the model to resolve that itself. It
        finished instead, reporting ced ext_platform_part and full false, with
        the board twin sitting in the candidate list it had already been shown.
        """
        self.session.live_support = dict(
            Session.live_support,
            **{'xilinx.com:design:beta_pcie:2.0': dict(present='1', supported='0', part_only='1', boards='')})
        await self.begin()
        twin = dict(self.session.index.by_name('alpha_preset'))
        twin.update(vlnv='xilinx.com:design:alpha_preset_part:1.0', name='alpha_preset_part',
                    display='Alpha Processor Presets (Part based)')
        self.session.index.entries.append(twin)
        self.session.support[twin['vlnv']] = dict(present='1', supported='0', part_only='1', boards='')
        reply = await self.session.request(dict(op='choose_ced', name='Alpha Processor Presets (Part based)'))
        self.assertIn('part-based variant', reply['reason'])
        self.assertIn('board twin is Alpha Processor Presets', reply['reason'])
        self.assertIn('{"op":"choose_ced","name":"Alpha Processor Presets"}', reply['reason'])
        # With no buildable twin it still says which variant this is, and names none.
        reply = await self.session.request(dict(op='choose_ced', name='Beta PCIe Endpoint'))
        self.assertIn('part-based variant', reply['reason'])
        self.assertNotIn('board twin is', reply['reason'])

    async def test_unsupported_and_unavailable_designs_are_refused_without_creating_anything(self):
        await self.begin()
        reply = await self.session.request(dict(op='choose_ced', name='Beta PCIe Endpoint'))
        self.assertIn('does not support target', reply['reason'])
        self.assertEqual(reply['example_boards'], ['vck190', 'vpk120'])
        reply = await self.session.request(dict(op='choose_ced', name='Gamma Waveform Generator'))
        self.assertIn('not loadable', reply['reason'])
        self.assertFalse(any(c.startswith('[cedcfg::options') for c in self.session.calls))

    async def test_substituting_a_buildable_lookalike_for_a_named_unbuildable_design_is_refused(self):
        await self.session.request(dict(op='begin', task='Beta PCIe endpoint example on the ZCU102.', target='zcu102',
                                        requirements=['Beta PCIe endpoint example']))
        reply = await self.session.request(dict(op='choose_ced', name='alpha_preset'))
        self.assertEqual(reply['requested_design'], 'Beta PCIe Endpoint')
        self.assertIn('silently change the request', reply['reason'])
        self.assertFalse(any(c.startswith('[cedcfg::options') for c in self.session.calls))
        expected = dict(ced='beta_pcie', phase1_options={}, phase2_status='not_needed', full=False)
        self.assertEqual(reply['report'], expected)
        final = await self.session.request(dict(op='finish'))
        self.assertEqual(final['report'], expected)

    async def choose_alpha(self):
        await self.begin()
        return await self.session.request(dict(op='choose_ced', name='alpha_preset'))

    async def test_options_come_from_vivados_evaluated_view(self):
        reply = await self.choose_alpha()
        options = {o['name']: o for o in reply['options']}
        self.assertEqual(options['Preset']['choices'][1], 'Application_Processor = Application Embedded linux capable')
        self.assertFalse(options['Include_DDR']['enabled'])
        self.assertEqual(reply['option_hints'], {'r1': 'o1=Application_Processor'})

    async def test_values_outside_the_choices_and_updater_conflicts_are_rejected(self):
        await self.choose_alpha()
        reply = await self.session.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Bogus')]))
        self.assertEqual(reply['rejected'][0]['reason'], 'not one of the choices')
        self.session.writes = [['Preset.VALUE', 'Application_Processor', 'Microcontroller', 'update_0']]
        reply = await self.session.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Application_Processor')]))
        self.assertEqual(reply['conflicts'][0]['design_sets'], 'Microcontroller')
        self.assertEqual(reply['accepted'], [])
        self.assertEqual(self.session.option_selections, {})

    async def test_a_value_the_design_forced_over_the_request_does_not_count_as_satisfying_it(self):
        """ced-gen_H4 asked for multi-stream from DDR on the VEK385 and reported full: true.

        set_options refused Stream_Source=AVTPG_and_DDR_(MST) with design_sets
        AVTPG, the model then selected AVTPG and the skill counted the
        requirement as a satisfied phase1_option. It cannot know that AVTPG does
        not mean "from DDR", but it does know the design forced exactly that
        value over the requested one.
        """
        await self.choose_alpha()
        self.session.writes = [['Preset.VALUE', 'Application_Processor', 'Microcontroller', 'update_0']]
        await self.session.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Application_Processor')]))
        self.session.writes = []
        reply = await self.session.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Microcontroller')]))
        self.assertEqual(reply['accepted'][0]['value'], 'Microcontroller')
        await self.session.request(dict(op='instantiate'))
        final = await self.session.request(dict(op='finish'))
        self.assertEqual(final['requirements'][0]['status'], 'overridden_by_design')
        self.assertFalse(final['full'], 'the design answered this requirement, the requirement did not')
        # A value the design never forced is satisfied as usual, so a retry after
        # "not one of the choices" still counts. Its own workspace: a second run
        # of the same task over a finished one is answered from its report.
        s = self.new_session(fresh=True)
        s.writes = []
        await s.request(dict(op='begin', task='Alpha processor preset that can run embedded linux on the ZCU102.',
                             target='zcu102', requirements=['processor preset that can run embedded linux']))
        await s.request(dict(op='choose_ced', name='alpha_preset'))
        await s.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Bogus')]))
        await s.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Microcontroller')]))
        await s.request(dict(op='instantiate'))
        final = await s.request(dict(op='finish'))
        self.assertEqual(final['requirements'][0]['status'], 'phase1_option')
        self.assertTrue(final['full'])

    async def test_a_board_initializer_seeds_a_value_and_does_not_veto_the_request(self):
        """MPSoC_Ext_Platform's gui_updater reads only PROJECT_PARAM.BOARD_PART
        and writes Include_DDR.VALUE false on every board while enabling it on
        DDR4 ones. It initializes the widget the user then ticks; treating that
        write as a rejection made "with DDR" unrequestable (ced-gen_H3, 0/2).
        An updater that reads an option VALUE is a rule and still wins.
        """
        await self.begin(['processor preset', 'DDR'])
        await self.session.request(dict(op='choose_ced', name='alpha_preset'))
        self.session.writes = [['Include_DDR.VALUE', 'true', 'false', 'update_gui_for_0']]
        reply = await self.session.request(dict(op='set_options', selections=[dict(requirement_id='r2', option_id='o2', value='true')]))
        self.assertNotIn('conflicts', reply)
        self.assertEqual(reply['accepted'], [dict(requirement_id='r2', option='Include_DDR', value='true')])
        self.assertEqual(reply['effective_options']['Include_DDR'], 'true')
        await self.session.request(dict(op='waive', requirement_id='r1', reason='default preset'))
        await self.session.request(dict(op='instantiate'))
        self.assertIn(atom('Include_DDR.VALUE'), self.session.instantiated)
        self.assertEqual(self.session.phase1['options_passed'], {'Include_DDR': 'true'})
        # The same write from an updater that reads the configuration is a rule.
        self.session.writes = [['Include_DDR.VALUE', 'true', 'false', 'update_1']]
        s = Session(Guard(self.root), None, 'sid', str(self.root / 'work/.vivado-ai/native.log'))
        s.calls, s.writes = [], self.session.writes
        await s.request(dict(op='begin', task='Alpha processor preset with DDR on the ZCU102.', target='zcu102',
                             requirements=['processor preset', 'DDR']))
        await s.request(dict(op='choose_ced', name='alpha_preset'))
        reply = await s.request(dict(op='set_options', selections=[dict(requirement_id='r2', option_id='o2', value='true')]))
        self.assertEqual(reply['conflicts'][0]['updater'], 'update_1')

    async def test_labels_are_accepted_and_instantiate_passes_selected_values(self):
        await self.choose_alpha()
        reply = await self.session.request(dict(op='set_options', selections=[
            dict(requirement_id='r1', option_id='o1', value='application processor')]))
        self.assertEqual(reply['accepted'][0]['value'], 'Application_Processor')
        reply = await self.session.request(dict(op='instantiate'))
        self.assertEqual(reply['stage'], 'phase1_done')
        self.assertIn(atom('Preset.VALUE'), self.session.instantiated)
        self.assertIn(atom('Application_Processor'), self.session.instantiated)
        final = await self.session.request(dict(op='finish'))
        self.assertTrue(final['full'] and final['session_closed'])
        self.assertEqual(final['requirements'][0]['status'], 'phase1_option')

    async def test_an_option_the_board_forces_is_reported_though_it_was_never_passed(self):
        """Include_DDR reads Enabled 0: the board decides it and the model is never
        offered it, so nothing passes it to instantiate. It still describes how the
        design was configured, and a final answer cannot report a value it was not told.
        Quad is the opposite case: it exists only in init.tcl, so its "value" is the
        unevaluated Tcl literal `[lindex $quads 0]` and the design never confirmed it.
        """
        await self.choose_alpha()
        await self.session.request(dict(op='set_options', selections=[
            dict(requirement_id='r1', option_id='o1', value='Application_Processor')]))
        await self.session.request(dict(op='instantiate'))
        self.assertEqual(self.session.phase1['options_passed'], {'Preset': 'Application_Processor'})
        final = await self.session.request(dict(op='finish'))
        self.assertEqual(final['report']['phase1_options'],
                         {'Preset': 'Application_Processor', 'Include_DDR': 'false'})

    async def test_unmapped_requirements_pause_instantiate_once_then_stay_unresolved(self):
        await self.begin(['processor preset', 'UART at 9600 baud'])
        await self.session.request(dict(op='choose_ced', name='alpha_preset'))
        await self.session.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Microcontroller')]))
        reply = await self.session.request(dict(op='instantiate'))
        self.assertTrue(reply['instantiate_paused'])
        self.assertFalse(hasattr(self.session, 'instantiated'))
        await self.session.request(dict(op='instantiate'))
        final = await self.session.request(dict(op='finish'))
        self.assertFalse(final['full'])
        self.assertEqual(final['requirements'][1]['status'], 'unresolved')

    async def test_deferred_requirements_open_phase2_with_a_cell_table(self):
        await self.begin(['processor preset', 'UART at 9600 baud'])
        await self.session.request(dict(op='choose_ced', name='alpha_preset'))
        await self.session.request(dict(op='waive', requirement_id='r1', reason='default preset'))
        await self.session.request(dict(op='defer', requirement_ids=['r2']))
        reply = await self.session.request(dict(op='instantiate'))
        self.assertEqual((reply['stage'], reply['next_op']), ('phase2', 'assign'))
        self.assertEqual(reply['cells'], [dict(id='k1', path='axi_uartlite_0', ip='AXI Uartlite', ports=['rs232_uart'])])
        reply = await self.session.request(dict(op='cells', query='uart'))
        self.assertEqual((reply['matched'], reply['more']), (1, False))
        self.assertEqual((await self.session.request(dict(op='cells', query='ethernet')))['matched'], 0)

    async def test_a_cell_whose_customizer_will_not_run_says_so_once_for_the_cell(self):
        """Per candidate, a dead customizer shows only as PB_MISS and "unavailable".

        clk_wiz_0's customizer will not start in batch mode, so every row it
        offers carries range PB_MISS:*, range_scope/help_source unavailable and
        no enabled field, while still reading write_mode write and a confident
        current. ced-gen_H3 took CONFIG.REF_CLK_FREQ for a settable float and
        wrote 125 to the wizard's own input rather than to the PS driving it.

        Keying the notice on lite's backend field missed it: clk_wiz_0 answers
        live row by row and only its parameter bank is absent, so the backend
        still reads live and probe6 lost H3 again on both models. The rows
        themselves are the evidence.
        """
        live_but_unchecked = CEDSession.compact_sub('r1', 'clk_wiz_0', dict(backend='live', candidates=[
            dict(id='r1.p1', key='CONFIG.REF_CLK_FREQ', range='PB_MISS:REF_CLK_FREQ', range_scope='unavailable',
                 write_mode='write', value_src='DEFAULT', current='100.0')]))
        self.assertIn('degraded', live_but_unchecked['metadata'])
        self.assertIn('clk_wiz_0', live_but_unchecked['metadata'])
        self.assertIn('set on the cell that produces it', live_but_unchecked['metadata'])
        packaged = CEDSession.compact_sub('r1', 'clk_wiz_0', dict(
            backend='packaged_component_xml', fallback='METADATA_UNAVAILABLE:Cannot initialize customizer',
            candidates=[dict(id='r1.p1', key='CONFIG.REF_CLK_FREQ', range='PB_MISS:REF_CLK_FREQ')]))
        self.assertIn('degraded', packaged['metadata'])
        plain = CEDSession.compact_sub('r1', 'axi_uartlite_0', dict(backend='live', candidates=[
            dict(id='r1.p1', key='CONFIG.C_BAUDRATE', range='RANGE_LIST:9600 115200', range_scope='native_current_instance')]))
        self.assertNotIn('metadata', plain, 'a cell whose customizer answers says nothing extra')
        mixed = CEDSession.compact_sub('r1', 'axi_uartlite_0', dict(backend='live', candidates=[
            dict(id='r1.p1', key='CONFIG.C_BAUDRATE', range='RANGE_LIST:9600 115200', range_scope='native_current_instance'),
            dict(id='r1.p2', key='CONFIG.C_ODD', range='PB_MISS:C_ODD', range_scope='unavailable')]))
        self.assertNotIn('metadata', mixed, 'one missing row is not a dead customizer')

    async def test_a_proposal_that_changes_nothing_says_which_kind_of_nothing(self):
        """already_satisfied asserts the design already matches what was selected.

        It was also reported when every deferred requirement had been waived in
        Phase 2 and nothing was ever selected, which asserts that about selections
        that do not exist. ced-gen_E2 is the real case: its GPIO is bound to the
        ZC702 LED interface and already reads 4 bits and all outputs.
        """
        stub = lambda selections: unittest.mock.Mock(selections=selections)
        self.session.subs = {'axi_gpio_0': stub([('r2', 'r2.p1')])}
        self.assertEqual(self.session.empty_proposal_status([]), 'already_satisfied')
        self.assertEqual(self.session.empty_proposal_status([dict(cell='axi_gpio_0')]), 'failed')
        self.session.subs = {'axi_uartlite_0': stub([])}
        self.assertEqual(self.session.empty_proposal_status([]), 'not_needed')

    async def test_a_query_that_matches_no_cell_lists_them_all_instead_of_nothing(self):
        """Cells carry the name of the IP that implements a function, never the
        function: nothing in a Zynq-7000 design is called "clock", the PL clock
        comes out of processing_system7_0. An empty reply read as "this design
        has no clock cell", so ced-gen_M7's 26B run went back to the BRAM and
        set Port_A_Clock, leaving PCW_FPGA0_PERIPHERAL_FREQMHZ at 50.
        """
        await self.begin(['processor preset', 'UART at 9600 baud'])
        await self.session.request(dict(op='choose_ced', name='alpha_preset'))
        await self.session.request(dict(op='waive', requirement_id='r1', reason='default preset'))
        await self.session.request(dict(op='defer', requirement_ids=['r2']))
        await self.session.request(dict(op='instantiate'))
        reply = await self.session.request(dict(op='cells', query='clock'))
        self.assertEqual(reply['matched'], 0, 'the honest count is still zero')
        self.assertEqual([c['path'] for c in reply['cells']], ['axi_uartlite_0'])
        self.assertEqual(reply['cell_count'], 1)
        self.assertIn('named for the IP that implements the function', reply['note'])

    async def test_a_cell_that_fails_to_open_leaves_its_requirement_assignable(self):
        """A half-done assignment would strand the requirement: describe asks for
        an assignment and assign reports it already has one."""
        await self.begin(['processor preset', 'UART at 9600 baud'])
        await self.session.request(dict(op='choose_ced', name='alpha_preset'))
        await self.session.request(dict(op='waive', requirement_id='r1', reason='default preset'))
        await self.session.request(dict(op='defer', requirement_ids=['r2']))
        await self.session.request(dict(op='instantiate'))

        async def refuse(*args, **kwargs):
            raise ValueError('Nested schema must be bounded current-run evidence')

        with unittest.mock.patch.object(ced_session.CellSession, 'adopted', refuse):
            with self.assertRaises(ValueError) as caught:
                await self.session.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k1')]))
        self.assertIn('stays unassigned', str(caught.exception))
        self.assertNotIn('r2', self.session.assignments)
        self.assertEqual(self.session.subs, {})

    async def test_a_resubmitted_batch_still_places_the_requirement_that_is_new_to_it(self):
        s = self.session
        await self.begin(['processor preset', 'UART at 9600 baud', 'second UART at 9600 baud'])
        await s.request(dict(op='choose_ced', name='alpha_preset'))
        await s.request(dict(op='waive', requirement_id='r1', reason='default preset'))
        await s.request(dict(op='defer', requirement_ids=['r2', 'r3']))
        await s.request(dict(op='instantiate'))

        class Sub:
            requirements = {}
            selections = {}
            waived = {}
            state = staticmethod(lambda: {})
            pending = staticmethod(lambda: [])
            bank_missing = staticmethod(lambda: False)
            bank_blocks_writes = staticmethod(lambda: False)

        s.subs['axi_uartlite_0'] = Sub()
        s.assignments['r2'] = dict(ids=['r2'], cells=['axi_uartlite_0'])
        reply = await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k1'),
                                                               dict(requirement_id='r3', cell_id='k1')]))
        self.assertEqual([a['requirement_id'] for a in reply['assigned']], ['r3'])
        self.assertEqual(sorted(s.assignments), ['r2', 'r3'])
        with self.assertRaises(ValueError):
            await s.request(dict(op='assign', assignments=[dict(requirement_id='r3', cell_id='k1')]))

    async def phase2_with_two_cells(self):
        """A design whose cell names invite the wrong guess: rst_ps7_0_50M reads
        like a clock, and a real run assigned "PL clock at 100 MHz" to it."""
        s = self.session
        await self.begin(['processor preset', 'UART at 9600 baud'])
        await s.request(dict(op='choose_ced', name='alpha_preset'))
        await s.request(dict(op='waive', requirement_id='r1', reason='default preset'))
        await s.request(dict(op='defer', requirement_ids=['r2']))
        await s.request(dict(op='instantiate'))
        s.cells['k2'] = dict(path='rst_ps7_0_50M', vlnv='xilinx.com:ip:proc_sys_reset:5.0',
                             display='Processor System Reset', ports=['slowest_sync_clk'])
        return s

    def sub(self, cell, requirements, selections=None, no_bank=False, refuse=None, blocks=None):
        """blocks defaults to no_bank: a cell with no bank whose rows are all values it
        receives. Pass blocks=False for a bank-less cell that still holds a writable row,
        which is what clk_wiz is on 2026.2."""
        session = self.session
        blocked = no_bank if blocks is None else blocks

        class Sub:
            def __init__(self):
                self.cell, self.requirements = cell, dict(requirements)
                self.selections, self.waived, self.exhausted, self.describe_counts = dict(selections or {}), {}, {}, {}

            describe_limit = staticmethod(lambda rid: 4)
            bank_missing = staticmethod(lambda: no_bank)
            bank_blocks_writes = staticmethod(lambda: blocked)

            def pending(self):
                return [dict(id=k, text=v) for k, v in self.requirements.items()]

            def state(self):
                return dict(next_op='describe')

            async def request(self, request):
                if refuse and request.get('op') == 'select':
                    raise ValueError(refuse)
                return dict(port_dependencies=dict(status='unavailable'))

        session.subs[cell] = Sub()
        return session.subs[cell]

    async def test_a_requirement_moves_to_another_cell_until_it_has_selected_something(self):
        s = await self.phase2_with_two_cells()
        wrong = self.sub('rst_ps7_0_50M', {'r2': 'UART at 9600 baud'})
        self.sub('axi_uartlite_0', {})
        s.assignments['r2'] = dict(ids=['r2'], cells=['rst_ps7_0_50M'])
        reply = await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k1')]))
        self.assertEqual(reply['reassigned'], [dict(requirement_id='r2', was=['rst_ps7_0_50M'], now=['axi_uartlite_0'])])
        self.assertEqual(s.assignments['r2']['cells'], ['axi_uartlite_0'])
        self.assertEqual(s.subs['axi_uartlite_0'].requirements, {'r2': 'UART at 9600 baud'})
        self.assertNotIn('rst_ps7_0_50M', s.subs, 'a sub with no requirements left is dropped')
        self.assertEqual(wrong.requirements, {})
        # Once it has picked a parameter there, the assignment stands.
        s.subs['axi_uartlite_0'].selections[('r2', 'p1')] = dict(value='9600')
        with self.assertRaises(ValueError) as caught:
            await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k2')]))
        self.assertIn('has selections there', str(caught.exception))
        self.assertEqual(s.assignments['r2']['cells'], ['axi_uartlite_0'])

    async def test_a_cell_with_nothing_to_offer_points_at_reassignment_not_another_query(self):
        s = await self.phase2_with_two_cells()
        self.sub('rst_ps7_0_50M', {'r2': 'UART at 9600 baud'})
        s.assignments['r2'] = dict(ids=['r2'], cells=['rst_ps7_0_50M'])
        reply = await s.request(dict(op='describe', requirement_id='r2', query='9600 baud'))
        self.assertNotIn('candidates', reply['result'])
        self.assertIn('No parameter of rst_ps7_0_50M matches', reply['result']['note'])
        self.assertIn('assign r2 to that cell', reply['result']['note'])

    async def test_a_refused_write_to_a_bankless_cell_names_the_call_that_moves_the_requirement(self):
        """The 26B run found the right cell from the refusal and still never moved.

        ced-gen_H3 wrote 125 MHz into clk_wiz_0's own REF_CLK_FREQ, was refused
        because that cell publishes no parameter bank, ran {"op":"cells",
        "query":"ps_e"} and got the PS back -- then described clk_wiz_0 again
        and proposed nothing, leaving the requirement unresolved. The refusal
        ended "configure that one instead" and named no op, and next_op read
        propose because the describe budget was spent. Both now say assign.
        """
        s = await self.phase2_with_two_cells()
        self.sub('rst_ps7_0_50M', {'r2': 'PL clock at 100 MHz'}, no_bank=True,
                 refuse='CONFIG.REF_CLK_FREQ has no published range ... this cell has no parameter bank in this session.')
        s.assignments['r2'] = dict(ids=['r2'], cells=['rst_ps7_0_50M'])
        with self.assertRaises(ValueError) as caught:
            await s.request(dict(op='select', selections=[dict(requirement_id='r2', candidate_id='p1', value='100')]))
        message = str(caught.exception)
        self.assertIn('"op":"assign"', message)
        self.assertIn('"requirement_id":"r2"', message)
        self.assertIn('nothing is selected on rst_ps7_0_50M', message)
        # And the stage itself points at assign rather than at propose.
        self.assertEqual(s.state()['next_op'], 'assign')
        # When the block design shows which cell drives this one's clock input,
        # the refusal names it and the exact call (ced-gen_H3, Haiku: it searched,
        # found the DDR4 controller instead of ps_e, and wrote there).
        s.drivers = {'rst_ps7_0_50M': dict(pin='slowest_sync_clk', cell='axi_uartlite_0', source_pin='clk_out')}
        with self.assertRaises(ValueError) as caught:
            await s.request(dict(op='select', selections=[dict(requirement_id='r2', candidate_id='p1', value='100')]))
        self.assertIn('axi_uartlite_0 (k1) drives rst_ps7_0_50M/slowest_sync_clk', str(caught.exception))
        self.assertIn('{"requirement_id":"r2","cell_id":"k1"}', str(caught.exception))
        s.drivers = {}
        # Proposing anyway is refused while the requirement still sits there,
        # even once its describe budget is spent (ced-gen_H3, Haiku).
        s.subs['rst_ps7_0_50M'].exhausted['r2'] = 1
        reply = await s.request(dict(op='propose'))
        self.assertIn('Nothing was proposed', reply['note'])
        self.assertIn('"op":"assign"', reply['note'])
        self.assertEqual(reply['next_op'], 'assign')
        self.assertFalse(any(c.startswith('[cedcfg::snapshot') for c in s.calls))
        # A select error about the value leaves the cell alone.
        self.sub('axi_uartlite_0', {'r3': 'UART at 9600 baud'}, refuse='9600 is not in the range of C_BAUDRATE')
        s.assignments['r3'] = dict(ids=['r3'], cells=['axi_uartlite_0'])
        with self.assertRaises(ValueError) as caught:
            await s.request(dict(op='select', selections=[dict(requirement_id='r3', candidate_id='p1', value='9600')]))
        self.assertNotIn('"op":"assign"', str(caught.exception))

    async def test_approval_gate_restores_the_exact_proposal_and_detects_divergence(self):
        s = self.session
        await self.begin(['processor preset', 'UART at 9600 baud'])
        await s.request(dict(op='choose_ced', name='alpha_preset'))
        after = s.root / 'evidence' / 'after.tsv'
        after.write_text('\n'.join([row('axi_uartlite_0', 'CONFIG.C_BAUDRATE', '9600'),
                                    row('axi_uartlite_0', 'CONFIG.Component_Name', 'old')]) + '\n')
        s.stage, s.phase2 = 'awaiting_approval', dict(status='proposed', proposal_id='p1')
        s.proposal = dict(id='p1', tag='proposal-1', after=str(after))
        with self.assertRaises(ValueError):
            await s.request(dict(op='describe', requirement_id='r2', query='baud'))
        with self.assertRaises(ValueError):
            await s.approve('p2')
        s.snapshots = ['\n'.join([row('axi_uartlite_0', 'CONFIG.C_BAUDRATE', '115200'),
                                  row('axi_uartlite_0', 'CONFIG.Component_Name', 'renamed')]) + '\n']
        reply = await s.approve('p1')
        self.assertFalse(reply['matches_proposal'])
        self.assertEqual(reply['divergent'], [dict(cell='axi_uartlite_0', parameter='C_BAUDRATE', expected='9600', actual='115200')])
        self.assertIn('[cedcfg::restore ' + atom('proposal-1') + ']', s.calls)

    async def test_finish_while_awaiting_approval_keeps_phase1_and_reports_declined(self):
        s = self.session
        await self.begin(['processor preset', 'UART at 9600 baud'])
        await s.request(dict(op='choose_ced', name='alpha_preset'))
        await s.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Microcontroller')]))
        await s.request(dict(op='defer', requirement_id='r2'))
        await s.request(dict(op='instantiate'))
        s.assignments['r2'] = dict(ids=['r2'], cells=['axi_uartlite_0'])

        class Sub:
            selections = {('r2', 'p1'): dict(value='9600')}
            waived = {}
        s.subs['axi_uartlite_0'] = Sub()
        s.stage, s.phase2 = 'awaiting_approval', dict(status='proposed', proposal_id='p1')
        final = await s.request(dict(op='finish'))
        self.assertFalse(final['full'])
        self.assertEqual(final['phase2']['status'], 'proposed')
        self.assertEqual([r['status'] for r in final['requirements']], ['phase1_option', 'phase2_declined'])
        self.assertFalse(any(c.startswith('[cedcfg::restore') for c in s.calls))

    async def test_finishing_instead_of_applying_an_approval_already_given_is_refused_once(self):
        """next_op said ced_apply_phase2 and the model called finish anyway.

        ced-gen_M7's 26B run proposed both writes, finished, and finish read
        that as a decline: it recorded both requirements phase2_declined, saved
        the untouched Phase 1 design and closed Vivado. The model then called
        ced_apply_phase2 against a dead session, got nothing back, and restarted
        with begin until it hit the retry limit, so its final answer named no
        design at all. Where the user approved in advance, finish has to say so
        before it throws the approval away.
        """
        s = self.session
        await self.begin(['processor preset', 'UART at 9600 baud'], phase2_approval='pre_approved')
        await s.request(dict(op='choose_ced', name='alpha_preset'))
        await s.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Microcontroller')]))
        await s.request(dict(op='defer', requirement_id='r2'))
        await s.request(dict(op='instantiate'))
        s.stage, s.phase2 = 'awaiting_approval', dict(status='proposed', proposal_id='p1')
        s.proposal = dict(id='p1', tag='proposal-1')
        with self.assertRaisesRegex(ValueError, 'ced_apply_phase2'):
            await s.request(dict(op='finish'))
        self.assertEqual(s.stage, 'awaiting_approval', 'the refusal leaves the proposal standing')
        # Meaning it is still allowed: the second finish declines as before.
        final = await s.request(dict(op='finish'))
        self.assertEqual(final['phase2']['status'], 'proposed')
        self.assertTrue(final['session_closed'])

    async def test_an_ask_session_may_finish_straight_from_awaiting_approval(self):
        """Declining is what finish means where begin recorded "ask"."""
        s = self.session
        await self.begin(['processor preset'])
        s.stage, s.phase2 = 'awaiting_approval', dict(status='proposed', proposal_id='p1')
        s.proposal = dict(id='p1', tag='proposal-1')
        self.assertFalse(s.pre_approved)
        final = await s.request(dict(op='finish'))
        self.assertTrue(final['session_closed'])

    def slow_session(self, delay=0.3, fail_on=None, slow='[cedcfg::instantiate'):
        """A Vivado where one helper call outlasts the reply budget, optionally failing another."""
        class Slow(Session):
            builds = projects = 0

            async def native(self, expression, prefix=''):
                if fail_on and expression.startswith(fail_on):
                    raise ValueError('NATIVE_ERROR: no answer from Vivado within 900 s')
                self.builds += expression.startswith('[cedcfg::instantiate')
                self.projects += expression.startswith('[cedcfg::project')
                if expression.startswith(slow):
                    await asyncio.sleep(delay)
                return await super().native(expression, prefix)
        s = Slow(Guard(self.root), None, 'sid', str(self.root / 'work/.vivado-ai/native.log'))
        s.calls, s.writes, s.REPLY_BUDGET = [], [], 0.05
        return s

    @staticmethod
    async def settle(s, request):
        """Send a request and poll status while it runs, the way SKILL.md asks."""
        reply = await s.request(request)
        while reply.get('running'):
            reply = await s.request(dict(op='status'))
        return reply

    async def build_ready(self, s):
        await self.settle(s, dict(op='begin', task='Alpha processor preset on the ZCU102.', target='zcu102',
                                  requirements=['processor preset']))
        await self.settle(s, dict(op='choose_ced', name='alpha_preset'))
        await s.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Microcontroller')]))
        await self.waive_pending(s)

    @staticmethod
    async def waive_pending(s):
        for row in s.pending_phase1():
            await s.request(dict(op='waive', requirement_id=row['id'], reason='context the design covers'))

    async def test_a_build_that_outlasts_the_reply_budget_is_awaited_and_never_repeated(self):
        """ced-gen_M6/M3/H4 (Haiku): the client gave up on instantiate at 60 s,
        status still said options, and a second instantiate queued a second
        build that died with [Vivado_Tcl 4-481] Design is not empty."""
        s = self.slow_session()
        await self.build_ready(s)
        reply = await s.request(dict(op='instantiate'))
        self.assertEqual((reply['stage'], reply['next_op']), ('instantiating', 'status'))
        self.assertEqual(reply['running'], 'instantiate')
        self.assertIn('running_seconds', reply)
        # A resent instantiate and a status poll, at the same time, join the one build.
        replies = await asyncio.gather(s.request(dict(op='instantiate')), s.request(dict(op='status')))
        self.assertTrue(all(r['stage'] in ('instantiating', 'phase1_done') for r in replies))
        with self.assertRaisesRegex(ValueError, 'still working on instantiate'):
            await s.request(dict(op='finish'))
        while (await s.request(dict(op='status')))['stage'] == 'instantiating':
            pass
        self.assertEqual(s.stage, 'phase1_done')
        self.assertEqual(s.builds, 1)
        with self.assertRaisesRegex(ValueError, 'Already instantiated'):
            await s.request(dict(op='instantiate'))
        self.assertEqual(s.builds, 1)
        final = await s.request(dict(op='finish'))
        self.assertTrue(final['full'])

    async def test_a_slow_begin_is_awaited_and_a_resent_begin_joins_it(self):
        """ced-gen_M3, Haiku, after the instantiate fix: begin outlasted 60 s while
        parallel sessions queued on the store mirror, the resent begin raced the
        first, and the model ended up choosing a lookalike design."""
        s = self.slow_session(slow='[cedcfg::project')
        request = dict(op='begin', task='Alpha processor preset on the ZCU102.', target='zcu102', requirements=['processor preset'])
        reply = await s.request(dict(request))
        self.assertEqual((reply['running'], reply['next_op']), ('begin', 'status'))
        with self.assertRaisesRegex(ValueError, 'still working on begin'):
            await s.request(dict(op='choose_ced', name='alpha_preset'))
        replies = await asyncio.gather(s.request(dict(request)), s.request(dict(op='status')))
        while not any('ced_candidates' in r for r in replies):
            replies = [await s.request(dict(op='status'))]
        self.assertEqual(s.projects, 1)
        self.assertEqual(s.stage, 'choosing_ced')
        with self.assertRaisesRegex(ValueError, 'Already begun'):
            await s.request(dict(request))

    async def test_a_failure_after_the_build_reached_vivado_never_reopens_options(self):
        s = self.slow_session(delay=0, fail_on='[cedcfg::cells')
        await self.build_ready(s)
        reply = await s.request(dict(op='instantiate'))
        self.assertEqual(reply['stage'], 'failed')
        self.assertIn('reached Vivado', reply['reason'])
        with self.assertRaisesRegex(ValueError, 'Already instantiated'):
            await s.request(dict(op='instantiate'))
        self.assertEqual(s.builds, 1)

    async def test_a_failure_before_the_build_reached_vivado_allows_another_instantiate(self):
        s = self.slow_session(delay=0, fail_on='[cedcfg::evaluate')
        await self.settle(s, dict(op='begin', task='Alpha processor preset on the ZCU102.', target='zcu102',
                                  requirements=['processor preset']))
        await self.settle(s, dict(op='choose_ced', name='alpha_preset'))
        await self.waive_pending(s)
        with self.assertRaisesRegex(ValueError, 'NATIVE_ERROR'):
            await s.request(dict(op='instantiate'))
        self.assertEqual((s.stage, s.builds), ('options', 0))
        s.native = Session.native.__get__(s)
        reply = await s.request(dict(op='instantiate'))
        self.assertEqual(reply['stage'], 'phase1_done')

    async def test_a_requirement_the_design_vetoed_is_neither_deferred_nor_waived(self):
        """ced-gen_H4 (Haiku): updaters vetoed Video_Streams and Stream_Source, the
        model deferred both, and Phase 2 opened, proposed nothing and reported
        not_proposed instead of the unresolved Phase 1 answer."""
        await self.choose_alpha()
        self.session.writes = [['Preset.VALUE', 'Application_Processor', 'Microcontroller', 'update_0']]
        reply = await self.session.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Application_Processor')]))
        self.assertIn('do not defer it', reply['note'])
        with self.assertRaisesRegex(ValueError, 'r1 asked for design option Preset.*cannot change design options'):
            await self.session.request(dict(op='defer', requirement_ids=['r1']))
        with self.assertRaisesRegex(ValueError, 'not for a requested value it refuses'):
            await self.session.request(dict(op='waive', requirement_id='r1', reason='preset fixed by the design'))
        self.assertEqual((self.session.deferred, self.session.waived), ([], {}))
        # The veto already answers r1, so instantiate builds without pausing on it:
        # pausing asked for a defer or waive that is refused (H4 then finished unbuilt).
        await self.waive_pending(self.session)
        self.assertEqual((await self.session.request(dict(op='instantiate')))['stage'], 'phase1_done')
        final = await self.session.request(dict(op='finish'))
        self.assertEqual(final['requirements'][0]['status'], 'unresolved')
        self.assertEqual((final['report']['phase2_status'], final['full']), ('not_needed', False))

    async def test_a_named_design_this_board_cannot_build_is_answered_by_begin(self):
        """ced-gen_H6 (Haiku): Zynq-7000 preset on the ZCU102. The model read the
        card marked supported=false and answered without choose_ced or finish,
        so no report existed and it wrote phase2_status not_proposed."""
        reply = await self.session.request(dict(op='begin', task='Beta PCIe endpoint example on the ZCU102.', target='zcu102',
                                                requirements=['Beta PCIe endpoint example']))
        expected = dict(ced='beta_pcie', phase1_options={}, phase2_status='not_needed', full=False)
        self.assertEqual((reply['next_op'], reply['requested_design'], reply['report']), ('finish', 'Beta PCIe Endpoint', expected))
        self.assertEqual(reply['example_boards'], ['vck190', 'vpk120'])
        final = await self.session.request(dict(op='finish'))
        self.assertEqual(final['report'], expected)
        self.assertFalse(any(c.startswith('[cedcfg::options') for c in self.session.calls))
        # A request naming a design that builds here gets the ordinary cards.
        s = Session(Guard(self.root), None, 'sid', str(self.root / 'work/.vivado-ai/native.log'))
        s.calls, s.writes = [], []
        reply = await s.request(dict(op='begin', task='Alpha processor preset on the ZCU102.', target='zcu102',
                                     requirements=['processor preset']))
        self.assertEqual(reply['next_op'], 'choose_ced')
        self.assertNotIn('requested_design', reply)


    async def test_a_selection_the_engine_discarded_is_reported_not_kept(self):
        """ced-gen_H7: c_include_sg was dropped beside c_sg_length_width and the
        reply showed only the width, so the model reported scatter-gather applied."""
        reply = dict(selected=[dict(requirement_id='r1', key='CONFIG.c_sg_length_width', value='26')],
                     selection_repairs=[dict(candidate_id='r1.p1', repair='dropped_unrelated_boolean_for_exact_width', value=1),
                                        dict(candidate_id='r1.p2', repair='reused_selected_value')])
        compact = CEDSession.compact_sub('r1', 'axi_dma_0', reply)
        self.assertEqual(compact['not_kept'], [dict(candidate_id='r1.p1', repair='dropped_unrelated_boolean_for_exact_width')])

    async def test_an_upstream_handoff_marked_running_is_awaited_not_read_as_finished(self):
        """The 2026.10 Vivado MCP hands a command off at 300 s with status running
        and no error_type; ced-gen_H4's build was read as finished with no reply."""
        from mcp import types

        def result(data):
            return types.CallToolResult(structuredContent=data, content=[types.TextContent(type='text', text=json.dumps(data))])

        class Upstream:
            calls = []

            async def call_tool(self, name, arguments):
                self.calls.append(name)
                if name == 'vivado_status':
                    return result(dict(is_command_running=False))
                if len([c for c in self.calls if c == 'vivado_execute']) == 1:
                    return result(dict(status='running', elapsed_seconds=300, message='still running after 300s'))
                return result(dict(exit_code=0, output='__IPCFG__' + base64.b64encode(b'BUILT').decode()))
        upstream = Upstream()
        s = CEDSession(Guard(self.root), upstream, 'sid', str(self.root / 'work/.vivado-ai/native.log'))
        s.DETACH_POLL = 0
        self.assertEqual(await s.native('[cedcfg::instantiate x]'), 'BUILT')
        self.assertEqual(upstream.calls, ['vivado_execute', 'vivado_status', 'vivado_execute'])

    async def test_a_slow_finish_answers_within_the_budget_and_status_returns_the_report(self):
        s = self.slow_session(slow='[cedcfg::save')
        await self.build_ready(s)
        reply = await s.request(dict(op='instantiate'))
        self.assertIn('nothing for the user to approve', reply['note'])
        reply = await s.request(dict(op='finish'))
        self.assertEqual((reply['running'], reply['next_op']), ('finish', 'status'))
        while 'report' not in reply:
            reply = await s.request(dict(op='status'))
        self.assertTrue(reply['session_closed'])
        self.assertEqual(reply['report']['phase2_status'], 'not_needed')

    async def test_a_requirement_that_only_names_the_design_and_target_is_context(self):
        """Gemma-26B, stage1: E6/M3 left "Versal processor performance example" and
        a task clause begin added unmapped, instantiated twice and reported
        full false; E4 deferred such a clause into a Phase 2 with nothing to do."""
        s = self.session
        await s.request(dict(op='begin', task='Alpha processor presets on the ZCU102 with the UART at 9600 baud.',
                             target='zcu102', requirements=['Alpha processor presets on the ZCU102', 'UART at 9600 baud']))
        await s.request(dict(op='choose_ced', name='alpha_preset'))
        with self.assertRaisesRegex(ValueError, 'r1 only names the chosen design and the target.*"op":"waive"'):
            await s.request(dict(op='defer', requirement_ids=['r1']))
        await s.request(dict(op='defer', requirement_ids=['r2']))
        reply = await s.request(dict(op='instantiate'))
        self.assertNotIn('instantiate_paused', reply)
        self.assertEqual(reply['stage'], 'phase2')
        self.assertEqual(s.waived['r1'], 'names only the chosen design and the target')
        # A requirement with its own feature words is never waived this way.
        self.assertFalse(s.names_only_context('r2'))

    async def test_a_requirement_moved_off_a_bankless_cell_cannot_be_moved_back(self):
        """ced-gen_H3 (Haiku): r4 left clk_wiz_0, which has no parameter bank, for
        ps_e, then went straight back and the run ended unresolved."""
        s = await self.phase2_with_two_cells()
        self.sub('rst_ps7_0_50M', {'r2': 'PL clock at 100 MHz'}, no_bank=True)
        self.sub('axi_uartlite_0', {})
        s.assignments['r2'] = dict(ids=['r2'], cells=['rst_ps7_0_50M'])
        await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k1')]))
        self.assertEqual(s.assignments['r2']['cells'], ['axi_uartlite_0'])
        with self.assertRaisesRegex(ValueError, 'r2 was moved off rst_ps7_0_50M, which has no parameter bank.*"op":"describe"'):
            await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k2')]))
        self.assertEqual(s.assignments['r2']['cells'], ['axi_uartlite_0'])

    async def test_an_unattended_session_that_asked_cannot_apply_a_proposal(self):
        """ced-gen_H5w (Haiku): approval withheld, yet 230400 baud was written,
        because every eval backend auto-allows ced_apply_phase2."""
        import ced_guard
        guard = Guard(self.root)

        class Asked:
            pre_approved = False
            approved = False

            async def approve(self, proposal_id):
                self.approved = True
                return dict(applied=proposal_id)
        session = Asked()
        guard.structured_sessions = {'sid': session}
        with unittest.mock.patch.dict(os.environ, {'CED_UNATTENDED': '1'}):
            result = await ced_guard.apply_phase2(guard, None, dict(session_id='sid', proposal_id='p1'))
            self.assertIn('unattended', json.dumps(result.model_dump(mode='json')))
            self.assertFalse(session.approved)
            session.pre_approved = True
            await ced_guard.apply_phase2(guard, None, dict(session_id='sid', proposal_id='p1'))
            self.assertTrue(session.approved)
        session.pre_approved, session.approved = False, False
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CED_UNATTENDED', None)
            await ced_guard.apply_phase2(guard, None, dict(session_id='sid', proposal_id='p1'))
            self.assertTrue(session.approved, 'interactive use leaves the decision to the host gate')

    async def test_parallel_sessions_opening_cells_each_keep_their_own_parent(self):
        """One bridge serves every parallel case. A class-wide CellSession.parent
        was reset by another session's assign during ced-gen_H3's 132 s ps_e open:
        'NoneType' object has no attribute 'native'."""
        sessions = [self.session, Session(Guard(self.root), None, 'sid2', str(self.root / 'work/.vivado-ai/native.log'))]
        for s in sessions:
            s.calls, s.writes = [], []
            await s.request(dict(op='begin', task='Alpha processor preset with the UART at 9600 baud on the ZCU102.',
                                 target='zcu102', requirements=['processor preset', 'UART at 9600 baud']))
            await s.request(dict(op='choose_ced', name='alpha_preset'))
            await s.request(dict(op='waive', requirement_id='r1', reason='default preset'))
            await s.request(dict(op='defer', requirement_ids=['r2']))
            await self.waive_pending(s)
            await s.request(dict(op='instantiate'))
        parents = []

        async def adopted(cls, *args):
            # The first session's open is the slow one; the second finishes inside it.
            await asyncio.sleep(0.1 if cls.parent is sessions[0] else 0)
            parents.append(cls.parent)
            raise ValueError('stop after recording the parent')

        with unittest.mock.patch.object(ced_session.CellSession, 'adopted', classmethod(adopted)):
            await asyncio.gather(*(s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k1')]))
                                   for s in sessions), return_exceptions=True)
        self.assertEqual(sorted(map(id, parents)), sorted(map(id, sessions)))

    async def test_a_requirement_whose_vetoed_part_was_dropped_is_not_met(self):
        """ced-gen_H4 (Gemma-26B): the design vetoed DDR source and multi-stream,
        the model kept only Video_Streams=4 for the same requirement, and the
        report said full: true."""
        await self.choose_alpha()
        s = self.session
        s.writes = [['Preset.VALUE', 'Application_Processor', 'Microcontroller', 'update_0']]
        await s.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Application_Processor')]))
        s.writes = []
        reply = await s.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o2', value='false')]))
        self.assertEqual(reply['accepted'][0]['option'], 'Include_DDR')
        await self.waive_pending(s)
        await s.request(dict(op='instantiate'))
        final = await s.request(dict(op='finish'))
        self.assertEqual(final['requirements'][0]['status'], 'overridden_by_design')
        self.assertFalse(final['full'])

    async def test_a_waiting_proposal_names_the_next_step_instead_of_set_options(self):
        """ced-gen_H5 (Gemma-26B): select during a proposal approved in advance was told
        to use set_options; three refusals ended the session unapplied."""
        s = self.session
        await self.begin(['processor preset'])
        s.stage, s.pre_approved = 'awaiting_approval', True
        s.proposal = dict(id='p1', tag='proposal-1')
        with self.assertRaisesRegex(ValueError, 'awaiting approval.*call the ced_apply_phase2 tool .* p1, then finish'):
            await s.request(dict(op='select', selections=[dict(requirement_id='r1', candidate_id='p1', value='1')]))
        s.pre_approved = False
        with self.assertRaisesRegex(ValueError, 'stop and ask the user'):
            await s.request(dict(op='describe', requirement_id='r1', query='x'))

    async def test_a_stated_value_is_not_waived_and_the_clock_driver_is_named(self):
        """ced-gen_M7 (Gemma-26B) waived "PL clock at 100 MHz" on the BRAM
        controller and the clock stayed at 50."""
        s = await self.phase2_with_two_cells()
        sub = self.sub('axi_uartlite_0', {'r2': 'PL clock at 100 MHz'})
        s.requirements['r2'] = 'PL clock at 100 MHz'
        s.assignments['r2'] = dict(ids=['r2'], cells=['axi_uartlite_0'])
        s.drivers = {'axi_uartlite_0': dict(pin='s_axi_aclk', cell='rst_ps7_0_50M', source_pin='slowest_sync_clk')}
        with self.assertRaisesRegex(ValueError, 'r2 asks for 100 MHz; a waiver would report that value as met.*"cell_id":"k2"'):
            await s.request(dict(op='waive', requirement_id='r2', reason='system-level clock'))
        self.assertIn('r2', sub.exhausted, 'propose may now report it unresolved, as the refusal says')
        s.drivers = {}

    async def test_a_requirement_the_selected_preset_label_provides_is_not_deferred(self):
        """ced-gen_E2 (Gemma-26B) deferred "block RAM" under Preset=PS7_PL
        ("PS+PL with GPIO and Block RAM") and it ended unresolved."""
        await self.begin(['processor preset', 'embedded linux capable', 'UART at 9600 baud'])
        s = self.session
        await s.request(dict(op='choose_ced', name='alpha_preset'))
        await s.request(dict(op='set_options', selections=[dict(requirement_id='r1', option_id='o1', value='Application_Processor')]))
        with self.assertRaisesRegex(ValueError, 'r2 is already provided by the selected Preset=Application_Processor'):
            await s.request(dict(op='defer', requirement_ids=['r2']))
        reply = await s.request(dict(op='defer', requirement_ids=['r3']))
        self.assertEqual(reply['deferred'], ['r3'])

    async def test_a_generated_helper_cell_names_the_cell_it_serves_before_taking_the_work(self):
        """ced-gen_M7 (Haiku): "the block RAM controller in single-port mode"
        was configured on axi_bram_ctrl_0_bram, so the controller stayed dual
        port while validation passed."""
        s = await self.phase2_with_two_cells()
        s.cells['k3'] = dict(path='axi_uartlite_0_helper', vlnv='xilinx.com:ip:blk_mem_gen:8.4',
                             display='Block Memory Generator', ports=[])
        rows = {row['id']: row for row in s.cell_rows(0)}
        self.assertEqual(rows['k3']['generated_for'], 'axi_uartlite_0')
        self.assertNotIn('generated_for', rows['k1'], 'the cell it serves is not itself generated')
        with self.assertRaisesRegex(ValueError, r'k3 axi_uartlite_0_helper is the Block Memory Generator Vivado '
                                                r'generated to serve axi_uartlite_0 \(k1, AXI Uartlite\).*Assign r2 to k1'):
            await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k3')]))
        self.sub('axi_uartlite_0_helper', {})
        reply = await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k3')]))
        self.assertEqual(s.assignments['r2']['cells'], ['axi_uartlite_0_helper'], 'saying it twice means it')
        self.assertTrue(reply['assigned'])

    async def test_a_frequency_assigned_to_a_cell_another_cell_clocks_names_that_cell(self):
        """ced-gen_M7 (Gemma-26B): "PL clock at 100 MHz" was assigned to the BRAM
        controller, which processing_system7_0 clocks. Three describes returned
        read latency and ECC, and the clock was reported unresolved at 50 MHz."""
        s = await self.phase2_with_two_cells()
        s.requirements['r2'] = 'PL clock at 100 MHz'
        s.drivers = {'rst_ps7_0_50M': dict(pin='s_axi_aclk', cell='axi_uartlite_0', source_pin='clk_out')}
        with self.assertRaisesRegex(ValueError, 'r2 states a frequency and rst_ps7_0_50M does not set its own clock.*'
                                                'axi_uartlite_0 \\(k1\\) drives rst_ps7_0_50M/s_axi_aclk.*'
                                                '"requirement_id":"r2","cell_id":"k1"'):
            await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k2')]))
        self.assertNotIn('r2', s.assignments, 'nothing was opened while the move was offered')
        self.sub('rst_ps7_0_50M', {})
        reply = await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k2')]))
        self.assertEqual(s.assignments['r2']['cells'], ['rst_ps7_0_50M'], 'saying it twice means it')
        self.assertTrue(reply['assigned'])
        s.drivers = {}

    async def test_the_move_names_the_parameter_that_sets_the_driving_pin(self):
        """ced-gen_H3 (Gemma-31B): told ps_e drives the wizard "through pl_clk0",
        the model described ps_e four times by the pin's name and never reached
        PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ. The refusal now names it, with the
        describe call that reaches it; without a clear winner it says the pin
        name is not a parameter name instead of naming a guess."""
        s = await self.phase2_with_two_cells()
        s.requirements['r2'] = 'Feed the clocking wizard from a 125 MHz PL clock 0'
        self.sub('rst_ps7_0_50M', {'r2': s.requirements['r2']}, no_bank=True,
                 refuse='CONFIG.REF_CLK_FREQ has no published range ... this cell has no parameter bank in this session.')
        s.assignments['r2'] = dict(ids=['r2'], cells=['rst_ps7_0_50M'])
        s.drivers = {'rst_ps7_0_50M': dict(pin='clk_in1', cell='axi_uartlite_0', source_pin='pl_clk0')}
        pins = __import__('json').loads((SKILL / 'tests' / 'fixtures' / 'frequency_sources.json').read_text())['pins']
        s.frequency_sources = {'axi_uartlite_0': pins['ps_e/pl_clk0']}
        with self.assertRaises(ValueError) as caught:
            await s.request(dict(op='select', selections=[dict(requirement_id='r2', candidate_id='p1', value='125')]))
        message = str(caught.exception)
        self.assertIn('{"requirement_id":"r2","cell_id":"k1"}', message, 'the move itself is unchanged')
        self.assertIn('set by CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ', message)
        self.assertIn('{"op":"describe","requirement_id":"r2","query":"PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ"}', message)
        # A request that does not say which clock gets the warning, not a guess.
        s.requirements['r2'] = 'Clock the BRAM controller at 100 MHz'
        with self.assertRaises(ValueError) as caught:
            await s.request(dict(op='select', selections=[dict(requirement_id='r2', candidate_id='p1', value='100')]))
        self.assertIn('pl_clk0 is a pin name, not a parameter name', str(caught.exception))
        self.assertNotIn('set by CONFIG.', str(caught.exception))
        s.drivers, s.frequency_sources = {}, {}

    async def test_a_clocking_wizard_keeps_the_frequency_it_generates(self):
        """mbv-scu35-200 on 2026.2: "MicroBlaze V clock at 200 MHz" was assigned to
        clk_wiz_1, which drives microblaze_riscv_0/Clk through clk_out1 -- the right
        cell. clk_wiz fails create_gui on this build, so every row came back PB_MISS,
        and the flow read the missing bank as "nothing can be set here": select refused
        CLKOUT1_REQUESTED_OUT_FREQ and sent the model to the cell producing the clock,
        which was the wizard it was on. The run ended unresolved at 100 MHz. Written
        directly the value validates with 0 errors, and propose checks it by readback.

        A cell with clock loads generates the clock it hands out, so it is never told
        it "does not set its own clock", however its reference input is fed.
        """
        s = await self.phase2_with_two_cells()
        s.cells['k3'] = dict(path='clk_wiz_1', vlnv='xilinx.com:ip:clk_wiz:6.0',
                             display='Clocking Wizard', ports=['clk_out1'])
        s.requirements['r2'] = 'MicroBlaze V clock at 200 MHz'
        s.drivers = {'clk_wiz_1': dict(pin='clk_in1', cell='axi_uartlite_0', source_pin='clk_out')}
        s.clock_loads = {'clk_wiz_1': dict(pin='clk_out1', cell='rst_ps7_0_50M', sink_pin='slowest_sync_clk')}
        # Bank-less, but still holding the one row that sets the output frequency.
        self.sub('clk_wiz_1', {'r2': 'MicroBlaze V clock at 200 MHz'}, no_bank=True, blocks=False)
        reply = await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k3')]))
        self.assertEqual(s.assignments['r2']['cells'], ['clk_wiz_1'], 'the wizard generates the clock')
        self.assertTrue(reply['assigned'])
        self.assertNotEqual(s.state()['next_op'], 'assign', 'there is nowhere better to move it')
        s.drivers, s.clock_loads = {}, {}

    def test_a_degraded_cell_that_owns_a_value_is_not_sent_away_from_it(self):
        """The per-cell notice carried the same misdirection as the refusal."""
        produced = dict(key='CONFIG.CLKOUT1_REQUESTED_OUT_FREQ', label='Clkout1 requested out freq',
                        range='PB_MISS:CLKOUT1_REQUESTED_OUT_FREQ', range_scope='unavailable')
        received = dict(key='CONFIG.REF_CLK_FREQ', label='Ref clk freq',
                        range='PB_MISS:REF_CLK_FREQ', range_scope='unavailable')
        note = CEDSession.compact_sub('r2', 'clk_wiz_1', dict(candidates=[produced, received]))['metadata']
        self.assertIn('does not answer here', note)
        self.assertIn('drives on its own output is still set here', note)
        self.assertNotIn('"op":"cells"', note, 'the wizard is the cell that produces the clock')
        blocked = CEDSession.compact_sub('r2', 'rst_ps7_0_50M', dict(candidates=[received]))['metadata']
        self.assertIn('set on the cell that produces it', blocked)
        self.assertIn('"op":"cells"', blocked)

    async def test_a_cell_that_sets_its_own_clock_takes_a_frequency_unremarked(self):
        """The warning reads the block design, not the requirement's words: a
        clock source has no driver, so ced-gen_M1's ps_e assignment is untouched."""
        s = await self.phase2_with_two_cells()
        s.requirements['r2'] = 'PL clock at 100 MHz'
        s.drivers = {}
        self.sub('axi_uartlite_0', {})
        reply = await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k1')]))
        self.assertEqual(s.assignments['r2']['cells'], ['axi_uartlite_0'])
        self.assertTrue(reply['assigned'])

    async def test_two_cells_that_clock_each_other_leave_the_assignment_alone(self):
        """ced-gen_H3: ps_e/pl_clk0 feeds clk_wiz_0/clk_in1 and clk_wiz_0/clk_out2
        feeds ps_e/maxihpm0_fpd_aclk. Warning on both bounced r5 between them
        until the budget ran out and left it on clk_wiz_0, the wrong cell."""
        s = await self.phase2_with_two_cells()
        s.requirements['r2'] = 'Feed the clocking wizard from a 125 MHz PL clock 0'
        s.drivers = {'rst_ps7_0_50M': dict(pin='clk_in1', cell='axi_uartlite_0', source_pin='pl_clk0'),
                     'axi_uartlite_0': dict(pin='maxihpm0_fpd_aclk', cell='rst_ps7_0_50M', source_pin='clk_out2')}
        for kid, path in (('k2', 'rst_ps7_0_50M'), ('k1', 'axi_uartlite_0')):
            self.sub(path, {})
            reply = await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id=kid)]))
            self.assertTrue(reply['assigned'], path + ' is in the loop, so the design does not settle the choice')
            s.release('r2')
            s.assignments.pop('r2', None)
        s.drivers = {}

    async def test_a_bank_blocked_half_of_a_split_assignment_narrows_once_its_sibling_is_set(self):
        """ced-gen_H3 (live Opus run): r5 was assigned to both ps_e and clk_wiz_0 in
        one call. clk_wiz_0 has no parameter bank, so its half can never take a
        selection of its own -- the proposal's side_effects showed its frequency
        follow ps_e automatically. assign refused both ways to let go of that half:
        the derived id is not a deferred one, and the base id already had
        selections elsewhere. Resubmitting assign naming only the settled cell
        must narrow the assignment onto it instead.
        """
        s = await self.phase2_with_two_cells()
        s.requirements['r2'] = 'Feed the clocking wizard from a 125 MHz PL clock 0'
        self.sub('rst_ps7_0_50M', {}, no_bank=True)  # stands in for clk_wiz_0
        self.sub('axi_uartlite_0', {})  # stands in for ps_e
        reply = await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k2'),
                                                                dict(requirement_id='r2', cell_id='k1')]))
        self.assertTrue(reply['assigned'])
        self.assertEqual(sorted(s.assignments['r2']['cells']), ['axi_uartlite_0', 'rst_ps7_0_50M'])
        # Naming only ps_e (which has no bank-blocked status and no selection of
        # its own yet either) is not a narrowing: nothing here can be dropped
        # silently, so it reads the same as any other unresolved resubmission.
        with self.assertRaises(ValueError) as caught:
            await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k2')]))
        self.assertIn('already assigned', str(caught.exception))
        self.assertEqual(sorted(s.assignments['r2']['cells']), ['axi_uartlite_0', 'rst_ps7_0_50M'])
        # ps_e (k1) picks up a selection.
        s.subs['axi_uartlite_0'].selections[('r2_k1', 'p1')] = dict(value='125')
        reply = await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k1')]))
        self.assertEqual(reply['narrowed'], [dict(requirement_id='r2', dropped=['rst_ps7_0_50M'])])
        self.assertEqual(s.assignments['r2']['cells'], ['axi_uartlite_0'])
        self.assertNotIn('rst_ps7_0_50M', s.subs, 'the bank-blocked half is dropped once its sibling is settled')

    async def test_a_requirement_with_no_stated_frequency_is_not_sent_looking_for_a_clock(self):
        """"Dual channel GPIO" belongs on the cell it was assigned to even though
        that cell is clocked by another; only a stated frequency moves."""
        s = await self.phase2_with_two_cells()
        s.drivers = {'rst_ps7_0_50M': dict(pin='s_axi_aclk', cell='axi_uartlite_0', source_pin='clk_out')}
        self.sub('rst_ps7_0_50M', {})
        reply = await s.request(dict(op='assign', assignments=[dict(requirement_id='r2', cell_id='k2')]))
        self.assertEqual(s.assignments['r2']['cells'], ['rst_ps7_0_50M'], '"UART at 9600 baud" states no frequency')
        self.assertTrue(reply['assigned'])
        s.drivers = {}

    async def test_a_proposal_vivado_rejected_outright_is_the_answer_and_closes_phase_2(self):
        """ced-gen_M5 (Haiku): every write was rejected by the board interface,
        which is the graded outcome. The model read the next refusal as a broken
        session and rebuilt the whole task in a new project."""
        s = await self.phase2_with_two_cells()
        s.stage, s.phase2 = 'phase2_applied', dict(status='failed', proposal_id='p1')
        for op, request in [('describe', dict(op='describe', requirement_id='r2', query='width')),
                            ('propose', dict(op='propose')),
                            ('select', dict(op='select', selections=[dict(requirement_id='r2', candidate_id='p1', value='8')]))]:
            with self.assertRaisesRegex(ValueError, 'Phase 2 is over with phase2_status failed, so ' + op +
                                                    '.*rejected in any session.*finish, then report phase2_status failed'):
                await s.request(request)
        self.assertEqual((await s.request(dict(op='status')))['stage'], 'phase2_applied', 'status still reports')

    async def test_a_task_already_finished_in_this_workspace_is_reported_not_rebuilt(self):
        """ced-gen_M5 (Haiku) finished with the right report, then closed Vivado
        and ran the whole task again, and answered from that second session."""
        s = self.session
        await self.begin(['processor preset'])
        await s.request(dict(op='choose_ced', name='alpha_preset'))
        await s.request(dict(op='waive', requirement_id='r1', reason='default preset'))
        await s.request(dict(op='instantiate'))
        finished = await s.request(dict(op='finish'))
        self.assertEqual(json.loads((s.root / 'outputs' / s.REPORT_FILE).read_text())['report'], finished['report'])

        restarted = self.new_session()
        reply = await restarted.request(dict(op='begin', task=s.task, target='alpha',
                                             requirements=['processor preset']))
        self.assertEqual(reply['report'], finished['report'])
        self.assertEqual(reply['next_op'], 'stop')
        self.assertIn('already ran to completion in this workspace', reply['note'])

        # A different design in the same workspace still begins.
        other = self.new_session()
        reply = await other.request(dict(op='begin', task='PCIe root port endpoint bandwidth test',
                                         requirements=['PCIe root port'], target='alpha'))
        self.assertNotIn('report', reply)

    async def test_a_run_that_built_nothing_leaves_no_report_to_start_over_from(self):
        """An unknown target must not wedge the workspace: nothing was created."""
        s = self.session
        await s.request(dict(op='begin', task='design on a board nobody has', requirements=['a preset'], target='nosuch'))
        await s.request(dict(op='finish'))
        self.assertFalse((s.root / 'outputs' / s.REPORT_FILE).exists())

class HelperTests(unittest.TestCase):
    def test_a_design_name_spelled_out_by_its_description_is_context(self):
        """ced-gen_H5/H5w (Gemma-26B) deferred "Triple-modular-redundant classic
        MicroBlaze" for "TMR Microblaze Example Design" and wedged Phase 2."""
        s = CEDSession.__new__(CEDSession)
        s.entry = dict(name='TMR_Microblaze', display='TMR Microblaze Example Design',
                       description='TMR (Triple Modular Redundancy) MicroBlaze Support for Space DPU Applications.')
        s.target, s.requested_target, s.added_requirements = dict(name='xilinx.com:kcu105:part0:1.7', board='kcu105'), 'kcu105', []
        for text, context in [('Triple-modular-redundant classic MicroBlaze', True), ('TMR MicroBlaze on the KCU105', True),
                              ('all three UARTs at 230400 baud', False), ('MicroBlaze with 32 KB caches', False)]:
            s.requirements = {'r1': text}
            self.assertEqual(s.names_only_context('r1'), context, text)
        # The synonym table covers the acronym even when the description does not spell it out.
        s.entry = dict(name='TMR_Microblaze', display='TMR Microblaze Example Design', description='')
        s.requirements = {'r1': 'Triple-modular-redundant classic MicroBlaze'}
        self.assertTrue(s.names_only_context('r1'))
        s.entry = dict(name='cpm6_bmd', display='Versal CPM6 BMD Design', description='')
        s.requirements = {'r1': 'Versal CPM6 bus-mastering DMA design'}
        self.assertTrue(s.names_only_context('r1'))
        # "classic" is implied only by a MicroBlaze design without V in its name.
        s.entry = dict(name='microblaze_v_preset', display='MicroBlaze V Design Presets', description='')
        s.requirements = {'r1': 'classic MicroBlaze V'}
        self.assertFalse(s.names_only_context('r1'))

    def test_a_step_number_is_accepted_and_ignored_by_the_session(self):
        """SKILL.md numbers every op for the stock MCP; on the examples/ relay that number is
        not required, but a model that copies the skill's ops must not be refused for it."""
        with tempfile.TemporaryDirectory() as tmp:
            session = CEDSession(Guard(Path(tmp)), None, 'sid', str(Path(tmp) / 'a/b/c.log'))
            reply = asyncio.run(session.request({'seq': 7, 'op': 'status'}))
            self.assertEqual(reply['next_op'], 'begin')

    def test_debug_info_parsing_keeps_param_and_gui_fields(self):
        params = parse_debug_info(DEBUG)
        self.assertEqual([p['Name'] for p in params], ['Preset', 'Include_DDR'])
        self.assertEqual(params[0]['display Name'], 'Preset Configurations')
        self.assertEqual(params[1]['Enabled'], '0')

    def test_diff_separates_requested_changes_side_effects_and_dictionary_subkeys(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = CEDSession(Guard(Path(tmp)), None, 'sid', str(Path(tmp) / 'a/b/c.log'))

            class Sub:
                selections = {('r2', 'p1'): dict(value='9600')}
                candidates = {'p1': dict(key='CONFIG.C_BAUDRATE')}
                derived_enables = []
            session.subs = {'axi_uartlite_0': Sub()}
            big_old = tcl(tdict(dict(PS_UART0_BAUD_RATE='115200', PS_PADDING='x' * 150)))
            big_new = tcl(tdict(dict(PS_UART0_BAUD_RATE='230400', PS_PADDING='x' * 150)))
            before, after = Path(tmp) / 'before.tsv', Path(tmp) / 'after.tsv'
            before.write_text('\n'.join([row('axi_uartlite_0', 'CONFIG.C_BAUDRATE', '115200'), row('clk', 'CONFIG.FREQ', '100'),
                                         row('cips', 'CONFIG.PS_PMC_CONFIG', big_old)]))
            after.write_text('\n'.join([row('axi_uartlite_0', 'CONFIG.C_BAUDRATE', '9600'), row('clk', 'CONFIG.FREQ', '125'),
                                        row('cips', 'CONFIG.PS_PMC_CONFIG', big_new)]))
            changes = {(c['cell'], c['parameter']): c for c in session.diff(before, after)}
            self.assertEqual(changes[('axi_uartlite_0', 'C_BAUDRATE')]['requirements'], ['r2'])
            self.assertEqual(changes[('clk', 'FREQ')]['kind'], 'side_effect')
            self.assertEqual(changes[('cips', 'PS_PMC_CONFIG/PS_UART0_BAUD_RATE')]['after'], '230400')
            self.assertNotIn(('cips', 'PS_PMC_CONFIG/PS_PADDING'), changes)


class RunRootTest(unittest.TestCase):
    """The run root must never climb out of the run, whatever Vivado's working directory is.

    Vivado's working directory is meant to be <run>/work. Deriving the root by
    climbing a fixed number of folders meant that a session started anywhere
    else put proj/, evidence/ and snapshots/ in the working directory's parent.
    On a test host that parent holds every case's workspace, so runs built their
    projects on top of each other (ced-gen_E2 and ced-gen_M7, PR #722 CI).
    """

    def test_work_subfolder_gives_its_parent(self):
        self.assertEqual(ced_session.run_root('/runs/case1/work/.vivado-ai/s.log'),
                         Path('/runs/case1'))

    def test_other_working_directory_is_its_own_root(self):
        # Started in the workspace itself: the root is the workspace, not /runs.
        self.assertEqual(ced_session.run_root('/runs/case1/.vivado-ai/s.log'),
                         Path('/runs/case1'))

    def test_root_stays_inside_the_working_directory_tree(self):
        for log in ('/runs/case1/work/.vivado-ai/s.log', '/runs/case1/.vivado-ai/s.log',
                    '/runs/case1/scratch/.vivado-ai/s.log'):
            with self.subTest(log=log):
                self.assertNotEqual(ced_session.run_root(log), Path('/runs'),
                                    'the root escaped into the directory holding every run')

    def test_every_copy_of_the_derivation_agrees(self):
        """The rule lives in four places; a copy that drifts reopens the escape."""
        self.assertIn('proc cedcfg::run_root', (SKILL / 'lib/ced.tcl').read_text())
        for path in ('lib/ced.tcl', 'lib/ced_op.tcl', 'engine/scripts/structured_config.py'):
            with self.subTest(path=path):
                text = (SKILL / path).read_text()
                self.assertIn('work', text)
                self.assertNotIn('parents[2]', text, 'a fixed climb is back')

    def test_no_fixed_climb_remains(self):
        for path in ('scripts/ced_session.py', 'scripts/ced_bridge.py'):
            with self.subTest(path=path):
                self.assertNotIn('parents[2]', (SKILL / path).read_text())


if __name__ == '__main__':
    unittest.main()
