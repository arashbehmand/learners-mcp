"""Cross-material concept linking."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from learners_mcp.db import DB, content_hash
from learners_mcp.orientation.cross_material import (
    format_known_concepts_block,
    gather_known_concepts,
)


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


def test_gather_excludes_current_material(tmp_path):
    db = _mk_db(tmp_path)
    mid1 = db.create_material("Stats", "txt", None, content_hash("m1"))
    mid2 = db.create_material("ML", "txt", None, content_hash("m2"))
    db.create_section(mid1, "Intro", "body", 1)
    db.create_section(mid2, "Intro", "body", 1)
    db.upsert_learning_map(
        mid1,
        {"key_concepts": [{"name": "Probability", "why_load_bearing": "foundation"}]},
        "# m1",
    )
    db.upsert_learning_map(
        mid2,
        {
            "key_concepts": [
                {"name": "Gradient descent", "why_load_bearing": "optimization"}
            ]
        },
        "# m2",
    )

    known_from_mid2 = gather_known_concepts(db, exclude_material_id=mid2)
    assert len(known_from_mid2) == 1
    assert known_from_mid2[0]["material_id"] == mid1
    assert known_from_mid2[0]["concepts"][0]["name"] == "Probability"


def test_gather_labels_maturity(tmp_path):
    db = _mk_db(tmp_path)
    mid1 = db.create_material("Stats", "txt", None, content_hash("x1"))
    mid2 = db.create_material("ML", "txt", None, content_hash("x2"))
    s1 = db.create_section(mid1, "A", "body", 1)
    db.update_section_field(s1, "completed_at", datetime.now(timezone.utc))
    db.create_section(mid2, "A", "body", 1)
    db.upsert_learning_map(mid1, {"key_concepts": [{"name": "X"}]}, "# m")
    db.upsert_learning_map(mid2, {"key_concepts": [{"name": "Y"}]}, "# m")

    known = gather_known_concepts(db, exclude_material_id=mid2)
    assert known[0]["maturity"] == "mastered"


def test_format_block_is_empty_when_no_other_materials(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Solo", "txt", None, content_hash("solo"))
    db.create_section(mid, "A", "body", 1)
    known = gather_known_concepts(db, exclude_material_id=mid)
    assert known == []
    assert format_known_concepts_block(known) == ""
