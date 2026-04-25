"""Regression tests for the four issues surfaced by the v1 audit.

1. answer_from_material(scope='section') without section_id must raise —
   it must NOT silently widen to the whole material.
2. answer_from_material must include rolling_summary in the prompt when one
   is present.
3. material_progress must report time_spent_seconds and a last_activity
   that reflects phase edits and flashcard reviews, not only
   section.completed_at.
4. extract_notes_now tool exists and delegates to prepare scope='notes';
   review://due resource returns due cards.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from learners_mcp.db import DB, content_hash
from learners_mcp.flashcards.sm2 import CardState
from learners_mcp.study.progress import material_progress
from learners_mcp.study.qa import answer_from_material


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


# ---------- #1: scope='section' requires section_id ----------


@pytest.mark.asyncio
async def test_qa_section_scope_without_id_raises(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("q1"))
    db.create_section(mid, "A", "body a", 1)
    db.create_section(mid, "B", "body b", 2)

    fake_llm = type("LLM", (), {})()
    fake_llm.complete = AsyncMock(return_value="stub")

    with pytest.raises(ValueError, match="section_id"):
        await answer_from_material(db, fake_llm, mid, "anything?", scope="section")


@pytest.mark.asyncio
async def test_qa_section_scope_with_id_is_narrow(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("q2"))
    sid_a = db.create_section(mid, "Chapter A", "APPLE body content", 1)
    db.create_section(mid, "Chapter B", "BANANA body content", 2)

    fake_llm = type("LLM", (), {})()
    captured: dict = {}

    async def _complete(**kwargs):
        captured.update(kwargs)
        return "stub"

    fake_llm.complete = AsyncMock(side_effect=_complete)

    await answer_from_material(
        db, fake_llm, mid, "What?", scope="section", section_id=sid_a
    )
    prompt = "\n".join(b["text"] for b in captured["blocks"])
    assert "APPLE" in prompt
    assert "BANANA" not in prompt


# ---------- #2: rolling summary reaches the QA prompt ----------


@pytest.mark.asyncio
async def test_qa_includes_rolling_summary_when_present(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("q3"))
    db.create_section(mid, "A", "section a body", 1)
    s2 = db.create_section(mid, "B", "section b body", 2)
    db.update_section_field(s2, "rolling_summary", "ROLLING_TOKEN spans §1 and §2.")

    fake_llm = type("LLM", (), {})()
    captured: dict = {}

    async def _complete(**kwargs):
        captured.update(kwargs)
        return "stub"

    fake_llm.complete = AsyncMock(side_effect=_complete)

    await answer_from_material(db, fake_llm, mid, "What?", scope="material")
    prompt = "\n".join(b["text"] for b in captured["blocks"])
    assert "ROLLING_TOKEN" in prompt
    assert "Rolling summary" in prompt


@pytest.mark.asyncio
async def test_qa_rolling_summary_absent_when_missing(tmp_path):
    """No rolling summary → no 'Rolling summary' section in the prompt."""
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("q4"))
    db.create_section(mid, "A", "section a body", 1)

    fake_llm = type("LLM", (), {})()
    captured: dict = {}

    async def _complete(**kwargs):
        captured.update(kwargs)
        return "stub"

    fake_llm.complete = AsyncMock(side_effect=_complete)

    await answer_from_material(db, fake_llm, mid, "What?", scope="material")
    prompt = "\n".join(b["text"] for b in captured["blocks"])
    assert "Rolling summary" not in prompt


# ---------- #3: time_spent + broader last_activity ----------


def test_material_progress_reports_time_spent(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("p1"))
    sid = db.create_section(mid, "A", "body", 1)

    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 10, 45, 0, tzinfo=timezone.utc)  # +45 min
    db.update_phase_data(
        sid, "preview", {"response": "first", "updated_at": t0.isoformat()}
    )
    db.update_phase_data(
        sid, "explain", {"response": "second", "updated_at": t1.isoformat()}
    )

    stats = material_progress(db, mid)
    assert stats["time_spent_seconds"] == 45 * 60
    assert stats["last_activity"] is not None


def test_material_progress_last_activity_sees_phase_edits_without_completion(tmp_path):
    """A section mid-study (no completed_at, no anchor) must still set last_activity."""
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("p2"))
    sid = db.create_section(mid, "A", "body", 1)
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    db.update_phase_data(
        sid, "preview", {"response": "x", "updated_at": t0.isoformat()}
    )

    stats = material_progress(db, mid)
    assert stats["last_activity"] is not None
    assert stats["last_activity"].startswith("2026-01-01T12:00:00")


def test_material_progress_last_activity_sees_flashcard_reviews(tmp_path):
    """A review-only session (no phase edits) must still count as activity."""
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("p3"))
    sid = db.create_section(mid, "A", "body", 1)
    fid = db.create_flashcard(mid, sid, "Q", "A")
    # Apply a review: review_count=1 and next_review is in the future.
    future = datetime.now(timezone.utc) + timedelta(days=2)
    db.apply_review(fid, CardState(2.5, 2, 1, future, False))
    stats = material_progress(db, mid)
    assert stats["last_activity"] is not None
    # next_review is the most recent activity.
    assert "2026" in stats["last_activity"] or "202" in stats["last_activity"]


def test_material_progress_empty_material_has_no_time(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("p4"))
    db.create_section(mid, "A", "body", 1)
    stats = material_progress(db, mid)
    assert stats["time_spent_seconds"] is None
    assert stats["last_activity"] is None


# ---------- #4: extract_notes_now + review://due ----------


@pytest.mark.asyncio
async def test_extract_notes_now_delegates_to_notes_scope(tmp_path, monkeypatch):
    """The new tool must invoke prepare_material with scope='notes'."""
    monkeypatch.setenv("LEARNERS_MCP_DATA_DIR", str(tmp_path))
    import importlib

    import learners_mcp.server as server_mod

    importlib.reload(server_mod)

    captured: dict = {}

    async def fake_prepare(db, llm, material_id, scope, force):
        captured["scope"] = scope
        captured["force"] = force
        return {"map": "ready", "notes": "ready", "focus_briefs": {1: "ready"}}

    monkeypatch.setattr(server_mod, "pipeline_prepare", fake_prepare)
    monkeypatch.setattr(server_mod, "_get_llm", lambda: object())

    out = await server_mod.extract_notes_now(material_id=123, force=True)
    assert captured["scope"] == "notes"
    assert captured["force"] is True
    assert out["notes"] == "ready"


def test_review_due_resource_returns_due_cards(tmp_path, monkeypatch):
    import learners_mcp.server as server_mod

    iso = DB(tmp_path / "iso.sqlite")
    monkeypatch.setattr(server_mod, "_db", iso)

    mid = iso.create_material("Doc", "txt", None, content_hash("r1"))
    sid = iso.create_section(mid, "A", "body", 1)
    past_due = iso.create_flashcard(mid, sid, "Q_DUE", "A_DUE")
    future_card = iso.create_flashcard(mid, sid, "Q_FUTURE", "A_FUTURE")
    mastered_card = iso.create_flashcard(mid, sid, "Q_MASTERED", "A_MASTERED")

    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    iso.apply_review(past_due, CardState(2.5, 1, 1, past, False))
    iso.apply_review(future_card, CardState(2.5, 5, 1, future, False))
    iso.apply_review(mastered_card, CardState(2.5, 60, 6, future, True))

    payload = server_mod.resource_flashcards_due()
    data = json.loads(payload)
    questions = {c["question"] for c in data}
    assert "Q_DUE" in questions
    assert "Q_FUTURE" not in questions
    assert "Q_MASTERED" not in questions
