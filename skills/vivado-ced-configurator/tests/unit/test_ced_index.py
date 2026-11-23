# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Static store parsing and ranking. Run with TMPDIR and Python caches in scratch."""
from pathlib import Path
import sys
import unittest

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / 'scripts'))
from ced_index import Index, parse_options, parse_updaters, support_text, tcl_words  # noqa: E402

STORE = SKILL / 'tests/fixtures/store'
STORE2 = SKILL / 'tests/fixtures/store2'


class TclWordsTests(unittest.TestCase):
    def test_braces_quotes_brackets_and_continuations(self):
        words, _ = tcl_words('name "A b" value_list {x {y z}} \\\n value [lindex $q 0] bare')
        self.assertEqual(words, ['name', 'A b', 'value_list', 'x {y z}', 'value', '[lindex $q 0]', 'bare'])

    def test_limit_stops_after_requested_words(self):
        words, end = tcl_words('{a} {b} {c}', 0, 2)
        self.assertEqual(words, ['a', 'b'])
        self.assertEqual(tcl_words('{a} {b} {c}', end)[0], ['c'])


class OptionParsingTests(unittest.TestCase):
    def setUp(self):
        self.alpha = (STORE / 'ced/Xilinx/IPI/Alpha_Presets/init.tcl').read_text()
        self.beta = (STORE / 'ced/Xilinx/IPI/Beta_PCIe/init.tcl').read_text()

    def test_board_conditional_variants_merge_choices_and_ignore_comments(self):
        options = {o['name']: o for o in parse_options(self.alpha)}
        self.assertNotIn('Old', options)
        preset = options['Preset']
        self.assertEqual(preset['variants'], 2)
        self.assertEqual([c['value'] for c in preset['choices']], ['Microcontroller', 'Application_Processor'])
        self.assertEqual(preset['choices'][1]['label'], 'Application Embedded linux capable')

    def test_bool_options_get_true_false_and_dynamic_lists_are_flagged(self):
        options = {o['name']: o for o in parse_options(self.alpha)}
        self.assertEqual([c['value'] for c in options['Include_DDR']['choices']], ['true', 'false'])
        self.assertTrue(options['Quad']['dynamic'])
        self.assertEqual(options['Quad']['choices'], [])

    def test_multiline_dicts_with_continuations(self):
        options = {o['name']: o for o in parse_options(self.beta)}
        self.assertEqual(options['CPM_Config']['default'], 'CPM4')
        self.assertEqual([c['value'] for c in options['CPM5_Preset']['choices']], ['Gen4x8_MM', 'Gen5x8_MM_Performance'])
        self.assertEqual(options['LANE_RATE']['choices'][0]['value'], '16.0_GT/s')

    def test_updater_headers_map_to_vivado_proc_names_per_kind(self):
        rows = parse_updaters(self.alpha) + parse_updaters(self.beta)
        self.assertEqual([r['proc'] for r in rows], ['update_0', 'update_gui_for_0', 'update_1', 'update_gui_for_0', 'validate_0'])
        self.assertEqual(rows[0]['inputs'], ['PROJECT_PARAM.BOARD_PART', 'Preset.VALUE'])
        self.assertEqual(rows[3]['outputs'], ['CPM_Config.VISIBLE', 'CPM_Config.ENABLEMENT', 'CPM_Config.VALUE'])

    def test_support_text_keeps_filters_and_drops_empty_bodies(self):
        text = support_text(self.alpha)
        self.assertIn('getSupportedBoards', text)
        self.assertNotIn('getSupportedParts', text)


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.index = Index(STORE, '2026.1')

    def test_designs_come_from_design_xml_and_stale_catalog_items_are_reported(self):
        names = {e['name'] for e in self.index.entries}
        self.assertEqual(names, {'alpha_preset', 'beta_pcie', 'gamma_rtl'})
        self.assertEqual(self.index.catalog_only, ['beta_stale'])
        beta = self.index.by_vlnv('xilinx.com:design:beta_pcie:2.0')
        self.assertEqual(beta['revision'], '2.0')
        self.assertEqual(self.index.by_name('Gamma Waveform Generator')['kind'], 'nonIPI')

    def test_ranking_follows_the_request_wording(self):
        self.assertEqual(self.index.search('PCIe QDMA endpoint performance variant')[0][1]['name'], 'beta_pcie')
        self.assertEqual(self.index.search('processor that can run embedded linux')[0][1]['name'], 'alpha_preset')
        self.assertEqual(self.index.search('waveform generator RTL')[0][1]['name'], 'gamma_rtl')
        self.assertEqual(self.index.search('zzz unrelated words'), [])

    def test_the_target_adds_no_retrieval_evidence_of_its_own(self):
        """A package and speed grade are nearly unique, so they score highest.

        ced-gen_H2 asked for the CPM6 bus-mastering demo on an xc2vp3602 part.
        cpm6_dma's supports text matched vsvc3340 and 3hp, worth more than
        cpm6_bmd's own "controllers" and "bus", and the run reported the DMA
        design. A design listing the part says nothing about what it does;
        cedcfg::support answers compatibility separately and live.
        """
        part = 'xcvp1202-vsva2785-2MP-e-S'
        self.assertEqual([e['name'] for _, e in self.index.search('waveform generator RTL on ' + part)],
                         ['gamma_rtl', 'beta_pcie'], 'beta is reachable only through its supported part')
        self.assertEqual(self.index.search('waveform generator RTL on ' + part, exclude=[part]),
                         self.index.search('waveform generator RTL'))


