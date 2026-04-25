from __future__ import annotations

import json
from pathlib import Path

import pytest

from learners_mcp.db import DB, content_hash
from learners_mcp.export.artifacts import (
    auto_export_markdown_artifacts,
    export_material_artifacts,
)


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


def _seed_material(db: DB) -> int:
    mid = db.create_material(
        "Alice's Adventures", "txt", "alice.txt", content_hash("alice")
    )
    s1 = db.create_section(mid, "Down the Rabbit-Hole", "RAW SOURCE BODY SECRET", 1)
    db.create_section(mid, "Pool of Tears", "MORE RAW SOURCE BODY", 2)
    db.upsert_learning_map(
        mid,
        {"objectives": ["Understand Wonderland logic"], "suggested_path": []},
        "# Learning Map\n\nWonderland logic matters.",
    )
    db.update_section_field(
        s1,
        "focus_brief",
        {"focus": "Track unstable rules", "estimated_minutes": 15},
    )
    db.update_section_field(s1, "notes", "# Notes\n\nAlice loses control of scale.")
    db.update_phase_data(s1, "preview", {"response": "Rules will break."})
    db.update_section_field(
        s1, "rolling_summary", "Alice enters a strange logic-space."
    )
    db.create_flashcard(mid, s1, "What breaks first?", "Ordinary physical rules. [§1]")
    db.upsert_completion_report(s1, "# Completion\n\nPreview done.")
    db.add_evaluation(
        s1,
        "preview",
        "Rules will break.",
        {"verdict": "solid"},
        "# Evaluation\n\nSolid.",
    )
    return mid


def test_markdown_export_writes_readable_files_without_json(tmp_path):
    db = _mk_db(tmp_path)
    mid = _seed_material(db)

    result = export_material_artifacts(
        db, mid, output_dir=tmp_path / "learners", format="markdown"
    )

    artifact_dir = Path(result["artifact_dir"])
    assert artifact_dir.name == "alices-adventures"
    assert (artifact_dir / "README.md").exists()
    assert (artifact_dir / "sections.md").exists()
    assert (artifact_dir / "learning-map.md").exists()
    assert (artifact_dir / "focus-briefs.md").exists()
    assert (artifact_dir / "notes.md").exists()
    assert (artifact_dir / "progress.md").exists()
    assert (artifact_dir / "phase-responses.md").exists()
    assert (artifact_dir / "rolling-summaries.md").exists()
    assert (artifact_dir / "flashcards.md").exists()
    assert (artifact_dir / "completion-reports.md").exists()
    assert (artifact_dir / "evaluations.md").exists()
    assert not list(artifact_dir.rglob("*.json"))

    sections_md = (artifact_dir / "sections.md").read_text(encoding="utf-8")
    assert "Down the Rabbit-Hole" in sections_md
    assert "RAW SOURCE BODY SECRET" not in sections_md


def test_json_export_is_explicit_and_separate(tmp_path):
    db = _mk_db(tmp_path)
    mid = _seed_material(db)

    result = export_material_artifacts(
        db, mid, output_dir=tmp_path / "out", format="json"
    )

    artifact_dir = Path(result["artifact_dir"])
    json_dir = artifact_dir / "json"
    assert json_dir.exists()
    assert (json_dir / "manifest.json").exists()
    assert (json_dir / "learning-map.json").exists()
    assert (json_dir / "sections.json").exists()
    assert (json_dir / "flashcards.json").exists()
    assert not list(artifact_dir.glob("*.md"))

    manifest = json.loads((json_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["material"]["title"] == "Alice's Adventures"


def test_auto_markdown_export_respects_disabled_env(tmp_path, monkeypatch):
    db = _mk_db(tmp_path)
    mid = _seed_material(db)
    monkeypatch.setenv("LEARNERS_MCP_ARTIFACT_DIR", str(tmp_path / "learners"))
    monkeypatch.setenv("LEARNERS_MCP_ARTIFACT_MIRROR", "off")

    result = auto_export_markdown_artifacts(db, mid)

    assert result is None
    assert not (tmp_path / "learners").exists()


@pytest.mark.asyncio
async def test_ingest_material_auto_writes_markdown_only(tmp_path, monkeypatch):
    from learners_mcp import server

    monkeypatch.setenv("LEARNERS_MCP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LEARNERS_MCP_ARTIFACT_DIR", str(tmp_path / "learners"))
    monkeypatch.delenv("LEARNERS_MCP_ARTIFACT_MIRROR", raising=False)
    monkeypatch.setattr(server, "_db", None)

    result = await server.ingest_material(
        "Chapter 1\n\nAlice follows a rabbit.",
        title="Alice Auto Mirror",
        paste_text=True,
        auto_prepare=False,
    )

    artifact_dir = Path(result["artifact_dir"])
    assert artifact_dir.exists()
    assert (artifact_dir / "README.md").exists()
    assert (artifact_dir / "sections.md").exists()
    assert result["updated_files"]
    assert not list(artifact_dir.rglob("*.json"))


@pytest.mark.asyncio
async def test_phase_and_flashcard_tools_update_markdown_mirror(tmp_path, monkeypatch):
    from learners_mcp import server

    monkeypatch.setenv("LEARNERS_MCP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LEARNERS_MCP_ARTIFACT_DIR", str(tmp_path / "learners"))
    monkeypatch.delenv("LEARNERS_MCP_ARTIFACT_MIRROR", raising=False)
    monkeypatch.setattr(server, "_db", None)

    result = await server.ingest_material(
        "Chapter 1\n\nAlice follows a rabbit.",
        title="Alice Tool Mirror",
        paste_text=True,
        auto_prepare=False,
    )
    artifact_dir = Path(result["artifact_dir"])
    section_id = server._get_db().get_sections(result["material_id"])[0].id

    server.record_phase_response(
        section_id,
        "preview",
        "I expect Wonderland's rules to break.",
    )
    server.add_flashcard(
        section_id,
        "What breaks in Wonderland?",
        "Ordinary rules break. [§1]",
    )

    phase_md = (artifact_dir / "phase-responses.md").read_text(encoding="utf-8")
    flashcards_md = (artifact_dir / "flashcards.md").read_text(encoding="utf-8")
    assert "Wonderland's rules to break" in phase_md
    assert "What breaks in Wonderland?" in flashcards_md
