# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Three general guards, each written from a failed Haiku trial on Vivado 2026.2 (2026-11-14).

1. open_exact: the begin `ip` and the request text name different catalog IPs (test-kit_09: ip
   axi_fifo_mm_s, requirement "... (axis_data_fifo)"); the wrong cell was created.
2. same_quantity_unselected: one of two parameters for the same quantity was selected
   (test-kit_21: C_DPHY_LANES set to 4, CMN_NUM_LANES left at 1).
3. enable_counterparts: describe returned an instance's attribute but not its enable control
   (test-kit_30: PS_PMCPL_CLK3_BUF offered for "leave PL clock 3 disabled", PS_USE_PMCPL_CLK3 not).

The data in each test is the parameter and IP names of those trials; the rules are name-shape rules
and hold no IP-specific table.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from structured_config import atom
from nested_config import enable_counterparts
from test_structured_config import Guard, Session


def session():
    return Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')


class BeginIpConflictTests(unittest.IsolatedAsyncioTestCase):
    def stub(self, s, catalog):
        """Answer lite::catalog from `catalog` (name -> vlnv); any other name is a fuzzy match."""
        original = s.native
        async def native(expression, prefix=''):
            if expression.startswith('[lite::catalog'):
                for name, vlnv in catalog.items():
                    if atom(name) in expression:
                        return 'CATALOG:EXACT vlnv ' + vlnv
                return 'CATALOG:CANDIDATES shown {}'
            return await original(expression, prefix)
        s.native = native

    async def test_requirement_naming_another_catalog_ip_stops_the_cell_being_created(self):
        s = session()
        self.stub(s, {'axi_fifo_mm_s': 'xilinx.com:ip:axi_fifo_mm_s:4.3',
                      'axis_data_fifo': 'xilinx.com:ip:axis_data_fifo:2.0'})
        result = await s.request(dict(
            op='begin', ip='axi_fifo_mm_s', cell='bench_cell_009', part='part',
            task='Configure bench_cell_009 as an AXI4-Stream Data FIFO with a depth of 128.',
            requirements=['IP is AXI4-Stream Data FIFO (axis_data_fifo)', 'FIFO depth 128']))
        self.assertEqual(result['stage'], 'choosing_ip')
        vlnvs = [row['vlnv'] for row in result['ip_candidates']]
        self.assertEqual(vlnvs[0], 'xilinx.com:ip:axis_data_fifo:2.0')
        self.assertIn('xilinx.com:ip:axi_fifo_mm_s:4.3', vlnvs)
        self.assertEqual(result['ip_hint_check']['named_in_requirements'], ['axis_data_fifo'])

    async def test_an_identifier_that_is_not_a_catalog_ip_changes_nothing(self):
        # a parameter name, a signal name and the cell's own name are identifier-shaped too
        s = session()
        self.stub(s, {'axi_fifo_mm_s': 'xilinx.com:ip:axi_fifo_mm_s:4.3'})
        result = await s.request(dict(
            op='begin', ip='axi_fifo_mm_s', cell='bench_cell_009', part='part',
            task='Configure bench_cell_009 as an AXI memory mapped to stream FIFO.',
            requirements=['set fifo_depth to 128', 'clock from s_axi_aclk']))
        self.assertEqual(result['stage'], 'discovering')

    async def test_the_same_ip_named_twice_is_not_a_conflict(self):
        s = session()
        self.stub(s, {'axis_data_fifo': 'xilinx.com:ip:axis_data_fifo:2.0'})
        result = await s.request(dict(
            op='begin', ip='axis_data_fifo', cell='cell_0', part='part',
            task='An AXI4-Stream Data FIFO.', requirements=['IP is axis_data_fifo', 'depth 128']))
        self.assertEqual(result['stage'], 'discovering')


