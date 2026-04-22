from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType


def test_claude_launcher_delegates_to_server_main(monkeypatch):
    events: list[str] = []
    script = Path(__file__).resolve().parents[1] / ".claude" / "start-learners-mcp.py"

    fake_server = ModuleType("learners_mcp.server")

    def fake_server_main() -> None:
        events.append("server_main")

    fake_server.main = fake_server_main  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "learners_mcp.server", fake_server)

    namespace = runpy.run_path(str(script))
    namespace["main"]()

    assert events == ["server_main"]
