from __future__ import annotations

import re
from pathlib import Path


def test_no_allow_platform_override_true_in_api_v1() -> None:
    """
    Guardrail: API v1 must not allow platform_admin to override tenant scope via request params.
    If someone re-introduces allow_platform_override=True in any v1 router, fail fast.
    """
    repo_root = Path(__file__).resolve().parents[1]
    v1_root = repo_root / "app" / "api" / "v1"

    offenders: list[str] = []
    pattern = re.compile(r"allow_platform_override\s*=\s*True")

    for p in v1_root.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if pattern.search(text):
            offenders.append(str(p.relative_to(repo_root)))

    assert not offenders, "Found forbidden allow_platform_override=True in: " + ", ".join(offenders)
