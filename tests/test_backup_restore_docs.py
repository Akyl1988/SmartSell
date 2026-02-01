def test_backup_restore_docs_exists_and_has_key_strings():
    with open("docs/BACKUP_RESTORE.md", encoding="utf-8") as f:
        content = f.read()

    assert "Backup" in content
    assert "Restore" in content
    assert "pg_dump" in content
    assert "pg_restore" in content
