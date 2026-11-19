import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from value_semantics import annotate,requested_labels
from intent_contract import uncovered_values


class ValueSemanticsTests(unittest.TestCase):
    def test_source_identifier_label_allows_only_separator_changes(self):
        row=dict(key='CONFIG.MODE',choices=[dict(value='a',label='APHY'),dict(value='b',label='BPHY')])
        self.assertEqual([c['value'] for c in requested_labels(row,'enable A-PHY')],['a'])
        self.assertEqual(requested_labels(row,'enable SOMEBPHYEXTRA'),[])

    def test_documented_generation_matches_only_its_published_rate(self):
        row=annotate(dict(key='CONFIG.LINK_CONFIG',subkey='PCIE1_LINK_SPEED',
             choices=[dict(value='8.0_GT/s',label=''),dict(value='16.0_GT/s',label=''),dict(value='32.0_GT/s',label='')]))
        self.assertEqual(row['choices'][0]['semantic_labels'],['Gen3'])
        self.assertEqual(row['choices'][1]['semantic_labels'],['Gen4'])
        self.assertNotIn('semantic_labels',row['choices'][2])
        self.assertEqual(uncovered_values('PCIe Gen4',[(row,dict(value='16.0_GT/s'))]),[])
        self.assertTrue(uncovered_values('PCIe Gen4',[(row,dict(value='32.0_GT/s'))]))

    def test_meaning_does_not_create_values_ranges_or_other_protocol_bindings(self):
        original=dict(key='CONFIG.CLOCK_SPEED',range='RANGE_NUM:1:40',choices=[dict(value='16.0_GT/s',label='source')])
        self.assertEqual(annotate(original),original)
        original=dict(key='CONFIG.PCIE_LINK_SPEED',choices=[dict(value='5.0_GT/s',label='source')])
        result=annotate(original)
        self.assertEqual(result['choices'][0]['value'],'5.0_GT/s')
        self.assertEqual(result['choices'][0]['label'],'source')
        self.assertNotIn('range',result)
        self.assertNotIn('semantic_labels',original['choices'][0])

    def test_boolean_bar_width_does_not_require_model_recall(self):
        row=annotate(dict(key='CONFIG.SETTINGS',subkey='PCIE1_PF0_BAR4_64BIT',
             choices=[dict(value='0',label='false'),dict(value='1',label='true')]))
        self.assertEqual(row['choices'][0]['semantic_labels'],['32-bit'])
        self.assertEqual(uncovered_values('32-bit memory',[(row,dict(value='0'))]),[])
        self.assertTrue(uncovered_values('32-bit memory',[(row,dict(value='1'))]))
        other=annotate(dict(key='CONFIG.BUS_64BIT',choices=[dict(value='0',label='false')]))
        self.assertNotIn('semantic_labels',other['choices'][0])
