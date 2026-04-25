"""Prerequisite checking before a section.

Approach: read the material's learning map, find every `key_concept` whose
`sections` list touches both an earlier section and *this* section. Those
earlier sections are the prerequisites.

Two kinds of readiness check happen in parallel:

1. **Study readiness** — were the prerequisite sections actually studied?
   A section counts as studied if it's completed OR the learner has
   recorded at least one phase response for it. If a prerequisite was
   never studied, the section isn't ready regardless of flashcard state.

2. **Retention readiness** — are there outstanding (non-mastered, due)
   flashcards on those prerequisites? This was the v2 original check.

Verdict scale:
  - `ready`: prior sections were studied AND no outstanding review cards.
  - `review_recommended`: prior sections have due/overdue cards.
  - `review_required`: prior sections were never studied, OR prior
    sections have many overdue cards AND concept lean on them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import DB


def check_prerequisites(db: DB, section_id: int) -> dict[str, Any]:
    section = db.get_section(section_id)
    if section is None:
        raise KeyError(f"section {section_id} not found")

    learning_map = db.get_learning_map(section.material_id)
    if learning_map is None:
        return {
            "verdict": "ready",
            "reason": "No learning map available — cannot infer prerequisites.",
            "prerequisite_sections": [],
            "review_cards": [],
        }

    key_concepts = learning_map.map_json.get("key_concepts") or []
    prior_indices: set[int] = set()
    concept_hits: list[str] = []
    for kc in key_concepts:
        sections_of_concept = set(kc.get("sections") or [])
        if section.order_index in sections_of_concept:
            # All earlier sections that also carry this concept are prerequisites.
            earlier = {idx for idx in sections_of_concept if idx < section.order_index}
            if earlier:
                prior_indices.update(earlier)
                concept_hits.append(kc.get("name", "?"))

    if not prior_indices:
        return {
            "verdict": "ready",
            "reason": "No key concepts from earlier sections feed into this one.",
            "prerequisite_sections": [],
            "review_cards": [],
        }

    # Resolve prior order_indices → section rows in this material.
    all_sections = {s.order_index: s for s in db.get_sections(section.material_id)}

    # Study-readiness check: which prerequisites were never meaningfully
    # studied (no completion, no phase response recorded)?
    unstudied: list[dict[str, Any]] = []
    prerequisite_sections: list[dict[str, Any]] = []
    for i in sorted(prior_indices):
        if i not in all_sections:
            continue
        ps = all_sections[i]
        entry = {"order_index": ps.order_index, "title": ps.title, "id": ps.id}
        prerequisite_sections.append(entry)
        if not _has_been_studied(ps):
            unstudied.append(entry)

    if unstudied:
        labels = ", ".join(f"§{u['order_index']}" for u in unstudied)
        return {
            "verdict": "review_required",
            "reason": (
                f"Prerequisite section(s) {labels} have not been studied yet. "
                "Go through those first before attempting this section."
            ),
            "prerequisite_sections": prerequisite_sections,
            "unstudied_prerequisites": unstudied,
            "review_cards": [],
            "concepts_linking_back": concept_hits,
        }

    # Retention-readiness check: cards attached to prerequisites that are
    # due + not mastered.
    now = datetime.now(timezone.utc)
    due_cards: list[dict[str, Any]] = []
    overdue_cards = 0
    for ps in prerequisite_sections:
        cards = db.list_flashcards(section_id=ps["id"])
        for c in cards:
            if c.is_mastered:
                continue
            if c.next_review and c.next_review <= now:
                overdue_cards += 1
                due_cards.append(
                    {
                        "flashcard_id": c.id,
                        "section_id": c.section_id,
                        "question": c.question,
                        "answer": c.answer,
                    }
                )

    if not due_cards:
        return {
            "verdict": "ready",
            "reason": (
                f"Prerequisites ({', '.join('§' + str(i) for i in sorted(prior_indices))}) "
                "have been studied and have no outstanding review cards."
            ),
            "prerequisite_sections": prerequisite_sections,
            "review_cards": [],
            "concepts_linking_back": concept_hits,
        }

    verdict = (
        "review_required"
        if overdue_cards >= 5 and concept_hits
        else "review_recommended"
    )
    reason = (
        f"{overdue_cards} cards are due in prerequisite sections "
        f"({', '.join('§' + str(i) for i in sorted(prior_indices))}). "
        f"Concepts that link back: {', '.join(concept_hits) or '—'}."
    )
    return {
        "verdict": verdict,
        "reason": reason,
        "prerequisite_sections": prerequisite_sections,
        "review_cards": due_cards,
        "concepts_linking_back": concept_hits,
    }


def _has_been_studied(section) -> bool:
    """A section counts as studied once it's completed or any phase has a
    recorded response. Bare phase metadata without a response doesn't count —
    the learner may have opened the section and walked away."""
    if section.completed_at is not None:
        return True
    for phase_blob in (section.phase_data or {}).values():
        if not isinstance(phase_blob, dict):
            continue
        if (phase_blob.get("response") or "").strip():
            return True
        if phase_blob.get("completed_at"):
            return True
    return False
