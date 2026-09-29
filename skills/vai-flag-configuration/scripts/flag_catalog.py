# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""Load the catalog of legal non-default configuration flags.

At runtime the catalog combines:
  1. Documented ``vaiml_config`` tuning fields (always present).
  2. Public frontend flags from the shipped ``fe-args.html`` catalog.

``vitisai_config`` / driver keys ``fe_args`` and ``fe_experiment`` are aliases;
likewise ``group_args`` and ``experiments``. Logs may use either spelling.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Public, externally reachable source for the EP configuration fields. The
# in-repo vitisai_config_help draft is not shipped with the skill, so citing
# it would hand the user a path they cannot open.
_CONFIG_DOC = "https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/ep-config-file.html"


# Relevance levels. A flexml-compile run is expensive, so a flag is only worth
# proposing if it can plausibly change the compiled result for THIS model.
PROPOSABLE = "proposable"
NEEDS_CONSENT = "needs-consent"  # only with the user's explicit agreement
EXCLUDED = "excluded"  # never proposed


@dataclass
class FlagSpec:
    name: str
    source: str  # "vitisai-config" | "fe-args" | "dmac-fe-opt"
    type: str  # "bool" | "int" | "str" | "list[str]" | ...
    default: Any
    description: str
    path: str  # repo-relative source material to cite
    relevance: str = PROPOSABLE
    exclusion_reason: str = ""  # cited reason; required when not PROPOSABLE
    goal_conflicts: tuple[str, ...] = ()  # objectives this flag works against
    use_case: str = ""  # skill routing token from fe-experiment YAML (when public)


_RAW_VITISAI_CONFIG_FIELDS: list[FlagSpec] = [
    FlagSpec(
        "provider",
        "vitisai-config",
        "str",
        "vai",
        "Backend provider. Typically 'vai' for Vitis AI.",
        _CONFIG_DOC,
    ),
    FlagSpec(
        "cacheDir",
        "vitisai-config",
        "str",
        "./cache",
        "Directory where the compiled model cache is stored.",
        _CONFIG_DOC,
    ),
    FlagSpec(
        "cacheKey",
        "vitisai-config",
        "str",
        "",
        "Unique key identifying the cached model; changing it forces recompilation.",
        _CONFIG_DOC,
    ),
    FlagSpec(
        "target",
        "vitisai-config",
        "str",
        "AMD_AIE2_Nx4_Overlay",
        "Hardware target/overlay to compile for.",
        _CONFIG_DOC,
    ),
    FlagSpec(
        "verbosity",
        "vitisai-config",
        "int",
        0,
        "Logging verbosity level. 0 = silent, higher = more verbose.",
        _CONFIG_DOC,
    ),
    FlagSpec(
        "config_file",
        "vitisai-config",
        "str",
        "",
        "Path to the config file itself (used internally by the EP).",
        _CONFIG_DOC,
    ),
    FlagSpec(
        "encryptionKey",
        "vitisai-config",
        "str",
        "",
        "Optional encryption key for model protection.",
        _CONFIG_DOC,
    ),
    FlagSpec(
        "subgraphConfig",
        "vitisai-config",
        "list[str]",
        None,
        "Per-subgraph configuration overrides for multi-target deployments.",
        _CONFIG_DOC,
    ),
    FlagSpec(
        "device",
        "vitisai-config",
        "str",
        None,
        "Target device (mandatory). EP doc string e.g. ve2-xc2ve3858; see supported-devices table.",
        f"{_CONFIG_DOC}#device",
    ),
    FlagSpec(
        "optimize_level",
        "vitisai-config",
        "int",
        2,
        "Optimization level 1/2/3. O3 applies more aggressive latency optimizations.",
        f"{_CONFIG_DOC}#optimize-level",
    ),
    FlagSpec(
        "threshold_gops_percent",
        "vitisai-config",
        "int",
        20,
        "GOPS threshold (0-100): ops above run on NPU, below on CPU.",
        f"{_CONFIG_DOC}#threshold-gops-percent",
    ),
    FlagSpec(
        "dp_size",
        "vitisai-config",
        "int",
        1,
        "Data parallelism: replicate the model for concurrent requests.",
        f"{_CONFIG_DOC}#dp-size",
    ),
    FlagSpec(
        "tp_size",
        "vitisai-config",
        "int",
        0,
        "Tensor parallelism: partition one inference across units (0 = auto).",
        f"{_CONFIG_DOC}#tp-size",
    ),
    FlagSpec(
        "keep_outputs",
        "vitisai-config",
        "bool",
        False,
        "Retain vaiml cache artifacts (MLIR, spilling CSV). Always true for this skill.",
        f"{_CONFIG_DOC}#keep-outputs",
    ),
    FlagSpec(
        "preferred_data_storage",
        "vitisai-config",
        "str",
        "auto",
        "Intermediate data layout: vectorized, unvectorized, or auto (compiler picks).",
        f"{_CONFIG_DOC}#preferred-data-storage",
    ),
]


