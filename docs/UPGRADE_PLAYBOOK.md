# Upgrade Playbook (Production)

## Goals & scope

Provide a safe, repeatable upgrade and rollback process for SmartSell in production.
This playbook covers application updates, database migrations, smoke checks, and rollback.

## Preconditions

- **Backups:** Ensure a recent backup exists (use tools/backup_db.ps1).
- **Git revision:** Confirm the target revision/tag/branch.
- **Env vars:** Ensure required environment variables are set (see docs/DEPLOYMENT.md).
- **Maintenance window:** Schedule a window for upgrade/rollback if needed.
- **Service access:** Confirm you can restart the service (systemd, supervisor, or container).

## Step-by-step upgrade

1) **Pull code**
   - Fetch and checkout the target revision.

2) **Create or activate venv**
   - Use the project’s Python version.

3) **Install dependencies**
   - `pip install -r requirements.txt`

4) **Run migrations**
   - `python -m alembic upgrade head`

5) **Restart service**
   - Restart the application process (systemd, container, or supervisor).

6) **Run local gates (recommended)**
   - `scripts/prod-gate.ps1`

## Post-upgrade verification

Run smoke checks to validate basic functionality:

- `scripts/smoke-openapi.ps1`
- `scripts/smoke-auth.ps1`
- `scripts/smoke-kaspi-sync-now.ps1`

Also verify:

- Health endpoints: `/api/v1/health`, `/api/v1/wallet/health`
- Logs show no errors and request IDs are present.

## Rollback plan

1) **Restore previous code**
   - Checkout previous known-good revision and redeploy.

2) **Database rollback (if applicable)**
   - If migrations were applied, run a downgrade to the previous revision:
     `python -m alembic downgrade <previous_revision>`

3) **Database restore (if needed)**
   - Use `tools/restore_db.ps1 -File <backup.dump>`
   - Use `-Drop` only when you intend to replace all data.

4) **Restart service and re-run smoke checks**

## Troubleshooting

- **Failed migrations:** Check Alembic logs and DB connectivity.
- **HTTP 5xx:** Inspect application logs and correlate with request IDs.
- **Auth failures:** Verify env vars, time sync, and token secrets.
- **Kaspi issues:** Review integration events and re-run smoke tests.

## Checklists

### Pre-upgrade
- [ ] Backup completed (tools/backup_db.ps1)
- [ ] Target revision confirmed
- [ ] Maintenance window approved
- [ ] Service restart method confirmed

### During upgrade
- [ ] Dependencies installed
- [ ] Alembic upgrade completed
- [ ] Service restarted

### Post-upgrade
- [ ] scripts/prod-gate.ps1 run
- [ ] scripts/smoke-openapi.ps1 run
- [ ] scripts/smoke-auth.ps1 run
- [ ] scripts/smoke-kaspi-sync-now.ps1 run
- [ ] Health endpoints verified

### Rollback readiness
- [ ] Previous revision available
- [ ] Alembic downgrade plan ready
- [ ] tools/restore_db.ps1 available