"""Streak + weekly report."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from learners_mcp.db import DB, content_hash
from learners_mcp.study.streak import compute_streak, weekly_report, render_weekly_markdown


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


def _stamp(d: date, h: int = 12) -> str:
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).replace(hour=h).isoformat()


def test_streak_empty_db():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        db = DB(Path(td) / "e.sqlite")
        result = compute_streak(db, today=date(2026, 4, 19))
        assert result == {"current_streak_days": 0, "longest_streak_days": 0, "today_active": False}


def test_streak_counts_consecutive_days(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("D", "txt", None, content_hash("s1"))
    sid = db.create_section(mid, "A", "body", 1)
    # Activity on Apr 17, 18, 19.
    for d in [date(2026, 4, 17), date(2026, 4, 18), date(2026, 4, 19)]:
        db.update_phase_data(sid, "preview", {"response": "r", "updated_at": _stamp(d)})
    # Note: update_phase_data overwrites — to record multiple days, switch phases.
    db.update_phase_data(sid, "explain", {"response": "r", "updated_at": _stamp(date(2026, 4, 17))})
    db.update_phase_data(sid, "question", {"response": "r", "updated_at": _stamp(date(2026, 4, 18))})

    result = compute_streak(db, today=date(2026, 4, 19))
    assert result["current_streak_days"] == 3
    assert result["longest_streak_days"] >= 3
    assert result["today_active"] is True


def test_streak_breaks_on_gap(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("D", "txt", None, content_hash("s2"))
    sid = db.create_section(mid, "A", "body", 1)
    # Activity on Apr 17 and Apr 19 — Apr 18 missing.
    db.update_phase_data(sid, "preview", {"response": "r", "updated_at": _stamp(date(2026, 4, 17))})
    db.update_phase_data(sid, "explain", {"response": "r", "updated_at": _stamp(date(2026, 4, 19))})

    result = compute_streak(db, today=date(2026, 4, 19))
    assert result["current_streak_days"] == 1  # only today is part of streak
    assert result["today_active"] is True


def test_weekly_report_shape(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("D", "txt", None, content_hash("w1"))
    sid = db.create_section(mid, "A", "body", 1)
    db.update_phase_data(sid, "preview", {"response": "r", "updated_at": _stamp(date(2026, 4, 18))})
    db.create_flashcard(mid, sid, "Q", "A", created_at=datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc))

    rep = weekly_report(db, today=date(2026, 4, 19))
    assert "window_start" in rep and "window_end" in rep
    assert rep["totals"]["sections_touched"] >= 1
    assert rep["totals"]["cards_added"] >= 1

    md = render_weekly_markdown(rep)
    assert "Weekly report" in md
    assert "Sections touched" in md
