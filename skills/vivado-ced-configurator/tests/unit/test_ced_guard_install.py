# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The guard has to boot from an installed copy of the skill, with nothing set for it.

A run installs the skill into <run>/.claude/skills/<name>/ and launches
examples/ced_guard.py from there. ced_guard.py resolved its bundled engine as
HERE.parents[1]/'engine' -- the skills directory, not the skill -- so the import of
guard_mcp raised ModuleNotFoundError, the server exited before it spoke a word of
MCP, and the model saw only "vivado (CONNECTION_CLOSED)" with no design built. It
passed everywhere it was tried because every caller had CED_LITE_SKILL set.

So this test installs the skill the way a run does and launches the guard with that
variable removed. Importing ced_guard in-process would not catch it: this file sits
inside the source tree, where the broken path happens to be one level from an
engine/ that exists. It has to be a subprocess, from a copy, over real stdio.
"""
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
FAKE_UPSTREAM = SKILL / 'tests' / 'fixtures' / 'fake_vivado_mcp.py'

try:
    import mcp
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # pragma: no cover - the guard cannot run without it either
    ClientSession = None


def install(root):
    """Copy the skill into root/.claude/skills/ the way run_ced.py does."""
    target = Path(root, '.claude', 'skills', SKILL.name)
    shutil.copytree(SKILL, target, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', 'tests'))
    return target


def upstream_launcher(root):
    """An executable the guard can exec, since it runs --upstream as a binary.

    PYTHONPATH is set explicitly because the guard hands the upstream a private
    HOME, which moves the per-user site-packages the mcp package may live in.
    """
    site = str(Path(mcp.__file__).resolve().parents[1])
    path = Path(root, 'fake-vivado-mcp')
    path.write_text('#!/bin/sh\nPYTHONPATH="' + site + '${PYTHONPATH:+:$PYTHONPATH}" '
                    'exec "' + sys.executable + '" "' + str(FAKE_UPSTREAM) + '" "$@"\n')
    path.chmod(0o755)
    return path


@unittest.skipIf(ClientSession is None, 'the mcp package is not importable')
class GuardInstallTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_guard_serves_its_tools_from_an_installed_copy_with_no_engine_override(self):
        root = tempfile.mkdtemp(prefix='ced-install-')
        self.assertNotIn(Path('/home'), Path(root).resolve().parents,
                         'the guard refuses paths under /home; point TMPDIR outside it')
        self.addCleanup(shutil.rmtree, root, True)
        skill = install(root)
        self.assertTrue((skill / 'engine' / 'scripts' / 'guard_mcp.py').is_file())
        # The failure under test: nothing tells the guard where its engine is.
        env = {k: v for k, v in os.environ.items() if k != 'CED_LITE_SKILL'}
        env['PYTHONPYCACHEPREFIX'] = str(Path(root, 'pycache'))
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(skill / 'examples' / 'ced_guard.py'),
                  '--upstream', str(upstream_launcher(root)),
                  '--evidence', str(Path(root, 'evidence')),
                  '--ced-cache', str(Path(root, 'cache'))],
            env=env)
        log = Path(root, 'guard-stderr.log')
        try:
            async with AsyncExitStack() as stack:
                # stderr is kept: a crashed import surfaces only as a closed pipe otherwise.
                errlog = stack.enter_context(log.open('w+'))
                read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                tools = sorted(t.name for t in (await session.list_tools()).tools)
        except Exception as exc:
            text = log.read_text() if log.exists() else ''
            raise AssertionError('the guard did not serve from the installed copy: ' + str(exc) +
                                 '\n--- guard stderr ---\n' + text[-3000:]) from exc
        self.assertEqual(tools, ['ced_apply_phase2', 'vivado_execute', 'vivado_start', 'vivado_stop'])

    def test_the_guard_defaults_its_engine_to_the_one_it_ships_with(self):
        """The same bug, stated where it is readable: engine/ is the skill's child, not its sibling.

        The guard moved to examples/ when the skill stopped needing it (it is for
        local models only), so the path it derives is the skill root either way.
        """
        source = (SKILL / 'examples' / 'ced_guard.py').read_text()
        self.assertIn("CED_LITE_SKILL', SKILL / 'engine'", source)
        self.assertNotIn('parents[1]', source)


if __name__ == '__main__':
    unittest.main()
