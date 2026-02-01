def test_upgrade_playbook_docs_contains_key_strings():
    with open("docs/UPGRADE_PLAYBOOK.md", encoding="utf-8") as f:
        content = f.read()

    assert "Upgrade" in content
    assert "Rollback" in content
    assert "alembic" in content
    assert "backup_db.ps1" in content
    assert "restore_db.ps1" in content
    assert "smoke-auth" in content
    assert "prod-gate" in content