# --------------------------------------------------------------------------
# Relevance policy
# --------------------------------------------------------------------------
# ``public: true`` says a flag is published for external skill use, not
# that it is worth a compile.
# These tables narrow the proposable set; every entry cites what the flag's own
# description (or the doc) says. Flags are TAGGED, never dropped, so the agent
# can still answer "why didn't you try X?" -- see the SKILL.md Transparency
# rule. `test_policy_names_all_exist_in_the_catalog` guards against a name here
# drifting out of sync with the compiler sources.

EXCLUDED_FLAGS: dict[str, str] = {
    # -- affects only what the compiler emits, not what it compiles ---------
    "elide-large-constants-in-chained-mlir": (
        "only elides constants in the emitted '.chained_kernel.tosa.mlir'; "
        "cannot change the compiled result"
    ),
    # -- internal IR plumbing, driven by another experiment ------------------
    "enable-l3-ir": (
        "IR conversion to the L3 data format; per its description it is turned "
        "on automatically by the 'flat-format-ir' experiment, not tuned directly"
    ),
    "use-quant-types": (
        "internal representation change (DQ/Q nodes to quant type annotations), "
        "not a per-model tuning knob"
    ),
    # -- needs an input artifact the skill cannot synthesize -----------------
    "use-hsi-json-file": "requires a user-supplied FlexMLRT HSI JSON file",
    "fusion-sequence-file": (
        "requires a hand-written fusion pass-sequence file; without one "
        "the default sequence is used anyway"
    ),
    "tsg-config": (
        "requires a generated tiling configuration JSON that this skill cannot produce"
    ),
    # -- retired / internal, per their own description -----------------------
    "disable-conv-maxpool-bf16-chain": (
        "its own description says 'This flag should be DEPRECATED' (CR-1210672)"
    ),
    "use-bitwidth-based-dtype-in-value-agnostic-tgs": (
        "its own description marks it 'Internal option'"
    ),
    # -- vitisai_config fields that steer the compiler run, not its output ---
    "provider": "selects the execution-provider backend; not an optimization knob",
    "cacheDir": "only where the compiled-model cache is written",
    "cacheKey": "only forces recompilation; no effect on the compiled result",
    "config_file": "path to the config itself, used internally by the EP",
    "encryptionKey": "model protection; no effect on the compiled result",
    "verbosity": "logging verbosity only",
    "device": (
        "mandatory user-supplied target device; set from --device at startup, "
        "not explored as an optimization knob"
    ),
    "full-offloading-required": (
        "verification gate only: fails compilation when offload is incomplete; "
        "does not change the compiled graph — apply when the user selects an "
        "offload goal, not during flag exploration"
    ),
}

