from __future__ import annotations

import importlib
import os
import subprocess
import sys


def _assert_import_silent(module_name: str, capsys) -> None:
    if module_name in sys.modules:
        del sys.modules[module_name]
    importlib.import_module(module_name)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_imports_produce_no_output(capsys) -> None:
    _assert_import_silent("app.core.config", capsys)
    _assert_import_silent("app.main", capsys)


def _run_import_subprocess(module_name: str) -> tuple[str, str, int]:
    cmd = [sys.executable, "-c", f"import {module_name}; print('ok')"]
    env = dict(**os.environ)
    env.setdefault("ENVIRONMENT", "development")
    env.setdefault("TESTING", "1")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    return result.stdout, result.stderr, result.returncode


def test_import_main_subprocess_no_output() -> None:
    out, err, code = _run_import_subprocess("app.main")
    assert code == 0
    assert out == "ok\n"
    assert err == ""


def test_import_config_subprocess_no_output() -> None:
    out, err, code = _run_import_subprocess("app.core.config")
    assert code == 0
    assert out == "ok\n"
    assert err == ""
