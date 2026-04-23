"""learners-mcp FastMCP server.

Exposes tools, resources, and prompts for the four-phase learning loop.
Host-agnostic — works with any MCP-capable agent.

The server owns batch LLM work (note extraction, learning map, focus briefs,
flashcard suggestions) via its own Anthropic API key. In-chat coaching
happens on the *host* agent via the phase Prompt definitions — no nested
LLM calls during chat.
"""

from __future__ import annotations

import json
import logging
import sys
import types
import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _install_lightweight_mcp_package() -> None:
    """Avoid importing MCP client modules when this process is only a server."""
    if "mcp" in sys.modules:
        return
    spec = importlib.util.find_spec("mcp")
    if spec is None or spec.submodule_search_locations is None:
        return
    pkg = types.ModuleType("mcp")
    pkg.__path__ = list(spec.submodule_search_locations)  # type: ignore[attr-defined]
    pkg.__package__ = "mcp"
    pkg.__spec__ = spec
    sys.modules["mcp"] = pkg


_install_lightweight_mcp_package()

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts.base import Message, UserMessage
from mcp.types import (
    Annotations as MCPAnnotations,
    CallToolResult,
    ResourceLink,
    TextContent,
)

from . import __version__
from .config import ensure_data_dir
from .db import DB
from .db import content_hash as hash_text
from .export.anki import export_apkg as svc_export_apkg
from .export.anki import export_csv as svc_export_csv
from .export.artifacts import auto_export_markdown_artifacts as svc_auto_export_artifacts
from .export.artifacts import export_material_artifacts as svc_export_material_artifacts
from .export.markdown import export_notes_markdown as svc_export_notes
from .export.portable import export_project as svc_export_project
from .export.portable import import_project as svc_import_project
from .ingestion.loader import load as loader_load
from .ingestion.loader import load_text as loader_load_text
from .ingestion.loader import preload_markitdown as loader_preload_markitdown
from .llm.prompts import (
    ANCHOR_COACH_SYSTEM,
    EXPLAIN_COACH_SYSTEM,
    PREVIEW_COACH_SYSTEM,
    QUESTION_COACH_SYSTEM,
)
from .orientation.render import render_focus_brief_markdown
from .study.phases import (
    PHASES,
    next_phase,
    recommend_next_action as svc_recommend,
    resolved_current_phase,
    validate_phase_action,
)
from .study.plan import plan_study as svc_plan_study
from .study.prereqs import check_prerequisites as svc_check_prereqs
from .study.progress import library_progress as svc_library_progress
from .study.progress import material_progress as svc_material_progress
from .study.streak import (
    compute_streak as svc_compute_streak,
    render_weekly_markdown as svc_render_weekly_md,
    weekly_report as svc_weekly_report,
)


log = logging.getLogger(__name__)

mcp = FastMCP(
    name="learners-mcp",
    instructions=(
        "Guide the learner through any material with orientation (learning map + "
        "focus briefs), structured notes, and the four-phase study loop "
        "(Preview → Explain → Question → Anchor) with SM-2 flashcards.\n\n"
        "Preferred study order:\n"
        "1. Call `list_materials` to inspect the library, or `ingest_material` for a "
        "new local file, URL, YouTube link, or pasted text.\n"
        "2. Immediately call `prepare_material(material_id)` or "
        "`start_background_preparation(material_id)`.\n"
        "3. Check `get_preparation_status(material_id)`; prefer waiting until the "
        "learning map and focus briefs are ready before section study.\n"
        "4. Call `get_material_map(material_id)` once to orient the learner.\n"
        "5. Call `list_sections(material_id)` and choose the next section.\n"
        "6. Call `start_section(section_id)` to activate it.\n"
        "7. Run the section phases in order: `preview` → `explain` → `question` → "
        "`anchor`. For each phase, fetch the coaching prompt (`get_phase_prompt` or "
        "native MCP prompt), talk with the learner, then persist the result with "
        "`record_phase_response` and `complete_phase`.\n"
        "8. In Anchor, call `suggest_flashcards`, commit accepted cards with "
        "`add_flashcard`, then complete the phase.\n"
        "9. After section study, use `recommend_next_action`, `next_due`, and "
        "`review_flashcard` for follow-up and spaced repetition.\n\n"
        "Prefer orientation before deep section work unless the learner explicitly wants "
        "to jump ahead. Phase flow is soft-guidance — warnings, not blocks. Generated "
        "learner artifacts should stay in the source material's language; direct "
        "learner-facing coaching should use the language the learner is using. Tool "
        "results are intentionally compact: use linked resources and Markdown artifacts "
        "for full content instead of pasting large blobs into chat. If a host says a "
        "learners tool has not been loaded yet, call the host's `tool_search` for that "
        "exact learners tool name, then retry with the schema returned by tool_search."
    ),
)

# Module-level singletons. Initialised lazily the first time a tool runs,
# so stdio startup stays fast and missing API keys fail only on actual use.
_db: DB | None = None
_llm: Any | None = None


def _get_db() -> DB:
    """Return the DB singleton, rebuilding when `LEARNERS_MCP_DATA_DIR` has
    changed since our last self-built DB.

    We tag any DB we build with a hidden attribute recording the env value
    in force at build time. A DB installed from outside (e.g.
    `monkeypatch.setattr(server, "_db", custom_db)`) won't carry that tag,
    so we trust it and don't rebuild behind the user's back.
    """
    global _db
    import os as _os

    env_now = _os.environ.get("LEARNERS_MCP_DATA_DIR") or ""
    tag = getattr(_db, "_built_for_env_key", None) if _db is not None else None

    if _db is None or (tag is not None and tag != env_now):
        ensure_data_dir()
        _db = DB()
        _db._built_for_env_key = env_now  # type: ignore[attr-defined]
    return _db


def _get_llm() -> Any:
    global _llm
    if _llm is None:
        from .llm.client import LLM

        _llm = LLM()
    return _llm


def pipeline_ingest(*args, **kwargs):
    from .ingestion.pipeline import ingest

    return ingest(*args, **kwargs)


async def pipeline_prepare(*args, **kwargs):
    from .ingestion.pipeline import prepare_material

    return await prepare_material(*args, **kwargs)


def pipeline_status(*args, **kwargs):
    from .ingestion.pipeline import preparation_status

    return preparation_status(*args, **kwargs)


class _BackgroundProxy:
    """Lazy proxy kept patchable for tests and downstream integrations."""

    def start(self, *args, **kwargs):
        from .ingestion import background as real_background

        return real_background.start(*args, **kwargs)

    def status(self, *args, **kwargs):
        from .ingestion import background as real_background

        return real_background.status(*args, **kwargs)


