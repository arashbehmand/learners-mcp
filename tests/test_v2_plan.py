"""Calendar-aware study plan scheduling."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from learners_mcp.db import DB, content_hash
from learners_mcp.study.plan import plan_study


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


def _seed(db: DB, n_sections: int = 4, hash_seed: str = "pl") -> int:
    mid = db.create_material("Course", "md", None, content_hash(hash_seed))
    for i in range(1, n_sections + 1):
        sid = db.create_section(mid, f"Section {i}", "body" * 50, i)
        db.update_section_field(
            sid,
            "focus_brief",
            {"focus": "f", "estimated_minutes": 25},
        )
    return mid


def test_plan_schedules_all_sections(tmp_path):
    db = _mk_db(tmp_path)
    mid = _seed(db, 4)
    plan = plan_study(
        db, mid, start_date=date(2026, 1, 5), days_per_week=5, minutes_per_session=60
    )
    scheduled_ids = [sid for session in plan["sessions"] for sid in session["section_ids"]]
    assert len(scheduled_ids) == 4
    assert plan["sessions"][0]["day"] == "2026-01-05"
    # With 25-minute sections and a 60-minute budget, 2 sections per session.
    assert plan["sessions"][0]["estimated_minutes"] <= 60


def test_plan_skips_completed_sections(tmp_path):
    from datetime import datetime, timezone
    db = _mk_db(tmp_path)
    mid = _seed(db, 3, hash_seed="pl2")
    sections = db.get_sections(mid)
    db.update_section_field(sections[0].id, "completed_at", datetime.now(timezone.utc))
    plan = plan_study(db, mid, start_date=date(2026, 1, 5))
    scheduled_ids = [sid for session in plan["sessions"] for sid in session["section_ids"]]
    assert sections[0].id not in scheduled_ids
    assert len(scheduled_ids) == 2


def test_plan_respects_suggested_path_from_map(tmp_path):
    db = _mk_db(tmp_path)
    mid = _seed(db, 3, hash_seed="pl3")
    # Suggest reverse order.
    db.upsert_learning_map(
        mid,
        {"suggested_path": [{"section_ids": [3]}, {"section_ids": [2]}, {"section_ids": [1]}]},
        "# m",
    )
    sections = {s.order_index: s for s in db.get_sections(mid)}
    plan = plan_study(
        db, mid, start_date=date(2026, 1, 5), days_per_week=7, minutes_per_session=20
    )
    # minutes_per_session=20 < 25/section → each session gets one section.
    first_scheduled = plan["sessions"][0]["section_ids"][0]
    assert first_scheduled == sections[3].id


def test_plan_rejects_bad_inputs(tmp_path):
    db = _mk_db(tmp_path)
    mid = _seed(db, 2, hash_seed="pl4")
    with pytest.raises(ValueError):
        plan_study(db, mid, days_per_week=0)
    with pytest.raises(ValueError):
        plan_study(db, mid, minutes_per_session=5)
