# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`learners-mcp` is a FastMCP (stdio) server that turns source material (PDF/EPUB/DOCX/TXT, URLs, YouTube) into a guided learning experience. Host-agnostic — works with any MCP-capable agent (Claude Desktop, Claude Code, Codex, Gemini, Cursor, Zed, Continue, etc.).

**Ship state:** v2 shipped. 37 tools, 9 resource templates, 4 concrete resources, 4 phase-coaching prompts, SM-2 flashcards with real review events, portable JSON export/import, calendar-aware study plans, prerequisite checks, streak + weekly reports, cross-material concept linking, server-side phase evaluation. ~100 tests, all green.

## Commands

```bash
pip install -e ".[dev]"    # install for development
pytest                     # full suite (fast; LLM is stubbed)
pytest tests/test_sm2.py   # single file
pytest -k "rolling"        # substring match
pytest -x -vv              # first failure, verbose
learners-mcp               # run the server over stdio (needs ANTHROPIC_API_KEY)
```

No linter/formatter is configured; match surrounding style. Python ≥3.10.

## Required environment

- `ANTHROPIC_API_KEY` — required for any tool that does batch LLM work (ingestion, orientation, notes, flashcard suggestions, QA, evaluation). Tests stub the LLM; production does not.
- `LEARNERS_MCP_DATA_DIR` — optional override for the SQLite DB + artifact directory (default `~/.learners-mcp/`). Read *on every call* via `config.data_dir()` — never cached at import. Do not re-introduce module-level constants that read `os.environ`.

---

## Architecture

### Layered module split
- `server.py` — thin FastMCP app; tool bodies are one-liners delegating to services. Target: handlers stay ≈1 line. Under 1100 lines total despite 37 tools.
- `tools/` package exists but is effectively empty — **all tools live on `server.py` directly**. Don't add a new `tools/*.py` file expecting it to register anything.
- `study/`, `orientation/`, `notes/`, `flashcards/`, `export/`, `ingestion/` — fat service modules. New logic belongs here, not in `server.py`.
- `llm/client.py` + `llm/prompts.py` — the only LiteLLM call sites and the only place prompt strings live. Do not add `litellm.*` imports elsewhere.
- `db.py` — raw `sqlite3` + dataclass rows. No ORM. Schema is `CREATE TABLE IF NOT EXISTS`; there is **no migration runner**, so adding columns to existing tables on an existing install silently no-ops until the DB is deleted.
- `config.py` — all paths/models resolved via **functions, not constants**.

### The four-phase study loop — Preview → Explain → Question → Anchor
- **Preview** — survey the material, surface prior knowledge, set first impressions (SQ3R "Survey").
- **Explain** — reproduce the content in your own words, as if teaching (Feynman).
- **Question** — probe, interrogate, connect to other knowledge (elaborative interrogation).
- **Anchor** — flashcards + SM-2 spaced review (retrieval practice).

- Phase names are always `preview | explain | question | anchor`. Never PECS/PEQA-legacy names (`prime`, `engage`, `challenge`, `solidify`) — those were explicitly replaced.
- `study/phases.py` defines the ordering and **soft-guidance** state machine. Out-of-order `record_phase_response` emits a warning but still persists. **Do not add hard locks** — chat UX with hard locks feels patronising.
- In-chat coaching runs on the **host agent** via the four MCP Prompt definitions (`preview_prompt` / `explain_prompt` / `question_prompt` / `anchor_prompt`). The server makes **no LLM calls during chat** — only for batch work.

### LLM strategy (cost-tuned stratification)

| Task | Profile | Caching |
|------|---------|---------|
| Per-chunk map / TLDR | `fast` | within-chunk (Anthropic family) |
| Section focus brief | `fast` | shares cache with material map |
| Reduce / consistency pass | `default` | cache notes blob |
| Rolling summary per section | `default` | cache prior summary |
| Flashcard suggestions | `default` | cache context |
| Completion report | `default` | cache |
| QA (`answer_from_material`) | `default` | cache |
| Phase evaluation | `default` | cache |
| Material-level learning map | `oneshot` — **one-shot only** | cache source |
| In-chat phase coaching | **host agent** (not our key) | n/a |

