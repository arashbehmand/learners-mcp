"""Anki exports.

- `.apkg` via genanki — double-clickable, merges cleanly on re-import because
  the deck ID is deterministic from the deck name.
- CSV — portable to Quizlet, Remnote, etc.

Ported from PECS-learner/utils/anki_export.py but trimmed: dropped the
AnkiConnect HTTP path because it requires the user to install the
AnkiConnect add-on and have Anki running — too much friction for an MCP
install story. If someone wants it we can add it back in v2.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import genanki  # type: ignore[import-not-found]

# Fixed model id keeps Anki recognising cards across re-exports.
PECS_MODEL_ID = 1607392319


def _deck_id(deck_name: str) -> int:
    h = hashlib.md5(deck_name.encode("utf-8"))
    as_int = int(h.hexdigest()[:8], 16)
    return (as_int % (1 << 30)) + (1 << 30)


def _model() -> "genanki.Model":
    return genanki.Model(
        PECS_MODEL_ID,
        "learners-mcp Basic",
        fields=[{"name": "Question"}, {"name": "Answer"}],
        templates=[
            {
                "name": "Card 1",
                "qfmt": '<div style="font-size:20px;text-align:center">{{Question}}</div>',
                "afmt": (
                    '{{FrontSide}}<hr id="answer">'
                    '<div style="font-size:18px;text-align:center">{{Answer}}</div>'
                ),
            }
        ],
        css=(
            ".card{font-family:arial;font-size:20px;text-align:center;"
            "color:black;background:white}"
        ),
    )


def export_apkg(
    cards: list[dict[str, str]],
    deck_name: str,
    output_path: Path,
    tags: list[str] | None = None,
) -> int:
    """Write an Anki package to output_path. Returns card count written."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cards:
        raise ValueError("no cards to export")

    deck = genanki.Deck(_deck_id(deck_name), deck_name)
    model = _model()
    for card in cards:
        q = card["question"].replace("\n", "<br>")
        a = card["answer"].replace("\n", "<br>")
        deck.add_note(genanki.Note(model=model, fields=[q, a], tags=tags or []))

    genanki.Package(deck).write_to_file(str(output_path))
    return len(cards)


def export_csv(cards: list[dict[str, str]], output_path: Path) -> int:
    """Write a simple question/answer CSV. Quizlet-compatible (tab or comma)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["question", "answer"])
        for card in cards:
            writer.writerow([card["question"], card["answer"]])
    return len(cards)
