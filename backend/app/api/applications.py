"""Заявка глазами клиента: черновик → согласия → отправка → уточнения → предложение → подпись."""

import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.config import get_settings
from app.db import get_db
from app.domain.enums import (
    CONSENT_TEXTS,
    DOC_TYPES,
    POSITION_LABELS,
    PURPOSES,
    ActorType,
    AppStatus,
    ConsentType,
    InfoRequestStatus,
    JobStage,
    OfferStatus,
    Role,
    SignPurpose,
)
from app.domain.products import required_documents
from app.domain.statuses import CLIENT_CANCELLABLE
from app.models import (
    Application,
    ApplicationDocument,
    Company,
    CompanyMember,
    Consent,
    InfoRequest,
    Message,
    Offer,
    Product,
    SignChallenge,
    User,
)
from app.schemas import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationSummaryOut,
    ApplicationUpdate,
    ContractOut,
    DocumentOut,
    InfoResponseIn,
    MessageIn,
    MessageOut,
    SignChallengeIn,
    SignChallengeOut,
    SignConfirmIn,
)
from app.security import get_client_application, hash_code, require_company_member, require_roles
from app.services import assessment, storage
from app.services.views import application_out, document_out, message_out, offer_out, revenue_estimate, schedule_for, summary_out
from app.services.workflow import add_event, enqueue, next_application_number, notify_company, transition, utcnow

router = APIRouter(prefix="/api/v1/applications", tags=["Клиент: заявка"])
settings = get_settings()
client_only = require_roles(Role.CLIENT)

EDITABLE_FIELDS = {
    "product_code",
    "amount",
    "term_months",
    "purpose",
    "purpose_details",
    "declared_revenue",
    "has_collateral",
    "collateral_description",
    "owner_guarantee",
    "contact_name",
    "contact_phone",
}
INFO_EDITABLE_FIELDS = {"declared_revenue", "collateral_description", "contact_name", "contact_phone", "purpose_details"}


def _commit(db: Session) -> None:
    try:
        db.commit()
    except StaleDataError as e:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Заявка изменилась, обновите страницу") from e


def _out(db: Session, app: Application, member: CompanyMember) -> dict:
    db.refresh(app)
    return application_out(db, app, member)


def _require_status(app: Application, *allowed: AppStatus) -> None:
    if app.status not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Действие недоступно в статусе заявки «{app.status}»")


def _require_signer(member: CompanyMember) -> None:
    if not member.can_sign:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Подписывать может руководитель или представитель по доверенности. "
            "Нажмите «Передать на подпись», и мы уведомим руководителя.",
        )


# --------------------------------------------------------------------------- черновик


