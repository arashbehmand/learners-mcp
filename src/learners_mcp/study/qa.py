"""Mid-study ad-hoc Q&A grounded in the material.

Approach for v0/v1: include all in-scope sections' titles + order_index +
content (preferring extracted notes when present) + any rolling summaries,
then ask the model to cite `[§N]`. No embeddings retrieval yet — the
prompt-caching layer absorbs the cost for materials in the 50-page class
the plan targets.

scope='material' → whole material.
scope='section'  → a single section, specified by section_id. If section_id
                   is missing the call raises ValueError rather than
                   silently widening scope.
"""

from __future__ import annotations

from ..db import DB
from ..llm.client import LLM, cached_source, plain
from ..llm.prompts import USER_INTERACTION_LANGUAGE_POLICY


ANSWER_SYSTEM = (
    "You answer questions from a learner by drawing on the source material "
    "provided below. ALWAYS include `[§N]` citations when you state a specific "
    "claim — N is the section's order_index. If the material doesn't answer "
    "the question, say so; don't invent.\n\n"
    + USER_INTERACTION_LANGUAGE_POLICY
)


async def answer_from_material(
    db: DB,
    llm: LLM,
    material_id: int,
    question: str,
    scope: str = "material",
    section_id: int | None = None,
) -> str:
    if scope not in {"material", "section"}:
        raise ValueError(f"scope must be 'material' or 'section', got {scope!r}")
    if scope == "section" and section_id is None:
        raise ValueError("scope='section' requires a section_id")

    material = db.get_material(material_id)
    if material is None:
        raise KeyError(f"material {material_id} not found")

    sections = db.get_sections(material_id)
    if scope == "section":
        sections = [s for s in sections if s.id == section_id]
        if not sections:
            raise KeyError(f"section {section_id} not in material {material_id}")

    # Rolling summary: for material-scope use the last section's cumulative
    # summary (it's the broadest one). For section-scope use that section's
    # own rolling summary, which encodes everything up to and including it.
    rolling_summary = sections[-1].rolling_summary if sections else None

    # Prefer notes if available (tighter, already distilled); fall back to content.
    parts: list[str] = []
    for s in sections:
        body = s.notes or s.content
        parts.append(f"## §{s.order_index}: {s.title or '(untitled)'}\n\n{body}")
    source_block = "\n\n---\n\n".join(parts)

    context_blocks = cached_source(
        label=f"MATERIAL: {material.title}",
        body=source_block,
    )
    if rolling_summary:
        context_blocks += plain(
            "\n\n## Rolling summary (narrative continuity across prior sections)\n\n"
            + rolling_summary
        )

    user = f"\n\nQuestion: {question}\n\nAnswer from the material, with `[§N]` citations."
    blocks = context_blocks + plain(user)

    return await llm.complete(
        task="qa",
        system=ANSWER_SYSTEM,
        blocks=blocks,
        max_tokens=2048,
        temperature=0.2,
    )
