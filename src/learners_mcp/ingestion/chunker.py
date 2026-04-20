"""Chunker for the map-reduce notes pipeline.

Splits a single section's content into ~30KB chunks with 2KB overlap for
parallel map-phase processing. This is distinct from the section-level
splitter (see splitter.py), which detects chapter structure across an
entire document.
"""

from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_for_map_reduce(
    text: str,
    chunk_size: int = 30_000,
    overlap: int = 2_000,
) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=overlap, length_function=len
    )
    return splitter.split_text(text)
