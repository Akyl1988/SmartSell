import pathlib
import re


def _iter_v1_files() -> tuple[pathlib.Path, list[pathlib.Path]]:
    root = pathlib.Path(__file__).resolve().parents[3]
    api_root = root / "app" / "api" / "v1"
    return root, list(api_root.rglob("*.py"))


def test_no_sync_db_get_calls():
    root, files = _iter_v1_files()
    bad_lines: list[str] = []

    for py_file in files:
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if "db.get(" not in line:
                continue
            prefix = line.split("db.get(", 1)[0]
            if "await" not in prefix:
                rel = py_file.relative_to(root)
                bad_lines.append(f"{rel}:{lineno}: {line.strip()}")

    assert not bad_lines, "Non-awaited db.get detected:\n" + "\n".join(bad_lines)


def test_no_get_db_dependency_in_v1():
    root, files = _iter_v1_files()
    bad_lines: list[str] = []

    depends_pattern = re.compile(r"Depends\s*\(\s*get_db\s*\)")
    import_pattern = re.compile(r"from\s+app\.core\.db\s+import\s+.*\bget_db\b")
    alias_skip_pattern = re.compile(r"get_async_db\s+as\s+get_db")

    for py_file in files:
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if "get_db" not in line or alias_skip_pattern.search(line) or "get_async_db" in line:
                continue
            rel = py_file.relative_to(root)
            if depends_pattern.search(line) or import_pattern.search(line) or re.search(r"\bget_db\b", line):
                bad_lines.append(f"{rel}:{lineno}: {line.strip()}")

    assert not bad_lines, "get_db (sync) dependency is forbidden in app/api/v1 (use get_async_db):\n" + "\n".join(
        bad_lines
    )


def test_no_run_sync_in_v1():
    root, files = _iter_v1_files()
    bad_lines: list[str] = []

    for py_file in files:
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if "run_sync(" not in line:
                continue
            rel = py_file.relative_to(root)
            bad_lines.append(f"{rel}:{lineno}: {line.strip()}")

    assert not bad_lines, "run_sync is forbidden in app/api/v1 (async-native only):\n" + "\n".join(bad_lines)
