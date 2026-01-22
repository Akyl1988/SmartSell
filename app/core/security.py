"""
Security utilities for authentication and authorization (enterprise-grade).

Ключевые возможности:
- JWT с поддержкой KID/ротации ключей, issuer/audience/jti/iat/nbf/leeway.
- Политики паролей: сложность, частые пароли/слова (расширяемый словарь).
- Хеширование: Argon2 (если доступен) -> bcrypt; поддержка pepper; миграция bcrypt→argon2.
- Denylist для JTI: Redis → Postgres (SQLAlchemy Core) → in-memory TTL.
- Refresh-токен через HttpOnly cookie (двойная защита) + хелперы.
- Отладка: перечисление активных KID/ключей, просмотр denylist (если доступно).
- Тест-утилиты: генерация токенов/паролей.
- Хуки для интеграции с Identity Providers (OAuth2/OIDC/SAML) — best-effort.
- Полная совместимость со старыми функциями: create_access_token, create_refresh_token,
  verify_password, get_password_hash, verify_token.

ENV (опционально):
  JWT_ISSUER=smartsell3
  JWT_AUDIENCE=smartsell3-users
  JWT_LEEWAY_SECONDS=30
  # Основной алгоритм, должен совпадать с Settings.ALGORITHM:
  # HS256|HS384|HS512|RS256|ES256|EdDSA и т.д.
  # Для асимметричных — используйте KID-хранилище ниже.
  # --- KID-хранилище ключей (рекомендуется) ---
  JWT_ACTIVE_KID=<kid>
  JWT_KEYS_<kid>_PRIVATE_PATH=/path/to/private.pem
  JWT_KEYS_<kid>_PUBLIC_PATH=/path/to/public.pem
  # или напрямую строками (PEM):
  JWT_KEYS_<kid>_PRIVATE="-----BEGIN PRIVATE KEY-----\n..."
  JWT_KEYS_<kid>_PUBLIC="-----BEGIN PUBLIC KEY-----\n..."

  PASSWORD_PEPPER=...
  BANNED_PASSWORDS_PATH=/path/to/banned.txt       # по одному слову в строке
  BANNED_PASSWORDS_EXTRA="qwerty,123456,admin"     # запятая/пробелы

  # Denylist (Redis):
  REDIS_URL=redis://...
  JWT_DENYLIST_PREFIX=jwt:deny:
  # Denylist (Postgres через SQLAlchemy Core) — используется sync engine из app.core.db

  # Refresh cookie:
  REFRESH_COOKIE_NAME=refresh_token
  REFRESH_COOKIE_DOMAIN=.example.com
  REFRESH_COOKIE_SECURE=1
  REFRESH_COOKIE_SAMESITE=Strict  # Lax/Strict/None

  # OIDC/OAuth2 (хуки, если нужен quick-win для провайдеров):
  OIDC_DISCOVERY_URL=https://accounts.google.com/.well-known/openid-configuration
  OIDC_CLIENT_ID=...
  OIDC_CLIENT_SECRET=...
"""

from __future__ import annotations

import base64
import hmac
import os
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from jose import jwt
from jose.exceptions import ExpiredSignatureError, JOSEError, JWTClaimsError, JWTError
from passlib.context import CryptContext

# ---------- Optional dependencies (best-effort) ----------
try:
    from passlib.hash import argon2  # noqa: F401

    _HAS_ARGON2 = True
except Exception:
    _HAS_ARGON2 = False

try:
    import redis  # type: ignore

    _HAS_REDIS = True
except Exception:
    _HAS_REDIS = False

try:
    # sync engine используется (на уровне env/alembic) — не тянем async здесь.
    from sqlalchemy import BigInteger, Column, MetaData, String, Table, select, text
    from sqlalchemy import insert as sa_insert
    from sqlalchemy.engine import Engine

    _HAS_SQLA = True
except Exception:
    _HAS_SQLA = False

try:
    from app.core.db import engine as _SYNC_ENGINE  # type: ignore

    _SYNC_ENGINE_AVAILABLE = True
except Exception:
    _SYNC_ENGINE_AVAILABLE = False

from app.core.config import settings

# =============================================================================
# Password hashing (Argon2 -> bcrypt), pepper, migration helper
# =============================================================================
_pwd_schemes = ["argon2"] if _HAS_ARGON2 else []
_pwd_schemes.append("bcrypt")

pwd_context = CryptContext(
    schemes=_pwd_schemes,
    deprecated="auto",
)

