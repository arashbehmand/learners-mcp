"""material_progress + library_progress aggregation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from learners_mcp.db import DB, content_hash
from learners_mcp.flashcards.sm2 import CardState
from learners_mcp.study.progress import library_progress, material_progress


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


def _past(d: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=d)


def _future(d: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=d)


def test_material_progress_counts_correctly(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("a"))
    s1 = db.create_section(mid, "A", "body", 1)
    s2 = db.create_section(mid, "B", "body", 2)
    db.update_section_field(s1, "completed_at", datetime.now(timezone.utc))

    f_due = db.create_flashcard(mid, s1, "Q1", "A1")
    f_mastered = db.create_flashcard(mid, s1, "Q2", "A2")
    f_future = db.create_flashcard(mid, s2, "Q3", "A3")

    db.apply_review(
        f_mastered,
        CardState(2.5, 60, 6, _future(60), True),
    )
    db.apply_review(
        f_future,
        CardState(2.5, 2, 1, _future(2), False),
    )
    db.apply_review(
        f_due,
        CardState(2.5, 1, 1, _past(1), False),
    )

    stats = material_progress(db, mid)
    assert stats["material_id"] == mid
    assert stats["sections_total"] == 2
    assert stats["sections_completed"] == 1
    assert stats["flashcards_total"] == 3
    assert stats["flashcards_mastered"] == 1
    assert stats["flashcards_due"] == 1
    assert stats["has_learning_map"] is False


def test_library_progress_sums_materials(tmp_path):
    db = _mk_db(tmp_path)
    for title, hash_seed in [("One", "h1"), ("Two", "h2")]:
        mid = db.create_material(title, "txt", None, content_hash(hash_seed))
        db.create_section(mid, "s", "body", 1)

    lib = library_progress(db)
    assert lib["totals"]["materials"] == 2
    assert lib["totals"]["sections_total"] == 2
    assert len(lib["materials"]) == 2
