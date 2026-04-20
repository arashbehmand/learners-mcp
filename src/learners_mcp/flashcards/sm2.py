"""SM-2 spaced repetition scheduling.

Pure functions — no DB access, no side effects. The caller passes in the
current card state and the learner's grade; this returns the new state.

Mastery rule (from PECS): review_count >= 5 AND interval_days >= 30.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class CardState:
    ease_factor: float
    interval_days: int
    review_count: int
    next_review: datetime
    is_mastered: bool


def initial_state(now: datetime | None = None) -> CardState:
    """Fresh card — due immediately for its first review."""
    now = now or datetime.now(timezone.utc)
    return CardState(
        ease_factor=2.5,
        interval_days=0,
        review_count=0,
        next_review=now,
        is_mastered=False,
    )


def review(state: CardState, knew_it: bool, now: datetime | None = None) -> CardState:
    """Apply SM-2 update given a binary grade."""
    now = now or datetime.now(timezone.utc)
    review_count = state.review_count + 1

    if knew_it:
        if state.interval_days == 0:
            interval_days = 1
        else:
            interval_days = max(1, int(round(state.interval_days * state.ease_factor)))
        ease_factor = min(2.5, state.ease_factor + 0.1)
    else:
        interval_days = 1
        ease_factor = max(1.3, state.ease_factor - 0.2)

    is_mastered = review_count >= 5 and interval_days >= 30
    next_review = now + timedelta(days=interval_days)

    return CardState(
        ease_factor=ease_factor,
        interval_days=interval_days,
        review_count=review_count,
        next_review=next_review,
        is_mastered=is_mastered,
    )