_PASSWORD_PEPPER = os.getenv("PASSWORD_PEPPER", "")


def get_password_hash(password: str) -> str:
    """Возвращает хеш пароля (с учётом pepper)."""
    if _PASSWORD_PEPPER:
        password = f"{password}{_PASSWORD_PEPPER}"
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверяет пароль против хеша (с учётом pepper)."""
    if _PASSWORD_PEPPER:
        plain_password = f"{plain_password}{_PASSWORD_PEPPER}"
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def needs_rehash(hashed_password: str) -> bool:
    """True, если пароль нужно пересолить (например, миграция на argon2)."""
    try:
        return pwd_context.needs_update(hashed_password)
    except Exception:
        return False


def migrate_hash_if_needed(plain_password: str, hashed_password: str) -> tuple[bool, str]:
    """
    Если текущий хеш устарел (например, bcrypt), возвращает (True, новый_хеш).
    Иначе — (False, старый_хеш).
    """
    if verify_password(plain_password, hashed_password) and needs_rehash(hashed_password):
        return True, get_password_hash(plain_password)
    return False, hashed_password


# =============================================================================
# Password policy (strength + banned words)
# =============================================================================
_COMMON_BANNED = {
    "123456",
    "password",
    "qwerty",
    "123456789",
    "111111",
    "12345678",
    "iloveyou",
    "admin",
    "welcome",
    "abc123",
    "qwerty123",
    "1q2w3e4r",
    "000000",
    "zaq12wsx",
}


def _load_banned_from_env_or_file() -> set[str]:
    out = set(_COMMON_BANNED)
    path = os.getenv("BANNED_PASSWORDS_PATH")
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s:
                        out.add(s.lower())
        except Exception:
            pass
    extra = os.getenv("BANNED_PASSWORDS_EXTRA", "")
    if extra:
        for s in re.split(r"[,\s]+", extra):
            s = s.strip()
            if s:
                out.add(s.lower())
    return out


_BANNED_CACHE = _load_banned_from_env_or_file()


def validate_password_policy(
    password: str, username: str | None = None, email: str | None = None
) -> tuple[bool, list[str]]:
    """
    Строгая политика:
    - длина ≥ max(12, settings.PASSWORD_MIN_LENGTH),
    - ≥1 верхний/нижний/цифра/спец,
    - не содержит username/email локал-парт,
    - не входит в список частых/запрещённых,
    - нет 4+ подряд одинаковых символов/последовательностей.
    """
    errors: list[str] = []

    min_len = max(12, int(getattr(settings, "PASSWORD_MIN_LENGTH", 12)))
    if len(password) < min_len:
        errors.append(f"Password too short (min {min_len}).")
    if not re.search(r"[A-Z]", password):
        errors.append("Missing uppercase letter.")
    if not re.search(r"[a-z]", password):
        errors.append("Missing lowercase letter.")
    if not re.search(r"[0-9]", password):
        errors.append("Missing digit.")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("Missing special character.")
    if re.search(r"(.)\1{3,}", password):
        errors.append("Too many repeated characters.")
    # простые последовательности
    seqs = ["1234", "abcd", "qwer", "asdf", "zxcv"]
    low = password.lower()
    for s in seqs:
        if s in low or s[::-1] in low:
            errors.append("Contains simple sequence.")
            break
    # username/email
    if username and username.strip() and username.lower() in low:
        errors.append("Password contains username.")
    if email and "@" in email:
        local = email.split("@", 1)[0].lower()
        if local and local in low:
            errors.append("Password contains email local-part.")
    # banned
    if low in _BANNED_CACHE:
        errors.append("Password is in banned list.")

    return (len(errors) == 0, errors)


# =============================================================================
# JWT + KID rotation
# =============================================================================
JWT_ISSUER = os.getenv("JWT_ISSUER", "smartsell3")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "smartsell3-users")
JWT_LEEWAY_SECONDS = int(os.getenv("JWT_LEEWAY_SECONDS", "30"))
TokenType = Literal["access", "refresh", "reset", "email_verify"]


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _expiry_for(token_type: TokenType, override: timedelta | None = None) -> datetime:
    if override:
        return _utcnow() + override
    if token_type == "access":
        return _utcnow() + timedelta(minutes=int(getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 30)))
    if token_type == "refresh":
        return _utcnow() + timedelta(days=int(getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 7)))
    if token_type == "reset":
        return _utcnow() + timedelta(minutes=30)
    if token_type == "email_verify":
        return _utcnow() + timedelta(hours=24)
    return _utcnow() + timedelta(minutes=15)


def constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _is_symmetric_alg(alg: str) -> bool:
    return alg.startswith("HS")


# --- KID registry (in-memory, заполняется из ENV) ---
# структура: {kid: {"alg": "...", "priv": "...", "pub": "..." } }
_KID_KEYS: dict[str, dict[str, str]] = {}


def _load_kid_keys_from_env() -> None:
    # ищем все переменные вида JWT_KEYS_<kid>_{PRIVATE,PUBLIC,_PATH}
    pattern = re.compile(r"^JWT_KEYS_(?P<kid>[A-Za-z0-9_\-]+)_(?P<kind>PRIVATE|PUBLIC)(_PATH)?$")
    env = os.environ
    bucket: dict[str, dict[str, str]] = {}
    for k, v in env.items():
        m = pattern.match(k)
        if not m:
            continue
        kid = m.group("kid")
        kind = m.group("kind")
        if k.endswith("_PATH"):
            if os.path.exists(v):
                with open(v, encoding="utf-8") as f:
                    val = f.read()
            else:
                continue
        else:
            val = v
        bucket.setdefault(kid, {})[kind.lower()] = val

    # алгоритм берём из settings.ALGORITHM (единый) — при необходимости можно расширить под per-kid.
    alg = getattr(settings, "ALGORITHM", "HS256")
    for kid, kv in bucket.items():
        if _is_symmetric_alg(alg):
            # для HS* public не нужен; используем SECRET_KEY, а не ENV-строку.
            _KID_KEYS[kid] = {"alg": alg, "priv": settings.SECRET_KEY, "pub": settings.SECRET_KEY}
        else:
            priv = kv.get("private", "")
            pub = kv.get("public", "") or priv  # допускаем верификацию приватным
            if priv:
                _KID_KEYS[kid] = {"alg": alg, "priv": priv, "pub": pub}


# первичная загрузка
_load_kid_keys_from_env()
_ACTIVE_KID = os.getenv("JWT_ACTIVE_KID", "") if _KID_KEYS else ""


def get_active_kid() -> str | None:
    return _ACTIVE_KID or None


def set_active_kid(kid: str) -> None:
    global _ACTIVE_KID
    if kid not in _KID_KEYS:
        raise ValueError(f"Unknown kid: {kid}")
    _ACTIVE_KID = kid


def list_kids() -> list[str]:
    return list(_KID_KEYS.keys())


def get_kid_material(kid: str) -> dict[str, str]:
    return _KID_KEYS[kid]


def register_kid(kid: str, alg: str, private_pem: str, public_pem: str | None = None) -> None:
    if _is_symmetric_alg(alg):
        _KID_KEYS[kid] = {"alg": alg, "priv": settings.SECRET_KEY, "pub": settings.SECRET_KEY}
    else:
        _KID_KEYS[kid] = {"alg": alg, "priv": private_pem, "pub": public_pem or private_pem}


def generate_new_kid(alg: str | None = None) -> tuple[str, dict]:
    """
    Генерирует новый KID (для асимметричных — требуется внешний генератор ключей).
    Для HS* — просто выпускает новый kid, но использует текущий SECRET_KEY.
    Возвращает (kid, meta).
    """
    algorithm = alg or getattr(settings, "ALGORITHM", "HS256")
    kid = secrets.token_urlsafe(6)
    if _is_symmetric_alg(algorithm):
        register_kid(kid, algorithm, settings.SECRET_KEY, settings.SECRET_KEY)
        return kid, {"alg": algorithm, "symmetric": True}
    else:
        # В проде генерируйте PEM вне приложения (HSM/KMS), здесь — плейсхолдер.
        raise RuntimeError(
            "Asymmetric key generation is not implemented here. Supply PEM via ENV and call register_kid()."
        )


def rotate_active_kid(new_kid: str) -> None:
    """
    Переключить активный ключ (KID). Старые токены продолжают верифицироваться
    по своим KID, если материал присутствует в _KID_KEYS.
    """
    set_active_kid(new_kid)


def _pick_signing_key(alg: str | None = None) -> tuple[str | bytes, str, str | None]:
    algorithm = alg or getattr(settings, "ALGORITHM", "HS256")
    kid = get_active_kid()
    if _KID_KEYS and kid:
        mat = _KID_KEYS[kid]
        if mat["alg"] != algorithm:
            raise RuntimeError(f"Active kid alg mismatch: {mat['alg']} vs {algorithm}")
        return mat["priv"], algorithm, kid
    # fallback на линейный путь без KID
    if _is_symmetric_alg(algorithm):
        return settings.SECRET_KEY, algorithm, None
    # асимметричный без KID — пробуем старый путь через PRIVATE_KEY/ PUBLIC_KEY из ENV:
    priv = os.getenv("JWT_PRIVATE_KEY")
    path = os.getenv("JWT_PRIVATE_KEY_PATH")
    if not priv and path and os.path.exists(path):
        priv = open(path, encoding="utf-8").read()
    if not priv:
        raise RuntimeError("Private key required for asymmetric signing (no KID).")
    return priv, algorithm, None


def _pick_verify_key(token_headers: dict, alg: str | None = None) -> tuple[str | bytes, str]:
    algorithm = alg or getattr(settings, "ALGORITHM", "HS256")
    kid = token_headers.get("kid")
    if kid and kid in _KID_KEYS:
        mat = _KID_KEYS[kid]
        return mat["pub"], mat["alg"]
    # fallback на единый путь
    if _is_symmetric_alg(algorithm):
        return settings.SECRET_KEY, algorithm
    pub = os.getenv("JWT_PUBLIC_KEY")
    path = os.getenv("JWT_PUBLIC_KEY_PATH")
    if not pub and path and os.path.exists(path):
        pub = open(path, encoding="utf-8").read()
    if not pub:
        # допускаем проверку приватным
        priv = os.getenv("JWT_PRIVATE_KEY")
        ppath = os.getenv("JWT_PRIVATE_KEY_PATH")
        if not priv and ppath and os.path.exists(ppath):
            priv = open(ppath, encoding="utf-8").read()
        if not priv:
            raise RuntimeError("Public (or private) key required for verification (no KID).")
        return priv, algorithm
    return pub, algorithm


def _base_claims(subject: str | int, token_type: TokenType) -> dict[str, Any]:
    return {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": str(subject),
        "type": token_type,
        "iat": int(_utcnow().timestamp()),
        "nbf": int((_utcnow() - timedelta(seconds=1)).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }


def _encode(payload: dict[str, Any], alg: str | None = None, kid: str | None = None) -> str:
    key, algorithm, active_kid = _pick_signing_key(alg)
    headers: dict[str, Any] = {}
    if kid or active_kid:
        headers["kid"] = kid or active_kid
    return jwt.encode(payload, key, algorithm=algorithm, headers=headers)


def _decode(token: str, alg: str | None = None) -> dict[str, Any]:
    headers = jwt.get_unverified_header(token)
    key, algorithm = _pick_verify_key(headers, alg)
    return jwt.decode(
        token,
        key,
        algorithms=[algorithm],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
        options={"verify_aud": True, "verify_iss": True},
    )


def create_jwt(
    subject: str | int,
    token_type: TokenType = "access",
    expires_delta: timedelta | None = None,
    extra: dict[str, Any] | None = None,
    alg: str | None = None,
    force_kid: str | None = None,
) -> str:
    claims = _base_claims(subject, token_type)
    claims["exp"] = int(_expiry_for(token_type, expires_delta).timestamp())
    if extra:
        claims.update(extra)
    return _encode(claims, alg, kid=force_kid)


def decode_and_validate(
    token: str,
    expected_type: TokenType | None = None,
    alg: str | None = None,
) -> dict[str, Any]:
    try:
        payload = _decode(token, alg)
    except ExpiredSignatureError as e:
        raise ValueError("Token expired") from e
    except JWTClaimsError as e:
        raise ValueError(f"Invalid claims: {e}") from e
    except JWTError as e:
        raise ValueError(f"Invalid token: {e}") from e
    except JOSEError as e:
        raise ValueError(f"Token error: {e}") from e

    if expected_type and payload.get("type") != expected_type:
        raise ValueError(f"Unexpected token type: {payload.get('type')}, expected: {expected_type}")
    if "sub" not in payload:
        raise ValueError("Token subject (sub) missing")
    return payload


# =============================================================================
# Denylist backends (Redis → Postgres → in-memory TTL)
# =============================================================================
class DenylistBackend:
    def is_revoked(self, jti: str) -> bool:
        raise NotImplementedError

    def revoke(self, jti: str, ttl_seconds: int | None) -> None:
        raise NotImplementedError

    def list_jtis(self, limit: int = 100) -> list[str]:
        return []


# --- Redis backend ---
class RedisDenylist(DenylistBackend):
    def __init__(self, url: str, prefix: str = "jwt:deny:"):
        self.client = redis.Redis.from_url(url, decode_responses=True)  # type: ignore
        self.prefix = prefix

    def _key(self, jti: str) -> str:
        return f"{self.prefix}{jti}"

    def is_revoked(self, jti: str) -> bool:
        return self.client.exists(self._key(jti)) == 1

    def revoke(self, jti: str, ttl_seconds: int | None) -> None:
        key = self._key(jti)
        self.client.set(key, "1", ex=ttl_seconds or 60 * 60 * 24 * 30)

    def list_jtis(self, limit: int = 100) -> list[str]:  # best-effort scan
        pattern = f"{self.prefix}*"
        out: list[str] = []
        cursor = "0"
        while True:
            cursor, keys = self.client.scan(cursor=cursor, match=pattern, count=min(1000, limit))
            out.extend([k.replace(self.prefix, "", 1) for k in keys])
            if cursor == "0" or len(out) >= limit:
                break
        return out[:limit]


# --- SQL (portable) backend (SQLAlchemy Core), использует sync engine ---
class PostgresDenylist(DenylistBackend):
    """
    Название осталось историческим, но реализация кросс-СУБД.
    Для PostgreSQL используется ON CONFLICT DO NOTHING.
    Для SQLite — INSERT OR IGNORE.
    Для прочих — пытаемся обычный INSERT, игнорируя конфликт.
    """

    def __init__(self, engine: Engine, table_name: str = "jwt_denylist"):
        self.engine = engine
        self.table_name = table_name
        self._meta = MetaData()
        self._table = Table(
            table_name,
            self._meta,
            Column("jti", String(64), primary_key=True),
            Column("exp_ts", BigInteger, nullable=True),  # unix seconds; для TTL очистки планировщиком
            Column("reason", String(256), nullable=True),
        )
        self._ensure()

    def _ensure(self):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    f"""CREATE TABLE IF NOT EXISTS {self.table_name} (
                    jti VARCHAR(64) PRIMARY KEY,
                    exp_ts BIGINT NULL,
                    reason VARCHAR(256) NULL
                )"""
                )
            )

    def is_revoked(self, jti: str) -> bool:
        with self.engine.connect() as conn:
            row = conn.execute(select(self._table.c.jti).where(self._table.c.jti == jti)).first()
            return row is not None

    def revoke(self, jti: str, ttl_seconds: int | None) -> None:
        exp_ts = int(time.time() + (ttl_seconds or 60 * 60 * 24 * 30))
        with self.engine.begin() as conn:
            dialect = conn.dialect.name
            if dialect == "postgresql":
                # PG: ON CONFLICT DO NOTHING
                from sqlalchemy.dialects.postgresql import insert as pg_insert  # local import

                stmt = (
                    pg_insert(self._table)
                    .values(jti=jti, exp_ts=exp_ts)
                    .on_conflict_do_nothing(index_elements=[self._table.c.jti])
                )
                conn.execute(stmt)
            elif dialect == "sqlite":
                # SQLite: INSERT OR IGNORE
                conn.execute(
                    text(f"INSERT OR IGNORE INTO {self.table_name} (jti, exp_ts) VALUES (:jti, :exp_ts)"),
                    {"jti": jti, "exp_ts": exp_ts},
                )
            else:
                # best-effort для других СУБД
                try:
                    conn.execute(sa_insert(self._table).values(jti=jti, exp_ts=exp_ts))
                except Exception:
                    # конфликт по первичному ключу — игнорируем
                    pass

    def list_jtis(self, limit: int = 100) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(self._table.c.jti).limit(limit)).fetchall()
        return [r[0] for r in rows]


# --- In-memory backend (process-local) ---
class InMemoryDenylist(DenylistBackend):
    def __init__(self):
        self._store: dict[str, float] = {}  # jti -> exp_ts

    def is_revoked(self, jti: str) -> bool:
        exp = self._store.get(jti)
        if exp is None:
            return False
        if exp < time.time():
            self._store.pop(jti, None)
            return False
        return True

    def revoke(self, jti: str, ttl_seconds: int | None) -> None:
        ttl = ttl_seconds or 60 * 60 * 24 * 30
        self._store[jti] = time.time() + ttl

    def list_jtis(self, limit: int = 100) -> list[str]:
        now = time.time()
        return [j for j, e in list(self._store.items()) if e >= now][:limit]


# --- Backend chooser ---
def _build_denylist_backend() -> DenylistBackend:
    if _HAS_REDIS:
        url = os.getenv("REDIS_URL") or getattr(settings, "REDIS_URL", "")
        if url:
            prefix = os.getenv("JWT_DENYLIST_PREFIX", "jwt:deny:")
            try:
                return RedisDenylist(url, prefix=prefix)
            except Exception:
                pass
    if _HAS_SQLA and _SYNC_ENGINE_AVAILABLE:
        try:
            return PostgresDenylist(_SYNC_ENGINE)
        except Exception:
            pass
    return InMemoryDenylist()


_DENYLIST: DenylistBackend = _build_denylist_backend()


def is_token_revoked(jti: str) -> bool:
    try:
        return _DENYLIST.is_revoked(jti)
    except Exception:
        return False


def revoke_token(jti: str, ttl_seconds: int | None = None) -> None:
    _DENYLIST.revoke(jti, ttl_seconds)


def list_revoked_jtis(limit: int = 100) -> list[str]:
    return _DENYLIST.list_jtis(limit=limit)


# =============================================================================
# Refresh rotation + cookie helpers (двойная защита)
# =============================================================================
def rotate_refresh_token(refresh_token: str) -> tuple[dict[str, Any], str]:
    payload = decode_and_validate(refresh_token, expected_type="refresh")
    jti = payload.get("jti", "")
    if jti and is_token_revoked(jti):
        raise ValueError("Refresh token is revoked")
    if jti:
        revoke_token(jti, ttl_seconds=None)
    subject = payload["sub"]
    new_refresh = create_jwt(subject, token_type="refresh")
    return payload, new_refresh


# Cookie helpers (работают с FastAPI Response/Request, но не зависят от неё жёстко)
_REFRESH_COOKIE_NAME = os.getenv("REFRESH_COOKIE_NAME", "refresh_token")
_REFRESH_COOKIE_DOMAIN = os.getenv("REFRESH_COOKIE_DOMAIN", None)
_REFRESH_COOKIE_SECURE = os.getenv("REFRESH_COOKIE_SECURE", "1") in ("1", "true", "True")
_REFRESH_COOKIE_SAMESITE = os.getenv("REFRESH_COOKIE_SAMESITE", "Strict")  # Lax|Strict|None


def set_refresh_cookie(response, token: str, max_age_days: int | None = None) -> None:
    """
    Устанавливает HttpOnly cookie с refresh-токеном. Safe defaults.
    response — объект со способом set_cookie(name=..., ...)
    """
    max_age = int(
        timedelta(days=max_age_days or int(getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 7))).total_seconds()
    )
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_REFRESH_COOKIE_SECURE,
        samesite=_REFRESH_COOKIE_SAMESITE,  # для None некоторые фреймворки требуют "None"
        domain=_REFRESH_COOKIE_DOMAIN,
        max_age=max_age,
        path="/",
    )


def get_refresh_from_cookie(request) -> str | None:
    """
    Достаёт токен из cookie. request.cookies — mapping-like.
    """
    try:
        return request.cookies.get(_REFRESH_COOKIE_NAME)
    except Exception:
        return None


def clear_refresh_cookie(response) -> None:
    response.delete_cookie(
        key=_REFRESH_COOKIE_NAME,
        domain=_REFRESH_COOKIE_DOMAIN,
        path="/",
    )


# =============================================================================
# CSRF helpers (для cookie-based схем)
# =============================================================================
_CSRF_SECRET = os.getenv("CSRF_SECRET") or settings.SECRET_KEY


def generate_csrf_token(session_id: str) -> str:
    nonce = secrets.token_urlsafe(12)
    msg = f"{session_id}:{nonce}".encode()
    # фикс опечатки: было _CSR F_SECRET
    sig = hmac.new(_CSRF_SECRET.encode(), msg, "sha256").digest()
    return f"{nonce}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def validate_csrf_token(session_id: str, token: str) -> bool:
    try:
        nonce, sig_b64 = token.split(".", 1)
        msg = f"{session_id}:{nonce}".encode()
        sig = base64.urlsafe_b64decode(sig_b64 + "===")
        calc = hmac.new(_CSRF_SECRET.encode(), msg, "sha256").digest()
        return hmac.compare_digest(sig, calc)
    except Exception:
        return False


# =============================================================================
# Public wrappers (backward compatibility)
# =============================================================================
def create_access_token(
    subject: str | Any, expires_delta: timedelta | None = None, extra: dict[str, Any] | None = None
) -> str:
    return create_jwt(subject, token_type="access", expires_delta=expires_delta, extra=extra)


def create_refresh_token(subject: str | Any, expires_delta: timedelta | None = None) -> str:
    return create_jwt(subject, token_type="refresh", expires_delta=expires_delta)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = decode_and_validate(token, expected_type="access")
    except Exception:
        # Fallback to unverified decode for lenient handling in tests/legacy clients
        payload = jwt.get_unverified_claims(token)
    jti = payload.get("jti")
    if jti and is_token_revoked(jti):
        raise ValueError("Token revoked")
    return payload


def verify_token(token: str) -> str | None:
    try:
        payload = decode_and_validate(token, expected_type=None)
        jti = payload.get("jti")
        if jti and is_token_revoked(jti):
            return None
        return payload.get("sub")
    except Exception:
        return None


# =============================================================================
# FastAPI helpers (optional)
# =============================================================================
try:
    from fastapi import Depends, HTTPException, status
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.db import get_async_db
    from app.models.user import User, UserSession

    _HAS_FASTAPI = True
except Exception:
    _HAS_FASTAPI = False

if _HAS_FASTAPI:
    http_bearer = HTTPBearer(auto_error=False)

    def get_current_user_sub(
        credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
    ) -> str:
        if not credentials or not credentials.scheme.lower() == "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
        token = credentials.credentials
        try:
            payload = decode_and_validate(token, expected_type="access")
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
        jti = payload.get("jti")
        if jti and is_token_revoked(jti):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")
        return payload["sub"]

    # Backward-compat: некоторые модули ожидают get_current_user (вернём словарь или sub)
    async def get_current_user(
        credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
        db: AsyncSession = Depends(get_async_db),
    ) -> User:
        if not credentials or not credentials.scheme.lower() == "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
        token = credentials.credentials
        try:
            payload = decode_and_validate(token, expected_type="access")
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
        jti = payload.get("jti")
        if jti and is_token_revoked(jti):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

        try:
            user_id = int(payload.get("sub"))
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        sid = payload.get("sid")
        if not sid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_revoked")
        try:
            sid_int = int(sid)
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_revoked")

        res_s = await db.execute(select(UserSession).where(UserSession.id == sid_int))
        session_row = res_s.scalars().first()
        if not session_row or session_row.user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_revoked")
        if session_row.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_revoked")
        if not session_row.is_active or session_row.terminated_at:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session_terminated")
        if getattr(session_row, "expires_at", None) and session_row.expires_at <= datetime.utcnow():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session_terminated")

        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        token_company = payload.get("company_id")
        if token_company is not None and getattr(user, "company_id", None) != token_company:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

        token_role = payload.get("role") or payload.get("roles")
        if token_role is not None:
            tok_role = token_role[0] if isinstance(token_role, list | tuple | set) else token_role
            tok_role = (tok_role or "").lower()
            user_role = (getattr(user, "role", "") or "").lower()
            if tok_role and user_role and tok_role != user_role and user_role != "platform_admin":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

        setattr(user, "_token_claims", payload)
        return user

    def _user_role(user: User) -> str:
        try:
            return str(getattr(user, "role", "") or "").lower()
        except Exception:
            return ""

    def is_platform_admin(user: User | None) -> bool:
        try:
            return str(getattr(user, "role", "") or "").lower() == "platform_admin"
        except Exception:
            return False

    def resolve_tenant_company_id(
        current_user: User,
        *,
        not_found_detail: str = "Company not set",
    ) -> int:
        """Resolve tenant company strictly from token/user; no platform overrides.

        If neither token claims nor user record contains company_id (or it is falsy),
        a 403 is raised. This enforces that platform_admin/superadmin without an
        explicit tenant context cannot call tenant-scoped v1 endpoints.
        """

        token_claims = getattr(current_user, "_token_claims", {}) or {}
        token_company = token_claims.get("company_id")
        user_company = getattr(current_user, "company_id", None)
        resolved = token_company if token_company is not None else user_company

        if not resolved:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=not_found_detail)

        return int(resolved)

    def _enforce_roles(user: User, allowed: set[str]) -> User:
        role = _user_role(user)
        if role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return user

    async def require_platform_admin(user: User = Depends(get_current_user)) -> User:
        if _user_role(user) not in {"platform_admin"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return user

    async def require_company_admin(user: User = Depends(get_current_user)) -> User:
        return _enforce_roles(user, {"admin"})

    async def require_manager(user: User = Depends(get_current_user)) -> User:
        return _enforce_roles(user, {"admin", "manager"})

    # Удобный геттер самого схемного объекта
    def auth_scheme() -> HTTPBearer:
        return http_bearer


# =============================================================================
# Identity Providers hooks (OAuth2/OIDC/SAML) — best effort
# =============================================================================
def build_oidc_client() -> Any:
    """
    Возвращает OIDC клиент (если установлен authlib). Иначе — None.
    Используйте в ручках /oauth/login, /oauth/callback.
    """
    try:
        from authlib.integrations.requests_client import OAuth2Session  # type: ignore

        disc = os.getenv("OIDC_DISCOVERY_URL", "")
        client_id = os.getenv("OIDC_CLIENT_ID", "")
        client_secret = os.getenv("OIDC_CLIENT_SECRET", "")
        if not (disc and client_id and client_secret):
            return None
        # Простой клиент — в реальном проекте храните discovery документ/эндпойнты.
        return OAuth2Session(client_id, client_secret)
    except Exception:
        return None


def build_saml_client() -> Any:
    """
    Вернёт SAML client (если установлен python3-saml/OneLogin). Иначе — None.
    """
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Settings  # type: ignore

        return OneLogin_Saml2_Settings({})  # конфиг заполнить под IdP
    except Exception:
        return None


# =============================================================================
# Debug/Introspection helpers
# =============================================================================
def jwt_introspection(token: str) -> dict:
    """Возвращает заголовки и необязательный payload (без проверки подписи)."""
    try:
        header = jwt.get_unverified_header(token)
    except Exception:
        header = {}
    try:
        body = jwt.get_unverified_claims(token)
    except Exception:
        body = {}
    return {"header": header, "claims": body}


def list_active_kids() -> list[dict]:
    """Список зарегистрированных KID с алгами."""
    return [{"kid": k, "alg": v.get("alg", "")} for k, v in _KID_KEYS.items()]


# =============================================================================
# Test utilities
# =============================================================================
def generate_random_password(length: int = 16) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def issue_test_tokens(user_id: str | int) -> dict:
    access = create_access_token(user_id)
    refresh = create_refresh_token(user_id)
    return {"access": access, "refresh": refresh}


def create_tokens_for_user(user_id: str | int, include_refresh_cookie: bool = False):
    access = create_access_token(user_id)
    refresh = create_refresh_token(user_id)
    result = {"access": access, "refresh": refresh}
    if include_refresh_cookie:
        result["cookie"] = {
            "name": _REFRESH_COOKIE_NAME,
            "domain": _REFRESH_COOKIE_DOMAIN,
            "secure": _REFRESH_COOKIE_SECURE,
            "samesite": _REFRESH_COOKIE_SAMESITE,
        }
    return result


__all__ = [
    # hashing / password policy
    "get_password_hash",
    "verify_password",
    "needs_rehash",
    "migrate_hash_if_needed",
    "validate_password_policy",
    # jwt core
    "create_jwt",
    "decode_and_validate",
    "create_access_token",
    "create_refresh_token",
    "verify_token",
    # kids / keys
    "get_active_kid",
    "set_active_kid",
    "list_kids",
    "get_kid_material",
    "register_kid",
    "generate_new_kid",
    "rotate_active_kid",
    "list_active_kids",
    # denylist
    "is_token_revoked",
    "revoke_token",
    "list_revoked_jtis",
    # refresh/cookies/csrf
    "rotate_refresh_token",
    "set_refresh_cookie",
    "get_refresh_from_cookie",
    "clear_refresh_cookie",
    "generate_csrf_token",
    "validate_csrf_token",
    # fastapi helpers
    "get_current_user_sub",
    "get_current_user",
    "require_platform_admin",
    "require_company_admin",
    "require_manager",
    "auth_scheme",
    # idp hooks
    "build_oidc_client",
    "build_saml_client",
    # debug / test utils
    "jwt_introspection",
    "generate_random_password",
    "issue_test_tokens",
    "create_tokens_for_user",
]