- Prompt caching (`cache_control: {"type": "ephemeral"}`, 5-min TTL) is the biggest cost lever. `llm/client.py` exposes `cached_source()` / `plain()` helpers — use `cached_source` for any block that will be reused within 5 minutes.
- **Cross-chunk caching does not work** (disjoint content, TTL); within-chunk cache (map call writes, TLDR call immediately reads) is where the real savings happen.
- **Cross-call caching does work** on the orientation phase because source text is identical across one material map + N focus briefs fired inside the 5-min window.
- **Never loop oneshot.** Single call only.
- Full ingestion of a 300-page book runs ~$4–5. Overruns above $10 mean a prompt is using the wrong model.
- The LiteLLM client has a basic exponential-backoff retry; don't assume a single call succeeds.
- `complete_json` extracts the first balanced `{...}` block and tolerates ```` ```json ```` fences — don't trust raw `json.loads`.

### Grounding contract — `[§N]` citations
Every AI-generated artifact (learning map, notes, completion reports, flashcard answers, QA answers, evaluation reports) must cite source sections as `[§N]` or `[§N: Title]`. Sections are **1-based** (the `order_index` column, set at ingest as `i+1`). Beware `order_index + 1` anywhere in the code — that pattern caused a real grounding bug (rolling summaries cited §2 for the first section). Today that pattern is zero; keep it that way.

### Idempotency invariants
- `ingest_material` dedupes on `content_hash` of the source text. Re-ingesting the same file returns the existing `material_id`. MCP hosts retry tool calls on timeout — this matters.
- `prepare_material(scope='all|map|focus_briefs|notes', force=false)` is idempotent and resumable — each call picks up unfinished artifacts and skips completed ones. Host can call repeatedly between study actions.
- Every handler that writes must be safe to retry.

### Event tables vs state snapshots
- `review_events` records each flashcard review; `flashcards` holds current SM-2 state. These are **separate intentionally**.
- `flashcards/service.record_review()` updates state **and** logs an event. `flashcards/sm2.apply_review()` only updates state. Import paths use `apply_review`; user-driven reviews use `record_review`. Don't collapse them — that's how the "lifetime reviews masquerading as weekly reviews" proxy-metric bug crept in.
- Portable export (`export/portable.py`) round-trips `review_events`. Any new event-backed feature must extend that round-trip or streak/weekly history silently vanishes on import.

### Resource URI scheme rules (Pydantic RFC 3986)
MCP URIs go through Pydantic `AnyUrl`. **Scheme names cannot contain underscores** — use `review://due`, `report://weekly`, `plan://{id}`, not `flashcards_due://` or `weekly_report://`. Every scheme must match `[A-Za-z][A-Za-z0-9+\-.]*`. This rejection has hit twice; lint scheme names up-front, not at wire time.

- `@mcp.resource("foo://{id}")` registers a **template** (in `list_resource_templates()`).
- `@mcp.resource("foo://")` or `@mcp.resource("foo://bar")` registers a **concrete resource** (in `list_resources()`).
- Smoke tests must check both endpoints.

### FastMCP async boundary
FastMCP runs sync tools via `asyncio.to_thread`, so `asyncio.create_task` inside a sync handler fails with "no running event loop." Tools that kick off background work (e.g. `ingest_material` with `auto_prepare`, `start_background_preparation`) must be `async def`. Wrap any sync loader calls inside with `asyncio.to_thread`.

### `recommend_next_action` ↔ tool-name coupling
Every `action` string returned by `recommend_next_action` must exactly match an actual tool name on the surface. `test_regressions_v1_audit.py` enforces this. When there's no map yet, emit `prepare_material`, not `regenerate_map` (a real bug that made hosts dead-end).

