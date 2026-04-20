"""Server-side phase evaluation — uses a mocked LLM."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from learners_mcp.db import DB, content_hash
from learners_mcp.study.evaluation import evaluate_phase_response


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


@pytest.mark.asyncio
async def test_evaluation_persists_and_returns_markdown(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("e1"))
    sid = db.create_section(mid, "Entropy", "Entropy is a measure of disorder.", 3)
    db.update_phase_data(sid, "explain", {"response": "disorder thingy"})

    fake_llm = type("LLM", (), {})()
    analysis = {
        "strengths": ["Recognized entropy as disorder [§3]"],
        "gaps": [{"concept": "SI units", "evidence": "not mentioned", "sections": [3]}],
        "misconceptions": [],
        "suggested_followups": ["State the formula [§3]"],
        "verdict": "partial",
    }

    async def _complete_json(**kwargs):
        return analysis

    fake_llm.complete_json = AsyncMock(side_effect=_complete_json)

    result = await evaluate_phase_response(db, fake_llm, sid, "explain")
    assert result["section_id"] == sid
    assert result["phase"] == "explain"
    assert result["analysis"]["verdict"] == "partial"
    assert "Verdict" in result["markdown"]
    assert "§3" in result["markdown"]

    stored = db.list_evaluations(sid)
    assert len(stored) == 1
    assert stored[0]["phase"] == "explain"


@pytest.mark.asyncio
async def test_evaluation_requires_some_response(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("e2"))
    sid = db.create_section(mid, "A", "content", 1)

    fake_llm = type("LLM", (), {})()
    fake_llm.complete_json = AsyncMock(return_value={})

    with pytest.raises(ValueError, match="no response available"):
        await evaluate_phase_response(db, fake_llm, sid, "preview")


@pytest.mark.asyncio
async def test_evaluation_rejects_bad_phase(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("e3"))
    sid = db.create_section(mid, "A", "content", 1)
    fake_llm = type("LLM", (), {})()
    with pytest.raises(ValueError, match="phase must be"):
        await evaluate_phase_response(db, fake_llm, sid, "bogus")
