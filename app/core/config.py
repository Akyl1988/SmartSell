# ============================================
# app/core/config.py  — глобальные настройки SmartSell
# ============================================
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import sys
import time

# -------------------- ДОБАВЛЕНО --------------------
from datetime import datetime
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, AnyHttpUrl, EmailStr, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Константы JSON:API (Kaspi Shop Orders)
JSONAPI_MIME: str = "application/vnd.api+json"
KASPI_JSONAPI_AUTH_HEADER: str = "X-Auth-Token"

# Таймзона по требованиям Kaspi (все даты в часовом поясе Алматы)
KZ_TZ = ZoneInfo("Asia/Almaty")
# ---------------------------------------------------

_LOGGING_CONFIGURED = False


# ================================
# ВСПОМОГАТЕЛЬНЫЕ ХЕЛПЕРЫ
# ================================
def _under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def _mask_secret(val: str | None) -> str | None:
    if val is None:
        return None
    try:
        s = str(val)
    except Exception:
        return "***"
    if not s:
        return s
    if len(s) <= 6:
        return "***"
    return s[:3] + "***" + s[-3:]


def _project_root() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent.parent


def db_url_fingerprint(url: str) -> str:
    """
    Считаем fingerprint полного DSN (включая пароль) и возвращаем первые 12 символов sha256.
    Используем только для логов/ассертов без утечки пароля.
    """
    try:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    except Exception:
        return ""


def db_connection_fingerprint(url: str, include_password: bool = True) -> str:
    """
    Безопасный fingerprint соединения. Берёт user|host|port|db|password (если include_password)
    и возвращает первые 12 символов sha256.
    Если URL не парсится — пустая строка.
    """
    try:
        parsed = urlparse(url)
        user = parsed.username or ""
        host = parsed.hostname or ""
        port = str(parsed.port or "")
        db = (parsed.path or "").lstrip("/")
        pw = parsed.password or ""
        parts = [user, host, port, db]
        if include_password:
            parts.append(pw)
        data = "|".join(parts)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return ""


def _is_local_env(env_value: str, debug: bool) -> bool:
    env_norm = (env_value or "").lower()
    return debug or env_norm in {"local", "development", "dev"}


def _mask_db_fp(url: str) -> str:
    return db_connection_fingerprint(url, include_password=False)


def _inject_password_if_missing(url: str) -> str:
    """Inject password for local/dev/pytest Postgres URLs when missing.

    Priority: DB_PASSWORD -> PGPASSWORD -> password from DATABASE_URL/DB_URL.
    No-ops for non-Postgres URLs, URLs with password, or non-local/non-pytest envs.
    """

    try:
        if not url:
            return url

        env = os.environ
        env_name = env.get("ENVIRONMENT", "")
        debug_flag = env.get("DEBUG", "0").lower() in {"1", "true", "yes", "on"}
        if not (_is_local_env(env_name, debug_flag) or _under_pytest()):
            return url

        parsed = urlparse(url)
        if not parsed.scheme.startswith("postgres"):
            return url
        if parsed.password or not parsed.username:
            return url

        password = env.get("DB_PASSWORD") or env.get("PGPASSWORD")
        if not password:
            base_env_url = env.get("DATABASE_URL") or env.get("DB_URL")
            try:
                if base_env_url and base_env_url != url:
                    base_parsed = urlparse(base_env_url)
                    if base_parsed.password and (base_parsed.scheme or "").startswith("postgres"):
                        password = base_parsed.password
            except Exception:
                password = None

        if not password:
            return url

        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{quote(parsed.username)}:{quote(password)}@{host}{port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    except Exception:
        return url


def _to_asyncpg_url(url: str) -> str:
    try:
        if url.startswith("postgresql+asyncpg://"):
            return url
        if url.startswith("postgresql+psycopg2://"):
            return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql+psycopg://"):
            return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
    except Exception:
        return url
    return url


def resolve_database_url(settings: Settings | None = None) -> tuple[str, str, str]:
    """
    Strict DB URL resolution with runtime/pytest separation.

    Test mode (PYTEST_CURRENT_TEST or TESTING=true or ENVIRONMENT in {test, testing}):
      - Prefer TEST_ASYNC_DATABASE_URL, then TEST_DATABASE_URL, then assemble from TEST_DB_* parts.

    Runtime (otherwise):
      - Use only DATABASE_URL (or DB_URL), or assemble from DB_* parts. Ignore all TEST_* variables.

    Returns (resolved_url, source_token, fingerprint_8)
    """

    env = os.environ
    s = settings or Settings()

    env_environment = (env.get("ENVIRONMENT", s.ENVIRONMENT or "") or "").lower()
    explicit_testing_flag = bool(
        (env.get("TESTING", "").lower() in ("1", "true", "yes", "on")) or (env_environment in {"test", "testing"})
    )
    testing_flag = bool(explicit_testing_flag or _under_pytest())

    def _assemble_from_parts(prefix: str) -> tuple[str | None, str]:
        try:
            user = env.get(f"{prefix}_DB_USER") or env.get("POSTGRES_USER")
            password = env.get(f"{prefix}_DB_PASSWORD") or env.get("POSTGRES_PASSWORD")
            host = env.get(f"{prefix}_DB_HOST") or "127.0.0.1"
            port = env.get(f"{prefix}_DB_PORT") or "5432"
            dbname = (
                env.get(f"{prefix}_DB_NAME")
                or env.get("POSTGRES_DB")
                or ("smartsell_test" if prefix == "TEST" else "smartsell")
            )
            if not user or not password:
                return None, ""
            return (
                f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{dbname}",
                ("TEST_DB_*" if prefix == "TEST" else "DB_*"),
            )
        except Exception:
            return None, ""

    resolved_url: str | None = None
    source: str = "DEFAULT"

    if testing_flag:
        # Prefer explicit async test URL first, then sync test URL, then parts
        test_async = env.get("TEST_ASYNC_DATABASE_URL") or s.TEST_ASYNC_DATABASE_URL
        test_sync = (
            env.get("TEST_DATABASE_URL") or s.TEST_DATABASE_URL or env.get("DATABASE_TEST_URL") or s.DATABASE_TEST_URL
        )
        if test_async and test_async.strip():
            resolved_url = test_async.strip()
            source = "TEST_ASYNC_DATABASE_URL"
        elif test_sync and test_sync.strip():
            resolved_url = test_sync.strip()
            source = "TEST_DATABASE_URL"
        else:
            parts_url, parts_src = _assemble_from_parts("TEST")
            if parts_url:
                resolved_url = parts_url
                source = parts_src
    else:
        # Runtime path: ignore any TEST_*; use DATABASE_URL or DB_URL or parts
        base_url = env.get("DATABASE_URL") or env.get("DB_URL") or s.DATABASE_URL
        if base_url and base_url.strip():
            resolved_url = base_url.strip()
            # Prefer explicit env token for source
            if env.get("DATABASE_URL") and env.get("DATABASE_URL").strip() == resolved_url:
                source = "DATABASE_URL"
            elif env.get("DB_URL") and env.get("DB_URL").strip() == resolved_url:
                source = "DB_URL"
            else:
                source = "DATABASE_URL"
        else:
            parts_url, parts_src = _assemble_from_parts("DB")
            if parts_url:
                resolved_url = parts_url
                source = parts_src

    if not resolved_url:
        if explicit_testing_flag:
            resolved_url = _default_test_db_url()
            source = "DEFAULT"
        elif _is_local_env(s.ENVIRONMENT, s.DEBUG):
            resolved_url = _default_test_db_url()
            source = "DEFAULT"
        else:
            raise ValueError("DATABASE_URL is required in non-local environments")

    # Local/dev/pytest: if URL lacks password but PGPASSWORD is set, inject it
    resolved_url = _inject_password_if_missing(resolved_url)

    fingerprint = db_url_fingerprint(resolved_url)
    return resolved_url, source, fingerprint


def resolve_async_database_url(settings: Settings) -> tuple[str, str, str]:
    """
    Resolve async DB URL with driver normalization and strict test/runtime separation.
    Returns (url, source_with_context, fingerprint_no_pw)
    """
    env = os.environ

    env_environment = (env.get("ENVIRONMENT", settings.ENVIRONMENT or "") or "").lower()
    testing_flag = bool(
        settings.TESTING
        or _under_pytest()
        or (env.get("TESTING", "").lower() in ("1", "true", "yes", "on"))
        or (env_environment in {"test", "testing"})
    )

    if testing_flag:
        # Prefer test async URL, then test sync URL, then fall back to base resolver (which may build from parts)
        test_async = env.get("TEST_ASYNC_DATABASE_URL") or settings.TEST_ASYNC_DATABASE_URL
        test_sync = (
            env.get("TEST_DATABASE_URL")
            or settings.TEST_DATABASE_URL
            or env.get("DATABASE_TEST_URL")
            or settings.DATABASE_TEST_URL
        )
        if test_async and test_async.strip():
            base = test_async.strip()
            src = "TEST_ASYNC_DATABASE_URL"
        elif test_sync and test_sync.strip():
            base = test_sync.strip()
            src = "TEST_DATABASE_URL"
        else:
            base, src, _ = resolve_database_url(settings)
    else:
        base, src, _ = resolve_database_url(settings)

    url = _to_asyncpg_url(base or "")
    url = _inject_password_if_missing(url)
    fp = _mask_db_fp(url)

    # Provide context in source while keeping prefix compatible with existing tests
    context = "test" if testing_flag else "runtime"
    source_with_ctx = f"{src} ({context})->async"
    return url, source_with_ctx, fp


def _parse_list_like(v: Any) -> Any:
    """
    Принимает строку "a,b,c" или JSON-массив и возвращает list[str].
    Иначе — возвращает исходное значение.
    """
    if isinstance(v, str):
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(i).strip() for i in parsed if str(i).strip()]
            except Exception:
                pass
        return [i.strip() for i in v.split(",") if i.strip()]
    return v


