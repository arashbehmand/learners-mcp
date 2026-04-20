# learners-mcp

MCP server that turns any source material into a guided learning experience:

- **Orientation** — material-level learning map + per-section focus briefs; cross-material concept linking.
- **Notes** — handwritten-style Markdown notes extracted via a map → reduce → consistency pipeline.
- **Four-phase study loop** — Preview → Explain → Question → Anchor (soft-guidance, not locked).
- **Flashcards** — SM-2 spaced repetition; no duplicates.
- **Grounded Q&A** — ad-hoc questions against the material with `[§N]` citations.
- **Prerequisite checks** — before a section, surface unmastered cards from sections its key concepts build on.
- **Study plans** — calendar-aware schedule using focus-brief time estimates + suggested path.
- **Streak + weekly report** — activity roll-ups across the library.
- **Phase evaluation** — opt-in structured assessment of a phase response (strengths, gaps, misconceptions, follow-ups).
- **Exports** — Anki `.apkg`, CSV, combined Markdown notes, portable project JSON (full round-trip).

Host-agnostic: works with any MCP-capable agent (Claude Desktop, Claude Code, Codex, Gemini, Cursor, Zed, Continue, etc.).

## Install

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
```

## Register with a host

**Claude Desktop** — `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "learners": {
      "command": "learners-mcp",
      "env": { "ANTHROPIC_API_KEY": "sk-ant-..." }
    }
  }
}
```

**Claude Code** — `.mcp.json` in the project or `~/.claude.json`:
```json
{ "mcpServers": { "learners": { "command": "learners-mcp" } } }
```

Other MCP hosts (Codex, Gemini CLI, Cursor, Zed, Continue) follow the same `command + env` pattern — consult their docs for the exact config file.

## Surface

- **Tools** (37): ingestion/prep (`ingest_material`, `prepare_material`, `get_preparation_status`, `start_background_preparation`, `get_background_status`); orientation (`get_material_map`, `regenerate_map`, `get_focus_brief`); notes (`get_notes`, `extract_notes_now`); library (`list_sections`, `list_materials`, `material_progress`, `library_dashboard`); study loop (`start_section`, `get_phase_prompt`, `record_phase_response`, `complete_phase`, `check_prerequisites`, `plan_study`, `study_streak`, `weekly_report`); evaluation (`evaluate_phase_response`, `list_evaluations`); flashcards (`suggest_flashcards`, `add_flashcard`, `list_flashcards`, `review_flashcard`, `next_due`); ad-hoc (`answer_from_material`, `recommend_next_action`); completion (`get_completion_report`, `regenerate_completion_report`); exports (`export_anki`, `export_notes`, `export_project`, `import_project`).
- **Resource templates**: `material://{id}`, `learning_map://{id}`, `focus_brief://{section_id}`, `notes://{id}`, `notes://{id}/{section_id}`, `section_state://{section_id}`, `completion_report://{section_id}`, `evaluations://{section_id}`, `plan://{material_id}`.
- **Concrete resources**: `library://`, `review://due`, `streak://`, `report://weekly`.
- **Prompts**: `preview`, `explain`, `question`, `anchor` (phase-coaching prompts the host agent executes).


## Typical flow

1. `ingest_material("/path/to/book.pdf")` → returns `material_id`.
2. `start_background_preparation(material_id)` → learning map + focus briefs + notes generate asynchronously.
3. `get_material_map(material_id)` once map is ready → orient the learner.
4. `start_section(section_id)` → content + focus brief + phase state.
5. For each phase (`preview` → `explain` → `question` → `anchor`): host invokes the matching prompt, learner responds, server records via `record_phase_response` + `complete_phase`.
6. In Anchor: `suggest_flashcards` → `add_flashcard` × N. `complete_phase(section_id, 'anchor')` triggers a completion report.
7. Later: `next_due(material_id)` for review sessions; `review_flashcard(id, knew_it)` to grade.

## State

SQLite DB + artifacts live in `~/.learners-mcp/`. Override with `LEARNERS_MCP_DATA_DIR`. Delete the directory to start fresh.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
