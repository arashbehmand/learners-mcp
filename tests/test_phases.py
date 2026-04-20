"""Phase state machine — soft guidance checks."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from learners_mcp.study.phases import (
    PHASES,
    next_phase,
    phase_completed,
    resolved_current_phase,
    validate_phase_action,
)


def _section(phase_data: dict) -> SimpleNamespace:
    return SimpleNamespace(phase_data=phase_data)


def test_next_phase_sequence():
    assert next_phase("preview") == "explain"
    assert next_phase("explain") == "question"
    assert next_phase("question") == "anchor"
    assert next_phase("anchor") is None


def test_phase_completed_flag():
    s = _section({"preview": {"completed_at": "2026-01-01T00:00:00+00:00"}})
    assert phase_completed(s, "preview") is True
    assert phase_completed(s, "explain") is False


def test_resolved_current_phase_returns_lowest_incomplete():
    s = _section({"preview": {"completed_at": "x"}, "explain": {"completed_at": "y"}})
    assert resolved_current_phase(s) == "question"


def test_resolved_current_phase_when_all_done():
    done = {p: {"completed_at": "x"} for p in PHASES}
    assert resolved_current_phase(_section(done)) == "anchor"


def test_validate_in_order_no_warning():
    s = _section({})
    v = validate_phase_action(s, "preview")
    assert v.ok is True
    assert v.warning is None


def test_validate_going_back_no_warning():
    s = _section({"preview": {"completed_at": "x"}, "explain": {"completed_at": "y"}})
    # current = question; learner wants to revise preview
    v = validate_phase_action(s, "preview")
    assert v.ok is True
    assert v.warning is None


def test_validate_skipping_ahead_emits_warning_but_ok():
    s = _section({})
    v = validate_phase_action(s, "anchor")  # skips preview/explain/question
    assert v.ok is True
    assert v.warning is not None
    assert "preview" in v.warning
