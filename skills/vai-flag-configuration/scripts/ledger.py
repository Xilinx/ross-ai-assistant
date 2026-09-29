# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""Record and compare optimization iterations; pick the best flag set.

An iteration is only reconstructible after the fact if all three of its parts
are stored together: **what was proposed and why** (``IterationRecord.proposal``),
**what was compiled** (``IterationRecord.flags``), and **what came out**
(the metrics plus the ``stop_check`` the termination decision was made on).
``write_iteration_result`` writes exactly that triple next to the iteration's
``vitisai_config.json``, and ``Ledger.evaluate_stop`` computes the termination
metrics here -- deterministically -- rather than leaving the agent to eyeball
"did this improve?" and narrate the answer into a log that is then thrown away.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FlagChange:
    """One proposed flag change, carrying the citation the skill requires.

    The SKILL.md Transparency rule says a proposal without a source path and a
    rationale is incomplete; keeping that structural (rather than prose in the
    agent's table) is what makes it survive into ``result.json``.
    """

    flag: str
    source_path: str  # FlagSpec.path -- the YAML / doc to cite
    default: Any  # the value being deviated from
    value: Any  # the value chosen
    rationale: str


@dataclass
class IterationRecord:
    index: int
    flags: dict[str, Any]
    compiled: bool
    npu_offload_pct: float | None = None
    npu_time_usec: float | None = None
    board_inference_usec: float | None = None
    verdict: str = ""
    # The change proposal that produced ``flags``, and the termination metrics
    # computed once the results were in. Both default-empty so records written
    # by an earlier version of this skill still load.
    proposal: list[FlagChange] = field(default_factory=list)
    stop_check: dict[str, Any] | None = None


def flags_key(flags: dict[str, Any]) -> str:
    payload = json.dumps(flags, sort_keys=True).encode()
    return hashlib.sha1(payload).hexdigest()


def flag_delta_count(base: dict[str, Any], candidate: dict[str, Any]) -> int:
    """Number of flags added, changed, or removed relative to ``base``.

    Used to guardrail exploration: keep each proposal within a small budget of
    the current best config instead of changing many flags at once.
    """
    keys = set(base) | set(candidate)
    return sum(1 for k in keys if base.get(k) != candidate.get(k))


# Objective token -> (IterationRecord field it scores on, higher-is-better).
_OBJECTIVE_METRICS: dict[str, tuple[str, bool]] = {
    "offload": ("npu_offload_pct", True),
    "latency": ("board_inference_usec", False),
    "compile": ("compiled", True),
}


def primary_objective(objectives: list[str]) -> str:
    """The objective progress is measured against.

    ``compile`` is a precondition rather than something to maximize, so the
    first measurable objective wins; ``compile`` is the fallback.
    """
    for obj in objectives:
        if obj in _OBJECTIVE_METRICS and obj != "compile":
            return obj
    return "compile"


def _normalised_value(rec: IterationRecord, objective: str) -> float | None:
    """Score for ``objective``, sign-flipped so that larger is always better.

    ``None`` when the iteration produced no usable score (it did not compile,
    or the metric was never measured) -- which is treated as "no improvement",
    never as a regression to zero.
    """
    if objective == "compile":
        return 1.0 if rec.compiled else 0.0
    if not rec.compiled:
        return None
    field_name, higher_is_better = _OBJECTIVE_METRICS[objective]
    raw = getattr(rec, field_name, None)
    if raw is None:
        return None
    return float(raw) if higher_is_better else -float(raw)


def _sort_key(rec: IterationRecord, objectives: list[str]) -> tuple[float, ...]:
    key: list[float] = [1.0 if rec.compiled else 0.0]
    for obj in objectives:
        if obj == "offload":
            key.append(rec.npu_offload_pct if rec.npu_offload_pct is not None else -1.0)
        elif obj == "latency":
            board = rec.board_inference_usec
            # lower is better -> negate so max() picks the smallest
            key.append(-board if board is not None else float("-inf"))
        # "compile" is already handled by the leading element
    return tuple(key)


@dataclass
class StopCriteria:
    """The stop metrics agreed with the user in the Startup Interview.

    ``None`` disables a criterion. ``untried_candidates`` is the number of
    proposable, not-yet-tried flags left for the current goal -- the caller
    computes it from the catalog, the ledger tells it what that implies.
    """

    target_offload_pct: float | None = None
    max_no_improvement: int | None = 2
    max_iterations: int | None = None
    untried_candidates: int | None = None


