"""Контракты API (Pydantic). Из них же генерируется OpenAPI/Swagger."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- auth


# Ограничения длины и диапазонов совпадают с колонками БД: некорректный ввод получает 422, а не 500
MAX_AMOUNT = 10_000_000_000  # 10 млрд ₽ — заведомо больше любого продукта
MAX_REVENUE = 1_000_000_000_000
Money = Annotated[float, Field(gt=0, le=MAX_AMOUNT, allow_inf_nan=False)]
Term = Annotated[int, Field(gt=0, le=120)]
Code = Annotated[str, Field(min_length=1, max_length=32)]


class LoginIn(BaseModel):
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


class MatchIn(BaseModel):
    company_id: uuid.UUID
    purpose: Code
    amount: Money
    term_months: Term


class MatchOut(BaseModel):
    key_rate: float
    results: list[dict[str, Any]]


# --------------------------------------------------------------------------- заявка


class ApplicationCreate(BaseModel):
    company_id: uuid.UUID
    product_code: Code
    amount: Money
    term_months: Term
    purpose: Code
    purpose_details: str | None = Field(default=None, max_length=2000)
    parent_id: uuid.UUID | None = None


# Поля, которые нельзя очистить: в БД они NOT NULL
REQUIRED_DRAFT_FIELDS = {
    "product_code": "продукт",
    "amount": "сумма",
    "term_months": "срок",
    "purpose": "цель",
    "has_collateral": "залог",
    "owner_guarantee": "поручительство",
}


class ApplicationUpdate(BaseModel):
    product_code: Code | None = None
    amount: Money | None = None
    term_months: Term | None = None
    purpose: Code | None = None
    purpose_details: str | None = Field(default=None, max_length=2000)
    declared_revenue: float | None = Field(default=None, ge=0, le=MAX_REVENUE, allow_inf_nan=False)
    has_collateral: bool | None = None
    collateral_description: str | None = Field(default=None, max_length=400)
    owner_guarantee: bool | None = None
    contact_name: str | None = Field(default=None, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def no_null_for_required(self) -> ApplicationUpdate:
        cleared = [
            label
            for name, label in REQUIRED_DRAFT_FIELDS.items()
            if name in self.model_fields_set and getattr(self, name) is None
        ]
        if cleared:
            raise ValueError("нельзя оставить пустым: " + ", ".join(cleared))
        return self


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


class SignChallengeIn(BaseModel):
    purpose: Literal["CONSENTS", "CONTRACT"]


class SignChallengeOut(BaseModel):
    challenge_id: uuid.UUID
    expires_at: datetime
    demo_code: str | None = Field(
        default=None,
        description="Только в демо-режиме: код показывается в интерфейсе, т.к. SMS и почта не используются в сценарии",
    )
    documents: list[str]


class SignConfirmIn(BaseModel):
    challenge_id: uuid.UUID
    code: str = Field(min_length=4, max_length=8)


class InfoResponseIn(BaseModel):
    comment: str | None = Field(default=None, max_length=4000)
    fields: ApplicationUpdate | None = None


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


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


class BankOfferIn(BaseModel):
    product_code: Code
    amount: Money
    term_months: Term
    rate: float | None = Field(default=None, gt=0, lt=100, allow_inf_nan=False)
    label: str | None = Field(default=None, max_length=120)


class BankInfoItemIn(BaseModel):
    type: Literal["DOCUMENT", "COMMENT"]
    doc_type: Code | None = None
    label: str = Field(min_length=1, max_length=300)


class BankDecisionIn(BaseModel):
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


class SettingIn(BaseModel):
    value: Any


class FaultIn(BaseModel):
    service: Literal["EGRUL", "FNS", "BKI", "ABS"]
    mode: Literal["ok", "down", "slow"]
    duration_seconds: int = Field(default=120, ge=10, le=3600)
