"""Progress roll-ups for a material and for the whole library.

Time tracking
-------------
We do not instrument start/stop timers. Instead we derive two honest signals
from the timestamps already persisted:

- `time_spent_seconds`: wall-clock span between the earliest and latest
  recorded activity timestamp on the material's sections — phase
  `updated_at`/`completed_at` values, plus `section.completed_at`. This is
  inflated by idle gaps (the learner leaves the tab open overnight); it is
  *not* inflated by having more sections. It's a wall-clock "engagement
  window," not a desk-time total. The field is `None` when there are no
  activity timestamps yet.

- `last_activity`: the most recent of {section.completed_at, any phase
  `updated_at` or `completed_at`, flashcard `next_review` when
  review_count > 0, flashcard `created_at`}. That broader signal catches
  review-only sessions and in-progress study that hasn't hit Anchor yet,
  both of which the narrower version missed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from ..db import DB


def material_progress(db: DB, material_id: int) -> dict:
    m = db.get_material(material_id)
    if m is None:
        raise KeyError(f"material {material_id} not found")
    sections = db.get_sections(material_id)
    cards = db.list_flashcards(material_id=material_id)
    now = datetime.now(timezone.utc)
    due = [c for c in cards if not c.is_mastered and c.next_review and c.next_review <= now]
    mastered = [c for c in cards if c.is_mastered]

    completed_sections = [s for s in sections if s.completed_at is not None]

    # Collect every activity timestamp we know about.
    activity: list[datetime] = []
    for s in sections:
        if s.completed_at is not None:
            activity.append(s.completed_at)
        for phase_name, phase_blob in (s.phase_data or {}).items():
            for key in ("updated_at", "completed_at"):
                ts = _parse_iso(phase_blob.get(key))
                if ts is not None:
                    activity.append(ts)
    for c in cards:
        if c.created_at is not None:
            activity.append(c.created_at)
        # Only treat next_review as activity if the card has actually been
        # reviewed at least once; otherwise it's just the initial due-now
        # stamp from creation.
        if c.review_count > 0 and c.next_review is not None:
            activity.append(c.next_review)

    time_spent_seconds: int | None = None
    last_activity: datetime | None = None
    if activity:
        time_spent_seconds = int((max(activity) - min(activity)).total_seconds())
        last_activity = max(activity)

    return {
        "material_id": m.id,
        "title": m.title,
        "sections_total": len(sections),
        "sections_completed": len(completed_sections),
        "flashcards_total": len(cards),
        "flashcards_due": len(due),
        "flashcards_mastered": len(mastered),
        "last_activity": last_activity.isoformat() if last_activity else None,
        "time_spent_seconds": time_spent_seconds,
        "has_learning_map": db.get_learning_map(material_id) is not None,
    }


def library_progress(db: DB) -> dict:
    materials = db.list_materials()
    per = [material_progress(db, m.id) for m in materials]
    totals = {
        "materials": len(materials),
        "sections_completed": sum(p["sections_completed"] for p in per),
        "sections_total": sum(p["sections_total"] for p in per),
        "flashcards_total": sum(p["flashcards_total"] for p in per),
        "flashcards_due": sum(p["flashcards_due"] for p in per),
        "flashcards_mastered": sum(p["flashcards_mastered"] for p in per),
        "time_spent_seconds": sum((p["time_spent_seconds"] or 0) for p in per),
    }
    return {"totals": totals, "materials": per}


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