@dataclass
class StopCheck:
    """Outcome of the termination decision, plus the numbers behind it."""

    should_stop: bool
    reasons: list[str]
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _record_from_dict(data: dict[str, Any]) -> IterationRecord:
    payload = dict(data)
    proposal = payload.pop("proposal", None) or []
    record = IterationRecord(**payload)
    record.proposal = [
        change if isinstance(change, FlagChange) else FlagChange(**change)
        for change in proposal
    ]
    return record


def _plain_proposal(proposal: list[FlagChange]) -> list[dict[str, Any]]:
    # `load` rehydrates saved proposals into FlagChange, so this only ever sees
    # dataclasses.
    return [asdict(change) for change in proposal]


def write_iteration_result(
    iteration_dir: str | Path, record: IterationRecord, objectives: list[str]
) -> Path:
    """Write ``<iteration_dir>/result.json``: proposal + config + results.

    Keeps the iteration directory self-describing, so a run can be re-evaluated
    later from its artifacts alone rather than by replaying the agent log.
    """
    path = Path(iteration_dir) / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "index": record.index,
        "objectives": list(objectives),
        "proposal": _plain_proposal(record.proposal),
        "config": dict(record.flags),
        "results": {
            "compiled": record.compiled,
            "npu_offload_pct": record.npu_offload_pct,
            "npu_time_usec": record.npu_time_usec,
            "board_inference_usec": record.board_inference_usec,
            "verdict": record.verdict,
        },
        "stop_check": record.stop_check,
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


