"""Behavioral checks for the release database Vivado ships at data/ip/ipdb/ipdb.sqlite.

This is a different artifact from the one test_ip_database.py exercises: schema 1.7-merged,
no embedding vectors, discovered from a live install rather than a hand-mounted snapshot
config.  Skips (not fails) when that install isn't present on the host running the suite --
the production gate is IPCFG_TEST_DATABASE_CONFIG / the live Vivado fallback, not this file.
"""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from merged_database import MergedDatabase, release_of, shipped_database
from guard_mcp import ExecutionGuard

OVERRIDE = os.environ.get('IPCFG_TEST_RELEASE_INSTALL')
INSTALL = OVERRIDE or os.environ.get('XILINX_VIVADO')


def _test_database():
    path = shipped_database(INSTALL)
    if path and not OVERRIDE:
        # The assertions below exercise the 2026.2 database specifically.
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                if not db.execute("SELECT 1 FROM meta WHERE key='vivado_version' AND value='2026.2'").fetchone():
                    return None
        except sqlite3.Error:
            return None
    return path


DB_PATH = _test_database()
SKIP_REASON = 'Set IPCFG_TEST_RELEASE_INSTALL or XILINX_VIVADO to a Vivado 2026.2 install with ipdb.sqlite.'


@unittest.skipUnless(DB_PATH, SKIP_REASON)
class MergedDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = DB_PATH
        cls.database = MergedDatabase(cls.path, release='2026.2')

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def test_discovery_is_none_without_a_matching_file(self):
        self.assertIsNone(shipped_database(None))
        self.assertIsNone(shipped_database('/nonexistent-install-root'))
        self.assertEqual(shipped_database(INSTALL), self.path)

    def test_release_is_parsed_from_a_vivado_banner_not_the_build(self):
        self.assertEqual(release_of('Vivado v2026.2 (64-bit) | SW Build 6708075 on ...'), '2026.2')
        self.assertIsNone(release_of(''))
        self.assertIsNone(release_of(None))

    def test_schema_and_release_are_enforced_at_open(self):
        self.assertTrue(self.database.meta['schema_version'].startswith('1.7'))
        self.assertEqual(self.database.release, '2026.2')
        with self.assertRaises(ValueError):
            MergedDatabase(self.path, release='2026.1')

    def test_database_is_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.database.db.execute("delete from ip")

    def test_stamp_reports_drift_instead_of_rejecting(self):
        """The shipped file is always extracted from an earlier build of its own release, so a
        live-build mismatch is reported as drift, never raised as SNAPSHOT_MISMATCH."""
        same = self.database.stamp(self.database.build)
        self.assertEqual(same['release'], '2026.2')
        self.assertEqual(same['backend'], 'release_database')
        self.assertNotIn('build_scope', same)
        drifted = self.database.stamp('9999999')
        self.assertEqual(drifted['live_build'], '9999999')
        self.assertIn('build_scope', drifted)

    def test_exact_vlnv_lookup(self):
        result = self.database.lookup('xilinx.com:ip:axi_gpio:2.0', ['CONFIG.C_GPIO_WIDTH'],
                                      self.database.build)
        self.assertEqual(result['status'], 'DB:LOOKUP')
        self.assertFalse(result['missing'])
        row = result['parameters'][0]
        self.assertEqual(row['key'], 'CONFIG.C_GPIO_WIDTH')
        self.assertEqual(row['vlnv'], 'xilinx.com:ip:axi_gpio:2.0')
        self.assertEqual(row['range'], 'RANGE_NONE')
        self.assertTrue(row['requires_live_validation'])

    def test_lookup_reports_missing_without_raising(self):
        result = self.database.lookup('xilinx.com:ip:axi_gpio:2.0', ['CONFIG.NOT_A_PARAMETER'],
                                      self.database.build)
        self.assertEqual(result['missing'], ['CONFIG.NOT_A_PARAMETER'])
        self.assertFalse(result['parameters'])

    def test_lookup_rejects_unknown_vlnv_and_bad_key_shape(self):
        with self.assertRaises(ValueError):
            self.database.lookup('xilinx.com:ip:not_a_real_ip:9.9', ['CONFIG.X'], self.database.build)
        with self.assertRaises(ValueError):
            self.database.lookup('xilinx.com:ip:axi_gpio:2.0', ['NOT;A;KEY'], self.database.build)
        with self.assertRaises(ValueError):
            self.database.lookup('xilinx.com:ip:axi_gpio:2.0', [], self.database.build)

    def test_small_catalog_is_complete_and_bounded(self):
        reply = self.database.catalog('xilinx.com:ip:axi_gpio:2.0', self.database.build)
        self.assertEqual(reply['status'], 'DB:CATALOG')
        self.assertEqual(len(reply['candidates']), reply['count'])
        self.assertLessEqual(reply['count'], 24)

    def test_large_catalog_never_returns_a_partial_table(self):
        reply = self.database.catalog('xilinx.com:ip:ps11:1.0', self.database.build)
        self.assertEqual(reply['status'], 'DB:CATALOG_TOO_LARGE')
        self.assertGreater(reply['count'], 24)
        self.assertEqual(reply['candidates'], [])

    def test_ip_search_finds_the_named_ip(self):
        result = self.database.search('axi gpio', self.database.build, mode='lexical')
        self.assertEqual(result['status'], 'DB:IPS')
        self.assertEqual(result['mode'], 'lexical')
        self.assertIn('xilinx.com:ip:axi_gpio:2.0', [c['vlnv'] for c in result['candidates']])

    def test_parameter_search_is_scoped_to_the_requested_vlnv(self):
        result = self.database.search('gpio width', self.database.build,
                                      vlnv='xilinx.com:ip:axi_gpio:2.0', mode='lexical')
        self.assertEqual(result['status'], 'DB:PARAMETERS')
        self.assertIn('CONFIG.C_GPIO_WIDTH', [c['key'] for c in result['candidates']])

    def test_semantic_mode_is_refused_not_silently_degraded(self):
        with self.assertRaisesRegex(ValueError, 'no embeddings'):
            self.database.search('gpio', self.database.build, mode='semantic')

    def test_search_rejects_malformed_queries(self):
        with self.assertRaises(ValueError):
            self.database.search('', self.database.build)
        with self.assertRaises(ValueError):
            self.database.search('x' * 601, self.database.build)

    def test_sql_like_query_is_data_not_code(self):
        before = self.database.db.execute('select count(*) from ip').fetchone()[0]
        self.database.search('"; DROP TABLE ip; --', self.database.build, mode='lexical')
        self.assertEqual(before, self.database.db.execute('select count(*) from ip').fetchone()[0])

    def test_field_hints_resolve_a_nested_dictionary_key_through_its_subcore(self):
        """MMI_CONFIG.DC_27M_CLK_CTRL_DIVISOR0 has no definition of its own; nested_key maps it
        onto mmi:1.0's own DC_27M_CLK_CTRL_DIVISOR0 parameter, which carries the real help/type."""
        result = self.database.field_hints(['DC_27M_CLK_CTRL_DIVISOR0', 'NOT_A_FIELD'],
                                           self.database.build, vlnv='xilinx.com:ip:ps_wizard:1.0')
        self.assertEqual(result['status'], 'DB:FIELD_HINTS')
        self.assertNotIn('NOT_A_FIELD', result['fields'])
        hint = result['fields']['DC_27M_CLK_CTRL_DIVISOR0']
        self.assertEqual(hint['hint_scope'], 'exact_name_in_other_definitions_not_parent_binding')
        self.assertIn('xilinx.com:ip:mmi:1.0', hint['source_vlnvs'])

    def test_field_hints_carry_the_recorded_xgui_prerequisite(self):
        """probe_fact enabled_by records that this key only takes effect once
        MMI_CONFIG.PSPMC_MANUAL_CLK_ENABLE and MMI_CONFIG.SEG_MODE_ENABLE are set -- the one
        thing a live session can't tell you about a value it refuses to accept yet."""
        result = self.database.field_hints(['DC_27M_CLK_CTRL_DIVISOR0'], self.database.build,
                                           vlnv='xilinx.com:ip:ps_wizard:1.0')
        hint = result['fields']['DC_27M_CLK_CTRL_DIVISOR0']
        self.assertIn('enablement_requires', hint)
        self.assertTrue(any(k.endswith('PSPMC_MANUAL_CLK_ENABLE') for k in hint['enablement_requires']))
        self.assertEqual(hint['enablement_source_scope'], 'xgui_rule_recorded_in_release_database')

    def test_field_hints_rejects_bad_names_and_bounds_the_batch(self):
        with self.assertRaises(ValueError):
            self.database.field_hints(['BAD;NAME'], self.database.build)
        with self.assertRaises(ValueError):
            self.database.field_hints([], self.database.build)
        with self.assertRaises(ValueError):
            self.database.field_hints(['a'] * 17, self.database.build)

    def test_request_rejects_unknown_fields_and_unknown_ops(self):
        with self.assertRaises(ValueError):
            self.database.request({'op': 'ips', 'query': 'gpio', 'sql': 'drop table ip'})
        with self.assertRaises(ValueError):
            self.database.request({'op': 'sql', 'query': 'drop table ip'})

    def test_ip_discovery_must_not_require_a_vlnv_filter(self):
        with self.assertRaises(ValueError):
            self.database.request({'op': 'ips', 'query': 'gpio', 'vlnv': 'xilinx.com:ip:axi_gpio:2.0'})

    def test_a_count_with_a_qualifier_still_asks_for_a_number(self):
        """'4 active lanes' must reach the lane-count parameter, not only 'Enable Active Lanes'."""
        keys = [c['key'] for c in self.database.search(
            '4 active lanes', self.database.build, vlnv='xilinx.com:ip:mipi_csi2_rx_subsystem:6.0')['candidates']]
        self.assertIn('CONFIG.CMN_NUM_LANES', keys)

    def test_unlabelled_enum_entries_show_their_value(self):
        row = self.database.lookup('xilinx.com:ip:mipi_csi2_rx_subsystem:6.0', ['CONFIG.CMN_NUM_LANES'],
                                   self.database.build)['parameters'][0]
        self.assertTrue(row['choices'])
        self.assertTrue(all(c['label'] == c['value'] for c in row['choices']))

    def test_part_support_is_reported_without_reordering(self):
        part = 'xc2ve3558-sfva1440-2MP-e-S'
        query = 'Versal clocking wizard producing 100, 200 and 250 MHz outputs'
        plain = [c['vlnv'] for c in self.database.search(query, self.database.build)['candidates']]
        scoped = self.database.search(query, self.database.build, part=part)['candidates']
        self.assertEqual(plain, [c['vlnv'] for c in scoped])
        status = {c['vlnv']: c['catalog_part_supported'] for c in scoped}
        self.assertFalse(status.get('xilinx.com:ip:clk_wizard:2.0', False))
        gpio = self.database.search('axi_gpio', self.database.build, part=part)['candidates'][0]
        self.assertTrue(gpio['catalog_part_supported'])
        self.assertEqual(gpio['catalog_part_status'], 'Production')

    def test_part_support_answers_availability_without_vivado(self):
        reply = self.database.request({'op': 'part_support', 'part': 'xc2ve3558-sfva1440-2MP-e-S',
                                       'vlnvs': ['xilinx.com:ip:axi_gpio:2.0', 'xilinx.com:ip:axi_noc:1.1',
                                                 'vendor:lib:not_in_catalog:1.0']})
        rows = {r['vlnv']: r for r in reply['rows']}
        self.assertEqual(rows['xilinx.com:ip:axi_gpio:2.0']['status'], 'AVAILABLE')
        self.assertEqual(rows['vendor:lib:not_in_catalog:1.0']['status'], 'UNKNOWN')   # live check decides
        noc = rows['xilinx.com:ip:axi_noc:1.1']
        self.assertEqual(noc['status'], 'WRONG_PART')
        self.assertTrue(noc['example_parts'])
        self.assertGreater(noc['supported_count'], 0)

    def test_an_unsupported_ip_is_offered_a_same_function_supported_one(self):
        """Gen 1 IPs requested on a Gen 2 part (cases 24, 29, 16): the documented Gen 2 IP is
        offered, found from the catalog rather than any table of IP names."""
        part = 'xc2ve3558-sfva1440-2MP-e-S'
        expect = {'xilinx.com:ip:axi_noc:1.1': 'axi_noc2', 'xilinx.com:ip:versal_cips:3.4': 'ps_wizard',
                  'xilinx.com:ip:clk_wizard:2.0': 'clkx5_wiz'}
        rows = self.database.part_support_rows(list(expect), part)
        for row in rows:
            names = [a['vlnv'].split(':')[2] for a in row['alternatives']]
            self.assertEqual(names[0], expect[row['vlnv']], row)
            support = self.database.part_support(part)
            self.assertTrue(all(support.get(a['vlnv']) not in (None, 'Hidden') for a in row['alternatives']))

    def test_part_support_rejects_malformed_requests(self):
        with self.assertRaises(ValueError):
            self.database.part_support_rows(['not a vlnv'], 'xc2ve3558-sfva1440-2MP-e-S')
        with self.assertRaises(ValueError):
            self.database.part_support_rows(['xilinx.com:ip:axi_gpio:2.0'] * 7, 'xc2ve3558-sfva1440-2MP-e-S')
        with self.assertRaises(ValueError):
            self.database.part_support_rows(['xilinx.com:ip:axi_gpio:2.0'], 'bad part;')

    def test_support_table_covers_every_ip_for_a_part(self):
        reply = self.database.request({'op': 'support_table', 'part': 'xc2ve3558-sfva1440-2MP-e-S'})
        table = reply['table']
        self.assertEqual(table['xilinx.com:ip:axi_gpio:2.0'][0], 'AVAILABLE')
        status, count, examples = table['xilinx.com:ip:vcu:1.2']
        self.assertEqual(status, 'WRONG_PART')
        self.assertGreater(count, 0)
        self.assertTrue(1 <= len(examples) <= 4)
        with self.assertRaises(ValueError):
            self.database.request({'op': 'support_table', 'part': 'not-a-recorded-part'})

    def test_an_unsupported_ip_lists_its_supported_category_and_no_word_guess(self):
        """#674: on Versal the AXI Protocol Converter is not supported; SmartConnect does the
        conversion but shares no name or description words with it, only the catalog category
        and interfaces. The category is listed whole, and a weak word-match stand-in (an AXI4-Stream
        clock converter, then a protocol checker) is not named as the replacement."""
        for part in ('xc2ve3558-sfva1440-2MP-e-S', 'xcvc1902-vsva2197-2MP-e-S'):
            row = self.database.part_support_rows(['xilinx.com:ip:axi_protocol_converter:2.1'], part)[0]
            self.assertEqual(row['status'], 'WRONG_PART')
            self.assertIn('smartconnect', [c['name'] for c in row['same_category']])
            self.assertEqual(row['alternatives'], [])
        # Strong stand-ins are unchanged.
        rows = self.database.part_support_rows(['xilinx.com:ip:blk_mem_gen:8.4', 'xilinx.com:ip:axi_noc:1.1'],
                                               'xc2ve3558-sfva1440-2MP-e-S')
        self.assertEqual([r['alternatives'][0]['vlnv'].split(':')[2] for r in rows], ['emb_mem_gen', 'axi_noc2'])

    def test_influences_lists_recorded_links_probe_measured_first_without_board_interfaces(self):
        found = self.database.request({'op': 'influences', 'vlnv': 'xilinx.com:ip:axi_noc2:1.1',
                                       'keys': ['CONFIG.NUM_MC']})['influences']['CONFIG.NUM_MC']
        self.assertEqual(found[0]['param'], 'CONFIG.DDR5_DEVICE_TYPE')
        self.assertEqual(found[0]['source'], 'vivado_probe')
        self.assertIn('Components', found[0]['values'])
        self.assertFalse(any(f['param'].endswith('_BOARD_INTERFACE') for f in found))
        with self.assertRaises(ValueError):
            self.database.influences('xilinx.com:ip:axi_noc2:1.1', ['bad key;'])

    def test_info_reports_schema_and_build(self):
        reply = self.database.request({'op': 'info'})
        self.assertEqual(reply['status'], 'DB:INFO')
        self.assertTrue(reply['schema_version'].startswith('1.7'))
        self.assertEqual(reply['build'], self.database.build)
        self.assertGreater(reply['ip_count'], 0)