NEEDS_CONSENT_FLAGS: dict[str, str] = {
    # -- trades numerical accuracy; this skill does no correctness checking --
    "unsafe-math-optimizations": (
        "its own description says the transformations are not numerically "
        "equivalent and that the small rounding difference 'is an assumption, "
        "not a guarantee'; validating accuracy is a Non-goal of this skill"
    ),
    "qdq-tolerant-check": (
        "tolerates a DQ/Q round-trip difference instead of requiring bit-exact"
    ),
    "qdq-fold-tolerant-check": (
        "tolerates a DQ/Q round-trip difference when folding DQ->Q pairs"
    ),
    "use-accurate-mode": "trades speed against accuracy per operation",
    "freeze-fusion": "preserves the original dtype of frozen operations",
    "freeze-dtype": "controls the dtype freeze pass; changes op dtypes",
    "indices-are-positive": (
        "not a compiler choice: the user/model creator must guarantee the "
        "Gather indices are non-negative, which the skill cannot verify"
    ),
    # -- deployment contract: what the runtime is expected to do -------------
    "edge-quantization-in-rt": "changes the model's runtime I/O contract",
    "input-quantization-in-rt": "changes the model's runtime input contract",
    "output-dequantization-in-rt": "changes the model's runtime output contract",
    "layout-transformation-in-rt": (
        "moves edge layout transformations to FLEXMLRT/VART; only valid if the "
        "deployment actually provides them"
    ),
    "enable-input-without-rt-support": "suppresses RT support at the input edges",
    "enable-output-without-rt-support": "suppresses RT support at the output edges",
    "force-c4-in-rt": "forces an HCWNC4 runtime layout at the NPU boundary",
}

GOAL_CONFLICTS: dict[str, tuple[str, ...]] = {
    # These move work ONTO the CPU by construction: a legitimate latency or
    # compile-success tool, but self-defeating when the goal is max offload.
    "ops-blocklist": ("offload",),
    "unwrap-input-onnx-edge-by-op-type": ("offload",),
    "unwrap-output-onnx-edge-by-op-type": ("offload",),
    "unwrap-kernels-tgs-from-edges": ("offload",),
    "small-tensor-threshold-unwrapping": ("offload",),
}


def flags_for_use_case(use_case: str, catalog: list[FlagSpec]) -> list[str]:
    """Flag names whose public catalog entry carries ``use_case``."""
    return sorted(
        spec.name
        for spec in catalog
        if spec.source == "fe-args" and spec.use_case == use_case
    )


def classify(spec: FlagSpec) -> FlagSpec:
    """Apply the relevance policy to a freshly parsed flag."""
    if spec.name in EXCLUDED_FLAGS:
        return replace(
            spec, relevance=EXCLUDED, exclusion_reason=EXCLUDED_FLAGS[spec.name]
        )
    if spec.name in NEEDS_CONSENT_FLAGS:
        return replace(
            spec,
            relevance=NEEDS_CONSENT,
            exclusion_reason=NEEDS_CONSENT_FLAGS[spec.name],
        )
    if spec.name in GOAL_CONFLICTS:
        return replace(spec, goal_conflicts=GOAL_CONFLICTS[spec.name])
    return spec


VITISAI_CONFIG_FIELDS: list[FlagSpec] = [
    classify(spec) for spec in _RAW_VITISAI_CONFIG_FIELDS
]


def proposable(
    catalog: list[FlagSpec],
    *,
    objectives: list[str] | tuple[str, ...] = (),
    autonomous: bool = True,
) -> list[FlagSpec]:
    """Flags worth spending a compile on for these objectives.

    Drops hard exclusions always, consent-gated flags in autonomous mode, and
    flags that work against one of the selected objectives.
    """
    goals = set(objectives)
    out: list[FlagSpec] = []
    for spec in catalog:
        if spec.relevance == EXCLUDED:
            continue
        if spec.relevance == NEEDS_CONSENT and autonomous:
            continue
        if goals & set(spec.goal_conflicts):
            continue
        out.append(spec)
    return out


