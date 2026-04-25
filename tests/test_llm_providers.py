from learners_mcp.llm.profiles import Profile
from learners_mcp.llm.providers import effective_cache_mode, supports_anthropic_blocks


def test_anthropic_bare():
    assert supports_anthropic_blocks("claude-sonnet-4-6") is True


def test_anthropic_prefixed():
    assert supports_anthropic_blocks("anthropic/claude-3-haiku") is True


def test_openrouter_anthropic():
    assert supports_anthropic_blocks("openrouter/anthropic/claude-sonnet-4.6") is True


def test_vertex_claude():
    assert supports_anthropic_blocks("vertex_ai/claude-3-sonnet") is True


def test_bedrock_claude():
    assert supports_anthropic_blocks("bedrock/anthropic.claude-v2:1") is True


def test_openai_not_anthropic():
    assert supports_anthropic_blocks("gpt-4o-mini") is False


def test_openrouter_openai_not_anthropic():
    assert supports_anthropic_blocks("openrouter/openai/gpt-4o") is False


def test_effective_cache_auto_anthropic():
    p = Profile(name="test", model="claude-sonnet-4-6", prompt_cache="auto")
    assert effective_cache_mode(p) is True


def test_effective_cache_auto_openai():
    p = Profile(name="test", model="gpt-4o", prompt_cache="auto")
    assert effective_cache_mode(p) is False


def test_effective_cache_on_forces_true():
    p = Profile(name="test", model="gpt-4o", prompt_cache="on")
    assert effective_cache_mode(p) is True


def test_effective_cache_off_forces_false():
    p = Profile(name="test", model="claude-sonnet-4-6", prompt_cache="off")
    assert effective_cache_mode(p) is False
