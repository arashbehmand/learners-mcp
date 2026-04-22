"""Learner-readable artifact mirror exports.

SQLite remains canonical. This module renders the current DB state for one
material into a workspace folder so nontechnical learners can open generated
study artifacts directly.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import artifact_dir, artifact_mirror_enabled
from ..db import DB
from ..orientation.render import render_focus_brief_markdown
from ..study.progress import material_progress

MARKDOWN_FILES = (
    "README.md",
    "sections.md",
    "learning-map.md",
    "focus-briefs.md",
    "notes.md",
    "progress.md",
    "phase-responses.md",
    "rolling-summaries.md",
    "flashcards.md",
    "study-plan.md",
    "completion-reports.md",
    "evaluations.md",
)

JSON_FILES = (
    "manifest.json",
    "learning-map.json",
    "focus-briefs.json",
    "sections.json",
    "progress.json",
    "phase-responses.json",
    "flashcards.json",
    "study-plan.json",
    "completion-reports.json",
    "evaluations.json",
)


def auto_export_markdown_artifacts(
    db: DB,
    material_id: int,
    *,
    study_plan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Write Markdown artifacts unless the mirror is disabled."""
    if not artifact_mirror_enabled():
        return None
    return export_material_artifacts(
        db,
        material_id,
        format="markdown",
        study_plan=study_plan,
    )


def export_material_artifacts(
    db: DB,
    material_id: int,
    output_dir: str | Path | None = None,
    format: str = "markdown",
    *,
    study_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if format not in {"markdown", "json", "all"}:
        raise ValueError("format must be 'markdown', 'json', or 'all'")

    material = db.get_material(material_id)
    if material is None:
        raise KeyError(f"material {material_id} not found")

    root = Path(output_dir).expanduser() if output_dir is not None else artifact_dir()
    target = material_artifact_dir(db, material_id, root)
    target.mkdir(parents=True, exist_ok=True)

    updated: list[Path] = []
    if format in {"markdown", "all"}:
        updated.extend(_write_markdown_artifacts(db, material_id, target, study_plan))
    if format in {"json", "all"}:
        updated.extend(_write_json_artifacts(db, material_id, target / "json", study_plan))

    return {
        "ok": True,
        "format": format,
        "artifact_dir": str(target.resolve()),
        "updated_files": [str(p.resolve()) for p in updated],
    }


def material_artifact_dir(db: DB, material_id: int, root: Path | None = None) -> Path:
    material = db.get_material(material_id)
    if material is None:
        raise KeyError(f"material {material_id} not found")
    base = slugify(material.title) or f"material-{material_id}"
    same_slug = [
        m.id for m in sorted(db.list_materials(), key=lambda item: item.id)
        if (slugify(m.title) or f"material-{m.id}") == base
    ]
    if same_slug and same_slug[0] != material_id:
        base = f"{base}-material-{material_id}"
    return (root or artifact_dir()) / base


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    normalized = normalized.replace("'", "").replace("’", "")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:80].strip("-")


def _write_markdown_artifacts(
    db: DB,
    material_id: int,
    target: Path,
    study_plan: dict[str, Any] | None,
) -> list[Path]:
    renderers = {
        "README.md": _render_readme,
        "sections.md": _render_sections,
        "learning-map.md": _render_learning_map,
        "focus-briefs.md": _render_focus_briefs,
        "notes.md": _render_notes,
        "progress.md": _render_progress,
        "phase-responses.md": _render_phase_responses,
        "rolling-summaries.md": _render_rolling_summaries,
        "flashcards.md": _render_flashcards,
        "study-plan.md": lambda db, mid: _render_study_plan(study_plan),
        "completion-reports.md": _render_completion_reports,
        "evaluations.md": _render_evaluations,
    }
    out: list[Path] = []
    for filename in MARKDOWN_FILES:
        path = target / filename
        _atomic_write_text(path, renderers[filename](db, material_id))
        out.append(path)
    return out


def _write_json_artifacts(
    db: DB,
    material_id: int,
    target: Path,
    study_plan: dict[str, Any] | None,
) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    payloads = _json_payloads(db, material_id, study_plan)
    out: list[Path] = []
    for filename in JSON_FILES:
        path = target / filename
        _atomic_write_text(path, json.dumps(payloads[filename], indent=2), suffix=".tmp")
        out.append(path)
    return out


