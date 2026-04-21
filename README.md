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
- **Exports** — auto-written learner Markdown mirror, Anki `.apkg`, CSV, combined Markdown notes, explicit JSON artifact export, portable project JSON (full round-trip).

Host-agnostic: works with any MCP-capable agent (Claude Desktop, Claude Code, Codex, Gemini, Cursor, Zed, Continue, etc.).

## Configuring models

By default learners-mcp uses Anthropic models (haiku for fast tasks, sonnet for most work, opus for the learning map). Copy `examples/llm.yaml` to `~/.learners-mcp/llm.yaml` to change models, providers, or per-task routing.

### YAML structure

```yaml
profiles:
  default:
    model: openrouter/anthropic/claude-sonnet-4.6
    params:
      reasoning_effort: low
    prompt_cache: auto  # auto|on|off

routes:
  qa: default
  learning_map: oneshot
  # ... (11 tasks total — see examples/llm.yaml for full list)
```

### Supported providers

Any provider supported by [LiteLLM](https://docs.litellm.ai/docs/providers): Anthropic, OpenRouter, OpenAI, Gemini, Bedrock, Vertex, and custom OpenAI-compatible endpoints.

Set the matching API key in your environment — LiteLLM reads them automatically:
`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`.

### The 11 tasks and default profiles

| Task | Default profile | Used for |
|------|----------------|----------|
| `notes_map`, `notes_tldr`, `focus_brief` | `fast` (haiku) | Per-chunk work, high volume |
| `notes_reduce`, `notes_polish`, `rolling_summary`, `qa`, `phase_evaluation`, `completion_report`, `flashcards` | `default` (sonnet) | Most analytic work |
| `learning_map` | `oneshot` (opus) | Material-level orientation, one call |

### Env overrides

Override without editing the YAML:
- `LEARNERS_MCP_MODEL_DEFAULT=gpt-4o-mini` — change the model for a profile
- `LEARNERS_MCP_PARAMS_DEFAULT='{"reasoning_effort":"low"}'` — change params (JSON)
- `LEARNERS_MCP_ROUTE_QA=fast` — re-route a task to a different profile
- `LEARNERS_MCP_LLM_CONFIG=/path/to/llm.yaml` — use a custom config path

### Prompt caching

For Anthropic-family models (including via OpenRouter), `cache_control` blocks are preserved and caching applies automatically. Non-Anthropic models (OpenAI, Gemini, etc.) use flat text — no block-level caching, so map-reduce pipelines cost more. Set `prompt_cache: on` to force pass-through if you know your proxy supports it.

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

- **Tools** (38): ingestion/prep (`ingest_material`, `prepare_material`, `get_preparation_status`, `start_background_preparation`, `get_background_status`); orientation (`get_material_map`, `regenerate_map`, `get_focus_brief`); notes (`get_notes`, `extract_notes_now`); library (`list_sections`, `list_materials`, `material_progress`, `library_dashboard`); study loop (`start_section`, `get_phase_prompt`, `record_phase_response`, `complete_phase`, `check_prerequisites`, `plan_study`, `study_streak`, `weekly_report`); evaluation (`evaluate_phase_response`, `list_evaluations`); flashcards (`suggest_flashcards`, `add_flashcard`, `list_flashcards`, `review_flashcard`, `next_due`); ad-hoc (`answer_from_material`, `recommend_next_action`); completion (`get_completion_report`, `regenerate_completion_report`); exports (`export_anki`, `export_notes`, `export_material_artifacts`, `export_project`, `import_project`).
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

## Learner artifacts

Generated study material is mirrored as readable Markdown in `./learners/<material-slug>/` by default. The SQLite DB remains the canonical state, but the learner can open files such as `learning-map.md`, `focus-briefs.md`, `notes.md`, `flashcards.md`, and `progress.md` directly from the working directory.

Set `LEARNERS_MCP_ARTIFACT_DIR=/path/to/dir` to write the mirror somewhere else, or `LEARNERS_MCP_ARTIFACT_MIRROR=off` to disable automatic Markdown writes. JSON artifacts are explicit only: call `export_material_artifacts(material_id, format="json")` or `format="all"` to write structured files under `json/`.

## State

SQLite DB + server config live in `~/.learners-mcp/`. Override with `LEARNERS_MCP_DATA_DIR`. Delete the directory to start fresh.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
