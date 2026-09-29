#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Validate the vivado-simulate-rtl skill without third-party packages."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
EVALS = ROOT / "evals" / "evals.json"
CROSS_MODEL_RESULTS = ROOT / "evals" / "cross-model-results.json"
NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
REQUIRED_IDS = {
    "xsim",
    "modelsim",
    "questa",
    "vcs",
    "xcelium",
    "riviera",
    "activehdl",
}
REQUIRED_EVALS = {
    "xsim-behavioral-pass",
    "xsim-assertion-diagnosis",
    "xsim-coverage-closure",
    "xsim-unreachable-code",
    "questa-missing-runtime",
    "questa-missing-libraries",
    "questa-behavioral-pass",
    "questa-assertion-diagnosis",
    "post-implementation-timing",
    "questa-unsupported-platform",
    "new-simulator-backend",
    "xsim-functional-coverage-closure",
    "xsim-assertion-aware-verification",
    "xsim-uvm-counter",
    "xsim-random-regression-replay",
    "xsim-saif-power-activity",
    "xsim-vcd-capture",
    "xsim-portable-stimulus-capture",
    "guided-interactive-repair",
    "modelsim-missing-runtime",
    "modelsim-behavioral-pass",
    "vcs-missing-runtime",
    "vcs-behavioral-pass",
    "xcelium-missing-runtime",
    "xcelium-behavioral-pass",
    "riviera-missing-runtime",
    "riviera-behavioral-pass",
    "activehdl-unsupported-platform",
    "activehdl-behavioral-pass",
    "xsim-crash-known-issue-research",
    "cross-simulator-arbitration",
    "minimal-reproducer-package",
    "environment-doctor-preflight",
    "tool-version-bisect",
    "simulation-performance-profile",
    "headless-waveform-compare",
}


def error(errors: list[str], message: str) -> None:
    errors.append(message)


def parse_frontmatter(text: str, errors: list[str]) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        error(errors, "SKILL.md: missing opening YAML frontmatter delimiter")
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        error(errors, "SKILL.md: missing closing YAML frontmatter delimiter")
        return {}, text
    values: dict[str, str] = {}
    for number, line in enumerate(text[4:end].splitlines(), start=2):
        if not line.strip():
            continue
        if ":" not in line:
            error(errors, f"SKILL.md:{number}: unsupported frontmatter line")
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values, text[end + 5 :]


def validate_skill(errors: list[str]) -> None:
    if not SKILL.is_file():
        error(errors, "SKILL.md: file is missing")
        return
    text = SKILL.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(text, errors)

    name = metadata.get("name", "")
    description = metadata.get("description", "")
    if not NAME_RE.fullmatch(name):
        error(errors, "SKILL.md: name must be 1-64 lowercase letters, numbers, or hyphens")
    if "anthropic" in name or "claude" in name:
        error(errors, "SKILL.md: name contains a reserved word")
    if not description or len(description) > 1024:
        error(errors, "SKILL.md: description must contain 1-1024 characters")
    if re.search(r"\b(I|we|you|your)\b", description, re.IGNORECASE):
        error(errors, "SKILL.md: description must be written in third person")
    if len(body.splitlines()) >= 500:
        error(errors, "SKILL.md: body must remain under 500 lines")
    if "\\" in text:
        error(errors, "SKILL.md: use forward slashes in paths")

    linked_local = set()
    for target in LINK_RE.findall(text):
        if target.startswith(("http://", "https://", "#")):
            continue
        linked_local.add(target)
        path = (ROOT / target).resolve()
        if ROOT not in path.parents:
            error(errors, f"SKILL.md: local link escapes skill directory: {target}")
        elif not path.exists():
            error(errors, f"SKILL.md: broken local link: {target}")

    for reference in sorted((ROOT / "references").glob("*.md")):
        rel = reference.relative_to(ROOT).as_posix()
        if rel not in linked_local:
            error(errors, f"SKILL.md: reference is not linked directly: {rel}")


