"""Study streak + weekly report.

Streak: consecutive calendar days (UTC) with at least one activity.
Activity sources:
  - phase updates (`phase_data[*].updated_at` / `completed_at`)
  - section completions (`section.completed_at`)
  - flashcard creation (`flashcard.created_at`)
  - flashcard reviews (rows in `review_events`)

Weekly report: markdown summary of the last 7 UTC days — sections
touched/completed, cards added, cards reviewed (real review events in the
window), cards mastered lifetime.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..db import DB


def compute_streak(db: DB, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    active_days = _active_days(db)
    if not active_days:
        return {
            "current_streak_days": 0,
            "longest_streak_days": 0,
            "today_active": False,
        }

    today_active = today in active_days

    # Walk backwards from today counting consecutive days.
    current = 0
    day = today if today_active else today - timedelta(days=1)
    while day in active_days:
        current += 1
        day -= timedelta(days=1)

    # Longest run anywhere in history.
    longest = 0
    run = 0
    prev: date | None = None
    for d in sorted(active_days):
        if prev is not None and d == prev + timedelta(days=1):
            run += 1
        else:
            run = 1
        longest = max(longest, run)
        prev = d

    return {
        "current_streak_days": current,
        "longest_streak_days": longest,
        "today_active": today_active,
        "last_active_day": max(active_days).isoformat(),
    }


def weekly_report(db: DB, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=6)  # 7-day window inclusive
    start_dt = datetime.combine(window_start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(
        today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    )

    materials = db.list_materials()
    sections_touched = 0
    sections_completed = 0
    cards_added = 0
    cards_reviewed = 0
    cards_mastered_lifetime = 0
    per_material: list[dict[str, Any]] = []

    # Global review-events roll-up for the window. Joining across materials
    # is simpler than per-material lookups because review_events → card →
    # material is indirect; just bucket by the card's material_id in memory.
    window_events = db.list_review_events(since=start_dt, until=end_dt)
    card_to_material: dict[int, int] = {}
    for m in materials:
        for c in db.list_flashcards(material_id=m.id):
            card_to_material[c.id] = m.id
    events_by_material: dict[int, int] = {}
    for ev in window_events:
        mid = card_to_material.get(ev["flashcard_id"])
        if mid is not None:
            events_by_material[mid] = events_by_material.get(mid, 0) + 1

    for m in materials:
        m_sections_touched = 0
        m_sections_completed = 0
        for s in db.get_sections(m.id):
            touched = False
            for phase_blob in (s.phase_data or {}).values():
                if _iso_in_window(phase_blob.get("updated_at"), start_dt, end_dt):
                    touched = True
                if _iso_in_window(phase_blob.get("completed_at"), start_dt, end_dt):
                    touched = True
            if touched:
                m_sections_touched += 1
            if s.completed_at and start_dt <= s.completed_at < end_dt:
                m_sections_completed += 1

        m_cards_added = 0
        m_cards_mastered_lifetime = 0
        for c in db.list_flashcards(material_id=m.id):
            if c.created_at and start_dt <= c.created_at < end_dt:
                m_cards_added += 1
            if c.is_mastered:
                m_cards_mastered_lifetime += 1

        m_cards_reviewed = events_by_material.get(m.id, 0)

        if (
            m_sections_touched
            or m_sections_completed
            or m_cards_added
            or m_cards_reviewed
        ):
            per_material.append(
                {
                    "material_id": m.id,
                    "title": m.title,
                    "sections_touched": m_sections_touched,
                    "sections_completed": m_sections_completed,
                    "cards_added": m_cards_added,
                    "cards_reviewed": m_cards_reviewed,
                }
            )

        sections_touched += m_sections_touched
        sections_completed += m_sections_completed
        cards_added += m_cards_added
        cards_reviewed += m_cards_reviewed
        cards_mastered_lifetime += m_cards_mastered_lifetime

    return {
        "window_start": window_start.isoformat(),
        "window_end": today.isoformat(),
        "totals": {
            "sections_touched": sections_touched,
            "sections_completed": sections_completed,
            "cards_added": cards_added,
            "cards_reviewed": cards_reviewed,
            "cards_mastered_lifetime": cards_mastered_lifetime,
        },
        "per_material": per_material,
    }


def render_weekly_markdown(report: dict[str, Any]) -> str:
    out = [
        f"# Weekly report — {report['window_start']} → {report['window_end']}\n",
        "## Totals\n",
        f"- Sections touched: {report['totals']['sections_touched']}",
        f"- Sections completed: {report['totals']['sections_completed']}",
        f"- Flashcards added: {report['totals']['cards_added']}",
        f"- Flashcards reviewed (this week): {report['totals']['cards_reviewed']}",
        f"- Flashcards mastered (lifetime): {report['totals']['cards_mastered_lifetime']}",
        "",
    ]
    if report["per_material"]:
        out.append("## Per material")
        for m in report["per_material"]:
            out.append(
                f"- **{m['title']}** (#{m['material_id']}): "
                f"touched {m['sections_touched']}, completed {m['sections_completed']}, "
                f"added {m['cards_added']}, reviewed {m['cards_reviewed']}"
            )
    else:
        out.append("_No study activity this week._")
    return "\n".join(out) + "\n"


def _active_days(db: DB) -> set[date]:
    days: set[date] = set()
    for m in db.list_materials():
        for s in db.get_sections(m.id):
            if s.completed_at is not None:
                days.add(s.completed_at.date())
            for phase_blob in (s.phase_data or {}).values():
                for key in ("updated_at", "completed_at"):
                    dt = _parse_iso(phase_blob.get(key))
                    if dt is not None:
                        days.add(dt.date())
        for c in db.list_flashcards(material_id=m.id):
            if c.created_at is not None:
                days.add(c.created_at.date())
    # Real review events — finally.
    for ev in db.list_review_events():
        dt = ev["reviewed_at"]
        if dt is not None:
            days.add(dt.date())
    return days


def _iso_in_window(value: object, start_dt: datetime, end_dt: datetime) -> bool:
    dt = _parse_iso(value) if isinstance(value, str) else None
    if dt is None:
        return False
    return start_dt <= dt < end_dt


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
