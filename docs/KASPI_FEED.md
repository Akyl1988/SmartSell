# Kaspi Feed: от нуля до publish

Ниже — production‑ready гайд для администраторов SmartSell. Он описывает реальный путь: подготовка каталога → генерация offers.xml → upload → status → publish → проверка журналов.

> В этом гайде используются **точные** API пути из сервиса SmartSell.

Быстрый старт для админа: используйте скрипт scripts/kaspi-onboarding.ps1 (самотест + события + опционально upload/publish).

---

## A) Что такое Kaspi feed и чем отличается от “добавить товар через кабинет/Excel+ZIP”

**Kaspi feed** — это XML‑файл (offers.xml), который SmartSell формирует из вашего каталога и отправляет в Kaspi через официальный API. Это автоматизированный поток: вы обновляете каталог в SmartSell, а дальше всё уходит в Kaspi одной операцией.

**Excel/ZIP** — это ручная загрузка в кабинете Kaspi. Она подходит для разовых действий, но неудобна для регулярных обновлений.

**Итог:** feed нужен для регулярных, повторяемых обновлений ассортимента и цен без ручной рутины.

---

## B) Предусловия (что нужно заранее)

### 1) SmartSell доступен
- Вы можете зайти в SmartSell и получить токены через /api/v1/auth/login.

### 2) Kaspi token настроен в SmartSell
- Настройка делается через `/api/v1/kaspi/connect`.
- Это требуется, чтобы SmartSell мог отправлять feed в Kaspi.

### 3) Скрипт Kaspi.ps1 (официальный поток)
Файл расположен здесь: scripts/Kaspi.ps1.

Скрипт **должен быть dot‑sourced**, иначе команды не загрузятся:

```powershell
pwsh -NoProfile -Command "& {
  . \"$PWD\scripts\Kaspi.ps1\"
  ks:feedStatus -Store \"<KASPI_STORE_NAME>\" -ImportId \"<IMPORT_CODE>\"
}"
```

#### Обязательные env переменные для scripts/Kaspi.ps1
- KASPI_FEED_TOKEN или KASPI_TOKEN
  - Можно указать с суффиксом магазина: KASPI_FEED_TOKEN_<STORE>, KASPI_TOKEN_<STORE>
- KASPI_FEED_BASE_URL (если URL не абсолютные)
- KASPI_FEED_UPLOAD_URL
- KASPI_FEED_STATUS_URL
- KASPI_FEED_RESULT_URL
- KASPI_HTTP_TIMEOUT_SEC (таймаут на HTTP, по умолчанию 60)

Пример установки (PowerShell 7):

```powershell
pwsh -NoProfile -Command "& {
  $env:KASPI_FEED_TOKEN = '<KASPI_FEED_TOKEN>'
  $env:KASPI_FEED_BASE_URL = 'https://kaspi.kz'
  $env:KASPI_FEED_UPLOAD_URL = 'shop/api/feeds/import'
  $env:KASPI_FEED_STATUS_URL = 'shop/api/feeds/import/status'
  $env:KASPI_FEED_RESULT_URL = 'shop/api/feeds/import/result'
  $env:KASPI_HTTP_TIMEOUT_SEC = '60'
}"
```

> Плейсхолдеры вида `<...>` нужно **заменить на реальные значения** до запуска.

---

## C) Golden path (10–15 минут)

### 1) Логин в SmartSell
**Endpoint:** POST /api/v1/auth/login

**PowerShell 7:**
```powershell
pwsh -NoProfile -Command "& {
  $baseUrl = 'http://127.0.0.1:8000'
  $body = @{ identifier = '<PHONE_OR_EMAIL>'; password = '<PASSWORD>' } | ConvertTo-Json
  $resp = Invoke-RestMethod -Method Post -Uri ($baseUrl + '/api/v1/auth/login') -ContentType 'application/json' -Body $body
  $token = $resp.access_token
  $token | Out-Host
}"
```

**curl:**
```bash
curl -s -X POST "http://127.0.0.1:8000/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"identifier":"<PHONE_OR_EMAIL>","password":"<PASSWORD>"}'
```

В ответе будет `access_token`. Его используем в следующих запросах.

---

### 2) Подключение Kaspi (если ещё не настроено)
**Endpoint:** POST /api/v1/kaspi/connect

**JSON тело:**
- company_name
- store_name
- token
- verify (true/false)
- meta (опционально)