def validate_evals(errors: list[str]) -> None:
    try:
        data = json.loads(EVALS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        error(errors, "evals/evals.json: file is missing")
        return
    except json.JSONDecodeError as exc:
        error(errors, f"evals/evals.json:{exc.lineno}: invalid JSON: {exc.msg}")
        return

    verifier = data.get("advanced_artifact_verifier", "")
    if not verifier or not (EVALS.parent / verifier).is_file():
        error(errors, "evals/evals.json: advanced artifact verifier is missing")

    scenarios = data.get("scenarios", [])
    if len(scenarios) < 3:
        error(errors, "evals/evals.json: at least three scenarios are required")
    seen: set[str] = set()
    for index, scenario in enumerate(scenarios):
        prefix = f"evals/evals.json scenario {index + 1}"
        scenario_id = scenario.get("id", "")
        if not scenario_id or scenario_id in seen:
            error(errors, f"{prefix}: id is missing or duplicated")
        seen.add(scenario_id)
        if not scenario.get("query"):
            error(errors, f"{prefix}: query is required")
        for field in ("expected_behavior", "expected_artifacts", "forbidden_behavior"):
            value = scenario.get(field)
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(item, str) or not item.strip() for item in value)
            ):
                error(errors, f"{prefix}: {field} must be a non-empty string list")
        for file_name in scenario.get("files", []):
            if "\\" in file_name:
                error(errors, f"{prefix}: fixture path must use forward slashes: {file_name}")
            if not (EVALS.parent / file_name).is_file():
                error(errors, f"{prefix}: missing fixture: {file_name}")
    missing = sorted(REQUIRED_EVALS - seen)
    if missing:
        error(errors, "evals/evals.json: missing required scenarios: " + ", ".join(missing))


def validate_backend_ids(errors: list[str]) -> None:
    texts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "references").glob("*.md"))
    )
    missing = sorted(
        simulator for simulator in REQUIRED_IDS if f"`{simulator}`" not in texts
    )
    if missing:
        error(errors, "references: missing canonical simulator IDs: " + ", ".join(missing))


def validate_cross_model_results(errors: list[str]) -> None:
    try:
        data = json.loads(CROSS_MODEL_RESULTS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        error(errors, "evals/cross-model-results.json: file is missing")
        return
    except json.JSONDecodeError as exc:
        error(
            errors,
            "evals/cross-model-results.json:"
            f"{exc.lineno}: invalid JSON: {exc.msg}",
        )
        return

    if data.get("required_matrix_runs") != 14:
        error(errors, "cross-model results must record 14 required matrix runs")
    required_models = {"Claude 4.5 Haiku Thinking", "GPT 5.6 Luna"}
    if set(data.get("models", [])) != required_models:
        error(errors, "cross-model results must contain both required models")

    final_results = data.get("final_results", [])
    final_ids = {result.get("scenario") for result in final_results}
    advanced_ids = {item for item in REQUIRED_EVALS if item.startswith("xsim-") and item in {
        "xsim-functional-coverage-closure",
        "xsim-assertion-aware-verification",
        "xsim-uvm-counter",
        "xsim-random-regression-replay",
        "xsim-saif-power-activity",
        "xsim-vcd-capture",
        "xsim-portable-stimulus-capture",
    }}
    if final_ids != advanced_ids:
        error(errors, "cross-model results must contain all seven advanced scenarios")
    for result in final_results:
        model_results = result.get("model_results", {})
        if set(model_results) != required_models or any(
            verdict != "PASS" for verdict in model_results.values()
        ):
            error(
                errors,
                "cross-model final result is incomplete or failing: "
                + str(result.get("scenario")),
            )


def main() -> int:
    errors: list[str] = []
    validate_skill(errors)
    validate_backend_ids(errors)
    # The evals/ folder is development-only and not part of the distributed skill.
    has_evals = EVALS.parent.is_dir()
    if has_evals:
        validate_evals(errors)
        validate_cross_model_results(errors)
    if errors:
        for item in errors:
            print(f"ERROR: {item}")
        return 1
    checked = "frontmatter, line budget, direct references, paths, and simulator IDs"
    if has_evals:
        checked += ", fixtures, evaluation schema, and cross-model results"
    print(f"OK: {checked} are valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