@router.get("", response_model=list[ApplicationSummaryOut], summary="Заявки компании")
def list_applications(company_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    require_company_member(db, user, company_id)
    apps = db.scalars(select(Application).where(Application.company_id == company_id).order_by(Application.created_at.desc()))
    return [summary_out(a) for a in apps]


@router.post("", response_model=ApplicationOut, status_code=201, summary="Создать черновик заявки (данные компании подставляются автоматически)")
def create_application(body: ApplicationCreate, db: Session = Depends(get_db), user: User = Depends(client_only)):
    member = require_company_member(db, user, body.company_id)
    product = db.get(Product, body.product_code)
    if product is None or not product.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Продукт не найден")
    if body.purpose not in PURPOSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестная цель кредита")
    company = db.get(Company, body.company_id)
    stats = assessment.company_turnover(db, company.id)
    parent = None
    if body.parent_id:
        parent = db.get(Application, body.parent_id)
        if parent is None or parent.company_id != company.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Исходная заявка не найдена")
    app = Application(
        number=next_application_number(db),
        company_id=company.id,
        created_by_id=user.id,
        parent_id=parent.id if parent else None,
        product_code=product.code,
        amount=body.amount,
        term_months=body.term_months,
        purpose=body.purpose,
        purpose_details=body.purpose_details or (parent.purpose_details if parent else None),
        declared_revenue=(parent.declared_revenue if parent else None) or revenue_estimate(stats.avg_monthly) or None,
        has_collateral=product.collateral == "REQUIRED" or bool(parent and parent.has_collateral),
        collateral_description=parent.collateral_description if parent else None,
        owner_guarantee=bool(parent and parent.owner_guarantee),
        contact_name=user.full_name,
        contact_phone=user.phone,
        status=AppStatus.DRAFT,
    )
    db.add(app)
    db.flush()
    title = f"Создан черновик заявки на основе № {parent.number}" if parent else "Создан черновик заявки"
    add_event(db, app, "CREATED", title, actor_type=ActorType.CLIENT, actor_id=user.id, to_status=AppStatus.DRAFT)
    _commit(db)
    return _out(db, app, member)


@router.get("/{app_id}", response_model=ApplicationOut, summary="Заявка: статус, что дальше, предложения, история")
def get_application(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    return application_out(db, app, member)


@router.patch("/{app_id}", response_model=ApplicationOut, summary="Изменить черновик (автосохранение)")
def update_application(app_id: uuid.UUID, body: ApplicationUpdate, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.DRAFT)
    data = body.model_dump(exclude_unset=True)
    if "product_code" in data:
        product = db.get(Product, data["product_code"])
        if product is None or not product.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Продукт не найден")
        if product.collateral == "REQUIRED":
            data["has_collateral"] = True
    if "purpose" in data and data["purpose"] not in PURPOSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестная цель кредита")
    for key, value in data.items():
        if key in EDITABLE_FIELDS:
            setattr(app, key, value)
    db.flush()
    db.refresh(app)
    _commit(db)
    return _out(db, app, member)


# --------------------------------------------------------------------------- документы


def _save_document(db: Session, app: Application, user: User, doc_type: str, file_name: str, content: bytes, content_type: str):
    if doc_type not in DOC_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестный тип документа")
    try:
        key, sha = storage.save(app.id, content, content_type)
    except storage.StorageError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    doc = ApplicationDocument(
        application_id=app.id,
        doc_type=doc_type,
        file_name=file_name[:255],
        content_type=content_type,
        size_bytes=len(content),
        sha256=sha,
        storage_key=key,
        uploaded_by_id=user.id,
    )
    db.add(doc)
    add_event(db, app, "DOCUMENT_UPLOADED", f"Загружен документ: {DOC_TYPES[doc_type]}", actor_type=ActorType.CLIENT, actor_id=user.id)
    _commit(db)
    return doc


@router.post("/{app_id}/documents", response_model=DocumentOut, status_code=201, summary="Загрузить документ")
async def upload_document(
    app_id: uuid.UUID,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(client_only),
):
    app, _ = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.DRAFT, AppStatus.NEEDS_INFO)
    content = await file.read()
    doc = _save_document(db, app, user, doc_type, file.filename or "document", content, file.content_type or "application/octet-stream")
    return document_out(doc)


@router.post("/{app_id}/documents/demo", response_model=DocumentOut, status_code=201, summary="Приложить синтетический демо-документ")
def demo_document(app_id: uuid.UUID, doc_type: str, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, _ = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.DRAFT, AppStatus.NEEDS_INFO)
    label = DOC_TYPES.get(doc_type)
    if label is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестный тип документа")
    content = storage.demo_document(label, app.company.short_name, app.company.inn)
    doc = _save_document(db, app, user, doc_type, f"{label} (демо).txt", content, "text/plain")
    return document_out(doc)


@router.get("/{app_id}/documents/{doc_id}", summary="Скачать документ")
def download_document(app_id: uuid.UUID, doc_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, _ = get_client_application(db, user, app_id)
    doc = db.get(ApplicationDocument, doc_id)
    if doc is None or doc.application_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    return FileResponse(storage.path_for(doc.storage_key), media_type=doc.content_type, filename=doc.file_name)


@router.delete("/{app_id}/documents/{doc_id}", status_code=204, summary="Удалить документ")
def delete_document(app_id: uuid.UUID, doc_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, _ = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.DRAFT, AppStatus.NEEDS_INFO)
    doc = db.get(ApplicationDocument, doc_id)
    if doc is None or doc.application_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    storage.delete(doc.storage_key)
    db.delete(doc)
    _commit(db)


# --------------------------------------------------------------------------- подпись (простая ЭП кодом внутри продукта)


def _verify_challenge(db: Session, app: Application, user: User, purpose: str, challenge_id: uuid.UUID, code: str) -> SignChallenge:
    ch = db.get(SignChallenge, challenge_id)
    if ch is None or ch.application_id != app.id or ch.user_id != user.id or ch.purpose != purpose:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Код подтверждения не найден, запросите новый")
    if ch.used_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот код уже использован")
    if ch.expires_at < utcnow():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Срок действия кода истёк, запросите новый")
    if ch.attempts >= 5:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток, запросите новый код")
    ch.attempts += 1
    if hash_code(code.strip(), str(ch.id)) != ch.code_hash:
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неверный код подтверждения")
    ch.used_at = utcnow()
    return ch


@router.post("/{app_id}/sign-challenges", response_model=SignChallengeOut, summary="Получить код подтверждения подписи")
def create_sign_challenge(app_id: uuid.UUID, body: SignChallengeIn, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member)
    if body.purpose == SignPurpose.CONSENTS:
        _require_status(app, AppStatus.DRAFT)
        documents = [CONSENT_TEXTS[ConsentType.BKI_REQUEST], CONSENT_TEXTS[ConsentType.PERSONAL_DATA]]
    else:
        _require_status(app, AppStatus.SIGNING)
        documents = ["Кредитный договор", "График платежей", "Заявление на выдачу кредита"]
    code = f"{secrets.randbelow(1_000_000):06d}"
    ch = SignChallenge(
        id=uuid.uuid4(),
        application_id=app.id,
        user_id=user.id,
        purpose=body.purpose,
        code_hash="",
        expires_at=utcnow() + timedelta(seconds=settings.sign_code_ttl_seconds),
    )
    ch.code_hash = hash_code(code, str(ch.id))
    db.add(ch)
    _commit(db)
    return {
        "challenge_id": ch.id,
        "expires_at": ch.expires_at,
        "demo_code": code if settings.demo_mode else None,
        "documents": documents,
    }


@router.post("/{app_id}/consents", response_model=ApplicationOut, summary="Подписать согласия на запрос в БКИ и обработку данных")
def sign_consents(app_id: uuid.UUID, body: SignConfirmIn, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member)
    _require_status(app, AppStatus.DRAFT)
    ch = _verify_challenge(db, app, user, SignPurpose.CONSENTS, body.challenge_id, body.code)
    existing = {c.consent_type for c in db.scalars(select(Consent).where(Consent.application_id == app.id))}
    for ct in (ConsentType.BKI_REQUEST, ConsentType.PERSONAL_DATA):
        if ct not in existing:
            db.add(
                Consent(
                    application_id=app.id,
                    consent_type=ct,
                    text=CONSENT_TEXTS[ct],
                    signed_by_id=user.id,
                    signed_at=utcnow(),
                    signature_ref=str(ch.id),
                )
            )
    add_event(db, app, "CONSENTS_SIGNED", "Подписаны согласия на запрос в БКИ и обработку данных", actor_type=ActorType.CLIENT, actor_id=user.id)
    _commit(db)
    return _out(db, app, member)


@router.post("/{app_id}/request-signature", response_model=ApplicationOut, summary="Передать заявку руководителю на подпись")
def request_signature(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.DRAFT, AppStatus.SIGNING)
    what = "подписать согласия и отправить заявку" if app.status == AppStatus.DRAFT else "подписать кредитный договор"
    notify_company(
        db,
        app,
        f"Нужна ваша подпись: заявка № {app.number}",
        f"{user.full_name} ({POSITION_LABELS.get(member.position, member.position).lower()}) просит {what}.",
        signers_only=True,
    )
    add_event(db, app, "SIGNATURE_REQUESTED", f"Заявка передана руководителю: {what}", actor_type=ActorType.CLIENT, actor_id=user.id)
    _commit(db)
    return _out(db, app, member)


# --------------------------------------------------------------------------- отправка


def submit_problems(db: Session, app: Application) -> list[str]:
    p = app.product
    problems = []
    amount = float(app.amount)
    if not float(p.min_amount) <= amount <= float(p.max_amount):
        problems.append(f"Сумма должна быть от {float(p.min_amount):,.0f} до {float(p.max_amount):,.0f} ₽".replace(",", " "))
    if not p.min_term <= app.term_months <= p.max_term:
        problems.append(f"Срок должен быть от {p.min_term} до {p.max_term} мес.")
    if app.purpose not in p.purposes:
        problems.append("Цель не подходит для выбранного продукта")
    if not app.declared_revenue:
        problems.append("Укажите выручку за последние 12 месяцев")
    if not (app.contact_name and app.contact_phone):
        problems.append("Укажите контактное лицо и телефон")
    if p.collateral == "REQUIRED" and not (app.collateral_description or "").strip():
        problems.append("Опишите имущество, которое будет в залоге")
    have = {d.doc_type for d in app.documents}
    missing = [DOC_TYPES[t] for t in required_documents(p, amount, app.purpose, app.company.kind) if t not in have]
    if missing:
        problems.append("Приложите документы: " + ", ".join(missing))
    signed = {c.consent_type for c in db.scalars(select(Consent).where(Consent.application_id == app.id))}
    if not {ConsentType.BKI_REQUEST, ConsentType.PERSONAL_DATA} <= signed:
        problems.append("Подпишите согласия на запрос в БКИ и обработку данных")
    return problems


@router.post("/{app_id}/submit", response_model=ApplicationOut, summary="Отправить заявку в банк")
def submit(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member)
    _require_status(app, AppStatus.DRAFT)
    problems = submit_problems(db, app)
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"message": "Заявку нельзя отправить", "problems": problems})
    app.submitted_at = utcnow()
    transition(db, app, AppStatus.SUBMITTED, actor_type=ActorType.CLIENT, actor_id=user.id, title="Заявка отправлена в банк")
    transition(db, app, AppStatus.PRECHECK, title="Заявка принята в работу", stage_detail="Проверяем полноту и корректность данных")
    enqueue(db, app, JobStage.PRECHECK)
    _commit(db)
    return _out(db, app, member)


