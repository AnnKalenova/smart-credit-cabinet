"""Воркер кредитного конвейера.

Забирает этапы из очереди в PostgreSQL через SELECT … FOR UPDATE SKIP LOCKED, поэтому можно
запускать несколько воркеров параллельно (docker compose up --scale worker=3) без дублей.
При ошибке внешней системы этап повторяется с экспоненциальной задержкой; если попытки
исчерпаны — заявка передаётся специалисту, а не «зависает».

Запуск: python -m app.worker.runner
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.domain.enums import JobStatus
from app.integrations.client import SERVICE_TITLES, IntegrationError
from app.logging_setup import setup_logging
from app.models import Application, PipelineJob
from app.services.pipeline import FAILURE_HANDLERS, HANDLERS, expire_offers
from app.services.workflow import utcnow

settings = get_settings()
log = logging.getLogger("worker")
_stop = False


def _handle_signal(signum, frame):  # noqa: ARG001
    global _stop
    _stop = True
    log.info("получен сигнал остановки, завершаем текущий этап")


def claim_job(db: Session) -> int | None:
    job = db.scalar(
        select(PipelineJob)
        .where(PipelineJob.status == JobStatus.PENDING, PipelineJob.run_after <= utcnow())
        .order_by(PipelineJob.run_after)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        db.rollback()
        return None
    job.status = JobStatus.RUNNING
    job.locked_by = settings.worker_id
    job.locked_at = utcnow()
    job.attempts += 1
    db.commit()
    return job.id


def backoff_seconds(attempt: int) -> float:
    return min(settings.integration_backoff_base_seconds * 2 ** (attempt - 1), 30.0)


def process_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(PipelineJob, job_id)
        app = db.get(Application, job.application_id)
        started = time.perf_counter()
        try:
            HANDLERS[job.stage](db, app, job)
            job.status = JobStatus.DONE
            job.finished_at = utcnow()
            db.commit()
            log.info(
                "этап выполнен",
                extra={
                    "job_id": job.id,
                    "stage": job.stage,
                    "application": app.number,
                    "ms": int((time.perf_counter() - started) * 1000),
                },
            )
        except Exception as e:  # noqa: BLE001 — любая ошибка этапа обрабатывается одинаково
            db.rollback()
            _on_error(db, job_id, e)


def _on_error(db: Session, job_id: int, error: Exception) -> None:
    job = db.get(PipelineJob, job_id)
    app = db.get(Application, job.application_id)
    retryable = getattr(error, "retryable", True)
    job.last_error = str(error)[:500]
    if retryable and job.attempts < job.max_attempts:
        delay = backoff_seconds(job.attempts)
        job.status = JobStatus.PENDING
        job.run_after = utcnow() + timedelta(seconds=delay)
        if isinstance(error, IntegrationError):
            title = SERVICE_TITLES.get(error.service, error.service)
            app.stage_detail = (
                f"{title} временно не отвечает — повторим запрос через {delay:.0f} с "
                f"(попытка {job.attempts + 1} из {job.max_attempts})"
            )
        log.warning(
            "ошибка этапа, повтор",
            extra={
                "job_id": job.id,
                "stage": job.stage,
                "application": app.number,
                "error": str(error),
                "retry_in": delay,
            },
        )
        db.commit()
        return
    job.status = JobStatus.FAILED
    job.finished_at = utcnow()
    log.error(
        "этап не выполнен", extra={"job_id": job.id, "stage": job.stage, "application": app.number, "error": str(error)}
    )
    handler = FAILURE_HANDLERS.get(job.stage)
    if handler:
        handler(db, app, error)
    else:
        app.stage_detail = "Произошла техническая ошибка — специалист банка уже разбирается"
    db.commit()


class Heartbeat:
    """Раз в несколько секунд обновляет файл-пульс: воркер жив и достучался до очереди в БД."""

    def __init__(self, path: str, every_seconds: float = 5.0):
        self.path = Path(path)
        self.every = every_seconds
        self.last = 0.0

    def beat(self) -> None:
        now = time.monotonic()
        if now - self.last < self.every:
            return
        self.last = now
        try:
            self.path.touch()
        except OSError:
            log.warning("не удалось обновить файл-пульс", extra={"path": str(self.path)})


def housekeeping() -> None:
    with SessionLocal() as db:
        # «Зависшие» этапы упавшего воркера возвращаются в очередь
        db.execute(
            update(PipelineJob)
            .where(PipelineJob.status == JobStatus.RUNNING, PipelineJob.locked_at < utcnow() - timedelta(minutes=5))
            .values(status=JobStatus.PENDING, locked_by=None)
        )
        expired = expire_offers(db)
        db.commit()
        if expired:
            log.info("истекли предложения", extra={"count": expired})


def run() -> None:
    setup_logging()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    log.info("воркер запущен", extra={"worker_id": settings.worker_id})
    heartbeat = Heartbeat(settings.worker_heartbeat_file)
    last_housekeeping = 0.0
    while not _stop:
        if time.monotonic() - last_housekeeping > 30:
            try:
                housekeeping()
            except Exception:  # noqa: BLE001
                log.exception("ошибка обслуживания очереди")
            last_housekeeping = time.monotonic()
        try:
            with SessionLocal() as db:
                job_id = claim_job(db)
        except Exception:  # noqa: BLE001 — например, БД ещё не поднялась
            log.exception("не удалось получить задачу из очереди")
            time.sleep(2)
            continue
        heartbeat.beat()
        if job_id is None:
            time.sleep(settings.worker_poll_interval_seconds)
            continue
        process_job(job_id)
    log.info("воркер остановлен")


if __name__ == "__main__":
    run()
