"""Hierarchical content splitter.

Ported from PECS-learner/utils/hierarchical_processor.py. Detects chapter/section
structure via markdown headers or regex patterns; falls back to recursive chunking.
Format-agnostic — works on PDF-converted markdown, EPUB, plain text, transcripts.
"""

from __future__ import annotations

import re

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    MarkdownTextSplitter,
    RecursiveCharacterTextSplitter,
)


def _clean_title(title: str | None) -> str | None:
    if not title:
        return None

    lowered = title.lower().strip()
    if lowered.startswith(("here ", "see ", "cf. ", "e.g. ", "i.e. ", "ibid")):
        return None

    citation_score = 0
    if re.match(r"^[A-Z][a-z]+,\s", title):
        citation_score += 2
    if re.search(r"\b(19|20)\d{2}\b", title):
        citation_score += 1
    if "http" in title.lower() or re.search(r"www\.\S+", title):
        citation_score += 2
    if re.search(
        r"\b(Journal|Review|Post|Times|Magazine|Press|Publishing)\b",
        title,
        re.IGNORECASE,
    ):
        citation_score += 1
    if re.search(r"\b(vol\.|pp\.|p\.|no\.|doi:)", title, re.IGNORECASE):
        citation_score += 2
    punct_count = sum(title.count(c) for c in ",:;.")
    if punct_count > 5:
        citation_score += 1
    if citation_score >= 3:
        return None

    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", title)
    cleaned = re.sub(r"<https?://[^>]+>", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"[*_`]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned or len(cleaned) > 200:
        return None
    if len(cleaned) < 20 and len(title) > 100:
        return None
    return cleaned


class HierarchicalSplitter:
    """Splits large documents into logical sections."""

    SECTION_PATTERNS = [
        r"^\[([^\]]{10,200})\]\(ch\d+[^)]+\)\s*$",
        r"^(?:Chapter\s+\d+|CHAPTER\s+\d+|Chapter\s+[IVXLC]+)\s*[:-]?\s*(.+)$",
        r"^#+\s*(?:Chapter\s+\d+|Chapter\s+[IVXLC]+)\s*[:-]?\s*(.+)$",
        r"^\d+\.\s+\d+\.\s+(.+)$",
        r"^Section\s+\d+\s*[:-]?\s*(.+)$",
        r"^[IVXLC]+\.\s+(.+)$",
        r"^(?:Part\s+\d+|PART\s+\d+)\s*[:-]?\s*(.+)$",
        r"^---+\s*(.+?)\s*---+$",
    ]

    MD_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]

    def is_markdown(self, text: str) -> bool:
        pattern = r"^#{1,6}\s+(.+)$"
        real_headers = 0
        for line in text.split("\n"):
            m = re.match(pattern, line.strip())
            if not m:
                continue
            header_text = m.group(1)
            if (
                not header_text.lower().startswith("here ")
                and "http" not in header_text.lower()
                and len(header_text) < 150
                and re.search(r"\w{3,}", header_text)
            ):
                real_headers += 1
        return real_headers >= 3

    def _split_markdown(
        self,
        text: str,
        min_size: int,
        max_size: int,
        overlap: int,
    ) -> list[tuple[str, str | None]]:
        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=self.MD_HEADERS,
            strip_headers=False,
        )
        try:
            header_sections = header_splitter.split_text(text)
        except Exception:
            return self._split_regex(text, min_size, max_size, overlap)

        sections: list[tuple[str, str | None]] = []
        md_splitter = MarkdownTextSplitter(chunk_size=max_size, chunk_overlap=overlap)
        front_matter_buffer: list[str] = []

        for doc in header_sections:
            content = doc.page_content
            title: str | None = None
            is_citation = False
            if getattr(doc, "metadata", None):
                parts: list[str] = []
                for key in ("h1", "h2", "h3"):
                    raw = doc.metadata.get(key)
                    if not raw:
                        continue
                    cleaned = _clean_title(raw)
                    if cleaned:
                        parts.append(cleaned)
                    else:
                        is_citation = True
                title = " > ".join(parts) if parts else None

            if is_citation and not title:
                if not sections:
                    front_matter_buffer.append(content)
                else:
                    prev_content, prev_title = sections[-1]
                    sections[-1] = (prev_content + "\n\n" + content, prev_title)
                continue

            if front_matter_buffer and not sections:
                content = "\n\n".join(front_matter_buffer) + "\n\n" + content
                front_matter_buffer = []

            if len(content) < min_size and sections:
                prev_content, prev_title = sections[-1]
                sections[-1] = (prev_content + "\n\n" + content, prev_title or title)
                continue

            if len(content) > max_size:
                for i, chunk in enumerate(md_splitter.split_text(content)):
                    chunk_title = f"{title} (Part {i + 1})" if title else None
                    sections.append((chunk, chunk_title))
            else:
                sections.append((content, title))

        return [(c, t) for c, t in sections if c.strip()]

    def _detect_sections(self, text: str) -> list[tuple[str | None, int]]:
        lines = text.split("\n")
        out: list[tuple[str | None, int]] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            for pattern in self.SECTION_PATTERNS:
                m = re.match(pattern, stripped, re.IGNORECASE)
                if not m:
                    continue
                title = (m.group(1) if m.groups() else m.group(0)).strip()

                if pattern.startswith(r"^\["):
                    next_lines = lines[i + 1 : i + 10]
                    link_count = sum(
                        1 for l in next_lines if re.match(r"^\[.*\]\(.*\)", l.strip())
                    )
                    text_count = sum(
                        1
                        for l in next_lines
                        if l.strip() and not re.match(r"^\[.*\]\(.*\)", l.strip())
                    )
                    if link_count > text_count and link_count > 2:
                        continue

                start = text.find(line)
                out.append((title, start))
                break
        if not out:
            out.append((None, 0))
        return out

    def _split_regex(
        self,
        text: str,
        min_size: int,
        max_size: int,
        overlap: int,
    ) -> list[tuple[str, str | None]]:
        detected = self._detect_sections(text)
        if len(detected) == 1 and detected[0][0] is None:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=max_size, chunk_overlap=overlap, length_function=len
            )
            return [(c, None) for c in splitter.split_text(text)]

        sections: list[tuple[str, str | None]] = []
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_size, chunk_overlap=overlap, length_function=len
        )

        for i, (title, start) in enumerate(detected):
            cleaned_title = _clean_title(title) if title else None
            end = detected[i + 1][1] if i + 1 < len(detected) else len(text)
            content = text[start:end].strip()

            if title and not cleaned_title and sections:
                prev_content, prev_title = sections[-1]
                sections[-1] = (prev_content + "\n\n" + content, prev_title)
                continue

            if len(content) < min_size and sections:
                prev_content, prev_title = sections[-1]
                sections[-1] = (prev_content + "\n\n" + content, prev_title or cleaned_title)
                continue

            if len(content) > max_size:
                for j, chunk in enumerate(splitter.split_text(content)):
                    chunk_title = f"{cleaned_title} (Part {j + 1})" if cleaned_title else None
                    sections.append((chunk, chunk_title))
            else:
                sections.append((content, cleaned_title))

        return [(c, t) for c, t in sections if c.strip()]

    def split(
        self,
        text: str,
        min_size: int = 2_000,
        max_size: int = 20_000,
        overlap: int = 200,
    ) -> list[tuple[str, str | None]]:
        if self.is_markdown(text):
            return self._split_markdown(text, min_size, max_size, overlap)
        return self._split_regex(text, min_size, max_size, overlap)


def split_into_sections(
    text: str,
    min_size: int = 2_000,
    max_size: int = 20_000,
    overlap: int = 200,
) -> list[tuple[str, str | None]]:
    return HierarchicalSplitter().split(text, min_size, max_size, overlap)
