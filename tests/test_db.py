"""Database layer — schema + basic CRUD + idempotency."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from learners_mcp.db import DB, content_hash
from learners_mcp.flashcards.sm2 import CardState


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


def test_material_dedupe_on_content_hash(tmp_path):
    db = _mk_db(tmp_path)
    h = content_hash("hello world")
    mid = db.create_material("Doc", "txt", None, h)
    existing = db.find_material_by_hash(h)
    assert existing is not None
    assert existing.id == mid


def test_sections_and_phase_data_roundtrip(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("x"))
    sid = db.create_section(mid, "Title", "Body text", 1)
    s = db.get_section(sid)
    assert s is not None
    assert s.phase_data == {}
    assert s.current_phase == "preview"

    db.update_phase_data(sid, "preview", {"response": "initial thoughts"})
    db.update_phase_data(sid, "explain", {"response": "in own words"})
    s2 = db.get_section(sid)
    assert s2.phase_data["preview"]["response"] == "initial thoughts"
    assert s2.phase_data["explain"]["response"] == "in own words"


def test_update_section_fields(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("y"))
    sid = db.create_section(mid, "t", "body", 1)
    db.update_section_field(sid, "rolling_summary", "summary")
    db.update_section_field(sid, "notes", "# notes")
    db.update_section_field(sid, "focus_brief", {"focus": "the thing"})
    db.update_section_field(sid, "current_phase", "explain")
    s = db.get_section(sid)
    assert s.rolling_summary == "summary"
    assert s.notes == "# notes"
    assert s.focus_brief == {"focus": "the thing"}
    assert s.current_phase == "explain"


def test_learning_map_upsert_bumps_regeneration_count(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("z"))
    db.upsert_learning_map(mid, {"objectives": ["x"]}, "# Map\n")
    first = db.get_learning_map(mid)
    assert first.regeneration_count == 0
    db.upsert_learning_map(mid, {"objectives": ["x", "y"]}, "# Map v2\n")
    second = db.get_learning_map(mid)
    assert second.regeneration_count == 1
    assert second.map_json["objectives"] == ["x", "y"]


def test_flashcards_create_and_review_updates_state(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("q"))
    sid = db.create_section(mid, "t", "b", 1)
    fid = db.create_flashcard(mid, sid, "What?", "Thing")
    card = db.get_flashcard(fid)
    assert card.interval_days == 0
    new_state = CardState(
        ease_factor=2.5,
        interval_days=1,
        review_count=1,
        next_review=datetime.now(timezone.utc),
        is_mastered=False,
    )
    db.apply_review(fid, new_state)
    card2 = db.get_flashcard(fid)
    assert card2.interval_days == 1
    assert card2.review_count == 1


def test_list_flashcards_filters(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("w"))
    sid = db.create_section(mid, "t", "b", 1)
    fid1 = db.create_flashcard(mid, sid, "Q1", "A1")
    fid2 = db.create_flashcard(mid, sid, "Q2", "A2")
    # Both cards start due.
    due = db.list_flashcards(filter_="due")
    assert {c.id for c in due} == {fid1, fid2}

    # Mark fid1 mastered via apply_review.
    db.apply_review(
        fid1,
        CardState(
            ease_factor=2.5,
            interval_days=30,
            review_count=5,
            next_review=datetime.now(timezone.utc),
            is_mastered=True,
        ),
    )
    mastered = db.list_flashcards(filter_="mastered")
    assert [c.id for c in mastered] == [fid1]
