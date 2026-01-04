from __future__ import annotations

import re
from pathlib import Path


def test_no_company_id_params_in_api_v1() -> None:
    """
    Guardrail: tenant scope must come from token/user only.
    API v1 must not accept company_id as Query/Path/Body/Field parameter.
    """
    repo_root = Path(__file__).resolve().parents[1]
    v1_root = repo_root / "app" / "api" / "v1"

    offenders: list[str] = []
    # matches: company_id: ... = Query(...), Path(...), Body(...), Field(...)
    pattern = re.compile(r"(?m)^\s*company_id\s*:\s*[^#\n]+=\s*(Query|Path|Body|Field)\(")

    for p in v1_root.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if pattern.search(text):
            offenders.append(str(p.relative_to(repo_root)))

    assert not offenders, "Forbidden company_id params found in: " + ", ".join(offenders)
