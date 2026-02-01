# Backup & Restore (PostgreSQL)

This project provides simple PowerShell tools for backing up and restoring the database using PostgreSQL client tools.

## Prerequisites

- `pg_dump` and `pg_restore` are available on your PATH (PostgreSQL client tools).
- Environment variables are set (see below).

## Environment variables

Preferred:

- `DATABASE_URL`

OR standard PG variables:

- `PGHOST`
- `PGPORT`
- `PGDATABASE`
- `PGUSER`
- `PGPASSWORD`

Optional:

- `BACKUP_DIR` (default: .\backups)

## Backup

Creates a timestamped custom-format dump using `pg_dump`.

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\tools\backup_db.ps1
```

Output file pattern:

```
smartsell_<db>_<yyyyMMdd_HHmmss>.dump
```

## Restore

Restores a `.dump` file using `pg_restore`.

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\tools\restore_db.ps1 -File .\backups\smartsell_db_20260202_120000.dump
```

Optional `-Drop` will clean existing objects first:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\tools\restore_db.ps1 -File .\backups\smartsell_db_20260202_120000.dump -Drop
```

## Safety notes

- Double-check target DB before restoring.
- Use `smartsell_test` for test runs.
- Do **not** restore into production unless you intend to replace data.