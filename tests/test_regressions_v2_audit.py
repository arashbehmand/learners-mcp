"""Regression tests for the three v2 audit findings.

1. LEARNERS_MCP_DATA_DIR is honoured after import (was: frozen at import time).
2. Real flashcard reviews are tracked as events; streak + weekly_report
   reflect them (was: only card creation counted, lifetime proxies used).
3. check_prerequisites flips to review_required when a prerequisite
   section has never been studied (was: silently returned 'ready').
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from learners_mcp.db import DB, content_hash
from learners_mcp.flashcards.service import review_flashcard as svc_review
from learners_mcp.study.prereqs import check_prerequisites
from learners_mcp.study.streak import compute_streak, weekly_report


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


# ---------- #1: env override is honoured after import ----------


def test_config_resolves_data_dir_per_call(monkeypatch, tmp_path):
    """Changing the env after import must change where data_dir() resolves."""
    from learners_mcp.config import data_dir, db_path

    # Default (no override).
    monkeypatch.delenv("LEARNERS_MCP_DATA_DIR", raising=False)
    default = data_dir()
    assert default == Path.home() / ".learners-mcp"

    # Override after import.
    monkeypatch.setenv("LEARNERS_MCP_DATA_DIR", str(tmp_path))
    assert data_dir() == tmp_path
    assert db_path() == tmp_path / "db.sqlite"


def test_server_rebuilds_db_when_env_changes(monkeypatch, tmp_path):
    """The server singleton honours LEARNERS_MCP_DATA_DIR changes between calls."""
    import learners_mcp.server as server_mod

    # Force-clear any stale singleton.
    server_mod._db = None

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    monkeypatch.setenv("LEARNERS_MCP_DATA_DIR", str(first))
    db1 = server_mod._get_db()
    assert db1.path.parent.resolve() == first.resolve()

    monkeypatch.setenv("LEARNERS_MCP_DATA_DIR", str(second))
    db2 = server_mod._get_db()
    assert db2.path.parent.resolve() == second.resolve()
    assert db1 is not db2


# ---------- #2: real flashcard reviews are tracked ----------


def test_review_flashcard_logs_event_and_bumps_streak(tmp_path, monkeypatch):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("r1"))
    sid = db.create_section(mid, "A", "body", 1)
    # Backdate card creation so only today's review can drive streak.
    fid = db.create_flashcard(mid, sid, "Q", "A")
    old = datetime.now(timezone.utc) - timedelta(days=60)
    with db._connect() as conn:
        conn.execute(
            "UPDATE flashcards SET created_at = ? WHERE id = ?",
            (old.isoformat(), fid),
        )

    streak_before = compute_streak(db, today=datetime.now(timezone.utc).date())
    # With only a 60-day-old card and nothing else, today is not active.
    assert streak_before["today_active"] is False

    svc_review(db, fid, knew_it=True)
    events = db.list_review_events(flashcard_id=fid)
    assert len(events) == 1
    assert events[0]["knew_it"] is True

    streak_after = compute_streak(db, today=datetime.now(timezone.utc).date())
    assert streak_after["today_active"] is True
    assert streak_after["current_streak_days"] >= 1


def test_weekly_report_counts_real_reviews_in_window(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("r2"))
    sid = db.create_section(mid, "A", "body", 1)
    fid = db.create_flashcard(mid, sid, "Q", "A")

    # Two reviews this week.
    svc_review(db, fid, knew_it=True)
    svc_review(db, fid, knew_it=False)

    rep = weekly_report(db, today=datetime.now(timezone.utc).date())
    assert rep["totals"]["cards_reviewed"] == 2
    # Per-material bucket exists and reflects the reviews.
    assert rep["per_material"]
    assert rep["per_material"][0]["cards_reviewed"] == 2


def test_apply_review_does_not_log_event(tmp_path):
    """apply_review is pure SM-2 mutation — it must NOT forge a review event."""
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("r3"))
    sid = db.create_section(mid, "A", "body", 1)
    fid = db.create_flashcard(mid, sid, "Q", "A")

    from learners_mcp.flashcards.sm2 import CardState

    db.apply_review(fid, CardState(2.5, 1, 1, datetime.now(timezone.utc), False))
    assert db.list_review_events(flashcard_id=fid) == []


# ---------- #3: prerequisite study-readiness ----------


def test_unstudied_prereq_forces_review_required(tmp_path):
    """If §1 was never studied, §2 cannot be 'ready' — even with no flashcards."""
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("pr1"))
    db.create_section(mid, "A", "body", 1)
    s2 = db.create_section(mid, "B", "body", 2)
    db.upsert_learning_map(
        mid,
        {"key_concepts": [{"name": "Shared", "sections": [1, 2]}]},
        "# m",
    )

    verdict = check_prerequisites(db, s2)
    assert verdict["verdict"] == "review_required"
    assert verdict["unstudied_prerequisites"]
    assert verdict["unstudied_prerequisites"][0]["order_index"] == 1


def test_studied_prereq_with_no_due_cards_is_ready(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("pr2"))
    s1 = db.create_section(mid, "A", "body", 1)
    s2 = db.create_section(mid, "B", "body", 2)
    db.upsert_learning_map(
        mid,
        {"key_concepts": [{"name": "Shared", "sections": [1, 2]}]},
        "# m",
    )
    # Mark §1 as completed — it's now "studied".
    db.update_section_field(s1, "completed_at", datetime.now(timezone.utc))
    verdict = check_prerequisites(db, s2)
    assert verdict["verdict"] == "ready"


def test_phase_response_counts_as_studied(tmp_path):
    """A recorded phase response (without completion) is enough."""
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("pr3"))
    s1 = db.create_section(mid, "A", "body", 1)
    s2 = db.create_section(mid, "B", "body", 2)
    db.upsert_learning_map(
        mid,
        {"key_concepts": [{"name": "Shared", "sections": [1, 2]}]},
        "# m",
    )
    db.update_phase_data(s1, "preview", {"response": "I skimmed this"})
    verdict = check_prerequisites(db, s2)
    # No due cards, §1 is studied → ready.
    assert verdict["verdict"] == "ready"


def test_empty_phase_metadata_does_not_count_as_studied(tmp_path):
    """Bare metadata (no response) does NOT count — opening a phase isn't studying it."""
    db = _mk_db(tmp_path)
    mid = db.create_material("Doc", "txt", None, content_hash("pr4"))
    s1 = db.create_section(mid, "A", "body", 1)
    s2 = db.create_section(mid, "B", "body", 2)
    db.upsert_learning_map(
        mid,
        {"key_concepts": [{"name": "Shared", "sections": [1, 2]}]},
        "# m",
    )
    db.update_phase_data(s1, "preview", {"response": ""})  # empty — doesn't count
    verdict = check_prerequisites(db, s2)
    assert verdict["verdict"] == "review_required"


