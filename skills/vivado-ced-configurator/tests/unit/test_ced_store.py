# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Commit-keyed mirror behavior against a local git repository. Run with TMPDIR in scratch."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / 'scripts'))
import ced_store  # noqa: E402
from ced_store import Mirror, StoreError, release_branch, safe_root  # noqa: E402


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.com', GIT_COMMITTER_NAME='t',
               GIT_COMMITTER_EMAIL='t@example.com')
    return subprocess.run(['git', *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class HelperTests(unittest.TestCase):
    def test_release_branch_from_vivado_version(self):
        self.assertEqual(release_branch('Vivado v2026.1 (64-bit)\nSW Build 6511674'), '2026.1')
        self.assertEqual(release_branch('Vivado v2026.1.1 (64-bit)'), '2026.1.1')
        with self.assertRaises(StoreError):
            release_branch('unknown tool')

    def test_design_roots_are_validated(self):
        self.assertEqual(safe_root('ced/Xilinx/IPI/Zynq_UltraScale+_MPSoC_Design_Presets'),
                         'ced/Xilinx/IPI/Zynq_UltraScale+_MPSoC_Design_Presets')
        for bad in ['ced/../etc', 'ced/Xilinx/*', 'other/Xilinx/IPI/x', '/ced/x']:
            with self.assertRaises(StoreError):
                safe_root(bad)

    def test_cache_under_home_is_refused(self):
        with self.assertRaises(StoreError):
            Mirror('/home/someone/ced-cache', '2026.1')


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / 'origin'
        design = self.origin / 'ced/Xilinx/IPI/Alpha'
        design.mkdir(parents=True)
        (self.origin / 'catalog').mkdir()
        (self.origin / 'catalog/vivado_2026.1.json').write_text('{"catalog": {"items": []}}')
        (design / 'design.xml').write_text('<ExampleDesign><Name>alpha</Name></ExampleDesign>')
        (design / 'init.tcl').write_text('proc addOptions {a b} {}\n')
        (design / 'alpha_bd.tcl').write_text('# full design body\n')
        git(self.origin, 'init', '-q', '-b', '2026.1')
        git(self.origin, 'config', 'uploadpack.allowFilter', 'true')
        git(self.origin, 'add', '.')
        git(self.origin, 'commit', '-q', '-m', 'first')
        self.cache = root / 'cache'
        self.saved = ced_store.REPO
        ced_store.REPO = 'file://' + str(self.origin)

    def tearDown(self):
        ced_store.REPO = self.saved
        self.tmp.cleanup()

    def test_sparse_metadata_mirror_is_keyed_by_commit_and_designs_fetch_on_demand(self):
        first = Mirror(self.cache, '2026.1')
        info = first.resolve()
        head = git(self.origin, 'rev-parse', 'HEAD')
        self.assertEqual((info['sha'], info['source']), (head, 'live_git'))
        folder = Path(info['path']) / 'ced/Xilinx/IPI/Alpha'
        self.assertTrue((folder / 'design.xml').is_file())
        self.assertFalse((folder / 'alpha_bd.tcl').exists())
        self.assertEqual(first.add_design('ced/Xilinx/IPI/Alpha'), folder)
        self.assertTrue((folder / 'alpha_bd.tcl').is_file())

        (self.origin / 'ced/Xilinx/IPI/Alpha/init.tcl').write_text('proc addOptions {a b} {return {}}\n')
        git(self.origin, 'commit', '-qam', 'second')
        second = Mirror(self.cache, '2026.1').resolve()
        self.assertNotEqual(second['sha'], head)
        self.assertNotEqual(second['path'], info['path'])
        self.assertIn('return {}', (Path(second['path']) / 'ced/Xilinx/IPI/Alpha/init.tcl').read_text())

    def test_unreachable_store_falls_back_to_the_bundled_copy(self):
        ced_store.REPO = 'file://' + str(Path(self.tmp.name) / 'missing')
        bundled = Path(self.tmp.name) / 'bundled'
        (bundled / 'ced').mkdir(parents=True)
        (bundled / 'version.dat').write_text('GIT_BRANCH: 2026.1\nGIT_COMMIT_ID: ' + 'a' * 40 + '\n')
        mirror = Mirror(self.cache, '2026.1', bundled=bundled)
        mirror.resolve_http = lambda: (_ for _ in ()).throw(OSError('offline'))
        info = mirror.resolve()
        self.assertEqual((info['source'], info['sha']), ('bundled_offline', 'a' * 40))
        self.assertEqual(len(info['notes']), 2)


if __name__ == '__main__':
    unittest.main()
