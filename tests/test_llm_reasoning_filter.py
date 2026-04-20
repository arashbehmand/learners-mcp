"""Tests that LLM._extract_text strips reasoning/thinking content."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from learners_mcp.llm.client import LLM


def fake_response(content):
    """content can be a str or list of objects with .type and .text attrs."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = None
    return resp


def _block(type_: str, text: str) -> MagicMock:
    b = MagicMock()
    b.type = type_
    b.text = text
    return b


BASE_BLOCKS = [{"type": "text", "text": "q"}]


@pytest.mark.asyncio
async def test_think_tag_stripped_from_string():
    content = "<think>reasoning</think>final answer"
    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_response(content))):
        result = await LLM().complete(task="qa", system="s", blocks=BASE_BLOCKS)
    assert result == "final answer"


@pytest.mark.asyncio
async def test_think_tag_stripped_multiline():
    content = "<think>\nmulti\nline\n</think>result"
    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_response(content))):
        result = await LLM().complete(task="qa", system="s", blocks=BASE_BLOCKS)
    assert result == "result"


@pytest.mark.asyncio
async def test_thinking_block_stripped_from_list():
    content = [_block("thinking", "chain"), _block("text", "answer")]
    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_response(content))):
        result = await LLM().complete(task="qa", system="s", blocks=BASE_BLOCKS)
    assert result == "answer"


@pytest.mark.asyncio
async def test_reasoning_content_block_stripped():
    content = [_block("reasoning_content", "internal reasoning"), _block("text", "final")]
    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_response(content))):
        result = await LLM().complete(task="qa", system="s", blocks=BASE_BLOCKS)
    assert result == "final"


@pytest.mark.asyncio
async def test_plain_text_returned_as_is():
    content = "plain answer"
    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_response(content))):
        result = await LLM().complete(task="qa", system="s", blocks=BASE_BLOCKS)
    assert result == "plain answer"


@pytest.mark.asyncio
async def test_multiple_text_blocks_joined():
    content = [_block("text", "hello "), _block("text", "world")]
    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_response(content))):
        result = await LLM().complete(task="qa", system="s", blocks=BASE_BLOCKS)
    assert result == "hello world"
