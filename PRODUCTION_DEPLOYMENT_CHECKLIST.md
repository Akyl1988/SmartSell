# Production Deployment Checklist - SmartSell
## Чек-лист развертывания в продакшене

**Версия:** 1.0  
**Дата создания:** 12 февраля 2026  
**Цель:** Обеспечить безопасное и надежное развертывание SmartSell в production

---

## Pre-Deployment Requirements

### 1. Infrastructure Setup

#### 1.1 Server Requirements
- [ ] **Минимальные требования:**
  - CPU: 2+ cores (рекомендуется 4+)
  - RAM: 4GB+ (рекомендуется 8GB+)
  - Storage: 50GB+ SSD
  - Network: 100Mbps+
  - OS: Ubuntu 22.04 LTS или аналог

- [ ] **Выбрать хостинг провайдера:**
  - [ ] DigitalOcean (рекомендуется для MVP - $40-80/месяц)
  - [ ] AWS EC2 (для масштабирования)
  - [ ] Hetzner (дешевле, Европа)
  - [ ] Другой VPS провайдер

- [ ] **Создать сервер и настроить SSH доступ**
  - [ ] Настроить SSH ключи (отключить password auth)
  - [ ] Создать непривилегированного пользователя (не root)
  - [ ] Настроить firewall (ufw или iptables)

#### 1.2 DNS Configuration
- [ ] Зарегистрировать домен (если еще нет)
- [ ] Настроить DNS записи:
  - [ ] A record: `api.yourdomain.com` → IP сервера
  - [ ] A record: `yourdomain.com` → IP сервера (для фронтенда)
  - [ ] Опционально: AAAA record для IPv6

#### 1.3 PostgreSQL Database
- [ ] **Вариант A: Managed DB** (рекомендуется)
  - [ ] DigitalOcean Managed Database
  - [ ] AWS RDS
  - [ ] Настроить automatic backups
  - [ ] Настроить connection pooling

- [ ] **Вариант B: Self-hosted**
  - [ ] Установить PostgreSQL 15+
  - [ ] Настроить pg_hba.conf для безопасности
  - [ ] Настроить postgresql.conf (max_connections, shared_buffers)
  - [ ] Настроить automatic backups (pg_dump + cron)

- [ ] Создать production database
- [ ] Создать database user с ограниченными правами
- [ ] Протестировать подключение

#### 1.4 Redis
- [ ] **Вариант A: Managed Redis** (рекомендуется)
  - [ ] DigitalOcean Managed Redis
  - [ ] AWS ElastiCache
  
- [ ] **Вариант B: Self-hosted**
  - [ ] Установить Redis 7+
  - [ ] Настроить redis.conf (maxmemory, eviction policy)
  - [ ] Настроить persistence (AOF + RDB)

- [ ] Протестировать подключение

---

### 2. Application Configuration

#### 2.1 Environment Variables
- [ ] Создать `.env` файл на сервере (НЕ коммитить в Git!)
- [ ] Установить критически важные переменные:

```bash
# ОБЯЗАТЕЛЬНО
ENVIRONMENT=production
DEBUG=0
SECRET_KEY=<сгенерировать_64_символа>
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname
REDIS_URL=redis://host:6379/0
PGCRYPTO_KEY=<сгенерировать_16+_символов>
INTEGRATIONS_MASTER_KEY=<base64_ключ>

# Security
ALLOWED_HOSTS=["api.yourdomain.com"]
CORS_ORIGINS=["https://yourdomain.com"]
BACKEND_CORS_ORIGINS=["https://yourdomain.com"]

# Public URL
PUBLIC_URL=https://api.yourdomain.com

# Optional но рекомендуется
SENTRY_DSN=<your_sentry_dsn>
```

- [ ] **Сгенерировать SECRET_KEY:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

- [ ] **Сгенерировать PGCRYPTO_KEY:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

