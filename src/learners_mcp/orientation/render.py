"""Lightweight Markdown renderers for orientation artifacts."""

from __future__ import annotations

import json
from typing import Any


def _labels(language_code: str | None) -> dict[str, str]:
    if language_code == "fa":
        return {
            "learning_map": "نقشه یادگیری",
            "difficulty": "دشواری",
            "estimated_time": "زمان تخمینی",
            "objectives": "هدف‌ها",
            "prerequisites": "پیش‌نیازها",
            "key_concepts": "مفاهیم کلیدی",
            "common_pitfalls": "دام‌های رایج",
            "suggested_path": "مسیر پیشنهادی",
            "focus_brief": "راهنمای تمرکز",
            "estimated_minutes": "زمان تخمینی",
            "focus": "تمرکز",
            "key_terms": "اصطلاحات کلیدی",
            "watch_for": "مراقب باشید",
            "connects_to": "پیوند با",
            "untitled": "بدون عنوان",
        }
    return {
        "learning_map": "Learning Map",
        "difficulty": "Difficulty",
        "estimated_time": "Estimated time",
        "objectives": "Objectives",
        "prerequisites": "Prerequisites",
        "key_concepts": "Key concepts",
        "common_pitfalls": "Common pitfalls",
        "suggested_path": "Suggested path",
        "focus_brief": "Focus brief",
        "estimated_minutes": "Estimated time",
        "focus": "Focus",
        "key_terms": "Key terms",
        "watch_for": "Watch for",
        "connects_to": "Connects to",
        "untitled": "untitled",
    }


def render_map_markdown(payload: dict[str, Any], language_code: str | None = None) -> str:
    """Render the structured map as friendly markdown for human reading."""
    labels = _labels(language_code)
    out: list[str] = []
    out.append(f"# {labels['learning_map']}\n")

    if payload.get("difficulty"):
        out.append(f"**{labels['difficulty']}:** {payload['difficulty']}  ")
    if payload.get("time_estimate_hours"):
        out.append(f"**{labels['estimated_time']}:** ~{payload['time_estimate_hours']}h\n")

    out.append(f"\n## {labels['objectives']}\n")
    for obj in payload.get("objectives", []):
        out.append(f"- {obj}")

    if payload.get("prerequisites"):
        out.append(f"\n## {labels['prerequisites']}\n")
        for p in payload["prerequisites"]:
            out.append(f"- {p}")

    if payload.get("key_concepts"):
        out.append(f"\n## {labels['key_concepts']}\n")
        for kc in payload["key_concepts"]:
            sections = ", ".join(f"§{s}" for s in kc.get("sections", []))
            diff = kc.get("difficulty", "")
            out.append(
                f"- **{kc.get('name', '?')}** ({diff}, {sections}) — "
                f"{kc.get('why_load_bearing', '')}"
            )

    if payload.get("common_pitfalls"):
        out.append(f"\n## {labels['common_pitfalls']}\n")
        for p in payload["common_pitfalls"]:
            out.append(f"- {p}")

    if payload.get("suggested_path"):
        out.append(f"\n## {labels['suggested_path']}\n")
        for step in payload["suggested_path"]:
            ids = ", ".join(f"§{s}" for s in step.get("section_ids", []))
            out.append(f"- {ids}: {step.get('note', '')}")

    return "\n".join(out) + "\n"


def render_focus_brief_markdown(
    brief: dict[str, Any],
    order_index: int,
    title: str | None,
    language_code: str | None = None,
) -> str:
    """Small helper for displaying a brief as markdown."""
    labels = _labels(language_code)
    out: list[str] = []
    display_title = title or f"({labels['untitled']})"
    out.append(f"# {labels['focus_brief']} — §{order_index}: {display_title}\n")
    out.append(
        f"**{labels['estimated_minutes']}:** ~{brief.get('estimated_minutes', '?')} min\n"
    )

    if brief.get("focus"):
        out.append(f"\n## {labels['focus']}\n\n{brief['focus']}\n")

    if brief.get("key_terms"):
        out.append(f"\n## {labels['key_terms']}\n")
        for kt in brief["key_terms"]:
            out.append(f"- **{kt.get('term', '?')}** — {kt.get('gloss', '')}")

    if brief.get("watch_for"):
        out.append(f"\n## {labels['watch_for']}\n")
        for w in brief["watch_for"]:
            out.append(f"- {w}")

    if brief.get("connects_to"):
        out.append(f"\n## {labels['connects_to']}\n")
        for c in brief["connects_to"]:
            out.append(f"- {c}")

    return "\n".join(out) + "\n"


def map_payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2)
