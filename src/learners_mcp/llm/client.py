"""LiteLLM wrapper.

Thin async wrapper that handles:
- prompt caching (ephemeral, 5-minute TTL) on large shared context blocks
  when the underlying model supports Anthropic-style cache_control blocks
- plain text vs JSON extraction
- reasoning/thinking block filtering

Why LiteLLM: provider-neutral routing while still passing cache_control
through to Anthropic-compatible endpoints where it saves >80% on
repeat-chunk calls.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import litellm

from .profiles import resolve
from .providers import effective_cache_mode

litellm.drop_params = True
# allows litellm to rewrite params that differ between providers (e.g. max_tokens → max_completion_tokens)
litellm.modify_params = True

log = logging.getLogger(__name__)

_THINKING_TYPES = {"thinking", "reasoning", "reasoning_content"}
# some open-source models (DeepSeek, Qwen) inline reasoning in <think> tags
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _cached(text: str) -> dict[str, Any]:
    """Wrap text as a cacheable content block."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def _plain(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _extract_text(resp: Any) -> str:
    content = resp.choices[0].message.content
    if isinstance(content, str):
        return _THINK_RE.sub("", content).strip()
    parts: list[str] = []
    for block in content:
        block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if block_type in _THINKING_TYPES:
            continue
        text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
        if text:
            parts.append(text)
    joined = "".join(parts)
    return _THINK_RE.sub("", joined).strip()


class LLM:
    """LiteLLM-backed async client with retry + JSON helpers."""

    async def complete(
        self,
        *,
        task: str,
        system: str,
        blocks: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.2,
        extra_params: dict[str, Any] | None = None,
    ) -> str:
        profile = resolve(task)
        use_cache = effective_cache_mode(profile)

        if use_cache:
            messages = [{"role": "user", "content": blocks}]
        else:
            text = "".join(b.get("text", "") for b in blocks)
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ]

        call_kwargs: dict[str, Any] = {**profile.params}
        if extra_params:
            call_kwargs.update(extra_params)
        call_kwargs["max_tokens"] = max_tokens
        call_kwargs["temperature"] = temperature
        call_kwargs["num_retries"] = 4
        call_kwargs["timeout"] = 600
        if profile.api_base:
            call_kwargs["api_base"] = profile.api_base
        if profile.api_key_env:
            call_kwargs["api_key"] = os.environ.get(profile.api_key_env, "")
        if use_cache:
            call_kwargs["system"] = system

        resp = await litellm.acompletion(
            model=profile.model,
            messages=messages,
            **call_kwargs,
        )

        usage = getattr(resp, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        log.debug(
            "llm task=%s model=%s in=%d out=%d",
            task,
            profile.model,
            input_tokens,
            output_tokens,
        )

        return _extract_text(resp)

    async def complete_json(
        self,
        *,
        task: str,
        system: str,
        blocks: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.2,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system = (
            system
            + "\n\nRespond ONLY with a single valid JSON object. No prose before or after."
        )
        raw = await self.complete(
            task=task,
            system=system,
            blocks=blocks,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_params=extra_params,
        )
        return _extract_json(raw)


def _extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object from a model response, tolerating ```json fences."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"no JSON object found in response: {text[:200]!r}")
        candidate = text[start : end + 1]
    return json.loads(candidate)


def cached_source(label: str, body: str) -> list[dict[str, Any]]:
    """Returns a two-block sequence: a small label + the cacheable body.

    Putting the label before the cache block keeps cache-hits stable even when
    the wrapper text changes.
    """
    return [_plain(label), _cached(body)]


def plain(body: str) -> list[dict[str, Any]]:
    return [_plain(body)]


__all__ = ["LLM", "cached_source", "plain"]
