# SmartSell: Окружение и переменные

## Быстрый старт (Windows/WSL)

1. Клонируйте репозиторий и перейдите в папку проекта.
2. Скопируйте `.env.example` → `.env.local` и заполните своими значениями (секреты не коммитить!).
3. Установите зависимости:
   ```powershell
   pip install -r requirements.txt
   ```
4. Запустите Postgres и Redis (например, через Docker):
   ```powershell
   docker run --rm -e POSTGRES_PASSWORD=<PASS> -e POSTGRES_USER=<USER> -e POSTGRES_DB=smartsell -p 5432:5432 postgres:15
   docker run --rm -p 6379:6379 redis:7-alpine
   ```
5. Примените миграции:
   ```powershell
   alembic upgrade head
   ```
6. Запустите тесты:
   ```powershell
   pytest -q
   ```
7. Запустите приложение:
   ```powershell
   uvicorn app.main:app --reload
   ```

## Обязательные переменные окружения

| Переменная                | Пример значения                                                    |
|---------------------------|--------------------------------------------------------------------|
| ENVIRONMENT               | local / development / production / testing                         |
| TESTING                   | True / False                                                       |
| DEBUG                     | True / False                                                       |
| SECRET_KEY                | dev-secret-key (замените в production)                             |
| DATABASE_URL              | postgresql+psycopg://<USER>:<PASS>@127.0.0.1:5432/smartsell         |
| TEST_DATABASE_URL         | postgresql+psycopg://<USER>:<PASS>@127.0.0.1:5432/smartsell_test    |
| TEST_ASYNC_DATABASE_URL   | postgresql+asyncpg://<USER>:<PASS>@127.0.0.1:5432/smartsell_test    |
| REDIS_URL                 | redis://127.0.0.1:6379                                             |
| CORS_ORIGINS              | http://localhost:3000                                              |

## Опциональные переменные (пример)

| Переменная         | Назначение / Пример значения                |
|--------------------|---------------------------------------------|
| MOBIZON_API_URL    | https://api.mobizon.kz                      |
| MOBIZON_API_KEY    | your-mobizon-api-key                        |
| TIPTOP_API_URL     | https://api.tippy.kz                        |
| TIPTOP_API_KEY     | your-tiptop-api-key                         |
| KASPI_API_URL      | https://api.kaspi.kz                        |
| KASPI_API_KEY      | your-kaspi-api-key                          |
| KASPI_MERCHANT_ID  | your-kaspi-merchant-id                      |
| SMTP_HOST          | smtp.gmail.com                              |
| SMTP_PORT          | 587                                         |
| SMTP_USER          | your-email@gmail.com                        |

> **Используйте только те переменные, которые реально требуются вашему окружению и коду.**

## Проверка чтения переменных
- Для диагностики можно временно добавить в main.py:
  ```python
  import os; print('ENV:', {k: os.environ.get(k) for k in ['ENVIRONMENT','DATABASE_URL','REDIS_URL']})
  ```
- Для тестов и CI все переменные задаются через `.env.local` или secrets.

## Безопасность
- **Никогда не коммитьте реальные секреты или .env.local!**
- Для CI/CD используйте secrets в настройках GitHub Actions.
- `.env.example` содержит только безопасные плейсхолдеры.
- Для production — используйте отдельные секреты и переменные окружения.