### `scope` / `mode` arguments must validate, not silently widen
Functions with a `scope` or `mode` arg whose narrower mode has a required-argument dependency must `raise ValueError` when the dependency is missing — do not silently widen. `answer_from_material(scope='section')` without `section_id` used to silently query the whole material; don't reintroduce that pattern.

---

## Data model

SQLite at `~/.learners-mcp/db.sqlite` (override via `LEARNERS_MCP_DATA_DIR`).

```sql
materials(id, title, source_type, source_ref, content_hash UNIQUE, created_at, ingestion_status)
sections(id, material_id FK, title, content, order_index, rolling_summary,
         current_phase DEFAULT 'preview', phase_data JSON, notes, focus_brief, completed_at)
learning_maps(material_id PK/FK, map_json, map_markdown, generated_at, regeneration_count)
flashcards(id, material_id FK, section_id FK, question, answer,
           ease_factor DEFAULT 2.5, interval_days, review_count, next_review, is_mastered, created_at)
completion_reports(section_id PK/FK, report_markdown, generated_at)
evaluations(id, section_id FK, phase, ...)             -- many per section per phase (v2)
review_events(id, flashcard_id FK, ...)                -- many per card (v2-audit fix)

INDEX idx_sections_material ON sections(material_id, order_index)
INDEX idx_flashcards_due    ON flashcards(next_review) WHERE is_mastered = 0
```

- `phase_data` is a single JSON column keyed by phase name. Don't normalize — the blob is always read whole and "all preview responses across materials" is not a query we need.
- Each analytic table earned its keep; cramming evaluations or review events into JSON blobs would have made them unqueryable.
- `content_hash` is the dedupe key, not the title. Two files with the same title are different materials; two identical files with different titles are the same material.

---

## Learning-map JSON shapes (distinctive IP)

### Material-level map (oneshot profile, one call)
```json
{
  "objectives": ["...", "..."],                 // 3–7 "after this you should be able to..."
  "key_concepts": [
    {"name": "Entropy", "why_load_bearing": "...", "difficulty": "hard", "sections": [3,4,7]}
  ],
  "prerequisites": ["basic probability", "..."],
  "common_pitfalls": ["conflating X and Y because...", "..."],
  "suggested_path": [
    {"section_ids": [1,2], "note": "read straight through"},
    {"section_ids": [3],   "note": "spend extra time — foundational"}
  ],
  "time_estimate_hours": 12,
  "difficulty": "intermediate"
}
```

### Section-level focus brief (fast profile, per section)
```json
{
  "focus": "The core thing to take away is...",
  "key_terms": [{"term": "...", "gloss": "..."}],
  "watch_for": ["..."],                          // pitfalls specific to this section
  "connects_to": ["concept X from §2", "..."],
  "estimated_minutes": 25
}
```

Surfaced *before* the Preview phase. Prompts explicitly instruct the model to output **what to focus on**, not a summary — summaries are what notes are for.

---

## Recurring bug patterns (lint targets)

Three audit cycles found 12 real bugs across 91 tests that all passed while the bugs were present. Assume every "done" phase has ~4 bugs waiting. Treat each pattern below as a lint target for future work.

