"""Configuration: paths, model names, tunables.

Paths are resolved from the environment *on every call*, not cached at
import time. That's what lets `LEARNERS_MCP_DATA_DIR` actually redirect
storage — including inside test `monkeypatch.setenv` fixtures that run
after the first import.
"""

from __future__ import annotations

import os
from pathlib import Path


CHUNK_SIZE = 30_000
CHUNK_OVERLAP = 2_000
MIN_SECTION_SIZE = 2_000
MAX_SECTION_SIZE = 20_000
SECTION_OVERLAP = 200

ROLLING_CONTEXT_MAX_CHARS = 2_000


def data_dir() -> Path:
    """Resolve the data directory fresh from the environment each call."""
    override = os.environ.get("LEARNERS_MCP_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".learners-mcp"


def db_path() -> Path:
    return data_dir() / "db.sqlite"


def ensure_data_dir() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def llm_config_path() -> Path:
    return data_dir() / "llm.yaml"


def artifact_dir() -> Path:
    """Default directory for learner-readable generated artifacts."""
    override = os.environ.get("LEARNERS_MCP_ARTIFACT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.cwd() / "learners"


def artifact_mirror_enabled() -> bool:
    value = os.environ.get("LEARNERS_MCP_ARTIFACT_MIRROR", "on").strip().lower()
    return value not in {"0", "false", "no", "off"}
