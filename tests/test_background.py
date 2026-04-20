"""Background preparation — single-flight semantics."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from learners_mcp.db import DB, content_hash
from learners_mcp.ingestion import background


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


@pytest.fixture(autouse=True)
def _reset_tasks():
    background._tasks.clear()
    yield
    background._tasks.clear()


async def _fake_prepare(*args, **kwargs) -> dict:
    await asyncio.sleep(0.02)
    return {"map": "ready", "notes": "ready", "focus_briefs": {1: "ready"}}


@pytest.mark.asyncio
async def test_background_start_then_finish(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("D", "txt", None, content_hash("x"))
    with patch("learners_mcp.ingestion.background.prepare_material", _fake_prepare):
        s1 = background.start(db, llm=None, material_id=mid)  # type: ignore[arg-type]
        assert s1["status"] == "started"
        # Second call while running = no-op.
        s2 = background.start(db, llm=None, material_id=mid)  # type: ignore[arg-type]
        assert s2["status"] == "already_running"
        # Await completion.
        state = background._tasks[mid]
        await state.task
        s3 = background.status(mid)
        assert s3["status"] == "finished"
        assert s3["report"]["map"] == "ready"


@pytest.mark.asyncio
async def test_background_captures_error(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("D", "txt", None, content_hash("y"))

    async def boom(*a, **kw):
        raise RuntimeError("nope")

    with patch("learners_mcp.ingestion.background.prepare_material", boom):
        background.start(db, llm=None, material_id=mid)  # type: ignore[arg-type]
        state = background._tasks[mid]
        await state.task
        s = background.status(mid)
        assert s["status"] == "error"
        assert "RuntimeError: nope" in s["error"]
