"""Anthropic SDK wrapper.

Thin async wrapper that handles:
- prompt caching (ephemeral, 5-minute TTL) on large shared context blocks
- plain text vs JSON extraction
- a small retry loop for transient errors

Why direct SDK and not LangChain/LiteLLM: prompt caching is the biggest cost
lever for our map-reduce note pipeline, and it's provider-specific. The
abstraction tax of a multi-provider layer isn't worth paying when we save
>80% on repeat-chunk calls via `cache_control`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from anthropic import APIError, APIStatusError, AsyncAnthropic

from ..config import MODEL_SONNET, anthropic_api_key

log = logging.getLogger(__name__)


def _cached(text: str) -> dict[str, Any]:
    """Wrap text as a cacheable content block."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def _plain(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


class LLM:
    """Reusable Anthropic client with retry + JSON helpers."""

    def __init__(self, api_key: str | None = None):
        self.client = AsyncAnthropic(api_key=api_key or anthropic_api_key())

    async def complete(
        self,
        *,
        model: str,
        system: str,
        blocks: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        """Run a completion and return the plain-text response."""
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    messages=[{"role": "user", "content": blocks}],
                )
                usage = getattr(resp, "usage", None)
                if usage:
                    log.debug(
                        "llm model=%s in=%d out=%d cache_read=%d cache_write=%d",
                        model,
                        getattr(usage, "input_tokens", 0),
                        getattr(usage, "output_tokens", 0),
                        getattr(usage, "cache_read_input_tokens", 0) or 0,
                        getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    )
                return "".join(
                    b.text for b in resp.content if getattr(b, "type", None) == "text"
                )
            except APIStatusError as e:
                if e.status_code in (429, 500, 502, 503, 529) and attempt < 4:
                    delay = 2 ** attempt
                    log.warning("llm %s, retry %d in %ds", e.status_code, attempt, delay)
                    await asyncio.sleep(delay)
                    continue
                raise
            except APIError as e:
                if attempt < 3:
                    await asyncio.sleep(1)
                    continue
                raise

    async def complete_json(
        self,
        *,
        model: str,
        system: str,
        blocks: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Same as complete() but parses the JSON object from the response."""
        # Nudge the model toward JSON-only output.
        system = (
            system
            + "\n\nRespond ONLY with a single valid JSON object. No prose before or after."
        )
        raw = await self.complete(
            model=model,
            system=system,
            blocks=blocks,
            max_tokens=max_tokens,
            temperature=temperature,
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


# Convenience constructors for cache-friendly content blocks

def cached_source(label: str, body: str) -> list[dict[str, Any]]:
    """Returns a two-block sequence: a small label + the cacheable body.

    Putting the label before the cache block keeps cache-hits stable even when
    the wrapper text changes.
    """
    return [_plain(label), _cached(body)]


def plain(body: str) -> list[dict[str, Any]]:
    return [_plain(body)]


__all__ = ["LLM", "cached_source", "plain", "MODEL_SONNET"]
