#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
staging.py -- put a checkpoint where the farm can read it.

A farm job opens the DCP and writes its run folder by the paths `tc close`
generated. When those paths are on the submitting host's local disk (a CI
runner's /scratch, a workspace under ~/.cache), the job starts on another host,
finds nothing, and fails. `stage_root` in .tc/engine.yaml names a shared folder
(NFS) every farm host mounts: `tc close` copies the checkpoint there, once per
file version, and keeps the flow's output beside the copy.

Only the checkpoint is copied. Its run folders are created by the flow itself,
under the staged copy's folder, so everything a farm job touches is shared.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import hashlib
import json
import os
import shutil

from . import config

MARKER = "STAGED_FROM.json"


class StagingError(Exception):
    pass


def root_for(cfg, backend):
    """
    The stage root to use, or None. Only farm launches need one: a local
    backend runs Vivado on this host, which can read the checkpoint where it is.
    """
    root = config.get(cfg, "stage_root")
    if not root or backend == "local":
        return None
    root = os.path.expanduser(str(root))
    if not os.path.isabs(root):
        raise StagingError("stage_root must be an absolute path on a shared filesystem, "
                           "got %r" % root)
    return root


def under(path, root):
    """True when path (resolved) is root or inside it."""
    p = os.path.realpath(os.path.abspath(path))
    r = os.path.realpath(os.path.abspath(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def _key(real, st):
    text = "%s\0%d\0%d" % (real, int(getattr(st, "st_mtime_ns", st.st_mtime * 1e9)), st.st_size)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def stage_dcp(dcp, root):
    """
    (dcp to use, the original's real path or None).

    A checkpoint already under root is used where it is. Otherwise it is copied
    to <root>/<design>-<hash>/<name>.dcp, where the hash covers its real path,
    modification time and size: re-running close on the same file finds the same
    copy (so the flow resumes), and an edited checkpoint gets a new folder
    instead of overwriting one a flow already uses.
    """
    real = os.path.realpath(dcp)
    if under(real, root):
        return dcp, None
    st = os.stat(real)
    name = os.path.basename(real)
    design = name[:-4] if name.lower().endswith(".dcp") else name
    folder = os.path.join(root, "%s-%s" % (design, _key(real, st)))
    target = os.path.join(folder, name)
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
            # Group-writable: a CI account and its owner share one stage root.
            os.chmod(folder, 0o2775)
        if not (os.path.isfile(target) and os.path.getsize(target) == st.st_size):
            tmp = "%s.tmp.%d" % (target, os.getpid())
            shutil.copyfile(real, tmp)
            os.chmod(tmp, 0o664)
            os.replace(tmp, target)
        marker = os.path.join(folder, MARKER)
        if not os.path.isfile(marker):
            tmp = "%s.tmp.%d" % (marker, os.getpid())
            with open(tmp, "w") as fh:
                json.dump({"source": real, "size": st.st_size, "mtime": st.st_mtime}, fh,
                          indent=2)
            os.replace(tmp, marker)
    except OSError as exc:
        raise StagingError("could not stage %s under %s: %s" % (real, root, exc))
    return target, real


# ---------------------------------------------------------------------------
# The skill's Tcl assets
# ---------------------------------------------------------------------------
# A generated run.tcl sources Tcl from the skill (early_flow_fix.tcl, the
# planner's evidence.tcl, skill detect/fix scripts). With the skill installed on
# the submitting host's local disk, a farm job cannot read them: ZB CI run
# 40548363570's planner evidence job failed on .../assets/planner/evidence.tcl,
# and Stage 1's early fixes were skipped silently (their source is caught). The
# assets tree is staged beside the checkpoints, once per version of the tree,
# and TCA_ASSETS_ROOT points the generators at the copy.
ASSETS_ENV = "TCA_ASSETS_ROOT"
ASSETS_DIR = ".assets"


def _tree_key(src):
    """A short hash of the tree's relative paths, sizes and modification times."""
    h = hashlib.sha1()
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.endswith(".pyc"):
                continue
            path = os.path.join(dirpath, name)
            st = os.stat(path)
            h.update(("%s\0%d\0%d\n" % (os.path.relpath(path, src), st.st_size,
                                        int(getattr(st, "st_mtime_ns", st.st_mtime * 1e9))))
                     .encode("utf-8"))
    return h.hexdigest()[:10]


def _share(tree):
    """
    Group-writable folders whose new files keep the folder's group (mode 2775),
    group-readable files: a CI account and its owner share the tree.
    """
    for dirpath, dirnames, filenames in os.walk(tree):
        os.chmod(dirpath, 0o2775)
        for name in filenames:
            path = os.path.join(dirpath, name)
            os.chmod(path, (os.stat(path).st_mode & 0o111) | 0o664)


def stage_assets(src, root):
    """
    The staged copy of the skill's assets tree under <root>/.assets/<hash>/.

    Copied to a temporary sibling and renamed into place, so a folder that
    exists is always complete; a version already staged is reused.
    """
    if not os.path.isdir(src):
        raise StagingError("the skill has no assets folder at %s" % src)
    parent = os.path.join(root, ASSETS_DIR)
    dest = os.path.join(parent, _tree_key(src))
    if os.path.isdir(dest):
        return dest
    tmp = "%s.tmp.%d" % (dest, os.getpid())
    try:
        if not os.path.isdir(parent):
            os.makedirs(parent)
            os.chmod(parent, 0o2775)
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        _share(tmp)
        try:
            os.rename(tmp, dest)
        except OSError:
            # Another close staged the same version meanwhile: use theirs.
            if not os.path.isdir(dest):
                raise
            shutil.rmtree(tmp, ignore_errors=True)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise StagingError("could not stage the skill's assets under %s: %s" % (parent, exc))
    return dest


def use_assets(path):
    """Point this process, and every generator it starts, at a staged assets tree."""
    os.environ[ASSETS_ENV] = path


def assets_from_state(output_dir):
    """The staged assets tree a flow recorded in its state.json, if it still exists."""
    try:
        with open(os.path.join(output_dir, "state.json")) as fh:
            path = (json.load(fh) or {}).get("assets_root")
    except (OSError, ValueError):
        return None
    return path if path and os.path.isdir(path) else None


def local_skill_reference(run_tcl, skill_root):
    """
    The local skill folder a run.tcl names, or None. A farm job cannot read it,
    so a staged flow must not submit such a run.
    """
    try:
        with open(run_tcl, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    for form in {os.path.abspath(skill_root), os.path.realpath(skill_root)}:
        if form.rstrip(os.sep) + os.sep in text:
            return form
    return None
