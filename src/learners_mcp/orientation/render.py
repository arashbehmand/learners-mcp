"""Lightweight Markdown renderers for orientation artifacts."""

from __future__ import annotations

import json
from typing import Any


def render_map_markdown(payload: dict[str, Any]) -> str:
    """Render the structured map as friendly markdown for human reading."""
    out: list[str] = []
    out.append("# Learning Map\n")

    if payload.get("difficulty"):
        out.append(f"**Difficulty:** {payload['difficulty']}  ")
    if payload.get("time_estimate_hours"):
        out.append(f"**Estimated time:** ~{payload['time_estimate_hours']}h\n")

    out.append("\n## Objectives\n")
    for obj in payload.get("objectives", []):
        out.append(f"- {obj}")

    if payload.get("prerequisites"):
        out.append("\n## Prerequisites\n")
        for p in payload["prerequisites"]:
            out.append(f"- {p}")

    if payload.get("key_concepts"):
        out.append("\n## Key concepts\n")
        for kc in payload["key_concepts"]:
            sections = ", ".join(f"§{s}" for s in kc.get("sections", []))
            diff = kc.get("difficulty", "")
            out.append(
                f"- **{kc.get('name', '?')}** ({diff}, {sections}) — "
                f"{kc.get('why_load_bearing', '')}"
            )

    if payload.get("common_pitfalls"):
        out.append("\n## Common pitfalls\n")
        for p in payload["common_pitfalls"]:
            out.append(f"- {p}")

    if payload.get("suggested_path"):
        out.append("\n## Suggested path\n")
        for step in payload["suggested_path"]:
            ids = ", ".join(f"§{s}" for s in step.get("section_ids", []))
            out.append(f"- {ids}: {step.get('note', '')}")

    return "\n".join(out) + "\n"


def render_focus_brief_markdown(
    brief: dict[str, Any], order_index: int, title: str | None
) -> str:
    """Small helper for displaying a brief as markdown."""
    out: list[str] = []
    out.append(f"# Focus brief — §{order_index}: {title or '(untitled)'}\n")
    out.append(f"**Estimated time:** ~{brief.get('estimated_minutes', '?')} min\n")

    if brief.get("focus"):
        out.append(f"\n## Focus\n\n{brief['focus']}\n")

    if brief.get("key_terms"):
        out.append("\n## Key terms\n")
        for kt in brief["key_terms"]:
            out.append(f"- **{kt.get('term', '?')}** — {kt.get('gloss', '')}")

    if brief.get("watch_for"):
        out.append("\n## Watch for\n")
        for w in brief["watch_for"]:
            out.append(f"- {w}")

    if brief.get("connects_to"):
        out.append("\n## Connects to\n")
        for c in brief["connects_to"]:
            out.append(f"- {c}")

    return "\n".join(out) + "\n"


def map_payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2)