class ReleaseRolloverTests(unittest.TestCase):
    """The 2026.2 store shape: duplicate folders, and a catalog name that is not the design name.

    The 2026.1 fixture has neither, so every assertion here passed vacuously
    before the catalog was keyed on config.root.
    """

    def setUp(self):
        self.index = Index(STORE2, '2026.2')

    def test_each_folder_keeps_its_own_revision_and_commit(self):
        """Keyed by name, all three Delta folders inherited whichever parsed last.

        The live 2026.2 store does this with edf_base and edf_base_pl: three
        folders apiece, every one of them reporting revision 3.0.
        """
        found = {e['root']: (e['revision'], e['commit_id'][:1])
                 for e in self.index.entries + self.index.variants if e['name'] == 'delta_base'}
        self.assertEqual(found, {'ced/Xilinx/IPI/Delta_Base/1.0': ('1.0', '4'),
                                 'ced/Xilinx/IPI/Delta_Base/2.0': ('2.0', '5'),
                                 'ced/Xilinx/IPI/Delta_Base/3.0': ('3.0', '6')})

    def test_a_catalog_item_naming_another_design_still_joins_by_root(self):
        """base_ext_platform's catalog item is named edf_base, so the name join missed it."""
        epsilon = self.index.by_vlnv('xilinx.com:design:epsilon_ext:1.0')
        self.assertEqual(epsilon['revision'], '1.0')
        self.assertTrue(epsilon['commit_id'].startswith('7'), 'took the item sharing its root')

    def test_duplicate_folders_collapse_to_the_newest_and_stay_reachable_by_vlnv(self):
        surfaced = [e for e in self.index.entries if e['name'] == 'delta_base']
        self.assertEqual([e['root'] for e in surfaced], ['ced/Xilinx/IPI/Delta_Base/3.0'])
        self.assertEqual(sorted(e['root'] for e in self.index.variants),
                         ['ced/Xilinx/IPI/Delta_Base/1.0', 'ced/Xilinx/IPI/Delta_Base/2.0'])
        self.assertEqual(self.index.by_vlnv('xilinx.com:design:delta_base:1.0')['revision'], '1.0')

    def test_one_candidate_per_display_name_so_choose_ced_is_unambiguous(self):
        displays = [e['display'] for e in self.index.entries]
        self.assertEqual(len(displays), len(set(displays)), 'ranked rows must be distinguishable')

    def test_a_shadowed_item_and_a_dropped_design_are_both_reported(self):
        """beta_stale shares a root with beta_pcie; alpha_gone's folder is absent."""
        self.assertEqual(self.index.catalog_only, ['alpha_gone', 'beta_stale'])
        self.assertEqual(self.index.by_vlnv('xilinx.com:design:beta_pcie:2.0')['revision'], '2.0')


if __name__ == '__main__':
    unittest.main()