def _is_secret_key_name(key: str) -> bool:
    lk = key.lower()
    if any(s in lk for s in ("secret", "password", "token", "dsn", "api_key", "api-secret")):
        return True
    if "key" in lk and "public" not in lk:
        return True
    return False


def _mask_nested(obj: Any, key_hint: str | None = None) -> Any:
    """
    Рекурсивная маскировка секретов в dict/list/tuple.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _is_secret_key_name(k):
                if isinstance(v, dict | list | tuple):
                    out[k] = _mask_nested(v, key_hint=k)
                else:
                    out[k] = _mask_secret(v)
            else:
                out[k] = _mask_nested(v, key_hint=None)
        return out
    if isinstance(obj, list):
        return [_mask_nested(v, key_hint=key_hint) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_mask_nested(v, key_hint=key_hint) for v in obj)
    if key_hint and _is_secret_key_name(key_hint):
        return _mask_secret(obj)
    return obj


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        test = p / ".writetest"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _default_test_db_url() -> str:
    """Local-only safe fallback for development (no secrets, file-based)."""
    return "sqlite+aiosqlite:///./.smartsell_test.sqlite3"


def should_disable_startup_hooks() -> bool:
    """Return True when startup hooks should be skipped (tests/CI)."""
    if os.getenv("DISABLE_APP_STARTUP_HOOKS") == "1":
        return True
    try:
        return bool(getattr(settings, "TESTING", False) or _under_pytest())
    except Exception:
        return _under_pytest()


# ================================
# НАСТРОЙКИ ПРИЛОЖЕНИЯ (Pydantic v2)
# ================================
class Settings(BaseSettings):
    """
    Конфиг-прослойка проекта SmartSell
    - В продакшене и тестах разрешён ТОЛЬКО PostgreSQL.
    - В девелопменте fallback на SQLite для удобства.
    - Глубокая маскировка секретов в дампах.
    - Строгое логирование, JSON-формат по умолчанию.
    - Флаг DISABLE_APP_STARTUP_HOOKS для CI/CD.
    """

    model_config = SettingsConfigDict(
        env_file=(".env.test", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        env_ignore_empty=True,
    )

    # ---- базовые
    APP_NAME: str = Field(default="SmartSell", description="Application name")
    PROJECT_NAME: str = Field(default="SmartSell", description="Project name")
    VERSION: str = Field(default="0.1.0", description="Application version")
    DEBUG: bool = Field(default=False, description="Debug mode", validation_alias="DEBUG")
    ENVIRONMENT: str = Field(default="development", description="Environment", validation_alias="ENVIRONMENT")
    TESTING: bool = Field(default=False, description="Testing mode", validation_alias="TESTING")
    DEBUG_OTP_LOGGING: bool = Field(
        default=False, description="Allow masked OTP debug logging in development", validation_alias="DEBUG_OTP_LOGGING"
    )
    DEBUG_CONFIG_DUMP: bool = Field(
        default=False, description="Allow masked config dumps", validation_alias="DEBUG_CONFIG_DUMP"
    )
    API_V1_STR: str = Field(default="/api/v1", description="API v1 prefix")
    HOST: str = Field(default="127.0.0.1", description="Server host", validation_alias="HOST")
    PORT: int = Field(default=8000, description="Server port", validation_alias="PORT")
    SCHEME: str = Field(default="http", description="Public scheme", validation_alias="SCHEME")
    PUBLIC_URL: AnyHttpUrl | None = Field(default=None, description="Public API URL", validation_alias="PUBLIC_URL")

    # ---- security/JWT
    SECRET_KEY: str = Field(default="changeme", description="JWT secret key", validation_alias="SECRET_KEY")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30,
        description="Access token expiry",
        validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=7, description="Refresh token expiry", validation_alias="REFRESH_TOKEN_EXPIRE_DAYS"
    )
    ALGORITHM: str = Field(default="HS256", description="JWT algorithm", validation_alias="ALGORITHM")
    MAX_LOGIN_ATTEMPTS: int = Field(default=5, description="Max login attempts", validation_alias="MAX_LOGIN_ATTEMPTS")
    PASSWORD_MIN_LENGTH: int = Field(
        default=8, description="Password min length", validation_alias="PASSWORD_MIN_LENGTH"
    )

    # ---- БД
    DATABASE_URL: str | None = Field(
        default=None,
        description="Database URL",
        validation_alias=AliasChoices("DATABASE_URL", "DB_URL"),
    )
    DATABASE_TEST_URL: str | None = Field(
        default=None, description="Test database URL (legacy)", validation_alias="DATABASE_TEST_URL"
    )
    TEST_DATABASE_URL: str | None = Field(
        default=None, description="Test database URL", validation_alias="TEST_DATABASE_URL"
    )
    TEST_ASYNC_DATABASE_URL: str | None = Field(
        default=None, description="Test async database URL", validation_alias="TEST_ASYNC_DATABASE_URL"
    )
    SQLALCHEMY_POOL_SIZE: int = Field(default=10, description="Pool size", validation_alias="SQLALCHEMY_POOL_SIZE")
    SQLALCHEMY_MAX_OVERFLOW: int = Field(
        default=20, description="Max overflow", validation_alias="SQLALCHEMY_MAX_OVERFLOW"
    )
    SQLALCHEMY_POOL_TIMEOUT: int = Field(
        default=30, description="Pool timeout (s)", validation_alias="SQLALCHEMY_POOL_TIMEOUT"
    )
    SQLALCHEMY_POOL_RECYCLE: int = Field(
        default=1800, description="Pool recycle (s)", validation_alias="SQLALCHEMY_POOL_RECYCLE"
    )

    # ---- Redis/Celery
    REDIS_URL: str = Field(default="redis://localhost:6379", description="Redis URL", validation_alias="REDIS_URL")
    REDIS_PASSWORD: str | None = Field(default=None, description="Redis password", validation_alias="REDIS_PASSWORD")
    REDIS_DB: int = Field(default=0, description="Redis db index", validation_alias="REDIS_DB")
    REDIS_CLIENT_STRICT: bool = Field(
        default=False,
        description="Fail fast if Redis is unavailable (production safeguard)",
        validation_alias="REDIS_CLIENT_STRICT",
    )
    REDIS_SOCKET_TIMEOUT: float = Field(
        default=1.0,
        description="Redis socket timeout seconds",
        validation_alias="REDIS_SOCKET_TIMEOUT",
    )

    # ---- system integrations / provider registry
    INTEGRATIONS_MASTER_KEY: str | None = Field(
        default=None,
        description="Base64-encoded master key for encrypting provider configs",
        validation_alias="INTEGRATIONS_MASTER_KEY",
    )
    SYSTEM_INTEGRATIONS_CACHE_TTL: int = Field(
        default=30,
        description="TTL (seconds) for provider registry cache",
        validation_alias="SYSTEM_INTEGRATIONS_CACHE_TTL",
    )
    SYSTEM_CONFIG_CHANNEL: str = Field(
        default="smartsell.config_changed",
        description="Redis pub/sub channel for system integration changes",
        validation_alias="SYSTEM_CONFIG_CHANNEL",
    )

    CELERY_BROKER_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Celery broker URL",
        validation_alias="CELERY_BROKER_URL",
    )
    CELERY_RESULT_BACKEND: str = Field(
        default="redis://localhost:6379/0",
        description="Celery result backend",
        validation_alias="CELERY_RESULT_BACKEND",
    )
    SCHEDULER_TIMEZONE: str = Field(
        default="UTC", description="Scheduler timezone", validation_alias="SCHEDULER_TIMEZONE"
    )
    EAGER_SIDE_EFFECTS: bool = Field(default=True, validation_alias="EAGER_SIDE_EFFECTS")

    # Kaspi Auto-Sync Settings
    KASPI_AUTOSYNC_ENABLED: bool = Field(
        default=False,
        description="Enable automatic Kaspi orders sync",
        validation_alias="KASPI_AUTOSYNC_ENABLED",
    )
    KASPI_AUTOSYNC_INTERVAL_MINUTES: int = Field(
        default=15,
        description="Kaspi auto-sync interval in minutes",
        validation_alias="KASPI_AUTOSYNC_INTERVAL_MINUTES",
    )
    KASPI_AUTOSYNC_MAX_CONCURRENCY: int = Field(
        default=3,
        description="Maximum number of companies to sync concurrently",
        validation_alias="KASPI_AUTOSYNC_MAX_CONCURRENCY",
    )

    # ---- rate limits
    RATE_LIMIT_PER_MINUTE: int = Field(
        default=100, description="Rate limit per minute", validation_alias="RATE_LIMIT_PER_MINUTE"
    )
    RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        description="Rate limit window (seconds)",
        validation_alias="RATE_LIMIT_WINDOW_SECONDS",
    )
    RATE_LIMIT_BURST: int = Field(default=100, description="Rate limit burst", validation_alias="RATE_LIMIT_BURST")
    RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        description="Master switch for rate limiting",
        validation_alias="RATE_LIMIT_ENABLED",
    )
    AUTH_RATE_LIMIT_PER_MINUTE: int = Field(
        default=10,
        description="Auth endpoints rate limit per minute",
        validation_alias="AUTH_RATE_LIMIT_PER_MINUTE",
    )
    AUTH_RATE_WINDOW_SECONDS: int = Field(
        default=60,
        description="Auth endpoints rate limit window (seconds)",
        validation_alias="AUTH_RATE_WINDOW_SECONDS",
    )
    OTP_RATE_LIMIT_PER_MINUTE: int = Field(
        default=5,
        description="OTP/send-code rate limit per minute",
        validation_alias="OTP_RATE_LIMIT_PER_MINUTE",
    )
    OTP_RATE_WINDOW_SECONDS: int = Field(
        default=60,
        description="OTP/send-code rate limit window (seconds)",
        validation_alias="OTP_RATE_WINDOW_SECONDS",
    )
    IDEMPOTENCY_DEFAULT_TTL: int = Field(
        default=900,
        description="Default idempotency TTL seconds when header not provided",
        validation_alias="IDEMPOTENCY_DEFAULT_TTL",
    )
    IDEMPOTENCY_CACHE_PREFIX: str = Field(
        default="idemp",
        description="Redis key prefix for idempotency storage",
        validation_alias="IDEMPOTENCY_CACHE_PREFIX",
    )

    # ---- CORS/hosts
    ALLOWED_HOSTS: list[str] = Field(
        default_factory=lambda: ["*"], description="Allowed hosts", validation_alias="ALLOWED_HOSTS"
    )
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["*"], description="CORS origins", validation_alias="CORS_ORIGINS"
    )
    BACKEND_CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost", "http://localhost:3000"],
        description="Backend CORS origins",
        validation_alias="BACKEND_CORS_ORIGINS",
    )

    # ---- Файлы/логи
    STATIC_DIR: str = Field(default="static", description="Static directory", validation_alias="STATIC_DIR")
    MEDIA_DIR: str = Field(default="media", description="Media directory", validation_alias="MEDIA_DIR")
    UPLOAD_DIR: str = Field(default="uploads", description="Upload directory", validation_alias="UPLOAD_DIR")
    MAX_UPLOAD_SIZE: int = Field(
        default=10 * 1024 * 1024, description="Max upload size", validation_alias="MAX_UPLOAD_SIZE"
    )
    LOG_PATH: str = Field(default="logs/app.log", description="Log file path", validation_alias="LOG_PATH")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level", validation_alias="LOG_LEVEL")
    LOG_FORMAT: str = Field(default="json", description="Logging format (json|text)", validation_alias="LOG_FORMAT")

    # ---- Frontend
    FRONTEND_URL: AnyHttpUrl | str = Field(
        default="http://localhost:3000", description="Frontend URL", validation_alias="FRONTEND_URL"
    )

    # ---- Провайдеры
    MOBIZON_API_KEY: str | None = Field(default=None, description="Mobizon API key", validation_alias="MOBIZON_API_KEY")
    MOBIZON_API_URL: str = Field(default="https://api.mobizon.kz", description="Mobizon API URL")

    CLOUDINARY_CLOUD_NAME: str | None = Field(
        default=None, description="Cloudinary cloud name", validation_alias="CLOUDINARY_CLOUD_NAME"
    )
    CLOUDINARY_API_KEY: str | None = Field(
        default=None, description="Cloudinary API key", validation_alias="CLOUDINARY_API_KEY"
    )
    CLOUDINARY_API_SECRET: str | None = Field(
        default=None, description="Cloudinary API secret", validation_alias="CLOUDINARY_API_SECRET"
    )

    KASPI_MERCHANT_ID: str | None = Field(
        default=None, description="Kaspi merchant ID", validation_alias="KASPI_MERCHANT_ID"
    )
    KASPI_API_KEY: str | None = Field(default=None, description="Kaspi API key", validation_alias="KASPI_API_KEY")
    KASPI_API_URL: str = Field(default="https://api.kaspi.kz", description="Kaspi API URL")

    # -------------------- ДОБАВЛЕНО --------------------
    # Отдельный базовый URL для Shop Orders JSON:API (по документации Kaspi)
    KASPI_SHOP_API_URL: str = Field(default="https://kaspi.kz/shop/api", description="Kaspi Shop JSON:API base URL")
    # Таймзона приложения для конвертаций (по умолчанию Asia/Almaty)
    APP_TIMEZONE: str = Field(default="Asia/Almaty", description="App timezone for Kaspi filters")
    # Максимальный размер страницы по документации (до 100)
    KASPI_PAGE_SIZE_MAX: int = Field(default=100, description="Kaspi orders max page size")
    # Безопасный дефолт page[size] при наших запросах
    KASPI_DEFAULT_PAGE_SIZE: int = Field(default=50, description="Default page size")
    # Симметричный ключ для pgcrypto: pgp_sym_encrypt/decrypt (ОЧЕНЬ ВАЖНО!)
    PGCRYPTO_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PGCRYPTO_KEY", "KASPI_CRYPTO_KEY", "KASPI_TOKEN_KEY"),
        description="Symmetric key for pgcrypto (pgp_sym_encrypt/decrypt).",
    )

    # ---- Путь к скриптам/адаптерам Kaspi (для /api/v1/kaspi/*) ----
    # Основной исполняемый файл адаптера (Python-скрипт или .ps1/.bat)
    KASPI_SCRIPT_PATH: str | None = Field(
        default=None,
        description="Path to Kaspi adapter script (e.g., scripts/kaspi_adapter.py or tools/kaspi.ps1)",
        validation_alias="KASPI_SCRIPT_PATH",
    )
    # Явный Python-интерпретатор для запуска адаптера (если нужен)
    KASPI_PYTHON: str = Field(
        default=sys.executable, description="Python executable for adapter", validation_alias="KASPI_PYTHON"
    )
    # ДОБАВЛЕНО: явные пути к шеллам для .ps1 (Windows)
    KASPI_PWSH: str | None = Field(
        default=None,
        description="Path to PowerShell 7 (pwsh.exe), e.g., C:\\Program Files\\PowerShell\\7\\pwsh.exe",
        validation_alias="KASPI_PWSH",
    )
    KASPI_POWERSHELL: str | None = Field(
        default=None,
        description="Path to classic Windows PowerShell (powershell.exe)",
        validation_alias="KASPI_POWERSHELL",
    )
    # ДОБАВЛЕНО: режим выбора шелла для адаптера
    KASPI_SHELL: Literal["auto", "python", "pwsh", "powershell", "cmd"] = Field(
        default="auto",
        description="Adapter shell selector: auto|python|pwsh|powershell|cmd",
        validation_alias="KASPI_SHELL",
    )

    # Таймауты/ретраи для HTTP к Kaspi
    KASPI_HTTP_TIMEOUT_SEC: int = Field(default=60, description="HTTP timeout to Kaspi (seconds)")
    KASPI_HTTP_RETRIES: int = Field(default=3, description="HTTP retries to Kaspi")
    KASPI_HTTP_RETRY_BACKOFF_SEC: float = Field(default=1.5, description="HTTP retry backoff base")

    # Пути для интеграции с Bridge/outbox и для генерации фидов
    KASPI_BRIDGE_OUTBOX: str | None = Field(
        default=None,
        description="Bridge outbox path with marketplace data (e.g., D:\\LLM_HUB\\Bridge\\outbox)",
        validation_alias="KASPI_BRIDGE_OUTBOX",
    )
    KASPI_FEED_OUT_DIR: str = Field(
        default="var/kaspi/feeds",
        description="Directory for generated Kaspi feeds (XML/JSON)",
        validation_alias="KASPI_FEED_OUT_DIR",
    )
    KASPI_TMP_DIR: str = Field(
        default="var/kaspi/tmp", description="Temp dir for Kaspi operations", validation_alias="KASPI_TMP_DIR"
    )

    # Полезные флаги
    ENABLE_KASPI_ADAPTER: bool = Field(
        default=True, description="Enable internal Kaspi adapter endpoints (/api/v1/kaspi/*)"
    )
    KASPI_STORE_ALIAS_DEFAULT: str | None = Field(
        default=None, description="Optional default store alias", validation_alias="KASPI_STORE_ALIAS_DEFAULT"
    )
    # ---------------------------------------------------

    TIPTOP_PAY_PUBLIC_KEY: str | None = Field(
        default=None, description="TipTop Pay public key", validation_alias="TIPTOP_PAY_PUBLIC_KEY"
    )
    TIPTOP_PAY_SECRET_KEY: str | None = Field(
        default=None, description="TipTop Pay secret key", validation_alias="TIPTOP_PAY_SECRET_KEY"
    )
    TIPTOP_API_KEY: str | None = Field(default=None, description="TipTop API key", validation_alias="TIPTOP_API_KEY")
    TIPTOP_API_SECRET: str | None = Field(
        default=None, description="TipTop API secret", validation_alias="TIPTOP_API_SECRET"
    )
    TIPTOP_API_URL: str = Field(default="https://api.tippy.kz", description="TipTop API URL")

    # ---- SMTP
    SMTP_HOST: str = Field(default="smtp.gmail.com", description="SMTP host", validation_alias="SMTP_HOST")
    SMTP_PORT: int = Field(default=587, description="SMTP port", validation_alias="SMTP_PORT")
    SMTP_USER: str = Field(default="", description="SMTP user", validation_alias="SMTP_USER")
    SMTP_PASSWORD: str = Field(default="", description="SMTP password", validation_alias="SMTP_PASSWORD")
    SMTP_FROM_EMAIL: EmailStr | None = Field(
        default=None, description="Sender email", validation_alias="SMTP_FROM_EMAIL"
    )
    SMTP_TLS: bool = Field(default=True, description="Use STARTTLS", validation_alias="SMTP_TLS")
    SMTP_SSL: bool = Field(default=False, description="Use SSL", validation_alias="SMTP_SSL")

    # ---- OAuth
    GOOGLE_CLIENT_ID: str | None = Field(
        default="", description="Google client id", validation_alias="GOOGLE_CLIENT_ID"
    )
    GOOGLE_CLIENT_SECRET: str | None = Field(
        default="", description="Google client secret", validation_alias="GOOGLE_CLIENT_SECRET"
    )
    FACEBOOK_CLIENT_ID: str | None = Field(
        default="", description="Facebook client id", validation_alias="FACEBOOK_CLIENT_ID"
    )
    FACEBOOK_CLIENT_SECRET: str | None = Field(
        default="", description="Facebook client secret", validation_alias="FACEBOOK_CLIENT_SECRET"
    )

    # ---- Observability/Runtime
    SENTRY_DSN: str | None = Field(default=None, description="Sentry DSN", validation_alias="SENTRY_DSN")
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = Field(
        default=None, description="OTLP endpoint", validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    OTEL_SERVICE_NAME: str | None = Field(
        default=None, description="OTEL service name", validation_alias="OTEL_SERVICE_NAME"
    )
    UVICORN_WORKERS: int = Field(default=1, description="Uvicorn workers count", validation_alias="UVICORN_WORKERS")
    ROOT_PATH: str = Field(default="", description="ASGI root_path for reverse proxy", validation_alias="ROOT_PATH")
    PROCESS_ROLE: str = Field(
        default="web",
        description="Process role: web/scheduler/runner/worker/migrator",
        validation_alias="PROCESS_ROLE",
    )

    # ---- PostgreSQL доп-настройки
    POSTGRES_STATEMENT_TIMEOUT_MS: int | None = Field(default=None, validation_alias="POSTGRES_STATEMENT_TIMEOUT_MS")
    POSTGRES_SSLMODE: str | None = Field(default=None, validation_alias="POSTGRES_SSLMODE")
    POSTGRES_SET_TIMEOUT_DIRECT: bool = Field(default=False, validation_alias="POSTGRES_SET_TIMEOUT_DIRECT")

    # ---- Release metadata
    GIT_COMMIT_SHA: str | None = Field(
        default=None,
        description="Git commit SHA",
        validation_alias=AliasChoices("GIT_COMMIT", "GIT_COMMIT_SHA"),
    )
    GIT_BRANCH: str | None = Field(default=None, description="Git branch name", validation_alias="GIT_BRANCH")
    BUILD_TIMESTAMP: str | None = Field(default=None, description="Build timestamp", validation_alias="BUILD_TIMESTAMP")

    # --------- валидаторы ---------
    @field_validator("CORS_ORIGINS", mode="before")
    def _cors(cls, v: Any) -> Any:
        return _parse_list_like(v)

    @field_validator("ALLOWED_HOSTS", mode="before")
    def _hosts(cls, v: Any) -> Any:
        return _parse_list_like(v)

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    def _bcors(cls, v: Any) -> Any:
        return _parse_list_like(v)

    @field_validator("SMTP_FROM_EMAIL", mode="before")
    def empty_email_is_none(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("PUBLIC_URL", mode="before")
    def normalize_public_url(cls, v: Any) -> Any:
        if not v:
            return v
        if isinstance(v, str):
            v = v.strip().rstrip("/")
            # Приведём схему к нижнему регистру (если есть), без агрессивной модификации
            try:
                parsed = urlparse(v)
                if parsed.scheme:
                    v = urlunparse(
                        (
                            parsed.scheme.lower(),
                            parsed.netloc,
                            parsed.path,
                            parsed.params,
                            parsed.query,
                            parsed.fragment,
                        )
                    )
            except Exception:
                pass
        return v

    @field_validator("ALGORITHM")
    def check_alg(cls, v: str) -> str:
        allowed = {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"}
        if v not in allowed:
            raise ValueError(f"Unsupported JWT algorithm: {v}")
        return v

    # -------------------- ДОБАВЛЕНО --------------------
    @field_validator("KASPI_DEFAULT_PAGE_SIZE")
    def _validate_default_page_size(cls, v: int, info: ValidationInfo) -> int:
        data = info.data or {}
        try:
            max_v = int(data.get("KASPI_PAGE_SIZE_MAX", 100))
        except Exception:
            max_v = 100
        if v <= 0 or v > max_v:
            raise ValueError(f"KASPI_DEFAULT_PAGE_SIZE must be 1..{max_v}")
        return v

    @field_validator("APP_TIMEZONE")
    def _validate_app_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
            return v
        except Exception:
            # Фоллбэк на Asia/Almaty (Kaspi)
            return "Asia/Almaty"

    @field_validator("KASPI_SCRIPT_PATH", mode="before")
    def _normalize_script_path(cls, v: Any) -> Any:
        if not v:
            return v
        if isinstance(v, str):
            v = v.strip().strip('"').strip("'")
        return v

    @field_validator("KASPI_SHELL", mode="before")
    def _validate_shell_choice(cls, v: Any) -> Any:
        if not v:
            return "auto"
        s = str(v).strip().lower()
        if s not in {"auto", "python", "pwsh", "powershell", "cmd"}:
            raise ValueError("KASPI_SHELL must be one of: auto|python|pwsh|powershell|cmd")
        return s

    # ---------------------------------------------------

    # --------- удобные свойства ---------
    @property
    def base_dir(self) -> Path:
        return _project_root()

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT.lower() in {"dev", "development"}

    @property
    def is_testing(self) -> bool:
        return bool(self.TESTING or _under_pytest())

    @property
    def public_url(self) -> str:
        if self.PUBLIC_URL:
            return str(self.PUBLIC_URL)
        host = self.HOST.strip()
        port = int(self.PORT)
        scheme = (self.SCHEME or "http").strip().lower()
        default_port = 443 if scheme == "https" else 80
        if port == default_port:
            return f"{scheme}://{host}"
        return f"{scheme}://{host}:{port}"

    @property
    def build_info(self) -> dict:
        return {
            "project": self.PROJECT_NAME,
            "version": self.VERSION,
            "environment": self.ENVIRONMENT,
            "commit": self.GIT_COMMIT_SHA or "",
            "branch": self.GIT_BRANCH or "",
            "build_time": self.BUILD_TIMESTAMP or "",
        }

    # --------- проверки и инициализация ---------
    def ensure_dirs(self) -> None:
        dirs = {
            self.UPLOAD_DIR,
            self.STATIC_DIR,
            self.MEDIA_DIR,
            os.path.dirname(self.LOG_PATH) if self.LOG_PATH else "",
            self.KASPI_FEED_OUT_DIR,
            self.KASPI_TMP_DIR,
        }
        for d in filter(None, dirs):
            p = self.resolve_path(d)
            if not _writable(p):
                logging.getLogger(__name__).warning(f"Directory not writable: {p}")

    def check_secret_key(self) -> None:
        localish = _is_local_env(self.ENVIRONMENT, bool(self.DEBUG))
        if not localish:
            if not self.SECRET_KEY or self.SECRET_KEY.strip().lower() in {
                "changeme",
                "secret",
                "password",
            }:
                raise ValueError("Set a secure SECRET_KEY in non-local environments!")

    def _is_postgres_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            scheme = (parsed.scheme or "").lower()
            return scheme in {"postgres", "postgresql"} or scheme.startswith("postgresql+")
        except Exception:
            return False

    def check_database_url(self) -> None:
        if self.is_production:
            if not self.DATABASE_URL:
                raise ValueError("DATABASE_URL must be set in production!")
            if not self._is_postgres_url(self.DATABASE_URL):
                raise ValueError("In production, only PostgreSQL is allowed for DATABASE_URL!")

    def check_allowed_hosts(self) -> None:
        if self.is_production and self.ALLOWED_HOSTS == ["*"]:
            logging.getLogger(__name__).warning("ALLOWED_HOSTS='*' в production — небезопасно. Задайте список доменов.")

    def check_cors_frontend(self) -> None:
        try:
            if self.is_production and self.FRONTEND_URL:
                origin = str(self.FRONTEND_URL).rstrip("/")
                if self.CORS_ORIGINS != ["*"] and origin not in self.CORS_ORIGINS:
                    logging.getLogger(__name__).warning("FRONTEND_URL not present in CORS_ORIGINS: %s", origin)
        except Exception:
            pass

    def check_smtp(self) -> None:
        if (self.SMTP_HOST and (self.SMTP_USER or self.SMTP_PASSWORD)) and not self.SMTP_FROM_EMAIL:
            logging.getLogger(__name__).warning("SMTP_FROM_EMAIL is empty while SMTP credentials are set.")
        if self.SMTP_TLS and self.SMTP_SSL:
            logging.getLogger(__name__).warning(
                "SMTP_TLS and SMTP_SSL are both True; prefer SMTP_SSL (465) or TLS (587), not both."
            )

    def configure_logging(self) -> None:
        if os.getenv("DISABLE_APP_LOGGING") == "1":
            return
        global _LOGGING_CONFIGURED
        if _LOGGING_CONFIGURED:
            return
        logger = logging.getLogger()
        for h in list(logger.handlers):
            logger.removeHandler(h)
        level = getattr(logging, (self.LOG_LEVEL or "INFO").upper(), logging.INFO)
        logger.setLevel(level)

        if (self.LOG_FORMAT or "").lower() == "json":

            class JsonFormatter(logging.Formatter):
                # Гарантируем UTC (Z) в timestamp
                converter = time.gmtime

                def format(self, record: logging.LogRecord) -> str:
                    payload = {
                        "level": record.levelname,
                        "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
                        "message": record.getMessage(),
                        "name": record.name,
                        "process": record.process,
                        "pid": os.getpid(),
                        "module": record.module,
                    }
                    if record.exc_info:
                        payload["exc_info"] = self.formatException(record.exc_info)
                    return json.dumps(payload, ensure_ascii=False)

            formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                "%Y-%m-%d %H:%M:%S",
            )
            # Для текстового формата тоже принудим UTC
            try:
                formatter.converter = time.gmtime  # type: ignore[attr-defined]
            except Exception:
                pass

        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(level)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

        if self.LOG_PATH:
            log_file = self.resolve_path(self.LOG_PATH)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(str(log_file), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
            fh.setLevel(level)
            fh.setFormatter(formatter)
            logger.addHandler(fh)

        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uv_logger = logging.getLogger(name)
            uv_logger.handlers.clear()
            uv_logger.propagate = True
            uv_logger.setLevel(level)

        try:
            logger.info("build_info=%s", json.dumps(self.build_info, ensure_ascii=False))
        except Exception:
            pass

        _LOGGING_CONFIGURED = True

    # -------------------- ДОБАВЛЕНО --------------------
    @property
    def db_url_safe(self) -> str:
        """Маскированный DSN без логинов/паролей, для логов/метрик."""
        db_url = self.DATABASE_URL or ""
        try:
            parsed = urlparse(db_url)
            if not parsed.scheme:
                return ""
            safe_netloc = parsed.hostname or ""
            if parsed.port:
                safe_netloc += f":{parsed.port}"
            return urlunparse((parsed.scheme.split("+")[0], safe_netloc, parsed.path, "", "", ""))
        except Exception:
            return ""

    def db_url_source(self) -> str:
        """
        Пытаемся определить, откуда пришёл текущий DATABASE_URL.
        Приоритет: переменные окружения (DATABASE_URL/DB_URL/TEST_DATABASE_URL/DATABASE_TEST_URL),
        затем .env.test, затем .env, затем дефолт.
        Это эвристика для диагностики; на поведение не влияет.
        """

        def _match_env(name: str, value: str | None) -> str | None:
            env_val = os.getenv(name)
            if env_val and value and env_val.strip() == value.strip():
                return f"env:{name}"
            return None

        current = self.DATABASE_URL or ""

        for key in ("DATABASE_URL", "DB_URL", "TEST_DATABASE_URL", "DATABASE_TEST_URL"):
            src = _match_env(key, current)
            if src:
                return src

        # Если нет прямого совпадения в окружении — попробуем найти в .env.test и .env
        for env_file, tag in ((".env.test", "file:.env.test"), (".env", "file:.env")):
            try:
                path = _project_root() / env_file
                if not path.exists():
                    continue
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("DATABASE_URL"):
                        _, _, val = line.partition("=")
                        if val and current.strip() == val.strip():
                            return tag
            except Exception:
                continue

        return "default"

    def _log_config_summary(self) -> None:
        """
        Безопасная сводка конфигурации в лог (без секретов).
        """
        try:
            drv = self.sqlalchemy_urls["driver"] or "unknown"
            safe = self.db_url_safe
            parsed = urlparse(self.DATABASE_URL or "")
            fp_no_pw = db_connection_fingerprint(self.DATABASE_URL or "", include_password=False)
            logging.getLogger(__name__).info(
                "config_summary=%s",
                json.dumps(
                    {
                        "env": self.ENVIRONMENT,
                        "log_level": (self.LOG_LEVEL or "INFO").upper(),
                        "log_format": (self.LOG_FORMAT or "json").lower(),
                        "db_driver": drv,
                        "db_host": parsed.hostname or "",
                        "db_port": parsed.port or "",
                        "db_name": (parsed.path or "").lstrip("/"),
                        "db_url": safe,
                        "db_fp_no_pw": fp_no_pw,
                        "public_url": self.public_url,
                        "uvicorn_workers": int(self.UVICORN_WORKERS),
                        "kaspi_encryption_enabled": self.kaspi_encryption_enabled,
                        "kaspi_script": str(self.kaspi_script_path()) if self.kaspi_script_path() else "",
                        "kaspi_shell_mode": self.kaspi_shell_mode(),
                        "exec_preview": " ".join(self.kaspi_adapter_exec_preview(["health"]))
                        if self.kaspi_script_path()
                        else "",
                        "bridge_outbox": str(self.bridge_outbox_dir()) if self.bridge_outbox_dir() else "",
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception:
            pass

    # ---------------------------------------------------

    # --------- групповые представления настроек ---------
    @property
    def redis_settings(self) -> dict:
        return {
            "url": self.REDIS_URL,
            "password": self.REDIS_PASSWORD,
            "db": self.REDIS_DB,
            "strict": bool(self.REDIS_CLIENT_STRICT),
            "socket_timeout": float(self.REDIS_SOCKET_TIMEOUT),
        }

    @property
    def rate_limit_settings(self) -> dict:
        return {
            "enabled": bool(self.RATE_LIMIT_ENABLED),
            "api_per_minute": int(self.RATE_LIMIT_PER_MINUTE),
            "api_window_seconds": int(self.RATE_LIMIT_WINDOW_SECONDS),
            "api_burst": int(self.RATE_LIMIT_BURST),
            "auth_per_minute": int(self.AUTH_RATE_LIMIT_PER_MINUTE),
            "auth_window_seconds": int(self.AUTH_RATE_WINDOW_SECONDS),
            "otp_per_minute": int(self.OTP_RATE_LIMIT_PER_MINUTE),
            "otp_window_seconds": int(self.OTP_RATE_WINDOW_SECONDS),
        }

    @property
    def idempotency_settings(self) -> dict:
        return {
            "default_ttl": int(self.IDEMPOTENCY_DEFAULT_TTL),
            "prefix": self.IDEMPOTENCY_CACHE_PREFIX,
        }

    @property
    def smtp_settings(self) -> dict:
        return {
            "host": self.SMTP_HOST,
            "port": self.SMTP_PORT,
            "user": self.SMTP_USER,
            "password": self.SMTP_PASSWORD,
            "from_email": str(self.SMTP_FROM_EMAIL or ""),
            "tls": bool(self.SMTP_TLS),
            "ssl": bool(self.SMTP_SSL),
        }

    def normalized_smtp(self) -> dict:
        if self.SMTP_TLS and self.SMTP_SSL:
            logging.getLogger(__name__).warning("Both SMTP_TLS and SMTP_SSL are True; forcing SSL semantics.")
        use_ssl = bool(self.SMTP_SSL)
        port = self.SMTP_PORT
        if use_ssl and port == 587:
            port = 465
        if not use_ssl and bool(self.SMTP_TLS) and port == 465:
            port = 587
        return {
            "host": self.SMTP_HOST,
            "port": port,
            "user": self.SMTP_USER,
            "password": self.SMTP_PASSWORD,
            "from_email": str(self.SMTP_FROM_EMAIL or ""),
            "tls": bool(self.SMTP_TLS) and not use_ssl,
            "ssl": use_ssl,
        }

    @property
    def db_settings(self) -> dict:
        url = self.DATABASE_URL or self.sqlalchemy_urls["sync"]
        return {"url": url, "testing": self.TESTING, "echo": bool(self.DEBUG)}

    @property
    def celery_settings(self) -> dict:
        return {
            "broker_url": self.CELERY_BROKER_URL,
            "result_backend": self.CELERY_RESULT_BACKEND,
            "timezone": self.SCHEDULER_TIMEZONE,
        }

    @property
    def cloudinary_settings(self) -> dict:
        return {
            "cloud_name": self.CLOUDINARY_CLOUD_NAME,
            "api_key": self.CLOUDINARY_API_KEY,
            "api_secret": self.CLOUDINARY_API_SECRET,
        }

    @property
    def kaspi_settings(self) -> dict:
        return {
            "merchant_id": self.KASPI_MERCHANT_ID,
            "api_key": self.KASPI_API_KEY,
            "api_url": self.KASPI_API_URL,
        }

    # -------------------- ДОБАВЛЕНО --------------------
    @property
    def kaspi_shop_settings(self) -> dict:
        """
        Настройки для Kaspi Shop JSON:API (заказы):
        - базовый URL /shop/api
        - MIME: application/vnd.api+json
        - таймзона/пагинация
        """
        return {
            "base_url": self.KASPI_SHOP_API_URL.rstrip("/"),
            "jsonapi_mime": JSONAPI_MIME,
            "auth_header": KASPI_JSONAPI_AUTH_HEADER,
            "timezone": self.APP_TIMEZONE or "Asia/Almaty",
            "default_page_size": int(self.KASPI_DEFAULT_PAGE_SIZE),
            "page_size_max": int(self.KASPI_PAGE_SIZE_MAX),
        }

    @property
    def kaspi_adapter_settings(self) -> dict:
        """
        Сводные настройки для внутреннего адаптера Kaspi.
        """
        return {
            "enabled": bool(self.ENABLE_KASPI_ADAPTER),
            "python": str(self.kaspi_python_path()),
            "script": str(self.kaspi_script_path() or ""),
            "shell_mode": self.kaspi_shell_mode(),
            "exec_preview": " ".join(self.kaspi_adapter_exec_preview(["health"])) if self.kaspi_script_path() else "",
            "bridge_outbox": str(self.bridge_outbox_dir() or ""),
            "feed_out_dir": str(self.feed_out_dir()),
            "tmp_dir": str(self.tmp_dir()),
            "http": {
                "timeout_sec": int(self.KASPI_HTTP_TIMEOUT_SEC),
                "retries": int(self.KASPI_HTTP_RETRIES),
                "retry_backoff_sec": float(self.KASPI_HTTP_RETRY_BACKOFF_SEC),
            },
            "store_alias_default": self.KASPI_STORE_ALIAS_DEFAULT or "",
        }

    # ---------------------------------------------------

    @property
    def tiptop_settings(self) -> dict:
        return {
            "public_key": self.TIPTOP_PAY_PUBLIC_KEY,
            "secret_key": self.TIPTOP_PAY_SECRET_KEY,
            "api_key": self.TIPTOP_API_KEY,
            "api_secret": self.TIPTOP_API_SECRET,
            "api_url": self.TIPTOP_API_URL,
        }

    @property
    def jwt_settings(self) -> dict:
        return {
            "secret_key": self.SECRET_KEY,
            "algorithm": self.ALGORITHM,
            "access_exp_minutes": self.ACCESS_TOKEN_EXPIRE_MINUTES,
            "refresh_exp_days": self.REFRESH_TOKEN_EXPIRE_DAYS,
        }

    @property
    def cors_config(self) -> dict:
        return {
            "allow_origins": self.CORS_ORIGINS,
            "allow_credentials": True,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        }

    # --------- PostgreSQL DSN/engine helpers ---------
    def pg_extra_query_params(self) -> dict[str, str]:
        """
        Дополнительные query-параметры для PostgreSQL DSN.
        По умолчанию в проде добавим sslmode=require (если не задан).
        """
        q: dict[str, str] = {}
        if self.POSTGRES_STATEMENT_TIMEOUT_MS:
            if self.POSTGRES_SET_TIMEOUT_DIRECT:
                q["statement_timeout"] = str(int(self.POSTGRES_STATEMENT_TIMEOUT_MS))
            else:
                q["options"] = f"-c statement_timeout={int(self.POSTGRES_STATEMENT_TIMEOUT_MS)}"
        if self.POSTGRES_SSLMODE:
            q["sslmode"] = self.POSTGRES_SSLMODE
        elif self.is_production:
            q["sslmode"] = "require"
        return q

    def _coerce_sqlalchemy_urls(self, url: str | None) -> tuple[str | None, str | None, str | None]:
        # Для реальности: в тестах/проде отсутствие URL — ошибка (дев — SQLite fallback)
        if not url:
            if self.is_production or self.is_testing:
                raise ValueError(
                    "DATABASE_URL is required and must be PostgreSQL in production/tests. "
                    "Set TEST_DATABASE_URL (pytest/TESTING) or DATABASE_URL (prod)."
                )
            file_db = self.base_dir / "app.db"
            path = "/" + PurePosixPath(file_db).as_posix()
            async_url = f"sqlite+aiosqlite://{path}"
            sync_url = f"sqlite://{path}"
            return async_url, sync_url, "sqlite"

        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()

        if scheme.startswith("sqlite"):
            if self.is_production or self.is_testing:
                raise ValueError("SQLite is not allowed in production/tests. Use PostgreSQL DSN.")
            path = parsed.path or ("/" + PurePosixPath(self.base_dir / "app.db").as_posix())
            async_url = f"sqlite+aiosqlite://{path}"
            sync_url = f"sqlite://{path}"
            return async_url, sync_url, "sqlite"

        if scheme in {"postgres", "postgresql"} or scheme.startswith("postgresql+"):
            _, netloc, path, params, query, frag = parsed
            base = urlunparse(("postgresql", netloc, path, params, query, frag))
            parsed_base = urlparse(base)
            qs = parse_qs(parsed_base.query)
            for k, v in self.pg_extra_query_params().items():
                qs[k] = [v]
            new_query = urlencode(qs, doseq=True)
            base_with_q = urlunparse(
                (
                    parsed_base.scheme,
                    parsed_base.netloc,
                    parsed_base.path,
                    parsed_base.params,
                    new_query,
                    parsed_base.fragment,
                )
            )
            async_url = base_with_q.replace("postgresql://", "postgresql+asyncpg://", 1)
            sync_url = base_with_q.replace("postgres://", "postgresql://", 1)
            return async_url, sync_url, "postgresql"

        if self.is_production or self.is_testing:
            raise ValueError("Only PostgreSQL is allowed for DATABASE_URL in production/tests.")
        return url, url, None

    @property
    def sqlalchemy_urls(self) -> dict[str, str | None]:
        a, s, d = self._coerce_sqlalchemy_urls(self.DATABASE_URL)
        return {"async": a, "sync": s, "driver": d}

    @property
    def sqlalchemy_async_url(self) -> str | None:
        """Удобный аксессор для async-DSN (postgresql+asyncpg://..., либо sqlite+aiosqlite://...)."""
        return cast(str | None, self.sqlalchemy_urls["async"])

    @property
    def sqlalchemy_sync_url(self) -> str | None:
        """Удобный аксессор для sync-DSN (postgresql://..., либо sqlite://...)."""
        return cast(str | None, self.sqlalchemy_urls["sync"])

    @property
    def sqlalchemy_engine_options(self) -> dict[str, Any]:
        return {
            "pool_size": self.SQLALCHEMY_POOL_SIZE,
            "max_overflow": self.SQLALCHEMY_MAX_OVERFLOW,
            "pool_timeout": self.SQLALCHEMY_POOL_TIMEOUT,
            "pool_recycle": self.SQLALCHEMY_POOL_RECYCLE,
            "echo": bool(self.DEBUG),
        }

    @property
    def sqlalchemy_connect_args(self) -> dict[str, Any]:
        driver = self.sqlalchemy_urls["driver"]
        if driver == "sqlite":
            return {"check_same_thread": False}
        return {}

    def sqlalchemy_engine_options_effective(self, async_engine: bool = True) -> dict[str, Any]:
        opts = dict(self.sqlalchemy_engine_options)
        if self.sqlalchemy_urls["driver"] == "sqlite":
            for k in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
                opts.pop(k, None)
            if self.sqlalchemy_connect_args:
                opts["connect_args"] = self.sqlalchemy_connect_args
        else:
            if self.sqlalchemy_connect_args:
                opts["connect_args"] = self.sqlalchemy_connect_args
        return opts

    # --------- диагностика/дампы ---------
    def health_check(self) -> dict:
        errors: list[str] = []
        for d in [
            self.UPLOAD_DIR,
            self.STATIC_DIR,
            self.MEDIA_DIR,
            os.path.dirname(self.LOG_PATH) if self.LOG_PATH else "",
            self.KASPI_FEED_OUT_DIR,
            self.KASPI_TMP_DIR,
        ]:
            if d:
                p = self.resolve_path(d)
                if not p.exists():
                    errors.append(f"Missing directory: {p}")
                if not _writable(p if p.is_dir() else p.parent):
                    errors.append(f"Not writable: {p}")

        if not self.SECRET_KEY or self.SECRET_KEY.strip().lower() in {
            "changeme",
            "secret",
            "password",
        }:
            errors.append("Insecure SECRET_KEY")

        if self.is_production and not self.DATABASE_URL:
            errors.append("Missing DATABASE_URL in production")

        if (
            self.DATABASE_URL
            and not self._is_postgres_url(self.DATABASE_URL)
            and (self.is_production or self.is_testing)
        ):
            errors.append("Non-PostgreSQL DATABASE_URL in production/tests")

        # Ключ шифрования токенов Kaspi
        if not self.kaspi_encryption_enabled:
            errors.append("KASPI token encryption key is not configured (PGCRYPTO_KEY/KASPI_TOKEN_KEY)")

        # Наличие pgcrypto — напоминание (флаг-индикатор; саму проверку наличия делаем на уровне БД)
        if not self.has_pgcrypto_hint:
            errors.append("pgcrypto extension might be missing")

        # Проверка адаптера/скрипта и python
        if self.ENABLE_KASPI_ADAPTER:
            sp = self.kaspi_script_path()
            if not sp:
                errors.append("KASPI_SCRIPT_PATH is not configured")
            else:
                p = Path(sp)
                if not p.exists():
                    errors.append(f"KASPI_SCRIPT_PATH not found: {p}")
                elif not (p.is_file() or p.suffix.lower() in {".ps1", ".bat", ".cmd", ".py"}):
                    errors.append(f"KASPI_SCRIPT_PATH is not a regular file or script: {p}")
            py = self.kaspi_python_path()
            if not Path(py).exists():
                errors.append(f"KASPI_PYTHON not found: {py}")

            # При сценариях .ps1 — предупредим, если нет шеллов
            if sp and Path(sp).suffix.lower() == ".ps1":
                if not (self.KASPI_PWSH or self.KASPI_POWERSHELL):
                    errors.append("For .ps1 adapter set KASPI_PWSH or KASPI_POWERSHELL")

        ok = not errors
        result = {
            "ok": ok,
            "errors": errors,
            "system": {"python": sys.version.split()[0], "platform": platform.platform()},
            "build": self.build_info,
            "kaspi_encryption_enabled": self.kaspi_encryption_enabled,
            "kaspi_adapter": self.kaspi_adapter_settings if self.ENABLE_KASPI_ADAPTER else {"enabled": False},
        }
        if errors:
            logging.getLogger(__name__).warning("Health check errors: %s", errors)
        return result

    def dump_settings(self) -> None:
        if not self.DEBUG_CONFIG_DUMP:
            return
        logging.getLogger(__name__).info("Settings dump: %s", self.dump_settings_safe())

    def dump_settings_safe(self) -> dict:
        raw = self.model_dump()
        # безопасно подменим ключ шифрования на маску
        raw["PGCRYPTO_KEY"] = self.kaspi_crypto_key_masked
        return _mask_nested(raw)

    def generate_env_example(self, path: str | None = None) -> Path:
        example = []
        for k, v in self.model_dump().items():
            if v is None:
                val = ""
            elif isinstance(v, list | dict):
                val = json.dumps(v, ensure_ascii=False)
            else:
                val = str(v)
            if _is_secret_key_name(k):
                val = _mask_secret(val) or ""
            example.append(f"{k}={val}")
        content = "\n".join(example) + "\n"
        out_path = Path(path) if path else (self.base_dir / ".env.example")
        out_path.write_text(content, encoding="utf-8")
        return out_path

    # ---- интеграции observability ----
    def init_sentry(self) -> None:
        if not self.SENTRY_DSN:
            return
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration

            sentry_logging = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
            sentry_sdk.init(
                dsn=self.SENTRY_DSN,
                integrations=[sentry_logging],
                environment=self.ENVIRONMENT,
                release=f"{self.PROJECT_NAME}@{self.VERSION}",
                traces_sample_rate=0.0,
            )
            logging.getLogger(__name__).info("Sentry initialized")
        except Exception as e:
            logging.getLogger(__name__).warning("Sentry init failed: %s", e)

    def init_opentelemetry(self) -> None:
        if not self.OTEL_EXPORTER_OTLP_ENDPOINT:
            return
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create(
                {
                    "service.name": self.OTEL_SERVICE_NAME or self.PROJECT_NAME,
                    "service.version": self.VERSION,
                    "deployment.environment": self.ENVIRONMENT,
                }
            )
            provider = TracerProvider(resource=resource)
            processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=self.OTEL_EXPORTER_OTLP_ENDPOINT))
            provider.add_span_processor(processor)
            trace.set_tracer_provider(provider)
            logging.getLogger(__name__).info("OpenTelemetry initialized")
        except Exception as e:
            logging.getLogger(__name__).warning("OpenTelemetry init failed: %s", e)

    @property
    def uvicorn_kwargs(self) -> dict:
        reload_ = self.is_development
        workers = self.UVICORN_WORKERS
        if reload_:
            workers = 1  # при reload uvicorn всегда 1 воркер
        return {
            "host": self.HOST,
            "port": int(self.PORT),
            "reload": reload_,
            "log_level": (self.LOG_LEVEL or "info").lower(),
            "proxy_headers": True,
            "forwarded_allow_ips": "*",
            "workers": workers,
            "root_path": self.ROOT_PATH or "",
        }

    # -------------------- ДОБАВЛЕНО: Path helpers & Kaspi / JSON:API / Crypto --------------------
    def resolve_path(self, maybe_path: str | Path) -> Path:
        """
        Превращает относительный путь в абсолютный относительно корня проекта.
        Не трогает пути с диском (Windows) и абсолютные POSIX.
        """
        p = Path(maybe_path) if not isinstance(maybe_path, Path) else maybe_path
        if p.is_absolute():
            return p
        return (self.base_dir / p).resolve()

    def app_tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.APP_TIMEZONE or "Asia/Almaty")
        except Exception:
            return KZ_TZ

    def dt_to_ms_almaty(self, dt: datetime) -> int:
        """
        Перевод datetime -> миллисекунды Unix с учётом TZ Алматы (Kaspi).
        """
        tz = self.app_tz()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)
        return int(dt.timestamp() * 1000)

    def ms_to_dt_almaty(self, ms: int) -> datetime:
        """
        Миллисекунды Unix -> datetime в TZ Алматы.
        """
        seconds = ms / 1000.0
        return datetime.fromtimestamp(seconds, self.app_tz())

    def kaspi_jsonapi_headers(self, token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        """
        Базовые заголовки для Kaspi Shop JSON:API:
        - Content-Type: application/vnd.api+json
        - X-Auth-Token: <token>
        + любые дополнительные заголовки (например, X-Security-Code, X-Send-Code)
        """
        if not token or len(token.strip()) < 8:
            raise ValueError("Kaspi token looks invalid (too short).")
        headers = {
            "Content-Type": JSONAPI_MIME,
            KASPI_JSONAPI_AUTH_HEADER: token.strip(),
        }
        if extra:
            headers.update(extra)
        return headers

    def kaspi_orders_url(self) -> str:
        """
        Базовый URL для заказов:
        <base>/v2/orders
        """
        base = self.kaspi_shop_settings["base_url"]
        return f"{base}/v2/orders"

    def kaspi_orderentries_product_url(self, order_entry_id: str) -> str:
        """
        URL получения продукта по подпункту заказа:
        <base>/v2/orderentries/{orderEntryId}/product
        """
        base = self.kaspi_shop_settings["base_url"]
        return f"{base}/v2/orderentries/{order_entry_id}/product"

    def kaspi_partial_cancel_url(self, order_code_or_id: str) -> str:
        """
        URL для частичной отмены (вариативно в доках): /api/orderPartialCancel/{id}
        """
        base = self.kaspi_shop_settings["base_url"]
        # оставим /shop/api без модификаций; endpoint вне v2
        return f"{base}/orderPartialCancel/{order_code_or_id}"

    def kaspi_orders_query(
        self,
        *,
        page_number: int = 0,
        page_size: int | None = None,
        state: str,
        status: str | None = None,
        creation_date_ge_ms: int,
        creation_date_le_ms: int | None = None,
        delivery_type: str | None = None,
        include_entries: bool = True,
        signature_required: bool | None = None,
        sort: str | None = None,
    ) -> dict[str, list[str] | str | int | bool]:
        """
        Формирует query-параметры для GET /v2/orders по JSON:API:
        - page[number], page[size]
        - filter[orders][state] (обязателен)
        - filter[orders][status] (опционально)
        - filter[orders][creationDate][$ge] (обязателен), [$le] (опц.)
        - filter[orders][deliveryType] (опц.)
        - filter[orders][signatureRequired] (опц.)
        - include[orders]=entries (для состава)
        - sort (опц.)
        """
        max_size = int(self.kaspi_shop_settings["page_size_max"])
        size = int(page_size or self.kaspi_shop_settings["default_page_size"])
        if size <= 0 or size > max_size:
            size = max_size

        q: dict[str, Any] = {
            "page[number]": page_number,
            "page[size]": size,
            "filter[orders][state]": state,
            "filter[orders][creationDate][$ge]": creation_date_ge_ms,
        }
        if creation_date_le_ms is not None:
            q["filter[orders][creationDate][$le]"] = creation_date_le_ms
        if status:
            q["filter[orders][status]"] = status
        if delivery_type:
            q["filter[orders][deliveryType]"] = delivery_type
        if signature_required is not None:
            q["filter[orders][signatureRequired]"] = "true" if bool(signature_required) else "false"
        if include_entries:
            q["include[orders]"] = "entries"
        if sort:
            q["sort"] = sort
        return q

    # --- Ключ шифрования для pgcrypto (используется в моделях KaspiStoreToken и пр.) ---
    def get_kaspi_enc_key(self) -> str:
        """
        Возвращает симметричный ключ для pgp_sym_encrypt/decrypt.
        Политика:
        1) Берём PGCRYPTO_KEY (или его алиасы);
        2) Если пусто — фоллбэк на SECRET_KEY;
        3) Минимальная длина 16 символов.
        В случае несоответствия — поднимаем ValueError (в проде это критично).
        """
        key = (self.PGCRYPTO_KEY or "").strip()
        if not key:
            key = (self.SECRET_KEY or "").strip()
        if len(key) < 16:
            raise ValueError(
                "KASPI token encryption key is too short. " "Set PGCRYPTO_KEY (or KASPI_TOKEN_KEY) with length >= 16."
            )
        return key

    @property
    def kaspi_encryption_enabled(self) -> bool:
        try:
            return len((self.PGCRYPTO_KEY or self.SECRET_KEY or "").strip()) >= 16
        except Exception:
            return False

    @property
    def kaspi_crypto_key_masked(self) -> str | None:
        raw = (self.PGCRYPTO_KEY or self.SECRET_KEY or "").strip()
        return _mask_secret(raw) if raw else None

    @property
    def has_pgcrypto_hint(self) -> bool:
        """
        Хелпер-флаг для диагностики: напоминаем, что в БД должно быть
        расширение pgcrypto (CREATE EXTENSION IF NOT EXISTS pgcrypto;).
        """
        return True

    # ---- Paths used by adapters/Bridge ----
    def kaspi_python_path(self) -> str:
        return self.KASPI_PYTHON or sys.executable

    def kaspi_script_path(self) -> Path | None:
        if not self.KASPI_SCRIPT_PATH:
            return None
        return self.resolve_path(self.KASPI_SCRIPT_PATH)

    def bridge_outbox_dir(self) -> Path | None:
        if not self.KASPI_BRIDGE_OUTBOX:
            return None
        return self.resolve_path(self.KASPI_BRIDGE_OUTBOX)

    def feed_out_dir(self) -> Path:
        return self.resolve_path(self.KASPI_FEED_OUT_DIR)

    def tmp_dir(self) -> Path:
        return self.resolve_path(self.KASPI_TMP_DIR)

    # ---- ДОБАВЛЕНО: Унифицированный выбор шелла и сборка команды адаптера ----
    def kaspi_shell_mode(self) -> str:
        """
        Возвращает эффективный режим запуска адаптера:
        - 'python' для .py при наличии KASPI_PYTHON;
        - 'pwsh' если .ps1 и есть KASPI_PWSH;
        - 'powershell' если .ps1 и нет pwsh, но есть powershell.exe;
        - 'cmd' для .bat/.cmd;
        - если KASPI_SHELL явно задан — уважим его (кроме недоступного интерпретатора).
        """
        forced = (self.KASPI_SHELL or "auto").lower()
        sp = self.kaspi_script_path()
        ext = sp.suffix.lower() if sp else ""

        def has(path: str | None) -> bool:
            return bool(path and Path(path).exists())

        # Явно форсированный режим
        if forced in {"python", "pwsh", "powershell", "cmd"}:
            if forced == "python" and not has(self.kaspi_python_path()):
                return "auto"
            if forced == "pwsh" and not has(self.KASPI_PWSH):
                return "auto"
            if forced == "powershell" and not has(self.KASPI_POWERSHELL):
                return "auto"
            return forced

        # AUTO
        if ext == ".py" and has(self.kaspi_python_path()):
            return "python"
        if ext == ".ps1":
            if has(self.KASPI_PWSH):
                return "pwsh"
            if has(self.KASPI_POWERSHELL):
                return "powershell"
        if ext in {".bat", ".cmd"}:
            return "cmd"

        # fallback: python если есть
        if has(self.kaspi_python_path()):
            return "python"
        return "cmd"

    def kaspi_adapter_exec_preview(self, args: list[str] | None = None) -> list[str]:
        """
        Возвращает список аргументов процесса (preview), которым будет запущен адаптер.
        """
        return self.build_adapter_command(args or [])

    def build_adapter_command(self, args: list[str]) -> list[str]:
        """
        Собирает команду запуска адаптера с учётом выбранного шелла.
        Примеры:
          - python <script.py> <args...>
          - "C:\\Program Files\\PowerShell\\7\\pwsh.exe" -NoProfile -NonInteractive -File <script.ps1> <args...>
          - powershell.exe -NoProfile -NonInteractive -File <script.ps1> <args...>
          - cmd /c "<script.bat> <args...>"
        """
        sp = self.kaspi_script_path()
        if not sp:
            raise RuntimeError("KASPI_SCRIPT_PATH is not configured")

        mode = self.kaspi_shell_mode()
        script = str(sp)

        if mode == "python":
            exe = self.kaspi_python_path()
            return [exe, script, *args]

        if mode == "pwsh":
            exe = self.KASPI_PWSH or "pwsh"
            return [exe, "-NoProfile", "-NonInteractive", "-File", script, *args]

        if mode == "powershell":
            exe = self.KASPI_POWERSHELL or "powershell"
            return [exe, "-NoProfile", "-NonInteractive", "-File", script, *args]

        if mode == "cmd":
            # В cmd /c — выполнит и закроет. Скрипт/батник будет первым аргументом.
            return ["cmd", "/c", script, *args]

        # На крайний случай — запуск как исполняемого файла
        return [script, *args]

    # -------------------------------------------------------------------------


# Глобальный объект настроек
@lru_cache
def get_settings() -> Settings:
    s = Settings()

    # Resolve database URL once with strict priority (no rewriting)
    resolved_url, resolved_source, resolved_fp = resolve_database_url(s)
    object.__setattr__(s, "DATABASE_URL", resolved_url)
    object.__setattr__(s, "DB_URL_SOURCE", resolved_source)
    object.__setattr__(s, "DB_URL_FINGERPRINT", resolved_fp)

    # ensure TESTING flag reflects env/pytest for downstream checks
    if _under_pytest() and not s.TESTING:
        object.__setattr__(s, "TESTING", True)

    # Export password to PGPASSWORD when present (helps psycopg2/Alembic)
    try:
        parsed = urlparse(resolved_url)
        if parsed.password and not os.getenv("PGPASSWORD"):
            os.environ["PGPASSWORD"] = parsed.password
    except Exception:
        pass

    # Для тестов фиксируем безопасный SMTP порт, чтобы .env не подменял на 25
    if s.TESTING:
        try:
            smtp_test = int(os.getenv("SMTP_PORT_TEST", "587"))
        except Exception:
            smtp_test = 587
        object.__setattr__(s, "SMTP_PORT", smtp_test)

    # One-time structured log for DB URL resolution (without secrets)
    try:
        logging.getLogger(__name__).info(
            "db_url_resolved",
            extra={
                "event_name": "db_url_resolved",
                "db_url_source": resolved_source,
                "db_url_fingerprint": resolved_fp,
            },
        )
    except Exception:
        pass

    # Нормализация REDIS_URL с учётом REDIS_PASSWORD/DB
    if (s.REDIS_PASSWORD is not None) or (s.REDIS_DB is not None):
        try:
            p = urlparse(s.REDIS_URL)
            scheme = p.scheme or "redis"
            host = p.hostname or "localhost"
            port = p.port or 6379
            username = p.username or ""
            password = s.REDIS_PASSWORD if s.REDIS_PASSWORD not in (None, "") else (p.password or "")
            username_q = quote(username, safe="") if username else ""
            password_q = quote(password, safe="") if password else ""
            if username_q and password_q:
                auth = f"{username_q}:{password_q}@"
            elif username_q and not password_q:
                auth = f"{username_q}@"
            elif password_q and not username_q:
                auth = f":{password_q}@"
            else:
                auth = ""
            current_db = p.path.lstrip("/") if p.path else "0"
            path_db = str(s.REDIS_DB) if s.REDIS_DB is not None else current_db
            path = f"/{path_db}"
            new_url = urlunparse((scheme, f"{auth}{host}:{port}", path, p.params, p.query, p.fragment))
            object.__setattr__(s, "REDIS_URL", new_url)
        except Exception:
            pass

    # Безопасный дефолт sslmode=require под прод, если не задан явно
    if s.is_production and not s.POSTGRES_SSLMODE:
        object.__setattr__(s, "POSTGRES_SSLMODE", "require")

    # Страховка: если порт SMTP не задан или 0 — выставим 587
    try:
        if int(s.SMTP_PORT or 0) <= 0:
            object.__setattr__(s, "SMTP_PORT", 587)
    except Exception:
        object.__setattr__(s, "SMTP_PORT", 587)

    # Флаг для отключения побочных эффектов на старте
    disable_hooks = os.getenv("DISABLE_APP_STARTUP_HOOKS") == "1"

    if s.EAGER_SIDE_EFFECTS and not _under_pytest() and not disable_hooks:
        s.ensure_dirs()
        s.check_secret_key()
        s.check_database_url()
        s.check_allowed_hosts()
        s.check_cors_frontend()
        s.check_smtp()
        s.configure_logging()
        s._log_config_summary()  # информативная сводка в лог
        s.init_sentry()
        s.init_opentelemetry()
    return s


settings = get_settings()

__all__ = [
    "Settings",
    "get_settings",
    "settings",
    "should_disable_startup_hooks",
    "db_url_fingerprint",
    "db_connection_fingerprint",
    "JSONAPI_MIME",
    "KASPI_JSONAPI_AUTH_HEADER",
]
