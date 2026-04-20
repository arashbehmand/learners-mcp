"""Portable export/import — round-trip verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from learners_mcp.db import DB, content_hash
from learners_mcp.export.portable import export_project, import_project
from learners_mcp.flashcards.sm2 import CardState


def _mk_db(tmp_path: Path, name: str) -> DB:
    return DB(tmp_path / name)


def _seed_material(db: DB, hash_seed: str = "seed") -> int:
    mid = db.create_material("Deep Work", "pdf", "/tmp/x.pdf", content_hash(hash_seed))
    s1 = db.create_section(mid, "Chapter 1", "content 1", 1)
    s2 = db.create_section(mid, "Chapter 2", "content 2", 2)
    db.update_section_field(s1, "notes", "# §1 notes")
    db.update_section_field(s1, "rolling_summary", "rolling for §1")
    db.update_section_field(s2, "focus_brief", {"focus": "core idea", "estimated_minutes": 20})
    db.update_phase_data(s1, "preview", {"response": "first thoughts"})
    db.update_phase_data(s1, "explain", {"response": "deep work means..."})
    db.upsert_learning_map(mid, {"objectives": ["focus"], "key_concepts": []}, "# Map")
    db.upsert_completion_report(s1, "# Completion for §1")

    fid = db.create_flashcard(mid, s1, "Q1?", "A1")
    now = datetime.now(timezone.utc)
    db.apply_review(fid, CardState(2.6, 5, 2, now + timedelta(days=5), False))
    db.add_evaluation(s1, "explain", "deep work means...", {"verdict": "solid"}, "# eval")
    return mid


def test_export_round_trips_into_fresh_db(tmp_path):
    src = _mk_db(tmp_path, "src.sqlite")
    mid = _seed_material(src)

    export_path = tmp_path / "project.json"
    info = export_project(src, mid, export_path)
    assert info["sections"] == 2
    assert info["flashcards"] == 1
    assert export_path.exists()

    dst = _mk_db(tmp_path, "dst.sqlite")
    result = import_project(dst, export_path)
    new_mid = result["material_id"]
    assert result["sections_imported"] == 2
    assert result["flashcards_imported"] == 1

    restored = dst.get_material(new_mid)
    assert restored.title == "Deep Work"

    sections = dst.get_sections(new_mid)
    assert [s.title for s in sections] == ["Chapter 1", "Chapter 2"]
    assert sections[0].notes == "# §1 notes"
    assert sections[0].rolling_summary == "rolling for §1"
    assert sections[1].focus_brief == {"focus": "core idea", "estimated_minutes": 20}
    assert sections[0].phase_data["preview"]["response"] == "first thoughts"

    cards = dst.list_flashcards(material_id=new_mid)
    assert len(cards) == 1
    # ease_factor was bumped by apply_review (max 2.5 cap); interval preserved.
    assert cards[0].interval_days == 5
    # Section_id must be rewired to the new section id, not the original.
    assert cards[0].section_id == sections[0].id

    report = dst.get_completion_report(sections[0].id)
    assert report is not None and report[0].startswith("# Completion")

    evals = dst.list_evaluations(sections[0].id)
    assert len(evals) == 1
    assert evals[0]["phase"] == "explain"


def test_import_rejects_duplicate_content_hash(tmp_path):
    src = _mk_db(tmp_path, "src.sqlite")
    _seed_material(src, hash_seed="duplicate-seed")

    export_path = tmp_path / "project.json"
    export_project(src, 1, export_path)

    # Import into the same DB (already has the material) → must raise.
    with pytest.raises(ValueError, match="already exists"):
        import_project(src, export_path)
