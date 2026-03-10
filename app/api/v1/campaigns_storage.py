from __future__ import annotations

import os
import threading
from typing import Any

from app.api.v1.campaigns_helpers import _normalize_tags, _now_iso


class InMemoryStorage:
    """
    Простое и потокобезопасное in-memory хранилище для кампаний и сообщений.
    Интерфейс спроектирован так, чтобы легко заменить реализацией на SQL.
    """

    def __init__(self) -> None:
        self._db: dict[str, dict[int, Any]] = {"campaigns": {}, "messages": {}}
        self._seq: dict[str, int] = {"campaigns": 0, "messages": 0}
        self._lock = threading.RLock()
        self._seed_once()

    def next_id(self, kind: str) -> int:
        with self._lock:
            self._seq[kind] = (self._seq.get(kind, 0) or 0) + 1
            return self._seq[kind]

    def get_campaign(self, cid: int) -> dict[str, Any] | None:
        with self._lock:
            c = self._db["campaigns"].get(cid)
            return dict(c) if c else None

    def save_campaign(self, data: dict[str, Any]) -> None:
        with self._lock:
            data = dict(data)
            cid = int(data["id"])
            data["tags"] = _normalize_tags(data.get("tags"))
            msgs = []
            for m in data.get("messages") or []:
                if isinstance(m, dict):
                    msgs.append(dict(m))
                else:
                    msgs.append(dict(getattr(m, "__dict__", {})))
            data["messages"] = msgs
            self._db["campaigns"][cid] = data

    def delete_campaign(self, cid: int) -> None:
        with self._lock:
            data = self._db["campaigns"].pop(cid, None)
            if not data:
                return
            for m in data.get("messages") or []:
                mid = int(m.get("id")) if isinstance(m, dict) else int(getattr(m, "id", 0))
                self._db["messages"].pop(mid, None)

    def list_campaigns(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._db["campaigns"].values()]

    def title_exists(
        self,
        title: str,
        exclude_id: int | None = None,
        owner: str | None = None,
        company_id: int | None = None,
    ) -> bool:
        t = (title or "").strip().lower()
        own = (owner or "").strip().lower() if owner else None
        filter_company_id = int(company_id) if company_id is not None else None
        with self._lock:
            for cid, data in self._db["campaigns"].items():
                if exclude_id is not None and cid == exclude_id:
                    continue
                if data.get("archived"):
                    continue
                if own is not None and (data.get("owner") or "").strip().lower() != own:
                    continue
                if filter_company_id is not None:
                    try:
                        row_cid = int(data.get("company_id")) if data.get("company_id") is not None else None
                    except Exception:
                        row_cid = None
                    if row_cid != filter_company_id:
                        continue
                other = (data.get("title") or "").strip().lower()
                if t == other:
                    return True
        return False

    def get_message(self, mid: int) -> dict[str, Any] | None:
        with self._lock:
            m = self._db["messages"].get(mid)
            return dict(m) if m else None

    def list_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._db["messages"].values()]

    def save_message(self, mid: int, payload: dict[str, Any]) -> None:
        with self._lock:
            self._db["messages"][mid] = dict(payload)

    def delete_message(self, mid: int) -> None:
        with self._lock:
            self._db["messages"].pop(mid, None)

    def _ensure_seed_campaign(self, campaign_id: int, title: str, active: bool = True) -> None:
        if campaign_id not in self._db["campaigns"]:
            self._db["campaigns"][campaign_id] = {
                "id": campaign_id,
                "title": title,
                "description": f"{title} campaign for tests",
                "active": active,
                "archived": False,
                "tags": ["test"],
                "messages": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "owner": "demo",
                "schedule": None,
            }

    def _seed_once(self) -> None:
        running_pytest = "PYTEST_CURRENT_TEST" in os.environ
        seed_enabled = os.getenv("SMARTSELL_SEED_CAMPAIGNS")
        do_seed = (seed_enabled == "1") or (seed_enabled is None and not running_pytest)
        if do_seed and not self._db["campaigns"]:
            self._db["campaigns"][1] = {
                "id": 1,
                "title": "Demo",
                "description": "Demo campaign",
                "active": True,
                "archived": False,
                "tags": ["test"],
                "messages": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "owner": "demo",
                "schedule": None,
            }
            self._db["campaigns"][999] = {
                "id": 999,
                "title": "Draft",
                "description": "Draft campaign",
                "active": False,
                "archived": False,
                "tags": ["draft"],
                "messages": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "owner": "demo",
                "schedule": None,
            }
            self._ensure_seed_campaign(1001, "Seeded")
            self._ensure_seed_campaign(1002, "Seeded 2")
            self._ensure_seed_campaign(1003, "Seeded 3")

        current_max = max(self._db["campaigns"].keys()) if self._db["campaigns"] else 0
        self._seq["campaigns"] = max(self._seq.get("campaigns", 0), current_max, 1003)
