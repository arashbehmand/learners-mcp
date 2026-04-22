"""Source → text dispatch.

Handles:
- Local files (PDF/EPUB/DOCX/TXT/MD) via markitdown.
- YouTube URLs via youtube-transcript-api (lazy import; optional dep).
- Web URLs via markitdown's URL conversion.
- Raw pasted text.

Returns a LoadedMaterial so the caller can persist without knowing the
input format.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
SUPPORTED_SUFFIXES = {".pdf", ".epub", ".docx", *TEXT_SUFFIXES}

YT_HOSTS = ("youtube.com", "youtu.be", "m.youtube.com")
_MARKITDOWN_CLASS = None


@dataclass
class LoadedMaterial:
    title: str
    text: str
    source_type: str
    source_ref: str


def load(source: str, title: str | None = None) -> LoadedMaterial:
    """Dispatch to the right loader for the given source string."""
    s = source.strip()
    if _looks_like_youtube(s):
        return _load_youtube(s, title)
    if s.startswith(("http://", "https://")):
        return _load_url(s, title)
    return _load_file(s, title)


def load_text(raw: str, title: str) -> LoadedMaterial:
    """Ingest raw pasted text directly."""
    if not raw.strip():
        raise ValueError("raw text is empty")
    return LoadedMaterial(title=title, text=raw, source_type="text", source_ref="(pasted)")


def preload_markitdown(source: str | None = None) -> None:
    """Import MarkItDown on the caller thread when the source needs it."""
    if source is not None and not _uses_markitdown(source):
        return
    _get_markitdown_class()


# -------------------- internal --------------------


def _load_file(source: str, title: str | None) -> LoadedMaterial:
    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"source not found: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"unsupported file type {path.suffix!r}. Supported: {sorted(SUPPORTED_SUFFIXES)}"
        )

    if path.suffix.lower() in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8-sig")
        if not text.strip():
            raise ValueError(f"text file is empty: {path}")
        return LoadedMaterial(
            title=title or path.stem,
            text=text,
            source_type=path.suffix.lstrip(".").lower() or "text",
            source_ref=str(path),
        )

    result = _get_markitdown_class()().convert(str(path))
    text = result.text_content or ""
    if not text.strip():
        raise ValueError(f"markitdown returned empty content for {path}")

    return LoadedMaterial(
        title=title or path.stem,
        text=text,
        source_type=path.suffix.lstrip(".").lower() or "text",
        source_ref=str(path),
    )


def _load_url(url: str, title: str | None) -> LoadedMaterial:
    """Fetch a web page via markitdown."""
    result = _get_markitdown_class()().convert(url)
    text = result.text_content or ""
    if not text.strip():
        raise ValueError(f"URL returned empty or unreadable content: {url}")

    resolved_title = (
        title
        or (result.title if hasattr(result, "title") and result.title else None)
        or _title_from_url(url)
    )
    return LoadedMaterial(
        title=resolved_title,
        text=text,
        source_type="url",
        source_ref=url,
    )


def _load_youtube(url: str, title: str | None) -> LoadedMaterial:
    """Fetch a YouTube transcript via youtube-transcript-api."""
    try:
        # Newer API (v1.x): YouTubeTranscriptApi().fetch(video_id)
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "YouTube ingestion needs the `youtube-transcript-api` package. "
            "Install it: `pip install youtube-transcript-api`."
        ) from e

    video_id = _youtube_video_id(url)
    if video_id is None:
        raise ValueError(f"could not parse YouTube video id from: {url}")

    # Try the two calling conventions (old class-method, new instance-method)
    # so we work across api versions.
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id)
        snippets = fetched.snippets if hasattr(fetched, "snippets") else list(fetched)
        text = "\n".join(
            s.text if hasattr(s, "text") else s.get("text", "") for s in snippets
        )
    except AttributeError:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)  # type: ignore[attr-defined]
        text = "\n".join(s["text"] for s in transcript)

    if not text.strip():
        raise ValueError(f"empty transcript for video {video_id}")

    return LoadedMaterial(
        title=title or f"YouTube {video_id}",
        text=text,
        source_type="youtube",
        source_ref=url,
    )


def _looks_like_youtube(s: str) -> bool:
    low = s.lower()
    return any(host in low for host in YT_HOSTS)


def _youtube_video_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if host.endswith("youtu.be"):
        return parsed.path.lstrip("/") or None
    if "youtube.com" in host:
        qs = urllib.parse.parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return qs["v"][0]
        # /shorts/<id> or /embed/<id>
        m = re.match(r"^/(shorts|embed)/([A-Za-z0-9_-]{5,})", parsed.path)
        if m:
            return m.group(2)
    return None


def _title_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/") or parsed.netloc
    return path[:80]


def _get_markitdown_class():
    global _MARKITDOWN_CLASS
    if _MARKITDOWN_CLASS is None:
        from markitdown import MarkItDown  # type: ignore[import-not-found]

        _MARKITDOWN_CLASS = MarkItDown
    return _MARKITDOWN_CLASS


def _uses_markitdown(source: str) -> bool:
    s = source.strip()
    if _looks_like_youtube(s):
        return False
    if s.startswith(("http://", "https://")):
        return True
    suffix = Path(s).expanduser().suffix.lower()
    return suffix in (SUPPORTED_SUFFIXES - TEXT_SUFFIXES)
