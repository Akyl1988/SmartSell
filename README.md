# SmartSell — FastAPI Platform

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

SmartSell — облачная платформа для автоматизации онлайн-торговли и маркетинговых кампаний.
Основу составляют **FastAPI** (приложение) и **React (Vite, MUI)** (фронтенд). Поддерживаются интеграции с Kaspi, TipTop Pay, Mobizon, Cloudinary, управление товарами, заказами, платежами, складами, кампаниями, мониторинг и журналирование.

SmartSell is a modern, scalable e-commerce platform built with FastAPI, designed for high performance and security. It provides a comprehensive solution for online businesses with features including user management, product catalog, order processing, and integration with external services.

## 🚀 Features

### Core Features
- **User Management**: Registration, authentication, profile management with JWT tokens
- **Product Catalog**: Categories, products, variants with advanced search and filtering
- **Order Management**: Shopping cart, checkout, order tracking
- **Security**: Rate limiting, CORS protection, input validation, audit logging
- **API Versioning**: Clean `/api/v1/` structure with dynamic router loading
- **Database**: SQLAlchemy with Alembic migrations, proper constraints and indexes

### Security Features
- **JWT Authentication**: Access and refresh tokens
- **Password Security**: bcrypt hashing, password strength validation
- **Rate Limiting**: Per-endpoint and global rate limiting
- **Input Validation**: Comprehensive Pydantic schemas with custom validators
- **Audit Logging**: Security events and data changes tracking
- **CORS Configuration**: Production-ready CORS settings

### External Integrations
- **SMS OTP**: Mobizon API for phone verification
- **Image Storage**: Cloudinary integration for media management
- **Payment Processing**: TipTop Pay integration
- **Marketplace**: Kaspi API integration
- **Background Tasks**: Celery with Redis for async operations

## 📋 Requirements

- Python 3.11+
- Poetry for dependency management
- PostgreSQL (production) or SQLite (development)
- Redis (for caching and background tasks)

## 🛠 Installation

### 1. Install Poetry (if needed)
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

### 2. Clone the repository
```bash
git clone https://github.com/Akyl1988/SmartSell.git
cd SmartSell
```

### 3. Install dependencies
```bash
poetry install
```

### 4. Configure environment
```bash
cp .env.example .env
# Edit .env with your configuration
```

### 5. Initialize database
```bash
# Create migration
poetry run alembic revision --autogenerate -m "Initial migration"

# Apply migration
poetry run alembic upgrade head
```

### 6. Run the application
```bash
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The application will be available at `http://127.0.0.1:8000`

## 🏗 Project Structure

```
SmartSell/
├── app/                    # Main application package
│   ├── api/               # API endpoints
│   │   └── v1/           # API version 1
│   │       ├── __init__.py    # Dynamic router loading
│   │       ├── auth.py        # Authentication endpoints
│   │       ├── users.py       # User management
│   │       └── products.py    # Product management
│   ├── core/              # Core application components
│   │   ├── config.py         # Configuration settings
│   │   ├── security.py       # Security utilities
│   │   ├── logging.py        # Centralized logging
│   │   ├── exceptions.py     # Custom exceptions & handlers
│   │   └── dependencies.py   # FastAPI dependencies
│   ├── models/            # SQLAlchemy models
│   │   ├── base.py           # Base model with timestamps
│   │   ├── user.py           # User models
│   │   └── product.py        # Product models
│   ├── schemas/           # Pydantic schemas
│   │   ├── base.py           # Base schemas, pagination
│   │   ├── user.py           # User schemas with validation
│   │   └── product.py        # Product schemas with validation
│   ├── services/          # Business logic services
│   ├── utils/             # Utility functions
│   ├── db.py             # Database configuration
│   └── main.py           # FastAPI application
├── tests/                 # Test suite
│   ├── unit/             # Unit tests
│   ├── integration/      # Integration tests
│   └── conftest.py       # Pytest configuration
├── alembic/              # Database migrations
├── pyproject.toml        # Project configuration
├── .env.example          # Environment variables template
├── .python-version       # Python version specification
├── .pre-commit-config.yaml # Pre-commit hooks configuration
└── README.md             # This file
```

## ⚙️ Configuration

### Environment Variables

