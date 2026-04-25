"""DRY context engineering for AI features.

Ported from PECS-learner/utils/context_builder.py. Phase keys are renamed to
the new surface: preview / explain / question / anchor. The committed-cards-only
semantics for flashcards are preserved — the biggest bug this module prevents
is duplicate flashcard generation caused by ephemeral suggestions leaking into
the context. Only DB-committed cards go in.
"""

from __future__ import annotations

from typing import Any

PHASES = ("preview", "explain", "question", "anchor")


def build_learning_context(
    section_content: str,
    phase_data: dict[str, Any],
    flashcards: list[dict[str, str]] | None = None,
    include_conversations: bool = False,
    rolling_summary: str | None = None,
    section_ref: str | None = None,
) -> dict[str, Any]:
    """Gather all relevant AI context from a section's state.

    Args:
        section_content: The source section text.
        phase_data: JSON blob keyed by phase name (preview/explain/question/anchor),
            each value is `{response, conversation, completed_at}` or subset.
        flashcards: Committed DB flashcards. NEVER pass ephemeral suggestions.
        include_conversations: Include the phase chat transcripts (expensive).
        rolling_summary: Cumulative summary of prior sections, if any.
        section_ref: Citation tag like "§3: Maximum Entropy" for grounding.
    """
    context: dict[str, Any] = {
        "content": section_content,
        "section_ref": section_ref,
        "preview": None,
        "explain": None,
        "question": None,
        "conversations": {},
        "flashcards": list(flashcards or []),
        "rolling_summary": rolling_summary,
    }

    for phase in ("preview", "explain", "question"):
        pd = phase_data.get(phase, {}) or {}
        context[phase] = pd.get("response")
        if include_conversations:
            convo = pd.get("conversation") or []
            if convo:
                context["conversations"][phase] = convo

    return context


def _divider(label: str) -> list[str]:
    return ["=" * 60, label, "=" * 60]


def _truncate(text: str, limit: int) -> str:
    return text[:limit] + ("..." if len(text) > limit else "")


def format_context_for_flashcards(context: dict[str, Any]) -> str:
    """Format context for flashcard generation with duplicate-prevention warnings."""
    parts: list[str] = []

    ref = context.get("section_ref")
    parts.extend(_divider(f"LEARNING MATERIAL {ref}" if ref else "LEARNING MATERIAL"))
    parts.append(_truncate(context["content"], 1500))
    parts.append("")

    labels = {
        "preview": "STUDENT'S INITIAL IMPRESSIONS (Preview phase)",
        "explain": "STUDENT'S EXPLANATION IN OWN WORDS (Explain phase)",
        "question": "STUDENT'S QUESTIONS & CONNECTIONS (Question phase)",
    }
    for phase, label in labels.items():
        if context.get(phase):
            parts.extend(_divider(label))
            parts.append(context[phase])
            parts.append("")

    cards = context.get("flashcards") or []
    if cards:
        parts.extend(
            _divider(f"EXISTING FLASHCARDS ({len(cards)} cards - DO NOT DUPLICATE)")
        )
        for i, card in enumerate(cards, 1):
            parts.append(f"\n{i}. Q: {card['question']}")
            parts.append(f"   A: {card['answer']}")
        parts.append("")
        parts.append(
            "Generate DIFFERENT flashcards covering new aspects not in the list above."
        )
        parts.append(
            "Each flashcard Q/A should reference the source section in its answer, e.g. '[§N]'."
        )
        parts.append("")

    return "\n".join(parts)


def format_context_for_completion(context: dict[str, Any]) -> str:
    """Format context for a section completion report, including conversations."""
    parts: list[str] = []

    parts.extend(_divider("LEARNING MATERIAL"))
    parts.append(_truncate(context["content"], 2000))
    parts.append("")

    phase_labels = {
        "preview": "PREVIEW — First impressions",
        "explain": "EXPLAIN — In own words (Feynman)",
        "question": "QUESTION — Critical thinking & connections",
    }

    for phase, label in phase_labels.items():
        convo = context.get("conversations", {}).get(phase) or []
        if convo:
            parts.extend(_divider(f"{label} — Coaching conversation"))
            for msg in convo:
                role = (msg.get("role") or "unknown").upper()
                parts.append(f"[{role}]: {msg.get('content', '')}")
            parts.append("")
        if context.get(phase):
            parts.extend(_divider(f"{label} — Recorded response"))
            parts.append(context[phase])
            parts.append("")

    cards = context.get("flashcards") or []
    if cards:
        parts.extend(_divider(f"FLASHCARDS CREATED ({len(cards)} cards)"))
        for i, card in enumerate(cards, 1):
            parts.append(f"{i}. Q: {card['question']}")
            parts.append(f"   A: {card['answer']}")
        parts.append("")

    return "\n".join(parts)


def format_context_with_rolling_summary(context: dict[str, Any]) -> str:
    """Format context with a prepended rolling summary from prior sections."""
    parts: list[str] = []

    if context.get("rolling_summary"):
        parts.extend(_divider("PREVIOUS SECTIONS — Rolling summary"))
        parts.append(context["rolling_summary"])
        parts.append("")
        parts.append("The current section builds upon this foundation.")
        parts.append("")

    ref = context.get("section_ref")
    parts.extend(_divider(f"CURRENT SECTION {ref}" if ref else "CURRENT SECTION"))
    parts.append(_truncate(context["content"], 2000))
    parts.append("")

    return "\n".join(parts)
