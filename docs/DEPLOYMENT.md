# Deployment (Golden Path)

This guide covers a single, practical path to deploy SmartSell in production.

See also: Kaspi feed lifecycle guide in docs/KASPI_FEED.md.

## Requirements

- Python 3.11
- PostgreSQL 14+ (required)
- Redis (optional; can run without it)
- Reverse proxy (e.g., Nginx) for TLS termination

## PostgreSQL setup

Create a role and database, then enable pgcrypto:

```sql
-- adjust user/password/database as needed
CREATE ROLE smartsell LOGIN PASSWORD 'change_me' CREATEDB;
CREATE DATABASE smartsell OWNER smartsell;

-- connect to the database and enable extension
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

## Environment variables

Use .env.example as the reference. These keys are **required in production**:

- ENVIRONMENT: set to "production"
- SECRET_KEY: app secret (JWT/signing)
- DATABASE_URL: Postgres connection URL
- PUBLIC_URL: external base URL (used in links/webhooks)
- ALLOWED_HOSTS: comma-separated hostnames
- PGCRYPTO_KEY: key used for DB-level encryption
- INTEGRATIONS_MASTER_KEY: key for integrations secrets

Kaspi feed (optional, for feed upload flow):
- KASPI_FEED_TOKEN or KASPI_TOKEN
- KASPI_FEED_BASE_URL, KASPI_FEED_UPLOAD_URL, KASPI_FEED_STATUS_URL, KASPI_FEED_RESULT_URL
- KASPI_HTTP_TIMEOUT_SEC

Kaspi PowerShell helper script: scripts/Kaspi.ps1 (dot-source before use).

Never commit real secrets. Use a secure secrets manager or systemd EnvironmentFile.

Redis is optional. If disabled, set:

- REDIS_DISABLED=1
- REDIS_URL=disabled

## Install and migrate

```bash
python -m alembic upgrade head
```

## Run (development)

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

## Run (production)

```bash
gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  -w 4 \
  -b 0.0.0.0:8000
```

## systemd unit (gunicorn)

```ini
[Unit]
Description=SmartSell API
After=network.target

[Service]
Type=simple
User=smartsell
WorkingDirectory=/opt/smartsell
EnvironmentFile=/opt/smartsell/.env
ExecStart=/opt/smartsell/.venv/bin/gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  -w 4 \
  -b 0.0.0.0:8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Nginx reverse proxy (TLS termination)

```nginx
server {
    listen 443 ssl;
    server_name example.com;

    # TLS config placeholder
    ssl_certificate /etc/ssl/certs/your_cert.pem;
    ssl_certificate_key /etc/ssl/private/your_key.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;
    }
}
```

## Health checks

- /api/v1/health
- /api/v1/wallet/health

Readiness is exposed at /ready (optional strict gating with env flags).

## Logging and request IDs

Logs include a request identifier. Clients can send X-Request-ID to correlate logs.

## Integration events (Kaspi)

Recent Kaspi integration events (connect, selftest, orders sync, feed uploads) are stored
in the integration events log. Use it to trace failures by request_id.

- GET /api/v1/integrations/events?kind=kaspi&limit=100

## Backup and restore

```bash
pg_dump -Fc -d smartsell -f smartsell.dump
pg_restore -d smartsell smartsell.dump
```

## Password reset / admin recovery

Use the CLI tool:

```bash
python -m app.cli.reset_password --email admin@example.com --password 'new_password'
```