**PowerShell 7:**
```powershell
pwsh -NoProfile -Command "& {
  $baseUrl = 'http://127.0.0.1:8000'
  $token = '<ACCESS_TOKEN>'
  $body = @{
    company_name = '<COMPANY_NAME>'
    store_name = '<KASPI_STORE_NAME>'
    token = '<KASPI_TOKEN>'
    verify = $true
  } | ConvertTo-Json
  Invoke-RestMethod -Method Post -Uri ($baseUrl + '/api/v1/kaspi/connect') -Headers @{ Authorization = 'Bearer ' + $token } -ContentType 'application/json' -Body $body
}"
```

---

### 3) Подготовка каталога (если нужно)
**Endpoint:** POST /api/v1/kaspi/catalog/import

Поддерживаемые форматы: CSV / XLSX / JSON / JSONL. Минимальные поля:
- sku
- title
- price

Дополнительные:
- master_sku, old_price, stock_count, pre_order, stock_specified, updated_at

**Параметры:**
- merchantUid (обязательный)
- dry_run (опционально)

**PowerShell 7 (CSV):**
```powershell
pwsh -NoProfile -Command "& {
  $baseUrl = 'http://127.0.0.1:8000'
  $token = '<ACCESS_TOKEN>'
  $filePath = '<PATH_TO_FILE>'
  $form = @{ file = Get-Item $filePath }
  Invoke-RestMethod -Method Post -Uri ($baseUrl + '/api/v1/kaspi/catalog/import?merchantUid=<MERCHANT_UID>') -Headers @{ Authorization = 'Bearer ' + $token } -Form $form
}"
```

---

### 4) Генерация offers.xml в SmartSell
**Вариант 1 — экспорт из SmartSell**

- POST /api/v1/kaspi/feed/exports (генерирует XML)
- GET /api/v1/kaspi/feed/exports/{export_id}/download (скачать XML)

**PowerShell 7:**
```powershell
pwsh -NoProfile -Command "& {
  $baseUrl = 'http://127.0.0.1:8000'
  $token = '<ACCESS_TOKEN>'
  $export = Invoke-RestMethod -Method Post -Uri ($baseUrl + '/api/v1/kaspi/feed/exports?merchantUid=<MERCHANT_UID>') -Headers @{ Authorization = 'Bearer ' + $token }
  $exportId = $export.id
  Invoke-WebRequest -Uri ($baseUrl + '/api/v1/kaspi/feed/exports/' + $exportId + '/download') -Headers @{ Authorization = 'Bearer ' + $token } -OutFile '<OUT_XML_PATH>'
}"
```

**Вариант 2 — публичный XML (если есть public token)**

- POST /api/v1/kaspi/feed/public-tokens
- GET /api/v1/kaspi/feed/public/offers.xml?token=...&merchantUid=...

> В продакшене токен может **не возвращаться в ответе** из соображений безопасности. В таком случае используйте экспорт (вариант 1) или `source=export_id` в upload.

---

### 5) Upload: создать job
**Endpoint:** POST /api/v1/kaspi/feed/uploads

**JSON тело:**
- merchant_uid (обязательно)
- source: "public_token" | "export_id" | "local_file_path"
- comment (опционально)
- export_id (если source=export_id)
- local_file_path (если source=local_file_path, путь на сервере)

**Пример (export_id):**
```powershell
pwsh -NoProfile -Command "& {
  $baseUrl = 'http://127.0.0.1:8000'
  $token = '<ACCESS_TOKEN>'
  $body = @{ merchant_uid = '<MERCHANT_UID>'; source = 'export_id'; export_id = '<EXPORT_ID>'; comment = 'first upload' } | ConvertTo-Json
  Invoke-RestMethod -Method Post -Uri ($baseUrl + '/api/v1/kaspi/feed/uploads') -Headers @{ Authorization = 'Bearer ' + $token; 'X-Request-ID' = '<REQUEST_ID>' } -ContentType 'application/json' -Body $body
}"
```

**Пример (public_token = загрузка из текущих offers):**
```powershell
pwsh -NoProfile -Command "& {
  $baseUrl = 'http://127.0.0.1:8000'
  $token = '<ACCESS_TOKEN>'
  $body = @{ merchant_uid = '<MERCHANT_UID>'; source = 'public_token' } | ConvertTo-Json
  Invoke-RestMethod -Method Post -Uri ($baseUrl + '/api/v1/kaspi/feed/uploads') -Headers @{ Authorization = 'Bearer ' + $token; 'X-Request-ID' = '<REQUEST_ID>' } -ContentType 'application/json' -Body $body
}"
```

