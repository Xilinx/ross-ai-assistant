# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The port request: build a CED on a board it supports and carry it to the
user's board (references/port.md). Opt-in at begin, one step for the model."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_ced_session import Session, SessionTests, tcl  # noqa: E402
from structured_config import tdict  # noqa: E402

PLAN = dict(found='1', board='xilinx.com:vck190:part0:3.4', part='xcvc1902', family='versal',
            why='same family (versal) as the target and supported by the design')
APPLIED = dict(ok='1', **{'from': PLAN['board']}, to='xilinx.com:zcu102:part0:3.4', part='xczu9eg', bd='design_1',
               kept_settings=tcl(tdict(dict(ps_wizard_0=tcl(tdict(dict(PS_ASU_ENABLE='0', PS_USE_BSCAN_USER2='1')))))),
               dropped=tcl('[list ' + tdict(dict(cell='axi_gpio_0', ip='xilinx.com:ip:axi_gpio:2.0',
                                                  board_interfaces='gpio_led',
                                                  reason='the target board has no interface named gpio_led')) + ']'),
               rebound='', tied_off=tcl('[list ' + tdict(dict(pin='ps_wizard_0/pl_mmi_dc_i2s_s0_clk',
                                                              to='ilconstant cedport_tie_1',
                                                              reason='clock input the design does not use')) + ']'),
               validation='OK', result='', wrapper='w.v', dir='proj',
               messages=tcl(tdict(dict(counts='errors 0 critical_warnings 1', first='', available='1'))))


class PortSession(Session):
    plan = PLAN
    applied = APPLIED

    async def native(self, expression, prefix=''):
        if expression.startswith('[cedcfg::port_plan'):
            self.calls.append(expression)
            return tcl(tdict(self.plan))
        if expression.startswith('[cedcfg::port_reference'):
            self.calls.append(expression)
            return 'PORT_REFERENCE:OK'
        if expression.startswith('[cedcfg::port_apply'):
            self.calls.append(expression)
            return tcl(tdict(self.applied))
        return await super().native(expression, prefix)


