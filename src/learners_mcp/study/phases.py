"""Phase state machine — soft guidance, not enforcement.

The learner moves through Preview → Explain → Question → Anchor. We track
`current_phase` on each section but never hard-block out-of-order calls.
Instead, tools return a `warning` field when the learner skips ahead, so
the host agent can surface the advice without fighting them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db import DB


PHASES = ("preview", "explain", "question", "anchor")


def next_phase(phase: str) -> str | None:
    try:
        i = PHASES.index(phase)
    except ValueError:
        return None
    return PHASES[i + 1] if i + 1 < len(PHASES) else None


def phase_completed(section, phase: str) -> bool:
    """A phase is completed if its `completed_at` field is present."""
    data = (section.phase_data or {}).get(phase, {}) or {}
    return bool(data.get("completed_at"))


def resolved_current_phase(section) -> str:
    """Lowest phase that hasn't been completed yet."""
    for p in PHASES:
        if not phase_completed(section, p):
            return p
    return "anchor"  # fully complete — stay on anchor


@dataclass
class PhaseValidation:
    ok: bool
    warning: str | None


def validate_phase_action(section, phase: str) -> PhaseValidation:
    """Check whether operating on `phase` is in sequence.

    Returns ok=True always (soft guidance), but populates `warning` when the
    learner is acting out of order.
    """
    expected = resolved_current_phase(section)
    if phase == expected:
        return PhaseValidation(ok=True, warning=None)

    exp_i = PHASES.index(expected) if expected in PHASES else -1
    req_i = PHASES.index(phase) if phase in PHASES else -1

    if req_i < exp_i:
        # Going back — always allowed, no warning.
        return PhaseValidation(ok=True, warning=None)

    # Going forward past an incomplete phase.
    skipped = ", ".join(PHASES[exp_i:req_i])
    msg = (
        f"You're about to act on '{phase}' but '{skipped}' hasn't been completed. "
        f"The loop works best in order (Preview → Explain → Question → Anchor). "
        f"Proceeding anyway — but consider finishing '{expected}' first for better retention."
    )
    return PhaseValidation(ok=True, warning=msg)


def recommend_next_action(db: DB, material_id: int | None = None) -> dict:
    """Pick what the learner should probably do next.

    Priority order:
      1. Due-card review if any are overdue.
      2. Continue the last in-progress section.
      3. Start the next unstarted section.
      4. Regenerate map if no material map exists yet.
      5. Rest / nothing to do.
    """
    if material_id is None:
        due = db.list_flashcards(filter_="due")
        if due:
            return {
                "action": "review_due_cards",
                "target_id": None,
                "reason": f"{len(due)} flashcards due across your library.",
            }
        materials = db.list_materials()
        if not materials:
            return {
                "action": "ingest_material",
                "target_id": None,
                "reason": "No materials ingested yet. Add one to get started.",
            }
        material_id = materials[0].id  # most recent

    due = db.list_flashcards(material_id=material_id, filter_="due")
    if due:
        return {
            "action": "review_due_cards",
            "target_id": material_id,
            "reason": f"{len(due)} cards due in this material.",
        }

    if db.get_learning_map(material_id) is None:
        return {
            "action": "prepare_material",
            "target_id": material_id,
            "reason": "Learning map not yet generated — call prepare_material(material_id).",
        }

    sections = db.get_sections(material_id)
    for s in sections:
        if s.completed_at is None:
            # First incomplete section — either in-progress or not yet started.
            if any(phase_completed(s, p) for p in PHASES):
                return {
                    "action": "continue_section",
                    "target_id": s.id,
                    "reason": f"§{s.order_index} is in progress on phase '{resolved_current_phase(s)}'.",
                }
            return {
                "action": "start_section",
                "target_id": s.id,
                "reason": f"§{s.order_index} is next in the suggested path.",
            }

    return {
        "action": "rest",
        "target_id": material_id,
        "reason": "All sections complete. Consider a review session or a new material.",
    }