@router.post("/{app_id}/info-response", response_model=ApplicationOut, summary="Ответить на запрос банка")
def info_response(app_id: uuid.UUID, body: InfoResponseIn, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.NEEDS_INFO)
    req = db.scalar(
        select(InfoRequest)
        .where(InfoRequest.application_id == app.id, InfoRequest.status == InfoRequestStatus.OPEN)
        .order_by(InfoRequest.created_at.desc())
    )
    if req is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Нет открытого запроса")
    if body.fields:
        for key, value in body.fields.model_dump(exclude_unset=True).items():
            if key in INFO_EDITABLE_FIELDS or (key in ("amount", "term_months") and value):
                setattr(app, key, value)
    have_new = {d.doc_type for d in app.documents if d.uploaded_at >= req.created_at}
    have_any = {d.doc_type for d in app.documents}
    problems = []
    for item in req.items:
        if item["type"] == "DOCUMENT" and item["doc_type"] not in have_new and item["doc_type"] not in have_any:
            problems.append(f"Приложите: {item['label']}")
        if item["type"] == "COMMENT" and not (body.comment or "").strip():
            problems.append(f"Ответьте: {item['label']}")
        if item["type"] == "FIELD":
            field = item.get("field")
            if field and not getattr(app, field, None):
                problems.append(f"Заполните: {item['label']}")
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"message": "Ответ неполный", "problems": problems})
    req.status = InfoRequestStatus.ANSWERED
    req.answered_at = utcnow()
    req.answer_comment = body.comment
    if body.comment:
        db.add(Message(application_id=app.id, author_id=user.id, author_role=Role.CLIENT, text=body.comment))
    add_event(db, app, "INFO_PROVIDED", "Вы ответили на запрос банка", actor_type=ActorType.CLIENT, actor_id=user.id)
    transition(db, app, AppStatus.PRECHECK, title="Заявка снова в работе", stage_detail="Проверяем новые данные")
    enqueue(db, app, JobStage.PRECHECK)
    _commit(db)
    return _out(db, app, member)


