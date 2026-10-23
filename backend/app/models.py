"""ORM-модель данных кредитного конвейера (PostgreSQL)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --------------------------------------------------------------------------- пользователи и клиенты


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    position_title: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    memberships: Mapped[list[CompanyMember]] = relationship(back_populates="user")


class Company(TimestampMixin, Base):
    """Действующий клиент банка (ИП или юрлицо). Базовые сведения уже известны банку."""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    inn: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    ogrn: Mapped[str] = mapped_column(String(15), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    short_name: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name: Mapped[str] = mapped_column(String(400), nullable=False)
    okved_code: Mapped[str] = mapped_column(String(16), nullable=False)
    okved_name: Mapped[str] = mapped_column(String(300), nullable=False)
    registration_date: Mapped[date] = mapped_column(Date, nullable=False)
    legal_address: Mapped[str] = mapped_column(String(400), nullable=False)
    client_since: Mapped[date] = mapped_column(Date, nullable=False)
    segment: Mapped[str] = mapped_column(String(32), nullable=False)  # микро / малое
    settlement_account: Mapped[str] = mapped_column(String(20), nullable=False)
    headcount: Mapped[int | None] = mapped_column(Integer)

    members: Mapped[list[CompanyMember]] = relationship(back_populates="company")
    turnovers: Mapped[list[AccountTurnover]] = relationship(back_populates="company", order_by="AccountTurnover.month")


class CompanyMember(Base):
    """Связь пользователя с компанией и его полномочия (кто может подписывать)."""

    __tablename__ = "company_members"
    __table_args__ = (UniqueConstraint("company_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    position: Mapped[str] = mapped_column(String(32), nullable=False)
    can_sign: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    company: Mapped[Company] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class AccountTurnover(Base):
    """Внутренние данные банка: обороты по расчётному счёту клиента помесячно."""

    __tablename__ = "account_turnovers"
    __table_args__ = (UniqueConstraint("company_id", "month"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    credit_turnover: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # поступления
    debit_turnover: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # списания
    avg_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    company: Mapped[Company] = relationship(back_populates="turnovers")


# --------------------------------------------------------------------------- продукты и настройки


class Product(Base):
    __tablename__ = "products"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    tagline: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    purposes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    min_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    max_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    min_term: Mapped[int] = mapped_column(Integer, nullable=False)
    max_term: Mapped[int] = mapped_column(Integer, nullable=False)
    base_spread: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)  # надбавка к ключевой ставке
    max_spread: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    turnover_multiplier: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    auto_decision_limit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    repayment_type: Mapped[str] = mapped_column(String(16), nullable=False)  # ANNUITY / REVOLVING
    collateral: Mapped[str] = mapped_column(String(16), nullable=False)  # NONE / OPTIONAL / REQUIRED
    doc_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    features: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Setting(Base):
    """Параметры кредитной политики, которые риск-менеджер меняет без релиза (например, ключевая ставка)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by: Mapped[str | None] = mapped_column(String(200))


# --------------------------------------------------------------------------- заявка


class Application(TimestampMixin, Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("ix_applications_status", "status"),
        Index("ix_applications_company", "company_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id"))

    product_code: Mapped[str] = mapped_column(ForeignKey("products.code"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose_details: Mapped[str | None] = mapped_column(Text)
    declared_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # годовая выручка со слов клиента
    has_collateral: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    collateral_description: Mapped[str | None] = mapped_column(String(400))
    owner_guarantee: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(32))

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stage_detail: Mapped[str | None] = mapped_column(String(300))  # что происходит прямо сейчас
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # SLA ручного рассмотрения
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    info_rounds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}  # оптимистичная блокировка

    company: Mapped[Company] = relationship()
    product: Mapped[Product] = relationship()
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])
    documents: Mapped[list[ApplicationDocument]] = relationship(
        back_populates="application", order_by="ApplicationDocument.uploaded_at"
    )


class ApplicationDocument(Base):
    __tablename__ = "application_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    application: Mapped[Application] = relationship(back_populates="documents")


class Consent(Base):
    __tablename__ = "consents"
    # одно согласие каждого вида на заявку: повторная подпись параллельным запросом не создаст дубль
    __table_args__ = (UniqueConstraint("application_id", "consent_type", name="uq_consents_application_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    consent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    signed_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signature_ref: Mapped[str] = mapped_column(String(64), nullable=False)


class SignChallenge(TimestampMixin, Base):
    """Код подтверждения простой электронной подписи внутри продукта (без SMS)."""

    __tablename__ = "sign_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- обработка в банке


class EnrichmentResult(Base):
    """Ответ внешнего или внутреннего источника, использованный в оценке."""

    __tablename__ = "enrichment_results"
    __table_args__ = (UniqueConstraint("application_id", "source"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # EGRUL / FNS / BKI / INTERNAL
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntegrationCall(TimestampMixin, Base):
    """Журнал вызовов внешних систем: для аналитика и для мониторинга."""

    __tablename__ = "integration_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id"))
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # SUCCESS / ERROR / TIMEOUT / INVALID
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[str | None] = mapped_column(String(500))
    correlation_id: Mapped[str | None] = mapped_column(String(64))


class CheckResult(TimestampMixin, Base):
    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # OK / INFO / WARN / STOP / MISSING
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)


class ScoringResult(TimestampMixin, Base):
    __tablename__ = "scoring_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    grade: Mapped[str] = mapped_column(String(2), nullable=False)
    pd: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class Decision(TimestampMixin, Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # AUTO / MANUAL
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # расчёт лимита, ставки, правила
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    comment_internal: Mapped[str | None] = mapped_column(Text)
    message_to_client: Mapped[str | None] = mapped_column(Text)

    decided_by: Mapped[User | None] = relationship()


class Offer(TimestampMixin, Base):
    __tablename__ = "offers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decisions.id"), nullable=False)
    product_code: Mapped[str] = mapped_column(ForeignKey("products.code"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    monthly_payment: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total_interest: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    conditions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    pricing: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_counter: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # дата подписания договора: от неё строится график — подписанный и в выданном кредите один и тот же
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship()


class InfoRequest(TimestampMixin, Base):
    """Запрос банка на уточнение данных. Отвечают внутри той же заявки."""

    __tablename__ = "info_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answer_comment: Mapped[str | None] = mapped_column(Text)


class Message(TimestampMixin, Base):
    """Переписка клиента и банка внутри заявки (вместо email и мессенджеров)."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    author_role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    author: Mapped[User | None] = relationship()


class Loan(TimestampMixin, Base):
    __tablename__ = "loans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), unique=True, nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    product_code: Mapped[str] = mapped_column(ForeignKey("products.code"), nullable=False)
    contract_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    loan_account: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    monthly_payment: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    schedule: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    product: Mapped[Product] = relationship()


class ApplicationEvent(TimestampMixin, Base):
    """Журнал аудита: все изменения заявки, кто и когда их сделал."""

    __tablename__ = "application_events"
    __table_args__ = (Index("ix_events_application", "application_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    client_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    actor: Mapped[User | None] = relationship()


class LoginAttempt(TimestampMixin, Base):
    """Попытки входа: защита от подбора пароля и журнал входов."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_login", "login", "created_at"),
        Index("ix_login_attempts_ip", "ip", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String(64), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))


class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user", "user_id", "is_read"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PipelineJob(TimestampMixin, Base):
    """Очередь этапов конвейера в PostgreSQL (FOR UPDATE SKIP LOCKED): воркеры масштабируются горизонтально."""

    __tablename__ = "pipeline_jobs"
    __table_args__ = (Index("ix_jobs_pick", "status", "run_after"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
