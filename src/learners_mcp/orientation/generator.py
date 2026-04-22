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

import logging
from typing import Any

from ..llm.client import LLM, cached_source, plain
from ..llm.prompts import (
    FOCUS_BRIEF_SYSTEM,
    FOCUS_BRIEF_USER_TEMPLATE,
    LEARNING_MAP_SYSTEM,
    LEARNING_MAP_USER_TEMPLATE,
)
from .render import (
    map_payload_json,
    render_focus_brief_markdown,
    render_map_markdown,
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
        task="learning_map",
        system=LEARNING_MAP_SYSTEM,
        blocks=blocks,
        max_tokens=4096,
        temperature=0.2,
    )

    return payload, render_map_markdown(payload)


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
        task="focus_brief",
        system=FOCUS_BRIEF_SYSTEM,
        blocks=blocks,
        max_tokens=1024,
        temperature=0.2,
    )


_render_map_markdown = render_map_markdown
