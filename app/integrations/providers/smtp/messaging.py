from __future__ import annotations

import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.ports.messaging import MessagingProvider
from app.utils.pii import mask_email

log = get_logger(__name__)


class SmtpMessagingProvider(MessagingProvider):
    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        name: str | None = None,
        version: int | None = None,
    ) -> None:
        cfg = config or {}
        self.name = (name or "smtp").strip() or "smtp"
        self.version = int(version or 0)

        self.host = (cfg.get("host") or settings.SMTP_HOST or "").strip()
        self.port = int(cfg.get("port") or settings.SMTP_PORT or 587)
        self.user = (cfg.get("user") or settings.SMTP_USER or "").strip()
        self.password = (cfg.get("password") or settings.SMTP_PASSWORD or "").strip()
        self.from_email = (cfg.get("from_email") or settings.SMTP_FROM_EMAIL or "").strip()
        self.use_tls = bool(cfg.get("tls") if "tls" in cfg else settings.SMTP_TLS)
        self.use_ssl = bool(cfg.get("ssl") if "ssl" in cfg else settings.SMTP_SSL)

        if self.use_tls and self.use_ssl:
            log.warning("SMTP TLS and SSL are both enabled; prefer one", extra={"provider": self.name})

        if settings.is_production and not self._is_configured():
            raise ProviderNotConfiguredError("email_provider_not_configured")

    def _is_configured(self) -> bool:
        return bool(self.host and self.port and self.user and self.password and self.from_email)

    def _payload(self, status: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {"status": status, "provider": self.name, "version": self.version}
        if extra:
            data.update(extra)
        return data

    def _build_message(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html_body: str | None,
        attachments: list[str] | None,
        cc: list[str] | None,
    ) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["From"] = self.from_email
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = ", ".join(cc)

        msg.attach(MIMEText(text, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        if attachments:
            for file_path in attachments:
                if not os.path.exists(file_path):
                    continue
                with open(file_path, "rb") as f:
                    attachment = MIMEApplication(f.read())
                    attachment.add_header(
                        "Content-Disposition",
                        "attachment",
                        filename=os.path.basename(file_path),
                    )
                    msg.attach(attachment)
        return msg

    async def send_message(
        self,
        to: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if settings.is_production and not self._is_configured():
            raise ProviderNotConfiguredError("email_provider_not_configured")

        meta = metadata or {}
        subject = str(meta.get("subject") or "Notification")
        html_body = meta.get("html_body")
        attachments = meta.get("attachments")
        cc = meta.get("cc")
        bcc = meta.get("bcc")
        from_email = str(meta.get("from_email") or self.from_email)

        if from_email and from_email != self.from_email:
            self.from_email = from_email

        msg = self._build_message(
            to=to,
            subject=subject,
            text=text,
            html_body=html_body,
            attachments=attachments if isinstance(attachments, list) else None,
            cc=cc if isinstance(cc, list) else None,
        )

        recipients = [to]
        if isinstance(cc, list):
            recipients.extend(cc)
        if isinstance(bcc, list):
            recipients.extend(bcc)

        try:
            if self.use_ssl:
                with smtplib.SMTP_SSL(self.host, self.port) as server:
                    server.login(self.user, self.password)
                    server.send_message(msg, to_addrs=recipients)
            else:
                with smtplib.SMTP(self.host, self.port) as server:
                    if self.use_tls:
                        server.starttls()
                    server.login(self.user, self.password)
                    server.send_message(msg, to_addrs=recipients)
            log.info(
                "smtp_email_sent",
                extra={"to": mask_email(to), "provider": self.name, "version": self.version},
            )
            return self._payload("ok", {"provider_status": "sent"})
        except Exception as exc:  # pragma: no cover - network guard
            log.warning(
                "smtp_email_send_failed",
                extra={"to": mask_email(to), "provider": self.name, "error": str(exc)},
            )
            return self._payload("error", {"provider_error": str(exc)})


__all__ = ["SmtpMessagingProvider"]