# ---------- #4: MODEL_* constants must not be imported in source ----------


def test_no_model_constants_imported():
    """No production source file should import MODEL_HAIKU, MODEL_SONNET, or MODEL_OPUS."""
    import ast
    import pathlib

    src_root = pathlib.Path(__file__).parent.parent / "src" / "learners_mcp"
    forbidden = {"MODEL_HAIKU", "MODEL_SONNET", "MODEL_OPUS"}
    violations = []

    for py_file in src_root.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                names = [alias.name for alias in node.names]
                found = forbidden & set(names)
                if found:
                    violations.append(
                        f"{py_file.relative_to(src_root)}: imports {found}"
                    )

    assert not violations, "Forbidden MODEL_* imports found:\n" + "\n".join(violations)


# ---------- #5: llm.complete / complete_json calls must use valid task names ----------


def test_llm_calls_use_valid_tasks():
    """Every llm.complete() / llm.complete_json() call in source must use a task= kwarg
    whose value is a string literal that appears in TASKS."""
    import ast
    import pathlib

    from learners_mcp.llm.profiles import TASKS

    src_root = pathlib.Path(__file__).parent.parent / "src" / "learners_mcp"
    violations = []

    for py_file in src_root.rglob("*.py"):
        # Skip the llm package itself (client.py defines complete, not calls it)
        if "llm" in py_file.parts:
            continue
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match llm.complete(...) or llm.complete_json(...)
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr in ("complete", "complete_json")
            ):
                continue
            # Find the task= keyword argument
            task_kw = next((kw for kw in node.keywords if kw.arg == "task"), None)
            if task_kw is None:
                violations.append(
                    f"{py_file.name}:{node.lineno} — complete() missing task= kwarg"
                )
                continue
            # The value should be a string literal
            if not isinstance(task_kw.value, ast.Constant) or not isinstance(
                task_kw.value.value, str
            ):
                violations.append(
                    f"{py_file.name}:{node.lineno} — task= is not a string literal"
                )
                continue
            task_name = task_kw.value.value
            if task_name not in TASKS:
                violations.append(
                    f"{py_file.name}:{node.lineno} — unknown task {task_name!r} (valid: {sorted(TASKS)})"
                )

    assert not violations, "LLM call site issues:\n" + "\n".join(violations)
