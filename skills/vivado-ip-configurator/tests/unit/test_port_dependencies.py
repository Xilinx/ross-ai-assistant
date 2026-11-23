import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from port_dependencies import keywords, parse, detail_fields
from structured_config import atom
from test_structured_config import Guard, Session


def tcl(expression):
    return subprocess.check_output(['tclsh'], input='puts ' + expression + '\n', text=True).strip()


def gates(ports, more=0):
    pairs = ' '.join(atom(name) + ' ' + atom(value) for name, value in ports.items())
    return tcl('[list GATES:OK matched ' + str(len(ports)) + ' more ' + str(more) +
               ' dependencies [dict create ' + pairs + '] evidence /scratch/current/gates.tcldict]')


DETAIL = '''[list DETAIL:OK parameters [dict create CONFIG.B [dict create
 {Display Name} {Port enabled} Type bool {Default Value} false
 help {Enable the requested output port} help_source native_description
 range {RANGE_LIST:B:false true} range_scope native_default_instance
 enums {count 2 offset 0 shown {{false Disabled} {true Enabled}} more 0}]]]
'''.replace('\n', ' ')


class PortDataTests(unittest.TestCase):
    def test_engineering_keywords_preserve_explicit_port_and_expand_irq(self):
        found = keywords('Enable M_AXI_FPD with the interrupt output and active-low reset input')
        self.assertEqual(found[0], 'm_axi_fpd')
        self.assertIn('irq', found)
        self.assertIn('rst', found)
        self.assertNotIn('enable', found)
        self.assertLessEqual(len(keywords(' '.join('field'+str(i) for i in range(100)))), 8)

    def test_direct_derived_and_missing_graph_are_distinct(self):
        result = parse(gates({'OUT': 'PE:OUT:B DERIVED{D E} UNMAPPED{NODE}',
                              'COMPUTED': 'PE_DERIVED:COMPUTED:F',
                              'ALWAYS': 'PE_NONE:ALWAYS'}))
        self.assertEqual(result['ports'][0]['parameters'], ['B'])
        self.assertEqual(result['ports'][0]['derived_parameters'], ['D', 'E'])
        self.assertEqual(result['ports'][1]['parameters'], [])
        self.assertEqual(result['ports'][1]['status'], 'derived')
        self.assertEqual(result['ports'][2]['status'], 'no_direct_dependencies')
        self.assertEqual(parse('GATES:UNAVAILABLE reason {IP publishes no port gating}')['status'], 'unavailable')

    def test_native_enum_labels_types_and_truncation_are_preserved(self):
        fields = tcl('[dict create Type bool {Default Value} false help x help_truncated 1 enums {count 9 offset 0 shown {{false Disabled} {true Enabled}} more 1}]')
        result = detail_fields(fields)
        self.assertEqual(result['type'], 'bool')
        self.assertEqual(result['choices'][1], dict(value='true', label='Enabled', truncated=False))
        self.assertTrue(result['choices_more'])
        self.assertTrue(result['help_truncated'])


class PortWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s = Session(Guard(), None, 'session', '/scratch/proj/.vivado-ai/log')
        self.s.gating_receipt = gates({'OUT': 'PE:OUT:B'})
        self.s.control_details = tcl(DETAIL)
        await self.s.request(dict(op='begin', ip='generic', cell='cell_0', part='part', requirements=['OUT enabled']))

    async def describe(self):
        return await self.s.request(dict(op='describe', requirement_id='r1', query='OUT enabled'))

    async def test_automatic_direct_control_is_selectable_with_native_encoding(self):
        reply = await self.describe()
        control = next(c for c in reply['candidates'] if c['key'] == 'CONFIG.B')
        self.assertEqual(control['dependency_ports'], ['OUT'])
        self.assertEqual(control['dependency_scope'], 'direct_native_predecessor')
        self.assertEqual(control['type'], 'bool')
        self.assertEqual(control['choices'][1]['label'], 'Enabled')
        self.assertEqual(reply['port_dependencies']['ports'][0]['candidate_ids'], [control['id']])
        with self.assertRaises(ValueError):
            await self.s.request(dict(op='select', selections=[dict(requirement_id='r1', candidate_id=control['id'], value='guessed')]))
        await self.s.request(dict(op='select', selections=[
            dict(requirement_id='r1', candidate_id=reply['candidates'][0]['id'], value=8),
            dict(requirement_id='r1', candidate_id=control['id'], value='true')]))
        await self.s.request(dict(op='apply'))
        self.assertIn('8\ntrue\nOUT enabled', self.s.generated_result)

    async def test_primary_control_is_deduplicated_and_native_metadata_wins(self):
        self.s.gating_receipt = gates({'OUT': 'PE:OUT:A'})
        self.s.control_details = tcl(DETAIL.replace('CONFIG.B', 'CONFIG.A'))
        reply = await self.describe()
        self.assertEqual(len(reply['candidates']), 1)
        self.assertEqual(reply['candidates'][0]['type'], 'bool')
        self.assertEqual(reply['candidates'][0]['dependency_ports'], ['OUT'])

    async def test_controls_are_added_to_complete_catalog_without_describe(self):
        guard = Guard(); original = guard.database_request
        def request(command):
            if json.loads(command)['op'] == 'catalog':
                return dict(status='DB:CATALOG', candidates=[dict(key='CONFIG.B', type='bool', label='Port enabled', default='false')])
            return original(command)
        guard.database_request = request
        session = Session(guard, None, 'session', '/scratch/proj/.vivado-ai/log')
        session.gating_receipt = self.s.gating_receipt;session.control_details = self.s.control_details
        reply = await session.request(dict(op='begin', ip='generic', cell='cell_0', part='part', requirements=['OUT enabled']))
        self.assertEqual(reply['port_dependencies']['ports'][0]['candidate_ids'], ['p1'])
        self.assertEqual(reply['catalog'][0]['dependency_ports'], ['OUT'])
        self.assertEqual(reply['catalog'][0]['choices'][1]['value'], 'true')

    async def test_unpublished_gating_keeps_regular_candidates_without_inference(self):
        self.s.gating_receipt = 'GATES:UNAVAILABLE reason {IP publishes no port gating}'
        reply = await self.describe()
        self.assertEqual(reply['port_dependencies']['status'], 'unavailable')
        self.assertEqual(reply['candidates'][0]['key'], 'CONFIG.A')
        self.assertNotIn('dependency_ports', reply['candidates'][0])

    async def test_derived_and_nonwritable_controls_cannot_create_write_candidates(self):
        self.s.gating_receipt = gates({'OUT': 'PE:OUT:INTERNAL DERIVED{A B}', 'OTHER': 'PE_DERIVED:OTHER:B'})
        reply = await self.describe()
        self.assertEqual(len(reply['candidates']), 1)
        self.assertEqual(reply['port_dependencies']['ports'][0]['unresolved_parameters'], ['INTERNAL'])
        self.assertEqual(reply['port_dependencies']['ports'][1]['candidate_ids'], [])

    async def test_new_controls_and_port_context_are_bounded(self):
        self.s.properties.update('CONFIG.C'+str(i) for i in range(20))
        self.s.gating_receipt = gates({'OUT': 'PE:OUT:' + ' '.join('C'+str(i) for i in range(20))}, more=1)
        reply = await self.describe()
        self.assertEqual(len(reply['candidates']), 5)
        self.assertTrue(reply['port_dependencies']['controls_more'])
        self.assertTrue(reply['port_dependencies']['more'])
        self.assertEqual(len(reply['port_dependencies']['ports'][0]['unresolved_parameters']), 4)

    async def test_ambiguous_nested_control_is_unresolved_not_assigned_to_a_parent(self):
        self.s.gating_receipt = gates({'OUT': 'PE:OUT:FIELD'})
        self.s.nested_rows = [dict(key=key, subkey='FIELD', label='FIELD') for key in ['CONFIG.LEFT', 'CONFIG.RIGHT']]
        rows, context = await self.s.port_controls('OUT', [dict(key='CONFIG.A')])
        self.assertEqual(len(rows), 1)
        summary = self.s.port_summary(context, [dict(id='r1.p1', key='CONFIG.A')])
        self.assertEqual(summary['ports'][0]['unresolved_parameters'], ['FIELD'])

    async def test_repeated_query_reuses_native_graph_and_candidate_ids(self):
        native = self.s.native;calls = []
        async def count(expression, prefix=''):
            if expression.startswith('[lite::gates'):calls.append(expression)
            return await native(expression, prefix)
        self.s.native = count
        first = await self.describe();second = await self.describe()
        self.assertEqual(len(calls), 1)
        self.assertEqual([c['id'] for c in first['candidates']], [c['id'] for c in second['candidates']])

    async def test_nested_control_retains_unique_live_parent_and_mapping_provenance(self):
        self.s.gating_receipt = gates({'OUT': 'PE:OUT:FIELD'})
        self.s.properties.add('CONFIG.SETTINGS')
        self.s.nested_rows = [dict(key='CONFIG.SETTINGS', subkey='FIELD', label='Enable field',
            native_key='CONFIG.SETTINGS_INTERNAL', mapping_scope='native_same_name_candidate', default='0')]
        rows, _ = await self.s.port_controls('OUT', [dict(key='CONFIG.A')])
        self.assertEqual(rows[1]['key'], 'CONFIG.SETTINGS')
        self.assertEqual(rows[1]['subkey'], 'FIELD')
        self.assertEqual(rows[1]['mapping_scope'], 'native_same_name_candidate')
        self.s.properties.remove('CONFIG.SETTINGS')
        rows, _ = await self.s.port_controls('OUT', [dict(key='CONFIG.A')])
        self.assertEqual(len(rows), 1)

    async def test_boolean_control_without_published_enum_retains_unavailable_range(self):
        self.s.control_details = tcl('[list DETAIL:OK parameters [dict create CONFIG.B [dict create Type bool {Default Value} false range RANGE_NONE range_scope native_default_instance help_source unavailable]]]')
        reply = await self.describe()
        control = next(c for c in reply['candidates'] if c['key'] == 'CONFIG.B')
        self.assertEqual(control['type'], 'bool')
        self.assertEqual(control['range'], 'RANGE_NONE')
        self.assertNotIn('choices', control)
        await self.s.request(dict(op='select', selections=[dict(requirement_id='r1', candidate_id=control['id'], value='true')]))
        self.assertFalse(self.s.pending())

    async def test_truncated_port_query_is_explicit(self):
        rows, context = await self.s.port_controls(' '.join('topic'+str(i) for i in range(20)), [dict(key='CONFIG.A')])
        summary = self.s.port_summary(context, [])
        self.assertTrue(summary['keywords_more'])
