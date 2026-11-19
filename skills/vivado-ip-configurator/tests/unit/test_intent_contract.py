import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from intent_contract import missing_clauses, uncovered_values, facts, atomic_requirements
from nested_config import related_fields, rank_fields


class IntentContractTests(unittest.TestCase):
    def test_numbered_cell_heading_is_not_a_requirement(self):
        self.assertEqual(missing_clauses('2) axi_uartlite_0: baudrate 19200',
                                         ['baudrate 19200']), [])

    def test_bit_width_clause_is_covered_by_width_requirement(self):
        task = 'Configure an AXI GPIO as a 2-bit, all-outputs port.'
        self.assertEqual(missing_clauses(task, ['AXI GPIO width 2', 'all outputs'],
                                         'AXI GPIO'), [])

    def test_single_bit_is_an_exact_width_and_does_not_duplicate_task_clause(self):
        self.assertEqual([(f['value'], f['unit']) for f in facts('single-bit GPIO output')],
                         [('1', 'bit')])
        task = 'Single-bit AXI GPIO to drive the camera sensor-hub reset line.'
        self.assertEqual(missing_clauses(task, ['AXI4Lite interface', 'single bit GPIO output'],
                                         'AXI GPIO'), [])

    def test_negative_multiplicity_is_preserved_separately_from_width(self):
        task='We only need one 32-bit timer, not two.'
        self.assertEqual(missing_clauses(task,['timer width 32-bit'],'AXI timer'),['not two timers'])

    def test_enable_with_measured_setting_becomes_two_obligations(self):
        self.assertEqual(atomic_requirements(['enable instruction cache with 16KB']),
                         ['enable instruction cache', 'instruction cache size 16KB'])
        self.assertEqual(atomic_requirements(['enable instruction and data cache with 16KB each']),
                         ['enable instruction cache', 'instruction cache size 16KB',
                          'enable data cache', 'data cache size 16KB'])

    def test_shared_negative_slash_becomes_independent_obligations(self):
        self.assertEqual(atomic_requirements(['no AXI slave/master ports']),
                         ['no AXI slave ports', 'no AXI master ports'])

    def test_gpio_direction_verbs_restore_omitted_obligations(self):
        task = ('Insert an AXI GPIO. Channel 1 reads an 8-bit DIP switch bank '
                'and channel 2 drives 4 LEDs.')
        missing = missing_clauses(task, ['channel 1 width: 8', 'channel 2 width: 4'],
                                  'AXI GPIO')
        self.assertEqual(missing,
                         ['Channel 1 input direction',
                          'channel 2 output direction'])
    def test_catalog_identifier_is_not_a_missing_setting(self):
        task = 'AXI4-Stream Data FIFO with a depth of 128 for the audio sample path.'
        self.assertEqual(missing_clauses(task, ['depth of 128'], 'AXI4-Stream Data FIFO'), [])

    def test_non_catalog_identifier_remains_a_missing_setting(self):
        task = 'AXI4-Stream Data FIFO in RAW12 mode with a depth of 128.'
        additions = missing_clauses(task, ['depth of 128'], 'AXI4-Stream Data FIFO')
        self.assertEqual(len(additions), 1)
        self.assertIn('RAW12', additions[0])
        self.assertNotIn('AXI4', additions[0])

    def test_split_reset_requirements_do_not_duplicate_introductory_clause(self):
        task='Processor System Reset block for the 100 MHz control domain (default configuration, active-low aux reset).'
        self.assertEqual(missing_clauses(task,['100 MHz control domain','active-low aux reset']),[])

    def test_product_purpose_clause_does_not_duplicate_realized_reset_output_behavior(self):
        task='Reset-control AXI GPIO: all outputs, 32-bit, power-on default value 0xFFFFFFFF so every reset line is deasserted high.'
        req=['all outputs','32-bit','power-on default value 0xFFFFFFFF']
        self.assertEqual(missing_clauses(task,req),[])

    def test_external_signal_identity_is_not_an_ip_enum_requirement(self):
        task='Configure an AXI GPIO as a 2-bit, all-outputs port to drive the auto-IOB MSI-X enable lines.'
        self.assertEqual(missing_clauses(task,['GPIO width 2','all-outputs'],'AXI GPIO'),[])

    def test_unrepresented_drive_behavior_is_retained(self):
        task='Single-bit AXI GPIO to drive the camera sensor-hub reset line.'
        extra=missing_clauses(task,['single-bit GPIO','AXI interface'])
        self.assertEqual(extra,[task.rstrip('.')])

    def test_omitted_mode_is_retained_from_original_task(self):
        extra = missing_clauses('Receiver bridged via C-PHY: 3 lanes, RAW12', ['3 lanes', 'RAW12'])
        self.assertEqual(extra, ['Receiver bridged via C-PHY'])

    def test_enable_bit_cannot_cover_size_or_pin_assignment(self):
        selected = [(dict(key='CONFIG.OPTIONS', subkey='WINDOW2_EN'), dict(value='1'))]
        self.assertEqual([f['text'] for f in uncovered_values('WINDOW2 enabled at 256 MB', selected)], ['256 MB'])
        selected = [(dict(key='CONFIG.PINS', subkey='PIN3'), dict(value='GPIO'))]
        self.assertEqual([f['text'] for f in uncovered_values('reset on PIN_3', selected)], ['PIN_3'])

    def test_source_enum_labels_cover_encoded_values(self):
        row = dict(key='CONFIG.MODE', choices=[dict(value='2', label='C-PHY')])
        self.assertEqual(uncovered_values('C-PHY', [(row, dict(value='2'))]), [])
        row = dict(key='CONFIG.SIZE', label='Window size')
        self.assertTrue(uncovered_values('256 MB', [(row, dict(value='256'))]))
        self.assertEqual(uncovered_values('256 MB', [(row, dict(value='256')),
                         (dict(key='CONFIG.SCALE'), dict(value='MegaBytes'))]), [])

    def test_source_enum_suffix_covers_only_all_requested_tokens(self):
        row = dict(key='CONFIG.MODE', choices=[
            dict(value='LPDDR5_SDRAM', label='LPDDR5_SDRAM'),
            dict(value='PCIe0_x2', label='PCIe0_x2')])
        self.assertEqual(uncovered_values('LPDDR5 controller type',
                         [(row, dict(value='LPDDR5_SDRAM'))]), [])
        self.assertTrue(uncovered_values('MODE_X2_10GBE',
                        [(row, dict(value='PCIe0_x2'))]))

    def test_every_numeric_part_of_compound_requirement_needs_a_mapping(self):
        picked = [(dict(key='CONFIG.LANES'), dict(value='3'))]
        self.assertEqual([f['text'] for f in uncovered_values('3 lanes and 4 pixels per clock', picked)], ['4 pixels'])
        picked.append((dict(key='CONFIG.PIXELS'), dict(value='4')))
        self.assertEqual(uncovered_values('3 lanes and 4 pixels per clock', picked), [])

    def test_related_fields_keep_exact_index_and_parent(self):
        rows = [dict(key='CONFIG.OPTIONS', subkey='WINDOW2_' + s) for s in ['EN', 'SIZE', 'SCALE', 'TYPE']]
        rows += [dict(key='CONFIG.OTHER', subkey='WINDOW2_SIZE'), dict(key='CONFIG.OPTIONS', subkey='WINDOW3_SIZE')]
        extra = related_fields(rows, rows[:1], 'WINDOW2 enabled at 256 MB')
        self.assertEqual({r['subkey'] for r in extra}, {'WINDOW2_SIZE', 'WINDOW2_SCALE', 'WINDOW2_TYPE'})
        self.assertTrue(all(r['key'] == 'CONFIG.OPTIONS' for r in extra))

    def test_assignment_subject_outranks_pin_configuration(self):
        rows = [dict(key='CONFIG.OPTIONS', subkey='LINK_RESET'), dict(key='CONFIG.PINS', subkey='PIN3')]
        self.assertEqual(rank_fields(rows, 'RESET on PIN3')[0]['subkey'], 'LINK_RESET')

    def test_unrequested_clock_sibling_does_not_outrank_peripheral(self):
        rows = [
            dict(key='CONFIG.OPTIONS', subkey='BUS1_CLK', label='bus1 clock IO', help='clock enable and MIO numbers'),
            dict(key='CONFIG.OPTIONS', subkey='BUS1_PERIPHERAL', label='bus1 peripheral IO', help='MIO numbers')]
        self.assertEqual(rank_fields(rows, 'enable BUS 1 on MIO 44 45')[0]['subkey'], 'BUS1_PERIPHERAL')
        self.assertEqual(rank_fields(rows, 'enable BUS 1 clock on MIO 44 45')[0]['subkey'], 'BUS1_CLK')

    def test_interface_qualifier_is_not_a_second_mode_request(self):
        row = dict(key='CONFIG.RATE', label='A-PHY line rate')
        self.assertEqual(uncovered_values('A-PHY line rate 1500 Mbps', [(row, dict(value='1500'))]), [])

    def test_semantic_encoding_requires_explicit_grounding(self):
        row = dict(key='CONFIG.SPEED')
        self.assertTrue(uncovered_values('Gen4', [(row, dict(value='16_GT/s'))]))
        self.assertEqual(uncovered_values('Gen4', [(row, dict(value='16_GT/s', covers=['Gen4'],
                         grounding='Retrieved protocol specification establishes this generation encoding.'))]), [])

    def test_sign_and_index_are_preserved(self):
        row=dict(key='CONFIG.PHASE',label='Phase')
        self.assertTrue(uncovered_values('phase -90',[(row,dict(value='90'))]))
        self.assertEqual(uncovered_values('phase -90',[(row,dict(value='-90'))]),[])
        row=dict(key='CONFIG.CLOCK',label='Clock frequency')
        self.assertEqual(uncovered_values('clock 0 at 250 MHz',[(row,dict(value='250'))]),[])

    def test_compound_link_does_not_hide_explicit_port_type_assignment(self):
        task='PCIe Gen4 x2 endpoint on MDB5 GT: port type endpoint, PERST on PS_MIO_3'
        req=['PCIe Gen4 x2 endpoint on MDB5 GT','PERST on PS_MIO_3']
        self.assertIn('port type endpoint',missing_clauses(task,req))

    def test_boolean_source_identifier_proves_active_polarity(self):
        high=dict(key='CONFIG.C_AUX_RESET_HIGH',label='Aux Reset High')
        low=dict(key='CONFIG.RESET_ACTIVE_LOW',label='Reset Active Low')
        self.assertEqual(uncovered_values('active-low aux reset',[(high,dict(value=0))]),[])
        self.assertTrue(uncovered_values('active-low aux reset',[(high,dict(value=1))]))
        self.assertEqual(uncovered_values('active-high reset',[(low,dict(value=0))]),[])
        self.assertTrue(uncovered_values('active-high reset',[(low,dict(value=1))]))
        unrelated=dict(key='CONFIG.RESET_POLARITY',label='Reset polarity')
        self.assertTrue(uncovered_values('active-low reset',[(unrelated,dict(value=0))]))

    def test_index_enumeration_does_not_create_detached_value_requirements(self):
        task='Enable PL clocks 0, 1 and 2 at 100 MHz, 150 MHz and 134 MHz respectively, leave PL clock 3 disabled'
        req=['enable PL clock 0 at 100 MHz','enable PL clock 1 at 150 MHz',
             'enable PL clock 2 at 134 MHz','disable PL clock 3']
        self.assertEqual(missing_clauses(task,req),[])

    def test_protocol_controller_indices_and_dictionary_pin_ranges_are_not_values(self):
        row=dict(key='CONFIG.SETTINGS',subkey='PS_BUS1_PERIPHERAL',shape='dictionary')
        text='enable CAN-FD 1 on PMC_MIO_44:45'
        self.assertEqual(uncovered_values(text,[(row,dict(value='ENABLE 1 IO PMC_MIO_44:45'))]),[])
        self.assertEqual(missing_clauses(text,[text]),[])

    def test_boolean_feature_name_is_not_a_literal_value_requirement(self):
        row=dict(key='CONFIG.INCLUDE_AB_FEC',choices=[dict(value='0',label='false'),dict(value='1',label='true')])
        self.assertEqual(uncovered_values('include AB-FEC block',[(row,dict(value='1'))]),[])
        self.assertTrue(uncovered_values('include AB-FEC block',[(row,dict(value='0'))]))

    def test_intro_retains_only_missing_mode_when_lane_count_is_already_present(self):
        task='Camera receiver subsystem operating over D-PHY for a 4-lane camera: 4 active lanes'
        self.assertEqual(missing_clauses(task,['4 active lanes']),['D-PHY'])

    def test_parenthetical_exact_clock_refines_approximate_clock(self):
        task='clocked from the ~100 MHz AXI clock (99.999 MHz)'
        self.assertEqual([f['text'] for f in facts(task)],['99.999 MHz'])
        self.assertEqual(missing_clauses(task,['AXI clock frequency 99.999 MHz']),[])
        self.assertEqual(len(facts('100 MHz clock and 99.999 MHz clock')),2)
        self.assertEqual(len(facts('~100 MHz clock and 99.999 MHz clock')),2)

    def test_declared_source_units_cover_only_correct_converted_frequency(self):
        row=dict(key='CONFIG.CLOCK',label='Clock frequency',units='khz')
        self.assertEqual(uncovered_values('1.25 MHz',[(row,dict(value=1250))]),[])
        self.assertTrue(uncovered_values('1.25 MHz',[(row,dict(value=1250000))]))

    def test_assignment_names_are_not_requested_enum_values(self):
        self.assertEqual([f['text'] for f in facts('NUM_PORTS=4 and FORMAT=RAW12')],['4','RAW12'])
        self.assertIn('Gen4',[f['text'] for f in facts('Gen4=16 GT/s')])
        row=dict(key='CONFIG.OPTIONS',subkey='MEM_NUM_PORTS')
        for text in ['4 ports (NUM_PORTS)','NUM_PORTS 4','NUM_PORTS: 4','NUM_PORTS=4']:
            self.assertEqual(uncovered_values(text,[(row,dict(value=4))]),[])
        row=dict(key='CONFIG.PINS',subkey='PS_MIO_3')
        self.assertTrue(uncovered_values('reset on (PS_MIO_3)',[(row,dict(value='GPIO'))]))

    def test_parenthesized_source_name_without_index_is_not_a_second_value(self):
        self.assertEqual(facts('no AXI clocks (NUM_CLKS)'),[])
        self.assertEqual(missing_clauses('no AXI clocks (NUM_CLKS)',
                                        ['no AXI clocks']),[])
        self.assertTrue(facts('reset on (PS_MIO_3)'))

    def test_source_style_bit_width_requirement_covers_equivalent_prose(self):
        task='controller type LPDDR5, 32-bit per-MC data width'
        requirements=['controller_type=LPDDR5','data_width_per_mc=32']
        self.assertEqual(missing_clauses(task,requirements),[])
        # Storage units still require explicit scale evidence.
        self.assertTrue(missing_clauses('window size 256 MB',['WINDOW_SIZE=256']))
