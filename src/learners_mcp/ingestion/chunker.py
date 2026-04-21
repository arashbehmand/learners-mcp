"""Chunker for the map-reduce notes pipeline.

Splits a single section's content into ~30KB chunks with 2KB overlap for
parallel map-phase processing. This is distinct from the section-level
splitter (see splitter.py), which detects chapter structure across an
entire document.
"""

from __future__ import annotations


def chunk_for_map_reduce(
    text: str,
    chunk_size: int = 30_000,
    overlap: int = 2_000,
) -> list[str]:
    """Split text into chunks of at most chunk_size with overlap between them.

    Splits preferentially at paragraph boundaries, then line breaks, then
    spaces, falling back to hard character cuts only if necessary.
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Prefer a clean break near the end of the window.
        for sep in ("\n\n", "\n", " "):
            idx = text.rfind(sep, start + chunk_size // 2, end)
            if idx != -1:
                end = idx + len(sep)
                break
        chunks.append(text[start:end])
        start = end - overlap

    return [c for c in chunks if c.strip()]
