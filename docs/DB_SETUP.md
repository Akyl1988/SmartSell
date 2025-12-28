# DB Setup How to run

Revision id column is stored as VARCHAR(256); `db_reset.ps1` will automatically widen `public.alembic_version.version_num` to 256 chars (or create it that way if missing).

```powershell
cd /d d:\LLM_HUB\SmartSell
$env:DATABASE_URL="postgresql://USER@HOST:5432/smartsell_main"
$env:TEST_DATABASE_URL="postgresql://USER@HOST:5432/smartsell_test"
pwsh -ExecutionPolicy Bypass -File tools/db_reset.ps1
```

If you explicitly need to stamp without migrating (dangerous, not recommended):
```powershell
pwsh -ExecutionPolicy Bypass -File tools/db_reset.ps1 -AllowStamp
```

## UTF-8 на Windows

- Inline `psql -c "..."` с кириллицей/қазақша в Windows-консолях ненадёжен из-за CP1251 — сервер получит битые байты.
- Используйте `psql -f` с файлом в UTF-8 без BOM (например, `tools/utf8_probe.sql`) или запускайте psql из WSL/Git Bash/UTF-8 окружения.
- Postgres хранит данные в UTF-8 (`server_encoding=UTF8`); `datcollate`/`datctype` могут быть `Russian_Kazakhstan.1251` — это влияет на сортировку/сравнение, но не на хранение кодовых точек.
