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

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.config import get_settings
from app.db import SessionLocal
from app.domain.enums import JobStatus
from app.integrations.client import SERVICE_TITLES, IntegrationError
from app.logging_setup import setup_logging
from app.models import Application, PipelineJob
from app.services.pipeline import (
    FAILURE_HANDLERS,
    HANDLERS,
    expire_offers,
    recover_stuck_applications,
    requeue_abandoned_jobs,
)
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
            # Заявку могли параллельно изменить (клиент отозвал, специалист взял в работу), пока этап ждал
            # ответа внешней системы: тогда сохранение ошибки повторяется на свежих данных
            for attempt in range(3):
                try:
                    _on_error(db, job_id, e)
                    return
                except StaleDataError:
                    db.rollback()
                    db.expire_all()
                    log.info("заявка изменилась при обработке ошибки этапа, повторяем", extra={"job_id": job_id})
                    time.sleep(0.05 * (attempt + 1))
            log.error("не удалось сохранить ошибку этапа, этап вернётся в очередь", extra={"job_id": job_id})


def _on_error(db: Session, job_id: int, error: Exception) -> None:
    job = db.get(PipelineJob, job_id)
    app = db.get(Application, job.application_id)
    db.refresh(app)  # свежая версия заявки: оптимистичная блокировка сравнивает именно её
    retryable = getattr(error, "retryable", True)
    job.last_error = str(error)[:500]
    if retryable and job.attempts < job.max_attempts:
        delay = backoff_seconds(job.attempts)
        job.status = JobStatus.PENDING
        job.run_after = utcnow() + timedelta(seconds=delay)
        if isinstance(error, IntegrationError) and app.status == job.stage:
            # подсказка клиенту — только пока заявка на этом этапе (её могли уже отозвать)
            title = SERVICE_TITLES.get(error.service, error.service)
            what = "отвечает без нужных сведений" if error.invalid else "временно не отвечает"
            app.stage_detail = (
                f"{title} {what} — повторим запрос через {delay:.0f} с "
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
    # проверка полноты, сбор сведений, скоринг и решение передают заявку специалисту, выдача ждёт его повтора
    FAILURE_HANDLERS[job.stage](db, app, error)
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
        # «Зависшие» этапы упавшего воркера возвращаются в очередь, а те, на которых воркеры падали
        # уже все отведённые попытки, — передаются специалисту (иначе такой этап крутился бы вечно)
        requeued, abandoned = requeue_abandoned_jobs(db)
        if requeued:
            log.warning("брошенные этапы возвращены в очередь", extra={"count": requeued})
        if abandoned:
            log.error("этапы, ронявшие воркер, переданы специалисту", extra={"count": abandoned})
        expired = expire_offers(db)
        db.commit()
        if expired:
            log.info("истекли предложения", extra={"count": expired})
        recovered = recover_stuck_applications(db)
        if recovered:
            log.warning("заявки после сбоя этапа переданы специалисту", extra={"count": recovered})


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
        try:
            process_job(job_id)
        except Exception:  # noqa: BLE001 — воркер не должен останавливаться из-за одной заявки
            # этап остаётся RUNNING и через 5 минут вернётся в очередь (housekeeping)
            log.exception("необработанная ошибка этапа", extra={"job_id": job_id})
    log.info("воркер остановлен")


if __name__ == "__main__":
    run()
