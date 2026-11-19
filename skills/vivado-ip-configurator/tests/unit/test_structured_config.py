import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from structured_config import StructuredSession, atom, dict_tcl, tcl_list_string, tdict, tlist


class Guard:
    database_config = 'declared'
    def record(self, data):
        return Path(os.environ['TMPDIR']) / 'structured-unit-evidence.json'
    def database_request(self, command):
        request = json.loads(command)
        if request['op'] == 'ips':
            return {'candidates': []}
        return {'candidates': [dict(key='CONFIG.A', label='Requested feature', type='long',
                                   default='0', range='RANGE_NUM:0:32', help='Published source purpose')]}


class Session(StructuredSession):
    async def close(self):
        return True

    async def native(self, expression, prefix=''):
        self.last_expression = expression
        if expression.startswith('[apply {{} {set p [current_project'):
            return tcl_list_string(['part', getattr(self, 'open_part', '')]) if getattr(self, 'open_part', '') else ''
        if expression.startswith('[apply {{name} {set rows [get_bd_cells'):
            return tcl_list_string(['vlnv', self.existing_vlnv]) if getattr(self, 'existing_vlnv', None) else ''
        if expression.startswith('[lite::catalog'):
            return 'CATALOG:EXACT vlnv vendor:lib:generic:1.0'
        if expression.startswith('[lite::candidate_availability'):
            vlnvs = re.findall(r'[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+', expression)
            rows = tcl_list_string([tcl_list_string(['vlnv', v, 'status', 'AVAILABLE']) for v in vlnvs])
            return tcl_list_string(['AVAILABILITY:OK', 'rows', rows])
        if expression.startswith('[lite::nested_schema'):
            return 'NESTED:OK file {} containers 0'
        if expression.startswith('[lite::gates'):
            return getattr(self, 'gating_receipt', 'GATES:UNAVAILABLE reason {IP publishes no port gating}')
        if expression.startswith('[lite::live_parameters'):
            values = getattr(self, 'live_values', {'CONFIG.A':'0', 'CONFIG.B':'0'})
            sources = getattr(self, 'live_sources', {})
            expression = '[dict create ' + ' '.join(atom(k)+' '+tdict(dict(value=v,value_src=sources.get(k,'DEFAULT'))) for k,v in values.items()) + ']'
            return subprocess.check_output(['tclsh'], input='puts '+expression+'\n', text=True).strip()
        if expression.startswith('[lite::describe') and hasattr(self, 'control_details'):
            return self.control_details
        if expression == '[lite::partial_save]':
            return 'SAVE:OK'
        if expression.startswith('[lite::verified_readback'):
            rows = []
            for key, actual in getattr(self, 'verified_fields', {}).items():
                row = tdict(dict(requirement_id='r1', text='requested feature',
                                 key=key, actual=actual, **{'pass': 1}))
                rows.append(subprocess.check_output(
                    ['tclsh'], input='puts ' + row + '\n', text=True).strip())
            rendered = tdict(dict(fields=tcl_list_string(rows)))
            return subprocess.check_output(['tclsh'], input='puts ' + rendered + '\n', text=True).strip()
        if expression.startswith('[get_property'):
            return getattr(self,'live_vector','10,20,30')
        if expression.startswith('[lite::vector'):
            lib=Path(__file__).resolve().parents[2]/'lib/lite.tcl'
            result=subprocess.run(['tclsh'],input='source '+atom(lib)+'\nputs '+expression+'\n',text=True,capture_output=True)
            if result.stderr:raise ValueError(result.stderr)
            return result.stdout.strip()
        if self.stage in ['new', 'choosing_ip']:
            # Data is produced by a Tcl interpreter, never a handwritten pseudo-list.
            return subprocess.check_output(['tclsh'], input='puts '+tdict(dict(vlnv='vendor:lib:generic:1.0',
                properties='CONFIG.A CONFIG.B', version='Vivado v2026.1\nSW Build 6511674'))+'\n', text=True).strip()
        if expression.startswith('[lite::configure'):
            script = 'namespace eval lite {}\nproc lite::configure {hint cell config reqs} {\n if {[dict exists $config CONFIG.A]} {puts [dict get $config CONFIG.A]}\n if {[dict exists $config CONFIG.B]} {puts [dict get $config CONFIG.B]}\n puts [dict get [lindex $reqs 0] text]\n return CFG:OK\n}\nproc lite::configure_subset {hint cell config full reqs} {lite::configure $hint $cell $config $reqs}\nputs '+expression+'\n'
            self.generated_result = subprocess.check_output(['tclsh'], input=script, text=True)
            self.configured = getattr(self, 'configured', []) + [self.generated_result]
            replies = getattr(self, 'configure_replies', [])
            return replies.pop(0) if replies else 'CFG:OK'
        if expression.startswith('[lite::verify_pinned'):
            return getattr(self, 'verify_reply', 'CFG:OK cell /cell_0 verified 1')
        return 'DONE:COVERAGE:FULL'


class StructuredTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        await self.s.request(dict(op='begin', ip='generic', cell='cell_0', part='part', requirements=['requested feature']))

    async def describe(self, attempt=None):
        if attempt is None:
            return await self.s.request(dict(op='describe', requirement_id='r1'))
        return await self.s.request(dict(op='describe', requirement_id='r1', query=self.s.requirements['r1'] + ' ' + 'again ' * attempt))

    async def select(self, cid='r1.p1', value=8, rid='r1'):
        return await self.s.request(dict(op='select', selections=[dict(requirement_id=rid, candidate_id=cid, value=value)]))


    async def test_channel_ordinal_width_repairs_nearby_boolean_selection(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        await s.request(dict(op='begin', ip='generic', cell='gpio_0', part='part',
            requirements=['channel 1 width 8', 'channel 2 width 4']))
        boolean = [dict(value='0', label='false'), dict(value='1', label='true')]
        def row(rid, key, label, choices=None, controls=None):
            return dict(requirement_id=rid, key=key, label=label, type='long',
                        default='0', current='0', help='', enabled=True,
                        choices=choices or [], enablement_controls=controls or [], evidence='native')
        s.candidates = {
            'r1.p1': row('r1', 'CONFIG.C_GPIO_WIDTH', 'GPIO Width'),
            'r1.p2': row('r1', 'CONFIG.C_IS_DUAL', 'Enable Dual Channel', boolean),
            'r1.p4': row('r1', 'CONFIG.C_GPIO2_WIDTH', 'GPIO Width'),
            'r2.p1': row('r2', 'CONFIG.C_GPIO_WIDTH', 'GPIO Width'),
            'r2.p2': row('r2', 'CONFIG.C_IS_DUAL', 'Enable Dual Channel', boolean),
            'r2.p4': row('r2', 'CONFIG.C_GPIO2_WIDTH', 'GPIO Width',
                         controls=['CONFIG.C_IS_DUAL']),
        }
        s.describe_counts = {'r1': 1, 'r2': 1}
        result = await s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id='r1.p2', value=1),
            dict(requirement_id='r2', candidate_id='r2.p2', value=1)]))
        self.assertEqual(s.selections[('r1', 'r1.p1')]['value'], 8)
        self.assertEqual(s.selections[('r2', 'r2.p4')]['value'], 4)
        self.assertNotIn(('r1', 'r1.p2'), s.selections)
        self.assertIn(('r2', 'r2.p2'), s.selections)
        repairs = {item['repair'] for item in result['selection_repairs']}
        self.assertIn('added_unique_ordinal_width', repairs)
        self.assertIn('dropped_unrelated_boolean_for_exact_width', repairs)

    async def test_database_route_is_automatic(self):
        result = await self.describe()
        self.assertEqual(result['backend'], 'database')
        self.assertEqual(result['candidates'][0]['key'], 'CONFIG.A')

    async def test_finish_preserves_borrowed_session(self):
        self.s.borrowed_session = True
        self.s.selections = {('r1', 'r1.p1'): dict(candidate_id='r1.p1', value=8)}
        self.s.candidates = {'r1.p1': dict(key='CONFIG.A', requirement_id='r1', evidence='source')}
        self.s.stage = 'configured'
        async def must_not_close():
            self.fail('borrowed session was closed')
        self.s.close = must_not_close
        result = await self.s.request({'op': 'finish'})
        self.assertTrue(result['session_preserved'])
        self.assertFalse(result['session_closed'])
        self.assertEqual(result['next_op'], 'begin')

    async def test_preinitialization_failure_preserves_prepared_project(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        s.open_part = 'prepared-part'
        async def must_not_close():
            self.fail('prepared session was closed before initialization')
        s.close = must_not_close
        result = await s.finish_partial('malformed begin repeated')
        self.assertTrue(result['session_preserved'])
        self.assertFalse(result['session_closed'])
        self.assertEqual(result['next_op'], 'begin')

    async def test_begin_uses_open_projects_authoritative_part(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        s.open_part = 'xc2ve3558-sfva1440-2MP-e-S'
        result = await s.request(dict(op='begin', ip='generic', cell='cell_0',
            task='change the existing cell', requirements=['requested feature']))
        self.assertEqual(s.part, 'xc2ve3558-sfva1440-2MP-e-S')
        self.assertEqual(result['stage'], 'discovering')

    async def test_begin_uses_existing_cells_authoritative_vlnv(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        s.open_part = 'part'
        s.existing_vlnv = 'xilinx.com:ip:axi_uartlite:2.0'
        await s.request(dict(op='begin', ip='nl_bd', cell='uart_cam_0',
            task='change uart_cam_0', requirements=['baud rate 115200'], part='guessed-part'))
        self.assertEqual(s.part, 'part')
        self.assertEqual(s.vlnv, 'vendor:lib:generic:1.0')
        self.assertEqual(s.last_expression.startswith('[lite::nested_schema'), True)

    async def test_multicell_task_does_not_import_other_cells_clauses(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        result = await s.request(dict(op='begin', ip='uart', cell='uart_0', part='part',
            task='1) uart_0: baud 19200. 2) bram_0: width 64.',
            requirements=['baud 19200']))
        self.assertEqual(s.requirements, {'r1': 'baud 19200'})
        self.assertEqual(result['multi_cell_scope']['cell'], 'uart_0')
        self.assertNotIn('added_task_requirements', result)

    async def test_batch_describe_returns_scoped_candidates_for_all_requirements(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        await s.request(dict(op='begin', ip='generic', cell='cell_0', part='part',
                             requirements=['first feature', 'second feature', 'third feature']))
        result = await s.request(dict(op='describe', queries=['r1', 'r2', 'r3']))
        self.assertTrue(result['batch_describe'])
        self.assertEqual([row['requirement_id'] for row in result['descriptions']],
                         ['r1', 'r2', 'r3'])
        for index, row in enumerate(result['descriptions'], 1):
            self.assertEqual(row['backend'], 'database')
            self.assertTrue(row['candidates'])
            self.assertTrue(all(candidate['id'].startswith(f'r{index}.')
                                for candidate in row['candidates']))
        self.assertEqual(s.describe_counts, {'r1': 1, 'r2': 1, 'r3': 1})
        self.assertEqual(result['next_op'], 'select')

    async def test_cell_name_is_verified_as_native_identity_not_component_parameter(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        await s.request(dict(op='begin', ip='generic', cell='named_cell_0', part='part',
            task='Insert a block named named_cell_0 with requested feature',
            requirements=['name: named_cell_0', 'requested feature']))
        self.assertEqual(s.pending(), [{'id': 'r2', 'text': 'requested feature'}])
        named = await s.request(dict(op='describe', requirement_id='r1'))
        self.assertEqual(named['satisfied_by_native']['key'], '__CELL_NAME__')
        described = await s.request(dict(op='describe', requirement_id='r2'))
        self.assertNotIn('CONFIG.Component_Name', {row['key'] for row in described['candidates']})
        await s.request(dict(op='select', selections=[
            dict(requirement_id='r2', candidate_id='r2.p1', value=8)]))
        applied = await s.request({'op': 'apply'})
        self.assertEqual(applied['stage'], 'configured')
        self.assertIn(atom('__CELL_NAME__'), s.last_expression)
        self.assertIn(atom('native_bd_cell_identity'), s.last_expression)

    async def test_batch_describe_maps_unique_semantic_strings_to_requirements(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        await s.request(dict(op='begin', ip='generic', cell='cell_0', part='part',
            requirements=['enable instruction cache with 16KB', 'enable data cache with 16KB']))
        result = await s.request(dict(op='describe', queries=['instruction cache size', 'data cache size']))
        self.assertEqual([row['requirement_id'] for row in result['descriptions']], ['r2', 'r4'])
        self.assertEqual(s.describe_counts, {'r2': 1, 'r4': 1})

    async def test_begin_splits_enable_and_measured_setting(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        await s.request(dict(op='begin', ip='generic', cell='cell_0', part='part',
            requirements=['enable instruction cache with 16KB']))
        self.assertEqual(s.requirements,
                         {'r1': 'enable instruction cache',
                          'r2': 'instruction cache size 16KB'})

    def test_direction_inflections_expand_to_source_vocabulary(self):
        self.assertIn('input direction all inputs',
                      self.s.semantic_query('channel reads switches', 'channel reads switches'))
        self.assertIn('output direction all outputs',
                      self.s.semantic_query('channel drives LEDs', 'channel drives LEDs'))

    async def test_exact_width_and_unique_direction_drop_boolean_siblings(self):
        self.s.requirements={'r1':'AXI GPIO width 2','r2':'all outputs'}
        boolean=[dict(value='0',label='false'),dict(value='1',label='true')]
        self.s.candidates={
            'r1.p1':dict(key='CONFIG.C_GPIO_WIDTH',label='GPIO Width',type='long',
                         choices=[dict(value='2',label='')],requirement_id='r1',evidence='source',enabled=True),
            'r1.p2':dict(key='CONFIG.C_IS_DUAL',label='Dual Channel',type='long',
                         choices=boolean,requirement_id='r1',evidence='source',enabled=True),
            'r1.p3':dict(key='CONFIG.C_ALL_OUTPUTS_2',label='All Outputs 2',type='long',
                         choices=boolean,requirement_id='r1',evidence='source',enabled=False),
            'r2.p1':dict(key='CONFIG.C_ALL_OUTPUTS',label='All Outputs',type='long',
                         choices=boolean,requirement_id='r2',evidence='source',enabled=True),
            'r2.p2':dict(key='CONFIG.C_ALL_OUTPUTS_2',label='All Outputs 2',type='long',
                         choices=boolean,requirement_id='r2',evidence='source',enabled=False),
            'r2.p3':dict(key='CONFIG.C_IS_DUAL',label='Dual Channel',type='long',
                         choices=boolean,requirement_id='r2',evidence='source',enabled=True)}
        result=await self.s.request(dict(op='select',selections=[
            dict(requirement_id='r1',candidate_id='r1.p1',value=2),
            dict(requirement_id='r1',candidate_id='r1.p2',value=1),
            dict(requirement_id='r1',candidate_id='r1.p3',value=1),
            dict(requirement_id='r2',candidate_id='r2.p3',value=1)]))
        self.assertEqual({row['key'] for row in result['selected']},
                         {'CONFIG.C_GPIO_WIDTH','CONFIG.C_ALL_OUTPUTS'})

    async def test_an_exact_width_keeps_a_boolean_the_requirement_names(self):
        """ced-gen_H7: "scatter-gather enabled with 26-bit buffer length" selected
        c_include_sg and c_sg_length_width together, and the exact-width rule
        dropped c_include_sg as an unrelated Boolean without a word to the model."""
        self.s.requirements={'r1':'scatter-gather enabled with 26-bit buffer length register'}
        boolean=[dict(value='0',label='false'),dict(value='1',label='true')]
        self.s.candidates={
            'r1.p1':dict(key='CONFIG.c_include_sg',label='Enable Scatter Gather Engine',type='long',
                         choices=boolean,requirement_id='r1',evidence='source',enabled=True),
            'r1.p2':dict(key='CONFIG.c_sg_length_width',label='Length Width',type='long',
                         choices=[dict(value='26',label='')],requirement_id='r1',evidence='source',enabled=True)}
        result=await self.s.request(dict(op='select',selections=[
            dict(requirement_id='r1',candidate_id='r1.p1',value=1),
            dict(requirement_id='r1',candidate_id='r1.p2',value=26)]))
        self.assertEqual({row['key'] for row in result['selected']},
                         {'CONFIG.c_include_sg','CONFIG.c_sg_length_width'})

    async def test_atomic_indexed_direction_replaces_topology_guess(self):
        self.s.requirements = {'r1': 'channel 1: read', 'r2': 'single-bit GPIO output'}
        boolean = [dict(value='0', label='false'), dict(value='1', label='true')]
        self.s.candidates = {
            'r1.p1': dict(key='CONFIG.C_ALL_INPUTS', label='All Inputs', type='long',
                          choices=boolean, requirement_id='r1', evidence='source', enabled=True),
            'r1.p2': dict(key='CONFIG.C_ALL_INPUTS_2', label='All Inputs', type='long',
                          choices=boolean, requirement_id='r1', evidence='source', enabled=False,
                          enablement_controls=['CONFIG.C_IS_DUAL']),
            'r1.p3': dict(key='CONFIG.C_IS_DUAL', label='Enable Dual Channel', type='long',
                          choices=boolean, requirement_id='r1', evidence='source', enabled=True),
            'r2.p1': dict(key='CONFIG.C_ALL_OUTPUTS_2', label='All Outputs', type='long',
                          choices=boolean, requirement_id='r2', evidence='source', enabled=False,
                          enablement_controls=['CONFIG.C_IS_DUAL']),
            'r2.p2': dict(key='CONFIG.C_IS_DUAL', label='Enable Dual Channel', type='long',
                          choices=boolean, requirement_id='r2', evidence='source', enabled=True)}
        result = await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id='r1.p3', value=1),
            dict(requirement_id='r2', candidate_id='r2.p2', value=1)]))
        selected = {(row['requirement_id'], row['key']) for row in result['selected']}
        self.assertIn(('r1', 'CONFIG.C_ALL_INPUTS'), selected)
        self.assertIn(('r2', 'CONFIG.C_ALL_OUTPUTS_2'), selected)
        self.assertNotIn(('r1', 'CONFIG.C_IS_DUAL'), selected)
        self.assertIn(('r2', 'CONFIG.C_IS_DUAL'), selected)

    async def test_batch_describe_validates_whole_request_before_discovery(self):
        # T6 (F3): a duplicated requirement ID is dropped and reported, not rejected.
        with self.assertRaisesRegex(ValueError, 'Use a returned requirement ID'):
            await self.s.request(dict(op='describe', queries=['r1', 'r1', 'r404']))
        self.assertEqual(self.s.describe_counts, {})
        with self.assertRaisesRegex(ValueError, 'queries must contain'):
            await self.s.request(dict(op='describe', queries=[]))
        self.assertEqual(self.s.describe_counts, {})

    async def test_apply_requires_every_requirement(self):
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            await self.s.request({'op':'apply'})
        self.assertEqual(self.s.apply_attempts, 0)

    async def test_repeated_selection_after_exhaustion_applies_grounded_subset(self):
        self.s.requirements = {'r1': 'set structural count to 2', 'r2': 'integration-only setting'}
        self.s.candidates = {'r1.p1': dict(key='CONFIG.A', label='Structural count', requirement_id='r1',
                                           evidence='source', type='long', current='0')}
        self.s.selections = {('r1', 'r1.p1'): dict(candidate_id='r1.p1', value=2)}
        self.s.exhausted['r2'] = 1
        result = await self.s.request(dict(op='select', selections=[dict(
            requirement_id='r1', candidate_id='r1.p1', value=2)]))
        self.assertEqual(result['partial_apply_trigger'], 'repeated_selection_after_discovery_exhaustion')
        self.assertEqual(result['stage'], 'configured')
        self.assertEqual(self.s.apply_attempts, 1)
        self.assertIn('2', self.s.generated_result)
        self.assertEqual(result['unresolved_requirements'], [dict(id='r2', text='integration-only setting')])

    async def test_enable_selection_cannot_claim_compound_size_requirement(self):
        self.s.requirements = {'r1': 'window enabled at 256 MB'}
        self.s.candidates = {'r1.p1': dict(key='CONFIG.A', label='Window enable', requirement_id='r1', evidence='source')}
        await self.select(value=1)
        reply = await self.s.request(dict(op='apply'))
        self.assertTrue(reply['apply_paused'])
        self.assertEqual(reply['coverage_gaps'][0]['missing_values'], ['256 MB'])
        self.assertEqual(self.s.apply_attempts, 0)
        await self.s.request(dict(op='apply'))
        finish = await self.s.request(dict(op='finish'))
        self.assertFalse(finish['full'])
        self.assertTrue(finish['coverage_gaps'])

    async def test_verified_summary_uses_native_observation_not_selected_value(self):
        async def native(expression, prefix=''):
            if expression.startswith('[lite::verified_readback'):
                field=tdict(dict(key='CONFIG.A', actual='13', wanted='8', **{'pass':'0'}))
                return subprocess.check_output(['tclsh'], input='puts [dict create fields [list '+field+']]\n', text=True).strip()
            return await Session.native(self.s, expression, prefix)
        self.s.native = native
        reply = await self.s.verified_readback()
        self.assertEqual(reply['fields'][0]['actual'], '13')
        self.assertFalse(reply['fields'][0]['passed'])

    async def test_source_enum_value_cannot_be_waived_as_context(self):
        self.s.requirements = {'r1': 'use A-PHY', 'r2': 'width 8'}
        self.s.describe_counts['r1'] = 1
        self.s.candidates = {'r1.p1':dict(key='CONFIG.MODE',requirement_id='r1',
                               choices=[dict(value='AP HY',label='A-PHY')])}
        with self.assertRaisesRegex(ValueError, 'cannot be waived'):
            await self.s.request(dict(op='waive',requirement_id='r1',reason='context only'))

    async def test_unique_enum_label_is_normalized_to_source_value(self):
        self.s.requirements = {'r1': "4 live inputs on tile 2's ISP0"}
        self.s.candidates = {'r1.p1': dict(
            key='CONFIG.C_TILE2_ISP0_IO_TYPE', label='ISP0 IO Type', type='string',
            requirement_id='r1', evidence='source', enabled=True, current='0',
            choices=[dict(value='0', label='Disable'), dict(value='1', label='LILO'),
                     dict(value='2', label='LIMO'), dict(value='3', label='MIMO')])}
        result = await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id='r1.p1', value='LIMO')]))
        self.assertEqual(result['selected'][0]['value'], '2')
        self.assertIn('normalized_unique_source_enum_label',
                      {repair['repair'] for repair in result['selection_repairs']})

    async def test_negative_hardware_count_with_candidate_cannot_be_waived(self):
        self.s.requirements = {'r1': 'no AXI slave/master ports', 'r2': 'memory controller type'}
        self.s.describe_counts['r1'] = 1
        self.s.candidates = {
            'r1.p1': dict(key='CONFIG.NUM_SI', label='Number of AXI slave ports', requirement_id='r1'),
            'r1.p2': dict(key='CONFIG.NUM_MI', label='Number of AXI master ports', requirement_id='r1'),
        }
        with self.assertRaisesRegex(ValueError, 'zero/disabled hardware'):
            await self.s.request(dict(op='waive', requirement_id='r1', reason='leave defaults'))

    async def test_negative_context_without_candidate_can_still_be_waived(self):
        self.s.requirements = {'r1': 'without external board interface', 'r2': 'memory controller type'}
        self.s.describe_counts['r1'] = 1
        self.s.candidates = {}
        result = await self.s.request(dict(op='waive', requirement_id='r1', reason='board context only'))
        self.assertEqual(result['waived'][0]['requirement_id'], 'r1')

    async def test_counted_family_coverage_detects_omitted_instance(self):
        self.s.requirements={'r1':'enable 3 units', 'r2':'unit core clocks at 600 MHz'}
        self.s.api_parameters=[dict(key='CONFIG.UNIT%d_CLK'%i,label='Core clock') for i in range(3)]
        self.s.candidates={f'r2.p{i}':dict(row,requirement_id='r2') for i,row in enumerate(self.s.api_parameters)}
        self.s.selections={('r2','r2.p0'):dict(value=600)}
        gaps=self.s.coverage_findings()
        self.assertEqual(gaps[0]['missing_values'],['CONFIG.UNIT1_CLK','CONFIG.UNIT2_CLK'])

    async def test_counted_family_bundle_expands_through_ordinary_candidates(self):
        self.s.requirements={'r1':'set clocks on 3 tiles to 600 MHz'}
        rows=[dict(id=f'r1.p{i+1}',key=f'CONFIG.TILE{i}_CORE_CLK',label='Core clock',type='long') for i in range(3)]
        self.s.candidates={row['id']:dict(row,requirement_id='r1',evidence='source') for row in rows}
        bundles=self.s.family_bundles('r1',rows)
        self.assertEqual(len(bundles),1)
        reply=await self.s.request(dict(op='select',selections=[dict(
            requirement_id='r1',candidate_id=bundles[0]['id'],value=600)]))
        self.assertEqual({x['key'] for x in reply['selected']},
                         {f'CONFIG.TILE{i}_CORE_CLK' for i in range(3)})
        self.assertEqual(reply['selection_repairs'][0]['repair'],'expanded_family_bundle')

    async def test_grounding_cannot_override_a_direct_source_enum_encoding(self):
        self.s.requirements={'r1':'A-PHY'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.A',requirement_id='r1',evidence='source',
             choices=[dict(value='1',label='A-PHY'),dict(value='2',label='B-PHY')])}
        with self.assertRaisesRegex(ValueError,'directly encode|Requested source label'):
            await self.s.request(dict(op='select', selections=[dict(requirement_id='r1',candidate_id='r1.p1',
              value='2',covers=['A-PHY'],grounding='unsupported claim')]))

    async def test_annotation_only_reselect_reuses_value_and_wraps_scalar_covers(self):
        self.s.requirements={'r1':'controller_type=LPDDR5'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.MODE',requirement_id='r1',evidence='source')}
        await self.s.request(dict(op='select',selections=[dict(
            requirement_id='r1',candidate_id='r1.p1',value='LPDDR5_SDRAM')]))
        result=await self.s.request(dict(op='select',selections=[dict(
            requirement_id='r1',candidate_id='r1.p1',covers='LPDDR5',grounding='source enum uses this suffix')]))
        self.assertTrue(result['request_repaired'])
        self.assertEqual({r['repair'] for r in result['selection_repairs']},
                         {'reused_selected_value','wrapped_scalar_covers'})
        self.assertEqual(self.s.selections[('r1','r1.p1')]['value'],'LPDDR5_SDRAM')
        self.assertEqual(self.s.selections[('r1','r1.p1')]['covers'],['LPDDR5'])

    async def test_missing_verified_summary_prevents_full_success(self):
        await self.describe();await self.select()
        await self.s.request(dict(op='apply'))
        async def missing(): return dict(available=False,reason='readback failed')
        self.s.verified_readback=missing
        self.assertFalse((await self.s.request(dict(op='finish')))['full'])

    async def test_unretained_provisional_field_has_complete_grounded_repair_batch(self):
        self.s.requirements={'r1':'link x2'}
        self.s.candidates={
          'r1.p1':dict(key='CONFIG.D',subkey='MODE',requirement_id='r1',evidence='source'),
          'r1.p2':dict(key='CONFIG.D',subkey='WIDTH',requirement_id='r1',evidence='source',mapping_scope='native_same_name_candidate'),
          'r1.p3':dict(key='CONFIG.D',subkey='SPEED',requirement_id='r1',evidence='source')}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value='LINK_x2_AUX'),
                           ('r1','r1.p2'):dict(candidate_id='r1.p2',value='X2'),
                           ('r1','r1.p3'):dict(candidate_id='r1.p3',value='fast')}
        reply=self.s.validation_findings('CFG:FIX missing nested key WIDTH')
        batch=reply['unretained_field_repairs'][0]['replacement_selections']
        self.assertEqual({s['candidate_id'] for s in batch},{'r1.p1','r1.p3'})
        self.assertEqual(batch[0]['covers'],['x2'])
        self.s.apply_attempts=1
        # The actual model copied the repair values but omitted covers, then
        # re-added the failed field after the generic replacement warning.
        bare_batch=[{k:v for k,v in x.items() if k not in ['covers','grounding']} for x in batch]
        response=await self.s.request(dict(op='select',selections=bare_batch))
        self.assertIn('do not reselect',response['replaced_note'])
        self.assertEqual(self.s.selections[('r1','r1.p1')]['covers'],['x2'])
        before=dict(self.s.selections)
        with self.assertRaisesRegex(ValueError,'did not retain WIDTH'):
            await self.s.request(dict(op='select',selections=bare_batch+[
                dict(requirement_id='r1',candidate_id='r1.p2',value='X2')]))
        self.assertEqual(self.s.selections,before)
        self.assertNotIn(('r1','r1.p2'),self.s.selections)
        self.assertEqual(self.s.coverage_findings(),[])

    def test_cross_requirement_source_enum_is_strict_propagated_equivalence(self):
        self.s.requirements={'r1':'PCIe x2','r2':'MDB5 shared quad'}
        self.s.candidates={
          'r1.p1':dict(key='CONFIG.D',subkey='PCIE0_LINK_WIDTH',requirement_id='r1',
                       evidence='source',mapping_scope='native_same_name_candidate'),
          'r2.p1':dict(key='CONFIG.D',subkey='MDB5_GT',requirement_id='r2',evidence='source')}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value='X2'),
                           ('r2','r2.p1'):dict(candidate_id='r2.p1',value='PCIe0_x2_10GbE')}
        found=self.s.propagated_equivalences()
        self.assertEqual(found[('r1','r1.p1')]['subkey'],'MDB5_GT')
        self.assertEqual(found[('r1','r1.p1')]['wanted'],'PCIe0_x2_10GbE')

        self.s.candidates['r2.p2']=dict(key='CONFIG.D',subkey='OTHER_MODE',requirement_id='r2',evidence='source')
        self.s.selections[('r2','r2.p2')]=dict(candidate_id='r2.p2',value='PCIe0_x2_other')
        self.assertEqual(self.s.propagated_equivalences(),{})

    async def test_source_mapped_enum_candidate_can_serve_another_requirement(self):
        self.s.requirements = {'r1': 'C-PHY interface', 'r2': 'D-PHY line rate 1500 Mbps'}
        self.s.candidates = {
            'r2.p1': dict(key='CONFIG.C_PHY_MODE', label='PHY mode',
                          requirement_id='r2', evidence='source', type='string',
                          enabled=True, current='DPHY', range_complete=True,
                          range='RANGE_LIST:DPHY CPHY',
                          choices=[dict(value='DPHY', label='D-PHY'),
                                   dict(value='CPHY', label='C-PHY')])}
        result = await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id='r2.p1', value='CPHY')]))
        self.assertEqual(self.s.selections[('r1', 'r2.p1')]['value'], 'CPHY')
        self.assertEqual(result['selection_repairs'][0]['repair'],
                         'reused_source_mapped_candidate')

    async def test_cross_requirement_candidate_requires_exact_source_mapping(self):
        self.s.requirements = {'r1': 'C-PHY interface', 'r2': 'other mode'}
        self.s.candidates = {
            'r2.p1': dict(key='CONFIG.C_PHY_MODE', label='PHY mode',
                          requirement_id='r2', evidence='source', type='string',
                          enabled=True, current='DPHY', range_complete=True,
                          range='RANGE_LIST:DPHY CPHY',
                          choices=[dict(value='DPHY', label='D-PHY'),
                                   dict(value='CPHY', label='C-PHY')])}
        with self.assertRaisesRegex(ValueError, 'published value mapping does not match'):
            await self.s.request(dict(op='select', selections=[
                dict(requirement_id='r1', candidate_id='r2.p1', value='DPHY')]))

    async def test_a_cell_with_no_parameter_bank_can_be_read_back_but_not_written(self):
        """Every row unavailable means the bank is absent, not that the value is free.

        ced-gen_H3 asked for the clocking wizard to be fed from a 125 MHz PL
        clock 0. clk_wiz_0 answers live, so its rows carry a current and a
        value_src and look ordinary; only range reads PB_MISS:* and range_scope
        reads unavailable. Both models wrote 125 into the wizard's own
        REF_CLK_FREQ instead of into the PS driving it, including the run where
        describe said so in prose. Nothing would have checked that value.
        """
        self.s.requirements = {'r4': 'feed the clocking wizard from a 125 MHz PL clock 0'}
        row = dict(label='Reference Frequency(MHz)', type='float', enabled=True, requirement_id='r4',
                   range_scope='unavailable', range_complete=False, value_src='DEFAULT', current='100.0')
        self.s.candidates = {
            'r4.p1': dict(row, key='CONFIG.REF_CLK_FREQ', range='PB_MISS:REF_CLK_FREQ'),
            'r4.p2': dict(row, key='CONFIG.USER_CLK_FREQ0', range='PB_MISS:USER_CLK_FREQ0')}
        with self.assertRaisesRegex(ValueError, 'no parameter bank'):
            await self.s.request(dict(op='select', selections=[
                dict(requirement_id='r4', candidate_id='r4.p1', value='125.0')]))
        # Reading the value back is the one thing these rows still support.
        await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r4', candidate_id='r4.p1', value='100.0', mode='assert')]))
        self.assertIn(('r4', 'r4.p1'), self.s.selections)
        # One missing row among ranged ones is ordinary and stays writable.
        self.s.selections = {}
        self.s.candidates['r4.p2'] = dict(row, key='CONFIG.USER_CLK_FREQ0', range='RANGE_NUM:0.1:250',
                                          range_scope='native_current_instance', range_complete=True)
        await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r4', candidate_id='r4.p1', value='125.0')]))
        self.assertIn(('r4', 'r4.p1'), self.s.selections)

    async def test_writing_an_achieved_clock_readback_goes_to_the_parameter_that_sets_it(self):
        """Marking the readback assert was not enough on its own.

        ced-gen_M7's 26B run still picked PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ for
        "PL clock at 100 MHz" over the request parameter listed beside it: the
        readback reads value_src USER and a live-looking 50.000000, so it looks
        like the authoritative one. Before the fix that write was silently
        dropped and the run claimed success; after it, apply failed on readback.
        Neither reports the clock the request asked for, so the write is sent to
        the parameter that sets it.
        """
        self.s.requirements = {'r2': 'PL clock at 100 MHz'}
        row = dict(label='PL clock', evidence='source', type='float', enabled=True,
                   range='RANGE_NUM:0.1:250', range_complete=True)
        self.s.candidates = {
            'r2.p1': dict(row, key='CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ', requirement_id='r2', current='50'),
            'r2.p3': dict(row, key='CONFIG.PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ', requirement_id='r2',
                          current='50.000000', value_src='USER', write_mode='assert',
                          derived_from='CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ', derived_scope='achieved_readback')}
        reply = await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r2', candidate_id='r2.p3', value='100')]))
        self.assertIn(('r2', 'r2.p1'), self.s.selections)
        self.assertNotIn(('r2', 'r2.p3'), self.s.selections)
        repair = next(x for x in reply['selection_repairs']
                      if x['repair'] == 'redirected_achieved_readback_to_its_setter')
        self.assertEqual((repair['candidate_id'], repair['replacement_candidate_id']), ('r2.p3', 'r2.p1'))
        # Asserting the achieved value is a legitimate check and stays as asked.
        reply = await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r2', candidate_id='r2.p3', value='100', mode='assert')]))
        self.assertIn(('r2', 'r2.p3'), self.s.selections)
        # Without the setter among the candidates, say which one to describe.
        del self.s.candidates['r2.p1']
        self.s.selections = {}
        with self.assertRaisesRegex(ValueError, 'PCW_FPGA0_PERIPHERAL_FREQMHZ'):
            await self.s.request(dict(op='select', selections=[
                dict(requirement_id='r2', candidate_id='r2.p3', value='100')]))

    async def test_stale_requirement_candidate_id_uses_exact_returned_twin(self):
        self.s.requirements={'r5':'line buffer','r9':'D-PHY line rate 1500 Mbps'}
        row=dict(key='CONFIG.DPY_LINE_RATE',label='Line Rate',evidence='source',
                 type='long',enabled=True,current='800',range='RANGE_NUM:400:4500')
        self.s.candidates={'r5.p3':dict(row,requirement_id='r5'),
                           'r9.p3':dict(row,requirement_id='r9')}
        reply=await self.s.request(dict(op='select',selections=[dict(
            requirement_id='r9',candidate_id='r5.p3',value='1500')]))
        self.assertIn(('r9','r9.p3'),self.s.selections)
        self.assertNotIn(('r9','r5.p3'),self.s.selections)
        repair=next(x for x in reply['selection_repairs']
                    if x['repair']=='canonicalized_requirement_candidate_id')
        self.assertEqual((repair['candidate_id'],repair['replacement_candidate_id']),
                         ('r5.p3','r9.p3'))

    async def test_stale_candidate_id_is_not_repaired_when_target_identity_is_ambiguous(self):
        self.s.requirements={'r1':'target','r2':'other'}
        base=dict(key='CONFIG.D',subkey='MODE',label='Mode',evidence='source',
                  type='string',enabled=True,current='A')
        self.s.candidates={'r2.p1':dict(base,requirement_id='r2'),
                           'r1.p1':dict(base,requirement_id='r1'),
                           'r1.p2':dict(base,requirement_id='r1')}
        with self.assertRaisesRegex(ValueError,'published value mapping does not match'):
            await self.s.request(dict(op='select',selections=[dict(
                requirement_id='r1',candidate_id='r2.p1',value='B')]))

    async def test_explicit_setting_name_routes_selection_and_completes_vacated_requirement(self):
        self.s.requirements={'r1':'NUM_MC: 4','r2':'NUM_MCP: 1'}
        self.s.candidates={
            'r2.p1':dict(key='CONFIG.D',subkey='DDRMC5_NUM_MCP',requirement_id='r2',
                         evidence='source',type='string',current='1',enabled=True),
            'r2.p2':dict(key='CONFIG.D',subkey='DDRMC5_NUM_MC',requirement_id='r2',
                         evidence='source',type='string',current='1',enabled=True),
            'r2.p3':dict(key='CONFIG.NUM_MCP',requirement_id='r2',evidence='source',
                         type='long',current='0',enabled=False)}
        reply=await self.s.request(dict(op='select',selections=[dict(
            requirement_id='r2',candidate_id='r2.p2',value='4')]))
        self.assertIn(('r1','r2.p2'),self.s.selections)
        self.assertNotIn(('r2','r2.p2'),self.s.selections)
        self.assertEqual(next(x for x in reply['selection_repairs']
            if x['repair']=='routed_explicit_setting_candidate')['requirement_id'],'r1')
        applied=await self.s.request(dict(op='apply'))
        self.assertEqual(self.s.selections[('r2','r2.p1')]['value'],'1')
        self.assertEqual(self.s.selections[('r2','r2.p1')]['mode'],'assert')
        self.assertIn('added_unique_explicit_setting',
                      {x['repair'] for x in reply['selection_repairs']})
        self.assertEqual(applied['stage'],'configured')

    async def test_selection_batch_adds_omitted_unique_explicit_setting(self):
        self.s.requirements={'r1':'NUM_MC=4','r2':'NUM_MCP=1'}
        self.s.candidates={
            'r1.p1':dict(key='CONFIG.D',subkey='DDRMC5_NUM_MC',requirement_id='r1',
                         evidence='source',type='string',current='1',enabled=True),
            'r1.p2':dict(key='CONFIG.NUM_MC',requirement_id='r1',evidence='source',
                         type='long',current='0',enabled=False),
            'r2.p1':dict(key='CONFIG.D',subkey='DDRMC5_NUM_MCP',requirement_id='r2',
                         evidence='source',type='string',current='1',enabled=True)}
        reply=await self.s.request(dict(op='select',selections=[dict(
            requirement_id='r2',candidate_id='r2.p1',value='1')]))
        self.assertEqual(self.s.selections[('r1','r1.p1')]['value'],'4')
        self.assertEqual(self.s.selections[('r2','r2.p1')]['value'],'1')
        self.assertIn('added_unique_explicit_setting',
                      {x['repair'] for x in reply['selection_repairs']})

    def test_propagated_equivalence_requires_distinguishing_identifier(self):
        self.s.requirements={'r1':'link x2','r2':'other x2'}
        self.s.candidates={
          'r1.p1':dict(key='CONFIG.D',subkey='LINK_WIDTH',requirement_id='r1',
                       evidence='source',mapping_scope='native_same_name_candidate'),
          'r2.p1':dict(key='CONFIG.D',subkey='MODE',requirement_id='r2',evidence='source')}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value='X2'),
                           ('r2','r2.p1'):dict(candidate_id='r2.p1',value='unrelated_x2')}
        self.assertEqual(self.s.propagated_equivalences(),{})

    async def test_size_value_and_scale_are_completed_from_ledger_unit(self):
        self.s.requirements={'r1':'independent 32-bit memory BAR2 enabled at 256 MB'}
        choices=lambda values:[dict(value=value,label='') for value in values]
        self.s.candidates={
          'r1.p1':dict(key='CONFIG.D',subkey='BAR2_SIZE',requirement_id='r1',evidence='source',
                       type='string',choices=choices(['1','8','32','128','256'])),
          'r1.p2':dict(key='CONFIG.D',subkey='BAR2_SCALE',requirement_id='r1',evidence='source',
                       type='string',choices=choices(['Bytes','KiloBytes','MegaBytes','GigaBytes']))}
        result=await self.s.request(dict(op='select',selections=[
            dict(requirement_id='r1',candidate_id='r1.p1',value='8')]))
        self.assertEqual(self.s.selections[('r1','r1.p1')]['value'],'256')
        self.assertEqual(self.s.selections[('r1','r1.p2')]['value'],'MegaBytes')
        self.assertEqual({r['repair'] for r in result['selection_repairs']},
                         {'used_unique_ledger_size','added_unique_unit_scale'})

    def test_size_role_does_not_confuse_bit_width_or_field_index(self):
        choices=[dict(value=value,label='') for value in ['1','8','32','128','256']]
        row=dict(key='CONFIG.D',subkey='BAR2_SIZE',label='BAR2 SIZE',choices=choices)
        self.assertEqual(self.s.role_numeric_choice(row,'32-bit BAR2 at 256 MB'),'256')
        self.assertIsNone(self.s.role_numeric_choice(row,'32-bit BAR2'))

    def test_direction_verb_maps_only_matching_boolean_field(self):
        choices=[dict(value='0',label='false'),dict(value='1',label='true')]
        output=dict(key='CONFIG.C_ALL_OUTPUTS',label='All Outputs',choices=choices)
        dual=dict(key='CONFIG.C_IS_DUAL',label='Enable Dual Channel',choices=choices)
        self.assertEqual(self.s.text_choice(output,'drive the reset line'),'1')
        self.assertIsNone(self.s.text_choice(dual,'drive the reset line'))

    async def test_apply_completes_one_unique_enabled_grounded_pending_choice(self):
        self.s.requirements={'r1':'width 1','r2':'drive reset output'}
        choices=[dict(value='0',label='false'),dict(value='1',label='true')]
        self.s.candidates={
          'r1.p1':dict(key='CONFIG.WIDTH',requirement_id='r1',evidence='source',type='long'),
          'r2.p1':dict(key='CONFIG.C_ALL_OUTPUTS',requirement_id='r2',evidence='source',type='long',
                       label='All Outputs',choices=choices,enabled=True,current='0'),
          'r2.p2':dict(key='CONFIG.C_ALL_OUTPUTS_2',requirement_id='r2',evidence='source',type='long',
                       label='All Outputs',choices=choices,enabled=False,current='0')}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value=1)}
        result=await self.s.request(dict(op='apply'))
        self.assertEqual(result['auto_completed_selections'],[
            dict(requirement_id='r2',candidate_id='r2.p1',value='1',
                 scope='unique_enabled_returned_text_choice')])
        self.assertIn(('r2','r2.p1'),self.s.selections)

    async def test_select_replaces_unrequested_default_with_unique_direction(self):
        self.s.requirements = {'r1': 'drive the camera reset line'}
        choices = [dict(value='0', label='false'), dict(value='1', label='true')]
        self.s.candidates = {
            'r1.p1': dict(key='CONFIG.C_ALL_OUTPUTS', label='All Outputs',
                          requirement_id='r1', evidence='source', type='long',
                          choices=choices, enabled=True, current='0'),
            'r1.p2': dict(key='CONFIG.C_DOUT_DEFAULT', label='Default Output Value',
                          requirement_id='r1', evidence='source', type='string',
                          enabled=True, current='0x00000000')}
        result = await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id='r1.p2', value='0x00000001')]))
        self.assertEqual(self.s.selections, {
            ('r1', 'r1.p1'): dict(candidate_id='r1.p1', value='1')})
        self.assertEqual({item['repair'] for item in result['selection_repairs']},
                         {'dropped_unrequested_default_for_direction',
                          'added_unique_direction_control'})

    def test_shared_width_does_not_cover_a_requested_direction(self):
        task = 'Single-bit AXI GPIO to drive the camera sensor-hub reset line'
        self.s.requirements = {'r1': 'AXI GPIO width: 1 bit', 'r2': task}
        self.s.added_requirements = [task]
        choices = [dict(value='0', label='false'), dict(value='1', label='true')]
        width = dict(key='CONFIG.C_GPIO_WIDTH', label='GPIO Width', type='long', evidence='source',
                     enabled=True, current='32', range='RANGE_NUM:1:32')
        self.s.candidates = {
            'r1.p1': dict(width, requirement_id='r1'),
            'r2.p1': dict(width, requirement_id='r2'),
            'r2.p2': dict(key='CONFIG.C_ALL_OUTPUTS', label='All Outputs', type='long', evidence='source',
                          choices=choices, enabled=True, current='0', requirement_id='r2')}
        self.s.selections = {('r1', 'r1.p1'): dict(candidate_id='r1.p1', value=1)}
        self.assertNotIn('r2', self.s.scalar_covered())
        self.assertIn('r2', [p['id'] for p in self.s.pending()])
        self.assertEqual(self.s.requested_direction(task), 'outputs')
        self.assertIsNone(self.s.requested_direction('GPIO output default 0x1'))

    async def test_driver_noun_names_output_direction(self):
        self.s.requirements = {'r1': 'camera sensor-hub reset line driver'}
        choices = [dict(value='0', label='false'), dict(value='1', label='true')]
        self.s.candidates = {
            'r1.p1': dict(key='CONFIG.C_DOUT_DEFAULT', label='Default Output Value',
                          requirement_id='r1', evidence='source', type='string',
                          enabled=True, current='0x00000000'),
            'r1.p2': dict(key='CONFIG.C_ALL_OUTPUTS', label='All Outputs',
                          requirement_id='r1', evidence='source', type='long',
                          choices=choices, enabled=True, current='0'),
            'r1.p3': dict(key='CONFIG.C_ALL_OUTPUTS_2', label='All Outputs',
                          requirement_id='r1', evidence='source', type='long',
                          choices=choices, enabled=False, current='0',
                          enablement_controls=['CONFIG.C_IS_DUAL'])}
        await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id='r1.p1', value='0x00000000')]))
        self.assertEqual(self.s.selections, {
            ('r1', 'r1.p2'): dict(candidate_id='r1.p2', value='1')})

    async def test_select_does_not_add_disabled_direction_control(self):
        self.s.requirements = {'r1': 'Single-bit GPIO output'}
        choices = [dict(value='0', label='false'), dict(value='1', label='true')]
        self.s.candidates = {
            'r1.p1': dict(key='CONFIG.C_ALL_OUTPUTS_2', label='All Outputs',
                          requirement_id='r1', evidence='source', type='long',
                          choices=choices, enabled=False, current='0',
                          enablement_controls=['CONFIG.C_IS_DUAL']),
            'r1.p2': dict(key='CONFIG.C_GPIO_WIDTH', label='GPIO Width',
                          requirement_id='r1', evidence='source', type='long',
                          enabled=True, current='32'),
            'r1.p3': dict(key='CONFIG.C_IS_DUAL', label='Enable Dual Channel',
                          requirement_id='r1', evidence='source', type='long',
                          choices=choices, enabled=True, current='0')}
        result = await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id='r1.p2', value=1),
            dict(requirement_id='r1', candidate_id='r1.p3', value=0)]))
        self.assertNotIn(('r1', 'r1.p1'), self.s.selections)
        repairs = {item['repair'] for item in result.get('selection_repairs', [])}
        self.assertIn('not_added_disabled_direction_control', repairs)
        self.assertNotIn('added_unique_direction_control', repairs)

    async def test_select_still_prefers_enabled_copy_of_direction_control(self):
        self.s.requirements = {'r1': 'Single-bit GPIO output'}
        choices = [dict(value='0', label='false'), dict(value='1', label='true')]
        self.s.candidates = {
            'r1.p1': dict(key='CONFIG.C_ALL_OUTPUTS_2', label='All Outputs',
                          requirement_id='r1', evidence='source', type='long',
                          choices=choices, enabled=False, current='0'),
            'r1.p2': dict(key='CONFIG.C_GPIO_WIDTH', label='GPIO Width',
                          requirement_id='r1', evidence='source', type='long',
                          enabled=True, current='32'),
            'r1.p3': dict(key='CONFIG.C_ALL_OUTPUTS', label='All Outputs',
                          requirement_id='r1', evidence='source', type='long',
                          choices=choices, enabled=True, current='0')}
        result = await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id='r1.p2', value=1)]))
        self.assertIn(('r1', 'r1.p3'), self.s.selections)
        self.assertNotIn(('r1', 'r1.p1'), self.s.selections)
        self.assertIn('added_unique_direction_control',
                      {item['repair'] for item in result['selection_repairs']})

    async def test_same_label_numbered_copies_are_shown_together(self):
        self.s.api_parameters = [
            dict(key='CONFIG.C_ALL_OUTPUTS', label='All Outputs'),
            dict(key='CONFIG.C_ALL_OUTPUTS_2', label='All Outputs'),
            dict(key='CONFIG.C_ALL_INPUTS', label='All Inputs'),
            dict(key='CONFIG.C_GPIO_WIDTH', label='GPIO Width'),
            dict(key='CONFIG.C_GPIO2_WIDTH', label='GPIO Width'),
            dict(key='CONFIG.OTHER_OUTPUTS', label='All Outputs')]
        added = await self.s.same_label_siblings([
            dict(key='CONFIG.C_ALL_OUTPUTS_2', label='All Outputs'),
            dict(key='CONFIG.C_GPIO_WIDTH', label='GPIO Width')])
        self.assertEqual([(a['key'], a['related_to'], a['relation_scope']) for a in added], [
            ('CONFIG.C_ALL_OUTPUTS', 'CONFIG.C_ALL_OUTPUTS_2', 'same_label_numbered_copy'),
            ('CONFIG.C_GPIO2_WIDTH', 'CONFIG.C_GPIO_WIDTH', 'same_label_numbered_copy')])
        self.assertEqual(await self.s.same_label_siblings([dict(key='CONFIG.X', subkey='A', label='All Outputs')]), [])

    def test_numbered_stem_removes_channel_numbers_only(self):
        self.assertEqual(self.s.numbered_stem('CONFIG.C_ALL_OUTPUTS_2'), 'CONFIG.C_ALL_OUTPUTS')
        self.assertEqual(self.s.numbered_stem('CONFIG.C_GPIO2_WIDTH'), 'CONFIG.C_GPIO_WIDTH')
        self.assertEqual(self.s.numbered_stem('CONFIG.CLKOUT1_USED'), 'CONFIG.CLKOUT_USED')
        self.assertNotEqual(self.s.numbered_stem('CONFIG.C_ALL_OUTPUTS'), self.s.numbered_stem('CONFIG.C_ALL_INPUTS'))

    async def test_output_default_does_not_imply_output_only_direction(self):
        self.s.requirements = {'r1': 'GPIO output default 0x00024042'}
        choices = [dict(value='0', label='false'), dict(value='1', label='true')]
        self.s.candidates = {
            'r1.p1': dict(key='CONFIG.C_DOUT_DEFAULT', label='Default Output Value',
                          requirement_id='r1', evidence='source', type='string',
                          enabled=True, current='0x00000000'),
            'r1.p2': dict(key='CONFIG.C_ALL_OUTPUTS', label='All Outputs',
                          requirement_id='r1', evidence='source', type='long',
                          choices=choices, enabled=True, current='0')}
        result = await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id='r1.p1', value='0x00024042')]))
        self.assertEqual(self.s.selections, {
            ('r1', 'r1.p1'): dict(candidate_id='r1.p1', value='0x00024042')})
        self.assertNotIn('selection_repairs', result)

    async def test_output_value_does_not_imply_output_only_direction(self):
        self.s.requirements = {'r1': 'GPIO output value 0x00024042'}
        choices = [dict(value='0', label='false'), dict(value='1', label='true')]
        self.s.candidates = {
            'r1.p1': dict(key='CONFIG.C_DOUT_DEFAULT', label='Default Output Value',
                          requirement_id='r1', evidence='source', type='string',
                          enabled=True, current='0x00000000'),
            'r1.p2': dict(key='CONFIG.C_ALL_OUTPUTS', label='All Outputs',
                          requirement_id='r1', evidence='source', type='long',
                          choices=choices, enabled=True, current='0')}
        result = await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id='r1.p1', value='0x00024042')]))
        self.assertEqual(self.s.selections, {
            ('r1', 'r1.p1'): dict(candidate_id='r1.p1', value='0x00024042')})
        self.assertNotIn('selection_repairs', result)

    def test_explicit_single_count_does_not_warn_about_a_correct_default(self):
        self.s.requirements={'r1':'Single-clock'}
        self.s.candidates={'p1':dict(key='CONFIG.A',label='Number of clocks',type='long',current='1',requirement_id='r1'),
                           'p2':dict(key='CONFIG.B',label='Number of inputs',type='long',current='2',requirement_id='r1')}
        selections={('r1','p1'):dict(value=1)}
        self.assertEqual(self.s.unchanged_selections(selections,{'r1'}),[])
        self.assertFalse(self.s.value_named_in_text(self.s.candidates['p1'],1,'single-ended mode'))

    def test_duplicate_scalar_value_requires_its_own_discovered_source_field(self):
        self.s.requirements={'r1':'active-low reset','r2':'with an active-low reset',
                             'r3':'active-high reset','r4':'active-low reset'}
        mode=dict(key='CONFIG.POLARITY',choices=[dict(value='LOW',label='active-low'),dict(value='HIGH',label='active-high')])
        control=dict(key='CONFIG.USE_RESET',type='bool',dependency_ports=['RESETN'],dependency_scope='direct_native_predecessor')
        self.s.candidates={'p1':dict(mode,requirement_id='r1'),'p2':dict(control,requirement_id='r1'),
                           'p3':dict(mode,requirement_id='r2'),'p4':dict(control,requirement_id='r2'),
                           'p5':dict(mode,requirement_id='r3'),'p6':dict(key='CONFIG.OTHER',requirement_id='r4')}
        self.s.selections={('r1','p1'):dict(value='LOW'),('r1','p2'):dict(value=True)}
        self.assertIn('r2',self.s.scalar_covered())
        self.assertEqual([x['id'] for x in self.s.pending()],['r3','r4'])
        self.assertIn('covered_by_source',self.s.state())
        self.s.selections[('r1','p2')]['value']=False
        self.assertNotIn('r2',self.s.scalar_covered())

    def test_shared_port_exposure_requires_all_named_source_controls(self):
        self.s.requirements={'r1':'setup','r2':'ready exposed'}
        def port(name,rid):return dict(key='CONFIG.USE_'+name,type='bool',requirement_id=rid,
                                       dependency_ports=[name],dependency_scope='direct_native_predecessor',current='true')
        self.s.candidates={'p1':port('READY','r1'),'p2':port('DONE','r1'),
                           'p3':port('READY','r2'),'p4':port('DONE','r2')}
        self.s.selections={('r1','p1'):dict(value=True)}
        self.assertIn('r2',self.s.scalar_covered())
        self.s.requirements['r2']='ready and done exposed'
        self.assertNotIn('r2',self.s.scalar_covered())
        self.s.selections[('r1','p2')]=dict(value=True)
        self.assertIn('r2',self.s.scalar_covered())
        self.s.requirements['r2']='ready and unknown exposed'
        self.assertNotIn('r2',self.s.scalar_covered())
        self.s.requirements['r2']='ready exposed'
        selected={('r2','p3'):dict(value=True)}
        self.assertEqual(self.s.unchanged_selections(selected,{'r2'}),[])
        self.s.candidates['p3'].pop('dependency_scope')
        self.assertTrue(self.s.unchanged_selections(selected,{'r2'}))

    def test_cross_requirement_coverage_keeps_target_interface_qualification(self):
        self.s.requirements={'r1':'line buffer 4096','r2':'D-PHY line rate 1500 Mbps'}
        alias=dict(key='CONFIG.ALIAS',label='Line rate',type='long')
        explicit=dict(key='CONFIG.EXPLICIT',label='Line rate',type='long',
                      help='D-PHY line rate per lane',help_source='packaged_gui_tooltip',gui_context=['PHY Options'])
        self.s.candidates={'p1':dict(alias,requirement_id='r1'),'p2':dict(alias,requirement_id='r2'),
                           'p3':dict(explicit,requirement_id='r2'),'p4':dict(explicit,requirement_id='r1')}
        self.s.selections={('r1','p1'):dict(value=1500)}
        self.assertNotIn('r2',self.s.scalar_covered())
        self.s.selections={('r1','p4'):dict(value=1500)}
        self.assertIn('r2',self.s.scalar_covered())

    async def test_shared_label_requires_source_qualified_interface_mapping(self):
        self.s.requirements={'r1':'D-PHY line rate 1500 Mbps'}
        self.s.candidates={
          'p1':dict(key='CONFIG.ALIAS',label='Line rate',type='long',requirement_id='r1',evidence='native'),
          'p2':dict(key='CONFIG.EXPLICIT',label='Line rate',type='long',requirement_id='r1',evidence='source',
                    help='D-PHY line rate per lane',help_source='packaged_gui_tooltip',gui_context=['PHY Options'])}
        with self.assertRaisesRegex(ValueError,'source-qualified candidate'):
            await self.s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='p1',value=1500)]))
        await self.s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='p2',value=1500)]))
        self.assertEqual(self.s.coverage_findings(),[])
        self.s.candidates['p2'].pop('help_source')
        self.assertEqual(self.s.qualified_source_alternatives('r1',self.s.candidates['p1']),[])

    async def test_shared_and_single_qualified_candidate_batch_keeps_qualified_only(self):
        self.s.requirements={'r1':'D-PHY line rate 1500 Mbps'}
        self.s.candidates={
          'p1':dict(key='CONFIG.ALIAS',label='Line rate',type='long',requirement_id='r1',evidence='native'),
          'p2':dict(key='CONFIG.EXPLICIT',label='Line rate',type='long',requirement_id='r1',evidence='source',
                    help='D-PHY line rate per lane',help_source='packaged_gui_tooltip',gui_context=['PHY Options'])}
        result=await self.s.request(dict(op='select',selections=[
            dict(requirement_id='r1',candidate_id='p1',value=1500),
            dict(requirement_id='r1',candidate_id='p2',value=1500)]))
        self.assertNotIn(('r1','p1'),self.s.selections)
        self.assertEqual(self.s.selections[('r1','p2')]['value'],1500)
        self.assertIn(dict(candidate_id='p1',repair='dropped_redundant_unqualified_candidate',
                           replacement_candidate_id='p2'),result['selection_repairs'])

    async def test_disabled_weak_match_is_dropped_for_stronger_enabled_selection(self):
        self.s.requirements={'r1':'active lanes detection enabled'}
        choices=[dict(value='0',label='false'),dict(value='1',label='true')]
        self.s.candidates={
          'p1':dict(key='CONFIG.ACTIVE_LANES_DETECTION',label='Active lanes detection',type='long',
                    requirement_id='r1',evidence='live',enabled=True,choices=choices),
          'p2':dict(key='CONFIG.DESKEW_SEQ',label='Deskew sequence detection',type='long',
                    requirement_id='r1',evidence='live',enabled=False,choices=choices)}
        result=await self.s.request(dict(op='select',selections=[
            dict(requirement_id='r1',candidate_id='p1',value=1),
            dict(requirement_id='r1',candidate_id='p2',value=1)]))
        self.assertIn(('r1','p1'),self.s.selections)
        self.assertNotIn(('r1','p2'),self.s.selections)
        self.assertIn('dropped_redundant_disabled_candidate',
                      {repair['repair'] for repair in result['selection_repairs']})

    async def test_live_legal_label_rebinds_mispaired_packaged_ordinal(self):
        self.s.guard.database_config='declared'
        self.s.gui_parameters={'VC':dict(choices=[dict(value='4',label='All'),
                                                  dict(value='4',label='4')])}
        async def metadata(): return self.s.gui_parameters
        self.s.gui_metadata=metadata
        async def native(expression, prefix=''):
            if expression.startswith('[lite::live_parameters'):
                return subprocess.check_output(['tclsh'],input='puts [dict create CONFIG.VC [dict create value All value_src DEFAULT range {RANGE_LIST:All 0 1 2 3 4} range_scope current_configuration_mirror range_complete 1]]\n',text=True).strip()
            return await Session.native(self.s,expression,prefix)
        self.s.native=native
        row=dict(key='CONFIG.VC',label='Allowed VC',range='RANGE_LIST:All 0 1 2 3 4',
                 range_complete=True,choices=[])
        await self.s.enrich_live([row])
        self.assertEqual([(choice['value'],choice['label']) for choice in row['choices']],
                         [('All','All'),('4','4')])

    async def test_polarity_does_not_cover_source_declared_port_enable(self):
        self.s.requirements={'r1':'active-low reset','r2':'with active-low reset'}
        self.s.candidates={
          'p1':dict(key='CONFIG.POLARITY',label='Reset type',choices=[dict(value='LOW',label='active-low')],requirement_id='r1',evidence='source'),
          'p2':dict(key='CONFIG.USE_RESET',label='Reset',type='bool',requirement_id='r1',evidence='source',
                    dependency_ports=['RESETN'],dependency_scope='direct_native_predecessor'),
          'p3':dict(key='CONFIG.USE_MONITOR',label='Monitor',type='bool',requirement_id='r1',evidence='source',
                    dependency_ports=['RESETN'],dependency_scope='direct_native_predecessor'),
          'p4':dict(key='CONFIG.USE_RESET',label='Reset',type='bool',requirement_id='r2',evidence='source',
                    dependency_ports=['RESETN'],dependency_scope='direct_native_predecessor')}
        await self.s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='p1',value='LOW')]))
        gap=self.s.coverage_findings()[0]
        self.assertEqual(gap['reason'],'requested_port_enablement')
        self.assertEqual(gap['required_port_controls'][0]['candidate_id'],'p2')
        await self.s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='p2',value=True)]))
        self.assertEqual(self.s.coverage_findings(),[])
        self.assertEqual(self.s.requested_port_controls('r2'),[])
        self.s.requirements['r2']='reset disabled'
        self.assertFalse(self.s.requested_port_controls('r2')[0]['value'])

    async def test_port_control_without_native_dependency_cannot_be_inferred(self):
        self.s.requirements={'r1':'active-low reset'}
        self.s.candidates={'p1':dict(key='CONFIG.USE_RESET',type='bool',requirement_id='r1')}
        self.assertEqual(self.s.requested_port_controls('r1'),[])

    async def test_literal_source_scaling_exposes_units_and_rejects_double_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'xgui').mkdir()
            (root/'xgui'/'gui.tcl').write_text("""proc update_PARAM_VALUE.OUT_HZ {PARAM_VALUE.OUT_HZ PARAM_VALUE.DISPLAY} {
 set input_value [get_property value ${PARAM_VALUE.DISPLAY}]
 set output_value [expr {int($input_value*1000000)} ]
 set_property value $output_value ${PARAM_VALUE.OUT_HZ}
}""")
            async def native(expression,prefix=''):
                if 'XML_FILE_NAME' in expression:return str(root/'component.xml')
                return await Session.native(self.s,expression,prefix)
            self.s.native=native
            data=await self.s.gui_metadata()
            self.assertEqual(data['DISPLAY']['units'],'mhz')
            self.assertEqual(data['OUT_HZ']['units'],'hz')
            self.assertEqual(data['OUT_HZ']['write_mode'],'assert')
            self.s.requirements={'r1':'AXI clock at 99.999 MHz'}
            self.s.candidates={'p1':dict(data['DISPLAY'],key='CONFIG.DISPLAY',type='float',requirement_id='r1',evidence='source')}
            with self.assertRaisesRegex(ValueError,'Source unit.*mhz'):
                await self.s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='p1',value=99999000)]))
            await self.s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='p1',value=99.999)]))
            self.assertEqual(self.s.coverage_findings(),[])
            self.s.candidates['p1'].update(data['OUT_HZ'],key='CONFIG.OUT_HZ')
            self.assertTrue(self.s.value_named_in_text(self.s.candidates['p1'],99999000,self.s.requirements['r1']))

    async def test_documented_enum_semantics_avoid_false_gap_and_reject_wrong_rate(self):
        self.s.requirements={'r1':'PCIe Gen4'}
        row=dict(key='CONFIG.A',label='PCIe Link Speed',type='string',
                 choices=[dict(value='16.0_GT/s',label=''),dict(value='32.0_GT/s',label='')])
        self.s.register_candidates([row],'r1','source')
        await self.select(value='16.0_GT/s')
        self.assertEqual(self.s.coverage_findings(),[])
        with self.assertRaisesRegex(ValueError,'Documented source meaning'):
            await self.select(value='32.0_GT/s')

    async def test_database_candidate_keeps_packaged_labels_filtered_by_live_range(self):
        self.s.gui_parameters={'MODE':dict(choices=[
            dict(value='0',label='Disable'),dict(value='1',label='Live in/live out'),
            dict(value='2',label='Live in/memory out'),dict(value='3',label='Memory in/memory out')])}
        row=dict(key='CONFIG.MODE',label='I/O type',type='string',
                 range='RANGE_LIST:1 2',range_complete=True,range_scope='current_configuration_mirror')
        await self.s.enrich_live([row])
        self.assertEqual([(choice['value'],choice['label']) for choice in row['choices']],
                         [('1','Live in/live out'),('2','Live in/memory out')])
        self.assertEqual(row['choices_scope'],'packaged_labels_filtered_by_current_live_range')

    async def test_unique_live_range_trial_repairs_only_source_prerequisite(self):
        self.s.requirements={'r1':'four live inputs'}
        self.s.candidates={
            'r1.p1':dict(key='CONFIG.MODE',related_to='CONFIG.INPUTS',requirement_id='r1',evidence='source',
                         choices=[dict(value='1',label='LILO'),dict(value='2',label='LIMO'),dict(value='3',label='MIMO')]),
            'r1.p2':dict(key='CONFIG.INPUTS',enabled=False,enablement_controls=['CONFIG.MODE'],
                         requirement_id='r1',evidence='source')}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value='1'),
                           ('r1','r1.p2'):dict(candidate_id='r1.p2',value=4)}
        trials=[]
        async def native(expression,prefix=''):
            trials.append(expression)
            legal='RANGE_LIST:0 1 2 3 4' if len(trials)==2 else 'RANGE_LIST:0 1'
            script='puts [dict create CONFIG.INPUTS [dict create range {'+legal+'} range_complete 1 enabled 1]]\n'
            return subprocess.check_output(['tclsh'],input=script,text=True).strip()
        self.s.native=native
        config={'CONFIG.MODE':'1','CONFIG.INPUTS':4}
        repairs=await self.s.resolve_disabled_dependencies(config)
        self.assertEqual((config['CONFIG.MODE'],self.s.selections[('r1','r1.p1')]['value']),('2','2'))
        self.assertEqual(repairs[0]['evidence_scope'],'current_configuration_range_trials')
        self.s.candidates['r1.p1'].pop('related_to')
        self.s.selections[('r1','r1.p1')]['value']='1';config['CONFIG.MODE']='1'
        self.assertEqual(await self.s.resolve_disabled_dependencies(config),[])
        self.assertEqual(config['CONFIG.MODE'],'1')

    async def test_live_transition_infers_and_applies_unselected_prerequisite(self):
        self.s.cell='cell_0'
        rows=[
            dict(key='CONFIG.DUAL',type='long',current='0',choices=[dict(value='0'),dict(value='1')]),
            dict(key='CONFIG.CH2_WIDTH',type='long',current='32',enabled=False)]
        calls=[]
        async def native(expression,prefix=''):
            if expression.startswith('[lite::context_ranges'):
                calls.append(expression)
                enabled = '0' if len(calls)==2 else '1'
                return subprocess.check_output(['tclsh'],input=
                    'puts [dict create CONFIG.CH2_WIDTH [dict create enabled '+enabled+' range {RANGE_NUM:1:32} range_complete 1]]\n',
                    text=True).strip()
            return await Session.native(self.s,expression,prefix)
        self.s.native=native
        await self.s.infer_live_prerequisites(rows)
        self.assertEqual(rows[1]['enablement_controls'],['CONFIG.DUAL'])
        self.s.requirements={'r1':'channel 2 width 4'}
        self.s.candidates={
            'r1.p1':dict(key='CONFIG.CH2_WIDTH',type='long',enabled=False,
                         enablement_controls=['CONFIG.DUAL'],requirement_id='r1',evidence='source'),
            'r1.p2':dict(key='CONFIG.DUAL',type='long',current='0',requirement_id='r1',evidence='source',
                         choices=[dict(value='0'),dict(value='1')])}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value=4)}
        config={'CONFIG.CH2_WIDTH':4}
        repairs=await self.s.resolve_disabled_dependencies(config)
        self.assertEqual(config['CONFIG.DUAL'],'1')
        self.assertTrue(repairs[0]['derived'])

    def test_exact_duplicate_nested_field_joins_unique_selected_container_majority(self):
        self.s.requirements={'r1':'endpoint','r2':'device id','r3':'bar enabled'}
        self.s.candidates={
            'r1.a':dict(key='CONFIG.A',subkey='PORT_TYPE',requirement_id='r1'),
            'r1.b':dict(key='CONFIG.B',subkey='PORT_TYPE',requirement_id='r1'),
            'r2.b':dict(key='CONFIG.B',subkey='DEVICE_ID',requirement_id='r2'),
            'r3.b':dict(key='CONFIG.B',subkey='BAR_ENABLE',requirement_id='r3')}
        self.s.selections={
            ('r1','r1.a'):dict(candidate_id='r1.a',value='Endpoint'),
            ('r2','r2.b'):dict(candidate_id='r2.b',value='9038'),
            ('r3','r3.b'):dict(candidate_id='r3.b',value='1')}
        repairs=self.s.coalesce_nested_containers()
        self.assertNotIn(('r1','r1.a'),self.s.selections)
        self.assertEqual(self.s.selections[('r1','r1.b')]['value'],'Endpoint')
        self.assertEqual(repairs[0]['evidence_scope'],'exact_duplicate_field_selected_container_majority')
        # A tie is inconclusive and must not rewrite the user's source choice.
        self.s.selections={('r1','r1.a'):dict(candidate_id='r1.a',value='Endpoint'),
                           ('r2','r2.b'):dict(candidate_id='r2.b',value='9038')}
        self.assertEqual(self.s.coalesce_nested_containers(),[])
        self.assertIn(('r1','r1.a'),self.s.selections)

    async def test_explicit_source_label_beats_a_digit_in_its_name(self):
        self.s.requirements={'r1':'enable QMODE-4 mode'}
        row=dict(key='CONFIG.A',type='long',choices=[dict(value='1',label='QMODE4'),dict(value='4',label='100QMODE4')])
        self.s.register_candidates([row],'r1','source')
        self.assertEqual(self.s.text_choice(row,self.s.requirements['r1']),'1')
        with self.assertRaisesRegex(ValueError,'Requested source label'):
            await self.select(value='4')
        await self.select(value='1')

    async def test_unique_enum_suffix_named_by_task_is_required(self):
        self.s.requirements={'r1':'PCIe x2 on a quad shared with 10GbE'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.MODE',requirement_id='r1',evidence='source',
            choices=[dict(value='PCIe0_x2',label=''),dict(value='PCIe0_x2_10GbE',label='')])}
        with self.assertRaisesRegex(ValueError,'distinguishing task terms 10gbe'):
            await self.select(value='PCIe0_x2')
        await self.select(value='PCIe0_x2_10GbE')

    async def test_t6_soft_rule_advises_once_then_accepts_unchanged_resubmission(self):
        self.s.requirements={'r1':'PCIe x2 on a quad shared with 10GbE'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.MODE',requirement_id='r1',evidence='source',
            choices=[dict(value='PCIe0_x2',label=''),dict(value='PCIe0_x2_10GbE',label='')])}
        with self.assertRaisesRegex(ValueError,'resubmit it unchanged'):
            await self.select(value='PCIe0_x2')
        reply=await self.select(value='PCIe0_x2')
        self.assertIn('advisory_override',[r['repair'] for r in reply.get('selection_repairs',[])])
        self.assertEqual([o['rule'] for o in self.s.advisory_overrides],['S3'])

    async def test_t6_changed_value_is_a_new_first_occurrence(self):
        self.s.requirements={'r1':'PCIe x2 on a quad shared with 10GbE'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.MODE',requirement_id='r1',evidence='source',
            choices=[dict(value='PCIe0_x2',label=''),dict(value='PCIe0_x4',label=''),dict(value='PCIe0_x2_10GbE',label='')])}
        with self.assertRaisesRegex(ValueError,'resubmit it unchanged'):
            await self.select(value='PCIe0_x2')
        with self.assertRaisesRegex(ValueError,'resubmit it unchanged'):
            await self.select(value='PCIe0_x4')
        self.assertEqual(self.s.advisory_overrides,[])

    async def test_t6_hard_rejection_is_never_overridden(self):
        self.s.requirements={'r1':'select mode C'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.MODE',requirement_id='r1',evidence='source',
            choices=[dict(value='A',label=''),dict(value='B',label='')])}
        for _ in range(3):
            with self.assertRaisesRegex(ValueError,'does not accept'):
                await self.select(value='C')
        self.assertEqual(self.s.advisory_overrides,[])

    async def test_t6_boolean_spelling_follows_the_choice_list(self):
        self.s.requirements={'r1':'enable the feature'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.FEATURE_EN',requirement_id='r1',evidence='source',type='string',
            choices=[dict(value='false',label=''),dict(value='true',label='')])}
        reply=await self.select(value='True')
        self.assertEqual(reply['selected'][0]['value'],'true')
        self.assertIn('normalized_boolean_spelling',[r['repair'] for r in reply.get('selection_repairs',[])])

    async def test_equal_enum_word_matches_remain_a_model_choice(self):
        self.s.requirements={'r1':'select x2 mode'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.MODE',requirement_id='r1',evidence='source',
            choices=[dict(value='x2_A',label=''),dict(value='x2_B',label='')])}
        await self.select(value='x2_A')

    async def test_punctuated_task_matches_concatenated_enum(self):
        self.s.requirements={'r1':'AXI interface: AXI4-Lite'}
        row=dict(key='CONFIG.PROTOCOL',requirement_id='r1',evidence='source',
                 choices=[dict(value='AXI4',label=''),dict(value='AXI4LITE',label='')])
        self.s.candidates={'r1.p1':row}
        self.assertEqual(self.s.text_choice(row,self.s.requirements['r1']),'AXI4LITE')
        await self.select(value='AXI4LITE')

    async def test_cell_name_index_does_not_override_single_port_boolean(self):
        self.s.requirements={'r1':'cell:bram_dma_0 single BRAM port'}
        row=dict(key='CONFIG.SINGLE_PORT_BRAM',requirement_id='r1',evidence='source',
                 choices=[dict(value='0',label='false'),dict(value='1',label='true')])
        self.s.candidates={'r1.p1':row}
        self.assertIsNone(self.s.text_choice(row,self.s.requirements['r1']))
        await self.select(value='1')

    async def test_unknown_candidate_cannot_be_written(self):
        await self.describe()
        with self.assertRaisesRegex(ValueError, 'Candidate ID'):
            await self.select('invented')
        self.assertFalse(self.s.selections)

    async def test_candidate_is_bound_to_requirement(self):
        await self.describe()
        with self.assertRaisesRegex(ValueError, 'Candidate ID'):
            await self.select(rid='r2')

    async def test_invalid_integer_rejected_before_mutation(self):
        await self.describe()
        with self.assertRaisesRegex(ValueError, 'integer'):
            await self.select(value='[exec touch /tmp/ipcfg_bad]')
        self.assertFalse(self.s.selections)

    async def test_generated_tcl_applies_values_and_pins_original_text(self):
        await self.describe();await self.select()
        result = await self.s.request({'op':'apply'})
        self.assertEqual(result['stage'], 'configured')
        self.assertIn('8\nrequested feature\nCFG:OK', self.s.generated_result)
        self.assertTrue((await self.select(value=9))['operation_rejected'])
        self.assertTrue((await self.s.request({'op':'finish'}))['full'])

    async def test_duplicate_rows_register_one_candidate_and_disabled_rank_last(self):
        row = dict(key='CONFIG.A', label='Requested feature', type='long', default='0', range='RANGE_NUM:0:32')
        self.s.guard.database_request = lambda command: {'candidates': [dict(row), dict(row), dict(row, key='CONFIG.B')]}
        result = await self.describe()
        self.assertEqual([c['key'] for c in result['candidates']], ['CONFIG.A', 'CONFIG.B'])
        rows = [dict(key='CONFIG.A', enabled=False), dict(key='CONFIG.B'), dict(key='CONFIG.C', enabled=True)]
        self.assertEqual([r['key'] for r in StructuredSession.enabled_first(rows)], ['CONFIG.B', 'CONFIG.C', 'CONFIG.A'])

    def test_direction_setting_outranks_topology_controller(self):
        rows=[dict(key='CONFIG.C_IS_DUAL'),dict(key='CONFIG.C_ALL_OUTPUTS_2'),
              dict(key='CONFIG.C_ALL_INPUTS_2')]
        ranked=StructuredSession.requirement_role_first(rows,'channel 2 drives four LEDs')
        self.assertEqual([r['key'] for r in ranked],
                         ['CONFIG.C_ALL_OUTPUTS_2','CONFIG.C_IS_DUAL','CONFIG.C_ALL_INPUTS_2'])
        ranked=StructuredSession.requirement_role_first(rows,'channel 2 reads four switches')
        self.assertEqual(ranked[0]['key'],'CONFIG.C_ALL_INPUTS_2')

    async def test_failed_apply_allows_one_reselection_for_the_same_requirement(self):
        row = dict(label='Requested feature', type='long', default='0', range='RANGE_NUM:0:32')
        self.s.guard.database_request = lambda command: {'candidates': [dict(row, key='CONFIG.B'), dict(row, key='CONFIG.A')]}
        self.s.configure_replies = ['CFG:FIX failures {CONFIG.B} saved SAVE:OK evidence e']
        result = await self.describe()
        wrong = next(c['id'] for c in result['candidates'] if c['key'] == 'CONFIG.B')
        right = next(c['id'] for c in result['candidates'] if c['key'] == 'CONFIG.A')
        await self.select(wrong)
        failed = await self.s.request({'op':'apply'})
        self.assertEqual((failed['stage'], failed['next_op']), ('apply_failed', 'select_or_finish'))
        selected = await self.select(right)
        self.assertEqual([s['key'] for s in selected['selected']], ['CONFIG.A'])
        self.assertEqual((await self.s.request({'op':'apply'}))['stage'], 'configured')
        self.assertTrue((await self.select(wrong))['operation_rejected'])

    async def test_second_failed_apply_saves_verified_partial_and_keeps_vivado_open(self):
        await self.describe(); await self.select()
        self.s.configure_replies = [
            'CFG:FIX failures {{r1.r1.p1 actual 0 wanted 8}} saved SAVE:OK evidence e',
            'CFG:FIX failures {{r1.r1.p1 actual 0 wanted 8}} saved SAVE:OK evidence e']
        first = await self.s.request({'op':'apply'})
        self.assertEqual(first['stage'], 'apply_failed')
        second = await self.s.request({'op':'apply'})
        self.assertTrue(second['apply_retry_exhausted'])
        self.assertFalse(second['session_closed'])
        self.assertTrue(second['session_preserved'])
        self.assertEqual(second['stage'], 'finished')
        self.assertEqual(second['next_op'], 'begin')
        self.assertFalse(second['full'])

    def test_selection_equal_to_current_value_points_at_differing_alternatives(self):
        self.s.requirements={'r1':'register interface on','r2':'width 8'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.CTRL_REG_IF',requirement_id='r1',type='bool',current='true',label='Controller registers'),
                           'r1.p2':dict(key='CONFIG.PHY_REG_IF',requirement_id='r1',type='bool',current='false',label='PHY registers'),
                           'r2.p1':dict(key='CONFIG.WIDTH',requirement_id='r2',type='long',current='4')}
        selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value=True),('r2','r2.p1'):dict(candidate_id='r2.p1',value='8')}
        found=self.s.unchanged_selections(selections,{'r1','r2'})
        self.assertEqual([f['requirement_id'] for f in found],['r1'])
        self.assertEqual([a['key'] for a in found[0]['alternatives']],['CONFIG.PHY_REG_IF'])
        selections[('r1','r1.p2')]=dict(candidate_id='r1.p2',value=True)
        self.assertEqual(self.s.unchanged_selections(selections,{'r1'}),[])

    async def test_shared_multiword_boolean_feature_adds_unique_off_sibling(self):
        self.s.requirements={'r1':'register interface on'}
        self.s.candidates={
            'r1.p1':dict(key='CONFIG.CSI_CONTROLLER_REG_IF',requirement_id='r1',type='bool',
                         label='Controller Register Interface',current='true',enabled=True),
            'r1.p2':dict(key='CONFIG.DPY_EN_REG_IF',requirement_id='r1',type='bool',
                         label='PHY Register Interface',current='false',enabled=True),
            'r1.p3':dict(key='CONFIG.CSI_EMB_NON_IMG',requirement_id='r1',type='bool',
                         label='Embedded non-image Interface',current='false',enabled=True),
            'r1.p4':dict(key='CONFIG.C_CSI_OPT1_REGS',requirement_id='r1',type='bool',
                         label='Remove Debug registers',current='false',enabled=True)}
        reply=await self.s.request(dict(op='select',selections=[dict(
            requirement_id='r1',candidate_id='r1.p1',value=True)]))
        self.assertEqual({x['key'] for x in reply['selected']},
                         {'CONFIG.CSI_CONTROLLER_REG_IF','CONFIG.DPY_EN_REG_IF'})
        self.assertIn('added_shared_boolean_feature',
                      {x['repair'] for x in reply['selection_repairs']})

    async def test_shared_boolean_feature_respects_interface_qualifier(self):
        self.s.requirements={'r1':'controller register interface on'}
        self.s.candidates={
            'r1.p1':dict(key='CONFIG.CSI_CONTROLLER_REG_IF',requirement_id='r1',type='bool',
                         label='Controller Register Interface',current='true',enabled=True),
            'r1.p2':dict(key='CONFIG.DPY_EN_REG_IF',requirement_id='r1',type='bool',
                         label='PHY Register Interface',current='false',enabled=True)}
        reply=await self.s.request(dict(op='select',selections=[dict(
            requirement_id='r1',candidate_id='r1.p1',value=True)]))
        self.assertEqual([x['key'] for x in reply['selected']],['CONFIG.CSI_CONTROLLER_REG_IF'])

    def test_validation_findings_name_disabled_and_rejected_fields(self):
        self.s.candidates={'r1.p1':dict(key='CONFIG.D',subkey='BAR_EN',requirement_id='r1'),
                           'r2.p1':dict(key='CONFIG.D',subkey='PIN3',requirement_id='r2')}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value='1'),('r2','r2.p1'):dict(candidate_id='r2.p1',value='x')}
        reply=("CFG:FIX failures {...} ERROR: Validation failed for parameter 'D': 'An attempt to modify the value of disabled parameter "
               "'BAR_EN' from '0' to '1' has been ignored\nValidation failed for parameter 'PIN3(PIN3)' with current value 'x' is not legal")
        found=self.s.validation_findings(reply)
        self.assertEqual(found['disabled_writes'],[dict(field='BAR_EN',requirement_ids=['r1'])])
        self.assertEqual(found['rejected_fields'][0]['requirement_ids'],['r2'])
        self.assertIn('not legal',found['rejected_fields'][0]['detail'])

    async def test_begin_retains_task_values_missing_from_requirements(self):
        terms = StructuredSession.uncovered_task_terms
        self.assertEqual(terms('24-bit GPIO: default 0x1, 4-lane link', ['default 0x1', '4 active lanes'], 'GPIO'), ['24-bit'])
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        begin = dict(op='begin', ip='generic', cell='cell_0', part='part', task='generic with 24-bit width', requirements=['generic'])
        reply = await s.request(begin)
        self.assertIn('generic with 24-bit width', s.requirements.values())
        self.assertEqual(reply['added_task_requirements'], ['generic with 24-bit width'])
        self.assertNotEqual(reply['stage'], 'new')

    def test_source_declared_enum_covers_punctuation_equivalent_requirement(self):
        self.s.requirements={'r1':'4096-deep line buffer','r2':'C-PHY'}
        self.s.added_requirements=['C-PHY']
        self.s.candidates={'r1.p1':dict(key='CONFIG.C_PHY_MODE',requirement_id='r1',
            choices=[dict(value='CPHY',label=''),dict(value='DPHY',label='')])}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value='CPHY')}
        covered=self.s.scalar_covered()
        self.assertEqual(covered['r2']['covered_by'],['r1'])
        self.assertEqual(covered['r2']['fields'],[dict(key='CONFIG.C_PHY_MODE')])

    def test_cross_requirement_literal_coverage_requires_exact_declared_choice(self):
        self.s.requirements={'r1':'controller type','r2':'LPDDR5_SDRAM'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.TYPE',requirement_id='r1',
            choices=[dict(value='LPDDR5',label='LPDDR5')])}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value='LPDDR5')}
        self.assertNotIn('r2',self.s.scalar_covered())
        self.s.requirements['r2']='5 lanes'
        self.s.candidates['r1.p1']['choices']=[dict(value='5',label='five')]
        self.s.selections[('r1','r1.p1')]['value']='5'
        self.assertNotIn('r2',self.s.scalar_covered())

    def test_out_of_range_finding_points_to_returned_candidate(self):
        self.s.candidates={'r1.p1':dict(key='CONFIG.D',subkey='WIDTH'),'r2.p1':dict(key='CONFIG.D',subkey='KIND')}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value='32')}
        reply=("CFG:FIX ERROR: Validation failed for parameter 'D': 'Value 'NA' is out of the range for parameter "
               "'D KIND(KIND)' for BD Cell 'sub_0' . Valid values are - X1, X2\n")
        found=self.s.validation_findings(reply)
        self.assertEqual(found['rejected_fields'][0]['field'],'KIND')
        self.assertEqual(found['rejected_fields'][0]['returned_candidates'],['r2.p1'])
        self.assertEqual(found['rejected_fields'][0]['valid_values'],['X1','X2'])
        self.assertEqual([choice['value'] for choice in self.s.candidates['r2.p1']['choices']],['X1','X2'])
        self.assertEqual(self.s.candidates['r2.p1']['choices_scope'],'current_configuration_validation_error')
        self.assertIn('Valid values are - X1, X2',found['rejected_fields'][0]['detail'])
        self.assertIn('did not select',found['rejected_note'])

    async def test_live_valid_enum_suffix_carries_immutable_intent_coverage(self):
        self.s.requirements={'r1':'LPDDR5 controller type'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.D',subkey='CONTROLLER',requirement_id='r1',evidence='source')}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value='LPDDR5')}
        self.s.validation_findings("CFG:FIX ERROR: Validation failed for parameter 'CONTROLLER(CONTROLLER)' "
                                   "with current value 'LPDDR5'. Valid values are - LPDDR5_SDRAM, DDR5_SDRAM")
        self.s.apply_attempts=1
        await self.s.request(dict(op='select',selections=[dict(
            requirement_id='r1',candidate_id='r1.p1',value='LPDDR5_SDRAM')]))
        selected=self.s.selections[('r1','r1.p1')]
        self.assertEqual(selected['covers'],['LPDDR5'])
        self.assertIn('Vivado current-configuration validation',selected['grounding'])

    def test_out_of_range_parameter_is_rejected_key(self):
        reply = ("CFG:FIX failures {{apply reason {CONFIGURE_FAIL:UNKNOWN:ERROR: [IP_Flow 19-3461] Value '1.5' is out of the range "
                 "for parameter 'Ref Clock(REF_CLK)' for IP 'x' . Valid values are - 1.0, 2.0}}} saved SAVE:OK evidence e")
        self.assertEqual(self.s.rejected_keys(reply, {'CONFIG.MODE': '4', 'CONFIG.REF_CLK': '1.5'}), ['CONFIG.REF_CLK'])

    def test_context_preflight_out_of_range_is_rejected_key(self):
        reply = 'CFG:FIX failures {{apply reason {VALUE_FAIL:VAL_OUT_OF_RANGE:NUM_MCP=1 legal=0..0}}}'
        config = {'CONFIG.NUM_MCP': '1', 'CONFIG.NUM_NSI': '2'}
        self.assertEqual(self.s.rejected_keys(reply, config), ['CONFIG.NUM_MCP'])

    async def test_rejected_parameter_is_withheld_so_other_writes_apply(self):
        row = dict(label='Requested feature', type='long', default='0', range='RANGE_NUM:0:32')
        self.s.guard.database_request = lambda command: {'candidates': [dict(row, key='CONFIG.A'), dict(row, key='CONFIG.B')]}
        self.s.configure_replies = ["CFG:FIX failures {{apply reason {CONFIGURE_FAIL: Validation failed for parameter 'B(B)' bad}}} saved SAVE:OK evidence e",
                                    'CFG:OK cell /cell_0 verified 1',
                                    "CFG:FIX failures {{apply reason {CONFIGURE_FAIL: Validation failed for parameter 'B(B)' still_bad}}} saved SAVE:OK evidence e"]
        result = await self.describe()
        ids = {c['key']: c['id'] for c in result['candidates']}
        await self.s.request(dict(op='select', selections=[dict(requirement_id='r1', candidate_id=ids['CONFIG.A'], value=8),
                                                           dict(requirement_id='r1', candidate_id=ids['CONFIG.B'], value=3)]))
        result = await self.s.request({'op':'apply'})
        self.assertEqual(result['withheld_keys'], ['CONFIG.B'])
        self.assertEqual(result['stage'], 'apply_failed')
        self.assertEqual(self.s.apply_attempts, 1)
        self.assertEqual(result['dynamic_range_retry']['status'], 'rejected')
        self.assertEqual(result['partial_checkpoint'], 'SAVE:OK')
        self.assertEqual([c.split('\n')[:2] for c in self.s.configured], [['8', '3'], ['8', 'requested feature'], ['3', 'requested feature']])

    async def test_deferred_parameter_retries_after_legal_subset_changes_ranges(self):
        row = dict(label='Requested feature', type='long', default='0', range='RANGE_NUM:0:32')
        self.s.guard.database_request = lambda command: {'candidates': [dict(row, key='CONFIG.A'), dict(row, key='CONFIG.B')]}
        self.s.configure_replies = ["CFG:FIX failures {{apply reason {CONFIGURE_FAIL: Validation failed for parameter 'B(B)' initial_range}}} saved SAVE:OK evidence e",
                                    'CFG:OK cell /cell_0 verified 1',
                                    'CFG:OK cell /cell_0 verified 2']
        result = await self.describe()
        ids = {c['key']: c['id'] for c in result['candidates']}
        await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id=ids['CONFIG.A'], value=8),
            dict(requirement_id='r1', candidate_id=ids['CONFIG.B'], value=3)]))
        result = await self.s.request({'op':'apply'})
        self.assertEqual(result['stage'], 'configured')
        self.assertEqual(result['dynamic_range_retry'],
                         {'keys':['CONFIG.B'], 'live_ranges_refreshed':True, 'status':'passed'})
        self.assertNotIn('withheld_keys', result)

    async def test_legal_subset_recovery_peels_multiple_rejected_layers(self):
        row = dict(label='Requested feature', type='long', default='0', range='RANGE_NUM:0:32')
        self.s.guard.database_request = lambda command: {'candidates': [
            dict(row, key='CONFIG.A'), dict(row, key='CONFIG.B'), dict(row, key='CONFIG.C')]}
        self.s.configure_replies = [
            'CFG:FIX failures {{apply reason {VALUE_FAIL:VAL_OUT_OF_RANGE:B=3 legal=0..0}}} saved SAVE:OK evidence e',
            "CFG:FIX failures {{apply reason {CONFIGURE_FAIL: Validation failed for parameter 'C(C)' bad}}} saved SAVE:OK evidence e",
            'CFG:OK cell /cell_0 verified 1',
            "CFG:FIX failures {{apply reason {CONFIGURE_FAIL: Validation failed for parameter 'C(C)' still_bad}}} saved SAVE:OK evidence e"]
        self.s.candidates = {f'r1.p{i}': dict(row, key=f'CONFIG.{name}', requirement_id='r1', evidence='source')
                             for i, name in enumerate('ABC', 1)}
        ids = {c['key']: cid for cid, c in self.s.candidates.items()}
        await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id=ids['CONFIG.A'], value=8),
            dict(requirement_id='r1', candidate_id=ids['CONFIG.B'], value=3),
            dict(requirement_id='r1', candidate_id=ids['CONFIG.C'], value=4)]))
        result = await self.s.request({'op':'apply'})
        self.assertEqual(result['withheld_keys'], ['CONFIG.B', 'CONFIG.C'])
        self.assertEqual([r['keys'] for r in result['legal_subset_recovery']],
                         [['CONFIG.A', 'CONFIG.C'], ['CONFIG.A']])
        self.assertEqual(result['partial_checkpoint'], 'SAVE:OK')

    async def test_a_requirement_naming_the_selected_setting_is_already_met_and_board_bindings_are_never_alternatives(self):
        """ced-gen_E2 (Gemma-31B): "all outputs" selected All Outputs, already 1 on
        the ZC702 LED binding; the pause offered GPIO_BOARD_INTERFACE and the
        model unbound the board (Custom) where the verified answer is already_satisfied."""
        boolean=[dict(value='0',label='false'),dict(value='1',label='true')]
        self.s.requirements={'r1':'all outputs','r2':'GPIO enabled'}
        self.s.candidates={
            'r1.p1':dict(key='CONFIG.C_ALL_OUTPUTS',requirement_id='r1',type='long',current='1',label='All Outputs',choices=boolean),
            'r1.p2':dict(key='CONFIG.GPIO_BOARD_INTERFACE',requirement_id='r1',type='string',current='leds_4bits',label='GPIO BOARD INTERFACE'),
            'r2.p1':dict(key='CONFIG.C_INTERRUPT_PRESENT',requirement_id='r2',type='long',current='1',label='Enable Interrupt',choices=boolean),
            'r2.p2':dict(key='CONFIG.GPIO_BOARD_INTERFACE',requirement_id='r2',type='string',current='leds_4bits',label='GPIO BOARD INTERFACE'),
            'r2.p3':dict(key='CONFIG.C_IS_DUAL',requirement_id='r2',type='long',current='0',label='Enable Dual Channel',choices=boolean)}
        self.assertEqual(self.s.unchanged_selections({('r1','r1.p1'):dict(candidate_id='r1.p1',value='1')},{'r1'}),[])
        found=self.s.unchanged_selections({('r2','r2.p1'):dict(candidate_id='r2.p1',value='1')},{'r2'})
        self.assertEqual([a['key'] for a in found[0]['alternatives']],['CONFIG.C_IS_DUAL'])

    async def test_a_board_interface_is_written_only_when_the_requirement_asks_for_it(self):
        """ced-gen_H3 (Haiku) set the DDR4 controller's board interface to Custom
        while chasing a 125 MHz clock it does not own."""
        self.s.requirements={'r1':'125 MHz input clock','r2':'use a custom board interface for the GPIO'}
        row=lambda rid: dict(key='CONFIG.C0_CLOCK_BOARD_INTERFACE',requirement_id=rid,type='string',current='user_si570_sysclk',
                             label='C0 CLOCK BOARD INTERFACE',evidence='source',enabled=True,
                             choices=[dict(value='Custom',label='Custom'),dict(value='user_si570_sysclk',label='user_si570_sysclk')])
        self.s.candidates={'r1.p1':row('r1'),'r2.p1':row('r2')}
        with self.assertRaisesRegex(ValueError,'binds this cell to a board interface'):
            await self.s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='r1.p1',value='Custom')]))
        result=await self.s.request(dict(op='select',selections=[dict(requirement_id='r2',candidate_id='r2.p1',value='Custom')]))
        self.assertEqual([row['key'] for row in result['selected']],['CONFIG.C0_CLOCK_BOARD_INTERFACE'])

    async def test_first_apply_pauses_on_no_op_selection_with_alternatives(self):
        self.s.requirements={'r1':'register interface on'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.CTRL_REG_IF',requirement_id='r1',type='bool',current='true',label='Controller registers'),
                           'r1.p2':dict(key='CONFIG.PHY_REG_IF',requirement_id='r1',type='bool',current='false',label='PHY registers')}
        self.s.selections={('r1','r1.p1'):dict(candidate_id='r1.p1',value=True)}
        self.s.requirements['r2']='use LINK4 mode'
        self.s.candidates['r2.p1']=dict(key='CONFIG.MODE',requirement_id='r2',type='long',current='1',
                                       choices=[dict(value='1',label='LINK4'),dict(value='4',label='XLINK4')])
        self.assertEqual(self.s.unchanged_selections({('r2','r2.p1'):dict(candidate_id='r2.p1',value='1')},{'r2'}),[])
        del self.s.requirements['r2'], self.s.candidates['r2.p1']
        result=await self.s.request({'op':'apply'})
        self.assertTrue(result['apply_paused'])
        self.assertEqual(result['next_op'],'select')
        self.assertEqual(result['unchanged_selections'][0]['alternatives'][0]['key'],'CONFIG.PHY_REG_IF')
        self.assertEqual(self.s.apply_attempts,0)
        try:
            result=await self.s.request({'op':'apply'})
        except Exception as exc:
            result={'error':str(exc)}
        self.assertNotIn('apply_paused',result)

    def test_inner_dictionary_selection_keeps_only_known_requested_keys(self):
        row=dict(key='CONFIG.D',subkey='PORT1',shape='dictionary',fields=['ENABLE','IO','IO_TYPE'],default='ENABLE 0 IO A IO_TYPE MIO')
        self.assertEqual(self.s.inner_dictionary_value(row,{'enable':True,'IO':'PIN 4:5'}),'ENABLE 1 IO {PIN 4:5}')
        self.assertEqual(self.s.inner_dictionary_value(row,'ENABLE 1 IO B'),'ENABLE 1 IO B')
        with self.assertRaisesRegex(ValueError,'keys ENABLE IO IO_TYPE'):
            self.s.inner_dictionary_value(row,'1')
        with self.assertRaisesRegex(ValueError,'Unknown key SPEED'):
            self.s.inner_dictionary_value(row,{'SPEED':1})

    def test_stray_closing_brackets_are_repaired_but_other_json_errors_are_not(self):
        from guard_mcp import parse_request
        request,removed=parse_request('{"op":"select","selections":[{"requirement_id":"r1","candidate_id":"r1.p1","value":{"indices":{"0":"1"}}}},{"requirement_id":"r2","candidate_id":"r2.p1","value":2}]}')
        self.assertEqual(removed,1)
        self.assertEqual([s['requirement_id'] for s in request['selections']],['r1','r2'])
        with self.assertRaises(ValueError):
            parse_request('{"op":"apply",}')
        with self.assertRaises(ValueError):
            parse_request('{"op":"apply"}}}}}')

    async def test_exhausted_requirement_does_not_block_applying_the_others(self):
        self.s.requirements['r2']='second feature'
        await self.describe()
        await self.select()
        with self.assertRaisesRegex(ValueError,'Unresolved'):
            await self.s.request({'op':'apply'})
        for i in range(4):
            result=await self.s.request(dict(op='describe',requirement_id='r2',query='nothing like this'+' again'*i))
        self.assertEqual(result['exhausted_requirements'],['r2'])
        self.assertEqual(result['next_op'],'apply')
        result=await self.s.request({'op':'apply'})
        self.assertEqual(result['unapplied_requirements'],[dict(id='r2',text='second feature')])
        result=await self.s.request({'op':'finish'})
        self.assertFalse(result['full'])
        self.assertEqual(result['unapplied_requirements'][0]['id'],'r2')

    async def test_exhausted_requirement_does_not_close_while_others_await_selection(self):
        self.s.requirements['r2']='second feature'
        await self.s.request(dict(op='describe',requirement_id='r2',query='width'))
        for i in range(5):
            result=await self.describe(i)
        self.assertTrue(result['describe_exhausted'])
        self.assertEqual(result['awaiting_selection'],['r2'])
        self.assertIn('Select now for r2',result['note'])
        await self.describe(5)
        result=await self.describe(6)
        self.assertFalse(result['session_closed'])   # finish keeps Vivado open
        self.assertTrue(result['session_preserved'])

    async def test_identical_query_returns_earlier_candidates_without_budget(self):
        first=await self.describe()
        again=await self.describe()
        self.assertTrue(again['repeated_query'])
        self.assertEqual([c['id'] for c in again['candidates']],[c['id'] for c in first['candidates']])
        self.assertEqual(self.s.describe_counts['r1'],1)
        await self.describe()
        self.assertNotIn('repeated_query',await self.describe())
        self.assertEqual(self.s.describe_counts['r1'],2)

    async def test_discovery_retry_is_bounded(self):
        for i in range(3):await self.describe(i)
        result=await self.describe(3)
        self.assertTrue(result['describe_exhausted'])
        self.assertEqual(result['candidates'][0]['id'],'r1.p1')
        result=await self.describe(4)
        self.assertFalse(result['full'])
        self.assertFalse(result['session_closed'])   # finish keeps Vivado open
        self.assertTrue(result['session_preserved'])
        self.assertEqual(result['native_receipt'],'SAVE:OK')
        self.assertEqual(self.s.describe_counts['r1'],3)

    async def test_values_are_tcl_data_not_code(self):
        malicious='[error INJECTED]; $env(HOME) { unmatched\nline'
        encoded=subprocess.check_output(['tclsh'], input='puts '+tdict({'text':malicious})+'\n', text=True)
        self.assertEqual(dict_tcl(encoded)['text'], malicious)

    async def test_live_shape_and_ownership_come_from_current_cell(self):
        self.s.live_values={'CONFIG.A':'true,false,false'}
        self.s.live_sources={'CONFIG.A':'PROPAGATED'}
        result=await self.describe();row=result['candidates'][0]
        self.assertEqual((row['shape'],row['length'],row['element_type']),('vector',3,'boolean'))
        self.assertEqual((row['value_src'],row['write_mode']),('PROPAGATED','assert'))

    async def test_assertions_preserve_intent_but_do_not_enter_writes(self):
        self.s.live_sources={'CONFIG.A':'PROPAGATED'}
        await self.describe();await self.select(value=8)
        self.assertEqual(await self.s.merge_selections(self.s.selections),{})
        self.assertFalse(self.s.pending())
        self.assertEqual(next(iter(self.s.selections.values()))['mode'],'assert')

    async def test_boolean_candidate_cannot_accept_requested_frequency(self):
        await self.describe();self.s.candidates['r1.p1']['type']='bool'
        with self.assertRaisesRegex(ValueError,'Boolean candidate'):
            await self.select(value=250)
        self.assertFalse(self.s.selections)

    async def test_indexed_scalar_is_rejected_before_native_vector_reads(self):
        await self.describe()
        with self.assertRaisesRegex(ValueError,'is a scalar'):
            await self.select(value={'indices':{'0':250}})
        self.assertFalse(self.s.vector_bases)

    async def test_explicit_assertion_is_kept_separate_from_user_writes(self):
        await self.describe()
        await self.s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='r1.p1',value=8,mode='assert')]))
        self.assertEqual(await self.s.merge_selections(self.s.selections),{})

    async def test_named_original_intent_overrides_vendor_only_hint_and_bad_cell(self):
        s=Session(Guard(),None,'session','/scratch/proj/.vivado-ai/log')
        task='Add an Example Streaming Queue named queue_0 with eight data bytes.'
        seen=[]
        original=s.discover_ip
        async def discover(hint):
            seen.append(hint);return await original(hint)
        s.discover_ip=discover
        await s.request(dict(op='begin',ip='Vendor',cell='Example Streaming Queue',part='part',task=task,requirements=['eight data bytes']))
        self.assertEqual((seen,s.cell),(['Example Streaming Queue'],'queue_0'))
        self.assertEqual(s.task,task)

    async def test_repeat_choose_after_selection_returns_current_action(self):
        result=await self.s.request(dict(op='choose_ip',candidate_id='i1'))
        self.assertEqual(result['stage'],'discovering')
        self.assertEqual(result['next_op'],'describe')

    async def test_different_candidate_cannot_replace_live_accepted_choice(self):
        self.s.ip_candidates['i2']=dict(vlnv='vendor:lib:other:1.0')
        self.s.candidate_validation=dict(status='inconclusive',matched_requirements=[],
                                         writable_keys=[],trial_write=None)
        original_vlnv=self.s.vlnv
        result=await self.s.request(dict(op='choose_ip',candidate_id='i2'))
        self.assertTrue(result['selection_locked'])
        self.assertEqual(result['attempted_candidate_id'],'i2')
        self.assertEqual(self.s.vlnv,original_vlnv)
        self.assertFalse(self.s.reselection_used)
        self.assertNotIn('choose_ip',self.s.allowed_operations())

    async def test_finished_session_does_not_restart_or_lose_partial_state(self):
        partial=await self.s.request(dict(op='finish'))
        self.assertEqual(await self.s.request(dict(op='describe',requirement_id='r1')),partial)
        self.assertEqual(await self.s.request(dict(op='begin',ip='new',cell='other',part='part',requirements=['other'])),partial)

    async def test_ip_unsupported_on_part_is_offered_with_a_part_change(self):
        s=Session(Guard(),None,'session','/scratch/proj/.vivado-ai/log')
        calls=[]
        async def native(expression, prefix=''):
            calls.append(expression)
            if expression.startswith('[lite::catalog'):
                script=('puts [list CATALOG:CANDIDATES shown [list [dict create vlnv v:l:near:1.0 matched_terms x]] '
                        'unavailable [list [dict create vlnv v:l:other:1.0 display_name {other ip} supported_count 9 example_parts {p-one p-two}]]]\n')
                return subprocess.check_output(['tclsh'], input=script, text=True).strip()
            if expression.startswith('[lite::set_part'):
                return 'PART:OK part p-two previous part'
            return await Session.native(s, expression, prefix)
        s.native=native
        result=await s.request(dict(op='begin',ip='other thing',cell='cell_0',part='part',requirements=['feature']))
        unavailable=[c for c in result['ip_candidates'] if c['id']=='u1'][0]
        self.assertEqual((unavailable['vlnv'],unavailable['example_parts']),('v:l:other:1.0',['p-one','p-two']))
        self.assertIn('part_note',result)
        with self.assertRaisesRegex(ValueError,'example_parts'):
            await s.request(dict(op='choose_ip',candidate_id='u1'))
        s.task='an ip for the board'
        with self.assertRaisesRegex(ValueError,'nor approves'):
            await s.request(dict(op='choose_ip',candidate_id='u1',part='p-two'))
        self.assertFalse(any(c.startswith('[lite::set_part') for c in calls))
        self.assertTrue(StructuredSession.part_change_approved('swapping the project part is approved'))
        s.task='an Other IP for the board'
        result=await s.request(dict(op='choose_ip',candidate_id='u1',part='p-two'))
        self.assertEqual(s.part,'p-two')
        self.assertEqual(result['part_change']['previous'],'part')
        self.assertTrue(any(c.startswith('[lite::set_part') for c in calls))

    async def test_failed_first_retarget_creation_scans_returned_example_parts(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        s.initialized = True
        s.part = s.original_part = 'original-part'
        s.task = 'Swapping the project part to a supported device is approved.'
        s.requirements = {'r1': 'enable requested mode'}
        s.stage = 'choosing_ip'
        s.begin_attempts = 1
        s.ip_candidates = {'u1': dict(vlnv='v:l:target:1.0', supported_on_current_part=False,
                                      example_parts=['bad-part', 'good-part'])}
        attempts = []
        async def native(expression, prefix=''):
            if expression.startswith('[lite::set_part'):
                chosen = 'good-part' if 'good-part' in expression else 'bad-part'
                s.part = chosen
                return 'PART:OK part ' + chosen + ' previous original-part'
            if expression.startswith('[lite::discard_cell'):
                return 'DISCARD:OK'
            return await Session.native(s, expression, prefix)
        async def choose(cid):
            attempts.append(s.part)
            if s.part == 'bad-part':
                raise ValueError('customizer creation failed')
            s.created_by_session = True
            s.chosen_candidate_id = cid
            s.vlnv = s.ip_candidates[cid]['vlnv']
            s.candidate_validation = dict(status='inconclusive')
            s.stage = 'discovering'
            return s.state(vlnv=s.vlnv)
        s.native = native
        s.choose_candidate = choose
        s.current_part_fit = lambda: __import__('asyncio').sleep(0, result=dict(
            matched=1, ready=1, blocked=0, requirements=1, status='complete'))
        result = await s.request(dict(op='choose_ip', candidate_id='u1', part='bad-part'))
        self.assertEqual(attempts, ['bad-part', 'good-part'])
        self.assertEqual(result['creation_recovery']['selected'], 'good-part')
        self.assertEqual(s.part, 'good-part')

    async def test_repeated_begin_after_catalog_discovery_does_not_consume_creation_retry(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        async def native(expression, prefix=''):
            if expression.startswith('[lite::catalog'):
                return 'CATALOG:CANDIDATES shown {{vlnv v:l:first:1.0} {vlnv v:l:second:1.0}}'
            return await Session.native(s, expression, prefix)
        s.native = native
        request = dict(op='begin', ip='ambiguous', cell='cell_0', part='part', requirements=['feature'])
        await s.request(request)
        self.assertEqual(s.begin_attempts, 1)
        await s.request(request)
        self.assertEqual(s.begin_attempts, 1)

    async def test_fuzzy_availability_checks_only_final_ranked_candidates(self):
        s=Session(Guard(),None,'session','/scratch/proj/.vivado-ai/log')
        checked=[]
        async def native(expression,prefix=''):
            if expression.startswith('[lite::candidate_availability'):
                checked.append(expression)
                rows=tcl_list_string([
                    tcl_list_string(['vlnv','v:l:first:1.0','status','AVAILABLE']),
                    tcl_list_string(['vlnv','v:l:second:1.0','status','WRONG_PART',
                                     'supported_count','2','example_parts',tcl_list_string(['p-one','p-two'])])])
                return tcl_list_string(['AVAILABILITY:OK','rows',rows])
            return await Session.native(s,expression,prefix)
        s.native=native
        rows=[dict(vlnv='v:l:first:1.0'),dict(vlnv='v:l:second:1.0')]
        result=await s.available_ip_candidates(rows)
        self.assertEqual(len(checked),1)
        self.assertEqual([r['vlnv'] for r in result],['v:l:first:1.0'])
        self.assertEqual(s.unavailable_ips['v:l:second:1.0']['example_parts'],['p-one','p-two'])

    def test_text_choice_and_named_value_mismatch(self):
        row=dict(choices=[dict(value='LINK0_x2'),dict(value='LINK0_x2_10GbE'),dict(value='NONE')])
        self.assertEqual(StructuredSession.text_choice(row,'link x2 (shared with 10GbE)'),'LINK0_x2_10GbE')
        self.assertIsNone(StructuredSession.text_choice(row,'something else'))
        boolean=dict(key='CONFIG.PS_CONFIG',subkey='PS_USE_CLK0',
                     choices=[dict(value='0',label='false'),dict(value='1',label='true')])
        self.assertEqual(StructuredSession.text_choice(boolean,'enable PL clock 0 at 100 MHz'),'1')
        self.assertEqual(StructuredSession.text_choice(boolean,'leave PL clock 3 disabled'),'0')
        self.s.requirements={'r1':'15 clocks','r2':'core clock at ~600 MHz'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.N',type='long'),'r2.p1':dict(key='CONFIG.F',type='float')}
        found=self.s.named_value_mismatches({('r1','r1.p1'):dict(value='12'),('r2','r2.p1'):dict(value='600.0')})
        self.assertEqual([m['requirement_id'] for m in found],['r1'])
        self.s.requirements={'r1':'enable LINK-4 mode','r2':'use XB2 encoding 8'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.M',type='long'),
                           'r2.p1':dict(key='CONFIG.E',type='long',choices=[dict(value='1',label='XB2'),dict(value='8',label='XB8')])}
        self.assertEqual(self.s.named_value_mismatches({('r1','r1.p1'):dict(value='1'),('r2','r2.p1'):dict(value='1')}),[])

    def test_numbered_instance_is_not_a_requested_scalar_value(self):
        self.s.requirements={'r1':'leave PL clock 3 disabled'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.PS11_CONFIG',subkey='PS_USE_PMCPL_CLK3',type='long')}
        self.assertEqual(self.s.named_value_mismatches({('r1','r1.p1'):dict(value='0')}),[])

    def test_single_bit_query_adds_width_terms(self):
        query = StructuredSession.semantic_query('single bit GPIO output', 'single bit GPIO output')
        self.assertIn('width 1 data width', query)

    def test_not_two_maps_to_second_instance_disable(self):
        row=dict(key='CONFIG.enable_timer2',label='Enable timer 2',
                 choices=[dict(value='0',label='false'),dict(value='1',label='true')])
        self.assertEqual(StructuredSession.text_choice(row,'not two timers'),'0')
        self.assertIn('disable second instance',StructuredSession.semantic_query('not two timers','not two timers'))

    async def test_boolean_action_rejects_index_as_value(self):
        self.s.requirements={'r1':'leave PL clock 3 disabled'}
        self.s.candidates={'r1.p1':dict(requirement_id='r1',key='CONFIG.PS11_CONFIG',subkey='PS_USE_PMCPL_CLK3',type='long',
            choices=[dict(value='0',label='false'),dict(value='1',label='true')],
            choices_scope='sibling_definition_not_current_legal_values', evidence='native metadata')}
        self.s.ids={('r1','CONFIG.PS11_CONFIG','PS_USE_PMCPL_CLK3'):'r1.p1'}
        with self.assertRaisesRegex(ValueError, 'Boolean action'):
            await self.s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='r1.p1',value=3)]))

    async def test_compound_enabled_requirement_allows_false_qualifier(self):
        self.s.requirements={'r1':'independent 32-bit memory BAR2 enabled at 256 MB'}
        self.s.candidates={'r1.p1':dict(requirement_id='r1',key='CONFIG.PS_CONFIG',subkey='PCIE0_PF0_BAR2_64BIT',type='long',
            choices=[dict(value='0',label='false'),dict(value='1',label='true')],
            choices_scope='sibling_definition_not_current_legal_values', evidence='native metadata')}
        self.s.ids={('r1','CONFIG.PS_CONFIG','PCIE0_PF0_BAR2_64BIT'):'r1.p1'}
        reply=await self.s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='r1.p1',value=0)]))
        self.assertEqual(reply['selected'][0]['value'],0)

    async def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            await self.s.request({'op':'apply','config':{'CONFIG.A':8}})

    async def test_compound_feature_preserved_verbatim(self):
        s=Session(Guard(),None,'session','/scratch/proj/.vivado-ai/log')
        text='channel 1 is 8 bits wide and output-only'
        await s.request(dict(op='begin',ip='generic',cell='cell_0',part='part',requirements=[text],task=text))
        self.assertEqual(s.requirements['r1'],text)
        self.assertEqual(s.task,text)

    async def test_nonwritable_database_candidates_are_filtered(self):
        self.s.properties = {'CONFIG.B'}
        async def fallback(expression, prefix=''):
            if expression.startswith('[lite::live_parameters'):
                return await Session.native(self.s, expression, prefix)
            if 'inspect_for' in expression:
                script='puts [list META:OK candidates [list [dict create key CONFIG.B label Native type long default 0 range RANGE_NONE]]]\n'
            else:
                script='puts [list DETAIL:OK parameters [dict create CONFIG.B [dict create help {Native purpose} help_source native]]]\n'
            return subprocess.check_output(['tclsh'], input=script, text=True).strip()
        self.s.native=fallback
        result=await self.describe()
        self.assertEqual(result['backend'],'live')
        self.assertEqual(result['candidates'][0]['key'],'CONFIG.B')
        self.assertEqual(result['candidates'][0]['help'],'Native purpose')

    async def test_unavailable_customizer_falls_back_to_packaged_parameters(self):
        guard = Guard();guard.database_config = None
        s = Session(guard, None, 'session', '/scratch/proj/.vivado-ai/log')
        xml = Path(os.environ['TMPDIR']) / 'structured-unit-component.xml'
        xml.write_text('''<spirit:component xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009"><spirit:parameters>
<spirit:parameter><spirit:name>A</spirit:name><spirit:displayName>Aux Level High</spirit:displayName>
<spirit:value spirit:format="long" spirit:resolve="user" spirit:minimum="0" spirit:maximum="1">1</spirit:value></spirit:parameter>
</spirit:parameters></spirit:component>''')
        async def native(expression, prefix=''):
            if expression.startswith('[lite::metadata'):
                raise ValueError('NATIVE_ERROR: METADATA_UNAVAILABLE:Cannot initialize customizer')
            if 'XML_FILE_NAME' in expression:
                return str(xml)
            if expression.startswith('[lite::describe'):
                return 'DETAIL:PACKAGED parameters {} missing {}'
            if expression.startswith('[lite::inspect_for'):
                return 'LIVE_FALLBACK reason none result {}'
            return await Session.native(s, expression, prefix)
        s.native = native
        await s.request(dict(op='begin', ip='generic', cell='cell_0', part='part', requirements=['auxiliary level']))
        result = await s.request(dict(op='describe', requirement_id='r1'))
        self.assertEqual(result['backend'], 'packaged_component_xml')
        self.assertIn('Cannot initialize customizer', s.metadata_fallback)
        self.assertEqual(result['candidates'][0]['key'], 'CONFIG.A')
        self.assertEqual(result['candidates'][0]['label'], 'Aux Level High')
        self.assertEqual(result['candidates'][0]['default'], '1')

    async def test_empty_discovery_reports_a_reason(self):
        guard = Guard();guard.database_config = None
        s = Session(guard, None, 'session', '/scratch/proj/.vivado-ai/log')
        await s.request(dict(op='begin', ip='generic', cell='cell_0', part='part', requirements=['feature']))
        s.api_parameters = []
        async def native(expression, prefix=''):
            if expression.startswith('[lite::inspect_for'):
                return 'META:OK candidates {}'
            return await Session.native(s, expression, prefix)
        s.native = native
        result = await s.request(dict(op='describe', requirement_id='r1'))
        self.assertEqual(result['candidates'], [])
        self.assertIn('no_candidates_reason', result)

    async def test_multiple_writes_for_one_feature_are_retained(self):
        await self.describe()
        self.s.candidates['r1.p2']=dict(self.s.candidates['r1.p1'],key='CONFIG.B')
        await self.s.request(dict(op='select',selections=[
            dict(requirement_id='r1',candidate_id='r1.p1',value=8),
            dict(requirement_id='r1',candidate_id='r1.p2',value=1)]))
        self.assertEqual(len(self.s.selections),2)
        self.assertFalse(self.s.pending())
        await self.s.request({'op':'apply'})
        self.assertIn('8\n1\nrequested feature',self.s.generated_result)

    async def test_duplicate_pair_rejected_atomically(self):
        await self.describe()
        entry=dict(requirement_id='r1',candidate_id='r1.p1',value=8)
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            await self.s.request(dict(op='select',selections=[entry,entry]))
        self.assertFalse(self.s.selections)

    async def test_complete_catalog_avoids_ranked_discovery(self):
        guard=Guard()
        original=guard.database_request
        def request(command):
            if json.loads(command)['op']=='catalog':
                return dict(status='DB:CATALOG',candidates=[dict(key='CONFIG.A',label='Feature',type='long',default='0',range='RANGE_NUM:0:32')])
            return original(command)
        guard.database_request=request
        s=Session(guard,None,'session','/scratch/proj/.vivado-ai/log')
        result=await s.request(dict(op='begin',ip='generic',cell='cell_0',part='part',requirements=['feature']))
        self.assertEqual(result['catalog'][0]['id'],'p1')
        self.assertEqual(result['next_op'],'select')
        await s.request(dict(op='select',selections=[dict(requirement_id='r1',candidate_id='p1',value=8)]))
        self.assertFalse(s.pending())

    def test_large_catalog_exposes_unique_scalar_candidate_at_begin(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        s.requirements = {'r1': 'depth of 128'}
        rows = [dict(key='CONFIG.FIFO_DEPTH', label='FIFO depth', type='long',
                     default='512', current='512', enabled=True, shape='scalar',
                     range='RANGE_LIST:16 32 64 128 256 512', range_complete=True,
                     choices=[dict(value=str(v), label='') for v in [16, 32, 64, 128, 256, 512]])]
        rows += [dict(key='CONFIG.UNRELATED_'+str(i), label='Unrelated '+str(i),
                      type='long', default='0', current='0', enabled=True,
                      shape='scalar', range='RANGE_NUM:0:1024', range_complete=True)
                 for i in range(30)]
        catalog = s.scalar_begin_catalog(rows, 'database')
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]['id'], 'r1.p1')
        self.assertEqual(catalog[0]['key'], 'CONFIG.FIFO_DEPTH')
        self.assertEqual(catalog[0]['text_choice'], '128')

    def test_scalar_begin_candidate_stays_empty_when_mapping_is_ambiguous(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        s.requirements = {'r1': 'depth of 128'}
        row = dict(label='FIFO depth', type='long', default='512', current='512',
                   enabled=True, shape='scalar', range='RANGE_NUM:16:1024', range_complete=True)
        rows = [dict(row, key='CONFIG.READ_DEPTH'), dict(row, key='CONFIG.WRITE_DEPTH')]
        self.assertEqual(s.scalar_begin_catalog(rows, 'database'), [])

    def test_scalar_source_count_maps_to_writable_port_count(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        s.requirements = {'r1': 'merge 22 interrupt sources into one interrupt vector'}
        common = dict(type='long', default='1', current='1', enabled=True,
                      shape='scalar', range='RANGE_NUM:1:4096', range_complete=True)
        rows = [dict(common, key='CONFIG.NUM_PORTS', label='Number of Ports'),
                dict(common, key='CONFIG.IN0_WIDTH', label='In0 Width'),
                dict(common, key='CONFIG.IN1_WIDTH', label='In1 Width'),
                dict(common, key='CONFIG.dout_width', label='Dout Width', write_mode='assert')]
        catalog = s.scalar_begin_catalog(rows, 'database')
        self.assertEqual([row['key'] for row in catalog], ['CONFIG.NUM_PORTS'])
        self.assertEqual(catalog[0]['id'], 'r1.p1')

    async def test_finish_saves_and_never_closes_vivado(self):
        """A design is built over many prompts: finish saves the cell and leaves Vivado open."""
        await self.describe(); await self.select(); await self.s.request({'op':'apply'})
        calls=[]
        async def close():
            calls.append('close'); return True
        self.s.close=close
        result=await self.s.request({'op':'finish'})
        self.assertEqual(calls,[])
        self.assertFalse(result['session_closed'])
        self.assertTrue(result['session_preserved'])
        self.assertEqual(result['next_op'],'begin')
        self.assertIn('vivado_stop only when', result['session_note'])

    async def test_status_reports_stage_specific_action(self):
        await self.describe(); await self.select(); await self.s.request({'op':'apply'})
        self.assertEqual((await self.s.request({'op':'status'}))['next_op'],'finish')
        await self.s.request({'op':'finish'})
        self.assertEqual((await self.s.request({'op':'status'}))['next_op'],'begin')

    async def test_failed_discovery_reuses_initialization_and_preserves_intent(self):
        s=Session(Guard(),None,'session','/scratch/proj/.vivado-ai/log')
        native=s.native;initializations=[];catalogs=[]
        async def flaky(expression,prefix=''):
            if 'lite::begin' in prefix:initializations.append(prefix)
            if expression.startswith('[lite::catalog'):
                catalogs.append(expression)
                if len(catalogs)==1:raise ValueError('Catalog temporarily unavailable')
            return await native(expression,prefix)
        s.native=flaky
        request=dict(op='begin',ip='unknown',cell='cell_0',part='part',task='original',requirements=['feature'])
        with self.assertRaisesRegex(ValueError,'temporarily'):await s.request(request)
        with self.assertRaisesRegex(ValueError,'preserve'):await s.request(dict(request,requirements=['different']))
        result=await s.request(dict(request,ip='generic'))
        self.assertEqual(result['stage'],'discovering')
        self.assertEqual(len(initializations),1)
        self.assertEqual(s.requirements,{'r1':'feature'})

    async def test_fuzzy_catalog_choice_uses_returned_identity(self):
        s=Session(Guard(),None,'session','/scratch/proj/.vivado-ai/log')
        native=s.native
        async def fuzzy(expression,prefix=''):
            if expression.startswith('[lite::catalog'):
                return 'CATALOG:CANDIDATES shown {{vlnv vendor:lib:first:1.0} {vlnv vendor:lib:second:2.0}}'
            if 'lite::open_cell' in prefix:s.chosen_prefix=prefix
            return await native(expression,prefix)
        s.native=fuzzy
        result=await s.request(dict(op='begin',ip='ambiguous',cell='cell_0',part='part',task='original',requirements=['feature']))
        self.assertEqual(result['next_op'],'choose_ip')
        self.assertEqual(len(result['ip_candidates']),2)
        with self.assertRaisesRegex(ValueError,'returned'):await s.request(dict(op='choose_ip',candidate_id='invented'))
        await s.request(dict(op='choose_ip',candidate_id='i2'))
        self.assertIn(atom('vendor:lib:second:2.0'),s.chosen_prefix)
        self.assertEqual(s.task,'original')

    async def test_initialization_failure_stops_after_three_attempts(self):
        s=Session(Guard(),None,'session','/scratch/proj/.vivado-ai/log')
        async def failed(expression,prefix=''):raise ValueError('Native unavailable')
        s.native=failed
        request=dict(op='begin',ip='generic',cell='cell_0',part='part',requirements=['feature'])
        for _ in range(2):
            with self.assertRaisesRegex(ValueError,'Native unavailable'):await s.request(request)
        result=await s.request(request)
        self.assertFalse(result['full'])
        self.assertFalse(result['session_closed'])   # finish keeps Vivado open
        self.assertTrue(result['session_preserved'])
        self.assertEqual((await s.request(dict(op='status')))['next_op'],'begin')
        self.assertEqual((await s.request(request))['stage'],'finished')
        self.assertEqual(s.begin_attempts,3)

    async def test_finish_unresolved_saves_and_never_claims_full(self):
        result=await self.s.request(dict(op='finish'))
        self.assertFalse(result['full'])
        self.assertFalse(result['session_closed'])   # finish keeps Vivado open
        self.assertTrue(result['session_preserved'])
        self.assertEqual(result['unresolved'],[dict(id='r1',text='requested feature')])
        self.assertEqual(self.s.last_expression,'[lite::partial_save]')
        self.assertEqual(self.s.apply_attempts,0)
        # Repeating a finished partial operation must not call a closed native session.
        self.s.last_expression=None
        await self.s.request(dict(op='finish'))
        self.assertIsNone(self.s.last_expression)

    async def test_finish_applies_grounded_subset_before_closing_partial(self):
        self.s.requirements = {'r1': 'set structural count to 2', 'r2': 'integration-only setting'}
        self.s.candidates = {'r1.p1': dict(key='CONFIG.A', label='Structural count', requirement_id='r1',
                                           evidence='source', type='long', current='0')}
        self.s.selections = {('r1', 'r1.p1'): dict(candidate_id='r1.p1', value=2)}
        result = await self.s.request(dict(op='finish'))
        self.assertEqual(result['partial_apply_trigger'], 'finish_with_grounded_selections')
        self.assertEqual(result['stage'], 'finished')
        self.assertEqual(self.s.apply_attempts, 1)
        self.assertIn('2', self.s.generated_result)
        self.assertFalse(result['full'])
        self.assertEqual(result['unresolved_requirements'], [dict(id='r2', text='integration-only setting')])

    async def test_terminal_discovery_exhaustion_preserves_grounded_subset(self):
        self.s.requirements = {'r1': 'set structural count to 2', 'r2': 'integration-only setting'}
        self.s.candidates = {'r1.p1': dict(key='CONFIG.A', label='Structural count', requirement_id='r1',
                                           evidence='source', type='long', current='0')}
        self.s.selections = {('r1', 'r1.p1'): dict(candidate_id='r1.p1', value=2)}
        self.s.describe_counts['r2'] = self.s.describe_limit('r2')
        self.s.exhausted['r2'] = 1
        result = await self.s.request(dict(op='describe', requirement_id='r2', query='terminal retry'))
        self.assertEqual(result['exhaustion_finish_trigger'], 'r2')
        self.assertEqual(result['partial_apply_trigger'], 'finish_with_grounded_selections')
        self.assertEqual(self.s.apply_attempts, 1)
        self.assertIn('2', self.s.generated_result)
        self.assertFalse(result['full'])

    async def test_waived_context_requirement_lets_apply_run_and_is_reported(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        await s.request(dict(op='begin', ip='generic', cell='cell_0', part='part', requirements=['requested feature', '100 MHz control domain']))
        with self.assertRaisesRegex(ValueError, 'Describe r2'):
            await s.request(dict(op='waive', requirement_id='r2', reason='clock domain context'))
        await s.request(dict(op='describe', requirement_id='r2'))
        result = await s.request(dict(op='waive', requirement_id='r2', reason='clock domain context, no IP setting'))
        self.assertEqual(result['waived'][0]['requirement_id'], 'r2')
        self.assertEqual(result['unresolved'], [dict(id='r1', text='requested feature')])
        with self.assertRaisesRegex(ValueError, 'At least one requirement'):
            await s.request(dict(op='waive', requirement_id='r1', reason='x'))
        await s.request(dict(op='describe', requirement_id='r1'))
        await s.request(dict(op='select', selections=[dict(requirement_id='r1', candidate_id='r1.p1', value=8)]))
        self.assertEqual(s.stage, 'ready')
        self.assertTrue((await s.request({'op': 'apply'}))['native_receipt'].startswith('CFG:OK'))
        final = await s.request({'op': 'finish'})
        self.assertEqual(final['waived_requirements'][0]['text'], '100 MHz control domain')

    async def test_begin_hint_absent_from_task_offers_task_matched_ip_first(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        original = s.native
        async def native(expression, prefix=''):
            if expression.startswith('[lite::catalog'):
                if 'concat' in expression or 'Y29uY2F0' in expression:
                    return 'CATALOG:CANDIDATES shown {{vlnv vendor:lib:concat:1.0 display_name concat}}'
                return 'CATALOG:EXACT vlnv vendor:lib:intc:1.0'
            return await original(expression, prefix)
        s.native = native
        result = await s.request(dict(op='begin', ip='AXI Interrupt Controller', cell='cell_0', part='part',
                                      task='Concat block that merges 22 interrupt sources into one vector.',
                                      requirements=['merge 22 interrupt sources']))
        self.assertEqual(result['stage'], 'choosing_ip')
        self.assertEqual(result['ip_candidates'][0]['vlnv'], 'vendor:lib:concat:1.0')
        self.assertEqual(result['ip_candidates'][0]['source'], 'task_text_catalog')
        self.assertIn('controller', result['ip_hint_check']['terms_not_in_task'])

    def test_case10_real_catalog_fixture_ranks_concat_before_generic_interrupt(self):
        fixture = json.loads((Path(__file__).parents[1] / 'fixtures/case10-live-catalog.json').read_text())
        ranked = StructuredSession.rerank_ip_candidates(fixture['task'], fixture['candidates'])
        self.assertEqual(ranked[0]['vlnv'], 'xilinx.com:ip:xlconcat:2.1')
        self.assertEqual(ranked[0]['matched_phrase'], 'concat')
        self.assertEqual(ranked[0]['original_catalog_position'], 2)
        intc = next(row for row in ranked if ':axi_intc:' in row['vlnv'])
        self.assertIn('interrupt', intc['display_terms'])

    def test_generic_catalog_ranking_regressions(self):
        cases = [
            ('Network on chip with four paths', 'xilinx.com:ip:axi_noc:1.0', 'xilinx.com:ip:axi_gpio:2.0'),
            ('Clocking Wizard with two output clocks', 'xilinx.com:ip:clk_wizard:1.0', 'xilinx.com:ip:proc_sys_reset:5.0'),
            ('processing system wizard', 'xilinx.com:ip:ps_wizard:1.0', 'xilinx.com:ip:system_management_wiz:1.0'),
            ('AIE array interface', 'xilinx.com:ip:ai_engine:1.0', 'xilinx.com:ip:axis_register_slice:1.1')]
        for task, expected, other in cases:
            rows = [dict(vlnv=other, display_name=other.split(':')[2]),
                    dict(vlnv=expected, display_name=task.split(' with ')[0])]
            self.assertEqual(StructuredSession.rerank_ip_candidates(task, rows)[0]['vlnv'], expected)

    async def test_weaker_candidate_is_rejected_for_unique_explicit_multiword_identity(self):
        self.s.task = 'Configure the Versal Gen2 processing system as a PCIe endpoint.'
        self.s.ip_hint = 'Versal Gen2 processing system'
        self.s.stage = 'choosing_ip'
        self.s.ip_candidates = {
            'i1': dict(vlnv='xilinx.com:ip:ps_wizard:1.0', display_name='processing system wizard',
                       source='begin_ip_hint', matched_phrase='processing system'),
            'i2': dict(vlnv='xilinx.com:ip:pcie_versal:1.1', display_name='PCI Express',
                       source='task_text_catalog', matched_phrase='versal')}
        result = await self.s.request(dict(op='choose_ip', candidate_id='i2'))
        self.assertEqual(result['candidate_validation']['status'], 'rejected')
        self.assertEqual(result['candidate_validation']['reason'], 'weaker_explicit_ip_identity')
        self.assertEqual(result['recommended_candidate'], 'i1')
        self.assertFalse(self.s.created_by_session)

    async def test_identity_guard_does_not_decide_single_word_or_tied_matches(self):
        self.s.task = 'Concat interrupt sources'
        self.s.ip_hint = 'interrupt controller'
        self.s.ip_candidates = {
            'i1': dict(vlnv='v:l:xlconcat:1', source='task_text_catalog', matched_phrase='concat'),
            'i2': dict(vlnv='v:l:axi_intc:1', source='begin_ip_hint', matched_phrase='interrupt')}
        self.assertIsNone(self.s.explicit_identity_candidate())

    async def test_apply_accepts_embedded_selections_through_normal_validator(self):
        await self.describe()
        result = await self.s.request(dict(op='apply', selections=[dict(
            requirement_id='r1', candidate_id='r1.p1', value=8)]))
        self.assertTrue(result['request_repaired'])
        self.assertEqual(result['request_repairs'][0]['repair'], 'validated_embedded_apply_selections')
        self.assertTrue(result['native_receipt'].startswith('CFG:OK'))

    def test_unique_native_enum_token_completion_is_bounded(self):
        self.s.requirements = {'r1': 'LPDDR5 controller type'}
        self.s.candidates = {'r1.p1': dict(key='CONFIG.SUB', subkey='TYPE', requirement_id='r1')}
        self.s.selections = {('r1', 'r1.p1'): dict(candidate_id='r1.p1', value='LPDDR5')}
        self.s.validation_findings = lambda reply: dict(rejected_fields=[
            dict(field='TYPE', valid_values=['DDR5_SDRAM', 'LPDDR5_SDRAM'])])
        repairs = self.s.repair_unique_native_enums('failure')
        self.assertEqual(self.s.selections[('r1', 'r1.p1')]['value'], 'LPDDR5_SDRAM')
        self.assertEqual(repairs[0]['scope'], 'unique_native_valid_value_token_completion')
        self.s.selections[('r1', 'r1.p1')]['value'] = 'DDR'
        self.s.requirements['r1'] = 'DDR controller type'
        self.assertEqual(self.s.repair_unique_native_enums('failure'), [])

    async def test_current_part_fit_counts_only_relevant_enabled_writable_rows(self):
        self.s.requirements = {'r1': 'enable CAUI-4 mode', 'r2': 'GT reference clock frequency'}
        rows = [dict(key='CONFIG.CMAC_CAUI4_MODE', label='CAUI4 Mode'),
                dict(key='CONFIG.GT_REF_CLK_FREQ', label='GT Reference Clock Frequency')]
        self.s.api_rows = lambda: __import__('asyncio').sleep(0, result=rows)
        async def enrich(found):
            for row in found:
                row.update(enabled=row['key'].endswith('MODE'), write_mode='write')
        self.s.enrich_live = enrich
        result = await self.s.current_part_fit()
        self.assertEqual((result['matched'], result['ready'], result['blocked']), (2, 1, 1))

        self.s.task = self.s.ip_hint = 'processing system'
        self.s.ip_candidates = {
            'i1': dict(vlnv='v:l:a:1', source='begin_ip_hint', matched_phrase='processing system'),
            'i2': dict(vlnv='v:l:b:1', source='task_text_catalog', matched_phrase='processing system')}
        self.assertIsNone(self.s.explicit_identity_candidate())

    async def test_repeated_invalid_reselection_stops_the_cell_on_second_call(self):
        self.s.ip_candidates = {'i1': {'vlnv': self.s.vlnv}, 'i2': {'vlnv': 'v:l:other:1.0'}}
        self.s.chosen_candidate_id = 'i1'; self.s.reselection_used = True
        request = dict(op='choose_ip', candidate_id='i2')
        first = await self.s.request(request)
        self.assertEqual((first['operation_rejected'], first['repeat_count']), (True, 1))
        second = await self.s.request(request)
        self.assertEqual((second['full'], second['session_closed'], second['next_op']), (False, False, 'begin'))
        self.assertTrue(second['cell_stopped'])

    async def test_candidate_validation_probe_outcomes_and_nested_inconclusive(self):
        self.s.requirements = {'r1': 'merge 22 interrupt sources'}
        self.s.api_parameters = [dict(key='CONFIG.NUM_PORTS', label='Number of Ports', help='input sources')]
        async def enrich(rows):
            for row in rows:
                row.update(write_mode='write', enabled=True, shape='scalar',
                           range='RANGE_NUM:1:128', range_complete=True)
        self.s.enrich_live = enrich
        for receipt, expected in [
                ('PROBE:OK key CONFIG.NUM_PORTS before 2 wanted 22 actual 22 restored 2', 'passed'),
                ('PROBE:REJECTED key CONFIG.NUM_PORTS reason write_failed before 2', 'rejected'),
                ('PROBE:REJECTED key CONFIG.NUM_PORTS reason readback_mismatch before 2 actual 2 restored 2', 'rejected'),
                ('PROBE:DIRTY key CONFIG.NUM_PORTS reason restore_failed before 2 actual 22', 'rejected')]:
            async def native(expression, prefix='', value=receipt):
                return value
            self.s.native = native
            self.s.candidates = {}
            self.s.ids = {}
            self.s.selections = {}
            result = await self.s.validate_candidate('i1')
            self.assertEqual(result['status'], expected)
            self.assertEqual(result['writable_keys'], ['CONFIG.NUM_PORTS'] if expected == 'passed' else [])
            if expected == 'passed':
                self.assertEqual(result['auto_selection']['key'], 'CONFIG.NUM_PORTS')
                self.assertEqual(result['auto_selection']['value'], 22)
                self.assertFalse(self.s.pending())
            else:
                self.assertNotIn('auto_selection', result)
        self.s.nested_rows = [dict(key='CONFIG.SUB_CONFIG', subkey='COUNT')]
        self.assertEqual((await self.s.validate_candidate('i1'))['status'], 'inconclusive')

    async def test_one_reselection_clears_ip_state_and_preserves_ledger(self):
        self.s.ip_candidates = {'i1': {'vlnv': self.s.vlnv}, 'i2': {'vlnv': 'v:l:replacement:1.0'}}
        self.s.chosen_candidate_id = 'i1'; self.s.created_by_session = True
        self.s.candidates = {'r1.p1': {'key': 'CONFIG.A', 'requirement_id': 'r1'}}
        self.s.selections = {('r1', 'r1.p1'): {'value': 8}}
        original_requirements = dict(self.s.requirements)
        async def discard():
            self.s.created_by_session = False
            return 'DISCARD:OK cell cell_0'
        async def choose(cid):
            self.s.vlnv = self.s.ip_candidates[cid]['vlnv']; self.s.chosen_candidate_id = cid
            self.s.created_by_session = True; self.s.stage = 'discovering'
            return self.s.state(vlnv=self.s.vlnv)
        self.s.discard_provisional = discard; self.s.choose_candidate = choose
        result = await self.s.request(dict(op='choose_ip', candidate_id='i2'))
        self.assertTrue(result['reselection_used'])
        self.assertEqual((result['previous_vlnv'], result['vlnv']), ('vendor:lib:generic:1.0', 'v:l:replacement:1.0'))
        self.assertEqual(self.s.requirements, original_requirements)
        self.assertFalse(self.s.candidates); self.assertFalse(self.s.selections)

    async def test_vector_written_for_one_requirement_covers_a_sibling_that_names_its_value(self):
        self.s.requirements = {'r1': 'output clock at 74.25 MHz', 'r2': 'output clock at 148.5 MHz',
                               'r3': 'output clock at 22.5792 MHz', 'r4': 'output clock at 50 MHz'}
        self.s.candidates = {'r1.p1': dict(key='CONFIG.FREQ', requirement_id='r1', shape='vector'),
                             'r2.p1': dict(key='CONFIG.FREQ', requirement_id='r2', shape='vector'),
                             'r4.p1': dict(key='CONFIG.FREQ', requirement_id='r4', shape='vector')}
        self.s.selections = {('r1', 'r1.p1'): dict(candidate_id='r1.p1', value='74.25,148.500,22.5792,100',
                                                    indices={'0': '74.250', '1': '148.500', '2': '22.5792'})}
        covered = self.s.vector_covered()
        self.assertEqual(covered['r2']['indices'], ['1'])
        self.assertNotIn('r3', covered)
        self.assertNotIn('r4', covered)
        self.assertEqual([r['id'] for r in self.s.pending()], ['r3', 'r4'])

    async def test_begin_hint_named_in_task_is_not_second_guessed(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        result = await s.request(dict(op='begin', ip='Generic', cell='cell_0', part='part',
                                      task='Generic block with a requested feature.', requirements=['requested feature']))
        self.assertEqual(result['stage'], 'discovering')

    async def test_indexed_value_write_turns_on_matching_enable_positions(self):
        self.s.properties = {'CONFIG.CLKOUT_FREQ', 'CONFIG.CLKOUT_USED', 'CONFIG.RESET_TYPE', 'CONFIG.USE_RESET', 'CONFIG.OTHER_EN'}
        self.s.candidates = {'r1.p1': dict(key='CONFIG.CLKOUT_FREQ', requirement_id='r1', shape='vector'),
                             'r1.p2': dict(key='CONFIG.RESET_TYPE', requirement_id='r1', enabled=False)}
        self.s.selections = {('r1', 'r1.p1'): dict(candidate_id='r1.p1', value='100,200,250,0', indices={'0': 100, '1': 200, '2': '250.000'}),
                             ('r1', 'r1.p2'): dict(candidate_id='r1.p2', value='ACTIVE_LOW')}
        self.s.vector_bases = {'CONFIG.CLKOUT_FREQ': '100,0,0,0'}
        live = {'CONFIG.CLKOUT_USED': 'true,false,false,false', 'CONFIG.USE_RESET': 'false', 'CONFIG.OTHER_EN': 'false'}
        async def native(expression, prefix=''):
            keys = [k for k in sorted(live) if atom(k) in expression]
            return subprocess.check_output(['tclsh'], input='puts [list ' + ' '.join(live[k] for k in keys) + ']\n', text=True).strip()
        self.s.native = native
        config = {'CONFIG.CLKOUT_FREQ': '100,200,250,0', 'CONFIG.RESET_TYPE': 'ACTIVE_LOW'}
        derived = await self.s.enable_companions(config)
        self.assertEqual(config['CONFIG.CLKOUT_USED'], 'true,true,true,false')
        self.assertEqual(config['CONFIG.USE_RESET'], 'true')
        self.assertNotIn('CONFIG.OTHER_EN', config)
        self.assertEqual({d['key'] for d in derived}, {'CONFIG.CLKOUT_USED', 'CONFIG.USE_RESET'})

    async def vector_candidate(self):
        await self.describe()
        self.s.candidates['r1.p1'].update(type='string',default=getattr(self.s,'live_vector','10,20,30'))
        self.s.candidates['r1.p1'].update(shape='vector')
        self.s.live_values={'CONFIG.A':getattr(self.s,'live_vector','10,20,30')}

    async def test_vector_features_merge_without_losing_unrelated_positions(self):
        await self.vector_candidate()
        self.s.requirements['r2']='second vector feature'
        self.s.candidates['r2.p1']=dict(self.s.candidates['r1.p1'],requirement_id='r2')
        await self.select(value={'indices':{'0':7}})
        result=await self.select(cid='r2.p1',rid='r2',value={'indices':{'2':8}})
        self.assertEqual({x['value'] for x in result['selected']},{'7,20,8'})
        self.assertFalse(result['unresolved'])
        await self.s.request({'op':'apply'})
        self.assertIn('7,20,8',self.s.generated_result)

    async def test_vector_truncation_and_bad_indices_rejected_before_selection(self):
        await self.vector_candidate()
        with self.assertRaisesRegex(ValueError,'length 3'):await self.select(value='7,8')
        with self.assertRaisesRegex(ValueError,'unrelated positions'):await self.select(value='7,0,0')
        with self.assertRaisesRegex(ValueError,'outside length'):await self.select(value={'indices':{'9':7}})
        self.assertFalse(self.s.selections)

    async def test_boolean_vector_indices_preserve_encoding(self):
        self.s.live_vector='true,false,false,false'
        await self.vector_candidate()
        result=await self.select(value={'indices':{'1':True,'2':1}})
        self.assertEqual(result['selected'][0]['value'],'true,true,true,false')

    async def test_nested_fields_merge_and_pin_each_subkey(self):
        await self.describe()
        self.s.candidates['r1.p1'].update(subkey='known',mapping_scope='live_dictionary')
        self.s.candidates['r1.p2']=dict(self.s.candidates['r1.p1'],subkey='other')
        await self.s.request(dict(op='select',selections=[
            dict(requirement_id='r1',candidate_id='r1.p1',value=8),
            dict(requirement_id='r1',candidate_id='r1.p2',value=9)]))
        await self.s.request({'op':'apply'})
        self.assertIn('known 8 other 9',self.s.generated_result)
        self.assertEqual(len(self.s.selections),2)

    async def test_nested_candidate_accepts_exact_redundant_subkey_only(self):
        await self.describe()
        self.s.candidates['r1.p1'].update(subkey='known',mapping_scope='live_dictionary')
        result = await self.s.request(dict(op='select', selections=[dict(
            requirement_id='r1', candidate_id='r1.p1', subkey='known', value=8)]))
        self.assertEqual(result['selected'][0]['subkey'], 'known')
        with self.assertRaisesRegex(ValueError, 'subkey must match'):
            await self.s.request(dict(op='select', selections=[dict(
                requirement_id='r1', candidate_id='r1.p1', subkey='other', value=9)]))
        self.assertEqual(self.s.selections[('r1', 'r1.p1')]['value'], 8)

    async def test_nested_conflicts_and_overwrites_fail_atomically(self):
        await self.describe()
        self.s.candidates['r1.p1'].update(subkey='known',mapping_scope='live_dictionary')
        self.s.candidates['r1.p2']=dict(self.s.candidates['r1.p1'])
        await self.select(value=8)
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            await self.select(cid='r1.p2',value=9)
        self.assertEqual(len(self.s.selections),1)
        with self.assertRaisesRegex(ValueError,'scalar value'):
            await self.select(value={'indices':{'0':1}})
        self.s.candidates['r1.p3']=dict(self.s.candidates['r1.p1'])
        self.s.candidates['r1.p3'].pop('subkey')
        with self.assertRaisesRegex(ValueError,'dictionary overwrite'):
            await self.select(cid='r1.p3',value=9)

    async def test_two_nested_fields_get_distinct_scoped_ids(self):
        self.s.nested_rows=[dict(key='CONFIG.A',subkey=name,label=name,type='long',default='0',range='RANGE_NONE',mapping_scope='live_dictionary') for name in ['ENABLE_CHANNEL0','CHANNEL0_FREQUENCY']]
        result=await self.s.request(dict(op='describe',requirement_id='r1',query='channel0 enable frequency'))
        self.assertEqual(len({r['id'] for r in result['candidates']}),2)
        self.assertEqual({r['subkey'] for r in result['candidates']},{'ENABLE_CHANNEL0','CHANNEL0_FREQUENCY'})

    async def test_better_named_top_level_parameter_is_not_hidden_by_loose_dictionary_fields(self):
        self.s.nested_rows=[dict(key='CONFIG.DICT',subkey=name,label=name.replace('_',' '),type='string',default='0',range='RANGE_NONE',mapping_scope='live_dictionary')
                            for name in ['MEM_SYSTEM_CLOCK','MEM_F0_CL','MEM_CLOCK_STOPPED']]
        self.s.properties|={'CONFIG.DICT','CONFIG.NUM_CLKS'}
        self.s.api_parameters=[dict(key='CONFIG.NUM_CLKS',label='Number of Clocks',type='long',default='1',range='RANGE_NUM:1:16',help=''),
                               dict(key='CONFIG.DICT',label='Dictionary',type='string',default='',range='RANGE_NONE',help='')]
        result=await self.s.request(dict(op='describe',requirement_id='r1',query='6 clocks'))
        self.assertIn('CONFIG.NUM_CLKS',[r['key'] for r in result['candidates']])
        self.assertEqual(result['backend'],'native_nested_and_live')
        result=await self.s.request(dict(op='describe',requirement_id='r1',query='memory system clock'))
        self.assertNotIn('CONFIG.NUM_CLKS',[r['key'] for r in result['candidates']])

    async def test_repeated_identical_select_is_flagged_then_rejected(self):
        await self.describe()
        self.assertNotIn('repeated_select', await self.select())
        self.assertTrue((await self.select())['repeated_select'])
        await self.select()
        with self.assertRaisesRegex(ValueError, 'Repeated identical select'):
            await self.select()
        self.assertNotIn('repeated_select', await self.select(value=9))

    async def test_same_named_definition_adds_only_missing_fields_after_overlap(self):
        import tempfile,os
        names=['F%d'%i for i in range(8)]
        params=''.join('<spirit:parameter><spirit:name>%s</spirit:name><spirit:displayName>%s</spirit:displayName><spirit:value spirit:resolve="user">0</spirit:value></spirit:parameter>'%(n,n) for n in names)
        params+='<spirit:parameter><spirit:name>NEW_PORT</spirit:name><spirit:description>New port pins</spirit:description><spirit:value spirit:resolve="user">{ENABLE 0 IO A}</spirit:value></spirit:parameter>'
        xml=os.path.join(tempfile.mkdtemp(dir=os.environ.get('TMPDIR')),'component.xml')
        open(xml,'w').write('<spirit:component xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009"><spirit:parameters>'+params+'</spirit:parameters></spirit:component>')
        async def native(expr,prefix=''):
            return 'SIBLING:OK vlnv x:ip:sub:1.0 xml '+xml if 'Q09ORklHLlNVQl9DT05GSUc' in expr else 'SIBLING:NONE'
        self.s.native=native
        rows=[dict(key='CONFIG.SUB_CONFIG',subkey=n,mapping_scope='live_dictionary') for n in names[:6]]
        added=await self.s.sibling_rows(rows+[dict(key='CONFIG.OTHER',subkey='F0')])
        self.assertEqual(sorted(r['subkey'] for r in added),['F6','F7','NEW_PORT'])
        port=[r for r in added if r['subkey']=='NEW_PORT'][0]
        self.assertEqual((port['mapping_scope'],port['source_vlnv'],port['shape'],port['fields'],port['default']),
                         ('sibling_definition_candidate','x:ip:sub:1.0','dictionary',['ENABLE','IO'],'ENABLE 0 IO A'))
        self.assertEqual(await self.s.sibling_rows(rows[:2]),[])

    async def test_same_named_definition_adds_choices_to_listed_fields_without_enforcing_them(self):
        import tempfile,os
        names=['F%d'%i for i in range(6)]
        params=''.join('<spirit:parameter><spirit:name>%s</spirit:name><spirit:value spirit:resolve="user">0</spirit:value></spirit:parameter>'%n for n in names[1:])
        params+='<spirit:parameter><spirit:name>F0</spirit:name><spirit:description>Lane mode</spirit:description><spirit:value spirit:resolve="user" spirit:choiceRef="c">A</spirit:value></spirit:parameter>'
        choices='<spirit:choices><spirit:choice><spirit:name>c</spirit:name>'+''.join('<spirit:enumeration>V%d</spirit:enumeration>'%i for i in range(9))+'</spirit:choice></spirit:choices>'
        xml=os.path.join(tempfile.mkdtemp(dir=os.environ.get('TMPDIR')),'component.xml')
        open(xml,'w').write('<spirit:component xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009">'+choices+'<spirit:parameters>'+params+'</spirit:parameters></spirit:component>')
        async def native(expr,prefix=''):
            return 'SIBLING:OK vlnv x:ip:sub:1.0 xml '+xml
        self.s.native=native
        rows=[dict(key='CONFIG.SUB_CONFIG',subkey=n,mapping_scope='live_dictionary',range='RANGE_NONE') for n in names]
        await self.s.sibling_rows(rows)
        self.assertEqual(len(rows[0]['choices']),9)
        self.assertEqual((rows[0].get('help'),rows[0]['choices_scope']),(None,'sibling_definition_not_current_legal_values'))
        self.assertEqual(rows[1].get('choices'),None)

    async def test_discovered_dictionary_rejects_replacement_without_field_selection(self):
        await self.describe()
        self.s.nested_rows=[dict(key='CONFIG.A',subkey='WIDTH')]
        with self.assertRaisesRegex(ValueError,'Preserve unrelated dictionary'):
            await self.select(value='WIDTH 8')
        self.assertEqual(self.s.selections,{})

    async def test_repeated_native_reads_do_not_receive_truncated_duplicate_receipts(self):
        import base64
        from mcp.types import CallToolResult,TextContent
        class Upstream:
            commands=[]
            async def call_tool(self,name,args):
                command=args['command']
                output='SKIPPED (duplicate): truncated' if command in self.commands else '__IPCFG__'+base64.b64encode(b'key value').decode()
                self.commands.append(command)
                return CallToolResult(content=[TextContent(type='text',text=json.dumps(dict(exit_code=0,output=output)))])
        upstream=Upstream();s=StructuredSession(Guard(),upstream,'session','/scratch/proj/.vivado-ai/log')
        self.assertEqual(await s.native('[dict create key value]'),'key value')
        self.assertEqual(await s.native('[dict create key value]'),'key value')
        self.assertNotEqual(upstream.commands[0],upstream.commands[1])

    def test_semantic_query_adds_source_direction_and_polarity_terms(self):
        query=self.s.semantic_query('driving the reset line','GPIO to drive an active-low reset')
        self.assertIn('output direction',query)
        self.assertIn('all outputs',query)
        self.assertIn('polarity low active',query)
        self.assertNotIn('CONFIG.',query)
        self.assertIn('all inputs',self.s.semantic_query('capture status','input status'))

    def test_semantic_query_preserves_discriminating_ledger_terms(self):
        query=self.s.semantic_query('inter-NoC master ports',
                                    '3 inter-NoC master (NMI) ports')
        self.assertIn('NMI',query)
        self.assertTrue(query.startswith('inter-NoC master ports'))

    async def test_explicit_zero_count_repairs_contradictory_selection(self):
        self.s.requirements={'r1':'no AXI clocks (NUM_CLKS)'}
        self.s.candidates={'r1.p1':dict(key='CONFIG.NUM_CLKS',
            label='Number of AXI Clocks',type='long',requirement_id='r1',
            evidence='source',enabled=True,current='1')}
        result=await self.s.request(dict(op='select',selections=[dict(
            requirement_id='r1',candidate_id='r1.p1',value=1)]))
        self.assertEqual(result['selected'][0]['value'],0)
        self.assertIn('used_explicit_ledger_count',
                      {repair['repair'] for repair in result['selection_repairs']})

    async def test_observable_direction_requirement_cannot_be_waived(self):
        self.s.requirements={'r1':'GPIO to drive the reset line','r2':'camera subsystem context'}
        self.s.describe_counts={'r1':1}
        self.s.candidates={'r1.p1':dict(key='CONFIG.DIRECTION',label='Output direction',requirement_id='r1')}
        with self.assertRaisesRegex(ValueError,'observable IP behavior'):
            await self.s.request(dict(op='waive',requirement_id='r1',reason='purpose only'))

    async def test_context_requirement_can_still_be_waived(self):
        self.s.requirements={'r1':'requested feature','r2':'camera subsystem context'}
        self.s.describe_counts={'r2':1}
        result=await self.s.request(dict(op='waive',requirement_id='r2',reason='system context only'))
        self.assertEqual(result['waived'][0]['requirement_id'],'r2')

    def test_saved_result_contains_only_native_observations(self):
        self.s.vlnv='xilinx.com:ip:generic:1.0'
        verified=dict(available=True,fields=[
            dict(key='CONFIG.A',actual='13',wanted='8',passed=False),
            dict(key='CONFIG.DICT',subkey='MODE',actual='Endpoint',wanted='Endpoint',passed=True),
            dict(key='CONFIG.MISSING',wanted='1',passed=False)])
        result=self.s.saved_configuration(verified)
        self.assertEqual(result['identified_ip'],'generic')
        self.assertEqual(result['as_configured'],{'CONFIG.A':'13','CONFIG.DICT.MODE':'Endpoint'})


if __name__ == '__main__':unittest.main()


class LiteralReviewTests(unittest.IsolatedAsyncioTestCase):
    async def session(self, task='set rate to 8'):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        await s.request(dict(op='begin', ip='generic', cell='cell_0', part='part',
                             task=task, requirements=[task]))
        s.candidates = {'r1.p1': dict(key='CONFIG.A', requirement_id='r1',
                                     evidence='source', type='long', current='0',
                                     default='0', enabled=True)}
        s.selections = {('r1', 'r1.p1'): dict(candidate_id='r1.p1', value=4)}
        s.stage = 'configured'
        s.apply_attempts = 1
        s.verified_fields = {'CONFIG.A': '4'}
        # Isolate the T5 second check from the pre-existing ledger checker.
        s.coverage_findings = lambda: []
        return s

    async def test_review_emitted_once_and_context_ignored_at_first_finish(self):
        s = await self.session()
        first = await s.request({'op': 'finish', 'context': {'8': 'not a setting'}})
        self.assertEqual(first['stage'], 'literal_review')
        self.assertEqual(first['unaccounted_literals'], ['8'])
        self.assertEqual(s.stage, 'literal_review')
        second = await s.request({'op': 'finish'})
        self.assertEqual(second['stage'], 'finished')
        self.assertFalse(second['full'])
        self.assertEqual(second['declared_context'], {})
        self.assertIn('literal not realized: 8', second['coverage_gaps'])

    async def test_review_covered_by_selection_and_changed_field_reported(self):
        s = await self.session()
        await s.request({'op': 'finish'})
        s.verified_fields = {'CONFIG.A': '8'}
        final = await s.request({'op': 'finish'})
        self.assertTrue(final['full'])
        self.assertEqual(final['literal_review_result']['fixed_by_selection'], ['8'])
        self.assertEqual(final['literal_review_result']['changed_verified_fields'], ['CONFIG.A'])

    async def test_review_covered_by_declaration(self):
        s = await self.session()
        await s.request({'op': 'finish'})
        final = await s.request({'op': 'finish', 'context': {'8': 'a protocol revision label'}})
        self.assertTrue(final['full'])
        self.assertEqual(final['declared_context'], {'8': 'a protocol revision label'})
        self.assertEqual(final['literal_review_result']['declared_as_context'], ['8'])

    async def test_undeclared_remainder_forces_partial_with_gap(self):
        s = await self.session('set rates to 8, 9')
        first = await s.request({'op': 'finish'})
        self.assertEqual(first['unaccounted_literals'], ['8', '9'])
        final = await s.request({'op': 'finish', 'context': {'8': 'context only'}})
        self.assertFalse(final['full'])
        self.assertEqual(final['literal_review_result']['still_uncovered'], ['9'])
        self.assertIn('literal not realized: 9', final['coverage_gaps'])

    async def test_bounded_finish_does_not_open_review_and_forces_partial(self):
        s = await self.session()
        final = await s.bounded_finish('bounded test')
        self.assertEqual(final['stage'], 'finished')
        self.assertFalse(final['full'])
        self.assertIn('literal not realized: 8', final['coverage_gaps'])
        self.assertFalse(s.literal_reviewed)

    async def test_select_and_apply_are_allowed_during_review(self):
        s = await self.session()
        await s.request({'op': 'finish'})
        selected = await s.request({'op': 'select', 'selections': [
            {'requirement_id': 'r1', 'candidate_id': 'r1.p1', 'value': 8}]})
        self.assertNotIn('operation_rejected', selected)
        applied = await s.request({'op': 'apply'})
        self.assertIn(applied['stage'], ('configured', 'finished'))


class VerifiedPrerequisiteTests(unittest.IsolatedAsyncioTestCase):
    """Exact enabling assignments from the IP's own (encrypted) XGUI rules, not live guesses."""

    def session(self):
        s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        s.vlnv = 'xilinx.com:ip:ps_wizard:1.0'
        s.nested_rows = [dict(key='CONFIG.MMI_CONFIG', subkey=k, default='0') for k in
                         ['MMI_PCIE0_ENABLE', 'PCIE0_PF', 'PCIE0_PF1_DEV_ID', 'PCIE0_COPY_PF0_EN']]
        async def no_live(rows):
            return None
        async def no_api():
            return []
        s.enrich_live, s.api_rows = no_live, no_api
        return s

    def test_shipped_table_covers_pcie_pf_fields(self):
        table = StructuredSession.verified_prerequisite_table()['xilinx.com:ip:ps_wizard:1.0']
        self.assertEqual(table['MMI_CONFIG.PCIE0_PF1_DEV_ID'],
                         ({'MMI_CONFIG.MMI_PCIE0_ENABLE': '1', 'MMI_CONFIG.PCIE0_PF': '2'},
                          'xgui_rule_verified_by_reexecution', None))
        self.assertGreater(len(table), 1000)
        probed = {k: v for k, v in table.items() if k.startswith('PS11_CONFIG.')}
        self.assertGreater(len(probed), 50)
        self.assertTrue(all(scope == 'probed_single_switch_on_part' and part for _, scope, part in probed.values()))

    async def test_probed_ps11_entry_is_labelled_as_a_part_scoped_hint(self):
        s = self.session()
        name, (need, _, part) = next((k, v) for k, v in StructuredSession.verified_prerequisite_table()
                                     ['xilinx.com:ip:ps_wizard:1.0'].items() if k.startswith('PS11_CONFIG.'))
        container, sub = name.split('.', 1)
        dep = next(iter(need)).split('.', 1)[1]
        s.nested_rows = [dict(key='CONFIG.' + container, subkey=dep, default='0')]
        row = dict(key='CONFIG.' + container, subkey=sub)
        rows = await s.verified_prerequisites([row])
        self.assertEqual(row['enablement_source_scope'], 'probed_single_switch_on_part')
        self.assertEqual(row['enablement_observed_part'], part)
        self.assertEqual(rows[1]['relation_scope'], 'probed_prerequisite')
        self.assertIn('enablement_observed_part', StructuredSession.compact(row, 'p1'))

    async def test_nested_disabled_field_gets_multi_switch_prerequisite_and_its_controls(self):
        s = self.session()
        target = dict(key='CONFIG.MMI_CONFIG', subkey='PCIE0_PF1_DEV_ID')
        rows = await s.verified_prerequisites([target])
        self.assertEqual(target['enablement_requires'],
                         {'CONFIG.MMI_CONFIG.MMI_PCIE0_ENABLE': '1', 'CONFIG.MMI_CONFIG.PCIE0_PF': '2'})
        self.assertEqual(target['enablement_source_scope'], 'xgui_rule_verified_by_reexecution')
        added = {(r['key'], r['subkey'], r['relation_scope']) for r in rows[1:]}
        self.assertEqual(added, {('CONFIG.MMI_CONFIG', 'MMI_PCIE0_ENABLE', 'verified_prerequisite'),
                                 ('CONFIG.MMI_CONFIG', 'PCIE0_PF', 'verified_prerequisite')})
        self.assertIn('enablement_requires', StructuredSession.compact(target, 'p1'))

    async def test_enabled_rows_and_other_ips_are_untouched(self):
        s = self.session()
        row = dict(key='CONFIG.MMI_CONFIG', subkey='PCIE0_PF1_DEV_ID', enabled=True)
        self.assertEqual(await s.verified_prerequisites([row]), [row])
        self.assertNotIn('enablement_requires', row)
        s.vlnv = 'xilinx.com:ip:axi_gpio:2.0'
        other = dict(key='CONFIG.MMI_CONFIG', subkey='PCIE0_PF1_DEV_ID')
        self.assertEqual(await s.verified_prerequisites([other]), [other])
