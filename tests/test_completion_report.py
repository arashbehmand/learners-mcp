"""Completion report generation — uses a mocked LLM to verify the
context is built correctly and the report is persisted."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from learners_mcp.db import DB, content_hash
from learners_mcp.study.completion import generate_completion_report


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


@pytest.mark.asyncio
async def test_completion_report_uses_full_journey_and_persists(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("z"))
    sid = db.create_section(mid, "Chapter 1", "Python is versatile.", 1)
    db.update_phase_data(sid, "preview", {"response": "seems easy"})
    db.update_phase_data(sid, "explain", {"response": "high-level, indentation-based"})
    db.update_phase_data(sid, "question", {"response": "how does GIL work?"})
    db.create_flashcard(mid, sid, "What is Python?", "A language [§1]")

    fake_llm = type("LLM", (), {})()
    captured: dict = {}

    async def _complete(**kwargs):
        captured.update(kwargs)
        return "# Nice work\n\nYou locked in Python basics. [§1]"

    fake_llm.complete = AsyncMock(side_effect=_complete)

    md = await generate_completion_report(db, fake_llm, sid)
    assert "Nice work" in md

    # Verify the LLM saw the phase responses + flashcards in the context.
    blocks = captured["blocks"]
    payload = "\n".join(b["text"] for b in blocks)
    assert "seems easy" in payload
    assert "high-level, indentation-based" in payload
    assert "how does GIL work?" in payload
    assert "FLASHCARDS CREATED" in payload

    # Persisted in the DB.
    stored = db.get_completion_report(sid)
    assert stored is not None
    assert stored[0].startswith("# Nice work")