- [ ] **Сгенерировать INTEGRATIONS_MASTER_KEY:**
```bash
python -c "import secrets; import base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

#### 2.2 External Services Configuration
- [ ] **Kaspi Integration** (если нужна)
  - [ ] Получить API credentials от Kaspi
  - [ ] Установить переменные: KASPI_API_KEY, KASPI_MERCHANT_ID
  - [ ] Настроить autosync если нужно: KASPI_AUTOSYNC_ENABLED=1

- [ ] **TipTop Pay** (если нужна)
  - [ ] Получить API credentials
  - [ ] Установить: TIPTOP_PAY_PUBLIC_KEY, TIPTOP_PAY_SECRET_KEY

- [ ] **Mobizon SMS** (если нужна)
  - [ ] Получить API key
  - [ ] Установить: MOBIZON_API_KEY
  - [ ] Настроить: OTP_PROVIDER=mobizon

- [ ] **Cloudinary** (если нужна)
  - [ ] Создать аккаунт
  - [ ] Установить: CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET

- [ ] **SMTP Email** (если нужна)
  - [ ] Настроить SMTP сервер или использовать сервис (SendGrid, Mailgun)
  - [ ] Установить: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_EMAIL

#### 2.3 Security Configuration
- [ ] Проверить настройки безопасности:
  - [ ] DEBUG=0 (критически важно!)
  - [ ] ALLOWED_HOSTS настроен правильно
  - [ ] CORS_ORIGINS ограничен
  - [ ] SECRET_KEY уникальный и сложный
  - [ ] PGCRYPTO_KEY установлен

---

### 3. Docker Deployment

#### 3.1 Install Docker
- [ ] Установить Docker Engine
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

- [ ] Установить Docker Compose
```bash
sudo apt-get update
sudo apt-get install docker-compose-plugin
```

- [ ] Проверить установку
```bash
docker --version
docker compose version
```

#### 3.2 Prepare Docker Configuration
- [ ] Скопировать код на сервер:
```bash
# На локальной машине
git clone https://github.com/Akyl1988/SmartSell.git
cd SmartSell

