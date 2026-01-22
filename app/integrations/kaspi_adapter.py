from __future__ import annotations

import json
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

    def _run_json(self, ps_command: str) -> Any:
        """
        Выполняет PowerShell команду и возвращает JSON->Python.
        PowerShell функции уже возвращают JSON, не нужно добавлять ConvertTo-Json.
        """
        # Запускаем pwsh -NoProfile -Command "<cmd>"
        completed = subprocess.run(
            [self.pwsh, "-NoProfile", "-Command", ps_command],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            stderr = self._strip_ansi(completed.stderr.strip())
            raise KaspiAdapterError(f"Kaspi.ps1 error: {stderr}")

        stdout = completed.stdout.strip()
        if not stdout:
            return None

        # Strip ANSI sequences
        stdout = self._strip_ansi(stdout)

        # Handle multi-line output: take the last non-empty line (should be JSON)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return None

        json_line = lines[-1]

        try:
            parsed = json.loads(json_line)
            # Гарантируем, что наружу возвращается объект/массив, а не JSON-строка
            if isinstance(parsed, dict | list):
                return parsed
            return {"raw": parsed}
        except json.JSONDecodeError:
            # Если пришёл не JSON — завернём как текст
            return {"raw": json_line}

    def health(self, store: str) -> Any:
        cmd = f". '{self.script_path}'; ks:health -Store {shlex.quote(store)}"
        return self._run_json(cmd)

    def orders(self, store: str, state: Optional[str] = None) -> Any:
        state_part = f"-State {shlex.quote(state)}" if state else ""
        cmd = f". '{self.script_path}'; ks:orders -Store {shlex.quote(store)} {state_part}"
        return self._run_json(cmd)

    def publish_feed(self, store: str, offers_json_path: str) -> Any:
        cmd = (
            f". '{self.script_path}'; "
            f"ks:publishFeed -Store {shlex.quote(store)} -OffersJsonPath {shlex.quote(offers_json_path)}"
        )
        return self._run_json(cmd)

    def feed_upload(self, store: str, xml_path: str, comment: Optional[str] = None) -> Any:
        comment_part = f"-Comment {shlex.quote(comment)}" if comment else ""
        cmd = (
            f". '{self.script_path}'; "
            f"ks:feedUpload -Store {shlex.quote(store)} -XmlPath {shlex.quote(xml_path)} {comment_part}"
        )
        return self._run_json(cmd)

    def feed_import_status(self, store: str, import_id: Optional[str] = None) -> Any:
        import_part = f"-ImportId {shlex.quote(import_id)}" if import_id else ""
        cmd = f". '{self.script_path}'; ks:feedStatus -Store {shlex.quote(store)} {import_part}"
        return self._run_json(cmd)

    def import_status(self, store: str, import_id: Optional[str] = None) -> Any:
        import_part = f"-ImportId {shlex.quote(import_id)}" if import_id else ""
        cmd = f". '{self.script_path}'; ks:import -Store {shlex.quote(store)} {import_part} -StatusOnly"
        return self._run_json(cmd)
