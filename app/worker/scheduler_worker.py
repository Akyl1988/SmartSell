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
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import update

try:
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.date import DateTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except ModuleNotFoundError:  # pragma: no cover - optional in tests
    EVENT_JOB_ERROR = 1
    EVENT_JOB_MAX_INSTANCES = 2
    EVENT_JOB_MISSED = 4

    class _StubTrigger:
        def __init__(self, *args, **kwargs):  # noqa: D401
            pass

    DateTrigger = _StubTrigger
    IntervalTrigger = _StubTrigger

    class _StubJob:
        def __init__(self, job_id: str):
            self.id = job_id

    class BackgroundScheduler:  # type: ignore[override]
        def __init__(self, *args, **kwargs):
            self.running = False
            self._jobs: dict[str, _StubJob] = {}

        def add_listener(self, *_args, **_kwargs) -> None:
            return None

        def add_job(self, *_args, id: str | None = None, **_kwargs) -> _StubJob:
            job_id = id or f"job-{len(self._jobs) + 1}"
            job = _StubJob(job_id)
            self._jobs[job_id] = job
            return job

        def start(self) -> None:
            self.running = True

        def shutdown(self, wait: bool = True) -> None:
            self.running = False

        def remove_job(self, job_id: str) -> None:
            self._jobs.pop(job_id, None)

        def get_jobs(self) -> list[_StubJob]:
            return list(self._jobs.values())

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

from app.models.campaign import Campaign, CampaignProcessingStatus, Message, MessageStatus
from app.services.campaign_runner import enqueue_due_campaigns_sync
from app.worker import campaign_processing

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_ERROR_MESSAGE_LIMIT = 500


# -------- Kaspi autosync mutual exclusion helper -------- #


def _env_truthy(value: str | None, default: bool = False) -> bool:
    """Check if environment variable is truthy (same logic as main.py)."""
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")


def should_register_kaspi_autosync() -> bool:
    """
    Determine if Kaspi autosync APScheduler job should be registered.

    Returns True only when:
    - PROCESS_ROLE == "scheduler" (settings or env PROCESS_ROLE, default "web")
    - AND settings.KASPI_AUTOSYNC_ENABLED is True
    - AND env ENABLE_KASPI_SYNC_RUNNER is NOT truthy (runner takes precedence)

    This ensures mutual exclusion between APScheduler job and main.py runner loop,
    and prevents dual activation in production.
    """
    import os

    # Check PROCESS_ROLE: only scheduler role can register
    role = getattr(settings, "PROCESS_ROLE", os.getenv("PROCESS_ROLE", "web")) or "web"
    if role != "scheduler":
        return False

    # Check if runner is enabled (takes precedence)
    runner_enabled = _env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))
    if runner_enabled:
        return False  # Runner takes precedence

    # Only register if explicitly enabled
    scheduler_enabled = getattr(settings, "KASPI_AUTOSYNC_ENABLED", False)
    return scheduler_enabled


# -------- Вспомогательные сущности -------- #


def _utcnow_naive() -> datetime:
    """Naive UTC для совместимости с большинством наших моделей."""
    return datetime.utcnow()


def _utcnow_aware() -> datetime:
    """Aware UTC — для сравнения, где это критично (планировщик)."""
    return datetime.now(UTC)


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


def _truncate_error(message: str | None, limit: int = _ERROR_MESSAGE_LIMIT) -> str | None:
    if not message:
        return None
    cleaned = message.strip()
    if not cleaned:
        return None
    return cleaned[:limit]


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


def _smtp_send_with_retry(
    send_fn: Callable[[], str | None],
    *,
    retries: int = 2,
    base_delay: float = 0.7,
) -> str | None:
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return send_fn()
        except (TimeoutError, smtplib.SMTPException, OSError) as e:
            last_err = e
            if attempt >= retries:
                break
            sleep_s = base_delay * (2**attempt)
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
_JOB_ID_KASPI_AUTOSYNC = "kaspi_autosync"


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
        claim = (
            update(Message)
            .where(Message.id == message_id, Message.status == MessageStatus.PENDING)
            .values(status=MessageStatus.SENDING)
        )
        result = db.execute(claim)
        if not result.rowcount:
            logger.info("Message %s уже обработан или отсутствует", message_id)
            return

        db.commit()

        message: Message | None = db.query(Message).filter(Message.id == message_id).first()
        if not message:
            logger.error("Message %s не найден после claim", message_id)
            return

        recipient = message.recipient
        subject = f"Campaign: {message.campaign.title}" if message.campaign else "Campaign"
        body = message.content or ""

        msg = _build_email(smtp, recipient, subject, body)
        logger.info("Отправка message_id=%s -> %s", message.id, recipient)

        try:
            provider_id = _smtp_send_with_retry(lambda: _send_via_smtp(smtp, msg))
            message.status = MessageStatus.SENT
            message.sent_at = _utcnow_naive()
            message.error_message = None
            if provider_id:
                message.provider_message_id = str(provider_id)
            logger.info("Сообщение %s успешно отправлено", message.id)
            db.commit()
        except Exception as e:
            logger.error("Ошибка отправки message_id=%s: %s", message.id, e)
            message.status = MessageStatus.FAILED
            message.error_message = _truncate_error(f"{type(e).__name__}: {e}")
            db.commit()