class PortTests(SessionTests):
    def new_session(self, fresh=False):
        session = PortSession(*self._session_args())
        session.calls = []
        return session

    def _session_args(self):
        from test_ced_session import Guard
        return Guard(self.root), None, 'sid', str(self.log)

    async def begin_beta(self, **extra):
        return await self.session.request(dict(op='begin', task='Beta PCIe endpoint example on the ZCU102.',
                                               target='zcu102', requirements=['Beta PCIe endpoint example'], **extra))

    async def test_without_port_nothing_changes(self):
        reply = await self.begin_beta()
        self.assertEqual(reply['next_op'], 'finish')
        self.assertNotIn('port_possible', reply)
        self.assertFalse(any(c.startswith('[cedcfg::port') for c in self.session.calls))

    async def test_a_port_value_other_than_allowed_or_never_is_refused(self):
        with self.assertRaises(ValueError):
            await self.begin_beta(port='yes')

    async def test_the_whole_port_takes_one_extra_step(self):
        reply = await self.begin_beta(port='allowed')
        self.assertEqual(reply['next_op'], 'choose_ced')
        self.assertEqual(reply['port_possible']['reference_board'], PLAN['board'])
        reply = await self.session.request(dict(op='choose_ced', name='Beta PCIe Endpoint'))
        self.assertEqual(reply['stage'], 'options')
        self.assertEqual(reply['port']['reference_board'], PLAN['board'])
        calls = self.session.calls
        self.assertLess(next(i for i, c in enumerate(calls) if c.startswith('[cedcfg::port_reference')),
                        next(i for i, c in enumerate(calls) if c.startswith('[cedcfg::options')))
        await self.session.request(dict(op='waive', requirement_id='r1', reason='names the chosen design'))
        reply = await self.session.request(dict(op='instantiate'))
        self.assertEqual((reply['stage'], reply['next_op']), ('port_ready', 'port'))
        self.assertFalse(any(c.startswith('[cedcfg::cells') for c in self.session.calls))
        reply = await self.session.request(dict(op='port'))
        self.assertEqual((reply['stage'], reply['next_op']), ('phase1_done', 'finish'))
        port = reply['port']
        self.assertEqual(port['from'], PLAN['board'])
        self.assertEqual(port['kept_settings'], {'ps_wizard_0': {'PS_ASU_ENABLE': '0', 'PS_USE_BSCAN_USER2': '1'}})
        self.assertEqual(port['dropped'][0]['cell'], 'axi_gpio_0')
        self.assertEqual(port['dropped'][0]['board_interfaces'], ['gpio_led'])
        self.assertEqual(port['tied_off'][0]['pin'], 'ps_wizard_0/pl_mmi_dc_i2s_s0_clk')
        final = await self.session.request(dict(op='finish'))
        self.assertEqual(final['report']['port']['to'], 'xilinx.com:zcu102:part0:3.4')
        self.assertTrue(final['full'])

    async def test_no_reference_board_refuses_and_reports_why(self):
        self.session.plan = dict(found='0', family='versal', reason='no installed board that the design supports has a part of family versal')
        reply = await self.begin_beta(port='allowed')
        self.assertEqual(reply['next_op'], 'finish')
        reply = await self.session.request(dict(op='choose_ced', name='Beta PCIe Endpoint'))
        self.assertIn('no installed board', reply['port_refused'])
        self.assertFalse(any(c.startswith('[cedcfg::port_reference') for c in self.session.calls))

    async def test_a_request_naming_the_design_says_why_a_part_cannot_take_a_port(self):
        self.session.targets = [dict(kind='part', name='xczu9eg', part='xczu9eg', family='zynquplus')]
        reply = await self.session.request(dict(op='begin', task='Beta PCIe endpoint example on xczu9eg.', target='xczu9eg',
                                                requirements=['Beta PCIe endpoint example'], port='allowed'))
        self.assertEqual(reply['next_op'], 'finish')
        self.assertIn('needs a target board', reply['port_refused'])
        final = await self.session.request(dict(op='finish'))
        self.assertIn('needs a target board', final['report']['port']['refused'])

    async def test_a_part_target_cannot_be_ported_to(self):
        self.session.targets = [dict(kind='part', name='xczu9eg', part='xczu9eg', family='zynquplus')]
        await self.session.request(dict(op='begin', task='Beta PCIe endpoint example on xczu9eg.', target='xczu9eg',
                                        requirements=['Beta PCIe endpoint example'], port='allowed'))
        reply = await self.session.request(dict(op='choose_ced', name='Beta PCIe Endpoint'))
        self.assertIn('needs a target board', reply['port_refused'])

    async def test_a_port_that_cannot_carry_the_design_fails_with_its_reason(self):
        self.session.applied = dict(ok='0', reason='mig_0 is bound to the reference board\'s ddr4_dimm1, which the target lacks',
                                    messages='')
        await self.begin_beta(port='allowed')
        await self.session.request(dict(op='choose_ced', name='Beta PCIe Endpoint'))
        await self.session.request(dict(op='waive', requirement_id='r1', reason='names the chosen design'))
        await self.session.request(dict(op='instantiate'))
        reply = await self.session.request(dict(op='port'))
        self.assertEqual((reply['stage'], reply['next_op']), ('failed', 'finish'))
        self.assertIn('ddr4_dimm1', reply['port_refused'])
        final = await self.session.request(dict(op='finish'))
        self.assertFalse(final['full'])
        self.assertIn('ddr4_dimm1', final['report']['port']['refused'])

    async def test_the_most_specific_named_design_is_the_one_ported(self):
        """ced-port-haiku2: the request named "Versal MicroBlaze V Design Presets",
        which also contains every word of "MicroBlaze Design Presets"; the port
        path looked only at the shorter one, found no reference board for it and
        told the model to finish."""
        from ced_session import CEDSession
        self.session.index = type('I', (), {'entries': [
            dict(display='MicroBlaze Design Presets', name='mb_preset', vlnv='v:mb'),
            dict(display='Versal MicroBlaze V Design Presets', name='versal_mb_v_preset', vlnv='v:vmbv')]})()
        self.session.task = 'Create the Versal MicroBlaze V Design Presets example and port it to the Rave2.'
        self.assertEqual([e['name'] for e in CEDSession.named_designs(self.session)],
                         ['versal_mb_v_preset', 'mb_preset'])
        # The one-letter "V" is not a word, so the classic Versal design ties on
        # words; the name the request spells out wins (ced_port suite, P2).
        self.session.index.entries = [dict(display='Versal MicroBlaze Design Presets', name='versal_mb_preset', vlnv='v:vmb'),
                                      dict(display='Versal MicroBlaze V Design Presets', name='versal_mb_v_preset', vlnv='v:vmbv')]
        self.assertEqual(CEDSession.named_designs(self.session)[0]['name'], 'versal_mb_v_preset')

    async def test_an_uninstalled_part_names_the_design_with_fewest_unasked_words(self):
        """ced-gen_H1 on Vivado 2026.2 offline: the text search ranked the CXL EP
        bridge above the DMA design the request names."""
        from ced_session import CEDSession
        self.session.task = 'CPM6 DMA example on part xc2vp3602-vsvc3340-2LHP-e-S using controller 0 with DDR, PCIe Gen5 x4.'
        self.session.requirements = {'r1': 'CPM6 DMA example'}
        bridge = dict(display='Versal CPM6 CXL EP Bridge Design', name='cpm6_cxl_ep_brg')
        dma = dict(display='Versal CPM6 DMA Design', name='cpm6_dma')
        bmd = dict(display='Versal CPM6 BMD Design', name='cpm6_bmd')
        ranked = CEDSession.fewest_unasked(self.session, [(9, bridge), (8, dma), (7, bmd)])
        self.assertEqual([e['name'] for _, e in ranked], ['cpm6_dma', 'cpm6_bmd', 'cpm6_cxl_ep_brg'])
        # Ties keep the search order (ced-gen_H2 names the BMD design first already).
        self.session.task = 'Versal CPM6 bus-mastering demo with both controllers on xc2vp3602.'
        self.session.requirements = {'r1': 'CPM6 bus-mastering demo'}
        ranked = CEDSession.fewest_unasked(self.session, [(9, bmd), (8, dma)])
        self.assertEqual([e['name'] for _, e in ranked], ['cpm6_bmd', 'cpm6_dma'])

    async def test_port_out_of_turn_is_refused(self):
        await self.begin_beta(port='allowed')
        with self.assertRaises(ValueError):
            await self.session.request(dict(op='port'))


# The inherited SessionTests cases already run in test_ced_session; run only the port ones here.
for name in [n for n in dir(SessionTests) if n.startswith('test_')]:
    if not hasattr(PortTests, name) or getattr(PortTests, name) is getattr(SessionTests, name):
        setattr(PortTests, name, None)
del SessionTests

if __name__ == '__main__':
    unittest.main()
