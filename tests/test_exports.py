"""Anki + CSV + markdown exports — file-writing tests."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from learners_mcp.db import DB, content_hash
from learners_mcp.export.anki import export_apkg, export_csv
from learners_mcp.export.markdown import export_notes_markdown


def _mk_db(tmp_path: Path) -> DB:
    return DB(tmp_path / "t.sqlite")


def test_export_apkg_writes_file(tmp_path):
    out = tmp_path / "deck.apkg"
    cards = [
        {"question": "What is entropy?", "answer": "A measure of disorder. [§3]"},
        {"question": "SI units?", "answer": "J/K. [§3]"},
    ]
    n = export_apkg(cards, "Thermodynamics", out)
    assert n == 2
    assert out.exists()
    # genanki writes a zip archive — magic bytes PK.
    assert out.read_bytes()[:2] == b"PK"


def test_export_apkg_rejects_empty(tmp_path):
    with pytest.raises(ValueError):
        export_apkg([], "Empty", tmp_path / "empty.apkg")


def test_export_csv_round_trips(tmp_path):
    out = tmp_path / "cards.csv"
    cards = [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q, with comma", "answer": "A\"with quote"},
    ]
    n = export_csv(cards, out)
    assert n == 2
    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["question", "answer"]
    assert rows[1] == ["Q1", "A1"]
    assert rows[2][0] == "Q, with comma"
    assert rows[2][1] == 'A"with quote'


def test_export_notes_markdown(tmp_path):
    db = _mk_db(tmp_path)
    mid = db.create_material("Book", "txt", None, content_hash("x"))
    s1 = db.create_section(mid, "Chapter 1", "c1 body", 1)
    s2 = db.create_section(mid, "Chapter 2", "c2 body", 2)
    db.update_section_field(s1, "notes", "# §1 notes\n\ndetails")
    # Second section's notes deliberately pending.
    out = tmp_path / "notes.md"
    included = export_notes_markdown(db, mid, out)
    assert included == 1
    text = out.read_text(encoding="utf-8")
    assert "# Book" in text
    assert "§1: Chapter 1" in text
    assert "§2: Chapter 2" in text
    assert "# §1 notes" in text
    assert "Notes pending" in text  # placeholder for §2