def _render_readme(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    sections = db.get_sections(material_id)
    return "\n".join(
        [
            f"# {material.title}",
            "",
            "Generated study artifacts for this material.",
            "",
            f"- Material ID: {material.id}",
            f"- Source type: {material.source_type or 'unknown'}",
            f"- Source: {material.source_ref or '(pasted)'}",
            f"- Sections: {len(sections)}",
            "",
            "## Files",
            "",
            *[f"- `{name}`" for name in MARKDOWN_FILES if name != "README.md"],
            "",
        ]
    )


def _render_sections(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    lines = [f"# Sections — {material.title}", ""]
    for s in db.get_sections(material_id):
        phases_done = [
            phase for phase, blob in (s.phase_data or {}).items()
            if blob.get("completed_at")
        ]
        lines.append(f"## §{s.order_index}: {s.title or '(untitled)'}")
        lines.append("")
        lines.append(f"- Section ID: {s.id}")
        lines.append(f"- Current phase: {s.current_phase}")
        lines.append(f"- Focus brief: {'ready' if s.focus_brief else 'pending'}")
        lines.append(f"- Notes: {'ready' if s.notes else 'pending'}")
        lines.append(f"- Rolling summary: {'ready' if s.rolling_summary else 'pending'}")
        lines.append(f"- Completed phases: {', '.join(phases_done) if phases_done else 'none'}")
        lines.append("")
    return "\n".join(lines)


def _render_learning_map(db: DB, material_id: int) -> str:
    lm = db.get_learning_map(material_id)
    if lm is None:
        return "# Learning map\n\nPending. Run `prepare_material` to generate it.\n"
    return lm.map_markdown.rstrip() + "\n"


def _render_focus_briefs(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    parts = [f"# Focus briefs — {material.title}"]
    for s in db.get_sections(material_id):
        if s.focus_brief:
            parts.append(render_focus_brief_markdown(s.focus_brief, s.order_index, s.title))
        else:
            parts.append(f"## §{s.order_index}: {s.title or '(untitled)'}\n\nPending.\n")
    return "\n\n---\n\n".join(parts).rstrip() + "\n"


def _render_notes(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    parts = [f"# Notes — {material.title}"]
    for s in db.get_sections(material_id):
        parts.append(f"## §{s.order_index}: {s.title or '(untitled)'}")
        parts.append(s.notes or "Pending. Run `prepare_material(..., scope='notes')`.")
    return "\n\n".join(parts).rstrip() + "\n"


def _render_progress(db: DB, material_id: int) -> str:
    progress = material_progress(db, material_id)
    return "\n".join(
        [
            f"# Progress — {progress['title']}",
            "",
            f"- Sections completed: {progress['sections_completed']} / {progress['sections_total']}",
            f"- Flashcards: {progress['flashcards_total']}",
            f"- Due flashcards: {progress['flashcards_due']}",
            f"- Mastered flashcards: {progress['flashcards_mastered']}",
            f"- Learning map: {'ready' if progress['has_learning_map'] else 'pending'}",
            f"- Last activity: {progress['last_activity'] or 'none'}",
            "",
        ]
    )


def _render_phase_responses(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    parts = [f"# Phase responses — {material.title}"]
    for s in db.get_sections(material_id):
        parts.append(f"## §{s.order_index}: {s.title or '(untitled)'}")
        if not s.phase_data:
            parts.append("No recorded responses.")
            continue
        for phase, blob in s.phase_data.items():
            parts.append(f"### {phase}")
            response = blob.get("response") or "(no response text)"
            parts.append(str(response))
            if blob.get("completed_at"):
                parts.append(f"\nCompleted: {blob['completed_at']}")
    return "\n\n".join(parts).rstrip() + "\n"


def _render_rolling_summaries(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    parts = [f"# Rolling summaries — {material.title}"]
    for s in db.get_sections(material_id):
        parts.append(f"## §{s.order_index}: {s.title or '(untitled)'}")
        parts.append(s.rolling_summary or "Pending.")
    return "\n\n".join(parts).rstrip() + "\n"


def _render_flashcards(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    cards = db.list_flashcards(material_id=material_id)
    parts = [f"# Flashcards — {material.title}"]
    if not cards:
        parts.append("No flashcards yet.")
    for c in cards:
        section = f"§{c.section_id}" if c.section_id else "material"
        parts.append(f"## Card {c.id} ({section})")
        parts.append(f"**Q:** {c.question}")
        parts.append(f"**A:** {c.answer}")
        parts.append(
            f"Reviews: {c.review_count}; interval: {c.interval_days} days; "
            f"next review: {c.next_review.isoformat()}"
        )
    return "\n\n".join(parts).rstrip() + "\n"


def _render_study_plan(study_plan: dict[str, Any] | None) -> str:
    if not study_plan:
        return "# Study plan\n\nNo study plan has been generated in this export.\n"
    parts = ["# Study plan"]
    for session in study_plan.get("sessions", []):
        sections = ", ".join(str(sid) for sid in session.get("section_ids", []))
        parts.append(
            f"- {session.get('day')}: sections {sections} "
            f"({session.get('estimated_minutes', '?')} min)"
        )
    return "\n".join(parts).rstrip() + "\n"


def _render_completion_reports(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    parts = [f"# Completion reports — {material.title}"]
    any_report = False
    for s in db.get_sections(material_id):
        report = db.get_completion_report(s.id)
        if report:
            any_report = True
            parts.append(f"## §{s.order_index}: {s.title or '(untitled)'}")
            parts.append(report[0])
    if not any_report:
        parts.append("No completion reports yet.")
    return "\n\n".join(parts).rstrip() + "\n"


def _render_evaluations(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    parts = [f"# Evaluations — {material.title}"]
    any_eval = False
    for s in db.get_sections(material_id):
        for ev in db.list_evaluations(s.id):
            any_eval = True
            parts.append(f"## §{s.order_index}: {s.title or '(untitled)'} — {ev['phase']}")
            parts.append(ev["markdown"])
    if not any_eval:
        parts.append("No evaluations yet.")
    return "\n\n".join(parts).rstrip() + "\n"


def _json_payloads(
    db: DB,
    material_id: int,
    study_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    material = db.get_material(material_id)
    sections = db.get_sections(material_id)
    learning_map = db.get_learning_map(material_id)
    focus_briefs = [
        {"section_id": s.id, "order_index": s.order_index, "title": s.title, "brief": s.focus_brief}
        for s in sections
    ]
    section_payloads = [
        {
            "section_id": s.id,
            "order_index": s.order_index,
            "title": s.title,
            "current_phase": s.current_phase,
            "has_focus_brief": s.focus_brief is not None,
            "has_notes": s.notes is not None,
            "has_rolling_summary": s.rolling_summary is not None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        }
        for s in sections
    ]
    phase_responses = [
        {
            "section_id": s.id,
            "order_index": s.order_index,
            "title": s.title,
            "phase_data": s.phase_data,
        }
        for s in sections
    ]
    completion_reports = []
    evaluations = []
    for s in sections:
        report = db.get_completion_report(s.id)
        if report:
            completion_reports.append(
                {
                    "section_id": s.id,
                    "order_index": s.order_index,
                    "title": s.title,
                    "markdown": report[0],
                    "generated_at": report[1].isoformat() if report[1] else None,
                }
            )
        evaluations.extend(db.list_evaluations(s.id))

    cards = [
        {
            "flashcard_id": c.id,
            "material_id": c.material_id,
            "section_id": c.section_id,
            "question": c.question,
            "answer": c.answer,
            "ease_factor": c.ease_factor,
            "interval_days": c.interval_days,
            "review_count": c.review_count,
            "next_review": c.next_review.isoformat() if c.next_review else None,
            "is_mastered": c.is_mastered,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in db.list_flashcards(material_id=material_id)
    ]
    return {
        "manifest.json": {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "material": {
                "id": material.id,
                "title": material.title,
                "source_type": material.source_type,
                "source_ref": material.source_ref,
                "content_hash": material.content_hash,
                "created_at": material.created_at.isoformat() if material.created_at else None,
                "ingestion_status": material.ingestion_status,
            },
            "artifact_files": list(JSON_FILES),
        },
        "learning-map.json": {
            "map": learning_map.map_json if learning_map else None,
            "markdown": learning_map.map_markdown if learning_map else None,
            "generated_at": learning_map.generated_at.isoformat() if learning_map else None,
        },
        "focus-briefs.json": focus_briefs,
        "sections.json": section_payloads,
        "progress.json": material_progress(db, material_id),
        "phase-responses.json": phase_responses,
        "flashcards.json": cards,
        "study-plan.json": study_plan or {"sessions": []},
        "completion-reports.json": completion_reports,
        "evaluations.json": evaluations,
    }


def _atomic_write_text(path: Path, text: str, suffix: str = ".tmp") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + suffix)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
