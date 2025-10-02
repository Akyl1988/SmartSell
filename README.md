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

## 🧪 Testing

### Run all tests
```bash
poetry run pytest
```

### Run with coverage
```bash
poetry run pytest --cov=app --cov-report=html
```

## 🔧 Development

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
