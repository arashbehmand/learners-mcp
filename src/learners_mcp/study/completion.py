"""Completion report generation — the closing artifact of the Anchor phase.

Uses the full learning journey: source content + every phase response +
optional chat transcripts + the committed flashcards. Produces a warm,
specific markdown report. Idempotent (overwrites prior reports for the
same section).
"""

from __future__ import annotations

import logging

from ..db import DB
from ..llm.client import LLM, plain
from ..llm.prompts import COMPLETION_REPORT_SYSTEM, COMPLETION_REPORT_USER_TEMPLATE
from .context import build_learning_context, format_context_for_completion

log = logging.getLogger(__name__)


async def generate_completion_report(
    db: DB,
    llm: LLM,
    section_id: int,
) -> str:
    section = db.get_section(section_id)
    if section is None:
        raise KeyError(f"section {section_id} not found")

    committed = db.list_flashcards(section_id=section_id)
    cards_payload = [{"question": f.question, "answer": f.answer} for f in committed]

    context = build_learning_context(
        section_content=section.content,
        phase_data=section.phase_data,
        flashcards=cards_payload,
        include_conversations=True,
        rolling_summary=section.rolling_summary,
        section_ref=f"§{section.order_index}: {section.title or ''}".strip(": "),
    )
    formatted = format_context_for_completion(context)

    report_md = await llm.complete(
        task="completion_report",
        system=COMPLETION_REPORT_SYSTEM,
        blocks=plain(COMPLETION_REPORT_USER_TEMPLATE + "\n\n" + formatted),
        max_tokens=1500,
        temperature=0.4,
    )
    db.upsert_completion_report(section_id, report_md)
    return report_md
