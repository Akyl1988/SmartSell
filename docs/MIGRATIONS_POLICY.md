# SmartSell — Migration Policy (Locked)

## Правило №1: исторические миграции не редактируем
- Любая правка схемы после мержа в dev/main делается ТОЛЬКО новой миграцией.
- Никаких «подправить старую миграцию, потому что так проще».

## Правило №2: дрейф схемы лечим новой миграцией
- Если runtime БД уже “уехала” (колонка/тип/constraint отличается) — создаём миграцию, которая:
  - добавляет недостающее,
  - меняет тип/constraint безопасно,
  - НЕ ломает upgrade/downgrade smoke.

## Правило №3: локально всегда гоняем migration smoke
- Используем scripts/prod-gate.ps1 (alembic downgrade/upgrade smoke обязателен).
- Любой PR, который трогает models/migrations, должен проходить этот шаг.

## Definition of Done
- На чистой БД: alembic upgrade head OK.
- На существующей: upgrade OK.
- downgrade -1 (если допускается) и upgrade head повторно — OK.
