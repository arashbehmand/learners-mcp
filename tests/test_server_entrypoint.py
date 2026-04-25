from __future__ import annotations

import json
import subprocess
import sys

from learners_mcp import server


def test_importing_server_does_not_load_heavy_runtime_dependencies():
    code = (
        "import json, sys, time; "
        "start=time.perf_counter(); "
        "import learners_mcp.server; "
        "print(json.dumps({"
        "'seconds': round(time.perf_counter()-start, 3), "
        "'litellm': 'litellm' in sys.modules, "
        "'langchain_text_splitters': 'langchain_text_splitters' in sys.modules, "
        "'mcp_client_stdio': 'mcp.client.stdio' in sys.modules"
        "}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)

    assert payload["litellm"] is False
    assert payload["langchain_text_splitters"] is False
    assert payload["mcp_client_stdio"] is False
    assert payload["seconds"] < 8


def test_main_preloads_markitdown_before_stdio_run(monkeypatch):
    events: list[str] = []

    def fake_run(*, transport):
        events.append(f"run:{transport}")

    def fake_preload_markitdown(source=None):
        events.append(f"preload:{source}")

    monkeypatch.delenv("LEARNERS_MCP_PRELOAD_MARKITDOWN", raising=False)
    monkeypatch.setattr(server, "loader_preload_markitdown", fake_preload_markitdown)
    monkeypatch.setattr(server.mcp, "run", fake_run)

    server.main()

    assert events == ["preload:None", "run:stdio"]


def test_main_can_disable_markitdown_preload(monkeypatch):
    events: list[str] = []

    def fake_run(*, transport):
        events.append(f"run:{transport}")

    def fake_preload_markitdown(source=None):
        events.append(f"preload:{source}")

    monkeypatch.setenv("LEARNERS_MCP_PRELOAD_MARKITDOWN", "off")
    monkeypatch.setattr(server, "loader_preload_markitdown", fake_preload_markitdown)
    monkeypatch.setattr(server.mcp, "run", fake_run)

    server.main()

    assert events == ["run:stdio"]