@router.post("/{app_id}/cancel", response_model=ApplicationOut, summary="Отозвать заявку")
def cancel(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    if app.status not in CLIENT_CANCELLABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Заявку уже нельзя отозвать")
    for req in db.scalars(select(InfoRequest).where(InfoRequest.application_id == app.id, InfoRequest.status == InfoRequestStatus.OPEN)):
        req.status = InfoRequestStatus.CANCELLED
    transition(db, app, AppStatus.CANCELLED, actor_type=ActorType.CLIENT, actor_id=user.id, title="Заявка отозвана клиентом")
    _commit(db)
    return _out(db, app, member)


# --------------------------------------------------------------------------- предложение и подписание


@router.post("/{app_id}/offers/{offer_id}/accept", response_model=ApplicationOut, summary="Принять предложение")
def accept_offer(app_id: uuid.UUID, offer_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.OFFER_READY)
    offer = db.get(Offer, offer_id)
    if offer is None or offer.application_id != app.id or offer.status != OfferStatus.ACTIVE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Предложение не найдено")
    if offer.valid_until < utcnow():
        raise HTTPException(status.HTTP_409_CONFLICT, "Срок действия предложения истёк")
    for o in db.scalars(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACTIVE)):
        o.status = OfferStatus.SUPERSEDED
    offer.status = OfferStatus.ACCEPTED
    offer.accepted_at = utcnow()
    amount = f"{float(offer.amount):,.0f}".replace(",", " ")
    transition(
        db,
        app,
        AppStatus.SIGNING,
        actor_type=ActorType.CLIENT,
        actor_id=user.id,
        title=f"Выбрано предложение: {amount} ₽ на {offer.term_months} мес. под {float(offer.rate):.1f}%".replace(".", ","),
    )
    _commit(db)
    return _out(db, app, member)


