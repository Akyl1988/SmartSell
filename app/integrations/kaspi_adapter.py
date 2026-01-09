from __future__ import annotations

import json
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

    def _run_json(self, ps_command: str) -> Any:
        """
        Выполняет PowerShell команду и возвращает JSON->Python.
        ps_command уже должен включать dot-source скрипта и ConvertTo-Json.
        """
        # Запускаем pwsh -NoProfile -Command "<cmd>"
        completed = subprocess.run(
            [self.pwsh, "-NoProfile", "-Command", ps_command],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            raise KaspiAdapterError(f"Kaspi.ps1 error: {completed.stderr.strip()}")
        stdout = completed.stdout.strip()
        if not stdout:
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            # Если пришёл не JSON — завернём как текст
            return {"raw": stdout}

    def health(self, store: str) -> Any:
        cmd = f". '{self.script_path}'; ks:health -Store {shlex.quote(store)} | ConvertTo-Json -Depth 8"
        return self._run_json(cmd)

    def orders(self, store: str, state: Optional[str] = None) -> Any:
        state_part = f"-State {shlex.quote(state)}" if state else ""
        cmd = f". '{self.script_path}'; ks:orders -Store {shlex.quote(store)} {state_part} | ConvertTo-Json -Depth 8"
        return self._run_json(cmd)

    def publish_feed(self, store: str, offers_json_path: str) -> Any:
        cmd = (
            f". '{self.script_path}'; "
            f"ks:publishFeed -Store {shlex.quote(store)} -OffersJsonPath {shlex.quote(offers_json_path)} "
            f"| ConvertTo-Json -Depth 8"
        )
        return self._run_json(cmd)

    def import_status(self, store: str, import_id: Optional[str] = None) -> Any:
        import_part = f"-ImportId {shlex.quote(import_id)}" if import_id else ""
        cmd = f". '{self.script_path}'; ks:import -Store {shlex.quote(store)} {import_part} -StatusOnly | ConvertTo-Json -Depth 8"
        return self._run_json(cmd)
