from __future__ import annotations

from pathlib import Path

import pytest

from learners_mcp.db import DB, content_hash
from learners_mcp.export.artifacts import export_material_artifacts
from learners_mcp.ingestion.pipeline import preparation_status
from learners_mcp.language import detect_source_language
from learners_mcp.llm.prompts import LANGUAGE_POLICY_VERSION


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


PERSIAN_TEXT = (
    "این متن فارسی درباره مغالطه و استدلال است. "
    "خواننده باید تفاوت میان مقدمه، نتیجه، و خطای منطقی را بفهمد. "
    "نمونه‌ها نشان می‌دهند که چرا ارزیابی دلیل از احساسات جداست."
)


def test_detect_source_language_identifies_persian():
    info = detect_source_language(PERSIAN_TEXT * 5)

    assert info["code"] == "fa"
    assert info["name"] == "Persian/Farsi"
    assert "Persian/Farsi" in info["artifact_instruction"]


def test_detect_source_language_identifies_english():
    info = detect_source_language(
        "This chapter explains how arguments use premises to support a conclusion. "
        "Readers should distinguish evidence from emotional persuasion."
    )

    assert info["code"] == "en"
    assert info["name"] == "English"
    assert info["direction"] == "ltr"


def test_detect_source_language_handles_empty_text():
    info = detect_source_language("")

    assert info["code"] == "und"
    assert info["direction"] == "auto"


@pytest.mark.asyncio
async def test_prepare_regenerates_stale_language_policy_artifacts(
    tmp_path, monkeypatch
):
    from learners_mcp.ingestion import pipeline

    db = _mk_db(tmp_path)
    mid = db.create_material("کتاب فارسی", "txt", None, content_hash("fa"))
    sid = db.create_section(mid, "بخش اول", PERSIAN_TEXT * 20, 1)
    db.upsert_learning_map(mid, {"objectives": ["old English"]}, "# Learning Map\nold")
    db.update_section_field(
        sid,
        "focus_brief",
        {"focus": "old English", "estimated_minutes": 5},
    )
    db.update_section_field(sid, "notes", "# Old English notes")

    before = preparation_status(db, mid)
    assert before["map"] == "pending"
    assert before["focus_briefs"][1] == "pending"
    assert before["notes"] == "pending"

    calls: list[str] = []

    async def fake_map(llm, full_text, section_index, **kwargs):
        calls.append("map")
        assert "Persian/Farsi" in kwargs["language_instruction"]
        return {"objectives": ["هدف فارسی"], "suggested_path": []}, "# نقشه یادگیری"

    async def fake_focus(llm, full_text, order_index, title, **kwargs):
        calls.append("focus")
        assert "Persian/Farsi" in kwargs["language_instruction"]
        return {"focus": "تمرکز فارسی", "estimated_minutes": 10}

    async def fake_notes(llm, section_content, section_ref, **kwargs):
        calls.append("notes")
        assert "Persian/Farsi" in kwargs["language_instruction"]
        return "# یادداشت فارسی"

    monkeypatch.setattr(pipeline, "generate_material_map", fake_map)
    monkeypatch.setattr(pipeline, "generate_focus_brief", fake_focus)
    monkeypatch.setattr(pipeline, "extract_notes", fake_notes)

    await pipeline.prepare_material(db, object(), mid, scope="all", force=False)

    assert calls == ["map", "focus", "notes"]
    status = db.get_material(mid).ingestion_status
    assert status["source_language"]["code"] == "fa"
    assert (
        status["artifact_language_policy"]["map"]["version"] == LANGUAGE_POLICY_VERSION
    )
    assert (
        status["artifact_language_policy"]["focus_briefs"]["1"]["version"]
        == LANGUAGE_POLICY_VERSION
    )
    assert (
        status["artifact_language_policy"]["notes"]["1"]["version"]
        == LANGUAGE_POLICY_VERSION
    )


def test_persian_material_markdown_mirror_uses_persian_static_labels(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("کتاب فارسی", "txt", None, content_hash("fa-render"))
    sid = db.create_section(mid, "بخش اول", PERSIAN_TEXT, 1)
    db.set_ingestion_status(
        mid,
        {
            "source_language": detect_source_language(PERSIAN_TEXT),
            "artifact_language_policy": {
                "map": {"version": LANGUAGE_POLICY_VERSION, "source_language": "fa"}
            },
        },
    )
    db.upsert_learning_map(
        mid,
        {"objectives": ["هدف فارسی"], "suggested_path": []},
        "# نقشه یادگیری\n\nهدف فارسی",
    )
    db.update_section_field(
        sid,
        "focus_brief",
        {
            "focus": "تمرکز فارسی",
            "key_terms": [{"term": "استدلال", "gloss": "ساختار دلیل"}],
            "estimated_minutes": 12,
        },
    )

    result = export_material_artifacts(
        db, mid, output_dir=tmp_path / "learners", format="markdown"
    )
    artifact_dir = Path(result["artifact_dir"])
    focus_md = (artifact_dir / "focus-briefs.md").read_text(encoding="utf-8")
    sections_md = (artifact_dir / "sections.md").read_text(encoding="utf-8")

    assert "راهنمای تمرکز" in focus_md
    assert "زمان تخمینی" in focus_md
    assert "اصطلاحات کلیدی" in focus_md
    assert "Focus brief" not in focus_md
    assert "Current phase" not in sections_md
    assert "مرحله فعلی" in sections_md