background = _BackgroundProxy()


def _background_start(*args, **kwargs):
    return background.start(*args, **kwargs)


def _background_status(*args, **kwargs):
    return background.status(*args, **kwargs)


async def svc_gen_material_map(*args, **kwargs):
    from .orientation.generator import generate_material_map

    return await generate_material_map(*args, **kwargs)


async def svc_gen_completion(*args, **kwargs):
    from .study.completion import generate_completion_report

    return await generate_completion_report(*args, **kwargs)


async def svc_evaluate_phase(*args, **kwargs):
    from .study.evaluation import evaluate_phase_response

    return await evaluate_phase_response(*args, **kwargs)


async def svc_suggest_flashcards(*args, **kwargs):
    from .flashcards.service import suggest_flashcards

    return await suggest_flashcards(*args, **kwargs)


def svc_review_flashcard(*args, **kwargs):
    from .flashcards.service import review_flashcard

    return review_flashcard(*args, **kwargs)


async def svc_answer(*args, **kwargs):
    from .study.qa import answer_from_material

    return await answer_from_material(*args, **kwargs)


async def ensure_rolling_summary(*args, **kwargs):
    from .study.rolling import ensure_rolling_summary as _ensure_rolling_summary

    return await _ensure_rolling_summary(*args, **kwargs)


# --------------------- helpers ---------------------


def _section_ref(section) -> str:
    title = f": {section.title}" if section.title else ""
    return f"§{section.order_index}{title}"


