"""Prerequisite checking verdicts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from learners_mcp.db import DB, content_hash
from learners_mcp.flashcards.sm2 import CardState
from learners_mcp.study.prereqs import check_prerequisites


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


def _seed(db: DB, hash_seed="p") -> tuple[int, int, int]:
    mid = db.create_material("Doc", "txt", None, content_hash(hash_seed))
    s1 = db.create_section(mid, "A", "body1", 1)
    s2 = db.create_section(mid, "B", "body2", 2)
    return mid, s1, s2


def test_ready_when_no_learning_map(tmp_path):
    db = _mk_db(tmp_path)
    _, s1, _ = _seed(db)
    verdict = check_prerequisites(db, s1)
    assert verdict["verdict"] == "ready"
    assert "No learning map" in verdict["reason"]


def test_ready_when_no_earlier_sections_feed_in(tmp_path):
    db = _mk_db(tmp_path)
    mid, s1, s2 = _seed(db)
    db.upsert_learning_map(
        mid,
        {
            "key_concepts": [
                {"name": "X", "sections": [1]},
                {"name": "Y", "sections": [2]},
            ]
        },
        "# m",
    )
    verdict = check_prerequisites(db, s2)
    # X is only in §1; Y is only in §2 — no concept spans earlier → §2.
    assert verdict["verdict"] == "ready"


def test_review_recommended_when_prereq_cards_due(tmp_path):
    db = _mk_db(tmp_path)
    mid, s1, s2 = _seed(db)
    db.upsert_learning_map(
        mid,
        {"key_concepts": [{"name": "Shared", "sections": [1, 2]}]},
        "# m",
    )
    # §1 needs to count as "studied" for the retention check to fire.
    db.update_section_field(s1, "completed_at", datetime.now(timezone.utc))
    fid = db.create_flashcard(mid, s1, "Q", "A")
    past = datetime.now(timezone.utc) - timedelta(days=2)
    db.apply_review(fid, CardState(2.5, 1, 1, past, False))

    verdict = check_prerequisites(db, s2)
    assert verdict["verdict"] == "review_recommended"
    assert verdict["review_cards"]
    assert verdict["prerequisite_sections"][0]["order_index"] == 1


def test_review_required_on_many_overdue_with_concept_hit(tmp_path):
    db = _mk_db(tmp_path)
    mid, s1, s2 = _seed(db)
    db.upsert_learning_map(
        mid,
        {"key_concepts": [{"name": "Core", "sections": [1, 2]}]},
        "# m",
    )
    # §1 needs to count as "studied" for the retention check to fire.
    db.update_section_field(s1, "completed_at", datetime.now(timezone.utc))
    past = datetime.now(timezone.utc) - timedelta(days=3)
    for i in range(6):
        fid = db.create_flashcard(mid, s1, f"Q{i}", f"A{i}")
        db.apply_review(fid, CardState(2.5, 1, 1, past, False))

    verdict = check_prerequisites(db, s2)
    assert verdict["verdict"] == "review_required"
    assert len(verdict["review_cards"]) == 6
