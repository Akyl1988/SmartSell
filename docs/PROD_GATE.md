# Prod-Gate Pipeline

Автоматизированный проверочный пайплайн для SmartSell. Запуск одной командой, остановка на первом FAIL.

## Что проверяет
1. Env prep (маскирование паролей, проверка ENVIRONMENT/DATABASE_URL/PYTHONPATH)
2. `pip check`
3. `ruff check`
4. `mypy app tests`
5. `pytest -q`
6. Alembic: `current`, `heads`, `upgrade head`
7. Runtime smoke: `uvicorn app.main:app` с явным `DATABASE_URL`, опрос `/health` и `/live`
8. Fail-fast guard: в `ENVIRONMENT=production` без `DATABASE_URL` импорт `app.main` обязан упасть `RuntimeError`
9. Secret scan: `gitleaks detect` (если установлен)
10. Docker: `docker build` и короткий `docker run` smoke

## Как запускать
- Обычный прогон (без docker/gitleaks/uvicorn):
  - `./scripts/prod-gate.ps1 -SkipDocker -SkipGitleaks -SkipUvicorn`
- Указать явный DSN и окружение:
  - `./scripts/prod-gate.ps1 -DatabaseUrl "postgresql://user:pass@host:5432/db" -Environment production`
- Полный прогон со всеми стадиями (при наличии docker/gitleaks):
  - `./scripts/prod-gate.ps1 -DatabaseUrl "postgresql://user:pass@host:5432/db"`
- Включить строгий ruff/mypy (без exit-zero/ignore-errors):
  - `./scripts/prod-gate.ps1 -Strict`

## Типичные причины FAIL и как чинить
- `pip check`: несовместимые версии — обновить/зафиксировать зависимости в `requirements.txt`.
- `ruff`/`mypy`: исправить lint/type ошибки.
- `pytest`: падение тестов — анализировать логи, починить код/фикстуры.
- Alembic `upgrade head`: конфликт миграций или отсутствующий DSN — проверить `DATABASE_URL` и миграции.
- Runtime smoke: `uvicorn` не стартует или `/health` 5xx — проверить DB DSN, секреты, настройки.
- Fail-fast guard не срабатывает — проверить логику `app.main` и `resolve_database_url`.
- `gitleaks`: удалить секреты, переиздать ключи.
- `docker build/run`: ошибки сборки или сервис не стартует — фиксить Dockerfile/зависимости, проверить логи контейнера.
