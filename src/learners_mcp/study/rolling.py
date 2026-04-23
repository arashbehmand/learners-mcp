"""Rolling summary generation.

Ported from PECS-learner/utils/rolling_context_service.py with two changes:
- LLM call swapped from LiteLLM to our Anthropic client.
- Summaries include `[§N]` citations so downstream prompts can trace claims.

Rolling summaries are materialised lazily: when a section's summary is
requested, we ensure every prior section's summary exists first (recursively,
in order). Each section's summary is the previous summary + the current
section, rewritten.
"""

from __future__ import annotations

import logging

from ..config import ROLLING_CONTEXT_MAX_CHARS
from ..db import DB
from ..language import detect_source_language, language_instruction
from ..llm.client import LLM, plain
from ..llm.prompts import ROLLING_SUMMARY_SYSTEM, ROLLING_SUMMARY_USER_TEMPLATE

log = logging.getLogger(__name__)


async def ensure_rolling_summary(db: DB, llm: LLM, section_id: int) -> str | None:
    """Compute (and persist) the rolling summary for this section if missing."""
    section = db.get_section(section_id)
    if section is None:
        log.error("rolling: section %d not found", section_id)
        return None
    if section.rolling_summary:
        return section.rolling_summary

    # Recursively ensure all prior summaries exist.
    all_sections = db.get_sections(section.material_id)
    prior = [s for s in all_sections if s.order_index < section.order_index]
    previous_summary = ""
    if prior:
        prev = prior[-1]
        if prev.rolling_summary:
            previous_summary = prev.rolling_summary
        else:
            previous_summary = await ensure_rolling_summary(db, llm, prev.id) or ""

    # order_index is already 1-based (set in ingestion.pipeline.ingest).
    user = ROLLING_SUMMARY_USER_TEMPLATE.format(
        previous_summary=previous_summary or "(none — this is the first section)",
        section_title=section.title or f"Section {section.order_index}",
        order_index=section.order_index,
    )
    user = language_instruction(detect_source_language(section.content)) + "\n\n" + user
    out = await llm.complete(
        task="rolling_summary",
        system=ROLLING_SUMMARY_SYSTEM,
        blocks=plain(user + "\n\nSection content:\n\n" + section.content),
        max_tokens=2048,
        temperature=0.1,
    )
    if len(out) > ROLLING_CONTEXT_MAX_CHARS:
        out = out[:ROLLING_CONTEXT_MAX_CHARS] + "..."
    db.update_section_field(section_id, "rolling_summary", out)
    return out