class SameQuantityTests(unittest.TestCase):
    def advisory(self, text, candidates, picked):
        s = session()
        s.requirements = {'r1': text}
        s.candidates = {cid: dict(row, requirement_id='r1') for cid, row in candidates.items()}
        selections = {('r1', cid): dict(candidate_id=cid, value=value) for cid, value in picked.items()}
        return s.same_quantity_unselected(selections, {'r1'})

    def test_second_parameter_for_the_same_quantity_is_reported(self):
        found = self.advisory('4 active lanes', {
            'p6': dict(key='CONFIG.C_DPHY_LANES', type='long', current='1', write_mode='write'),
            'p7': dict(key='CONFIG.CMN_NUM_LANES', type='long', current='1', write_mode='write'),
            'p8': dict(key='CONFIG.HP_IO_BANK_SELECTION', type='long', current='64', write_mode='write')},
            {'p6': 4})
        self.assertEqual([a['key'] for a in found[0]['also']], ['CONFIG.CMN_NUM_LANES'])
        self.assertEqual(found[0]['selected_key'], 'CONFIG.C_DPHY_LANES')

    def test_another_interfaces_copy_of_the_quantity_is_not_offered(self):
        # M and AXIS are neither generic nor in the request: this is a different interface's width
        self.assertEqual(self.advisory('AXI data width 64', {
            'p1': dict(key='CONFIG.S_AXI_DATA_WIDTH', type='long', current='32', write_mode='write'),
            'p2': dict(key='CONFIG.M_AXIS_DATA_WIDTH', type='long', current='32', write_mode='write')},
            {'p1': 64}), [])

    def test_nothing_is_reported_when_both_were_selected_or_already_agree(self):
        rows = {'p6': dict(key='CONFIG.C_DPHY_LANES', type='long', current='1', write_mode='write'),
                'p7': dict(key='CONFIG.CMN_NUM_LANES', type='long', current='1', write_mode='write')}
        self.assertEqual(self.advisory('4 active lanes', rows, {'p6': 4, 'p7': 4}), [])
        rows['p7'] = dict(rows['p7'], current='4')
        self.assertEqual(self.advisory('4 active lanes', rows, {'p6': 4}), [])

    def test_a_disabled_or_read_only_sibling_is_not_offered(self):
        for change in (dict(enabled=False), dict(write_mode='assert')):
            self.assertEqual(self.advisory('4 active lanes', {
                'p6': dict(key='CONFIG.C_DPHY_LANES', type='long', current='1', write_mode='write'),
                'p7': dict(dict(key='CONFIG.CMN_NUM_LANES', type='long', current='1', write_mode='write'), **change)},
                {'p6': 4}), [])


class EnableCounterpartTests(unittest.TestCase):
    ROWS = [dict(key='CONFIG.PS11_CONFIG', subkey=name) for name in (
        'PS_PMCPL_CLK3_BUF', 'PS_USE_PMCPL_CLK3', 'PS_USE_PMCPL_CLK2', 'PS_PMCPL_CLK2_BUF',
        'PMC_CRP_PL3_REF_CTRL_DIVISOR0', 'PS_HSDP0_REFCLK')]

    def offered(self, query, selected=('PS_PMCPL_CLK3_BUF',)):
        picked = [r for r in self.ROWS if r['subkey'] in selected]
        return [r['subkey'] for r in enable_counterparts(self.ROWS, picked, query)]

    def test_disable_request_brings_the_instances_own_enable_control(self):
        self.assertEqual(self.offered('leave PL clock 3 disabled'), ['PS_USE_PMCPL_CLK3'])

    def test_a_value_request_brings_no_enable_flag(self):
        self.assertEqual(self.offered('PL clock 3 at 100 MHz'), [])

    def test_another_instances_number_brings_nothing(self):
        self.assertEqual(self.offered('leave PL clock 2 disabled'), [])

    def test_a_field_whose_number_is_its_last_token_has_no_instance_prefix(self):
        self.assertEqual(self.offered('disable HSDP 0', selected=('PS_HSDP0_REFCLK',)), [])



class RunRootTests(unittest.TestCase):
    """The evidence root a nested-parameter schema must sit under (Gemma 4 26B, Vivado 2026.1,
    2026-11-15: the agent created <workdir>/proj itself, begin rooted the run at <workdir>, and
    the re-derived root was <workdir>'s parent, so ps_wizard's PL clock schema was rejected)."""

    def test_the_root_begin_reports_wins(self):
        s = Session(Guard(), None, 'session', '/t/trial/proj/.vivado-ai/v.log')
        s.evidence_root = Path('/t/trial/proj')
        self.assertEqual(s.run_root(), Path('/t/trial/proj'))

    def test_before_begin_reports_the_derivation_applies(self):
        owned = Session(Guard(), None, 'session', '/t/trial/proj/.vivado-ai/v.log')
        self.assertEqual(owned.run_root(), Path('/t/trial'))
        elsewhere = Session(Guard(), None, 'session', '/t/trial/.vivado-ai/v.log')
        self.assertEqual(elsewhere.run_root(), Path('/t/trial'))
        borrowed = Session(Guard(), None, 'session', '/t/trial/proj/.vivado-ai/v.log')
        borrowed.borrowed_session = True
        self.assertEqual(borrowed.run_root(), Path('/t/trial/proj'))


if __name__ == '__main__':
    unittest.main()
