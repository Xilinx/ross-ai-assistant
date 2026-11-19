# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where a Claude Code session's context and time went, per lab stage.

Usage: context_audit.py [TRANSCRIPT.jsonl ...] [--cwd DIR] [--top N] [--format md|json]

With no transcript, reads the newest session under
~/.claude/projects/<cwd with / replaced by ->/. Read-only; prints sizes,
names, paths and timings, never message content.

Per bucket (notebook "Stage N" prompt x ipiasm stage) it reports wall clock
split into model time (request sent -> last block of the reply written) and
tool time (tool_use -> tool_result), the context high-water mark and output
and thinking tokens from the API usage, and the bytes that entered context by
source: skill bodies, attachments, and tool results grouped by tool, by path
class for reads, and by first TCL word for vivado_execute. Byte counts
divided by 4 are labelled est_tokens; usage numbers are exact.
"""

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import re

STAGE = re.compile(r"ipiasm::stage\s+([A-Za-z_]\w*)|assemble_from_plan\s+\S+\s+([A-Za-z_]\w*)")
PROMPT_STAGE = re.compile(r"\bStage\s+(\d+)\b")


def escaped(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))


def newest(cwd):
    folder = Path.home() / ".claude" / "projects" / escaped(cwd)
    found = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise SystemExit(f"no transcripts under {folder}")
    return found[-1]


def seconds(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(text_of(part.get("text", part.get("content", ""))) if isinstance(part, dict) else str(part)
                       for part in content)
    return ""


def path_class(path):
    parts = Path(path).parts
    for marker, label in (("inputs", "inputs/"), ("outputs", "outputs/"), (".claude", ".claude/skills")):
        if marker in parts:
            return label
    return "other"


def new_bucket():
    return {"first": None, "last": None, "model_s": 0.0, "tool_s": 0.0, "replies": 0, "tool_calls": 0,
            "peak_context": 0, "output_tokens": 0, "thinking_tokens": 0, "sources": defaultdict(int)}


def audit(paths):
    buckets = defaultdict(new_bucket)
    fixed = defaultdict(int)
    prompt_stage, build_stage = "-", "-"
    tools = {}              # tool_use_id -> (label, started, bucket key)
    replies = {}            # message id -> bucket key (usage counted once)
    request_at = None       # when the pending model request was sent
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = record.get("type")
                stamp = record.get("timestamp")
                at = seconds(stamp) if stamp else None
                key = f"{prompt_stage}:{build_stage}"
                bucket = buckets[key]
                if at is not None:
                    bucket["first"] = bucket["first"] or at
                    bucket["last"] = at
                if kind == "attachment":
                    attachment = record.get("attachment", {})
                    label = f"attachment:{attachment.get('type')}"
                    if attachment.get("type") == "prompt_snapshot":
                        # The system prompt and tool schemas ride on every
                        # request; each snapshot re-records them, so keep the
                        # largest rather than summing.
                        for part in ("systemPrompt", "tools"):
                            if part in attachment:
                                size = len(json.dumps(attachment[part]))
                                fixed[f"every request: {part}"] = max(fixed[f"every request: {part}"], size)
                        continue
                    if attachment.get("type") == "invoked_skills":
                        for skill in attachment.get("skills", []):
                            bucket["sources"][f"skill:{skill.get('name')}"] += len(skill.get("content", ""))
                        continue
                    if attachment.get("type") in ("file", "edited_text_file"):
                        label += f" {path_class(attachment.get('filename', ''))}"
                    bucket["sources"][label] += len(json.dumps(attachment))
                elif kind == "user":
                    message = record.get("message", {})
                    content = message.get("content")
                    blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_result":
                            label, started, owner = tools.pop(block.get("tool_use_id"), ("tool:?", at, key))
                            size = len(text_of(block.get("content")))
                            buckets[owner]["sources"][label] += size
                            if at is not None and started is not None:
                                buckets[owner]["tool_s"] += max(0.0, at - started)
                        elif block.get("type") == "text":
                            text = block.get("text") or ""
                            if "Base directory for this skill" in text:
                                bucket["sources"]["skill body (Skill tool)"] += len(text)
                            elif record.get("isMeta"):
                                bucket["sources"]["harness text"] += len(text)
                            else:
                                bucket["sources"]["user prompt"] += len(text)
                                found = PROMPT_STAGE.search(text)
                                if found:
                                    prompt_stage, build_stage = f"Stage {found.group(1)}", "-"
                        elif block.get("type") == "image":
                            bucket["sources"]["image"] += len(json.dumps(block))
                    request_at = at
                elif kind == "assistant":
                    message = record.get("message", {})
                    ident = message.get("id")
                    if ident not in replies:
                        replies[ident] = key
                        usage = message.get("usage") or {}
                        context = sum(usage.get(name) or 0 for name in
                                      ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                        bucket["peak_context"] = max(bucket["peak_context"], context)
                        bucket["output_tokens"] += usage.get("output_tokens") or 0
                        bucket["thinking_tokens"] += (usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0
                        bucket["replies"] += 1
                    if request_at is not None and at is not None:
                        # Blocks of one reply arrive as separate records; the
                        # reply's model time runs to its last block.
                        buckets[replies[ident]]["model_s"] += max(0.0, at - request_at)
                        request_at = at
                    for block in message.get("content") or []:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        name = block.get("name", "?")
                        tool_input = block.get("input") or {}
                        label = f"tool:{name}"
                        if name == "Read":
                            label += f" {path_class(tool_input.get('file_path', ''))}"
                        command = tool_input.get("command")
                        if isinstance(command, str):
                            if name.endswith("vivado_execute"):
                                first = command.strip().split(None, 1)
                                label += f" {first[0] if first else ''}"
                            for found in STAGE.finditer(command):
                                build_stage = found.group(1) or found.group(2)
                        tools[block.get("id")] = (label, at, key)
                        bucket["tool_calls"] += 1
    return buckets, fixed


def render(buckets, fixed, top):
    lines = ["| bucket | wall min | model min | tool min | replies | tool calls | peak context tok | output tok | thinking tok |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    totals = defaultdict(int)
    for key, data in buckets.items():
        if not data["replies"] and not data["tool_calls"]:
            continue
        wall = ((data["last"] or 0) - (data["first"] or 0)) / 60
        lines.append(f"| {key} | {wall:.1f} | {data['model_s'] / 60:.1f} | {data['tool_s'] / 60:.1f} | "
                     f"{data['replies']} | {data['tool_calls']} | {data['peak_context']:,} | "
                     f"{data['output_tokens']:,} | {data['thinking_tokens']:,} |")
        for source, size in data["sources"].items():
            totals[source] += size
    lines += ["", "Carried on every request (largest seen):", ""]
    lines += [f"- {name}: {size / 1024:.0f} KB, est_tokens {size // 4:,}" for name, size in fixed.items()]
    lines += ["", f"Bytes that entered context by source (top {top}; est_tokens = bytes/4):", "",
              "| source | KB | est_tokens |", "|---|---:|---:|"]
    for source, size in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:top]:
        lines.append(f"| {source} | {size / 1024:.0f} | {size // 4:,} |")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcripts", nargs="*", type=Path)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--format", choices=("md", "json"), default="md")
    args = parser.parse_args(argv)
    buckets, fixed = audit(args.transcripts or [newest(args.cwd)])
    if args.format == "json":
        print(json.dumps({"buckets": buckets, "every_request": fixed}, indent=2, default=dict))
    else:
        print(render(buckets, fixed, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
