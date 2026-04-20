"""Learning map + focus brief generation.

The learning map is the top-level orientation: objectives, key concepts,
prerequisites, common pitfalls, suggested path. The focus brief is the
per-section pre-study scaffold: what to pay attention to, key terms,
pitfalls, connections, time estimate.

Both are generated from the source text (not from extracted notes — the raw
material is richer for deciding what will matter). Prompt caching on the
full-material block lets the material map + all focus briefs share the cache.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import MODEL_HAIKU, MODEL_OPUS
from ..llm.client import LLM, cached_source, plain
from ..llm.prompts import (
    FOCUS_BRIEF_SYSTEM,
    FOCUS_BRIEF_USER_TEMPLATE,
    LEARNING_MAP_SYSTEM,
    LEARNING_MAP_USER_TEMPLATE,
)

log = logging.getLogger(__name__)


async def generate_material_map(
    llm: LLM,
    full_material_text: str,
    section_index: list[tuple[int, str | None]],
    learner_notes: str | None = None,
    known_concepts_block: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Generate the material-level learning map.

    learner_notes (optional): free-form adjustment hints from the learner when
    regenerating, e.g. "focus more on ch 4-6, skim ch 2".

    known_concepts_block (optional): markdown listing concepts the learner
    has encountered in other materials — used for cross-material linking so
    the map can reference them instead of re-introducing. Produced by
    `orientation.cross_material.format_known_concepts_block`.

    Returns (json_payload, markdown_rendering).
    """
    index_str = "\n".join(
        f"- §{idx}: {title or '(untitled)'}" for idx, title in section_index
    )
    user_text = LEARNING_MAP_USER_TEMPLATE.format(section_index=index_str)
    if learner_notes and learner_notes.strip():
        user_text += (
            "\n\n## Learner's adjustment notes (apply these when shaping the map)\n\n"
            + learner_notes.strip()
        )
    if known_concepts_block and known_concepts_block.strip():
        user_text += "\n\n" + known_concepts_block.strip()

    blocks = cached_source(
        label="FULL MATERIAL TEXT (cached):",
        body=full_material_text,
    ) + plain("\n\n" + user_text)

    payload = await llm.complete_json(
        model=MODEL_OPUS,
        system=LEARNING_MAP_SYSTEM,
        blocks=blocks,
        max_tokens=4096,
        temperature=0.2,
    )

    return payload, _render_map_markdown(payload)


async def generate_focus_brief(
    llm: LLM,
    full_material_text: str,
    order_index: int,
    title: str | None,
) -> dict[str, Any]:
    """Generate a pre-study focus brief for one section.

    The full material text is passed cached — reused across all briefs — so
    the model can judge what matters in this section relative to the whole.
    """
    user_text = FOCUS_BRIEF_USER_TEMPLATE.format(
        order_index=order_index, title=title or "(untitled)"
    )
    blocks = cached_source(
        label="FULL MATERIAL TEXT (cached):",
        body=full_material_text,
    ) + plain("\n\n" + user_text)

    return await llm.complete_json(
        model=MODEL_HAIKU,
        system=FOCUS_BRIEF_SYSTEM,
        blocks=blocks,
        max_tokens=1024,
        temperature=0.2,
    )


def _render_map_markdown(payload: dict[str, Any]) -> str:
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
                f"- **{kc.get('name', '?')}** ({diff}, {sections}) — {kc.get('why_load_bearing', '')}"
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


def render_focus_brief_markdown(brief: dict[str, Any], order_index: int, title: str | None) -> str:
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