def excluded_flags(catalog: list[FlagSpec]) -> list[FlagSpec]:
    """Flags the policy holds back, each with the reason to cite for it."""
    return [spec for spec in catalog if spec.relevance != PROPOSABLE]


# --------------------------------------------------------------------------
# fe-args.html parsing (bundled catalog source)
# --------------------------------------------------------------------------


class _FeExperimentHTMLParser(HTMLParser):
    """Extract frontend flag rows from the shipped public HTML catalog.

    Each flag is a ``<tr id="fe-flag-NAME">``. Columns are resolved from the
    table's ``<th>`` headers rather than by position: an earlier standalone HTML
    generator gained a "Kind" column as the SECOND cell, which shifts every later
    column right by one. A positional parse would silently read availability as
    the description and the description as the type, producing a plausible but
    wrong catalog.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, list[str]]] = []
        self.headers: list[str] = []
        self._row_id = ""
        self._in_row = False
        self._in_cell = False
        self._in_head_row = False
        self._headers_done = False
        self._cells: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "tr":
            rid = attr.get("id") or ""
            self._in_row = rid.startswith("fe-flag-")
            self._row_id = rid
            self._in_head_row = False
            self._cells = []
        elif tag == "th" and not self._headers_done:
            # Only the first header row is read; later tables (e.g. the removed
            # flags one) repeat the same layout.
            self._in_head_row = True
            self._in_cell = True
            self._buf = []
        elif tag == "td" and self._in_row:
            self._in_cell = True
            self._buf = []
        elif tag == "br" and self._in_cell:
            self._buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            text = "".join(self._buf).strip()
            if tag == "th":
                self.headers.append(" ".join(text.split()).lower())
            else:
                self._cells.append(text)
        elif tag == "tr":
            if self._in_head_row:
                self._in_head_row = False
                self._headers_done = True
            if self._in_row:
                self._in_row = False
                self.rows.append((self._row_id, self._cells))

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._buf.append(data)


# Header text -> the FlagSpec field it feeds. Fallback positions are the legacy
# (pre-Kind-column) layout, kept so older shipped docs still parse.
_LEGACY_COLUMNS = {"description": 2, "type": 3}


def _column_map(headers: list[str]) -> dict[str, int]:
    """Resolve column indices from the table headers, with a legacy fallback.

    A header we do not find keeps its legacy position; "kind" has no legacy
    position, so its absence simply means the doc cannot be filtered by kind.
    """
    columns = dict(_LEGACY_COLUMNS)
    for name in ("kind", "description", "type"):
        if name in headers:
            columns[name] = headers.index(name)
    return columns


class _FeExperimentDetailParser(HTMLParser):
    """Parse the per-flag detail blocks of the rendered fe-args page.

    The published documentation site renders each flag as::

        <div class=fe-flag data-type=|bool| data-kind=|option| data-status=|Available|>
          <h3 id=fe-flag-NAME>...</h3>
          <p>full description</p>
          <dl class=fe-meta>... <dt>Defaults</dt><dd>
            <div class=fe-def><code>otherwise</code>...<code class=fe-val>False</code></div>
          </dd></dl>
        </div>

    which is strictly better than the summary table above it: the table truncates
    descriptions with an ellipsis, while these blocks carry the full text *and*
    the real default values. ``data-*`` attributes are wrapped in ``|`` as
    delimiters by the template, so they are stripped here.
    """

    def __init__(self) -> None:
        super().__init__()
        self.flags: list[dict[str, Any]] = []
        self._cur: dict[str, Any] | None = None
        self._depth = 0
        self._buf: list[str] = []
        self._in_desc = False
        self._in_def = False
        self._def_codes: list[str] = []
        self._in_code = False
        self._label = ""
        self._in_dt = False
        self._in_title = False

    @staticmethod
    def _unpipe(value: str | None) -> str:
        return (value or "").strip().strip("|").strip()

    @staticmethod
    def _classes(attr: dict[str, str | None]) -> set[str]:
        return set((attr.get("class") or "").split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = self._classes(attr)
        if tag == "div":
            if self._cur is None and "fe-flag" in classes:
                self._cur = {
                    "type": self._unpipe(attr.get("data-type")) or "bool",
                    "kind": self._unpipe(attr.get("data-kind")).lower(),
                    "public": self._unpipe(attr.get("data-public")).lower() == "true",
                    "use_case": self._unpipe(attr.get("data-use-case")),
                    "status": self._unpipe(attr.get("data-status")).lower(),
                    "name": "",
                    "anchor": "",
                    "description": "",
                    "default": None,
                }
                self._depth = 1
                return
            if self._cur is not None:
                self._depth += 1
                if "fe-def" in classes:
                    self._in_def = True
                    self._def_codes = []
        elif self._cur is None:
            return
        elif tag == "h3":
            ident = attr.get("id") or ""
            if ident.startswith("fe-flag-"):
                # The anchor is ``fe-flag-<name>`` with the flag's exact casing
                # preserved (the generator emits the name verbatim, not a
                # lower-cased slug), so it is directly citable and guessable.
                # The flag name itself still comes from the title link text
                # below rather than from stripping this prefix.
                self._cur["anchor"] = ident
                self._cur.setdefault("name", "")
        elif tag == "a" and "fe-flag-title" in classes:
            # The link text preserves the flag's real capitalisation.
            self._in_title = True
            self._buf = []
        elif tag == "span" and self._in_def and "fe-empty" in classes:
            # An empty default renders as "(empty)", not as a value <code>.
            self._def_codes.append("")
        elif tag == "p" and not self._cur["description"]:
            self._in_desc = True
            self._buf = []
        elif tag == "dt":
            self._in_dt = True
            self._buf = []
        elif tag == "code" and self._in_def:
            self._in_code = True
            self._buf = []
        elif tag == "br" and self._in_desc:
            self._buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._cur is None:
            return
        if tag == "a" and self._in_title:
            self._in_title = False
            self._cur["name"] = "".join(self._buf).strip()
        elif tag == "p" and self._in_desc:
            self._in_desc = False
            self._cur["description"] = " ".join("".join(self._buf).split())
        elif tag == "dt" and self._in_dt:
            self._in_dt = False
            self._label = "".join(self._buf).strip().lower()
        elif tag == "code" and self._in_code:
            self._in_code = False
            self._def_codes.append("".join(self._buf).strip())
        elif tag == "div":
            if self._in_def and len(self._def_codes) >= 2:
                # `otherwise` is the fallback default, matching the YAML loader.
                if self._def_codes[0].lower() == "otherwise":
                    self._cur["default"] = _coerce_default(self._def_codes[1])
            if self._in_def:
                self._in_def = False
            self._depth -= 1
            if self._depth == 0:
                if self._cur["name"]:
                    self.flags.append(self._cur)
                self._cur = None

    def handle_data(self, data: str) -> None:
        if self._in_desc or self._in_dt or self._in_code or self._in_title:
            self._buf.append(data)


def _coerce_default(raw: str) -> Any:
    """Turn a rendered default back into a Python value."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text in ("True", "False"):
        return text == "True"
    try:
        return int(text)
    except ValueError:
        return text


