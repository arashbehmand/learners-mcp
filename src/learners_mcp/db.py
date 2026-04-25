"""SQLite layer — thin repository over raw sqlite3.

Schema follows plan §5. Dataclasses are used as lightweight row types; no ORM.
All writes happen inside short-lived connections. WAL mode is enabled so
concurrent readers don't block writers during long ingestion runs.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import db_path, ensure_data_dir
from .flashcards.sm2 import CardState

SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_type TEXT,
    source_ref TEXT,
    content_hash TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    ingestion_status TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    title TEXT,
    content TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    rolling_summary TEXT,
    current_phase TEXT NOT NULL DEFAULT 'preview',
    phase_data TEXT NOT NULL DEFAULT '{}',
    notes TEXT,
    focus_brief TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sections_material
    ON sections(material_id, order_index);

CREATE TABLE IF NOT EXISTS learning_maps (
    material_id INTEGER PRIMARY KEY REFERENCES materials(id) ON DELETE CASCADE,
    map_json TEXT NOT NULL,
    map_markdown TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    regeneration_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS flashcards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    section_id INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    ease_factor REAL NOT NULL DEFAULT 2.5,
    interval_days INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    next_review TEXT NOT NULL,
    is_mastered INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_flashcards_due
    ON flashcards(next_review) WHERE is_mastered = 0;

CREATE TABLE IF NOT EXISTS completion_reports (
    section_id INTEGER PRIMARY KEY REFERENCES sections(id) ON DELETE CASCADE,
    report_markdown TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    phase TEXT NOT NULL,
    response TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    analysis_markdown TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evaluations_section_phase
    ON evaluations(section_id, phase);

CREATE TABLE IF NOT EXISTS review_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flashcard_id INTEGER NOT NULL REFERENCES flashcards(id) ON DELETE CASCADE,
    reviewed_at TEXT NOT NULL,
    knew_it INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_events_time
    ON review_events(reviewed_at);

CREATE INDEX IF NOT EXISTS idx_review_events_card
    ON review_events(flashcard_id, reviewed_at);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ----------------------------- row types -----------------------------


@dataclass
class Material:
    id: int
    title: str
    source_type: str | None
    source_ref: str | None
    content_hash: str
    created_at: datetime
    ingestion_status: dict[str, Any] = field(default_factory=dict)


@dataclass
class Section:
    id: int
    material_id: int
    title: str | None
    content: str
    order_index: int
    rolling_summary: str | None
    current_phase: str
    phase_data: dict[str, Any]
    notes: str | None
    focus_brief: dict[str, Any] | None
    completed_at: datetime | None


@dataclass
class LearningMap:
    material_id: int
    map_json: dict[str, Any]
    map_markdown: str
    generated_at: datetime
    regeneration_count: int


@dataclass
class Flashcard:
    id: int
    material_id: int
    section_id: int | None
    question: str
    answer: str
    ease_factor: float
    interval_days: int
    review_count: int
    next_review: datetime
    is_mastered: bool
    created_at: datetime

    def card_state(self) -> CardState:
        return CardState(
            ease_factor=self.ease_factor,
            interval_days=self.interval_days,
            review_count=self.review_count,
            next_review=self.next_review,
            is_mastered=self.is_mastered,
        )


# ----------------------------- repository -----------------------------


class DB:
    """Thin repository. One instance per server process."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else db_path()
        ensure_data_dir()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -------- materials --------

    def create_material(
        self,
        title: str,
        source_type: str | None,
        source_ref: str | None,
        hash_: str,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO materials(title, source_type, source_ref, content_hash, created_at, ingestion_status) "
                "VALUES (?,?,?,?,?,?)",
                (
                    title,
                    source_type,
                    source_ref,
                    hash_,
                    _iso(datetime.now(timezone.utc)),
                    "{}",
                ),
            )
            return int(cur.lastrowid)

    def find_material_by_hash(self, hash_: str) -> Material | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM materials WHERE content_hash = ?", (hash_,)
            ).fetchone()
        return _row_to_material(row) if row else None

    def get_material(self, material_id: int) -> Material | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
        return _row_to_material(row) if row else None

    def list_materials(self) -> list[Material]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM materials ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_material(r) for r in rows]

    def set_ingestion_status(self, material_id: int, status: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE materials SET ingestion_status = ? WHERE id = ?",
                (json.dumps(status), material_id),
            )

    # -------- sections --------

    def create_section(
        self,
        material_id: int,
        title: str | None,
        content: str,
        order_index: int,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO sections(material_id, title, content, order_index, current_phase, phase_data) "
                "VALUES (?,?,?,?,?,?)",
                (material_id, title, content, order_index, "preview", "{}"),
            )
            return int(cur.lastrowid)

    def get_section(self, section_id: int) -> Section | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sections WHERE id = ?", (section_id,)
            ).fetchone()
        return _row_to_section(row) if row else None

    def get_sections(self, material_id: int) -> list[Section]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sections WHERE material_id = ? ORDER BY order_index",
                (material_id,),
            ).fetchall()
        return [_row_to_section(r) for r in rows]

    def update_section_field(
        self, section_id: int, field_name: str, value: Any
    ) -> None:
        allowed = {
            "rolling_summary",
            "notes",
            "focus_brief",
            "current_phase",
            "completed_at",
        }
        if field_name not in allowed:
            raise ValueError(f"field '{field_name}' not updatable")
        if field_name == "focus_brief" and value is not None:
            value = json.dumps(value)
        elif field_name == "completed_at" and isinstance(value, datetime):
            value = _iso(value)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE sections SET {field_name} = ? WHERE id = ?",
                (value, section_id),
            )

    def update_phase_data(
        self, section_id: int, phase: str, data: dict[str, Any]
    ) -> None:
        """Merge new phase data into the section's phase_data JSON blob."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT phase_data FROM sections WHERE id = ?", (section_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"section {section_id} not found")
            blob: dict[str, Any] = json.loads(row["phase_data"] or "{}")
            blob[phase] = data
            conn.execute(
                "UPDATE sections SET phase_data = ? WHERE id = ?",
                (json.dumps(blob), section_id),
            )

    # -------- learning maps --------

    def upsert_learning_map(
        self,
        material_id: int,
        map_json: dict[str, Any],
        map_markdown: str,
    ) -> None:
        now = _iso(datetime.now(timezone.utc))
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT regeneration_count FROM learning_maps WHERE material_id = ?",
                (material_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE learning_maps SET map_json = ?, map_markdown = ?, "
                    "generated_at = ?, regeneration_count = ? WHERE material_id = ?",
                    (
                        json.dumps(map_json),
                        map_markdown,
                        now,
                        existing["regeneration_count"] + 1,
                        material_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO learning_maps(material_id, map_json, map_markdown, generated_at, regeneration_count) "
                    "VALUES (?,?,?,?,0)",
                    (material_id, json.dumps(map_json), map_markdown, now),
                )

    def get_learning_map(self, material_id: int) -> LearningMap | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM learning_maps WHERE material_id = ?",
                (material_id,),
            ).fetchone()
        if not row:
            return None
        return LearningMap(
            material_id=row["material_id"],
            map_json=json.loads(row["map_json"]),
            map_markdown=row["map_markdown"],
            generated_at=_parse_iso(row["generated_at"]),
            regeneration_count=row["regeneration_count"],
        )

    # -------- flashcards --------

    def create_flashcard(
        self,
        material_id: int,
        section_id: int | None,
        question: str,
        answer: str,
        created_at: datetime | None = None,
    ) -> int:
        now = created_at or datetime.now(timezone.utc)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO flashcards(material_id, section_id, question, answer, "
                "ease_factor, interval_days, review_count, next_review, is_mastered, created_at) "
                "VALUES (?,?,?,?,2.5,0,0,?,0,?)",
                (material_id, section_id, question, answer, _iso(now), _iso(now)),
            )
            return int(cur.lastrowid)

    def get_flashcard(self, flashcard_id: int) -> Flashcard | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM flashcards WHERE id = ?", (flashcard_id,)
            ).fetchone()
        return _row_to_flashcard(row) if row else None

    def list_flashcards(
        self,
        material_id: int | None = None,
        section_id: int | None = None,
        filter_: str = "all",
    ) -> list[Flashcard]:
        """filter_: 'all' | 'due' | 'mastered'."""
        clauses: list[str] = []
        params: list[Any] = []
        if material_id is not None:
            clauses.append("material_id = ?")
            params.append(material_id)
        if section_id is not None:
            clauses.append("section_id = ?")
            params.append(section_id)
        if filter_ == "due":
            clauses.append("is_mastered = 0 AND next_review <= ?")
            params.append(_iso(datetime.now(timezone.utc)))
        elif filter_ == "mastered":
            clauses.append("is_mastered = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM flashcards {where} ORDER BY next_review ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_flashcard(r) for r in rows]

    def apply_review(self, flashcard_id: int, new_state: CardState) -> None:
        """Mutate SM-2 state only. Does NOT log a review event.

        Used for seeding state during import or for synthetic time-travel in
        tests. For a real learner review, use `record_review` so streak /
        weekly-report metrics reflect actual study activity.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE flashcards SET ease_factor = ?, interval_days = ?, "
                "review_count = ?, next_review = ?, is_mastered = ? WHERE id = ?",
                (
                    new_state.ease_factor,
                    new_state.interval_days,
                    new_state.review_count,
                    _iso(new_state.next_review),
                    1 if new_state.is_mastered else 0,
                    flashcard_id,
                ),
            )

    def record_review(
        self,
        flashcard_id: int,
        new_state: CardState,
        knew_it: bool,
        reviewed_at: datetime | None = None,
    ) -> int:
        """Update SM-2 state AND log a review event. Returns event id."""
        when = reviewed_at or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "UPDATE flashcards SET ease_factor = ?, interval_days = ?, "
                "review_count = ?, next_review = ?, is_mastered = ? WHERE id = ?",
                (
                    new_state.ease_factor,
                    new_state.interval_days,
                    new_state.review_count,
                    _iso(new_state.next_review),
                    1 if new_state.is_mastered else 0,
                    flashcard_id,
                ),
            )
            cur = conn.execute(
                "INSERT INTO review_events(flashcard_id, reviewed_at, knew_it) "
                "VALUES (?,?,?)",
                (flashcard_id, _iso(when), 1 if knew_it else 0),
            )
            return int(cur.lastrowid)

    def list_review_events(
        self,
        flashcard_id: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if flashcard_id is not None:
            clauses.append("flashcard_id = ?")
            params.append(flashcard_id)
        if since is not None:
            clauses.append("reviewed_at >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("reviewed_at < ?")
            params.append(_iso(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM review_events {where} ORDER BY reviewed_at"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": r["id"],
                "flashcard_id": r["flashcard_id"],
                "reviewed_at": _parse_iso(r["reviewed_at"]),
                "knew_it": bool(r["knew_it"]),
            }
            for r in rows
        ]

    # -------- completion reports --------

    def upsert_completion_report(self, section_id: int, report_md: str) -> None:
        now = _iso(datetime.now(timezone.utc))
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO completion_reports(section_id, report_markdown, generated_at) "
                "VALUES (?,?,?)",
                (section_id, report_md, now),
            )

    def get_completion_report(self, section_id: int) -> tuple[str, datetime] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT report_markdown, generated_at FROM completion_reports "
                "WHERE section_id = ?",
                (section_id,),
            ).fetchone()
        if not row:
            return None
        return row["report_markdown"], _parse_iso(row["generated_at"])

    # -------- evaluations --------

    def add_evaluation(
        self,
        section_id: int,
        phase: str,
        response: str,
        analysis_json: dict[str, Any],
        analysis_markdown: str,
    ) -> int:
        now = _iso(datetime.now(timezone.utc))
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO evaluations(section_id, phase, response, "
                "analysis_json, analysis_markdown, generated_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    section_id,
                    phase,
                    response,
                    json.dumps(analysis_json),
                    analysis_markdown,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def list_evaluations(
        self, section_id: int, phase: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM evaluations WHERE section_id = ?"
        params: list[Any] = [section_id]
        if phase is not None:
            query += " AND phase = ?"
            params.append(phase)
        query += " ORDER BY generated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": r["id"],
                "section_id": r["section_id"],
                "phase": r["phase"],
                "response": r["response"],
                "analysis": json.loads(r["analysis_json"]),
                "markdown": r["analysis_markdown"],
                "generated_at": r["generated_at"],
            }
            for r in rows
        ]


