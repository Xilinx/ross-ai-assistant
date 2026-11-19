import os
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from nested_config import terms,rank_fields,rank_fields_covering,enrich,load_schema
from structured_config import atom,tdict
import subprocess


class NestedTests(unittest.TestCase):
    def test_compound_link_requirement_keeps_each_semantic_role(self):
        names = ['MMI_PCIE0_PORT_TYPE', 'MMI_PCIE1_PORT_TYPE',
                 'PCIE0_LINK_WIDTH', 'PCIE0_OVERRIDE_MAX_LINK_WIDTH',
                 'PCIE0_LINK_SPEED', 'PCIE1_LINK_SPEED']
        rows = [dict(key='CONFIG.MMI_CONFIG', subkey=name,
                     label=name.replace('_', ' ')) for name in names]
        found = [row['subkey'] for row in rank_fields_covering(
            rows, 'PCIe Gen4 x2 endpoint port type', 6)]
        self.assertIn('MMI_PCIE0_PORT_TYPE', found)
        self.assertIn('PCIE0_LINK_WIDTH', found)
        self.assertIn('PCIE0_LINK_SPEED', found)

    def test_generation_number_does_not_promote_an_unrelated_index(self):
        rows=[dict(key='CONFIG.M',subkey=n,label=n.replace('_',' ')) for n in
              ['MMI_LINK0_PORT_TYPE','LINK0_PF0_BAR4_TYPE','LINK0_PF1_BAR4_TYPE']]
        found=rank_fields(rows,'link Gen4 x2 endpoint port type',1)
        self.assertEqual(found[0]['subkey'],'MMI_LINK0_PORT_TYPE')

    def test_exact_interface_name_survives_broad_help_ranking(self):
        rows=[dict(key='CONFIG.SETTINGS',subkey='USE_M_AXI_FPD',label='USE M AXI FPD')]
        rows += [dict(key='CONFIG.SETTINGS',subkey='USE_FPD_AXI_NOC'+str(i),
                      label='AXI master interface enable FPD') for i in range(12)]
        self.assertEqual(rank_fields(rows,'M_AXI_FPD master interface enable',4)[0]['subkey'],'USE_M_AXI_FPD')

    def test_channel_index_and_requested_frequency_survive_ranking(self):
        rows=[dict(key='CONFIG.SETTINGS',subkey=n,label=n) for n in [
            'PL0_REF_FREQMHZ','PL1_REF_FREQMHZ','PL0_REF_ACT_FREQMHZ','USE_PL_CLK0','USE_PL_CLK1']]
        found=rank_fields(rows,'PL0 clock frequency',4)
        self.assertEqual(found[0]['subkey'],'PL0_REF_FREQMHZ')
        self.assertIn('USE_PL_CLK0',[r['subkey'] for r in found])
        self.assertNotIn('PL1_REF_FREQMHZ',[r['subkey'] for r in found])

    def test_index_belongs_to_its_word(self):
        rows=[dict(key='CONFIG.S',subkey=n,label=n.replace('_',' ')) for n in [
            'LINK0_MODE','GEN_IPI4_ENABLE','BUS0_PERIPHERAL','BUS1_PERIPHERAL','BUS2_PERIPHERAL','PIN1']]
        self.assertEqual(rank_fields(rows,'link gen4 mode',1)[0]['subkey'],'LINK0_MODE')
        self.assertEqual(rank_fields(rows,'enable BUS-X 1 peripheral',1)[0]['subkey'],'BUS1_PERIPHERAL')
        found=[r['subkey'] for r in rank_fields(rows,'BUS-X 0 and BUS-X 2 peripheral',2)]
        self.assertEqual(found,['BUS0_PERIPHERAL','BUS2_PERIPHERAL'])

    def test_two_query_words_match_a_glued_field_token(self):
        rows=[dict(key='CONFIG.M',subkey=n,label=n.replace('_',' ')) for n in [
            'MC5_F0_XY5_BANK_ARCH','MC5_F0_XY5_TRP','MC5_KINDTYPE','MC5_KIND_MODE_X']]
        self.assertEqual(rank_fields(rows,'XYMEM5 kind type',1)[0]['subkey'],'MC5_KINDTYPE')

    def test_unit_words_reach_frequency_fields_and_monitors_rank_below_settings(self):
        rows=[dict(key='CONFIG.SETTINGS',subkey=n,label=n.replace('_',' ')) for n in [
            'MON_X0_CLK_FREQHZ','MON_X0_CLK_THRESHOLD_H','EN_X0_CLK_MON','USE_X_CLK0','X0_REF_FREQMHZ']]
        found=[r['subkey'] for r in rank_fields(rows,'enable X clock 0 at 100 MHz',2)]
        self.assertEqual(set(found),{'USE_X_CLK0','X0_REF_FREQMHZ'})
        self.assertEqual(rank_fields(rows,'monitor X0 clock',1)[0]['subkey'][:3] in ('MON','EN_'),True)

    def test_help_remains_separate_from_parent_binding(self):
        row=dict(key='CONFIG.SETTINGS',subkey='WIDTH',mapping_scope='live_dictionary',default='8')
        hint=dict(hint_scope='exact_name_in_other_definitions_not_parent_binding',source_vlnvs=['vendor:ip:source:1.0'],labels=['Bus width'],help='Bits',type='long',range='RANGE_NUM:1:32')
        found=enrich([row],{'WIDTH':hint})[0]
        self.assertEqual(found['key'],'CONFIG.SETTINGS')
        self.assertEqual(found['mapping_scope'],'live_dictionary')
        self.assertEqual(found['range_scope'],'standalone_definition_not_parent_live_range')
        self.assertEqual(found['default'],'8')

    def test_schema_rejects_other_run_and_renamed_native_field_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'evidence').mkdir();path=root/'evidence/schema.tcldict'
            fields='[dict create WIDTH '+tdict(dict(default='8',mapping_scope='native_same_name_candidate'))+']'
            parent='[dict create native_key '+atom('CONFIG.UNRELATED_INTERNAL')+' fields '+fields+']'
            command='puts [dict create schema_version 1 data [dict create parameters [dict create CONFIG.SETTINGS '+parent+']]]\n'
            path.write_text(subprocess.check_output(['tclsh'],input=command,text=True))
            with self.assertRaisesRegex(ValueError,'exact parent name'):load_schema(path,root)
            with self.assertRaisesRegex(ValueError,'current-run'):load_schema(path,root/'other')