> `X-Request-ID` делает операцию идемпотентной: повторный запрос вернёт тот же job.

---

### 6) Status/Refresh
**Endpoints:**
- GET /api/v1/kaspi/feed/uploads/{upload_id}
- POST /api/v1/kaspi/feed/uploads/{upload_id}/refresh

**Пример refresh:**
```powershell
pwsh -NoProfile -Command "& {
  $baseUrl = 'http://127.0.0.1:8000'
  $token = '<ACCESS_TOKEN>'
  Invoke-RestMethod -Method Post -Uri ($baseUrl + '/api/v1/kaspi/feed/uploads/<UPLOAD_ID>/refresh') -Headers @{ Authorization = 'Bearer ' + $token }
}"
```

**Ключевые поля ответа:**
- import_code — код импорта в Kaspi
- status — текущий статус
- attempts — число попыток
- last_error_code / last_error_message — если есть ошибки

---

### 7) Publish
**Endpoint:** POST /api/v1/kaspi/feed/uploads/{upload_id}/publish

**Что означает publish сейчас:**
- SmartSell повторно запрашивает статус в Kaspi
- Если статус **done/success/completed/published**, job помечается как `published`
- Иначе возвращается ошибка `feed_not_ready_for_publish`

**Пример:**
```powershell
pwsh -NoProfile -Command "& {
  $baseUrl = 'http://127.0.0.1:8000'
  $token = '<ACCESS_TOKEN>'
  Invoke-RestMethod -Method Post -Uri ($baseUrl + '/api/v1/kaspi/feed/uploads/<UPLOAD_ID>/publish') -Headers @{ Authorization = 'Bearer ' + $token }
}"
```

---

### 8) Журнал интеграций
**Endpoint:** GET /api/v1/integrations/events

Фильтр по kind:
- kind=kaspi_feed (только feed)
- kind=kaspi (все kaspi события)

**Пример:**
```powershell
pwsh -NoProfile -Command "& {
  $baseUrl = 'http://127.0.0.1:8000'
  $token = '<ACCESS_TOKEN>'
  Invoke-RestMethod -Method Get -Uri ($baseUrl + '/api/v1/integrations/events?kind=kaspi_feed&limit=50') -Headers @{ Authorization = 'Bearer ' + $token }
}"
```

---

## D) Troubleshooting

**401 / Token expired**
- Перелогиньтесь через `/api/v1/auth/login` или обновите токен через `/api/v1/auth/refresh`.

**missing env vars / неверные URL**
- Проверьте `KASPI_FEED_TOKEN`/`KASPI_TOKEN` и URL‑переменные. Если URL относительные — нужен `KASPI_FEED_BASE_URL`.

**ParserError в PowerShell из‑за “<...>”**
- Плейсхолдеры нужно заменить на реальные значения **до запуска**. В командах они должны быть в кавычках.

**IMPORT_FILE_NOT_FOUND**
- Обычно это означает, что Kaspi не нашёл файл по коду импорта или не получил файл. Проверьте, что upload прошёл успешно и `import_code` сохранён.

**kaspi_upstream_unavailable**
- Внешний API недоступен. Подождите 5–10 минут и повторите refresh/publish.

**XML валидность**
- Файл должен быть в UTF‑8.
- Спецсимволы (&, <, >) должны быть экранированы.
- Внутри `<offer>` обязательны `sku` и `model`. Для цены/наличия — `<price>` или `<availabilities>`.

---

## E) Checklist “готово к клиенту”

1) ✅ Пользователь может залогиниться через /api/v1/auth/login.
2) ✅ Kaspi токен сохранён через /api/v1/kaspi/connect.
3) ✅ Каталог импортирован через /api/v1/kaspi/catalog/import (rows_ok > 0).
4) ✅ Экспорт создан через /api/v1/kaspi/feed/exports.
5) ✅ offers.xml скачивается через /api/v1/kaspi/feed/exports/{id}/download.
6) ✅ Upload job создан через /api/v1/kaspi/feed/uploads.
7) ✅ Есть import_code и валидный status.
8) ✅ refresh обновляет статус через /api/v1/kaspi/feed/uploads/{id}/refresh.
9) ✅ publish завершает job со статусом published.
10) ✅ В /api/v1/integrations/events видны записи kind=kaspi_feed.
11) ✅ Все нужные env переменные для scripts/Kaspi.ps1 заданы.
12) ✅ Ошибки понятны по last_error_code/last_error_message.
