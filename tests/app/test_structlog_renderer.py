from __future__ import annotations

import structlog


def test_structlog_renderer_not_console() -> None:
    processors = structlog.get_config().get("processors", [])
    has_console = any("ConsoleRenderer" in type(proc).__name__ for proc in processors)
    has_json = any("JSONRenderer" in type(proc).__name__ for proc in processors)
    assert not has_console
    assert has_json
