#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SmartSell Bot – безопасное применение инструкций из комментариев к PR/Issues.

Синтаксис блока:
  /bot apply:
  CONFIRM: YES
  FILE: path/to/file.ext
  MODE: SET | APPEND | REPLACE | PATCH
  FLAGS: ims         # (необязательно, для REPLACE/PATCH)
  PATTERN: ...       # (для REPLACE/PATCH)
  CONTENT:
  <<<
  многострочное содержимое
  >>>

Правила безопасности:
- Нужна строка `CONFIRM: YES` в каждом блоке.
- Разрешены только пути внутри SAFE_DIRS.
- Лимит на число файлов и размер файла.
"""

import os
import re
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

# ---------- Параметры безопасности ----------
SAFE_DIRS = [
    "app/",
    "tests/",
    "docs/",
    ".github/bot-sandbox/",  # песочница для проб
]
MAX_FILES_PER_RUN = 10
MAX_FILE_SIZE_MB = 2

# -------------------------------------------

class BotLogger:
    def __init__(self, log_file: Optional[str] = None):
        self.logger = logging.getLogger("SmartSellBot")
        self.logger.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        self.logger.addHandler(ch)
        if log_file:
            fh = logging.FileHandler(log_file)
            fh.setFormatter(fmt)
            self.logger.addHandler(fh)

    def info(self, m: str): self.logger.info(m)
    def warn(self, m: str): self.logger.warning(m)
    def error(self, m: str): self.logger.error(m)


class InstructionParser:
    CMD_RE = re.compile(r"/bot\s+apply:\s*(.*?)(?=/bot\s+apply:|$)", re.DOTALL | re.IGNORECASE)
    # Секции формата NAME: value … до следующей секции или конца блока
    SEC_RE = re.compile(r"(?im)^(CONFIRM|FILE|MODE|PATTERN|REPLACEMENT|FLAGS|CONTENT):\s*(.*)$")

    def __init__(self, log: BotLogger):
        self.log = log

    @staticmethod
    def _strip_code_fences(content: str) -> str:
        # Поддержка <<< >>> как явных границ контента
        fence_match = re.search(r"<<<\s*\n?(.*?)\n?>>>", content, re.DOTALL)
        if fence_match:
            return fence_match.group(1)
        return content

    def parse(self, text: str) -> List[Dict[str, Any]]:
        blocks = []
        for m in self.CMD_RE.finditer(text or ""):
            raw = m.group(1).strip()
            sections: Dict[str, str] = {}
            # Собираем секции построчно, учитывая многострочный CONTENT
            current_key = None
            buf: List[str] = []
            for line in raw.splitlines():
                sec = self.SEC_RE.match(line)
                if sec:
                    # Сохраняем предыдущую секцию
                    if current_key is not None:
                        sections[current_key] = "\n".join(buf).strip()
                    current_key = sec.group(1).upper()
                    buf = [sec.group(2)]
                else:
                    if current_key is None:
                        continue
                    buf.append(line)
            if current_key is not None:
                sections[current_key] = "\n".join(buf).strip()

            # Нормализуем контент
            if "CONTENT" in sections:
                sections["CONTENT"] = self._strip_code_fences(sections["CONTENT"])

            blocks.append(sections)

        self.log.info(f"Найдено блоков: {len(blocks)}")
        return blocks


class FileProcessor:
    SUPPORTED = {"SET", "APPEND", "REPLACE", "PATCH"}

    def __init__(self, log: BotLogger, base_dir: Path):
        self.log = log
        self.repo_root = base_dir.resolve()
        self.changed_files = 0

    def _is_safe(self, path: Path) -> bool:
        try:
            rp = path.resolve()
            if not str(rp).startswith(str(self.repo_root)):
                return False
            rel = rp.relative_to(self.repo_root).as_posix() + "/"
            return any(rel.startswith(d) for d in SAFE_DIRS)
        except Exception:
            return False

    def _check_limits(self, path: Path):
        if self.changed_files >= MAX_FILES_PER_RUN:
            raise RuntimeError(f"Лимит файлов исчерпан ({MAX_FILES_PER_RUN}).")
        if path.exists() and path.is_file() and path.stat().st_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise RuntimeError(f"Файл {path} слишком большой (> {MAX_FILE_SIZE_MB}MB).")

    def apply(self, ins: Dict[str, Any]) -> bool:
        file_rel = ins.get("FILE") or ins.get("file")
        mode = (ins.get("MODE") or ins.get("mode") or "").upper()
        if not file_rel or mode not in self.SUPPORTED:
            self.log.error("Отсутствуют FILE или MODE, либо MODE не поддерживается.")
            return False

        path = (self.repo_root / file_rel).resolve()

        if not self._is_safe(path):
            self.log.error(f"Запрещённый путь: {path}")
            return False

        self._check_limits(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "SET":
            content = ins.get("CONTENT", "")
            path.write_text(content, encoding="utf-8")
            self.log.info(f"[SET] {path}")
        elif mode == "APPEND":
            content = ins.get("CONTENT", "")
            with open(path, "a", encoding="utf-8") as f:
                if not content.endswith("\n"):
                    content += "\n"
                f.write(content)
            self.log.info(f"[APPEND] {path}")
        elif mode in ("REPLACE", "PATCH"):
            if not path.exists():
                self.log.error(f"Файл не существует для {mode}: {path}")
                return False
            pattern = ins.get("PATTERN")
            repl = ins.get("REPLACEMENT", "")
            flags_s = (ins.get("FLAGS") or "").lower()
            flags = 0
            if "i" in flags_s: flags |= re.IGNORECASE
            if "m" in flags_s: flags |= re.MULTILINE
            if "s" in flags_s: flags |= re.DOTALL

            text = path.read_text(encoding="utf-8")
            new = re.sub(pattern, repl, text, flags=flags) if pattern else text
            if new == text:
                self.log.warn(f"[{mode}] без изменений (паттерн не найден): {path}")
                return False
            path.write_text(new, encoding="utf-8")
            self.log.info(f"[{mode}] {path}")
        else:
            self.log.error(f"Неизвестный режим: {mode}")
            return False

        self.changed_files += 1
        return True


def process_comment(comment: str, base_path: str = ".", log_file: Optional[str] = None) -> Tuple[int, int]:
    log = BotLogger(log_file)
    parser = InstructionParser(log)
    proc = FileProcessor(log, Path(base_path))

    blocks = parser.parse(comment)
    ok, fail = 0, 0

    for idx, sec in enumerate(blocks, 1):
        if str(sec.get("CONFIRM", "")).strip().upper() != "YES":
            log.warn(f"Блок {idx} пропущен: нет CONFIRM: YES")
            fail += 1
            continue
        try:
            if proc.apply(sec):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            log.error(f"Ошибка в блоке {idx}: {e}")
            fail += 1

    log.info(f"Готово. Успешно: {ok}, Ошибки: {fail}, Изменено файлов: {proc.changed_files}")
    return ok, fail


def main():
    # 1) CLI-параметр имеет приоритет
    cli_comment = None
    if len(sys.argv) >= 3 and sys.argv[1] == "--comment":
        cli_comment = sys.argv[2]

    # 2) Иначе пробуем ENV от GitHub Actions
    env_comment = os.getenv("BOT_COMMENT", "")

    comment = cli_comment or env_comment
    if not comment:
        print("No comment text provided (neither --comment nor BOT_COMMENT).")
        sys.exit(0)

    ok, fail = process_comment(comment, base_path=".")
    # Не валим джобу, если нет критических ошибок и просто ничего не изменили
    sys.exit(1 if fail > 0 else 0)


if __name__ == "__main__":
    main()