def _load_from_detail_blocks(html_path: Path) -> list[FlagSpec]:
    parser = _FeExperimentDetailParser()
    parser.feed(html_path.read_text(errors="replace"))
    flags: list[FlagSpec] = []
    for entry in parser.flags:
        if not entry.get("public"):
            continue
        if entry["status"] and entry["status"] != "available":
            continue
        flags.append(
            classify(
                FlagSpec(
                    name=entry["name"],
                    source="fe-args",
                    type=entry["type"],
                    default=entry["default"],
                    description=entry["description"],
                    path=f"{html_path}#{entry['anchor']}",
                    use_case=entry.get("use_case") or "",
                )
            )
        )
    return flags


def load_option_flags_from_html(html_path: str | Path) -> list[FlagSpec]:
    """Parse frontend flags from the shipped ``fe-args.html`` documentation.

    Two renderings exist and both are supported: the documentation site emits
    per-flag detail blocks (preferred -- full descriptions and real defaults),
    while older standalone builds emit a single summary table.

    Where the rendering exposes ``data-public=|true|``, only those entries are
    returned. A summary table without a public column cannot be filtered, so
    every documented (non-removed) flag is returned.

    Defaults come back as real values from the detail blocks; the table
    rendering does not carry them, so there they stay ``None``. ``path`` always
    cites the exact doc anchor.
    """
    html_path = Path(html_path)
    detailed = _load_from_detail_blocks(html_path)
    if detailed:
        return detailed

    parser = _FeExperimentHTMLParser()
    parser.feed(html_path.read_text(errors="replace"))
    columns = _column_map(parser.headers)
    desc_col = columns.get("description")
    type_col = columns.get("type")
    kind_col = columns.get("kind")

    flags: list[FlagSpec] = []
    for row_id, cells in parser.rows:
        if not cells or "(removed)" in cells[0]:
            continue
        if desc_col is None or type_col is None or len(cells) <= type_col:
            continue
        if kind_col is not None and len(cells) > kind_col:
            if cells[kind_col].strip().lower() != "public-option":
                continue
        name = row_id[len("fe-flag-") :]
        flags.append(
            classify(
                FlagSpec(
                    name=name,
                    source="fe-args",
                    type=cells[type_col].strip() or "bool",
                    default=None,
                    description=" ".join(cells[desc_col].split()),
                    path=f"{html_path}#{row_id}",
                )
            )
        )
    return flags