def _schedule_message_send(message_id: int) -> bool:
    """
    Постановка задачи на немедленную отправку конкретного сообщения.
    """
    job_id = f"send_message_{message_id}"
    get_job = getattr(scheduler, "get_job", None)
    if callable(get_job) and get_job(job_id) is not None:
        logger.info("Запланированная отправка уже существует message_id=%s (job_id=%s)", message_id, job_id)
        return False
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
    return True


def _schedule_pending_messages_for_campaigns(campaign_ids: list[int]) -> int:
    if not campaign_ids:
        return 0

    scheduled = 0
    with db_session() as db:
        rows = (
            db.query(Message.id)
            .filter(Message.campaign_id.in_(campaign_ids), Message.status == MessageStatus.PENDING)
            .all()
        )
        for (message_id,) in rows:
            if _schedule_message_send(message_id):
                scheduled += 1
    return scheduled


def process_scheduled_campaigns() -> None:
    """
    Запускает конвейер кампаний:
      - ставит due кампании в очередь
      - обрабатывает QUEUED кампании
    """
    now = _utcnow_naive()
    logger.info("Проверка кампаний к отправке (%s)", now.isoformat())
    enqueue_summary = enqueue_due_campaigns_sync(now=_utcnow_aware())
    processed = campaign_processing.process_campaign_queue_once_sync()
    processed_ids = [
        item["campaign_id"] for item in processed if item.get("status") == CampaignProcessingStatus.DONE.value
    ]
    scheduled = _schedule_pending_messages_for_campaigns(processed_ids)
    logger.info(
        "Campaign pipeline tick: queued=%s skipped=%s processed=%s scheduled=%s",
        enqueue_summary.get("queued"),
        enqueue_summary.get("skipped"),
        len(processed),
        scheduled,
    )


# -------- Публичные сервисные функции воркера -------- #


def enqueue_campaign(campaign_id: int) -> dict[str, int]:
    """
    Принудительно поставить в очередь PENDING-сообщения указанной кампании.
    Удобно дергать из админки/скриптов.
    """
    enqueued = 0
    failed = 0
    with db_session() as db:
        campaign: Campaign | None = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} не найдена")

        messages = (
            db.query(Message).filter(Message.campaign_id == campaign_id, Message.status == MessageStatus.PENDING).all()
        )
        for m in messages:
            try:
                if _schedule_message_send(m.id):
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
      - job kaspi_autosync (если enabled) с настраиваемым интервалом
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
        coalesce=True,  # слить пропущенные запуски в один
        misfire_grace_time=60,  # допуск по пропуску
    )

    # Kaspi auto-sync job (mutual exclusion with runner)
    if should_register_kaspi_autosync():
        try:
            from app.worker.kaspi_autosync import run_kaspi_autosync

            interval_minutes = getattr(settings, "KASPI_AUTOSYNC_INTERVAL_MINUTES", 15)
            scheduler.add_job(
                run_kaspi_autosync,
                trigger=IntervalTrigger(minutes=interval_minutes),
                id=_JOB_ID_KASPI_AUTOSYNC,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,  # 5 минут допуска
            )
            logger.info(
                "Kaspi auto-sync job добавлен (интервал=%d мин, concurrency=%d)",
                interval_minutes,
                getattr(settings, "KASPI_AUTOSYNC_MAX_CONCURRENCY", 3),
            )
        except ImportError as e:
            logger.warning("Не удалось загрузить kaspi_autosync: %s", e)
    else:
        import os

        runner_enabled = _env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))
        if runner_enabled:
            logger.info("Kaspi autosync APScheduler job skipped: runner enabled (ENABLE_KASPI_SYNC_RUNNER=1)")
        elif not getattr(settings, "KASPI_AUTOSYNC_ENABLED", False):
            logger.debug("Kaspi autosync APScheduler job skipped: KASPI_AUTOSYNC_ENABLED=False")

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

    # Также перезагружаем Kaspi auto-sync (mutual exclusion with runner)
    try:
        scheduler.remove_job(_JOB_ID_KASPI_AUTOSYNC)
    except Exception:
        pass

    if should_register_kaspi_autosync():
        try:
            from app.worker.kaspi_autosync import run_kaspi_autosync

            interval_minutes = getattr(settings, "KASPI_AUTOSYNC_INTERVAL_MINUTES", 15)
            scheduler.add_job(
                run_kaspi_autosync,
                trigger=IntervalTrigger(minutes=interval_minutes),
                id=_JOB_ID_KASPI_AUTOSYNC,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
            )
        except ImportError as e:
            logger.warning("Не удалось загрузить kaspi_autosync: %s", e)
    else:
        import os

        runner_enabled = _env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))
        if runner_enabled:
            logger.info("Kaspi autosync APScheduler job reload skipped: runner enabled")

    logger.info("Базовые задачи планировщика пересозданы")


def get_status() -> dict[str, str]:
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
