# SmartSell2 Backend

## Быстрый старт
1. Создайте `.env` с переменными:
   ```
   DATABASE_URL=postgresql://postgres:postgres@db:5432/smartsell2
   JWT_SECRET_KEY=your_jwt_secret
   TIPTOP_PUBLIC_ID=your_tiptop_id
   TIPTOP_API_SECRET=your_tiptop_secret
   ```
2. Соберите и запустите:
   ```
   docker compose up --build
   ```
3. Swagger UI будет доступен на `/apidocs`

## Миграции
```
alembic upgrade head
```

## Тесты
```
pytest -q
```