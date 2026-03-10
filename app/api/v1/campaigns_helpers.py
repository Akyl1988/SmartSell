from __future__ import annotations

from datetime import UTC, datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _utcnow().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_dt(value: str) -> datetime:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _normalize_tags(tags: list[str] | None) -> list[str]:
    norm: list[str] = []
    seen = set()
    for t in tags or []:
        tt = (t or "").strip().lower()
        if not tt:
            continue
        if tt not in seen:
            seen.add(tt)
            norm.append(tt)
    return norm
