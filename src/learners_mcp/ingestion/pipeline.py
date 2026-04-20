"""Ingestion + preparation pipeline.

Idempotent and resumable: `prepare_material` can be called repeatedly; each
call picks up unfinished artifacts (learning map, focus briefs, per-section
notes) and skips completed ones.

This is intentionally a plain async function — no background workers in v0.
Per plan §9 question 2, the host agent calls `prepare_material` repeatedly
between study actions to progressively unlock content. v1 adds a background
task; v0 runs synchronously per call.
"""

from __future__ import annotations

import logging

from ..db import DB, Material, Section
from ..llm.client import LLM
from ..notes.extractor import extract_notes
from ..orientation.cross_material import (
    format_known_concepts_block,
    gather_known_concepts,
)
from ..orientation.generator import generate_focus_brief, generate_material_map
from .loader import LoadedMaterial
from .splitter import split_into_sections

log = logging.getLogger(__name__)


def ingest(db: DB, loaded: LoadedMaterial, content_hash: str) -> int:
    """Persist material + its sections. Idempotent on content_hash.

    Returns the material_id (existing if dedupe hit).
    """
    existing = db.find_material_by_hash(content_hash)
    if existing:
        log.info("ingest: dedupe hit — reusing material %d", existing.id)
        return existing.id

    material_id = db.create_material(
        title=loaded.title,
        source_type=loaded.source_type,
        source_ref=loaded.source_ref,
        hash_=content_hash,
    )
    sections = split_into_sections(loaded.text)
    for i, (content, title) in enumerate(sections):
        db.create_section(
            material_id=material_id, title=title, content=content, order_index=i + 1
        )
    log.info(
        "ingest: created material %d with %d sections", material_id, len(sections)
    )
    return material_id


async def prepare_material(
    db: DB,
    llm: LLM,
    material_id: int,
    scope: str = "all",
    force: bool = False,
) -> dict:
    """Generate missing artifacts: learning map, focus briefs, notes.

    scope: 'all' | 'map' | 'focus_briefs' | 'notes'. Each call is idempotent:
    artifacts that already exist are skipped unless force=True.

    Returns a readiness dict the caller can surface to the host agent.
    """
    material = db.get_material(material_id)
    if material is None:
        raise KeyError(f"material {material_id} not found")

    sections = db.get_sections(material_id)
    if not sections:
        raise RuntimeError(f"material {material_id} has no sections")

    full_text = _concat_sections(sections)

    report: dict = {
        "map": "pending",
        "focus_briefs": {},
        "notes": "pending",
    }

    # --- Learning map ---
    if scope in ("all", "map"):
        existing_map = db.get_learning_map(material_id)
        if existing_map and not force:
            report["map"] = "ready"
        else:
            log.info("prepare: generating learning map for material %d", material_id)
            section_index = [(s.order_index, s.title) for s in sections]
            known = gather_known_concepts(db, exclude_material_id=material_id)
            known_block = format_known_concepts_block(known)
            payload, md = await generate_material_map(
                llm, full_text, section_index, known_concepts_block=known_block
            )
            db.upsert_learning_map(material_id, payload, md)
            report["map"] = "ready"
    else:
        report["map"] = "ready" if db.get_learning_map(material_id) else "pending"

    # --- Focus briefs (per section) ---
    for s in sections:
        if scope not in ("all", "focus_briefs"):
            report["focus_briefs"][s.order_index] = "ready" if s.focus_brief else "pending"
            continue
        if s.focus_brief and not force:
            report["focus_briefs"][s.order_index] = "ready"
            continue
        log.info("prepare: generating focus brief for §%d", s.order_index)
        brief = await generate_focus_brief(llm, full_text, s.order_index, s.title)
        db.update_section_field(s.id, "focus_brief", brief)
        report["focus_briefs"][s.order_index] = "ready"

    # --- Notes (per section, map-reduce) ---
    notes_states: list[str] = []
    for s in sections:
        current = db.get_section(s.id)  # refresh
        if scope not in ("all", "notes"):
            notes_states.append("ready" if current and current.notes else "pending")
            continue
        if current and current.notes and not force:
            notes_states.append("ready")
            continue
        log.info("prepare: extracting notes for §%d", s.order_index)
        md = await extract_notes(llm, s.content, s.order_index)
        db.update_section_field(s.id, "notes", md)
        notes_states.append("ready")

    if all(st == "ready" for st in notes_states):
        report["notes"] = "ready"
    elif any(st == "ready" for st in notes_states):
        report["notes"] = "partial"
    else:
        report["notes"] = "pending"

    db.set_ingestion_status(material_id, report)
    return report


def preparation_status(db: DB, material_id: int) -> dict:
    """Read-only status — does not kick off new work."""
    material = db.get_material(material_id)
    if material is None:
        raise KeyError(f"material {material_id} not found")
    sections = db.get_sections(material_id)
    focus_state: dict[int, str] = {
        s.order_index: ("ready" if s.focus_brief else "pending") for s in sections
    }
    notes_ready = [s.notes is not None for s in sections]
    if not notes_ready:
        notes = "pending"
    elif all(notes_ready):
        notes = "ready"
    elif any(notes_ready):
        notes = "partial"
    else:
        notes = "pending"
    return {
        "map": "ready" if db.get_learning_map(material_id) else "pending",
        "focus_briefs": focus_state,
        "notes": notes,
    }


def _concat_sections(sections: list[Section]) -> str:
    parts: list[str] = []
    for s in sections:
        header = f"# §{s.order_index}: {s.title}\n\n" if s.title else f"# §{s.order_index}\n\n"
        parts.append(header + s.content)
    return "\n\n".join(parts)