1. **Off-by-one in 1-based indices.** `order_index + 1` anywhere in the codebase is suspicious. Sections are 1-based at persistence time.
2. **Module-level constants frozen at import.** Any module-level expression that reads `os.environ` should be a function, not a constant. We already have `data_dir()`, `db_path()`, `anthropic_api_key()`. Resist re-caching.
3. **Silent fallback to wider scope.** Functions with `scope`/`mode` args whose narrower mode has required deps must `raise ValueError`, not quietly widen. Don't "be helpful."
4. **Documentation drift (comments that lie).** When a docstring makes a *specific* behavioural claim (caching on, summary included, review events tracked, review scheduling present), add a test that would fail if the claim were false. Comments are not load-bearing; tests are.
5. **URI scheme validation.** No underscores in scheme names. Use path components (`review://due`) for sub-kinds.
6. **Action-name / tool-name mismatch.** Every action string in `recommend_next_action` must be an actual tool. Subset test enforces this.
7. **Test order dependency.** Avoid `importlib.reload(server_mod)` — it interacts badly with module-level state. Prefer per-test `tmp_path`-scoped DBs. When a singleton cache is unavoidable, tag the instance itself (pattern: `_db._built_for_env_key`) so external `setattr` can't desync.
8. **"SM-2 seed" vs "real review" conflation.** `apply_review` = pure SM-2 math; `record_review` = update + log event. Import uses the first, user reviews the second. Name intent.
9. **Proxy metrics masquerading as real metrics.** If a field is a proxy, the name must say so (or a docstring + test must, with coverage of a case where proxy ≠ truth). Better: introduce the real event table up front.
10. **Reasoning content leaking to learner output.** `_extract_text` in `llm/client.py` must drop `thinking`/`reasoning_content` blocks and strip `<think>` tags. If a provider starts returning chain-of-thought, learners must not see it. `test_llm_reasoning_filter.py` is the canary.

---

## Testing patterns

### What works
- **Per-test `DB(tmp_path / "t.sqlite")`** — zero state leakage.
- **Capture LLM `blocks` with `AsyncMock` + closure-captured dict** and assert on the prompt text:
  ```python
  captured: dict = {}
  async def _complete(**kwargs): captured.update(kwargs); return "stub"
  ```
  This is the single highest-leverage pattern — it caught QA ignoring rolling_summary, notes extractor not using `cache_control`, rolling summary §N off-by-one, evaluation context missing learner response. Every prompt-generating function deserves one.
- **Per-test `monkeypatch.setattr(server, "_db", iso)`** when testing server entry points with an isolated DB.
- **Split "SM-2 math" tests from "DB integration" tests.** Pure `flashcards.sm2.review()` tests don't need SQLite.

### What causes pain (avoid)
- `importlib.reload(server_mod)` — see bug pattern #7.
- Assuming `~/.learners-mcp/` exists — tests pass locally, fail on clean clones.
- Module-order-dependent env overrides — `monkeypatch.setenv` after `from x import CONST` doesn't propagate.

### Regression discipline
Regression tests are organized by audit cycle: `test_regressions_v{0,1,2}_audit.py`. When you fix an audit-class bug, add to the matching file.

### Standing residual risk
**End-to-end stdio + fake-LLM test is missing.** `test_server_smoke.py` only checks schema shapes — not real MCP protocol negotiation, capability exchange, or a full study flow through stdio. Three audits flagged this. It's the single most valuable test we don't have.

---

## MCP / SDK specifics

- `UserMessage("plain text")` auto-wraps the string in `TextContent` — no need to construct blocks manually.
- Prompt decorators derive `PromptArgument` entries from the Python signature. `section_id` as a positional arg becomes a required prompt arg. Smoke-test every prompt has the expected arguments.

---

## Naming conventions

- **Phase names:** `preview | explain | question | anchor`. PEQA is shorthand only. Never the legacy PECS names (`prime`, `engage`, `challenge`, `solidify`).
- **Section references:** `§N`. 1-based. Always the `order_index`. In prompts: `[§3]` or `[§3: Maximum Entropy]`.
- **Tool names:** `snake_case` verbs or verb_nouns (`ingest_material`, `check_prerequisites`, `regenerate_map`).
- **Resource schemes:** `lowercase-no-underscores`. Use path components for sub-kinds.
- **Citations are required in every prompt that generates a claim.** Bake it into the prompt template, not into optional instructions the model can skip.

---

## Architectural calls that held (don't reverse without cause)

