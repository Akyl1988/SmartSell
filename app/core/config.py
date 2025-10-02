from __future__ import annotations

import os
import sys
import json
import time
import logging
import platform
from logging.handlers import RotatingFileHandler
from functools import lru_cache
from typing import Optional, List, Tuple, Dict, Any, cast
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs, quote

from pydantic import Field, EmailStr, AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOGGING_CONFIGURED = False

# ================================
# ВСПОМОГАТЕЛЬНЫЕ ХЕЛПЕРЫ
# ================================
def _under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def _mask_secret(val: Optional[str]) -> Optional[str]:
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


def _parse_list_like(v):
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
    if any(s in lk for s in ("secret", "password", "token", "dsn")):
        return True
    if "key" in lk and "public" not in lk:
        return True
    return False


def _mask_nested(obj: Any, key_hint: Optional[str] = None) -> Any:
    """
    Рекурсивная маскировка секретов в dict/list/tuple.
    """
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _is_secret_key_name(k):
                if isinstance(v, (dict, list, tuple)):
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
    DEBUG: bool = Field(default=False, description="Debug mode", env="DEBUG")
    ENVIRONMENT: str = Field(default="development", description="Environment", env="ENVIRONMENT")
    TESTING: bool = Field(default=False, description="Testing mode", env="TESTING")
    API_V1_STR: str = Field(default="/api/v1", description="API v1 prefix")
    HOST: str = Field(default="127.0.0.1", description="Server host", env="HOST")
    PORT: int = Field(default=8000, description="Server port", env="PORT")
    SCHEME: str = Field(default="http", description="Public scheme", env="SCHEME")
    PUBLIC_URL: Optional[AnyHttpUrl] = Field(default=None, description="Public API URL", env="PUBLIC_URL")

    # ---- security/JWT
    SECRET_KEY: str = Field(default="changeme", description="JWT secret key", env="SECRET_KEY")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, description="Access token expiry")
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, description="Refresh token expiry")
    ALGORITHM: str = Field(default="HS256", description="JWT algorithm")
    MAX_LOGIN_ATTEMPTS: int = Field(default=5, description="Max login attempts")
    PASSWORD_MIN_LENGTH: int = Field(default=8, description="Password min length")

    # ---- БД
    DATABASE_URL: Optional[str] = Field(default=None, description="Database URL", env="DATABASE_URL")
    DATABASE_TEST_URL: Optional[str] = Field(default=None, description="Test database URL (legacy)", env="DATABASE_TEST_URL")
    TEST_DATABASE_URL: Optional[str] = Field(default=None, description="Test database URL", env="TEST_DATABASE_URL")
    SQLALCHEMY_POOL_SIZE: int = Field(default=10, description="Pool size")
    SQLALCHEMY_MAX_OVERFLOW: int = Field(default=20, description="Max overflow")
    SQLALCHEMY_POOL_TIMEOUT: int = Field(default=30, description="Pool timeout (s)")
    SQLALCHEMY_POOL_RECYCLE: int = Field(default=1800, description="Pool recycle (s)")

    # ---- Redis/Celery
    REDIS_URL: str = Field(default="redis://localhost:6379", description="Redis URL", env="REDIS_URL")
    REDIS_PASSWORD: Optional[str] = Field(default=None, description="Redis password", env="REDIS_PASSWORD")
    REDIS_DB: int = Field(default=0, description="Redis db index", env="REDIS_DB")

    CELERY_BROKER_URL: str = Field(default="redis://localhost:6379/0", description="Celery broker URL", env="CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:6379/0", description="Celery result backend", env="CELERY_RESULT_BACKEND")
    SCHEDULER_TIMEZONE: str = Field(default="UTC", description="Scheduler timezone")
    EAGER_SIDE_EFFECTS: bool = Field(default=True, env="EAGER_SIDE_EFFECTS")

    # ---- rate limits
    RATE_LIMIT_PER_MINUTE: int = Field(default=100, description="Rate limit per minute")
    RATE_LIMIT_WINDOW_SECONDS: int = Field(default=60, description="Rate limit window (seconds)")
    RATE_LIMIT_BURST: int = Field(default=100, description="Rate limit burst")

    # ---- CORS/hosts
    ALLOWED_HOSTS: List[str] = Field(default=["*"], description="Allowed hosts", env="ALLOWED_HOSTS")
    CORS_ORIGINS: List[str] = Field(default=["*"], description="CORS origins", env="CORS_ORIGINS")
    BACKEND_CORS_ORIGINS: List[str] = Field(
        default=["http://localhost", "http://localhost:3000"],
        description="Backend CORS origins",
        env="BACKEND_CORS_ORIGINS",
    )

    # ---- Файлы/логи
    STATIC_DIR: str = Field(default="static", description="Static directory", env="STATIC_DIR")
    MEDIA_DIR: str = Field(default="media", description="Media directory", env="MEDIA_DIR")
    UPLOAD_DIR: str = Field(default="uploads", description="Upload directory", env="UPLOAD_DIR")
    MAX_UPLOAD_SIZE: int = Field(default=10 * 1024 * 1024, description="Max upload size")
    LOG_PATH: str = Field(default="logs/app.log", description="Log file path", env="LOG_PATH")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level", env="LOG_LEVEL")
    LOG_FORMAT: str = Field(default="json", description="Logging format (json|text)", env="LOG_FORMAT")

    # ---- Frontend
    FRONTEND_URL: AnyHttpUrl | str = Field(default="http://localhost:3000", description="Frontend URL", env="FRONTEND_URL")

    # ---- Провайдеры
    MOBIZON_API_KEY: Optional[str] = Field(default=None, description="Mobizon API key", env="MOBIZON_API_KEY")
    MOBIZON_API_URL: str = Field(default="https://api.mobizon.kz", description="Mobizon API URL")

    CLOUDINARY_CLOUD_NAME: Optional[str] = Field(default=None, description="Cloudinary cloud name", env="CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY: Optional[str] = Field(default=None, description="Cloudinary API key", env="CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET: Optional[str] = Field(default=None, description="Cloudinary API secret", env="CLOUDINARY_API_SECRET")

    KASPI_MERCHANT_ID: Optional[str] = Field(default=None, description="Kaspi merchant ID", env="KASPI_MERCHANT_ID")
    KASPI_API_KEY: Optional[str] = Field(default=None, description="Kaspi API key", env="KASPI_API_KEY")
    KASPI_API_URL: str = Field(default="https://api.kaspi.kz", description="Kaspi API URL")

    TIPTOP_PAY_PUBLIC_KEY: Optional[str] = Field(default=None, description="TipTop Pay public key", env="TIPTOP_PAY_PUBLIC_KEY")
    TIPTOP_PAY_SECRET_KEY: Optional[str] = Field(default=None, description="TipTop Pay secret key", env="TIPTOP_PAY_SECRET_KEY")
    TIPTOP_API_KEY: Optional[str] = Field(default=None, description="TipTop API key", env="TIPTOP_API_KEY")
    TIPTOP_API_SECRET: Optional[str] = Field(default=None, description="TipTop API secret", env="TIPTOP_API_SECRET")
    TIPTOP_API_URL: str = Field(default="https://api.tippy.kz", description="TipTop API URL")

    # ---- SMTP
    SMTP_HOST: str = Field(default="smtp.gmail.com", description="SMTP host", env="SMTP_HOST")
    SMTP_PORT: int = Field(default=587, description="SMTP port", env="SMTP_PORT")
    SMTP_USER: str = Field(default="", description="SMTP user", env="SMTP_USER")
    SMTP_PASSWORD: str = Field(default="", description="SMTP password", env="SMTP_PASSWORD")
    SMTP_FROM_EMAIL: EmailStr | None = Field(default=None, description="Sender email", env="SMTP_FROM_EMAIL")
    SMTP_TLS: bool = Field(default=True, description="Use STARTTLS", env="SMTP_TLS")
    SMTP_SSL: bool = Field(default=False, description="Use SSL", env="SMTP_SSL")

    # ---- OAuth
    GOOGLE_CLIENT_ID: str | None = Field(default="", description="Google client id", env="GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: str | None = Field(default="", description="Google client secret", env="GOOGLE_CLIENT_SECRET")
    FACEBOOK_CLIENT_ID: str | None = Field(default="", description="Facebook client id", env="FACEBOOK_CLIENT_ID")
    FACEBOOK_CLIENT_SECRET: str | None = Field(default="", description="Facebook client secret", env="FACEBOOK_CLIENT_SECRET")

    # ---- Observability/Runtime
    SENTRY_DSN: Optional[str] = Field(default=None, description="Sentry DSN", env="SENTRY_DSN")
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = Field(default=None, description="OTLP endpoint", env="OTEL_EXPORTER_OTLP_ENDPOINT")
    OTEL_SERVICE_NAME: Optional[str] = Field(default=None, description="OTEL service name", env="OTEL_SERVICE_NAME")
    UVICORN_WORKERS: int = Field(default=1, description="Uvicorn workers count", env="UVICORN_WORKERS")
    ROOT_PATH: str = Field(default="", description="ASGI root_path for reverse proxy", env="ROOT_PATH")

    # ---- PostgreSQL доп-настройки
    POSTGRES_STATEMENT_TIMEOUT_MS: Optional[int] = Field(default=None, env="POSTGRES_STATEMENT_TIMEOUT_MS")
    POSTGRES_SSLMODE: Optional[str] = Field(default=None, env="POSTGRES_SSLMODE")
    POSTGRES_SET_TIMEOUT_DIRECT: bool = Field(default=False, env="POSTGRES_SET_TIMEOUT_DIRECT")

    # ---- Release metadata
    GIT_COMMIT_SHA: Optional[str] = Field(default=None, description="Git commit SHA", env="GIT_COMMIT")
    GIT_BRANCH: Optional[str] = Field(default=None, description="Git branch name", env="GIT_BRANCH")
    BUILD_TIMESTAMP: Optional[str] = Field(default=None, description="Build timestamp", env="BUILD_TIMESTAMP")

    # --------- валидаторы ---------
    @field_validator("CORS_ORIGINS", mode="before")
    def _cors(cls, v):
        return _parse_list_like(v)

    @field_validator("ALLOWED_HOSTS", mode="before")
    def _hosts(cls, v):
        return _parse_list_like(v)

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    def _bcors(cls, v):
        return _parse_list_like(v)

    @field_validator("SMTP_FROM_EMAIL", mode="before")
    def empty_email_is_none(cls, v):
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("PUBLIC_URL", mode="before")
    def normalize_public_url(cls, v):
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
    def check_alg(cls, v):
        allowed = {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"}
        if v not in allowed:
            raise ValueError(f"Unsupported JWT algorithm: {v}")
        return v

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
        }
        for d in filter(None, dirs):
            p = Path(d)
            if not p.is_absolute():
                p = self.base_dir / d
            if not _writable(p):
                logging.getLogger(__name__).warning(f"Directory not writable: {p}")

    def check_secret_key(self) -> None:
        if self.is_production:
            if not self.SECRET_KEY or self.SECRET_KEY.strip().lower() in {"changeme", "secret", "password"}:
                raise ValueError("Set a secure SECRET_KEY in .env for production!")

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
            logging.getLogger(__name__).warning(
                "ALLOWED_HOSTS='*' в production — небезопасно. Задайте список доменов."
            )

    def check_cors_frontend(self) -> None:
        try:
            if self.is_production and self.FRONTEND_URL:
                origin = str(self.FRONTEND_URL).rstrip("/")
                if self.CORS_ORIGINS != ["*"] and origin not in self.CORS_ORIGINS:
                    logging.getLogger(__name__).warning(
                        "FRONTEND_URL not present in CORS_ORIGINS: %s", origin
                    )
        except Exception:
            pass

    def check_smtp(self) -> None:
        if (self.SMTP_HOST and (self.SMTP_USER or self.SMTP_PASSWORD)) and not self.SMTP_FROM_EMAIL:
            logging.getLogger(__name__).warning(
                "SMTP_FROM_EMAIL is empty while SMTP credentials are set."
            )
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
            log_file = Path(self.LOG_PATH)
            if not log_file.is_absolute():
                log_file = self.base_dir / self.LOG_PATH
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                str(log_file), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
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

    def _log_config_summary(self) -> None:
        """
        Безопасная сводка конфигурации в лог (без секретов).
        """
        try:
            drv = self.sqlalchemy_urls["driver"] or "unknown"
            db_url = self.DATABASE_URL or ""
            # Маскируем креды в DSN
            try:
                parsed = urlparse(db_url)
                if parsed.scheme:
                    safe_netloc = parsed.hostname or ""
                    if parsed.port:
                        safe_netloc += f":{parsed.port}"
                    db_url_safe = urlunparse(
                        (parsed.scheme.split("+")[0], safe_netloc, parsed.path, "", "", "")
                    )
                else:
                    db_url_safe = ""
            except Exception:
                db_url_safe = ""
            logging.getLogger(__name__).info(
                "config_summary=%s",
                json.dumps(
                    {
                        "env": self.ENVIRONMENT,
                        "log_level": (self.LOG_LEVEL or "INFO").upper(),
                        "log_format": (self.LOG_FORMAT or "json").lower(),
                        "db_driver": drv,
                        "db_url": db_url_safe,
                        "public_url": self.public_url,
                        "uvicorn_workers": int(self.UVICORN_WORKERS),
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception:
            pass

    # --------- групповые представления настроек ---------
    @property
    def redis_settings(self) -> dict:
        return {"url": self.REDIS_URL, "password": self.REDIS_PASSWORD, "db": self.REDIS_DB}

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
            logging.getLogger(__name__).warning(
                "Both SMTP_TLS and SMTP_SSL are True; forcing SSL semantics."
            )
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
        return {"merchant_id": self.KASPI_MERCHANT_ID, "api_key": self.KASPI_API_KEY, "api_url": self.KASPI_API_URL}

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
    def pg_extra_query_params(self) -> Dict[str, str]:
        """
        Дополнительные query-параметры для PostgreSQL DSN.
        По умолчанию в проде добавим sslmode=require (если не задан).
        """
        q: Dict[str, str] = {}
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

    def _coerce_sqlalchemy_urls(self, url: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
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
    def sqlalchemy_urls(self) -> Dict[str, Optional[str]]:
        a, s, d = self._coerce_sqlalchemy_urls(self.DATABASE_URL)
        return {"async": a, "sync": s, "driver": d}

    @property
    def sqlalchemy_async_url(self) -> Optional[str]:
        """Удобный аксессор для async-DSN (postgresql+asyncpg://..., либо sqlite+aiosqlite://...)."""
        return cast(Optional[str], self.sqlalchemy_urls["async"])

    @property
    def sqlalchemy_sync_url(self) -> Optional[str]:
        """Удобный аксессор для sync-DSN (postgresql://..., либо sqlite://...)."""
        return cast(Optional[str], self.sqlalchemy_urls["sync"])

    @property
    def sqlalchemy_engine_options(self) -> Dict[str, Any]:
        return {
            "pool_size": self.SQLALCHEMY_POOL_SIZE,
            "max_overflow": self.SQLALCHEMY_MAX_OVERFLOW,
            "pool_timeout": self.SQLALCHEMY_POOL_TIMEOUT,
            "pool_recycle": self.SQLALCHEMY_POOL_RECYCLE,
            "echo": bool(self.DEBUG),
        }

    @property
    def sqlalchemy_connect_args(self) -> Dict[str, Any]:
        driver = self.sqlalchemy_urls["driver"]
        if driver == "sqlite":
            return {"check_same_thread": False}
        return {}

    def sqlalchemy_engine_options_effective(self, async_engine: bool = True) -> Dict[str, Any]:
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
        ]:
            if d:
                p = Path(d)
                if not p.is_absolute():
                    p = self.base_dir / d
                if not p.exists():
                    errors.append(f"Missing directory: {p}")
                if not _writable(p if p.is_dir() else p.parent):
                    errors.append(f"Not writable: {p}")
        if not self.SECRET_KEY or self.SECRET_KEY.strip().lower() in {"changeme", "secret", "password"}:
            errors.append("Insecure SECRET_KEY")
        if self.is_production and not self.DATABASE_URL:
            errors.append("Missing DATABASE_URL in production")
        if self.DATABASE_URL and not self._is_postgres_url(self.DATABASE_URL) and (self.is_production or self.is_testing):
            errors.append("Non-PostgreSQL DATABASE_URL in production/tests")

        ok = not errors
        result = {
            "ok": ok,
            "errors": errors,
            "system": {"python": sys.version.split()[0], "platform": platform.platform()},
            "build": self.build_info,
        }
        if errors:
            logging.getLogger(__name__).warning("Health check errors: %s", errors)
        return result

    def dump_settings(self) -> None:
        import pprint
        pprint.pprint(self.model_dump())

    def dump_settings_safe(self) -> dict:
        raw = self.model_dump()
        return _mask_nested(raw)

    def generate_env_example(self, path: Optional[str] = None) -> Path:
        example = []
        for k, v in self.model_dump().items():
            if v is None:
                val = ""
            elif isinstance(v, (list, dict)):
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


# Глобальный объект настроек
@lru_cache
def get_settings() -> Settings:
    s = Settings()

    # Жёсткая политика: в тестах (pytest/TESTING) — только PostgreSQL
    under_test = s.TESTING or _under_pytest()
    if under_test:
        test_db = s.TEST_DATABASE_URL or s.DATABASE_TEST_URL or s.DATABASE_URL
        if not test_db:
            raise ValueError(
                "TEST_DATABASE_URL (или DATABASE_TEST_URL) обязателен в тестах и должен быть PostgreSQL, "
                "например: postgresql://user:pass@localhost:5432/testdb"
            )
        try:
            parsed = urlparse(test_db)
            scheme = (parsed.scheme or "").lower()
            if not (scheme in {"postgres", "postgresql"} or scheme.startswith("postgresql+")):
                raise ValueError
        except Exception:
            raise ValueError("Некорректный TEST_DATABASE_URL: требуется PostgreSQL URL")
        object.__setattr__(s, "DATABASE_URL", test_db)

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

__all__ = ["Settings", "get_settings", "settings"]
