import pathlib


def test_no_sync_db_get_calls():
    root = pathlib.Path(__file__).resolve().parents[3]
    api_root = root / "app" / "api" / "v1"
    bad_lines: list[str] = []

    for py_file in api_root.rglob("*.py"):
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if "db.get(" not in line:
                continue
            prefix = line.split("db.get(", 1)[0]
            if "await" not in prefix:
                rel = py_file.relative_to(root)
                bad_lines.append(f"{rel}:{lineno}: {line.strip()}")

    assert not bad_lines, "Non-awaited db.get detected:\n" + "\n".join(bad_lines)
