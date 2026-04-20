"""Map-reduce note extraction.

For each section:
  1. Chunk the content (30KB with 2KB overlap, configurable).
  2. Map phase: produce handwritten-style notes for each chunk + a running TLDR.
     Chunks run sequentially because the TLDR from chunk N feeds chunk N+1 —
     the rolling TLDR is the biggest quality gain in the original pipeline.
  3. Reduce phase: combine per-chunk notes into one coherent study guide.
  4. Consistency phase: polish to pure Markdown.

Cost optimisation: within each chunk iteration, the chunk body is the only
large shared input between the map call and the TLDR call. We wrap it as a
single `cache_control: ephemeral` block so the TLDR call's input tokens on
that chunk hit the cache written by the map call — roughly halving per-chunk
input cost on Haiku. Cross-chunk caching is not useful here because chunks
are disjoint.
"""

from __future__ import annotations

import logging

from ..config import CHUNK_OVERLAP, CHUNK_SIZE
from ..ingestion.chunker import chunk_for_map_reduce
from ..llm.client import LLM, cached_source, plain
from ..llm.prompts import (
    CONSISTENCY_SYSTEM,
    CONSISTENCY_USER_TEMPLATE,
    MAP_SYSTEM,
    MAP_USER_TEMPLATE,
    REDUCE_SYSTEM,
    REDUCE_USER_TEMPLATE,
    TLDR_SYSTEM,
    TLDR_USER_TEMPLATE,
)

log = logging.getLogger(__name__)


async def extract_notes(
    llm: LLM,
    section_content: str,
    section_ref: int,
) -> str:
    """Return polished Markdown notes for one section.

    section_ref is the order_index (1-based) used in `[§N]` citations
    that downstream prompts preserve.
    """
    chunks = chunk_for_map_reduce(section_content, CHUNK_SIZE, CHUNK_OVERLAP)
    log.info("notes: section §%d → %d chunks", section_ref, len(chunks))

    mapped: list[str] = []
    tldr = ""
    for i, chunk in enumerate(chunks):
        # Cache the chunk body: the map call writes the cache, the TLDR call
        # immediately after re-uses it. Instruction-style blocks (`map_user`,
        # `tldr_user`) stay uncached because they differ per call.
        chunk_blocks = cached_source(
            label=f"CHUNK {i + 1}/{len(chunks)} of §{section_ref}:",
            body=chunk,
        )

        map_user = MAP_USER_TEMPLATE.format(prior_tldr=tldr or "(none)", section_ref=section_ref)
        map_out = await llm.complete(
            task="notes_map",
            system=MAP_SYSTEM,
            blocks=chunk_blocks + plain("\n\n" + map_user),
            max_tokens=4096,
            temperature=0.0,
        )
        mapped.append(map_out)

        tldr_user = TLDR_USER_TEMPLATE.format(prior_tldr=tldr or "(none)")
        tldr = await llm.complete(
            task="notes_tldr",
            system=TLDR_SYSTEM,
            blocks=chunk_blocks + plain("\n\n" + tldr_user),
            max_tokens=1024,
            temperature=0.0,
        )

    reduce_user = REDUCE_USER_TEMPLATE.format(notes="\n\n---\n\n".join(mapped))
    reduced = await llm.complete(
        task="notes_reduce",
        system=REDUCE_SYSTEM,
        blocks=plain(reduce_user),
        max_tokens=8192,
        temperature=0.1,
    )

    polish_user = CONSISTENCY_USER_TEMPLATE.format(notes=reduced)
    polished = await llm.complete(
        task="notes_polish",
        system=CONSISTENCY_SYSTEM,
        blocks=plain(polish_user),
        max_tokens=8192,
        temperature=0.1,
    )
    return polished
