from __future__ import annotations

from functools import lru_cache
from typing import Any

from lingua import Language, LanguageDetectorBuilder


_UNKNOWN_LANGUAGE_NAME = "the source material's dominant language"
_RIGHT_TO_LEFT = {
    Language.ARABIC,
    Language.HEBREW,
    Language.PERSIAN,
    Language.URDU,
}
_DISPLAY_NAMES = {
    Language.PERSIAN: "Persian/Farsi",
}


def detect_source_language(text: str) -> dict[str, Any]:
    """Return a small language profile for artifact generation/rendering."""
    sample = text.strip()[:200_000]
    if not sample:
        return _profile("und", _UNKNOWN_LANGUAGE_NAME, direction="auto")

    language = _detector().detect_language_of(sample)
    if language is None:
        return _profile("und", _UNKNOWN_LANGUAGE_NAME, direction="auto")

    code = language.iso_code_639_1.name.lower()
    return _profile(code, _display_name(language), direction=_direction(language))


def language_instruction(info: dict[str, Any]) -> str:
    return str(
        info.get("artifact_instruction")
        or _profile("und", _UNKNOWN_LANGUAGE_NAME)["artifact_instruction"]
    )


def _profile(code: str, name: str, *, direction: str = "auto") -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "direction": direction,
        "artifact_instruction": (
            f"Write the generated learner artifact in {name}, matching the source "
            "material's language. Preserve original technical terms, names, "
            "citations, and quoted text when useful."
        ),
    }


@lru_cache(maxsize=1)
def _detector():
    return LanguageDetectorBuilder.from_all_languages().with_low_accuracy_mode().build()


def _display_name(language: Language) -> str:
    if language in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[language]
    return language.name.replace("_", " ").title()


def _direction(language: Language) -> str:
    return "rtl" if language in _RIGHT_TO_LEFT else "ltr"
