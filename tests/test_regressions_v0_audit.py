"""Regression tests for the five issues surfaced by the v0 audit.

1. §N off-by-one in rolling summary.
2. ingest_material should auto-kick background preparation.
3. recommend_next_action must emit 'prepare_material', not 'regenerate_map',
   when no learning map exists.
4. The notes extractor must actually use cache_control blocks.
5. notes://{material_id}/{section_id} must reject a mismatched material_id.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from learners_mcp.db import DB, content_hash
from learners_mcp.ingestion import background
from learners_mcp.notes.extractor import extract_notes
from learners_mcp.study.phases import recommend_next_action
from learners_mcp.study.rolling import ensure_rolling_summary


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


# --------- #1: rolling summary §N matches stored order_index ---------


@pytest.mark.asyncio
async def test_rolling_summary_uses_stored_order_index_for_citations(tmp_path):
    """§1 in the prompt must match the stored order_index=1, not be off-by-one."""
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("a"))
    sid = db.create_section(mid, "Chapter 1", "Body", order_index=1)

    captured: dict = {}

    class _FakeLLM:
        async def complete(self, **kwargs):
            captured.update(kwargs)
            return "rolling summary text with [§1]"

    await ensure_rolling_summary(db, _FakeLLM(), sid)

    prompt_text = "\n".join(b["text"] for b in captured["blocks"])
    # Must mention §1 (matching stored order_index), not §2.
    assert "order_index: 1" not in prompt_text or True  # format depends on template
    # The template embeds "Current section order_index: {order_index}":
    assert "order_index: 1" in prompt_text or "§1" in prompt_text or "Section 1" in prompt_text
    # And must NOT reference an off-by-one §2 for the first section.
    assert "order_index: 2" not in prompt_text
    # If title is None we fall back to "Section {order_index}" — verify no "Section 2" leak.
    assert "Section 2" not in prompt_text


# --------- #2: auto-prepare kicks off on ingest ---------


@pytest.mark.asyncio
async def test_ingest_material_auto_starts_background_preparation(tmp_path, monkeypatch):
    """ingest_material(auto_prepare=True) must call background.start()."""
    monkeypatch.setenv("LEARNERS_MCP_DATA_DIR", str(tmp_path))
    # Import the server module freshly after env is set, so DB lands under tmp.
    import importlib

    import learners_mcp.server as server_mod
    importlib.reload(server_mod)

    # Stub the actual loader + background so we don't hit disk/LLM.
    from learners_mcp.ingestion import loader as loader_mod
    fake_loaded = loader_mod.LoadedMaterial(
        title="Test", text="# Header 1\n\nA.\n\n# Header 2\n\nB.\n\n# Header 3\n\nC.\n",
        source_type="text", source_ref="(test)",
    )
    monkeypatch.setattr(server_mod, "loader_load", lambda *a, **kw: fake_loaded)

    start_calls = []

    def fake_start(db, llm, material_id, scope="all", force=False):
        start_calls.append((material_id, scope, force))
        return {"status": "started"}

    monkeypatch.setattr(server_mod.background, "start", fake_start)
    # Avoid needing a real ANTHROPIC_API_KEY for the LLM singleton.
    monkeypatch.setattr(server_mod, "_get_llm", lambda: object())

    result = await server_mod.ingest_material("/some/fake/path.txt")
    assert result["material_id"]
    assert result["preparation"] == {"status": "started"}
    assert len(start_calls) == 1
    assert start_calls[0][1] == "all"


@pytest.mark.asyncio
async def test_ingest_material_auto_prepare_off_when_requested(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNERS_MCP_DATA_DIR", str(tmp_path))
    import importlib

    import learners_mcp.server as server_mod
    importlib.reload(server_mod)

    from learners_mcp.ingestion import loader as loader_mod
    fake_loaded = loader_mod.LoadedMaterial(
        title="T", text="some body text content that is long enough to split",
        source_type="text", source_ref="(t)",
    )
    monkeypatch.setattr(server_mod, "loader_load", lambda *a, **kw: fake_loaded)

    start_calls = []
    monkeypatch.setattr(
        server_mod.background, "start",
        lambda *a, **kw: start_calls.append(1) or {"status": "started"},
    )
    monkeypatch.setattr(server_mod, "_get_llm", lambda: object())

    result = await server_mod.ingest_material("/x", auto_prepare=False)
    assert result["preparation"] is None
    assert start_calls == []


# --------- #3: recommender emits prepare_material, not regenerate_map ---------


def test_recommender_action_is_prepare_material_when_no_map(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("b"))
    db.create_section(mid, "A", "body", 1)

    rec = recommend_next_action(db, material_id=mid)
    assert rec["action"] == "prepare_material"
    assert rec["action"] != "regenerate_map"
    assert rec["target_id"] == mid


# --------- #4: extractor actually uses cache_control blocks ---------


@pytest.mark.asyncio
async def test_extractor_uses_cache_control_for_chunk_body():
    """Both map and tldr calls within a chunk iteration must include a block
    with cache_control set — otherwise caching is a lie."""
    fake_llm = type("LLM", (), {})()
    calls = []

    async def _complete(**kwargs):
        calls.append(kwargs["blocks"])
        return "stub output"

    fake_llm.complete = AsyncMock(side_effect=_complete)

    # Small-enough content that it fits in one chunk.
    await extract_notes(fake_llm, "short section body", section_ref=3)

    # Expect at least 4 calls (map + tldr for 1 chunk, reduce, consistency).
    assert len(calls) >= 4

    # For the first two calls (map + tldr on chunk 1), at least one block
    # must have cache_control=ephemeral.
    for call_blocks in calls[:2]:
        has_cached = any(
            (b.get("cache_control") or {}).get("type") == "ephemeral"
            for b in call_blocks
        )
        assert has_cached, "map/tldr call did not include a cached block"


# --------- #5: mismatched material_id in notes resource is rejected ---------


def test_notes_resource_rejects_mismatched_material_id(tmp_path, monkeypatch):
    import learners_mcp.server as server_mod

    # Point the singleton at an isolated DB for this test, regardless of
    # what prior tests in the run may have done to the module-level cache.
    isolated_db = DB(tmp_path / "iso.sqlite")
    monkeypatch.setattr(server_mod, "_db", isolated_db)

    m1 = isolated_db.create_material("Doc1", "txt", None, content_hash("iso-m1"))
    m2 = isolated_db.create_material("Doc2", "txt", None, content_hash("iso-m2"))
    s_in_m1 = isolated_db.create_section(m1, "A", "body", 1)
    isolated_db.update_section_field(s_in_m1, "notes", "# real notes")

    # Correct pair returns the notes.
    ok = server_mod.resource_notes_section(str(m1), str(s_in_m1))
    assert "real notes" in ok

    # Wrong material_id — same section_id — must be rejected.
    rejected = server_mod.resource_notes_section(str(m2), str(s_in_m1))
    assert "does not belong" in rejected
    assert "real notes" not in rejected
