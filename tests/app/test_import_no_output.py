from __future__ import annotations

import importlib
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
