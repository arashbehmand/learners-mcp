"""Server-side evaluation of phase responses.

Called (optionally) by the host agent after the learner produces a phase
response. Returns a structured analysis (strengths / gaps / misconceptions /
suggested follow-ups / verdict) with `[§N]` citations and persists it for
history. The host can surface it directly to the learner or keep it as
internal feedback — plan §9-3 explicitly wanted this to be opt-in, not a
replacement for host coaching.
"""

from __future__ import annotations

from typing import Any

from ..db import DB
from ..language import detect_source_language, language_instruction
from ..llm.client import LLM, cached_source, plain
from ..llm.prompts import EVALUATE_PHASE_SYSTEM, EVALUATE_PHASE_USER_TEMPLATE
from .phases import PHASES


async def evaluate_phase_response(
    db: DB,
    llm: LLM,
    section_id: int,
    phase: str,
    response: str | None = None,
) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")

    section = db.get_section(section_id)
    if section is None:
        raise KeyError(f"section {section_id} not found")

    # If no explicit response is passed, pull the one previously recorded.
    if response is None:
        stored = (section.phase_data or {}).get(phase, {}) or {}
        response = stored.get("response") or ""
    if not response.strip():
        raise ValueError(
            f"no response available for section {section_id} phase '{phase}' — "
            "record_phase_response first, or pass response= explicitly"
        )

    source_body = section.notes or section.content
    context_blocks = cached_source(
        label=f"SECTION §{section.order_index}: {section.title or '(untitled)'}",
        body=source_body,
    )
    if section.rolling_summary:
        context_blocks += plain(
            "\n\n## Rolling summary (prior sections)\n\n" + section.rolling_summary
        )

    user = language_instruction(detect_source_language(source_body)) + "\n\n"
    user += EVALUATE_PHASE_USER_TEMPLATE.format(phase=phase) + (
        f"\n\n## Learner's {phase}-phase response\n\n{response}"
    )
    analysis = await llm.complete_json(
        task="phase_evaluation",
        system=EVALUATE_PHASE_SYSTEM,
        blocks=context_blocks + plain(user),
        max_tokens=2048,
        temperature=0.2,
    )

    md = _render_evaluation_markdown(analysis, section.order_index, phase)
    evaluation_id = db.add_evaluation(
        section_id=section_id,
        phase=phase,
        response=response,
        analysis_json=analysis,
        analysis_markdown=md,
    )
    return {
        "evaluation_id": evaluation_id,
        "section_id": section_id,
        "phase": phase,
        "analysis": analysis,
        "markdown": md,
    }


def _render_evaluation_markdown(
    analysis: dict[str, Any], order_index: int, phase: str
) -> str:
    verdict = analysis.get("verdict", "partial")
    out = [f"# Evaluation — §{order_index}, {phase} phase\n"]
    out.append(f"**Verdict:** {verdict}\n")

    strengths = analysis.get("strengths") or []
    if strengths:
        out.append("\n## Strengths\n")
        for s in strengths:
            out.append(f"- {s}")

    gaps = analysis.get("gaps") or []
    if gaps:
        out.append("\n## Gaps\n")
        for g in gaps:
            sections = ", ".join(f"§{s}" for s in (g.get("sections") or []))
            out.append(
                f"- **{g.get('concept', '?')}** ({sections}) — {g.get('evidence', '')}"
            )

    misconceptions = analysis.get("misconceptions") or []
    if misconceptions:
        out.append("\n## Misconceptions\n")
        for m in misconceptions:
            sections = ", ".join(f"§{s}" for s in (m.get("sections") or []))
            out.append(
                f"- You said: _{m.get('claim', '?')}_. Correction: {m.get('correct', '')} ({sections})"
            )

    followups = analysis.get("suggested_followups") or []
    if followups:
        out.append("\n## Suggested follow-ups\n")
        for f in followups:
            out.append(f"- {f}")

    return "\n".join(out) + "\n"
