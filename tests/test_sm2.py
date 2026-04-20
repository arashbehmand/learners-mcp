"""SM-2 scheduling — pure-function tests, no DB involved."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from learners_mcp.flashcards.sm2 import CardState, initial_state, review


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_initial_state_is_due_now():
    s = initial_state(now=NOW)
    assert s.ease_factor == 2.5
    assert s.interval_days == 0
    assert s.review_count == 0
    assert s.next_review == NOW
    assert s.is_mastered is False


def test_first_correct_review_sets_1_day_interval():
    s = initial_state(now=NOW)
    out = review(s, knew_it=True, now=NOW)
    assert out.interval_days == 1
    assert out.review_count == 1
    assert out.next_review == NOW + timedelta(days=1)
    assert out.is_mastered is False


def test_failed_review_resets_to_1_day_and_drops_ease():
    s = CardState(
        ease_factor=2.5,
        interval_days=15,
        review_count=3,
        next_review=NOW,
        is_mastered=False,
    )
    out = review(s, knew_it=False, now=NOW)
    assert out.interval_days == 1
    assert out.ease_factor == 2.3  # 2.5 - 0.2
    assert out.review_count == 4
    assert out.is_mastered is False


def test_ease_floor_is_1_3():
    s = CardState(
        ease_factor=1.35,
        interval_days=5,
        review_count=4,
        next_review=NOW,
        is_mastered=False,
    )
    out = review(s, knew_it=False, now=NOW)
    assert out.ease_factor == 1.3


def test_ease_ceiling_is_2_5():
    s = CardState(
        ease_factor=2.5,
        interval_days=10,
        review_count=3,
        next_review=NOW,
        is_mastered=False,
    )
    out = review(s, knew_it=True, now=NOW)
    assert out.ease_factor == 2.5  # capped


def test_mastery_requires_5_reviews_and_30_day_interval():
    # Interval big enough but too few reviews — not mastered.
    s = CardState(2.5, 30, 3, NOW, False)
    out = review(s, knew_it=True, now=NOW)
    assert out.review_count == 4
    assert out.is_mastered is False

    # Now 5 reviews and a big interval — mastered.
    s2 = CardState(2.5, 30, 4, NOW, False)
    out2 = review(s2, knew_it=True, now=NOW)
    assert out2.review_count == 5
    assert out2.interval_days >= 30
    assert out2.is_mastered is True


def test_interval_grows_by_ease_factor():
    s = CardState(2.0, 10, 2, NOW, False)
    out = review(s, knew_it=True, now=NOW)
    # 10 * 2.0 = 20 → rounded stays 20, ease bumped to 2.1
    assert out.interval_days == 20
    assert round(out.ease_factor, 3) == 2.1
