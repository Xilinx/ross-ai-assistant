# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Commit-keyed mirror of the live XilinxCEDStore branch.

Each run resolves the branch head and reuses <cache>/<sha> only when that exact
commit is already mirrored, so a new upload always produces a new mirror. Only
small metadata files are fetched for search; a chosen design's folder is
fetched on demand. Mirrors are data for Vivado; Python never executes them.
"""
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

REPO = 'https://github.com/Xilinx/XilinxCEDStore.git'
API = 'https://api.github.com/repos/Xilinx/XilinxCEDStore'
RAW = 'https://raw.githubusercontent.com/Xilinx/XilinxCEDStore'
METADATA = ['xitem.json', 'design.xml', 'init.tcl', 'readme.txt', 'README.md']
FOLDER_LIMIT = 64 * 1024 * 1024


class StoreError(Exception):
    pass


def release_branch(version_text):
    """Store branch for the running Vivado, e.g. 'Vivado v2026.1 (64-bit)' -> '2026.1'."""
    match = re.search(r'\bv(\d{4}\.\d+(?:\.\d+)?)\b', version_text)
    if not match:
        raise StoreError('Cannot read the Vivado release from: ' + version_text[:120])
    return match[1]


def safe_root(rel):
    if not re.fullmatch(r'ced(/[A-Za-z0-9_.+ -]+)+', rel) or '..' in rel.split('/'):
        raise StoreError('Unexpected design folder ' + rel)
    return rel


class Mirror:
    def __init__(self, cache, branch, bundled=None, timeout=180):
        self.cache = Path(cache).resolve()
        if self.cache == Path('/home') or Path('/home') in self.cache.parents:
            raise StoreError('Mirror cache must be outside /home: ' + str(self.cache))
        self.branch, self.bundled, self.timeout = branch, bundled, timeout
        self.path = self.sha = self.source = None
        self.notes = []

    def git(self, *args, cwd=None):
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='/bin/false')
        result = subprocess.run(['git', *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=self.timeout)
        if result.returncode:
            raise StoreError('git ' + args[0] + ' failed: ' + (result.stderr or result.stdout)[-300:])
        return result.stdout

    def http(self, url, data=True):
        request = urllib.request.Request(url, headers={'User-Agent': 'vivado-ced-configurator'})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read(FOLDER_LIMIT + 1)
        if len(body) > FOLDER_LIMIT:
            raise StoreError('Response too large: ' + url)
        return json.loads(body) if not data else body

    def resolve(self):
        """Mirror the branch head. Falls back to HTTP, then to the bundled offline copy."""
        self.cache.mkdir(parents=True, exist_ok=True)
        try:
            return self.resolve_git()
        except (StoreError, OSError, subprocess.TimeoutExpired) as exc:
            self.notes.append('git unavailable: ' + str(exc)[:200])
        try:
            return self.resolve_http()
        except (StoreError, OSError, ValueError) as exc:
            self.notes.append('http unavailable: ' + str(exc)[:200])
        return self.resolve_bundled()

    def info(self):
        return dict(path=str(self.path), sha=self.sha, branch=self.branch, source=self.source,
                    **({'notes': self.notes} if self.notes else {}))

    def resolve_git(self):
        heads = self.git('ls-remote', REPO, 'refs/heads/' + self.branch).split()
        if not heads or not re.fullmatch(r'[0-9a-f]{40}', heads[0]):
            raise StoreError('Branch ' + self.branch + ' not found in ' + REPO)
        sha = heads[0]
        target = self.cache / sha
        if not (target / '.git').is_dir():
            temporary = self.cache / (sha + '.tmp-' + str(os.getpid()))
            shutil.rmtree(temporary, ignore_errors=True)
            self.git('clone', '--quiet', '--depth', '1', '--branch', self.branch, '--filter=blob:none', '--sparse',
                     REPO, str(temporary))
            self.git('sparse-checkout', 'set', '--no-cone', '/catalog/', *METADATA, cwd=temporary)
            actual = self.git('rev-parse', 'HEAD', cwd=temporary).strip()
            if actual != sha:
                shutil.rmtree(temporary, ignore_errors=True)
                raise StoreError('Branch moved during clone; retry')
            try:
                temporary.rename(target)
            except OSError:
                shutil.rmtree(temporary, ignore_errors=True)
        self.path, self.sha, self.source = target, sha, 'live_git'
        return self.info()

    def resolve_http(self):
        sha = self.http(API + '/commits/' + self.branch, data=False)['sha']
        if not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise StoreError('Unexpected commit id')
        target = self.cache / (sha + '-http')
        if not (target / '.complete').is_file():
            tree = self.http(API + '/git/trees/' + sha + '?recursive=1', data=False)
            if tree.get('truncated'):
                raise StoreError('Store tree listing truncated')
            self.tree = [t['path'] for t in tree['tree'] if t['type'] == 'blob']
            for path in self.tree:
                if path.startswith('catalog/') or path.rsplit('/', 1)[-1] in METADATA:
                    self.download(sha, path, target)
            (target / '.tree.json').write_text(json.dumps(self.tree))
            (target / '.complete').write_text(sha)
        self.path, self.sha, self.source = target, sha, 'live_http'
        return self.info()

    def download(self, sha, path, target):
        if '..' in path.split('/'):
            raise StoreError('Unexpected path ' + path)
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.http(RAW + '/' + sha + '/' + urllib.request.quote(path)))

    def resolve_bundled(self):
        if not self.bundled or not (Path(self.bundled) / 'ced').is_dir():
            raise StoreError('No live store and no bundled copy; ' + '; '.join(self.notes))
        version = Path(self.bundled) / 'version.dat'
        text = version.read_text() if version.is_file() else ''
        match = re.search(r'GIT_COMMIT_ID:\s*([0-9a-f]{40})', text)
        self.path, self.sha, self.source = Path(self.bundled), match[1] if match else None, 'bundled_offline'
        return self.info()

    @contextlib.contextmanager
    def locked(self):
        """Serialize writes to a shared commit mirror across concurrent sessions."""
        with open(self.cache / (str(self.sha) + '.lock'), 'w') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def add_design(self, rel):
        """Fetch one design folder completely; returns its absolute path."""
        rel = safe_root(rel)
        folder = self.path / rel
        if self.source == 'live_git':
            with self.locked():
                # sparse-checkout add rewrites the pattern list and re-checks out
                # the tree even for a folder already present. begin adds its top 8
                # designs under this lock, so parallel sessions queued behind each
                # other: ced-gen_M3's begin outlasted the client's 60 s limit.
                patterns = self.path / '.git' / 'info' / 'sparse-checkout'
                listed = patterns.is_file() and '/' + rel + '/' in patterns.read_text().split('\n')
                if not (listed and folder.is_dir()):
                    self.git('sparse-checkout', 'add', '/' + rel + '/', cwd=self.path)
        elif self.source == 'live_http':
            tree = json.loads((self.path / '.tree.json').read_text())
            files = [p for p in tree if p.startswith(rel + '/')]
            with self.locked():
                for path in files:
                    if not (self.path / path).is_file():
                        self.download(self.sha, path, self.path)
        if not folder.is_dir():
            raise StoreError('Design folder missing after fetch: ' + rel)
        return folder
