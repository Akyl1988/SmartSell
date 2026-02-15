# Production Deployment Runbook (VPS)

## Requirements

- Ubuntu 22.04+ VPS
- Docker Engine + Docker Compose plugin
- Domain name with DNS pointing to the VPS (optional but recommended)

## Initial setup

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

Log out/in to apply group changes.

## Configure environment

```bash
cp .env.example.prod .env.prod
# Edit .env.prod to set SECRET_KEY, CSRF_SECRET, DATABASE_URL, REDIS_URL, CORS, etc.
```

## Build and start

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

## Migrations

```bash
# Run migrations in the api container
CONTAINER=$(docker compose -f docker-compose.prod.yml ps -q api)
docker exec -it $CONTAINER alembic upgrade head
```

## Health checks

```bash
curl -fsS http://127.0.0.1:8000/api/v1/wallet/health
curl -fsS http://127.0.0.1:8000/openapi.json
```

## Logs

```bash
# All services
docker compose -f docker-compose.prod.yml logs -f

# API only
docker compose -f docker-compose.prod.yml logs -f api
```

## Rollback (image tag)

```bash
# Example: rollback to a previous image tag
docker compose -f docker-compose.prod.yml pull
# Edit docker-compose.prod.yml to pin image: smartsell:<tag>
docker compose -f docker-compose.prod.yml up -d
```

## Backup / restore (Postgres)

```bash
# Backup
PG_CONTAINER=$(docker compose -f docker-compose.prod.yml ps -q postgres)
docker exec -t $PG_CONTAINER pg_dump -U postgres -d smartsell > smartsell_$(date +%F).sql

# Restore (drop/create before restore if needed)
cat smartsell_2026-02-15.sql | docker exec -i $PG_CONTAINER psql -U postgres -d smartsell
```

## Secret rotation

- SECRET_KEY: required for token signing and encryption. Rotating invalidates existing JWTs.
- CSRF_SECRET: must differ from SECRET_KEY in production. Rotating invalidates existing CSRF tokens.

Safe rotation steps:
1) Notify users of session invalidation.
2) Update .env.prod with new secrets.
3) Restart api container: `docker compose -f docker-compose.prod.yml up -d`.

Do not rotate secrets without a planned maintenance window if active sessions must stay valid.
