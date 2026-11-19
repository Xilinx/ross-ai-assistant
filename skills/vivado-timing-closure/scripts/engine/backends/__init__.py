#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Compute backends: submit, status, kill, logs. See base.py."""

from __future__ import print_function

import shlex

from .base import (ComputeBackend, JobHandle, JobStatus, Resources,  # noqa: F401
                   SubmitError, LauncherUnavailable, load_handle, save_handle, clear_handle,
                   PENDING, RUNNING, DONE, FAILED, UNKNOWN, TERMINAL)

_REGISTRY = {}


def register(name, factory):
    _REGISTRY[name] = factory


def available_names():
    return sorted(_REGISTRY)


def make(name, cfg=None, output_dir=None):
    """
    Build a backend by name from the `backend:` config block.

    `output_dir` is optional and only used by backends that need to keep
    per-flow state of their own (the session backend isolates its MCP server's
    state directory there, so it cannot see an editor's sessions).
    """
    cfg = cfg or {}
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise ValueError("unknown backend %r (have: %s)"
                         % (name, ", ".join(available_names())))
    try:
        return factory(cfg, output_dir=output_dir)
    except TypeError:
        return factory(cfg)


def _make_local(cfg):
    from .local import LocalBackend
    sub = (cfg.get("local") or {})
    return LocalBackend(vivado=cfg.get("vivado", "vivado"),
                        extra_args=sub.get("extra_args"))


def _make_lsf(cfg):
    from .lsf import LSFBackend
    sub = (cfg.get("lsf") or {})
    return LSFBackend(vivado=cfg.get("vivado", "vivado"),
                      env_script=sub.get("env_script"),
                      extra_args=sub.get("extra_args"))


def _make_session(cfg, output_dir=None):
    from . import session
    return session.make(cfg, output_dir=output_dir)


register("local", _make_local)
register("lsf", _make_lsf)
register("session", _make_session)


def resources_from_config(cfg, backend_name, job_name=None):
    """
    The Resources a run asks for, from the backend's config block.

    A session backend chosen by `auto` usually has no block of its own: the
    site wrote its farm settings for bsub. It then borrows the lsf block, with
    the OS selection reduced to the bare type vivado_lsf expects ("osver==ws8"
    -> "ws8"; the server builds the select clause itself).
    """
    cfg = cfg or {}
    sub = dict(cfg.get(backend_name) or {})
    if backend_name == "session":
        lsf = cfg.get("lsf") or {}
        for key in ("queue", "memory_mb", "slots", "os_type", "runtime_limit_min"):
            if sub.get(key) is None and lsf.get(key) is not None:
                sub[key] = lsf.get(key)
        os_type = sub.get("os_type")
        if os_type and "==" in str(os_type):
            sub["os_type"] = str(os_type).split("==", 1)[1].strip()
    return Resources(
        queue=sub.get("queue"),
        memory_mb=sub.get("memory_mb"),
        slots=sub.get("slots"),
        os_type=sub.get("os_type"),
        runtime_limit_min=sub.get("runtime_limit_min"),
        job_name=job_name,
        extra=_bsub_args(sub.get("extra_bsub_args")))


def _bsub_args(extra):
    """extra_bsub_args as a list, or one string of options (tc setup --extra-lsf-options)."""
    if isinstance(extra, str):
        return shlex.split(extra)
    return extra
