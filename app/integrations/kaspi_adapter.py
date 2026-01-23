from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from typing import Any, Optional

from app.core.config import settings


class KaspiAdapterError(RuntimeError):
    pass


class KaspiAdapter:
    """
    Обёртка над PowerShell-скриптом Kaspi.ps1.
    Все команды возвращают Python-объекты, распарсенные из JSON.
    """

    def __init__(self, pwsh: Optional[str] = None, script_path: Optional[str] = None):
        self.pwsh = pwsh or settings.KASPI_POWERSHELL
        self.script_path = script_path or settings.KASPI_SCRIPT_PATH

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI escape sequences from text."""
        ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        return ansi_escape.sub("", text)

    def _run_json(self, ps_command: str, extra_env: dict[str, str] | None = None) -> Any:
        """
        Выполняет PowerShell команду и возвращает JSON->Python.
        PowerShell функции уже возвращают JSON, не нужно добавлять ConvertTo-Json.
        """
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)

        completed = subprocess.run(
            [self.pwsh, "-NoProfile", "-Command", ps_command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )

        stdout_raw = completed.stdout or ""
        stderr_raw = completed.stderr or ""
        stdout = self._strip_ansi(stdout_raw).strip()
        stderr = self._strip_ansi(stderr_raw).strip()

        def _preview(value: str, limit: int = 500) -> str:
            if not value:
                return ""
            text_value = value.strip()
            if len(text_value) <= limit:
                return text_value
            return f"{text_value[:limit]}..."

        if completed.returncode != 0:
            raise KaspiAdapterError(
                "Kaspi.ps1 error: "
                f"exit_code={completed.returncode} "
                f"stderr={_preview(stderr)} "
                f"stdout={_preview(stdout)}"
            )

        if not stdout:
            raise KaspiAdapterError(
                "Kaspi.ps1 error: empty stdout " f"exit_code={completed.returncode} " f"stderr={_preview(stderr)}"
            )

        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise KaspiAdapterError(
                "Kaspi.ps1 error: empty stdout lines " f"exit_code={completed.returncode} " f"stderr={_preview(stderr)}"
            )

        json_line = lines[-1]

        try:
            parsed = json.loads(json_line)
        except json.JSONDecodeError as exc:
            raise KaspiAdapterError(
                "Kaspi.ps1 error: invalid JSON "
                f"exit_code={completed.returncode} "
                f"stderr={_preview(stderr)} "
                f"stdout={_preview(json_line)}"
            ) from exc

        if isinstance(parsed, dict | list):
            return parsed
        return {"raw": parsed}

    def health(self, store: str, *, extra_env: dict[str, str] | None = None) -> Any:
        cmd = f". '{self.script_path}'; ks:health -Store {shlex.quote(store)}"
        return self._run_json(cmd, extra_env=extra_env)

    def orders(self, store: str, state: Optional[str] = None, *, extra_env: dict[str, str] | None = None) -> Any:
        state_part = f"-State {shlex.quote(state)}" if state else ""
        cmd = f". '{self.script_path}'; ks:orders -Store {shlex.quote(store)} {state_part}"
        return self._run_json(cmd, extra_env=extra_env)

    def publish_feed(self, store: str, offers_json_path: str, *, extra_env: dict[str, str] | None = None) -> Any:
        cmd = (
            f". '{self.script_path}'; "
            f"ks:publishFeed -Store {shlex.quote(store)} -OffersJsonPath {shlex.quote(offers_json_path)}"
        )
        return self._run_json(cmd, extra_env=extra_env)

    def feed_upload(
        self,
        store: str,
        xml_path: str,
        comment: Optional[str] = None,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> Any:
        comment_part = f"-Comment {shlex.quote(comment)}" if comment else ""
        cmd = (
            f". '{self.script_path}'; "
            f"ks:feedUpload -Store {shlex.quote(store)} -XmlPath {shlex.quote(xml_path)} {comment_part}"
        )
        return self._run_json(cmd, extra_env=extra_env)

    def feed_import_status(
        self,
        store: str,
        import_id: Optional[str] = None,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> Any:
        import_part = f"-ImportId {shlex.quote(import_id)}" if import_id else ""
        cmd = f". '{self.script_path}'; ks:feedStatus -Store {shlex.quote(store)} {import_part}"
        return self._run_json(cmd, extra_env=extra_env)

    def import_status(
        self, store: str, import_id: Optional[str] = None, *, extra_env: dict[str, str] | None = None
    ) -> Any:
        import_part = f"-ImportId {shlex.quote(import_id)}" if import_id else ""
        cmd = f". '{self.script_path}'; ks:import -Store {shlex.quote(store)} {import_part} -StatusOnly"
        return self._run_json(cmd, extra_env=extra_env)