- **LiteLLM behind a semantic-task facade; no LangChain.** Call sites address tasks (`"qa"`, `"learning_map"`, etc.); `llm/profiles.py` routes tasks to named profiles; `llm/client.py` calls `litellm.acompletion`. Prompt caching for Anthropic-family models is preserved via block pass-through and tested in `test_llm_translation.py`. We skip LangChain because it hides `cache_control` behind a generic interface.
- **Host-agnostic MCP surface.** Tool/prompt/resource descriptions say "the host agent," not "Claude." Zero cost to us, portable day one.
- **Soft-guidance phase loop, not hard locks.** Warning in tool return is enough; hard blocks feel patronising.
- **Idempotent resumable `prepare_material` over detached workers.** Host can re-invoke safely. v1 added a background task on top, but the idempotent core is the safety guarantee.
- **Raw `sqlite3` + dataclasses, not SQLAlchemy.** ~400 lines, no dependency, no import tax.
- **`[§N]` citation convention in every AI prompt.** Catches hallucinations; traceable claims. Never relax.
- **Thin MCP server, fat services.** Keeps `server.py` comprehensible even at 37 tools.
- **`content_hash` dedupe on ingest.** Critical because MCP hosts retry.

---

## Known debt / v3+ watchlist

In rough priority order (top is most load-bearing):

1. **End-to-end stdio + fake-LLM test.** See "standing residual risk" above.
2. **Timezone for streak/weekly.** Everything is UTC. `compute_streak(today=..., tz=...)` is a small change; a `LEARNERS_MCP_TZ` env var is a small follow-up.
3. **Prerequisite "studied" heuristic is a string check.** `(phase_data.preview.response or "").strip() != ""` — typing one character counts. Fine for soft guidance; if it becomes load-bearing, LLM-grade or require min length / `completed_at`.
4. **`time_spent_seconds` is wall-clock.** Span between earliest and latest activity timestamp. 8-hour open tab looks like 8 hours studied. Docstring is explicit.
5. **Weekly report per-material event lookup is O(cards).** Fine for thousands; for millions add a join-aware query on `review_events`.
6. ~~**Provider-neutral batch LLM layer.**~~ Done — LiteLLM + profiles/routes, see `llm/profiles.py`.
7. **MOOC ingestion.** Plan §10 v2 listed it, scoped out. If it returns, route via `loader.py` dispatch (same pattern as YouTube). Coursera/edX would live in `ingestion/mooc/*`.
8. **Voice / audio.** Explicitly not built. MCP supports audio content blocks if needed; work would be in prompts + tool IO.
9. **Regeneration versioning.** `extract_notes_now` currently overwrites. If learners want "show me the previous extraction," we need a `notes_versions` table. Ask before building.
10. **Schema migrations.** `CREATE TABLE IF NOT EXISTS` only. `evaluations` (v2) and `review_events` (v2-audit) added without migration scripts — works for new installs, silently no-ops on existing ones until next boot. If real users arrive, add a `schema_version` table + runner.

---

## Audit discipline

Three audits found 4, 4, and 4 substantive issues (3 of 12 high-severity). Ship a phase, then have an auditor run the plan contract against the code before declaring victory.

**An audit should check:**
- Every plan-level tool / resource / prompt name is actually exposed with that exact name.
- Every plan-level contract (scope enforcement, citations, idempotency, soft vs hard guidance) is exercised by a test.
- Every docstring that makes a behavioural claim has a test that would fail if the claim were false.
- Every proxy metric is labelled as a proxy.
- Every sync ↔ async boundary is justified.
- Every module-level constant that reads `os.environ` is converted to a function.

**An audit should NOT:**
- Catalogue style preferences or refactor opportunities.
- Test host-agent behaviour — only server-side contracts are in scope.
- Require handling of LLM provider failure beyond what's implemented.

**Responding to an audit:** validate every finding against the code before fixing. Two of ~14 claims across three cycles turned out to have test-local explanations (clean-env machine-specific, etc.). Checking takes minutes and prevents chasing ghosts.

