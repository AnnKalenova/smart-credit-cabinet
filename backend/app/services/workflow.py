"""Общие операции над заявкой: переходы статусов, аудит, уведомления, постановка этапов в очередь."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.enums import ActorType, AppStatus, JobStage, JobStatus
from app.domain.statuses import CLIENT_STATUS, assert_transition
from app.models import (
    Application,
    ApplicationEvent,
    CompanyMember,
    Notification,
    PipelineJob,
    Setting,
)

settings = get_settings()

# Статусы, о которых клиенту приходит уведомление в кабинете
NOTIFY_STATUSES = {
    AppStatus.NEEDS_INFO,
    AppStatus.MANUAL_REVIEW,
    AppStatus.OFFER_READY,
    AppStatus.SIGNING,
    AppStatus.ISSUED,
    AppStatus.DECLINED,
    AppStatus.EXPIRED,
}

DEFAULT_SETTINGS: dict[str, tuple[Any, str]] = {
    "key_rate": (settings.default_key_rate, "Ключевая ставка Банка России, % годовых"),
    "key_rate_note": (
        "Решение Банка России от 24.07.2026, сохранена 11.09.2026",
        "Источник и дата ключевой ставки",
    ),
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def get_setting(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULT_SETTINGS[key][0]
    return row.value


def key_rate(db: Session) -> float:
    return float(get_setting(db, "key_rate"))


def next_application_number(db: Session) -> str:
    seq = db.execute(text("SELECT nextval('application_number_seq')")).scalar_one()
    return f"{utcnow().year}-{seq:06d}"


def add_event(
    db: Session,
    app: Application,
    event_type: str,
    title: str,
    *,
    actor_type: str = ActorType.SYSTEM,
    actor_id: uuid.UUID | None = None,
    payload: dict | None = None,
    client_visible: bool = True,
    from_status: str | None = None,
    to_status: str | None = None,
) -> ApplicationEvent:
    ev = ApplicationEvent(
        application_id=app.id,
        event_type=event_type,
        title=title,
        actor_type=actor_type,
        actor_id=actor_id,
        payload=payload or {},
        client_visible=client_visible,
        from_status=from_status,
        to_status=to_status,
    )
    db.add(ev)
    return ev


def notify_company(db: Session, app: Application, title: str, body: str, *, signers_only: bool = False) -> None:
    q = select(CompanyMember).where(CompanyMember.company_id == app.company_id)
    if signers_only:
        q = q.where(CompanyMember.can_sign.is_(True))
    for member in db.scalars(q):
        db.add(Notification(user_id=member.user_id, application_id=app.id, title=title, body=body))


def transition(
    db: Session,
    app: Application,
    to_status: str,
    *,
    actor_type: str = ActorType.SYSTEM,
    actor_id: uuid.UUID | None = None,
    title: str | None = None,
    payload: dict | None = None,
    stage_detail: str | None = None,
    client_visible: bool = True,
) -> None:
    from_status = app.status
    assert_transition(from_status, to_status)
    app.status = to_status
    app.status_changed_at = utcnow()
    app.stage_detail = stage_detail
    client_title = CLIENT_STATUS[AppStatus(to_status)][0]
    add_event(
        db,
        app,
        "STATUS_CHANGED",
        title or client_title,
        actor_type=actor_type,
        actor_id=actor_id,
        payload=payload,
        client_visible=client_visible,
        from_status=from_status,
        to_status=to_status,
    )
    if AppStatus(to_status) in NOTIFY_STATUSES:
        notify_company(
            db, app, f"Заявка № {app.number}: {client_title.lower()}", title or CLIENT_STATUS[AppStatus(to_status)][2]
        )


def enqueue(
    db: Session,
    app: Application,
    stage: str,
    *,
    delay_seconds: float | None = None,
    max_attempts: int | None = None,
) -> PipelineJob:
    delay = settings.pipeline_stage_delay_seconds if delay_seconds is None else delay_seconds
    job = PipelineJob(
        application_id=app.id,
        stage=stage,
        status=JobStatus.PENDING,
        attempts=0,
        max_attempts=max_attempts or (30 if stage == JobStage.DISBURSEMENT else settings.integration_max_attempts),
        run_after=utcnow() + timedelta(seconds=delay),
    )
    db.add(job)
    return job
