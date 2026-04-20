"""Calendar-aware study plan.

Given a material, a start date, a cadence (days per week), and how long the
learner wants each session to be, produce a day-by-day schedule:
- which sections to study on which date
- what to review on which date (due flashcards)

Uses the learning map's `suggested_path` for ordering when present, falling
back to natural order. Uses each section's `focus_brief.estimated_minutes`
for budgeting; sections without a focus brief get a sensible default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..db import DB


DEFAULT_SECTION_MINUTES = 30
DEFAULT_MINUTES_PER_SESSION = 45
DEFAULT_DAYS_PER_WEEK = 5


@dataclass
class ScheduledSession:
    day: date
    section_ids: list[int]
    estimated_minutes: int


def plan_study(
    db: DB,
    material_id: int,
    start_date: date | None = None,
    days_per_week: int = DEFAULT_DAYS_PER_WEEK,
    minutes_per_session: int = DEFAULT_MINUTES_PER_SESSION,
) -> dict[str, Any]:
    if not 1 <= days_per_week <= 7:
        raise ValueError("days_per_week must be in 1..7")
    if minutes_per_session < 10:
        raise ValueError("minutes_per_session must be >= 10")

    material = db.get_material(material_id)
    if material is None:
        raise KeyError(f"material {material_id} not found")

    sections = db.get_sections(material_id)
    if not sections:
        raise RuntimeError(f"material {material_id} has no sections")

    learning_map = db.get_learning_map(material_id)
    ordered_indices = _resolve_order(learning_map, sections)

    # Skip sections already completed.
    done_indices = {s.order_index for s in sections if s.completed_at is not None}
    by_index = {s.order_index: s for s in sections}

    remaining: list[tuple[int, int]] = []  # (section_id, estimated_minutes)
    for idx in ordered_indices:
        if idx in done_indices or idx not in by_index:
            continue
        s = by_index[idx]
        est = DEFAULT_SECTION_MINUTES
        if s.focus_brief and isinstance(s.focus_brief.get("estimated_minutes"), (int, float)):
            est = int(s.focus_brief["estimated_minutes"])
        remaining.append((s.id, max(10, est)))

    start = start_date or datetime.now(timezone.utc).date()
    # Build session days: every day of the week if days_per_week==7, else
    # pick the first N weekdays of each week starting from `start`.
    sessions: list[ScheduledSession] = []
    current_day = start
    while remaining:
        if _is_study_day(current_day, start, days_per_week):
            budget = minutes_per_session
            bucket: list[int] = []
            total = 0
            while remaining and total + remaining[0][1] <= budget:
                sid, est = remaining.pop(0)
                bucket.append(sid)
                total += est
            if not bucket:
                # This section is larger than a whole session. Schedule it
                # anyway — the learner can split within the session or run long.
                sid, est = remaining.pop(0)
                bucket.append(sid)
                total = est
            sessions.append(
                ScheduledSession(day=current_day, section_ids=bucket, estimated_minutes=total)
            )
        current_day += timedelta(days=1)

    return {
        "material_id": material_id,
        "title": material.title,
        "start_date": start.isoformat(),
        "days_per_week": days_per_week,
        "minutes_per_session": minutes_per_session,
        "total_sessions": len(sessions),
        "total_minutes": sum(s.estimated_minutes for s in sessions),
        "sessions": [
            {
                "day": s.day.isoformat(),
                "section_ids": s.section_ids,
                "sections": [
                    {
                        "section_id": by_index_by_id(sections, sid).id,
                        "order_index": by_index_by_id(sections, sid).order_index,
                        "title": by_index_by_id(sections, sid).title,
                    }
                    for sid in s.section_ids
                ],
                "estimated_minutes": s.estimated_minutes,
            }
            for s in sessions
        ],
    }


def _resolve_order(learning_map, sections) -> list[int]:
    if learning_map is not None:
        suggested = learning_map.map_json.get("suggested_path") or []
        ordered: list[int] = []
        seen: set[int] = set()
        for step in suggested:
            for idx in step.get("section_ids") or []:
                if idx not in seen:
                    ordered.append(idx)
                    seen.add(idx)
        # Append any sections not mentioned in the path, in order.
        for s in sections:
            if s.order_index not in seen:
                ordered.append(s.order_index)
                seen.add(s.order_index)
        if ordered:
            return ordered
    # No usable map — natural order.
    return [s.order_index for s in sorted(sections, key=lambda s: s.order_index)]


def _is_study_day(day: date, start: date, days_per_week: int) -> bool:
    """`days_per_week` study days per rolling 7-day window starting at `start`.

    Simple approach: within each 7-day block, the first `days_per_week` days
    are study days. With days_per_week=5 and start on a Monday this gives
    Mon-Fri; with start on a Wednesday it gives Wed-Sun.
    """
    if days_per_week >= 7:
        return True
    delta = (day - start).days
    return delta % 7 < days_per_week


def by_index_by_id(sections, sid):
    for s in sections:
        if s.id == sid:
            return s
    raise KeyError(f"section id {sid} not in material")