def _material_brief(m) -> dict[str, Any]:
    return {
        "material_id": m.id,
        "title": m.title,
        "source_type": m.source_type,
        "source_ref": m.source_ref,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _section_brief(s) -> dict[str, Any]:
    phases_done = [p for p in PHASES if (s.phase_data or {}).get(p, {}).get("completed_at")]
    return {
        "section_id": s.id,
        "order_index": s.order_index,
        "title": s.title,
        "current_phase": resolved_current_phase(s),
        "phases_completed": phases_done,
        "has_focus_brief": s.focus_brief is not None,
        "has_notes": s.notes is not None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
    }


def _with_artifacts(
    payload: dict[str, Any],
    material_id: int,
    *,
    study_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach best-effort Markdown mirror metadata to a tool response."""
    try:
        artifact = svc_auto_export_artifacts(
            _get_db(),
            material_id,
            study_plan=study_plan,
        )
        if artifact:
            payload["artifact_dir"] = artifact["artifact_dir"]
            payload["updated_files"] = artifact["updated_files"]
    except Exception as exc:
        payload["artifact_warning"] = f"{type(exc).__name__}: {exc}"
    return payload


def _material_id_for_section(section_id: int) -> int:
    s = _get_db().get_section(section_id)
    if s is None:
        raise KeyError(f"section {section_id} not found")
    return s.material_id


def _source_language_code(material_id: int) -> str | None:
    material = _get_db().get_material(material_id)
    if material is None:
        return None
    source_language = (material.ingestion_status or {}).get("source_language") or {}
    return source_language.get("code")


HEAVY_RESOURCE_ANNOTATIONS = MCPAnnotations(
    audience=["user", "assistant"],
    priority=0.2,
)


def _preview_text(text: str | None, limit: int = 600) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    head = compact[:limit]
    cut = head.rfind(" ")
    if cut > limit * 0.6:
        head = head[:cut]
    return head.rstrip() + "..."


def _text_block(text: str, *, priority: float = 1.0) -> TextContent:
    return TextContent(
        type="text",
        text=text,
        annotations=MCPAnnotations(audience=["user", "assistant"], priority=priority),
    )


def _resource_link(
    uri: str,
    *,
    name: str,
    title: str,
    description: str,
    mime_type: str = "text/markdown",
    priority: float = 0.2,
) -> ResourceLink:
    return ResourceLink(
        type="resource_link",
        uri=uri,
        name=name,
        title=title,
        description=description,
        mimeType=mime_type,
        annotations=MCPAnnotations(audience=["user", "assistant"], priority=priority),
    )


def _artifact_location(payload: dict[str, Any], file_name: str | None = None) -> str | None:
    artifact_dir = payload.get("artifact_dir")
    if not artifact_dir:
        return None
    base = Path(str(artifact_dir))
    return str((base / file_name) if file_name else base)


def _compact_tool_result(
    summary_lines: list[str],
    payload: dict[str, Any],
    *,
    links: list[ResourceLink] | None = None,
) -> CallToolResult:
    text = "\n".join(line for line in summary_lines if line).strip()
    content: list[Any] = [_text_block(text)] if text else []
    if links:
        content.extend(links)
    return CallToolResult(content=content, structuredContent=payload)


# --------------------- tools: ingestion + preparation ---------------------


@mcp.tool(
    description=(
        "Ingest source material from a local file, URL, YouTube link, or pasted "
        "text. Creates the material record, splits it into sections, and by "
        "default kicks off background preparation (learning map + focus briefs + "
        "notes) so artifacts are ready when the learner starts. Idempotent on "
        "content hash — ingesting the same source twice returns the existing "
        "material_id. Supported local files: PDF/EPUB/DOCX/TXT/MD."
    )
)
async def ingest_material(
    source: str,
    title: str | None = None,
    paste_text: bool = False,
    auto_prepare: bool = True,
) -> dict[str, Any]:
    import asyncio

    db = _get_db()

    # Loader is sync + can be slow (markitdown, network). Run in a thread so
    # we don't block the event loop while background.start() wants to attach.
    if paste_text:
        loaded = await asyncio.to_thread(loader_load_text, source, title or "Untitled")
    else:
        loader_preload_markitdown(source)
        loaded = await asyncio.to_thread(loader_load, source, title)

    h = hash_text(loaded.text)
    material_id = await asyncio.to_thread(pipeline_ingest, db, loaded, h)
    sections = db.get_sections(material_id)

    prep_status: dict[str, Any] | None = None
    if auto_prepare:
        try:
            prep_status = _background_start(
                db, _get_llm(), material_id, scope="all", force=False
            )
        except Exception as exc:
            # Missing API key or lifecycle issues shouldn't fail the ingest.
            log.warning(
                "auto-prepare for material %d could not start: %s", material_id, exc
            )
            prep_status = {"status": "not_started", "reason": str(exc)}

    return _with_artifacts({
        "material_id": material_id,
        "sections_detected": len(sections),
        "title": loaded.title,
        "source_type": loaded.source_type,
        "preparation": prep_status,
    }, material_id)


@mcp.tool(
    description=(
        "Generate or continue generating the learning map, per-section focus "
        "briefs, and extracted notes. Idempotent and resumable — each call picks "
        "up unfinished artifacts and skips completed ones. Pass `scope` to limit "
        "work: 'all' | 'map' | 'focus_briefs' | 'notes'. Safe to call repeatedly "
        "while the learner studies."
    )
)
async def prepare_material(
    material_id: int, scope: str = "all", force: bool = False
) -> dict[str, Any]:
    db = _get_db()
    llm = _get_llm()
    report = await pipeline_prepare(db, llm, material_id, scope=scope, force=force)
    return _with_artifacts(report, material_id)


@mcp.tool(
    description=(
        "Read-only preparation status. Returns which artifacts are ready for a "
        "material without kicking off new work."
    )
)
def get_preparation_status(material_id: int) -> dict[str, Any]:
    return pipeline_status(_get_db(), material_id)


# --------------------- tools: orientation ---------------------


@mcp.tool(
    description=(
        "Return the material-level learning map: objectives, key concepts, "
        "prerequisites, common pitfalls, suggested path. Call `prepare_material` "
        "first if pending. Returns both structured JSON and a markdown rendering."
    )
)
def get_material_map(material_id: int) -> dict[str, Any]:
    lm = _get_db().get_learning_map(material_id)
    if lm is None:
        payload = {
            "status": "pending",
            "note": "Call prepare_material(material_id) first.",
            "resource_uri": f"learning-map://{material_id}",
        }
        return _compact_tool_result(
            [
                f"Learning map for material {material_id} is still pending.",
                "Preferred order: call `prepare_material(material_id)` or "
                "`start_background_preparation(material_id)`, then retry.",
            ],
            payload,
            links=[
                _resource_link(
                    f"learning-map://{material_id}",
                    name="learning-map",
                    title="Learning map",
                    description="Full learning map resource for this material.",
                )
            ],
        )
    payload = {
        "status": "ready",
        "map": lm.map_json,
        "markdown": lm.map_markdown,
        "generated_at": lm.generated_at.isoformat() if lm.generated_at else None,
        "regeneration_count": lm.regeneration_count,
        "resource_uri": f"learning-map://{material_id}",
    }
    map_json = lm.map_json or {}
    objectives = map_json.get("objectives") or []
    key_concepts = map_json.get("key_concepts") or []
    suggested_path = map_json.get("suggested_path") or []
    return _compact_tool_result(
        [
            f"Learning map ready for material {material_id}.",
            (
                f"Difficulty: {map_json.get('difficulty') or 'unknown'}. "
                f"Estimated time: {map_json.get('estimated_time') or 'unknown'}."
            ),
            (
                f"Objectives: {len(objectives)}. "
                f"Key concepts: {len(key_concepts)}. "
                f"Suggested path entries: {len(suggested_path)}."
            ),
            (
                f"Preview: {_preview_text(lm.map_markdown, 320)}"
                if lm.map_markdown
                else ""
            ),
            "Full map is available via the resource link below.",
        ],
        payload,
        links=[
            _resource_link(
                f"learning-map://{material_id}",
                name="learning-map",
                title="Learning map",
                description="Full learning map resource for this material.",
            )
        ],
    )


@mcp.tool(
    description=(
        "Return the section-level focus brief: what to focus on BEFORE reading. "
        "Includes one-sentence focus, key terms, pitfalls, connections, time estimate."
    )
)
def get_focus_brief(section_id: int) -> dict[str, Any]:
    s = _get_db().get_section(section_id)
    if s is None:
        raise KeyError(f"section {section_id} not found")
    if s.focus_brief is None:
        payload = {
            "status": "pending",
            "note": "Call prepare_material(material_id) first.",
            "resource_uri": f"focus-brief://{section_id}",
        }
        return _compact_tool_result(
            [
                f"Focus brief for section {section_id} is still pending.",
                "Call `prepare_material(material_id)` first if orientation artifacts have not been generated.",
            ],
            payload,
            links=[
                _resource_link(
                    f"focus-brief://{section_id}",
                    name="focus-brief",
                    title="Focus brief",
                    description="Full focus brief resource for this section.",
                )
            ],
        )
    markdown = render_focus_brief_markdown(
        s.focus_brief,
        s.order_index,
        s.title,
        language_code=_source_language_code(s.material_id),
    )
    payload = {
        "status": "ready",
        "order_index": s.order_index,
        "title": s.title,
        "brief": s.focus_brief,
        "markdown": markdown,
        "resource_uri": f"focus-brief://{section_id}",
    }
    brief = s.focus_brief or {}
    return _compact_tool_result(
        [
            f"Focus brief ready for §{s.order_index}: {s.title or '(untitled)'}.",
            f"Estimated time: {brief.get('estimated_minutes', 'unknown')} minutes.",
            f"Key terms: {len(brief.get('key_terms') or [])}.",
            f"Focus: {brief.get('focus') or 'n/a'}",
            "Full focus brief is available via the resource link below.",
        ],
        payload,
        links=[
            _resource_link(
                f"focus-brief://{section_id}",
                name="focus-brief",
                title="Focus brief",
                description="Full focus brief resource for this section.",
            )
        ],
    )


# --------------------- tools: notes ---------------------


@mcp.tool(
    description=(
        "Return the extracted Markdown notes for a whole material (combined) or "
        "a single section. Notes are auto-generated by prepare_material."
    )
)
def get_notes(material_id: int, section_id: int | None = None) -> dict[str, Any]:
    db = _get_db()
    if section_id is not None:
        s = db.get_section(section_id)
        if s is None:
            raise KeyError(f"section {section_id} not found")
        payload = {
            "scope": "section",
            "order_index": s.order_index,
            "title": s.title,
            "status": "ready" if s.notes else "pending",
            "markdown": s.notes or "",
            "resource_uri": f"notes://{material_id}/{section_id}",
        }
        status_line = (
            f"Section notes ready for §{s.order_index}: {s.title or '(untitled)'}."
            if s.notes
            else f"Section notes for §{s.order_index}: {s.title or '(untitled)'} are still pending."
        )
        return _compact_tool_result(
            [
                status_line,
                f"Preview: {_preview_text(s.notes, 320)}" if s.notes else "",
                "Full notes are available via the resource link below.",
            ],
            payload,
            links=[
                _resource_link(
                    f"notes://{material_id}/{section_id}",
                    name="section-notes",
                    title="Section notes",
                    description="Full notes resource for this section.",
                )
            ],
        )
    sections = db.get_sections(material_id)
    parts: list[str] = []
    any_ready = False
    for s in sections:
        if s.notes:
            any_ready = True
            parts.append(f"# §{s.order_index}: {s.title or '(untitled)'}\n\n{s.notes}")
    status = "ready" if all(s.notes for s in sections) else ("partial" if any_ready else "pending")
    payload = {
        "scope": "material",
        "status": status,
        "markdown": "\n\n---\n\n".join(parts),
        "sections_ready": sum(1 for s in sections if s.notes),
        "sections_total": len(sections),
        "resource_uri": f"notes://{material_id}",
    }
    return _compact_tool_result(
        [
            f"Material notes status for material {material_id}: {status}.",
            f"Sections with notes: {payload['sections_ready']}/{payload['sections_total']}.",
            (
                f"Preview: {_preview_text(payload['markdown'], 320)}"
                if payload["markdown"]
                else ""
            ),
            "Full notes are available via the resource link below.",
        ],
        payload,
        links=[
            _resource_link(
                f"notes://{material_id}",
                name="material-notes",
                title="Material notes",
                description="Combined notes resource for the full material.",
            )
        ],
    )


# --------------------- tools: study loop ---------------------


@mcp.tool(description="List sections of a material with progress badges.")
def list_sections(material_id: int) -> dict[str, Any]:
    sections = _get_db().get_sections(material_id)
    return {
        "material_id": material_id,
        "sections": [_section_brief(s) for s in sections],
    }


@mcp.tool(description="List all ingested materials.")
def list_materials() -> list[dict[str, Any]]:
    return [_material_brief(m) for m in _get_db().list_materials()]


@mcp.tool(
    description=(
        "Mark a section as active and return its full state: content, focus brief, "
        "current phase, any recorded phase responses. Also ensures the rolling "
        "summary for this section is computed. Requires `section_id`; optional "
        "`material_id` is accepted only to validate host retries that include it."
    )
)
async def start_section(section_id: int, material_id: int | str | None = None) -> dict[str, Any]:
    db = _get_db()
    s = db.get_section(section_id)
    if s is None:
        raise KeyError(f"section {section_id} not found")
    if material_id is not None and int(material_id) != s.material_id:
        raise ValueError(
            f"section {section_id} belongs to material {s.material_id}, not {material_id}"
        )

    await ensure_rolling_summary(db, _get_llm(), section_id)
    s = db.get_section(section_id)

    payload = _with_artifacts({
        "section": _section_brief(s),
        "content": s.content,
        "focus_brief": s.focus_brief,
        "focus_brief_markdown": (
            render_focus_brief_markdown(
                s.focus_brief,
                s.order_index,
                s.title,
                language_code=_source_language_code(s.material_id),
            )
            if s.focus_brief
            else None
        ),
        "rolling_summary": s.rolling_summary,
        "phase_data": s.phase_data,
    }, s.material_id)
    section_ref = _section_ref(s)
    phase_data = s.phase_data or {}
    recorded = [p for p in PHASES if (phase_data.get(p) or {}).get("response")]
    artifact_note = _artifact_location(payload, "sections.md")
    return _compact_tool_result(
        [
            f"{section_ref} is active. Current phase: {resolved_current_phase(s)}.",
            "Preferred study order from here: preview -> explain -> question -> anchor.",
            (
                f"Focus brief: {'ready' if s.focus_brief else 'pending'}. "
                f"Rolling summary: {'ready' if s.rolling_summary else 'pending'}."
            ),
            (
                f"Recorded phase responses: {', '.join(recorded)}."
                if recorded
                else "Recorded phase responses: none yet."
            ),
            f"Section preview: {_preview_text(s.content, 900)}",
            f"Readable section index mirror: {artifact_note}" if artifact_note else "",
            "Full section state, focus brief, notes, and source text are available via the resource links below.",
        ],
        payload,
        links=[
            _resource_link(
                f"section://{section_id}",
                name="section-source",
                title="Section source",
                description="Full source text for the active section.",
                mime_type="text/markdown",
            ),
            _resource_link(
                f"section-state://{section_id}",
                name="section-state",
                title="Section state",
                description="Structured section state including phase data and rolling summary.",
                mime_type="application/json",
            ),
            _resource_link(
                f"focus-brief://{section_id}",
                name="focus-brief",
                title="Focus brief",
                description="Full focus brief resource for this section.",
            ),
            _resource_link(
                f"notes://{s.material_id}/{section_id}",
                name="section-notes",
                title="Section notes",
                description="Full notes resource for this section.",
            ),
        ],
    )


@mcp.tool(
    description=(
        "Record the learner's response for a phase (and optionally a chat "
        "transcript). Soft-guidance: persists even if out-of-order, but returns "
        "a warning in `warning` when skipping ahead."
    )
)
def record_phase_response(
    section_id: int,
    phase: str,
    response: str,
    conversation: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    db = _get_db()
    s = db.get_section(section_id)
    if s is None:
        raise KeyError(f"section {section_id} not found")

    validation = validate_phase_action(s, phase)
    existing = (s.phase_data or {}).get(phase, {}) or {}
    merged = {
        **existing,
        "response": response,
        "conversation": conversation or existing.get("conversation") or [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db.update_phase_data(section_id, phase, merged)
    return _with_artifacts(
        {"ok": True, "warning": validation.warning, "phase": phase},
        s.material_id,
    )


@mcp.tool(
    description=(
        "Mark a phase as complete. Advances current_phase to the next. If the "
        "phase is 'anchor', also marks the section completed."
    )
)
async def complete_phase(section_id: int, phase: str) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    db = _get_db()
    s = db.get_section(section_id)
    if s is None:
        raise KeyError(f"section {section_id} not found")

    existing = (s.phase_data or {}).get(phase, {}) or {}
    merged = {**existing, "completed_at": datetime.now(timezone.utc).isoformat()}
    db.update_phase_data(section_id, phase, merged)

    advanced_to = next_phase(phase)
    if advanced_to is not None:
        db.update_section_field(section_id, "current_phase", advanced_to)

    completion_report: str | None = None
    if phase == "anchor":
        db.update_section_field(section_id, "completed_at", datetime.now(timezone.utc))
        try:
            completion_report = await svc_gen_completion(db, _get_llm(), section_id)
        except Exception as exc:  # don't fail the completion if the report errors
            log.warning("completion report failed for section %d: %s", section_id, exc)

    return _with_artifacts({
        "ok": True,
        "phase_completed": phase,
        "current_phase": advanced_to or "anchor",
        "section_completed": phase == "anchor",
        "completion_report": completion_report,
    }, s.material_id)


# --------------------- tools: flashcards ---------------------


@mcp.tool(
    description=(
        "Generate N flashcard candidates for a section using the learner's "
        "full context (source + phase responses + rolling summary), excluding "
        "any already-committed cards. Does NOT commit — the host agent calls "
        "`add_flashcard` for the ones the learner accepts."
    )
)
async def suggest_flashcards(section_id: int, n: int = 3) -> dict[str, Any]:
    db = _get_db()
    cards = await svc_suggest_flashcards(db, _get_llm(), section_id, n=n)
    return {"section_id": section_id, "candidates": cards}


@mcp.tool(description="Commit a flashcard for a section.")
def add_flashcard(section_id: int, question: str, answer: str) -> dict[str, Any]:
    db = _get_db()
    s = db.get_section(section_id)
    if s is None:
        raise KeyError(f"section {section_id} not found")
    fid = db.create_flashcard(
        material_id=s.material_id, section_id=section_id, question=question, answer=answer
    )
    return _with_artifacts(
        {"flashcard_id": fid, "section_id": section_id},
        s.material_id,
    )


@mcp.tool(
    description=(
        "List flashcards, optionally filtered by material, section, and status. "
        "filter_: 'all' | 'due' | 'mastered'."
    )
)
def list_flashcards(
    material_id: int | None = None,
    section_id: int | None = None,
    filter_: str = "all",
) -> list[dict[str, Any]]:
    cards = _get_db().list_flashcards(material_id=material_id, section_id=section_id, filter_=filter_)
    return [
        {
            "flashcard_id": c.id,
            "material_id": c.material_id,
            "section_id": c.section_id,
            "question": c.question,
            "answer": c.answer,
            "ease_factor": round(c.ease_factor, 3),
            "interval_days": c.interval_days,
            "review_count": c.review_count,
            "next_review": c.next_review.isoformat() if c.next_review else None,
            "is_mastered": c.is_mastered,
        }
        for c in cards
    ]


@mcp.tool(
    name="review_flashcard",
    description=(
        "Record a review result. knew_it=True increases the interval per SM-2; "
        "knew_it=False resets interval to 1 day. Mastery = review_count >= 5 "
        "AND interval_days >= 30."
    ),
)
def review_flashcard(flashcard_id: int, knew_it: bool) -> dict[str, Any]:
    db = _get_db()
    card = db.get_flashcard(flashcard_id)
    if card is None:
        raise KeyError(f"flashcard {flashcard_id} not found")
    result = svc_review_flashcard(db, flashcard_id, knew_it)
    return _with_artifacts(result, card.material_id)


@mcp.tool(description="Next N flashcards due for review.")
def next_due(material_id: int | None = None, n: int = 10) -> list[dict[str, Any]]:
    cards = _get_db().list_flashcards(material_id=material_id, filter_="due")
    return [
        {"flashcard_id": c.id, "question": c.question, "answer": c.answer, "section_id": c.section_id}
        for c in cards[:n]
    ]


# --------------------- tools: Q&A + progress ---------------------


@mcp.tool(
    description=(
        "Answer an ad-hoc question from the learner using the material as the "
        "source of truth. Responses include `[§N]` citations back to sections. "
        "scope='material' searches everything; scope='section' with a section_id "
        "limits to that section."
    )
)
async def answer_from_material(
    material_id: int,
    question: str,
    scope: str = "material",
    section_id: int | None = None,
) -> str:
    return await svc_answer(
        _get_db(), _get_llm(), material_id, question, scope=scope, section_id=section_id
    )


@mcp.tool(
    description=(
        "Recommend the next best study action: review due cards, continue a "
        "section, start the next section, or regenerate the map. Soft guidance — "
        "the host may present this as a suggestion, not a directive."
    )
)
def recommend_next_action(material_id: int | None = None) -> dict[str, Any]:
    return svc_recommend(_get_db(), material_id=material_id)


# --------------------- tools: orientation regeneration ---------------------


@mcp.tool(
    description=(
        "Regenerate the material-level learning map, optionally biased by the "
        "learner's adjustment notes (e.g., 'focus more on ch 4-6, skim ch 2'). "
        "Uses the existing map's regeneration_count to track revisions."
    )
)
async def regenerate_map(material_id: int, notes: str | None = None) -> dict[str, Any]:
    db = _get_db()
    material = db.get_material(material_id)
    if material is None:
        raise KeyError(f"material {material_id} not found")

    sections = db.get_sections(material_id)
    if not sections:
        raise RuntimeError(f"material {material_id} has no sections")

    full_text_parts: list[str] = []
    for s in sections:
        header = f"# §{s.order_index}: {s.title}\n\n" if s.title else f"# §{s.order_index}\n\n"
        full_text_parts.append(header + s.content)
    full_text = "\n\n".join(full_text_parts)

    section_index = [(s.order_index, s.title) for s in sections]
    from .orientation.cross_material import (
        format_known_concepts_block,
        gather_known_concepts,
    )

    known = gather_known_concepts(db, exclude_material_id=material_id)
    payload, md = await svc_gen_material_map(
        _get_llm(),
        full_text,
        section_index,
        learner_notes=notes,
        known_concepts_block=format_known_concepts_block(known),
    )
    db.upsert_learning_map(material_id, payload, md)
    lm = db.get_learning_map(material_id)
    return _with_artifacts({
        "ok": True,
        "regeneration_count": lm.regeneration_count if lm else 0,
        "map": payload,
        "markdown": md,
    }, material_id)


# --------------------- tools: notes regeneration ---------------------


@mcp.tool(
    description=(
        "Force or resume extraction of the per-section Markdown notes for a "
        "material. Equivalent to `prepare_material(..., scope='notes')` but "
        "named explicitly per the plan. With force=true, existing notes are "
        "overwritten; otherwise any already-extracted notes are kept."
    )
)
async def extract_notes_now(
    material_id: int, force: bool = False
) -> dict[str, Any]:
    db = _get_db()
    llm = _get_llm()
    report = await pipeline_prepare(db, llm, material_id, scope="notes", force=force)
    return _with_artifacts(report, material_id)


# --------------------- tools: completion reports ---------------------


@mcp.tool(
    description=(
        "Return the completion report for a section if one exists. Reports are "
        "generated automatically when the Anchor phase is completed via "
        "`complete_phase(section_id, 'anchor')`."
    )
)
def get_completion_report(section_id: int) -> dict[str, Any]:
    db = _get_db()
    report = db.get_completion_report(section_id)
    if report is None:
        payload = {
            "status": "pending",
            "resource_uri": f"completion-report://{section_id}",
        }
        return _compact_tool_result(
            [
                f"Completion report for section {section_id} is still pending.",
                "It is generated automatically after the Anchor phase completes.",
            ],
            payload,
            links=[
                _resource_link(
                    f"completion-report://{section_id}",
                    name="completion-report",
                    title="Completion report",
                    description="Full completion report resource for this section.",
                )
            ],
        )
    md, generated_at = report
    payload = {
        "status": "ready",
        "markdown": md,
        "generated_at": generated_at.isoformat() if generated_at else None,
        "resource_uri": f"completion-report://{section_id}",
    }
    return _compact_tool_result(
        [
            f"Completion report ready for section {section_id}.",
            f"Preview: {_preview_text(md, 320)}",
            "Full report is available via the resource link below.",
        ],
        payload,
        links=[
            _resource_link(
                f"completion-report://{section_id}",
                name="completion-report",
                title="Completion report",
                description="Full completion report resource for this section.",
            )
        ],
    )

@mcp.tool(
    description=(
        "Force regeneration of a completion report for a section. Uses the "
        "current phase responses, conversations, and committed flashcards."
    )
)
async def regenerate_completion_report(section_id: int) -> dict[str, Any]:
    db = _get_db()
    md = await svc_gen_completion(db, _get_llm(), section_id)
    return _with_artifacts(
        {"status": "ready", "markdown": md},
        _material_id_for_section(section_id),
    )


# --------------------- tools: exports ---------------------


@mcp.tool(
    description=(
        "Export flashcards to Anki. format='apkg' writes a double-clickable "
        "Anki package (stable deck id — re-exports merge). format='csv' writes "
        "a portable question/answer CSV (works with Quizlet, Remnote, etc.). "
        "Scope defaults to the whole material; pass section_id to limit."
    )
)
def export_anki(
    material_id: int,
    output_path: str,
    format: str = "apkg",
    section_id: int | None = None,
) -> dict[str, Any]:
    if format not in {"apkg", "csv"}:
        raise ValueError("format must be 'apkg' or 'csv'")
    db = _get_db()
    material = db.get_material(material_id)
    if material is None:
        raise KeyError(f"material {material_id} not found")

    cards = db.list_flashcards(material_id=material_id, section_id=section_id)
    payload = [{"question": c.question, "answer": c.answer} for c in cards]
    if not payload:
        return {"ok": False, "count": 0, "reason": "no flashcards to export"}

    out = Path(output_path).expanduser()
    if format == "apkg":
        count = svc_export_apkg(payload, material.title or f"material-{material_id}", out)
    else:
        count = svc_export_csv(payload, out)
    return {"ok": True, "count": count, "path": str(out.resolve())}


@mcp.tool(
    description=(
        "Export all section notes for a material as one combined Markdown file. "
        "Sections whose notes are still pending are included as placeholders."
    )
)
def export_notes(material_id: int, output_path: str) -> dict[str, Any]:
    out = Path(output_path).expanduser()
    count = svc_export_notes(_get_db(), material_id, out)
    return {"ok": True, "sections_with_notes": count, "path": str(out.resolve())}


@mcp.tool(
    description=(
        "Export learner-facing artifacts for a material. format='markdown' "
        "regenerates the readable Markdown mirror; format='json' writes "
        "structured JSON files under json/; format='all' writes both. JSON is "
        "never written by the automatic mirror."
    )
)
def export_material_artifacts(
    material_id: int,
    output_dir: str | None = None,
    format: str = "markdown",
) -> dict[str, Any]:
    return svc_export_material_artifacts(
        _get_db(),
        material_id,
        output_dir=output_dir,
        format=format,
    )


# --------------------- tools: library + progress ---------------------


@mcp.tool(
    description=(
        "Progress stats for one material: sections done, flashcards total/due/"
        "mastered, last-activity timestamp, learning-map readiness."
    )
)
def material_progress(material_id: int) -> dict[str, Any]:
    return svc_material_progress(_get_db(), material_id)


@mcp.tool(
    description=(
        "Cross-library progress dashboard: totals plus per-material stats. "
        "Useful for 'what should I study today' sessions."
    )
)
def library_dashboard() -> dict[str, Any]:
    return svc_library_progress(_get_db())


# --------------------- tools: background preparation ---------------------


@mcp.tool(
    description=(
        "Kick off prepare_material as a background task inside the server so "
        "the learner can study earlier sections while later ones are still being "
        "processed. Single-flight per material — calling while running returns "
        "'already_running'. Poll via `get_background_status`."
    )
)
def start_background_preparation(
    material_id: int, scope: str = "all", force: bool = False
) -> dict[str, Any]:
    result = _background_start(_get_db(), _get_llm(), material_id, scope=scope, force=force)
    return _with_artifacts(result, material_id)


@mcp.tool(
    description=(
        "Status of the background preparation task for a material: 'idle', "
        "'running', 'finished', or 'error'. Read-only."
    )
)
def get_background_status(material_id: int) -> dict[str, Any]:
    return _background_status(material_id)


# --------------------- resources ---------------------


@mcp.resource("material://{material_id}")
def resource_material(material_id: str) -> str:
    db = _get_db()
    m = db.get_material(int(material_id))
    if m is None:
        return json.dumps({"error": f"material {material_id} not found"})
    payload = {
        **_material_brief(m),
        "ingestion_status": m.ingestion_status,
        "sections": [_section_brief(s) for s in db.get_sections(m.id)],
    }
    return json.dumps(payload, indent=2)


@mcp.resource("learning_map://{material_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_learning_map(material_id: str) -> str:
    lm = _get_db().get_learning_map(int(material_id))
    if lm is None:
        return "# Learning map\n\n_Pending — call `prepare_material` to generate._\n"
    return lm.map_markdown


@mcp.resource("learning-map://{material_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_learning_map_alias(material_id: str) -> str:
    return resource_learning_map(material_id)


@mcp.resource("focus_brief://{section_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_focus_brief(section_id: str) -> str:
    s = _get_db().get_section(int(section_id))
    if s is None or s.focus_brief is None:
        return "# Focus brief\n\n_Pending._\n"
    return render_focus_brief_markdown(
        s.focus_brief,
        s.order_index,
        s.title,
        language_code=_source_language_code(s.material_id),
    )


@mcp.resource("focus-brief://{section_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_focus_brief_alias(section_id: str) -> str:
    return resource_focus_brief(section_id)


@mcp.resource("notes://{material_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_notes_material(material_id: str) -> str:
    mid = int(material_id)
    sections = _get_db().get_sections(mid)
    parts = [
        f"# §{s.order_index}: {s.title or '(untitled)'}\n\n{s.notes}"
        for s in sections
        if s.notes
    ]
    if not parts:
        return "# Notes\n\n_Pending — call `prepare_material` to generate._\n"
    return "\n\n---\n\n".join(parts)


@mcp.resource("notes://{material_id}/{section_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_notes_section(material_id: str, section_id: str) -> str:
    mid = int(material_id)
    s = _get_db().get_section(int(section_id))
    if s is None or s.material_id != mid:
        return f"# Notes\n\n_Section {section_id} does not belong to material {material_id}._\n"
    if s.notes is None:
        return "# Notes\n\n_Pending._\n"
    return s.notes


@mcp.resource("section://{section_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_section(section_id: str) -> str:
    s = _get_db().get_section(int(section_id))
    if s is None:
        return f"# Section\n\n_Section {section_id} not found._\n"
    title = s.title or "(untitled)"
    return f"# §{s.order_index}: {title}\n\n{s.content}"


@mcp.resource("section_state://{section_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_section_state(section_id: str) -> str:
    s = _get_db().get_section(int(section_id))
    if s is None:
        return json.dumps({"error": f"section {section_id} not found"})
    return json.dumps(
        {
            **_section_brief(s),
            "rolling_summary": s.rolling_summary,
            "phase_data": s.phase_data,
        },
        indent=2,
    )


@mcp.resource("section-state://{section_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_section_state_alias(section_id: str) -> str:
    return resource_section_state(section_id)


@mcp.resource("completion_report://{section_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_completion_report(section_id: str) -> str:
    report = _get_db().get_completion_report(int(section_id))
    if report is None:
        return "# Completion report\n\n_Pending — complete the Anchor phase to generate._\n"
    md, _ = report
    return md


@mcp.resource("completion-report://{section_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_completion_report_alias(section_id: str) -> str:
    return resource_completion_report(section_id)


@mcp.resource("library://")
def resource_library() -> str:
    return json.dumps(svc_library_progress(_get_db()), indent=2)


# --------------------- tools: prerequisites (v2) ---------------------


@mcp.tool(
    description=(
        "Check whether the learner is ready to start a section based on the "
        "learning map's key-concept dependencies. Returns a verdict ('ready', "
        "'review_recommended', 'review_required'), the prerequisite sections, "
        "and any due flashcards from those sections to review first."
    )
)
def check_prerequisites(section_id: int) -> dict[str, Any]:
    return svc_check_prereqs(_get_db(), section_id)


# --------------------- tools: study plan (v2) ---------------------


@mcp.tool(
    description=(
        "Build a calendar-aware study plan for a material. Uses the learning "
        "map's suggested_path and focus briefs' time estimates. Skips sections "
        "already completed. start_date defaults to today (YYYY-MM-DD). "
        "days_per_week: 1..7. minutes_per_session: >= 10."
    )
)
def plan_study(
    material_id: int,
    start_date: str | None = None,
    days_per_week: int = 5,
    minutes_per_session: int = 45,
) -> dict[str, Any]:
    from datetime import date as _date

    start = _date.fromisoformat(start_date) if start_date else None
    plan = svc_plan_study(
        _get_db(),
        material_id,
        start_date=start,
        days_per_week=days_per_week,
        minutes_per_session=minutes_per_session,
    )
    return _with_artifacts(plan, material_id, study_plan=plan)


# --------------------- tools: streak + weekly report (v2) ---------------------


@mcp.tool(
    description=(
        "Current and longest consecutive-day study streaks across the whole "
        "library. An 'active day' means any phase update, section completion, "
        "or flashcard creation on that UTC day."
    )
)
def study_streak() -> dict[str, Any]:
    return svc_compute_streak(_get_db())


@mcp.tool(
    description=(
        "Markdown weekly report: the last 7 UTC days of activity across the "
        "library. Returns both structured totals and a rendered markdown view."
    )
)
def weekly_report() -> dict[str, Any]:
    rep = svc_weekly_report(_get_db())
    rep["markdown"] = svc_render_weekly_md(rep)
    return rep


# --------------------- tools: phase evaluation (v2) ---------------------


@mcp.tool(
    description=(
        "Opt-in server-side evaluation of a phase response. Returns a "
        "structured analysis (strengths, gaps, misconceptions, suggested "
        "followups, verdict) with `[§N]` citations. Persists to evaluations "
        "table so the learner can revisit past feedback. This is complementary "
        "to host coaching, not a replacement — the host is still the primary "
        "tutor; this is structured after-action review."
    )
)
async def evaluate_phase_response(
    section_id: int, phase: str, response: str | None = None
) -> dict[str, Any]:
    result = await svc_evaluate_phase(_get_db(), _get_llm(), section_id, phase, response)
    return _with_artifacts(result, _material_id_for_section(section_id))


@mcp.tool(
    description=(
        "List past evaluations for a section, optionally filtered by phase. "
        "Most recent first."
    )
)
def list_evaluations(
    section_id: int, phase: str | None = None
) -> list[dict[str, Any]]:
    return _get_db().list_evaluations(section_id, phase=phase)


# --------------------- tools: portable export/import (v2) ---------------------


@mcp.tool(
    description=(
        "Export a material as a portable JSON file: sections, notes, focus "
        "briefs, phase data, rolling summaries, learning map, flashcards with "
        "SM-2 state, completion reports, and evaluations. Use with "
        "import_project to back up or migrate between machines."
    )
)
def export_project(material_id: int, output_path: str) -> dict[str, Any]:
    out = Path(output_path).expanduser()
    return svc_export_project(_get_db(), material_id, out)


@mcp.tool(
    description=(
        "Import a project JSON file exported by export_project. Creates a "
        "fresh material_id. Raises if a material with the same content_hash "
        "already exists — delete it first to re-import."
    )
)
def import_project(input_path: str) -> dict[str, Any]:
    path = Path(input_path).expanduser()
    result = svc_import_project(_get_db(), path)
    return _with_artifacts(result, result["material_id"])


@mcp.resource("review://due")
def resource_flashcards_due() -> str:
    """Global review queue: every non-mastered card whose next_review has passed.

    The plan named this `flashcards_due://` but underscores are not legal in
    URI scheme names (RFC 3986) and the MCP SDK rejects them at validation
    time. Using `review://due` instead — same semantics, future-proof for
    `review://mastered`, `review://all` siblings.
    """
    due = _get_db().list_flashcards(filter_="due")
    return json.dumps(
        [
            {
                "flashcard_id": c.id,
                "material_id": c.material_id,
                "section_id": c.section_id,
                "question": c.question,
                "answer": c.answer,
                "next_review": c.next_review.isoformat() if c.next_review else None,
                "ease_factor": round(c.ease_factor, 3),
                "interval_days": c.interval_days,
                "review_count": c.review_count,
            }
            for c in due
        ],
        indent=2,
    )


@mcp.resource("streak://")
def resource_streak() -> str:
    return json.dumps(svc_compute_streak(_get_db()), indent=2)


@mcp.resource("report://weekly")
def resource_weekly_report() -> str:
    rep = svc_weekly_report(_get_db())
    return svc_render_weekly_md(rep)


@mcp.resource("evaluations://{section_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_evaluations(section_id: str) -> str:
    return json.dumps(_get_db().list_evaluations(int(section_id)), indent=2)


@mcp.resource("plan://{material_id}", annotations=HEAVY_RESOURCE_ANNOTATIONS)
def resource_study_plan(material_id: str) -> str:
    return json.dumps(svc_plan_study(_get_db(), int(material_id)), indent=2)


# --------------------- prompts: phase coaching ---------------------


def _load_phase_context(section_id: int) -> dict[str, Any]:
    db = _get_db()
    s = db.get_section(section_id)
    if s is None:
        raise KeyError(f"section {section_id} not found")

    ctx: dict[str, Any] = {
        "section_ref": _section_ref(s),
        "content": s.content,
        "focus_brief_md": (
            render_focus_brief_markdown(
                s.focus_brief,
                s.order_index,
                s.title,
                language_code=_source_language_code(s.material_id),
            )
            if s.focus_brief
            else None
        ),
        "rolling_summary": s.rolling_summary,
        "prior_responses": {},
    }
    for p in PHASES:
        pd = (s.phase_data or {}).get(p, {}) or {}
        if pd.get("response"):
            ctx["prior_responses"][p] = pd["response"]
    return ctx


def _format_phase_user(phase: str, ctx: dict[str, Any]) -> str:
    parts: list[str] = [f"# {ctx['section_ref']}"]

    if ctx.get("focus_brief_md"):
        parts.append("\n## Pre-study focus brief\n")
        parts.append(ctx["focus_brief_md"])

    if ctx.get("rolling_summary"):
        parts.append("\n## Previous sections (rolling summary)\n")
        parts.append(ctx["rolling_summary"])

    parts.append("\n## Section content\n")
    parts.append(ctx["content"][:6000] + ("..." if len(ctx["content"]) > 6000 else ""))

    prior = ctx.get("prior_responses", {})
    if prior:
        parts.append("\n## Learner's recorded responses so far\n")
        for p in PHASES:
            if p in prior:
                parts.append(f"\n### {p.capitalize()}\n{prior[p]}")

    footer = {
        "preview": (
            "\n\n## Your task\n\nCoach the learner through the PREVIEW phase. Ask what "
            "stands out, what they already know, and what they expect to find. Brief, "
            "warm, curious. When ready, instruct the host to call `record_phase_response"
            "(section_id, 'preview', response=...)` and `complete_phase(section_id, 'preview')`."
        ),
        "explain": (
            "\n\n## Your task\n\nCoach the learner through the EXPLAIN phase. Ask them "
            "to teach the material back in their own words. Push gently where the "
            "explanation is borrowed or vague. Do NOT re-teach — elicit. When the "
            "explanation is solid, instruct the host to call `record_phase_response` "
            "with the final form and `complete_phase`."
        ),
        "question": (
            "\n\n## Your task\n\nCoach the learner through the QUESTION phase. Probe "
            "assumptions, elicit connections to prior knowledge, surface edge cases. "
            "Ask 'why', 'how does this connect', 'where does this break'. Save the "
            "learner's articulated questions + connections with `record_phase_response` "
            "and `complete_phase` when ready."
        ),
        "anchor": (
            "\n\n## Your task\n\nCoach the learner through the ANCHOR phase. Call "
            "`suggest_flashcards(section_id, n=3)` to get AI candidates. Present them, "
            "invite the learner to accept / edit / reject each. For each accepted card, "
            "call `add_flashcard`. When done, `complete_phase(section_id, 'anchor')`."
        ),
    }
    parts.append(footer[phase])
    return "\n".join(parts)


@mcp.prompt(
    name="preview",
    description="Coach the learner through the Preview phase of a section.",
)
def prompt_preview(section_id: int) -> list[Message]:
    ctx = _load_phase_context(section_id)
    body = _format_phase_user("preview", ctx)
    return [UserMessage(PREVIEW_COACH_SYSTEM + "\n\n" + body)]


@mcp.prompt(
    name="explain",
    description="Coach the learner through the Explain phase (Feynman technique).",
)
def prompt_explain(section_id: int) -> list[Message]:
    ctx = _load_phase_context(section_id)
    body = _format_phase_user("explain", ctx)
    return [UserMessage(EXPLAIN_COACH_SYSTEM + "\n\n" + body)]


@mcp.prompt(
    name="question",
    description="Coach the learner through the Question phase (elaborative interrogation).",
)
def prompt_question(section_id: int) -> list[Message]:
    ctx = _load_phase_context(section_id)
    body = _format_phase_user("question", ctx)
    return [UserMessage(QUESTION_COACH_SYSTEM + "\n\n" + body)]


@mcp.prompt(
    name="anchor",
    description="Coach the learner through the Anchor phase (flashcard creation + SM-2).",
)
def prompt_anchor(section_id: int) -> list[Message]:
    ctx = _load_phase_context(section_id)
    body = _format_phase_user("anchor", ctx)
    return [UserMessage(ANCHOR_COACH_SYSTEM + "\n\n" + body)]


# --------------------- get_phase_prompt (tool variant) ---------------------


@mcp.tool(
    description=(
        "Return the pre-filled coaching prompt text for a phase, as a plain "
        "system+user pair. Useful when the host can't invoke MCP prompts "
        "directly but can pass text into a conversation. Prefer the native "
        "prompt (`preview`, `explain`, `question`, `anchor`) when available."
    )
)
def get_phase_prompt(section_id: int, phase: str) -> dict[str, str]:
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    ctx = _load_phase_context(section_id)
    body = _format_phase_user(phase, ctx)
    systems = {
        "preview": PREVIEW_COACH_SYSTEM,
        "explain": EXPLAIN_COACH_SYSTEM,
        "question": QUESTION_COACH_SYSTEM,
        "anchor": ANCHOR_COACH_SYSTEM,
    }
    return {"system": systems[phase], "user": body}


# --------------------- entry point ---------------------

def _preload_markitdown_enabled() -> bool:
    return os.environ.get("LEARNERS_MCP_PRELOAD_MARKITDOWN", "").lower() not in {
        "0",
        "false",
        "off",
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("learners-mcp %s starting (stdio)", __version__)
    if _preload_markitdown_enabled():
        try:
            loader_preload_markitdown()
            log.info("MarkItDown preloaded")
        except Exception as exc:
            log.warning("MarkItDown preload failed: %s", exc)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