| Variable             | Description                     | Default                      | Required |
|----------------------|---------------------------------|------------------------------|----------|
| `DEBUG`              | Enable debug mode               | `False`                      | No       |
| `ENVIRONMENT`        | Environment (development/production) | `development`           | No       |
| `SECRET_KEY`         | JWT secret key                  | -                            | Yes      |
| `DATABASE_URL`       | Database connection URL         | `sqlite:///./smartsell.db`   | No       |
| `REDIS_URL`          | Redis connection URL            | `redis://localhost:6379`     | No       |
| `ALLOWED_HOSTS`      | Allowed hosts (comma-separated) | `*`                          | No       |
| `CORS_ORIGINS`       | CORS origins (comma-separated)  | `*`                          | No       |

### External Services

Configure these services in your `.env` file:

```bash
# SMS OTP Service
MOBIZON_API_KEY=your-mobizon-api-key

# Image Storage
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret

# Payment Processing
TIPTOP_PAY_PUBLIC_KEY=your-public-key
TIPTOP_PAY_SECRET_KEY=your-secret-key

# Marketplace Integration
KASPI_MERCHANT_ID=your-merchant-id
KASPI_API_KEY=your-api-key
```

## Roles (DEV smoke)

Canonical roles used in docs/scripts:
- platform_admin: users.role="platform_admin"; allowed to call `/api/v1/admin/*`.
- store_admin: users.role="admin" (legacy DB name); allowed to call tenant APIs (wallet/kaspi/etc) within their company.

Notes:
- The DB role string remains `admin` for store_admin. Docs/scripts use store_admin for clarity.
- Legacy env vars ADMIN_IDENTIFIER/ADMIN_PASSWORD and SMARTSELL_IDENTIFIER/SMARTSELL_PASSWORD are treated as store_admin.

Example env vars for smoke:
```bash
# store_admin (users.role="admin")
STORE_IDENTIFIER=77078342842
STORE_PASSWORD=admin123

# platform_admin (users.role="platform_admin")
PLATFORM_IDENTIFIER=77052384799
PLATFORM_PASSWORD=admin123
```

## Campaign Processing Pipeline

Campaigns are processed in a safe, lock-protected pipeline:

1. Scheduler tick runs `enqueue_due_campaigns_sync()` to queue due campaigns.
2. Worker runs `process_campaign_queue_once_sync()` to process queued campaigns.
3. Finished campaigns have pending messages scheduled for delivery.

Concurrency safeguards:
- Scheduler lock: `pg_try_advisory_lock` to ensure only one scheduler tick runs at a time.
- Queue lock: `pg_try_advisory_xact_lock` to prevent concurrent queue processing in one tick.
- Per-campaign lock: `pg_try_advisory_xact_lock` keyed by campaign id.

Tuning parameters:
- `CAMPAIGN_PROCESS_BATCH` (default 50): max campaigns processed per worker tick.
- `CAMPAIGN_MAX_ATTEMPTS` (default 3): max processing retries; `0` disables the guard.

If a campaign reaches the limit, it is marked FAILED with `last_error=max_attempts_exceeded`.
Operators can manually re-run the campaign (platform_admin/store_admin run endpoints), which resets attempts and clears failure fields.

## 🧪 Testing

### How tests pick the database

Pytest resolves the test database using TEST_ASYNC_DATABASE_URL (preferred) or TEST_DATABASE_URL.
The test harness sets DATABASE_URL to the sync test URL, so dev DB settings do not leak into tests.
Use a separate database for tests (for example smartsell_test) and keep it isolated from dev data.

### Run all tests
```bash
poetry run pytest
```

### Run with coverage
```bash
poetry run pytest --cov=app --cov-report=html
```

## Release gate

Run the standard pre-release checks locally:

```powershell
pwsh -NoProfile -File .\scripts\prod-gate.ps1 -BaseUrl http://127.0.0.1:8000
```

What it checks:
- `ruff format --check`, `ruff check`, `pytest -q`
- Alembic sanity: `alembic heads` and `alembic history`
- Reports smoke (optional): `scripts/smoke-reports-all.ps1`

Skip smoke if the API is not running:

```powershell
pwsh -NoProfile -File .\scripts\prod-gate.ps1 -SkipSmoke
```

Skip format check if needed:

```powershell
pwsh -NoProfile -File .\scripts\prod-gate.ps1 -SkipFormatCheck
```

## 🔧 Development

### Windows one-button dev (PowerShell)

1) Create scripts/env.local.ps1 (do not commit secrets) with DATABASE_URL and optional REDIS_URL.
2) Run the one-button entrypoint:

```powershell
.\scripts\dev.ps1 up
.\scripts\dev.ps1 api
```

Common commands:

```powershell
.\scripts\dev.ps1 up     # start db+redis (docker compose if present) + run migrations
.\scripts\dev.ps1 api    # start uvicorn and stream logs to logs/api.log
.\scripts\dev.ps1 down   # stop services if docker compose is used
.\scripts\dev.ps1 reset  # DEV ONLY: drop schema, re-run migrations (optional -Seed)
```

