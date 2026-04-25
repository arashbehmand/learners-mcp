"""Tests that zero-config (Anthropic-backed via litellm) works correctly."""
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from learners_mcp.llm.client import LLM
from learners_mcp.llm.profiles import resolve, BUILT_IN_PROFILES


def fake_response(content="ok"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = None
    return resp


def _clear_llm_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("LEARNERS_MCP_MODEL_") or key.startswith("LEARNERS_MCP_PARAMS_") or key.startswith("LEARNERS_MCP_ROUTE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LEARNERS_MCP_LLM_CONFIG", "/nonexistent/llm-test-defaults.yaml")


def test_default_qa_profile_is_sonnet(monkeypatch):
    _clear_llm_env(monkeypatch)
    assert resolve("qa").model == "claude-sonnet-4-6"


def test_default_learning_map_is_opus(monkeypatch):
    _clear_llm_env(monkeypatch)
    assert resolve("learning_map").model == "claude-opus-4-7"


def test_default_notes_map_is_haiku(monkeypatch):
    _clear_llm_env(monkeypatch)
    assert resolve("notes_map").model == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_anthropic_model_uses_caching_blocks(monkeypatch):
    _clear_llm_env(monkeypatch)
    captured: dict = {}

    async def _fake_completion(*, model, messages, **kwargs):
        captured["messages"] = messages
        return fake_response()

    with patch("litellm.acompletion", new=_fake_completion):
        await LLM().complete(
            task="qa",
            system="s",
            blocks=[{"type": "text", "text": "q", "cache_control": {"type": "ephemeral"}}],
        )

    assert captured["messages"][0]["role"] == "user"
    assert isinstance(captured["messages"][0]["content"], list)


@pytest.mark.asyncio
async def test_openai_model_flattens_blocks(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LEARNERS_MCP_MODEL_DEFAULT", "gpt-4o-mini")

    captured: dict = {}

    async def _fake_completion(*, model, messages, **kwargs):
        captured["messages"] = messages
        return fake_response()

    with patch("litellm.acompletion", new=_fake_completion):
        await LLM().complete(
            task="qa",
            system="sys",
            blocks=[{"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}],
        )

    roles = [m["role"] for m in captured["messages"]]
    assert "system" in roles
    user_msg = next(m for m in captured["messages"] if m["role"] == "user")
    assert isinstance(user_msg["content"], str)
