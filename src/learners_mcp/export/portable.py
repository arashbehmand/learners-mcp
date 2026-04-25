"""Portable project JSON export/import.

A single JSON file that captures everything the server knows about one
material: metadata, sections (with phase_data/rolling_summary/notes/
focus_briefs/completed state), the learning map, flashcards with their SM-2
state, completion reports, and evaluations.

Use cases: back up a library, move between machines, share a project with
another learner, archive a completed course before deleting state.

import_project() creates a fresh material_id. If a material with the same
content_hash already exists in the target DB, import raises — the learner
must delete the existing one first to avoid ambiguity.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import DB

EXPORT_VERSION = 2


def export_project(db: DB, material_id: int, output_path: Path) -> dict[str, Any]:
    material = db.get_material(material_id)
    if material is None:
        raise KeyError(f"material {material_id} not found")
    sections = db.get_sections(material_id)
    learning_map = db.get_learning_map(material_id)
    cards = db.list_flashcards(material_id=material_id)

    # Attach review events per card so streak/weekly history survives a
    # round-trip through export → import.
    flashcards_dicts: list[dict[str, Any]] = []
    for c in cards:
        d = _flashcard_to_dict(c)
        events = db.list_review_events(flashcard_id=c.id)
        if events:
            d["review_events"] = [
                {
                    "reviewed_at": _iso(e["reviewed_at"]),
                    "knew_it": e["knew_it"],
                }
                for e in events
            ]
        flashcards_dicts.append(d)

    export: dict[str, Any] = {
        "schema_version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "material": _material_to_dict(material),
        "sections": [],
        "learning_map": None,
        "flashcards": flashcards_dicts,
    }

    if learning_map is not None:
        export["learning_map"] = {
            "map_json": learning_map.map_json,
            "map_markdown": learning_map.map_markdown,
            "generated_at": _iso(learning_map.generated_at),
            "regeneration_count": learning_map.regeneration_count,
        }

    for s in sections:
        section_dict = _section_to_dict(s)
        report = db.get_completion_report(s.id)
        if report is not None:
            section_dict["completion_report"] = {
                "markdown": report[0],
                "generated_at": _iso(report[1]),
            }
        evals = db.list_evaluations(s.id)
        if evals:
            # Drop the local id — the re-imported rows will get fresh ones.
            section_dict["evaluations"] = [
                {k: v for k, v in e.items() if k != "id"} for e in evals
            ]
        export["sections"].append(section_dict)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(export, indent=2), encoding="utf-8")
    return {
        "path": str(output_path.resolve()),
        "schema_version": EXPORT_VERSION,
        "sections": len(sections),
        "flashcards": len(cards),
    }


def import_project(db: DB, input_path: Path) -> dict[str, Any]:
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"import file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))

    version = payload.get("schema_version")
    # Schema v1 exports (pre-review_events) remain importable — they just
    # won't carry review history. Reject anything newer or older than known.
    if version not in (1, 2):
        raise ValueError(
            f"unsupported schema_version {version!r} (this build supports 1 and 2)"
        )

    material = payload["material"]
    hash_ = material["content_hash"]
    existing = db.find_material_by_hash(hash_)
    if existing is not None:
        raise ValueError(
            f"material with content_hash {hash_} already exists (id={existing.id}). "
            "Delete it first, or import into a fresh DB."
        )

    new_material_id = db.create_material(
        title=material["title"],
        source_type=material.get("source_type"),
        source_ref=material.get("source_ref"),
        hash_=hash_,
    )
    db.set_ingestion_status(new_material_id, material.get("ingestion_status") or {})

    # Map old section ids → new section ids so we can rewire flashcards.
    section_id_map: dict[int, int] = {}

    for s_dict in payload.get("sections", []):
        new_sid = db.create_section(
            material_id=new_material_id,
            title=s_dict.get("title"),
            content=s_dict["content"],
            order_index=s_dict["order_index"],
        )
        section_id_map[s_dict["id"]] = new_sid

        # Restore optional state.
        if s_dict.get("rolling_summary"):
            db.update_section_field(
                new_sid, "rolling_summary", s_dict["rolling_summary"]
            )
        if s_dict.get("notes"):
            db.update_section_field(new_sid, "notes", s_dict["notes"])
        if s_dict.get("focus_brief"):
            db.update_section_field(new_sid, "focus_brief", s_dict["focus_brief"])
        if s_dict.get("current_phase"):
            db.update_section_field(new_sid, "current_phase", s_dict["current_phase"])
        for phase_name, phase_blob in (s_dict.get("phase_data") or {}).items():
            db.update_phase_data(new_sid, phase_name, phase_blob)
        if s_dict.get("completed_at"):
            dt = datetime.fromisoformat(s_dict["completed_at"])
            db.update_section_field(new_sid, "completed_at", dt)
        if s_dict.get("completion_report"):
            db.upsert_completion_report(
                new_sid, s_dict["completion_report"]["markdown"]
            )
        for ev in s_dict.get("evaluations") or []:
            db.add_evaluation(
                section_id=new_sid,
                phase=ev["phase"],
                response=ev["response"],
                analysis_json=ev["analysis"],
                analysis_markdown=ev["markdown"],
            )

    learning_map = payload.get("learning_map")
    if learning_map:
        db.upsert_learning_map(
            new_material_id,
            learning_map["map_json"],
            learning_map["map_markdown"],
        )

    from ..flashcards.sm2 import CardState

    for card in payload.get("flashcards") or []:
        new_sid = section_id_map.get(card.get("section_id"))
        fid = db.create_flashcard(
            material_id=new_material_id,
            section_id=new_sid,
            question=card["question"],
            answer=card["answer"],
        )
        state = CardState(
            ease_factor=card["ease_factor"],
            interval_days=card["interval_days"],
            review_count=card["review_count"],
            next_review=datetime.fromisoformat(card["next_review"]),
            is_mastered=bool(card["is_mastered"]),
        )
        db.apply_review(fid, state)
        # Replay review events (v2+ exports) so streak / weekly history
        # round-trips. For each event we use record_review but pass the
        # *stored* state back in — we're recording history, not advancing
        # scheduling.
        for ev in card.get("review_events") or []:
            db.record_review(
                fid,
                new_state=state,
                knew_it=bool(ev.get("knew_it")),
                reviewed_at=datetime.fromisoformat(ev["reviewed_at"]),
            )

    return {
        "material_id": new_material_id,
        "sections_imported": len(section_id_map),
        "flashcards_imported": len(payload.get("flashcards") or []),
    }


def _material_to_dict(m) -> dict[str, Any]:
    return {
        "id": m.id,
        "title": m.title,
        "source_type": m.source_type,
        "source_ref": m.source_ref,
        "content_hash": m.content_hash,
        "created_at": _iso(m.created_at),
        "ingestion_status": m.ingestion_status,
    }


def _section_to_dict(s) -> dict[str, Any]:
    return {
        "id": s.id,
        "title": s.title,
        "content": s.content,
        "order_index": s.order_index,
        "rolling_summary": s.rolling_summary,
        "current_phase": s.current_phase,
        "phase_data": s.phase_data,
        "notes": s.notes,
        "focus_brief": s.focus_brief,
        "completed_at": _iso(s.completed_at) if s.completed_at else None,
    }


def _flashcard_to_dict(c) -> dict[str, Any]:
    return {
        "section_id": c.section_id,
        "question": c.question,
        "answer": c.answer,
        "ease_factor": c.ease_factor,
        "interval_days": c.interval_days,
        "review_count": c.review_count,
        "next_review": _iso(c.next_review),
        "is_mastered": c.is_mastered,
        "created_at": _iso(c.created_at),
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