# Public catalog only — generated by fe-experiments.py --only-public-facing.
_FE_HTML_CANDIDATES = (
    Path("docs") / "fe-args.html",
    Path("fe-args.html"),
)
# Pre-rename filename. A TA that still ships only this file predates ``fe_args``
# and must not leak a parent checkout's ``fe-args.html``.
_FE_HTML_LEGACY_CANDIDATES = (
    Path("docs") / "fe-experiments.html",
    Path("fe-experiments.html"),
)


def _html_catalog_env_path() -> Path | None:
    env = os.environ.get("FE_ARGS_HTML")
    if env and Path(env).is_file():
        return Path(env)
    return None


def find_fe_args_html(start: str | Path | None = None) -> Path | None:
    """Locate the shipped public frontend flag catalog (``fe-args.html``).

    Order: ``FE_ARGS_HTML`` env override, then walk up from ``start`` (or cwd).
    Stop at a tree that only has the pre-rename ``fe-experiments.html``: that is
    an older TA, not a missing file to skip past.
    """
    env_path = _html_catalog_env_path()
    if env_path is not None:
        return env_path
    base = Path(start or Path.cwd()).resolve()
    for parent in [base, *base.parents]:
        for candidate in _FE_HTML_CANDIDATES:
            path = parent / candidate
            if path.is_file():
                return path
        if any((parent / legacy).is_file() for legacy in _FE_HTML_LEGACY_CANDIDATES):
            return None
    return None


def load_catalog(
    *,
    html_path: str | Path | None = None,
) -> list[FlagSpec]:
    """Assemble the flag catalog from FlagSpecs and the public HTML catalog.

    Precedence: explicit ``html_path`` -> auto-discovered ``fe-args.html`` ->
    vaiml_config fields only (graceful degrade).
    """
    fields = list(VITISAI_CONFIG_FIELDS)
    path = Path(html_path) if html_path is not None else find_fe_args_html()
    if path is not None and path.is_file():
        return fields + load_option_flags_from_html(path)
    return fields