@router.post("/{app_id}/offers/decline", response_model=ApplicationOut, summary="Отказаться от предложения")
def decline_offers(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.OFFER_READY)
    for o in db.scalars(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACTIVE)):
        o.status = OfferStatus.DECLINED
    transition(db, app, AppStatus.CLIENT_DECLINED, actor_type=ActorType.CLIENT, actor_id=user.id, title="Вы отказались от предложения")
    _commit(db)
    return _out(db, app, member)


@router.post("/{app_id}/signing/back", response_model=ApplicationOut, summary="Вернуться к выбору условий")
def back_to_offers(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.SIGNING)
    accepted = db.scalar(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACCEPTED))
    for o in db.scalars(
        select(Offer).where(Offer.application_id == app.id, Offer.decision_id == accepted.decision_id)
    ):
        if o.status in (OfferStatus.ACCEPTED, OfferStatus.SUPERSEDED) and o.valid_until > utcnow():
            o.status = OfferStatus.ACTIVE
            o.accepted_at = None
    transition(db, app, AppStatus.OFFER_READY, actor_type=ActorType.CLIENT, actor_id=user.id, title="Вы вернулись к выбору условий")
    _commit(db)
    return _out(db, app, member)


@router.get("/{app_id}/contract", response_model=ContractOut, summary="Проект кредитного договора и график платежей")
def contract(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, _ = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.SIGNING, AppStatus.DISBURSEMENT, AppStatus.ISSUED)
    offer = db.scalar(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACCEPTED))
    signer = db.scalar(
        select(CompanyMember).where(CompanyMember.company_id == app.company_id, CompanyMember.can_sign.is_(True))
    )
    c = app.company
    return {
        "contract_number": f"КД-{app.number}",
        "company": {
            "short_name": c.short_name,
            "full_name": c.full_name,
            "inn": c.inn,
            "ogrn": c.ogrn,
            "legal_address": c.legal_address,
            "settlement_account": c.settlement_account,
        },
        "offer": offer_out(offer),
        "schedule": schedule_for(offer),
        "documents": [
            {"code": "contract", "title": f"Кредитный договор № КД-{app.number}"},
            {"code": "schedule", "title": "График платежей (приложение 1 к договору)"},
            {"code": "disbursement", "title": "Заявление на выдачу кредита на расчётный счёт"},
        ],
        "signer": {"full_name": signer.user.full_name, "position_label": POSITION_LABELS.get(signer.position)} if signer else None,
    }


@router.post("/{app_id}/sign", response_model=ApplicationOut, summary="Подписать кредитный договор кодом")
def sign_contract(app_id: uuid.UUID, body: SignConfirmIn, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member)
    _require_status(app, AppStatus.SIGNING)
    _verify_challenge(db, app, user, SignPurpose.CONTRACT, body.challenge_id, body.code)
    add_event(db, app, "CONTRACT_SIGNED", f"Кредитный договор подписан: {user.full_name}", actor_type=ActorType.CLIENT, actor_id=user.id)
    transition(db, app, AppStatus.DISBURSEMENT, title="Договор подписан, перечисляем деньги", stage_detail="Готовим выдачу кредита")
    enqueue(db, app, JobStage.DISBURSEMENT)
    _commit(db)
    return _out(db, app, member)


# --------------------------------------------------------------------------- прочее


@router.post("/{app_id}/reapply", response_model=ApplicationOut, status_code=201, summary="Подать новую заявку на основе закрытой")
def reapply(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, _ = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.DECLINED, AppStatus.EXPIRED, AppStatus.CLIENT_DECLINED, AppStatus.CANCELLED)
    body = ApplicationCreate(
        company_id=app.company_id,
        product_code=app.product_code,
        amount=float(app.amount),
        term_months=app.term_months,
        purpose=app.purpose,
        purpose_details=app.purpose_details,
        parent_id=app.id,
    )
    return create_application(body, db, user)


@router.get("/{app_id}/messages", response_model=list[MessageOut], summary="Переписка с банком по заявке")
def get_messages(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, _ = get_client_application(db, user, app_id)
    return [message_out(m) for m in db.scalars(select(Message).where(Message.application_id == app.id).order_by(Message.id))]


@router.post("/{app_id}/messages", response_model=MessageOut, status_code=201, summary="Написать в банк по заявке")
def post_message(app_id: uuid.UUID, body: MessageIn, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, _ = get_client_application(db, user, app_id)
    m = Message(application_id=app.id, author_id=user.id, author_role=Role.CLIENT, text=body.text.strip())
    db.add(m)
    _commit(db)
    db.refresh(m)
    return message_out(m)
