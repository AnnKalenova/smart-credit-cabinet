"""Контракты API (Pydantic). Из них же генерируется OpenAPI/Swagger."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _has_nul(value: Any, depth: int = 0) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if depth > 8:
        return False
    if isinstance(value, dict):
        return any(_has_nul(k, depth + 1) or _has_nul(v, depth + 1) for k, v in value.items())
    if isinstance(value, list | tuple):
        return any(_has_nul(v, depth + 1) for v in value)
    return False


class InModel(BaseModel):
    """Входные данные. Символ NUL (\\x00) PostgreSQL хранить не умеет — такой ввод получает 422, а не 500."""

    @model_validator(mode="before")
    @classmethod
    def reject_nul(cls, data: Any) -> Any:
        if _has_nul(data):
            raise ValueError("недопустимый символ в тексте")
        return data


def _not_blank(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("не может быть пустым")
    return value


# --------------------------------------------------------------------------- auth


# Ограничения длины и диапазонов совпадают с колонками БД: некорректный ввод получает 422, а не 500
MAX_AMOUNT = 10_000_000_000  # 10 млрд ₽ — заведомо больше любого продукта
MAX_REVENUE = 1_000_000_000_000


def _not_bool(value: Any) -> Any:
    # JSON true/false не превращается в 1/0 рубль или месяц
    if isinstance(value, bool):
        raise ValueError("должно быть числом")
    return value


# Field — первым: так ограничения попадают в OpenAPI стандартными minimum/maximum
Money = Annotated[float, Field(ge=1, le=MAX_AMOUNT, allow_inf_nan=False), BeforeValidator(_not_bool)]
Term = Annotated[int, Field(gt=0, le=120), BeforeValidator(_not_bool)]
Code = Annotated[str, Field(min_length=1, max_length=32)]


class LoginIn(InModel):
    login: str = Field(max_length=64, examples=["smirnov"])
    password: str = Field(max_length=128, examples=["demo1234"])


class UserOut(ORMModel):
    id: uuid.UUID
    login: str
    full_name: str
    role: str
    position_title: str | None = None


class CompanyBrief(BaseModel):
    id: uuid.UUID
    inn: str
    short_name: str
    kind: str
    position: str
    position_label: str
    can_sign: bool


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MeOut(BaseModel):
    user: UserOut
    companies: list[CompanyBrief]


class DemoAccount(BaseModel):
    login: str
    password: str
    full_name: str
    role: str
    title: str
    company: str | None = None
    scenario: str
    scenario_title: str | None = None
    scenario_hint: str | None = None


# --------------------------------------------------------------------------- справочники и компания


class ProductOut(ORMModel):
    code: str
    name: str
    tagline: str
    description: str
    purposes: list[str]
    min_amount: float
    max_amount: float
    min_term: int
    max_term: int
    base_spread: float
    repayment_type: str
    collateral: str
    features: list[str]
    auto_decision_limit: float


class DictionariesOut(BaseModel):
    purposes: list[dict[str, Any]]
    doc_types: dict[str, str]
    statuses: dict[str, dict[str, Any]]
    key_rate: float
    key_rate_note: str
    demo_mode: bool


class MemberOut(BaseModel):
    full_name: str
    position_label: str
    can_sign: bool


class CompanyProfileOut(BaseModel):
    id: uuid.UUID
    inn: str
    ogrn: str
    kind: str
    short_name: str
    full_name: str
    okved_code: str
    okved_name: str
    registration_date: date
    legal_address: str
    client_since: date
    segment: str
    settlement_account: str
    headcount: int | None
    members: list[MemberOut]
    turnover: dict[str, Any]
    preapproved: dict[str, Any] | None
    revenue_estimate: float


class MatchIn(InModel):
    company_id: uuid.UUID
    purpose: Code
    amount: Money
    term_months: Term
    has_collateral: bool | None = Field(default=None, description="Залог: не указан — как требует продукт")
    owner_guarantee: bool = Field(default=False, description="Поручительство собственника")


class MatchOut(BaseModel):
    key_rate: float
    results: list[dict[str, Any]]


# --------------------------------------------------------------------------- заявка


class ApplicationCreate(InModel):
    company_id: uuid.UUID
    product_code: Code
    amount: Money
    term_months: Term
    purpose: Code
    purpose_details: str | None = Field(default=None, max_length=2000)


# Поля, которые нельзя очистить: в БД они NOT NULL
REQUIRED_DRAFT_FIELDS = ("product_code", "amount", "term_months", "purpose", "has_collateral", "owner_guarantee")


class ApplicationUpdate(InModel):
    product_code: Code | None = None
    amount: Money | None = None
    term_months: Term | None = None
    purpose: Code | None = None
    purpose_details: str | None = Field(default=None, max_length=2000)
    declared_revenue: Annotated[
        float | None, Field(ge=0, le=MAX_REVENUE, allow_inf_nan=False), BeforeValidator(_not_bool)
    ] = None
    has_collateral: bool | None = None
    collateral_description: str | None = Field(default=None, max_length=400)
    owner_guarantee: bool | None = None
    contact_name: str | None = Field(default=None, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=32)

    @field_validator(*REQUIRED_DRAFT_FIELDS, mode="after")
    @classmethod
    def no_null_for_required(cls, value: Any) -> Any:
        # значение по умолчанию (поле не передано) не проверяется — сюда попадает только явный null
        if value is None:
            raise ValueError("нельзя оставить пустым")
        return value


class DocumentOut(ORMModel):
    id: uuid.UUID
    doc_type: str
    doc_type_label: str = ""
    file_name: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime


class OfferOut(BaseModel):
    id: uuid.UUID
    product_code: str
    product_name: str
    repayment_type: str
    amount: float
    term_months: int
    rate: float
    monthly_payment: float
    total_interest: float
    conditions: list[str]
    pricing: dict[str, Any]
    is_counter: bool
    label: str
    status: str
    valid_until: datetime
    schedule_preview: list[dict[str, Any]]


class EventOut(BaseModel):
    id: int
    created_at: datetime
    event_type: str
    title: str
    actor_type: str
    actor_name: str | None
    from_status: str | None
    to_status: str | None


class InfoRequestOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    items: list[dict[str, Any]]
    message: str
    status: str
    answered_at: datetime | None
    answer_comment: str | None
    requested_by: str | None


class LoanOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    application_number: str
    company_name: str
    product_code: str
    product_name: str
    contract_number: str
    loan_account: str
    amount: float
    term_months: int
    rate: float
    monthly_payment: float
    issued_at: datetime
    first_payment_date: date
    status: str
    repayment_type: str
    schedule: list[dict[str, Any]] | None = None


class ApplicationSummaryOut(BaseModel):
    id: uuid.UUID
    number: str
    company_id: uuid.UUID
    company_name: str
    product_code: str
    product_name: str
    amount: float
    term_months: int
    purpose: str
    status: str
    status_view: dict[str, Any]
    stage_detail: str | None
    created_at: datetime
    status_changed_at: datetime
    submitted_at: datetime | None


class ApplicationOut(ApplicationSummaryOut):
    purpose_details: str | None
    declared_revenue: float | None
    has_collateral: bool
    collateral_description: str | None
    owner_guarantee: bool
    contact_name: str | None
    contact_phone: str | None
    product: ProductOut
    required_documents: list[dict[str, Any]]
    documents: list[DocumentOut]
    consents: list[dict[str, Any]]
    info_request: InfoRequestOut | None
    offers: list[OfferOut]
    decision: dict[str, Any] | None
    events: list[EventOut]
    loan: LoanOut | None
    permissions: dict[str, Any]
    review_due_at: datetime | None
    parent_id: uuid.UUID | None
    next_application: dict[str, Any] | None = Field(
        default=None, description="Новая заявка, поданная на основе этой: номер и статус"
    )
    proposed_conditions: dict[str, Any] | None = Field(
        default=None,
        description="Условия из конструктора, которые сотрудник без права подписи передал руководителю",
    )


class ConditionsIn(InModel):
    """Условия для конструктора и для новой заявки: не переданное поле берётся из исходной заявки."""

    product_code: Code | None = None
    amount: Money | None = None
    term_months: Term | None = None
    has_collateral: bool | None = None
    owner_guarantee: bool | None = None


class ReapplyIn(ConditionsIn):
    replace_current: bool = Field(
        default=False,
        description="Для заявки с предложением или на ручном рассмотрении: закрыть её (отказ от предложения "
        "или отзыв) и создать новую на этих условиях — одним действием",
    )


class SignatureRequestIn(InModel):
    conditions: ConditionsIn | None = Field(
        default=None, description="Условия из конструктора, на которых сотрудник предлагает подать новую заявку"
    )


class SimulationIn(ConditionsIn):
    with_suggestions: bool = Field(default=True, description="Посчитать подсказки «что улучшит условия»")


class SimulationOut(BaseModel):
    base: dict[str, Any] = Field(description="Условия исходной заявки")
    result: dict[str, Any] = Field(description="Решение банка при заданных условиях")
    suggestions: list[dict[str, Any]] = Field(description="Что изменить, чтобы условия стали лучше")
    products: list[dict[str, Any]] = Field(description="Продукты под цель заявки: суммы, сроки, залог")


class SignChallengeIn(InModel):
    purpose: Literal["CONSENTS", "CONTRACT"]


class SignChallengeOut(BaseModel):
    challenge_id: uuid.UUID
    expires_at: datetime
    demo_code: str | None = Field(
        default=None,
        description="Только в демо-режиме: код показывается в интерфейсе, т.к. SMS и почта не используются в сценарии",
    )
    documents: list[str]


class SignConfirmIn(InModel):
    challenge_id: uuid.UUID
    code: str = Field(min_length=4, max_length=8)


class InfoResponseIn(InModel):
    comment: str | None = Field(default=None, max_length=4000)
    fields: ApplicationUpdate | None = None


class MessageIn(InModel):
    text: Annotated[str, Field(max_length=4000), AfterValidator(_not_blank)]


class MessageOut(BaseModel):
    id: int
    created_at: datetime
    author_role: str
    author_name: str | None
    text: str


class NotificationOut(ORMModel):
    id: int
    created_at: datetime
    application_id: uuid.UUID | None
    title: str
    body: str
    is_read: bool


class NotificationsOut(BaseModel):
    unread: int
    items: list[NotificationOut]


class ContractOut(BaseModel):
    contract_number: str
    company: dict[str, Any]
    offer: OfferOut
    schedule: list[dict[str, Any]]
    documents: list[dict[str, str]]
    signer: dict[str, Any] | None


# --------------------------------------------------------------------------- банк


class BankApplicationRow(BaseModel):
    id: uuid.UUID
    number: str
    company_name: str
    company_inn: str
    company_kind: str
    product_code: str
    product_name: str
    amount: float
    term_months: int
    status: str
    status_label: str
    stage_detail: str | None
    grade: str | None
    score: int | None
    assignee: str | None
    submitted_at: datetime | None
    status_changed_at: datetime
    review_due_at: datetime | None
    sla_overdue: bool
    triggers: list[str]
    awaiting_reply: bool = Field(
        default=False, description="Последнее сообщение по заявке — от клиента, он ждёт ответа"
    )


class BankOfferIn(InModel):
    product_code: Code
    amount: Money
    term_months: Term
    rate: Annotated[float | None, Field(gt=0, lt=100, allow_inf_nan=False), BeforeValidator(_not_bool)] = None
    label: str | None = Field(default=None, max_length=120)


class BankInfoItemIn(InModel):
    type: Literal["DOCUMENT", "COMMENT"]
    doc_type: Code | None = None
    label: Annotated[str, Field(max_length=300), AfterValidator(_not_blank)]


class BankDecisionIn(InModel):
    action: Literal["APPROVE", "REQUEST_INFO", "DECLINE"]
    offers: list[BankOfferIn] = Field(default=[], max_length=3)
    info_items: list[BankInfoItemIn] = Field(default=[], max_length=10)
    reason: str | None = Field(default=None, max_length=500)
    message_to_client: str | None = Field(default=None, max_length=4000)
    comment_internal: str | None = Field(default=None, max_length=4000)


class SettingOut(BaseModel):
    key: str
    value: Any
    description: str
    updated_at: datetime | None
    updated_by: str | None


class SettingIn(InModel):
    value: Any


class FaultIn(InModel):
    service: Literal["EGRUL", "FNS", "BKI", "ABS"]
    mode: Literal["ok", "down", "slow"]
    duration_seconds: int = Field(default=120, ge=10, le=3600)