@unittest.skipUnless(DB_PATH, SKIP_REASON)
class ReleaseDatabaseAdoptionTests(unittest.TestCase):
    """guard_mcp's auto-discovery: adopted only when no explicit snapshot was mounted, and the
    reserved ipdb session answers from it without ever reaching Vivado."""

    def test_adoption_is_skipped_when_a_snapshot_is_already_configured(self):
        guard = ExecutionGuard(Path(os.environ['TMPDIR']) / 'guard-release-test-configured',
                               database_config='/does/not/need/to/exist.json')
        self.assertIsNone(guard.adopt_release_database(INSTALL, 'Vivado v2026.2 ...'))
        self.assertTrue(guard.has_database())
        self.assertIsNone(guard.release_database_path)

    def test_adoption_is_skipped_for_an_install_with_no_shipped_database(self):
        guard = ExecutionGuard(Path(os.environ['TMPDIR']) / 'guard-release-test-empty')
        self.assertIsNone(guard.adopt_release_database('/nonexistent-install-root', 'Vivado v2026.2 ...'))
        self.assertFalse(guard.has_database())

    def test_adopted_release_database_answers_the_reserved_session_without_vivado(self):
        guard = ExecutionGuard(Path(os.environ['TMPDIR']) / 'guard-release-test-adopted')
        adopted = guard.adopt_release_database(INSTALL, 'Vivado v2026.2 (64-bit) | SW Build 6711342 ...')
        self.assertEqual(adopted, str(DB_PATH))
        self.assertTrue(guard.has_database())

        class Upstream:
            async def call_tool(self, *args, **kwargs):
                raise AssertionError('Database request must never launch/call Vivado')

        async def probe():
            info = await guard.execute(Upstream(), {'session_id': 'ipdb', 'command': '{"op":"info"}'})
            # Read the text block: payload() trips over the installed mcp/pydantic attribute
            # rename (structuredContent -> structured_content), a pre-existing failure.
            data = json.loads(info.content[0].text)
            self.assertEqual(data['used_mode'], 'sqlite_readonly')
            body = json.loads(data['output'])
            self.assertEqual(body['status'], 'DB:INFO')
            self.assertEqual(body['release'], '2026.2')
            self.assertEqual(body['backend'], 'release_database')
        try:
            asyncio.run(probe())
        finally:
            guard.close_database()


if __name__ == '__main__':
    unittest.main()
