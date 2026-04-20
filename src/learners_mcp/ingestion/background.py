"""Single-flight background preparation.

One asyncio.Task per material_id. Re-entrant calls while a task is running
are no-ops and return the existing task's state. Errors are captured and
exposed via status(), not raised into the caller.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..db import DB
from ..llm.client import LLM
from .pipeline import prepare_material

log = logging.getLogger(__name__)


@dataclass
class BackgroundState:
    started_at: datetime
    scope: str
    force: bool
    task: Optional[asyncio.Task[Any]] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    last_report: dict[str, Any] = field(default_factory=dict)


_tasks: dict[int, BackgroundState] = {}


def is_running(material_id: int) -> bool:
    state = _tasks.get(material_id)
    return state is not None and state.task is not None and not state.task.done()


async def _run(db: DB, llm: LLM, material_id: int, state: BackgroundState) -> None:
    try:
        state.last_report = await prepare_material(
            db, llm, material_id, scope=state.scope, force=state.force
        )
    except Exception as exc:
        log.exception("background prepare for material %d failed", material_id)
        state.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
    finally:
        state.finished_at = datetime.now(timezone.utc)


def start(
    db: DB, llm: LLM, material_id: int, scope: str = "all", force: bool = False
) -> dict[str, Any]:
    """Start a preparation task if not already running. Returns status dict."""
    existing = _tasks.get(material_id)
    if existing and existing.task and not existing.task.done():
        return {
            "status": "already_running",
            "started_at": existing.started_at.isoformat(),
            "scope": existing.scope,
        }

    state = BackgroundState(
        started_at=datetime.now(timezone.utc),
        scope=scope,
        force=force,
    )
    state.task = asyncio.create_task(_run(db, llm, material_id, state))
    _tasks[material_id] = state

    return {
        "status": "started",
        "started_at": state.started_at.isoformat(),
        "scope": scope,
    }


def status(material_id: int) -> dict[str, Any]:
    state = _tasks.get(material_id)
    if state is None:
        return {"status": "idle"}
    task = state.task
    if task and not task.done():
        return {
            "status": "running",
            "started_at": state.started_at.isoformat(),
            "scope": state.scope,
            "last_report": state.last_report,
        }
    if state.error:
        return {
            "status": "error",
            "started_at": state.started_at.isoformat(),
            "finished_at": state.finished_at.isoformat() if state.finished_at else None,
            "error": state.error,
        }
    return {
        "status": "finished",
        "started_at": state.started_at.isoformat(),
        "finished_at": state.finished_at.isoformat() if state.finished_at else None,
        "report": state.last_report,
    }
