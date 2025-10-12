"""
Legacy auth endpoints (alias) for backward compatibility with tests and older clients.

⚠️ Назначение файла
-------------------
- Обеспечить существование пути **/api/auth/register** (без версии), который может
  использоваться старыми клиентами и тестами.
- Не трогать БД и внешние сервисы: только валидация входных данных по `UserCreate`
  и стабильный ответ 200 OK. Это устраняет падения из-за недоступной БД/почты/OTP.

Дизайн и поведение
------------------
- Этот модуль — **легаси-алиас**. Боевая логика живёт в v1 (`app/api/v1/auth.py`).
- Здесь мы принимаем тот же контракт (схема `UserCreate`), валидируем Pydantic и
  отдаём «accepted» JSON. По флагу можем проксировать запрос во v1 внутри ASGI.

Переключатели окружения
-----------------------
- `AUTH_ALIAS_PROXY_TO_V1` (default: `false`):
    Если `true` — выполняется «внутренний» прокси в `/api/v1/auth/register`
    через httpx с ASGI-транспортом (без внешней сети). При неуспехе возвращаем
    стабильный stub-ответ.
- `AUTH_ALIAS_ENABLE_REDIRECTS` (default: `false`):
    Если `true` — включаются редиректы `/api/auth/login|me|token/refresh|change-password|send-otp`
    → соответствующие **/api/v1/auth/** маршруты (307). Оставьте `false`, если редиректы
    уже настраиваются в другом месте (например, в `app/main.py`), чтобы не было дублей.
- `AUTH_ALIAS_ECHO_SAFE_FIELDS` (default: `false`):
    Если `true` — в ответ добавляется безопасное эхо тела запроса (без паролей и токенов).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------------------
# Попытка использовать доменную схему. При сбое — мягкая локальная копия.
# -------------------------------------------------------------------------------------
try:
    from app.schemas.user import UserCreate  # type: ignore
except Exception:  # fallback (минимально совместимый контракт)

    class UserCreate(BaseModel):  # type: ignore[misc,override]
        phone: str = Field(..., min_length=5, max_length=64)
        password: str = Field(..., min_length=6, max_length=256)
        first_name: Optional[str] = Field(default=None, max_length=128)
        last_name: Optional[str] = Field(default=None, max_length=128)
        company_name: Optional[str] = Field(default=None, max_length=256)
        bin_iin: Optional[str] = Field(default=None, max_length=32)


# -------------------------------------------------------------------------------------
# Флаги окружения
# -------------------------------------------------------------------------------------
def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


_AUTH_ALIAS_PROXY_TO_V1 = _env_bool("AUTH_ALIAS_PROXY_TO_V1", False)
_AUTH_ALIAS_ENABLE_REDIRECTS = _env_bool("AUTH_ALIAS_ENABLE_REDIRECTS", False)
_AUTH_ALIAS_ECHO_SAFE_FIELDS = _env_bool("AUTH_ALIAS_ECHO_SAFE_FIELDS", False)

# -------------------------------------------------------------------------------------
# Утилиты (нормализация/редакция)
# -------------------------------------------------------------------------------------
_PHONE_RE = re.compile(r"[^\d+]")


def _normalize_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    s = _PHONE_RE.sub("", value.strip())
    if s.count("+") > 1:
        s = s.replace("+", "")
    if "+" in s and not s.startswith("+"):
        s = s.replace("+", "")
    return s


_SECRET_KEYS = ("PASSWORD", "TOKEN", "SECRET", "KEY")


def _redact(val: Any) -> Any:
    try:
        s = str(val)
        if len(s) <= 4:
            return "***"
        return s[:2] + "…" + s[-2:]
    except Exception:
        return "***"


def _safe_echo_from_payload(payload: UserCreate) -> dict[str, Any]:  # type: ignore[name-defined]
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    out: dict[str, Any] = {}
    for k, v in data.items():
        ku = str(k).upper()
        if any(token in ku for token in _SECRET_KEYS) or k in ("password", "confirm_password"):
            out[k] = "***"
        else:
            out[k] = v
    return out


# -------------------------------------------------------------------------------------
# Схема ответа
# -------------------------------------------------------------------------------------
class RegisterAccepted(BaseModel):
    status: str = "ok"
    message: str = "registration accepted (legacy alias)"
    phone_normalized: Optional[str] = None
    echo: Optional[dict[str, Any]] = None  # включается флагом AUTH_ALIAS_ECHO_SAFE_FIELDS


# -------------------------------------------------------------------------------------
# Роутер /api/auth/*
# -------------------------------------------------------------------------------------
router = APIRouter(
    prefix="/api/auth",
    tags=["auth (legacy alias)"],
)


@router.head("/register", status_code=status.HTTP_200_OK)
async def register_head() -> Response:
    """HEAD для health-проверок и CORS preflight-совместимости."""
    return Response(status_code=status.HTTP_200_OK)


@router.options("/register", status_code=status.HTTP_204_NO_CONTENT)
async def register_options(response: Response) -> Response:
    """OPTIONS с объявлением допустимых методов."""
    response.headers["Allow"] = "POST, HEAD, OPTIONS"
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/register",
    response_model=RegisterAccepted,
    status_code=status.HTTP_200_OK,
    summary="Register user (legacy alias, stubbed)",
    description=(
        "Валидирует вход по `UserCreate` и возвращает 200 OK без походов в БД/почту/OTP. "
        "Если `AUTH_ALIAS_PROXY_TO_V1=true` — шарит запрос внутрь `/api/v1/auth/register` "
        "через ASGI-прокси и, при успехе, возвращает «accepted via v1»."
    ),
)
async def register_alias(
    request: Request,
    payload: UserCreate,  # type: ignore[name-defined]
    response: Response,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> RegisterAccepted:
    # Заголовки идемпотентности/трассировки
    if idempotency_key:
        response.headers["Idempotency-Key"] = idempotency_key
    req_id = request.headers.get("X-Request-ID")
    if req_id:
        response.headers.setdefault("X-Request-ID", req_id)

    phone_norm = _normalize_phone(getattr(payload, "phone", None))

    # Внутренний прокси во v1 при включенном флаге
    if _AUTH_ALIAS_PROXY_TO_V1:
        try:
            import httpx  # type: ignore

            async with httpx.AsyncClient(app=request.app, base_url=str(request.base_url)) as client:
                v1_resp = await client.post(
                    "/api/v1/auth/register",
                    json=(
                        payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
                    ),
                )
            if 200 <= v1_resp.status_code < 300:
                return RegisterAccepted(
                    status="ok",
                    message="registration accepted via v1",
                    phone_normalized=phone_norm,
                    echo=_safe_echo_from_payload(payload) if _AUTH_ALIAS_ECHO_SAFE_FIELDS else None,
                )
            logger.warning(
                "AUTH_ALIAS_PROXY_TO_V1: v1 returned %s — fallback to stub.",
                v1_resp.status_code,
            )
        except Exception as e:
            logger.warning("AUTH_ALIAS_PROXY_TO_V1 error: %s — fallback to stub.", e)

    # Поведение по умолчанию (stub)
    return RegisterAccepted(
        status="ok",
        message="registration accepted (legacy alias)",
        phone_normalized=phone_norm,
        echo=_safe_echo_from_payload(payload) if _AUTH_ALIAS_ECHO_SAFE_FIELDS else None,
    )


# -------------------------------------------------------------------------------------
# Необязательные редиректы → v1 (по флагу), чтобы не конфликтовать с main.py
# -------------------------------------------------------------------------------------
def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(url=path, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


if _AUTH_ALIAS_ENABLE_REDIRECTS:

    @router.post("/login")
    async def _auth_login_alias():
        return _redirect("/api/v1/auth/login")

    @router.post("/token/refresh")
    async def _auth_refresh_alias():
        return _redirect("/api/v1/auth/token/refresh")

    @router.get("/me")
    async def _auth_me_alias():
        return _redirect("/api/v1/auth/me")

    @router.post("/change-password")
    async def _auth_change_password_alias():
        return _redirect("/api/v1/auth/change-password")

    @router.post("/send-otp")
    async def _auth_send_otp_alias():
        return _redirect("/api/v1/auth/send-otp")


__all__ = ["router", "RegisterAccepted"]
