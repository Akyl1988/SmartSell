# Руководство по использованию SmartSell Bot

## Обзор

SmartSell Bot — это автоматический бот для применения изменений к файлам в репозитории через комментарии в Pull Request'ах и Issues. Бот реагирует на команды вида `/bot apply:` и выполняет операции с файлами согласно заданным инструкциям.

## Синтаксис команд

### Базовый синтаксис

```
/bot apply:
FILE: путь/к/файлу
MODE: режим_операции
[дополнительные_секции]
```

### Обязательные секции

- **FILE**: Путь к файлу относительно корня репозитория
- **MODE**: Режим операции (SET, APPEND, REPLACE, PATCH)

### Дополнительные секции

- **PATTERN**: Регулярное выражение для поиска (требуется для REPLACE и PATCH)
- **REPLACEMENT**: Текст для замены (используется с PATTERN)
- **FLAGS**: Флаги для регулярных выражений (i, m, s)
- **CONTENT**: Содержимое для записи (требуется для SET и APPEND)

## Режимы операций

### 1. SET - Установка содержимого файла

Полностью заменяет содержимое файла новым текстом.

**Пример:**
```
/bot apply:
FILE: docs/example.md
MODE: SET
CONTENT: # Новый документ

Это содержимое полностью заменит старое.

## Секция 1
Текст секции 1.

## Секция 2
Текст секции 2.
```

### 2. APPEND - Добавление в конец файла

Добавляет содержимое в конец существующего файла.

**Пример:**
```
/bot apply:
FILE: README.md
MODE: APPEND
CONTENT: 

## Обновления

- Добавлена поддержка автоматического бота
- Улучшена документация
- Исправлены ошибки
```

### 3. REPLACE - Замена по паттерну

Заменяет текст в файле согласно регулярному выражению.

**Пример замены версии:**
```
/bot apply:
FILE: package.json
MODE: REPLACE
PATTERN: "version":\s*"[^"]*"
REPLACEMENT: "version": "2.1.0"
FLAGS: i
```

**Пример замены раздела в коде:**
```
/bot apply:
FILE: app/config.py
MODE: REPLACE
PATTERN: DEBUG\s*=\s*.*
REPLACEMENT: DEBUG = False
```

### 4. PATCH - Частичная замена строк

Находит строки, соответствующие паттерну, и заменяет их.

**Пример изменения настройки в YAML:**
```
/bot apply:
FILE: .github/workflows/ci.yml
MODE: PATCH
PATTERN: ^\s*runs-on:.*
REPLACEMENT:     runs-on: ubuntu-22.04
FLAGS: m
```

## Флаги регулярных выражений

- **i** - Игнорировать регистр (case-insensitive)
- **m** - Многострочный режим (^ и $ соответствуют началу/концу строк)
- **s** - Режим "точка соответствует всему" (. соответствует символам новой строки)

**Пример с флагами:**
```
/bot apply:
FILE: src/main.py
MODE: REPLACE
PATTERN: def\s+old_function.*?^def
REPLACEMENT: def new_function(params):
    """Новая функция."""
    pass

def
FLAGS: ims
```

## Несколько команд в одном комментарии

Можно выполнить несколько операций в одном комментарии:

```
/bot apply:
FILE: VERSION
MODE: SET
CONTENT: 2.1.0

/bot apply:
FILE: CHANGELOG.md
MODE: APPEND
CONTENT: 

## Версия 2.1.0
- Добавлена новая функциональность
- Исправлены критические ошибки

/bot apply:
FILE: README.md
MODE: REPLACE
PATTERN: \[Версия \d+\.\d+\.\d+\]
REPLACEMENT: [Версия 2.1.0]
```

## Примеры использования

### Пример 1: Создание нового файла конфигурации

```
/bot apply:
FILE: config/database.yml
MODE: SET
CONTENT: development:
  adapter: postgresql
  host: localhost
  port: 5432
  database: smartsell_dev
  username: dev_user
  password: dev_password

production:
  adapter: postgresql
  host: <%= ENV['DB_HOST'] %>
  port: <%= ENV['DB_PORT'] %>
  database: <%= ENV['DB_NAME'] %>
  username: <%= ENV['DB_USER'] %>
  password: <%= ENV['DB_PASSWORD'] %>
```

### Пример 2: Обновление документации

```
/bot apply:
FILE: docs/API.md
MODE: APPEND
CONTENT: 

## Новый эндпоинт: /api/v2/products

### GET /api/v2/products
Получает список всех продуктов.

**Параметры:**
- `limit` (integer): Максимальное количество результатов
- `offset` (integer): Смещение для пагинации

**Ответ:**
```json
{
  "products": [...],
  "total": 150,
  "limit": 20,
  "offset": 0
}
```
```

### Пример 3: Исправление бага в коде

```
/bot apply:
FILE: src/utils/validator.py
MODE: REPLACE
PATTERN: def validate_email\(email\):.*?return.*
REPLACEMENT: def validate_email(email):
    """Проверяет корректность email адреса."""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None
FLAGS: s
```

### Пример 4: Обновление CI/CD конфигурации

```
/bot apply:
FILE: .github/workflows/test.yml
MODE: PATCH
PATTERN: ^\s*python-version:.*
REPLACEMENT:       python-version: ['3.8', '3.9', '3.10', '3.11']
FLAGS: m
```

## Обработка ошибок

Бот логирует все операции и ошибки. В случае ошибки:

1. Операция пропускается
2. Ошибка записывается в лог
3. Обработка продолжается со следующей инструкции

## Безопасность

### Ограничения:
- Бот работает только с файлами внутри репозитория
- Не может выполнять системные команды
- Создает резервные копии перед изменением файлов
- Все операции логируются

### Рекомендации:
- Проверяйте команды перед отправкой
- Используйте тестовые файлы для экспериментов
- Регулярно проверяйте логи выполнения

## Устранение неполадок

### Частые ошибки:

1. **"Пропущены обязательные секции FILE или MODE"**
   - Убедитесь, что указаны секции FILE и MODE

2. **"Неподдерживаемый режим"**
   - Проверьте, что MODE один из: SET, APPEND, REPLACE, PATCH

3. **"Не указан паттерн для замены"**
   - Для режимов REPLACE и PATCH требуется секция PATTERN

4. **"Ошибка синтаксиса регулярного выражения"**
   - Проверьте корректность паттерна
   - Экранируйте специальные символы

### Отладка:

Для отладки команд можно использовать простые тесты:

```
/bot apply:
FILE: test_debug.txt
MODE: SET
CONTENT: Тестовое содержимое для отладки
```

## Интеграция с workflow

Бот может быть интегрирован в GitHub Actions для автоматической обработки комментариев:

```yaml
name: Bot Apply
on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]

jobs:
  bot-apply:
    if: contains(github.event.comment.body, '/bot apply:')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.9'
      - name: Run Bot
        run: |
          python .github/bot/apply.py --comment "${{ github.event.comment.body }}"
```

## Заключение

SmartSell Bot предоставляет мощный и гибкий способ автоматизации операций с файлами через комментарии. Правильное использование всех возможностей бота поможет значительно упростить процесс разработки и поддержки проекта.