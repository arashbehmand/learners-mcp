from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import llm_config_path

TASKS: frozenset[str] = frozenset(
    {
        "notes_map",
        "notes_tldr",
        "notes_reduce",
        "notes_polish",
        "focus_brief",
        "learning_map",
        "rolling_summary",
        "qa",
        "phase_evaluation",
        "completion_report",
        "flashcards",
    }
)


@dataclass
class Profile:
    name: str
    model: str
    params: dict = field(default_factory=dict)
    prompt_cache: str = "auto"
    api_base: str | None = None
    api_key_env: str | None = None


BUILT_IN_PROFILES: dict[str, Profile] = {
    "fast": Profile(name="fast", model="claude-haiku-4-5-20251001"),
    "default": Profile(name="default", model="claude-sonnet-4-6"),
    "oneshot": Profile(name="oneshot", model="claude-opus-4-7"),
}

BUILT_IN_ROUTES: dict[str, str] = {
    "notes_map": "fast",
    "notes_tldr": "fast",
    "focus_brief": "fast",
    "notes_reduce": "default",
    "notes_polish": "default",
    "rolling_summary": "default",
    "qa": "default",
    "phase_evaluation": "default",
    "completion_report": "default",
    "flashcards": "default",
    "learning_map": "oneshot",
}


def load_config() -> tuple[dict[str, Profile], dict[str, str]]:
    profiles: dict[str, Profile] = {
        name: Profile(
            name=p.name,
            model=p.model,
            params=dict(p.params),
            prompt_cache=p.prompt_cache,
            api_base=p.api_base,
            api_key_env=p.api_key_env,
        )
        for name, p in BUILT_IN_PROFILES.items()
    }
    routes: dict[str, str] = dict(BUILT_IN_ROUTES)

    config_path_override = os.environ.get("LEARNERS_MCP_LLM_CONFIG")
    config_file = (
        Path(config_path_override) if config_path_override else llm_config_path()
    )

    if config_file.exists():
        with open(config_file) as f:
            data = yaml.safe_load(f) or {}

        for pname, pdata in (data.get("profiles") or {}).items():
            profiles[pname] = Profile(
                name=pname,
                model=pdata["model"],
                params=dict(pdata.get("params") or {}),
                prompt_cache=pdata.get("prompt_cache", "auto"),
                api_base=pdata.get("api_base"),
                api_key_env=pdata.get("api_key_env"),
            )

        for task, pname in (data.get("routes") or {}).items():
            routes[task] = pname

    for name in list(profiles):
        model_env = os.environ.get(f"LEARNERS_MCP_MODEL_{name.upper()}")
        if model_env:
            profiles[name].model = model_env

        params_env = os.environ.get(f"LEARNERS_MCP_PARAMS_{name.upper()}")
        if params_env:
            profiles[name].params = json.loads(params_env)

    for task in list(routes):
        route_env = os.environ.get(f"LEARNERS_MCP_ROUTE_{task.upper()}")
        if route_env:
            routes[task] = route_env

    return profiles, routes


def resolve(task: str) -> Profile:
    if task not in TASKS:
        raise ValueError(f"Unknown task {task!r}. Available tasks: {sorted(TASKS)}")
    profiles, routes = load_config()
    profile_name = routes[task]
    if profile_name not in profiles:
        raise ValueError(
            f"Route for task {task!r} points to unknown profile {profile_name!r}. "
            f"Available profiles: {sorted(profiles)}"
        )
    return profiles[profile_name]