---

## Out of scope (explicitly not building)

- Web UI — the host is the UI.
- Multi-user / shared libraries / peer review.
- Docker packaging.
- Voice input / TTS.
- Analytics / observability infra.

---


Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.


<!-- dgc-policy-v11 -->
## Dual-Graph Context Policy

This project uses a local dual-graph MCP server for efficient context retrieval.

### MANDATORY: Adaptive graph_continue rule

**Call `graph_continue` ONLY when you do NOT already know the relevant files.**

Call `graph_continue` when:
- This is the first message of a new task / conversation
- The task shifts to a completely different area of the codebase
- You need files you haven't read yet in this session

SKIP `graph_continue` when:
- You already identified the relevant files earlier in this conversation
- You are doing follow-up work on files already read (verify, refactor, test, docs, cleanup, commit)
- The task is pure text (writing a commit message, summarising, explaining)

**If skipping, go directly to `graph_read` on the already-known `file::symbol`.**

### When you DO call graph_continue

1. If `graph_continue` returns `needs_project=true`: call `graph_scan` with `pwd`. Do NOT ask the user.
2. If `graph_continue` returns `skip=true`: fewer than 5 files — read only specifically named files.
3. Read `recommended_files` using `graph_read`.
   - Always use `file::symbol` notation (e.g. `src/auth.ts::handleLogin`) — never read whole files.
   - `recommended_files` entries that already contain `::` must be passed verbatim.
4. Obey confidence caps:
   - `confidence=high` → Stop. Do NOT grep or explore further.
   - `confidence=medium` → `fallback_rg` at most `max_supplementary_greps` times, then `graph_read` at most `max_supplementary_files` more symbols. Stop.
   - `confidence=low` → same as medium. Stop.

### Rules

- Do NOT use `rg`, `grep`, or bash file exploration before calling `graph_continue` (when required).
- Do NOT do broad/recursive exploration at any confidence level.
- `max_supplementary_greps` and `max_supplementary_files` are hard caps — never exceed them.
- Do NOT call `graph_continue` more than once per turn.
- Always use `file::symbol` notation with `graph_read` — never bare filenames.
- After edits, call `graph_register_edit` with changed files using `file::symbol` notation.

### Session State (compact, update after every turn)

```json
{
  "files_identified": ["path/to/file.py"],
  "symbols_changed": ["module::function"],
  "fix_applied": true,
  "features_added": ["description"],
  "open_issues": ["one-line note"]
}
```

Use this state — not prose summaries — to remember what's been done across turns.

### Token Usage

A `token-counter` MCP is available for tracking live token usage.

- Before reading a large file: `count_tokens({text: "<content>"})` to check cost first.
- To show running session cost: `get_session_stats()`
- To log completed task: `log_usage({input_tokens: N, output_tokens: N, description: "task"})`

### Context Store

Whenever you make a decision, identify a task, note a next step, fact, or blocker during a conversation, append it to `.dual-graph/context-store.json`.

Entry format:
```json
{"type": "decision|task|next|fact|blocker", "content": "one sentence max 15 words", "tags": ["topic"], "files": ["relevant/file.ts"], "date": "YYYY-MM-DD"}
```

To append: Read the file → add the new entry to the array → Write it back → call `graph_register_edit` on `.dual-graph/context-store.json`.

Rules:
- Only log things worth remembering across sessions (not every minor detail)
- `content` must be under 15 words
- `files` lists the files this decision/task relates to (can be empty)
- Log immediately when the item arises — not at session end

### Session End

When the user signals they are done (e.g. "bye", "done", "wrap up", "end session"), proactively update `CONTEXT.md` in the project root with:
- **Current Task**: one sentence on what was being worked on
- **Key Decisions**: bullet list, max 3 items
- **Next Steps**: bullet list, max 3 items

Keep `CONTEXT.md` under 20 lines total. Do NOT summarize the full conversation — only what's needed to resume next session.
