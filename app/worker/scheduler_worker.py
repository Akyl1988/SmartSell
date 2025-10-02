# app/worker/scheduler_worker.py
"""
APScheduler worker для SmartSell:
- периодическая обработка активных кампаний (каждую минуту)
- постановка в очередь всех PENDING-сообщений кампании
- отправка писем по SMTP (TLS/SSL, ретраи, аккуратные таймауты)
- подробный лог и безопасная работа с БД (SessionLocal)
- сервисные функции: start/stop/reload/get_status/enqueue_campaign

Ожидаемые настройки в .env (см. app/core/config.py):
  SCHEDULER_TIMEZONE=UTC
  SMTP_HOST=
  SMTP_PORT=0
  SMTP_USER=
  SMTP_PASSWORD=
  SMTP_FROM_EMAIL=
  SMTP_FROM_NAME=SmartSell
  SMTP_TLS=True
  SMTP_SSL=False
"""

from __future__ import annotations

import atexit
import logging
import smtplib
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable, Dict, Optional

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

# -------- Настройки / БД: мягкие импорты под разные проекты -------- #

try:
    # Наш единый объект настроек
    from app.core.config import settings
except Exception:  # pragma: no cover
    # Если проект старого образца с фабрикой настроек
    from app.core.config import get_settings

    settings = get_settings()  # type: ignore

SessionLocal = None
for _path in (
    "app.core.db",  # наш основной
    "app.core.database",  # вариант из старых скриптов
    "app.database.session",  # наследие
):
    try:
        mod = __import__(_path, fromlist=["SessionLocal"])
        SessionLocal = getattr(mod, "SessionLocal")
        break
    except Exception:
        continue
if SessionLocal is None:
    raise RuntimeError("SessionLocal не найден. Проверьте, что есть app.core.db.SessionLocal")

# Модели
from app.models.campaign import Campaign, CampaignStatus, Message, MessageStatus

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# -------- Вспомогательные сущности -------- #

def _utcnow_naive() -> datetime:
    """Naive UTC для совместимости с большинством наших моделей."""
    return datetime.utcnow()


def _utcnow_aware() -> datetime:
    """Aware UTC — для сравнения, где это критично (планировщик)."""
    return datetime.now(timezone.utc)


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_email: str
    from_name: str
    use_tls: bool
    use_ssl: bool
    connect_timeout: float = 15.0
    op_timeout: float = 30.0


def _load_smtp_config() -> SmtpConfig:
    return SmtpConfig(
        host=getattr(settings, "SMTP_HOST", "") or "",
        port=int(getattr(settings, "SMTP_PORT", 0) or 0),
        user=getattr(settings, "SMTP_USER", "") or "",
        password=getattr(settings, "SMTP_PASSWORD", "") or "",
        from_email=getattr(settings, "SMTP_FROM_EMAIL", "") or "",
        from_name=getattr(settings, "SMTP_FROM_NAME", "SmartSell") or "SmartSell",
        use_tls=bool(getattr(settings, "SMTP_TLS", True)),
        use_ssl=bool(getattr(settings, "SMTP_SSL", False)),
    )


