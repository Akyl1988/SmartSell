from __future__ import annotations

from pathlib import Path

from app.models.campaign import MessageStatus


def test_message_status_has_no_uppercase_sending() -> None:
    values = {status.value for status in MessageStatus}
    assert "SENDING" not in values


def test_migrations_do_not_add_uppercase_sending() -> None:
    versions_dir = Path("migrations") / "versions"
    allowlist = {"20260213_message_status_sending.py"}

    offenders: list[str] = []
    for path in versions_dir.glob("*.py"):
        if path.name in allowlist:
            continue
        text = path.read_text(encoding="utf-8")
        if "message_status" in text and "ADD VALUE" in text and "'SENDING'" in text:
            offenders.append(path.name)

    assert not offenders, f"Uppercase SENDING found in: {', '.join(sorted(offenders))}"