@dataclass
class Ledger:
    path: Path
    records: list[IterationRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self._keys: set[str] = {flags_key(r.flags) for r in self.records}

    def has_tried(self, flags: dict[str, Any]) -> bool:
        return flags_key(flags) in self._keys

    def add(self, record: IterationRecord) -> None:
        self.records.append(record)
        self._keys.add(flags_key(record.flags))

    def best(self, objectives: list[str]) -> IterationRecord | None:
        if not self.records:
            return None
        return max(self.records, key=lambda r: _sort_key(r, objectives))

    def all_failed(self) -> bool:
        """True if iterations were tried but none compiled (bail-out signal)."""
        return bool(self.records) and not any(r.compiled for r in self.records)

    def status(self) -> str:
        if not self.records:
            return "empty"
        return "ok" if any(r.compiled for r in self.records) else "compile-failed"

    # -- termination metrics -------------------------------------------------

    def improvement_series(self, objective: str) -> list[float | None]:
        """Per-iteration gain over the best of every *earlier* iteration.

        ``None`` for the first scored iteration (it is the baseline) and for any
        iteration with no usable score. Shared by ``summary_table`` and
        ``evaluate_stop`` so the table and the stop decision can never disagree.
        """
        series: list[float | None] = []
        best: float | None = None
        for rec in self.records:
            value = _normalised_value(rec, objective)
            if value is None:
                series.append(None)
                continue
            series.append(None if best is None else value - best)
            if best is None or value > best:
                best = value
        return series

    def no_improvement_streak(self, objective: str) -> int:
        """Trailing iterations that failed to beat the best before them."""
        streak = 0
        best: float | None = None
        for rec in self.records:
            value = _normalised_value(rec, objective)
            if value is not None and (best is None or value > best):
                streak = 0
                best = value
            else:
                streak += 1
        return streak

    def best_offload_pct(self) -> float | None:
        values = [
            r.npu_offload_pct
            for r in self.records
            if r.compiled and r.npu_offload_pct is not None
        ]
        return max(values) if values else None

    def evaluate_stop(self, objectives: list[str], criteria: StopCriteria) -> StopCheck:
        """Apply the stop metrics and report the numbers they were applied to.

        Deterministic on purpose: the loop's termination decision, and the
        evidence the final report has to cite for it, are computed here instead
        of being re-derived by eye each iteration.
        """
        objective = primary_objective(objectives)
        latest = self.records[-1] if self.records else None
        best_offload = self.best_offload_pct()
        streak = self.no_improvement_streak(objective)
        series = self.improvement_series(objective)

        target = criteria.target_offload_pct
        target_gap = (
            None if target is None or best_offload is None else target - best_offload
        )
        target_reached = target_gap is not None and target_gap <= 0

        metrics: dict[str, Any] = {
            "iterations": len(self.records),
            "compiled_count": sum(1 for r in self.records if r.compiled),
            "objectives": list(objectives),
            "primary_objective": objective,
            "primary_metric": _OBJECTIVE_METRICS[objective][0],
            "latest_value": (
                None
                if latest is None or objective == "compile"
                else getattr(latest, _OBJECTIVE_METRICS[objective][0])
            ),
            "best_offload_pct": best_offload,
            "improvement": series[-1] if series else None,
            "no_improvement_streak": streak,
            "target_offload_pct": target,
            "target_gap": target_gap,
            "target_reached": target_reached,
            "untried_candidates": criteria.untried_candidates,
        }

        reasons: list[str] = []
        if self.all_failed():
            reasons.append("all-failed")
        if target_reached:
            reasons.append("target-reached")
        if (
            criteria.max_no_improvement is not None
            and criteria.max_no_improvement > 0
            and streak >= criteria.max_no_improvement
        ):
            reasons.append("no-improvement-limit")
        if (
            criteria.max_iterations is not None
            and len(self.records) >= criteria.max_iterations
        ):
            reasons.append("max-iterations")
        if criteria.untried_candidates is not None and criteria.untried_candidates <= 0:
            reasons.append("no-untried-flags")

        return StopCheck(should_stop=bool(reasons), reasons=reasons, metrics=metrics)

    def summary(self, objectives: list[str]) -> dict[str, Any]:
        """Machine-readable run summary. Produced even when nothing compiled.

        ``best`` is only populated when a config actually compiled; on a
        ``compile-failed`` run it is ``None`` but every attempt is still listed
        in ``records`` (with its verdict), so a failed run yields useful output.
        """
        best = self.best(objectives)
        compiled_best = best if best is not None and best.compiled else None
        return {
            "status": self.status(),
            "iterations": len(self.records),
            "compiled_count": sum(1 for r in self.records if r.compiled),
            "objectives": list(objectives),
            "best": asdict(compiled_best) if compiled_best is not None else None,
            # The termination metrics of the last iteration: why the loop
            # stopped, in the same file as what it stopped on.
            "final_stop_check": self.records[-1].stop_check if self.records else None,
            "records": [asdict(r) for r in self.records],
        }

    def write_summary(self, path: str | Path, objectives: list[str]) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.summary(objectives), indent=2))
        return path

    def _default_objectives(self) -> list[str]:
        if any(r.npu_offload_pct is not None for r in self.records):
            return ["offload"]
        if any(r.board_inference_usec is not None for r in self.records):
            return ["latency"]
        return ["compile"]

    def summary_table(self, objectives: list[str] | None = None) -> str:
        """Per-iteration table, including the gain each iteration produced.

        The ``delta`` column is the same series ``evaluate_stop`` decides on, so
        the final report's "which iterations improved and by how much" is read
        off the table rather than recomputed.
        """
        objective = primary_objective(objectives or self._default_objectives())
        deltas = self.improvement_series(objective)
        header = (
            f"{'iter':>4} | {'compiled':>8} | {'offload%':>8} | "
            f"{'npu_us':>10} | {'board_us':>10} | {'delta':>8} | verdict"
        )
        lines = [header, "-" * len(header)]
        for r, delta in zip(self.records, deltas):
            offload = "" if r.npu_offload_pct is None else f"{r.npu_offload_pct:.1f}"
            npu = "" if r.npu_time_usec is None else f"{r.npu_time_usec:.1f}"
            board = (
                ""
                if r.board_inference_usec is None
                else f"{r.board_inference_usec:.1f}"
            )
            gain = "" if delta is None else f"{delta:+.1f}"
            lines.append(
                f"{r.index:>4} | {str(r.compiled):>8} | {offload:>8} | "
                f"{npu:>10} | {board:>10} | {gain:>8} | {r.verdict}"
            )
        return "\n".join(lines)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(r) for r in self.records], indent=2))

    def load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        self.records = [_record_from_dict(d) for d in data]
        self._keys = {flags_key(r.flags) for r in self.records}
