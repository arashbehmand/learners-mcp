"""Combined-notes Markdown export."""

from __future__ import annotations

from pathlib import Path

from ..db import DB


def export_notes_markdown(db: DB, material_id: int, output_path: Path) -> int:
    """Write concatenated per-section notes to a single .md file.

    Returns the number of sections whose notes were included.
    """
    material = db.get_material(material_id)
    if material is None:
        raise KeyError(f"material {material_id} not found")
    sections = db.get_sections(material_id)

    parts: list[str] = [f"# {material.title}\n"]
    included = 0
    for s in sections:
        title = s.title or "(untitled)"
        parts.append(f"\n## §{s.order_index}: {title}\n")
        if s.notes:
            parts.append(s.notes)
            included += 1
        else:
            parts.append("_Notes pending — run `prepare_material`._")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts), encoding="utf-8")
    return included
