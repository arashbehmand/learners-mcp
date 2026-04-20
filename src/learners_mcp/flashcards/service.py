"""Flashcard suggestion + review business logic.

`suggest_flashcards` is the one place where duplicate-prevention matters.
We read only DB-committed cards for the section (never ephemeral suggestions
the host agent might be holding in chat), pass them to the LLM with an
explicit "DO NOT DUPLICATE" instruction, and return the candidates without
committing them. The host agent calls `add_flashcard` per selection.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..db import DB
from ..llm.client import LLM, plain
from ..llm.prompts import SUGGEST_CARDS_SYSTEM, SUGGEST_CARDS_USER_TEMPLATE
from ..study.context import build_learning_context, format_context_for_flashcards
from .sm2 import review

log = logging.getLogger(__name__)


async def suggest_flashcards(
    db: DB,
    llm: LLM,
    section_id: int,
    n: int = 3,
) -> list[dict[str, str]]:
    section = db.get_section(section_id)
    if section is None:
        raise KeyError(f"section {section_id} not found")

    committed = db.list_flashcards(section_id=section_id)
    committed_payload = [{"question": f.question, "answer": f.answer} for f in committed]

    context = build_learning_context(
        section_content=section.content,
        phase_data=section.phase_data,
        flashcards=committed_payload,
        include_conversations=False,
        rolling_summary=section.rolling_summary,
        section_ref=f"§{section.order_index}: {section.title or ''}".strip(": "),
    )
    formatted = format_context_for_flashcards(context)

    user = SUGGEST_CARDS_USER_TEMPLATE.format(n=n, section_ref=section.order_index)
    payload = await llm.complete_json(
        task="flashcards",
        system=SUGGEST_CARDS_SYSTEM,
        blocks=plain(user + "\n\n" + formatted),
        max_tokens=1024,
        temperature=0.4,
    )
    cards = payload.get("flashcards") or []
    cleaned: list[dict[str, str]] = []
    for c in cards:
        q = (c.get("question") or "").strip()
        a = (c.get("answer") or "").strip()
        if q and a:
            cleaned.append({"question": q, "answer": a})
    return cleaned[:n]


def review_flashcard(db: DB, flashcard_id: int, knew_it: bool) -> dict:
    card = db.get_flashcard(flashcard_id)
    if card is None:
        raise KeyError(f"flashcard {flashcard_id} not found")
    now = datetime.now(timezone.utc)
    new_state = review(card.card_state(), knew_it, now=now)
    # record_review writes both SM-2 state AND a review_events row so
    # streak / weekly-report metrics reflect actual study activity.
    db.record_review(flashcard_id, new_state, knew_it=knew_it, reviewed_at=now)
    return {
        "flashcard_id": flashcard_id,
        "knew_it": knew_it,
        "new_interval_days": new_state.interval_days,
        "new_ease_factor": round(new_state.ease_factor, 3),
        "review_count": new_state.review_count,
        "next_review": new_state.next_review.isoformat(),
        "is_mastered": new_state.is_mastered,
    }
