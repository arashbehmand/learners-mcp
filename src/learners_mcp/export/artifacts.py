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
from ..language import detect_source_language
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
        updated.extend(
            _write_json_artifacts(db, material_id, target / "json", study_plan)
        )

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
        m.id
        for m in sorted(db.list_materials(), key=lambda item: item.id)
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
        _atomic_write_text(
            path, json.dumps(payloads[filename], indent=2), suffix=".tmp"
        )
        out.append(path)
    return out


def _render_readme(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    sections = db.get_sections(material_id)
    labels = _labels(_material_language_code(db, material_id))
    return "\n".join(
        [
            f"# {material.title}",
            "",
            labels["generated_artifacts"],
            "",
            f"- {labels['material_id']}: {material.id}",
            f"- {labels['source_type']}: {material.source_type or labels['unknown']}",
            f"- {labels['source']}: {material.source_ref or labels['pasted']}",
            f"- {labels['sections']}: {len(sections)}",
            "",
            f"## {labels['files']}",
            "",
            *[f"- `{name}`" for name in MARKDOWN_FILES if name != "README.md"],
            "",
        ]
    )


def _render_sections(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    labels = _labels(_material_language_code(db, material_id))
    lines = [f"# {labels['sections']} — {material.title}", ""]
    for s in db.get_sections(material_id):
        phases_done = [
            phase
            for phase, blob in (s.phase_data or {}).items()
            if blob.get("completed_at")
        ]
        lines.append(
            f"## §{s.order_index}: {s.title or labels['untitled_parenthesized']}"
        )
        lines.append("")
        lines.append(f"- {labels['section_id']}: {s.id}")
        lines.append(f"- {labels['current_phase']}: {s.current_phase}")
        lines.append(
            f"- {labels['focus_brief']}: {labels['ready'] if s.focus_brief else labels['pending']}"
        )
        lines.append(
            f"- {labels['notes']}: {labels['ready'] if s.notes else labels['pending']}"
        )
        lines.append(
            f"- {labels['rolling_summary']}: {labels['ready'] if s.rolling_summary else labels['pending']}"
        )
        lines.append(
            f"- {labels['completed_phases']}: {', '.join(phases_done) if phases_done else labels['none']}"
        )
        lines.append("")
    return "\n".join(lines)


def _render_learning_map(db: DB, material_id: int) -> str:
    lm = db.get_learning_map(material_id)
    labels = _labels(_material_language_code(db, material_id))
    if lm is None:
        return f"# {labels['learning_map']}\n\n{labels['pending_prepare']}\n"
    return lm.map_markdown.rstrip() + "\n"


def _render_focus_briefs(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    language_code = _material_language_code(db, material_id)
    labels = _labels(language_code)
    parts = [f"# {labels['focus_briefs']} — {material.title}"]
    for s in db.get_sections(material_id):
        if s.focus_brief:
            parts.append(
                render_focus_brief_markdown(
                    s.focus_brief,
                    s.order_index,
                    s.title,
                    language_code=language_code,
                )
            )
        else:
            parts.append(
                f"## §{s.order_index}: {s.title or labels['untitled_parenthesized']}\n\n"
                f"{labels['pending']}.\n"
            )
    return "\n\n---\n\n".join(parts).rstrip() + "\n"


def _render_notes(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    labels = _labels(_material_language_code(db, material_id))
    parts = [f"# {labels['notes']} — {material.title}"]
    for s in db.get_sections(material_id):
        parts.append(
            f"## §{s.order_index}: {s.title or labels['untitled_parenthesized']}"
        )
        parts.append(s.notes or labels["pending_notes"])
    return "\n\n".join(parts).rstrip() + "\n"


def _render_progress(db: DB, material_id: int) -> str:
    progress = material_progress(db, material_id)
    labels = _labels(_material_language_code(db, material_id))
    return "\n".join(
        [
            f"# {labels['progress']} — {progress['title']}",
            "",
            f"- {labels['sections_completed']}: {progress['sections_completed']} / {progress['sections_total']}",
            f"- {labels['flashcards']}: {progress['flashcards_total']}",
            f"- {labels['due_flashcards']}: {progress['flashcards_due']}",
            f"- {labels['mastered_flashcards']}: {progress['flashcards_mastered']}",
            f"- {labels['learning_map']}: {labels['ready'] if progress['has_learning_map'] else labels['pending']}",
            f"- {labels['last_activity']}: {progress['last_activity'] or labels['none']}",
            "",
        ]
    )


def _render_phase_responses(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    labels = _labels(_material_language_code(db, material_id))
    parts = [f"# {labels['phase_responses']} — {material.title}"]
    for s in db.get_sections(material_id):
        parts.append(
            f"## §{s.order_index}: {s.title or labels['untitled_parenthesized']}"
        )
        if not s.phase_data:
            parts.append(labels["no_recorded_responses"])
            continue
        for phase, blob in s.phase_data.items():
            parts.append(f"### {phase}")
            response = blob.get("response") or labels["no_response_text"]
            parts.append(str(response))
            if blob.get("completed_at"):
                parts.append(f"\n{labels['completed']}: {blob['completed_at']}")
    return "\n\n".join(parts).rstrip() + "\n"


def _render_rolling_summaries(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    labels = _labels(_material_language_code(db, material_id))
    parts = [f"# {labels['rolling_summaries']} — {material.title}"]
    for s in db.get_sections(material_id):
        parts.append(
            f"## §{s.order_index}: {s.title or labels['untitled_parenthesized']}"
        )
        parts.append(s.rolling_summary or f"{labels['pending']}.")
    return "\n\n".join(parts).rstrip() + "\n"


def _render_flashcards(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    cards = db.list_flashcards(material_id=material_id)
    labels = _labels(_material_language_code(db, material_id))
    parts = [f"# {labels['flashcards']} — {material.title}"]
    if not cards:
        parts.append(labels["no_flashcards"])
    for c in cards:
        section = f"§{c.section_id}" if c.section_id else labels["material"]
        parts.append(f"## {labels['card']} {c.id} ({section})")
        parts.append(f"**Q:** {c.question}")
        parts.append(f"**A:** {c.answer}")
        parts.append(
            f"{labels['reviews']}: {c.review_count}; {labels['interval']}: "
            f"{c.interval_days} {labels['days']}; {labels['next_review']}: "
            f"{c.next_review.isoformat()}"
        )
    return "\n\n".join(parts).rstrip() + "\n"


def _render_study_plan(study_plan: dict[str, Any] | None) -> str:
    labels = _labels((study_plan or {}).get("source_language", {}).get("code"))
    if not study_plan:
        return f"# {labels['study_plan']}\n\n{labels['no_study_plan']}\n"
    parts = [f"# {labels['study_plan']}"]
    for session in study_plan.get("sessions", []):
        sections = ", ".join(str(sid) for sid in session.get("section_ids", []))
        parts.append(
            f"- {session.get('day')}: {labels['sections']} {sections} "
            f"({session.get('estimated_minutes', '?')} min)"
        )
    return "\n".join(parts).rstrip() + "\n"


def _render_completion_reports(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    labels = _labels(_material_language_code(db, material_id))
    parts = [f"# {labels['completion_reports']} — {material.title}"]
    any_report = False
    for s in db.get_sections(material_id):
        report = db.get_completion_report(s.id)
        if report:
            any_report = True
            parts.append(
                f"## §{s.order_index}: {s.title or labels['untitled_parenthesized']}"
            )
            parts.append(report[0])
    if not any_report:
        parts.append(labels["no_completion_reports"])
    return "\n\n".join(parts).rstrip() + "\n"


def _render_evaluations(db: DB, material_id: int) -> str:
    material = db.get_material(material_id)
    labels = _labels(_material_language_code(db, material_id))
    parts = [f"# {labels['evaluations']} — {material.title}"]
    any_eval = False
    for s in db.get_sections(material_id):
        for ev in db.list_evaluations(s.id):
            any_eval = True
            parts.append(
                f"## §{s.order_index}: {s.title or labels['untitled_parenthesized']} — {ev['phase']}"
            )
            parts.append(ev["markdown"])
    if not any_eval:
        parts.append(labels["no_evaluations"])
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
        {
            "section_id": s.id,
            "order_index": s.order_index,
            "title": s.title,
            "brief": s.focus_brief,
        }
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
                "created_at": (
                    material.created_at.isoformat() if material.created_at else None
                ),
                "ingestion_status": material.ingestion_status,
            },
            "artifact_files": list(JSON_FILES),
        },
        "learning-map.json": {
            "map": learning_map.map_json if learning_map else None,
            "markdown": learning_map.map_markdown if learning_map else None,
            "generated_at": (
                learning_map.generated_at.isoformat() if learning_map else None
            ),
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


def _material_language_code(db: DB, material_id: int) -> str | None:
    material = db.get_material(material_id)
    if material and material.ingestion_status.get("source_language"):
        return material.ingestion_status["source_language"].get("code")
    sections = db.get_sections(material_id)
    sample = "\n\n".join(s.content for s in sections[:3])
    return detect_source_language(sample).get("code") if sample else None


def _labels(language_code: str | None) -> dict[str, str]:
    if language_code == "fa":
        return {
            "generated_artifacts": "آثار آموزشی تولیدشده برای این منبع.",
            "material_id": "شناسه منبع",
            "source_type": "نوع منبع",
            "source": "منبع",
            "sections": "بخش‌ها",
            "files": "فایل‌ها",
            "unknown": "نامشخص",
            "pasted": "(متن واردشده)",
            "untitled_parenthesized": "(بدون عنوان)",
            "section_id": "شناسه بخش",
            "current_phase": "مرحله فعلی",
            "focus_brief": "راهنمای تمرکز",
            "focus_briefs": "راهنماهای تمرکز",
            "notes": "یادداشت‌ها",
            "rolling_summary": "خلاصه پیوسته",
            "rolling_summaries": "خلاصه‌های پیوسته",
            "completed_phases": "مرحله‌های تکمیل‌شده",
            "ready": "آماده",
            "pending": "در انتظار",
            "none": "هیچ",
            "learning_map": "نقشه یادگیری",
            "pending_prepare": "در انتظار. برای تولید آن `prepare_material` را اجرا کنید.",
            "pending_notes": "در انتظار. `prepare_material(..., scope='notes')` را اجرا کنید.",
            "progress": "پیشرفت",
            "sections_completed": "بخش‌های تکمیل‌شده",
            "flashcards": "فلش‌کارت‌ها",
            "due_flashcards": "فلش‌کارت‌های موعددار",
            "mastered_flashcards": "فلش‌کارت‌های مسلط‌شده",
            "last_activity": "آخرین فعالیت",
            "phase_responses": "پاسخ‌های مرحله‌ای",
            "no_recorded_responses": "پاسخی ثبت نشده است.",
            "no_response_text": "(متن پاسخ وجود ندارد)",
            "completed": "تکمیل‌شده",
            "no_flashcards": "هنوز فلش‌کارتی وجود ندارد.",
            "material": "منبع",
            "card": "کارت",
            "reviews": "مرورها",
            "interval": "فاصله",
            "days": "روز",
            "next_review": "مرور بعدی",
            "study_plan": "برنامه مطالعه",
            "no_study_plan": "در این خروجی هنوز برنامه مطالعه‌ای تولید نشده است.",
            "completion_reports": "گزارش‌های تکمیل",
            "no_completion_reports": "هنوز گزارش تکمیلی وجود ندارد.",
            "evaluations": "ارزیابی‌ها",
            "no_evaluations": "هنوز ارزیابی‌ای وجود ندارد.",
        }
    return {
        "generated_artifacts": "Generated study artifacts for this material.",
        "material_id": "Material ID",
        "source_type": "Source type",
        "source": "Source",
        "sections": "Sections",
        "files": "Files",
        "unknown": "unknown",
        "pasted": "(pasted)",
        "untitled_parenthesized": "(untitled)",
        "section_id": "Section ID",
        "current_phase": "Current phase",
        "focus_brief": "Focus brief",
        "focus_briefs": "Focus briefs",
        "notes": "Notes",
        "rolling_summary": "Rolling summary",
        "rolling_summaries": "Rolling summaries",
        "completed_phases": "Completed phases",
        "ready": "ready",
        "pending": "pending",
        "none": "none",
        "learning_map": "Learning map",
        "pending_prepare": "Pending. Run `prepare_material` to generate it.",
        "pending_notes": "Pending. Run `prepare_material(..., scope='notes')`.",
        "progress": "Progress",
        "sections_completed": "Sections completed",
        "flashcards": "Flashcards",
        "due_flashcards": "Due flashcards",
        "mastered_flashcards": "Mastered flashcards",
        "last_activity": "Last activity",
        "phase_responses": "Phase responses",
        "no_recorded_responses": "No recorded responses.",
        "no_response_text": "(no response text)",
        "completed": "Completed",
        "no_flashcards": "No flashcards yet.",
        "material": "material",
        "card": "Card",
        "reviews": "Reviews",
        "interval": "interval",
        "days": "days",
        "next_review": "next review",
        "study_plan": "Study plan",
        "no_study_plan": "No study plan has been generated in this export.",
        "completion_reports": "Completion reports",
        "no_completion_reports": "No completion reports yet.",
        "evaluations": "Evaluations",
        "no_evaluations": "No evaluations yet.",
    }