# Или на сервере
git clone https://github.com/Akyl1988/SmartSell.git
cd SmartSell
```

- [ ] Проверить docker-compose.prod.yml
- [ ] Настроить `.env` файл (см. раздел 2.1)

#### 3.3 Build and Start Containers
- [ ] Build Docker image:
```bash
docker compose -f docker-compose.prod.yml build
```

- [ ] Run migrations:
```bash
docker compose -f docker-compose.prod.yml run --rm app poetry run alembic upgrade head
```

- [ ] Start services:
```bash
docker compose -f docker-compose.prod.yml up -d
```

- [ ] Проверить логи:
```bash
docker compose -f docker-compose.prod.yml logs -f app
```

- [ ] Проверить что контейнеры запущены:
```bash
docker compose -f docker-compose.prod.yml ps
```

---

### 4. Nginx Reverse Proxy & SSL

#### 4.1 Install Nginx
- [ ] Установить Nginx:
```bash
sudo apt-get update
sudo apt-get install nginx
```

- [ ] Проверить что Nginx запущен:
```bash
sudo systemctl status nginx
```

#### 4.2 Configure Nginx
- [ ] Создать конфигурацию для API:
```bash
sudo nano /etc/nginx/sites-available/smartsell-api
```

**Базовая конфигурация:**
```nginx
server {
    listen 80;
    server_name api.yourdomain.com;

    client_max_body_size 20M;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Health check
    location /health {
        access_log off;
        proxy_pass http://localhost:8000/health;
    }
}
```

- [ ] Включить конфигурацию:
```bash
sudo ln -s /etc/nginx/sites-available/smartsell-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 4.3 Install SSL Certificate (Let's Encrypt)
- [ ] Установить Certbot:
```bash
sudo apt-get install certbot python3-certbot-nginx
```

- [ ] Получить SSL сертификат:
```bash
sudo certbot --nginx -d api.yourdomain.com
```

- [ ] Проверить auto-renewal:
```bash
sudo certbot renew --dry-run
```

- [ ] Nginx автоматически обновит конфигурацию с SSL

---

### 5. Database Initialization

#### 5.1 Run Migrations
- [ ] Применить все миграции:
```bash
docker compose -f docker-compose.prod.yml run --rm app poetry run alembic upgrade head
```

- [ ] Проверить версию миграции:
```bash
docker compose -f docker-compose.prod.yml run --rm app poetry run alembic current
```

#### 5.2 Create Initial Data
- [ ] **Создать первого admin пользователя** (через bootstrap или вручную)
  - Если есть bootstrap скрипт:
  ```bash
  docker compose -f docker-compose.prod.yml run --rm app python bootstrap_schema.py
  ```
  
  - Или через API после запуска (использовать OTP)

- [ ] Создать тестовые данные если нужно (только для staging!)

---

### 6. Application Verification

#### 6.1 Health Checks
- [ ] Проверить health endpoint:
```bash
curl https://api.yourdomain.com/health
```
Ожидается: `{"status":"ok"}`

- [ ] Проверить API docs (только если нужно в production):
```bash
curl https://api.yourdomain.com/docs
```

#### 6.2 Smoke Tests
- [ ] Тестовая регистрация пользователя (если открыто)
- [ ] Тестовая аутентификация (login)
- [ ] Проверить основные API endpoints:
  - [ ] GET /api/v1/users/me (с токеном)
  - [ ] GET /api/v1/products (если есть данные)
  - [ ] POST /api/v1/kaspi/orders/sync (если Kaspi настроен)

#### 6.3 Security Tests
- [ ] Проверить HTTPS работает (no mixed content)
- [ ] Проверить CORS headers
- [ ] Проверить rate limiting
- [ ] Проверить что debug mode выключен (нет stack traces в errors)
- [ ] Попробовать несколько неудачных логинов (проверить rate limit)

---

### 7. Monitoring & Logging

#### 7.1 Application Logs
- [ ] Настроить ротацию логов Docker:
```json
// /etc/docker/daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "5"
  }
}
```

- [ ] Перезапустить Docker:
```bash
sudo systemctl restart docker
docker compose -f docker-compose.prod.yml up -d
```

#### 7.2 Error Tracking (Sentry) - Опционально но рекомендуется
- [ ] Создать аккаунт на sentry.io
- [ ] Создать проект
- [ ] Скопировать DSN
- [ ] Добавить в .env: `SENTRY_DSN=your_dsn`
- [ ] Перезапустить приложение

#### 7.3 Monitoring (Prometheus + Grafana) - Опционально
- [ ] Установить Prometheus
- [ ] Настроить scraping для FastAPI metrics
- [ ] Установить Grafana
- [ ] Импортировать дашборды

**Для MVP можно пропустить и использовать только логи**

---

### 8. Backup & Recovery

#### 8.1 Database Backups
- [ ] **Managed DB:** Настроить automatic backups в панели провайдера
  - [ ] Daily backups
  - [ ] Retention: минимум 7 дней

- [ ] **Self-hosted:** Настроить автоматический backup script:
```bash
# /usr/local/bin/backup-smartsell-db.sh
#!/bin/bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/var/backups/smartsell"
mkdir -p $BACKUP_DIR

# Database backup
docker compose -f /path/to/SmartSell/docker-compose.prod.yml \
  exec -T db pg_dump -U postgres smartsell | \
  gzip > $BACKUP_DIR/smartsell_$TIMESTAMP.sql.gz

# Keep only last 7 days
find $BACKUP_DIR -name "smartsell_*.sql.gz" -mtime +7 -delete
```

- [ ] Добавить в crontab:
```bash
sudo crontab -e
# Add line:
0 2 * * * /usr/local/bin/backup-smartsell-db.sh
```

#### 8.2 Media Files Backup (если используется)
- [ ] Настроить backup media директории
- [ ] Или использовать cloud storage (Cloudinary) - рекомендуется

#### 8.3 Test Recovery
- [ ] Протестировать восстановление из бэкапа:
```bash
# Восстановление
gunzip < smartsell_TIMESTAMP.sql.gz | \
  docker compose exec -T db psql -U postgres -d smartsell
```

---

### 9. Security Hardening

#### 9.1 Firewall Configuration
- [ ] Настроить UFW firewall:
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

#### 9.2 SSH Hardening
- [ ] Отключить root login: `PermitRootLogin no` в `/etc/ssh/sshd_config`
- [ ] Отключить password auth: `PasswordAuthentication no`
- [ ] Перезапустить SSH: `sudo systemctl restart sshd`

#### 9.3 System Updates
- [ ] Настроить automatic security updates:
```bash
sudo apt-get install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

#### 9.4 Fail2Ban (опционально)
- [ ] Установить fail2ban:
```bash
sudo apt-get install fail2ban
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
```

---

### 10. Documentation & Training

#### 10.1 User Documentation
- [ ] Написать User Manual на русском:
  - [ ] Как зарегистрироваться
  - [ ] Как войти в систему
  - [ ] Как работать с товарами
  - [ ] Как работать с заказами
  - [ ] Как настроить интеграцию с Kaspi
  - [ ] FAQ

- [ ] Создать video tutorials (опционально но очень полезно)

#### 10.2 Admin Documentation
- [ ] Документировать deployment процесс
- [ ] Задокументировать backup/restore процедуры
- [ ] Создать troubleshooting guide
- [ ] Задокументировать мониторинг и логи

#### 10.3 Training
- [ ] Провести обучение для первого клиента
- [ ] Показать основные функции
- [ ] Ответить на вопросы
- [ ] Собрать feedback

---

### 11. Go-Live Checklist

#### Final Pre-Launch Verification
- [ ] Все environment variables настроены правильно
- [ ] Database migrations применены
- [ ] SSL certificate установлен и работает
- [ ] Backups настроены и протестированы
- [ ] Мониторинг настроен (хотя бы логи)
- [ ] Smoke tests пройдены успешно
- [ ] Security hardening выполнен
- [ ] User documentation готова
- [ ] Support процедуры определены

#### Launch
- [ ] Объявить клиенту что система готова
- [ ] Предоставить доступ (URL, credentials)
- [ ] Провести walkthrough
- [ ] Быть на связи для hotfixes

#### Post-Launch (первые 24-48 часов)
- [ ] Активно мониторить логи
- [ ] Быстро реагировать на issues
- [ ] Собирать feedback от клиента
- [ ] Фиксить критические баги если есть
- [ ] Отслеживать production метрики:
  - [ ] Response times
  - [ ] Error rates
  - [ ] Database connections
  - [ ] Disk usage

---

## Emergency Contacts & Procedures

### Кто кого вызывать при проблемах:
- **Критический баг:** [Ваш телефон/email]
- **Инфраструктура проблемы:** [DevOps contact]
- **Вопросы клиента:** [Support contact]

### Quick Restart Procedures:
```bash
# Restart application
cd /path/to/SmartSell
docker compose -f docker-compose.prod.yml restart app

# Check logs
docker compose -f docker-compose.prod.yml logs -f app

# Restart all services
docker compose -f docker-compose.prod.yml restart

# Emergency: Stop all and rebuild
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d --build
```

### Rollback Procedure:
```bash
# 1. Stop current version
docker compose -f docker-compose.prod.yml down

# 2. Checkout previous version
git checkout <previous-tag-or-commit>

# 3. Rollback database if needed
docker compose exec -T db psql -U postgres -d smartsell < backup.sql

# 4. Start previous version
docker compose -f docker-compose.prod.yml up -d
```

---

## MVP vs Full Production

### Можно пропустить для MVP (но нужно для масштабирования):
- ❌ Load balancer
- ❌ CDN для статики
- ❌ Advanced monitoring (Prometheus + Grafana)
- ❌ Automated scaling
- ❌ Multi-region deployment
- ❌ Advanced caching strategies

### Обязательно даже для MVP:
- ✅ SSL/HTTPS
- ✅ Database backups
- ✅ Basic monitoring (logs)
- ✅ Security hardening
- ✅ Environment configuration

---

**Финальная проверка:** Прошли ли все пункты с галочкой? Если да - можно запускать! 🚀

**Документ подготовлен:** AI Assistant  
**Дата:** 12 февраля 2026  
**Версия:** 1.0
