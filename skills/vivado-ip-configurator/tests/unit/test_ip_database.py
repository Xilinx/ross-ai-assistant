"""Behavioral checks against a declared scratch metadata snapshot, never test answers."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from ip_database import Database
from guard_mcp import ExecutionGuard, payload
from structured_config import StructuredSession


# The vector metadata database is a local snapshot (examples/relay/database.md) that only the
# examples/ relay serves; point IPCFG_TEST_DATABASE_CONFIG at its runtime.json to run these.
@unittest.skipUnless(os.environ.get('IPCFG_TEST_DATABASE_CONFIG'), 'IPCFG_TEST_DATABASE_CONFIG is not set')
class MetadataDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_path = Path(os.environ['IPCFG_TEST_DATABASE_CONFIG'])
        cls.config = json.loads(cls.config_path.read_text())
        cls.database = Database(cls.config)

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def test_integrity_foreign_keys_and_index_counts(self):
        d = self.database.db
        self.assertEqual(d.execute('pragma integrity_check').fetchone()[0], 'ok')
        self.assertFalse(d.execute('pragma foreign_key_check').fetchall())
        self.assertEqual(d.execute('select count(*) from documents').fetchone()[0],
                         d.execute('select count(*) from search_vectors').fetchone()[0])

    def test_database_is_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.database.db.execute('delete from ips')

    def test_build_mismatch_rejected_before_embedding(self):
        with self.assertRaisesRegex(ValueError, 'SNAPSHOT_MISMATCH'):
            self.database.search('clocking', 'wrong-build')

    def test_exact_identity_and_lexical_budget(self):
        result = self.database.search('axi_gpio', '6511674', mode='lexical')
        self.assertEqual(result['candidates'][0]['vlnv'], 'xilinx.com:ip:axi_gpio:2.0')
        self.assertLessEqual(len(result['candidates']), 3)

    def test_requested_output_frequency_is_not_hidden_by_input_frequency_hits(self):
        result = self.database.search('output clock frequency 250 MHz', '6511674',
            vlnv='xilinx.com:ip:clk_wizard:1.0', part='xcvc1902-vsva2197-2MP-e-S')
        keys = [c['key'] for c in result['candidates']]
        self.assertIn('CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY', keys)
        self.assertIn('CONFIG.CLKOUT_USED', keys)
        related=next(c for c in result['candidates'] if c['key']=='CONFIG.CLKOUT_USED')
        self.assertEqual(related['relation_scope'],'shared_identifier_family_not_dependency_proof')
        self.assertLessEqual(len(keys), 4)

    def test_drive_intent_retrieves_direction_control_ahead_of_output_value(self):
        query = StructuredSession.semantic_query(
            'drive the reset line using GPIO output', 'drive the reset line')
        result = self.database.search(query, '6511674',
            vlnv='xilinx.com:ip:axi_gpio:2.0', part='xc2ve3558-sfva1440-2MP-e-S')
        keys = [c['key'] for c in result['candidates']]
        self.assertEqual(keys[0], 'CONFIG.C_ALL_OUTPUTS')
        self.assertIn('CONFIG.C_DOUT_DEFAULT', keys)

    def test_identifier_words_are_split_without_parameter_aliases(self):
        self.assertEqual(self.database.identifier_terms('OutputFrequencyMHz_23'), {'output', 'frequency'})

    def test_a_unit_glued_onto_an_all_caps_quantity_still_yields_frequency(self):
        """PCW_FPGA0_PERIPHERAL_FREQMHZ (real Zynq-7000 PL clock property) and
        PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ (its UltraScale+ counterpart) have
        no case transition between FREQ and MHZ, so without this split the
        'mhz'->'frequency' alias never fires and a decoy CLK0_FREQ-style
        property outranks the real one.
        """
        self.assertEqual(self.database.identifier_terms('PCW_FPGA0_PERIPHERAL_FREQMHZ'),
                         {'pcw', 'fpga', 'peripheral', 'frequency'})
        self.assertEqual(self.database.identifier_terms('PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ'),
                         {'psu', 'crl', 'apb', 'ref', 'ctrl', 'frequency'})
        # The separately spelled decoy keeps the terms it always had.
        self.assertEqual(self.database.identifier_terms('PCW_CLK0_FREQ'), {'pcw', 'clock', 'frequency'})

    def test_numbered_output_clocks_with_units_retrieve_the_value_vector(self):
        for query in ['output clock 1 at 250 MHz', 'output clock 2 at 200 MHz', 'output clock 3 at 125 MHz']:
            result = self.database.search(query, '6511674', vlnv='xilinx.com:ip:clk_wizard:1.0', part='xcvc1902-vsva2197-2MP-e-S')
            keys = [c['key'] for c in result['candidates']]
            self.assertEqual(keys[0], 'CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY')
            self.assertIn('CONFIG.CLKOUT_USED', keys)
            self.assertLessEqual(len(keys), 4)

    def test_nested_hints_are_exact_names_and_never_parent_bindings(self):
        result=self.database.field_hints(['PS_USE_PMCPL_CLK0','NOT_A_FIELD'],'6511674')
        self.assertNotIn('NOT_A_FIELD',result['fields'])
        hint=result['fields']['PS_USE_PMCPL_CLK0']
        self.assertEqual(hint['hint_scope'],'exact_name_in_other_definitions_not_parent_binding')
        self.assertNotIn('write_key',hint)
        self.assertNotIn('native_key',hint)
        self.assertGreaterEqual(len(hint['source_vlnvs']),2)
        with self.assertRaises(ValueError):self.database.field_hints(['BAD;KEY'],'6511674')

    def test_part_scope_excludes_inapplicable_native_range(self):
        keys = ['CONFIG.C_GPIO_WIDTH']
        native = self.database.lookup('xilinx.com:ip:axi_gpio:2.0', keys, '6511674',
                                      'xcvc1902-vsva2197-2MP-e-S')['parameters'][0]
        other = self.database.lookup('xilinx.com:ip:axi_gpio:2.0', keys, '6511674',
                                     'different-part')['parameters'][0]
        self.assertEqual(native['range_scope'], 'requires_current_configuration')
        self.assertEqual(native['range'], 'RANGE_NONE')
        self.assertEqual(other['range_scope'], 'requires_current_configuration')
        self.assertTrue(other['requires_live_validation'])

    def test_missing_key_explicit_and_lookup_bounded(self):
        result = self.database.lookup('xilinx.com:ip:axi_gpio:2.0', ['CONFIG.NOT_A_PARAMETER'], '6511674')
        self.assertEqual(result['missing'], ['CONFIG.NOT_A_PARAMETER'])
        with self.assertRaises(ValueError):
            self.database.lookup('xilinx.com:ip:axi_gpio:2.0', ['CONFIG.C_GPIO_WIDTH'] * 5, '6511674')

    def test_sql_like_query_is_data_not_code(self):
        before = self.database.db.execute('select count(*) from ips').fetchone()[0]
        self.database.search('"; DROP TABLE ips; --', '6511674', mode='lexical')
        self.assertEqual(before, self.database.db.execute('select count(*) from ips').fetchone()[0])

    def test_gui_evidence_preserves_device_and_capture_scope(self):
        row = self.database.db.execute("select e.part,e.scope,e.payload from evidence e where kind='gui' limit 1").fetchone()
        self.assertEqual(row['scope'], 'captured_gui_instance')
        self.assertTrue(row['part'])
        self.assertIn('fingerprint was not recorded', json.loads(row['payload'])['capture_configuration'])

    def test_no_free_sql_operation_or_wrong_stage(self):
        with self.assertRaises(ValueError):
            self.database.request({'op': 'sql', 'query': 'delete from ips', 'build': '6511674'})
        with self.assertRaises(ValueError):
            self.database.request({'op': 'parameters', 'query': 'width', 'build': '6511674'})

    def test_guard_reserved_session_never_forwards_to_vivado(self):
        class Upstream:
            async def call_tool(self, *args, **kwargs):
                raise AssertionError('Database request must never launch/call Vivado')
        guard = ExecutionGuard(Path(os.environ['TMPDIR']) / 'guard-db-test', database_config=str(self.config_path))
        async def probe():
            info = await guard.execute(Upstream(), {'session_id': 'ipdb', 'command': '{"op":"info"}'})
            self.assertEqual(payload(info)['used_mode'], 'sqlite_readonly')
            self.assertEqual(json.loads(payload(info)['output'])['build'], '6511674')
            bad = await guard.execute(Upstream(), {'session_id': 'ipdb', 'command': 'exit; exec touch /tmp/should-never-exist'})
            self.assertTrue(bad.isError)
            with self.assertRaises(ValueError):
                guard.database_request('{"op":"ips","query":"width","build":"6511674","sql":"delete from ips"}')
        try:
            asyncio.run(probe())
        finally:
            guard.close_database()

    def test_complete_small_catalog_without_embeddings(self):
        encoder=self.database.encoder
        class ForbiddenEncoder:
            def encode(self,*args):
                raise AssertionError('Complete catalog lookup must not embed')
        self.database.encoder=ForbiddenEncoder()
        try:
            reply=self.database.request(dict(op='catalog',vlnv='xilinx.com:ip:axi_gpio:2.0',build='6511674'))
            self.assertEqual(reply['status'],'DB:CATALOG')
            self.assertEqual(len(reply['candidates']),reply['count'])
            self.assertLessEqual(reply['count'],24)
        finally:
            self.database.encoder=encoder

    def test_large_catalog_never_returns_partial_table(self):
        reply=self.database.request(dict(op='catalog',vlnv='xilinx.com:ip:ps11:1.0',build='6511674'))
        self.assertEqual(reply['status'],'DB:CATALOG_TOO_LARGE')
        self.assertGreater(reply['count'],24)
        self.assertEqual(reply['candidates'],[])


if __name__ == '__main__':
    unittest.main()
