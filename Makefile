# SmartSell3 Development Makefile

.PHONY: help install dev test lint format migration upgrade clean docker-up docker-down

help: ## Show this help message
	@echo "SmartSell3 Development Commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	pip install -r requirements.txt
	pip install -e .

dev: ## Install development dependencies
	pip install -r requirements.txt
	pip install pytest pytest-asyncio pytest-cov flake8 mypy black isort

test: ## Run tests
	pytest tests/ -v --cov=app --cov-report=term-missing

test-coverage: ## Run tests with HTML coverage report
	pytest tests/ -v --cov=app --cov-report=html --cov-report=term-missing

lint: ## Run linting
	flake8 app/ tests/ --max-line-length=88
	mypy app/ --ignore-missing-imports

format: ## Format code
	black app/ tests/ --line-length=88
	isort app/ tests/ --line-length=88

format-check: ## Check code formatting
	black app/ tests/ --line-length=88 --check
	isort app/ tests/ --line-length=88 --check-only

migration: ## Create database migration
	alembic revision --autogenerate -m "$(msg)"

upgrade: ## Apply database migrations
	alembic upgrade head

downgrade: ## Rollback database migration
	alembic downgrade -1

clean: ## Clean up temporary files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf dist
	rm -rf build

docker-up: ## Start development environment with Docker
	docker-compose up -d

docker-down: ## Stop development environment
	docker-compose down

docker-build: ## Build Docker image
	docker build -t smartsell3:latest .

run-dev: ## Run development server
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

run-prod: ## Run production server
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

celery-worker: ## Start Celery worker
	celery -A app.services.background_tasks:celery_app worker --loglevel=info

celery-beat: ## Start Celery beat scheduler
	celery -A app.services.background_tasks:celery_app beat --loglevel=info

setup-env: ## Set up environment file
	cp .env.example .env
	@echo "Please edit .env file with your configuration"

init-db: ## Initialize database
	alembic upgrade head

reset-db: ## Reset database (WARNING: This will delete all data)
	rm -f *.db
	alembic upgrade head

security-check: ## Run security checks
	safety check
	bandit -r app/

pre-commit: format lint test ## Run pre-commit checks (format, lint, test)

ci: format-check lint test ## Run CI checks

all: clean install dev migration upgrade test ## Full setup and test
