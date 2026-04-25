"""Loader routing — ensures the right backend is picked per source string.

We mock the heavy backends (markitdown, youtube-transcript-api) so these
tests don't hit the network or require large dependencies at runtime.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import learners_mcp.ingestion.loader as loader_mod
from learners_mcp.ingestion.loader import (
    _looks_like_youtube,
    _youtube_video_id,
    load,
    load_text,
)


def test_looks_like_youtube_detects_hosts():
    assert _looks_like_youtube("https://www.youtube.com/watch?v=abc")
    assert _looks_like_youtube("https://youtu.be/xyz")
    assert _looks_like_youtube("https://m.youtube.com/watch?v=abc")
    assert not _looks_like_youtube("https://example.com/page")
    assert not _looks_like_youtube("/local/file.pdf")


def test_youtube_video_id_parses_shapes():
    assert _youtube_video_id("https://www.youtube.com/watch?v=abc123") == "abc123"
    assert _youtube_video_id("https://youtu.be/abc123") == "abc123"
    assert _youtube_video_id("https://www.youtube.com/shorts/short456") == "short456"
    assert _youtube_video_id("https://example.com") is None


def test_load_text_happy_path():
    lm = load_text("some pasted content", title="My Notes")
    assert lm.title == "My Notes"
    assert lm.source_type == "text"
    assert lm.source_ref == "(pasted)"


def test_load_text_rejects_empty():
    with pytest.raises(ValueError):
        load_text("   ", title="Empty")


def test_load_reads_plain_text_files_without_markitdown(tmp_path, monkeypatch):
    path = tmp_path / "book.txt"
    path.write_text("Chapter 1\n\nDown the Rabbit-Hole", encoding="utf-8")

    def fail_markitdown():
        raise AssertionError("plain text files should not use MarkItDown")

    monkeypatch.setitem(
        sys.modules,
        "markitdown",
        SimpleNamespace(MarkItDown=fail_markitdown),
    )

    lm = load(str(path), title=None)

    assert lm.title == "book"
    assert lm.source_type == "txt"
    assert lm.source_ref == str(path.resolve())
    assert "Down the Rabbit-Hole" in lm.text


def test_load_routes_url_to_markitdown():
    loader_mod._MARKITDOWN_CLASS = None
    fake_result = SimpleNamespace(text_content="# Page\n\nHello", title="Page")
    with patch("markitdown.MarkItDown") as mid_cls:
        mid_cls.return_value.convert.return_value = fake_result
        lm = load("https://example.com/article", title=None)
    assert lm.source_type == "url"
    assert lm.source_ref == "https://example.com/article"
    assert "Hello" in lm.text


def test_load_routes_epub_to_markitdown(tmp_path):
    loader_mod._MARKITDOWN_CLASS = None
    path = tmp_path / "book.epub"
    path.write_bytes(b"fake epub")

    fake_result = SimpleNamespace(text_content="# Book\n\nConverted", title=None)

    with patch("markitdown.MarkItDown") as mid_cls:
        mid_cls.return_value.convert.return_value = fake_result
        lm = load(str(path), title=None)

    assert lm.title == "book"
    assert lm.source_type == "epub"
    assert lm.source_ref == str(path.resolve())
    assert "Converted" in lm.text


def test_load_routes_youtube_to_transcript_api():
    # Mock the new instance-method API.
    snippets = [
        SimpleNamespace(text="first line"),
        SimpleNamespace(text="second line"),
    ]
    fake_api = SimpleNamespace()
    fake_api.fetch = lambda vid: SimpleNamespace(snippets=snippets)
    with patch("youtube_transcript_api.YouTubeTranscriptApi", return_value=fake_api):
        lm = load("https://youtu.be/xyz789")
    assert lm.source_type == "youtube"
    assert lm.source_ref == "https://youtu.be/xyz789"
    assert "first line" in lm.text
    assert "second line" in lm.text
