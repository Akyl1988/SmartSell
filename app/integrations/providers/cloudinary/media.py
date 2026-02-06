from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.ports.media import MediaProvider

log = get_logger(__name__)


class CloudinaryMediaProvider(MediaProvider):
    def __init__(
        self,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        version: int | None = None,
    ):
        self.name = (name or "cloudinary").strip() or "cloudinary"
        self.config = config or {}
        self.version = int(version or 0)

    def _merged_config(self) -> dict[str, Any]:
        base = settings.cloudinary_settings or {}
        merged = {
            "cloud_name": self.config.get("cloud_name") or base.get("cloud_name"),
            "api_key": self.config.get("api_key") or base.get("api_key"),
            "api_secret": self.config.get("api_secret") or base.get("api_secret"),
        }
        return merged

    def _has_required_config(self) -> bool:
        cfg = self._merged_config()
        return all(cfg.get(k) for k in ("cloud_name", "api_key", "api_secret"))

    def _ensure_ready(self) -> bool:
        if self._has_required_config():
            return True
        if settings.is_production:
            raise ProviderNotConfiguredError("cloudinary_not_configured")
        log.warning("Cloudinary config missing; using noop media provider")
        return False

    def _get_cloudinary(self):
        try:
            import cloudinary  # type: ignore

            return cloudinary
        except Exception:
            if settings.is_production:
                raise ProviderNotConfiguredError("cloudinary_sdk_missing")
            log.warning("Cloudinary SDK missing; using noop media provider")
            return None

    def _configure(self, cloudinary_mod) -> None:
        cfg = self._merged_config()
        cloudinary_mod.config(
            cloud_name=cfg.get("cloud_name"),
            api_key=cfg.get("api_key"),
            api_secret=cfg.get("api_secret"),
            secure=True,
        )

    async def upload(
        self,
        file: bytes | str,
        *,
        public_id: str | None = None,
        folder: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._ensure_ready():
            return {"status": "noop", "provider": self.name, "version": self.version}

        cloudinary_mod = self._get_cloudinary()
        if not cloudinary_mod:
            return {"status": "noop", "provider": self.name, "version": self.version}

        self._configure(cloudinary_mod)
        options: dict[str, Any] = {
            "resource_type": "auto",
        }
        if public_id:
            options["public_id"] = public_id
        if folder:
            options["folder"] = folder
        if metadata:
            options["context"] = metadata

        result = cloudinary_mod.uploader.upload(file, **options)
        return {
            "status": "ok",
            "provider": self.name,
            "version": self.version,
            "public_id": result.get("public_id"),
            "url": result.get("secure_url") or result.get("url"),
            "resource_type": result.get("resource_type"),
        }

    async def remove(self, public_id: str) -> dict[str, Any]:
        if not self._ensure_ready():
            return {"status": "noop", "provider": self.name, "version": self.version}

        cloudinary_mod = self._get_cloudinary()
        if not cloudinary_mod:
            return {"status": "noop", "provider": self.name, "version": self.version}

        self._configure(cloudinary_mod)
        result = cloudinary_mod.uploader.destroy(public_id, invalidate=True)
        return {
            "status": "ok",
            "provider": self.name,
            "version": self.version,
            "public_id": public_id,
            "result": result.get("result"),
        }


__all__ = ["CloudinaryMediaProvider"]
