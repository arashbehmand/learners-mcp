"""Cross-material concept linking.

Extracts `key_concepts` from every other material's stored learning map,
along with the material title and degree of mastery (sections_completed /
sections_total as a rough proxy). The result feeds into the
LEARNING_MAP_USER_TEMPLATE as a "concepts the learner already knows" block
so the map-generation model can reference them instead of re-introducing
them from scratch.
"""

from __future__ import annotations

from typing import Any

from ..db import DB


def gather_known_concepts(
    db: DB, exclude_material_id: int, max_concepts_per_material: int = 5
) -> list[dict[str, Any]]:
    """Return a list of `{material_title, concepts: [{name, gloss}], maturity}`.

    `maturity` is either 'mastered' (all sections complete), 'in_progress', or
    'new' — a coarse hint the model can use to decide whether the learner
    actually knows the concept or just encountered it.
    """
    out: list[dict[str, Any]] = []
    for m in db.list_materials():
        if m.id == exclude_material_id:
            continue
        learning_map = db.get_learning_map(m.id)
        if learning_map is None:
            continue
        key_concepts = (learning_map.map_json or {}).get("key_concepts") or []
        if not key_concepts:
            continue
        # Compute rough maturity.
        sections = db.get_sections(m.id)
        if not sections:
            continue
        completed = sum(1 for s in sections if s.completed_at is not None)
        total = len(sections)
        if completed == total:
            maturity = "mastered"
        elif completed > 0:
            maturity = "in_progress"
        else:
            maturity = "new"

        picked = key_concepts[:max_concepts_per_material]
        out.append(
            {
                "material_id": m.id,
                "material_title": m.title,
                "maturity": maturity,
                "progress": f"{completed}/{total} sections",
                "concepts": [
                    {"name": c.get("name", "?"), "gloss": c.get("why_load_bearing", "")}
                    for c in picked
                ],
            }
        )
    return out


def format_known_concepts_block(known: list[dict[str, Any]]) -> str:
    if not known:
        return ""
    lines = [
        "## Concepts from the learner's other materials",
        "These are concepts the learner has already encountered elsewhere — "
        "reference them by name when relevant rather than re-explaining.",
        "",
    ]
    for m in known:
        lines.append(
            f"### {m['material_title']} ({m['maturity']}, {m['progress']})"
        )
        for c in m["concepts"]:
            lines.append(f"- **{c['name']}**: {c['gloss']}")
        lines.append("")
    return "\n".join(lines)
