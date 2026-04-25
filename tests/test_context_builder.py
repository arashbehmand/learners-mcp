"""Context builder tests — ported from PECS-learner/tests/test_context_builder.py
with phase keys updated to the new surface (preview / explain / question / anchor).
"""

from __future__ import annotations

from learners_mcp.study.context import (
    build_learning_context,
    format_context_for_completion,
    format_context_for_flashcards,
)


def test_build_empty():
    ctx = build_learning_context(
        section_content="Python is a programming language.",
        phase_data={},
    )
    assert ctx["content"] == "Python is a programming language."
    assert ctx["preview"] is None
    assert ctx["explain"] is None
    assert ctx["question"] is None
    assert ctx["conversations"] == {}
    assert ctx["flashcards"] == []


def test_build_all_phases():
    phase_data = {
        "preview": {"response": "Python seems easy."},
        "explain": {"response": "High-level, simple syntax."},
        "question": {"response": "How does it compare to Ruby?"},
    }
    ctx = build_learning_context(
        section_content="Python content", phase_data=phase_data
    )
    assert ctx["preview"] == "Python seems easy."
    assert ctx["explain"] == "High-level, simple syntax."
    assert ctx["question"] == "How does it compare to Ruby?"


def test_build_with_flashcards():
    cards = [
        {"question": "What is Python?", "answer": "A language"},
        {"question": "Compiled?", "answer": "Interpreted"},
    ]
    ctx = build_learning_context(
        section_content="Python content", phase_data={}, flashcards=cards
    )
    assert len(ctx["flashcards"]) == 2
    assert ctx["flashcards"][0]["question"] == "What is Python?"


def test_build_conversations_opt_in():
    phase_data = {
        "preview": {
            "response": "thoughts",
            "conversation": [{"role": "user", "content": "hi"}],
        }
    }
    on = build_learning_context(
        section_content="x", phase_data=phase_data, include_conversations=True
    )
    off = build_learning_context(
        section_content="x", phase_data=phase_data, include_conversations=False
    )
    assert "preview" in on["conversations"]
    assert off["conversations"] == {}


def test_format_flashcards_minimal():
    ctx = {
        "content": "c",
        "preview": None,
        "explain": None,
        "question": None,
        "conversations": {},
        "flashcards": [],
    }
    out = format_context_for_flashcards(ctx)
    assert "LEARNING MATERIAL" in out
    assert "EXISTING FLASHCARDS" not in out


def test_format_flashcards_with_existing_cards_emits_duplicate_warning():
    ctx = {
        "content": "c",
        "preview": None,
        "explain": None,
        "question": None,
        "conversations": {},
        "flashcards": [
            {"question": "Q1?", "answer": "A1"},
            {"question": "Q2?", "answer": "A2"},
        ],
    }
    out = format_context_for_flashcards(ctx)
    assert "EXISTING FLASHCARDS (2 cards - DO NOT DUPLICATE)" in out
    assert "1. Q: Q1?" in out
    assert "DIFFERENT flashcards" in out


def test_format_flashcards_truncates_long_content():
    ctx = {
        "content": "x" * 2000,
        "preview": None,
        "explain": None,
        "question": None,
        "conversations": {},
        "flashcards": [],
    }
    out = format_context_for_flashcards(ctx)
    assert "..." in out
    assert len(out) < 2500


def test_format_completion_with_conversations():
    ctx = {
        "content": "content",
        "preview": "initial",
        "explain": "detailed",
        "question": "critical",
        "conversations": {
            "preview": [
                {"role": "user", "content": "what is this?"},
                {"role": "assistant", "content": "it is..."},
            ],
            "question": [{"role": "user", "content": "why?"}],
        },
        "flashcards": [{"question": "q", "answer": "a"}],
        "section_ref": None,
    }
    out = format_context_for_completion(ctx)
    assert "PREVIEW — First impressions — Coaching conversation" in out
    assert "[USER]: what is this?" in out
    assert "[ASSISTANT]: it is..." in out
    assert "QUESTION — Critical thinking & connections — Coaching conversation" in out
    assert "FLASHCARDS CREATED (1 cards)" in out


def test_full_workflow_flashcards():
    phase_data = {
        "preview": {"response": "seems powerful"},
        "explain": {"response": "indentation-based blocks"},
        "question": {"response": "vs ruby?"},
    }
    cards = [{"question": "what is a variable?", "answer": "named storage"}]
    ctx = build_learning_context(
        section_content="Python is a versatile language.",
        phase_data=phase_data,
        flashcards=cards,
    )
    out = format_context_for_flashcards(ctx)
    assert "versatile language" in out
    assert "seems powerful" in out
    assert "indentation-based blocks" in out
    assert "vs ruby?" in out
    assert "EXISTING FLASHCARDS (1 cards" in out
