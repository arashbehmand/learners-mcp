import json
import pytest
from pathlib import Path

from learners_mcp.llm.profiles import resolve, TASKS


def test_default_resolve_fast_tasks():
    profile = resolve("notes_map")
    assert profile.model == "claude-haiku-4-5-20251001"


def test_default_resolve_default_tasks():
    profile = resolve("qa")
    assert profile.model == "claude-sonnet-4-6"


def test_default_resolve_oneshot():
    profile = resolve("learning_map")
    assert profile.model == "claude-opus-4-7"


def test_unknown_task_raises():
    with pytest.raises(ValueError, match="nonexistent"):
        resolve("nonexistent")


def test_env_override_model(monkeypatch):
    monkeypatch.setenv("LEARNERS_MCP_MODEL_DEFAULT", "gpt-4o-mini")
    assert resolve("qa").model == "gpt-4o-mini"


def test_env_override_params(monkeypatch):
    monkeypatch.setenv("LEARNERS_MCP_PARAMS_DEFAULT", '{"reasoning_effort":"low"}')
    assert resolve("qa").params == {"reasoning_effort": "low"}


def test_env_route_override(monkeypatch):
    monkeypatch.setenv("LEARNERS_MCP_ROUTE_QA", "fast")
    assert resolve("qa").model == "claude-haiku-4-5-20251001"


def test_route_to_missing_profile_raises(monkeypatch):
    monkeypatch.setenv("LEARNERS_MCP_ROUTE_QA", "nonexistent_profile")
    with pytest.raises(ValueError, match="nonexistent_profile"):
        resolve("qa")


def test_yaml_config_loading(tmp_path, monkeypatch):
    config_file = tmp_path / "llm.yaml"
    config_file.write_text(
        "profiles:\n"
        "  custom:\n"
        "    model: gpt-4.1-mini\n"
        "routes:\n"
        "  qa: custom\n"
    )
    monkeypatch.setenv("LEARNERS_MCP_LLM_CONFIG", str(config_file))
    assert resolve("qa").model == "gpt-4.1-mini"