Campaigns storage is explicit via SMARTSELL_CAMPAIGNS_STORAGE=sql|memory (default: sql).
Tests force in-memory backends via FORCE_INMEMORY_BACKENDS=1 to keep isolation.

### Code Quality

The project uses several tools for code quality:

```bash
poetry run ruff check .
poetry run ruff format --check .
poetry run black --check .
poetry run isort --check-only .
poetry run mypy .
```

### Auto-fix formatting issues
```bash
poetry run ruff format .
poetry run black .
poetry run isort .
```

### Pre-commit hooks
```bash
poetry run pre-commit install
```

### OpenAPI download
If OpenAPI JSON breaks when copied from the browser, download it via HTTP:
```bash
curl -sS http://127.0.0.1:8000/openapi.json -o openapi.json
```
```powershell
Invoke-WebRequest "http://127.0.0.1:8000/openapi.json" -OutFile .\openapi.json
```

### Dev-only schema check

```powershell
python .\scripts\print_campaigns_columns.py
```

## Branching & Releases

- **Branches**: `dev` is the main development branch. `main` updates only via PRs (usually from release branches). Release preparation happens on `release/*` (e.g., `release/v0.1.0`) branched off `dev`.
- **Tags**: Use SemVer tags with `v` prefix (e.g., `v0.1.0`). Tag on the release branch after it is ready.
- **Release workflow (M0.1 pattern)**:
  1. Create `release/v0.1.0` from `dev`.
  2. Stabilize and update `CHANGELOG.md` on that branch.
  3. Tag `v0.1.0` on the release branch.
  4. Open a PR from `release/v*` into `main`; merge only after checks pass.
- **Changelog rule**: Every release must update `CHANGELOG.md` (Keep a Changelog format) to record added/changed/fixed notes for the new version.

### Database Migrations

```bash
poetry run alembic revision --autogenerate -m "Description of changes"
poetry run alembic upgrade head
```

## 📚 API Documentation

When running in development mode, API documentation is available at:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI Schema**: http://localhost:8000/openapi.json

### Authentication

Most endpoints require authentication. Include the JWT token in the Authorization header:

```bash
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     http://localhost:8000/api/v1/users/me
```

Cookie-mode refresh/logout: if you use the refresh token via HttpOnly cookies, the client must send an
`X-CSRF-Token` header bound to the refresh session (see `generate_csrf_token`/`validate_csrf_token` in
app/core/security.py). Requests without a valid CSRF token are rejected with 403.

Manual wallet top-up is platform-only: `POST /api/v1/admin/wallet/topup` is available only to
platform_admins (or superuser break-glass) and does not depend on payments providers.

## 🚀 Deployment

### Production Checklist

1. **Environment Configuration**
   - Set `ENVIRONMENT=production`
   - Set `DEBUG=False`
   - Configure strong `SECRET_KEY`
   - Use PostgreSQL database

2. **Security Settings**
   - Configure `ALLOWED_HOSTS`
   - Set specific `CORS_ORIGINS`
   - Enable HTTPS
   - Set up reverse proxy (nginx)

3. **Database**
   - Run migrations: `poetry run alembic upgrade head`
   - Set up database backups
   - Configure connection pooling

4. **Monitoring**
   - Set up application logging
   - Configure health checks
   - Monitor performance metrics

### Docker Deployment

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY poetry.lock .
RUN pip install poetry && poetry install --no-interaction --no-ansi

COPY . .

EXPOSE 8000

CMD ["poetry", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Docker Compose

```yaml
version: '3.8'

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://postgres:password@db:5432/smartsell
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis

  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=smartsell
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=password
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
```

## 🤝 Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature/new-feature`
3. Make changes and add tests
4. Run tests and linting: `poetry run pytest && poetry run ruff check . && poetry run mypy .`
5. Commit changes: `git commit -m "Add new feature"`
6. Push branch: `git push origin feature/new-feature`
7. Create pull request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

- **Issues**: [GitHub Issues](https://github.com/Akyl1988/SmartSell/issues)
- **Documentation**: Check the `/docs` endpoint when running the application
- **Discussions**: [GitHub Discussions](https://github.com/Akyl1988/SmartSell/discussions)

## 🔄 Changelog

### Version 1.0.0
- Initial release with core features
- User authentication and management
- Product catalog management
- API versioning with dynamic router loading
- Comprehensive security features
- Database migrations with Alembic
- Test suite with pytest
- Production-ready configuration


