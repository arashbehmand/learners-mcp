"""MCP contract smoke test.

Verifies the server exposes the expected tool/resource/prompt names and that
each tool's input schema is valid JSON Schema. No LLM calls — this is a
structural check, not a behavior test.
"""

from __future__ import annotations

import asyncio

import pytest

from learners_mcp.server import mcp


EXPECTED_TOOLS = {
    # v0 surface
    "ingest_material",
    "prepare_material",
    "get_preparation_status",
    "get_material_map",
    "get_focus_brief",
    "get_notes",
    "list_sections",
    "list_materials",
    "start_section",
    "record_phase_response",
    "complete_phase",
    "suggest_flashcards",
    "add_flashcard",
    "list_flashcards",
    "review_flashcard",
    "next_due",
    "answer_from_material",
    "recommend_next_action",
    "get_phase_prompt",
    # v1 additions
    "regenerate_map",
    "get_completion_report",
    "regenerate_completion_report",
    "export_anki",
    "export_notes",
    "export_material_artifacts",
    "material_progress",
    "library_dashboard",
    "start_background_preparation",
    "get_background_status",
    "extract_notes_now",
    # v2 additions
    "check_prerequisites",
    "plan_study",
    "study_streak",
    "weekly_report",
    "evaluate_phase_response",
    "list_evaluations",
    "export_project",
    "import_project",
}

EXPECTED_RESOURCE_TEMPLATES = {
    "material://{material_id}",
    "learning_map://{material_id}",
    "focus_brief://{section_id}",
    "notes://{material_id}",
    "notes://{material_id}/{section_id}",
    "section_state://{section_id}",
    "completion_report://{section_id}",
    # v2
    "evaluations://{section_id}",
    "plan://{material_id}",
}

EXPECTED_RESOURCES = {
    "library://",
    "review://due",
    # v2
    "streak://",
    "report://weekly",
}

EXPECTED_PROMPTS = {"preview", "explain", "question", "anchor"}


@pytest.mark.asyncio
async def test_tools_list():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


@pytest.mark.asyncio
async def test_resource_templates_list():
    templates = await mcp.list_resource_templates()
    uris = {t.uriTemplate for t in templates}
    assert EXPECTED_RESOURCE_TEMPLATES <= uris, (
        f"missing: {EXPECTED_RESOURCE_TEMPLATES - uris}"
    )


@pytest.mark.asyncio
async def test_resources_list():
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert EXPECTED_RESOURCES <= uris, f"missing: {EXPECTED_RESOURCES - uris}"


@pytest.mark.asyncio
async def test_prompts_list():
    prompts = await mcp.list_prompts()
    names = {p.name for p in prompts}
    assert EXPECTED_PROMPTS <= names, f"missing: {EXPECTED_PROMPTS - names}"


@pytest.mark.asyncio
async def test_each_tool_has_valid_input_schema():
    tools = await mcp.list_tools()
    for t in tools:
        schema = t.inputSchema
        assert isinstance(schema, dict), f"{t.name} inputSchema not a dict"
        assert schema.get("type") == "object", f"{t.name} schema not object-type"
        # Properties may be empty for no-arg tools (list_materials).
        assert "properties" in schema, f"{t.name} has no properties key"


@pytest.mark.asyncio
async def test_start_section_accepts_optional_material_id_for_host_retries():
    tools = await mcp.list_tools()
    start_section = next(t for t in tools if t.name == "start_section")
    props = start_section.inputSchema["properties"]

    assert "section_id" in props
    assert "material_id" in props
    assert start_section.inputSchema["required"] == ["section_id"]


@pytest.mark.asyncio
async def test_each_prompt_has_section_id_argument():
    prompts = await mcp.list_prompts()
    for p in prompts:
        if p.name in EXPECTED_PROMPTS:
            arg_names = {a.name for a in (p.arguments or [])}
            assert "section_id" in arg_names, f"prompt {p.name} missing section_id"
