from __future__ import annotations

from learners_mcp.llm import prompts
from learners_mcp.study import qa

ARTIFACT_SYSTEM_PROMPTS = [
    prompts.MAP_SYSTEM,
    prompts.TLDR_SYSTEM,
    prompts.REDUCE_SYSTEM,
    prompts.CONSISTENCY_SYSTEM,
    prompts.LEARNING_MAP_SYSTEM,
    prompts.FOCUS_BRIEF_SYSTEM,
    prompts.ROLLING_SUMMARY_SYSTEM,
    prompts.SUGGEST_CARDS_SYSTEM,
    prompts.COMPLETION_REPORT_SYSTEM,
    prompts.EVALUATE_PHASE_SYSTEM,
]

INTERACTION_SYSTEM_PROMPTS = [
    prompts.PREVIEW_COACH_SYSTEM,
    prompts.EXPLAIN_COACH_SYSTEM,
    prompts.QUESTION_COACH_SYSTEM,
    prompts.ANCHOR_COACH_SYSTEM,
    qa.ANSWER_SYSTEM,
]


def test_all_artifact_generation_prompts_use_source_language_policy():
    for system_prompt in ARTIFACT_SYSTEM_PROMPTS:
        assert prompts.SOURCE_ARTIFACT_LANGUAGE_POLICY in system_prompt


def test_all_user_interaction_prompts_use_user_language_policy():
    for system_prompt in INTERACTION_SYSTEM_PROMPTS:
        assert prompts.USER_INTERACTION_LANGUAGE_POLICY in system_prompt