@contextmanager
def db_session():
    """Контекстный менеджер для сессии БД с корректным закрытием."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# -------- SMTP отправка с ретраями -------- #

def _build_email(smtp: SmtpConfig, recipient: str, subject: str, plain_text: str) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = f"{smtp.from_name} <{smtp.from_email or smtp.user}>"
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(plain_text or "", "plain", "utf-8"))
    return msg


def _send_via_smtp(smtp: SmtpConfig, msg: MIMEMultipart) -> None:
    if not smtp.host or not smtp.port:
        raise RuntimeError("SMTP не настроен: проверьте SMTP_HOST и SMTP_PORT")

    def _configure(server: smtplib.SMTP) -> None:
        server.timeout = smtp.op_timeout
        if smtp.use_tls:
            server.starttls()
        if smtp.user and smtp.password:
            server.login(smtp.user, smtp.password)

    if smtp.use_ssl:
        with smtplib.SMTP_SSL(host=smtp.host, port=smtp.port, timeout=smtp.connect_timeout) as server:
            _configure(server)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host=smtp.host, port=smtp.port, timeout=smtp.connect_timeout) as server:
            _configure(server)
            server.send_message(msg)


def _smtp_send_with_retry(send_fn: Callable[[], None], *, retries: int = 2, base_delay: float = 0.7) -> None:
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            send_fn()
            return
        except (smtplib.SMTPException, OSError, socket.timeout) as e:
            last_err = e
            if attempt >= retries:
                break
            sleep_s = base_delay * (2 ** attempt)
            logger.warning("SMTP send retry %s/%s через %.1fs: %s", attempt + 1, retries, sleep_s, e)
            time.sleep(sleep_s)
    assert last_err is not None
    raise last_err


# -------- Планировщик -------- #

scheduler = BackgroundScheduler(
    timezone=getattr(settings, "SCHEDULER_TIMEZONE", "UTC") or "UTC",
    daemon=True,
)

_JOB_ID_PROCESS_CAMPAIGNS = "process_campaigns"

# События планировщика для детального лога
def _on_scheduler_event(event):
    if event.code == EVENT_JOB_MISSED:
        logger.warning("APScheduler: пропущен запуск job_id=%s", getattr(event, "job_id", "?"))
    elif event.code == EVENT_JOB_MAX_INSTANCES:
        logger.error("APScheduler: достигнут максимум инстансов job_id=%s", getattr(event, "job_id", "?"))
    elif event.code == EVENT_JOB_ERROR:
        logger.exception("APScheduler: ошибка в job_id=%s", getattr(event, "job_id", "?"))


scheduler.add_listener(_on_scheduler_event, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES | EVENT_JOB_ERROR)


# -------- Бизнес-логика -------- #

def send_message(message_id: int) -> None:
    """
    Отправка одного сообщения через SMTP c аккуратным изменением статусов.
    """
    smtp = _load_smtp_config()
    with db_session() as db:
        message: Optional[Message] = db.query(Message).filter(Message.id == message_id).first()
        if not message:
            logger.error("Message %s не найден", message_id)
            return

        if message.status != MessageStatus.PENDING:
            logger.info("Message %s уже обработан (status=%s)", message_id, message.status)
            return

        recipient = message.recipient
        subject = f"Campaign: {message.campaign.title}" if message.campaign else "Campaign"
        body = message.content or ""

        msg = _build_email(smtp, recipient, subject, body)
        logger.info("Отправка message_id=%s -> %s", message.id, recipient)

        try:
            _smtp_send_with_retry(lambda: _send_via_smtp(smtp, msg))
            message.status = MessageStatus.SENT
            message.sent_at = _utcnow_naive()
            message.error_message = None
            logger.info("Сообщение %s успешно отправлено", message.id)
        except Exception as e:
            logger.error("Ошибка отправки message_id=%s: %s", message.id, e)
            message.status = MessageStatus.FAILED
            message.error_message = f"{type(e).__name__}: {e}"


def _schedule_message_send(message_id: int) -> None:
    """
    Постановка задачи на немедленную отправку конкретного сообщения.
    """
    job_id = f"send_message_{message_id}"
    scheduler.add_job(
        send_message,
        trigger=DateTrigger(run_date=_utcnow_aware()),
        id=job_id,
        replace_existing=True,
        kwargs={"message_id": message_id},
        max_instances=5,
        coalesce=True,
        misfire_grace_time=60,
    )
    logger.info("Запланирована отправка message_id=%s (job_id=%s)", message_id, job_id)


def process_scheduled_campaigns() -> None:
    """
    Обрабатывает активные кампании, у которых время запуска наступило:
      - выбирает PENDING-сообщения
      - для каждого ставит job на отправку
      - если больше нет PENDING — помечает кампанию COMPLETED
    """
    now = _utcnow_naive()
    logger.info("Проверка кампаний к отправке (%s)", now.isoformat())
    with db_session() as db:
        campaigns = (
            db.query(Campaign)
            .filter(
                Campaign.status == CampaignStatus.ACTIVE,
                Campaign.scheduled_at <= now,
            )
            .all()
        )

        for campaign in campaigns:
            logger.info("Обработка кампании id=%s title=%r", campaign.id, campaign.title)

            # Берём PENDING сообщения
            pending = (
                db.query(Message)
                .filter(
                    Message.campaign_id == campaign.id,
                    Message.status == MessageStatus.PENDING,
                )
                .all()
            )

            if not pending:
                # Если нечего слать — завершаем кампанию
                campaign.status = CampaignStatus.COMPLETED
                logger.info("Кампания id=%s помечена как COMPLETED (нет PENDING сообщений)", campaign.id)
                continue

            # Ставим каждое сообщение в очередь на ближайший запуск
            for m in pending:
                try:
                    _schedule_message_send(m.id)
                except Exception as e:
                    logger.error("Не удалось запланировать message_id=%s: %s", m.id, e)
                    m.status = MessageStatus.FAILED
                    m.error_message = f"Scheduling error: {e}"

            # Проверяем, остались ли ещё PENDING в БД (на случай ошибок постановки)
            still_pending = (
                db.query(Message)
                .filter(Message.campaign_id == campaign.id, Message.status == MessageStatus.PENDING)
                .count()
            )
            if still_pending == 0:
                campaign.status = CampaignStatus.COMPLETED
                logger.info("Кампания id=%s завершена (все сообщения расписаны)", campaign.id)


# -------- Публичные сервисные функции воркера -------- #

def enqueue_campaign(campaign_id: int) -> Dict[str, int]:
    """
    Принудительно поставить в очередь PENDING-сообщения указанной кампании.
    Удобно дергать из админки/скриптов.
    """
    enqueued = 0
    failed = 0
    with db_session() as db:
        campaign: Optional[Campaign] = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} не найдена")

        messages = (
            db.query(Message)
            .filter(Message.campaign_id == campaign_id, Message.status == MessageStatus.PENDING)
            .all()
        )
        for m in messages:
            try:
                _schedule_message_send(m.id)
                enqueued += 1
            except Exception as e:
                logger.error("Ошибка постановки message_id=%s: %s", m.id, e)
                m.status = MessageStatus.FAILED
                m.error_message = f"Scheduling error: {e}"
                failed += 1
    return {"enqueued": enqueued, "failed": failed}


def start() -> None:
    """
    Запуск планировщика:
      - job process_scheduled_campaigns каждую минуту
      - graceful shutdown при завершении процесса
    """
    logger.info("Запуск APScheduler worker")

    # Основная периодическая задача
    scheduler.add_job(
        process_scheduled_campaigns,
        trigger=IntervalTrigger(minutes=1),
        id=_JOB_ID_PROCESS_CAMPAIGNS,
        replace_existing=True,
        max_instances=1,
        coalesce=True,           # слить пропущенные запуски в один
        misfire_grace_time=60,   # допуск по пропуску
    )

    scheduler.start()
    logger.info("APScheduler запущен (timezone=%s)", getattr(settings, "SCHEDULER_TIMEZONE", "UTC"))

    # Автоматический graceful shutdown
    atexit.register(stop)


def stop() -> None:
    """Остановка планировщика (graceful)."""
    try:
        logger.info("Остановка APScheduler worker")
        scheduler.shutdown(wait=True)
        logger.info("APScheduler остановлен")
    except Exception as e:
        logger.error("Ошибка при остановке планировщика: %s", e)


def reload_jobs() -> None:
    """Перезагрузить (пересоздать) базовые задачи планировщика."""
    try:
        scheduler.remove_job(_JOB_ID_PROCESS_CAMPAIGNS)
    except Exception:
        pass
    scheduler.add_job(
        process_scheduled_campaigns,
        trigger=IntervalTrigger(minutes=1),
        id=_JOB_ID_PROCESS_CAMPAIGNS,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    logger.info("Базовые задачи планировщика пересозданы")


def get_status() -> Dict[str, str]:
    """Короткий статус воркера: запущен/нет, кол-во задач."""
    try:
        jobs = scheduler.get_jobs()
        return {
            "running": str(scheduler.running),
            "jobs_count": str(len(jobs)),
            "jobs": ", ".join(j.id for j in jobs),
        }
    except Exception as e:
        logger.error("Не удалось получить статус планировщика: %s", e)
        return {"running": "unknown", "error": str(e)}
