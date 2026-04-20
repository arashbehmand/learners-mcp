"""Hierarchical splitter — shape checks on representative inputs."""

from __future__ import annotations

from learners_mcp.ingestion.splitter import (
    HierarchicalSplitter,
    split_into_sections,
)


def test_plain_text_single_section():
    text = "This is a short document with no headers.\n" * 30
    sections = split_into_sections(text, min_size=100, max_size=10_000)
    assert len(sections) >= 1
    assert all(content.strip() for content, _ in sections)


def test_markdown_headers_detected_as_sections():
    text = (
        "# Chapter 1: Introduction\n\n"
        + ("Paragraph about introduction.\n" * 80)
        + "\n\n# Chapter 2: Basics\n\n"
        + ("Paragraph about basics.\n" * 80)
        + "\n\n# Chapter 3: Advanced\n\n"
        + ("Paragraph about advanced topics.\n" * 80)
    )
    sp = HierarchicalSplitter()
    assert sp.is_markdown(text)
    sections = split_into_sections(text, min_size=500, max_size=10_000)
    assert len(sections) >= 2
    titles = [t for _, t in sections if t]
    assert any("Chapter 1" in t or "Introduction" in t for t in titles)


def test_large_section_split_into_chunks():
    """A titled markdown section larger than max_size is further split; the
    sub-chunks inherit the parent title with a '(Part N)' marker."""
    body = "paragraph content. " * 2_000
    text = (
        "# Introduction\n\nShort intro.\n\n"
        f"# Main Topic\n\n{body}\n\n"
        "# Conclusion\n\nShort conclusion.\n"
    )
    sections = split_into_sections(text, min_size=500, max_size=4_000)
    # Should split the big middle section into multiple parts.
    part_titles = [t for _, t in sections if t and "Part" in t]
    assert part_titles, f"expected Part-marked sub-sections, got titles: {[t for _, t in sections]}"


def test_fallback_chunking_when_no_recognizable_sections():
    """Plain text with no headers/patterns falls back to recursive chunking."""
    body = "word " * 10_000
    sections = split_into_sections(body, min_size=500, max_size=3_000)
    assert len(sections) > 1
    assert all(title is None for _, title in sections)


def test_markdown_with_fewer_than_3_headers_falls_back_to_regex():
    text = "# Only Heading\n\n" + ("Some body text.\n" * 100)
    sp = HierarchicalSplitter()
    assert sp.is_markdown(text) is False
    sections = split_into_sections(text, min_size=200, max_size=10_000)
    assert len(sections) >= 1