# ----------------------------- row parsers -----------------------------


def _row_to_material(row: sqlite3.Row) -> Material:
    return Material(
        id=row["id"],
        title=row["title"],
        source_type=row["source_type"],
        source_ref=row["source_ref"],
        content_hash=row["content_hash"],
        created_at=_parse_iso(row["created_at"]),
        ingestion_status=json.loads(row["ingestion_status"] or "{}"),
    )


def _row_to_section(row: sqlite3.Row) -> Section:
    return Section(
        id=row["id"],
        material_id=row["material_id"],
        title=row["title"],
        content=row["content"],
        order_index=row["order_index"],
        rolling_summary=row["rolling_summary"],
        current_phase=row["current_phase"],
        phase_data=json.loads(row["phase_data"] or "{}"),
        notes=row["notes"],
        focus_brief=json.loads(row["focus_brief"]) if row["focus_brief"] else None,
        completed_at=_parse_iso(row["completed_at"]),
    )


def _row_to_flashcard(row: sqlite3.Row) -> Flashcard:
    return Flashcard(
        id=row["id"],
        material_id=row["material_id"],
        section_id=row["section_id"],
        question=row["question"],
        answer=row["answer"],
        ease_factor=row["ease_factor"],
        interval_days=row["interval_days"],
        review_count=row["review_count"],
        next_review=_parse_iso(row["next_review"]),
        is_mastered=bool(row["is_mastered"]),
        created_at=_parse_iso(row["created_at"]),
    )
