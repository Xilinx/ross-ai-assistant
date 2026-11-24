import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from native_metadata import parameters, rank
from parameter_contract import parse_live, achieved_source


class NativeMetadataTests(unittest.TestCase):
    def test_direction_prefixed_cache_size_outranks_generic_cache_fields(self):
        rows = [
            {'key': 'CONFIG.C_IC_AXI_MON', 'label': 'Cache AXI Instruction Interface'},
            {'key': 'CONFIG.C_BRANCH_TARGET_CACHE_SIZE', 'label': 'Branch Target Cache Size'},
            {'key': 'CONFIG.C_ICACHE_BYTE_SIZE', 'label': 'Size in Bytes'},
            {'key': 'CONFIG.C_DCACHE_BYTE_SIZE', 'label': 'Size in Bytes'},
        ]
        self.assertEqual(rank(rows, 'instruction cache size', limit=1)[0]['key'],
                         'CONFIG.C_ICACHE_BYTE_SIZE')
        self.assertEqual(rank(rows, 'data cache size', limit=1)[0]['key'],
                         'CONFIG.C_DCACHE_BYTE_SIZE')

    def test_api_ranking_preserves_frequency_identity_and_does_not_export_snapshot_range(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'metadata.txt'
            path.write_text('''Parameter Name: ENABLE_CLOCK_MONITOR
Display Name: Clock Monitor
Description:
Type: bool
Range List: false
 true
**********
Parameter Name: CLKOUT_REQUESTED_OUT_FREQUENCY
Display Name: Requested
Description:
Type: string
Range Minimum: 1 Maximum: 100
**********
Parameter Name: CLKOUT_USED
Display Name: Clock Output Enable
Description:
Type: string
**********
''')
            rows = parameters(path)
            self.assertEqual(rows[0]['help'], '')
            self.assertTrue(all(row['range'] == 'RANGE_NONE' for row in rows))
            self.assertEqual(rank(rows, 'output clock 1 at 250 MHz')[0]['key'],
                             'CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY')

    def test_pl_clock_setting_outranks_its_readback_and_a_neighbouring_clock(self):
        """Both PS families name the fabric clock the request calls "PL": a
        Zynq-7000 spells it FPGA<n>, an UltraScale+ spells it PL<n>, and each
        sits beside an ACT_ readback of itself and a differently named clock
        that would silently take the write instead.
        """
        ps7 = [
            {'key': 'CONFIG.PCW_CLK0_FREQ', 'label': 'PCW CLK0 FREQ'},
            {'key': 'CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ', 'label': 'PCW FPGA0 PERIPHERAL FREQMHZ'},
            {'key': 'CONFIG.PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ', 'label': 'PCW ACT FPGA0 PERIPHERAL FREQMHZ'},
            {'key': 'CONFIG.PCW_ACT_TTC0_CLK0_PERIPHERAL_FREQMHZ', 'label': 'PCW ACT TTC0 CLK0 PERIPHERAL FREQMHZ'},
        ]
        self.assertEqual(rank(ps7, 'PL clock at 100 MHz')[0]['key'],
                         'CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ')
        mpsoc = [
            {'key': 'CONFIG.PSU__AUX_REF_CLK__FREQMHZ', 'label': 'PSU AUX REF CLK FREQMHZ'},
            {'key': 'CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ', 'label': 'PL0'},
            {'key': 'CONFIG.PSU__CRL_APB__PL0_REF_CTRL__ACT_FREQMHZ', 'label': 'PL0'},
            {'key': 'CONFIG.C_PL_CLK0_BUF', 'label': 'C PL CLK0 BUF'},
        ]
        self.assertEqual(rank(mpsoc, 'PL clock 0 at 150 MHz')[0]['key'],
                         'CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ')
        # Asking for the readback by name still reaches it.
        self.assertEqual(rank(mpsoc, 'actual PL clock 0 frequency MHz')[0]['key'],
                         'CONFIG.PSU__CRL_APB__PL0_REF_CTRL__ACT_FREQMHZ')

    def test_numbered_family_cannot_crowd_out_its_count_parameter(self):
        rows = [dict(key='CONFIG.IN%d_WIDTH' % i, label='In%d Width' % i) for i in range(128)]
        rows.append(dict(key='CONFIG.NUM_PORTS', label='Number of Ports'))
        ranked = rank(rows, 'number of inputs supported')
        self.assertIn('CONFIG.NUM_PORTS', [r['key'] for r in ranked])
        family = [r for r in ranked if r.get('family_pattern') == 'CONFIG.IN#_WIDTH']
        self.assertEqual([r['key'] for r in family], ['CONFIG.IN0_WIDTH', 'CONFIG.IN1_WIDTH'])
        self.assertEqual(family[0]['family_size'], 128)
        named = rank(rows, 'input 7 width')
        self.assertEqual(named[0]['key'], 'CONFIG.IN7_WIDTH')

    def test_counted_plural_shows_that_many_family_members(self):
        rows = [dict(key='CONFIG.C_UNIT%d_ENABLE' % i, label='Enable UNIT%d' % i) for i in range(4)]
        self.assertEqual([r['key'] for r in rank(rows, 'enable 3 units')],
                         ['CONFIG.C_UNIT0_ENABLE', 'CONFIG.C_UNIT1_ENABLE', 'CONFIG.C_UNIT2_ENABLE'])
        self.assertEqual(len(rank(rows, 'enable units')), 2)

    def test_counted_family_stays_together_spreads_on_counted_index_and_reaches_plural_queries(self):
        rows = ([dict(key='CONFIG.C_UNIT%d_ENABLE' % i, label='Enable UNIT%d' % i) for i in range(3)]
                + [dict(key='CONFIG.C_UNIT0_PORT%d_ENABLE_X' % p, label='Enable X') for p in range(2)]
                + [dict(key='CONFIG.C_UNIT%d_PORT%d_CORE_CLK' % (u, p), label='Core Clock') for u in range(3) for p in range(2)]
                + [dict(key='CONFIG.C_UNIT0_PORT0_ACTUAL_CORE_CLK', label='Actual Core Clock')])
        self.assertEqual([r['key'][7:] for r in rank(rows, 'enable 3 units', 3)], ['C_UNIT0_ENABLE', 'C_UNIT1_ENABLE', 'C_UNIT2_ENABLE'])
        found = [r['key'][7:] for r in rank(rows, 'core clocks at 600 MHz', 3, context=['enable 3 units'])]
        self.assertEqual(found, ['C_UNIT0_PORT0_CORE_CLK', 'C_UNIT1_PORT0_CORE_CLK', 'C_UNIT2_PORT0_CORE_CLK'])
        # Singular setting queries still inherit an explicit instance count.
        self.assertEqual(len([r for r in rank(rows, 'core clock at 600 MHz', 4, context=['enable 3 units']) if 'PORT' in r['key'] and 'ACTUAL' not in r['key']]), 3)

    def test_query_words_match_their_abbreviation(self):
        rows = [dict(key='CONFIG.CMN_VC', label='Allowed VC'), dict(key='CONFIG.C_EN_CRC', label='Enable CRC')]
        self.assertEqual([r['key'] for r in rank(rows, 'all virtual channels')], ['CONFIG.CMN_VC'])

    def test_family_member_matching_each_named_index_wins(self):
        rows = [dict(key='CONFIG.C_UNIT%d_LANE%d_INPUTS' % (u, l), label='Lane%d Inputs' % l) for u in range(3) for l in range(2)]
        ranked = rank(rows, '4 inputs on unit 2 lane0')
        self.assertEqual(ranked[0]['key'], 'CONFIG.C_UNIT2_LANE0_INPUTS')

    def test_packaged_user_parameters_replace_an_unavailable_dump(self):
        from parameter_help import user_rows
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'component.xml'
            path.write_text('''<spirit:component xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009">
<spirit:parameters>
<spirit:parameter><spirit:name>Component_Name</spirit:name><spirit:value spirit:resolve="user">x</spirit:value></spirit:parameter>
<spirit:parameter><spirit:name>C_LEVEL_HIGH</spirit:name><spirit:displayName>Level High</spirit:displayName>
<spirit:value spirit:format="long" spirit:resolve="user" spirit:minimum="0" spirit:maximum="1">1</spirit:value></spirit:parameter>
<spirit:parameter><spirit:name>C_FAMILY</spirit:name><spirit:value spirit:resolve="immediate">x</spirit:value></spirit:parameter>
</spirit:parameters></spirit:component>''')
            rows = user_rows(path)
        self.assertEqual([r['key'] for r in rows], ['CONFIG.C_LEVEL_HIGH'])
        self.assertEqual(rows[0]['range'], 'RANGE_NUM:0:1')
        self.assertEqual(rows[0]['range_scope'], 'packaged_not_live')
        self.assertEqual(rows[0]['default'], '1')

    def test_configuration_range_is_explicit_and_can_replace_old_bounds(self):
        live = parse_live('CONFIG.THRESH {value 4000 value_src USER range RANGE_NUM:5:4091 range_scope proposed_configuration_mirror range_complete 1}')
        stale = dict(range='RANGE_NUM:5:507', range_scope='native_default_instance')
        stale.update(live['CONFIG.THRESH'])
        self.assertEqual(stale['range'], 'RANGE_NUM:5:4091')
        self.assertTrue(stale['range_complete'])
        self.assertEqual(stale['range_scope'], 'proposed_configuration_mirror')

    def test_unavailable_range_does_not_retain_a_snapshot_constraint(self):
        live = parse_live('CONFIG.THRESH {value 4000 value_src USER}')
        self.assertEqual(live['CONFIG.THRESH']['range'], 'RANGE_NONE')
        self.assertFalse(live['CONFIG.THRESH']['range_complete'])

    def test_an_achieved_clock_readback_points_at_the_parameter_that_sets_it(self):
        """Vivado publishes a PS clock twice and both look writable.

        ced-gen_M7 set PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ to 100: enabled, float,
        same 0.1-250 range as the request, and value_src USER where the request
        read DEFAULT, so it looked like the live one. The write was ignored, the
        PL clock stayed at 50, and the run reported success. H3 lost the same way
        on the MPSoC spelling.
        """
        keys = {'CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ', 'CONFIG.PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ',
                'CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ', 'CONFIG.PSU__CRL_APB__PL0_REF_CTRL__ACT_FREQMHZ',
                'CONFIG.C_ACT_WIDTH'}
        self.assertEqual(achieved_source('CONFIG.PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ', keys),
                         'CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ')
        self.assertEqual(achieved_source('CONFIG.PSU__CRL_APB__PL0_REF_CTRL__ACT_FREQMHZ', keys),
                         'CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ')
        self.assertIsNone(achieved_source('CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ', keys),
                          'the parameter that sets the clock stays writable')
        self.assertIsNone(achieved_source('CONFIG.C_ACT_WIDTH', keys),
                          'no requested twin, so this is just a parameter spelled with ACT')



class CountedFamilyAndExactNameTests(unittest.TestCase):
    """ip-configurator-test-kit_22 with claude-opus-5-5 (Vivado 2026.1, 2026-11-15): the requirement
    came as "number of enabled ISP tiles = 3", the per-ISP enables outranked the per-tile ones,
    and even a query naming C_TILE1_ENABLE did not return it. Names below are the IP's own, used
    as test data; the rules read only name shape and the request's words."""

    ROWS = [dict(key='CONFIG.' + name, label=name.replace('_', ' ').title(), help='')
            for name in ['C_TILE0_ENABLE', 'C_TILE1_ENABLE', 'C_TILE2_ENABLE']
            + ['C_TILE%d_ISP%d_ENABLE_MP' % (t, i) for t in range(3) for i in range(2)]
            + ['C_TILE%d_ISP%d_LIVE_INPUTS' % (t, i) for t in range(3) for i in range(2)]
            + ['C_TILE%d_ISP%d_BASIC_DATA_FORMAT' % (t, i) for t in range(3) for i in range(2)]]
    TASK = ['number of enabled ISP tiles = 3', "4 live inputs on tile 2's ISP0"]

    def keys(self, query, context=()):
        return [r['key'][7:] for r in rank(self.ROWS, query, context=context)]

    def test_counted_tiles_bring_the_tile_enables_however_the_count_is_phrased(self):
        for query in ['number of enabled ISP tiles = 3', 'enable 3 ISP tiles', 'enable 3 tiles']:
            found = self.keys(query, self.TASK)
            for name in ['C_TILE0_ENABLE', 'C_TILE1_ENABLE', 'C_TILE2_ENABLE']:
                self.assertIn(name, found, query)

    def test_a_family_with_a_word_the_request_never_says_is_not_added(self):
        found = self.keys('number of enabled ISP tiles = 3', self.TASK)
        self.assertNotIn('C_TILE2_ISP1_LIVE_INPUTS', found)
        self.assertNotIn('C_TILE2_ISP1_BASIC_DATA_FORMAT', found)

    def test_a_named_parameter_comes_first(self):
        found = self.keys('C_TILE1_ENABLE C_TILE1_ISP0_IO_TYPE NUM_TILES')
        self.assertEqual(found[0], 'C_TILE1_ENABLE')

    def test_a_lower_case_word_before_the_noun_counts_nothing(self):
        # "4 live inputs" is not a count of a family; ranking is unchanged for it.
        query = "4 live inputs on tile 2's ISP0"
        self.assertEqual(self.keys(query, self.TASK)[0], 'C_TILE2_ISP0_LIVE_INPUTS')
        self.assertNotIn('C_TILE0_ENABLE', self.keys(query))


if __name__ == '__main__':
    unittest.main()
