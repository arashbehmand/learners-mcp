from __future__ import annotations

from pathlib import Path

import pytest
from mcp.types import CallToolResult, ResourceLink, TextContent

from learners_mcp import server
from learners_mcp.db import DB, content_hash


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


def _seed_material(db: DB) -> tuple[int, int]:
    mid = db.create_material("Compaction Test", "txt", "compaction.txt", content_hash("compact"))
    long_section = ("This is a long source sentence about arguments and fallacies. " * 180).strip()
    sid = db.create_section(mid, "Opening", long_section, 1)
    db.upsert_learning_map(
        mid,
        {
            "difficulty": "beginner",
            "estimated_time": "~2h",
            "objectives": ["Spot basic fallacies", "Explain why a conclusion fails"],
            "key_concepts": [
                {"concept": "argument", "why_it_matters": "core"},
                {"concept": "fallacy", "why_it_matters": "core"},
            ],
            "suggested_path": [{"section": 1, "why": "start here"}],
        },
        "# Learning Map\n\nUnderstand arguments before judging them.\n\nThen inspect common fallacies.",
    )
    db.update_section_field(
        sid,
        "focus_brief",
        {
            "focus": "Separate claims, premises, and conclusions.",
            "estimated_minutes": 12,
            "key_terms": [
                {"term": "argument", "gloss": "premises + conclusion"},
                {"term": "fallacy", "gloss": "bad support"},
            ],
        },
    )
    db.update_section_field(
        sid,
        "notes",
        "# Notes\n\n"
        + ("A concise learner note about arguments and evidence.\n" * 80),
    )
    db.update_section_field(
        sid,
        "rolling_summary",
        "The learner has not yet built prior context; this is the opening section.",
    )
    db.update_phase_data(sid, "preview", {"response": "I expect weak support and hidden assumptions."})
    return mid, sid


def _text(result: CallToolResult) -> str:
    return "\n".join(
        block.text for block in result.content if isinstance(block, TextContent)
    )


def _resource_uris(result: CallToolResult) -> set[str]:
    return {
        str(block.uri) for block in result.content if isinstance(block, ResourceLink)
    }


def test_get_material_map_returns_compact_text_and_resource_link(tmp_path, monkeypatch):
    db = _mk_db(tmp_path)
    mid, _ = _seed_material(db)
    monkeypatch.setattr(server, "_db", db)

    result = server.get_material_map(mid)

    assert isinstance(result, CallToolResult)
    text = _text(result)
    assert "Learning map ready" in text
    assert "Full map is available via the resource link below." in text
    assert len(text) < 1200
    assert f"learning-map://{mid}" in _resource_uris(result)
    assert result.structuredContent is not None
    assert result.structuredContent["markdown"].startswith("# Learning Map")


def test_get_notes_returns_summary_not_full_markdown(tmp_path, monkeypatch):
    db = _mk_db(tmp_path)
    mid, _ = _seed_material(db)
    monkeypatch.setattr(server, "_db", db)

    result = server.get_notes(mid)

    assert isinstance(result, CallToolResult)
    text = _text(result)
    assert "Material notes status" in text
    assert "Full notes are available via the resource link below." in text
    assert len(text) < 1200
    assert "A concise learner note about arguments and evidence." in text
    assert text.count("A concise learner note about arguments and evidence.") < 10
    assert f"notes://{mid}" in _resource_uris(result)
    assert result.structuredContent is not None
    assert result.structuredContent["markdown"].startswith("# §1: Opening")


@pytest.mark.asyncio
async def test_start_section_returns_preview_and_links_instead_of_full_body(
    tmp_path, monkeypatch
):
    db = _mk_db(tmp_path)
    mid, sid = _seed_material(db)
    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(server, "_llm", object())
    monkeypatch.setenv("LEARNERS_MCP_ARTIFACT_DIR", str(tmp_path / "learners"))

    async def fake_rolling_summary(*args, **kwargs):
        return None

    monkeypatch.setattr(server, "ensure_rolling_summary", fake_rolling_summary)

    result = await server.start_section(sid)

    assert isinstance(result, CallToolResult)
    text = _text(result)
    assert "Preferred study order from here: preview -> explain -> question -> anchor." in text
    assert "Section preview:" in text
    assert len(text) < 2500
    assert ("This is a long source sentence about arguments and fallacies. " * 20) not in text
    assert result.structuredContent is not None
    assert result.structuredContent["content"].startswith("This is a long source sentence")
    uris = _resource_uris(result)
    assert f"section://{sid}" in uris
    assert f"section-state://{sid}" in uris
    assert f"focus-brief://{sid}" in uris
    assert f"notes://{mid}/{sid}" in uris
